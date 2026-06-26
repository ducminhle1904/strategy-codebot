from __future__ import annotations

import argparse
import logging
import os
import time
from typing import Any

from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.backtest_auto_chain import BACKTEST_AUTO_CHAIN_EVENTS
from strategy_codebot.server.backtest_summary_text import format_backtest_summary_text
from strategy_codebot.server.backtest_summary_text import string_value
from strategy_codebot.server.database import create_sqlalchemy_repository
from strategy_codebot.server.llm_clients import E2EFakeLLMClient
from strategy_codebot.server.llm_clients import LLMClient
from strategy_codebot.server.llm_clients import ResponsesClient
from strategy_codebot.server.model_routing import RegistryRoutedLLMClient
from strategy_codebot.server.model_routing import default_route_client_factory
from strategy_codebot.server.model_routing import model_registry_path_from_env
from strategy_codebot.server.preview_compatibility_repair import process_preview_compatibility_repair_job
from strategy_codebot.server.repository import ConversationRepository
from strategy_codebot.server.run_modes import CHAT_BACKTEST_SUMMARY_JOB_TYPE
from strategy_codebot.server.run_modes import PREVIEW_COMPATIBILITY_REPAIR_JOB_TYPE

logger = logging.getLogger(__name__)

CHAT_WORKER_JOB_TYPES = (PREVIEW_COMPATIBILITY_REPAIR_JOB_TYPE, CHAT_BACKTEST_SUMMARY_JOB_TYPE)


def run_chat_worker(
    repository: ConversationRepository,
    *,
    worker_id: str,
    repair_client: LLMClient | None = None,
    lease_seconds: int = 60,
    poll_interval_seconds: float = 2.0,
    once: bool = False,
) -> int:
    processed = 0
    resolved_repair_client = repair_client
    while True:
        job = None
        for job_type in CHAT_WORKER_JOB_TYPES:
            job = repository.claim_run_job(job_type=job_type, worker_id=worker_id, lease_seconds=lease_seconds)
            if job is not None:
                break
        if job is None:
            if once:
                return processed
            time.sleep(poll_interval_seconds)
            continue
        auth = AuthContext(job.owner_user_id, job.workspace_id)
        try:
            if job.job_type == PREVIEW_COMPATIBILITY_REPAIR_JOB_TYPE:
                if resolved_repair_client is None:
                    resolved_repair_client = _default_repair_client()
                result = process_preview_compatibility_repair_job(repository, resolved_repair_client, auth, job.run_id, job.payload_json)
            else:
                result = _process_backtest_summary_job(repository, auth, job.run_id, job.payload_json)
            repository.complete_run_job(job.id, status="completed", result_json=result)
            processed += 1
        except Exception as exc:
            message = str(exc)
            logger.warning("chat worker job failed job_id=%s run_id=%s type=%s error=%s", job.id, job.run_id, job.job_type, message)
            failure_payload = {"job_id": job.id, "job_type": job.job_type, "message": _user_safe_failure_message(job.job_type)}
            repository.append_run_event(
                auth,
                job.run_id,
                BACKTEST_AUTO_CHAIN_EVENTS["failed"],
                failure_payload,
            )
            source_run_id = string_value(job.payload_json.get("source_run_id")) if isinstance(job.payload_json, dict) else None
            if source_run_id and source_run_id != job.run_id:
                repository.append_run_event(auth, source_run_id, BACKTEST_AUTO_CHAIN_EVENTS["failed"], failure_payload)
            repository.complete_run_job(
                job.id,
                status="failed",
                result_json={"error": exc.__class__.__name__, "message": message},
                error_code=_job_error_code(job.job_type),
            )
        if once:
            return processed


