import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

import pytest
from fastapi.testclient import TestClient

from strategy_codebot.server import create_app
from strategy_codebot.server import llm_tools
from strategy_codebot.server.action_registry import evaluate_action_registry
from strategy_codebot.server.artifact_store import LocalArtifactStore
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.conversation_context import ConversationContextBuilder
from strategy_codebot.server.database import create_sqlite_repository
from strategy_codebot.server.llm_clients import LLMClientEvent
from strategy_codebot.server.llm_clients import LLM_EVENT_SOURCES
from strategy_codebot.server.llm_clients import ProviderTimeoutError
from strategy_codebot.server.llm_clients import chat_completion_events
from strategy_codebot.server.llm_clients import response_events
from strategy_codebot.server.llm_clients import stream_response_events
from strategy_codebot.server.domain_intent_gate import load_chat_intent_registry
from strategy_codebot.server.domain_intent_gate import normalize_evidence_signals
from strategy_codebot.server.domain_intent_gate import validate_chat_intent_registry
from strategy_codebot.server.model_routing import PROVIDER_ROUTE_EVENT
from strategy_codebot.server.model_routing import PROVIDER_KEEPALIVE_EVENT
from strategy_codebot.server.llm_orchestrator import LLMOrchestrator
from strategy_codebot.server.llm_orchestrator import ActionPlanner
from strategy_codebot.server.llm_orchestrator import ActionPlanDecision
from strategy_codebot.server.llm_orchestrator import ChatIntentDecisionPlanner
from strategy_codebot.server.llm_orchestrator import ClassifierRouteCaptureClient
from strategy_codebot.server.llm_orchestrator import ResponseIntentClassifier
from strategy_codebot.server.llm_orchestrator import _classify_domain_scope
from strategy_codebot.server.llm_orchestrator import _classifier_route_timeout_seconds
from strategy_codebot.server.llm_orchestrator import _classifier_timeout_seconds
from strategy_codebot.server.llm_orchestrator import _classify_response_intent
from strategy_codebot.server.llm_orchestrator import _fallback_chat_intent_decision
from strategy_codebot.server.llm_orchestrator import _model_stage_for_chat
from strategy_codebot.server.llm_orchestrator import _parse_action_plan_json
from strategy_codebot.server.llm_orchestrator import _parse_chat_intent_decision_json
from strategy_codebot.server.llm_orchestrator import _sanitize_user_facing_model_text
from strategy_codebot.server.llm_orchestrator import _should_enable_web_search_auto
from strategy_codebot.server.llm_orchestrator import _action_planner_system_prompt
from strategy_codebot.server.llm_orchestrator import _chat_intent_decision_system_prompt
from strategy_codebot.server.llm_orchestrator import _direct_action_plan_tool_args
from strategy_codebot.server.llm_orchestrator import _suggestions_payload
from strategy_codebot.server.llm_orchestrator import _strategy_bot_workflow_payload
from strategy_codebot.server.llm_orchestrator import _system_prompt
from strategy_codebot.server.llm_orchestrator import _workflow_kickoff_fallback_decision
from strategy_codebot.server.llm_orchestrator import chat_safety_preflight
from strategy_codebot.server.policy_semantic_gate import PolicySemanticGateClassifier
from strategy_codebot.server.policy_semantic_gate import collect_semantic_policy_candidates
from strategy_codebot.server.policy_semantic_gate import should_block_semantic_policy
from strategy_codebot.server.llm_orchestrator import _maybe_backtest_summary_response
from strategy_codebot.server.llm_orchestrator import _maybe_backtest_trades_response
from strategy_codebot.server.llm_orchestrator import _maybe_strategy_bot_workflow_payload
from strategy_codebot.server.llm_orchestrator import _tool_only_success_message
from strategy_codebot.server.llm_orchestrator import _tool_success_result
from strategy_codebot.server.llm_orchestrator import _workflow_task_resume_chat_decision
from strategy_codebot.server.market_data import MarketDataGateway
from strategy_codebot.server.model_audit import MODEL_ACTION_PROPOSED
from strategy_codebot.server.model_audit import MODEL_ACTION_REJECTED
from strategy_codebot.server.model_audit import WORKFLOW_GATE_CONFIRMED
from strategy_codebot.server.model_audit import WORKFLOW_GATE_REJECTED
from strategy_codebot.server.model_audit import append_model_audit_event
from strategy_codebot.server.schemas import MessageCreate
from strategy_codebot.server.security_controls import SecurityControlError
from strategy_codebot.server.security_controls import SecurityControls
from strategy_codebot.server.repository import BotProposalCreateInput
from strategy_codebot.server.repository import InMemoryConversationRepository
from strategy_codebot.server.workflow_registry import validate_workflow_payload
from strategy_codebot.server.workflow_registry import workflow_catalog_guidance
from strategy_codebot.server.workflow_tasks import build_workflow_task_payload
from strategy_codebot.server.llm_tools import compact_tool_output, tool_catalog_consistency_errors
from strategy_codebot.pine import generate_pine
from server_helpers import parse_sse
from server_helpers import valid_spec

AUTH_A = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-a"}
AUTH_B = {"X-User-Id": "user-b", "X-Workspace-Id": "workspace-a"}
AUTH_OTHER_WORKSPACE = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-b"}


def test_chat_intent_registry_contract_is_valid() -> None:
    registry = load_chat_intent_registry()

    validate_chat_intent_registry(registry)
    assert "off_topic" in registry["domain_scopes"]
    assert "strategy_building" in registry["response_intents"]
    assert registry["intent_domain_scopes"]["strategy_building"] == "trading_workflow"
    assert registry["intent_model_stages"]["strategy_building"] == "strategy_reasoning"
    assert registry["intent_model_stages"]["backtest_preview"] == "pine_code_generation"
    assert "workflow_fast" in registry["model_stages"]
    assert "preview_intent" in registry["evidence_signals"]


def test_chat_intent_registry_requires_model_stage_for_every_intent() -> None:
    registry = json.loads(json.dumps(load_chat_intent_registry()))
    registry["intent_model_stages"].pop("strategy_building")

    with pytest.raises(ValueError, match="intent_model_stages"):
        validate_chat_intent_registry(registry)


def test_chat_intent_registry_rejects_unknown_model_stage() -> None:
    registry = json.loads(json.dumps(load_chat_intent_registry()))
    registry["intent_model_stages"]["strategy_building"] = "invented_stage"

    with pytest.raises(ValueError, match="unknown stages"):
        validate_chat_intent_registry(registry)


def test_chat_intent_registry_rejects_unknown_workflow_policy_refs() -> None:
    registry = json.loads(json.dumps(load_chat_intent_registry()))
    registry["response_intent_policies"]["strategy_building"]["allowed_workflow_intents"] = ["invented_workflow"]

    with pytest.raises(ValueError, match="unknown workflow intents"):
        validate_chat_intent_registry(registry)


def test_chat_intent_registry_rejects_unknown_workflow_id() -> None:
    registry = json.loads(json.dumps(load_chat_intent_registry()))
    registry["workflow_intent_policies"]["strategy_to_paper_bot_simulation"]["workflow_id"] = "invented_workflow"

    with pytest.raises(ValueError, match="unknown workflow ids"):
        validate_chat_intent_registry(registry)


def test_chat_intent_registry_rejects_duplicate_evidence_signal_ids() -> None:
    registry = json.loads(json.dumps(load_chat_intent_registry()))
    registry["evidence_signals"].append(registry["evidence_signals"][0])

    with pytest.raises(ValueError, match="evidence_signals contains duplicate ids"):
        validate_chat_intent_registry(registry)


def test_chat_intent_registry_rejects_unknown_workflow_timeout_fallback_signal() -> None:
    registry = json.loads(json.dumps(load_chat_intent_registry()))
    registry["workflow_intent_policies"]["strategy_to_paper_bot_simulation"]["timeout_fallback"][
        "required_evidence_signals"
    ] = ["invented_signal"]

    with pytest.raises(ValueError, match="timeout_fallback.required_evidence_signals"):
        validate_chat_intent_registry(registry)


def test_normalize_evidence_signals_drops_unknown_values() -> None:
    assert normalize_evidence_signals(["preview_intent", "invented_signal", "preview_intent"]) == ("preview_intent",)


def test_model_audit_event_redacts_raw_prompt_secret_and_tool_output() -> None:
    repository = InMemoryConversationRepository()
    auth = AuthContext(user_id="user-a", workspace_id="workspace-a")
    conversation = repository.create_conversation(auth)
    run = repository.create_run(auth, conversation.id)
    assert run is not None

    event = append_model_audit_event(
        repository,
        auth,
        run,
        MODEL_ACTION_PROPOSED,
        {
            "actor": "model",
            "source": "planner",
            "status": "proposed",
            "safe_args_summary": {
                "api_key": "sk-secret",
                "prompt": "raw user prompt",
                "strategy_code": "//@version=6\nstrategy('secret')",
                "symbol": "BTCUSDT",
                "runtime_id": "rt_1",
            },
            "tool_output": {"raw": "do not persist"},
        },
    )

    assert event is not None
    payload = event.payload
    assert payload["schema_version"] == 1
    assert payload["actor"] == "model"
    safe_summary = payload["safe_args_summary"]
    assert "symbol" in safe_summary["keys"]
    assert "runtime_id" in safe_summary["ids"]
    serialized = json.dumps(payload)
    assert "sk-secret" not in serialized
    assert "api_key" not in serialized
    assert "raw user prompt" not in serialized
    assert "strategy_code" not in serialized
    assert "do not persist" not in serialized


def test_direct_bot_action_args_do_not_inject_backtest_run_id() -> None:
    result = _direct_action_plan_tool_args(
        ActionPlanDecision(
            decision="call_tool",
            intent_id="bot_status",
            confidence=0.9,
            source="planner",
            tool_id="get_bot_status",
            arguments={"runtime_id": "rt_1"},
        ),
        artifact_kinds=set(),
        context_text="Show bot runtime rt_1 status",
        web_search="auto",
    )

    assert result == ("get_bot_status", {"runtime_id": "rt_1"})


def test_direct_backtest_plan_action_accepts_complete_args() -> None:
    strategy_spec = {
        "target_platform": "pine_v6",
        "script_type": "strategy",
        "market": "crypto",
        "symbol": "ETHUSDT",
        "timeframe": "15m",
        "entry_rules": ["Enter long on fast EMA crossing above slow EMA."],
        "exit_rules": ["Exit when fast EMA crosses below slow EMA."],
        "risk_rules": ["Risk 1% per trade."],
    }
    result = _direct_action_plan_tool_args(
        ActionPlanDecision(
            decision="call_tool",
            intent_id="backtest_preview",
            confidence=1.0,
            source="selected_action",
            tool_id="create_backtest_plan",
            arguments={
                "prompt": "Prepare a review-only local preview.",
                "strategy_spec": strategy_spec,
                "pine_code": "//@version=6\nstrategy(\"x\")",
            },
        ),
        artifact_kinds={"pine_file", "validation_report"},
        context_text="Backtest Preview selected.",
        web_search="auto",
    )

    assert result == (
        "create_backtest_plan",
        {
            "prompt": "Prepare a review-only local preview.",
            "strategy_spec": strategy_spec,
            "pine_code": "//@version=6\nstrategy(\"x\")",
        },
    )


def _strategy_workflow_llm(final_text: str = "Mình cần vài thông tin tối thiểu.") -> "SequencedRecordingLLMClient":
    return SequencedRecordingLLMClient(
        [
            [LLMClientEvent(type="message.delta", text="Strategy Workflow")],
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "response_intent": "strategy_building",
                            "action": "answer",
                            "model_stage": "strategy_reasoning",
                            "confidence": 0.88,
                            "tool_id": None,
                            "auto_chain": False,
                            "current_context_required": False,
                            "domain_scope": "trading_workflow",
                            "workflow_intent": "strategy_to_paper_bot_simulation",
                            "missing_inputs": ["market", "timeframe", "style", "risk_preference"],
                            "reasons": ["The user asks to build a strategy and paper Bot workflow."],
                            "used_signals": [],
                        }
                    ),
                )
            ],
            [LLMClientEvent(type="message.delta", text="not an action plan")],
            [LLMClientEvent(type="message.delta", text=final_text)],
        ]
    )


def _semantic_boundary_strategy_workflow_llm(
    final_text: str = "Mình cần vài thông tin tối thiểu.",
) -> "SequencedRecordingLLMClient":
    return SequencedRecordingLLMClient(
        [
            [LLMClientEvent(type="message.delta", text="Strategy Workflow")],
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "policy_intent": "boundary_statement",
                            "target": "broker_execution",
                            "polarity": "deny",
                            "confidence": 0.92,
                            "reason_code": "paper_only_boundary",
                        }
                    ),
                )
            ],
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "response_intent": "strategy_building",
                            "action": "answer",
                            "model_stage": "strategy_reasoning",
                            "confidence": 0.88,
                            "tool_id": None,
                            "auto_chain": False,
                            "current_context_required": False,
                            "domain_scope": "trading_workflow",
                            "workflow_intent": "strategy_to_paper_bot_simulation",
                            "missing_inputs": ["market", "timeframe", "style", "risk_preference"],
                            "reasons": ["The user asks to build a strategy and paper Bot workflow."],
                            "used_signals": [],
                        }
                    ),
                )
            ],
            [LLMClientEvent(type="message.delta", text="not an action plan")],
            [LLMClientEvent(type="message.delta", text=final_text)],
        ]
    )


def test_strategy_bot_prompt_emits_workflow_collect_inputs(tmp_path: Path) -> None:
    llm = _strategy_workflow_llm()
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={
            "content": "Mình muốn xây dựng một trading strategy mới và sau đó tạo Bot simulation để theo dõi thử.",
            "language": "vi",
        },
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    workflow = next(frame for frame in frames if frame["event"] == "chat.workflow.updated")
    payload = workflow["data"]["payload"]
    assert payload["workflow_id"] == "strategy_bot_simulation"
    assert payload["current_step"] == "collect_strategy_inputs"
    assert payload["blocked_reason"] == "missing_strategy_inputs"
    assert payload["start_allowed"] is False
    assert set(payload["missing_fields"]) >= {"market", "symbol", "timeframe", "style", "risk_preference"}
    assert payload["tasks"][0]["task_template_id"] == "collect_strategy_inputs"
    assert payload["tasks"][0]["status"] == "pending_user"
    run_id = frames[0]["data"]["run_id"]
    persisted_events = repository.list_run_events(AuthContext(user_id="user-a", workspace_id="workspace-a"), run_id)
    assert persisted_events is not None
    event_types = [frame["event"] for frame in frames] + [event.type for event in persisted_events]
    assert "model_action.validated" in event_types
    assert "workflow.gate.required" in event_types

    tasks = client.get(
        f"/v1/conversations/{conversation['id']}/workflow-tasks",
        headers=AUTH_A,
    )
    assert tasks.status_code == 200, tasks.text
    assert tasks.json()["items"][0]["task_template_id"] == "collect_strategy_inputs"


def test_workflow_task_response_validates_inputs_and_tenant(tmp_path: Path) -> None:
    llm = _strategy_workflow_llm()
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={
            "content": "Mình muốn xây dựng trading strategy mới rồi tạo Bot simulation paper.",
            "language": "vi",
        },
    )
    assert stream.status_code == 200, stream.text
    run_id = parse_sse(stream.text)[0]["data"]["run_id"]
    auth = AuthContext(user_id="user-a", workspace_id="workspace-a")
    task = client.get(
        f"/v1/conversations/{conversation['id']}/workflow-tasks",
        headers=AUTH_A,
    ).json()["items"][0]

    bad = client.post(
        f"/v1/workflow-tasks/{task['id']}/responses",
        headers=AUTH_A,
        json={"values": {"market": "crypto", "unknown": "x"}},
    )
    assert bad.status_code == 422
    events = repository.list_run_events(auth, run_id)
    assert events is not None
    reject = next(event for event in events if event.type == WORKFLOW_GATE_REJECTED)
    assert reject.payload["source"] == "workflow_task"
    assert reject.payload["reason_code"] == "task_response_invalid"
    assert reject.payload["task_id"] == task["id"]

    cross_tenant = client.post(
        f"/v1/workflow-tasks/{task['id']}/responses",
        headers=AUTH_B,
        json={"values": {"market": "crypto"}},
    )
    assert cross_tenant.status_code == 404

    partial = client.post(
        f"/v1/workflow-tasks/{task['id']}/responses",
        headers=AUTH_A,
        json={"values": {"market": "crypto"}},
    )
    assert partial.status_code == 200, partial.text
    assert partial.json()["status"] == "pending_user"
    assert partial.json()["values"]["market"] == "crypto"

    submitted = client.post(
        f"/v1/workflow-tasks/{task['id']}/responses",
        headers=AUTH_A,
        json={
            "values": {
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "style": "trend",
                "risk_preference": "balanced",
            }
        },
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "completed"
    assert submitted.json()["continuation"]["required"] is True
    assert submitted.json()["continuation"]["task_id"] == task["id"]
    assert submitted.json()["response"]["values"]["symbol"] == "BTCUSDT"
    events = repository.list_run_events(auth, run_id)
    assert events is not None
    confirmed = [event for event in events if event.type == WORKFLOW_GATE_CONFIRMED]
    assert confirmed[-1].payload["source"] == "workflow_task"
    assert confirmed[-1].payload["actor"] == "user"
    assert confirmed[-1].payload["task_id"] == task["id"]
    assert any(event.type == "workflow.continuation.required" for event in events)

    state = client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A)
    assert state.status_code == 200, state.text
    assert state.json()["pending_workflow_continuation"]["task_id"] == task["id"]

    messages_before = state.json()["messages"]
    continuation = client.post(
        f"/v1/workflow-tasks/{task['id']}/continuations?stream=true",
        headers=AUTH_A,
        json={"language": "vi"},
    )
    assert continuation.status_code == 200, continuation.text
    continuation_frames = parse_sse(continuation.text)
    assert any(frame["event"] == "workflow.continuation.started" for frame in continuation_frames)
    assert any(frame["event"] == "workflow.continuation.completed" for frame in continuation_frames)
    intent_frame = next(frame for frame in continuation_frames if frame["event"] == "chat.response_intent")
    assert intent_frame["data"]["payload"]["source"] == "workflow_task_resume"
    workflow_frames = [
        frame["data"]["payload"]
        for frame in continuation_frames
        if frame["event"] == "chat.workflow.updated"
    ]
    assert workflow_frames[-1]["current_step"] == "generate_pine"
    assert workflow_frames[-1]["completed_steps"] == [
        "collect_strategy_inputs",
        "draft_strategy_spec",
    ]
    next_task = next(
        task
        for task in workflow_frames[-1]["tasks"]
        if task["task_template_id"] == "review_strategy_spec_next_step"
    )
    assert next_task["status"] == "pending_user"
    assert next_task["blocking"] is True
    next_request = next_task["input_requests"][0]
    assert next_request["id"] == "next_after_strategy_spec"
    assert next_request["question"] == "Strategy spec is ready. What should we do next?"
    assert next_request["recommended_option_id"] == "generate_pine"

    after = client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A)
    assert after.status_code == 200, after.text
    assert after.json()["pending_workflow_continuation"] is None
    assert len([item for item in after.json()["messages"] if item["role"] == "user"]) == len(
        [item for item in messages_before if item["role"] == "user"]
    )
    assert len([item for item in after.json()["messages"] if item["role"] == "assistant"]) > len(
        [item for item in messages_before if item["role"] == "assistant"]
    )

    review_task = next(
        item
        for item in client.get(
            f"/v1/conversations/{conversation['id']}/workflow-tasks",
            headers=AUTH_A,
        ).json()["items"]
        if item["task_template_id"] == "review_strategy_spec_next_step"
    )
    reviewed = client.post(
        f"/v1/workflow-tasks/{review_task['id']}/responses",
        headers=AUTH_A,
        json={"values": {"next_after_strategy_spec": "generate_pine"}},
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "completed"
    assert reviewed.json()["continuation"]["required"] is True
    stale_run = repository.create_run(auth, conversation["id"], status="completed")
    assert stale_run is not None
    stale_continuation_payload = {
        **reviewed.json()["continuation"],
        "source": "workflow_task_resume",
        "status": "completed",
    }
    repository.append_run_event(
        auth,
        stale_run.id,
        "workflow.continuation.started",
        {**stale_continuation_payload, "status": "started"},
    )
    repository.append_run_event(
        auth,
        stale_run.id,
        "workflow.continuation.completed",
        stale_continuation_payload,
    )
    stale_state = client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A)
    assert stale_state.status_code == 200, stale_state.text
    assert stale_state.json()["pending_workflow_continuation"]["task_id"] == review_task["id"]
    assert stale_state.json()["pending_workflow_continuation"]["reason"] == "workflow_continuation_incomplete"

    pine_continuation = client.post(
        f"/v1/workflow-tasks/{review_task['id']}/continuations?stream=true",
        headers=AUTH_A,
        json={"language": "vi"},
    )
    assert pine_continuation.status_code == 200, pine_continuation.text
    pine_frames = parse_sse(pine_continuation.text)
    assert any(
        frame["event"] == "tool.started"
        and frame["data"]["payload"]["tool_id"] == "generate_pine"
        for frame in pine_frames
    )
    assert any(
        frame["event"] == "tool.completed"
        and frame["data"]["payload"]["tool_id"] == "generate_pine"
        for frame in pine_frames
    )
    assert not any(frame["event"] == "provider.started" for frame in pine_frames)
    pine_workflow_frames = [
        frame["data"]["payload"]
        for frame in pine_frames
        if frame["event"] == "chat.workflow.updated"
    ]
    assert pine_workflow_frames
    assert "collect_strategy_inputs" in pine_workflow_frames[-1]["completed_steps"]
    assert "draft_strategy_spec" in pine_workflow_frames[-1]["completed_steps"]
    assert "generate_pine" in pine_workflow_frames[-1]["completed_steps"]
    assert pine_workflow_frames[-1]["current_step"] != "collect_strategy_inputs"

    duplicate = client.post(
        f"/v1/workflow-tasks/{task['id']}/continuations?stream=true",
        headers=AUTH_A,
        json={"language": "vi"},
    )
    assert duplicate.status_code == 409

    duplicate_response = client.post(
        f"/v1/workflow-tasks/{task['id']}/responses",
        headers=AUTH_A,
        json={"values": {"market": "crypto"}},
    )
    assert duplicate_response.status_code == 200, duplicate_response.text
    duplicate_state = client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A)
    assert duplicate_state.status_code == 200, duplicate_state.text
    assert duplicate_state.json()["pending_workflow_continuation"] is None

    noise_events = [
        ("progress.snapshot", {"index": index})
        for index in range(130)
    ]
    assert repository.append_run_events(auth, run_id, noise_events) is not None
    late_state = client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A)
    assert late_state.status_code == 200, late_state.text
    assert late_state.json()["pending_workflow_continuation"] is None
    late_duplicate = client.post(
        f"/v1/workflow-tasks/{task['id']}/continuations?stream=true",
        headers=AUTH_A,
        json={"language": "vi"},
    )
    assert late_duplicate.status_code == 409


def test_workflow_collect_input_started_continuation_becomes_retryable_when_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _strategy_workflow_llm()
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={
            "content": "Mình muốn xây dựng trading strategy mới rồi tạo Bot simulation paper.",
            "language": "vi",
        },
    )
    assert stream.status_code == 200, stream.text
    auth = AuthContext(user_id="user-a", workspace_id="workspace-a")
    task = client.get(
        f"/v1/conversations/{conversation['id']}/workflow-tasks",
        headers=AUTH_A,
    ).json()["items"][0]

    submitted = client.post(
        f"/v1/workflow-tasks/{task['id']}/responses",
        headers=AUTH_A,
        json={
            "values": {
                "market": "crypto",
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "style": "trend",
                "risk_preference": "balanced",
            }
        },
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["continuation"]["task_id"] == task["id"]

    stale_run = repository.create_run(auth, conversation["id"], status="running")
    assert stale_run is not None
    repository.append_run_event(
        auth,
        stale_run.id,
        "workflow.continuation.started",
        {
            **submitted.json()["continuation"],
            "source": "workflow_task_resume",
            "status": "started",
        },
    )

    fresh_state = client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A)
    assert fresh_state.status_code == 200, fresh_state.text
    assert fresh_state.json()["pending_workflow_continuation"] is None

    monkeypatch.setenv("STRATEGY_CODEBOT_WORKFLOW_CONTINUATION_STALE_SECONDS", "0")
    stale_state = client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A)
    assert stale_state.status_code == 200, stale_state.text
    assert stale_state.json()["pending_workflow_continuation"]["task_id"] == task["id"]
    assert stale_state.json()["pending_workflow_continuation"]["reason"] == "workflow_continuation_stale"

    retry = client.post(
        f"/v1/workflow-tasks/{task['id']}/continuations?stream=true",
        headers=AUTH_A,
        json={"language": "vi"},
    )
    assert retry.status_code == 200, retry.text
    retry_frames = parse_sse(retry.text)
    assert any(frame["event"] == "workflow.continuation.started" for frame in retry_frames)


