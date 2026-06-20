import json
from pathlib import Path

from fastapi.testclient import TestClient

from strategy_codebot.schemas import write_json
from strategy_codebot.server import create_app
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.database import create_sqlite_repository
from server_helpers import parse_sse
from server_helpers import valid_spec

AUTH_A = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-a"}
AUTH_B = {"X-User-Id": "user-b", "X-Workspace-Id": "workspace-a"}


def test_message_list_returns_authorized_messages_oldest_first() -> None:
    client = TestClient(create_app(repository=create_sqlite_repository()))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    first = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        headers=AUTH_A,
        json={"content": "First message"},
    ).json()
    second = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        headers=AUTH_A,
        json={"content": "Second message"},
    ).json()

    response = client.get(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH_A)

    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [first["id"], second["id"]]
    assert client.get(f"/v1/conversations/{conversation['id']}/messages").status_code == 401
    assert client.get(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH_B).status_code == 404


def test_sidebar_returns_tenant_owned_conversation_summaries(tmp_path: Path) -> None:
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path))
    older = client.post("/v1/conversations", headers=AUTH_A, json={"title": "Older"}).json()
    newer = client.post("/v1/conversations", headers=AUTH_A, json={"title": "Newer"}).json()
    other = client.post("/v1/conversations", headers=AUTH_B, json={"title": "Other"}).json()
    client.post(
        f"/v1/conversations/{older['id']}/messages",
        headers=AUTH_A,
        json={"content": "A long user message that should become the sidebar preview for the older thread."},
    )
    run = client.post(
        "/v1/runs",
        headers=AUTH_A,
        json={"conversation_id": older["id"], "strategy_spec": valid_spec()},
    ).json()
    client.post(
        f"/v1/conversations/{newer['id']}/messages",
        headers=AUTH_A,
        json={"content": "Newest sidebar item"},
    )

    response = client.get("/v1/conversations/sidebar", headers=AUTH_A)

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["conversation"]["id"] for item in items] == [newer["id"], older["id"]]
    assert other["id"] not in [item["conversation"]["id"] for item in items]
    older_item = next(item for item in items if item["conversation"]["id"] == older["id"])
    assert older_item["message_count"] == 1
    assert older_item["last_message_preview"].startswith("A long user message")
    assert older_item["latest_run_id"] == run["id"]
    assert older_item["latest_run_status"] == "completed"
    assert client.get("/v1/conversations/sidebar").status_code == 401


def test_conversation_state_bootstraps_messages_latest_run_artifacts_and_feedback_targets(tmp_path: Path) -> None:
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    message = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        headers=AUTH_A,
        json={"content": "Draft state"},
    ).json()
    run = client.post(
        "/v1/runs",
        headers=AUTH_A,
        json={"conversation_id": conversation["id"], "strategy_spec": valid_spec()},
    ).json()
    auth = AuthContext(user_id=AUTH_A["X-User-Id"], workspace_id=AUTH_A["X-Workspace-Id"])
    for index in range(35):
        repository.append_run_event(auth, run["id"], "debug.progress", {"index": index})

    response = client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["conversation"]["id"] == conversation["id"]
    assert [item["id"] for item in payload["messages"]] == [message["id"]]
    assert payload["latest_run"]["id"] == run["id"]
    assert {artifact["kind"] for artifact in payload["latest_run_artifacts"]} >= {"pine_file", "validation_report"}
    assert payload["feedback_targets"]["conversation_id"] == conversation["id"]
    assert payload["feedback_targets"]["latest_run_id"] == run["id"]
    assert message["id"] in payload["feedback_targets"]["message_ids"]
    assert payload["strategy_profile"]["source"] == "strategy_spec"
    assert payload["strategy_profile"]["snapshot"]["completeness"] == "ready_for_artifact"
    assert payload["strategy_profile"]["brief"]["entry_rules"]
    assert payload["strategy_profile"]["memory"]["has_context"] is True
    assert all(
        "strategy_spec" not in (artifact["metadata_json"] or {})
        for artifact in payload["latest_run_artifacts"]
    )
    assert len(payload["latest_run_events"]) == 30
    assert [event["sequence"] for event in payload["latest_run_events"]] == sorted(
        event["sequence"] for event in payload["latest_run_events"]
    )
    assert [event["payload"]["index"] for event in payload["latest_run_events"]] == list(range(5, 35))
    full_replay = parse_sse(client.get(f"/v1/runs/{run['id']}/events", headers=AUTH_A).text)
    replayed_indexes = [
        frame["data"]["payload"]["index"]
        for frame in full_replay
        if frame["event"] == "debug.progress"
    ]
    assert replayed_indexes == list(range(35))
    assert client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_B).status_code == 404