def _process_backtest_summary_job(
    repository: ConversationRepository,
    auth: AuthContext,
    run_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    backtest_run_id = string_value(payload.get("backtest_run_id")) or run_id
    conversation_id = string_value(payload.get("conversation_id"))
    if not conversation_id:
        run = repository.get_run(auth, backtest_run_id)
        if run is None:
            raise ValueError("Backtest run not found")
        conversation_id = run.conversation_id
    summary = repository.get_backtest_summary(auth, backtest_run_id)
    if summary is None:
        raise ValueError("Backtest summary is not available")
    text = format_backtest_summary_text(summary, completed=True, include_run_id=True)
    if not _has_completed_summary_event(repository, auth, backtest_run_id):
        created = repository.create_message(auth, conversation_id, text, role="assistant")
        if created is None:
            raise ValueError("Could not append backtest summary message")
    event_payload = {
        "job_type": CHAT_BACKTEST_SUMMARY_JOB_TYPE,
        "backtest_run_id": backtest_run_id,
        "conversation_id": conversation_id,
        "source_run_id": payload.get("source_run_id"),
        "summary": {
            "symbol": summary.get("symbol"),
            "signal_timeframe": summary.get("signal_timeframe"),
            "candle_timeframe": summary.get("candle_timeframe"),
            "metrics": summary.get("metrics"),
        },
    }
    repository.append_run_event(auth, backtest_run_id, BACKTEST_AUTO_CHAIN_EVENTS["summary_completed"], event_payload)
    source_run_id = string_value(payload.get("source_run_id"))
    if source_run_id and source_run_id != backtest_run_id:
        repository.append_run_event(auth, source_run_id, BACKTEST_AUTO_CHAIN_EVENTS["summary_completed"], event_payload)
    return {"message": "summary_appended", "backtest_run_id": backtest_run_id, "conversation_id": conversation_id}


def _has_completed_summary_event(repository: ConversationRepository, auth: AuthContext, run_id: str) -> bool:
    events = repository.list_run_events(auth, run_id) or []
    return any(
        event.type == BACKTEST_AUTO_CHAIN_EVENTS["summary_completed"]
        and isinstance(event.payload, dict)
        and event.payload.get("backtest_run_id") == run_id
        for event in events
    )


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _default_repair_client() -> LLMClient:
    if os.getenv("STRATEGY_CODEBOT_LLM_MODE") == "fake":
        return E2EFakeLLMClient()
    routing = os.getenv("STRATEGY_CODEBOT_LLM_ROUTING", "").strip().lower()
    if routing == "registry":
        return RegistryRoutedLLMClient(registry_path=model_registry_path_from_env())
    provider = os.getenv("STRATEGY_CODEBOT_LLM_PROVIDER", "").strip().lower()
    model = os.getenv("STRATEGY_CODEBOT_LLM_MODEL", "openai/gpt-5.5").strip() or "openai/gpt-5.5"
    if provider:
        return default_route_client_factory(f"{provider}/{model}")
    return ResponsesClient()


def _user_safe_failure_message(job_type: str) -> str:
    if job_type == PREVIEW_COMPATIBILITY_REPAIR_JOB_TYPE:
        return "Local preview compatibility repair failed."
    return "Backtest summary could not be appended."


def _job_error_code(job_type: str) -> str:
    if job_type == PREVIEW_COMPATIBILITY_REPAIR_JOB_TYPE:
        return "preview_compatibility_repair_failed"
    return "chat_backtest_summary_failed"


def main() -> None:
    parser = argparse.ArgumentParser(description="Strategy Codebot chat continuation worker")
    parser.add_argument("--once", action="store_true", help="Process at most one available job then exit.")
    args = parser.parse_args()
    logging.basicConfig(level=os.getenv("STRATEGY_CODEBOT_CHAT_WORKER_LOG_LEVEL", "INFO"))
    database_url = os.getenv("STRATEGY_CODEBOT_API_DATABASE_URL")
    if not database_url:
        raise SystemExit("STRATEGY_CODEBOT_API_DATABASE_URL is required")
    worker_id = os.getenv("STRATEGY_CODEBOT_CHAT_WORKER_ID", f"chat-worker-{os.getpid()}")
    lease_seconds = _positive_int_env("STRATEGY_CODEBOT_CHAT_WORKER_LEASE_SECONDS", 60)
    poll_interval_seconds = _positive_float_env("STRATEGY_CODEBOT_CHAT_WORKER_POLL_INTERVAL_SECONDS", 2.0)
    repository = create_sqlalchemy_repository(database_url)
    run_chat_worker(
        repository,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        poll_interval_seconds=poll_interval_seconds,
        once=args.once,
    )


if __name__ == "__main__":
    main()
