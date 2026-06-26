from __future__ import annotations

from datetime import UTC, datetime
import logging
import re
from typing import Any

from strategy_codebot.server.redaction import redact_text, redact_value

SENSITIVE_KEY_PATTERN = re.compile(r"(authorization|cookie|password|secret|token|api[_-]?key)", re.IGNORECASE)
CONTENT_KEY_PATTERN = re.compile(r"(content|message_content|prompt|raw_body|tool_output)", re.IGNORECASE)
MAX_VALUE_LENGTH = 240


def agent_log(
    logger: logging.Logger,
    level: str,
    event: str,
    *,
    component: str,
    **fields: Any,
) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    if not logger.isEnabledFor(log_level):
        return
    safe_fields = {
        key: value
        for key, value in fields.items()
        if key not in {"ts", "lvl", "svc", "component", "event"}
    }
    payload = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "lvl": level,
        "svc": "api",
        "component": component,
        "event": event,
        **safe_fields,
    }
    message = format_logfmt(payload)
    logger.log(log_level, message)


def format_logfmt(fields: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{_normalize_key(key)}={_format_value(key, value)}")
    return " ".join(parts)


def _normalize_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", key).lower()


def _format_value(key: str, value: Any) -> str:
    raw = _single_line(str(_sanitize_value(key, value)))
    escaped = raw.replace("\\", "\\\\").replace('"', '\\"')
    if re.search(r'\s|=|"', escaped):
        return f'"{escaped}"'
    return escaped


def _sanitize_value(key: str, value: Any) -> str | int | float | bool:
    if SENSITIVE_KEY_PATTERN.search(key):
        return "[redacted]"
    if CONTENT_KEY_PATTERN.search(key):
        return f"[redacted len={len(value)}]" if isinstance(value, str) else "[redacted]"
    if isinstance(value, str):
        return _truncate(redact_text(value))
    if isinstance(value, int | float | bool):
        return value
    if isinstance(value, list | tuple | set):
        return _truncate(",".join(str(item) for item in redact_value(list(value))))
    if isinstance(value, dict):
        redacted = redact_value(value)
        if isinstance(redacted, dict):
            return f"keys:{','.join(sorted(str(item_key) for item_key in redacted.keys()))}"
        return _truncate(str(redacted))
    return _truncate(redact_text(str(value)))


def _truncate(value: str) -> str:
    return f"{value[:MAX_VALUE_LENGTH]}..." if len(value) > MAX_VALUE_LENGTH else value


def _single_line(value: str) -> str:
    return re.sub(r"[\r\n\t]+", " ", value)
