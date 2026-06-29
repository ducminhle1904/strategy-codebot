from datetime import UTC
from datetime import datetime
from datetime import timedelta
import importlib.util
from typing import Any

import pytest

from strategy_codebot.nautilus_streams import MarketDataStreamSubscription
from strategy_codebot.nautilus_streams import decode_stream_fields
from strategy_codebot.nautilus_streams import encode_stream_fields
from strategy_codebot.nautilus_streams import market_data_stream_key
from strategy_codebot.nautilus_streams import runtime_command_stream_key
from strategy_codebot.server.market_data_collector import CcxtOhlcvAdapter
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.database import create_sqlite_repository
from strategy_codebot.server.market_data_collector import MarketDataCollector
from strategy_codebot.server.market_data_collector import NormalizedBar
from strategy_codebot.server.market_data_collector import default_exchange_adapter_registry
from strategy_codebot.server.nautilus_native_runner import NativeMarketMessage
from strategy_codebot.server.nautilus_native_runner import NativeNautilusExecutionRunner
from strategy_codebot.server.nautilus_native_runner import NativeRunResult
from strategy_codebot.server.nautilus_paper_worker import NautilusPaperRuntimeRunner
from strategy_codebot.server.nautilus_paper_worker import NautilusPaperWorker
from strategy_codebot.server.nautilus_paper_worker import NautilusPaperWorkerConfig


NAUTILUS_AVAILABLE = importlib.util.find_spec("nautilus_trader") is not None


