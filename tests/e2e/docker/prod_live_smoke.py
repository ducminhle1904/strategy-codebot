from __future__ import annotations

import os
import time
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import Any

import httpx
import pytest

from .helpers import parse_sse
from .helpers import write_json


PROVIDER_ENV_KEYS = (
    "OPENAI_API_KEY",
    "LITELLM_PROXY_API_KEY",
    "OPENROUTER_API_KEY",
    "VERCEL_AI_GATEWAY_API_KEY",
    "GROQ_API_KEY",
    "TOGETHER_API_KEY",
    "FIREWORKS_API_KEY",
    "DEEPINFRA_API_KEY",
)
PROVIDER_REQUIRED_ENV = {
    "openrouter": "OPENROUTER_API_KEY",
    "vercel-ai-gateway": "VERCEL_AI_GATEWAY_API_KEY",
    "vercel_ai_gateway": "VERCEL_AI_GATEWAY_API_KEY",
}


def latest_completed_utc_window(days: int, now: datetime | None = None) -> tuple[str, str]:
    if days <= 0:
        raise ValueError("days must be positive")
    current = now.astimezone(UTC) if now is not None else datetime.now(UTC)
    end = datetime(current.year, current.month, current.day, tzinfo=UTC)
    start = end - timedelta(days=days)
    return start.date().isoformat(), end.date().isoformat()


def require_live_smoke_enabled() -> None:
    if os.getenv("STRATEGY_CODEBOT_RUN_PROD_LIVE_SMOKE") != "1":
        pytest.skip("Set STRATEGY_CODEBOT_RUN_PROD_LIVE_SMOKE=1 to run the production live model backtest smoke.")


def assert_live_provider_env(env: dict[str, str | None] = os.environ) -> None:
    if (env.get("STRATEGY_CODEBOT_LLM_MODE") or "").strip().lower() == "fake":
        raise AssertionError("Production live smoke must not run with STRATEGY_CODEBOT_LLM_MODE=fake")
    provider = (env.get("STRATEGY_CODEBOT_LLM_PROVIDER") or "").strip().lower()
    required = PROVIDER_REQUIRED_ENV.get(provider)
    if required is not None and not (env.get(required) or "").strip():
        raise AssertionError(f"Production live smoke with provider {provider} requires {required}")
    if required is None and not any((env.get(key) or "").strip() for key in PROVIDER_ENV_KEYS):
        raise AssertionError(f"Production live smoke requires at least one provider key: {', '.join(PROVIDER_ENV_KEYS)}")


def build_live_prompt(config: dict[str, Any]) -> str:
    return (
        "Generate PineScript v6 strategy source directly, then call create_backtest_plan and run_backtest_preview. "
        "Use a conservative EMA/RSI long-only strategy with strategy.entry and strategy.exit. "
        f"Backtest {config['symbol']} on {config['exchange']} from {config['start']} to {config['end']}. "
        f"Use signal timeframe {config['timeframe']} and candle_timeframe {config['candle_timeframe']} for 1m execution candles. "
        "Use PineForge local preview evidence only; do not claim TradingView validation, broker proof, live proof, or profitability."
    )


def build_auto_chain_live_prompt(config: dict[str, Any]) -> str:
    return (
        "Generate PineScript v6 strategy source only for a conservative EMA/RSI long-only strategy. "
        "Do not call create_backtest_plan or run_backtest_preview yourself; the server auto-chain should run the backtest after Pine is generated. "
        f"Explicitly backtest {config['symbol']} on {config['exchange']} from {config['start']} to {config['end']} "
        f"with signal timeframe {config['timeframe']} and candle_timeframe {config['candle_timeframe']} for 1m execution candles. "
        "The Pine must use //@version=6, strategy(...), strategy.entry, and strategy.exit. "
        "Use PineForge local preview evidence only; do not claim TradingView validation, broker proof, live proof, or profitability."
    )