def test_workflow_collect_input_failed_continuation_remains_retryable(tmp_path: Path) -> None:
    llm = _strategy_workflow_llm()
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={
            "content": "Mình muốn xây dựng trading strategy mới rồi tạo Bot simulation paper.",
            "language": "vi",
        },
    )
    assert stream.status_code == 200, stream.text
    auth = AuthContext(user_id="user-a", workspace_id="workspace-a")
    task = client.get(
        f"/v1/conversations/{conversation['id']}/workflow-tasks",
        headers=AUTH_A,
    ).json()["items"][0]
    submitted = client.post(
        f"/v1/workflow-tasks/{task['id']}/responses",
        headers=AUTH_A,
        json={
            "values": {
                "market": "crypto",
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "style": "trend",
                "risk_preference": "balanced",
            }
        },
    )
    assert submitted.status_code == 200, submitted.text

    failed_run = repository.create_run(auth, conversation["id"], status="failed")
    assert failed_run is not None
    continuation_payload = {
        **submitted.json()["continuation"],
        "source": "workflow_task_resume",
    }
    repository.append_run_event(
        auth,
        failed_run.id,
        "workflow.continuation.started",
        {**continuation_payload, "status": "started"},
    )
    repository.append_run_event(
        auth,
        failed_run.id,
        "workflow.continuation.failed",
        {**continuation_payload, "status": "failed", "reason": "provider_idle_timeout"},
    )

    state = client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A)
    assert state.status_code == 200, state.text
    assert state.json()["pending_workflow_continuation"]["task_id"] == task["id"]
    assert state.json()["pending_workflow_continuation"]["reason"] == "workflow_continuation_failed"


def test_workflow_task_partial_response_regenerates_next_prompt(tmp_path: Path) -> None:
    auth = AuthContext(user_id="user-a", workspace_id="workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth)
    assert conversation is not None
    repository.create_message(
        auth,
        conversation.id,
        "Mình muốn strategy paper bot cho crypto, hãy hỏi từng câu.",
    )
    run = repository.create_run(auth, conversation.id)
    assert run is not None
    payload = build_workflow_task_payload(
        "strategy_bot_simulation",
        "collect_strategy_inputs",
        input_request_ids=["market", "symbol", "style"],
        status="pending_user",
    )
    assert payload is not None
    task = repository.upsert_workflow_task(
        auth,
        conversation_id=conversation.id,
        workflow_id="strategy_bot_simulation",
        task_template_id="collect_strategy_inputs",
        step_id="collect_strategy_inputs",
        kind="input_request",
        status="pending_user",
        payload_json=payload,
        run_id=run.id,
    )
    assert task is not None
    llm = RecordingLLMClient(
        [
            LLMClientEvent(
                type="message.delta",
                text=(
                    '{"id":"symbol","question":"Crypto đã chọn. Symbol nào nên backtest?",'
                    '"options":['
                    '{"id":"ethusdt","value":"ETHUSDT","label":"ETHUSDT","description":"Altcoin thanh khoản cao"},'
                    '{"id":"btcusdt","value":"BTCUSDT","label":"BTCUSDT","description":"Benchmark crypto"},'
                    '{"id":"solusdt","value":"SOLUSDT","label":"SOLUSDT","description":"Biến động cao"}'
                    '],"recommended_option_id":"ethusdt"}'
                ),
            )
        ]
    )
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path, llm_client=llm))

    response = client.post(
        f"/v1/workflow-tasks/{task.id}/responses",
        headers=AUTH_A,
        json={"values": {"market": "crypto"}},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pending_user"
    symbol = next(item for item in body["input_requests"] if item["id"] == "symbol")
    assert symbol["question"] == "Crypto đã chọn. Symbol nào nên backtest?"
    assert [option["label"] for option in symbol["options"]] == ["ETHUSDT", "BTCUSDT", "SOLUSDT"]
    assert symbol["options"][0]["description"] == "Altcoin thanh khoản cao"
    assert body["values"]["market"] == "crypto"


def test_workflow_task_action_rejects_disabled_gate(tmp_path: Path) -> None:
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path, llm_client=FakeLLMClient([])))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    auth = AuthContext(user_id="user-a", workspace_id="workspace-a")
    run = repository.create_run(auth, conversation["id"])
    assert run is not None
    task = repository.upsert_workflow_task(
        auth,
        conversation_id=conversation["id"],
        run_id=run.id,
        workflow_id="strategy_bot_simulation",
        task_template_id="confirm_paper_start",
        step_id="complete_setup_confirm_start",
        kind="confirmation_gate",
        status="pending_user",
        payload_json={
            "task_template_id": "confirm_paper_start",
            "step_id": "complete_setup_confirm_start",
            "kind": "confirmation_gate",
            "title": "Confirm paper simulation",
            "blocking": True,
            "status": "pending_user",
            "input_request_ids": [],
            "action_ids": ["confirm_paper_start"],
            "input_requests": [],
            "actions": [{"id": "confirm_paper_start", "enabled": False}],
            "values": {},
        },
    )
    assert task is not None

    response = client.post(
        f"/v1/workflow-tasks/{task.id}/actions/confirm_paper_start",
        headers=AUTH_A,
        json={"values": {}, "status": "approved"},
    )

    assert response.status_code == 422
    events = repository.list_run_events(auth, run.id)
    assert events is not None
    rejected = next(event for event in events if event.type == WORKFLOW_GATE_REJECTED)
    assert rejected.payload["source"] == "workflow_task"
    assert rejected.payload["reason_code"] == "task_action_invalid"
    assert rejected.payload["action_id"] == "confirm_paper_start"


def test_bot_proposal_confirm_start_records_audit_reject_and_execute(tmp_path: Path) -> None:
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path, llm_client=FakeLLMClient([])))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    auth = AuthContext(user_id="user-a", workspace_id="workspace-a")
    run = repository.create_run(auth, conversation["id"])
    assert run is not None
    missing_proposal = repository.create_bot_proposal(
        auth,
        BotProposalCreateInput(
            status="draft",
            source_conversation_id=conversation["id"],
            source_run_id=run.id,
            source_artifact_ids=[],
            strategy_id="strategy_a",
            strategy_name="Audit strategy",
            manifest_json={"strategy_id": "strategy_a"},
            data_subscriptions_json=[],
            broker_connection_id=None,
            account_id=None,
            risk_policy_id=None,
            readiness_checks_json=[],
            missing_inputs_json=["broker_connection_id", "account_id", "risk_policy_id"],
        ),
    )

    missing = client.post(
        f"/v1/bots/proposals/{missing_proposal.id}/confirm-start",
        headers=AUTH_A,
        json={},
    )

    assert missing.status_code == 422
    events = repository.list_run_events(auth, run.id)
    assert events is not None
    rejected = next(event for event in events if event.type == WORKFLOW_GATE_REJECTED)
    assert rejected.payload["source"] == "bot_proposal"
    assert rejected.payload["proposal_id"] == missing_proposal.id
    assert rejected.payload["missing_fields"] == [
        "broker_connection_id",
        "account_id",
        "risk_policy_id",
        "data_subscriptions",
    ]

    ready_proposal = repository.create_bot_proposal(
        auth,
        BotProposalCreateInput(
            status="draft",
            source_conversation_id=conversation["id"],
            source_run_id=run.id,
            source_artifact_ids=[],
            strategy_id="strategy_b",
            strategy_name="Ready audit strategy",
            manifest_json={"strategy_id": "strategy_b"},
            data_subscriptions_json=[{"symbol": "BTCUSDT", "timeframe": "1h"}],
            broker_connection_id="broker_paper",
            account_id="account_paper",
            risk_policy_id="risk_policy_1",
            readiness_checks_json=[],
            missing_inputs_json=[],
        ),
    )

    started = client.post(
        f"/v1/bots/proposals/{ready_proposal.id}/confirm-start",
        headers=AUTH_A,
        json={},
    )

    assert started.status_code == 200, started.text
    events = repository.list_run_events(auth, run.id)
    assert events is not None
    confirmed = [
        event
        for event in events
        if event.type == WORKFLOW_GATE_CONFIRMED
        and event.payload.get("source") == "bot_proposal"
        and event.payload.get("proposal_id") == ready_proposal.id
    ]
    assert confirmed
    assert confirmed[-1].payload["status"] == "executed"
    assert confirmed[-1].payload["runtime_id"] == started.json()["runtime"]["id"]


def test_workflow_task_batch_sync_noops_terminal_and_auto_resolves() -> None:
    auth = AuthContext(user_id="user-a", workspace_id="workspace-a")
    for repository in (InMemoryConversationRepository(), create_sqlite_repository()):
        conversation = repository.create_conversation(auth)
        run = repository.create_run(auth, conversation.id)
        assert run is not None
        collect_task = build_workflow_task_payload(
            "strategy_bot_simulation",
            "collect_strategy_inputs",
            values={
                "market": "crypto",
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "style": "trend",
                "risk_preference": "balanced",
            },
        )
        choice_task = build_workflow_task_payload(
            "strategy_bot_simulation",
            "draft_only_backtest_choice",
            values={"draft_only_choice": "draft_only"},
        )
        assert collect_task is not None
        assert choice_task is not None

        first = repository.sync_workflow_tasks(
            auth,
            conversation_id=conversation.id,
            run_id=run.id,
            workflow_id="strategy_bot_simulation",
            task_payloads=[collect_task, choice_task],
            completed_steps=set(),
        )
        assert first is not None
        assert len(first.created) == 2
        assert not first.updated
        assert not first.resolved

        second = repository.sync_workflow_tasks(
            auth,
            conversation_id=conversation.id,
            run_id=run.id,
            workflow_id="strategy_bot_simulation",
            task_payloads=[collect_task, choice_task],
            completed_steps=set(),
        )
        assert second is not None
        assert not second.created
        assert not second.updated
        assert not second.resolved
        assert len(second.unchanged) == 2

        resolved = repository.sync_workflow_tasks(
            auth,
            conversation_id=conversation.id,
            run_id=run.id,
            workflow_id="strategy_bot_simulation",
            task_payloads=[choice_task],
            completed_steps={"collect_strategy_inputs"},
        )
        assert resolved is not None
        assert [task.task_template_id for task in resolved.resolved] == ["collect_strategy_inputs"]
        assert resolved.resolved[0].status == "completed"

        changed_collect_task = build_workflow_task_payload(
            "strategy_bot_simulation",
            "collect_strategy_inputs",
            values={**collect_task["values"], "symbol": "ETHUSDT"},
        )
        assert changed_collect_task is not None
        terminal_preserved = repository.sync_workflow_tasks(
            auth,
            conversation_id=conversation.id,
            run_id=run.id,
            workflow_id="strategy_bot_simulation",
            task_payloads=[changed_collect_task, choice_task],
            completed_steps=set(),
        )
        assert terminal_preserved is not None
        collect_record = next(
            task for task in terminal_preserved.records if task.task_template_id == "collect_strategy_inputs"
        )
        assert collect_record.status == "completed"
        assert collect_record.payload_json["values"]["symbol"] == "BTCUSDT"
        assert repository.sync_workflow_tasks(
            AuthContext(user_id="user-b", workspace_id="workspace-a"),
            conversation_id=conversation.id,
            run_id=run.id,
            workflow_id="strategy_bot_simulation",
            task_payloads=[choice_task],
            completed_steps=set(),
        ) is None


def test_strategy_bot_workflow_keeps_missing_setup_start_locked() -> None:
    payload = _strategy_bot_workflow_payload(
        message_content="Create a paper bot simulation from this strategy.",
        context_text="market crypto symbol BTCUSDT timeframe 1h style trend risk 1%. strategy artifact with backtest evidence",
        artifact_kinds={"pine_file", "backtest_report"},
        tool_name="draft_bot",
        tool_result={
            "proposal_id": "botp_1",
            "bot_proposal": {
                "proposal_id": "botp_1",
                "strategy_id": "strategy_1",
                "data_subscriptions": [{"symbol": "BTCUSDT", "timeframe": "1h"}],
            },
            "missing_inputs": ["broker_connection_id", "account_id", "risk_policy_id"],
        },
    )

    assert payload is not None
    assert payload["current_step"] == "complete_setup_confirm_start"
    assert payload["bot_proposal_id"] == "botp_1"
    assert payload["blocked_reason"] == "missing_bot_setup_fields"
    assert payload["start_allowed"] is False
    assert payload["missing_fields"] == ["broker_connection_id", "account_id", "risk_policy_id"]
    assert payload["schema_version"] == 1
    assert payload["intent"] == "strategy_to_paper_bot_simulation"
    assert payload["status"]["key"] == "reviewable_with_caveats"
    assert payload["actions"][0]["kind"] == "confirm_start_bot_proposal"
    assert payload["actions"][0]["enabled"] is False
    assert [section["id"] for section in payload["sections"]] == ["strategy_inputs", "paper_setup"]
    assert [task["task_template_id"] for task in payload["tasks"]] == [
        "complete_paper_setup",
        "confirm_paper_start",
    ]
    assert payload["tasks"][0]["input_request_ids"] == [
        "broker_connection_id",
        "account_id",
        "risk_policy_id",
    ]
    assert payload["tasks"][1]["status"] == "blocked"


def test_workflow_registry_sanitizes_model_proposed_ui_parts() -> None:
    payload = validate_workflow_payload(
        {
            "workflow_id": "strategy_bot_simulation",
            "current_step": "collect_strategy_inputs",
            "completed_steps": ["collect_strategy_inputs", "unknown_step"],
            "skipped_steps": [
                "generate_pine",
                "collect_strategy_inputs",
                "complete_setup_confirm_start",
                "unknown_step",
            ],
            "step_reasons": {
                "generate_pine": "User prefers no Pine.",
                "complete_setup_confirm_start": "Unsafe skip.",
                "unknown_step": "Unknown.",
            },
            "evidence_status": "profitable",
            "intent": "live_trading",
            "required_fields": ["market", "live_broker_secret"],
            "missing_fields": ["market", "live_broker_secret"],
            "sections": [
                {"id": "strategy_inputs", "component_kind": "field_status_section"},
                {"id": "paper_setup", "component_kind": "custom_adapter_section"},
                {"id": "unknown", "component_kind": "field_status_section"},
            ],
            "actions": [
                {"id": "confirm_paper_start", "enabled": True, "label": "Confirm"},
                {"id": "runtime_start", "enabled": True, "label": "Start live runtime"},
            ],
            "tasks": [
                {
                    "id": "wft_collect",
                    "task_template_id": "collect_strategy_inputs",
                    "status": "pending_user",
                    "input_request_ids": ["market", "live_broker_secret"],
                },
                {
                    "id": "wft_live",
                    "task_template_id": "start_live_runtime",
                    "status": "pending_user",
                },
            ],
            "start_allowed": True,
        }
    )

    assert payload is not None
    assert payload["intent"] == "strategy_to_paper_bot_simulation"
    assert payload["evidence_status"] == "insufficient_evidence"
    assert payload["completed_steps"] == ["collect_strategy_inputs"]
    assert payload["skipped_steps"] == ["generate_pine"]
    assert payload["step_reasons"] == {"generate_pine": "User prefers no Pine."}
    assert payload["required_fields"] == ["market"]
    assert payload["missing_fields"] == ["market"]
    assert payload["sections"] == [
        {
            "id": "strategy_inputs",
            "component_kind": "field_status_section",
            "title": "Strategy inputs",
            "fields": ["market", "symbol", "timeframe", "style", "entry_exit_idea", "risk_preference"],
        }
    ]
    assert payload["actions"] == [
        {
            "id": "confirm_paper_start",
            "kind": "confirm_start_bot_proposal",
            "label": "Confirm",
            "enabled": False,
            "disabled_reason": "Setup fields are incomplete.",
            "target_ref": None,
        }
    ]
    assert payload["start_allowed"] is False
    assert len(payload["tasks"]) == 1
    assert payload["tasks"][0]["id"] == "wft_collect"
    assert payload["tasks"][0]["task_template_id"] == "collect_strategy_inputs"
    assert payload["tasks"][0]["input_request_ids"] == ["market"]


def test_workflow_registry_task_sanitizer_matches_payload_builder() -> None:
    values = {
        "market": " crypto ",
        "symbol": " BTCUSDT ",
        "live_broker_secret": "do-not-render",
    }
    payload = validate_workflow_payload(
        {
            "workflow_id": "strategy_bot_simulation",
            "current_step": "collect_strategy_inputs",
            "tasks": [
                {
                    "id": "wft_collect",
                    "task_template_id": "collect_strategy_inputs",
                    "input_request_ids": ["market", "symbol", "live_broker_secret"],
                    "values": values,
                }
            ],
            "task_values": values,
        }
    )
    built = build_workflow_task_payload(
        "strategy_bot_simulation",
        "collect_strategy_inputs",
        input_request_ids=["market", "symbol", "live_broker_secret"],
        values=values,
    )

    assert payload is not None
    assert built is not None
    assert payload["tasks"][0]["input_request_ids"] == built["input_request_ids"] == ["market", "symbol"]
    assert payload["tasks"][0]["values"] == built["values"] == {"market": "crypto", "symbol": "BTCUSDT"}
    assert payload["task_values"] == {"market": "crypto", "symbol": "BTCUSDT"}


def test_workflow_catalog_guidance_reads_generated_registry() -> None:
    guidance = workflow_catalog_guidance()

    assert "strategy_bot_simulation (strategy_to_paper_bot_simulation)" in guidance
    assert "field_status_section" in guidance
    assert "Do not invent component names" in guidance


def test_strategy_bot_workflow_skips_pine_when_user_has_existing_spec() -> None:
    payload = _strategy_bot_workflow_payload(
        message_content=(
            "Mình có strategy spec có sẵn, không cần Pine. "
            "Create a paper bot simulation for BTCUSDT 1h trend with balanced risk."
        ),
        context_text="market crypto symbol BTCUSDT timeframe 1h style trend risk balanced",
        artifact_kinds=set(),
    )

    assert payload is not None
    assert payload["current_step"] == "backtest_preview"
    assert payload["completed_steps"] == ["collect_strategy_inputs", "draft_strategy_spec"]
    assert payload["skipped_steps"] == ["generate_pine", "static_validation"]
    assert payload["step_reasons"] == {
        "generate_pine": "User asked to skip Pine generation.",
        "static_validation": "No generated Pine artifact to validate.",
    }
    assert payload["evidence_status"] == "insufficient_evidence"
    assert payload["start_allowed"] is False


def test_strategy_bot_workflow_does_not_skip_pine_from_negated_or_descriptive_text() -> None:
    negated = _strategy_bot_workflow_payload(
        message_content=(
            "Please do not skip Pine. Create a paper bot simulation for BTCUSDT 1h trend "
            "with balanced risk."
        ),
        context_text="market crypto symbol BTCUSDT timeframe 1h style trend risk balanced",
        artifact_kinds=set(),
    )
    descriptive = _strategy_bot_workflow_payload(
        message_content=(
            "No Pine artifact yet, generate Pine and create a paper bot simulation for "
            "BTCUSDT 1h trend with balanced risk."
        ),
        context_text="market crypto symbol BTCUSDT timeframe 1h style trend risk balanced",
        artifact_kinds=set(),
    )

    assert negated is not None
    assert negated["current_step"] == "draft_strategy_spec"
    assert negated["skipped_steps"] == []
    assert descriptive is not None
    assert descriptive["current_step"] == "draft_strategy_spec"
    assert descriptive["skipped_steps"] == []


def test_strategy_bot_workflow_skip_pine_alone_does_not_complete_spec_with_missing_inputs() -> None:
    payload = _strategy_bot_workflow_payload(
        message_content="Không cần Pine. Create a paper bot simulation.",
        context_text="",
        artifact_kinds=set(),
    )

    assert payload is not None
    assert payload["current_step"] == "collect_strategy_inputs"
    assert "draft_strategy_spec" not in payload["completed_steps"]
    assert set(payload["missing_fields"]) >= {"market", "symbol", "timeframe", "style", "risk_preference"}


def test_strategy_bot_workflow_field_catalog_does_not_count_as_inputs() -> None:
    payload = _strategy_bot_workflow_payload(
        message_content=(
            "Mình muốn xây dựng một trading strategy mới và sau đó tạo Bot simulation để theo dõi thử. "
            "Hãy đi theo workflow từng bước: Hỏi mình các thông tin tối thiểu để thiết kế strategy: "
            "market/symbol timeframe style: trend, mean reversion, breakout, scalping, hoặc DCA "
            "entry/exit idea nếu có risk preference. Tạo strategy spec trước, chưa tạo Bot."
        ),
        context_text="",
        artifact_kinds=set(),
    )

    assert payload is not None
    assert payload["current_step"] == "collect_strategy_inputs"
    assert "collect_strategy_inputs" not in payload["completed_steps"]
    assert set(payload["missing_fields"]) >= {"market", "symbol", "timeframe", "style", "risk_preference"}
    assert payload["tasks"][0]["task_template_id"] == "collect_strategy_inputs"


def test_strategy_bot_workflow_concrete_values_count_as_inputs() -> None:
    payload = _strategy_bot_workflow_payload(
        message_content=(
            "Tạo trading strategy và paper bot simulation cho market crypto, symbol BTCUSDT, timeframe 1h, "
            "style trend, risk balanced."
        ),
        context_text="",
        artifact_kinds=set(),
    )

    assert payload is not None
    assert payload["current_step"] == "draft_strategy_spec"
    assert payload["completed_steps"] == ["collect_strategy_inputs"]
    assert payload["missing_fields"] == []
    assert payload["tasks"] == []


def test_strategy_bot_workflow_prompts_next_action_after_strategy_spec() -> None:
    payload = _strategy_bot_workflow_payload(
        message_content=(
            "Tạo trading strategy và paper bot simulation cho market crypto, symbol BTCUSDT, timeframe 1h, "
            "style trend, risk balanced."
        ),
        context_text="",
        artifact_kinds=set(),
        completed_strategy_spec=True,
    )

    assert payload is not None
    assert payload["current_step"] == "generate_pine"
    assert payload["completed_steps"] == ["collect_strategy_inputs", "draft_strategy_spec"]
    assert [task["task_template_id"] for task in payload["tasks"]] == ["review_strategy_spec_next_step"]
    task = payload["tasks"][0]
    assert task["blocking"] is True
    assert task["input_request_ids"] == ["next_after_strategy_spec"]
    assert task["input_requests"][0]["question"] == "Strategy spec is ready. What should we do next?"
    assert [option["id"] for option in task["input_requests"][0]["options"]] == [
        "generate_pine",
        "revise_strategy_spec",
        "skip_pine",
    ]


def test_workflow_task_resume_decision_uses_strategy_spec_next_action() -> None:
    decision = _workflow_task_resume_chat_decision(
        {
            "task_template_id": "review_strategy_spec_next_step",
            "resume_intent": "strategy_building",
            "values": {"next_after_strategy_spec": "generate_pine"},
        }
    )

    assert decision.source == "workflow_task_resume"
    assert decision.response_intent == "pine_generation"
    assert decision.workflow_intent == "strategy_to_paper_bot_simulation"
    assert decision.auto_chain is False


def test_generate_pine_next_action_resume_keeps_strategy_spec_completed() -> None:
    repository = create_sqlite_repository()
    auth = AuthContext(user_id="user-a", workspace_id="workspace-a")
    conversation = repository.create_conversation(auth)
    assert conversation is not None
    decision = _workflow_task_resume_chat_decision(
        {
            "task_template_id": "review_strategy_spec_next_step",
            "resume_intent": "strategy_building",
            "values": {"next_after_strategy_spec": "generate_pine"},
        }
    )

    payload = _maybe_strategy_bot_workflow_payload(
        repository=repository,
        auth=auth,
        conversation_id=conversation.id,
        chat_decision=decision,
        message_content=(
            "Resume workflow from reviewed spec for market crypto, symbol BTCUSDT, timeframe 1h, "
            "style trend, risk balanced."
        ),
        context_text="market crypto symbol BTCUSDT timeframe 1h style trend risk balanced",
        artifact_kinds=set(),
        completed_strategy_spec=True,
    )

    assert payload is not None
    assert payload["current_step"] == "generate_pine"
    assert payload["completed_steps"] == ["collect_strategy_inputs", "draft_strategy_spec"]


def test_strategy_bot_workflow_skips_backtest_for_draft_only_review() -> None:
    payload = _strategy_bot_workflow_payload(
        message_content=(
            "Bỏ backtest preview, chỉ draft Bot proposal để review cho paper simulation."
        ),
        context_text="market crypto symbol BTCUSDT timeframe 1h style trend risk balanced",
        artifact_kinds={"pine_file", "validation_report"},
    )

    assert payload is not None
    assert payload["current_step"] == "draft_bot_proposal"
    assert payload["completed_steps"] == ["collect_strategy_inputs", "draft_strategy_spec", "generate_pine", "static_validation"]
    assert payload["skipped_steps"] == ["backtest_preview", "evidence_review"]
    assert payload["step_reasons"] == {
        "backtest_preview": "User asked to skip backtest preview.",
        "evidence_review": "Draft-only review without backtest evidence.",
    }
    assert payload["evidence_status"] == "needs_validation_or_robustness_check"
    assert payload["start_allowed"] is False


def test_generic_generate_pine_tool_does_not_create_paper_bot_workflow(tmp_path: Path) -> None:
    repository = create_sqlite_repository()
    artifact_store = LocalArtifactStore(tmp_path)
    auth = AuthContext(user_id="user-a", workspace_id="workspace-a")
    conversation = repository.create_conversation(auth)
    assert conversation is not None
    run = repository.create_run(auth, conversation.id)
    assert run is not None
    orchestrator = LLMOrchestrator(
        repository=repository,
        artifact_store=artifact_store,
        client=RecordingLLMClient([]),
    )

    frames = [
        frame
        for raw in orchestrator._execute_tool_call(
            auth,
            run,
            "generate_pine",
            {"strategy_spec": valid_spec()},
            orchestrator._new_budget(),
            response_intent="strategy_building",
            user_message=(
                "Build a review-only BTCUSDT 1h EMA 20/50 trend-following strategy "
                "with bounded risk and generate Pine v6 output."
            ),
            context_text="",
            web_search="off",
            language="en",
            workflow_intent=None,
        )
        for frame in parse_sse(raw)
    ]

    event_types = [frame["event"] for frame in frames]
    assert "tool.completed" in event_types
    assert "artifact.created" in event_types
    assert "chat.workflow.updated" not in event_types
    assert not any(event.type == "workflow.task.created" for event in repository.list_run_events(auth, run.id))
    assert repository.list_workflow_tasks(auth, conversation.id) == []


