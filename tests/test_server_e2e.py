import json
import os
import socket
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn

from strategy_codebot.schemas import write_json
from strategy_codebot.live import LIVE_WORKFLOW_TRACE_PATH
from strategy_codebot.quality import QUALITY_REPORT_PATH
from strategy_codebot.server import ServerAppConfig, create_app
from strategy_codebot.server.database import create_sqlalchemy_repository
from strategy_codebot.server.database import create_sqlite_repository
from strategy_codebot.server.llm_clients import LLMClientEvent
from strategy_codebot.server.security_controls import RateLimitConfig
from strategy_codebot.server.security_controls import RateLimitRule
from strategy_codebot.tool_runtime import RUNTIME_SUMMARY_PATH
from server_helpers import parse_sse
from server_helpers import valid_spec

AUTH_A = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-a"}
AUTH_B = {"X-User-Id": "user-b", "X-Workspace-Id": "workspace-a"}


class FakeRedis:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.values: dict[str, str] = {}
        self.expiry: dict[str, float] = {}

    def ping(self) -> bool:
        self._check_available()
        return True

    def incr(self, key: str) -> int:
        self._check_available()
        value = int(self.values.get(key, "0")) + 1
        self.values[key] = str(value)
        return value

    def expire(self, key: str, seconds: int) -> bool:
        self._check_available()
        self.expiry[key] = time.time() + seconds
        return True

    def get(self, key: str):
        self._check_available()
        return self.values.get(key)

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        self._check_available()
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expiry[key] = time.time() + ex
        return True

    def _check_available(self) -> None:
        if not self.available:
            raise RuntimeError("redis unavailable")


@dataclass
class ScriptedLLMClient:
    events: list[LLMClientEvent]
    model: str = "fake-responses-model"

    def ensure_configured(self) -> None:
        return None

    def stream(self, *, messages: list[dict[str, str]], tools: list[dict]) -> Iterable[LLMClientEvent]:
        return list(self.events)


@contextmanager
def live_client(app) -> Iterator[httpx.Client]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off", ws="none"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(base_url)
        with httpx.Client(base_url=base_url, timeout=20.0) as client:
            yield client
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_live_http_api_full_stack_flow(tmp_path: Path) -> None:
    repository = create_sqlite_repository(f"sqlite+pysqlite:///{tmp_path / 'api.sqlite'}")
    app = create_app(
        config=ServerAppConfig(
            repository=repository,
            artifact_root=tmp_path / "artifacts",
            llm_client=ScriptedLLMClient(
                [
                    LLMClientEvent(type="usage", model="fake-responses-model", input_tokens=12, output_tokens=5),
                    LLMClientEvent(type="message.delta", text="Education only: trading strategies can lose money."),
                ]
            ),
        )
    )

    with live_client(app) as client:
        assert client.get("/health").json()["service"] == "strategy-codebot-api"
        assert client.get("/v1/conversations").status_code == 401

        conversation = client.post("/v1/conversations", headers=AUTH_A, json={"title": "E2E"}).json()
        message = client.post(
            f"/v1/conversations/{conversation['id']}/messages",
            headers=AUTH_A,
            json={"content": "Draft a safe strategy"},
        ).json()
        messages = client.get(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH_A).json()
        assert [item["id"] for item in messages["items"]] == [message["id"]]

        deterministic = client.post(
            f"/v1/conversations/{conversation['id']}/messages?stream=true",
            headers=AUTH_A,
            json={"content": "Stream deterministic progress"},
        )
        deterministic_frames = parse_sse(deterministic.text)
        deterministic_run_id = deterministic_frames[0]["data"]["run_id"]
        assert "run.completed" in [frame["event"] for frame in deterministic_frames]

        replay = parse_sse(client.get(f"/v1/runs/{deterministic_run_id}/events", headers=AUTH_A).text)
        resumed = parse_sse(
            client.get(
                f"/v1/runs/{deterministic_run_id}/events",
                headers={**AUTH_A, "Last-Event-ID": str(replay[1]["data"]["sequence"])},
            ).text
        )
        assert all(frame["data"]["sequence"] > replay[1]["data"]["sequence"] for frame in resumed)

        run = client.post(
            "/v1/runs",
            headers=AUTH_A,
            json={"conversation_id": conversation["id"], "strategy_spec": valid_spec()},
        ).json()
        artifact_kinds = {artifact["kind"] for artifact in run["artifacts"]}
        assert {"pine_file", "validation_report", "review_report", "manual_checklist", "runtime_trace_summary"} <= artifact_kinds
        assert "storage_key" not in json.dumps(run)

        pine_artifact = next(artifact for artifact in run["artifacts"] if artifact["kind"] == "pine_file")
        pine = client.get(f"/v1/artifacts/{pine_artifact['id']}", headers=AUTH_A).json()
        preview = client.get(f"/v1/artifacts/{pine_artifact['id']}/preview?max_bytes=128", headers=AUTH_A).json()
        assert pine["content"].startswith("//@version=6")
        assert preview["language"] == "pine"
        assert "storage_key" not in json.dumps(preview)

        progress_frames = parse_sse(client.get(f"/v1/runs/{run['id']}/progress", headers=AUTH_A).text)
        assert progress_frames[0]["event"] == "progress.snapshot"
        assert progress_frames[-1]["data"]["payload"]["source_event_type"] == "run.completed"

        state = client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A).json()
        assert state["conversation"]["id"] == conversation["id"]
        assert state["latest_run"]["id"] == run["id"]
        assert state["feedback_targets"]["latest_run_id"] == run["id"]

        agent = client.post(
            f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
            headers=AUTH_A,
            json={"content": "Explain risk boundaries"},
        )
        agent_frames = parse_sse(agent.text)
        agent_run_id = agent_frames[0]["data"]["run_id"]
        assert agent_frames[-1]["event"] == "run.completed"
        observability = client.get(f"/v1/runs/{agent_run_id}/observability", headers=AUTH_A).json()
        assert observability["run_id"] == agent_run_id
        assert observability["usage"]["total_tokens"] == 17
        assert "model" in observability["latency_by_stage"]

        feedback_options = client.get("/v1/feedback/options", headers=AUTH_A).json()
        assert [item["value"] for item in feedback_options["ratings"]] == ["up", "down", "neutral"]
        feedback = client.post(
            "/v1/feedback",
            headers=AUTH_A,
            json={
                "conversation_id": conversation["id"],
                "run_id": run["id"],
                "message_id": message["id"],
                "artifact_id": pine_artifact["id"],
                "rating": "neutral",
                "category": "missing_evidence",
                "correction": "Add more manual validation evidence.",
            },
        )
        assert feedback.status_code == 201, feedback.text

        assert client.get(f"/v1/artifacts/{pine_artifact['id']}", headers=AUTH_B).status_code == 404
        assert client.get(f"/v1/runs/{run['id']}/observability", headers=AUTH_B).status_code == 404


