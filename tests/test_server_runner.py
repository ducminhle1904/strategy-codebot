import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from strategy_codebot.schemas import write_json
from strategy_codebot.server import create_app
from strategy_codebot.server.database import create_sqlite_repository
from strategy_codebot.server.run_modes import RUN_MODE_BACKTEST_PREVIEW
from strategy_codebot.server.run_modes import RUN_MODE_DRY_RUN
from strategy_codebot.server.run_modes import RUN_MODES
from strategy_codebot.server.schemas import RunCreate
from strategy_codebot.live import LIVE_WORKFLOW_TRACE_PATH
from strategy_codebot.quality import QUALITY_REPORT_PATH
from strategy_codebot.tool_runtime import RUNTIME_SUMMARY_PATH
from server_helpers import parse_sse
from server_helpers import valid_spec

AUTH_A = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-a"}
AUTH_B = {"X-User-Id": "user-b", "X-Workspace-Id": "workspace-a"}
AUTH_OTHER_WORKSPACE = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-b"}
BACKTEST_CONFIG = {
    "engine": "backtest-kit",
    "symbol": "BTCUSDT",
    "timeframe": "15m",
    "start": "2024-01-01T00:00:00Z",
    "end": "2024-02-01T00:00:00Z",
    "initial_capital": 10000,
    "fee_bps": 10,
    "slippage_bps": 5,
    "data_source": "public-readonly-cache",
}
BACKTEST_STRATEGY_LOGIC = {
    "logic_version": "backtest-strategy-logic.v1",
    "position": "long",
    "indicators": {
        "fast_ema": {"kind": "ema", "period": 3, "source": "close"},
        "slow_ema": {"kind": "ema", "period": 5, "source": "close"},
        "rsi": {"kind": "rsi", "period": 14, "source": "close"},
    },
    "entry": {
        "all": [
            {"type": "crossover", "left": "fast_ema", "right": "slow_ema"},
            {"type": "greater_than", "left": "rsi", "right": 45},
        ]
    },
    "exit": {"take_profit_pct": 4, "stop_loss_pct": 2, "max_holding_minutes": 1440},
    "risk": {"cost": 1000},
}


def test_run_create_defaults_web_search_to_auto() -> None:
    payload = RunCreate(conversation_id="conv_1", strategy_spec=valid_spec())

    assert payload.web_search == "auto"
    assert payload.mode == RUN_MODE_DRY_RUN


def test_run_mode_constants_match_capability_response(tmp_path: Path) -> None:
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path))

    response = client.get("/v1/me", headers=AUTH_A)

    assert response.status_code == 200
    assert response.json()["capability"]["allowed_run_modes"] == list(RUN_MODES)


def test_backtest_preview_requires_backtest_config() -> None:
    try:
        RunCreate(conversation_id="conv_1", strategy_spec=valid_spec(), mode=RUN_MODE_BACKTEST_PREVIEW)
    except ValueError as exc:
        assert "backtest_config" in str(exc)
    else:
        raise AssertionError("backtest-preview without config should fail validation")


def test_backtest_preview_run_is_queued_with_job_and_event(tmp_path: Path) -> None:
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    response = client.post(
        "/v1/runs",
        headers=AUTH_A,
        json={
            "conversation_id": conversation["id"],
            "strategy_spec": valid_spec(),
            "strategy_logic": BACKTEST_STRATEGY_LOGIC,
            "mode": "backtest-preview",
            "backtest_config": BACKTEST_CONFIG,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["mode"] == "backtest-preview"
    stats = repository.run_queue_stats(job_type="backtest-preview")
    assert stats.queued == 1
    events = parse_sse(client.get(f"/v1/runs/{payload['id']}/events", headers=AUTH_A).text)
    assert events[-1]["event"] == "backtest.queued"
    job = repository.claim_run_job(job_type="backtest-preview", worker_id="worker-test")
    assert job is not None
    assert job.payload_json["backtest_config"]["symbol"] == "BTCUSDT"
    assert job.payload_json["strategy_logic"] == BACKTEST_STRATEGY_LOGIC
    assert job.payload_json["limits"]["workspace_active_limit"] == 2
    assert job.payload_json["limits"]["max_variants"] == 6


def test_cancel_backtest_preview_cancels_queued_job(tmp_path: Path) -> None:
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    run = client.post(
        "/v1/runs",
        headers=AUTH_A,
        json={
            "conversation_id": conversation["id"],
            "strategy_spec": valid_spec(),
            "mode": "backtest-preview",
            "backtest_config": BACKTEST_CONFIG,
        },
    ).json()

    response = client.post(f"/v1/runs/{run['id']}/cancel", headers=AUTH_A)

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert repository.run_queue_stats(job_type="backtest-preview").queued == 0
    assert repository.claim_run_job(job_type="backtest-preview", worker_id="worker-test") is None


def test_retry_backtest_preview_is_rejected_until_payload_replay_exists(tmp_path: Path) -> None:
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    run = client.post(
        "/v1/runs",
        headers=AUTH_A,
        json={
            "conversation_id": conversation["id"],
            "strategy_spec": valid_spec(),
            "mode": "backtest-preview",
            "backtest_config": BACKTEST_CONFIG,
        },
    ).json()

    response = client.post(f"/v1/runs/{run['id']}/retry", headers=AUTH_A)

    assert response.status_code == 409
    assert "backtest-preview" in response.json()["detail"]


def test_runner_run_requires_auth(tmp_path: Path) -> None:
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path))

    response = client.post("/v1/runs", json={"conversation_id": "conv_missing", "strategy_spec": valid_spec()})

    assert response.status_code == 401


