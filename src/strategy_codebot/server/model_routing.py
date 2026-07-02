import inspect
import os
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty
from queue import Queue
from typing import Any

import yaml

from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.llm_clients import ChatCompletionsClient
from strategy_codebot.server.llm_clients import LLMClient
from strategy_codebot.server.llm_clients import LLMClientEvent
from strategy_codebot.server.llm_clients import ProviderConfigurationError
from strategy_codebot.server.llm_clients import ProviderTimeoutError
from strategy_codebot.server.llm_clients import ResponsesClient

DEFAULT_MODEL_STAGE = "strategy_reasoning"
MODEL_STAGE_CLASSIFIER = "classifier"
MODEL_STAGE_WORKFLOW_FAST = "workflow_fast"
MODEL_STAGE_STRATEGY_CODING = "strategy_coding"
MODEL_STAGE_PINE_CODE_GENERATION = "pine_code_generation"
MODEL_STAGE_BALANCED_REVIEW = "balanced_review"
MODEL_STAGE_REPAIR = "repair"
MODEL_STAGE_KNOWLEDGE_LEARNING_REVIEW = "knowledge_learning_review"
CHAT_INTENT_MODEL_STAGE_VALUES = frozenset(
    {
        DEFAULT_MODEL_STAGE,
        MODEL_STAGE_CLASSIFIER,
        MODEL_STAGE_WORKFLOW_FAST,
        MODEL_STAGE_STRATEGY_CODING,
        MODEL_STAGE_PINE_CODE_GENERATION,
        MODEL_STAGE_BALANCED_REVIEW,
        MODEL_STAGE_REPAIR,
    }
)
DEFAULT_USER_TIER = "paid_low"
DEFAULT_REGISTRY_RELATIVE_PATH = "configs/model-registry.example.yaml"
DEFAULT_ROUTE_TIMEOUT_SECONDS = 25.0
DEFAULT_ROUTE_KEEPALIVE_SECONDS = 15.0
PROVIDER_KEEPALIVE_EVENT = "provider.keepalive"
PROVIDER_ROUTE_EVENT = "provider.route"

TIER_ALIASES = {
    "paid.cheap": "paid_low",
    "paid_cheap": "paid_low",
    "paid.low": "paid_low",
    "paid-low": "paid_low",
    "paid_low": "paid_low",
    "paid.medium": "paid_medium",
    "paid-medium": "paid_medium",
    "paid_medium": "paid_medium",
    "paid.high": "paid_high",
    "paid-high": "paid_high",
    "paid_high": "paid_high",
    "dev": "dev",
    "local": "dev",
    "local_dev": "dev",
    "local-dev": "dev",
    "free": "free",
}


class ModelRouteUnavailable(ProviderConfigurationError):
    pass


ClientFactory = Callable[..., LLMClient]


def model_registry_path_from_env() -> Path:
    configured = os.getenv("STRATEGY_CODEBOT_MODEL_REGISTRY", DEFAULT_REGISTRY_RELATIVE_PATH).strip()
    path = Path(configured)
    if path.is_absolute():
        return path
    return _repo_root() / path


def load_model_registry(path: str | Path | None = None) -> dict[str, Any]:
    registry_path = Path(path) if path is not None else model_registry_path_from_env()
    with registry_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise ProviderConfigurationError(f"Model registry is not a mapping: {registry_path}")
    return loaded


def normalize_user_tier(value: str | None) -> str:
    tier = (value or os.getenv("STRATEGY_CODEBOT_SERVER_USER_TIER") or DEFAULT_USER_TIER).strip()
    if not tier:
        return DEFAULT_USER_TIER
    return TIER_ALIASES.get(tier.lower(), tier)


