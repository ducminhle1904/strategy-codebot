import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

from fastapi.testclient import TestClient

from strategy_codebot.server import create_app
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.conversation_context import ConversationContextBuilder
from strategy_codebot.server.database import create_sqlite_repository
from strategy_codebot.server.llm_clients import LLMClientEvent
from strategy_codebot.server.llm_clients import LLM_EVENT_SOURCES
from strategy_codebot.server.llm_clients import ProviderTimeoutError
from strategy_codebot.server.llm_clients import chat_completion_events
from strategy_codebot.server.llm_clients import response_events
from strategy_codebot.server.llm_clients import stream_response_events
from strategy_codebot.server.llm_orchestrator import LLMOrchestrator
from strategy_codebot.server.llm_orchestrator import ActionPlanner
from strategy_codebot.server.llm_orchestrator import ActionPlanDecision
from strategy_codebot.server.llm_orchestrator import ChatIntentDecisionPlanner
from strategy_codebot.server.llm_orchestrator import ResponseIntentClassifier
from strategy_codebot.server.llm_orchestrator import _classify_domain_scope
from strategy_codebot.server.llm_orchestrator import _classifier_timeout_seconds
from strategy_codebot.server.llm_orchestrator import _classify_response_intent
from strategy_codebot.server.llm_orchestrator import _model_stage_for_chat
from strategy_codebot.server.llm_orchestrator import _parse_action_plan_json
from strategy_codebot.server.llm_orchestrator import _parse_chat_intent_decision_json
from strategy_codebot.server.llm_orchestrator import _sanitize_user_facing_model_text
from strategy_codebot.server.llm_orchestrator import _should_enable_web_search_auto
from strategy_codebot.server.llm_orchestrator import _action_planner_system_prompt
from strategy_codebot.server.llm_orchestrator import _chat_intent_decision_system_prompt
from strategy_codebot.server.llm_orchestrator import _direct_action_plan_tool_args
from strategy_codebot.server.llm_orchestrator import _suggestions_payload
from strategy_codebot.server.llm_orchestrator import _system_prompt
from strategy_codebot.server.llm_orchestrator import _maybe_backtest_summary_response
from strategy_codebot.server.llm_orchestrator import _maybe_backtest_trades_response
from strategy_codebot.server.llm_orchestrator import _tool_only_success_message
from strategy_codebot.server.llm_orchestrator import _tool_success_result
from strategy_codebot.server.market_data import MarketDataGateway
from strategy_codebot.server.schemas import MessageCreate
from strategy_codebot.server.llm_tools import compact_tool_output, tool_catalog_consistency_errors
from strategy_codebot.pine import generate_pine
from server_helpers import parse_sse
from server_helpers import valid_spec

AUTH_A = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-a"}
AUTH_B = {"X-User-Id": "user-b", "X-Workspace-Id": "workspace-a"}
AUTH_OTHER_WORKSPACE = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-b"}


def test_classifier_timeout_defaults_to_route_aware_budget(monkeypatch) -> None:
    monkeypatch.delenv("STRATEGY_CODEBOT_CLASSIFIER_TIMEOUT_SECONDS", raising=False)

    assert _classifier_timeout_seconds() == 25.0


