import json
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
from strategy_codebot.server.llm_clients import response_events
from strategy_codebot.server.llm_clients import stream_response_events
from strategy_codebot.server.llm_orchestrator import LLMOrchestrator
from strategy_codebot.server.llm_orchestrator import ResponseIntentClassifier
from strategy_codebot.server.llm_orchestrator import _classify_response_intent
from strategy_codebot.server.llm_orchestrator import _should_enable_web_search_auto
from strategy_codebot.server.llm_orchestrator import _system_prompt
from strategy_codebot.server.market_data import MarketDataGateway
from strategy_codebot.server.schemas import MessageCreate
from strategy_codebot.server.llm_tools import compact_tool_output, tool_catalog_consistency_errors
from strategy_codebot.pine import generate_pine
from server_helpers import parse_sse
from server_helpers import valid_spec

AUTH_A = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-a"}
AUTH_B = {"X-User-Id": "user-b", "X-Workspace-Id": "workspace-a"}
AUTH_OTHER_WORKSPACE = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-b"}


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
        if len(self.calls_messages) == 1:
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
    assert "review-only" in prompt


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
    assert enabled_flags == [False, False, False, True, True, True]
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
    assert [action["category"] for action in payload["actions"]] == ["market", "strategy"]
    assert "code" not in {action["category"] for action in payload["actions"]}


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
    assert any(action["id"] == "add-risk" for action in payload["actions"])


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
    assert llm.calls_tools[1] == [{"type": "web_search"}]


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
    provider_messages = llm.calls_messages[0]
    contents = [message["content"] for message in provider_messages]
    assert "My strategy uses EMA 20/50 crossover." in contents
    assert "I can help review that strategy." in contents
    assert contents.count("What risk rule did I mention?") == 1
    run_id = parse_sse(frames[0])[0]["data"]["run_id"]
    events = repository.list_run_events(auth, run_id)
    assert events is not None
    context_event = next(event for event in events if event.type == "context.built")
    assert context_event.payload["history_message_count"] == 2


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

    assert len(llm.calls_messages) == 1
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
            [LLMClientEvent(type="message.delta", text="Risk noted.")],
            [LLMClientEvent(type="message.delta", text="Summary: EMA crossover strategy with 1% risk.")],
            [LLMClientEvent(type="message.delta", text="Second answer.")],
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
    assert "Summarize conversation memory" in llm.calls_messages[1][0]["content"]

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

    assert len(llm.calls_messages) == 3


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
    assert len(llm.calls_messages) == 2
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
    assert "Generated review-only Pine v6 code" in fallback_delta["data"]["payload"]["text"]
    messages = client.get(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH_A).json()["items"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert "Generated review-only Pine v6 code" in messages[1]["content"]
    assert "policy.blocked" not in [frame["event"] for frame in frames]
    state = client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A).json()
    assert {artifact["kind"] for artifact in state["latest_run_artifacts"]} == {"pine_file"}
    assert state["latest_run_artifacts"][0]["display_name"] == "strategy.pine"
    assert state["strategy_profile"]["source"] == "strategy_spec"
    replay = client.get(f"/v1/runs/{completed['data']['run_id']}/events", headers=AUTH_A).text
    assert "artifact.created" in [frame["event"] for frame in parse_sse(replay)]


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
