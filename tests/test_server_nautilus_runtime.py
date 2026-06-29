from datetime import UTC
from datetime import datetime
from datetime import timedelta

from fastapi.testclient import TestClient

from strategy_codebot.server import create_app
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.database import create_sqlite_repository
from strategy_codebot.server.repository import InMemoryConversationRepository
from tests.test_server_security_cost_controls import FakeRedis


AUTH_A = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-a"}
AUTH_B = {"X-User-Id": "user-b", "X-Workspace-Id": "workspace-a"}


def runtime_payload(strategy_id: str = "strategy-1", *, account_id: str = "acct-1") -> dict:
    return {
        "broker_connection_id": "paper-binance",
        "account_id": account_id,
        "mode": "paper",
        "risk_policy_id": "risk-basic",
        "strategy_id": strategy_id,
        "manifest": {"artifact_bundle": "bundle-1"},
        "data_subscriptions": [{"venue": "BINANCE", "symbol": "BTCUSDT", "timeframe": "1m"}],
    }


def test_nautilus_runtime_start_groups_strategies_by_runtime_key() -> None:
    client = TestClient(create_app(repository=create_sqlite_repository()))

    first = client.post("/v1/nautilus/runtimes", headers=AUTH_A, json=runtime_payload("strategy-1"))
    assert first.status_code == 201, first.text
    first_body = first.json()
    assert first_body["state"] == "requested"
    assert first_body["strategy_ids"] == ["strategy-1"]

    second = client.post("/v1/nautilus/runtimes", headers=AUTH_A, json=runtime_payload("strategy-2"))
    assert second.status_code == 201, second.text
    second_body = second.json()
    assert second_body["id"] == first_body["id"]
    assert second_body["strategy_ids"] == ["strategy-1", "strategy-2"]

    runtimes = client.get("/v1/nautilus/runtimes", headers=AUTH_A).json()["items"]
    assert [runtime["id"] for runtime in runtimes] == [first_body["id"]]

    other_account = client.post(
        "/v1/nautilus/runtimes",
        headers=AUTH_A,
        json=runtime_payload("strategy-3", account_id="acct-2"),
    )
    assert other_account.status_code == 201, other_account.text
    assert other_account.json()["id"] != first_body["id"]


def test_nautilus_runtime_api_is_tenant_isolated_and_live_blocked() -> None:
    client = TestClient(create_app(repository=create_sqlite_repository()))
    runtime = client.post("/v1/nautilus/runtimes", headers=AUTH_A, json=runtime_payload()).json()

    assert client.get(f"/v1/nautilus/runtimes/{runtime['id']}", headers=AUTH_A).status_code == 200
    assert client.get(f"/v1/nautilus/runtimes/{runtime['id']}", headers=AUTH_B).status_code == 404
    assert client.post(f"/v1/nautilus/runtimes/{runtime['id']}/heartbeat", headers=AUTH_B, json={}).status_code == 404

    live_payload = runtime_payload()
    live_payload["mode"] = "live"
    live = client.post("/v1/nautilus/runtimes", headers=AUTH_A, json=live_payload)
    assert live.status_code == 403
    assert "disabled" in live.json()["detail"]


def test_nautilus_runtime_heartbeat_events_and_kill_switch() -> None:
    client = TestClient(create_app(repository=create_sqlite_repository()))
    runtime = client.post("/v1/nautilus/runtimes", headers=AUTH_A, json=runtime_payload()).json()

    heartbeat = client.post(
        f"/v1/nautilus/runtimes/{runtime['id']}/heartbeat",
        headers=AUTH_A,
        json={"metrics": {"market_data_lag_ms": 12}},
    )
    assert heartbeat.status_code == 200, heartbeat.text
    assert heartbeat.json()["state"] == "running"
    assert heartbeat.json()["last_heartbeat_at"] is not None
    assert heartbeat.json()["heartbeat_count"] == 1
    assert heartbeat.json()["heartbeat_metrics"] == {"status": "ok", "metrics": {"market_data_lag_ms": 12}}

    event = client.post(
        f"/v1/nautilus/runtimes/{runtime['id']}/events",
        headers=AUTH_A,
        json={"type": "order_intent", "payload": {"side": "BUY", "quantity": 1}},
    )
    assert event.status_code == 200, event.text
    assert event.json()["event_appended"] is True
    assert event.json()["event"]["sequence"] == 3

    kill = client.post(
        f"/v1/nautilus/runtimes/{runtime['id']}/kill-switch",
        headers=AUTH_A,
        json={"reason": "manual operator stop"},
    )
    assert kill.status_code == 200, kill.text
    assert kill.json()["state"] == "stopping"
    assert kill.json()["kill_switch_active"] is True

    post_kill_heartbeat = client.post(
        f"/v1/nautilus/runtimes/{runtime['id']}/heartbeat",
        headers=AUTH_A,
        json={"metrics": {"market_data_lag_ms": 8}},
    )
    assert post_kill_heartbeat.status_code == 200, post_kill_heartbeat.text
    assert post_kill_heartbeat.json()["state"] == "stopping"
    assert post_kill_heartbeat.json()["heartbeat_count"] == 2

    events = client.get(f"/v1/nautilus/runtimes/{runtime['id']}/events", headers=AUTH_A).json()
    assert [event["type"] for event in events] == [
        "strategy_loaded",
        "heartbeat",
        "order_intent",
        "risk_block",
    ]


