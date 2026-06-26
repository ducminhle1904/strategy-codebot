from __future__ import annotations

import os
import time
from datetime import UTC
from datetime import datetime
from typing import Any

from .helpers import auth
from .helpers import client
from .helpers import write_json
from .prod_live_smoke import assert_cache_reuse_manifest
from .prod_live_smoke import assert_auto_chain_source_events
from .prod_live_smoke import assert_auto_chain_summary_message
from .prod_live_smoke import assert_live_provider_env
from .prod_live_smoke import assert_report_metrics
from .prod_live_smoke import assert_required_artifacts
from .prod_live_smoke import assert_summary_completed_events
from .prod_live_smoke import assert_terminal_events_are_unique
from .prod_live_smoke import artifact_by_kind
from .prod_live_smoke import build_auto_chain_live_prompt
from .prod_live_smoke import build_live_prompt
from .prod_live_smoke import completed_tool_ids
from .prod_live_smoke import latest_completed_utc_window
from .prod_live_smoke import require_live_smoke_enabled
from .prod_live_smoke import tool_outputs
from .prod_live_smoke import wait_for_auto_chain_summary_message
from .prod_live_smoke import wait_for_run_events_live


def test_latest_completed_utc_window_is_aligned_to_previous_full_day() -> None:
    start, end = latest_completed_utc_window(365, now=datetime(2026, 6, 21, 15, 30, tzinfo=UTC))

    assert start == "2025-06-21"
    assert end == "2026-06-21"


def test_live_provider_guard_rejects_fake_mode() -> None:
    try:
        assert_live_provider_env({"STRATEGY_CODEBOT_LLM_MODE": "fake", "OPENAI_API_KEY": "sk-test"})
    except AssertionError as exc:
        assert "must not run" in str(exc)
    else:
        raise AssertionError("expected fake mode to be rejected")


def test_live_provider_guard_requires_selected_gateway_key() -> None:
    try:
        assert_live_provider_env({"STRATEGY_CODEBOT_LLM_PROVIDER": "openrouter", "VERCEL_AI_GATEWAY_API_KEY": "test"})
    except AssertionError as exc:
        assert "OPENROUTER_API_KEY" in str(exc)
    else:
        raise AssertionError("expected missing OpenRouter key to be rejected")


def test_live_report_metric_assertion_accepts_nullable_ratios() -> None:
    report = {
        "execution_semantics": "model_generated_pine_pineforge",
        "signal_timeframe": "1h",
        "candle_timeframe": "1m",
        "source_feed_checksum": "abc123",
        "market_data_source": {"exchange": "binance"},
        "applied_cost_model": {"commission_type": "percent", "commission_value": 0.1},
        "metrics": {
            "pnl": {"absolute": 10, "percentage": 0.1},
            "max_drawdown": 1.5,
            "trade_count": 0,
            "win_rate": None,
            "sharpe": None,
            "sortino": None,
        },
        "pineforge_runtime": {"input_tf": 1, "script_tf": 60, "bars_processed": 365 * 24 * 60},
    }

    assert_report_metrics(
        report,
        {"exchange": "binance", "timeframe": "1h", "candle_timeframe": "1m"},
        expected_days=365,
    )


def test_auto_chain_live_prompt_requests_generate_only_and_explicit_backtest() -> None:
    prompt = build_auto_chain_live_prompt(
        {
            "symbol": "BTC/USDT",
            "exchange": "binance",
            "start": "2025-01-01",
            "end": "2026-01-01",
            "timeframe": "1h",
            "candle_timeframe": "1m",
        }
    )

    assert "Generate PineScript v6 strategy source only" in prompt
    assert "Do not call create_backtest_plan or run_backtest_preview yourself" in prompt
    assert "server auto-chain should run the backtest" in prompt
    assert "Explicitly backtest BTC/USDT" in prompt


def test_auto_chain_summary_message_assertion_rejects_missing_metrics() -> None:
    try:
        assert_auto_chain_summary_message({"content": "Backtest completed for BTC/USDT"}, {"symbol": "BTC/USDT", "timeframe": "1h", "candle_timeframe": "1m"})
    except AssertionError:
        pass
    else:
        raise AssertionError("expected incomplete auto-chain summary to be rejected")


