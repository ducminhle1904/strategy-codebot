from collections.abc import Iterable

import pytest

from strategy_codebot.server.llm_clients import LLMClientEvent
from strategy_codebot.server.llm_clients import ProviderConfigurationError
from strategy_codebot.server.llm_clients import ProviderTimeoutError
from strategy_codebot.server.model_routing import DEFAULT_MODEL_STAGE
from strategy_codebot.server.model_routing import MODEL_STAGE_PINE_CODE_GENERATION
from strategy_codebot.server.model_routing import PROVIDER_ROUTE_EVENT
from strategy_codebot.server.model_routing import RegistryRoutedLLMClient
from strategy_codebot.server.model_routing import load_model_registry
from strategy_codebot.server.model_routing import normalize_user_tier
from strategy_codebot.server.model_routing import model_registry_path_from_env
from strategy_codebot.server.model_routing import resolve_routes
from strategy_codebot.server.provider_errors import provider_run_failed_payload
from strategy_codebot.server.provider_errors import run_failed_payload
from strategy_codebot.server.tool_errors import ToolExecutionError


def test_tier_aliases_normalize_to_registry_names() -> None:
    assert normalize_user_tier("paid.cheap") == "paid_low"
    assert normalize_user_tier("paid.medium") == "paid_medium"
    assert normalize_user_tier("paid.high") == "paid_high"
    assert normalize_user_tier("local") == "dev"
    assert normalize_user_tier("local-dev") == "dev"
    assert normalize_user_tier("free") == "free"


def test_default_registry_has_dev_direct_routes() -> None:
    registry = load_model_registry(model_registry_path_from_env())

    routes = resolve_routes(registry, tier="local", stage=MODEL_STAGE_PINE_CODE_GENERATION)

    assert routes
    assert all(route.startswith("openrouter/") for route in routes)


def test_route_resolver_returns_registry_order() -> None:
    registry = {
        "model_tiers": {
            "paid_low": {
                "routes_by_stage": {
                    DEFAULT_MODEL_STAGE: ["litellm_proxy/paid_low.strategy_reasoning"],
                    MODEL_STAGE_PINE_CODE_GENERATION: [
                        "litellm_proxy/paid_low.pine_code_generation_qwen",
                        "litellm_proxy/paid_medium.pine_code_generation",
                    ],
                }
            }
        }
    }

    routes = resolve_routes(registry, tier="paid.cheap", stage=MODEL_STAGE_PINE_CODE_GENERATION)

    assert routes == [
        "litellm_proxy/paid_low.pine_code_generation_qwen",
        "litellm_proxy/paid_medium.pine_code_generation",
    ]


def test_route_resolver_falls_back_to_strategy_reasoning_stage() -> None:
    registry = {
        "model_tiers": {
            "paid_low": {
                "routes_by_stage": {
                    DEFAULT_MODEL_STAGE: ["litellm_proxy/paid_low.strategy_reasoning"],
                }
            }
        }
    }

    assert resolve_routes(registry, tier="paid.cheap", stage="unknown_stage") == [
        "litellm_proxy/paid_low.strategy_reasoning"
    ]


class FakeRouteClient:
    def __init__(
        self,
        route: str,
        *,
        error: Exception | None = None,
        events: list[LLMClientEvent] | None = None,
        stream_error: Exception | None = None,
    ) -> None:
        self.route = route
        self.model = route.removeprefix("openrouter/").removeprefix("litellm_proxy/")
        self.error = error
        self.events = events
        self.stream_error = stream_error

    def ensure_configured(self) -> None:
        if self.error is not None:
            raise self.error

    def stream(
        self,
        *,
        messages: list[dict[str, str]],
        tools: list[dict],
        routing_context: dict | None = None,
    ) -> Iterable[LLMClientEvent]:
        if self.stream_error is not None:
            raise self.stream_error
        if self.events is not None:
            yield from self.events
            return
        yield LLMClientEvent(type="usage", model=self.model, input_tokens=10, output_tokens=4)
        yield LLMClientEvent(type="message.delta", text="ok", model=self.model)


def test_registry_client_skips_unconfigured_route_and_emits_selected_route(tmp_path) -> None:
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        """
model_tiers:
  paid_low:
    routes_by_stage:
      strategy_reasoning:
        - litellm_proxy/missing.alias
        - openrouter/openai/gpt-5.5
""",
        encoding="utf-8",
    )

    def factory(route: str) -> FakeRouteClient:
        if route == "litellm_proxy/missing.alias":
            return FakeRouteClient(route, error=ProviderConfigurationError("missing env"))
        return FakeRouteClient(route)

    client = RegistryRoutedLLMClient(registry_path=registry_path, client_factory=factory)

    events = list(
        client.stream(
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
            routing_context={"user_tier": "paid.cheap", "stage": DEFAULT_MODEL_STAGE},
        )
    )

    assert events[0].type == PROVIDER_ROUTE_EVENT
    assert events[0].arguments["provider_route"] == "openrouter/openai/gpt-5.5"
    assert events[0].arguments["fallback_used"] is True
    assert events[0].arguments["attempt_count"] == 2
    assert events[1].type == "usage"
    assert events[1].arguments["provider_route"] == "openrouter/openai/gpt-5.5"


