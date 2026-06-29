from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from strategy_codebot.reporting import aggregate_status, validation_check
from strategy_codebot.strategy_spec import TARGET_NAUTILUS
from strategy_codebot.strategy_spec import wants_target


NAUTILUS_STRATEGY_PATH = "nautilus/strategy.py"
NAUTILUS_MANIFEST_PATH = "nautilus/runtime-manifest.json"
NAUTILUS_PARITY_REPORT_PATH = "nautilus/parity-report.json"

_UNSUPPORTED_TERMS = (
    "request.security",
    "multi-timeframe",
    "multiple timeframe",
    "intrabar",
    "tick-level",
    "tick level",
    "repaint",
    "lookahead",
    "webhook",
    "telegram",
    "broker execution",
    "live trading automation",
)


@dataclass(frozen=True)
class MovingAverageContract:
    average_type: str
    fast_period: int
    slow_period: int


def moving_average_contract(spec: dict[str, Any]) -> MovingAverageContract | None:
    return _moving_average_contract(spec)


def nautilus_warmup_bar_count(spec: dict[str, Any], *, override: int | None = None) -> int:
    if override is not None:
        return min(max(1, override), 5_000)
    contract = moving_average_contract(spec)
    if contract is None:
        raise ValueError("strategy_spec is outside the Nautilus V1 supported subset")
    return min(max(200, contract.slow_period * 3), 5_000)


def generate_nautilus_strategy(spec: dict[str, Any]) -> str:
    contract = moving_average_contract(spec)
    if contract is None:
        raise ValueError("strategy_spec is outside the Nautilus V1 supported subset")
    symbol = _symbol(spec)
    venue = _venue(spec)
    timeframe = spec["timeframe"]
    class_name = _strategy_class_name(symbol)
    config_name = f"{class_name}Config"
    indicator_name = "ExponentialMovingAverage" if contract.average_type == "ema" else "SimpleMovingAverage"
    return "\n".join(
        [
            "from __future__ import annotations",
            "",
            "from decimal import Decimal",
            "",
            "from nautilus_trader.config import StrategyConfig",
            f"from nautilus_trader.indicators.averages import {indicator_name}",
            "from nautilus_trader.model import Bar",
            "from nautilus_trader.model import BarType",
            "from nautilus_trader.model import InstrumentId",
            "from nautilus_trader.model.enums import OrderSide",
            "from nautilus_trader.model.enums import TimeInForce",
            "from nautilus_trader.trading.strategy import Strategy",
            "",
            "",
            f"class {config_name}(StrategyConfig):",
            f"    instrument_id: InstrumentId = InstrumentId.from_str(\"{symbol}.{venue}\")",
            f"    bar_type: BarType = BarType.from_str(\"{symbol}.{venue}-{_bar_type_timeframe(timeframe)}-LAST-EXTERNAL\")",
            f"    fast_period: int = {contract.fast_period}",
            f"    slow_period: int = {contract.slow_period}",
            "    warmup_bars: int = 0",
            "    trade_size: Decimal = Decimal(\"1\")",
            "    order_id_tag: str = \"strategy-codebot-nautilus-v1\"",
            "",
            "",
            f"class {class_name}(Strategy):",
            f"    def __init__(self, config: {config_name}) -> None:",
            "        super().__init__(config)",
            "        self.instrument = None",
            f"        self.fast = {indicator_name}(config.fast_period)",
            f"        self.slow = {indicator_name}(config.slow_period)",
            "        self._last_fast = None",
            "        self._last_slow = None",
            "        self._bars_seen = 0",
            "",
            "    def on_start(self) -> None:",
            "        self.instrument = self.cache.instrument(self.config.instrument_id)",
            "        if self.instrument is None:",
            "            self.log.error(f\"Could not find instrument {self.config.instrument_id}\")",
            "            self.stop()",
            "            return",
            "        self.register_indicator_for_bars(self.config.bar_type, self.fast)",
            "        self.register_indicator_for_bars(self.config.bar_type, self.slow)",
            "        self.subscribe_bars(self.config.bar_type)",
            "",
            "    def on_bar(self, bar: Bar) -> None:",
            "        self._bars_seen += 1",
            "        if not self.fast.initialized or not self.slow.initialized:",
            "            return",
            "        fast = float(self.fast.value)",
            "        slow = float(self.slow.value)",
            "        if self._bars_seen <= self.config.warmup_bars:",
            "            self._last_fast = fast",
            "            self._last_slow = slow",
            "            return",
            "        crossed_up = self._last_fast is not None and self._last_slow is not None and self._last_fast <= self._last_slow and fast > slow",
            "        crossed_down = self._last_fast is not None and self._last_slow is not None and self._last_fast >= self._last_slow and fast < slow",
            "        self._last_fast = fast",
            "        self._last_slow = slow",
            "        has_open_position = self.cache.positions_open_count(instrument_id=self.config.instrument_id) > 0",
            "        if crossed_up and not has_open_position:",
            "            self._submit_market(OrderSide.BUY)",
            "        elif crossed_down and has_open_position:",
            "            self.close_all_positions(self.config.instrument_id)",
            "",
            "    def _submit_market(self, side: OrderSide) -> None:",
            "        if self.instrument is None:",
            "            return",
            "        order = self.order_factory.market(",
            "            instrument_id=self.config.instrument_id,",
            "            order_side=side,",
            "            quantity=self.instrument.make_qty(self.config.trade_size),",
            "            time_in_force=TimeInForce.GTC,",
            "            tags=[self.config.order_id_tag],",
            "        )",
            "        self.submit_order(order)",
            "",
        ]
    )