def test_prod_live_model_btc_1y_pineforge_backtest_smoke() -> None:
    require_live_smoke_enabled()
    assert_live_provider_env()

    days = int(os.getenv("STRATEGY_CODEBOT_PROD_LIVE_SMOKE_DAYS", "365"))
    symbol = os.getenv("STRATEGY_CODEBOT_PROD_LIVE_SMOKE_SYMBOL", "BTC/USDT")
    exchange = os.getenv("STRATEGY_CODEBOT_PROD_LIVE_SMOKE_EXCHANGE", "binance")
    timeframe = os.getenv("STRATEGY_CODEBOT_PROD_LIVE_SMOKE_TIMEFRAME", "1h")
    candle_timeframe = os.getenv("STRATEGY_CODEBOT_PROD_LIVE_SMOKE_CANDLE_TIMEFRAME", "1m")
    timeout_seconds = float(os.getenv("STRATEGY_CODEBOT_PROD_LIVE_SMOKE_TIMEOUT_SECONDS", "1200"))
    start, end = latest_completed_utc_window(days)
    config = {
        "engine": "pineforge",
        "exchange": exchange,
        "symbol": symbol,
        "timeframe": timeframe,
        "candle_timeframe": candle_timeframe,
        "start": start,
        "end": end,
        "initial_capital": 10000,
        "fee_bps": 10,
        "slippage_bps": 5,
        "data_source": "public-readonly-cache",
    }
    timings: dict[str, Any] = {"config": config}
    headers = auth(workspace="prod-live-btc-1y")

    with client(timeout=90.0) as api:
        readiness = api.get("/ready", headers=headers)
        readiness.raise_for_status()
        write_json("prod-live-ready-before.json", readiness.json())

        conversation = api.post(
            "/v1/conversations",
            headers=headers,
            json={"title": "Prod live BTC 1Y PineForge smoke"},
        )
        conversation.raise_for_status()
        conversation_id = conversation.json()["id"]
        timings["conversation_id"] = conversation_id

        model_started = time.monotonic()
        response = api.post(
            f"/v1/conversations/{conversation_id}/messages?stream=true&mode=agent",
            headers=headers,
            json={"content": build_live_prompt(config), "language": "en"},
        )
        timings["model_and_tool_seconds"] = time.monotonic() - model_started
        assert response.status_code == 200, response.text

        from .helpers import parse_sse

        frames = parse_sse(response.text)
        write_json("prod-live-chat-frames.json", frames)
        outputs = tool_outputs(frames)
        assert "create_backtest_plan" in outputs, outputs
        assert "run_backtest_preview" in outputs, outputs
        queued = outputs["run_backtest_preview"]
        assert queued["status"] == "queued"
        run_id = queued["run_id"]
        timings["run_id"] = run_id

        child_frames, worker_seconds = wait_for_run_events_live(api, headers, run_id, timeout_seconds=timeout_seconds)
        timings["worker_total_seconds"] = worker_seconds
        assert_terminal_events_are_unique(child_frames)

        state = api.get(f"/v1/conversations/{conversation_id}/state", headers=headers)
        state.raise_for_status()
        state_payload = state.json()
        artifacts = state_payload["latest_run_artifacts"]
        write_json("prod-live-artifacts.json", artifacts)
        assert_required_artifacts(artifacts)

        report_artifact = artifact_by_kind(artifacts, "backtest_report")
        cache_artifact = artifact_by_kind(artifacts, "market_data_cache_manifest")
        metadata_artifact = artifact_by_kind(artifacts, "backtest_run_metadata")
        report = api.get(f"/v1/artifacts/{report_artifact['id']}", headers=headers).json()["content"]
        cache_manifest = api.get(f"/v1/artifacts/{cache_artifact['id']}", headers=headers).json()["content"]
        run_metadata = api.get(f"/v1/artifacts/{metadata_artifact['id']}", headers=headers).json()["content"]
        write_json("prod-live-report-summary.json", report)
        write_json("prod-live-cache-manifest.json", cache_manifest)
        write_json("prod-live-run-metadata.json", run_metadata)

    assert_report_metrics(report, config, expected_days=days)
    assert cache_manifest["cache_version"] == "range-v2"
    if os.getenv("STRATEGY_CODEBOT_PROD_LIVE_SMOKE_EXPECT_WARM_CACHE") == "1":
        assert worker_seconds < 60, timings
        assert (run_metadata.get("runner_stats") or {}).get("run_ms", 0) < 30_000, run_metadata
        assert_cache_reuse_manifest(cache_manifest)

    write_json("prod-live-timings.json", timings)


