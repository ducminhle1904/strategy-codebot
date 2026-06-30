from __future__ import annotations

from typing import Any

from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.redaction import redact_text
from strategy_codebot.server.repository import AssistantRunRecord
from strategy_codebot.server.repository import ConversationRepository
from strategy_codebot.server.repository import RunEventRecord

MODEL_AUDIT_SCHEMA_VERSION = 1

MODEL_ACTION_PROPOSED = "model_action.proposed"
MODEL_ACTION_VALIDATED = "model_action.validated"
MODEL_ACTION_REJECTED = "model_action.rejected"
MODEL_ACTION_EXECUTED = "model_action.executed"
WORKFLOW_GATE_REQUIRED = "workflow.gate.required"
WORKFLOW_GATE_CONFIRMED = "workflow.gate.confirmed"
WORKFLOW_GATE_REJECTED = "workflow.gate.rejected"

MODEL_AUDIT_EVENT_TYPES = (
    MODEL_ACTION_PROPOSED,
    MODEL_ACTION_VALIDATED,
    MODEL_ACTION_REJECTED,
    MODEL_ACTION_EXECUTED,
    WORKFLOW_GATE_REQUIRED,
    WORKFLOW_GATE_CONFIRMED,
    WORKFLOW_GATE_REJECTED,
)

_STRING_FIELDS = {
    "actor",
    "source",
    "status",
    "reason_code",
    "risk_level",
    "intent_id",
    "workflow_id",
    "task_id",
    "task_template_id",
    "step_id",
    "tool_id",
    "proposal_id",
    "action_id",
    "decision",
    "policy_intent",
    "target",
    "polarity",
    "model",
    "output_status",
    "artifact_kind",
    "runtime_id",
    "error_class",
}
_NUMERIC_FIELDS = {"confidence", "duration_ms", "input_tokens", "output_tokens", "row_count"}
_LIST_FIELDS = {"missing_fields", "artifact_refs", "suggested_actions", "dropped_fields"}
_DICT_SUMMARY_FIELDS = {"safe_args_summary", "dropped_counts", "workflow_summary"}
_SENSITIVE_KEY_TERMS = (
    "authorization",
    "broker_connection",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "account_id",
    "risk_policy_id",
)
_CONTENT_KEY_TERMS = ("content", "message", "prompt", "raw_body", "tool_output", "code", "pine")
_SAFE_ID_KEYS = {
    "artifact_id",
    "bot_proposal_id",
    "candidate_id",
    "conversation_id",
    "proposal_id",
    "run_id",
    "runtime_id",
    "strategy_id",
    "task_id",
    "tool_id",
    "workflow_id",
}


def append_model_audit_event(
    repository: ConversationRepository,
    auth: AuthContext,
    run: AssistantRunRecord,
    event_name: str,
    payload: dict[str, Any],
) -> RunEventRecord | None:
    if event_name not in MODEL_AUDIT_EVENT_TYPES:
        raise ValueError(f"Unknown model audit event: {event_name}")
    return repository.append_run_event(auth, run.id, event_name, model_audit_payload(run, payload))


def model_audit_payload(run: AssistantRunRecord, payload: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "schema_version": MODEL_AUDIT_SCHEMA_VERSION,
        "conversation_id": run.conversation_id,
        "run_id": run.id,
        "request_id": run.request_id,
        "trace_id": run.trace_id,
    }
    for key in _STRING_FIELDS:
        value = _safe_string(payload.get(key))
        if value is not None:
            normalized[key] = value
    for key in _NUMERIC_FIELDS:
        value = payload.get(key)
        if isinstance(value, int | float):
            normalized[key] = round(value, 3) if isinstance(value, float) else value
    for key in _LIST_FIELDS:
        values = _safe_string_list(payload.get(key))
        if values:
            normalized[key] = values
    for key in _DICT_SUMMARY_FIELDS:
        value = payload.get(key)
        if isinstance(value, dict):
            normalized[key] = safe_args_summary(value) if key == "safe_args_summary" else _safe_summary_dict(value)
    return normalized


def _safe_summary_dict(value: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or _is_sensitive_or_content_key(key):
            continue
        safe_key = key[:80]
        if isinstance(item, bool):
            summary[safe_key] = item
        elif isinstance(item, int | float) and not isinstance(item, bool):
            summary[safe_key] = round(item, 3) if isinstance(item, float) else item
        elif isinstance(item, str):
            summary[safe_key] = _safe_string(item)
        elif isinstance(item, list | tuple | set):
            summary[safe_key] = {"count": len(item)}
        elif isinstance(item, dict):
            nested_keys = [
                nested_key[:80]
                for nested_key in item
                if isinstance(nested_key, str) and not _is_sensitive_or_content_key(nested_key)
            ][:20]
            summary[safe_key] = {"key_count": len(nested_keys), "keys": nested_keys}
        elif item is None:
            summary[safe_key] = None
        else:
            summary[safe_key] = type(item).__name__
    return summary


def safe_args_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"type": type(value).__name__}
    keys = sorted(
        str(key)
        for key in value.keys()
        if not _is_sensitive_or_content_key(str(key))
    )
    summary: dict[str, Any] = {
        "key_count": len(keys),
        "keys": keys[:50],
    }
    safe_ids: dict[str, str] = {}
    value_types: dict[str, str] = {}
    list_counts: dict[str, int] = {}
    object_keys: dict[str, list[str]] = {}
    for raw_key, item in value.items():
        key = str(raw_key)
        key_lower = key.lower()
        if _is_sensitive_or_content_key(key_lower):
            continue
        if key_lower in _SAFE_ID_KEYS and isinstance(item, str):
            safe_ids[key] = redact_text(item)[:160]
        if isinstance(item, str):
            value_types[key] = "string"
        elif isinstance(item, bool):
            value_types[key] = "boolean"
        elif isinstance(item, int | float):
            value_types[key] = "number"
        elif isinstance(item, list):
            value_types[key] = "list"
            list_counts[key] = len(item)
        elif isinstance(item, dict):
            value_types[key] = "object"
            object_keys[key] = sorted(
                str(child_key)
                for child_key in item.keys()
                if not _is_sensitive_or_content_key(str(child_key))
            )[:30]
        elif item is None:
            value_types[key] = "null"
        else:
            value_types[key] = type(item).__name__
    if safe_ids:
        summary["ids"] = safe_ids
    if value_types:
        summary["value_types"] = value_types
    if list_counts:
        summary["list_counts"] = list_counts
    if object_keys:
        summary["object_keys"] = object_keys
    return summary


def _safe_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = redact_text(value.strip())
    return stripped[:240] if stripped else None


def _safe_string_list(value: Any) -> list[str]:
    if not isinstance(value, list | tuple | set):
        return []
    selected: list[str] = []
    for item in value:
        text = _safe_string(item)
        if text is not None:
            selected.append(text)
    return selected[:50]


def _is_sensitive_or_content_key(key: str) -> bool:
    return any(term in key for term in _SENSITIVE_KEY_TERMS) or any(
        term in key for term in _CONTENT_KEY_TERMS
    )
