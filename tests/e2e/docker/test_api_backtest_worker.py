from __future__ import annotations

import os
import subprocess
import time

from .helpers import auth
from .helpers import backtest_config
from .helpers import client
from .helpers import db_rows
from .helpers import parse_sse
from .helpers import pine_code
from .helpers import valid_spec
from .helpers import wait_for_run_events
from .helpers import write_json


def _queue_backtest(api, headers: dict[str, str], conversation_id: str, config: dict) -> dict:
    response = api.post(
        "/v1/runs",
        headers=headers,
        json={
            "conversation_id": conversation_id,
            "mode": "backtest-preview",
            "strategy_spec": valid_spec(),
            "pine_code": pine_code(),
            "backtest_config": config,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _manifest_for_run(api, headers: dict[str, str], run_id: str) -> dict:
    rows = db_rows(
        """
        SELECT id
        FROM artifacts
        WHERE run_id = %s AND kind = 'market_data_cache_manifest'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (run_id,),
    )
    assert rows
    return api.get(f"/v1/artifacts/{rows[0]['id']}", headers=headers).json()["content"]


def test_readiness_reports_real_dependencies() -> None:
    with client() as api:
        health = api.get("/health")
        health.raise_for_status()
        assert health.json()["service"] == "strategy-codebot-api"

        ready = api.get("/ready")
        ready.raise_for_status()
        payload = ready.json()
        write_json("ready.json", payload)
        assert payload["status"] == "ok"
        assert "repository" in payload["checks"]
        assert "artifact_store" in payload["checks"]


def test_backtest_preview_completes_with_real_worker() -> None:
    headers = auth(workspace="e2e-backtest-worker")
    with client(timeout=60.0) as api:
        conversation = api.post("/v1/conversations", headers=headers, json={"title": "Docker backtest"}).json()
        response = api.post(
            "/v1/runs",
            headers=headers,
            json={
                "conversation_id": conversation["id"],
                "mode": "backtest-preview",
                "strategy_spec": valid_spec(),
                "pine_code": pine_code(),
                "backtest_config": backtest_config(),
            },
        )
        assert response.status_code == 201, response.text
        run = response.json()
        assert run["status"] == "queued"

        frames = wait_for_run_events(api, headers, run["id"])
        event_types = [frame["event"] for frame in frames]
        assert event_types[0] == "backtest.queued"
        assert "backtest.data.started" in event_types
        assert "backtest.data.completed" in event_types
        assert "backtest.execution.started" in event_types
        assert "backtest.execution.completed" in event_types
        assert "backtest.report.completed" in event_types
        assert event_types[-1] == "run.completed"

        state = api.get(f"/v1/conversations/{conversation['id']}/state", headers=headers).json()
        artifacts = state["latest_run_artifacts"]
        kinds = {artifact["kind"] for artifact in artifacts}
        assert {
            "backtest_plan",
            "backtest_dashboard",
            "backtest_report",
            "backtest_trades",
            "backtest_equity_curve",
            "backtest_source_bundle",
            "market_data_cache_manifest",
            "backtest_run_metadata",
        } <= kinds

        report_artifact = next(artifact for artifact in artifacts if artifact["kind"] == "backtest_report")
        report = api.get(f"/v1/artifacts/{report_artifact['id']}", headers=headers).json()["content"]
        assert report["evidence_label"] == "Local sandbox preview evidence"
        assert report["execution_semantics"] == "model_generated_pine_pineforge"
        assert "TradingView proof" in " ".join(report["warnings"])
        assert report["reproducibility_hash"]
        assert report["assumptions"]["data_source"] == "public-readonly-cache"

        replay = parse_sse(api.get(f"/v1/runs/{run['id']}/events", headers=headers).text)
        resumed = parse_sse(
            api.get(
                f"/v1/runs/{run['id']}/events",
                headers={**headers, "Last-Event-ID": str(replay[1]["data"]["sequence"])},
            ).text
        )
        assert resumed
        assert all(frame["data"]["sequence"] > replay[1]["data"]["sequence"] for frame in resumed)


def test_state_snapshot_is_capped_but_messages_endpoint_is_full() -> None:
    headers = auth(workspace="e2e-state-cap")
    with client(timeout=60.0) as api:
        conversation = api.post("/v1/conversations", headers=headers, json={"title": "Long state"}).json()
        for index in range(105):
            response = api.post(
                f"/v1/conversations/{conversation['id']}/messages",
                headers=headers,
                json={"content": f"message {index:03d}"},
            )
            assert response.status_code == 201, response.text

        state = api.get(f"/v1/conversations/{conversation['id']}/state", headers=headers).json()
        assert state["message_count"] == 105
        assert state["messages_truncated"] is True
        assert state["message_limit"] == 100
        assert len(state["messages"]) == 100
        assert state["messages"][0]["content"] == "message 005"
        assert state["messages"][-1]["content"] == "message 104"

        messages = api.get(f"/v1/conversations/{conversation['id']}/messages", headers=headers).json()
        assert len(messages["items"]) == 105


def test_cache_reuse_and_duplicate_terminal_guards() -> None:
    headers = auth(workspace="e2e-cache-reuse")
    with client(timeout=60.0) as api:
        conversation = api.post("/v1/conversations", headers=headers, json={"title": "Cache reuse"}).json()
        run_ids: list[str] = []
        for _index in range(2):
            run = api.post(
                "/v1/runs",
                headers=headers,
                json={
                    "conversation_id": conversation["id"],
                    "mode": "backtest-preview",
                    "strategy_spec": valid_spec(),
                    "pine_code": pine_code(),
                    "backtest_config": backtest_config(),
                },
            ).json()
            run_ids.append(run["id"])
            wait_for_run_events(api, headers, run["id"])

        manifest_rows = db_rows(
            """
            SELECT a.run_id, a.id AS artifact_id
            FROM artifacts a
            JOIN assistant_runs r ON r.id = a.run_id
            WHERE r.id = ANY(%s) AND a.kind = 'market_data_cache_manifest'
            ORDER BY r.created_at
            """,
            (run_ids,),
        )
        assert len(manifest_rows) == 2

        event_rows = db_rows(
            """
            SELECT run_id, type, count(*) AS count
            FROM run_events
            WHERE run_id = ANY(%s) AND type IN ('run.completed', 'run.failed', 'run.cancelled')
            GROUP BY run_id, type
            """,
            (run_ids,),
        )
        assert event_rows
        assert all(row["count"] == 1 for row in event_rows)

        jobs = db_rows(
            """
            SELECT status, count(*) AS count
            FROM run_jobs
            WHERE run_id = ANY(%s)
            GROUP BY status
            """,
            (run_ids,),
        )
        write_json("cache-reuse-jobs.json", jobs)
        assert {row["status"] for row in jobs} == {"completed"}


def test_range_cache_reuses_partial_data_and_fetches_only_missing_interval() -> None:
    seed_headers = auth(workspace="e2e-range-cache-seed")
    extend_headers = auth(workspace="e2e-range-cache-extend")
    with client(timeout=60.0) as api:
        seed_conversation = api.post("/v1/conversations", headers=seed_headers, json={"title": "Range seed"}).json()
        seed_run = _queue_backtest(
            api,
            seed_headers,
            seed_conversation["id"],
            backtest_config(
                symbol="ETH/USDT",
                timeframe="1h",
                start="2024-02-01",
                end="2024-02-03",
            ),
        )
        wait_for_run_events(api, seed_headers, seed_run["id"])
        seed_manifest = _manifest_for_run(api, seed_headers, seed_run["id"])
        assert seed_manifest["cache_version"] == "range-v2"
        assert seed_manifest["segments_created"] > 0

        extend_conversation = api.post("/v1/conversations", headers=extend_headers, json={"title": "Range extend"}).json()
        extend_run = _queue_backtest(
            api,
            extend_headers,
            extend_conversation["id"],
            backtest_config(
                symbol="ETH/USDT",
                timeframe="1h",
                start="2024-02-01",
                end="2024-02-05",
            ),
        )
        wait_for_run_events(api, extend_headers, extend_run["id"])
        extend_manifest = _manifest_for_run(api, extend_headers, extend_run["id"])

    write_json("range-cache-seed-manifest.json", seed_manifest)
    write_json("range-cache-extend-manifest.json", extend_manifest)
    assert extend_manifest["cache_version"] == "range-v2"
    assert extend_manifest["segments_reused"] > 0
    assert extend_manifest["segments_created"] > 0
    assert extend_manifest["missing_intervals_fetched"] > 0
    assert extend_manifest["candles_fetched"] < extend_manifest["candles_used"]


def test_api_restart_preserves_persisted_run_state() -> None:
    project = os.getenv("STRATEGY_CODEBOT_E2E_COMPOSE_PROJECT")
    compose_files = os.getenv("STRATEGY_CODEBOT_E2E_COMPOSE_FILES")
    if not project or not compose_files:
        return
    headers = auth(workspace="e2e-api-restart")
    with client(timeout=60.0) as api:
        conversation = api.post("/v1/conversations", headers=headers, json={"title": "Restart"}).json()
        run = api.post(
            "/v1/runs",
            headers=headers,
            json={
                "conversation_id": conversation["id"],
                "mode": "backtest-preview",
                "strategy_spec": valid_spec(),
                "pine_code": pine_code(),
                "backtest_config": backtest_config(),
            },
        ).json()
        wait_for_run_events(api, headers, run["id"])

    command = ["docker", "compose", "-p", project]
    for compose_file in compose_files.split(":"):
        command.extend(["-f", compose_file])
    subprocess.run([*command, "restart", "api"], check=True, timeout=60)

    deadline = time.time() + 90
    with client(timeout=15.0) as api:
        while time.time() < deadline:
            try:
                if api.get("/health").status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(1)
        else:
            raise AssertionError("api did not recover after restart")

        frames = parse_sse(api.get(f"/v1/runs/{run['id']}/events", headers=headers).text)
        state = api.get(f"/v1/conversations/{conversation['id']}/state", headers=headers).json()
        assert frames[-1]["event"] == "run.completed"
        assert state["latest_run"]["id"] == run["id"]
