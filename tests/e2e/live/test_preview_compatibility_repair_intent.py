from __future__ import annotations

import json
import os
from hashlib import sha256
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.chat_worker import run_chat_worker
from strategy_codebot.server.database import create_sqlite_repository
from strategy_codebot.server.models import RunJob
from strategy_codebot.server.run_modes import PREVIEW_COMPATIBILITY_REPAIR_JOB_TYPE
from strategy_codebot.server.run_modes import RUN_MODE_BACKTEST_PREVIEW


UNSUPPORTED_FIXTURE_PINE = """//@version=6
strategy("HTF RSI pullback", overlay=true)
htfClose = request.security(syminfo.tickerid, "D", close)
rsi = ta.rsi(close, 14)
longCondition = close > htfClose and rsi < 45
if longCondition
    strategy.entry("Long", strategy.long)
strategy.exit("Risk", "Long", stop=close * 0.98, limit=close * 1.04)
"""

FORBIDDEN_PUBLIC_TERMS = ("pineforge", "runner", "engine", "compile", "transpile")


def test_live_model_repairs_preview_compatibility_intent() -> None:
    _require_live_repair_intent_smoke()
    report_dir = _report_dir()
    auth = AuthContext("live-repair-user", "live-repair-workspace")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth, title="Live repair intent smoke")
    source_run = repository.create_run(auth, conversation.id, status="running", mode="agent")
    failed_run = repository.create_run(auth, conversation.id, status="failed", mode=RUN_MODE_BACKTEST_PREVIEW)
    assert source_run is not None
    assert failed_run is not None

    payload = {
        "failed_backtest_run_id": failed_run.id,
        "failed_job_id": "job_live_repair_fixture",
        "source_run_id": source_run.id,
        "conversation_id": conversation.id,
        "preview_error_code": "preview_compatibility_limit",
        "strategy_spec": _strategy_spec(),
        "pine_code": UNSUPPORTED_FIXTURE_PINE,
        "backtest_config": _backtest_config(),
        "auto_chain": {"summary_on_complete": False, "source_run_id": source_run.id, "conversation_id": conversation.id},
        "compatibility_repair": {"attempt": 1, "max_attempts": 2},
        "internal_diagnostics": {
            "raw_runtime_message": "request.security is not supported by the local preview compatibility surface",
            "compile_stage": "transpile",
        },
    }
    repair_job = repository.create_run_job(auth, failed_run.id, job_type=PREVIEW_COMPATIBILITY_REPAIR_JOB_TYPE, payload_json=payload)
    assert repair_job is not None

    processed = run_chat_worker(repository, worker_id="live-repair-intent-smoke", once=True)
    assert processed == 1

    runs = repository.list_runs(auth, conversation.id)
    assert runs is not None
    repaired_runs = [run for run in runs if run.retry_of_run_id == failed_run.id]
    source_events = repository.list_run_events(auth, source_run.id) or []
    failed_events = repository.list_run_events(auth, failed_run.id) or []
    evidence: dict[str, Any] = {
        "provider_env": _provider_summary(),
        "conversation_id": conversation.id,
        "source_run_id": source_run.id,
        "failed_run_id": failed_run.id,
        "repair_jobs": _job_summaries(repository, PREVIEW_COMPATIBILITY_REPAIR_JOB_TYPE),
        "source_events": [_event_summary(event) for event in source_events],
        "failed_events": [_event_summary(event) for event in failed_events],
    }

    try:
        assert len(repaired_runs) == 1
        repaired_run = repaired_runs[0]
        repaired_payload = _preview_job_payload(repository, repaired_run.id)
        repaired_pine = repaired_payload["pine_code"]
        evidence.update(
            {
                "repaired_run_id": repaired_run.id,
                "repaired_job_payload": {
                    "auto_chain": repaired_payload.get("auto_chain"),
                    "compatibility_repair": repaired_payload.get("compatibility_repair"),
                    "backtest_config": repaired_payload.get("backtest_config"),
                },
                "repaired_pine_hash": sha256(repaired_pine.encode("utf-8")).hexdigest(),
                "repaired_pine_excerpt": repaired_pine[:1200],
            }
        )

        assert repaired_pine.lstrip().startswith("//@version=6")
        assert "strategy(" in repaired_pine
        assert "indicator(" not in repaired_pine
        assert "strategy.entry" in repaired_pine
        assert "strategy.exit" in repaired_pine or "strategy.close" in repaired_pine
        assert "request.security" not in repaired_pine
        assert repaired_payload["compatibility_repair"]["attempt"] == 1
        assert repaired_payload["compatibility_repair"]["max_attempts"] == 2
        assert repaired_payload["auto_chain"]["summary_on_complete"] is False
        assert repaired_payload["backtest_config"]["timeframe"] == "1h"

        visible_text = " ".join(str(event.payload) for event in source_events + failed_events)
        lowered_visible_text = visible_text.lower()
        for term in FORBIDDEN_PUBLIC_TERMS:
            assert term not in lowered_visible_text
    finally:
        _write_json(report_dir / "preview-repair-intent-smoke.json", evidence)