def test_prod_live_model_auto_chain_btc_1y_pineforge_backtest_smoke() -> None:
    require_live_smoke_enabled()
    if os.getenv("STRATEGY_CODEBOT_PROD_LIVE_SMOKE_AUTO_CHAIN") != "1":
        import pytest

        pytest.skip("Set STRATEGY_CODEBOT_PROD_LIVE_SMOKE_AUTO_CHAIN=1 or pass --auto-chain to run auto-chain live smoke.")
    assert_live_provider_env()

    days = int(os.getenv("STRATEGY_CODEBOT_PROD_LIVE_SMOKE_DAYS", "365"))
    symbol = os.getenv("STRATEGY_CODEBOT_PROD_LIVE_SMOKE_SYMBOL", "BTC/USDT")
    exchange = os.getenv("STRATEGY_CODEBOT_PROD_LIVE_SMOKE_EXCHANGE", "binance")
    timeframe = os.getenv("STRATEGY_CODEBOT_PROD_LIVE_SMOKE_TIMEFRAME", "1h")
    candle_timeframe = os.getenv("STRATEGY_CODEBOT_PROD_LIVE_SMOKE_CANDLE_TIMEFRAME", "1m")
    timeout_seconds = float(os.getenv("STRATEGY_CODEBOT_PROD_LIVE_SMOKE_TIMEOUT_SECONDS", "1200"))
    start, end = latest_completed_utc_window(days)
    config = {
        "engine": "pineforge",
        "exchange": exchange,
        "symbol": symbol,
        "timeframe": timeframe,
        "candle_timeframe": candle_timeframe,
        "start": start,
        "end": end,
        "initial_capital": 10000,
        "fee_bps": 10,
        "slippage_bps": 5,
        "data_source": "public-readonly-cache",
    }
    timings: dict[str, Any] = {"config": config, "auto_chain": True}
    headers = auth(workspace="prod-live-auto-chain-btc-1y")

    with client(timeout=90.0) as api:
        readiness = api.get("/ready", headers=headers)
        readiness.raise_for_status()
        write_json("prod-live-auto-chain-ready-before.json", readiness.json())

        conversation = api.post(
            "/v1/conversations",
            headers=headers,
            json={"title": "Prod live auto-chain BTC 1Y PineForge smoke"},
        )
        conversation.raise_for_status()
        conversation_id = conversation.json()["id"]
        timings["conversation_id"] = conversation_id

        model_started = time.monotonic()
        response = api.post(
            f"/v1/conversations/{conversation_id}/messages?stream=true&mode=agent",
            headers=headers,
            json={"content": build_auto_chain_live_prompt(config), "language": "en"},
        )
        timings["model_and_tool_seconds"] = time.monotonic() - model_started
        assert response.status_code == 200, response.text

        from .helpers import parse_sse

        frames = parse_sse(response.text)
        write_json("prod-live-auto-chain-chat-frames.json", frames)
        assert_auto_chain_source_events(frames)
        tool_ids = completed_tool_ids(frames)
        assert tool_ids[:3] == ["generate_pine", "create_backtest_plan", "run_backtest_preview"], tool_ids
        outputs = tool_outputs(frames)
        write_json("tool_outputs.json", outputs)
        queued = outputs["run_backtest_preview"]
        assert queued["status"] == "queued"
        run_id = queued["run_id"]
        timings["run_id"] = run_id

        source_run_id = next(frame["data"]["run_id"] for frame in frames if frame.get("data", {}).get("run_id"))
        source_events_before = api.get(f"/v1/runs/{source_run_id}/events", headers=headers)
        source_events_before.raise_for_status()
        write_json("source_run_events_before_summary.json", parse_sse(source_events_before.text))

        child_frames, worker_seconds = wait_for_run_events_live(api, headers, run_id, timeout_seconds=timeout_seconds)
        timings["worker_total_seconds"] = worker_seconds
        assert_terminal_events_are_unique(child_frames)

        summary_message = wait_for_auto_chain_summary_message(
            api,
            headers,
            conversation_id,
            run_id,
            timeout_seconds=120,
        )
        assert_auto_chain_summary_message(summary_message, config)
        readiness_after = api.get("/ready", headers=headers)
        readiness_after.raise_for_status()
        write_json("prod-live-auto-chain-ready-after-summary.json", readiness_after.json())

        source_events_after = api.get(f"/v1/runs/{source_run_id}/events", headers=headers)
        source_events_after.raise_for_status()
        source_frames_after = parse_sse(source_events_after.text)
        write_json("source_run_events.json", source_frames_after)
        child_events_after = api.get(f"/v1/runs/{run_id}/events", headers=headers)
        child_events_after.raise_for_status()
        child_frames_after = parse_sse(child_events_after.text)
        write_json("child_run_events.json", child_frames_after)
        assert_summary_completed_events(source_frames_after, child_frames_after)

        state = api.get(f"/v1/conversations/{conversation_id}/state", headers=headers)
        state.raise_for_status()
        state_payload = state.json()
        artifacts = state_payload["latest_run_artifacts"]
        write_json("prod-live-auto-chain-artifacts.json", artifacts)
        assert_required_artifacts(artifacts)

        report_artifact = artifact_by_kind(artifacts, "backtest_report")
        cache_artifact = artifact_by_kind(artifacts, "market_data_cache_manifest")
        metadata_artifact = artifact_by_kind(artifacts, "backtest_run_metadata")
        report = api.get(f"/v1/artifacts/{report_artifact['id']}", headers=headers).json()["content"]
        cache_manifest = api.get(f"/v1/artifacts/{cache_artifact['id']}", headers=headers).json()["content"]
        run_metadata = api.get(f"/v1/artifacts/{metadata_artifact['id']}", headers=headers).json()["content"]
        write_json("report_summary.json", report)
        write_json("cache_manifest.json", cache_manifest)
        write_json("prod-live-auto-chain-run-metadata.json", run_metadata)

    assert_report_metrics(report, config, expected_days=days)
    assert cache_manifest["cache_version"] == "range-v2"
    if os.getenv("STRATEGY_CODEBOT_PROD_LIVE_SMOKE_EXPECT_WARM_CACHE") == "1":
        assert worker_seconds < 60, timings
        assert (run_metadata.get("runner_stats") or {}).get("run_ms", 0) < 30_000, run_metadata
        assert_cache_reuse_manifest(cache_manifest)

    write_json("timings.json", timings)
