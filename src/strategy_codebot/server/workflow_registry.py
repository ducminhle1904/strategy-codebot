from __future__ import annotations

from copy import deepcopy
from typing import Any

from strategy_codebot.server.workflow_registry_contract import WORKFLOW_COMPONENT_KINDS
from strategy_codebot.server.workflow_registry_contract import WORKFLOW_DEFINITIONS
from strategy_codebot.server.workflow_registry_contract import WORKFLOW_SCHEMA_VERSION
from strategy_codebot.server.workflow_tasks import normalize_workflow_tasks
from strategy_codebot.server.workflow_tasks import workflow_task_input_requests
from strategy_codebot.server.workflow_tasks import workflow_task_values

STRATEGY_BOT_WORKFLOW_ID = "strategy_bot_simulation"
STRATEGY_BOT_WORKFLOW = WORKFLOW_DEFINITIONS[STRATEGY_BOT_WORKFLOW_ID]
STRATEGY_BOT_WORKFLOW_STEPS = tuple(STRATEGY_BOT_WORKFLOW["steps"])
STRATEGY_BOT_OPTIONAL_STEPS = frozenset(STRATEGY_BOT_WORKFLOW.get("optional_steps", []))
STRATEGY_BOT_INPUT_FIELDS = tuple(
    section["fields"]
    for section in STRATEGY_BOT_WORKFLOW["sections"]
    if section.get("id") == "strategy_inputs"
)[0]
STRATEGY_BOT_REQUIRED_INPUT_FIELDS = tuple(
    STRATEGY_BOT_WORKFLOW.get("required_input_fields") or STRATEGY_BOT_INPUT_FIELDS
)
STRATEGY_BOT_SETUP_FIELDS = tuple(
    section["fields"]
    for section in STRATEGY_BOT_WORKFLOW["sections"]
    if section.get("id") == "paper_setup"
)[0]


def workflow_catalog_guidance() -> str:
    lines = [
        "Workflow UI registry:",
        "- Models may choose workflow_id and propose next-step state, but backend registry validates payloads.",
        "- Do not invent component names. Allowed component kinds are: "
        + ", ".join(sorted(WORKFLOW_COMPONENT_KINDS))
        + ".",
    ]
    for definition in WORKFLOW_DEFINITIONS.values():
        lines.append(
            f"- {definition['workflow_id']} ({definition['intent']}): "
            + "; ".join(definition.get("model_guidance", []))
        )
    return "\n".join(lines)


def validate_workflow_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    workflow_id = _safe_string(payload.get("workflow_id") or payload.get("workflow"))
    if not workflow_id:
        return None
    definition = WORKFLOW_DEFINITIONS.get(workflow_id)
    if definition is None:
        return None

    step_ids = set(definition["steps"])
    current_step = _safe_string(payload.get("current_step")) or definition["steps"][0]
    if current_step not in step_ids:
        return None

    allowed_fields = set(definition["allowed_fields"])
    completed_steps = [step for step in _string_list(payload.get("completed_steps")) if step in step_ids]
    skipped_steps = _skipped_steps(payload.get("skipped_steps"), definition, completed_steps, current_step)
    step_reasons = _step_reasons(payload.get("step_reasons"), skipped_steps)
    required_fields = [field for field in _string_list(payload.get("required_fields")) if field in allowed_fields]
    missing_fields = [field for field in _string_list(payload.get("missing_fields")) if field in allowed_fields]
    artifact_refs = _string_dict(payload.get("artifact_refs"))
    status_labels = definition["status_labels"]
    raw_evidence_status = _safe_string(payload.get("evidence_status"))
    requested_status_key = _status_key(payload.get("status"))
    status_key = (
        requested_status_key
        if requested_status_key in status_labels
        else raw_evidence_status
        if raw_evidence_status in status_labels
        else definition["default_status"]
    )
    status = status_labels[status_key]
    bot_proposal_id = _safe_string(payload.get("bot_proposal_id")) or artifact_refs.get("bot_proposal_id")
    start_allowed = _start_allowed(
        definition,
        payload.get("start_allowed") is True,
        missing_fields,
        bot_proposal_id,
        current_step,
    )
    tasks = normalize_workflow_tasks(
        workflow_id,
        payload.get("tasks"),
        start_allowed=start_allowed,
        bot_proposal_id=bot_proposal_id,
    )
    input_requests = workflow_task_input_requests(workflow_id, payload.get("input_requests"), tasks)

    return {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "workflow_id": workflow_id,
        "intent": definition["intent"],
        "current_step": current_step,
        "completed_steps": completed_steps,
        "skipped_steps": skipped_steps,
        "step_reasons": step_reasons,
        "blocked_reason": _safe_string(payload.get("blocked_reason")),
        "required_fields": required_fields,
        "missing_fields": missing_fields,
        "artifact_refs": artifact_refs,
        "status": deepcopy(status),
        "actions": _actions(payload.get("actions"), definition, start_allowed, bot_proposal_id),
        "sections": _sections(payload.get("sections"), definition),
        "tasks": tasks,
        "input_requests": input_requests,
        "task_values": workflow_task_values(workflow_id, payload.get("task_values"), input_requests, tasks),
        # Compatibility fields for existing clients and tests.
        "evidence_status": status["key"],
        "bot_proposal_id": bot_proposal_id,
        "start_allowed": start_allowed,
    }


