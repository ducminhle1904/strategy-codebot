from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.llm_clients import LLMClientEvent
from strategy_codebot.server.model_routing import PROVIDER_ROUTE_EVENT
from strategy_codebot.server.workflow_prompt_generator import generate_workflow_task_prompt_payload
from strategy_codebot.server.workflow_prompt_generator import workflow_prompt_generator_events
from strategy_codebot.server.workflow_registry import STRATEGY_BOT_WORKFLOW_ID
from strategy_codebot.server.workflow_tasks import build_workflow_task_payload


@dataclass
class PromptGeneratorLLMClient:
    text: str | list[str]
    model: str = "fake-classifier"
    calls: int = 0
    calls_messages: list[list[dict[str, str]]] | None = None
    routing_contexts: list[dict[str, Any] | None] | None = None

    def ensure_configured(self) -> None:
        return None

    def stream(
        self,
        *,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]],
        routing_context: dict[str, Any] | None = None,
    ) -> Iterable[LLMClientEvent]:
        del tools
        self.calls += 1
        if self.calls_messages is None:
            self.calls_messages = []
        if self.routing_contexts is None:
            self.routing_contexts = []
        self.calls_messages.append(messages)
        self.routing_contexts.append(routing_context)
        text = self.text[min(self.calls - 1, len(self.text) - 1)] if isinstance(self.text, list) else self.text
        return [LLMClientEvent(type="message.delta", text=text)]


@dataclass
class RouteAwarePromptGeneratorLLMClient(PromptGeneratorLLMClient):
    route_arguments: dict[str, Any] | None = None

    def stream(
        self,
        *,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]],
        routing_context: dict[str, Any] | None = None,
    ) -> Iterable[LLMClientEvent]:
        self.calls += 1
        if self.calls_messages is None:
            self.calls_messages = []
        if self.routing_contexts is None:
            self.routing_contexts = []
        self.calls_messages.append(messages)
        self.routing_contexts.append(routing_context)
        route_event = LLMClientEvent(
            type=PROVIDER_ROUTE_EVENT,
            arguments=self.route_arguments
            or {
                "model_tier": "paid_low",
                "model_stage": "workflow_fast",
                "provider_route": "litellm_proxy/paid_low.workflow_fast_gemini_flash",
                "provider": "litellm_proxy",
                "model": "paid_low.workflow_fast_gemini_flash",
                "fallback_used": False,
                "attempt_count": 1,
            },
        )
        return [route_event, LLMClientEvent(type="message.delta", text=self.text)]


def test_workflow_prompt_generator_preserves_valid_dynamic_question_and_caps_options() -> None:
    task = build_workflow_task_payload(
        STRATEGY_BOT_WORKFLOW_ID,
        "collect_strategy_inputs",
        input_request_ids=["market", "symbol"],
        status="pending_user",
    )
    assert task is not None
    client = PromptGeneratorLLMClient(
        '{"input_requests":['
        '{"id":"market","question":"Bạn muốn trade market nào trước?",'
        '"options":['
        '{"id":"crypto","value":"crypto","label":"Crypto","description":"Thanh khoản tốt cho paper preview"},'
        '{"id":"stock","value":"stock","label":"Stock","description":"Cổ phiếu niêm yết"},'
        '{"id":"forex","value":"forex","label":"Forex","description":"FX majors"},'
        '{"id":"futures","value":"futures","label":"Futures","description":"Dropped"}'
        '],"recommended_option_id":"stock","custom_option_label":"Nhập market khác"},'
        '{"id":"symbol","question":"Bạn muốn theo dõi mã nào?",'
        '"options":['
        '{"id":"btcusdt","value":"BTCUSDT","label":"BTCUSDT"},'
        '{"id":"ethusdt","value":"ETHUSDT","label":"ETHUSDT"}'
        '],"recommended_option_id":"btcusdt","custom_option_label":"Nhập mã khác"}'
        ']}'
    )

    result = generate_workflow_task_prompt_payload(
        client,
        workflow_id=STRATEGY_BOT_WORKFLOW_ID,
        task_payload=task,
        language="vi",
        user_prompt="Mình muốn bot simulation cho crypto.",
    )

    request = next(item for item in result.payload["input_requests"] if item["id"] == "market")
    symbol = next(item for item in result.payload["input_requests"] if item["id"] == "symbol")
    assert result.status == "generated"
    assert result.target_input_ids == ("market", "symbol")
    assert result.generated_input_ids == ("market", "symbol")
    assert result.fallback_input_ids == ()
    assert result.payload["prompt_source"] == "generated"
    assert request["id"] == "market"
    assert request["question"] == "Bạn muốn trade market nào trước?"
    assert [option["id"] for option in request["options"]] == ["crypto", "stock", "forex"]
    assert request["recommended_option_id"] == "stock"
    assert request["custom_option_label"] == "Nhập market khác"
    assert request["options"][0]["description"] == "Thanh khoản tốt cho paper preview"
    assert symbol["question"] == "Bạn muốn theo dõi mã nào?"
    assert client.routing_contexts == [
        {
            "stage": "workflow_fast",
            "route_timeout_seconds": 8.0,
            "hard_route_timeout": True,
        }
    ]