def nautilus_runtime_manifest(spec: dict[str, Any], *, strategy_path: str = NAUTILUS_STRATEGY_PATH) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "runtime": "nautilus_trader",
        "mode": "paper",
        "live_enabled": False,
        "strategy_path": strategy_path,
        "strategy_id": _strategy_id(spec),
        "runtime_key_fields": ["user_id", "broker_connection_id", "account_id", "mode", "risk_policy_id"],
        "instrument": {
            "venue": _venue(spec),
            "symbol": _symbol(spec),
            "timeframe": spec["timeframe"],
            "bar_type": f"{_symbol(spec)}.{_venue(spec)}-{_bar_type_timeframe(spec['timeframe'])}-LAST-EXTERNAL",
        },
        "risk_policy": {
            "position_sizing": spec.get("position_sizing"),
            "stop_loss": spec.get("stop_loss"),
            "take_profit": spec.get("take_profit"),
            "rules": spec.get("risk_rules", []),
        },
        "data_subscriptions": [
            {
                "venue": _venue(spec),
                "symbol": _symbol(spec),
                "timeframe": spec["timeframe"],
                "data_type": "bar",
            }
        ],
        "adapter_refs": [],
        "safety": {
            "live_broker_execution": "blocked_until_explicit_decision",
            "requires_parity_report": True,
            "requires_paper_soak": True,
            "requires_global_kill_switch": True,
        },
    }


def validate_nautilus_spec(spec: dict[str, Any]) -> dict[str, Any]:
    checks = [
        validation_check("target_platform", wants_target(spec, TARGET_NAUTILUS), "Spec requests Nautilus target."),
        validation_check("script_type", spec.get("script_type") == "strategy", "Nautilus V1 target supports strategy scripts only."),
        validation_check("symbol", bool(spec.get("symbol")), "Nautilus V1 requires an explicit symbol."),
        validation_check("bar_close_subset", _is_bar_close_subset(spec), "Spec remains in the bar-close V1 subset."),
        validation_check("moving_average_cross", _moving_average_contract(spec) is not None, "Spec uses a supported SMA/EMA crossover contract."),
        validation_check("live_execution_blocked", True, "Generated Nautilus manifests keep live broker execution disabled by default."),
    ]
    status = aggregate_status({check["status"] for check in checks})
    return {
        "platform": TARGET_NAUTILUS,
        "status": status,
        "checks": checks,
        "evidence": ["nautilus-static-contract-v1"],
        "warnings": [] if status == "pass" else ["Unsupported Nautilus features fail closed."],
        "next_actions": (
            ["Run Nautilus local/sim compile and parity checks before paper runtime use."]
            if status == "pass"
            else ["Revise StrategySpec to the Nautilus V1 supported subset before generating runtime artifacts."]
        ),
    }