def resolve_routes(registry: dict[str, Any], *, tier: str | None, stage: str | None) -> list[str]:
    normalized_tier = normalize_user_tier(tier)
    normalized_stage = stage or DEFAULT_MODEL_STAGE
    tiers = registry.get("model_tiers") or {}
    tier_config = tiers.get(normalized_tier) or tiers.get(DEFAULT_USER_TIER) or {}
    routes_by_stage = tier_config.get("routes_by_stage") or {}
    routes = routes_by_stage.get(normalized_stage) or routes_by_stage.get(DEFAULT_MODEL_STAGE) or []
    return [str(route) for route in routes if str(route).strip()]


def gateway_env_report() -> dict[str, Any]:
    gateways = {
        "litellm_proxy": {"api_key": "LITELLM_PROXY_API_KEY", "base_url": "LITELLM_PROXY_API_BASE"},
        "openrouter": {"api_key": "OPENROUTER_API_KEY"},
        "vercel_ai_gateway": {"api_key": "VERCEL_AI_GATEWAY_API_KEY"},
        "openai": {"api_key": "OPENAI_API_KEY"},
    }
    available: list[str] = []
    missing: dict[str, list[str]] = {}
    for gateway, envs in gateways.items():
        required = {label: env for label, env in envs.items() if label != "base_url"}
        absent = [label for label, env in required.items() if not os.getenv(env)]
        if absent:
            missing[gateway] = absent
        else:
            available.append(gateway)
    return {"available_gateways": available, "missing_gateway_envs": missing}


def default_route_client_factory(route: str, *, timeout_seconds: float | None = None) -> LLMClient:
    provider, model = _split_route(route)
    timeout_seconds = timeout_seconds if timeout_seconds is not None else _route_timeout_seconds()
    if provider == "litellm_proxy":
        return ChatCompletionsClient(
            model=model,
            api_key=os.getenv("LITELLM_PROXY_API_KEY"),
            base_url=os.getenv("LITELLM_PROXY_API_BASE", "http://litellm-proxy:4000/v1"),
            timeout_seconds=timeout_seconds,
        )
    if provider == "openrouter":
        return ChatCompletionsClient(
            model=model,
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url=os.getenv("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1"),
            timeout_seconds=timeout_seconds,
        )
    if provider in {"vercel_ai_gateway", "vercel-ai-gateway"}:
        return ChatCompletionsClient(
            model=model,
            api_key=os.getenv("VERCEL_AI_GATEWAY_API_KEY"),
            base_url=os.getenv("VERCEL_AI_GATEWAY_API_BASE", "https://ai-gateway.vercel.sh/v1"),
            timeout_seconds=timeout_seconds,
        )
    if provider == "openai":
        return ResponsesClient(model=model.removeprefix("openai/"), api_key=os.getenv("OPENAI_API_KEY"), timeout_seconds=timeout_seconds)
    raise ModelRouteUnavailable(f"Unsupported model route provider: {provider}")