def test_strategy_bot_workflow_skipped_evidence_keeps_completed_proposal_start_locked() -> None:
    payload = _strategy_bot_workflow_payload(
        message_content="Bỏ backtest preview, chỉ draft Bot proposal để review cho paper simulation.",
        context_text="market crypto symbol BTCUSDT timeframe 1h style trend risk balanced",
        artifact_kinds={"pine_file", "validation_report"},
        tool_name="draft_bot",
        tool_result={
            "proposal_id": "botp_1",
            "bot_proposal": {
                "proposal_id": "botp_1",
                "broker_connection_id": "paper",
                "account_id": "acct_1",
                "risk_policy_id": "risk_1",
                "strategy_id": "strategy_1",
                "data_subscriptions": [{"symbol": "BTCUSDT", "timeframe": "1h"}],
            },
            "missing_inputs": [],
        },
    )

    assert payload is not None
    assert payload["current_step"] == "complete_setup_confirm_start"
    assert payload["skipped_steps"] == ["backtest_preview", "evidence_review"]
    assert payload["missing_fields"] == []
    assert payload["start_allowed"] is False
    assert payload["actions"][0]["enabled"] is False


def test_strategy_bot_workflow_ignores_generic_simulation_without_bot_signal() -> None:
    payload = _strategy_bot_workflow_payload(
        message_content="Simulate this strategy with a backtest preview.",
        context_text="market crypto symbol BTCUSDT timeframe 1h style trend risk 1%.",
        artifact_kinds={"pine_file"},
        tool_name="run_backtest_preview",
        tool_result={"run_id": "bt_1", "status": "queued"},
    )

    assert payload is None


def test_strategy_bot_workflow_downstream_artifacts_prevent_collect_input_regression() -> None:
    payload = _strategy_bot_workflow_payload(
        message_content="Prepare a review-only local preview evidence check for the current strategy.",
        context_text="crypto ETHUSDT 15m risk aggressive",
        artifact_kinds={"pine_file", "validation_report", "review_report"},
        force=True,
    )

    assert payload is not None
    assert payload["current_step"] == "backtest_preview"
    assert payload["completed_steps"] == [
        "collect_strategy_inputs",
        "draft_strategy_spec",
        "generate_pine",
        "static_validation",
    ]
    assert payload["missing_fields"] == []
    assert payload["blocked_reason"] is None


def test_strategy_bot_workflow_accepts_lowercase_stock_symbol() -> None:
    payload = _strategy_bot_workflow_payload(
        message_content="Create a stock nvda daily trend strategy paper bot simulation with balanced risk.",
        context_text="",
        artifact_kinds=set(),
    )

    assert payload is not None
    assert "symbol" not in payload["missing_fields"]


def test_strategy_bot_workflow_allows_confirmation_without_autostart() -> None:
    payload = _strategy_bot_workflow_payload(
        message_content="Create a paper bot simulation from this strategy.",
        context_text="market crypto symbol BTCUSDT timeframe 1h style trend risk 1%. strategy artifact with backtest evidence",
        artifact_kinds={"pine_file", "backtest_report"},
        tool_name="draft_bot",
        tool_result={
            "proposal_id": "botp_1",
            "bot_proposal": {
                "proposal_id": "botp_1",
                "broker_connection_id": "paper",
                "account_id": "acct_1",
                "risk_policy_id": "risk_1",
                "strategy_id": "strategy_1",
                "data_subscriptions": [{"symbol": "BTCUSDT", "timeframe": "1h"}],
            },
            "missing_inputs": [],
        },
    )

    assert payload is not None
    assert payload["current_step"] == "complete_setup_confirm_start"
    assert payload["start_allowed"] is True
    assert payload["missing_fields"] == []
    assert payload["artifact_refs"] == {"bot_proposal_id": "botp_1"}


def test_strategy_bot_workflow_draft_bot_alone_does_not_claim_evidence() -> None:
    payload = _strategy_bot_workflow_payload(
        message_content="Create a paper bot simulation from this strategy.",
        context_text="market crypto symbol BTCUSDT timeframe 1h style trend risk 1%. strategy artifact",
        artifact_kinds={"pine_file"},
        tool_name="draft_bot",
        tool_result={
            "proposal_id": "botp_1",
            "bot_proposal": {
                "proposal_id": "botp_1",
                "broker_connection_id": "paper",
                "account_id": "acct_1",
                "risk_policy_id": "risk_1",
                "strategy_id": "strategy_1",
                "data_subscriptions": [{"symbol": "BTCUSDT", "timeframe": "1h"}],
            },
            "missing_inputs": [],
        },
    )

    assert payload is not None
    assert payload["current_step"] == "static_validation"
    assert payload["evidence_status"] == "insufficient_evidence"
    assert payload["start_allowed"] is False


def test_classifier_timeout_defaults_to_route_aware_budget(monkeypatch) -> None:
    monkeypatch.delenv("STRATEGY_CODEBOT_CLASSIFIER_TIMEOUT_SECONDS", raising=False)

    assert _classifier_timeout_seconds() == 25.0


def test_classifier_timeout_env_override_still_wins(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_CLASSIFIER_TIMEOUT_SECONDS", "3.5")

    assert _classifier_timeout_seconds() == 3.5


def test_classifier_route_timeout_leaves_budget_for_fallback(monkeypatch) -> None:
    monkeypatch.delenv("STRATEGY_CODEBOT_CLASSIFIER_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("STRATEGY_CODEBOT_CLASSIFIER_ROUTE_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("STRATEGY_CODEBOT_CLASSIFIER_ROUTE_TIMEOUT_SECONDS_CHAT_INTENT_DECISION", raising=False)

    route_timeout = _classifier_route_timeout_seconds("chat_intent_decision")

    assert route_timeout is not None
    assert route_timeout < _classifier_timeout_seconds("chat_intent_decision")
    assert route_timeout == pytest.approx(5.0)


def test_classifier_route_timeout_env_override(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_CLASSIFIER_ROUTE_TIMEOUT_SECONDS_CHAT_INTENT_DECISION", "2.5")

    assert _classifier_route_timeout_seconds("chat_intent_decision") == pytest.approx(2.5)


def test_chat_safety_preflight_allows_boundary_statements() -> None:
    for prompt in (
        "No broker execution.",
        "không broker execution.",
        "paper simulation only.",
        "không tự start runtime nếu chưa có confirmation UI rõ ràng.",
    ):
        assert chat_safety_preflight(prompt) is None


def test_policy_semantic_gate_invalid_json_does_not_block_boundary_candidate() -> None:
    candidates = collect_semantic_policy_candidates("Bot chỉ là paper simulation, không broker execution.")
    assert [candidate.target for candidate in candidates] == ["broker_execution"]

    decision = PolicySemanticGateClassifier(FakeLLMClient([LLMClientEvent(type="message.delta", text="not json")])).classify(
        "Bot chỉ là paper simulation, không broker execution.",
        candidates=candidates,
        surface="agent.chat.input",
        evidence_level="strategy_idea",
    )

    assert decision.source == "fallback"
    assert not should_block_semantic_policy(decision)


def test_strategy_bot_boundary_prompt_reaches_workflow(tmp_path: Path) -> None:
    llm = _semantic_boundary_strategy_workflow_llm()
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={
            "content": (
                "Mình muốn xây dựng một trading strategy mới và sau đó tạo Bot simulation để theo dõi thử. "
                "Đi theo workflow từng bước, tạo strategy spec trước, chưa tạo Bot. "
                "Bot chỉ là paper simulation, không broker execution, không tự start runtime nếu chưa có "
                "confirmation UI rõ ràng."
            ),
            "language": "vi",
        },
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    event_types = [frame["event"] for frame in frames]

    assert "policy.blocked" not in event_types
    assert "chat.workflow.updated" in event_types
    policy_validated = [
        frame["data"]["payload"]
        for frame in frames
        if frame["event"] == "model_action.validated"
        and frame["data"]["payload"].get("source") == "policy_semantic_gate"
    ]
    assert policy_validated
    assert policy_validated[0]["status"] == "allowed"
    assert policy_validated[0]["policy_intent"] == "boundary_statement"
    assert policy_validated[0]["polarity"] == "deny"


def test_strategy_bot_timeout_fallback_opens_collect_input_workflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_CLASSIFIER_TIMEOUT_SECONDS", "0.01")
    repository = create_sqlite_repository()
    llm = SlowLLMClient([LLMClientEvent(type="message.delta", text="Mình cần vài thông tin tối thiểu.")], delay_seconds=0.05)
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={
            "content": (
                "Mình muốn xây dựng một trading strategy mới và sau đó tạo Bot simulation để theo dõi thử. "
                "Hãy đi theo workflow từng bước: hỏi market/symbol timeframe style entry exit risk preference. "
                "Bot là paper simulation only, no broker execution, không tự start runtime."
            ),
            "language": "vi",
        },
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    event_types = [frame["event"] for frame in frames]

    assert "classifier.timeout" in event_types
    assert "chat.workflow.updated" in event_types
    assert "provider.started" not in event_types
    assert "tool.started" not in event_types
    assert "chat.action_plan" not in event_types
    assert frames[-1]["event"] == "run.completed"
    assert frames[-1]["data"]["payload"]["source"] == "workflow_task_prompt"
    intent_payload = next(frame["data"]["payload"] for frame in frames if frame["event"] == "chat.response_intent")
    assert intent_payload["source"] == "workflow_timeout_fallback"
    assert intent_payload["workflow_intent"] == "strategy_to_paper_bot_simulation"
    workflow_payload = next(frame["data"]["payload"] for frame in frames if frame["event"] == "chat.workflow.updated")
    assert workflow_payload["workflow_id"] == "strategy_bot_simulation"
    assert workflow_payload["current_step"] == "collect_strategy_inputs"
    assert workflow_payload["start_allowed"] is False

    tasks = client.get(
        f"/v1/conversations/{conversation['id']}/workflow-tasks",
        headers=AUTH_A,
    )
    assert tasks.status_code == 200
    assert tasks.json()["items"][0]["task_template_id"] == "collect_strategy_inputs"


def test_strategy_bot_classifier_fallback_opens_collect_input_workflow(tmp_path: Path) -> None:
    repository = create_sqlite_repository()
    llm = SequencedRecordingLLMClient(
        [
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "policy_intent": "boundary_statement",
                            "target": "broker_execution",
                            "polarity": "deny",
                            "confidence": 0.92,
                            "reason_code": "paper_only_boundary",
                        }
                    ),
                )
            ],
            [LLMClientEvent(type="message.delta", text="not valid classifier json")],
            [LLMClientEvent(type="message.delta", text="main provider should not be called")],
        ]
    )
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={
            "content": (
                "Mình muốn xây dựng một trading strategy mới và sau đó tạo Bot simulation để theo dõi thử. "
                "Đi theo workflow từng bước, tạo strategy spec trước, chưa tạo Bot. "
                "Bot chỉ là paper simulation, không broker execution, không tự start runtime nếu chưa có confirmation UI rõ ràng."
            ),
            "language": "vi",
        },
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    event_types = [frame["event"] for frame in frames]

    assert "classifier.completed" in event_types
    assert "chat.workflow.updated" in event_types
    assert "provider.started" not in event_types
    assert "tool.started" not in event_types
    assert "chat.action_plan" not in event_types
    assert frames[-1]["event"] == "run.completed"
    assert frames[-1]["data"]["payload"]["source"] == "workflow_task_prompt"
    intent_payload = next(frame["data"]["payload"] for frame in frames if frame["event"] == "chat.response_intent")
    assert intent_payload["source"] == "workflow_classifier_fallback"
    assert intent_payload["workflow_intent"] == "strategy_to_paper_bot_simulation"
    assert set(intent_payload["used_signals"]) >= {"paper_bot_simulation_request", "strategy_design_request"}

    workflow_payload = next(frame["data"]["payload"] for frame in frames if frame["event"] == "chat.workflow.updated")
    assert workflow_payload["workflow_id"] == "strategy_bot_simulation"
    assert workflow_payload["current_step"] == "collect_strategy_inputs"
    assert workflow_payload["start_allowed"] is False

    run_id = frames[0]["data"]["run_id"]
    auth = AuthContext(user_id="user-a", workspace_id="workspace-a")
    persisted_events = repository.list_run_events(auth, run_id)
    assert any(event.type == "workflow.task.created" for event in persisted_events)
    assert any(
        event.type == "model_action.validated"
        and event.payload.get("reason_code") == "workflow_classifier_fallback"
        for event in persisted_events
    )

    tasks = client.get(
        f"/v1/conversations/{conversation['id']}/workflow-tasks",
        headers=AUTH_A,
    )
    assert tasks.status_code == 200
    assert tasks.json()["items"][0]["task_template_id"] == "collect_strategy_inputs"


def test_workflow_kickoff_fallback_requires_safe_input_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "strategy_codebot.server.llm_orchestrator.classifier_fallback_policy",
        lambda: {
            "readonly_web_search_allowed": True,
            "safe_workflow_kickoff_allowed": False,
            "workflow_creation_allowed": False,
            "auto_chain_allowed": False,
            "tool_actions_allowed": False,
        },
    )

    decision = _workflow_kickoff_fallback_decision(
        (
            "Mình muốn xây dựng một trading strategy mới và sau đó tạo Bot simulation để theo dõi thử. "
            "Bot chỉ là paper simulation, không broker execution, không tự start runtime."
        ),
        source="workflow_classifier_fallback",
    )

    assert decision is None


@pytest.mark.parametrize(
    "content",
    [
        "Check latest Pine Script v6 strategy.entry docs and summarize current changes.",
        "ETH đang bao nhiêu rồi?",
        "What can Strategy Codebot do?",
    ],
)
def test_classifier_fallback_does_not_open_strategy_workflow_for_non_workflow_prompts(
    content: str,
    tmp_path: Path,
) -> None:
    repository = create_sqlite_repository()
    llm = SequencedRecordingLLMClient(
        [
            [LLMClientEvent(type="message.delta", text="not valid classifier json")],
            [LLMClientEvent(type="message.delta", text="neutral provider response")],
        ]
    )
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": content, "language": "vi"},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    event_types = [frame["event"] for frame in frames]

    assert "chat.workflow.updated" not in event_types
    assert "workflow.task.created" not in event_types
    assert "tool.started" not in event_types
    tasks = client.get(
        f"/v1/conversations/{conversation['id']}/workflow-tasks",
        headers=AUTH_A,
    )
    assert tasks.status_code == 200
    assert tasks.json()["items"] == []


def test_docs_timeout_fallback_does_not_open_strategy_workflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_CLASSIFIER_TIMEOUT_SECONDS", "0.01")
    repository = create_sqlite_repository()
    llm = SlowLLMClient([LLMClientEvent(type="message.delta", text="docs answer")], delay_seconds=0.05)
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={
            "content": "Check latest Pine Script v6 strategy.entry docs and summarize current changes.",
            "language": "en",
        },
    )

    assert stream.status_code == 200, stream.text
    event_types = [frame["event"] for frame in parse_sse(stream.text)]
    assert "classifier.timeout" in event_types
    assert "chat.workflow.updated" not in event_types
    assert "workflow.task.created" not in event_types
    assert "tool.started" not in event_types


def test_classifier_route_event_persists_safe_route_summary(tmp_path: Path) -> None:
    route_event = LLMClientEvent(
        type=PROVIDER_ROUTE_EVENT,
        arguments={
            "model_tier": "paid_low",
            "model_stage": "workflow_fast",
            "provider_route": "litellm_proxy/paid_low.workflow_fast_gemini_flash",
            "provider": "litellm_proxy",
            "model": "paid-low-secret-alias",
            "fallback_used": False,
            "attempt_count": 1,
        },
        model="paid-low-secret-alias",
    )
    llm = SequencedRecordingLLMClient(
        [
            [LLMClientEvent(type="message.delta", text="Strategy Workflow")],
            [
                route_event,
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "response_intent": "strategy_building",
                            "action": "answer",
                            "model_stage": "strategy_reasoning",
                            "confidence": 0.88,
                            "domain_scope": "trading_workflow",
                            "workflow_intent": "strategy_to_paper_bot_simulation",
                            "auto_chain": False,
                        }
                    ),
                ),
            ],
            [LLMClientEvent(type="message.delta", text="not an action plan")],
            [LLMClientEvent(type="message.delta", text="Mình cần vài thông tin tối thiểu.")],
        ]
    )
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    message = "Mình muốn xây dựng strategy và tạo Bot simulation để theo dõi thử."

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": message, "language": "vi"},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    route_payload = next(frame["data"]["payload"] for frame in frames if frame["event"] == "classifier.route")
    assert route_payload["classifier_name"] == "chat_intent_decision"
    assert route_payload["stage"] == "workflow_fast"
    assert route_payload["provider_route"] == "litellm_proxy/paid_low.workflow_fast_gemini_flash"
    assert route_payload["model"] == "paid-low-secret-alias"
    assert "safe_prompt_summary" in route_payload
    assert message not in json.dumps(route_payload, ensure_ascii=False)


@dataclass
class FakeLLMClient:
    events: list[LLMClientEvent]
    configured: bool = True
    model: str = "fake-responses-model"

    def ensure_configured(self) -> None:
        if not self.configured:
            raise RuntimeError("fake client not configured")

    def stream(self, *, messages: list[dict[str, str]], tools: list[dict]) -> Iterable[LLMClientEvent]:
        return list(self.events)


@dataclass
class RecordingLLMClient(FakeLLMClient):
    calls: int = 0
    calls_messages: list[list[dict[str, str]]] = field(default_factory=list)
    calls_tools: list[list[dict]] = field(default_factory=list)

    def stream(self, *, messages: list[dict[str, str]], tools: list[dict]) -> Iterable[LLMClientEvent]:
        self.calls += 1
        self.calls_messages.append(messages)
        self.calls_tools.append(tools)
        return list(self.events)


@dataclass
class SlowLLMClient(FakeLLMClient):
    delay_seconds: float = 0.2
    calls: int = 0

    def stream(self, *, messages: list[dict[str, str]], tools: list[dict]) -> Iterable[LLMClientEvent]:
        self.calls += 1
        time.sleep(self.delay_seconds)
        return list(self.events)


@dataclass
class SequencedRecordingLLMClient:
    event_batches: list[list[LLMClientEvent]]
    model: str = "fake-responses-model"
    calls_messages: list[list[dict[str, str]]] = field(default_factory=list)
    calls_tools: list[list[dict]] = field(default_factory=list)

    def ensure_configured(self) -> None:
        return None

    def stream(self, *, messages: list[dict[str, str]], tools: list[dict]) -> Iterable[LLMClientEvent]:
        self.calls_messages.append(messages)
        self.calls_tools.append(tools)
        index = min(len(self.calls_messages) - 1, len(self.event_batches) - 1)
        return list(self.event_batches[index])


@dataclass
class RoutingAwareSequencedLLMClient:
    event_batches: list[list[LLMClientEvent]]
    model: str = "fake-responses-model"
    calls_messages: list[list[dict[str, str]]] = field(default_factory=list)
    calls_tools: list[list[dict]] = field(default_factory=list)
    routing_contexts: list[dict | None] = field(default_factory=list)

    def ensure_configured(self) -> None:
        return None

    def stream(
        self,
        *,
        messages: list[dict[str, str]],
        tools: list[dict],
        routing_context: dict | None = None,
    ) -> Iterable[LLMClientEvent]:
        self.calls_messages.append(messages)
        self.calls_tools.append(tools)
        self.routing_contexts.append(routing_context)
        index = min(len(self.calls_messages) - 1, len(self.event_batches) - 1)
        return list(self.event_batches[index])


def test_classifier_route_capture_client_injects_auth_tier_and_overrides_context() -> None:
    inner = RoutingAwareSequencedLLMClient([[LLMClientEvent(type="message.delta", text="{}")]])
    auth = AuthContext("user-a", "workspace-a", user_tier="paid_low")
    capture_client = ClassifierRouteCaptureClient(
        inner,
        stage="workflow_fast",
        route_timeout_seconds=2.5,
        auth=auth,
    )

    list(
        capture_client.stream(
            messages=[{"role": "user", "content": "classify"}],
            tools=[],
            routing_context={"stage": "strategy_reasoning", "user_tier": "free"},
        )
    )

    context = inner.routing_contexts[0]
    assert context["stage"] == "workflow_fast"
    assert context["auth"] is auth
    assert context["user_tier"] == "paid_low"
    assert context["route_timeout_seconds"] == 2.5
    assert context["hard_route_timeout"] is True


class BlockingSecondModelCallControls(SecurityControls):
    def __init__(self) -> None:
        self.model_calls = 0

    def check_model_call(self, auth: AuthContext, *, model: str) -> None:
        self.model_calls += 1
        if self.model_calls == 2:
            raise SecurityControlError("model-call-blocked")


@dataclass
class SummaryFailingAfterAnswerClient:
    model: str = "fake-responses-model"
    calls_messages: list[list[dict[str, str]]] = field(default_factory=list)

    def ensure_configured(self) -> None:
        return None

    def stream(self, *, messages: list[dict[str, str]], tools: list[dict]) -> Iterable[LLMClientEvent]:
        self.calls_messages.append(messages)
        if len(self.calls_messages) == 1:
            return [LLMClientEvent(type="message.delta", text="not an action plan")]
        if len(self.calls_messages) == 2:
            return [LLMClientEvent(type="message.delta", text="Answer before summary failure.")]
        raise RuntimeError("summary provider down")


@dataclass
class IntentFailingThenAnswerClient:
    model: str = "fake-responses-model"
    calls_messages: list[list[dict[str, str]]] = field(default_factory=list)

    def ensure_configured(self) -> None:
        return None

    def stream(self, *, messages: list[dict[str, str]], tools: list[dict]) -> Iterable[LLMClientEvent]:
        self.calls_messages.append(messages)
        if len(self.calls_messages) == 1:
            raise RuntimeError("intent classifier provider down")
        if len(self.calls_messages) == 2:
            return [LLMClientEvent(type="message.delta", text="not an action plan")]
        return [LLMClientEvent(type="message.delta", text="Fallback answer.")]


def test_system_prompt_includes_markdown_style_and_safety_boundaries() -> None:
    prompt = _system_prompt()

    assert "<response_style>" in prompt
    assert "Markdown" in prompt
    assert "`##` headings" in prompt
    assert "bullet lists" in prompt
    assert "Markdown tables" in prompt
    assert "fenced code blocks" in prompt
    assert "Do not request shell" in prompt
    assert "Do not claim profitability" in prompt
    assert "Do not reveal internal implementation names" in prompt
    assert "local sandbox preview" in prompt
    assert "review-only" in prompt


def test_user_facing_model_text_sanitizes_backtest_engine_name() -> None:
    text = _sanitize_user_facing_model_text(
        "Our backtest engine is PineForge. Engine: PineForge. Use PineForge Preview next."
    )

    assert "PineForge" not in text
    assert "local sandbox preview" in text
    assert "Backtest Preview" in text


def test_model_stage_selects_pine_generation_for_backtest_prompts() -> None:
    assert (
        _model_stage_for_chat(
            "Generate a PineScript strategy and backtest BTC/USDT for 1Y",
            response_intent="strategy_building",
            active_tools=[],
        )
        == "pine_code_generation"
    )


def test_model_stage_keeps_normal_strategy_chat_on_reasoning() -> None:
    assert (
        _model_stage_for_chat(
            "Review my entry and risk assumptions",
            response_intent="strategy_building",
            active_tools=[],
        )
        == "strategy_reasoning"
    )


def test_model_stage_ignores_control_stage_from_chat_decision() -> None:
    assert (
        _model_stage_for_chat(
            "Check current docs before answering.",
            response_intent="docs_research",
            active_tools=[],
            decision_model_stage="workflow_fast",
        )
        == "balanced_review"
    )