def nautilus_artifact_bundle(spec: dict[str, Any]) -> dict[str, str]:
    strategy = generate_nautilus_strategy(spec)
    manifest = nautilus_runtime_manifest(spec)
    parity_report = {
        "kind": "parity_report",
        "created_at": "1970-01-01T00:00:00+00:00",
        "status": "skipped",
        "target_platform": TARGET_NAUTILUS,
        "reference_runtime": "spec_oracle+pine_v6",
        "compared_runtime": TARGET_NAUTILUS,
        "checks": [],
        "evidence": [
            f"strategy_id={manifest['strategy_id']}",
            "comparison_level=signal_order_intent",
            "live_unlock_allowed=false",
        ],
        "warnings": ["Trace parity has not run for this artifact bundle."],
        "next_actions": ["Run spec/Pine/Nautilus trace parity before paper or live use."],
    }
    return {
        NAUTILUS_STRATEGY_PATH: strategy,
        NAUTILUS_MANIFEST_PATH: json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        NAUTILUS_PARITY_REPORT_PATH: json.dumps(parity_report, indent=2, sort_keys=True) + "\n",
    }


def _moving_average_contract(spec: dict[str, Any]) -> MovingAverageContract | None:
    text = " ".join([*spec.get("entry_rules", []), *spec.get("exit_rules", [])]).lower()
    average_type = "ema" if "ema" in text or "exponential" in text else "sma"
    matches = [int(value) for value in re.findall(r"(\d+)[-\s]*(?:period\s+)?(?:sma|ema|moving average)", text)]
    if len(matches) >= 2:
        fast, slow = matches[0], matches[1]
    else:
        fast, slow = 9, 21
    if fast <= 0 or slow <= 0 or fast >= slow:
        return None
    if "cross" not in text:
        return None
    return MovingAverageContract(average_type=average_type, fast_period=fast, slow_period=slow)


def _is_bar_close_subset(spec: dict[str, Any]) -> bool:
    text = " ".join(
        [
            *spec.get("entry_rules", []),
            *spec.get("exit_rules", []),
            str(spec.get("user_notes") or ""),
        ]
    ).lower()
    return not any(term in text for term in _UNSUPPORTED_TERMS)


def _symbol(spec: dict[str, Any]) -> str:
    return str(spec.get("symbol") or spec["market"]).upper().replace("/", "")


def _venue(spec: dict[str, Any]) -> str:
    default_venue = "BINANCE" if str(spec.get("market") or "").lower() == "crypto" else "SIM"
    return str(spec.get("venue") or default_venue).upper().replace(" ", "_")


def _bar_type_timeframe(timeframe: str) -> str:
    match = re.fullmatch(r"(\d+)([mhdw])", timeframe.strip().lower())
    if not match:
        return timeframe.upper()
    amount, unit = match.groups()
    label = {"m": "MINUTE", "h": "HOUR", "d": "DAY", "w": "WEEK"}[unit]
    return f"{amount}-{label}"


def _strategy_class_name(symbol: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z]+", " ", symbol).title().replace(" ", "")
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"Generated{cleaned}"
    return f"{cleaned}Strategy"


def _strategy_id(spec: dict[str, Any]) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{_symbol(spec)}-{spec['timeframe']}".lower()).strip("-")
