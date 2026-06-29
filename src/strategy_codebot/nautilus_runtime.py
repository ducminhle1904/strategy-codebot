from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
import json
from typing import Any


RUNTIME_STATES = {"requested", "provisioning", "warming_up", "running", "degraded", "stopping", "stopped", "failed"}
RUNTIME_MODES = {"backtest", "paper", "live"}
RUNTIME_EVENTS = {
    "heartbeat",
    "strategy_loaded",
    "signal",
    "order_intent",
    "order_submitted",
    "fill",
    "position_snapshot",
    "pnl_snapshot",
    "warmup_started",
    "warmup_completed",
    "warmup_failed",
    "risk_block",
    "runtime_error",
    "stop_requested",
}
TERMINAL_RUNTIME_STATES = {"stopped", "failed"}


@dataclass(frozen=True, order=True)
class RuntimeKey:
    user_id: str
    broker_connection_id: str
    account_id: str
    mode: str
    risk_policy_id: str

    def __post_init__(self) -> None:
        if self.mode not in RUNTIME_MODES:
            raise ValueError(f"unsupported runtime mode: {self.mode}")
        for field_name, value in self.__dict__.items():
            if not value:
                raise ValueError(f"{field_name} is required")

    def stable_id(self) -> str:
        payload = json.dumps(
            {
                "user_id": self.user_id,
                "broker_connection_id": self.broker_connection_id,
                "account_id": self.account_id,
                "mode": self.mode,
                "risk_policy_id": self.risk_policy_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"rk_{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True, order=True)
class MarketDataSubscription:
    venue: str
    symbol: str
    timeframe: str
    data_type: str = "bar"

    def collector_key(self) -> str:
        return f"{self.venue.upper()}:{self.symbol.upper()}:{self.timeframe}:{self.data_type}"


@dataclass
class RuntimeRecord:
    runtime_id: str
    key: RuntimeKey
    state: str = "requested"
    strategy_ids: set[str] = field(default_factory=set)
    event_count: int = 0
    last_heartbeat_at: str | None = None
    kill_switch_active: bool = False

    def transition(self, state: str) -> None:
        if state not in RUNTIME_STATES:
            raise ValueError(f"unsupported runtime state: {state}")
        self.state = state


@dataclass(frozen=True)
class RuntimeEvent:
    event_type: str
    runtime_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self) -> None:
        if self.event_type not in RUNTIME_EVENTS:
            raise ValueError(f"unsupported runtime event: {self.event_type}")


class RuntimeManager:
    def __init__(self) -> None:
        self._records: dict[RuntimeKey, RuntimeRecord] = {}

    def request_strategy_runtime(self, *, key: RuntimeKey, strategy_id: str) -> RuntimeRecord:
        record = self._records.get(key)
        if record is None:
            record = RuntimeRecord(runtime_id=f"nautilus:{len(self._records) + 1}", key=key)
            self._records[key] = record
        record.strategy_ids.add(strategy_id)
        return record

    def records(self) -> list[RuntimeRecord]:
        return list(self._records.values())

    def runtime_count(self) -> int:
        return len(self._records)

    def strategy_count(self) -> int:
        return sum(len(record.strategy_ids) for record in self._records.values())

    def ingest_event(self, event: RuntimeEvent) -> RuntimeRecord:
        record = self._record_by_runtime_id(event.runtime_id)
        record.event_count += 1
        if event.event_type == "heartbeat":
            record.last_heartbeat_at = event.timestamp
        record.transition(
            runtime_state_after_event(
                record.state,
                event.event_type,
                kill_switch_active=record.kill_switch_active,
            )
        )
        return record

    def activate_kill_switch(self, runtime_id: str) -> RuntimeRecord:
        record = self._record_by_runtime_id(runtime_id)
        record.kill_switch_active = True
        record.transition("stopping")
        return record

    def stale_runtime_ids(self, *, now: datetime | None = None, max_age_seconds: int = 90) -> list[str]:
        current = now or datetime.now(UTC)
        stale: list[str] = []
        for record in self._records.values():
            if record.state != "running":
                continue
            if record.last_heartbeat_at is None:
                stale.append(record.runtime_id)
                continue
            try:
                heartbeat_at = datetime.fromisoformat(record.last_heartbeat_at)
            except ValueError:
                stale.append(record.runtime_id)
                continue
            if current - heartbeat_at > timedelta(seconds=max_age_seconds):
                stale.append(record.runtime_id)
        return stale

    def _record_by_runtime_id(self, runtime_id: str) -> RuntimeRecord:
        for record in self._records.values():
            if record.runtime_id == runtime_id:
                return record
        raise KeyError(runtime_id)


def runtime_state_after_event(
    current_state: str,
    event_type: str,
    *,
    kill_switch_active: bool = False,
) -> str:
    if event_type == "heartbeat":
        if kill_switch_active:
            if current_state in TERMINAL_RUNTIME_STATES or current_state == "stopping":
                return current_state
            return "stopping"
        return "running" if current_state == "requested" else current_state
    if event_type == "stop_requested":
        return current_state if current_state in TERMINAL_RUNTIME_STATES else "stopping"
    if event_type in {"runtime_error", "risk_block"}:
        return current_state if current_state in TERMINAL_RUNTIME_STATES or current_state == "stopping" else "degraded"
    return current_state


class MarketDataFanout:
    def __init__(self) -> None:
        self._collector_subscribers: dict[str, set[str]] = {}

    def subscribe(self, *, runtime_id: str, subscription: MarketDataSubscription) -> str:
        collector_key = subscription.collector_key()
        self._collector_subscribers.setdefault(collector_key, set()).add(runtime_id)
        return collector_key

    def upstream_collector_count(self) -> int:
        return len(self._collector_subscribers)

    def subscriber_count(self, collector_key: str) -> int:
        return len(self._collector_subscribers.get(collector_key, set()))


def runtime_tier_for(*, mode: str, strategy_count: int, dedicated: bool = False) -> str:
    if dedicated:
        return "dedicated_runtime"
    if mode == "paper" and strategy_count <= 5:
        return "pooled_paper"
    return "account_runtime"


def runtime_restart_policy(record: RuntimeRecord, *, stale: bool = False) -> str:
    if record.kill_switch_active:
        return "do_not_restart_kill_switch_active"
    if record.state == "failed":
        return "restart_with_backoff"
    if record.state == "degraded" or stale:
        return "restart_or_reconcile"
    return "keep_running"


def runtime_scale_summary(records: list[RuntimeRecord]) -> dict[str, Any]:
    tier_counts = {"pooled_paper": 0, "account_runtime": 0, "dedicated_runtime": 0}
    strategy_count = 0
    for record in records:
        strategy_total = len(record.strategy_ids)
        strategy_count += strategy_total
        tier = runtime_tier_for(mode=record.key.mode, strategy_count=strategy_total)
        tier_counts[tier] += 1
    return {
        "runtime_count": len(records),
        "strategy_count": strategy_count,
        "tier_counts": tier_counts,
        "scales_by": "active_account_risk_boundary",
    }


def assess_live_readiness(
    *,
    live_enabled: bool = False,
    parity_passed: bool,
    paper_soak_passed: bool,
    risk_policy_approved: bool,
    broker_allowed: bool,
    credentials_vaulted: bool,
    user_confirmed: bool,
    global_kill_switch_ready: bool,
) -> dict[str, Any]:
    checks = {
        "live_enabled": live_enabled,
        "parity_passed": parity_passed,
        "paper_soak_passed": paper_soak_passed,
        "risk_policy_approved": risk_policy_approved,
        "broker_allowed": broker_allowed,
        "credentials_vaulted": credentials_vaulted,
        "user_confirmed": user_confirmed,
        "global_kill_switch_ready": global_kill_switch_ready,
    }
    status = "pass" if all(checks.values()) else "blocked"
    return {
        "status": status,
        "live_execution_allowed": status == "pass",
        "checks": checks,
        "next_actions": [] if status == "pass" else ["Keep live broker execution disabled until all readiness gates pass."],
    }
