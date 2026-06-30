from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from strategy_codebot.server import llm_tools
from strategy_codebot.server.agent_loop import AgentLoopBudget
from strategy_codebot.server.agent_loop import BoundedScoutRunner
from strategy_codebot.server.llm_clients import LLMClientEvent
from strategy_codebot.server.llm_clients import LLM_EVENT_MESSAGE_DELTA
from strategy_codebot.server.llm_clients import LLM_EVENT_TOOL_CALL
from strategy_codebot.server.llm_clients import LLM_EVENT_USAGE
from strategy_codebot.server.llm_tools import ToolExecutionContext


class FakeLLMClient:
    model = "test-model"

    def __init__(self, event_batches: list[list[LLMClientEvent]]) -> None:
        self.event_batches = list(event_batches)
        self.tools_seen: list[list[str]] = []

    def ensure_configured(self) -> None:
        return None

    def stream(
        self,
        *,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]],
        routing_context: dict[str, Any] | None = None,
    ):
        self.tools_seen.append([str(tool["name"]) for tool in tools])
        events = self.event_batches.pop(0) if self.event_batches else []
        yield from events


class LegacyLLMClient:
    model = "legacy-model"

    def __init__(self) -> None:
        self.called = False

    def ensure_configured(self) -> None:
        return None

    def stream(self, *, messages: list[dict[str, str]], tools: list[dict[str, Any]]):
        self.called = True
        yield LLMClientEvent(type=LLM_EVENT_MESSAGE_DELTA, text="Done.")


def _tool_context() -> ToolExecutionContext:
    return ToolExecutionContext(
        repository=object(),
        artifact_store=object(),
        auth=object(),
        run=SimpleNamespace(id="run-agent-loop"),
    )


def _registry() -> dict[str, Any]:
    metadata = {
        "capability": "test",
        "input_schema_ref": "test-input",
        "output_schema_ref": "test-output",
        "evidence_required": ["test evidence"],
        "phase_status": "implemented",
    }
    return {
        "tools": [
            {"id": "knowledge_check", "risk_tier": "read", **metadata},
            {"id": "generate_pine", "risk_tier": "code_generation", **metadata},
        ]
    }


@pytest.fixture
def knowledge_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        return {
            "knowledge_context": {
                "mode": "test",
                "internal_docs": [{"id": "doc-1", "title": "Doc 1", "summary": "Summary"}],
                "external_refs": [],
                "retrieved_chunks": [{"source_id": "doc-1"}],
            }
        }

    monkeypatch.setitem(llm_tools.TOOL_HANDLERS, "knowledge_check", handler)


def test_bounded_scout_runner_executes_registry_backed_read_tool(knowledge_handler: None) -> None:
    client = FakeLLMClient(
        [
            [
                LLMClientEvent(
                    type=LLM_EVENT_TOOL_CALL,
                    tool_name="knowledge_check",
                    arguments={"prompt": "Use EMA context"},
                ),
                LLMClientEvent(type=LLM_EVENT_USAGE, input_tokens=3, output_tokens=4),
            ],
            [LLMClientEvent(type=LLM_EVENT_MESSAGE_DELTA, text="Done.")],
        ]
    )
    runner = BoundedScoutRunner(
        llm_client=client,
        tool_context=_tool_context(),
        run_id="agent-read",
        registry=_registry(),
        budget=AgentLoopBudget(max_iterations=2, max_tool_calls=2, max_tokens=100),
    )

    result = runner.run([{"role": "user", "content": "Find relevant context."}])

    assert result.status == "completed"
    assert result.response_text == "Done."
    assert result.tool_results[0].status == "completed"
    assert result.tool_results[0].output == {
        "knowledge_context_summary": {
            "mode": "test",
            "store": None,
            "status": "ready",
            "internal_doc_ids": ["doc-1"],
            "external_source_ids": [],
            "retrieved_chunk_count": 1,
            "sources": [{"id": "doc-1", "label": "Internal reference", "title": "Doc 1", "type": "internal"}],
            "missing_context": [],
        }
    }
    assert client.tools_seen[0] == ["knowledge_check"]
    assert [event["event_type"] for event in result.events if event["event_type"].startswith("tool.")] == [
        "tool.started",
        "tool.completed",
    ]
    agent_loop_events = [event for event in result.events if event["event_type"].startswith("agent_loop.")]
    assert [event["event_type"] for event in agent_loop_events] == [
        "agent_loop.started",
        "agent_loop.tool_checked",
        "agent_loop.llm_completed",
        "agent_loop.llm_completed",
        "agent_loop.completed",
    ]
    tool_checked = next(event for event in agent_loop_events if event["event_type"] == "agent_loop.tool_checked")
    assert tool_checked["tool_id"] == "knowledge_check"
    assert tool_checked["decision"] == "allowed"
    assert tool_checked["gate"] == "policy"
    assert tool_checked["iteration"] == 1
    assert tool_checked["tool_call_count"] == 1
    assert tool_checked["budget_exhausted"] is False
    loop_started = next(event for event in result.events if event["event_type"] == "agent_loop.started")
    legacy_started = next(event for event in result.events if event["event_type"] == "agent.started")
    loop_completed = next(event for event in result.events if event["event_type"] == "agent_loop.completed")
    legacy_completed = next(event for event in result.events if event["event_type"] == "agent.completed")
    for key in ("workflow", "stage", "agent_role", "model", "status"):
        assert loop_started[key] == legacy_started[key]
        assert loop_completed[key] == legacy_completed[key]
    loop_llm_completed = [event for event in result.events if event["event_type"] == "agent_loop.llm_completed"][-1]
    legacy_llm_completed = [event for event in result.events if event["event_type"] == "llm.completed"][-1]
    for key in ("workflow", "stage", "agent_role", "model", "status", "usage"):
        assert loop_llm_completed[key] == legacy_llm_completed[key]
    assert loop_llm_completed["iteration"] == legacy_llm_completed["attempt"]