def test_workflow_prompt_generator_routes_with_auth_and_captures_safe_route_event() -> None:
    task = build_workflow_task_payload(
        STRATEGY_BOT_WORKFLOW_ID,
        "collect_strategy_inputs",
        input_request_ids=["market"],
        status="pending_user",
    )
    assert task is not None
    auth = AuthContext("user-a", "workspace-a", user_tier="paid_low")
    client = RouteAwarePromptGeneratorLLMClient(
        '{"id":"market","question":"Bạn muốn bắt đầu với thị trường nào?",'
        '"options":[{"id":"crypto","value":"crypto","label":"Crypto"}],'
        '"recommended_option_id":"crypto"}'
    )

    result = generate_workflow_task_prompt_payload(
        client,
        workflow_id=STRATEGY_BOT_WORKFLOW_ID,
        task_payload=task,
        language="vi",
        user_prompt="Mình muốn strategy crypto.",
        auth=auth,
    )

    assert result.status == "generated"
    assert result.generated_input_ids == ("market",)
    assert client.routing_contexts is not None
    assert client.routing_contexts[0]["auth"] is auth
    assert client.routing_contexts[0]["user_tier"] == "paid_low"
    assert result.route_events == (
        {
            "model_tier": "paid_low",
            "model_stage": "workflow_fast",
            "provider_route": "litellm_proxy/paid_low.workflow_fast_gemini_flash",
            "provider": "litellm_proxy",
            "model": "paid_low.workflow_fast_gemini_flash",
            "fallback_used": False,
            "attempt_count": 1,
        },
    )
    events = workflow_prompt_generator_events(
        result,
        workflow_id=STRATEGY_BOT_WORKFLOW_ID,
        task_id="wft_1",
        task_template_id="collect_strategy_inputs",
    )
    assert [event_type for event_type, _payload in events] == [
        "workflow_prompt_generator.started",
        "workflow_prompt_generator.route",
        "workflow_prompt_generator.completed",
    ]
    route_payload = events[1][1]
    assert route_payload["provider_route"] == "litellm_proxy/paid_low.workflow_fast_gemini_flash"
    assert route_payload["generated_input_ids"] == ["market"]
    assert "user_prompt" not in route_payload


def test_workflow_prompt_generator_falls_back_for_unknown_input_id() -> None:
    task = build_workflow_task_payload(
        STRATEGY_BOT_WORKFLOW_ID,
        "collect_strategy_inputs",
        input_request_ids=["market"],
        status="pending_user",
    )
    assert task is not None
    client = PromptGeneratorLLMClient('{"id":"unknown","question":"Bad","options":[]}')

    result = generate_workflow_task_prompt_payload(
        client,
        workflow_id=STRATEGY_BOT_WORKFLOW_ID,
        task_payload=task,
        language="en",
        user_prompt="Build a strategy.",
    )

    assert result.status == "fallback"
    assert result.fallback_reason == "invalid_output"
    assert result.payload["fallback_input_ids"] == ["market"]
    assert result.payload["prompt_source"] == "registry_fallback"


