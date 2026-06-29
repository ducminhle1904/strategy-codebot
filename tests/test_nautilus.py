import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from strategy_codebot.nautilus import NAUTILUS_MANIFEST_PATH
from strategy_codebot.nautilus import NAUTILUS_PARITY_REPORT_PATH
from strategy_codebot.nautilus import NAUTILUS_STRATEGY_PATH
from strategy_codebot.nautilus import generate_nautilus_strategy
from strategy_codebot.nautilus import nautilus_artifact_bundle
from strategy_codebot.nautilus import nautilus_warmup_bar_count
from strategy_codebot.nautilus import validate_nautilus_spec
from strategy_codebot.nautilus_runtime import MarketDataFanout
from strategy_codebot.nautilus_runtime import MarketDataSubscription
from strategy_codebot.nautilus_runtime import RuntimeEvent
from strategy_codebot.nautilus_runtime import RuntimeKey
from strategy_codebot.nautilus_runtime import RuntimeManager
from strategy_codebot.nautilus_runtime import assess_live_readiness
from strategy_codebot.nautilus_runtime import runtime_restart_policy
from strategy_codebot.nautilus_runtime import runtime_scale_summary
from strategy_codebot.nautilus_runtime import runtime_tier_for
from strategy_codebot.schemas import load_strategy_spec
from strategy_codebot.strategy_spec import build_parity_report


def _spec() -> dict:
    return load_strategy_spec(Path("examples/specs/ma-crossover-nautilus.json"))


def test_nautilus_contract_validates_supported_v1_subset() -> None:
    report = validate_nautilus_spec(_spec())

    assert report["status"] == "pass"
    assert report["platform"] == "nautilus_py"
    assert any(check["name"] == "live_execution_blocked" for check in report["checks"])


def test_nautilus_contract_fails_closed_for_unsupported_features() -> None:
    spec = _spec()
    spec["entry_rules"] = ["Enter long using request.security multi-timeframe intrabar confirmation"]

    report = validate_nautilus_spec(spec)

    assert report["status"] == "fail"
    assert "fail closed" in report["warnings"][0]


def test_nautilus_artifact_generation_fails_closed_for_unsupported_contract() -> None:
    spec = _spec()
    spec["entry_rules"] = ["Enter long when RSI is below 30"]
    spec["exit_rules"] = ["Exit when RSI is above 70"]

    with pytest.raises(ValueError, match="outside the Nautilus V1 supported subset"):
        generate_nautilus_strategy(spec)


def test_nautilus_warmup_uses_moving_average_contract_periods_only() -> None:
    spec = _spec()
    spec["entry_rules"] = ["Buy 100 USDT when the 9-period SMA crosses above the 21-period SMA after bar close"]
    spec["exit_rules"] = ["Exit when the 9-period SMA crosses below the 21-period SMA after bar close"]

    assert nautilus_warmup_bar_count(spec) == 200
    assert nautilus_warmup_bar_count(spec, override=3) == 3


def test_nautilus_artifact_bundle_is_deterministic_and_live_disabled() -> None:
    bundle = nautilus_artifact_bundle(_spec())
    manifest = json.loads(bundle[NAUTILUS_MANIFEST_PATH])
    parity = json.loads(bundle[NAUTILUS_PARITY_REPORT_PATH])

    assert set(bundle) == {NAUTILUS_STRATEGY_PATH, NAUTILUS_MANIFEST_PATH, NAUTILUS_PARITY_REPORT_PATH}
    assert "class BtcusdtPerpStrategy" in bundle[NAUTILUS_STRATEGY_PATH]
    assert "nautilus_trader.indicators.averages" in bundle[NAUTILUS_STRATEGY_PATH]
    assert "request_bars" not in bundle[NAUTILUS_STRATEGY_PATH]
    assert "positions_open_count" in bundle[NAUTILUS_STRATEGY_PATH]
    assert "OrderSide.BUY" in generate_nautilus_strategy(_spec())
    assert manifest["mode"] == "paper"
    assert manifest["live_enabled"] is False
    assert manifest["instrument"]["bar_type"] == "BTCUSDT-PERP.BINANCE-15-MINUTE-LAST-EXTERNAL"
    assert manifest["runtime_key_fields"] == ["user_id", "broker_connection_id", "account_id", "mode", "risk_policy_id"]
    assert parity["kind"] == "parity_report"
    assert "live_unlock_allowed=false" in parity["evidence"]


def test_runtime_manager_groups_by_runtime_key() -> None:
    manager = RuntimeManager()
    key = RuntimeKey(
        user_id="user-1",
        broker_connection_id="broker-1",
        account_id="account-1",
        mode="paper",
        risk_policy_id="risk-1",
    )
    first = manager.request_strategy_runtime(key=key, strategy_id="strategy-a")
    second = manager.request_strategy_runtime(key=key, strategy_id="strategy-b")
    other = manager.request_strategy_runtime(
        key=RuntimeKey(
            user_id="user-2",
            broker_connection_id="broker-1",
            account_id="account-1",
            mode="paper",
            risk_policy_id="risk-1",
        ),
        strategy_id="strategy-c",
    )

    assert first.runtime_id == second.runtime_id
    assert first.runtime_id != other.runtime_id
    assert manager.runtime_count() == 2
    assert manager.strategy_count() == 3


