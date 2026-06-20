import logging
from typing import Any

from strategy_codebot.server.redaction import redact_text

PROVIDER_ERROR_CODE = "provider_unavailable"
PROVIDER_RETRY_AFTER_SECONDS = 30

_LOGGER = logging.getLogger("strategy_codebot.server.provider")


def provider_error_payload() -> dict[str, Any]:
    return {
        "error": {
            "code": PROVIDER_ERROR_CODE,
            "dimension": "provider",
            "retry_after_seconds": PROVIDER_RETRY_AFTER_SECONDS,
        }
    }


def provider_run_failed_payload(exc: Exception) -> dict[str, str | bool]:
    error_class = exc.__class__.__name__
    if error_class in {"ProviderTimeoutError", "APITimeoutError", "Timeout", "ReadTimeout", "ConnectTimeout"}:
        return {
            "code": "provider_timeout",
            "error": error_class,
            "message": "The AI provider took too long to respond.",
            "retryable": True,
        }
    if error_class == "AuthenticationError":
        return {
            "code": "provider_auth_failed",
            "error": error_class,
            "message": "The AI provider rejected the configured API key.",
            "retryable": False,
        }
    if error_class == "RateLimitError":
        return {
            "code": "provider_rate_limited",
            "error": error_class,
            "message": "The AI provider is rate-limited right now.",
            "retryable": True,
        }
    return {
        "code": "provider_unavailable",
        "error": error_class,
        "message": "Provider execution failed",
        "retryable": True,
    }


def log_provider_exception(exc: Exception, *, run_id: str | None = None, trace_id: str | None = None) -> None:
    _LOGGER.warning(
        "provider_error error_class=%s run_id=%s trace_id=%s message=%s",
        exc.__class__.__name__,
        run_id,
        trace_id,
        redact_text(str(exc)),
    )
