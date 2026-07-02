from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
import json
import os
import re
import time
from typing import Any

from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.llm_clients import LLMClient
from strategy_codebot.server.llm_clients import LLM_EVENT_MESSAGE_DELTA
from strategy_codebot.server.llm_clients import stream_client
from strategy_codebot.server.llm_json import extract_json_object
from strategy_codebot.server.model_routing import MODEL_STAGE_WORKFLOW_FAST
from strategy_codebot.server.model_routing import PROVIDER_ROUTE_EVENT
from strategy_codebot.server.workflow_tasks import merge_workflow_task_input_request_overrides
from strategy_codebot.server.workflow_tasks import workflow_task_next_input_request_id


WORKFLOW_PROMPT_GENERATOR_TIMEOUT_SECONDS_ENV = "STRATEGY_CODEBOT_WORKFLOW_PROMPT_GENERATOR_TIMEOUT_SECONDS"
WORKFLOW_PROMPT_GENERATOR_ENABLED_ENV = "STRATEGY_CODEBOT_WORKFLOW_PROMPT_GENERATOR_ENABLED"
DEFAULT_WORKFLOW_PROMPT_GENERATOR_TIMEOUT_SECONDS = 8.0
WORKFLOW_PROMPT_GENERATOR_EVENT_STARTED = "workflow_prompt_generator.started"
WORKFLOW_PROMPT_GENERATOR_EVENT_ROUTE = "workflow_prompt_generator.route"
WORKFLOW_PROMPT_GENERATOR_EVENT_COMPLETED = "workflow_prompt_generator.completed"
WORKFLOW_PROMPT_GENERATOR_EVENT_TIMEOUT = "workflow_prompt_generator.timeout"
WORKFLOW_PROMPT_GENERATOR_EVENT_FAILED = "workflow_prompt_generator.failed"


@dataclass(frozen=True)
class WorkflowPromptGenerationResult:
    payload: dict[str, Any]
    status: str
    input_id: str | None = None
    target_input_ids: tuple[str, ...] = ()
    generated_input_ids: tuple[str, ...] = ()
    fallback_input_ids: tuple[str, ...] = ()
    option_count: int = 0
    duration_ms: int = 0
    fallback_reason: str | None = None
    route_events: tuple[dict[str, Any], ...] = ()


