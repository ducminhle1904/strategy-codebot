from __future__ import annotations

import time
from typing import Any

from .helpers import auth
from .helpers import client
from .helpers import db_rows
from .helpers import local_paper_runtime_payload
from .helpers import parse_sse
from .helpers import publish_local_paper_cross_fixture
from .helpers import redis_client
from .helpers import wait_for_runtime
from .helpers import wait_for_runtime_events
from .helpers import write_json


def _stream_agent_plan(*, workspace: str) -> tuple[dict[str, str], dict[str, Any]]:
    headers = auth(workspace=workspace)
    with client(timeout=60.0) as api:
        conversation = api.post("/v1/conversations", headers=headers, json={"title": "Docker paper bot"}).json()
        response = api.post(
            f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
            headers=headers,
            json={"content": "Create a local preview plan for BTC 1h and prepare paper bot inputs.", "language": "en"},
        )
        assert response.status_code == 200, response.text
        frames = parse_sse(response.text)
        write_json("paper-bot-chat-plan.json", frames)
        assert frames[-1]["event"] == "run.completed"
        return headers, _tool_output(frames, "create_backtest_plan")


def _tool_output(frames: list[dict[str, Any]], tool_id: str) -> dict[str, Any]:
    for frame in frames:
        if frame["event"] != "tool.completed":
            continue
        payload = frame["data"]["payload"]
        if payload.get("tool_id") == tool_id:
            assert "status" not in payload, payload
            return payload["output"]
    raise AssertionError(f"missing tool.completed for {tool_id}")


def _runtime_payload(
    plan: dict[str, Any],
    *,
    strategy_id: str,
    account_id: str = "acct-paper-e2e",
    mode: str = "paper",
) -> dict[str, Any]:
    config = plan["backtest_config"]
    venue = str(config.get("exchange") or "binance").upper()
    symbol = str(plan["strategy_spec"].get("symbol") or config.get("symbol") or "BTCUSDT").replace("/", "").upper()
    return local_paper_runtime_payload(
        broker_connection_id="paper-binance-e2e",
        account_id=account_id,
        mode=mode,
        risk_policy_id="risk-basic-e2e",
        strategy_id=strategy_id,
        source="docker_e2e_chat_plan",
        venue=venue,
        symbol=symbol,
        manifest_refs={
            "artifact_bundle": f"backtest-plan:{plan['artifact_id']}",
            "backtest_plan_artifact_id": plan["artifact_id"],
            "pine_code_artifact_id": plan["pine_code_artifact_id"],
        },
    )