def test_classifier_timeout_env_override_still_wins(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_CLASSIFIER_TIMEOUT_SECONDS", "3.5")

    assert _classifier_timeout_seconds() == 3.5


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
class SummaryFailingAfterAnswerClient:
    model: str = "fake-responses-model"
    calls_messages: list[list[dict[str, str]]] = field(default_factory=list)

    def ensure_configured(self) -> None:
        return None

    def stream(self, *, messages: list[dict[str, str]], tools: list[dict]) -> Iterable[LLMClientEvent]:
        self.calls_messages.append(messages)
        if len(self.calls_messages) in {1, 2}:
            return [LLMClientEvent(type="message.delta", text="not an action plan")]
        if len(self.calls_messages) == 3:
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


def test_system_prompt_includes_selected_language_instruction() -> None:
    prompt = _system_prompt("vi")

    assert "Respond in Vietnamese" in prompt
    assert "Pine syntax" in prompt
    assert "JSON schema keys" in prompt


def test_agent_chat_web_search_mode_controls_provider_tool() -> None:
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth)
    llm = RecordingLLMClient([LLMClientEvent(type="message.delta", text="ok")])
    orchestrator = LLMOrchestrator(
        repository=repository,
        artifact_store=None,
        client=llm,
    )

    list(orchestrator.stream_chat(auth=auth, conversation_id=conversation.id, message_content="build an EMA strategy", web_search="off"))
    list(orchestrator.stream_chat(auth=auth, conversation_id=conversation.id, message_content="build an EMA strategy", web_search="auto"))
    list(orchestrator.stream_chat(auth=auth, conversation_id=conversation.id, message_content="create a price action strategy", web_search="auto"))
    list(orchestrator.stream_chat(auth=auth, conversation_id=conversation.id, message_content="research latest Pine docs", web_search="auto"))
    list(orchestrator.stream_chat(auth=auth, conversation_id=conversation.id, message_content="what is the current BTC price", web_search="auto"))
    list(orchestrator.stream_chat(auth=auth, conversation_id=conversation.id, message_content="build an EMA strategy", web_search="on"))

    enabled_flags = [
        any(tool.get("type") == "web_search" for tool in tools)
        for tools in llm.calls_tools
    ]
    assert len(enabled_flags) == 18
    assert enabled_flags[0::3] == [False, False, False, False, False, False]
    assert enabled_flags[1::3] == [False, False, False, False, False, False]
    assert enabled_flags[2::3] == [
        False,
        False,
        False,
        True,
        True,
        True,
    ]
    assert _should_enable_web_search_auto("generate from current strategy context") is False


def test_response_intent_classifier_distinguishes_market_from_strategy() -> None:
    assert _classify_response_intent("what is the current ETH price?", web_search="auto") == "market_snapshot"
    assert _classify_response_intent("analyze current ETH market", web_search="auto") == "market_snapshot"
    assert _classify_response_intent("build an EMA strategy with risk rules", web_search="auto") == "strategy_building"
    assert _classify_response_intent("generate Pine v6 code for this strategy", web_search="auto") == "artifact_generation"
    assert _classify_response_intent("check latest OpenRouter pricing docs", web_search="auto") == "docs_research"


def test_response_intent_classifier_uses_deterministic_fast_path_without_llm() -> None:
    llm = RecordingLLMClient([LLMClientEvent(type="message.delta", text='{"intent":"general_chat","confidence":1}')])

    classification = ResponseIntentClassifier(llm).classify("what is the current ETH price?", web_search="auto")

    assert classification.intent == "market_snapshot"
    assert classification.source == "deterministic"
    assert classification.confidence >= 0.9
    assert llm.calls == 0


def test_response_intent_classifier_market_condition_followup_is_deterministic() -> None:
    llm = RecordingLLMClient([LLMClientEvent(type="message.delta", text='{"intent":"general_chat","confidence":1}')])

    classification = ResponseIntentClassifier(llm).classify(
        "what should I do with this market condition?",
        web_search="auto",
    )

    assert classification.intent == "market_research"
    assert classification.source == "deterministic"
    assert classification.confidence >= 0.9
    assert llm.calls == 0


def test_response_intent_classifier_uses_llm_for_semantic_paraphrase() -> None:
    llm = RecordingLLMClient([LLMClientEvent(type="message.delta", text='{"intent":"market_snapshot","confidence":0.86}')])

    classification = ResponseIntentClassifier(llm).classify("ETH đang bao nhiêu rồi?", web_search="auto")

    assert classification.intent == "market_snapshot"
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
                "missing_inputs": [],
                "reasons": ["The user asks to simulate strategy performance."],
                "used_signals": ["preview_intent"],
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
    assert decision.should_start_auto_chain() is True


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
    assert decision.should_start_auto_chain() is True
    assert llm.calls == 1