def test_bounded_scout_runner_supports_clients_without_routing_context() -> None:
    client = LegacyLLMClient()
    runner = BoundedScoutRunner(
        llm_client=client,
        tool_context=_tool_context(),
        run_id="agent-legacy",
        registry=_registry(),
    )

    result = runner.run([{"role": "user", "content": "Summarize read-only context."}])

    assert result.status == "completed"
    assert result.response_text == "Done."
    assert client.called is True


def test_bounded_scout_runner_returns_only_current_run_events() -> None:
    client = FakeLLMClient(
        [
            [LLMClientEvent(type=LLM_EVENT_MESSAGE_DELTA, text="One.")],
            [LLMClientEvent(type=LLM_EVENT_MESSAGE_DELTA, text="Two.")],
        ]
    )
    runner = BoundedScoutRunner(
        llm_client=client,
        tool_context=_tool_context(),
        run_id="agent-reused",
        registry=_registry(),
    )

    first = runner.run([{"role": "user", "content": "First."}])
    second = runner.run([{"role": "user", "content": "Second."}])

    assert first.response_text == "One."
    assert second.response_text == "Two."
    assert len(second.events) == len(first.events)
    assert second.events[0]["event_type"] == "agent_loop.started"
    assert [event["event_type"] for event in second.events].count("agent_loop.started") == 1


def test_bounded_scout_runner_blocks_non_read_risk_tool() -> None:
    client = FakeLLMClient(
        [
            [
                LLMClientEvent(
                    type=LLM_EVENT_TOOL_CALL,
                    tool_name="generate_pine",
                    arguments={"strategy_spec": {"name": "Unsafe"}},
                )
            ]
        ]
    )
    runner = BoundedScoutRunner(
        llm_client=client,
        tool_context=_tool_context(),
        run_id="agent-non-read",
        registry=_registry(),
    )

    result = runner.run([{"role": "user", "content": "Generate code."}])

    assert result.status == "blocked"
    assert result.blocked_reason == "generate_pine is blocked by risk tier code_generation."
    assert client.tools_seen[0] == ["knowledge_check"]
    tool_checked = next(event for event in result.events if event["event_type"] == "agent_loop.tool_checked")
    assert tool_checked["tool_id"] == "generate_pine"
    assert tool_checked["decision"] == "blocked"
    assert tool_checked["risk_tier"] == "code_generation"
    assert tool_checked["reason_code"] == "agent_loop_tool_risk_blocked"
    assert any(event["event_type"] == "tool.blocked" for event in result.events)


def test_bounded_scout_runner_blocks_unknown_tool() -> None:
    client = FakeLLMClient(
        [[LLMClientEvent(type=LLM_EVENT_TOOL_CALL, tool_name="shell_exec", arguments={"cmd": "pwd"})]]
    )
    runner = BoundedScoutRunner(
        llm_client=client,
        tool_context=_tool_context(),
        run_id="agent-unknown",
        registry=_registry(),
    )

    result = runner.run([{"role": "user", "content": "Run shell."}])

    assert result.status == "blocked"
    assert result.blocked_reason == "shell_exec is blocked by risk tier unknown."
    tool_checked = next(event for event in result.events if event["event_type"] == "agent_loop.tool_checked")
    assert tool_checked["risk_tier"] == "unknown"
    assert tool_checked["decision"] == "blocked"
    assert any(event["event_type"] == "tool.blocked" for event in result.events)


def test_bounded_scout_runner_returns_partial_when_iteration_budget_exhausts(knowledge_handler: None) -> None:
    client = FakeLLMClient(
        [
            [
                LLMClientEvent(
                    type=LLM_EVENT_TOOL_CALL,
                    tool_name="knowledge_check",
                    arguments={"prompt": "Use read context"},
                )
            ]
        ]
    )
    runner = BoundedScoutRunner(
        llm_client=client,
        tool_context=_tool_context(),
        run_id="agent-budget",
        registry=_registry(),
        budget=AgentLoopBudget(max_iterations=1, max_tool_calls=2, max_tokens=100),
    )

    result = runner.run([{"role": "user", "content": "Find context then continue."}])

    assert result.status == "partial"
    assert result.budget_exhausted == "max_iterations budget exhausted (1)"
    assert result.tool_results[0].status == "completed"
    assert any(event["event_type"] == "guardrail.blocked" for event in result.events)
    completed = next(event for event in result.events if event["event_type"] == "agent_loop.completed")
    assert completed["budget_exhausted"] == "max_iterations budget exhausted (1)"
