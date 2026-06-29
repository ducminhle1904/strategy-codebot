from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable


TARGET_PINE = "pine_v6"
TARGET_MQL5 = "mql5"
TARGET_NAUTILUS = "nautilus_py"
TARGET_BOTH = "both"


@dataclass(frozen=True)
class SignalTraceEvent:
    signal_id: str
    bar_time: str
    action: str
    side: str
    quantity_intent: str
    reason: str

    def to_payload(self) -> dict[str, str]:
        return {
            "signal_id": self.signal_id,
            "bar_time": self.bar_time,
            "action": self.action,
            "side": self.side,
            "quantity_intent": self.quantity_intent,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class StrategySpec:
    target_platform: str
    script_type: str
    market: str
    timeframe: str
    entry_rules: tuple[str, ...]
    exit_rules: tuple[str, ...]
    risk_rules: tuple[str, ...]
    symbol: str | None = None
    venue: str | None = None
    runtime_targets: tuple[str, ...] = ()


def parse_strategy_spec(payload: dict[str, Any]) -> StrategySpec:
    return StrategySpec(
        target_platform=str(payload["target_platform"]),
        script_type=str(payload["script_type"]),
        market=str(payload["market"]),
        timeframe=str(payload["timeframe"]),
        entry_rules=tuple(str(item) for item in payload["entry_rules"]),
        exit_rules=tuple(str(item) for item in payload["exit_rules"]),
        risk_rules=tuple(str(item) for item in payload["risk_rules"]),
        symbol=str(payload["symbol"]) if payload.get("symbol") else None,
        venue=str(payload["venue"]) if payload.get("venue") else None,
        runtime_targets=tuple(str(item) for item in payload.get("runtime_targets", [])),
    )


def requested_targets(spec: dict[str, Any]) -> set[str]:
    targets = set(spec.get("runtime_targets") or [])
    target_platform = spec.get("target_platform")
    if target_platform == TARGET_BOTH:
        targets.update({TARGET_PINE, TARGET_MQL5})
    elif target_platform:
        targets.add(str(target_platform))
    return targets


def wants_target(spec: dict[str, Any], target: str) -> bool:
    return target in requested_targets(spec)


def build_parity_report(
    *,
    strategy_id: str,
    oracle_trace: Iterable[dict[str, Any]],
    pine_trace: Iterable[dict[str, Any]],
    nautilus_trace: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    oracle = [_normalize_trace_event(event) for event in oracle_trace]
    pine = [_normalize_trace_event(event) for event in pine_trace]
    nautilus = [_normalize_trace_event(event) for event in nautilus_trace]
    checks = [
        _trace_check("pine_matches_oracle", oracle, pine),
        _trace_check("nautilus_matches_oracle", oracle, nautilus),
        _trace_check("pine_matches_nautilus", pine, nautilus),
    ]
    status = "pass" if all(check["status"] == "pass" for check in checks) else "fail"
    warnings = [] if status == "pass" else ["Signal/order-intent traces drift between runtimes."]
    return {
        "kind": "parity_report",
        "created_at": datetime.now(UTC).isoformat(),
        "status": status,
        "target_platform": TARGET_NAUTILUS,
        "reference_runtime": "spec_oracle+pine_v6",
        "compared_runtime": TARGET_NAUTILUS,
        "checks": checks,
        "evidence": [
            f"strategy_id={strategy_id}",
            f"oracle_events={len(oracle)}",
            f"pine_events={len(pine)}",
            f"nautilus_events={len(nautilus)}",
            "comparison_level=signal_order_intent",
            "live_unlock_allowed=false",
        ],
        "warnings": warnings,
        "next_actions": (
            ["Paper/live remains blocked by default even after parity passes."]
            if status == "pass"
            else ["Fix signal/order-intent drift before Nautilus paper or live use."]
        ),
    }


def _normalize_trace_event(event: dict[str, Any]) -> dict[str, str]:
    return {
        "signal_id": str(event.get("signal_id") or ""),
        "bar_time": str(event.get("bar_time") or event.get("bar_close_time") or ""),
        "action": str(event.get("action") or ""),
        "side": str(event.get("side") or ""),
        "quantity_intent": str(event.get("quantity_intent") or event.get("qty_intent") or ""),
        "reason": str(event.get("reason") or ""),
    }


def _trace_check(name: str, left: list[dict[str, str]], right: list[dict[str, str]]) -> dict[str, str]:
    if left == right:
        return {"name": name, "status": "pass", "details": f"{len(left)} signal events match."}
    return {
        "name": name,
        "status": "fail",
        "details": f"Signal trace drift: left={len(left)} events right={len(right)} events.",
    }
