import json
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Protocol

LLM_EVENT_MESSAGE_DELTA = "message.delta"
LLM_EVENT_SOURCES = "web.sources"
LLM_EVENT_TOOL_CALL = "tool.call"
LLM_EVENT_USAGE = "usage"
LLMClientEventType = Literal["message.delta", "tool.call", "usage", "web.sources"]


class ProviderConfigurationError(RuntimeError):
    pass


class ProviderTimeoutError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMClientEvent:
    type: LLMClientEventType | str
    text: str | None = None
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient(Protocol):
    model: str

    def ensure_configured(self) -> None: ...

    def stream(self, *, messages: list[dict[str, str]], tools: list[dict[str, Any]]) -> Iterable[LLMClientEvent]: ...


class ResponsesClient:
    def __init__(self, *, model: str = "gpt-5.5", api_key: str | None = None, timeout_seconds: float | None = None) -> None:
        self.model = model
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def ensure_configured(self) -> None:
        if not (self._api_key or os.getenv("OPENAI_API_KEY")):
            raise ProviderConfigurationError("LLM provider is not configured")

    def stream(self, *, messages: list[dict[str, str]], tools: list[dict[str, Any]]) -> Iterable[LLMClientEvent]:
        self.ensure_configured()
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderConfigurationError("OpenAI SDK is not installed") from exc

        timeout = self._timeout_seconds or _provider_timeout_seconds()
        client = OpenAI(api_key=self._api_key, timeout=timeout)
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "input": messages,
            "tools": tools,
            "stream": True,
        }
        if _is_web_search_only_request(tools):
            request_kwargs["tool_choice"] = "required"
            request_kwargs["include"] = ["web_search_call.action.sources"]
        try:
            stream = client.responses.create(**request_kwargs)
        except Exception as exc:
            if exc.__class__.__name__ in {"APITimeoutError", "Timeout", "ReadTimeout", "ConnectTimeout"}:
                raise ProviderTimeoutError("Provider request timed out") from exc
            raise
        try:
            yield from stream_response_events(stream, model=self.model)
        except Exception as exc:
            if exc.__class__.__name__ in {"APITimeoutError", "Timeout", "ReadTimeout", "ConnectTimeout"}:
                raise ProviderTimeoutError("Provider request timed out") from exc
            raise


class E2EFakeLLMClient:
    """Deterministic chat-model substitute for Docker E2E runs only."""

    model = "local/e2e-fake-chat-model"

    def ensure_configured(self) -> None:
        return None

    def stream(self, *, messages: list[dict[str, str]], tools: list[dict[str, Any]]) -> Iterable[LLMClientEvent]:
        prompt = _last_user_content(messages)
        if not tools:
            yield LLMClientEvent(
                type=LLM_EVENT_MESSAGE_DELTA,
                text='{"intent":"strategy_building","confidence":0.91}',
                model=self.model,
            )
            return
        yield LLMClientEvent(type=LLM_EVENT_USAGE, model=self.model, input_tokens=120, output_tokens=24)
        for tool_name, arguments in _e2e_tool_calls(prompt):
            yield LLMClientEvent(type=LLM_EVENT_TOOL_CALL, tool_name=tool_name, arguments=arguments, model=self.model)


class AgentsClient:
    def __init__(self, *, model: str = "gpt-5.5", api_key: str | None = None) -> None:
        self.model = model
        self._api_key = api_key

    def ensure_configured(self) -> None:
        if not (self._api_key or os.getenv("OPENAI_API_KEY")):
            raise ProviderConfigurationError("LLM provider is not configured")
        try:
            import agents  # noqa: F401
        except ImportError as exc:
            raise ProviderConfigurationError("OpenAI Agents SDK is not installed") from exc

    def stream(self, *, messages: list[dict[str, str]], tools: list[dict[str, Any]]) -> Iterable[LLMClientEvent]:
        self.ensure_configured()
        raise ProviderConfigurationError("Agents SDK adapter is not enabled for tool execution in Phase 5")


