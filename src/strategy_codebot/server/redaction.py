import re
from typing import Any

REDACTED = "[REDACTED]"

SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\bsk-proj-[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?i)\b(api[_-]?key|secret|token)\s*[:=]\s*['\"]?[^'\"\s,}]{8,}"),
    re.compile(r"/(?:Users|private|var|tmp|etc)/[^\s\"']+"),
)

SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "bearer",
    "messages",
    "prompt",
    "provider_payload",
    "provider_request",
    "provider_response",
    "raw_prompt",
    "secret",
    "storage_key",
    "token",
}


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                redacted[key] = REDACTED
            else:
                redacted[key] = redact_value(item)
        return redacted
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


def redact_text(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted
