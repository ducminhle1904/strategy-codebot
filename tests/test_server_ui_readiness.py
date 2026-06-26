import json
from pathlib import Path

from fastapi.testclient import TestClient

from strategy_codebot.schemas import write_json
from strategy_codebot.server import create_app
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.database import create_sqlite_repository
from strategy_codebot.server.models import BacktestEquitySummary
from strategy_codebot.server.models import BacktestReport
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
    repository.create_artifact(
        auth,
        run["id"],
        kind="backtest_plan",
        mime_type="application/json",
        display_name="Backtest plan",
        storage_key="runs/run_1/backtest-plan.json",
        metadata_json={"schema_version": "backtest_plan.v1"},
    )

    response = client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["conversation"]["id"] == conversation["id"]
    assert [item["id"] for item in payload["messages"]] == [message["id"]]
    assert payload["latest_run"]["id"] == run["id"]
    assert {artifact["kind"] for artifact in payload["latest_run_artifacts"]} >= {
        "backtest_plan",
        "pine_file",
        "validation_report",
    }
    conversation_artifact_kinds = {artifact["kind"] for artifact in payload["conversation_artifacts"]}
    assert "pine_file" in conversation_artifact_kinds
    assert "backtest_plan" not in conversation_artifact_kinds
    assert "validation_report" not in conversation_artifact_kinds
    pine_artifact = next(artifact for artifact in payload["latest_run_artifacts"] if artifact["kind"] == "pine_file")
    validation_artifact = next(
        artifact for artifact in payload["latest_run_artifacts"] if artifact["kind"] == "validation_report"
    )
    assert pine_artifact["presentation"] == {
        "dedupe_key": "code:strategy.pine",
        "is_primary": True,
        "language_hint": "pine",
        "user_kind": "code",
        "viewer_kind": "code",
        "visibility": "user",
    }
    assert validation_artifact["presentation"]["is_primary"] is False
    assert validation_artifact["presentation"]["user_kind"] == "validation"
    assert validation_artifact["presentation"]["viewer_kind"] == "json"
    assert validation_artifact["presentation"]["visibility"] == "internal"
    plan_artifact = next(artifact for artifact in payload["latest_run_artifacts"] if artifact["kind"] == "backtest_plan")
    assert plan_artifact["presentation"]["viewer_kind"] == "backtest_plan"
    assert plan_artifact["presentation"]["is_primary"] is False
    assert plan_artifact["presentation"]["visibility"] == "internal"
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
    assert [
        event["payload"]["index"]
        for event in payload["conversation_run_events"]
        if event["type"] == "debug.progress"
    ] == list(range(35))
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


def test_conversation_state_bounds_artifacts_and_exposes_paginated_history(tmp_path: Path) -> None:
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    auth = AuthContext(user_id=AUTH_A["X-User-Id"], workspace_id=AUTH_A["X-Workspace-Id"])
    run = repository.create_run(auth, conversation["id"], status="completed", mode="agent")
    assert run is not None
    for index in range(55):
        repository.create_artifact(
            auth,
            run.id,
            kind="review_report",
            mime_type="application/json",
            display_name=f"review-{index}.json",
            storage_key=f"runs/{run.id}/review-{index}.json",
            metadata_json={"index": index},
        )
    repository.create_artifact(
        auth,
        run.id,
        kind="runtime_trace_summary",
        mime_type="application/json",
        display_name="runtime-trace.json",
        storage_key=f"runs/{run.id}/runtime-trace.json",
        metadata_json={"internal": True},
    )

    response = client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["conversation_artifacts"]) == 50
    assert payload["conversation_artifacts_next_cursor"]
    assert all(item["preview_summary"] is None for item in payload["conversation_artifacts"])
    assert all(item["kind"] != "runtime_trace_summary" for item in payload["conversation_artifacts"])

    page = client.get(
        f"/v1/conversations/{conversation['id']}/artifacts",
        headers=AUTH_A,
        params={"cursor": payload["conversation_artifacts_next_cursor"], "limit": 10},
    )

    assert page.status_code == 200, page.text
    page_payload = page.json()
    assert len(page_payload["items"]) == 5
    assert page_payload["next_cursor"] is None
    assert client.get(f"/v1/conversations/{conversation['id']}/artifacts", headers=AUTH_B).status_code == 404
    bad_cursor = client.get(
        f"/v1/conversations/{conversation['id']}/artifacts",
        headers=AUTH_A,
        params={"cursor": "not-a-cursor"},
    )
    assert bad_cursor.status_code == 400


def test_workspace_artifacts_lists_visible_artifacts_across_conversations(tmp_path: Path) -> None:
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path))
    first_conversation = client.post("/v1/conversations", headers=AUTH_A, json={"title": "First"}).json()
    second_conversation = client.post("/v1/conversations", headers=AUTH_A, json={"title": "Second"}).json()
    other_conversation = client.post("/v1/conversations", headers=AUTH_B, json={"title": "Other"}).json()
    auth = AuthContext(user_id=AUTH_A["X-User-Id"], workspace_id=AUTH_A["X-Workspace-Id"])
    other_auth = AuthContext(user_id=AUTH_B["X-User-Id"], workspace_id=AUTH_B["X-Workspace-Id"])
    first_run = repository.create_run(auth, first_conversation["id"], status="completed", mode="agent")
    second_run = repository.create_run(auth, second_conversation["id"], status="completed", mode="agent")
    other_run = repository.create_run(other_auth, other_conversation["id"], status="completed", mode="agent")
    assert first_run is not None
    assert second_run is not None
    assert other_run is not None
    repository.create_artifact(
        auth,
        first_run.id,
        kind="pine_file",
        mime_type="text/x-pine",
        display_name="First strategy.pine",
        storage_key=f"runs/{first_run.id}/strategy.pine",
        metadata_json={"index": 1},
    )
    repository.create_artifact(
        auth,
        second_run.id,
        kind="backtest_report",
        mime_type="application/json",
        display_name="Second report",
        storage_key=f"runs/{second_run.id}/backtest-report.json",
        metadata_json={"index": 2},
    )
    repository.create_artifact(
        auth,
        second_run.id,
        kind="runtime_trace_summary",
        mime_type="application/json",
        display_name="Internal trace",
        storage_key=f"runs/{second_run.id}/runtime-trace.json",
        metadata_json={"internal": True},
    )
    repository.create_artifact(
        auth,
        second_run.id,
        kind="pineforge_compile_report",
        mime_type="application/json",
        display_name="pineforge-compile.json",
        storage_key=f"runs/{second_run.id}/pineforge-compile.json",
        metadata_json={"internal": True},
    )
    repository.create_artifact(
        auth,
        second_run.id,
        kind="backtest_strategy_adapter_source",
        mime_type="application/json",
        display_name="strategy-adapter-source.json",
        storage_key=f"runs/{second_run.id}/strategy-adapter-source.json",
        metadata_json={"internal": True},
    )
    for kind, display_name in (
        ("validation_report", "validation.json"),
        ("backtest_trades", "trades.json"),
        ("backtest_plan", "backtest_plan.json"),
    ):
        repository.create_artifact(
            auth,
            second_run.id,
            kind=kind,
            mime_type="application/json",
            display_name=display_name,
            storage_key=f"runs/{second_run.id}/{display_name}",
            metadata_json={"internal": True},
        )
    repository.create_artifact(
        other_auth,
        other_run.id,
        kind="pine_file",
        mime_type="text/x-pine",
        display_name="Other strategy.pine",
        storage_key=f"runs/{other_run.id}/strategy.pine",
        metadata_json={"index": 3},
    )

    response = client.get("/v1/artifacts", headers=AUTH_A, params={"limit": 1})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [item["display_name"] for item in payload["items"]] == ["Second report"]
    state_response = client.get(f"/v1/conversations/{second_conversation['id']}/state", headers=AUTH_A)
    assert state_response.status_code == 200, state_response.text
    state_names = {item["display_name"] for item in state_response.json()["conversation_artifacts"]}
    assert {
        "backtest_plan.json",
        "pineforge-compile.json",
        "strategy-adapter-source.json",
        "trades.json",
        "validation.json",
    }.isdisjoint(state_names)
    assert payload["next_cursor"]
    next_page = client.get(
        "/v1/artifacts",
        headers=AUTH_A,
        params={"cursor": payload["next_cursor"], "limit": 10},
    )
    assert next_page.status_code == 200, next_page.text
    next_payload = next_page.json()
    assert [item["display_name"] for item in next_payload["items"]] == ["First strategy.pine"]
    assert next_payload["next_cursor"] is None
    all_page = client.get("/v1/artifacts", headers=AUTH_A, params={"visibility": "all", "limit": 10})
    assert all_page.status_code == 200, all_page.text
    all_names = {item["display_name"] for item in all_page.json()["items"]}
    assert {
        "backtest_plan.json",
        "pineforge-compile.json",
        "strategy-adapter-source.json",
        "trades.json",
        "validation.json",
    } <= all_names
    assert client.get("/v1/artifacts").status_code == 401
    bad_cursor = client.get("/v1/artifacts", headers=AUTH_A, params={"cursor": "not-a-cursor"})
    assert bad_cursor.status_code == 400