def generate_workflow_task_prompt_payload(
    client: LLMClient,
    *,
    workflow_id: str,
    task_payload: dict[str, Any],
    language: str | None,
    user_prompt: str,
    task_values: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    auth: AuthContext | None = None,
    user_tier: str | None = None,
) -> WorkflowPromptGenerationResult:
    values = task_values if isinstance(task_values, dict) else {}
    target_input_ids = _target_input_request_ids(workflow_id, task_payload, values=values)
    input_id = target_input_ids[0] if target_input_ids else None
    if not target_input_ids:
        return WorkflowPromptGenerationResult(payload=task_payload, status="skipped", fallback_reason="no_open_input")
    if not _workflow_prompt_generator_enabled():
        return WorkflowPromptGenerationResult(
            payload=_payload_with_prompt_metadata(
                task_payload,
                prompt_source="registry_fallback",
                target_input_ids=target_input_ids,
                generated_input_ids=(),
                fallback_input_ids=target_input_ids,
            ),
            status="fallback",
            input_id=input_id,
            target_input_ids=target_input_ids,
            fallback_input_ids=target_input_ids,
            fallback_reason="disabled",
        )
    started = time.perf_counter()
    timeout_seconds = _workflow_prompt_generator_timeout_seconds()
    route_events: list[dict[str, Any]] = []
    try:
        decoded = _run_with_timeout(
            lambda: _generate_prompt_override(
                client,
                workflow_id=workflow_id,
                task_payload=task_payload,
                target_input_ids=target_input_ids,
                language=_workflow_prompt_language(language, user_prompt),
                user_prompt=user_prompt,
                task_values=values,
                context=context or {},
                timeout_seconds=timeout_seconds,
                route_events=route_events,
                auth=auth,
                user_tier=user_tier,
            ),
            timeout_seconds=timeout_seconds,
        )
    except FutureTimeoutError:
        return WorkflowPromptGenerationResult(
            payload=_payload_with_prompt_metadata(
                task_payload,
                prompt_source="registry_fallback",
                target_input_ids=target_input_ids,
                generated_input_ids=(),
                fallback_input_ids=target_input_ids,
            ),
            status="timeout",
            input_id=input_id,
            target_input_ids=target_input_ids,
            fallback_input_ids=target_input_ids,
            duration_ms=_duration_ms(started),
            fallback_reason="timeout",
            route_events=tuple(route_events),
        )
    except Exception:
        return WorkflowPromptGenerationResult(
            payload=_payload_with_prompt_metadata(
                task_payload,
                prompt_source="registry_fallback",
                target_input_ids=target_input_ids,
                generated_input_ids=(),
                fallback_input_ids=target_input_ids,
            ),
            status="fallback",
            input_id=input_id,
            target_input_ids=target_input_ids,
            fallback_input_ids=target_input_ids,
            duration_ms=_duration_ms(started),
            fallback_reason="provider_or_parse_error",
            route_events=tuple(route_events),
        )
    overrides = _decoded_input_request_overrides(decoded, target_input_ids)
    generated_ids = _accepted_override_ids(overrides, target_input_ids)
    missing_ids = tuple(request_id for request_id in target_input_ids if request_id not in generated_ids)
    if missing_ids and isinstance(decoded, dict):
        try:
            repair_decoded = _run_with_timeout(
                lambda: _generate_prompt_override(
                    client,
                    workflow_id=workflow_id,
                    task_payload=task_payload,
                    target_input_ids=missing_ids,
                    language=_workflow_prompt_language(language, user_prompt),
                    user_prompt=user_prompt,
                    task_values=values,
                    context={**(context or {}), "repair_missing_input_ids": list(missing_ids)},
                    timeout_seconds=timeout_seconds,
                    route_events=route_events,
                    auth=auth,
                    user_tier=user_tier,
                    repair=True,
                ),
                timeout_seconds=timeout_seconds,
            )
        except (FutureTimeoutError, Exception):
            repair_decoded = None
        repair_overrides = _decoded_input_request_overrides(repair_decoded, missing_ids)
        overrides = _merge_override_lists(overrides, repair_overrides)
        generated_ids = _accepted_override_ids(overrides, target_input_ids)
        missing_ids = tuple(request_id for request_id in target_input_ids if request_id not in generated_ids)
    if not generated_ids:
        fallback_payload = _payload_with_prompt_metadata(
            task_payload,
            prompt_source="registry_fallback",
            target_input_ids=target_input_ids,
            generated_input_ids=(),
            fallback_input_ids=target_input_ids,
        )
        return WorkflowPromptGenerationResult(
            payload=fallback_payload,
            status="fallback",
            input_id=input_id,
            target_input_ids=target_input_ids,
            fallback_input_ids=target_input_ids,
            duration_ms=_duration_ms(started),
            fallback_reason="invalid_output",
            route_events=tuple(route_events),
        )
    overrides = [item for item in overrides if item.get("id") in set(generated_ids)]
    enriched = merge_workflow_task_input_request_overrides(
        workflow_id,
        task_payload,
        overrides,
        values=values,
    )
    option_count = sum(_input_option_count(enriched, request_id) for request_id in target_input_ids)
    generated_ids = _generated_ids_after_merge(enriched, overrides, target_input_ids)
    fallback_ids = tuple(request_id for request_id in target_input_ids if request_id not in generated_ids)
    prompt_source = "generated" if not fallback_ids else "registry_fallback"
    enriched = _payload_with_prompt_metadata(
        enriched,
        prompt_source=prompt_source,
        target_input_ids=target_input_ids,
        generated_input_ids=generated_ids,
        fallback_input_ids=fallback_ids,
    )
    if enriched == task_payload:
        return WorkflowPromptGenerationResult(
            payload=enriched,
            status="fallback",
            input_id=input_id,
            target_input_ids=target_input_ids,
            generated_input_ids=generated_ids,
            fallback_input_ids=fallback_ids or target_input_ids,
            option_count=option_count,
            duration_ms=_duration_ms(started),
            fallback_reason="sanitized_empty",
            route_events=tuple(route_events),
        )
    return WorkflowPromptGenerationResult(
        payload=enriched,
        status="generated",
        input_id=input_id,
        target_input_ids=target_input_ids,
        generated_input_ids=generated_ids,
        fallback_input_ids=fallback_ids,
        option_count=option_count,
        duration_ms=_duration_ms(started),
        fallback_reason="partial_registry_fallback" if fallback_ids else None,
        route_events=tuple(route_events),
    )