class FakeRedisStreams:
    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.values: dict[str, str] = {}

    def xadd(
        self,
        name: str,
        fields: dict[str, str],
        *,
        id: str = "*",
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        _ = approximate
        entries = self.streams.setdefault(name, [])
        entry_id = f"{len(entries) + 1}-0" if id == "*" else id
        entries.append((entry_id, fields))
        if maxlen is not None and len(entries) > maxlen:
            del entries[: len(entries) - maxlen]
        return entry_id

    def xread(
        self,
        streams: dict[str, str],
        *,
        count: int | None = None,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        _ = block
        results: list[tuple[str, list[tuple[str, dict[str, str]]]]] = []
        for stream, last_id in streams.items():
            entries = [(entry_id, fields) for entry_id, fields in self.streams.get(stream, []) if _stream_id_gt(entry_id, last_id)]
            if count is not None:
                entries = entries[:count]
            if entries:
                results.append((stream, entries))
        return results

    def xrange(self, name: str, min: str = "-", max: str = "+"):
        _ = min, max
        return list(self.streams.get(name, []))

    def xrevrange(self, name: str, max: str = "+", min: str = "-", count: int | None = None):
        _ = min
        entries = [
            (entry_id, fields)
            for entry_id, fields in self.streams.get(name, [])
            if max == "+" or not _stream_id_gt(entry_id, max)
        ]
        entries = list(reversed(entries))
        if count is not None:
            entries = entries[:count]
        return entries

    def get(self, name: str):
        return self.values.get(name)

    def set(self, name: str, value: str, *, ex: int | None = None, nx: bool = False):
        _ = ex
        if nx and name in self.values:
            return False
        self.values[name] = value
        return True

    def expire(self, name: str, seconds: int) -> bool:
        _ = seconds
        return name in self.values


def test_nautilus_stream_keys_match_tick_and_bar_contract() -> None:
    assert market_data_stream_key(venue="binance", symbol="btcusdt", data_type="tick") == "md:BINANCE:BTCUSDT:ticks"
    assert (
        market_data_stream_key(venue="binance", symbol="btcusdt", data_type="bar", timeframe="1m")
        == "md:BINANCE:BTCUSDT:1m:bars"
    )
    assert (
        MarketDataStreamSubscription.from_payload({"venue": "binance", "symbol": "btcusdt", "timeframe": "1m"}).stream_key()
        == "md:BINANCE:BTCUSDT:1m:bars"
    )


def test_nautilus_runtime_lease_claim_renew_release_and_stale_takeover() -> None:
    repository = create_sqlite_repository()
    auth = AuthContext("user-a", "workspace-a")
    runtime = _runtime(repository, auth)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    claimed = repository.claim_nautilus_runtime_lease(runtime.id, worker_id="worker-a", now=now, lease_seconds=30)
    assert claimed is not None
    assert claimed.worker_id == "worker-a"
    assert claimed.lease_until == now + timedelta(seconds=30)
    assert claimed.generation == 1
    assert repository.claim_nautilus_runtime_lease(runtime.id, worker_id="worker-b", now=now, lease_seconds=30) is None

    renewed = repository.renew_nautilus_runtime_lease(runtime.id, worker_id="worker-a", now=now, lease_seconds=60)
    assert renewed is not None
    assert renewed.lease_until == now + timedelta(seconds=60)
    same_worker_claim = repository.claim_nautilus_runtime_lease(
        runtime.id,
        worker_id="worker-a",
        now=now + timedelta(seconds=1),
        lease_seconds=30,
    )
    assert same_worker_claim is not None
    assert same_worker_claim.generation == 1

    takeover = repository.claim_nautilus_runtime_lease(
        runtime.id,
        worker_id="worker-b",
        now=now + timedelta(seconds=61),
        lease_seconds=30,
    )
    assert takeover is not None
    assert takeover.worker_id == "worker-b"
    assert takeover.generation == 2

    released = repository.release_nautilus_runtime_lease(runtime.id, worker_id="worker-b", state="stopped", now=now)
    assert released is not None
    assert released.worker_id is None
    assert released.lease_until is None
    assert released.stopped_at == now


def test_nautilus_paper_worker_consumes_bar_stream_via_local_paper_runner_and_is_idempotent() -> None:
    repository = create_sqlite_repository()
    redis = FakeRedisStreams()
    auth = AuthContext("user-a", "workspace-a")
    runtime = _runtime(repository, auth)
    lease = repository.claim_nautilus_runtime_lease(runtime.id, worker_id="worker-a", lease_seconds=30)
    assert lease is not None
    collector = MarketDataCollector(redis)
    stream_id = collector.publish_bar(
        venue="BINANCE",
        symbol="BTCUSDT",
        timeframe="1m",
        open=100,
        high=110,
        low=90,
        close=105,
        volume=12,
        closed=True,
        event_id="bar-1",
    )

    native_runner = FakeNativeRunner()
    runner = NautilusPaperRuntimeRunner(
        repository=repository,
        redis_client=redis,
        worker_id="worker-a",
        stream_block_ms=0,
        native_runner=native_runner,
    )
    first = runner.run_once(lease)
    second = runner.run_once(first)

    assert second is not None
    assert second.stream_cursor_json == {"md:BINANCE:BTCUSDT:1m:bars": stream_id}
    assert second.heartbeat_metrics_json["metrics"]["paper_engine"] == "nautilus_local_paper"
    assert [message.source_event_id for message in native_runner.messages] == ["bar-1"]
    events = repository.list_nautilus_runtime_events(auth, runtime.id, limit=20)
    assert events is not None
    assert [event.type for event in events] == ["order_submitted", "heartbeat"]
    assert repository.list_nautilus_runtime_events(auth, runtime.id, limit=20) == events

    duplicate_stream_id = collector.publish_bar(
        venue="BINANCE",
        symbol="BTCUSDT",
        timeframe="1m",
        open=100,
        high=110,
        low=90,
        close=105,
        volume=12,
        closed=True,
        event_id="bar-1",
    )
    third = runner.run_once(second)

    assert third is not None
    assert third.stream_cursor_json == {"md:BINANCE:BTCUSDT:1m:bars": duplicate_stream_id}
    assert repository.list_nautilus_runtime_events(auth, runtime.id, limit=20) == events


def test_nautilus_paper_worker_delegates_to_local_paper_runner() -> None:
    repository = create_sqlite_repository()
    redis = FakeRedisStreams()
    auth = AuthContext("user-a", "workspace-a")
    runtime = _runtime(repository, auth)
    lease = repository.claim_nautilus_runtime_lease(runtime.id, worker_id="worker-a", lease_seconds=30)
    assert lease is not None
    collector = MarketDataCollector(redis)
    stream_id = collector.publish_bar(
        venue="BINANCE",
        symbol="BTCUSDT",
        timeframe="1m",
        open=100,
        high=110,
        low=90,
        close=105,
        volume=12,
        closed=True,
        event_id="native-bar-1",
    )
    native_runner = FakeNativeRunner()
    runner = NautilusPaperRuntimeRunner(
        repository=repository,
        redis_client=redis,
        worker_id="worker-a",
        stream_block_ms=0,
        native_runner=native_runner,
    )

    updated = runner.run_once(lease)

    assert updated is not None
    assert updated.stream_cursor_json == {"md:BINANCE:BTCUSDT:1m:bars": stream_id}
    assert updated.heartbeat_metrics_json["metrics"]["paper_engine"] == "nautilus_local_paper"
    assert updated.heartbeat_metrics_json["metrics"]["order_count"] == 1
    assert [message.source_event_id for message in native_runner.messages] == ["native-bar-1"]
    events = repository.list_nautilus_runtime_events(auth, runtime.id, limit=20)
    assert events is not None
    assert [event.type for event in events] == ["order_submitted", "heartbeat"]
    assert events[0].payload["source"] == "fake_native"


def test_nautilus_paper_worker_persists_all_events_before_cursor_advance() -> None:
    repository = create_sqlite_repository()
    redis = FakeRedisStreams()
    auth = AuthContext("user-a", "workspace-a")
    runtime = _runtime(repository, auth)
    lease = repository.claim_nautilus_runtime_lease(runtime.id, worker_id="worker-a", lease_seconds=30)
    assert lease is not None
    collector = MarketDataCollector(redis)
    stream_id = collector.publish_bar(
        venue="BINANCE",
        symbol="BTCUSDT",
        timeframe="1m",
        open=100,
        high=110,
        low=90,
        close=105,
        volume=12,
        closed=True,
        event_id="chunk-bar-1",
    )
    runner = NautilusPaperRuntimeRunner(
        repository=repository,
        redis_client=redis,
        worker_id="worker-a",
        stream_block_ms=0,
        event_batch_size=2,
        native_runner=ManyEventsRunner(event_count=5),
    )

    updated = runner.run_once(lease)

    assert updated is not None
    assert updated.stream_cursor_json == {"md:BINANCE:BTCUSDT:1m:bars": stream_id}
    events = repository.list_nautilus_runtime_events(auth, runtime.id, limit=20)
    assert events is not None
    assert [event.type for event in events] == [
        "order_submitted",
        "order_submitted",
        "order_submitted",
        "order_submitted",
        "order_submitted",
        "heartbeat",
    ]


def test_nautilus_paper_worker_backfills_warmup_from_redis_history_when_cursor_is_latest() -> None:
    repository = create_sqlite_repository()
    redis = FakeRedisStreams()
    auth = AuthContext("user-a", "workspace-a")
    runtime = _runtime(repository, auth, manifest_json=_native_manifest())
    lease = repository.claim_nautilus_runtime_lease(runtime.id, worker_id="worker-a", lease_seconds=30)
    assert lease is not None
    collector = MarketDataCollector(redis)
    stream_id = "0-0"
    for index, close in enumerate([100, 99, 98], start=1):
        stream_id = collector.publish_bar(
            venue="BINANCE",
            symbol="BTCUSDT",
            timeframe="1m",
            open=close - 1,
            high=close + 1,
            low=close - 2,
            close=close,
            volume=12,
            closed=True,
            event_id=f"history-{index}",
        )
    with_cursor = repository.persist_nautilus_runtime_stream_cursor(
        runtime.id,
        worker_id="worker-a",
        stream_cursor_json={"md:BINANCE:BTCUSDT:1m:bars": stream_id},
    )
    assert with_cursor is not None
    native_runner = WarmupRecoveryRunner(required_bars=3)
    runner = NautilusPaperRuntimeRunner(
        repository=repository,
        redis_client=redis,
        worker_id="worker-a",
        stream_block_ms=0,
        native_runner=native_runner,
    )

    updated = runner.run_once(with_cursor)

    assert updated is not None
    assert updated.stream_cursor_json == {"md:BINANCE:BTCUSDT:1m:bars": stream_id}
    assert updated.heartbeat_metrics_json["metrics"]["warmup_status"] == "complete"
    assert native_runner.batches == [[], ["history-1", "history-2", "history-3"]]
    events = repository.list_nautilus_runtime_events(auth, runtime.id, limit=20)
    assert events is not None
    assert [event.type for event in events] == ["warmup_completed", "heartbeat"]


def test_nautilus_paper_worker_claims_desired_runtime_and_heartbeats() -> None:
    repository = create_sqlite_repository()
    redis = FakeRedisStreams()
    auth = AuthContext("user-a", "workspace-a")
    runtime = _runtime(repository, auth)
    config = NautilusPaperWorkerConfig(
        worker_id="worker-a",
        database_url="sqlite+pysqlite:///:memory:",
        redis_url="redis://localhost:6379/0",
        stream_block_ms=0,
    )

    claimed = NautilusPaperWorker(repository=repository, redis_client=redis, config=config).run_once()

    assert claimed == 1
    updated = repository.get_nautilus_runtime(auth, runtime.id)
    assert updated is not None
    assert updated.worker_id == "worker-a"
    assert updated.state == "warming_up"
    assert updated.heartbeat_count == 1


def test_nautilus_paper_worker_finalizes_stopping_runtime_owned_by_worker() -> None:
    repository = create_sqlite_repository()
    redis = FakeRedisStreams()
    auth = AuthContext("user-a", "workspace-a")
    runtime = _runtime(repository, auth)
    lease = repository.claim_nautilus_runtime_lease(runtime.id, worker_id="worker-a", lease_seconds=30)
    assert lease is not None
    assert repository.set_nautilus_runtime_state(auth, runtime.id, state="stopping") is not None
    assert repository.set_nautilus_runtime_desired_state(auth, runtime.id, desired_state="stopping") is not None
    config = NautilusPaperWorkerConfig(
        worker_id="worker-a",
        database_url="sqlite+pysqlite:///:memory:",
        redis_url="redis://localhost:6379/0",
        stream_block_ms=0,
    )

    processed = NautilusPaperWorker(repository=repository, redis_client=redis, config=config).run_once()

    assert processed == 1
    stopped = repository.get_nautilus_runtime(auth, runtime.id)
    assert stopped is not None
    assert stopped.state == "stopped"
    assert stopped.desired_state == "stopping"
    assert stopped.worker_id is None
    assert stopped.lease_until is None
    assert stopped.stopped_at is not None


def test_nautilus_paper_worker_stop_command_releases_runtime_as_stopped() -> None:
    repository = create_sqlite_repository()
    redis = FakeRedisStreams()
    auth = AuthContext("user-a", "workspace-a")
    runtime = _runtime(repository, auth)
    lease = repository.claim_nautilus_runtime_lease(runtime.id, worker_id="worker-a", lease_seconds=30)
    assert lease is not None
    redis.xadd(
        runtime_command_stream_key(runtime.id),
        encode_stream_fields({"action": "stop", "reason": "operator stop"}),
    )
    runner = NautilusPaperRuntimeRunner(
        repository=repository,
        redis_client=redis,
        worker_id="worker-a",
        stream_block_ms=0,
        native_runner=FakeNativeRunner(),
    )

    stopped = runner.run_once(lease)

    assert stopped is not None
    assert stopped.state == "stopped"
    assert stopped.worker_id is None
    assert stopped.lease_until is None
    assert stopped.stopped_at is not None
    events = repository.list_nautilus_runtime_events(auth, runtime.id, limit=20)
    assert events is not None
    assert [event.type for event in events] == ["stop_requested"]


def test_market_data_collector_reuses_one_upstream_per_subscription() -> None:
    redis = FakeRedisStreams()
    collector = MarketDataCollector(redis)
    subscription = MarketDataStreamSubscription.from_payload(
        {"venue": "BINANCE", "symbol": "BTCUSDT", "data_type": "tick"}
    )

    for _ in range(100):
        collector.ensure_subscription(subscription)

    assert collector.upstream_collectors == {"md:BINANCE:BTCUSDT:ticks"}


def test_market_data_collector_discovers_and_dedupes_runtime_subscriptions() -> None:
    repository = create_sqlite_repository()
    redis = FakeRedisStreams()
    collector = MarketDataCollector(redis, repository=repository)
    auth_a = AuthContext("user-a", "workspace-a")
    auth_b = AuthContext("user-b", "workspace-b")
    _runtime(repository, auth_a, runtime_key="rk-a")
    _runtime(repository, auth_b, runtime_key="rk-b")

    subscriptions = collector.desired_subscriptions()

    assert [subscription.stream_key() for subscription in subscriptions] == ["md:BINANCE:BTCUSDT:1m:bars"]


def test_market_data_collector_redis_lease_allows_one_owner_per_stream() -> None:
    redis = FakeRedisStreams()
    stream = market_data_stream_key(venue="BINANCE", symbol="BTCUSDT", data_type="bar", timeframe="1m")

    assert MarketDataCollector(redis, worker_id="collector-a").acquire_stream_lease(stream)
    assert not MarketDataCollector(redis, worker_id="collector-b").acquire_stream_lease(stream)
    assert MarketDataCollector(redis, worker_id="collector-a").acquire_stream_lease(stream)


def test_market_data_collector_treats_failed_expire_as_lost_lease() -> None:
    redis = FakeRedisStreams()
    stream = market_data_stream_key(venue="BINANCE", symbol="BTCUSDT", data_type="bar", timeframe="1m")
    collector = MarketDataCollector(redis, worker_id="collector-a")
    assert collector.acquire_stream_lease(stream)
    redis.expire = lambda _name, _seconds: False  # type: ignore[method-assign]

    assert not collector.acquire_stream_lease(stream)


def test_market_data_collector_uses_persisted_exchange_cursor_for_next_poll() -> None:
    repository = create_sqlite_repository()
    auth = AuthContext("user-a", "workspace-a")
    _runtime(repository, auth)
    redis = FakeRedisStreams()
    adapter = FakeBarsAdapter()
    collector = MarketDataCollector(redis, repository=repository, adapters={"BINANCE": adapter})
    stream = market_data_stream_key(venue="BINANCE", symbol="BTCUSDT", data_type="bar", timeframe="1m")

    assert collector.poll_once(backfill_limit=8) == 1
    collector.next_poll_after_ms_by_stream[stream] = 0
    assert collector.poll_once(backfill_limit=8) == 0

    assert adapter.since_values[0] is None
    assert adapter.since_values[1] == datetime(2026, 1, 1, tzinfo=UTC)


def test_exchange_adapter_registry_supports_default_venues_and_rejects_ticks() -> None:
    registry = default_exchange_adapter_registry()

    assert set(registry) == {"BINANCE", "BYBIT", "OKX", "KRAKEN"}
    for venue, adapter in registry.items():
        assert adapter.supports(
            MarketDataStreamSubscription.from_payload(
                {"venue": venue, "symbol": "BTCUSDT", "data_type": "bar", "timeframe": "1m"}
            )
        )
        assert not adapter.supports(
            MarketDataStreamSubscription.from_payload({"venue": venue, "symbol": "BTCUSDT", "data_type": "tick"})
        )


def test_market_data_collector_publishes_normalized_exchange_bar_with_metadata() -> None:
    redis = FakeRedisStreams()
    collector = MarketDataCollector(redis)
    subscription = MarketDataStreamSubscription.from_payload(
        {"venue": "BINANCE", "symbol": "BTCUSDT", "data_type": "bar", "timeframe": "1m"}
    )
    adapter = CcxtOhlcvAdapter(venue="BINANCE", exchange_id="binance")
    bar = adapter._normalize_ohlcv(
        subscription,
        [1_700_000_000_000, "100", "102", "99", "101", "12.5"],
        now_ms=1_700_000_060_000,
    )
    assert bar is not None

    stream_id = collector.publish_normalized_bar(bar)

    assert stream_id == "1-0"
    stream = market_data_stream_key(venue="BINANCE", symbol="BTCUSDT", data_type="bar", timeframe="1m")
    payload = decode_stream_fields(redis.streams[stream][0][1])
    assert payload["event_id"] == "exchange_collector:binance:BTCUSDT:1m:1700000000000"
    assert payload["source"] == "exchange_collector"
    assert payload["exchange"] == "binance"
    assert payload["adapter"] == "ccxt_ohlcv_rest"
    assert payload["close"] == 101
    assert collector.publish_normalized_bar(bar) is None


def test_native_local_paper_session_warms_indicators_and_reuses_engine_incrementally() -> None:
    if not NAUTILUS_AVAILABLE:
        pytest.skip("nautilus-trader is required for Nautilus-owned paper session")
    repository = create_sqlite_repository()
    auth = AuthContext("user-a", "workspace-a")
    runtime = _runtime(repository, auth, manifest_json=_native_manifest())
    runner = NativeNautilusExecutionRunner()
    stream = market_data_stream_key(venue="BINANCE", symbol="BTCUSDT", data_type="bar", timeframe="1m")

    warmup = runner.run_bar_batch(runtime, _messages(stream, [100, 99, 98], prefix="warmup"))
    trade = runner.run_bar_batch(runtime, _messages(stream, [101, 105], prefix="trade"))
    next_batch = runner.run_bar_batch(runtime, _messages(stream, [106], prefix="next"))

    assert warmup.metrics["warmup_status"] == "complete"
    assert warmup.metrics["engine"] == "nautilus_trading_node"
    assert warmup.metrics["indicator_owner"] == "nautilus"
    assert warmup.metrics["strategy_callback_owner"] == "nautilus"
    assert warmup.metrics["order_owner"] == "nautilus"
    assert warmup.metrics["execution_owner"] == "nautilus_sandbox"
    assert warmup.metrics["fill_owner"] == "nautilus"
    assert warmup.metrics["position_owner"] == "nautilus"
    assert warmup.metrics["pnl_owner"] == "nautilus"
    assert trade.metrics["engine_init_count"] == 1
    assert trade.metrics["strategy_module_load_count"] == 1
    assert next_batch.metrics["engine_init_count"] == 1
    assert "BacktestEngine" not in str(trade.metrics)
    assert [event_type for event_type, _, _ in trade.events] == [
        "signal",
        "order_intent",
        "order_submitted",
        "fill",
        "position_snapshot",
        "pnl_snapshot",
    ]
    submitted = [payload for event_type, payload, _ in trade.events if event_type == "order_submitted"]
    assert submitted and submitted[0]["execution_owner"] == "nautilus_sandbox"
    assert "status" not in submitted[0]


def test_native_local_paper_tick_strategy_fails_closed_without_tick_adapter() -> None:
    repository = create_sqlite_repository()
    auth = AuthContext("user-a", "workspace-a")
    manifest = _native_manifest()
    manifest["strategy_spec"] = {**manifest["strategy_spec"], "execution_semantics": "on_every_tick"}
    runtime = _runtime(repository, auth, manifest_json=manifest)
    runner = NativeNautilusExecutionRunner()

    result = runner.run_bar_batch(
        runtime,
        [
            NativeMarketMessage(
                stream_name="md:BINANCE:BTCUSDT:ticks",
                stream_id="1-0",
                source_event_id="tick-1",
                payload={"event_id": "tick-1", "venue": "BINANCE", "symbol": "BTCUSDT", "last": 100},
            )
        ],
    )

    assert result.events[0][0] == "runtime_error"
    assert result.metrics["warmup_status"] == "failed"


def test_native_local_paper_tick_declared_strategy_fails_closed_on_bar_data() -> None:
    repository = create_sqlite_repository()
    auth = AuthContext("user-a", "workspace-a")
    manifest = _native_manifest()
    manifest["strategy_spec"] = {**manifest["strategy_spec"], "execution_semantics": "on_every_tick"}
    runtime = _runtime(repository, auth, manifest_json=manifest)
    runner = NativeNautilusExecutionRunner()
    stream = market_data_stream_key(venue="BINANCE", symbol="BTCUSDT", data_type="bar", timeframe="1m")

    result = runner.run_bar_batch(runtime, _messages(stream, [100, 99, 98], prefix="bar-tick-spec"))

    assert result.events[0][0] == "runtime_error"
    assert "tick-driven" in result.metrics["native_error_message"]


def test_nautilus_paper_worker_skips_idle_heartbeat_until_interval() -> None:
    repository = create_sqlite_repository()
    redis = FakeRedisStreams()
    auth = AuthContext("user-a", "workspace-a")
    runtime = _runtime(repository, auth)
    lease = repository.claim_nautilus_runtime_lease(runtime.id, worker_id="worker-a", lease_seconds=30)
    assert lease is not None
    runner = NautilusPaperRuntimeRunner(
        repository=repository,
        redis_client=redis,
        worker_id="worker-a",
        stream_block_ms=0,
        heartbeat_interval_seconds=60,
        native_runner=FakeNativeRunner(),
    )

    first = runner.run_once(lease)
    assert first is not None
    second = runner.run_once(first)

    assert second is not None
    assert first.heartbeat_count == 1
    assert second.heartbeat_count == 1


def _runtime(
    repository: Any,
    auth: AuthContext,
    *,
    runtime_key: str = "rk-test",
    manifest_json: dict[str, Any] | None = None,
):
    return repository.upsert_nautilus_runtime(
        auth,
        runtime_key=runtime_key,
        broker_connection_id="paper-binance",
        account_id="acct-1",
        mode="paper",
        risk_policy_id="risk-basic",
        strategy_id="strategy-1",
        manifest_json=manifest_json or {"paper_runtime": {"default_side": "BUY"}},
        data_subscriptions_json=[{"venue": "BINANCE", "symbol": "BTCUSDT", "timeframe": "1m", "data_type": "bar"}],
    )


def _native_manifest() -> dict[str, Any]:
    return {
        "strategy_spec": {
            "target_platform": "nautilus_py",
            "script_type": "strategy",
            "market": "crypto",
            "venue": "BINANCE",
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "entry_rules": ["Enter long when the 2-period SMA crosses above the 3-period SMA after bar close"],
            "exit_rules": ["Exit when the 2-period SMA crosses below the 3-period SMA after bar close"],
            "risk_rules": ["Use fixed local paper size only and do not place live orders"],
            "position_sizing": {"type": "fixed", "value": 1},
        },
        "supported_execution_mode": "paper",
        "paper_runtime": {"warmup_min_bars": 3},
        "risk_policy": {"live_enabled": False},
    }


def _messages(stream: str, closes: list[int], *, prefix: str) -> list[NativeMarketMessage]:
    messages: list[NativeMarketMessage] = []
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for index, close in enumerate(closes, start=1):
        payload = {
            "event_id": f"{prefix}-{index}",
            "venue": "BINANCE",
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "closed": True,
            "sequence": index,
            "open": close - 1,
            "high": close + 1,
            "low": close - 2,
            "close": close,
            "volume": 10,
            "ts_exchange": (start + timedelta(minutes=index)).isoformat(),
        }
        messages.append(
            NativeMarketMessage(
                stream_name=stream,
                stream_id=f"{index}-0",
                source_event_id=payload["event_id"],
                payload=payload,
            )
        )
    return messages


class FakeNativeRunner:
    def __init__(self) -> None:
        self.messages = []

    def run_bar_batch(self, runtime, messages):
        if not messages:
            return NativeRunResult(events=[], metrics={"paper_engine": "nautilus_local_paper", "order_count": 0})
        self.messages.extend(messages)
        return NativeRunResult(
            events=[
                (
                    "order_submitted",
                    {"source": "fake_native", "runtime_id": runtime.id},
                    "fake-native-order-key",
                )
            ],
            metrics={"paper_engine": "nautilus_local_paper", "order_count": 1},
        )


class ManyEventsRunner:
    def __init__(self, *, event_count: int) -> None:
        self.event_count = event_count

    def run_bar_batch(self, runtime, messages):
        return NativeRunResult(
            events=[
                (
                    "order_submitted",
                    {"source": "many_events", "runtime_id": runtime.id, "index": index},
                    f"many-events-{index}",
                )
                for index in range(self.event_count)
            ],
            metrics={"paper_engine": "nautilus_local_paper", "order_count": self.event_count},
        )


class WarmupRecoveryRunner:
    def __init__(self, *, required_bars: int) -> None:
        self.required_bars = required_bars
        self.batches: list[list[str]] = []

    def run_bar_batch(self, runtime, messages):
        source_event_ids = [message.source_event_id for message in messages]
        self.batches.append(source_event_ids)
        warmup_complete = len(messages) >= self.required_bars
        return NativeRunResult(
            events=[
                (
                    "warmup_completed",
                    {"source": "warmup_recovery", "runtime_id": runtime.id},
                    "warmup-recovery-key",
                )
            ]
            if warmup_complete
            else [],
            metrics={
                "paper_engine": "nautilus_local_paper",
                "warmup_status": "complete" if warmup_complete else "pending",
                "processed_bar_count": len(messages),
            },
        )


class FakeBarsAdapter:
    venue = "BINANCE"

    def __init__(self) -> None:
        self.since_values: list[datetime | None] = []

    def supports(self, subscription: MarketDataStreamSubscription) -> bool:
        return subscription.venue == self.venue and subscription.data_type == "bar"

    def fetch_recent_bars(self, subscription, *, limit, timeout_seconds):
        return self.poll_closed_bars(subscription, limit=limit, timeout_seconds=timeout_seconds)

    def poll_closed_bars(self, subscription, *, since=None, limit=8, timeout_seconds=10.0):
        self.since_values.append(since)
        if since is not None:
            return []
        return [
            NormalizedBar(
                event_id="fake-bar-1",
                venue="BINANCE",
                symbol="BTCUSDT",
                timeframe="1m",
                ts_exchange=datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
                ts_received=datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
                sequence=1,
                open=100,
                high=101,
                low=99,
                close=100,
                volume=10,
                closed=True,
                source="exchange_collector",
                exchange="binance",
                adapter="fake",
            )
        ]


def _stream_id_gt(left: str, right: str) -> bool:
    left_major, left_minor = [int(part) for part in left.split("-", 1)]
    right_major, right_minor = [int(part) for part in right.split("-", 1)]
    return (left_major, left_minor) > (right_major, right_minor)