def test_artifact_response_exposes_backtest_preview_summary_from_metadata(tmp_path: Path) -> None:
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    run = client.post(
        "/v1/runs",
        headers=AUTH_A,
        json={"conversation_id": conversation["id"], "strategy_spec": valid_spec()},
    ).json()
    auth = AuthContext(user_id=AUTH_A["X-User-Id"], workspace_id=AUTH_A["X-Workspace-Id"])
    repository.create_artifact(
        auth,
        run["id"],
        kind="backtest_dashboard",
        mime_type="application/json",
        display_name="backtest-dashboard.json",
        storage_key=f"runs/{run['id']}/backtest-dashboard.json",
        metadata_json={
            "preview_summary": {
                "kind": "backtest_result",
                "run_id": run["id"],
                "symbol": "BNBUSDT",
                "timeframe": "1h",
                "metrics": {"net_pnl": -12.5, "trade_count": 4},
                "equity_preview": [{"index": 0, "pnl": 0}, {"index": 1, "pnl": -12.5}],
                "generated_at": "2026-06-24T00:00:00+00:00",
            }
        },
    )

    response = client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A)

    assert response.status_code == 200, response.text
    dashboard = next(item for item in response.json()["conversation_artifacts"] if item["kind"] == "backtest_dashboard")
    assert dashboard["preview_summary"]["kind"] == "backtest_result"
    assert dashboard["preview_summary"]["metrics"]["net_pnl"] == -12.5


def test_legacy_backtest_dashboard_preview_summary_uses_indexed_report(tmp_path: Path) -> None:
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    auth = AuthContext(user_id=AUTH_A["X-User-Id"], workspace_id=AUTH_A["X-Workspace-Id"])
    run = repository.create_run(auth, conversation["id"], status="completed", mode="backtest-preview")
    assert run is not None
    with repository._session_factory() as session:  # noqa: SLF001
        session.add(
            BacktestReport(
                id="btr_legacy_preview",
                run_id=run.id,
                owner_user_id=auth.user_id,
                workspace_id=auth.workspace_id,
                engine="pineforge",
                evidence_label="Local preview evidence",
                execution_semantics="model_generated_pine_pineforge",
                symbol="BNBUSDT",
                signal_timeframe="1h",
                candle_timeframe="1m",
                metrics_json={
                    "pnl": {"absolute": -222.533, "percentage": -2.2253},
                    "max_drawdown": 3.1913,
                    "trade_count": 236,
                    "win_rate": 31.7797,
                },
                assumptions_json=[],
                warnings_json=[],
                reproducibility_hash=None,
            )
        )
        session.add(
            BacktestEquitySummary(
                id="bte_legacy_preview",
                run_id=run.id,
                owner_user_id=auth.user_id,
                workspace_id=auth.workspace_id,
                sample_resolution="bounded",
                points_json=[
                    {"index": 0, "timestamp": "2024-01-01T00:00:00Z", "pnl_cost": 0},
                    {"index": 1, "timestamp": "2024-01-01T01:00:00Z", "pnl_cost": -222.533},
                ],
                drawdown_windows_json=[],
                monthly_returns_json=[],
            )
        )
        session.commit()
    repository.create_artifact(
        auth,
        run.id,
        kind="backtest_dashboard",
        mime_type="application/json",
        display_name="backtest-dashboard.json",
        storage_key=f"runs/{run.id}/backtest-dashboard.json",
        metadata_json={"source": "legacy"},
    )

    response = client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A)

    assert response.status_code == 200, response.text
    dashboard = next(item for item in response.json()["conversation_artifacts"] if item["kind"] == "backtest_dashboard")
    assert dashboard["preview_summary"]["kind"] == "backtest_result"
    assert dashboard["preview_summary"]["symbol"] == "BNBUSDT"
    assert dashboard["preview_summary"]["timeframe"] == "1h"
    assert dashboard["preview_summary"]["metrics"]["net_pnl"] == -222.533
    assert dashboard["preview_summary"]["metrics"]["return_pct"] == -2.2253
    workspace_response = client.get("/v1/artifacts", headers=AUTH_A)
    assert workspace_response.status_code == 200, workspace_response.text
    workspace_dashboard = next(item for item in workspace_response.json()["items"] if item["kind"] == "backtest_dashboard")
    assert workspace_dashboard["preview_summary"]["kind"] == "backtest_result"
    assert workspace_dashboard["preview_summary"]["metrics"]["net_pnl"] == -222.533
    assert dashboard["preview_summary"]["metrics"]["max_drawdown_pct"] == 3.1913
    assert dashboard["preview_summary"]["metrics"]["trade_count"] == 236
    assert len(dashboard["preview_summary"]["equity_preview"]) == 2


def test_conversation_state_keeps_older_artifacts_when_latest_run_has_none(tmp_path: Path) -> None:
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    auth = AuthContext(user_id=AUTH_A["X-User-Id"], workspace_id=AUTH_A["X-Workspace-Id"])
    first_run = repository.create_run(auth, conversation["id"], status="completed", mode="agent")
    assert first_run is not None
    artifact = repository.create_artifact(
        auth,
        first_run.id,
        kind="pine_file",
        mime_type="text/plain",
        display_name="breakout-continuation.pine",
        storage_key=f"runs/{first_run.id}/breakout-continuation.pine",
    )
    assert artifact is not None
    second_run = repository.create_run(auth, conversation["id"], status="completed", mode="agent")
    assert second_run is not None

    response = client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["latest_run"]["id"] == second_run.id
    assert payload["latest_run_artifacts"] == []
    assert [item["id"] for item in payload["conversation_artifacts"]] == [artifact.id]


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
