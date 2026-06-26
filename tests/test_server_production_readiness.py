from collections.abc import Iterable
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from strategy_codebot.server import create_app
from strategy_codebot.server.database import create_sqlite_repository
from strategy_codebot.server.llm_clients import LLMClientEvent


class FakeLLMClient:
    model = "fake-ready-model"

    def ensure_configured(self) -> None:
        return None

    def stream(self, *, messages: list[dict[str, str]], tools: list[dict]) -> Iterable[LLMClientEvent]:
        return []


def test_health_remains_shallow_without_provider_config() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_reports_all_backend_dependencies_when_configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("STRATEGY_CODEBOT_KNOWLEDGE_DATABASE_URL", raising=False)
    monkeypatch.delenv("STRATEGY_CODEBOT_LLM_ROUTING", raising=False)
    monkeypatch.setattr(
        "strategy_codebot.server.readiness._pineforge_runner_check",
        lambda: {
            "status": "ok",
            "backtest_default_engine": "pineforge",
            "pineforge_runner_ready": True,
            "pineforge_runner_version": "test",
        },
    )
    client = TestClient(
        create_app(
            repository=create_sqlite_repository(),
            artifact_root=tmp_path,
            llm_client=FakeLLMClient(),
        )
    )

    response = client.get("/ready")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checks"]["repository"]["status"] == "ok"
    assert payload["checks"]["artifact_store"]["status"] == "ok"
    assert payload["checks"]["security_controls"]["status"] == "ok"
    llm_check = payload["checks"]["llm_provider"]
    assert llm_check["status"] == "ok"
    assert llm_check["model"] == "fake-ready-model"
    assert llm_check["model_routing_mode"] == "registry"
    assert "available_gateways" in llm_check
    assert "missing_gateway_envs" in llm_check
    assert payload["checks"]["run_worker"]["mode"] == "inline"
    assert payload["checks"]["knowledge_base"] == {"status": "ok", "configured": False}


def test_ready_fails_closed_for_missing_llm_config_without_secret_details(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path))

    response = client.get("/ready")

    assert response.status_code == 503
    serialized = response.text
    assert "OPENAI_API_KEY" not in serialized
    assert "sk-" not in serialized
    assert response.json()["checks"]["llm_provider"]["status"] == "unavailable"