def test_runtime_key_stable_id_does_not_collide_on_separator_values() -> None:
    first = RuntimeKey("user", "a:b", "c", "paper", "risk").stable_id()
    second = RuntimeKey("user", "a", "b:c", "paper", "risk").stable_id()

    assert first.startswith("rk_")
    assert second.startswith("rk_")
    assert first != second


def test_runtime_events_heartbeat_error_and_kill_switch() -> None:
    manager = RuntimeManager()
    record = manager.request_strategy_runtime(
        key=RuntimeKey("user", "broker", "account", "paper", "risk"),
        strategy_id="strategy",
    )

    manager.ingest_event(RuntimeEvent(event_type="heartbeat", runtime_id=record.runtime_id))
    assert record.state == "running"
    assert record.last_heartbeat_at is not None

    manager.ingest_event(RuntimeEvent(event_type="runtime_error", runtime_id=record.runtime_id))
    assert record.state == "degraded"

    manager.activate_kill_switch(record.runtime_id)
    assert record.state == "stopping"
    assert record.kill_switch_active is True

    manager.ingest_event(RuntimeEvent(event_type="heartbeat", runtime_id=record.runtime_id))
    assert record.state == "stopping"


def test_runtime_manager_detects_stale_heartbeat_and_restart_policy() -> None:
    manager = RuntimeManager()
    record = manager.request_strategy_runtime(
        key=RuntimeKey("user", "broker", "account", "paper", "risk"),
        strategy_id="strategy",
    )
    manager.ingest_event(
        RuntimeEvent(
            event_type="heartbeat",
            runtime_id=record.runtime_id,
            timestamp=(datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
        )
    )

    assert manager.stale_runtime_ids(max_age_seconds=60) == [record.runtime_id]
    assert runtime_restart_policy(record, stale=True) == "restart_or_reconcile"

    manager.activate_kill_switch(record.runtime_id)
    assert runtime_restart_policy(record, stale=True) == "do_not_restart_kill_switch_active"


def test_market_data_fanout_shares_one_collector_for_many_runtime_subscribers() -> None:
    fanout = MarketDataFanout()
    subscription = MarketDataSubscription(venue="BINANCE", symbol="ETHUSDT", timeframe="1m")

    for index in range(100):
        collector_key = fanout.subscribe(runtime_id=f"runtime-{index}", subscription=subscription)

    assert collector_key == "BINANCE:ETHUSDT:1m:bar"
    assert fanout.upstream_collector_count() == 1
    assert fanout.subscriber_count(collector_key) == 100


def test_live_readiness_is_blocked_by_default() -> None:
    report = assess_live_readiness(
        parity_passed=True,
        paper_soak_passed=True,
        risk_policy_approved=True,
        broker_allowed=True,
        credentials_vaulted=True,
        user_confirmed=True,
        global_kill_switch_ready=True,
    )

    assert report["status"] == "blocked"
    assert report["live_execution_allowed"] is False


def test_runtime_tiers_prefer_account_runtime_for_large_or_live_boundaries() -> None:
    assert runtime_tier_for(mode="paper", strategy_count=2) == "pooled_paper"
    assert runtime_tier_for(mode="paper", strategy_count=20) == "account_runtime"
    assert runtime_tier_for(mode="live", strategy_count=1) == "account_runtime"
    assert runtime_tier_for(mode="live", strategy_count=1, dedicated=True) == "dedicated_runtime"


def test_scale_summary_models_1000_users_2000_strategies_by_runtime_boundary() -> None:
    manager = RuntimeManager()
    for user_index in range(1000):
        key = RuntimeKey(
            user_id=f"user-{user_index}",
            broker_connection_id="binance",
            account_id=f"account-{user_index}",
            mode="paper",
            risk_policy_id="standard",
        )
        manager.request_strategy_runtime(key=key, strategy_id=f"strategy-{user_index}-a")
        manager.request_strategy_runtime(key=key, strategy_id=f"strategy-{user_index}-b")

    summary = runtime_scale_summary(manager.records())

    assert summary["runtime_count"] == 1000
    assert summary["strategy_count"] == 2000
    assert summary["scales_by"] == "active_account_risk_boundary"


def test_parity_report_detects_signal_drift() -> None:
    event = {
        "signal_id": "sig-1",
        "bar_time": "2026-06-26T00:00:00Z",
        "action": "enter",
        "side": "long",
        "quantity_intent": "risk_1_percent",
        "reason": "fast_cross_above_slow",
    }

    report = build_parity_report(
        strategy_id="strategy",
        oracle_trace=[event],
        pine_trace=[event],
        nautilus_trace=[event | {"side": "flat"}],
    )

    assert report["status"] == "fail"
    assert report["kind"] == "parity_report"
    assert "live_unlock_allowed=false" in report["evidence"]