def _require_live_repair_intent_smoke() -> None:
    if os.getenv("STRATEGY_CODEBOT_RUN_LIVE_REPAIR_INTENT_SMOKE") != "1":
        pytest.skip("Set STRATEGY_CODEBOT_RUN_LIVE_REPAIR_INTENT_SMOKE=1 to run live repair intent smoke.")
    if (os.getenv("STRATEGY_CODEBOT_LLM_MODE") or "").strip().lower() == "fake":
        raise AssertionError("Live repair intent smoke must not run with STRATEGY_CODEBOT_LLM_MODE=fake")
    routing = (os.getenv("STRATEGY_CODEBOT_LLM_ROUTING") or "").strip().lower()
    if routing == "registry":
        if not (os.getenv("LITELLM_PROXY_API_KEY") or os.getenv("OPENROUTER_API_KEY") or os.getenv("VERCEL_AI_GATEWAY_API_KEY") or os.getenv("OPENAI_API_KEY")):
            raise AssertionError("Registry live repair smoke requires at least one configured provider credential.")
        return
    provider = (os.getenv("STRATEGY_CODEBOT_LLM_PROVIDER") or "").strip().lower()
    if not provider:
        if not os.getenv("OPENAI_API_KEY"):
            raise AssertionError("OPENAI_API_KEY is required when STRATEGY_CODEBOT_LLM_PROVIDER is unset.")
        return
    required_env_by_provider = {
        "openrouter": "OPENROUTER_API_KEY",
        "vercel-ai-gateway": "VERCEL_AI_GATEWAY_API_KEY",
        "vercel_ai_gateway": "VERCEL_AI_GATEWAY_API_KEY",
        "openai": "OPENAI_API_KEY",
    }
    required = required_env_by_provider.get(provider)
    if required is None:
        raise AssertionError(f"Unsupported live repair intent provider: {provider}")
    if not os.getenv(required):
        raise AssertionError(f"{required} is required for live repair intent provider {provider}.")


def _strategy_spec() -> dict[str, Any]:
    return {
        "name": "HTF RSI pullback",
        "script_type": "strategy",
        "position_sizing": "risk 1% per trade",
        "stop_loss": "2%",
        "take_profit": "4%",
        "risk_rules": ["Use explicit stop and take profit."],
        "timeframes": {"signal": "1h", "higher_timeframe_context": "1D"},
    }


def _backtest_config() -> dict[str, Any]:
    return {
        "engine": "pineforge",
        "exchange": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "candle_timeframe": "1m",
        "start": "2025-01-01",
        "end": "2025-01-08",
        "initial_capital": 10000,
        "fee_bps": 10,
        "slippage_bps": 5,
        "data_source": "public-readonly-cache",
    }


def _preview_job_payload(repository, run_id: str) -> dict[str, Any]:
    with repository._session_factory() as session:  # noqa: SLF001
        job = session.query(RunJob).filter_by(run_id=run_id, job_type=RUN_MODE_BACKTEST_PREVIEW).one()
        return job.payload_json


def _job_summaries(repository, job_type: str) -> list[dict[str, Any]]:
    with repository._session_factory() as session:  # noqa: SLF001
        jobs = session.query(RunJob).filter_by(job_type=job_type).order_by(RunJob.created_at, RunJob.id).all()
        return [
            {
                "id": job.id,
                "run_id": job.run_id,
                "status": job.status,
                "attempts": job.attempts,
                "error_code": job.error_code,
                "result_json": job.result_json,
            }
            for job in jobs
        ]


def _event_summary(event) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return {
        "type": event.type,
        "payload": {
            key: payload.get(key)
            for key in (
                "preview_error_code",
                "repair_attempts",
                "compatibility_repair_applied",
                "manual_validation_required",
                "message",
                "child_run_id",
            )
            if key in payload
        },
    }


def _provider_summary() -> dict[str, str | None]:
    keys = (
        "STRATEGY_CODEBOT_LLM_MODE",
        "STRATEGY_CODEBOT_LLM_ROUTING",
        "STRATEGY_CODEBOT_LLM_PROVIDER",
        "STRATEGY_CODEBOT_LLM_MODEL",
        "STRATEGY_CODEBOT_MODEL_REGISTRY",
    )
    return {key: os.getenv(key) for key in keys}


def _report_dir() -> Path:
    configured = os.getenv("STRATEGY_CODEBOT_LIVE_REPAIR_INTENT_REPORT_DIR")
    path = Path(configured) if configured else Path("reports/live-repair-intent") / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