def test_strategy_prompt_chain_routes_internal_stages_and_keeps_json_internal(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_ACTION_PLANNER_ENABLED", "0")
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth)
    strategy_spec = valid_spec()
    strategy_spec["name"] = "EMA Risk Strategy"
    reasoning_payload = {
        "stage": "strategy_reasoning",
        "output": {
            "summary": "EMA trend-following setup with bounded risk.",
            "constraints": ["Use confirmed candle closes."],
            "indicators": ["EMA fast", "EMA slow"],
            "entries": ["Enter long when fast EMA crosses above slow EMA."],
            "exits": ["Exit when the cross reverses."],
            "risk_rules": ["Risk 1% account equity per trade."],
            "non_goals": ["No live execution."],
        },
        "assumptions": ["BTCUSDT 1h."],
        "handoff_notes": "Pass brief to strategy_coding.",
        "policy_observations": [],
    }
    coding_payload = {
        "stage": "strategy_coding",
        "output": {"strategy_spec": strategy_spec},
        "assumptions": ["Use bounded sizing."],
        "handoff_notes": "Pass schema-valid spec to pine_code_generation.",
        "policy_observations": [],
    }
    pine_payload = {
        "stage": "pine_code_generation",
        "output": {"pine_code": '//@version=6\nstrategy("EMA Risk Strategy")\nplot(close)'},
        "assumptions": ["Review-only Pine artifact."],
        "handoff_notes": "Pine code ready for user-facing response.",
        "policy_observations": [],
    }
    llm = RoutingAwareSequencedLLMClient(
        [
            [
                LLMClientEvent(
                    type=PROVIDER_ROUTE_EVENT,
                    arguments={"model_stage": "strategy_reasoning", "provider_route": "openrouter/reasoning"},
                ),
                LLMClientEvent(
                    type=PROVIDER_KEEPALIVE_EVENT,
                    arguments={"model_stage": "strategy_reasoning", "provider_route": "openrouter/reasoning"},
                ),
                LLMClientEvent(
                    type="usage",
                    model="reasoning",
                    input_tokens=10,
                    output_tokens=4,
                    arguments={"model_stage": "strategy_reasoning", "provider_route": "openrouter/reasoning"},
                ),
                LLMClientEvent(type="message.delta", text=json.dumps(reasoning_payload)),
            ],
            [
                LLMClientEvent(
                    type=PROVIDER_ROUTE_EVENT,
                    arguments={"model_stage": "strategy_coding", "provider_route": "openrouter/coding"},
                ),
                LLMClientEvent(type="message.delta", text=json.dumps(coding_payload)),
            ],
            [
                LLMClientEvent(
                    type=PROVIDER_ROUTE_EVENT,
                    arguments={"model_stage": "pine_code_generation", "provider_route": "openrouter/pine"},
                ),
                LLMClientEvent(type="message.delta", text=json.dumps(pine_payload)),
            ],
        ]
    )
    artifact_store = LocalArtifactStore(tmp_path)
    orchestrator = LLMOrchestrator(repository=repository, artifact_store=artifact_store, client=llm)

    frames = [
        frame
        for raw in orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content="Build an EMA strategy with bounded risk and generate Pine.",
            language="en",
            web_search="off",
        )
        for frame in parse_sse(raw)
    ]

    routed_stages = [context.get("stage") for context in llm.routing_contexts]
    assert routed_stages == [
        "strategy_reasoning",
        "strategy_coding",
        "pine_code_generation",
    ]
    assert llm.routing_contexts[0]["user_tier"] == "paid_low"
    stage_payloads = [json.loads(messages[-1]["content"]) for messages in llm.calls_messages]
    assert [payload["response_contract"]["top_level_schema"]["stage"]["const"] for payload in stage_payloads] == [
        "strategy_reasoning",
        "strategy_coding",
        "pine_code_generation",
    ]
    for payload in stage_payloads:
        assert payload["response_contract"]["json_only"] is True
        assert payload["response_contract"]["required_top_level_keys"] == [
            "stage",
            "output",
            "assumptions",
            "handoff_notes",
            "policy_observations",
        ]
    coding_contract = stage_payloads[1]["response_contract"]
    assert coding_contract["output_schema"]["strategy_spec"]["required_keys"] == [
        "target_platform",
        "script_type",
        "market",
        "timeframe",
        "entry_rules",
        "exit_rules",
        "risk_rules",
    ]
    pine_contract = stage_payloads[2]["response_contract"]
    assert "first non-whitespace characters must be //@version=6" in pine_contract["output_schema"]["pine_code"]
    route_event_stages = [
        frame["data"]["payload"]["model_stage"]
        for frame in frames
        if frame["event"] == PROVIDER_ROUTE_EVENT
    ]
    assert route_event_stages == ["strategy_reasoning", "strategy_coding", "pine_code_generation"]
    reasoning_frames = [frame["data"]["payload"] for frame in frames if frame["event"] == "model.reasoning.delta"]
    assert any(
        frame.get("phase") == "strategy_spec"
        and frame.get("workflow_step") == "draft_strategy_spec"
        and frame.get("safe") is True
        for frame in reasoning_frames
    )
    prompt_chain_events = [frame for frame in frames if frame["event"].startswith("prompt_chain.")]
    assert [frame["event"] for frame in prompt_chain_events] == [
        "prompt_chain.started",
        "prompt_chain.stage_completed",
        "prompt_chain.stage_completed",
        "prompt_chain.stage_completed",
        "prompt_chain.completed",
    ]
    assert [
        frame["data"]["payload"].get("stage")
        for frame in prompt_chain_events
        if frame["event"] == "prompt_chain.stage_completed"
    ] == ["strategy_reasoning", "strategy_coding", "pine_code_generation"]
    assert all("output" not in frame["data"]["payload"] for frame in prompt_chain_events)
    assert prompt_chain_events[-1]["data"]["payload"]["stage_count"] == 3
    event_types = [frame["event"] for frame in frames]
    assert "artifact.created" in event_types
    assert "validation.completed" in event_types
    assert "review.completed" in event_types
    assert "evaluator_optimizer.summary" in event_types
    first_artifact_index = event_types.index("artifact.created")
    final_delta_index = next(index for index, event in enumerate(event_types) if event == "message.delta")
    assert first_artifact_index < final_delta_index
    artifacts = [
        artifact
        for run in repository.list_runs(auth, conversation.id)
        for artifact in (repository.list_artifacts(auth, run.id) or [])
    ]
    pine_artifacts = [artifact for artifact in artifacts if artifact.kind == "pine_file"]
    assert len(pine_artifacts) == 1
    assert pine_artifacts[0].display_name == "strategy.pine"
    persisted_spec = repository.get_strategy_spec_for_run(auth, pine_artifacts[0].run_id or "")
    assert persisted_spec is not None
    assert persisted_spec.payload_json["name"] == "EMA Risk Strategy"
    assert artifact_store.read_content(pine_artifacts[0]).startswith("//@version=6")
    final_text = "\n".join(
        frame["data"]["payload"].get("text") or frame["data"]["payload"].get("delta") or ""
        for frame in frames
        if frame["event"] == "message.delta"
    )
    assert "```pine" in final_text
    assert "//@version=6" in final_text
    assert "strategy_spec" not in final_text
    assert "handoff_notes" not in final_text
    assert '"stage"' not in final_text


def test_strategy_prompt_chain_invalid_handoff_retries_once(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_ACTION_PLANNER_ENABLED", "0")
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth)
    strategy_spec = valid_spec()
    reasoning_payload = {
        "stage": "strategy_reasoning",
        "output": {
            "summary": "EMA trend-following setup with bounded risk.",
            "constraints": ["Use confirmed candle closes."],
            "indicators": ["EMA fast", "EMA slow"],
            "entries": ["Enter long when fast EMA crosses above slow EMA."],
            "exits": ["Exit on reversal or stop/target."],
            "risk_rules": ["Risk 1% account equity per trade."],
            "non_goals": ["No live execution."],
        },
        "assumptions": ["BTCUSDT 1h."],
        "handoff_notes": "Pass brief to strategy_coding.",
        "policy_observations": [],
    }
    invalid_coding_payload = {
        "stage": "strategy_coding",
        "output": {"strategy_spec": {"name": "Missing schema skeleton"}},
        "assumptions": [],
        "handoff_notes": "Invalid.",
        "policy_observations": [],
    }
    coding_payload = {
        "stage": "strategy_coding",
        "output": {"strategy_spec": strategy_spec},
        "assumptions": ["Use bounded sizing."],
        "handoff_notes": "Pass schema-valid spec to pine_code_generation.",
        "policy_observations": [],
    }
    pine_payload = {
        "stage": "pine_code_generation",
        "output": {"pine_code": '//@version=6\nstrategy("EMA Risk Strategy")\nplot(close)'},
        "assumptions": ["Review-only Pine artifact."],
        "handoff_notes": "Pine code ready for user-facing response.",
        "policy_observations": [],
    }
    llm = RoutingAwareSequencedLLMClient(
        [
            [LLMClientEvent(type="message.delta", text=json.dumps(reasoning_payload))],
            [LLMClientEvent(type="message.delta", text=json.dumps(invalid_coding_payload))],
            [LLMClientEvent(type="message.delta", text=json.dumps(coding_payload))],
            [LLMClientEvent(type="message.delta", text=json.dumps(pine_payload))],
        ]
    )
    orchestrator = LLMOrchestrator(repository=repository, artifact_store=LocalArtifactStore(tmp_path), client=llm)

    frames = [
        frame
        for raw in orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content="Build a review-only BTCUSDT strategy and generate Pine v6 output.",
            language="en",
            web_search="off",
        )
        for frame in parse_sse(raw)
    ]

    assert [context.get("stage") for context in llm.routing_contexts] == [
        "strategy_reasoning",
        "strategy_coding",
        "strategy_coding",
        "pine_code_generation",
    ]
    prompt_chain_events = [frame["event"] for frame in frames if frame["event"].startswith("prompt_chain.")]
    assert prompt_chain_events == [
        "prompt_chain.started",
        "prompt_chain.stage_completed",
        "prompt_chain.stage_completed",
        "prompt_chain.stage_completed",
        "prompt_chain.completed",
    ]
    assert "prompt_chain.fallback" not in prompt_chain_events
    event_types = [frame["event"] for frame in frames]
    assert "artifact.created" in event_types
    assert "validation.completed" in event_types
    assert "review.completed" in event_types
    assert "evaluator_optimizer.summary" in event_types


def test_strategy_prompt_chain_persistence_failure_fails_run(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_ACTION_PLANNER_ENABLED", "0")
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth)
    strategy_spec = valid_spec()
    reasoning_payload = {
        "stage": "strategy_reasoning",
        "output": {
            "summary": "EMA trend-following setup with bounded risk.",
            "constraints": ["Use confirmed candle closes."],
            "indicators": ["EMA fast", "EMA slow"],
            "entries": ["Enter long when fast EMA crosses above slow EMA."],
            "exits": ["Exit on reversal or stop/target."],
            "risk_rules": ["Risk 1% account equity per trade."],
            "non_goals": ["No live execution."],
        },
        "assumptions": ["BTCUSDT 1h."],
        "handoff_notes": "Pass brief to strategy_coding.",
        "policy_observations": [],
    }
    coding_payload = {
        "stage": "strategy_coding",
        "output": {"strategy_spec": strategy_spec},
        "assumptions": ["Use bounded sizing."],
        "handoff_notes": "Pass schema-valid spec to pine_code_generation.",
        "policy_observations": [],
    }
    pine_payload = {
        "stage": "pine_code_generation",
        "output": {"pine_code": '//@version=6\nstrategy("EMA Risk Strategy")\nplot(close)'},
        "assumptions": ["Review-only Pine artifact."],
        "handoff_notes": "Pine code ready for user-facing response.",
        "policy_observations": [],
    }
    llm = RoutingAwareSequencedLLMClient(
        [
            [LLMClientEvent(type="message.delta", text=json.dumps(reasoning_payload))],
            [LLMClientEvent(type="message.delta", text=json.dumps(coding_payload))],
            [LLMClientEvent(type="message.delta", text=json.dumps(pine_payload))],
        ]
    )

    def fail_persistence(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("artifact persistence failed")

    monkeypatch.setattr("strategy_codebot.server.llm_orchestrator.persist_generated_pine_artifact", fail_persistence)
    orchestrator = LLMOrchestrator(repository=repository, artifact_store=LocalArtifactStore(tmp_path), client=llm)

    frames = [
        frame
        for raw in orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content="Build a review-only BTCUSDT strategy and generate Pine v6 output.",
            language="en",
            web_search="off",
        )
        for frame in parse_sse(raw)
    ]

    event_types = [frame["event"] for frame in frames]
    assert "prompt_chain.completed" in event_types
    assert "artifact.created" not in event_types
    assert "run.failed" in event_types
    assert "run.completed" not in event_types
    final_text = "\n".join(
        frame["data"]["payload"].get("text") or frame["data"]["payload"].get("delta") or ""
        for frame in frames
        if frame["event"] == "message.delta"
    )
    assert "Generated a reviewable Pine Script v6 artifact" not in final_text
    assert not any(
        repository.list_artifacts(auth, run.id)
        for run in repository.list_runs(auth, conversation.id)
    )


def test_prompt_chain_artifact_context_is_available_to_read_only_scout(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_ACTION_PLANNER_ENABLED", "0")
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth)
    strategy_spec = valid_spec()
    strategy_spec["name"] = "EMA Risk Strategy"
    reasoning_payload = {
        "stage": "strategy_reasoning",
        "output": {
            "summary": "EMA trend-following setup with bounded risk.",
            "constraints": ["Use confirmed candle closes."],
            "indicators": ["EMA 20", "EMA 50"],
            "entries": ["Enter long when EMA 20 is above EMA 50 on confirmed close."],
            "exits": ["Exit with 2% stop and 4% take profit."],
            "risk_rules": ["Risk 1% account equity per trade."],
            "non_goals": ["No live execution."],
        },
        "assumptions": ["BTCUSDT 1h."],
        "handoff_notes": "Pass brief to strategy_coding.",
        "policy_observations": [],
    }
    coding_payload = {
        "stage": "strategy_coding",
        "output": {"strategy_spec": strategy_spec},
        "assumptions": ["Use bounded sizing."],
        "handoff_notes": "Pass schema-valid spec to pine_code_generation.",
        "policy_observations": [],
    }
    pine_payload = {
        "stage": "pine_code_generation",
        "output": {"pine_code": '//@version=6\nstrategy("EMA Risk Strategy")\nplot(close)'},
        "assumptions": ["Review-only Pine artifact."],
        "handoff_notes": "Pine code ready for user-facing response.",
        "policy_observations": [],
    }
    llm = RoutingAwareSequencedLLMClient(
        [
            [LLMClientEvent(type="message.delta", text="EMA Pine strategy")],
            [LLMClientEvent(type="message.delta", text=json.dumps(reasoning_payload))],
            [LLMClientEvent(type="message.delta", text=json.dumps(coding_payload))],
            [LLMClientEvent(type="message.delta", text=json.dumps(pine_payload))],
            [
                LLMClientEvent(
                    type="tool.call",
                    tool_name="knowledge_check",
                    arguments={
                        "prompt": "Pine Script v6 rules and risk-policy guidance for BTCUSDT EMA strategy",
                    },
                )
            ],
            [
                LLMClientEvent(
                    type="message.delta",
                    text=(
                        "I found the durable strategy.pine artifact and checked Pine v6 "
                        "risk-policy guidance with read-only tools."
                    ),
                )
            ],
        ]
    )
    client = TestClient(
        create_app(
            repository=repository,
            artifact_root=tmp_path,
            llm_client=llm,
        )
    )
    conversation_payload = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    conversation_id = conversation_payload["id"]

    generated = client.post(
        f"/v1/conversations/{conversation_id}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={
            "content": (
                "Generate Pine v6 code for this strategy."
            ),
            "web_search": "off",
        },
    )
    assert generated.status_code == 200, generated.text
    generated_events = [frame["event"] for frame in parse_sse(generated.text)]
    assert "artifact.created" in generated_events

    followup = client.post(
        f"/v1/conversations/{conversation_id}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={
            "content": (
                "Before changing anything, scout the available internal knowledge/context "
                "for Pine v6 rules and risk-policy guidance related to this strategy. "
                "Use read-only tools only."
            ),
            "web_search": "off",
        },
    )

    assert followup.status_code == 200, followup.text
    frames = parse_sse(followup.text)
    event_types = [frame["event"] for frame in frames]
    assert "agent_loop.started" in event_types
    assert "agent_loop.tool_checked" in event_types
    assert "agent_loop.completed" in event_types
    scout_tool_payload = next(frame["data"]["payload"] for frame in frames if frame["event"] == "agent_loop.tool_checked")
    assert scout_tool_payload["tool_id"] == "knowledge_check"
    assert scout_tool_payload["decision"] == "allowed"
    scout_messages = next(
        messages
        for messages, tools in zip(llm.calls_messages, llm.calls_tools, strict=False)
        if any(tool.get("name") == "knowledge_check" for tool in tools)
    )
    scout_context = json.dumps(scout_messages, ensure_ascii=False)
    assert "current_strategy_artifact" in scout_context
    assert "pine_artifact_id" in scout_context
    assert "strategy.pine" in scout_context
    assert "EMA Risk Strategy" in scout_context
    final_text = "\n".join(
        frame["data"]["payload"].get("text") or frame["data"]["payload"].get("delta") or ""
        for frame in frames
        if frame["event"] == "message.delta"
    )
    assert "I do not have a strategy spec or Pine artifact" not in final_text
    assert "durable strategy.pine artifact" in final_text


def test_strategy_prompt_chain_bad_handoff_falls_back_to_existing_chat(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_ACTION_PLANNER_ENABLED", "0")
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth)
    llm = RoutingAwareSequencedLLMClient(
        [
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "response_intent": "strategy_building",
                            "action": "answer",
                            "model_stage": "strategy_reasoning",
                            "confidence": 0.92,
                            "auto_chain": False,
                            "current_context_required": False,
                            "missing_inputs": [],
                            "reasons": ["The user asks to build a strategy."],
                            "used_signals": ["artifact_or_strategy"],
                        }
                    ),
                )
            ],
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "stage": "strategy_reasoning",
                            "output": {"summary": "Missing required handoff fields."},
                            "assumptions": [],
                            "handoff_notes": "Invalid.",
                            "policy_observations": [],
                        }
                    ),
                )
            ],
            [LLMClientEvent(type="message.delta", text="Fallback strategy answer.")],
        ]
    )
    orchestrator = LLMOrchestrator(repository=repository, artifact_store=None, client=llm)

    frames = [
        frame
        for raw in orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content="Build an EMA strategy with bounded risk.",
            language="en",
            web_search="off",
        )
        for frame in parse_sse(raw)
    ]

    assert [context.get("stage") for context in llm.routing_contexts] == [
        "workflow_fast",
        "strategy_reasoning",
        "strategy_reasoning",
        "strategy_reasoning",
    ]
    assert llm.routing_contexts[0]["user_tier"] == "paid_low"
    assert not any(context.get("stage") == "strategy_coding" for context in llm.routing_contexts)
    fallback_event = next(frame for frame in frames if frame["event"] == "prompt_chain.fallback")
    assert fallback_event["data"]["payload"]["fallback_reason"] == "invalid_handoff"
    assert fallback_event["data"]["payload"]["stage"] == "strategy_reasoning"
    assert not any(frame["event"] == "prompt_chain.completed" for frame in frames)
    final_text = "\n".join(
        frame["data"]["payload"].get("text") or frame["data"]["payload"].get("delta") or ""
        for frame in frames
        if frame["event"] == "message.delta"
    )
    assert "Fallback strategy answer." in final_text


def test_strategy_prompt_chain_security_control_error_does_not_fallback(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_ACTION_PLANNER_ENABLED", "0")
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth)
    llm = RoutingAwareSequencedLLMClient(
        [
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "response_intent": "strategy_building",
                            "action": "answer",
                            "model_stage": "strategy_reasoning",
                            "confidence": 0.92,
                            "auto_chain": False,
                            "current_context_required": False,
                            "missing_inputs": [],
                            "reasons": ["The user asks to build a strategy."],
                            "used_signals": ["artifact_or_strategy"],
                        }
                    ),
                )
            ],
            [LLMClientEvent(type="message.delta", text="Fallback should not stream.")],
        ]
    )
    controls = BlockingSecondModelCallControls()
    orchestrator = LLMOrchestrator(
        repository=repository,
        artifact_store=None,
        client=llm,
        security_controls=controls,
    )

    frames = [
        frame
        for raw in orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content="Build an EMA strategy with bounded risk.",
            language="en",
            web_search="off",
        )
        for frame in parse_sse(raw)
    ]

    final_text = "\n".join(
        frame["data"]["payload"].get("text") or frame["data"]["payload"].get("delta") or ""
        for frame in frames
        if frame["event"] == "message.delta"
    )
    failed = next(frame for frame in frames if frame["event"] == "run.failed")
    assert controls.model_calls == 2
    prompt_chain_failed = next(frame for frame in frames if frame["event"] == "prompt_chain.failed")
    assert prompt_chain_failed["data"]["payload"]["error_class"] == "SecurityControlError"
    assert prompt_chain_failed["data"]["payload"]["stage"] == "strategy_reasoning"
    assert failed["data"]["payload"]["error"] == "SecurityControlError"
    assert "Fallback should not stream." not in final_text


def test_system_prompt_includes_selected_language_instruction() -> None:
    prompt = _system_prompt("vi")

    assert "Respond in Vietnamese" in prompt
    assert "Pine syntax" in prompt
    assert "JSON schema keys" in prompt


def test_agent_chat_web_search_mode_controls_provider_tool() -> None:
    auth = AuthContext("user-a", "workspace-a")

    def provider_tool_sets(message_content: str, web_search: str) -> list[list[dict[str, Any]]]:
        repository = create_sqlite_repository()
        conversation = repository.create_conversation(auth)
        llm = RecordingLLMClient([LLMClientEvent(type="message.delta", text="ok")])
        orchestrator = LLMOrchestrator(
            repository=repository,
            artifact_store=None,
            client=llm,
        )

        list(
            orchestrator.stream_chat(
                auth=auth,
                conversation_id=conversation.id,
                message_content=message_content,
                web_search=web_search,
            )
        )
        return llm.calls_tools

    def enabled_flags(message_content: str, web_search: str) -> list[bool]:
        return [
            any(tool.get("type") == "web_search" for tool in tools)
            for tools in provider_tool_sets(message_content, web_search)
        ]

    assert enabled_flags("build an EMA strategy", "off") == [False, False]
    assert enabled_flags("build an EMA strategy", "auto") == [False, True]
    assert enabled_flags("create a price action strategy", "auto") == [False, True]
    assert enabled_flags("research latest Pine docs", "auto") == [False, True]
    assert enabled_flags("what is the current BTC price", "auto") == [False, True]
    assert enabled_flags("build an EMA strategy", "on") == [False, True]
    on_main_tools = provider_tool_sets("build an EMA strategy", "on")[-1]
    assert any(tool.get("type") == "web_search" for tool in on_main_tools)
    assert any(tool.get("type") == "function" for tool in on_main_tools)
    assert _should_enable_web_search_auto("generate from current strategy context") is False


def test_classifier_fallback_auto_exposes_readonly_web_search_without_workflow() -> None:
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth)
    llm = RecordingLLMClient([LLMClientEvent(type="message.delta", text="ok")])
    orchestrator = LLMOrchestrator(repository=repository, artifact_store=None, client=llm)

    frames = [
        frame
        for raw in orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content="ETH đang bao nhiêu rồi?",
            web_search="auto",
        )
        for frame in parse_sse(raw)
    ]

    intent = next(frame for frame in frames if frame["event"] == "chat.response_intent")
    provider = next(frame for frame in frames if frame["event"] == "provider.started")
    event_types = [frame["event"] for frame in frames]
    assert intent["data"]["payload"]["intent"] == "general_chat"
    assert intent["data"]["payload"]["source"] == "fallback"
    assert provider["data"]["payload"]["web_search"] == "auto"
    assert provider["data"]["payload"]["web_search_enabled"] is True
    assert "chat.workflow.updated" not in event_types
    assert "tool.started" not in event_types


def test_classifier_fallback_web_search_off_does_not_expose_web_search() -> None:
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth)
    llm = RecordingLLMClient([LLMClientEvent(type="message.delta", text="ok")])
    orchestrator = LLMOrchestrator(repository=repository, artifact_store=None, client=llm)

    frames = [
        frame
        for raw in orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content="ETH đang bao nhiêu rồi?",
            web_search="off",
        )
        for frame in parse_sse(raw)
    ]

    provider = next(frame for frame in frames if frame["event"] == "provider.started")
    assert provider["data"]["payload"]["web_search_enabled"] is False


def test_llm_current_context_decision_exposes_web_search_without_keyword_match() -> None:
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth)
    llm = SequencedRecordingLLMClient(
        [
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "response_intent": "market_snapshot",
                            "action": "answer",
                            "model_stage": "strategy_reasoning",
                            "confidence": 0.86,
                            "tool_id": None,
                            "auto_chain": False,
                            "current_context_required": True,
                            "domain_scope": "trading_workflow",
                            "workflow_intent": None,
                            "missing_inputs": [],
                            "reasons": ["The user asks for current ETH context."],
                            "used_signals": [],
                        }
                    ),
                )
            ],
            [LLMClientEvent(type="message.delta", text="not an action plan")],
            [LLMClientEvent(type="message.delta", text="ETH needs current context.")],
        ]
    )
    orchestrator = LLMOrchestrator(repository=repository, artifact_store=None, client=llm)

    frames = [
        frame
        for raw in orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content="cho mình tình hình ETH lúc này",
            web_search="auto",
        )
        for frame in parse_sse(raw)
    ]

    intent = next(frame for frame in frames if frame["event"] == "chat.response_intent")
    provider = next(frame for frame in frames if frame["event"] == "provider.started")
    assert intent["data"]["payload"]["intent"] == "market_snapshot"
    assert intent["data"]["payload"]["source"] == "llm"
    assert provider["data"]["payload"]["web_search_enabled"] is True