def test_nautilus_runtime_heartbeat_preserves_warming_up_state() -> None:
    client = TestClient(create_app(repository=create_sqlite_repository()))
    runtime = client.post("/v1/nautilus/runtimes", headers=AUTH_A, json=runtime_payload()).json()

    warming = client.post(
        f"/v1/nautilus/runtimes/{runtime['id']}/heartbeat",
        headers=AUTH_A,
        json={"metrics": {"paper_engine": "nautilus_local_paper", "warmup_status": "warming_up"}},
    )
    assert warming.status_code == 200, warming.text
    assert warming.json()["state"] == "warming_up"

    running = client.post(
        f"/v1/nautilus/runtimes/{runtime['id']}/heartbeat",
        headers=AUTH_A,
        json={"metrics": {"paper_engine": "nautilus_local_paper", "warmup_status": "complete"}},
    )
    assert running.status_code == 200, running.text
    assert running.json()["state"] == "running"


def test_nautilus_runtime_generic_heartbeat_updates_health() -> None:
    client = TestClient(create_app(repository=create_sqlite_repository()))
    runtime = client.post("/v1/nautilus/runtimes", headers=AUTH_A, json=runtime_payload()).json()

    heartbeat = client.post(
        f"/v1/nautilus/runtimes/{runtime['id']}/events",
        headers=AUTH_A,
        json={"type": "heartbeat", "payload": {"source": "worker"}},
    )
    assert heartbeat.status_code == 200, heartbeat.text
    assert heartbeat.json()["event_appended"] is True
    assert heartbeat.json()["runtime"]["heartbeat_count"] == 1

    refreshed = client.get(f"/v1/nautilus/runtimes/{runtime['id']}", headers=AUTH_A).json()
    assert refreshed["state"] == "running"
    assert refreshed["last_heartbeat_at"] is not None

    replay = client.get(f"/v1/nautilus/runtimes/{runtime['id']}/events?after_sequence=1", headers=AUTH_A)
    assert replay.status_code == 200, replay.text
    assert [event["type"] for event in replay.json()] == ["heartbeat"]


def test_nautilus_runtime_heartbeat_throttles_event_samples() -> None:
    client = TestClient(create_app(repository=create_sqlite_repository()))
    runtime = client.post("/v1/nautilus/runtimes", headers=AUTH_A, json=runtime_payload()).json()

    first = client.post(f"/v1/nautilus/runtimes/{runtime['id']}/heartbeat", headers=AUTH_A, json={})
    second = client.post(f"/v1/nautilus/runtimes/{runtime['id']}/heartbeat", headers=AUTH_A, json={})

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["heartbeat_count"] == 2

    events = client.get(f"/v1/nautilus/runtimes/{runtime['id']}/events", headers=AUTH_A).json()
    assert [event["type"] for event in events] == ["strategy_loaded", "heartbeat"]


def test_nautilus_runtime_start_and_event_ingest_are_idempotent() -> None:
    client = TestClient(create_app(repository=create_sqlite_repository(), redis_client=FakeRedis()))
    start_headers = {**AUTH_A, "Idempotency-Key": "runtime-start-1"}

    first = client.post("/v1/nautilus/runtimes", headers=start_headers, json=runtime_payload())
    second = client.post("/v1/nautilus/runtimes", headers=start_headers, json=runtime_payload())

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.json() == first.json()
    runtime_id = first.json()["id"]
    assert [event["type"] for event in client.get(f"/v1/nautilus/runtimes/{runtime_id}/events", headers=AUTH_A).json()] == [
        "strategy_loaded"
    ]

    event_headers = {**AUTH_A, "Idempotency-Key": "runtime-event-1"}
    first_event = client.post(
        f"/v1/nautilus/runtimes/{runtime_id}/events",
        headers=event_headers,
        json={"type": "order_intent", "payload": {"side": "BUY"}},
    )
    second_event = client.post(
        f"/v1/nautilus/runtimes/{runtime_id}/events",
        headers=event_headers,
        json={"type": "order_intent", "payload": {"side": "BUY"}},
    )

    assert first_event.status_code == 200, first_event.text
    assert second_event.status_code == 200, second_event.text
    assert second_event.json() == first_event.json()
    events = client.get(f"/v1/nautilus/runtimes/{runtime_id}/events", headers=AUTH_A).json()
    assert [event["type"] for event in events] == ["strategy_loaded", "order_intent"]


