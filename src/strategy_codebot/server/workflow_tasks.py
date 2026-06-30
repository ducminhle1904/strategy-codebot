from __future__ import annotations

from typing import Any

from strategy_codebot.server.repository import WorkflowTaskRecord
from strategy_codebot.server.workflow_task_status import WORKFLOW_TASK_RESOLVED_STATUSES
from strategy_codebot.server.workflow_task_status import WORKFLOW_TASK_STATUSES
from strategy_codebot.server.workflow_registry_contract import WORKFLOW_DEFINITIONS
from strategy_codebot.server.workflow_registry_contract import WORKFLOW_TASK_KINDS

MAX_WORKFLOW_PROMPT_OPTIONS = 3


class WorkflowTaskValidationError(ValueError):
    pass


def build_workflow_task_payload(
    workflow_id: str,
    task_template_id: str,
    *,
    status: str | None = None,
    input_request_ids: list[str] | None = None,
    action_ids: list[str] | None = None,
    values: dict[str, Any] | None = None,
    reason: str | None = None,
    input_requests: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    definition = WORKFLOW_DEFINITIONS.get(workflow_id)
    if definition is None:
        return None
    template = _task_template(definition, task_template_id)
    if template is None:
        return None
    allowed_inputs = [
        request_id
        for request_id in list(template.get("input_request_ids") or [])
        if _input_request_template(definition, request_id) is not None
    ]
    allowed_actions = [
        action_id
        for action_id in list(template.get("action_ids") or [])
        if _action(definition, action_id) is not None
    ]
    selected_inputs = [item for item in (input_request_ids or allowed_inputs) if item in allowed_inputs]
    selected_actions = [item for item in (action_ids or allowed_actions) if item in allowed_actions]
    request_overrides = _input_request_overrides(definition, selected_inputs, input_requests)
    normalized_status = status if status in WORKFLOW_TASK_STATUSES else template.get("default_status", "pending_user")
    payload = {
        "task_template_id": template["id"],
        "step_id": template["step_id"],
        "kind": template["kind"],
        "title": template["title"],
        "blocking": template.get("blocking") is True,
        "status": normalized_status,
        "input_request_ids": selected_inputs,
        "action_ids": selected_actions,
        "input_requests": [
            _input_request_payload(definition, request_id, request_overrides.get(request_id))
            for request_id in selected_inputs
        ],
        "actions": [_action_payload(definition, action_id) for action_id in selected_actions],
        "values": _sanitized_known_values(definition, selected_inputs, values or {}),
    }
    if reason:
        payload["reason"] = reason
    return payload


def normalize_workflow_tasks(
    workflow_id: str,
    value: Any,
    *,
    start_allowed: bool,
    bot_proposal_id: str | None,
) -> list[dict[str, Any]]:
    definition = WORKFLOW_DEFINITIONS.get(workflow_id)
    if definition is None or not isinstance(value, list):
        return []
    normalized_tasks: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        task_template_id = _string_value(item.get("task_template_id") or item.get("template_id"))
        record_id = _string_value(item.get("id"))
        if task_template_id is None and record_id is not None and _task_template(definition, record_id):
            task_template_id = record_id
            record_id = None
        if task_template_id is None:
            continue
        template = _task_template(definition, task_template_id)
        if template is None or template.get("kind") not in WORKFLOW_TASK_KINDS:
            continue
        payload = build_workflow_task_payload(
            workflow_id,
            task_template_id,
            status=_string_value(item.get("status")),
            input_request_ids=_string_list(item.get("input_request_ids")) or None,
            action_ids=_string_list(item.get("action_ids")) or None,
            values=item.get("values") if isinstance(item.get("values"), dict) else None,
            reason=_string_value(item.get("reason")),
            input_requests=item.get("input_requests") if isinstance(item.get("input_requests"), list) else None,
        )
        if payload is None:
            continue
        payload["id"] = record_id or task_template_id
        payload["actions"] = [
            _gated_action_payload(action, start_allowed=start_allowed, bot_proposal_id=bot_proposal_id)
            for action in payload.get("actions", [])
            if isinstance(action, dict)
        ]
        normalized_tasks.append(payload)
    return normalized_tasks


def workflow_task_input_requests(
    workflow_id: str,
    value: Any,
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    definition = WORKFLOW_DEFINITIONS.get(workflow_id)
    if definition is None:
        return []
    templates = {
        item["id"]: item
        for item in definition.get("input_request_templates", [])
        if isinstance(item, dict) and item.get("id")
    }
    requested = _string_list(value)
    if not requested and isinstance(value, list):
        requested = [
            request_id
            for item in value
            if isinstance(item, dict)
            for request_id in [_string_value(item.get("id"))]
            if request_id
        ]
    task_request_ids = [
        request_id
        for task in tasks
        for request_id in task.get("input_request_ids", [])
        if isinstance(request_id, str)
    ]
    seen: set[str] = set()
    selected: list[dict[str, Any]] = []
    for request_id in requested or task_request_ids:
        if request_id in seen or request_id not in templates:
            continue
        selected.append(dict(templates[request_id]))
        seen.add(request_id)
    return selected


def workflow_task_values(
    workflow_id: str,
    value: Any,
    input_requests: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    definition = WORKFLOW_DEFINITIONS.get(workflow_id)
    if definition is None:
        return {}
    if not isinstance(value, dict):
        value = {}
    allowed_ids = [request["id"] for request in input_requests if isinstance(request.get("id"), str)]
    if not allowed_ids:
        seen: set[str] = set()
        allowed_ids = []
        for task in tasks:
            for request_id in task.get("input_request_ids", []):
                if isinstance(request_id, str) and request_id not in seen:
                    allowed_ids.append(request_id)
                    seen.add(request_id)
    return _sanitized_known_values(definition, allowed_ids, value)


def workflow_task_state(record: WorkflowTaskRecord) -> dict[str, Any]:
    payload = dict(record.payload_json or {})
    values = dict(payload.get("values") or {})
    if isinstance(record.response_json, dict):
        response_values = record.response_json.get("values")
        if isinstance(response_values, dict):
            values.update(response_values)
    state = {
        "id": record.id,
        "workflow_id": record.workflow_id,
        "task_template_id": record.task_template_id,
        "step_id": record.step_id,
        "kind": record.kind,
        "status": record.status,
        "title": payload.get("title") or record.task_template_id,
        "blocking": payload.get("blocking") is True,
        "input_request_ids": list(payload.get("input_request_ids") or []),
        "action_ids": list(payload.get("action_ids") or []),
        "input_requests": list(payload.get("input_requests") or []),
        "actions": list(payload.get("actions") or []),
        "values": values,
        "response": record.response_json,
        "reason": payload.get("reason"),
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "resolved_at": record.resolved_at.isoformat() if record.resolved_at is not None else None,
    }
    continuation = workflow_task_continuation_state(record)
    if continuation is not None:
        state["continuation"] = continuation
    return state


def workflow_task_continuation_state(record: WorkflowTaskRecord) -> dict[str, Any] | None:
    template = _workflow_task_template_for_record(record)
    if template is None or template.get("resume_on_complete") is not True:
        return None
    if record.status not in {"completed", "approved"}:
        return None
    return {
        "required": True,
        "task_id": record.id,
        "workflow_id": record.workflow_id,
        "task_template_id": record.task_template_id,
        "resume_intent": _string_value(template.get("resume_intent")),
        "reason": "workflow_task_completed",
    }


def workflow_task_resume_context(record: WorkflowTaskRecord, *, language: str = "en") -> str:
    state = workflow_task_state(record)
    values = state.get("values") if isinstance(state.get("values"), dict) else {}
    normalized_values = {
        key: value
        for key, value in values.items()
        if isinstance(key, str) and not _is_empty_value(value)
    }
    value_lines = "\n".join(
        f"- {key}: {value}" for key, value in sorted(normalized_values.items())
    )
    if not value_lines:
        value_lines = "- No completed task values were available."
    if language == "vi":
        return (
            "Tiếp tục workflow Strategy -> Paper Bot Simulation từ HITL task đã hoàn tất.\n"
            "Dùng các giá trị task bền vững dưới đây làm nguồn dữ liệu authoritative; không hỏi lại các field đã có.\n"
            "Hãy draft strategy spec trước. Không tạo hoặc start Bot/runtime. Giữ boundary: paper simulation only, "
            "no broker execution, review-only evidence.\n"
            f"Workflow task: {record.task_template_id}\n"
            f"Task values:\n{value_lines}"
        )
    return (
        "Resume the Strategy -> Paper Bot Simulation workflow from the completed HITL task.\n"
        "Use the durable task values below as authoritative context; do not ask again for fields that are present.\n"
        "Draft the strategy spec next. Do not create or start any Bot/runtime. Keep boundaries: paper simulation only, "
        "no broker execution, review-only evidence.\n"
        f"Workflow task: {record.task_template_id}\n"
        f"Task values:\n{value_lines}"
    )


def validate_workflow_task_response(
    task: WorkflowTaskRecord,
    *,
    values: dict[str, Any] | None = None,
    action_id: str | None = None,
    partial: bool = False,
) -> dict[str, Any]:
    definition = WORKFLOW_DEFINITIONS.get(task.workflow_id)
    if definition is None:
        raise WorkflowTaskValidationError("Unknown workflow")
    template = _task_template(definition, task.task_template_id)
    if template is None:
        raise WorkflowTaskValidationError("Unknown task template")
    payload = task.payload_json or {}
    allowed_inputs = [
        request_id
        for request_id in list(payload.get("input_request_ids") or template.get("input_request_ids") or [])
        if _input_request_template(definition, request_id) is not None
    ]
    allowed_actions = [
        candidate
        for candidate in list(payload.get("action_ids") or template.get("action_ids") or [])
        if _action(definition, candidate) is not None
    ]
    if action_id is not None:
        if task.status in WORKFLOW_TASK_RESOLVED_STATUSES or task.status == "blocked":
            raise WorkflowTaskValidationError("Task action is not available in the current status")
        action_payload = _payload_action(payload, action_id)
        if action_id not in allowed_actions or action_payload is None:
            raise WorkflowTaskValidationError("Action is not available for this task")
        if action_payload.get("enabled") is not True:
            raise WorkflowTaskValidationError("Action is disabled for this task")
    sanitized_values = _validate_values(
        definition,
        allowed_inputs,
        values or {},
        payload=payload,
        require_required=not partial,
    )
    if partial:
        merged_values = _existing_task_values(task)
        merged_values.update(sanitized_values)
        sanitized_values = merged_values
    return {
        "workflow_id": task.workflow_id,
        "task_template_id": task.task_template_id,
        "action_id": action_id,
        "values": sanitized_values,
    }


def workflow_task_response_status(
    task: WorkflowTaskRecord,
    response_json: dict[str, Any],
    *,
    requested_status: str,
) -> str:
    if requested_status in {"rejected", "cancelled", "blocked"}:
        return requested_status
    if _workflow_task_required_values_complete(task, response_json.get("values")):
        return "completed" if requested_status == "completed" else requested_status
    return "pending_user"


def _validate_values(
    definition: dict[str, Any],
    allowed_input_ids: list[str],
    values: dict[str, Any],
    *,
    payload: dict[str, Any] | None = None,
    require_required: bool = True,
) -> dict[str, Any]:
    unknown = sorted(set(values) - set(allowed_input_ids))
    if unknown:
        raise WorkflowTaskValidationError(f"Unknown input request: {unknown[0]}")
    sanitized: dict[str, Any] = {}
    for request_id in allowed_input_ids:
        template = _input_request_for_validation(definition, payload, request_id)
        if template is None:
            continue
        if request_id not in values:
            if require_required and template.get("required") is True:
                raise WorkflowTaskValidationError(f"{request_id} is required")
            continue
        value = _sanitize_input_value(definition, template, values[request_id])
        if _is_empty_value(value):
            if template.get("required") is True:
                raise WorkflowTaskValidationError(f"{request_id} is required")
            continue
        sanitized[request_id] = value
    return sanitized


def _sanitized_known_values(
    definition: dict[str, Any],
    input_request_ids: list[str],
    values: dict[str, Any],
) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for request_id in input_request_ids:
        template = _input_request_template(definition, request_id)
        if template is None or request_id not in values:
            continue
        try:
            value = _sanitize_input_value(definition, template, values[request_id])
        except WorkflowTaskValidationError:
            continue
        if not _is_empty_value(value):
            sanitized[request_id] = value
    return sanitized


def _sanitize_input_value(definition: dict[str, Any], template: dict[str, Any], value: Any) -> Any:
    kind = template.get("kind")
    if kind == "boolean":
        if not isinstance(value, bool):
            raise WorkflowTaskValidationError(f"{template['id']} must be boolean")
        return value
    if kind == "multi_select":
        if not isinstance(value, list):
            raise WorkflowTaskValidationError(f"{template['id']} must be a list")
        return [_sanitize_option_value(definition, template, item) for item in value]
    if kind in {"single_select", "select_or_text"}:
        normalized = _string_value(value)
        if normalized is None:
            return None
        option_value = _matching_option_value(definition, template, normalized)
        if option_value is not None:
            return option_value
        if kind == "select_or_text" and template.get("allow_custom") is True:
            return normalized
        raise WorkflowTaskValidationError(f"{template['id']} has an invalid option")
    if kind in {"text", "textarea"}:
        return _string_value(value)
    raise WorkflowTaskValidationError(f"{template['id']} has an unsupported kind")


def _sanitize_option_value(definition: dict[str, Any], template: dict[str, Any], value: Any) -> str:
    normalized = _string_value(value)
    if normalized is None:
        raise WorkflowTaskValidationError(f"{template['id']} has an invalid option")
    option_value = _matching_option_value(definition, template, normalized)
    if option_value is None:
        raise WorkflowTaskValidationError(f"{template['id']} has an invalid option")
    return option_value


def _matching_option_value(definition: dict[str, Any], template: dict[str, Any], value: str) -> str | None:
    for option in template.get("options") or []:
        if not isinstance(option, dict) or option.get("disabled") is True:
            continue
        if option.get("id") == value or option.get("value") == value:
            return option.get("value")
    option_set_id = template.get("option_set_id")
    if not option_set_id:
        return None
    options = definition.get("option_sets", {}).get(option_set_id, [])
    for option in options:
        if option.get("id") == value or option.get("value") == value:
            return option.get("value")
    return None


def _input_request_overrides(
    definition: dict[str, Any],
    selected_inputs: list[str],
    input_requests: list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    del definition
    if not isinstance(input_requests, list):
        return {}
    allowed = set(selected_inputs)
    overrides: dict[str, dict[str, Any]] = {}
    for item in input_requests:
        if not isinstance(item, dict):
            continue
        request_id = _string_value(item.get("id"))
        if request_id is None or request_id not in allowed:
            continue
        overrides[request_id] = item
    return overrides


def _effective_input_options(
    definition: dict[str, Any],
    template: dict[str, Any],
    override: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    option_sources: list[Any] = []
    if override is not None:
        option_sources.append(override.get("options"))
    option_sources.append(template.get("options"))
    option_set_id = template.get("option_set_id")
    if option_set_id:
        option_sources.append(definition.get("option_sets", {}).get(option_set_id))
    for source in option_sources:
        options = _sanitize_prompt_options(source)
        if options:
            return options
    return []


def _sanitize_prompt_options(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    seen_ids: set[str] = set()
    seen_values: set[str] = set()
    options: list[dict[str, Any]] = []
    for item in value:
        if len(options) >= MAX_WORKFLOW_PROMPT_OPTIONS:
            break
        if not isinstance(item, dict):
            continue
        option_id = _string_value(item.get("id"))
        option_value = _string_value(item.get("value"))
        label = _string_value(item.get("label"))
        if option_id is None or option_value is None or label is None:
            continue
        if option_id in seen_ids or option_value in seen_values:
            continue
        if item.get("disabled") is True:
            continue
        option = {"id": option_id, "value": option_value, "label": label}
        description = _string_value(item.get("description"))
        if description:
            option["description"] = description
        tone = _string_value(item.get("tone"))
        if tone in {"neutral", "success", "warning", "danger"}:
            option["tone"] = tone
        options.append(option)
        seen_ids.add(option_id)
        seen_values.add(option_value)
    return options


def _input_request_for_validation(
    definition: dict[str, Any],
    payload: dict[str, Any] | None,
    request_id: str,
) -> dict[str, Any] | None:
    template = _input_request_template(definition, request_id)
    if template is None:
        return None
    merged = dict(template)
    payload_request = _payload_input_request(payload or {}, request_id)
    if payload_request is not None:
        options = _sanitize_prompt_options(payload_request.get("options"))
        if options:
            merged["options"] = options
        if template.get("allow_custom") is True:
            merged["allow_custom"] = True
    return merged


def _existing_task_values(task: WorkflowTaskRecord) -> dict[str, Any]:
    payload = task.payload_json or {}
    values = dict(payload.get("values") or {})
    if isinstance(task.response_json, dict):
        response_values = task.response_json.get("values")
        if isinstance(response_values, dict):
            values.update(response_values)
    return values


def _workflow_task_required_values_complete(task: WorkflowTaskRecord, values: Any) -> bool:
    definition = WORKFLOW_DEFINITIONS.get(task.workflow_id)
    if definition is None:
        return False
    payload = task.payload_json or {}
    template = _task_template(definition, task.task_template_id)
    if template is None:
        return False
    allowed_inputs = [
        request_id
        for request_id in list(payload.get("input_request_ids") or template.get("input_request_ids") or [])
        if _input_request_template(definition, request_id) is not None
    ]
    if not isinstance(values, dict):
        values = {}
    for request_id in allowed_inputs:
        request = _input_request_for_validation(definition, payload, request_id)
        if request is None or request.get("required") is not True:
            continue
        if request_id not in values:
            return False
        try:
            value = _sanitize_input_value(definition, request, values[request_id])
        except WorkflowTaskValidationError:
            return False
        if _is_empty_value(value):
            return False
    return True


def _input_request_payload(
    definition: dict[str, Any],
    request_id: str,
    override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    template = _input_request_template(definition, request_id)
    if template is None:
        return {"id": request_id}
    payload = dict(template)
    options = _effective_input_options(definition, template, override)
    if options:
        payload["options"] = options
    for key in ("question", "placeholder", "helper_text"):
        text = _string_value((override or {}).get(key)) or _string_value(template.get(key))
        if text:
            payload[key] = text
    if payload.get("allow_custom") is True:
        custom_label = _string_value((override or {}).get("custom_option_label")) or _string_value(
            template.get("custom_option_label")
        )
        if custom_label:
            payload["custom_option_label"] = custom_label
    recommended_option_id = _string_value((override or {}).get("recommended_option_id")) or _string_value(
        template.get("recommended_option_id")
    )
    option_ids = {option.get("id") for option in options if isinstance(option.get("id"), str)}
    if recommended_option_id in option_ids:
        payload["recommended_option_id"] = recommended_option_id
    elif options:
        payload["recommended_option_id"] = options[0]["id"]
    else:
        payload.pop("recommended_option_id", None)
    return payload


def _action_payload(definition: dict[str, Any], action_id: str) -> dict[str, Any]:
    action = _action(definition, action_id)
    return dict(action) if action is not None else {"id": action_id}


def _gated_action_payload(
    action: dict[str, Any],
    *,
    start_allowed: bool,
    bot_proposal_id: str | None,
) -> dict[str, Any]:
    normalized = dict(action)
    if normalized.get("kind") == "confirm_start_bot_proposal":
        normalized["enabled"] = start_allowed
        normalized["target_ref"] = bot_proposal_id
        if start_allowed:
            normalized.pop("disabled_reason", None)
    return normalized


def _task_template(definition: dict[str, Any], task_template_id: str) -> dict[str, Any] | None:
    for template in definition.get("task_templates", []):
        if template.get("id") == task_template_id:
            return template
    return None


def _workflow_task_template_for_record(record: WorkflowTaskRecord) -> dict[str, Any] | None:
    definition = WORKFLOW_DEFINITIONS.get(record.workflow_id)
    if definition is None:
        return None
    return _task_template(definition, record.task_template_id)


def _input_request_template(definition: dict[str, Any], request_id: str) -> dict[str, Any] | None:
    for template in definition.get("input_request_templates", []):
        if template.get("id") == request_id:
            return template
    return None


def _action(definition: dict[str, Any], action_id: str) -> dict[str, Any] | None:
    for action in definition.get("actions", []):
        if action.get("id") == action_id:
            return action
    return None


def _payload_action(payload: dict[str, Any], action_id: str) -> dict[str, Any] | None:
    for action in payload.get("actions") or []:
        if isinstance(action, dict) and action.get("id") == action_id:
            return action
    return None


def _payload_input_request(payload: dict[str, Any], request_id: str) -> dict[str, Any] | None:
    for request in payload.get("input_requests") or []:
        if isinstance(request, dict) and request.get("id") == request_id:
            return request
    return None


def _string_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    selected: list[str] = []
    for item in value:
        normalized = _string_value(item)
        if normalized is not None:
            selected.append(normalized)
    return selected


def _is_empty_value(value: Any) -> bool:
    return value is None or value == "" or value == []