def test_registry_client_falls_back_on_provider_timeout(tmp_path) -> None:
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        """
model_tiers:
  paid_medium:
    routes_by_stage:
      pine_code_generation:
        - openrouter/timeout-model
        - openrouter/working-model
""",
        encoding="utf-8",
    )

    def factory(route: str) -> FakeRouteClient:
        if route == "openrouter/timeout-model":
            return FakeRouteClient(route, stream_error=ProviderTimeoutError("slow"))
        return FakeRouteClient(route)

    client = RegistryRoutedLLMClient(registry_path=registry_path, client_factory=factory)

    events = list(
        client.stream(
            messages=[{"role": "user", "content": "generate pine"}],
            tools=[],
            routing_context={"user_tier": "paid.medium", "stage": MODEL_STAGE_PINE_CODE_GENERATION},
        )
    )

    assert events[0].type == PROVIDER_ROUTE_EVENT
    assert events[0].arguments["provider_route"] == "openrouter/timeout-model"
    assert events[0].arguments["fallback_used"] is False
    assert events[1].type == PROVIDER_ROUTE_EVENT
    assert events[1].arguments["provider_route"] == "openrouter/working-model"
    assert events[1].arguments["fallback_used"] is True
    assert events[1].arguments["fallback_attempts"][0]["error"] == "ProviderTimeoutError"


def test_registry_client_falls_back_on_quota_api_error_without_status(tmp_path) -> None:
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        """
model_tiers:
  paid_low:
    routes_by_stage:
      strategy_reasoning:
        - litellm_proxy/openai-quota
        - litellm_proxy/openrouter-working
""",
        encoding="utf-8",
    )

    class APIError(Exception):
        pass

    def factory(route: str) -> FakeRouteClient:
        if route == "litellm_proxy/openai-quota":
            return FakeRouteClient(
                route,
                error=APIError("You exceeded your current quota, please check your plan and billing details."),
            )
        return FakeRouteClient(route)

    client = RegistryRoutedLLMClient(registry_path=registry_path, client_factory=factory)

    events = list(
        client.stream(
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
            routing_context={"user_tier": "paid_low", "stage": DEFAULT_MODEL_STAGE},
        )
    )

    assert events[0].arguments["provider_route"] == "litellm_proxy/openrouter-working"
    assert events[0].arguments["fallback_used"] is True
    assert events[0].arguments["fallback_attempts"][0]["error"] == "APIError"


def test_registry_client_does_not_add_route_metadata_to_tool_arguments(tmp_path) -> None:
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        """
model_tiers:
  paid_low:
    routes_by_stage:
      pine_code_generation:
        - litellm_proxy/pine-route
""",
        encoding="utf-8",
    )

    def factory(route: str) -> FakeRouteClient:
        return FakeRouteClient(
            route,
            events=[
                LLMClientEvent(
                    type="tool.call",
                    tool_name="generate_pine",
                    arguments={"strategy_spec": {"name": "Breakout"}},
                )
            ],
        )

    client = RegistryRoutedLLMClient(registry_path=registry_path, client_factory=factory)

    events = list(
        client.stream(
            messages=[{"role": "user", "content": "generate pine"}],
            tools=[],
            routing_context={"user_tier": "paid_low", "stage": MODEL_STAGE_PINE_CODE_GENERATION},
        )
    )

    assert events[0].type == PROVIDER_ROUTE_EVENT
    assert events[1].type == "tool.call"
    assert events[1].arguments == {"strategy_spec": {"name": "Breakout"}}


def test_quota_api_error_maps_to_rate_limited_payload() -> None:
    class APIError(Exception):
        pass

    payload = provider_run_failed_payload(
        APIError("You exceeded your current quota, please check your plan and billing details.")
    )

    assert payload["code"] == "provider_rate_limited"
    assert payload["retryable"] is True


def test_tool_execution_error_maps_to_workflow_failure_payload() -> None:
    payload = run_failed_payload(
        ToolExecutionError(
            code="pine_validation_failed",
            message="Backtest plan failed because local Pine validation failed.",
            details={"validation_artifact_id": "artifact_validation"},
        )
    )

    assert payload["code"] == "pine_validation_failed"
    assert payload["dimension"] == "workflow"
    assert payload["retryable"] is False
    assert payload["message"] == "Backtest plan failed because local Pine validation failed."
    assert payload["validation_artifact_id"] == "artifact_validation"


def test_registry_client_raises_when_all_routes_fail(tmp_path) -> None:
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        """
model_tiers:
  paid_low:
    routes_by_stage:
      strategy_reasoning:
        - litellm_proxy/missing.alias
""",
        encoding="utf-8",
    )
    client = RegistryRoutedLLMClient(
        registry_path=registry_path,
        client_factory=lambda route: FakeRouteClient(route, error=ProviderConfigurationError("missing")),
    )

    with pytest.raises(ProviderConfigurationError):
        list(client.stream(messages=[{"role": "user", "content": "hello"}], tools=[], routing_context={}))