def tool_outputs(frames: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    outputs: dict[str, dict[str, Any]] = {}
    for frame in frames:
        if frame.get("event") != "tool.completed":
            continue
        payload = frame.get("data", {}).get("payload", {})
        tool_id = payload.get("tool_id")
        output = payload.get("output")
        if isinstance(tool_id, str) and isinstance(output, dict):
            outputs[tool_id] = output
    return outputs


def completed_tool_ids(frames: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for frame in frames:
        if frame.get("event") != "tool.completed":
            continue
        tool_id = frame.get("data", {}).get("payload", {}).get("tool_id")
        if isinstance(tool_id, str):
            ids.append(tool_id)
    return ids


def assert_auto_chain_source_events(frames: list[dict[str, Any]]) -> None:
    event_types = [frame["event"] for frame in frames]
    assert "chat.auto_chain.started" in event_types, event_types
    assert "chat.auto_chain.step.completed" in event_types, event_types
    assert "chat.auto_chain.waiting_for_backtest" in event_types, event_types


def wait_for_run_events_live(
    api: httpx.Client,
    headers: dict[str, str],
    run_id: str,
    *,
    timeout_seconds: float,
) -> tuple[list[dict[str, Any]], float]:
    started = time.monotonic()
    deadline = started + timeout_seconds
    frames: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        response = api.get(f"/v1/runs/{run_id}/events", headers=headers)
        response.raise_for_status()
        frames = parse_sse(response.text)
        event_types = [frame["event"] for frame in frames]
        if {"run.completed", "run.failed", "run.cancelled"} & set(event_types):
            write_json(f"prod-live-run-{run_id}-events.json", frames)
            return frames, time.monotonic() - started
        time.sleep(2)
    write_json(f"prod-live-run-{run_id}-events-timeout.json", frames)
    raise AssertionError(f"run {run_id} did not reach terminal status within {timeout_seconds}s")


def wait_for_auto_chain_summary_message(
    api: httpx.Client,
    headers: dict[str, str],
    conversation_id: str,
    backtest_run_id: str,
    *,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_messages: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        response = api.get(f"/v1/conversations/{conversation_id}/messages", headers=headers)
        response.raise_for_status()
        last_messages = response.json()["items"]
        matches = [
            message
            for message in last_messages
            if message.get("role") == "assistant"
            and backtest_run_id in str(message.get("content") or "")
            and "Backtest completed for" in str(message.get("content") or "")
        ]
        if len(matches) == 1:
            write_json("conversation_messages_after_summary.json", last_messages)
            return matches[0]
        if len(matches) > 1:
            write_json("conversation_messages_duplicate_summary.json", last_messages)
            raise AssertionError({"duplicate_auto_chain_summaries": len(matches), "backtest_run_id": backtest_run_id})
        time.sleep(2)
    write_json("conversation_messages_summary_timeout.json", last_messages)
    raise AssertionError(f"auto-chain summary message for {backtest_run_id} was not appended within {timeout_seconds}s")


def assert_auto_chain_summary_message(message: dict[str, Any], config: dict[str, Any]) -> None:
    content = str(message.get("content") or "")
    assert "Backtest completed for" in content
    assert config["symbol"] in content
    assert "PnL" in content
    assert "max drawdown" in content
    assert "trades" in content
    assert "win rate" in content
    assert config["timeframe"] in content
    assert config["candle_timeframe"] in content
    assert "not TradingView official validation" in content


def assert_summary_completed_events(source_frames: list[dict[str, Any]], child_frames: list[dict[str, Any]]) -> None:
    assert "chat.auto_chain.summary.completed" in [frame["event"] for frame in source_frames]
    assert "chat.auto_chain.summary.completed" in [frame["event"] for frame in child_frames]


def assert_terminal_events_are_unique(frames: list[dict[str, Any]]) -> None:
    terminal = [frame["event"] for frame in frames if frame["event"] in {"run.completed", "run.failed", "run.cancelled"}]
    assert terminal == ["run.completed"], terminal


def artifact_by_kind(artifacts: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    matches = [artifact for artifact in artifacts if artifact.get("kind") == kind]
    assert len(matches) == 1, {"kind": kind, "matches": matches}
    return matches[0]


def assert_required_artifacts(artifacts: list[dict[str, Any]]) -> None:
    kinds = {artifact["kind"] for artifact in artifacts}
    required = {
        "pine_strategy_source",
        "backtest_report",
        "backtest_trades",
        "backtest_equity_curve",
        "market_data_cache_manifest",
        "market_data_ohlcv_metadata",
        "backtest_run_metadata",
    }
    assert required <= kinds, {"missing": sorted(required - kinds), "present": sorted(kinds)}


def assert_report_metrics(report: dict[str, Any], config: dict[str, Any], *, expected_days: int) -> None:
    assert report["execution_semantics"] == "model_generated_pine_pineforge"
    assert report["signal_timeframe"] == config["timeframe"]
    assert report["candle_timeframe"] == config["candle_timeframe"]
    assert report["source_feed_checksum"]
    assert report["market_data_source"]["exchange"] == config["exchange"]
    assert report.get("applied_cost_model") is not None

    metrics = report["metrics"]
    for key in ("pnl", "max_drawdown", "trade_count", "win_rate", "sharpe", "sortino"):
        assert key in metrics, {"missing_metric": key, "metrics": metrics}
    assert "absolute" in metrics["pnl"]
    assert "percentage" in metrics["pnl"]
    assert isinstance(metrics["trade_count"], int)

    runtime = report.get("pineforge_runtime") or {}
    assert str(runtime.get("input_tf")) == "1", runtime
    assert str(runtime.get("script_tf")) == "60", runtime
    bars = int(runtime.get("bars_processed") or report.get("summary", {}).get("bars_processed") or 0)
    expected = expected_days * 24 * 60
    assert expected * 0.95 <= bars <= expected * 1.01, {"bars_processed": bars, "expected": expected}


def assert_cache_reuse_manifest(manifest: dict[str, Any]) -> None:
    assert manifest["cache_version"] == "range-v2"
    assert manifest["segments_reused"] > 0
    assert manifest["source_feed_checksum"]