def test_runner_run_returns_404_for_cross_tenant_conversation(tmp_path: Path) -> None:
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    cross_user = client.post(
        "/v1/runs",
        headers=AUTH_B,
        json={"conversation_id": conversation["id"], "strategy_spec": valid_spec()},
    )
    cross_workspace = client.post(
        "/v1/runs",
        headers=AUTH_OTHER_WORKSPACE,
        json={"conversation_id": conversation["id"], "strategy_spec": valid_spec()},
    )

    assert cross_user.status_code == 404
    assert cross_workspace.status_code == 404


def test_invalid_strategy_spec_returns_422_without_creating_artifacts(tmp_path: Path) -> None:
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    response = client.post(
        "/v1/runs",
        headers=AUTH_A,
        json={"conversation_id": conversation["id"], "strategy_spec": {"target_platform": "pine_v6"}},
    )

    assert response.status_code == 422
    assert not (tmp_path / "runs").exists()


def test_valid_pine_dry_run_returns_artifact_ids_and_content(tmp_path: Path) -> None:
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={"title": "Runner"}).json()

    response = client.post(
        "/v1/runs",
        headers=AUTH_A,
        json={"conversation_id": conversation["id"], "strategy_spec": valid_spec()},
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert re.fullmatch(r"run_[0-9a-f]{32}", payload["id"])
    assert payload["status"] == "completed"
    artifact_kinds = {artifact["kind"] for artifact in payload["artifacts"]}
    assert artifact_kinds == {
        "pine_file",
        "validation_report",
        "review_report",
        "manual_checklist",
        "runtime_trace_summary",
    }
    assert {artifact["visibility"] for artifact in payload["artifacts"]} == {"user", "internal"}
    pine_artifact = next(artifact for artifact in payload["artifacts"] if artifact["kind"] == "pine_file")
    assert pine_artifact["visibility"] == "user"
    assert pine_artifact["category"] == "code"
    trace_artifact = next(artifact for artifact in payload["artifacts"] if artifact["kind"] == "runtime_trace_summary")
    assert trace_artifact["visibility"] == "internal"
    assert trace_artifact["category"] == "trace"
    serialized = json.dumps(payload)
    assert "storage_key" not in serialized
    assert "out_dir" not in serialized
    assert "api-artifacts" not in serialized

    pine = client.get(f"/v1/artifacts/{pine_artifact['id']}", headers=AUTH_A)
    assert pine.status_code == 200, pine.text
    assert pine.json()["visibility"] == "user"
    assert pine.json()["category"] == "code"
    assert pine.json()["content"].startswith("//@version=6")
    assert "storage_key" not in json.dumps(pine.json())

    trace_preview = client.get(f"/v1/artifacts/{trace_artifact['id']}/preview", headers=AUTH_A)
    assert trace_preview.status_code == 200, trace_preview.text
    assert trace_preview.json()["visibility"] == "internal"
    assert trace_preview.json()["category"] == "trace"

    validation_artifact = next(artifact for artifact in payload["artifacts"] if artifact["kind"] == "validation_report")
    validation = client.get(f"/v1/artifacts/{validation_artifact['id']}", headers=AUTH_A).json()
    assert validation["content"]["platform"] == "pine_v6"
    assert "status" in validation["content"]


def test_artifact_access_is_object_authorized(tmp_path: Path) -> None:
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    run = client.post(
        "/v1/runs",
        headers=AUTH_A,
        json={"conversation_id": conversation["id"], "strategy_spec": valid_spec()},
    ).json()
    artifact_id = run["artifacts"][0]["id"]

    assert client.get(f"/v1/artifacts/{artifact_id}", headers=AUTH_A).status_code == 200
    assert client.get(f"/v1/artifacts/{artifact_id}", headers=AUTH_B).status_code == 404
    assert client.get(f"/v1/artifacts/{artifact_id}", headers=AUTH_OTHER_WORKSPACE).status_code == 404


def test_runner_run_persists_lifecycle_events(tmp_path: Path) -> None:
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    run = client.post(
        "/v1/runs",
        headers=AUTH_A,
        json={"conversation_id": conversation["id"], "strategy_spec": valid_spec()},
    ).json()

    replay = client.get(f"/v1/runs/{run['id']}/events", headers=AUTH_A)

    assert replay.status_code == 200, replay.text
    event_types = [frame["event"] for frame in parse_sse(replay.text)]
    assert event_types[0] == "tool.started"
    assert "tool.completed" in event_types
    assert event_types.count("artifact.created") == 5
    assert "validation.completed" in event_types
    assert "review.completed" in event_types
    assert event_types[-1] == "run.completed"


def test_live_generation_mode_returns_live_artifacts_without_paths(monkeypatch, tmp_path: Path) -> None:
    def fake_live_run_strategy(**kwargs):
        assert kwargs["mode"] == "live"
        assert kwargs["prompt"]
        assert "broker" in kwargs["prompt"]
        out_dir = kwargs["out_dir"]
        (out_dir / "pine").mkdir(parents=True, exist_ok=True)
        (out_dir / "pine" / "strategy.pine").write_text("//@version=6\nstrategy('Generated')\n", encoding="utf-8")
        (out_dir / "manual-tradingview-checklist.md").write_text("- Import manually\n", encoding="utf-8")
        write_json(out_dir / "validation-report.json", {"platform": "pine_v6", "status": "pass", "warnings": []})
        write_json(out_dir / "review-report.json", {"decision": "approve", "warnings": []})
        write_json(out_dir / RUNTIME_SUMMARY_PATH, {"status": "pass", "events": []})
        write_json(out_dir / "agent-run.json", {"status": "pass", "output_refs": ["pine/strategy.pine"]})
        write_json(out_dir / "live-metadata.json", {"workflow": "multi-agent", "status": "pass"})
        write_json(out_dir / LIVE_WORKFLOW_TRACE_PATH, {"production_gate": {"status": "pass"}})
        write_json(out_dir / QUALITY_REPORT_PATH, {"status": "pass", "score": 100})
        return {"status": "pass"}

    monkeypatch.setattr("strategy_codebot.server.runner_bridge.run_strategy", fake_live_run_strategy)
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    response = client.post(
        "/v1/runs",
        headers=AUTH_A,
        json={"conversation_id": conversation["id"], "strategy_spec": valid_spec(), "mode": "live-generation"},
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    artifact_kinds = {artifact["kind"] for artifact in payload["artifacts"]}
    assert {
        "pine_file",
        "validation_report",
        "review_report",
        "manual_checklist",
        "runtime_trace_summary",
        "agent_run",
        "live_metadata",
        "live_workflow_trace",
        "quality_report",
    } <= artifact_kinds
    serialized = json.dumps(payload)
    assert "storage_key" not in serialized
    assert str(tmp_path) not in serialized

    events = parse_sse(client.get(f"/v1/runs/{payload['id']}/events", headers=AUTH_A).text)
    assert events[0]["data"]["payload"]["mode"] == "live-generation"
    assert events[-1]["event"] == "run.completed"


def test_runner_exception_marks_run_failed_and_records_event(monkeypatch, tmp_path: Path) -> None:
    def broken_run_strategy(**_kwargs):
        raise RuntimeError("runner unavailable")

    monkeypatch.setattr("strategy_codebot.server.runner_bridge.run_strategy", broken_run_strategy)
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    response = client.post(
        "/v1/runs",
        headers=AUTH_A,
        json={"conversation_id": conversation["id"], "strategy_spec": valid_spec()},
    )

    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "failed"
    assert run["artifacts"] == []
    events = parse_sse(client.get(f"/v1/runs/{run['id']}/events", headers=AUTH_A).text)
    assert events[-1]["event"] == "run.failed"
    assert events[-1]["data"]["payload"]["message"] == "runner unavailable"