def test_live_http_policy_block_suppresses_unsafe_agent_delta(tmp_path: Path) -> None:
    repository = create_sqlite_repository(f"sqlite+pysqlite:///{tmp_path / 'blocked.sqlite'}")
    app = create_app(
        config=ServerAppConfig(
            repository=repository,
            artifact_root=tmp_path / "blocked-artifacts",
            llm_client=ScriptedLLMClient([LLMClientEvent(type="message.delta", text="This has guaranteed profit.")]),
        )
    )

    with live_client(app) as client:
        conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
        response = client.post(
            f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
            headers=AUTH_A,
            json={"content": "Make it profitable"},
        )

        frames = parse_sse(response.text)
        event_types = [frame["event"] for frame in frames]
        assert "policy.blocked" in event_types
        assert frames[-1]["event"] == "run.completed"
        assert frames[-1]["data"]["payload"]["status"] == "blocked"
        streamed_text = " ".join(
            str(frame["data"]["payload"].get("text", ""))
            for frame in frames
            if frame["event"] == "message.delta"
        )
        assert "guaranteed profit" not in streamed_text


def test_live_http_redis_idempotency_and_rate_limit_controls(tmp_path: Path) -> None:
    repository = create_sqlite_repository(f"sqlite+pysqlite:///{tmp_path / 'security.sqlite'}")
    app = create_app(
        config=ServerAppConfig(
            repository=repository,
            artifact_root=tmp_path / "security-artifacts",
            redis_client=FakeRedis(),
        )
    )

    with live_client(app) as client:
        conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
        key = f"idem-{uuid4().hex}"
        first = client.post(
            f"/v1/conversations/{conversation['id']}/messages",
            headers={**AUTH_A, "Idempotency-Key": key},
            json={"content": "same body"},
        )
        replay = client.post(
            f"/v1/conversations/{conversation['id']}/messages",
            headers={**AUTH_A, "Idempotency-Key": key},
            json={"content": "same body"},
        )
        conflict = client.post(
            f"/v1/conversations/{conversation['id']}/messages",
            headers={**AUTH_A, "Idempotency-Key": key},
            json={"content": "different body"},
        )

        assert first.status_code == 201, first.text
        assert replay.status_code == 201, replay.text
        assert replay.json() == first.json()
        assert conflict.status_code == 409

    limited_repository = create_sqlite_repository(f"sqlite+pysqlite:///{tmp_path / 'rate.sqlite'}")
    limited_app = create_app(
        config=ServerAppConfig(
            repository=limited_repository,
            artifact_root=tmp_path / "rate-artifacts",
            redis_client=FakeRedis(),
            rate_limit_config=RateLimitConfig(user_minute=RateLimitRule(1, 60)),
        )
    )
    limited_auth = {"X-User-Id": f"user-{uuid4().hex}", "X-Workspace-Id": "workspace-a"}
    with live_client(limited_app) as client:
        assert client.post("/v1/conversations", headers=limited_auth, json={}).status_code == 201
        limited = client.post("/v1/conversations", headers=limited_auth, json={})
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "rate_limit_exceeded"