def test_chat_plan_to_paper_runtime_with_real_worker_and_redis_streams() -> None:
    headers, plan = _stream_agent_plan(workspace="e2e-paper-bot")
    assert plan["artifact_id"]
    assert plan["pine_code_artifact_id"]

    runtime_payload = _runtime_payload(plan, strategy_id="paper-strategy-1")
    with client(timeout=60.0) as api:
        artifact = api.get(f"/v1/artifacts/{plan['artifact_id']}", headers=headers)
        artifact.raise_for_status()
        assert artifact.json()["content"]["strategy_spec"]["symbol"] == "BTCUSDT"

        live = api.post(
            "/v1/nautilus/runtimes",
            headers={**headers, "Idempotency-Key": "paper-bot-live-blocked"},
            json=_runtime_payload(plan, strategy_id="paper-strategy-live", mode="live"),
        )
        assert live.status_code == 403, live.text

        start_headers = {**headers, "Idempotency-Key": "paper-bot-start-1"}
        first = api.post("/v1/nautilus/runtimes", headers=start_headers, json=runtime_payload)
        retry = api.post("/v1/nautilus/runtimes", headers=start_headers, json=runtime_payload)
        assert first.status_code == 201, first.text
        assert retry.status_code == 201, retry.text
        runtime = first.json()
        assert retry.json()["id"] == runtime["id"]
        assert runtime["desired_state"] == "running"

        events_after_retry = api.get(f"/v1/nautilus/runtimes/{runtime['id']}/events", headers=headers)
        events_after_retry.raise_for_status()
        assert [event["type"] for event in events_after_retry.json()] == ["strategy_loaded"]

        grouped_payload = _runtime_payload(plan, strategy_id="paper-strategy-2")
        grouped = api.post("/v1/nautilus/runtimes", headers=headers, json=grouped_payload)
        assert grouped.status_code == 201, grouped.text
        assert grouped.json()["id"] == runtime["id"]
        assert grouped.json()["strategy_ids"] == ["paper-strategy-1", "paper-strategy-2"]

        warming = wait_for_runtime(
            api,
            headers,
            runtime["id"],
            lambda payload: payload["state"] == "warming_up"
            and bool(payload["worker_id"])
            and payload["heartbeat_count"] > 0
            and payload["last_heartbeat_at"] is not None,
        )
        assert warming["desired_state"] == "running"

        redis = redis_client()
        try:
            stream, stream_id = publish_local_paper_cross_fixture(redis, event_prefix="paper-bot-bar")
            running = wait_for_runtime(
                api,
                headers,
                runtime["id"],
                lambda payload: (payload.get("stream_cursor") or {}).get(stream) == stream_id
                and payload["state"] == "running"
                and (payload.get("heartbeat_metrics") or {}).get("metrics", {}).get("paper_engine") == "nautilus_local_paper"
                and (payload.get("heartbeat_metrics") or {}).get("metrics", {}).get("warmup_status") == "complete",
            )
            assert running["desired_state"] == "running"
            events = wait_for_runtime_events(
                api,
                headers,
                runtime["id"],
                {
                    "strategy_loaded",
                    "heartbeat",
                    "warmup_started",
                    "warmup_completed",
                    "signal",
                    "order_submitted",
                    "fill",
                },
            )
            assert "runtime_error" not in {event["type"] for event in events}
            first_order_submitted = sum(1 for event in events if event["type"] == "order_submitted")
            first_fills = sum(1 for event in events if event["type"] == "fill")
            assert first_order_submitted >= 1
            assert first_fills >= 1
            time.sleep(1)
            baseline = api.get(f"/v1/nautilus/runtimes/{runtime['id']}/events", headers=headers)
            baseline.raise_for_status()
            baseline_events = baseline.json()
            first_order_submitted = sum(1 for event in baseline_events if event["type"] == "order_submitted")
            first_fills = sum(1 for event in baseline_events if event["type"] == "fill")

            _duplicate_stream, duplicate_stream_id = publish_local_paper_cross_fixture(redis, event_prefix="paper-bot-bar")
            wait_for_runtime(
                api,
                headers,
                runtime["id"],
                lambda payload: (payload.get("stream_cursor") or {}).get(stream) == duplicate_stream_id,
            )
            time.sleep(1)
            deduped = api.get(f"/v1/nautilus/runtimes/{runtime['id']}/events", headers=headers)
            deduped.raise_for_status()
            deduped_events = deduped.json()
            assert sum(1 for event in deduped_events if event["type"] == "order_submitted") == first_order_submitted
            assert sum(1 for event in deduped_events if event["type"] == "fill") == first_fills
            write_json(
                "paper-bot-redis-streams.json",
                {
                    "stream": stream,
                    "length": redis.xlen(stream),
                    "first_stream_id": stream_id,
                    "duplicate_stream_id": duplicate_stream_id,
                },
            )
        finally:
            redis.close()

        runtime_rows = db_rows(
            """
            SELECT id, state, desired_state, worker_id, heartbeat_count, stream_cursor_json
            FROM nautilus_runtimes
            WHERE id = %s
            """,
            (runtime["id"],),
        )
        assert runtime_rows and runtime_rows[0]["worker_id"]
        write_json("paper-bot-db-runtime.json", runtime_rows)

        stopped = api.post(
            f"/v1/nautilus/runtimes/{runtime['id']}/stop",
            headers={**headers, "Idempotency-Key": "paper-bot-stop-1"},
        )
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["state"] == "stopping"
        assert stopped.json()["desired_state"] == "stopping"
        stop_events = wait_for_runtime_events(api, headers, runtime["id"], {"stop_requested"})
        assert any(event["type"] == "stop_requested" for event in stop_events)