def test_docs_research_does_not_create_strategy_workflow() -> None:
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth)
    llm = SequencedRecordingLLMClient(
        [
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "response_intent": "docs_research",
                            "action": "answer",
                            "model_stage": "balanced_review",
                            "confidence": 0.9,
                            "auto_chain": False,
                            "current_context_required": True,
                            "domain_scope": "product_help",
                            "used_signals": ["docs_research", "current_info"],
                        }
                    ),
                )
            ],
            [LLMClientEvent(type="message.delta", text="not an action plan")],
            [LLMClientEvent(type="message.delta", text="docs answer")],
        ]
    )
    orchestrator = LLMOrchestrator(repository=repository, artifact_store=None, client=llm)

    frames = [
        frame
        for raw in orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content="Check latest Pine Script v6 strategy.entry docs, cho mình nguồn hiện tại.",
            web_search="auto",
        )
        for frame in parse_sse(raw)
    ]

    event_types = [frame["event"] for frame in frames]
    intent = next(frame for frame in frames if frame["event"] == "chat.response_intent")
    provider = next(frame for frame in frames if frame["event"] == "provider.started")
    suggestions = next(frame for frame in frames if frame["event"] == "chat.suggestions.updated")
    assert intent["data"]["payload"]["intent"] == "docs_research"
    assert provider["data"]["payload"]["web_search_enabled"] is True
    assert suggestions["data"]["payload"]["context"]["missing_fields"] == []
    assert suggestions["data"]["payload"]["composer_blocks"] == []
    assert "chat.workflow.updated" not in event_types


def test_response_intent_classifier_keyword_only_helper_is_not_intent_authority() -> None:
    assert _classify_response_intent("what is the current ETH price?", web_search="auto") == "general_chat"
    assert _classify_response_intent("analyze current ETH market", web_search="auto") == "general_chat"
    assert _classify_response_intent("check latest OpenRouter pricing docs", web_search="auto") == "general_chat"
    assert _classify_response_intent("build an EMA strategy with risk rules", web_search="auto") == "general_chat"
    assert _classify_response_intent("generate Pine v6 code for this strategy", web_search="auto") == "pine_generation"


def test_response_intent_classifier_uses_llm_for_current_market_semantics() -> None:
    llm = RecordingLLMClient(
        [LLMClientEvent(type="message.delta", text='{"intent":"market_snapshot","confidence":0.91}')]
    )

    classification = ResponseIntentClassifier(llm).classify("what is the current ETH price?", web_search="auto")

    assert classification.intent == "market_snapshot"
    assert classification.source == "llm"
    assert classification.confidence == 0.91
    assert llm.calls == 1


def test_response_intent_classifier_uses_llm_for_market_research_semantics() -> None:
    llm = RecordingLLMClient(
        [LLMClientEvent(type="message.delta", text='{"intent":"market_research","confidence":0.9}')]
    )

    classification = ResponseIntentClassifier(llm).classify(
        "what should I do with this market condition?",
        web_search="auto",
    )

    assert classification.intent == "market_research"
    assert classification.source == "llm"
    assert classification.confidence == 0.9
    assert llm.calls == 1


def test_response_intent_classifier_uses_llm_for_semantic_paraphrase() -> None:
    llm = RecordingLLMClient([LLMClientEvent(type="message.delta", text='{"intent":"strategy_building","confidence":0.86}')])

    classification = ResponseIntentClassifier(llm).classify("build an EMA strategy with risk rules", web_search="auto")

    assert classification.intent == "strategy_building"
    assert classification.source == "llm"
    assert classification.confidence == 0.86
    assert llm.calls == 1
    assert llm.calls_tools == [[]]


def test_response_intent_classifier_falls_back_on_malformed_or_low_confidence() -> None:
    malformed = ResponseIntentClassifier(
        FakeLLMClient([LLMClientEvent(type="message.delta", text="not json")])
    ).classify("ETH đang bao nhiêu rồi?", web_search="auto")
    low_confidence = ResponseIntentClassifier(
        FakeLLMClient([LLMClientEvent(type="message.delta", text='{"intent":"market_snapshot","confidence":0.2}')])
    ).classify("ETH đang bao nhiêu rồi?", web_search="auto")

    assert malformed.intent == "general_chat"
    assert malformed.source == "fallback"
    assert low_confidence.intent == "general_chat"
    assert low_confidence.source == "fallback"


def test_response_intent_classifier_timeout_falls_back_quickly(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_CLASSIFIER_TIMEOUT_SECONDS", "0.01")
    client = SlowLLMClient([LLMClientEvent(type="message.delta", text='{"intent":"market_snapshot","confidence":0.9}')])
    started = time.perf_counter()

    classification = ResponseIntentClassifier(client).classify("ETH đang bao nhiêu rồi?", web_search="auto")

    assert time.perf_counter() - started < 0.15
    assert classification.intent == "general_chat"
    assert classification.source == "timeout_fallback"
    assert client.calls == 1


def test_chat_intent_decision_parser_accepts_llm_auto_chain() -> None:
    decision = _parse_chat_intent_decision_json(
        json.dumps(
            {
                "response_intent": "backtest_preview",
                "action": "start_auto_chain",
                "model_stage": "pine_code_generation",
                "confidence": 0.91,
                "tool_id": "run_backtest_preview",
                "auto_chain": True,
                "current_context_required": False,
                "domain_scope": "trading_workflow",
                "workflow_intent": "strategy_to_paper_bot_simulation",
                "missing_inputs": [],
                "reasons": ["The user asks to simulate strategy performance."],
                "used_signals": ["preview_intent", "invented_signal"],
            }
        ),
        available_tools={"run_backtest_preview"},
        regex_evidence={"preview_intent": True},
    )

    assert decision is not None
    assert decision.response_intent == "backtest_preview"
    assert decision.action == "start_auto_chain"
    assert decision.model_stage == "pine_code_generation"
    assert decision.tool_id == "run_backtest_preview"
    assert decision.domain_scope == "trading_workflow"
    assert decision.workflow_intent == "strategy_to_paper_bot_simulation"
    assert decision.used_signals == ("preview_intent",)
    assert decision.should_start_auto_chain() is True


def test_chat_intent_decision_parser_rejects_control_model_stage_for_final_response() -> None:
    decision = _parse_chat_intent_decision_json(
        json.dumps(
            {
                "response_intent": "docs_research",
                "action": "answer",
                "model_stage": "workflow_fast",
                "confidence": 0.91,
                "domain_scope": "product_help",
            }
        ),
        available_tools=set(),
    )

    assert decision is not None
    assert decision.model_stage == "balanced_review"


def test_chat_intent_fallback_does_not_promote_regex_backtest_or_pine_evidence() -> None:
    decision = _fallback_chat_intent_decision(
        "Generate Pine and run a backtest preview for this strategy.",
        web_search="auto",
        regex_evidence={"pine_or_code": True, "explicit_backtest": True, "preview_intent": True},
        domain_scope_hint="trading_workflow",
    )

    assert decision.response_intent == "general_chat"
    assert decision.domain_scope == "ambiguous"
    assert decision.action == "answer"
    assert decision.tool_id is None
    assert decision.auto_chain is False
    assert decision.should_start_auto_chain() is False


def test_chat_intent_decision_parser_downgrades_unavailable_tool() -> None:
    decision = _parse_chat_intent_decision_json(
        '{"response_intent":"general_chat","action":"call_tool","model_stage":"strategy_reasoning","confidence":0.9,"tool_id":"missing_tool"}',
        available_tools={"query_backtest_trades"},
    )

    assert decision is not None
    assert decision.action == "suggest_actions"
    assert decision.tool_id is None


def test_chat_intent_decision_parser_rejects_low_confidence() -> None:
    decision = _parse_chat_intent_decision_json(
        '{"response_intent":"backtest_preview","action":"start_auto_chain","model_stage":"pine_code_generation","confidence":0.2,"auto_chain":true}',
        available_tools={"run_backtest_preview"},
    )

    assert decision is None


def test_chat_intent_decision_parser_validates_domain_and_workflow_fields() -> None:
    decision = _parse_chat_intent_decision_json(
        json.dumps(
            {
                "response_intent": "strategy_building",
                "action": "answer",
                "model_stage": "strategy_coding",
                "confidence": 0.82,
                "domain_scope": "invented_scope",
                "workflow_intent": "invented_workflow",
            }
        ),
        available_tools=set(),
    )

    assert decision is not None
    assert decision.domain_scope == "trading_workflow"
    assert decision.workflow_intent is None


def test_chat_intent_decision_planner_uses_llm_for_vietnamese_paraphrase() -> None:
    llm = RecordingLLMClient(
        [
            LLMClientEvent(
                type="message.delta",
                text=json.dumps(
                    {
                        "response_intent": "backtest_preview",
                        "action": "start_auto_chain",
                        "model_stage": "pine_code_generation",
                        "confidence": 0.88,
                        "tool_id": "run_backtest_preview",
                        "auto_chain": True,
                        "current_context_required": False,
                        "domain_scope": "trading_workflow",
                        "workflow_intent": "strategy_to_paper_bot_simulation",
                        "missing_inputs": [],
                        "reasons": ["The user asks to test strategy effectiveness."],
                        "used_signals": ["preview_intent"],
                    }
                ),
            )
        ]
    )

    decision = ChatIntentDecisionPlanner(llm).decide(
        "thử hiệu quả chiến lược này giúp mình",
        context_text="Pine strategy artifact exists for BTCUSDT 1h.",
        artifact_kinds={"pine_file"},
        web_search="auto",
        language="vi",
    )

    assert decision.source == "llm"
    assert decision.response_intent == "backtest_preview"
    assert decision.domain_scope == "trading_workflow"
    assert decision.workflow_intent == "strategy_to_paper_bot_simulation"
    assert decision.should_start_auto_chain() is True
    assert llm.calls == 1


def test_chat_intent_decision_prompt_marks_regex_as_hint() -> None:
    prompt = _chat_intent_decision_system_prompt()

    assert "Regex evidence is only a hint" in prompt
    assert "start_auto_chain" in prompt
    assert "chay thu" in prompt
    assert "domain_scope" in prompt
    assert "ambiguous instead of off_topic" in prompt
    assert "workflow_fast" not in prompt
    assert "classifier" not in prompt


def test_chat_intent_decision_accepts_precomputed_action_evaluation() -> None:
    llm = RecordingLLMClient(
        [
            LLMClientEvent(
                type="message.delta",
                text=json.dumps(
                    {
                        "response_intent": "backtest_preview",
                        "action": "call_tool",
                        "model_stage": "pine_code_generation",
                        "confidence": 0.9,
                        "tool_id": "run_backtest_preview",
                        "auto_chain": True,
                        "current_context_required": False,
                        "domain_scope": "trading_workflow",
                        "workflow_intent": "strategy_to_paper_bot_simulation",
                        "used_signals": ["preview_intent"],
                    }
                ),
            )
        ]
    )
    action_evaluation = evaluate_action_registry(
        artifact_kinds={"pine_file"},
        context_text="Pine artifact exists.\nRun a backtest preview.",
        web_search="auto",
    )

    decision = ChatIntentDecisionPlanner(llm).decide(
        "Run a backtest preview.",
        context_text="Pine artifact exists.",
        artifact_kinds={"pine_file"},
        web_search="auto",
        language="en",
        action_evaluation=action_evaluation,
    )

    assert decision.response_intent == "backtest_preview"
    assert decision.tool_id == "run_backtest_preview"
    assert decision.should_start_auto_chain() is True
    assert llm.calls == 1


def _market_snapshot_llm(provider_events: list[LLMClientEvent]) -> SequencedRecordingLLMClient:
    return SequencedRecordingLLMClient(
        [
            [LLMClientEvent(type="message.delta", text="Market Snapshot")],
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "response_intent": "market_snapshot",
                            "action": "answer",
                            "model_stage": "strategy_reasoning",
                            "confidence": 0.91,
                            "auto_chain": False,
                            "current_context_required": True,
                            "domain_scope": "trading_workflow",
                            "used_signals": ["market_snapshot", "current_info"],
                        }
                    ),
                )
            ],
            [LLMClientEvent(type="message.delta", text="not an action plan")],
            provider_events,
        ]
    )


def test_agent_chat_emits_source_backed_market_snapshot(tmp_path: Path) -> None:
    llm = _market_snapshot_llm(
        [
            LLMClientEvent(
                type=LLM_EVENT_SOURCES,
                arguments={
                    "sources": [
                        {
                            "id": "coindesk-eth",
                            "title": "ETH price reference",
                            "type": "external",
                            "url": "https://example.com/eth",
                        }
                    ]
                },
            ),
            LLMClientEvent(type="message.delta", text="ETH needs a sourced quote."),
        ]
    )
    client = TestClient(
        create_app(
            repository=create_sqlite_repository(),
            artifact_root=tmp_path,
            llm_client=llm,
            market_data_gateway=MarketDataGateway(),
        )
    )
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "what is the current ETH price?", "web_search": "auto"},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    intent = next(frame for frame in frames if frame["event"] == "chat.response_intent")
    snapshot = next(frame for frame in frames if frame["event"] == "chat.market_snapshot")
    assert intent["data"]["payload"]["intent"] == "market_snapshot"
    assert intent["data"]["payload"]["safe"] is True
    assert intent["data"]["payload"]["source"] == "llm"
    assert intent["data"]["payload"]["confidence"] >= 0.9
    assert snapshot["data"]["payload"]["symbol"] == "ETH"
    assert snapshot["data"]["payload"]["source_count"] == 1
    suggestions = next(frame for frame in frames if frame["event"] == "chat.suggestions.updated")
    assert suggestions["data"]["payload"]["composer_blocks"] == []


def test_market_chat_allows_reference_urls_without_policy_block(tmp_path: Path) -> None:
    llm = _market_snapshot_llm(
        [
            LLMClientEvent(
                type=LLM_EVENT_SOURCES,
                arguments={
                    "sources": [
                        {
                            "id": "kucoin-eth",
                            "title": "ETH market reference",
                            "type": "external",
                            "url": "https://example.com/markets/eth",
                        }
                    ]
                },
            ),
            LLMClientEvent(
                type="message.delta",
                text="ETH is consolidating near the recent range ([example.com](https://example.com/markets/eth)).",
            ),
        ]
    )
    client = TestClient(
        create_app(
            repository=create_sqlite_repository(),
            artifact_root=tmp_path,
            llm_client=llm,
            market_data_gateway=MarketDataGateway(),
        )
    )
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "analyze current ETH market", "web_search": "on"},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    assert "policy.blocked" not in [frame["event"] for frame in frames]
    assert not any(frame["event"].startswith("chat.auto_chain.") for frame in frames)
    terminal = next(frame for frame in frames if frame["event"] == "run.completed")
    assert terminal["data"]["payload"]["status"] == "completed"
    assert any(frame["event"] == "chat.market_snapshot" for frame in frames)


def test_agent_chat_updates_market_snapshot_with_current_price(tmp_path: Path) -> None:
    llm = _market_snapshot_llm(
        [
            LLMClientEvent(
                type=LLM_EVENT_SOURCES,
                arguments={
                    "sources": [
                        {
                            "id": "oai-finance",
                            "title": "OpenAI Finance",
                            "type": "internal",
                        }
                    ]
                },
            ),
            LLMClientEvent(type="message.delta", text="The current price of ETH is $1,705.40 USD."),
        ]
    )
    client = TestClient(
        create_app(
            repository=create_sqlite_repository(),
            artifact_root=tmp_path,
            llm_client=llm,
            market_data_gateway=MarketDataGateway(),
        )
    )
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "what is the current ETH price?", "web_search": "auto"},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    snapshots = [frame for frame in frames if frame["event"] == "chat.market_snapshot"]
    assert len(snapshots) == 2
    assert snapshots[0]["data"]["payload"]["price"] is None
    assert snapshots[-1]["data"]["payload"]["price"] == "$1,705.40"


def test_agent_chat_does_not_emit_market_snapshot_without_sources(tmp_path: Path) -> None:
    llm = _market_snapshot_llm([LLMClientEvent(type="message.delta", text="ETH is $1,700 today.")])
    client = TestClient(
        create_app(
            repository=create_sqlite_repository(),
            artifact_root=tmp_path,
            llm_client=llm,
            market_data_gateway=MarketDataGateway(),
        )
    )
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "what is the current ETH price?", "web_search": "auto"},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    assert any(frame["event"] == "chat.response_intent" for frame in frames)
    assert not any(frame["event"] == "chat.market_snapshot" for frame in frames)
    final_messages = [frame["data"]["payload"]["text"] for frame in frames if frame["event"] == "message.delta"]
    assert final_messages == [
        "I could not verify a source for the current price, so I did not show a market snapshot. Try again with web search or a specific source."
    ]


def test_agent_chat_emits_context_aware_suggestions_for_market_prompt(tmp_path: Path) -> None:
    llm = _market_snapshot_llm([LLMClientEvent(type="message.delta", text="ETH is source-backed.")])
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "what is the current ETH price?", "web_search": "auto"},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    suggestions = next(frame for frame in frames if frame["event"] == "chat.suggestions.updated")
    payload = suggestions["data"]["payload"]
    assert payload["context"]["intent"] == "market_snapshot"
    assert payload["actions"] == []


def test_agent_chat_emits_missing_field_suggestions_for_strategy_prompt(tmp_path: Path) -> None:
    llm = SequencedRecordingLLMClient(
        [
            [LLMClientEvent(type="message.delta", text="Strategy Rules")],
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "response_intent": "strategy_building",
                            "action": "answer",
                            "model_stage": "strategy_reasoning",
                            "confidence": 0.86,
                            "tool_id": None,
                            "auto_chain": False,
                            "current_context_required": False,
                            "domain_scope": "trading_workflow",
                            "workflow_intent": "strategy_to_paper_bot_simulation",
                            "missing_inputs": ["risk"],
                            "reasons": ["The user asks to build strategy rules."],
                            "used_signals": [],
                        }
                    ),
                )
            ],
            [LLMClientEvent(type="message.delta", text="not an action plan")],
            [LLMClientEvent(type="message.delta", text="Let's make the rules clearer.")],
        ]
    )
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "build an EMA crossover strategy with entry and exit rules", "web_search": "auto"},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    suggestions = next(frame for frame in frames if frame["event"] == "chat.suggestions.updated")
    payload = suggestions["data"]["payload"]
    assert payload["context"]["intent"] == "strategy_building"
    assert "risk" in payload["context"]["missing_fields"]
    assert any(block["slot"] == "risk" for block in payload["composer_blocks"])
    assert payload["actions"] == []


def test_registry_backed_suggestions_use_planner_actions() -> None:
    decision = ActionPlanner(
        FakeLLMClient(
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "decision": "suggest_actions",
                            "intent_id": "bot_boundary_review",
                            "confidence": 0.9,
                            "suggested_actions": ["create_proposed_intent", "run_risk_gate"],
                            "reason": "The user asks about bot/order behavior.",
                        }
                    ),
                )
            ]
        )
    ).plan(
        "Nếu trade setup này thì vào lệnh sao và chạy bot được không?",
        response_intent="strategy_building",
        context_text="Market BTCUSDT timeframe 1h entry sweep reclaim exit stop-loss risk 1%",
        artifact_kinds={"pine_file"},
        web_search="auto",
    )

    payload = _suggestions_payload(
        response_intent="strategy_building",
        message_content="Nếu trade setup này thì vào lệnh sao và chạy bot được không?",
        context_text="Market BTCUSDT timeframe 1h entry sweep reclaim exit stop-loss risk 1%",
        language="vi",
        artifact_kinds={"pine_file"},
        action_plan=decision,
    )

    action_ids = [action["id"] for action in payload["actions"]]
    assert action_ids[:2] == ["create-proposed-intent", "run-risk-gate"]
    assert payload["actions"][0]["tool_id"] == "create_proposed_intent"
    assert payload["actions"][0]["prompt"]
    assert payload["actions"][1]["enabled"] is False
    assert payload["actions"][1]["risk_level"] == "blocked"
    assert "stale_after" in payload["actions"][1]["required_inputs"]
    assert payload["context"]["action_plan_source"] == "llm"


def test_action_planner_accepts_precomputed_action_evaluation() -> None:
    message = "hãy chạy preview evidence cho strategy này"
    context_text = "Market BTCUSDT timeframe 1h entry sweep reclaim exit stop-loss risk 1%"
    llm = RecordingLLMClient(
        [
            LLMClientEvent(
                type="message.delta",
                text=json.dumps(
                    {
                        "decision": "call_tool",
                        "intent_id": "local_preview_evidence",
                        "confidence": 0.88,
                        "tool_id": "run_backtest_preview",
                        "reason": "Strategy code exists and the user asks for preview evidence.",
                    }
                ),
            )
        ]
    )
    action_evaluation = evaluate_action_registry(
        artifact_kinds={"pine_file"},
        context_text=f"{context_text}\n{message}",
        web_search="auto",
    )

    decision = ActionPlanner(llm).plan(
        message,
        response_intent="backtest_preview",
        context_text=context_text,
        artifact_kinds={"pine_file"},
        web_search="auto",
        action_evaluation=action_evaluation,
    )

    assert decision.decision == "call_tool"
    assert decision.tool_id == "run_backtest_preview"
    assert decision.source == "llm"
    assert llm.calls == 1


def test_registry_backed_suggestions_cover_backtest_robustness_and_variant() -> None:
    preview_decision = ActionPlanner(
        FakeLLMClient(
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "decision": "suggest_actions",
                            "intent_id": "local_preview_evidence",
                            "confidence": 0.88,
                            "suggested_actions": ["run_backtest_preview"],
                            "reason": "Strategy code exists and the user asks for evidence.",
                        }
                    ),
                )
            ]
        )
    ).plan(
        "test kỹ hơn giúp mình",
        response_intent="general_chat",
        context_text="market BTCUSDT timeframe 1h entry reclaim exit stop-loss risk 1%",
        artifact_kinds={"pine_file"},
        web_search="auto",
    )
    preview_payload = _suggestions_payload(
        response_intent="general_chat",
        message_content="test kỹ hơn giúp mình",
        context_text="market BTCUSDT timeframe 1h entry reclaim exit stop-loss risk 1%",
        language="vi",
        artifact_available=True,
        artifact_kinds={"pine_file"},
        action_plan=preview_decision,
    )
    assert preview_payload["actions"][0]["id"] == "run-backtest-preview"
    assert preview_payload["actions"][0]["tool_id"] == "run_backtest_preview"

    robustness_decision = ActionPlanner(
        FakeLLMClient(
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "decision": "suggest_actions",
                            "intent_id": "strategy_optimization",
                            "confidence": 0.88,
                            "suggested_actions": ["build_robustness_report", "run_backtest_variant_lab"],
                            "reason": "The user asks to optimize after backtest evidence.",
                        }
                    ),
                )
            ]
        )
    ).plan(
        "optimize thêm đi",
        response_intent="general_chat",
        context_text="Backtest report is available for BTCUSDT.",
        artifact_kinds={"backtest_report"},
        web_search="auto",
    )
    robustness_payload = _suggestions_payload(
        response_intent="general_chat",
        message_content="optimize thêm đi",
        context_text="Backtest report is available for BTCUSDT.",
        language="vi",
        artifact_available=True,
        artifact_kinds={"backtest_report"},
        action_plan=robustness_decision,
    )
    action_ids = {action["id"] for action in robustness_payload["actions"]}
    assert "build-robustness-report" in action_ids
    assert "run-variant-lab" in action_ids


def test_registry_backed_suggestion_disabled_when_action_unavailable() -> None:
    decision = ActionPlanner(
        FakeLLMClient(
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "decision": "suggest_actions",
                            "intent_id": "validation_repair",
                            "confidence": 0.9,
                            "suggested_actions": ["repair"],
                            "reason": "Repair was requested.",
                        }
                    ),
                )
            ]
        )
    ).plan(
        "repair this",
        response_intent="general_chat",
        context_text="No validation issue is present.",
        artifact_kinds={"pine_file"},
        web_search="auto",
    )

    payload = _suggestions_payload(
        response_intent="general_chat",
        message_content="repair this",
        context_text="No validation issue is present.",
        language="en",
        artifact_available=True,
        artifact_kinds={"pine_file"},
        action_plan=decision,
    )

    repair = payload["actions"][0]
    assert repair["id"] == "repair-validation"
    assert repair["enabled"] is False
    assert repair["disabled_reason"] == "A static validation problem is required."


def test_action_planner_robustness_prompt_does_not_route_to_market_research() -> None:
    prompt = (
        "Build a review-only robustness report for the current preview evidence. "
        "Summarize sample size, fees, slippage, drawdown, OOS concerns, and suspicious metrics."
    )
    planner = ActionPlanner(
        FakeLLMClient(
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "decision": "call_tool",
                            "intent_id": "robustness_review",
                            "confidence": 0.92,
                            "tool_id": "build_robustness_report",
                            "arguments": {"run_id": "latest_completed_backtest"},
                            "suggested_actions": ["build_robustness_report"],
                            "reason": "The prompt asks for a robustness report from current preview evidence.",
                        }
                    ),
                )
            ]
        )
    )
    decision = planner.plan(
        prompt,
        response_intent="general_chat",
        context_text="Backtest report is available for BNB/USDT.",
        artifact_kinds={"backtest_report"},
        web_search="auto",
    )

    payload = _suggestions_payload(
        response_intent="general_chat",
        message_content=prompt,
        context_text="Backtest report is available for BNB/USDT.",
        language="en",
        artifact_available=True,
        artifact_kinds={"backtest_report"},
        action_plan=decision,
    )

    action_ids = [action["id"] for action in payload["actions"]]
    assert decision.suggested_actions == ("build_robustness_report",)
    assert "build-robustness-report" in action_ids
    assert "market-research" not in action_ids