def _actions(
    value: Any,
    definition: dict[str, Any],
    start_allowed: bool,
    bot_proposal_id: str | None,
) -> list[dict[str, Any]]:
    defaults = []
    for action in definition["actions"]:
        normalized = dict(action)
        if normalized.get("kind") == "confirm_start_bot_proposal":
            normalized["enabled"] = start_allowed
            normalized["target_ref"] = bot_proposal_id
            if start_allowed:
                normalized.pop("disabled_reason", None)
        defaults.append(normalized)
    if not isinstance(value, list):
        return defaults
    by_id = {action["id"]: action for action in defaults}
    selected: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        action_id = _safe_string(item.get("id"))
        if not action_id or action_id not in by_id:
            continue
        base = dict(by_id[action_id])
        label = _safe_string(item.get("label"))
        if label:
            base["label"] = label
        base["enabled"] = item.get("enabled") is True and base.get("enabled") is True
        disabled_reason = _safe_string(item.get("disabled_reason"))
        if disabled_reason:
            base["disabled_reason"] = disabled_reason
        selected.append(base)
    return selected or defaults


def _skipped_steps(
    value: Any,
    definition: dict[str, Any],
    completed_steps: list[str],
    current_step: str,
) -> list[str]:
    requested = set(_string_list(value))
    completed = set(completed_steps)
    optional_steps = set(definition.get("optional_steps", []))
    return [
        step
        for step in definition["steps"]
        if step in requested and step in optional_steps and step not in completed and step != current_step
    ]


def _step_reasons(value: Any, skipped_steps: list[str]) -> dict[str, str]:
    reasons = _string_dict(value)
    skipped = set(skipped_steps)
    return {step: reason for step, reason in reasons.items() if step in skipped}


def _start_allowed(
    definition: dict[str, Any],
    requested: bool,
    missing_fields: list[str],
    bot_proposal_id: str | None,
    current_step: str,
) -> bool:
    has_confirm_start_action = any(
        action.get("kind") == "confirm_start_bot_proposal"
        for action in definition.get("actions", [])
        if isinstance(action, dict)
    )
    if not has_confirm_start_action:
        return requested
    final_step = definition.get("steps", [None])[-1]
    return requested and bool(bot_proposal_id) and not missing_fields and current_step == final_step


def _sections(value: Any, definition: dict[str, Any]) -> list[dict[str, Any]]:
    defaults = [dict(section) for section in definition["sections"]]
    if not isinstance(value, list):
        return defaults
    by_id = {section["id"]: section for section in defaults}
    selected: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        section_id = _safe_string(item.get("id"))
        if not section_id or section_id not in by_id:
            continue
        base = by_id[section_id]
        component_kind = _safe_string(item.get("component_kind"))
        if component_kind and component_kind != base.get("component_kind"):
            continue
        if component_kind and component_kind not in WORKFLOW_COMPONENT_KINDS:
            continue
        selected.append(dict(base))
    return selected or defaults


def _status_key(value: Any) -> str | None:
    if isinstance(value, str):
        return _safe_string(value)
    if isinstance(value, dict):
        return _safe_string(value.get("key"))
    return None


def _safe_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _safe_string(item))]


def _string_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    refs: dict[str, str] = {}
    for key, item in value.items():
        text = _safe_string(item)
        if isinstance(key, str) and text:
            refs[key] = text
    return refs
