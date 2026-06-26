import logging
from typing import Any

from strategy_codebot.server.redaction import redact_text
from strategy_codebot.server.tool_errors import ToolExecutionError

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
    if error_class == "RateLimitError" or _looks_rate_limited(exc):
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


def run_failed_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, ToolExecutionError):
        return exc.payload()
    return provider_run_failed_payload(exc)


def _looks_rate_limited(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return True
    message = str(exc).lower()
    return any(token in message for token in ("quota", "rate limit", "rate-limit", "429"))


def log_provider_exception(exc: Exception, *, run_id: str | None = None, trace_id: str | None = None) -> None:
    _LOGGER.warning(
        "provider_error error_class=%s run_id=%s trace_id=%s message=%s",
        exc.__class__.__name__,
        run_id,
        trace_id,
        redact_text(str(exc)),
    )


def log_run_exception(exc: Exception, *, run_id: str | None = None, trace_id: str | None = None) -> None:
    if isinstance(exc, ToolExecutionError):
        _LOGGER.warning(
            "workflow_error error_class=%s code=%s run_id=%s trace_id=%s message=%s",
            exc.__class__.__name__,
            exc.code,
            run_id,
            trace_id,
            redact_text(str(exc)),
        )
        return
    log_provider_exception(exc, run_id=run_id, trace_id=trace_id)