def test_action_planner_routes_current_preview_evidence_to_robustness_tool() -> None:
    planner = ActionPlanner(
        FakeLLMClient(
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "decision": "call_tool",
                            "intent_id": "robustness_review",
                            "confidence": 0.91,
                            "tool_id": "build_robustness_report",
                            "arguments": {"run_id": "latest_completed_backtest"},
                            "suggested_actions": ["build_robustness_report"],
                            "reason": "The user asks for a robustness report from current preview evidence.",
                        }
                    ),
                )
            ]
        )
    )

    decision = planner.plan(
        "Build a review-only robustness report for the current preview evidence.",
        response_intent="general_chat",
        context_text="Backtest report is available for BNB/USDT.",
        artifact_kinds={"backtest_report"},
        web_search="auto",
    )

    assert decision.decision == "call_tool"
    assert decision.tool_id == "build_robustness_report"
    assert decision.arguments == {"run_id": "latest_completed_backtest"}


def test_action_planner_trade_followup_uses_structured_arguments() -> None:
    planner = ActionPlanner(
        FakeLLMClient(
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "decision": "call_tool",
                            "intent_id": "trade_review",
                            "confidence": 0.93,
                            "tool_id": "query_backtest_trades",
                            "arguments": {"run_id": "latest_completed_backtest", "bucket": "sample", "limit": 20},
                            "reason": "The user asks for the first 20 trades.",
                        }
                    ),
                )
            ]
        )
    )

    decision = planner.plan(
        "i mean the first 20 trades",
        response_intent="general_chat",
        context_text="Loaded 5 indexed trades from backtest run `run_a54`.",
        artifact_kinds={"backtest_dashboard", "backtest_report"},
        web_search="auto",
    )

    assert decision.decision == "call_tool"
    assert decision.tool_id == "query_backtest_trades"
    assert decision.arguments == {"run_id": "latest_completed_backtest", "bucket": "sample", "limit": 20}


def test_action_planner_prompt_requires_trade_queries_to_call_tool() -> None:
    prompt = _action_planner_system_prompt()

    assert "show/list/fetch/give first N trades" in prompt
    assert "query_backtest_trades" in prompt
    assert "Omit bucket for first/latest/all trade requests" in prompt
    assert "structured table" in prompt
    assert "Do not answer that you will fetch data" in prompt


def test_direct_trade_action_plan_does_not_default_to_sample_bucket() -> None:
    planned_tool = _direct_action_plan_tool_args(
        ActionPlanDecision(
            decision="call_tool",
            intent_id="trade_review",
            confidence=0.95,
            source="planner",
            tool_id="query_backtest_trades",
            arguments={"run_id": "latest_completed_backtest", "limit": 50},
        ),
        artifact_kinds={"backtest_report", "backtest_dashboard"},
        context_text="Backtest dashboard is available.",
        web_search="auto",
    )

    assert planned_tool == (
        "query_backtest_trades",
        {"run_id": "latest_completed_backtest", "limit": 50},
    )


def test_action_planner_timeout_does_not_fall_back_to_semantic_keywords(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_CLASSIFIER_TIMEOUT_SECONDS", "0.01")
    planner = ActionPlanner(
        SlowLLMClient(
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "decision": "suggest_actions",
                            "intent_id": "strategy_evidence_review",
                            "confidence": 0.9,
                            "suggested_actions": ["run_backtest_preview"],
                            "reason": "Would be useful, but this response times out.",
                        }
                    ),
                )
            ]
        )
    )

    decision = planner.plan(
        "test kỹ hơn giúp mình",
        response_intent="general_chat",
        context_text="strategy artifact exists",
        artifact_kinds={"pine_file"},
        web_search="auto",
    )

    assert decision.decision == "answer"
    assert decision.source == "timeout_fallback"
    assert decision.suggested_actions == ()


def test_action_planner_live_prompt_stays_review_only_boundary() -> None:
    decision = ActionPlanner(
        FakeLLMClient(
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "decision": "suggest_actions",
                            "intent_id": "bot_boundary_review",
                            "confidence": 0.9,
                            "suggested_actions": ["create_proposed_intent", "run_risk_gate"],
                            "reason": "The user asks about live/bot behavior, so only review artifacts are allowed.",
                        }
                    ),
                )
            ]
        )
    ).plan(
        "Cho setup này trade live luôn được không?",
        response_intent="strategy_building",
        context_text="Market BTCUSDT timeframe 1h entry reclaim exit stop-loss risk 1%",
        artifact_kinds={"pine_file"},
        web_search="auto",
    )
    payload = _suggestions_payload(
        response_intent="strategy_building",
        message_content="Cho setup này trade live luôn được không?",
        context_text="Market BTCUSDT timeframe 1h entry reclaim exit stop-loss risk 1%",
        language="vi",
        artifact_kinds={"pine_file"},
        action_plan=decision,
    )

    action_ids = [action["id"] for action in payload["actions"]]
    assert action_ids[:2] == ["create-proposed-intent", "run-risk-gate"]
    assert all("trade now" not in action["label"].lower() for action in payload["actions"])
    assert all(action.get("tool_id") not in {"paper_trade", "live_trade", "broker_execute"} for action in payload["actions"])


def test_agent_chat_classifier_failure_does_not_fail_stream(tmp_path: Path) -> None:
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth)
    llm = IntentFailingThenAnswerClient()
    orchestrator = LLMOrchestrator(
        repository=repository,
        artifact_store=None,
        client=llm,
    )

    frames = [
        frame
        for raw in orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content="ETH đang bao nhiêu rồi?",
            web_search="auto",
        )
        for frame in parse_sse(raw)
    ]

    intent = next(frame for frame in frames if frame["event"] == "chat.response_intent")
    assert intent["data"]["payload"]["intent"] == "general_chat"
    assert intent["data"]["payload"]["source"] == "fallback"
    assert any(frame["event"] == "message.delta" for frame in frames)
    assert len(llm.calls_messages) == 2


def test_domain_scope_guard_blocks_explicit_off_topic_without_provider_call() -> None:
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth)
    llm = RecordingLLMClient([LLMClientEvent(type="message.delta", text="should not be used")])
    orchestrator = LLMOrchestrator(
        repository=repository,
        artifact_store=None,
        client=llm,
    )

    frames = [
        frame
        for raw in orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content="help me write an email to my landlord",
            web_search="auto",
        )
        for frame in parse_sse(raw)
    ]

    event_types = [frame["event"] for frame in frames]
    intent = next(frame for frame in frames if frame["event"] == "chat.response_intent")
    assistant_delta = next(frame for frame in frames if frame["event"] == "message.delta")
    assert intent["data"]["payload"]["source"] == "domain_scope_guard"
    assert intent["data"]["payload"]["domain_scope"] == "off_topic"
    assert "Strategy Codebot" in assistant_delta["data"]["payload"]["text"]
    assert "provider.started" not in event_types
    assert "tool.started" not in event_types
    assert "policy.blocked" not in event_types
    assert llm.calls == 0


def test_semantic_domain_gate_blocks_llm_off_topic_before_main_provider() -> None:
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth)
    llm = RecordingLLMClient(
        [
            LLMClientEvent(
                type="message.delta",
                text=json.dumps(
                    {
                        "response_intent": "general_chat",
                        "action": "answer",
                        "model_stage": "strategy_reasoning",
                        "confidence": 0.88,
                        "domain_scope": "off_topic",
                        "reasons": ["The request is unrelated to Strategy Codebot."],
                        "used_signals": [],
                    }
                ),
            )
        ]
    )
    orchestrator = LLMOrchestrator(repository=repository, artifact_store=None, client=llm)

    frames = [
        frame
        for raw in orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content="Can you help me choose a birthday gift?",
            web_search="auto",
        )
        for frame in parse_sse(raw)
    ]

    event_types = [frame["event"] for frame in frames]
    intent = next(frame for frame in frames if frame["event"] == "chat.response_intent")
    assert intent["data"]["payload"]["source"] == "domain_scope_guard"
    assert intent["data"]["payload"]["domain_scope"] == "off_topic"
    assert MODEL_ACTION_PROPOSED in event_types
    assert MODEL_ACTION_REJECTED in event_types
    assert "provider.started" not in event_types
    assert "tool.started" not in event_types
    assert llm.calls == 1


def test_domain_scope_guard_allows_trading_and_product_requests() -> None:
    pine_request = _classify_domain_scope("write a Pine strategy for BTC risk review")
    assert pine_request.allowed is True
    assert pine_request.scope == "ambiguous"
    assert pine_request.reason == "semantic_classifier_required"
    assert _classify_domain_scope("Explain risk boundaries").allowed is True
    assert _classify_domain_scope("latest OpenRouter model pricing docs source?").allowed is True
    review_request = _classify_domain_scope(
        "Build a review-only robustness report for the current preview evidence. "
        "Summarize sample size, fees, slippage, drawdown, OOS concerns, and suspicious metrics."
    )
    assert review_request.allowed is True
    assert review_request.scope == "ambiguous"
    contextual = _classify_domain_scope(
        "summarize the current preview evidence",
        artifact_kinds={"backtest_report"},
    )
    assert contextual.allowed is True
    assert contextual.scope == "artifact_followup"
    assert contextual.reason == "artifact_context_signal"
    assert _classify_domain_scope("what did I mention?").allowed is True
    assert _classify_domain_scope("write a Python script for a todo app").allowed is False
    assert _classify_domain_scope(
        "write an email to my landlord",
        artifact_kinds={"backtest_report"},
    ).allowed is False


def test_agent_chat_llm_intent_can_enable_auto_web_search_for_paraphrase(tmp_path: Path) -> None:
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth)
    llm = SequencedRecordingLLMClient(
        [
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "response_intent": "market_snapshot",
                            "action": "answer",
                            "model_stage": "strategy_reasoning",
                            "confidence": 0.9,
                            "tool_id": None,
                            "auto_chain": False,
                            "current_context_required": True,
                            "domain_scope": "trading_workflow",
                            "workflow_intent": None,
                            "missing_inputs": [],
                            "reasons": ["The user asks for a current ETH quote."],
                            "used_signals": ["current_info"],
                        }
                    ),
                )
            ],
            [LLMClientEvent(type="message.delta", text="ETH needs a sourced quote.")],
        ]
    )
    orchestrator = LLMOrchestrator(
        repository=repository,
        artifact_store=None,
        client=llm,
    )

    frames = [
        frame
        for raw in orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content="ETH đang bao nhiêu rồi?",
            web_search="auto",
        )
        for frame in parse_sse(raw)
    ]

    intent = next(frame for frame in frames if frame["event"] == "chat.response_intent")
    assert intent["data"]["payload"]["intent"] == "market_snapshot"
    assert intent["data"]["payload"]["source"] == "llm"
    assert llm.calls_tools[0] == []
    assert llm.calls_tools[1] == []
    assert llm.calls_tools[2] == [{"type": "web_search"}]


def test_response_stream_extracts_web_search_annotations() -> None:
    events = list(
        stream_response_events(
            [
                SimpleNamespace(
                    type="response.output_text.annotation.added",
                    annotation=SimpleNamespace(
                        type="url_citation",
                        title="ETH market source",
                        url="https://example.com/markets/eth",
                    ),
                )
            ],
            model="fake-responses-model",
        )
    )

    assert len(events) == 1
    assert events[0].type == LLM_EVENT_SOURCES
    assert events[0].arguments == {
        "sources": [
            {
                "id": "https-example-com-markets-eth",
                "title": "ETH market source",
                "type": "external",
                "url": "https://example.com/markets/eth",
            }
        ]
    }


def test_response_stream_extracts_web_search_call_sources() -> None:
    events = list(
        stream_response_events(
            [
                SimpleNamespace(
                    type="response.output_item.done",
                    item=SimpleNamespace(
                        type="web_search_call",
                        action=SimpleNamespace(
                            sources=[
                                SimpleNamespace(
                                    title="ETH source result",
                                    url="https://example.com/eth-source",
                                )
                            ]
                        ),
                    ),
                )
            ],
            model="fake-responses-model",
        )
    )

    assert len(events) == 1
    assert events[0].type == LLM_EVENT_SOURCES
    assert events[0].arguments == {
        "sources": [
            {
                "id": "https-example-com-eth-source",
                "title": "ETH source result",
                "type": "external",
                "url": "https://example.com/eth-source",
            }
        ]
    }


def test_response_stream_extracts_provider_api_web_search_sources() -> None:
    events = list(
        stream_response_events(
            [
                SimpleNamespace(
                    type="response.output_item.done",
                    item=SimpleNamespace(
                        type="web_search_call",
                        action=SimpleNamespace(
                            sources=[
                                SimpleNamespace(
                                    type="api",
                                    name="oai-finance",
                                    url=None,
                                )
                            ]
                        ),
                    ),
                )
            ],
            model="fake-responses-model",
        )
    )

    assert len(events) == 1
    assert events[0].type == LLM_EVENT_SOURCES
    assert events[0].arguments == {
        "sources": [
            {
                "id": "oai-finance",
                "title": "OpenAI Finance",
                "type": "internal",
            }
        ]
    }


def test_message_create_normalizes_supported_language() -> None:
    assert MessageCreate(content="hello", language="vi").language == "vi"
    assert MessageCreate(content="hello", language="fr").language == "en"


def test_deterministic_chat_uses_vietnamese_language(tmp_path: Path) -> None:
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=FakeLLMClient([])))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=deterministic",
        headers=AUTH_A,
        json={"content": "xin chào", "language": "vi"},
    )

    assert response.status_code == 200
    frames = parse_sse(response.text)
    deltas = [frame["data"]["payload"] for frame in frames if frame["event"] == "message.delta"]
    assert any("Mình đã nhận request trading" in str(delta) for delta in deltas)
    assert any(
        frame["data"]["payload"].get("label") == "Chuẩn bị response deterministic"
        for frame in frames
        if frame["event"] == "tool.started"
    )
    messages = client.get(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH_A).json()["items"]
    assert "Mình đã nhận request trading" in messages[1]["content"]


@dataclass
class FailingLLMClient:
    message: str
    model: str = "fake-responses-model"

    def ensure_configured(self) -> None:
        return None

    def stream(self, *, messages: list[dict[str, str]], tools: list[dict]) -> Iterable[LLMClientEvent]:
        raise RuntimeError(self.message)


class TimeoutLLMClient(FailingLLMClient):
    def stream(self, *, messages: list[dict[str, str]], tools: list[dict]) -> Iterable[LLMClientEvent]:
        raise ProviderTimeoutError("provider took too long")


def test_selected_backtest_preview_action_creates_plan_without_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BACKTEST_PINEFORGE_ENABLED", "1")
    auth = AuthContext("user-a", "workspace-a")
    repository = InMemoryConversationRepository()
    conversation = repository.create_conversation(auth, title="Backtest action")
    assert conversation is not None
    previous_run = repository.create_run(auth, conversation.id, status="completed")
    assert previous_run is not None
    strategy_spec = {
        "target_platform": "pine_v6",
        "script_type": "strategy",
        "market": "crypto",
        "symbol": "ETHUSDT",
        "timeframe": "15m",
        "runtime_targets": ["pine_v6"],
        "entry_rules": ["Enter long when fast EMA crosses above slow EMA on a confirmed bar."],
        "exit_rules": ["Exit when fast EMA crosses below slow EMA on a confirmed bar."],
        "risk_rules": ["Risk 1% account equity per trade."],
        "position_sizing": "Risk 1% account equity per trade",
        "stop_loss": "2% below entry",
        "take_profit": "4% above entry",
    }
    repository.create_strategy_spec(auth, previous_run.id, strategy_spec, "strategy-spec.v1")
    artifact_store = LocalArtifactStore(tmp_path)
    pine_code = "\n".join(
        [
            "//@version=6",
            "strategy(\"EMA Cross\", overlay=true)",
            "fast = ta.ema(close, 9)",
            "slow = ta.ema(close, 21)",
            "if ta.crossover(fast, slow)",
            "    strategy.entry(\"Long\", strategy.long)",
            "if ta.crossunder(fast, slow)",
            "    strategy.close(\"Long\")",
        ]
    )
    pine_key = artifact_store.storage_key(previous_run.id, "pine/strategy.pine")
    pine_path = tmp_path / pine_key
    pine_path.parent.mkdir(parents=True, exist_ok=True)
    pine_path.write_text(pine_code, encoding="utf-8")
    validation_key = artifact_store.storage_key(previous_run.id, "validation-report.json")
    validation_path = tmp_path / validation_key
    validation_path.parent.mkdir(parents=True, exist_ok=True)
    validation_path.write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    repository.create_artifact(
        auth,
        previous_run.id,
        kind="pine_file",
        mime_type="text/plain",
        display_name="strategy.pine",
        storage_key=pine_key,
    )
    repository.create_artifact(
        auth,
        previous_run.id,
        kind="validation_report",
        mime_type="application/json",
        display_name="validation-report.json",
        storage_key=validation_key,
    )
    source_message = repository.create_message(
        auth,
        conversation.id,
        "Pine strategy is ready for Backtest Preview.",
        role="assistant",
    )
    assert source_message is not None
    llm = RecordingLLMClient([])
    orchestrator = LLMOrchestrator(repository=repository, artifact_store=artifact_store, client=llm)

    frames = [
        frame
        for raw in orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content="Prepare a review-only local preview evidence check for the current strategy.",
            language="en",
            selected_action={
                "action_id": "run-backtest-preview",
                "tool_id": "run_backtest_preview",
                "next_state": "local_preview_evidence",
                "artifact_kind": "backtest_report",
                "source_message_id": source_message.id,
            },
            web_search="off",
        )
        for frame in parse_sse(raw)
    ]

    assert not any(frame["event"] == "provider.started" for frame in frames)
    tool_started = [frame["data"]["payload"] for frame in frames if frame["event"] == "tool.started"]
    assert any(payload["tool_id"] == "create_backtest_plan" for payload in tool_started)
    assert any(frame["event"] == "backtest.preview.approval_required" for frame in frames)
    assert not any(frame["event"] == "chat.workflow.updated" for frame in frames)
    reasoning_frames = [frame["data"]["payload"] for frame in frames if frame["event"] == "model.reasoning.delta"]
    assert any(payload.get("workflow_step") == "backtest_preview" for payload in reasoning_frames)


def test_agent_chat_requires_auth(tmp_path: Path) -> None:
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=FakeLLMClient([])))

    response = client.post(
        "/v1/conversations/conv_missing/messages?stream=true&mode=agent",
        json={"content": "hello"},
    )

    assert response.status_code == 401


def test_first_user_message_generates_conversation_title_with_backend_llm(tmp_path: Path) -> None:
    llm = RecordingLLMClient([LLMClientEvent(type="message.delta", text="EMA crossover review")])
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        headers=AUTH_A,
        json={"content": "Can you review my EMA crossover strategy?"},
    )

    assert response.status_code == 201, response.text
    updated = client.get(f"/v1/conversations/{conversation['id']}", headers=AUTH_A).json()
    assert updated["title"] == "EMA crossover review"
    assert llm.calls == 1


def test_existing_conversation_title_is_not_overwritten(tmp_path: Path) -> None:
    llm = RecordingLLMClient([LLMClientEvent(type="message.delta", text="New generated title")])
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={"title": "Manual title"}).json()

    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        headers=AUTH_A,
        json={"content": "Review this RSI setup"},
    )

    assert response.status_code == 201, response.text
    updated = client.get(f"/v1/conversations/{conversation['id']}", headers=AUTH_A).json()
    assert updated["title"] == "Manual title"
    assert llm.calls == 0


def test_title_generation_failure_falls_back_to_user_message(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            repository=create_sqlite_repository(),
            artifact_root=tmp_path,
            llm_client=FailingLLMClient("title provider unavailable"),
        )
    )
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        headers=AUTH_A,
        json={"content": "Review a BTC breakout plan with ATR risk controls and position sizing."},
    )

    assert response.status_code == 201, response.text
    updated = client.get(f"/v1/conversations/{conversation['id']}", headers=AUTH_A).json()
    assert updated["title"] == "Review a BTC breakout plan with ATR risk controls and"


def test_responses_client_adapter_emits_usage_tool_calls_and_text() -> None:
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=11, output_tokens=7),
        output=[
            SimpleNamespace(type="function_call", name="generate_pine", arguments=json.dumps({"strategy_spec": valid_spec()})),
            SimpleNamespace(type="message", content=[SimpleNamespace(text="done")]),
        ],
        output_text=None,
    )

    events = list(response_events(response, model="fake-responses-model"))

    assert events[0] == LLMClientEvent(
        type="usage",
        model="fake-responses-model",
        input_tokens=11,
        output_tokens=7,
    )
    assert events[1].type == "tool.call"
    assert events[1].tool_name == "generate_pine"
    assert events[1].arguments == {"strategy_spec": valid_spec()}
    assert events[2] == LLMClientEvent(type="message.delta", text="done", model="fake-responses-model")


def test_responses_stream_adapter_emits_delta_before_usage() -> None:
    stream = [
        SimpleNamespace(type="response.output_text.delta", delta="hel"),
        SimpleNamespace(type="response.output_text.delta", delta="lo"),
        SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(
                type="function_call",
                name="generate_pine",
                arguments=json.dumps({"strategy_spec": valid_spec()}),
            ),
        ),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(usage=SimpleNamespace(input_tokens=3, output_tokens=2)),
        ),
    ]

    events = list(stream_response_events(stream, model="fake-responses-model"))

    assert events[0] == LLMClientEvent(type="message.delta", text="hel", model="fake-responses-model")
    assert events[1] == LLMClientEvent(type="message.delta", text="lo", model="fake-responses-model")
    assert events[2].type == "tool.call"
    assert events[3] == LLMClientEvent(type="usage", model="fake-responses-model", input_tokens=3, output_tokens=2)


def test_chat_completion_adapter_emits_tool_calls_for_gateway_clients() -> None:
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="queued",
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name="run_backtest_preview",
                                arguments=json.dumps({"strategy_spec": valid_spec()}),
                            )
                        )
                    ],
                )
            )
        ],
    )

    events = list(chat_completion_events(response, model="openai/gpt-5.5"))

    assert events[0] == LLMClientEvent(type="usage", model="openai/gpt-5.5", input_tokens=11, output_tokens=7)
    assert events[1] == LLMClientEvent(type="message.delta", text="queued", model="openai/gpt-5.5")
    assert events[2].type == "tool.call"
    assert events[2].tool_name == "run_backtest_preview"
    assert events[2].arguments == {"strategy_spec": valid_spec()}


def test_agent_chat_cross_tenant_conversation_returns_404(tmp_path: Path) -> None:
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=FakeLLMClient([])))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    cross_user = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_B,
        json={"content": "hello"},
    )
    cross_workspace = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_OTHER_WORKSPACE,
        json={"content": "hello"},
    )

    assert cross_user.status_code == 404
    assert cross_workspace.status_code == 404


def test_agent_chat_disconnect_marks_run_cancelled(tmp_path: Path) -> None:
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth)
    orchestrator = LLMOrchestrator(
        repository=repository,
        artifact_store=None,
        client=FakeLLMClient([LLMClientEvent(type="message.delta", text="hello")]),
    )

    stream = orchestrator.stream_chat(auth=auth, conversation_id=conversation.id, message_content="hello")
    first_frame = next(stream)
    stream.close()
    run_id = parse_sse(first_frame)[0]["data"]["run_id"]
    run = repository.get_run(auth, run_id)
    events = repository.list_run_events(auth, run_id)

    assert run is not None
    assert run.status == "cancelled"
    assert events is not None
    cancelled = next(event for event in events if event.type == "run.cancelled")
    assert cancelled.payload == {"status": "cancelled", "reason": "client_disconnected"}


def test_agent_chat_missing_provider_config_returns_503(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "hello"},
    )

    assert response.status_code == 503
    assert "OPENAI_API_KEY" not in response.text


def test_fake_responses_client_streams_and_persists_compact_delta(tmp_path: Path) -> None:
    llm = FakeLLMClient(
        [
            LLMClientEvent(type="message.delta", text="hello "),
            LLMClientEvent(type="message.delta", text="world"),
        ]
    )
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "stream please"},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    run_id = frames[0]["data"]["run_id"]
    assert [
        frame["event"]
        for frame in frames
        if frame["data"]["sequence"] == 0 and frame["event"] == "message.delta"
    ] == ["message.delta", "message.delta"]
    assert any(frame["event"] == "model.reasoning.delta" for frame in frames if frame["data"]["sequence"] == 0)
    replay = parse_sse(client.get(f"/v1/runs/{run_id}/events", headers=AUTH_A).text)
    persisted_deltas = [frame for frame in replay if frame["event"] == "message.delta"]
    assert len(persisted_deltas) == 1
    assert persisted_deltas[0]["data"]["payload"] == {"text": "hello world", "compact": True}
    messages = client.get(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH_A)
    assert messages.status_code == 200, messages.text
    assert [(message["role"], message["content"]) for message in messages.json()["items"]] == [
        ("user", "stream please"),
        ("assistant", "hello world"),
    ]


def test_agent_chat_sends_prior_conversation_context_to_model(tmp_path: Path) -> None:
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth, "Context")
    repository.create_message(auth, conversation.id, "My strategy uses EMA 20/50 crossover.", role="user")
    repository.create_message(auth, conversation.id, "I can help review that strategy.", role="assistant")
    current = repository.create_message(auth, conversation.id, "What risk rule did I mention?", role="user")
    assert current is not None
    llm = RecordingLLMClient([LLMClientEvent(type="message.delta", text="You mentioned EMA crossover context.")])
    orchestrator = LLMOrchestrator(repository=repository, artifact_store=None, client=llm)

    frames = list(
        orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content=current.content,
            current_message_id=current.id,
        )
    )

    assert frames
    provider_messages = llm.calls_messages[-1]
    contents = [message["content"] for message in provider_messages]
    assert "My strategy uses EMA 20/50 crossover." in contents
    assert "I can help review that strategy." in contents
    assert contents.count("What risk rule did I mention?") == 1
    run_id = parse_sse(frames[0])[0]["data"]["run_id"]
    events = repository.list_run_events(auth, run_id)
    assert events is not None
    context_event = next(event for event in events if event.type == "context.built")
    assert context_event.payload["history_message_count"] == 2