class RegistryRoutedLLMClient:
    model = "registry-routed"

    def __init__(
        self,
        *,
        registry_path: str | Path | None = None,
        client_factory: ClientFactory = default_route_client_factory,
    ) -> None:
        self.registry_path = Path(registry_path) if registry_path is not None else model_registry_path_from_env()
        self._client_factory = client_factory

    def ensure_configured(self) -> None:
        registry = load_model_registry(self.registry_path)
        tier = normalize_user_tier(None)
        routes = resolve_routes(registry, tier=tier, stage=DEFAULT_MODEL_STAGE)
        if not routes:
            raise ProviderConfigurationError(f"No model routes configured for tier={tier} stage={DEFAULT_MODEL_STAGE}")
        for route in routes:
            try:
                self._client_factory(route).ensure_configured()
                return
            except Exception as exc:
                if not _is_fallbackable_provider_error(exc):
                    raise
        raise ProviderConfigurationError(f"No configured model route is available for tier={tier}")

    def stream(
        self,
        *,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]],
        routing_context: dict[str, Any] | None = None,
    ) -> Iterable[LLMClientEvent]:
        context = routing_context or {}
        auth = context.get("auth")
        tier = normalize_user_tier(getattr(auth, "user_tier", None) if isinstance(auth, AuthContext) else context.get("user_tier"))
        stage = str(context.get("stage") or DEFAULT_MODEL_STAGE)
        registry = load_model_registry(self.registry_path)
        routes = resolve_routes(registry, tier=tier, stage=stage)
        if not routes:
            raise ProviderConfigurationError(f"No model routes configured for tier={tier} stage={stage}")
        route_timeout_seconds = _route_timeout_seconds_from_context(context)
        route_keepalive_seconds = _route_keepalive_seconds_from_context(context)
        hard_route_timeout = context.get("hard_route_timeout") is True

        failures: list[dict[str, Any]] = []
        for attempt_index, route in enumerate(routes):
            client: LLMClient
            try:
                client = _create_route_client(
                    self._client_factory,
                    route,
                    timeout_seconds=route_timeout_seconds,
                )
                client.ensure_configured()
            except Exception as exc:
                if not _is_fallbackable_provider_error(exc):
                    raise
                failures.append(
                    {
                        "provider_route": route,
                        "error": exc.__class__.__name__,
                        "message": str(exc),
                    }
                )
                continue
            route_payload = {
                "model_tier": tier,
                "model_stage": stage,
                "provider_route": route,
                "provider": _split_route(route)[0],
                "model": client.model,
                "fallback_used": attempt_index > 0,
                "attempt_count": attempt_index + 1,
                "fallback_attempts": failures,
            }
            yield LLMClientEvent(type=PROVIDER_ROUTE_EVENT, arguments=route_payload, model=client.model)
            emitted_provider_content = False
            try:
                events = (
                    _stream_with_hard_route_timeout(
                        client,
                        messages=messages,
                        tools=tools,
                        routing_context=context,
                        timeout_seconds=route_timeout_seconds,
                        keepalive_seconds=route_keepalive_seconds,
                    )
                    if hard_route_timeout and route_timeout_seconds is not None
                    else client.stream(messages=messages, tools=tools, routing_context=context)
                )
                for event in events:
                    if event.type != PROVIDER_KEEPALIVE_EVENT:
                        emitted_provider_content = True
                    yield _with_route_metadata(event, route_payload)
                return
            except Exception as exc:
                if emitted_provider_content or not _is_fallbackable_provider_error(exc):
                    raise
                failures.append(
                    {
                        "provider_route": route,
                        "error": exc.__class__.__name__,
                        "message": str(exc),
                    }
                )
                continue
        raise ProviderConfigurationError(f"All model routes failed for tier={tier} stage={stage}: {failures}")


def _stream_with_hard_route_timeout(
    client: LLMClient,
    *,
    messages: list[dict[str, str]],
    tools: list[dict[str, Any]],
    routing_context: dict[str, Any],
    timeout_seconds: float,
    keepalive_seconds: float | None = None,
) -> Iterable[LLMClientEvent]:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="strategy-route")
    done = object()
    queue: Queue[LLMClientEvent | Exception | object] = Queue()

    def collect_events() -> None:
        try:
            for event in client.stream(messages=messages, tools=tools, routing_context=routing_context):
                queue.put(event)
        except Exception as exc:
            queue.put(exc)
        finally:
            queue.put(done)

    future = executor.submit(collect_events)
    started_at = time.monotonic()
    heartbeat_seconds = keepalive_seconds or DEFAULT_ROUTE_KEEPALIVE_SECONDS
    try:
        while True:
            elapsed = time.monotonic() - started_at
            remaining = timeout_seconds - elapsed
            if remaining <= 0:
                future.cancel()
                raise ProviderTimeoutError(f"Provider route timed out after {timeout_seconds:.3f}s")
            try:
                item = queue.get(timeout=min(max(heartbeat_seconds, 0.001), remaining))
            except Empty:
                yield LLMClientEvent(
                    type=PROVIDER_KEEPALIVE_EVENT,
                    arguments={
                        "elapsed_ms": int((time.monotonic() - started_at) * 1000),
                        "timeout_seconds": timeout_seconds,
                    },
                    model=client.model,
                )
                continue
            if item is done:
                return
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _with_route_metadata(event: LLMClientEvent, route_payload: dict[str, Any]) -> LLMClientEvent:
    if event.type == "tool.call":
        return LLMClientEvent(
            type=event.type,
            text=event.text,
            tool_name=event.tool_name,
            arguments=event.arguments,
            model=event.model or route_payload["model"],
            input_tokens=event.input_tokens,
            output_tokens=event.output_tokens,
        )
    arguments = dict(event.arguments or {})
    arguments.setdefault("model_tier", route_payload["model_tier"])
    arguments.setdefault("model_stage", route_payload["model_stage"])
    arguments.setdefault("provider_route", route_payload["provider_route"])
    arguments.setdefault("fallback_used", route_payload["fallback_used"])
    arguments.setdefault("attempt_count", route_payload["attempt_count"])
    return LLMClientEvent(
        type=event.type,
        text=event.text,
        tool_name=event.tool_name,
        arguments=arguments or event.arguments,
        model=event.model or route_payload["model"],
        input_tokens=event.input_tokens,
        output_tokens=event.output_tokens,
    )


