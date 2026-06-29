from __future__ import annotations

from collections.abc import Callable
from datetime import UTC
from datetime import datetime
from datetime import timedelta
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import psycopg
import pytest

from strategy_codebot.nautilus_streams import bar_message
from strategy_codebot.nautilus_streams import decode_stream_fields
from strategy_codebot.nautilus_streams import encode_stream_fields
from strategy_codebot.nautilus_streams import market_data_stream_key


API_BASE_URL = os.getenv("STRATEGY_CODEBOT_E2E_API_BASE_URL")
WEB_BASE_URL = os.getenv("STRATEGY_CODEBOT_E2E_WEB_BASE_URL")
POSTGRES_URL = os.getenv("STRATEGY_CODEBOT_E2E_POSTGRES_URL")
REDIS_URL = os.getenv("STRATEGY_CODEBOT_E2E_REDIS_URL")
REPORT_DIR = Path(os.getenv("STRATEGY_CODEBOT_E2E_REPORT_DIR", "reports/e2e/manual"))


def require_docker_e2e() -> None:
    if not API_BASE_URL:
        pytest.skip("Set STRATEGY_CODEBOT_E2E_API_BASE_URL or run scripts/e2e-docker.sh.")


def client(timeout: float = 30.0) -> httpx.Client:
    require_docker_e2e()
    return httpx.Client(base_url=API_BASE_URL, timeout=timeout)


def auth(workspace: str | None = None, user: str | None = None) -> dict[str, str]:
    suffix = uuid4().hex[:10]
    return {
        "X-User-Id": user or f"e2e-user-{suffix}",
        "X-Workspace-Id": workspace or f"e2e-workspace-{suffix}",
    }


def local_paper_nautilus_strategy_spec(*, venue: str = "BINANCE", symbol: str = "BTCUSDT") -> dict[str, Any]:
    return {
        "target_platform": "nautilus_py",
        "script_type": "strategy",
        "market": "crypto",
        "venue": venue,
        "symbol": symbol,
        "timeframe": "1m",
        "entry_rules": ["Enter long when the 2-period SMA crosses above the 3-period SMA after bar close"],
        "exit_rules": ["Exit when the 2-period SMA crosses below the 3-period SMA after bar close"],
        "risk_rules": ["Use fixed local paper size only and do not place live orders"],
        "position_sizing": {"type": "fixed", "value": 1},
        "constraints": ["No live trading automation"],
    }