def test_agent_chat_sends_latest_backtest_live_status_to_model(tmp_path: Path) -> None:
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth, "Backtest status")
    backtest_run = repository.create_run(auth, conversation.id, status="running", mode="backtest-preview")
    assert backtest_run is not None
    repository.append_run_event(
        auth,
        backtest_run.id,
        "backtest.preview.heartbeat",
        {
            "stage": "fetching",
            "status": "running",
            "progress_pct": 42,
            "eta_ms": 18000,
            "message": "Fetching missing public OHLCV candles.",
            "updated_at": "2026-06-23T00:00:00Z",
        },
    )
    current = repository.create_message(auth, conversation.id, "What is the current backtest status?", role="user")
    assert current is not None
    llm = RecordingLLMClient([LLMClientEvent(type="message.delta", text="The backtest is still running.")])
    orchestrator = LLMOrchestrator(repository=repository, artifact_store=None, client=llm)

    frames = list(
        orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content=current.content,
            current_message_id=current.id,
        )
    )

    assert frames
    assert any(
        "Latest local backtest preview status" in message["content"] and "stage: fetching" in message["content"]
        for call in llm.calls_messages
        for message in call
    )
    run_id = parse_sse(frames[0])[0]["data"]["run_id"]
    events = repository.list_run_events(auth, run_id)
    assert events is not None
    context_event = next(event for event in events if event.type == "context.built")
    assert context_event.payload["backtest_live_status_included"] is True


def test_agent_chat_sends_failed_backtest_live_status_to_model(tmp_path: Path) -> None:
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth, "Backtest failed status")
    backtest_run = repository.create_run(auth, conversation.id, status="failed", mode="backtest-preview")
    assert backtest_run is not None
    repository.append_run_event(
        auth,
        backtest_run.id,
        "backtest.preview.failed",
        {"message": "No candles were available."},
    )
    current = repository.create_message(auth, conversation.id, "What happened to the backtest?", role="user")
    assert current is not None
    llm = RecordingLLMClient([LLMClientEvent(type="message.delta", text="The backtest failed.")])
    orchestrator = LLMOrchestrator(repository=repository, artifact_store=None, client=llm)

    frames = list(
        orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content=current.content,
            current_message_id=current.id,
        )
    )

    assert frames
    assert any(
        "status: failed" in message["content"] and "No candles were available." in message["content"]
        for call in llm.calls_messages
        for message in call
    )


def test_agent_chat_streams_safe_reasoning_summary_before_model_delta(tmp_path: Path) -> None:
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth, "Reasoning")
    current = repository.create_message(auth, conversation.id, "Review my EMA crossover context.", role="user")
    assert current is not None
    llm = RecordingLLMClient([LLMClientEvent(type="message.delta", text="Here is a review-only response.")])
    orchestrator = LLMOrchestrator(repository=repository, artifact_store=None, client=llm)

    frames = [
        frame
        for raw in orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content=current.content,
            current_message_id=current.id,
            language="en",
        )
        for frame in parse_sse(raw)
    ]

    event_types = [frame["event"] for frame in frames]
    assert event_types.index("model.reasoning.delta") < event_types.index("message.delta")
    reasoning_payloads = [
        frame["data"]["payload"]
        for frame in frames
        if frame["event"] == "model.reasoning.delta"
    ]
    assert reasoning_payloads[0] == {
        "phase": "context",
        "safe": True,
        "text": "Reading conversation context.",
        "transient": True,
    }
    assert any(payload["phase"] == "model" for payload in reasoning_payloads)
    assert any(payload["phase"] == "finalizing" for payload in reasoning_payloads)
    serialized = json.dumps(reasoning_payloads)
    assert "EMA crossover" not in serialized
    assert "trace" not in serialized.lower()
    persisted_events = repository.list_run_events(auth, frames[0]["data"]["run_id"])
    assert persisted_events is not None
    assert "model.reasoning.delta" not in [event.type for event in persisted_events]


def test_context_builder_filters_internal_roles_and_dedupes_current_message() -> None:
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth)
    repository.create_message(auth, conversation.id, "Entry uses RSI > 50.", role="user")
    repository.create_message(auth, conversation.id, "internal system note", role="system")
    repository.create_message(auth, conversation.id, "raw tool output", role="tool")
    repository.create_message(auth, conversation.id, "Risk should stay review-only.", role="assistant")
    current = repository.create_message(auth, conversation.id, "Summarize the strategy context.", role="user")
    assert current is not None

    context = ConversationContextBuilder(repository).build(
        auth=auth,
        conversation_id=conversation.id,
        current_message_id=current.id,
        current_user_message=current.content,
        system_prompt="system prompt",
    )

    contents = [message["content"] for message in context.messages]
    assert "Entry uses RSI > 50." in contents
    assert "Risk should stay review-only." in contents
    assert "internal system note" not in contents
    assert "raw tool output" not in contents
    assert contents.count("Summarize the strategy context.") == 1
    assert "Summarize the strategy context." not in context.prior_context_text


def test_context_builder_includes_memory_summary_and_truncates_recent_history(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_MODEL_CONTEXT_WINDOW_TOKENS", "4096")
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth)
    first = repository.create_message(auth, conversation.id, "Old durable strategy context.", role="user")
    assert first is not None
    memory = repository.upsert_conversation_memory(
        auth,
        conversation.id,
        summary="Memory says this is an EMA crossover strategy with 1% risk.",
        covered_message_id=first.id,
        estimated_tokens=20,
    )
    assert memory is not None
    for index in range(12):
        repository.create_message(auth, conversation.id, f"history {index} " + ("x" * 2000), role="user")
    current = repository.create_message(auth, conversation.id, "Continue.", role="user")
    assert current is not None

    context = ConversationContextBuilder(repository).build(
        auth=auth,
        conversation_id=conversation.id,
        current_message_id=current.id,
        current_user_message=current.content,
        system_prompt="system prompt",
    )

    assert context.summary_used is True
    assert context.truncated is True
    assert "Memory says this is an EMA crossover strategy" in context.messages[1]["content"]
    assert "memory: Memory says this is an EMA crossover strategy" in context.prior_context_text
    assert context.messages[-1] == {"role": "user", "content": "Continue."}


def test_agent_chat_guard_uses_memory_summary_as_selected_context() -> None:
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth)
    covered = repository.create_message(auth, conversation.id, "Old conversation seed.", role="user")
    assert covered is not None
    repository.upsert_conversation_memory(
        auth,
        conversation.id,
        summary="Strategy context: EMA entry, RSI filter, ATR stop, 1% risk.",
        covered_message_id=covered.id,
        estimated_tokens=18,
    )
    current = repository.create_message(auth, conversation.id, "Use the current strategy context to continue.", role="user")
    assert current is not None
    llm = RecordingLLMClient([LLMClientEvent(type="message.delta", text="Continuing from memory.")])
    orchestrator = LLMOrchestrator(repository=repository, artifact_store=None, client=llm)

    frames = list(
        orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content=current.content,
            current_message_id=current.id,
        )
    )

    assert len(llm.calls_messages) == 2
    frame_payloads = [frame["data"]["payload"] for raw in frames for frame in parse_sse(raw) if frame["event"] == "message.delta"]
    assert all(payload.get("source") != "missing_current_strategy_context" for payload in frame_payloads)
    assert any("Continuing from memory." in str(payload) for payload in frame_payloads)


def test_agent_chat_compacts_long_conversation_after_completion(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_CONVERSATION_COMPACTION_THRESHOLD_MESSAGES", "3")
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth, "Long context")
    repository.create_message(auth, conversation.id, "Entry: EMA 20 crosses EMA 50.", role="user")
    repository.create_message(auth, conversation.id, "Noted the EMA crossover entry.", role="assistant")
    current = repository.create_message(auth, conversation.id, "Remember the risk as 1%.", role="user")
    assert current is not None
    llm = SequencedRecordingLLMClient(
        [
            [LLMClientEvent(type="message.delta", text="not an action plan")],
            [LLMClientEvent(type="message.delta", text="Risk noted.")],
            [LLMClientEvent(type="message.delta", text="Summary: EMA crossover strategy with 1% risk.")],
        ]
    )
    orchestrator = LLMOrchestrator(repository=repository, artifact_store=None, client=llm)

    frames = list(
        orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content=current.content,
            current_message_id=current.id,
        )
    )

    assert parse_sse(frames[-1])[0]["event"] == "run.completed"
    memory = repository.get_conversation_memory(auth, conversation.id)
    assert memory is not None
    assert memory.summary == "Summary: EMA crossover strategy with 1% risk."
    assert memory.covered_message_id is not None
    assert "Summarize conversation memory" in llm.calls_messages[2][0]["content"]

    next_message = repository.create_message(auth, conversation.id, "Continue with the same context.", role="user")
    assert next_message is not None
    list(
        orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content=next_message.content,
            current_message_id=next_message.id,
        )
    )

    assert len(llm.calls_messages) == 5


def test_agent_chat_summary_failure_does_not_fail_completed_response(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_CONVERSATION_COMPACTION_THRESHOLD_MESSAGES", "3")
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth)
    repository.create_message(auth, conversation.id, "Entry: RSI crosses 50.", role="user")
    repository.create_message(auth, conversation.id, "Assistant captured the RSI entry.", role="assistant")
    current = repository.create_message(auth, conversation.id, "Risk is 1%.", role="user")
    assert current is not None
    llm = SummaryFailingAfterAnswerClient()
    orchestrator = LLMOrchestrator(repository=repository, artifact_store=None, client=llm)

    frames = list(
        orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content=current.content,
            current_message_id=current.id,
        )
    )

    assert parse_sse(frames[-1])[0]["event"] == "run.completed"
    assert repository.get_conversation_memory(auth, conversation.id) is None
    assert len(llm.calls_messages) == 3
    run_id = parse_sse(frames[0])[0]["data"]["run_id"]
    events = repository.list_run_events(auth, run_id)
    assert events is not None
    skipped = next(event for event in events if event.type == "context.compaction_skipped")
    assert skipped.payload["error"] == "RuntimeError"


def test_allowed_tool_call_runs_after_gates(tmp_path: Path) -> None:
    llm = FakeLLMClient([LLMClientEvent(type="tool.call", tool_name="generate_pine", arguments={"strategy_spec": valid_spec()})])
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "Please help with my trading workspace."},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    assert "tool.started" in [frame["event"] for frame in frames]
    tool_audits = [
        frame
        for frame in frames
        if frame["event"].startswith("model_action.")
        and frame["data"]["payload"].get("tool_id") == "generate_pine"
        and frame["data"]["payload"].get("source") == "llm_tool_call"
    ]
    assert [frame["event"] for frame in tool_audits] == [
        "model_action.proposed",
        "model_action.validated",
        "model_action.executed",
    ]
    assert tool_audits[-1]["data"]["payload"]["status"] == "executed"
    assert "pine_code" not in json.dumps([frame["data"]["payload"] for frame in tool_audits])
    completed = next(frame for frame in frames if frame["event"] == "tool.completed")
    assert completed["data"]["payload"]["tool_id"] == "generate_pine"
    output = completed["data"]["payload"]["output"]
    assert output["pine_code"].startswith("//@version=6")
    assert output["artifact_id"]
    assert output["validation"]["status"] in {"pass", "manual_required"}
    assert output["validation_artifact_id"]
    assert output["review"]["source"] == "deterministic_static_validation"
    assert output["review_artifact_id"]
    assert output["evaluator_optimizer_summary"]["stop_reason"] in {
        "production_gate_passed",
        "completed",
    }
    assert "validation.completed" in [frame["event"] for frame in frames]
    assert "review.completed" in [frame["event"] for frame in frames]
    assert "evaluator_optimizer.summary" in [frame["event"] for frame in frames]
    fallback_delta = next(
        frame
        for frame in frames
        if frame["event"] == "message.delta" and frame["data"]["payload"].get("source") == "tool_only_success_fallback"
    )
    assert fallback_delta["data"]["payload"]["compact"] is True
    fallback_text = fallback_delta["data"]["payload"]["text"]
    assert "`strategy.pine`" in fallback_text
    assert "BTCUSDT" in fallback_text
    assert "1h" in fallback_text
    assert "Backtest Preview" in fallback_text
    assert "PineForge" not in fallback_text
    messages = client.get(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH_A).json()["items"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert "`strategy.pine`" in messages[1]["content"]
    assert "policy.blocked" not in [frame["event"] for frame in frames]
    suggestion_frames = [frame for frame in frames if frame["event"] == "chat.suggestions.updated"]
    assert len(suggestion_frames) >= 2
    post_tool_actions = suggestion_frames[-1]["data"]["payload"]["actions"]
    post_tool_action_ids = [action["id"] for action in post_tool_actions]
    assert "run-backtest-preview" in post_tool_action_ids
    assert post_tool_action_ids[0] == "run-backtest-preview"
    assert "generate-pine-v6" not in post_tool_action_ids
    state = client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A).json()
    artifact_kinds = {artifact["kind"] for artifact in state["latest_run_artifacts"]}
    assert {"pine_file", "validation_report", "review_report"}.issubset(artifact_kinds)
    assert any(artifact["display_name"] == "strategy.pine" for artifact in state["latest_run_artifacts"])
    assert state["strategy_profile"]["source"] == "strategy_spec"
    replay = client.get(f"/v1/runs/{completed['data']['run_id']}/events", headers=AUTH_A).text
    assert "artifact.created" in [frame["event"] for frame in parse_sse(replay)]


def test_artifact_evidence_followup_reuses_persisted_validation_review_summary(tmp_path: Path) -> None:
    llm = FakeLLMClient([LLMClientEvent(type="tool.call", tool_name="generate_pine", arguments={"strategy_spec": valid_spec()})])
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    generated = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "Please generate Pine v6 for this review-only strategy."},
    )
    assert generated.status_code == 200, generated.text
    generated_events = [frame["event"] for frame in parse_sse(generated.text)]
    assert "validation.completed" in generated_events
    assert "review.completed" in generated_events
    assert "evaluator_optimizer.summary" in generated_events

    followup = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={
            "content": (
                "Now explain whether the generated Pine strategy passed static validation "
                "and review checks. Include any blocker or warning, but do not start paper or live trading."
            )
        },
    )

    assert followup.status_code == 200, followup.text
    frames = parse_sse(followup.text)
    event_types = [frame["event"] for frame in frames]
    assert "provider.started" not in event_types
    assert "prompt_chain.started" not in event_types
    assert "validation.completed" in event_types
    assert "review.completed" in event_types
    assert "evaluator_optimizer.summary" in event_types
    replayed = [
        frame
        for frame in frames
        if frame["event"] in {"validation.completed", "review.completed", "evaluator_optimizer.summary"}
    ]
    assert all(frame["data"]["payload"]["evidence_source"] == "persisted_conversation_evidence" for frame in replayed)
    answer = next(
        frame
        for frame in frames
        if frame["event"] == "message.delta" and frame["data"]["payload"].get("source") == "artifact_evidence_followup"
    )
    text = answer["data"]["payload"]["text"]
    assert "static validation" in text
    assert "review" in text
    assert "evaluator stop reason" in text
    assert "paper or live trading" in text
    assert "Let me" not in text


def test_tool_call_preface_is_not_persisted_as_final_answer(tmp_path: Path) -> None:
    llm = FakeLLMClient(
        [
            LLMClientEvent(type="message.delta", text="Let me run both the static validation and the parallel review now."),
            LLMClientEvent(type="tool.call", tool_name="generate_pine", arguments={"strategy_spec": valid_spec()}),
        ]
    )
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "Please generate Pine v6 for this review-only strategy."},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    fallback = next(
        frame
        for frame in frames
        if frame["event"] == "message.delta" and frame["data"]["payload"].get("source") == "tool_only_success_fallback"
    )
    assert "`strategy.pine`" in fallback["data"]["payload"]["text"]
    messages = client.get(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH_A).json()["items"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert "`strategy.pine`" in messages[1]["content"]
    assert "Let me run" not in messages[1]["content"]


def test_generate_pine_chat_validation_blocks_missing_risk_summary(tmp_path: Path) -> None:
    spec = valid_spec()
    spec["risk_rules"] = ["Risk controls are not defined yet."]
    spec["position_sizing"] = ""
    spec["stop_loss"] = ""
    spec["take_profit"] = ""
    llm = FakeLLMClient([LLMClientEvent(type="tool.call", tool_name="generate_pine", arguments={"strategy_spec": spec})])
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "Please help with my trading workspace."},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    completed = next(frame for frame in frames if frame["event"] == "tool.completed")
    output = completed["data"]["payload"]["output"]
    assert output["validation"]["status"] == "fail"
    assert output["review"]["decision"] == "fail"
    assert output["evaluator_optimizer_summary"]["stop_reason"] == "validation_blocked"
    summary = next(frame for frame in frames if frame["event"] == "evaluator_optimizer.summary")
    assert summary["data"]["payload"]["stop_reason"] == "validation_blocked"


def test_bounded_scout_chat_path_uses_read_only_tools(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_ACTION_PLANNER_ENABLED", "0")

    def knowledge_handler(arguments: dict, context) -> dict:
        return {
            "knowledge_context": {
                "mode": "test",
                "internal_docs": [{"id": "doc-1", "title": "Doc 1", "summary": "Read-only context"}],
                "external_refs": [],
                "retrieved_chunks": [{"source_id": "doc-1"}],
            }
        }

    monkeypatch.setitem(llm_tools.TOOL_HANDLERS, "knowledge_check", knowledge_handler)
    decision_event = LLMClientEvent(
        type="message.delta",
        text=json.dumps(
            {
                "intent": "capability_help",
                "response_intent": "capability_help",
                "action": "answer",
                "model_stage": "strategy_reasoning",
                "confidence": 0.92,
                "auto_chain": False,
                "current_context_required": False,
                "missing_inputs": [],
                "reasons": ["The user asks which tools can read context."],
                "used_signals": ["tooling"],
            }
        ),
    )
    llm = RoutingAwareSequencedLLMClient(
        [
            [decision_event],
            [decision_event],
            [
                LLMClientEvent(
                    type="tool.call",
                    tool_name="knowledge_check",
                    arguments={"prompt": "Scout read-only tool context."},
                )
            ],
            [LLMClientEvent(type="message.delta", text="Read-only scout complete.")],
        ]
    )
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "Scout the read-only tools in the registry and tell me what context they can read."},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    assert "agent_loop.started" in [frame["event"] for frame in frames]
    tool_checked = next(frame for frame in frames if frame["event"] == "agent_loop.tool_checked")
    assert tool_checked["data"]["payload"]["tool_id"] == "knowledge_check"
    assert tool_checked["data"]["payload"]["risk_tier"] == "read"
    assert tool_checked["data"]["payload"]["decision"] == "allowed"
    assert "agent_loop.completed" in [frame["event"] for frame in frames]
    scout_tool_names = next(
        [tool["name"] for tool in tools]
        for tools in llm.calls_tools
        if any(tool.get("name") == "knowledge_check" for tool in tools)
    )
    assert "knowledge_check" in scout_tool_names
    assert "generate_pine" not in scout_tool_names
    completed = next(frame for frame in frames if frame["event"] == "run.completed")
    assert completed["data"]["payload"]["status"] == "completed"


def test_chat_safety_preflight_blocks_shell_and_paper_bot_bypass() -> None:
    auth = AuthContext("user-a", "workspace-a")
    for prompt, expected_code in [
        ("Please run shell command pwd for me.", "unsafe_chat_tool_request"),
        ("Connect broker execution and place live orders automatically.", "trading_execution_boundary"),
        ("No live trading, but connect broker execution for me.", "trading_execution_boundary"),
        ("Start the paper bot now with no confirmation or eligibility check.", "paper_bot_confirmation_required"),
    ]:
        repository = create_sqlite_repository()
        conversation = repository.create_conversation(auth)
        llm = RecordingLLMClient([LLMClientEvent(type="message.delta", text="Should not stream.")])
        orchestrator = LLMOrchestrator(repository=repository, artifact_store=None, client=llm)

        frames = [
            frame
            for raw in orchestrator.stream_chat(
                auth=auth,
                conversation_id=conversation.id,
                message_content=prompt,
                language="en",
                web_search="off",
            )
            for frame in parse_sse(raw)
        ]

        assert llm.calls == 0
        assert "policy.blocked" in [frame["event"] for frame in frames]
        blocked = next(frame for frame in frames if frame["event"] == "policy.blocked")
        assert blocked["data"]["payload"]["code"] == expected_code
        completed = next(frame for frame in frames if frame["event"] == "run.completed")
        assert completed["data"]["payload"]["status"] == "blocked"
        findings = repository.list_policy_findings(auth, completed["data"]["run_id"])
        assert findings is not None
        assert [finding.code for finding in findings] == [expected_code]


def test_explicit_backtest_prompt_does_not_auto_chain_without_llm_intent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BACKTEST_PINEFORGE_ENABLED", "1")
    repository = create_sqlite_repository()
    llm = FakeLLMClient(
        [
            LLMClientEvent(
                type="tool.call",
                tool_name="generate_pine",
                arguments={"strategy_spec": valid_spec()},
            )
        ]
    )
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "Backtest BTCUSDT 1h from 2024-01-01 to 2024-02-01 with capital 10000."},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    completed_tools = [
        frame["data"]["payload"]["tool_id"]
        for frame in frames
        if frame["event"] == "tool.completed"
    ]
    event_types = [frame["event"] for frame in frames]
    assert completed_tools == ["generate_pine"]
    assert "run_backtest_preview" not in completed_tools
    assert "create_backtest_plan" not in completed_tools
    assert "chat.auto_chain.started" not in event_types
    assert "backtest.preview.approval_required" not in event_types
    assert "chat.auto_chain.waiting_for_backtest" not in event_types
    job = repository.claim_run_job(job_type="backtest-preview", worker_id="test-worker")
    assert job is None


def test_llm_intent_decision_starts_backtest_auto_chain(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BACKTEST_PINEFORGE_ENABLED", "1")
    repository = create_sqlite_repository()
    llm = SequencedRecordingLLMClient(
        [
            [LLMClientEvent(type="message.delta", text="Preview Test")],
            [
                LLMClientEvent(
                    type="message.delta",
                    text=json.dumps(
                        {
                            "response_intent": "backtest_preview",
                            "action": "start_auto_chain",
                            "model_stage": "pine_code_generation",
                            "confidence": 0.9,
                            "tool_id": "run_backtest_preview",
                            "auto_chain": True,
                            "current_context_required": False,
                            "missing_inputs": [],
                            "reasons": ["The user asks for local preview evidence."],
                            "used_signals": ["semantic_preview_request"],
                        }
                    ),
                )
            ],
            [LLMClientEvent(type="message.delta", text="not an action plan")],
            [LLMClientEvent(type="tool.call", tool_name="generate_pine", arguments={"strategy_spec": valid_spec()})],
        ]
    )
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "Evaluate how this BTCUSDT 1h strategy would have behaved from 2024-01-01 to 2024-02-01."},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    completed_tools = [
        frame["data"]["payload"]["tool_id"]
        for frame in frames
        if frame["event"] == "tool.completed"
    ]
    started = next(frame for frame in frames if frame["event"] == "chat.auto_chain.started")
    intent = next(frame for frame in frames if frame["event"] == "chat.response_intent")
    assert completed_tools[:2] == ["generate_pine", "create_backtest_plan"]
    assert started["data"]["payload"]["source"] == "llm"
    assert intent["data"]["payload"]["source"] == "llm"
    assert intent["data"]["payload"]["action"] == "start_auto_chain"


def test_backtest_plan_validation_failure_persists_evidence_without_provider_error(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BACKTEST_PINEFORGE_ENABLED", "1")
    spec = valid_spec()
    pine_code = generate_pine(spec)

    monkeypatch.setattr(
        "strategy_codebot.server.llm_tools.validate_pineforge_pine",
        lambda *_args, **_kwargs: {
            "status": "fail",
            "errors": [{"message": "strategy.exit requires stop or limit"}],
        },
    )
    repository = create_sqlite_repository()
    llm = FakeLLMClient(
        [
            LLMClientEvent(
                type="tool.call",
                tool_name="create_backtest_plan",
                arguments={
                    "prompt": "backtest for it again",
                    "strategy_spec": spec,
                    "pine_code": pine_code,
                    "backtest_config": {"symbol": "BNBUSDT", "timeframe": "1h"},
                },
            )
        ]
    )
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "backtest for it again"},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    event_types = [frame["event"] for frame in frames]
    assert "backtest.preview.approval_required" not in event_types
    assert "chat.auto_chain.waiting_for_backtest" not in event_types

    failed_tool = next(
        frame
        for frame in frames
        if frame["event"] == "tool.completed"
        and frame["data"]["payload"]["tool_id"] == "create_backtest_plan"
    )
    failed_payload = failed_tool["data"]["payload"]
    assert failed_payload["status"] == "failed"
    assert failed_payload["code"] == "pine_validation_failed"
    assert failed_payload["pine_code_artifact_id"]
    assert failed_payload["validation_artifact_id"]

    failed_run = next(frame for frame in frames if frame["event"] == "run.failed")
    run_failed_payload = failed_run["data"]["payload"]
    assert run_failed_payload["code"] == "pine_validation_failed"
    assert run_failed_payload["message"] == "Backtest plan failed because local Pine validation failed."
    assert run_failed_payload["retryable"] is False
    assert run_failed_payload["pine_code_artifact_id"] == failed_payload["pine_code_artifact_id"]
    assert run_failed_payload["validation_artifact_id"] == failed_payload["validation_artifact_id"]
    assert "Provider execution failed" not in stream.text

    state = client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A).json()
    artifact_kinds = {artifact["kind"] for artifact in state["latest_run_artifacts"]}
    assert {"pine_file", "validation_report"} <= artifact_kinds
    job = repository.claim_run_job(job_type="backtest-preview", worker_id="test-worker")
    assert job is None
    messages = client.get(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH_A).json()["items"]
    assert messages[-1]["content"] == "Backtest plan failed because local Pine validation failed."