def _last_user_content(messages: list[dict[str, str]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _e2e_tool_calls(prompt: str) -> list[tuple[str, dict[str, Any]]]:
    lowered = prompt.lower()
    spec = _e2e_strategy_spec()
    config = _e2e_backtest_config()
    logic = _e2e_strategy_logic()
    if "variant" in lowered:
        return [
            (
                "run_backtest_variant_lab",
                {
                    "prompt": prompt,
                    "strategy_spec": spec,
                    "strategy_logic": logic,
                    "base_backtest_config": config,
                    "variants": [
                        {"name": "baseline"},
                        {"name": "tight-risk", "backtest_config": {"fee_bps": 8, "slippage_bps": 3}},
                    ],
                },
            )
        ]
    if "pinets" in lowered or "pine" in lowered:
        return [("create_pinets_preview_plan", {"prompt": prompt, "strategy_spec": spec, "backtest_config": config})]
    if "signals" in lowered or "market context" in lowered:
        return [("create_signals_market_context_plan", {"prompt": prompt, "symbol": "BTC/USDT", "backtest_config": config})]
    if "graph" in lowered or "multi-timeframe" in lowered:
        return [
            (
                "create_graph_pipeline_plan",
                {
                    "prompt": prompt,
                    "strategy_spec": spec,
                    "base_backtest_config": config,
                    "timeframes": ["4h", "1h"],
                    "variants": ["baseline", "tight-risk"],
                },
            )
        ]
    if "sidekick" in lowered or "export" in lowered:
        return [("create_sidekick_export_plan", {"prompt": prompt, "strategy_spec": spec, "project_name": "e2e-backtest-kit"})]
    if "run" in lowered or "queue" in lowered:
        return [("run_backtest_preview", {"prompt": prompt, "strategy_spec": spec, "strategy_logic": logic, "backtest_config": config})]
    return [("create_backtest_plan", {"prompt": prompt, "strategy_spec": spec, "strategy_logic": logic, "backtest_config": config})]


def _e2e_strategy_spec() -> dict[str, Any]:
    return {
        "target_platform": "pine_v6",
        "script_type": "strategy",
        "market": "crypto",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "entry_rules": ["Enter long when fast EMA crosses above slow EMA and bar is confirmed."],
        "exit_rules": ["Exit with strategy.exit using stop loss and take profit levels."],
        "risk_rules": ["Risk 1% account equity per trade and avoid live order placement."],
        "position_sizing": "1% account equity risk per trade",
        "stop_loss": "2% below average entry price",
        "take_profit": "4% above average entry price",
    }


def _e2e_backtest_config() -> dict[str, Any]:
    return {
        "engine": "backtest-kit",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "start": "2024-01-01",
        "end": "2024-01-03",
        "initial_capital": 10000,
        "fee_bps": 10,
        "slippage_bps": 5,
        "data_source": "public-readonly-cache",
    }


def _e2e_strategy_logic() -> dict[str, Any]:
    return {
        "logic_version": "backtest-strategy-logic.v1",
        "position": "long",
        "indicators": {
            "fast_ema": {"kind": "ema", "period": 3, "source": "close"},
            "slow_ema": {"kind": "ema", "period": 5, "source": "close"},
            "rsi": {"kind": "rsi", "period": 14, "source": "close"},
        },
        "entry": {
            "all": [
                {"type": "crossover", "left": "fast_ema", "right": "slow_ema"},
                {"type": "greater_than", "left": "rsi", "right": 45},
            ]
        },
        "exit": {"take_profit_pct": 4, "stop_loss_pct": 2, "max_holding_minutes": 1440},
        "risk": {"cost": 1000},
    }


def response_events(response: Any, *, model: str) -> Iterable[LLMClientEvent]:
    yield from _usage_events(response, model=model)

    for item in _iter_response_items(response):
        source_event = _source_event_from_response_item(item, model=model)
        if source_event is not None:
            yield source_event
        event = _event_from_response_item(item, model=model)
        if event is not None:
            yield event

    output_text = _value(response, "output_text")
    if output_text:
        yield LLMClientEvent(type=LLM_EVENT_MESSAGE_DELTA, text=str(output_text), model=model)


def stream_response_events(stream: Iterable[Any], *, model: str) -> Iterable[LLMClientEvent]:
    for event in stream:
        event_type = str(_value(event, "type") or "")
        if event_type == "response.output_text.delta":
            delta = _value(event, "delta")
            if delta:
                yield LLMClientEvent(type=LLM_EVENT_MESSAGE_DELTA, text=str(delta), model=model)
            continue
        if event_type == "response.output_text.annotation.added":
            source = _source_from_annotation(_value(event, "annotation"))
            if source is not None:
                yield LLMClientEvent(type=LLM_EVENT_SOURCES, arguments={"sources": [source]}, model=model)
            continue
        if event_type == "response.output_item.done":
            item = _value(event, "item")
            source_event = _source_event_from_response_item(item, model=model)
            if source_event is not None:
                yield source_event
            item_event = _event_from_response_item(item, model=model)
            if item_event is not None and item_event.type != LLM_EVENT_MESSAGE_DELTA:
                yield item_event
            continue
        if event_type == "response.completed":
            yield from _usage_events(_value(event, "response"), model=model)
            continue
        if event_type == "response.failed":
            error = _value(event, "error")
            message = _value(error, "message") or "Provider response stream failed"
            raise RuntimeError(str(message))


def _usage_events(response: Any, *, model: str) -> Iterable[LLMClientEvent]:
    usage = _value(response, "usage")
    input_tokens = _int_value(usage, "input_tokens")
    output_tokens = _int_value(usage, "output_tokens")
    if input_tokens or output_tokens:
        yield LLMClientEvent(type=LLM_EVENT_USAGE, model=model, input_tokens=input_tokens, output_tokens=output_tokens)


def _iter_response_items(response: Any) -> Iterable[Any]:
    output = _value(response, "output")
    if isinstance(output, list | tuple):
        yield from output


def _event_from_response_item(item: Any, *, model: str) -> LLMClientEvent | None:
    item_type = str(_value(item, "type") or "")
    if item_type in {"function_call", "tool_call"}:
        tool_name = _value(item, "name") or _value(item, "tool_name")
        if not tool_name:
            return None
        return LLMClientEvent(
            type=LLM_EVENT_TOOL_CALL,
            tool_name=str(tool_name),
            arguments=_json_arguments(_value(item, "arguments")),
            model=model,
        )
    if item_type in {"message", "output_text"}:
        text = _message_text(item)
        if text:
            return LLMClientEvent(type=LLM_EVENT_MESSAGE_DELTA, text=text, model=model)
    return None


def _message_text(item: Any) -> str | None:
    text = _value(item, "text")
    if text:
        return str(text)
    content = _value(item, "content")
    if isinstance(content, str):
        return content
    if isinstance(content, list | tuple):
        chunks: list[str] = []
        for part in content:
            value = _value(part, "text")
            if value:
                chunks.append(str(value))
        return "".join(chunks) or None
    return None


def _source_event_from_response_item(item: Any, *, model: str) -> LLMClientEvent | None:
    sources = _sources_from_response_item(item)
    if not sources:
        return None
    return LLMClientEvent(type=LLM_EVENT_SOURCES, arguments={"sources": sources}, model=model)


def _sources_from_response_item(item: Any) -> list[dict[str, str]]:
    item_type = str(_value(item, "type") or "")
    if item_type == "web_search_call":
        return _dedupe_sources(_sources_from_web_search_call(item))

    content = _value(item, "content")
    sources: list[dict[str, str]] = []
    if isinstance(content, list | tuple):
        for part in content:
            annotations = _value(part, "annotations")
            if isinstance(annotations, list | tuple):
                for annotation in annotations:
                    source = _source_from_annotation(annotation)
                    if source is not None:
                        sources.append(source)
    return _dedupe_sources(sources)


def _sources_from_web_search_call(item: Any) -> list[dict[str, str]]:
    action = _value(item, "action")
    source_items = _value(action, "sources")
    if not isinstance(source_items, list | tuple):
        source_items = _value(item, "sources")
    if not isinstance(source_items, list | tuple):
        return []
    sources: list[dict[str, str]] = []
    for source_item in source_items:
        source = _source_from_web_search_source(source_item)
        if source is not None:
            sources.append(source)
    return sources


def _source_from_web_search_source(source: Any) -> dict[str, str] | None:
    source_type = _string_value(source, "type")
    url = _string_value(source, "url")
    provider_name = _string_value(source, "name")
    title = _string_value(source, "title") or _string_value(source, "hostname") or provider_name or url
    if not title:
        return None
    if source_type == "api" and not url:
        return {
            "id": _source_id(provider_name or title),
            "title": _provider_source_title(provider_name or title),
            "type": "internal",
        }
    if not url:
        return None
    return {
        "id": _source_id(url),
        "title": title[:160],
        "type": "external",
        "url": url,
    }


def _source_from_annotation(annotation: Any) -> dict[str, str] | None:
    if annotation is None:
        return None
    annotation_type = str(_value(annotation, "type") or "")
    url = _string_value(annotation, "url")
    title = _string_value(annotation, "title") or url
    if annotation_type not in {"url_citation", "citation", "web_search_result"} or not url or not title:
        return None
    return {
        "id": _source_id(url),
        "title": title[:160],
        "type": "external",
        "url": url,
    }


def _dedupe_sources(sources: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for source in sources:
        key = source.get("url") or source.get("id")
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(source)
    return deduped[:5]


def _provider_source_title(value: str) -> str:
    normalized = value.replace("_", "-").strip().lower()
    if normalized == "oai-finance":
        return "OpenAI Finance"
    return value.replace("_", " ").replace("-", " ").title()[:160]


def _is_web_search_only_request(tools: list[dict[str, Any]]) -> bool:
    return len(tools) == 1 and tools[0].get("type") == "web_search"


def _json_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _int_value(value: Any, name: str) -> int:
    item = _value(value, name)
    return item if isinstance(item, int) else 0


def _string_value(value: Any, name: str) -> str | None:
    item = _value(value, name)
    if not isinstance(item, str):
        return None
    stripped = item.strip()
    return stripped or None


def _source_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return normalized[:96] or "source"


def _value(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _provider_timeout_seconds() -> float:
    value = os.getenv("STRATEGY_CODEBOT_LLM_REQUEST_TIMEOUT_SECONDS", "90")
    try:
        timeout = float(value)
    except ValueError:
        return 90.0
    return timeout if timeout > 0 else 90.0