def test_live_http_live_generation_mode_smoke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_live_run_strategy(**kwargs):
        assert kwargs["mode"] == "live"
        out_dir = kwargs["out_dir"]
        (out_dir / "pine").mkdir(parents=True, exist_ok=True)
        (out_dir / "pine" / "strategy.pine").write_text("//@version=6\nstrategy('Live Generated')\n", encoding="utf-8")
        (out_dir / "manual-tradingview-checklist.md").write_text("- Verify manually\n", encoding="utf-8")
        write_json(out_dir / "validation-report.json", {"platform": "pine_v6", "status": "pass", "warnings": []})
        write_json(out_dir / "review-report.json", {"decision": "approve", "warnings": []})
        write_json(out_dir / RUNTIME_SUMMARY_PATH, {"status": "pass", "events": []})
        write_json(out_dir / "agent-run.json", {"status": "pass", "output_refs": ["pine/strategy.pine"]})
        write_json(out_dir / "live-metadata.json", {"workflow": "multi-agent", "status": "pass"})
        write_json(out_dir / LIVE_WORKFLOW_TRACE_PATH, {"production_gate": {"status": "pass"}})
        write_json(out_dir / QUALITY_REPORT_PATH, {"status": "pass", "score": 100})
        return {"status": "pass"}

    monkeypatch.setattr("strategy_codebot.server.runner_bridge.run_strategy", fake_live_run_strategy)
    repository = create_sqlite_repository(f"sqlite+pysqlite:///{tmp_path / 'live-generation.sqlite'}")
    app = create_app(
        config=ServerAppConfig(
            repository=repository,
            artifact_root=tmp_path / "live-generation-artifacts",
            llm_client=ScriptedLLMClient([]),
        )
    )

    with live_client(app) as client:
        assert client.get("/ready").status_code == 200
        conversation = client.post("/v1/conversations", headers=AUTH_A, json={"title": "Live generation"}).json()
        run = client.post(
            "/v1/runs",
            headers=AUTH_A,
            json={"conversation_id": conversation["id"], "strategy_spec": valid_spec(), "mode": "live-generation"},
        )

        assert run.status_code == 201, run.text
        payload = run.json()
        artifact_kinds = {artifact["kind"] for artifact in payload["artifacts"]}
        assert {"pine_file", "live_metadata", "live_workflow_trace", "quality_report"} <= artifact_kinds
        assert "storage_key" not in json.dumps(payload)


def test_optional_postgres_live_http_smoke_when_configured(tmp_path: Path) -> None:
    database_url = os.environ.get("STRATEGY_CODEBOT_API_E2E_POSTGRES_URL")
    if not database_url:
        pytest.skip("Set STRATEGY_CODEBOT_API_E2E_POSTGRES_URL to run API Postgres E2E.")
    repository = create_sqlalchemy_repository(database_url, create_schema=True)
    app = create_app(config=ServerAppConfig(repository=repository, artifact_root=tmp_path / "postgres-artifacts"))

    with live_client(app) as client:
        auth = {"X-User-Id": f"user-{uuid4().hex}", "X-Workspace-Id": f"workspace-{uuid4().hex}"}
        conversation = client.post("/v1/conversations", headers=auth, json={"title": "Postgres E2E"})
        assert conversation.status_code == 201, conversation.text
        assert client.get("/v1/conversations", headers=auth).json()["items"][0]["id"] == conversation.json()["id"]


def test_optional_real_redis_live_http_smoke_when_configured(tmp_path: Path) -> None:
    redis_url = os.environ.get("STRATEGY_CODEBOT_API_E2E_REDIS_URL")
    if not redis_url:
        pytest.skip("Set STRATEGY_CODEBOT_API_E2E_REDIS_URL to run API Redis E2E.")
    from redis import Redis

    repository = create_sqlite_repository(f"sqlite+pysqlite:///{tmp_path / 'real-redis.sqlite'}")
    app = create_app(
        config=ServerAppConfig(
            repository=repository,
            artifact_root=tmp_path / "real-redis-artifacts",
            redis_client=Redis.from_url(redis_url, decode_responses=True),
            rate_limit_config=RateLimitConfig(user_minute=RateLimitRule(1, 60)),
        )
    )

    with live_client(app) as client:
        auth = {"X-User-Id": f"user-{uuid4().hex}", "X-Workspace-Id": f"workspace-{uuid4().hex}"}
        assert client.post("/v1/conversations", headers=auth, json={}).status_code == 201
        assert client.post("/v1/conversations", headers=auth, json={}).status_code == 429


def _wait_for_health(base_url: str) -> None:
    deadline = time.time() + 5
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url}/health", timeout=0.5)
            if response.status_code == 200:
                return
        except Exception as exc:  # pragma: no cover - only surfaced on startup failure
            last_error = exc
        time.sleep(0.05)
    raise RuntimeError(f"live API server did not start: {last_error}")