def test_nautilus_runtime_start_event_idempotency_survives_reexecution() -> None:
    client = TestClient(create_app(repository=create_sqlite_repository()))
    headers = {**AUTH_A, "Idempotency-Key": "runtime-start-1"}

    first = client.post("/v1/nautilus/runtimes", headers=headers, json=runtime_payload())
    second = client.post("/v1/nautilus/runtimes", headers=headers, json=runtime_payload())

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    events = client.get(f"/v1/nautilus/runtimes/{first.json()['id']}/events", headers=AUTH_A).json()
    assert [event["type"] for event in events] == ["strategy_loaded"]


def test_nautilus_runtime_stop_records_neutral_lifecycle_event() -> None:
    client = TestClient(create_app(repository=create_sqlite_repository()))
    runtime = client.post("/v1/nautilus/runtimes", headers=AUTH_A, json=runtime_payload()).json()

    stopped = client.post(f"/v1/nautilus/runtimes/{runtime['id']}/stop", headers=AUTH_A)
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["state"] == "stopping"

    events = client.get(f"/v1/nautilus/runtimes/{runtime['id']}/events", headers=AUTH_A).json()
    assert [event["type"] for event in events] == ["strategy_loaded", "stop_requested"]


def test_nautilus_runtime_cleanup_removes_only_excess_heartbeat_samples() -> None:
    repository = create_sqlite_repository()
    auth = AuthContext("user-a", "workspace-a")
    runtime = repository.upsert_nautilus_runtime(
        auth,
        runtime_key="runtime-key",
        broker_connection_id="paper-binance",
        account_id="acct-1",
        mode="paper",
        risk_policy_id="risk-basic",
        strategy_id="strategy-1",
        manifest_json={},
        data_subscriptions_json=[],
    )
    for index in range(5):
        repository.append_nautilus_runtime_event(auth, runtime.id, "heartbeat", {"index": index})
    repository.append_nautilus_runtime_event(auth, runtime.id, "risk_block", {"reason": "limit"})

    removed = repository.cleanup_nautilus_heartbeat_events(auth, runtime.id, max_samples=2)

    assert removed == 3
    events = repository.list_nautilus_runtime_events(auth, runtime.id, limit=10)
    assert events is not None
    assert [event.type for event in events] == ["heartbeat", "heartbeat", "risk_block"]


def test_nautilus_runtime_load_simulation_bounds_heartbeat_event_writes() -> None:
    repository = InMemoryConversationRepository()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    runtime_ids: list[str] = []

    for index in range(1000):
        auth = AuthContext(f"user-{index}", "workspace-a")
        runtime = repository.upsert_nautilus_runtime(
            auth,
            runtime_key=f"user-{index}:paper-binance:acct-{index}:paper:risk-basic",
            broker_connection_id="paper-binance",
            account_id=f"acct-{index}",
            mode="paper",
            risk_policy_id="risk-basic",
            strategy_id=f"strategy-{index}",
            manifest_json={},
            data_subscriptions_json=[{"venue": "BINANCE", "symbol": "BTCUSDT", "timeframe": "1m"}],
        )
        runtime_ids.append(runtime.id)

    for offset in range(40):
        now = base + timedelta(seconds=offset * 15)
        for index, runtime_id in enumerate(runtime_ids):
            auth = AuthContext(f"user-{index}", "workspace-a")
            heartbeat = repository.record_nautilus_runtime_heartbeat(
                auth,
                runtime_id,
                now=now,
                payload={"market_data_lag_ms": offset},
            )
            assert heartbeat is not None

    heartbeat_event_count = 0
    for index, runtime_id in enumerate(runtime_ids):
        auth = AuthContext(f"user-{index}", "workspace-a")
        runtime = repository.get_nautilus_runtime(auth, runtime_id)
        events = repository.list_nautilus_runtime_events(auth, runtime_id, limit=10)
        assert runtime is not None
        assert events is not None
        assert runtime.heartbeat_count == 40
        heartbeat_event_count += sum(1 for event in events if event.type == "heartbeat")

    assert heartbeat_event_count == 2000