def test_artifact_preview_handles_text_json_truncation_and_redaction(tmp_path: Path) -> None:
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    auth = AuthContext(user_id=AUTH_A["X-User-Id"], workspace_id=AUTH_A["X-Workspace-Id"])
    run = repository.create_run(auth, conversation["id"], status="completed")
    assert run is not None
    artifact_dir = tmp_path / "runs" / run.id
    artifact_dir.mkdir(parents=True)
    text_path = artifact_dir / "strategy.pine"
    text_path.write_text("//@version=6\nsecret sk-proj-abcdefghijklmnop\n" + "x" * 200, encoding="utf-8")
    text_artifact = repository.create_artifact(
        auth,
        run.id,
        kind="pine_file",
        mime_type="text/plain",
        display_name="strategy.pine",
        storage_key=f"runs/{run.id}/strategy.pine",
    )
    json_path = artifact_dir / "validation-report.json"
    write_json(json_path, {"status": "pass", "platform": "pine_v6", "checks": [{"id": "syntax"}]})
    json_artifact = repository.create_artifact(
        auth,
        run.id,
        kind="validation_report",
        mime_type="application/json",
        display_name="validation-report.json",
        storage_key=f"runs/{run.id}/validation-report.json",
    )
    large_json_path = artifact_dir / "large-validation-report.json"
    large_json_path.write_text(json.dumps({"status": "pass", "items": ["x" * 1000]}), encoding="utf-8")
    large_json_artifact = repository.create_artifact(
        auth,
        run.id,
        kind="validation_report",
        mime_type="application/json",
        display_name="large-validation-report.json",
        storage_key=f"runs/{run.id}/large-validation-report.json",
    )

    text_response = client.get(f"/v1/artifacts/{text_artifact.id}/preview?max_bytes=48", headers=AUTH_A)
    json_response = client.get(f"/v1/artifacts/{json_artifact.id}/preview", headers=AUTH_A)
    large_json_response = client.get(f"/v1/artifacts/{large_json_artifact.id}/preview?max_bytes=64", headers=AUTH_A)

    assert text_response.status_code == 200, text_response.text
    text_preview = text_response.json()
    assert text_preview["language"] == "pine"
    assert text_preview["truncated"] is True
    assert text_preview["line_count"] == 3
    assert "[REDACTED]" in text_preview["preview"]
    assert "sk-proj" not in text_preview["preview"]
    assert "storage_key" not in json.dumps(text_preview)
    assert json_response.status_code == 200, json_response.text
    json_preview = json_response.json()
    assert json_preview["language"] == "json"
    assert json_preview["preview"]["status"] == "pass"
    assert json_preview["preview"]["checks_count"] == 1
    assert json_preview["raw_available"] is True
    assert large_json_response.status_code == 200, large_json_response.text
    large_json_preview = large_json_response.json()
    assert large_json_preview["language"] == "json"
    assert large_json_preview["truncated"] is True
    assert isinstance(large_json_preview["preview"], str)
    assert client.get(f"/v1/artifacts/{text_artifact.id}/preview", headers=AUTH_B).status_code == 404


def test_run_progress_stream_returns_snapshot_updates_and_resume(tmp_path: Path) -> None:
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    run = client.post(
        "/v1/runs",
        headers=AUTH_A,
        json={"conversation_id": conversation["id"], "strategy_spec": valid_spec()},
    ).json()

    response = client.get(f"/v1/runs/{run['id']}/progress", headers=AUTH_A)

    assert response.status_code == 200, response.text
    frames = parse_sse(response.text)
    assert frames[0]["event"] == "progress.snapshot"
    assert frames[0]["data"]["payload"]["status"] == "completed"
    update_kinds = [frame["data"]["payload"]["kind"] for frame in frames[1:]]
    assert "stage.completed" in update_kinds
    assert "artifact.created" in update_kinds
    assert "run.terminal" in update_kinds
    assert frames[-1]["data"]["payload"]["source_event_type"] == "run.completed"

    raw_events = parse_sse(client.get(f"/v1/runs/{run['id']}/events", headers=AUTH_A).text)
    resumed = parse_sse(
        client.get(
            f"/v1/runs/{run['id']}/progress",
            headers={**AUTH_A, "Last-Event-ID": raw_events[0]["id"]},
        ).text
    )
    assert resumed[0]["event"] == "progress.snapshot"
    assert raw_events[0]["id"] not in [frame["id"] for frame in resumed[1:]]
    assert client.get(f"/v1/runs/{run['id']}/progress", headers=AUTH_B).status_code == 404


def test_feedback_options_returns_stable_ui_choices() -> None:
    client = TestClient(create_app(repository=create_sqlite_repository()))

    response = client.get("/v1/feedback/options", headers=AUTH_A)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [item["value"] for item in payload["ratings"]] == ["up", "down", "neutral"]
    assert "unsafe_claim" in [item["value"] for item in payload["categories"]]
    assert client.get("/v1/feedback/options").status_code == 401