def test_backtest_summary_tool_only_fallback_surfaces_metrics() -> None:
    text = _tool_only_success_message(
        ["get_backtest_summary"],
        "en",
        tool_results=[
            {
                "tool_id": "get_backtest_summary",
                "output": {
                    "summary": {
                        "symbol": "ETH/USDT",
                        "signal_timeframe": "1h",
                        "candle_timeframe": "1m",
                        "evidence_label": "PineForge local Pine preview evidence",
                        "metrics": {
                            "pnl": {"absolute": -894.3091, "percentage": -8.9431},
                            "max_drawdown": None,
                            "trade_count": 87,
                            "win_rate": 0,
                        },
                    },
                },
            }
        ],
    )

    assert "ETH/USDT" in text
    assert "-894.3091" in text
    assert "-8.9431%" in text
    assert "87 trades" in text
    assert "1h" in text
    assert "1m" in text
    assert "not TradingView official validation" in text
    assert "PineForge" not in text


def test_backtest_summary_overrides_non_metric_model_text() -> None:
    text = _maybe_backtest_summary_response(
        "It looks like the previous `get_backtest_summary` call didn't return specific results.",
        ["get_backtest_summary"],
        [
            {
                "tool_id": "get_backtest_summary",
                "output": {
                    "summary": {
                        "symbol": "ETH/USDT",
                        "signal_timeframe": "1h",
                        "candle_timeframe": "1m",
                        "evidence_label": "PineForge local Pine preview evidence",
                        "metrics": {
                            "pnl": {"absolute": -894.3091, "percentage": -8.9431},
                            "max_drawdown": None,
                            "trade_count": 87,
                            "win_rate": 0,
                        },
                    },
                },
            }
        ],
    )

    assert "ETH/USDT" in text
    assert "-894.3091" in text
    assert "-8.9431%" in text
    assert "87 trades" in text
    assert "didn't return specific" not in text
    assert "PineForge" not in text


def test_backtest_summary_not_found_does_not_render_fake_na_metrics() -> None:
    text = _maybe_backtest_summary_response(
        "",
        ["get_backtest_summary"],
        [{"tool_id": "get_backtest_summary", "output": {"status": "not_found", "run_id": "run_missing"}}],
    )

    assert "not available" in text
    assert "PnL N/A" not in text


def test_backtest_summary_tool_only_not_found_does_not_render_fake_na_metrics() -> None:
    text = _tool_only_success_message(
        ["get_backtest_summary"],
        "en",
        tool_results=[{"tool_id": "get_backtest_summary", "output": {"status": "not_found", "run_id": "run_missing"}}],
    )

    assert "not available" in text
    assert "PnL N/A" not in text


def test_backtest_trades_tool_only_response_renders_indexed_rows() -> None:
    text = _tool_only_success_message(
        ["query_backtest_trades"],
        "en",
        tool_results=[
            {
                "tool_id": "query_backtest_trades",
                "output": {
                    "status": "ok",
                    "run_id": "run_backtest",
                    "requested_run_id": "run_missing",
                    "fallback_used": True,
                    "trades": [
                        {
                            "bucket": "top_loser",
                            "trade_rank": 42,
                            "opened_at": "2024-03-15T19:00:00+00:00",
                            "closed_at": "2024-03-15T20:00:00+00:00",
                            "pnl_cost": -17.0297,
                            "pnl_percentage": -0.17,
                            "trade": {"side": "long"},
                        }
                    ],
                },
            }
        ],
    )

    assert "Loaded 1 indexed trades" in text
    assert "run_backtest" in text
    assert "latest completed backtest report" in text
    assert "See the table below" in text
    assert "top loser" not in text
    assert "-17.03" not in text
    assert "The tool run completed successfully" not in text


def test_backtest_trades_tool_only_response_renders_requested_twenty_rows() -> None:
    text = _tool_only_success_message(
        ["query_backtest_trades"],
        "en",
        tool_results=[
            {
                "tool_id": "query_backtest_trades",
                "output": {
                    "status": "ok",
                    "run_id": "run_backtest",
                    "fallback_used": False,
                    "trades": [
                        {
                            "bucket": "sample",
                            "trade_rank": index,
                            "opened_at": f"2024-01-{index:02d}T00:00:00+00:00",
                            "closed_at": f"2024-01-{index:02d}T01:00:00+00:00",
                            "pnl_cost": float(index),
                            "trade": {"side": "long"},
                        }
                        for index in range(1, 21)
                    ],
                },
            }
        ],
    )

    assert "Loaded 20 indexed trades" in text
    assert "See the table below" in text
    assert "#1" not in text
    assert "#20" not in text


def test_backtest_trades_response_overrides_deferred_model_text() -> None:
    text = _maybe_backtest_trades_response(
        "I apologize for the confusion. I need to actually retrieve the trade data first. Let me do that now.",
        ["query_backtest_trades"],
        [
            {
                "tool_id": "query_backtest_trades",
                "output": {
                    "status": "ok",
                    "run_id": "run_backtest",
                    "fallback_used": False,
                    "trades": [
                        {
                            "bucket": "sample",
                            "trade_rank": 1,
                            "opened_at": "2024-01-01T00:00:00+00:00",
                            "closed_at": "2024-01-01T01:00:00+00:00",
                            "pnl_cost": -12.5,
                            "pnl_percentage": -0.12,
                            "trade": {"side": "long"},
                        }
                    ],
                },
            }
        ],
    )

    assert "Loaded 1 indexed trades" in text
    assert "See the table below" in text
    assert "#1" not in text
    assert "-12.50" not in text
    assert "Let me do that now" not in text


def test_backtest_summary_tool_result_keeps_bounded_output() -> None:
    result = _tool_success_result(
        "get_backtest_summary",
        {"run_id": "run_123"},
        {
            "status": "ok",
            "summary": {
                "symbol": "ETH/USDT",
                "metrics": {"trade_count": 87},
            },
        },
    )

    assert result["tool_id"] == "get_backtest_summary"
    assert result["output"]["summary"]["symbol"] == "ETH/USDT"
    assert result["output"]["summary"]["metrics"]["trade_count"] == 87


def test_backtest_trades_tool_result_keeps_bounded_rows() -> None:
    result = _tool_success_result(
        "query_backtest_trades",
        {"run_id": "run_123", "limit": 20},
        {
            "status": "ok",
            "run_id": "run_123",
            "requested_run_id": "run_123",
            "fallback_used": False,
            "trades": [{"trade_rank": index, "trade": {"side": "long"}} for index in range(25)],
        },
    )

    assert result["tool_id"] == "query_backtest_trades"
    assert result["output"]["run_id"] == "run_123"
    assert len(result["output"]["trades"]) == 20


def test_compact_tool_output_preserves_bounded_trade_rows() -> None:
    output = compact_tool_output(
        {
            "status": "ok",
            "run_id": "run_123",
            "requested_run_id": "run_123",
            "fallback_used": False,
            "trades": [{"trade_rank": index, "trade": {"side": "long"}} for index in range(75)],
        }
    )

    assert output["run_id"] == "run_123"
    assert len(output["trades"]) == 50
    assert output["truncated"] is True


def test_action_planner_persists_unavailable_tool_decision() -> None:
    decision = _parse_action_plan_json(
        json.dumps(
            {
                "confidence": 0.94,
                "decision": "call_tool",
                "intent_id": "robustness",
                "reason": "Needs robustness report.",
                "tool_id": "build_robustness_report",
                "arguments": {"run_id": "latest_completed_backtest"},
            }
        ),
        available_tools={"market_research"},
    )

    assert decision is not None
    assert decision.decision == "suggest_actions"
    assert decision.source == "llm"
    assert decision.tool_id == "build_robustness_report"
    assert decision.reason == "Needs robustness report."


def test_user_facing_chat_tools_persist_artifacts(tmp_path: Path) -> None:
    spec = valid_spec()
    pine_code = generate_pine(spec)
    cases = [
        (
            "create_mql5_design",
            {"strategy_spec": spec},
            {"mql5_file"},
            "runner-design.md",
        ),
        (
            "static_validate",
            {"strategy_spec": spec, "pine_code": pine_code},
            {"validation_report"},
            "validation-report.json",
        ),
        (
            "parallel_review",
            {
                "strategy_spec": spec,
                "validation": {"platform": "pine_v6", "status": "pass", "warnings": []},
                "pine_code": pine_code,
            },
            {"review_report"},
            "review-report.json",
        ),
    ]

    for tool_name, arguments, expected_kinds, expected_display_name in cases:
        llm = FakeLLMClient([LLMClientEvent(type="tool.call", tool_name=tool_name, arguments=arguments)])
        client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
        conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

        stream = client.post(
            f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
            headers=AUTH_A,
            json={"content": f"run {tool_name}"},
        )

        assert stream.status_code == 200, stream.text
        completed = next(frame for frame in parse_sse(stream.text) if frame["event"] == "tool.completed")
        assert completed["data"]["payload"]["output"]["artifact_id"]
        state = client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A).json()
        artifacts = state["latest_run_artifacts"]
        assert {artifact["kind"] for artifact in artifacts} == expected_kinds
        assert artifacts[0]["display_name"] == expected_display_name
        assert state["strategy_profile"]["source"] == "strategy_spec"
        replay = client.get(f"/v1/runs/{completed['data']['run_id']}/events", headers=AUTH_A).text
        assert "artifact.created" in [frame["event"] for frame in parse_sse(replay)]


def test_current_strategy_context_request_without_context_asks_for_details(tmp_path: Path) -> None:
    llm = RecordingLLMClient([LLMClientEvent(type="tool.call", tool_name="knowledge_check", arguments={"prompt": "current"})])
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    client.post(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH_A, json={"content": "hi"})
    llm.calls = 0

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "Generate a review-only Pine v6 artifact from the current strategy context."},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    assert llm.calls == 0
    assert "tool.started" not in [frame["event"] for frame in frames]
    delta = next(frame for frame in frames if frame["event"] == "message.delta")
    assert delta["data"]["payload"]["source"] == "missing_current_strategy_context"
    assert "do not have a current strategy spec" in delta["data"]["payload"]["text"]
    messages = client.get(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH_A).json()["items"]
    assert "do not have a current strategy spec" in messages[-1]["content"]


def test_current_strategy_context_request_uses_vietnamese_language(tmp_path: Path) -> None:
    llm = RecordingLLMClient([LLMClientEvent(type="tool.call", tool_name="knowledge_check", arguments={"prompt": "current"})])
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    client.post(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH_A, json={"content": "hi"})
    llm.calls = 0

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={
            "content": "Generate a review-only Pine v6 artifact from the current strategy context.",
            "language": "vi",
        },
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    assert llm.calls == 0
    delta = next(frame for frame in frames if frame["event"] == "message.delta")
    assert delta["data"]["payload"]["source"] == "missing_current_strategy_context"
    assert "Mình chưa có strategy spec" in delta["data"]["payload"]["text"]
    messages = client.get(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH_A).json()["items"]
    assert "Mình chưa có strategy spec" in messages[-1]["content"]


def test_knowledge_only_tool_response_does_not_claim_artifact_generation(monkeypatch, tmp_path: Path) -> None:
    def knowledge_tool(*_args, **_kwargs):
        return {
            "knowledge_context": {
                "mode": "auto",
                "store": "knowledge_base",
                "index_ref": "postgres:postgresql://strategy_codebot:secret@postgres:5432/strategy_codebot",
                "internal_docs": [{"id": "pine_v6_rules", "path": "docs/trading/pine-v6-rules.md"}],
                "external_refs": [{"id": "tradingview-pine-strategies", "url": "https://www.tradingview.com/pine-script-docs/"}],
                "retrieved_chunks": [{"chunk_id": "chunk-1"}],
            }
        }

    monkeypatch.setattr("strategy_codebot.server.llm_orchestrator.execute_tool", knowledge_tool)
    llm = FakeLLMClient([LLMClientEvent(type="tool.call", tool_name="knowledge_check", arguments={"prompt": "pine"})])
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "Check knowledge for Pine generation"},
    )

    frames = parse_sse(stream.text)
    completed = next(frame for frame in frames if frame["event"] == "tool.completed")
    payload = completed["data"]["payload"]
    assert payload["label"] == "Check knowledge context"
    assert payload["tool_user_summary"] == "Checked knowledge context: 1 internal docs, 1 retrieved chunks, 1 external refs."
    sources = payload["output"]["knowledge_context_summary"]["sources"]
    assert sources == [
        {
            "id": "tradingview-pine-strategies",
            "label": "External source",
            "title": "Tradingview Pine Strategies",
            "type": "external",
            "url": "https://www.tradingview.com/pine-script-docs/",
        },
        {
            "id": "pine_v6_rules",
            "label": "Internal reference",
            "title": "Pine V6 Rules",
            "type": "internal",
        },
    ]
    assert "index_ref" not in json.dumps(payload)
    assert "chunk-1" not in json.dumps(sources)
    delta = next(
        frame
        for frame in frames
        if frame["event"] == "message.delta" and frame["data"]["payload"].get("source") == "tool_only_success_fallback"
    )
    assert "checked the relevant knowledge context" in delta["data"]["payload"]["text"]
    assert "generated review-only output" not in delta["data"]["payload"]["text"]


def test_knowledge_only_tool_response_uses_vietnamese_language(monkeypatch, tmp_path: Path) -> None:
    def knowledge_tool(*_args, **_kwargs):
        return {
            "knowledge_context": {
                "mode": "auto",
                "store": "knowledge_base",
                "internal_docs": [{"id": "pine_v6_rules"}],
                "external_refs": [{"id": "tradingview-pine-strategies"}],
                "retrieved_chunks": [{"chunk_id": "chunk-1"}],
            }
        }

    monkeypatch.setattr("strategy_codebot.server.llm_orchestrator.execute_tool", knowledge_tool)
    llm = FakeLLMClient([LLMClientEvent(type="tool.call", tool_name="knowledge_check", arguments={"prompt": "pine"})])
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "Check knowledge for Pine generation", "language": "vi"},
    )

    frames = parse_sse(stream.text)
    completed = next(frame for frame in frames if frame["event"] == "tool.completed")
    payload = completed["data"]["payload"]
    assert payload["label"] == "Kiểm tra knowledge context"
    assert "Đã kiểm tra knowledge context" in payload["tool_user_summary"]
    delta = next(
        frame
        for frame in frames
        if frame["event"] == "message.delta" and frame["data"]["payload"].get("source") == "tool_only_success_fallback"
    )
    assert "Mình đã kiểm tra knowledge context" in delta["data"]["payload"]["text"]


def test_knowledge_context_summary_sources_are_deduped_and_capped() -> None:
    compact = compact_tool_output(
        {
            "knowledge_context": {
                "internal_docs": [
                    {"id": "pine_v6_rules", "path": "docs/trading/pine-v6-rules.md"},
                    {"id": "risk_policy", "path": "docs/trading/risk-policy.md"},
                    {"id": "strategy_patterns", "path": "docs/trading/strategy-patterns.md"},
                    {"id": "crypto_playbook", "path": "docs/trading/crypto-playbook.md"},
                    {"id": "forex_playbook", "path": "docs/trading/forex-playbook.md"},
                ],
                "external_refs": [
                    {"id": "tradingview-pine-strategies", "url": "https://www.tradingview.com/pine-script-docs/"},
                    {"id": "tradingview-pine-strategies", "url": "https://duplicate.example.com/"},
                ],
                "retrieved_chunks": [
                    {"source_id": "pine_v6_rules", "text": "raw text must not be exposed"},
                    {"source_id": "extra_source", "text": "raw text must not be exposed"},
                ],
            }
        }
    )

    sources = compact["knowledge_context_summary"]["sources"]
    assert len(sources) == 5
    assert [source["id"] for source in sources].count("tradingview-pine-strategies") == 1
    assert sources[0]["type"] == "external"
    assert "raw text" not in json.dumps(sources)


def test_tool_execution_failure_fails_run_without_success_terminal(monkeypatch, tmp_path: Path) -> None:
    def broken_tool(*_args, **_kwargs):
        raise RuntimeError("tool backend unavailable")

    monkeypatch.setattr("strategy_codebot.server.llm_orchestrator.execute_tool", broken_tool)
    repository = create_sqlite_repository()
    llm = FakeLLMClient([LLMClientEvent(type="tool.call", tool_name="generate_pine", arguments={"strategy_spec": valid_spec()})])
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "generate pine"},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    event_types = [frame["event"] for frame in frames]
    run_id = frames[0]["data"]["run_id"]
    run = repository.get_run(AuthContext("user-a", "workspace-a"), run_id)

    assert run is not None
    assert run.status == "failed"
    assert event_types.count("run.failed") == 1
    assert "run.completed" not in event_types
    completed = next(frame for frame in frames if frame["event"] == "tool.completed")
    assert completed["data"]["payload"] == {
        "tool_id": "generate_pine",
        "label": "Generate Pine v6",
        "status": "failed",
        "error": "RuntimeError",
        "message": "tool backend unavailable",
        "output_summary": "Tool failed: RuntimeError",
    }
    failure_delta = next(frame for frame in frames if frame["event"] == "message.delta")
    assert failure_delta["data"]["payload"]["compact"] is True
    assert "The AI run failed" in failure_delta["data"]["payload"]["text"]
    failed_tool = next(frame for frame in frames if frame["event"] == "run.failed")
    assert failed_tool["data"]["payload"]["message"] == "Provider execution failed"
    assert failed_tool["data"]["payload"]["assistant_message_persisted"] is True
    messages = client.get(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH_A).json()["items"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert "The AI run failed" in messages[1]["content"]


def test_invalid_tool_input_records_policy_block_without_execution(tmp_path: Path) -> None:
    llm = FakeLLMClient([LLMClientEvent(type="tool.call", tool_name="generate_pine", arguments={})])
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "bad tool"},
    )

    frames = parse_sse(stream.text)
    blocked = next(frame for frame in frames if frame["event"] == "policy.blocked")
    assert blocked["data"]["payload"]["code"] == "schema_invalid"
    rejected = next(
        frame
        for frame in frames
        if frame["event"] == "model_action.rejected"
        and frame["data"]["payload"].get("tool_id") == "generate_pine"
        and frame["data"]["payload"].get("source") == "llm_tool_call"
    )
    assert rejected["data"]["payload"]["reason_code"] == "schema_invalid"
    assert "tool.started" not in [frame["event"] for frame in frames]


def test_invalid_tool_input_uses_vietnamese_blocked_message(tmp_path: Path) -> None:
    llm = FakeLLMClient([LLMClientEvent(type="tool.call", tool_name="generate_pine", arguments={})])
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "bad tool", "language": "vi"},
    )

    frames = parse_sse(stream.text)
    blocked = next(frame for frame in frames if frame["event"] == "policy.blocked")
    delta = next(frame for frame in frames if frame["event"] == "message.delta")
    assert blocked["data"]["payload"]["code"] == "schema_invalid"
    assert "chạm boundary review-only" in delta["data"]["payload"]["text"]
    assert "tool.started" not in [frame["event"] for frame in frames]


def test_incomplete_strategy_spec_tool_input_is_blocked_before_execution(monkeypatch, tmp_path: Path) -> None:
    def unexpected_tool(*_args, **_kwargs):
        raise AssertionError("tool should not execute")

    monkeypatch.setattr("strategy_codebot.server.llm_orchestrator.execute_tool", unexpected_tool)
    llm = FakeLLMClient(
        [LLMClientEvent(type="tool.call", tool_name="generate_pine", arguments={"strategy_spec": {"market": "crypto"}})]
    )
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "bad tool"},
    )

    frames = parse_sse(stream.text)
    blocked = next(frame for frame in frames if frame["event"] == "policy.blocked")
    assert blocked["data"]["payload"]["code"] == "schema_invalid"
    assert "tool.started" not in [frame["event"] for frame in frames]


def test_policy_blocks_live_trading_or_profit_claim_tool_call(tmp_path: Path) -> None:
    spec = {**valid_spec(), "user_notes": "Guarantee profit after broker execution."}
    llm = FakeLLMClient([LLMClientEvent(type="tool.call", tool_name="generate_pine", arguments={"strategy_spec": spec})])
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "unsafe"},
    )

    blocked = next(frame for frame in parse_sse(stream.text) if frame["event"] == "policy.blocked")
    assert blocked["data"]["payload"]["code"] == "policy_violation"


def test_budget_denial_prevents_tool_execution(tmp_path: Path) -> None:
    llm = FakeLLMClient([LLMClientEvent(type="tool.call", tool_name="generate_pine", arguments={"strategy_spec": valid_spec()})])
    client = TestClient(
        create_app(
            repository=create_sqlite_repository(),
            artifact_root=tmp_path,
            llm_client=llm,
            llm_max_tool_calls=0,
        )
    )
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "too many tools"},
    )

    frames = parse_sse(stream.text)
    blocked = next(frame for frame in frames if frame["event"] == "policy.blocked")
    assert blocked["data"]["payload"]["code"] == "budget_exceeded"
    assert "tool.started" not in [frame["event"] for frame in frames]


def test_unknown_shell_tool_is_rejected_by_allowlist(tmp_path: Path) -> None:
    llm = FakeLLMClient([LLMClientEvent(type="tool.call", tool_name="shell", arguments={"command": "ls"})])
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "inspect the current trading workspace"},
    )

    blocked = next(frame for frame in parse_sse(stream.text) if frame["event"] == "policy.blocked")
    assert blocked["data"]["payload"]["code"] == "tool_not_allowed"


def test_agent_run_mode_produces_artifacts_and_events_without_paths(tmp_path: Path) -> None:
    llm = FakeLLMClient([LLMClientEvent(type="message.delta", text="Looks ready for dry-run.")])
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    response = client.post(
        "/v1/runs",
        headers=AUTH_A,
        json={"conversation_id": conversation["id"], "strategy_spec": valid_spec(), "mode": "agent"},
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    assert {artifact["kind"] for artifact in payload["artifacts"]} == {
        "pine_file",
        "validation_report",
        "review_report",
        "manual_checklist",
        "runtime_trace_summary",
    }
    serialized = json.dumps(payload)
    assert "storage_key" not in serialized
    assert "out_dir" not in serialized
    events = parse_sse(client.get(f"/v1/runs/{payload['id']}/events", headers=AUTH_A).text)
    assert "message.delta" in [frame["event"] for frame in events]
    assert events[-1]["event"] == "run.completed"


def test_tool_catalog_handlers_match_provider_definitions() -> None:
    assert tool_catalog_consistency_errors() == []


def test_unknown_llm_event_fails_run_without_silent_success(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            repository=create_sqlite_repository(),
            artifact_root=tmp_path,
            llm_client=FakeLLMClient([LLMClientEvent(type="unknown.event")]),
        )
    )
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "hello"},
    )

    assert response.status_code == 200
    frames = parse_sse(response.text)
    assert frames[-1]["event"] == "run.failed"
    assert frames[-1]["data"]["payload"]["error"] == "RuntimeError"


def test_provider_failure_event_is_sanitized(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            repository=create_sqlite_repository(),
            artifact_root=tmp_path,
            llm_client=FailingLLMClient("provider payload sk-proj-abcdefghijklmnop /Users/secret/raw.json"),
        )
    )
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "hello"},
    )

    assert response.status_code == 200
    frames = parse_sse(response.text)
    payload = frames[-1]["data"]["payload"]
    assert frames[-1]["event"] == "run.failed"
    assert payload == {
        "code": "provider_unavailable",
        "error": "RuntimeError",
        "message": "Provider execution failed",
        "retryable": True,
        "assistant_message_persisted": True,
    }
    messages = client.get(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH_A).json()["items"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "The AI run failed: Provider execution failed"
    assert "sk-proj" not in response.text
    assert "/Users/secret" not in response.text
    assert "provider payload" not in response.text


def test_provider_failure_message_uses_vietnamese_language(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            repository=create_sqlite_repository(),
            artifact_root=tmp_path,
            llm_client=FailingLLMClient("provider payload sk-proj-abcdefghijklmnop /Users/secret/raw.json"),
        )
    )
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "hello", "language": "vi"},
    )

    assert response.status_code == 200
    frames = parse_sse(response.text)
    delta = next(frame for frame in frames if frame["event"] == "message.delta")
    assert "AI run thất bại" in delta["data"]["payload"]["text"]
    messages = client.get(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH_A).json()["items"]
    assert "AI run thất bại" in messages[1]["content"]
    assert "sk-proj" not in response.text
    assert "/Users/secret" not in response.text


def test_provider_timeout_event_is_retryable(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            repository=create_sqlite_repository(),
            artifact_root=tmp_path,
            llm_client=TimeoutLLMClient("unused"),
        )
    )
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "hello"},
    )

    assert response.status_code == 200
    frames = parse_sse(response.text)
    assert "provider.started" in [frame["event"] for frame in frames]
    payload = frames[-1]["data"]["payload"]
    assert frames[-1]["event"] == "run.failed"
    assert payload["code"] == "provider_timeout"
    assert payload["retryable"] is True
