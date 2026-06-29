from __future__ import annotations

from typing import Any

from .helpers import auth
from .helpers import client
from .helpers import db_rows
from .helpers import local_paper_runtime_payload
from .helpers import publish_local_paper_cross_fixture
from .helpers import redis_client
from .helpers import wait_for_exchange_collector_bars
from .helpers import wait_for_runtime
from .helpers import wait_for_runtime_events
from .helpers import write_json


def test_native_nautilus_paper_runtime_executes_generated_strategy() -> None:
    headers = auth(workspace="e2e-paper-bot-native")

    with client(timeout=60.0) as api:
        start = api.post(
            "/v1/nautilus/runtimes",
            headers={**headers, "Idempotency-Key": "paper-bot-native-start-1"},
            json=_runtime_payload(),
        )
        assert start.status_code == 201, start.text
        runtime = start.json()
        assert runtime["mode"] == "paper"
        assert runtime["desired_state"] == "running"

        redis = redis_client()
        try:
            stream, exchange_last_stream_id, exchange_bars = wait_for_exchange_collector_bars(redis, min_count=5)
        finally:
            redis.close()
        assert len(exchange_bars) >= 5

        running = wait_for_runtime(
            api,
            headers,
            runtime["id"],
            lambda payload: _stream_id_gte((payload.get("stream_cursor") or {}).get(stream), exchange_last_stream_id)
            and payload["heartbeat_count"] > 0
            and (payload.get("heartbeat_metrics") or {}).get("metrics", {}).get("paper_engine") == "nautilus_local_paper"
            and (payload.get("heartbeat_metrics") or {}).get("metrics", {}).get("engine") == "nautilus_trading_node"
            and (payload.get("heartbeat_metrics") or {}).get("metrics", {}).get("indicator_owner") == "nautilus"
            and (payload.get("heartbeat_metrics") or {}).get("metrics", {}).get("strategy_callback_owner") == "nautilus"
            and (payload.get("heartbeat_metrics") or {}).get("metrics", {}).get("order_owner") == "nautilus"
            and (payload.get("heartbeat_metrics") or {}).get("metrics", {}).get("execution_owner") == "nautilus_sandbox"
            and (payload.get("heartbeat_metrics") or {}).get("metrics", {}).get("fill_owner") == "nautilus"
            and (payload.get("heartbeat_metrics") or {}).get("metrics", {}).get("position_owner") == "nautilus"
            and (payload.get("heartbeat_metrics") or {}).get("metrics", {}).get("pnl_owner") == "nautilus"
            and (payload.get("heartbeat_metrics") or {}).get("metrics", {}).get("warmup_status") == "complete",
            timeout_seconds=120,
        )
        assert running["worker_id"]
        warmup_metrics = running["heartbeat_metrics"]["metrics"]
        assert warmup_metrics["required_warmup_bars"] == 3
        assert warmup_metrics["warmup_bar_count"] >= 3
        assert warmup_metrics["processed_bar_count"] >= 3

        fixture_event_prefix = f"native-bar-{runtime['id']}"
        fixture_redis = redis_client()
        try:
            _fixture_stream, fixture_last_stream_id = publish_local_paper_cross_fixture(
                fixture_redis,
                event_prefix=fixture_event_prefix,
            )
        finally:
            fixture_redis.close()
        wait_for_runtime(
            api,
            headers,
            runtime["id"],
            lambda payload: _stream_id_gte((payload.get("stream_cursor") or {}).get(stream), fixture_last_stream_id)
            and (payload.get("heartbeat_metrics") or {}).get("metrics", {}).get("warmup_status") == "complete",
            timeout_seconds=120,
        )

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
                "order_intent",
                "order_submitted",
                "fill",
                "position_snapshot",
                "pnl_snapshot",
            },
            timeout_seconds=120,
        )
        event_types = [event["type"] for event in events]
        assert "runtime_error" not in event_types
        native_events = [event for event in events if event["type"] in {"signal", "order_intent", "order_submitted", "fill"}]
        assert native_events
        assert all((event.get("payload") or {}).get("source") == "nautilus_local_paper" for event in native_events)
        assert all((event.get("payload") or {}).get("status") != "FILLED" for event in native_events)
        assert sum(1 for event in events if event["type"] == "order_submitted") >= 1
        assert sum(1 for event in events if event["type"] == "fill") >= 1

        duplicate_redis = redis_client()
        try:
            _duplicate_stream, duplicate_last_stream_id = publish_local_paper_cross_fixture(
                duplicate_redis,
                event_prefix=fixture_event_prefix,
            )
        finally:
            duplicate_redis.close()
        wait_for_runtime(
            api,
            headers,
            runtime["id"],
            lambda payload: _stream_id_gte((payload.get("stream_cursor") or {}).get(stream), duplicate_last_stream_id),
            timeout_seconds=120,
        )
        deduped = api.get(f"/v1/nautilus/runtimes/{runtime['id']}/events", headers=headers)
        deduped.raise_for_status()
        deduped_events = deduped.json()
        submitted_event_ids = [
            event_id
            for event_id in _market_event_ids(deduped_events, event_type="order_submitted")
            if event_id.startswith(fixture_event_prefix)
        ]
        fill_event_ids = [
            event_id
            for event_id in _market_event_ids(deduped_events, event_type="fill")
            if event_id.startswith(fixture_event_prefix)
        ]
        assert len(submitted_event_ids) == len(set(submitted_event_ids))
        assert len(fill_event_ids) == len(set(fill_event_ids))

        runtime_rows = db_rows(
            """
            SELECT id, state, desired_state, worker_id, heartbeat_count, stream_cursor_json, heartbeat_metrics_json
            FROM nautilus_runtimes
            WHERE id = %s
            """,
            (runtime["id"],),
        )
        assert runtime_rows and runtime_rows[0]["worker_id"]
        assert runtime_rows[0]["heartbeat_metrics_json"]["metrics"]["paper_engine"] == "nautilus_local_paper"
        assert runtime_rows[0]["heartbeat_metrics_json"]["metrics"]["indicator_owner"] == "nautilus"
        assert runtime_rows[0]["heartbeat_metrics_json"]["metrics"]["strategy_callback_owner"] == "nautilus"
        assert runtime_rows[0]["heartbeat_metrics_json"]["metrics"]["execution_owner"] == "nautilus_sandbox"
        assert runtime_rows[0]["heartbeat_metrics_json"]["metrics"]["fill_owner"] == "nautilus"
        assert runtime_rows[0]["heartbeat_metrics_json"]["metrics"]["position_owner"] == "nautilus"
        assert runtime_rows[0]["heartbeat_metrics_json"]["metrics"]["pnl_owner"] == "nautilus"
        assert runtime_rows[0]["heartbeat_metrics_json"]["metrics"]["warmup_status"] == "complete"
        write_json("paper-bot-native-db-runtime.json", runtime_rows)
        write_json("paper-bot-native-events.json", deduped_events)


def _runtime_payload() -> dict[str, Any]:
    return local_paper_runtime_payload(
        broker_connection_id="paper-binance-native-e2e",
        account_id="acct-paper-native-e2e",
        risk_policy_id="risk-native-e2e",
        strategy_id="native-paper-strategy-1",
        source="docker_e2e_native_strategy_spec",
    )


def _market_event_ids(events: list[dict[str, Any]], *, event_type: str) -> list[str]:
    ids: list[str] = []
    for event in events:
        if event.get("type") != event_type:
            continue
        market_data = (event.get("payload") or {}).get("market_data") or {}
        event_id = market_data.get("event_id")
        if event_id:
            ids.append(str(event_id))
    return ids


def _stream_id_gte(value: Any, expected: str) -> bool:
    if not value:
        return False
    left_ms, left_seq = str(value).split("-", 1)
    right_ms, right_seq = expected.split("-", 1)
    return (int(left_ms), int(left_seq)) >= (int(right_ms), int(right_seq))
