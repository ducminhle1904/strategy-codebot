from __future__ import annotations

import json
from typing import Any

from strategy_codebot.pine import validate_pineforge_pine
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.backtest_auto_chain import BACKTEST_AUTO_CHAIN_EVENTS
from strategy_codebot.server.ids import opaque_id
from strategy_codebot.server.llm_clients import LLMClient
from strategy_codebot.server.llm_clients import LLM_EVENT_MESSAGE_DELTA
from strategy_codebot.server.llm_json import extract_json_object
from strategy_codebot.server.model_routing import MODEL_STAGE_REPAIR
from strategy_codebot.server.repository import ConversationRepository
from strategy_codebot.server.run_modes import PREVIEW_COMPATIBILITY_REPAIR_JOB_MAX_ATTEMPTS
from strategy_codebot.server.run_modes import PREVIEW_COMPATIBILITY_REPAIR_JOB_TYPE
from strategy_codebot.server.run_modes import RUN_MODE_BACKTEST_PREVIEW
from strategy_codebot.server.run_modes import backtest_job_limits_for_tier
from strategy_codebot.server.run_modes import backtest_runtime_boundary

MAX_REPAIR_ATTEMPTS = PREVIEW_COMPATIBILITY_REPAIR_JOB_MAX_ATTEMPTS
MAX_REPAIR_MODEL_RESPONSE_CHARS = 40_000
REPAIR_STARTED_EVENT = "validation.repair.started"
REPAIR_COMPLETED_EVENT = "validation.repair.completed"
REPAIR_FAILED_EVENT = "validation.repair.failed"
REPAIR_QUEUED_MESSAGE = "Compatibility repair applied. Re-running local preview."
UNREPAIRED_FAILURE_MESSAGE = (
    "Local preview cannot run part of this script yet. "
    "The Pine code may still require manual platform validation."
)


def process_preview_compatibility_repair_job(
    repository: ConversationRepository,
    client: LLMClient,
    auth: AuthContext,
    run_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    failed_run_id = _string_value(payload.get("failed_backtest_run_id")) or run_id
    source_run_id = _string_value(payload.get("source_run_id"))
    conversation_id = _string_value(payload.get("conversation_id"))
    strategy_spec = _dict_value(payload.get("strategy_spec"))
    backtest_config = _dict_value(payload.get("backtest_config"))
    pine_code = _string_value(payload.get("pine_code"))
    repair = _dict_value(payload.get("compatibility_repair"))
    auto_chain = _dict_value(payload.get("auto_chain")) or {}
    summary_on_complete = auto_chain.get("summary_on_complete") is True
    attempt = _positive_int(repair.get("attempt") if repair else None) or 1
    max_attempts = _positive_int(repair.get("max_attempts") if repair else None) or MAX_REPAIR_ATTEMPTS
    max_attempts = min(max_attempts, MAX_REPAIR_ATTEMPTS)
    if strategy_spec is None or backtest_config is None or pine_code is None:
        raise ValueError("Compatibility repair job payload is incomplete")
    if attempt > max_attempts:
        _append_repair_failed(repository, auth, failed_run_id, source_run_id, attempt, max_attempts)
        return {"status": "exhausted", "attempt": attempt, "max_attempts": max_attempts}

    event_payload = _public_repair_payload(
        failed_run_id=failed_run_id,
        source_run_id=source_run_id,
        attempt=attempt,
        max_attempts=max_attempts,
        message="Adapting strategy for local preview compatibility.",
    )
    _append_mirrored_event(repository, auth, failed_run_id, source_run_id, REPAIR_STARTED_EVENT, event_payload)

    repaired = _repair_pine_with_model(
        client,
        auth=auth,
        strategy_spec=strategy_spec,
        backtest_config=backtest_config,
        pine_code=pine_code,
        diagnostics=_dict_value(payload.get("internal_diagnostics")) or {},
        attempt=attempt,
        max_attempts=max_attempts,
    )
    repaired_pine = _string_value(repaired.get("pine_code"))
    if repaired_pine is None:
        _append_repair_failed(repository, auth, failed_run_id, source_run_id, attempt, max_attempts)
        return {"status": "failed", "attempt": attempt, "reason": "missing_pine_code"}

    validation = validate_pineforge_pine(repaired_pine, strategy_spec)
    if validation.get("status") == "fail":
        _append_repair_failed(repository, auth, failed_run_id, source_run_id, attempt, max_attempts)
        return {
            "status": "failed",
            "attempt": attempt,
            "reason": "validation_failed",
            "validation_status": validation.get("status"),
        }

    failed_run = repository.get_run(auth, failed_run_id)
    if failed_run is None:
        raise ValueError("Failed preview run not found")
    target_conversation_id = conversation_id or failed_run.conversation_id
    queued_run = repository.create_run(
        auth,
        target_conversation_id,
        status="queued",
        mode=RUN_MODE_BACKTEST_PREVIEW,
        request_id=opaque_id("req"),
        retry_of_run_id=failed_run_id,
        trace_id=failed_run.trace_id,
    )
    if queued_run is None:
        raise ValueError("Could not create repaired preview run")
    repository.create_strategy_spec(auth, queued_run.id, strategy_spec, "backtest-preview.compatibility-repair.v1")
    job = repository.create_run_job(
        auth,
        queued_run.id,
        job_type=RUN_MODE_BACKTEST_PREVIEW,
        payload_json={
            "strategy_spec": strategy_spec,
            "pine_code": repaired_pine,
            "backtest_config": backtest_config,
            "runtime": backtest_runtime_boundary(str(backtest_config.get("engine") or "pineforge")),
            "limits": payload.get("limits") if isinstance(payload.get("limits"), dict) else backtest_job_limits_for_tier(auth.user_tier),
            "auto_chain": {
                "summary_on_complete": summary_on_complete,
                "source_run_id": source_run_id or failed_run_id,
                "conversation_id": target_conversation_id,
            },
            "compatibility_repair": {
                "attempt": attempt,
                "max_attempts": max_attempts,
                "source": "local_preview_failure",
                "source_run_id": source_run_id or failed_run_id,
                "failed_run_id": failed_run_id,
                "failed_job_id": payload.get("failed_job_id"),
            },
        },
    )
    if job is None:
        repository.set_run_status(auth, queued_run.id, "failed")
        raise ValueError("Could not queue repaired preview job")

    completed_payload = _public_repair_payload(
        failed_run_id=failed_run_id,
        source_run_id=source_run_id,
        attempt=attempt,
        max_attempts=max_attempts,
        message=REPAIR_QUEUED_MESSAGE,
        child_run_id=queued_run.id,
        job_id=job.id,
    )
    _append_mirrored_event(repository, auth, failed_run_id, source_run_id, REPAIR_COMPLETED_EVENT, completed_payload)
    repository.append_run_event(
        auth,
        queued_run.id,
        "backtest.queued",
        {
            "job_id": job.id,
            "job_type": job.job_type,
            "mode": RUN_MODE_BACKTEST_PREVIEW,
            "preview_error_code": "preview_compatibility_limit",
            "repair_attempts": attempt,
            "compatibility_repair_applied": True,
            "manual_validation_required": False,
            "message": REPAIR_QUEUED_MESSAGE,
        },
    )
    if summary_on_complete and source_run_id and source_run_id != queued_run.id:
        repository.append_run_event(
            auth,
            source_run_id,
            BACKTEST_AUTO_CHAIN_EVENTS["waiting"],
            {"child_run_id": queued_run.id, "status": "queued", "repair_attempts": attempt},
        )
    return {
        "status": "queued",
        "attempt": attempt,
        "max_attempts": max_attempts,
        "run_id": queued_run.id,
        "job_id": job.id,
        "validation_status": validation.get("status"),
    }


def _repair_pine_with_model(
    client: LLMClient,
    *,
    auth: AuthContext,
    strategy_spec: dict[str, Any],
    backtest_config: dict[str, Any],
    pine_code: str,
    diagnostics: dict[str, Any],
    attempt: int,
    max_attempts: int,
) -> dict[str, Any]:
    messages = [
        {
            "role": "system",
            "content": (
                "You repair Pine Script v6 strategies only for local preview compatibility. "
                "Preserve strategy intent, timeframe assumptions, risk rules, and the //@version=6 header. "
                "Replace constructs the local preview cannot execute with simpler supported equivalents. "
                "Do not mention unsupported API or function names inside generated Pine comments. "
                "Comments must describe replacement behavior without naming internal compatibility failure details. "
                "Return only JSON with keys pine_code, repair_notes, and unsupported_surface_summary."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "strategy_spec": strategy_spec,
                    "backtest_config": backtest_config,
                    "pine_code": pine_code,
                    "internal_diagnostics": diagnostics,
                },
                sort_keys=True,
            ),
        },
    ]
    text_parts: list[str] = []
    text_length = 0
    for event in client.stream(
        messages=messages,
        tools=[],
        routing_context={"auth": auth, "stage": MODEL_STAGE_REPAIR},
    ):
        if event.type == LLM_EVENT_MESSAGE_DELTA and event.text:
            text_length += len(event.text)
            if text_length > MAX_REPAIR_MODEL_RESPONSE_CHARS:
                return {}
            text_parts.append(event.text)
    return _parse_model_json("".join(text_parts))


