from __future__ import annotations

import os

import pytest


EXTERNAL_SERVICE_ENV_VARS = (
    "STRATEGY_CODEBOT_KNOWLEDGE_DATABASE_URL",
    "STRATEGY_CODEBOT_API_E2E_POSTGRES_URL",
    "STRATEGY_CODEBOT_API_E2E_REDIS_URL",
    "STRATEGY_CODEBOT_API_CORS_ORIGINS",
    "STRATEGY_CODEBOT_API_CORS_ORIGIN_REGEX",
)


def _is_truthy(value: str | None) -> bool:
    return value is not None and value.lower() in {"1", "true", "yes", "on"}


@pytest.fixture(autouse=True)
def isolate_external_service_env(monkeypatch: pytest.MonkeyPatch) -> None:
    if _is_truthy(os.getenv("STRATEGY_CODEBOT_TEST_EXTERNAL_SERVICES")):
        return
    for name in EXTERNAL_SERVICE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
