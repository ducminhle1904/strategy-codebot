from __future__ import annotations

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


API_BASE_URL = os.getenv("STRATEGY_CODEBOT_E2E_API_BASE_URL")
WEB_BASE_URL = os.getenv("STRATEGY_CODEBOT_E2E_WEB_BASE_URL")
POSTGRES_URL = os.getenv("STRATEGY_CODEBOT_E2E_POSTGRES_URL")
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


def db_rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if not POSTGRES_URL:
        pytest.skip("Set STRATEGY_CODEBOT_E2E_POSTGRES_URL for DB assertions.")
    with psycopg.connect(POSTGRES_URL, row_factory=psycopg.rows.dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return list(cursor.fetchall())


def write_json(name: str, payload: Any) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / name).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(percentile_value) - 1]