def _parse_model_json(text: str) -> dict[str, Any]:
    return extract_json_object(text) or {}


def _append_repair_failed(
    repository: ConversationRepository,
    auth: AuthContext,
    failed_run_id: str,
    source_run_id: str | None,
    attempt: int,
    max_attempts: int,
) -> None:
    _append_mirrored_event(
        repository,
        auth,
        failed_run_id,
        source_run_id,
        REPAIR_FAILED_EVENT,
        _public_repair_payload(
            failed_run_id=failed_run_id,
            source_run_id=source_run_id,
            attempt=attempt,
            max_attempts=max_attempts,
            message=UNREPAIRED_FAILURE_MESSAGE,
            manual_validation_required=True,
        ),
    )


def _append_mirrored_event(
    repository: ConversationRepository,
    auth: AuthContext,
    run_id: str,
    source_run_id: str | None,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    repository.append_run_event(auth, run_id, event_type, payload)
    if source_run_id and source_run_id != run_id:
        repository.append_run_event(auth, source_run_id, event_type, payload)


def _public_repair_payload(
    *,
    failed_run_id: str,
    source_run_id: str | None,
    attempt: int,
    max_attempts: int,
    message: str,
    child_run_id: str | None = None,
    job_id: str | None = None,
    manual_validation_required: bool = False,
) -> dict[str, Any]:
    return {
        "job_type": PREVIEW_COMPATIBILITY_REPAIR_JOB_TYPE,
        "failed_backtest_run_id": failed_run_id,
        "source_run_id": source_run_id,
        "child_run_id": child_run_id,
        "job_id": job_id,
        "preview_error_code": "preview_compatibility_limit",
        "repair_attempts": attempt,
        "compatibility_repair_applied": child_run_id is not None,
        "manual_validation_required": manual_validation_required,
        "message": message,
        "max_attempts": max_attempts,
    }


def _dict_value(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _string_value(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    return None