def test_workflow_prompt_generator_targets_next_unanswered_input_from_task_values() -> None:
    task = build_workflow_task_payload(
        STRATEGY_BOT_WORKFLOW_ID,
        "collect_strategy_inputs",
        input_request_ids=["market", "symbol", "style"],
        status="pending_user",
    )
    assert task is not None
    client = PromptGeneratorLLMClient(
        '{"input_requests":['
        '{"id":"symbol","question":"Crypto đã chọn. Symbol nào nên dùng?",'
        '"options":['
        '{"id":"ethusdt","value":"ETHUSDT","label":"ETHUSDT","description":"Altcoin thanh khoản cao"},'
        '{"id":"btcusdt","value":"BTCUSDT","label":"BTCUSDT","description":"Benchmark crypto"},'
        '{"id":"solusdt","value":"SOLUSDT","label":"SOLUSDT","description":"Biến động cao"}'
        '],"recommended_option_id":"ethusdt"},'
        '{"id":"style","question":"Crypto 1h nên ưu tiên style nào?",'
        '"options":['
        '{"id":"trend","value":"trend","label":"Trend"},'
        '{"id":"breakout","value":"breakout","label":"Breakout"}'
        '],"recommended_option_id":"trend"}'
        ']}'
    )

    result = generate_workflow_task_prompt_payload(
        client,
        workflow_id=STRATEGY_BOT_WORKFLOW_ID,
        task_payload=task,
        language="vi",
        user_prompt="Mình chọn crypto.",
        task_values={"market": "crypto"},
    )

    assert result.status == "generated"
    assert result.target_input_ids == ("symbol", "style")
    assert result.generated_input_ids == ("symbol", "style")
    symbol = next(request for request in result.payload["input_requests"] if request["id"] == "symbol")
    style = next(request for request in result.payload["input_requests"] if request["id"] == "style")
    assert symbol["question"] == "Crypto đã chọn. Symbol nào nên dùng?"
    assert [option["label"] for option in symbol["options"]] == ["ETHUSDT", "BTCUSDT", "SOLUSDT"]
    assert style["question"] == "Crypto 1h nên ưu tiên style nào?"


def test_workflow_prompt_generator_repairs_missing_target_ids_before_fallback() -> None:
    task = build_workflow_task_payload(
        STRATEGY_BOT_WORKFLOW_ID,
        "collect_strategy_inputs",
        input_request_ids=["market", "symbol"],
        status="pending_user",
    )
    assert task is not None
    client = PromptGeneratorLLMClient(
        [
            '{"input_requests":[{"id":"market","question":"Market nào?",'
            '"options":[{"id":"crypto","value":"crypto","label":"Crypto"}],'
            '"recommended_option_id":"crypto"}]}',
            '{"input_requests":[{"id":"symbol","question":"Mã nào?",'
            '"options":[{"id":"btcusdt","value":"BTCUSDT","label":"BTCUSDT"}],'
            '"recommended_option_id":"btcusdt"}]}',
        ]
    )

    result = generate_workflow_task_prompt_payload(
        client,
        workflow_id=STRATEGY_BOT_WORKFLOW_ID,
        task_payload=task,
        language="vi",
        user_prompt="Mình muốn crypto.",
    )

    assert client.calls == 2
    assert result.status == "generated"
    assert result.generated_input_ids == ("market", "symbol")
    assert result.fallback_input_ids == ()
    assert result.payload["prompt_source"] == "generated"


def test_workflow_prompt_generator_audits_partial_registry_fallback_ids() -> None:
    task = build_workflow_task_payload(
        STRATEGY_BOT_WORKFLOW_ID,
        "collect_strategy_inputs",
        input_request_ids=["market", "symbol"],
        status="pending_user",
    )
    assert task is not None
    client = PromptGeneratorLLMClient(
        '{"input_requests":[{"id":"market","question":"Market nào?",'
        '"options":[{"id":"crypto","value":"crypto","label":"Crypto"}],'
        '"recommended_option_id":"crypto"}]}'
    )

    result = generate_workflow_task_prompt_payload(
        client,
        workflow_id=STRATEGY_BOT_WORKFLOW_ID,
        task_payload=task,
        language="vi",
        user_prompt="Mình muốn crypto.",
    )

    assert result.status == "generated"
    assert result.fallback_reason == "partial_registry_fallback"
    assert result.generated_input_ids == ("market",)
    assert result.fallback_input_ids == ("symbol",)
    assert result.payload["fallback_input_ids"] == ["symbol"]