def _generate_prompt_override(
    client: LLMClient,
    *,
    workflow_id: str,
    task_payload: dict[str, Any],
    target_input_ids: tuple[str, ...],
    language: str,
    user_prompt: str,
    task_values: dict[str, Any],
    context: dict[str, Any],
    timeout_seconds: float,
    route_events: list[dict[str, Any]],
    auth: AuthContext | None,
    user_tier: str | None,
    repair: bool = False,
) -> dict[str, Any] | None:
    prompt = {
        "workflow_id": workflow_id,
        "task_template_id": task_payload.get("task_template_id"),
        "target_input_ids": list(target_input_ids),
        "language": language,
        "user_prompt": user_prompt[:1800],
        "task_values": _safe_task_values(task_values),
        "target_requests": _request_payloads(task_payload, target_input_ids),
        "known_context": _safe_context(context),
        "rules": [
            "Return JSON only.",
            "Generate presentation copy for every id in target_input_ids.",
            "Return input_requests with exactly one item for each target_input_ids id.",
            "Do not create fields, tasks, actions, workflow steps, tool calls, or trading runtime instructions.",
            "Use up to 3 enabled options. The first option should be your recommendation.",
            "Use concise labels and optional short descriptions that match the user's language.",
        ],
    }
    if repair:
        prompt["repair"] = True
        prompt["rules"].append("This is a repair pass: include every target id that was missing or invalid previously.")
    routing_context: dict[str, Any] = {
        "stage": MODEL_STAGE_WORKFLOW_FAST,
        "route_timeout_seconds": timeout_seconds,
        "hard_route_timeout": True,
    }
    if auth is not None:
        routing_context["auth"] = auth
        routing_context["user_tier"] = auth.user_tier
    elif user_tier:
        routing_context["user_tier"] = user_tier
    chunks: list[str] = []
    for event in stream_client(
        client,
        messages=[
            {"role": "system", "content": _workflow_prompt_generator_system_prompt()},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        tools=[],
        routing_context=routing_context,
    ):
        if event.type == PROVIDER_ROUTE_EVENT and isinstance(event.arguments, dict):
            route_events.append(_safe_route_event(event.arguments))
        if event.type == LLM_EVENT_MESSAGE_DELTA and event.text:
            chunks.append(event.text)
    return extract_json_object("".join(chunks))


def workflow_prompt_generator_events(
    result: WorkflowPromptGenerationResult,
    *,
    workflow_id: str,
    task_id: str | None = None,
    task_template_id: str | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    payload = _event_payload(result, workflow_id, task_id=task_id, task_template_id=task_template_id)
    events: list[tuple[str, dict[str, Any]]] = [(WORKFLOW_PROMPT_GENERATOR_EVENT_STARTED, {**payload, "status": "started"})]
    for route_event in result.route_events:
        events.append(
            (
                WORKFLOW_PROMPT_GENERATOR_EVENT_ROUTE,
                {
                    **payload,
                    "status": "route",
                    **route_event,
                },
            )
        )
    final_type = {
        "generated": WORKFLOW_PROMPT_GENERATOR_EVENT_COMPLETED,
        "timeout": WORKFLOW_PROMPT_GENERATOR_EVENT_TIMEOUT,
    }.get(result.status, WORKFLOW_PROMPT_GENERATOR_EVENT_FAILED)
    events.append((final_type, payload))
    return events


def _workflow_prompt_generator_system_prompt() -> str:
    return (
        "You personalize human-in-the-loop workflow questions. "
        "You are not the workflow authority. The backend registry controls valid fields, requiredness, gates, "
        "actions, and runtime behavior. Return a single JSON object in this shape: "
        '{"input_requests":[{"id":"<target_input_id>","question":"...","options":[{"id":"...",'
        '"value":"...","label":"...","description":"..."}],"recommended_option_id":"<option id>",'
        '"custom_option_label":"...","placeholder":"...","helper_text":"..."}]}.'
    )


def _decoded_input_request_overrides(decoded: dict[str, Any] | None, target_input_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    if not isinstance(decoded, dict):
        return []
    allowed = set(target_input_ids)
    candidates: list[Any] = []
    if isinstance(decoded.get("input_requests"), list):
        candidates.extend(decoded.get("input_requests") or [])
    if isinstance(decoded.get("input_request"), dict):
        candidates.append(decoded.get("input_request"))
    if isinstance(decoded.get("id"), str):
        candidates.append(decoded)
    overrides_by_id: dict[str, dict[str, Any]] = {}
    for item in candidates:
        if not isinstance(item, dict):
            continue
        request_id = item.get("id")
        if isinstance(request_id, str) and request_id in allowed and request_id not in overrides_by_id:
            overrides_by_id[request_id] = item
    return [overrides_by_id[request_id] for request_id in target_input_ids if request_id in overrides_by_id]


def _request_payloads(task_payload: dict[str, Any], target_input_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    return [_request_payload(task_payload, input_id) for input_id in target_input_ids]


def _request_payload(task_payload: dict[str, Any], input_id: str) -> dict[str, Any]:
    for item in task_payload.get("input_requests") or []:
        if isinstance(item, dict) and item.get("id") == input_id:
            return {
                key: item.get(key)
                for key in (
                    "id",
                    "field",
                    "label",
                    "question",
                    "kind",
                    "required",
                    "placeholder",
                    "allow_custom",
                    "custom_option_label",
                    "recommended_option_id",
                    "options",
                )
                if key in item
            }
    return {"id": input_id}


def _safe_task_values(values: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in values.items():
        if not isinstance(key, str) or len(safe) >= 20:
            continue
        if isinstance(value, str):
            safe[key] = value[:240]
        elif isinstance(value, bool | int | float) or value is None:
            safe[key] = value
    return safe


def _target_input_request_ids(
    workflow_id: str,
    task_payload: dict[str, Any],
    *,
    values: dict[str, Any],
) -> tuple[str, ...]:
    task_values = dict(task_payload.get("values") or {})
    task_values.update(values)
    request_ids = [
        item
        for item in task_payload.get("input_request_ids") or []
        if isinstance(item, str) and item
    ]
    targets = tuple(request_id for request_id in request_ids if request_id not in task_values or _is_empty_value(task_values.get(request_id)))
    if targets:
        return targets
    next_input_id = workflow_task_next_input_request_id(workflow_id, task_payload, values=values)
    return (next_input_id,) if next_input_id else ()


def _is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _accepted_override_ids(overrides: list[dict[str, Any]], target_input_ids: tuple[str, ...]) -> tuple[str, ...]:
    allowed = set(target_input_ids)
    accepted: list[str] = []
    for item in overrides:
        request_id = item.get("id")
        if not isinstance(request_id, str) or request_id not in allowed or request_id in accepted:
            continue
        if not _has_generated_question_and_options(item):
            continue
        accepted.append(request_id)
    return tuple(accepted)


def _has_generated_question_and_options(item: dict[str, Any]) -> bool:
    question = item.get("question")
    if not isinstance(question, str) or not question.strip():
        return False
    options = item.get("options")
    if not isinstance(options, list) or not options:
        return False
    for option in options:
        if not isinstance(option, dict):
            continue
        if all(isinstance(option.get(key), str) and option.get(key).strip() for key in ("id", "value", "label")):
            return True
    return False


def _merge_override_lists(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in [*existing, *incoming]:
        request_id = item.get("id") if isinstance(item, dict) else None
        if isinstance(request_id, str) and request_id not in merged:
            merged[request_id] = item
    return list(merged.values())


def _generated_ids_after_merge(
    payload: dict[str, Any],
    overrides: list[dict[str, Any]],
    target_input_ids: tuple[str, ...],
) -> tuple[str, ...]:
    accepted = set(_accepted_override_ids(overrides, target_input_ids))
    generated: list[str] = []
    requests = {
        item.get("id"): item
        for item in payload.get("input_requests") or []
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    for request_id in target_input_ids:
        request = requests.get(request_id)
        if request_id in accepted and isinstance(request, dict) and _has_generated_question_and_options(request):
            generated.append(request_id)
    return tuple(generated)


def _payload_with_prompt_metadata(
    payload: dict[str, Any],
    *,
    prompt_source: str,
    target_input_ids: tuple[str, ...],
    generated_input_ids: tuple[str, ...],
    fallback_input_ids: tuple[str, ...],
) -> dict[str, Any]:
    updated = dict(payload)
    updated["prompt_source"] = prompt_source
    updated["target_input_ids"] = list(target_input_ids)
    updated["generated_input_ids"] = list(generated_input_ids)
    updated["fallback_input_ids"] = list(fallback_input_ids)
    return updated


def _safe_context(context: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in context.items():
        if not isinstance(key, str) or len(safe) >= 20:
            continue
        if isinstance(value, str):
            safe[key] = value[:240]
        elif isinstance(value, bool | int | float) or value is None:
            safe[key] = value
        elif isinstance(value, list | tuple | set):
            safe[key] = [item for item in value if isinstance(item, str)][:10]
    return safe


def _event_payload(
    result: WorkflowPromptGenerationResult,
    workflow_id: str,
    *,
    task_id: str | None,
    task_template_id: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source": "workflow_prompt_generator",
        "workflow_id": workflow_id,
        "stage": MODEL_STAGE_WORKFLOW_FAST,
        "status": result.status,
        "input_id": result.input_id,
        "target_input_ids": list(result.target_input_ids),
        "generated_input_ids": list(result.generated_input_ids),
        "fallback_input_ids": list(result.fallback_input_ids),
        "option_count": result.option_count,
        "duration_ms": result.duration_ms,
    }
    if task_id:
        payload["task_id"] = task_id
    if task_template_id:
        payload["task_template_id"] = task_template_id
    if result.fallback_reason:
        payload["fallback_reason"] = result.fallback_reason
        payload["reason_code"] = result.fallback_reason
    return {key: value for key, value in payload.items() if value is not None}


def _safe_route_event(route_event: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key in (
        "model_tier",
        "model_stage",
        "provider_route",
        "provider",
        "model",
        "fallback_used",
        "attempt_count",
    ):
        value = route_event.get(key)
        if isinstance(value, str | bool | int):
            safe[key] = value
    return safe


def _input_option_count(payload: dict[str, Any], input_id: str) -> int:
    for item in payload.get("input_requests") or []:
        if isinstance(item, dict) and item.get("id") == input_id and isinstance(item.get("options"), list):
            return len(item["options"])
    return 0


def _workflow_prompt_generator_enabled() -> bool:
    raw = os.getenv(WORKFLOW_PROMPT_GENERATOR_ENABLED_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "off", "no"}


def _workflow_prompt_generator_timeout_seconds() -> float:
    raw = os.getenv(WORKFLOW_PROMPT_GENERATOR_TIMEOUT_SECONDS_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_WORKFLOW_PROMPT_GENERATOR_TIMEOUT_SECONDS
    try:
        timeout = float(raw)
    except ValueError:
        return DEFAULT_WORKFLOW_PROMPT_GENERATOR_TIMEOUT_SECONDS
    return max(0.0, timeout)


def _workflow_prompt_language(language: str | None, user_prompt: str) -> str:
    if language == "vi":
        return "vi"
    normalized = user_prompt.lower()
    if re.search(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", normalized):
        return "vi"
    if any(token in normalized for token in ("mình", "không", "chiến lược", "tạo bot", "theo dõi")):
        return "vi"
    return "en"


def _run_with_timeout(callback, *, timeout_seconds: float) -> dict[str, Any] | None:
    if timeout_seconds <= 0:
        return callback()
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(callback)
    try:
        return future.result(timeout=timeout_seconds)
    finally:
        if not future.done():
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)


def _duration_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))