def _split_route(route: str) -> tuple[str, str]:
    provider, separator, model = route.partition("/")
    if not separator or not provider or not model:
        raise ModelRouteUnavailable(f"Invalid model route: {route}")
    return provider, model


def _is_fallbackable_provider_error(exc: Exception) -> bool:
    if isinstance(exc, (ProviderConfigurationError, ProviderTimeoutError, TimeoutError)):
        return True
    name = exc.__class__.__name__
    if name in {"RateLimitError", "APIConnectionError", "APITimeoutError", "APIStatusError", "InternalServerError"}:
        status_code = getattr(exc, "status_code", None)
        return status_code is None or int(status_code) in {408, 409, 429, 500, 502, 503, 504}
    if name == "APIError":
        status_code = getattr(exc, "status_code", None)
        if status_code is not None:
            return int(status_code) in {408, 409, 429, 500, 502, 503, 504}
        message = str(exc).lower()
        return any(token in message for token in ("quota", "rate limit", "rate-limit", "429", "temporarily unavailable"))
    status_code = getattr(exc, "status_code", None)
    return status_code in {408, 409, 429, 500, 502, 503, 504}


def _route_timeout_seconds() -> float | None:
    raw = os.getenv("STRATEGY_CODEBOT_LLM_ROUTE_TIMEOUT_SECONDS", str(DEFAULT_ROUTE_TIMEOUT_SECONDS)).strip()
    if not raw:
        return DEFAULT_ROUTE_TIMEOUT_SECONDS
    try:
        timeout = float(raw)
    except ValueError:
        return DEFAULT_ROUTE_TIMEOUT_SECONDS
    return timeout if timeout > 0 else DEFAULT_ROUTE_TIMEOUT_SECONDS


def _route_timeout_seconds_from_context(context: dict[str, Any]) -> float | None:
    raw = context.get("route_timeout_seconds")
    if raw is None:
        return None
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        return None
    return timeout if timeout > 0 else None


def _route_keepalive_seconds_from_context(context: dict[str, Any]) -> float | None:
    raw = context.get("route_keepalive_seconds")
    if raw is None:
        return None
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        return None
    return timeout if timeout > 0 else None


def _create_route_client(
    client_factory: ClientFactory,
    route: str,
    *,
    timeout_seconds: float | None,
) -> LLMClient:
    if timeout_seconds is None or not _client_factory_accepts_timeout_seconds(client_factory):
        return client_factory(route)
    return client_factory(route, timeout_seconds=timeout_seconds)


def _client_factory_accepts_timeout_seconds(client_factory: ClientFactory) -> bool:
    try:
        parameters = inspect.signature(client_factory).parameters
    except (TypeError, ValueError):
        return True
    timeout_parameter = parameters.get("timeout_seconds")
    return timeout_parameter is not None or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]