def test_chat_intent_decision_prompt_marks_regex_as_hint() -> None:
    prompt = _chat_intent_decision_system_prompt()

    assert "Regex evidence is only a hint" in prompt
    assert "start_auto_chain" in prompt
    assert "chay thu" in prompt


def test_agent_chat_emits_source_backed_market_snapshot(tmp_path: Path) -> None:
    llm = FakeLLMClient(
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
    assert intent["data"]["payload"]["source"] == "deterministic"
    assert intent["data"]["payload"]["confidence"] >= 0.9
    assert snapshot["data"]["payload"]["symbol"] == "ETH"
    assert snapshot["data"]["payload"]["source_count"] == 1
    suggestions = next(frame for frame in frames if frame["event"] == "chat.suggestions.updated")
    assert suggestions["data"]["payload"]["composer_blocks"] == []


def test_market_chat_allows_reference_urls_without_policy_block(tmp_path: Path) -> None:
    llm = FakeLLMClient(
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
    llm = FakeLLMClient(
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
    llm = FakeLLMClient([LLMClientEvent(type="message.delta", text="ETH is $1,700 today.")])
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
    llm = FakeLLMClient([LLMClientEvent(type="message.delta", text="ETH is source-backed.")])
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
    llm = FakeLLMClient([LLMClientEvent(type="message.delta", text="Let's make the rules clearer.")])
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
    assert len(llm.calls_messages) == 3


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


def test_domain_scope_guard_allows_trading_and_product_requests() -> None:
    assert _classify_domain_scope("write a Pine strategy for BTC risk review").allowed is True
    assert _classify_domain_scope("latest OpenRouter model pricing docs source?").allowed is True
    assert _classify_domain_scope(
        "Build a review-only robustness report for the current preview evidence. "
        "Summarize sample size, fees, slippage, drawdown, OOS concerns, and suspicious metrics."
    ).allowed is True
    contextual = _classify_domain_scope(
        "summarize the current preview evidence",
        artifact_kinds={"backtest_report"},
    )
    assert contextual.allowed is True
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
            [LLMClientEvent(type="message.delta", text='{"intent":"market_snapshot","confidence":0.9}')],
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

    assert len(llm.calls_messages) == 3
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
    assert "Summarize conversation memory" in llm.calls_messages[3][0]["content"]

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

    assert len(llm.calls_messages) == 7


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
    assert len(llm.calls_messages) == 4
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
        json={"content": "generate pine"},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    assert "tool.started" in [frame["event"] for frame in frames]
    completed = next(frame for frame in frames if frame["event"] == "tool.completed")
    assert completed["data"]["payload"]["tool_id"] == "generate_pine"
    assert completed["data"]["payload"]["output"]["pine_code"].startswith("//@version=6")
    assert completed["data"]["payload"]["output"]["artifact_id"]
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
    assert {artifact["kind"] for artifact in state["latest_run_artifacts"]} == {"pine_file"}
    assert state["latest_run_artifacts"][0]["display_name"] == "strategy.pine"
    assert state["strategy_profile"]["source"] == "strategy_spec"
    replay = client.get(f"/v1/runs/{completed['data']['run_id']}/events", headers=AUTH_A).text
    assert "artifact.created" in [frame["event"] for frame in parse_sse(replay)]


def test_explicit_backtest_prompt_auto_chains_to_pending_approval(monkeypatch, tmp_path: Path) -> None:
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
    assert completed_tools[:2] == ["generate_pine", "create_backtest_plan"]
    assert "run_backtest_preview" not in completed_tools
    assert "chat.auto_chain.started" in [frame["event"] for frame in frames]
    assert "backtest.preview.approval_required" in [frame["event"] for frame in frames]
    assert "chat.auto_chain.waiting_for_backtest" not in [frame["event"] for frame in frames]
    plan = next(
        frame
        for frame in frames
        if frame["event"] == "tool.completed" and frame["data"]["payload"]["tool_id"] == "create_backtest_plan"
    )
    assert plan["data"]["payload"]["output"]["requires_user_approval"] is True
    assert isinstance(plan["data"]["payload"]["output"]["approval_id"], str)
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
        json={"content": "run shell"},
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