def local_paper_runtime_payload(
    *,
    broker_connection_id: str,
    account_id: str,
    risk_policy_id: str,
    strategy_id: str,
    source: str,
    venue: str = "BINANCE",
    symbol: str = "BTCUSDT",
    mode: str = "paper",
    manifest_refs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = {
        "source": source,
        "strategy_spec": local_paper_nautilus_strategy_spec(venue=venue, symbol=symbol),
        "supported_execution_mode": "paper",
        "paper_runtime": {"warmup_min_bars": 3},
        "risk_policy": {"id": risk_policy_id, "live_enabled": False},
    }
    if manifest_refs:
        manifest.update(manifest_refs)
    return {
        "broker_connection_id": broker_connection_id,
        "account_id": account_id,
        "mode": mode,
        "risk_policy_id": risk_policy_id,
        "strategy_id": strategy_id,
        "manifest": manifest,
        "data_subscriptions": [
            {
                "venue": venue,
                "symbol": symbol,
                "timeframe": "1m",
                "data_type": "bar",
            }
        ],
    }


def publish_local_paper_cross_fixture(redis: Any, *, event_prefix: str) -> tuple[str, str]:
    stream = market_data_stream_key(venue="BINANCE", symbol="BTCUSDT", data_type="bar", timeframe="1m")
    closes = [100, 99, 98, 101, 105, 106, 104, 103, 102, 107, 110]
    last_stream_id = "0-0"
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for index, close in enumerate(closes, start=1):
        message = bar_message(
            event_id=f"{event_prefix}-{index}",
            venue="BINANCE",
            symbol="BTCUSDT",
            timeframe="1m",
            sequence=index,
            open=float(close - 1),
            high=float(close + 1),
            low=float(close - 2),
            close=float(close),
            volume=10.0,
            closed=True,
            ts_exchange=(start + timedelta(minutes=index)).isoformat(),
        )
        last_stream_id = redis.xadd(stream, encode_stream_fields(message), maxlen=100_000, approximate=True)
    return stream, last_stream_id


def wait_for_exchange_collector_bars(
    redis: Any,
    *,
    venue: str = "BINANCE",
    symbol: str = "BTCUSDT",
    timeframe: str = "1m",
    min_count: int = 3,
    timeout_seconds: float = 120.0,
) -> tuple[str, str, list[dict[str, Any]]]:
    stream = market_data_stream_key(venue=venue, symbol=symbol, data_type="bar", timeframe=timeframe)
    deadline = time.time() + timeout_seconds
    collector_bars: list[dict[str, Any]] = []
    while time.time() < deadline:
        collector_bars = []
        for stream_id, fields in redis.xrange(stream, min="-", max="+"):
            payload = decode_stream_fields(fields)
            if (
                payload.get("source") == "exchange_collector"
                and str(payload.get("exchange") or "").lower() == venue.lower()
                and payload.get("adapter") == "ccxt_ohlcv_rest"
                and str(payload.get("venue") or "").upper() == venue.upper()
                and str(payload.get("symbol") or "").upper() == symbol.upper()
                and str(payload.get("timeframe") or "") == timeframe
                and bool(payload.get("closed"))
            ):
                collector_bars.append({"stream_id": stream_id, **payload})
        if len(collector_bars) >= min_count:
            write_json(
                "paper-bot-native-exchange-bars.json",
                {"stream": stream, "bar_count": len(collector_bars), "bars": collector_bars[-min_count:]},
            )
            return stream, str(collector_bars[-1]["stream_id"]), collector_bars
        time.sleep(1)
    write_json(
        "paper-bot-native-exchange-bars-timeout.json",
        {"stream": stream, "bar_count": len(collector_bars), "bars": collector_bars[-10:]},
    )
    raise AssertionError(f"exchange collector did not publish {min_count} closed bars to {stream}")


def valid_spec() -> dict[str, Any]:
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


def pine_code() -> str:
    return """//@version=6
strategy("E2E EMA RSI", overlay=true, initial_capital=10000)
fast = ta.ema(close, 12)
slow = ta.ema(close, 26)
rsi = ta.rsi(close, 14)
if ta.crossover(fast, slow) and rsi < 70
    strategy.entry("Long", strategy.long)
strategy.exit("Long exit", "Long", stop=close * 0.98, limit=close * 1.04)
"""


def backtest_config(**overrides: Any) -> dict[str, Any]:
    config = {
        "engine": "pineforge",
        "exchange": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "candle_timeframe": "1m",
        "start": "2024-01-01",
        "end": "2024-01-03",
        "initial_capital": 10000,
        "fee_bps": 10,
        "slippage_bps": 5,
        "data_source": "public-readonly-cache",
    }
    config.update(overrides)
    return config


def parse_sse(body: str) -> list[dict[str, Any]]:
    frames = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        parsed: dict[str, Any] = {}
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("id: "):
                parsed["id"] = line.removeprefix("id: ")
            elif line.startswith("event: "):
                parsed["event"] = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data_lines.append(line.removeprefix("data: "))
        parsed["data"] = json.loads("\n".join(data_lines))
        frames.append(parsed)
    return frames


def wait_for_run_events(
    api: httpx.Client,
    headers: dict[str, str],
    run_id: str,
    *,
    timeout_seconds: float = 180.0,
) -> list[dict[str, Any]]:
    deadline = time.time() + timeout_seconds
    frames: list[dict[str, Any]] = []
    while time.time() < deadline:
        response = api.get(f"/v1/runs/{run_id}/events", headers=headers)
        response.raise_for_status()
        frames = parse_sse(response.text)
        event_types = [frame["event"] for frame in frames]
        if "run.completed" in event_types or "run.failed" in event_types or "run.cancelled" in event_types:
            write_json(f"run-{run_id}-events.json", frames)
            return frames
        time.sleep(1)
    write_json(f"run-{run_id}-events-timeout.json", frames)
    raise AssertionError(f"run {run_id} did not reach terminal status")


def wait_for_runtime(
    api: httpx.Client,
    headers: dict[str, str],
    runtime_id: str,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    timeout_seconds: float = 90.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    payload: dict[str, Any] = {}
    while time.time() < deadline:
        response = api.get(f"/v1/nautilus/runtimes/{runtime_id}", headers=headers)
        response.raise_for_status()
        payload = response.json()
        if predicate(payload):
            write_json(f"nautilus-runtime-{runtime_id}.json", payload)
            return payload
        time.sleep(1)
    write_json(f"nautilus-runtime-{runtime_id}-timeout.json", payload)
    raise AssertionError(f"runtime {runtime_id} did not satisfy predicate")


def wait_for_runtime_events(
    api: httpx.Client,
    headers: dict[str, str],
    runtime_id: str,
    expected_types: set[str],
    *,
    timeout_seconds: float = 90.0,
    after_sequence: int | None = None,
) -> list[dict[str, Any]]:
    deadline = time.time() + timeout_seconds
    events: list[dict[str, Any]] = []
    query = f"?after_sequence={after_sequence}" if after_sequence is not None else ""
    while time.time() < deadline:
        response = api.get(f"/v1/nautilus/runtimes/{runtime_id}/events{query}", headers=headers)
        response.raise_for_status()
        events = response.json()
        event_types = {event["type"] for event in events}
        if expected_types <= event_types:
            write_json(f"nautilus-runtime-{runtime_id}-events.json", events)
            return events
        time.sleep(1)
    write_json(f"nautilus-runtime-{runtime_id}-events-timeout.json", events)
    raise AssertionError(f"runtime {runtime_id} did not emit events {sorted(expected_types)}")


def db_rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if not POSTGRES_URL:
        pytest.skip("Set STRATEGY_CODEBOT_E2E_POSTGRES_URL for DB assertions.")
    with psycopg.connect(POSTGRES_URL, row_factory=psycopg.rows.dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return list(cursor.fetchall())


def redis_client():
    if not REDIS_URL:
        pytest.skip("Set STRATEGY_CODEBOT_E2E_REDIS_URL for Redis assertions.")
    from redis import Redis

    return Redis.from_url(REDIS_URL, decode_responses=True)


def write_json(name: str, payload: Any) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / name).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(percentile_value) - 1]
