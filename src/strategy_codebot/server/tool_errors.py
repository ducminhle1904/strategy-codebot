from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from strategy_codebot.server.redaction import redact_text


@dataclass
class ToolExecutionError(Exception):
    code: str
    message: str
    dimension: str = "workflow"
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "dimension": self.dimension,
            "error": self.__class__.__name__,
            "message": redact_text(self.message),
            "retryable": self.retryable,
        }
        payload.update(self.details)
        return payload


def tool_failure_fields(exc: Exception) -> dict[str, Any]:
    if not isinstance(exc, ToolExecutionError):
        return {}
    return exc.payload()
