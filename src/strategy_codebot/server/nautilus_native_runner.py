from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import time
from types import ModuleType
from typing import Any

from strategy_codebot.nautilus import MovingAverageContract
from strategy_codebot.nautilus import NAUTILUS_STRATEGY_PATH
from strategy_codebot.nautilus import moving_average_contract
from strategy_codebot.nautilus import nautilus_artifact_bundle
from strategy_codebot.nautilus import nautilus_warmup_bar_count
from strategy_codebot.nautilus import validate_nautilus_spec
from strategy_codebot.nautilus_streams import deterministic_event_key
from strategy_codebot.server.repository import NautilusRuntimeEventInput
from strategy_codebot.server.repository import NautilusRuntimeRecord


@dataclass(frozen=True)
class NativeMarketMessage:
    stream_name: str
    stream_id: str
    source_event_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class NativeRunResult:
    events: list[NautilusRuntimeEventInput]
    metrics: dict[str, Any]


class NativeNautilusExecutionError(RuntimeError):
    pass


MAX_LOCAL_PAPER_SESSIONS = 512
MAX_SESSION_IDEMPOTENCY_KEYS = 10_000
MAX_SESSION_SOURCE_EVENT_IDS = 20_000
LOCAL_NAUTILUS_SOURCE = "nautilus_local_paper"
LOCAL_NAUTILUS_ENGINE = "nautilus_trading_node"


@dataclass(frozen=True)
class PreparedStrategy:
    fingerprint: str
    artifact_hash: str
    contract: MovingAverageContract
    required_warmup_bars: int
    strategy_spec: dict[str, Any]


@dataclass(frozen=True)
class NautilusBindings:
    sandbox_execution_client: Any
    sandbox_execution_config: Any
    trading_node: Any
    trading_node_config: Any
    logging_config: Any
    bar: Any
    bar_type: Any
    price: Any
    quantity: Any
    simple_moving_average: Any
    exponential_moving_average: Any
    order_factory: Any
    test_clock: Any
    trader_id: Any
    strategy_id: Any
    client_order_id: Any
    instrument_id: Any
    order_side: Any
    time_in_force: Any
    submit_order: Any
    uuid4: Any
    test_instrument_provider: Any


@dataclass
class NautilusSandboxAccount:
    starting_balance: float = 100_000.0
    cash_balance: float = 100_000.0
    net_quantity: float = 0.0
    average_entry_price: float = 0.0
    realized_pnl: float = 0.0
    last_price: float = 0.0

    def apply_fill(self, *, side: str, quantity: float, price: float) -> None:
        self.last_price = price
        if side == "BUY":
            total_cost = self.average_entry_price * self.net_quantity + price * quantity
            self.net_quantity += quantity
            self.average_entry_price = total_cost / self.net_quantity if self.net_quantity else 0.0
            self.cash_balance -= price * quantity
            return
        closing_quantity = min(quantity, self.net_quantity)
        self.realized_pnl += (price - self.average_entry_price) * closing_quantity
        self.net_quantity = max(0.0, self.net_quantity - closing_quantity)
        self.cash_balance += price * closing_quantity
        if self.net_quantity == 0:
            self.average_entry_price = 0.0

    @property
    def unrealized_pnl(self) -> float:
        if self.net_quantity <= 0:
            return 0.0
        return (self.last_price - self.average_entry_price) * self.net_quantity

    @property
    def equity(self) -> float:
        return self.cash_balance + self.net_quantity * self.last_price


@dataclass
class NautilusRuntimeSession:
    runtime_id: str
    generation: int
    warmup_status: str = "pending"
    warmup_bars: list[NativeMarketMessage] = field(default_factory=list)
    processed_bar_count: int = 0
    processed_tick_count: int = 0
    trade_bar_count: int = 0
    order_count: int = 0
    session_started: bool = False
    engine_init_count: int = 0
    strategy_module_load_count: int = 0
    artifact_hash: str | None = None
    instrument_id: str | None = None
    bar_type: str | None = None
    required_warmup_bars: int | None = None
    prepared_strategy: PreparedStrategy | None = None
    bindings: NautilusBindings | None = None
    node: Any | None = None
    strategy: Any | None = None
    sandbox_client: Any | None = None
    sandbox_account: NautilusSandboxAccount = field(default_factory=NautilusSandboxAccount)
    instrument: Any | None = None
    bar_type_obj: Any | None = None
    current_market_message: NativeMarketMessage | None = None
    captured_orders: list[Any] = field(default_factory=list)
    emitted_idempotency_keys: set[str] = field(default_factory=set)
    emitted_idempotency_order: deque[str] = field(default_factory=deque)
    processed_source_event_ids: set[str] = field(default_factory=set)
    processed_source_event_order: deque[str] = field(default_factory=deque)
    last_access_monotonic: float = field(default_factory=time.monotonic)

    def start(
        self,
        *,
        prepared: PreparedStrategy,
        venue: str,
        symbol: str,
        timeframe: str,
    ) -> None:
        if self.session_started:
            return
        bindings = _load_nautilus_bindings()
        self.bindings = bindings
        self.session_started = True
        self.engine_init_count += 1
        self.strategy_module_load_count += 1
        self.artifact_hash = prepared.artifact_hash
        self.instrument_id = f"{symbol}.{venue}"
        self.bar_type = f"{symbol}.{venue}-{_bar_type_timeframe(timeframe)}-LAST-EXTERNAL"
        self.instrument = _instrument_for(bindings, venue=venue, symbol=symbol)
        if self.instrument is None:
            raise NativeNautilusExecutionError(f"Nautilus V1 paper runtime has no local instrument fixture for {symbol}.{venue}")
        self.bar_type_obj = bindings.bar_type.from_str(self.bar_type)
        self.node = bindings.trading_node(
            config=bindings.trading_node_config(
                logging=bindings.logging_config(log_level="ERROR", log_colors=False, print_config=False)
            )
        )
        self.node.cache.add_instrument(self.instrument)
        self.sandbox_client = bindings.sandbox_execution_client(
            loop=asyncio.new_event_loop(),
            portfolio=self.node.portfolio,
            msgbus=self.node.kernel.msgbus,
            cache=self.node.cache,
            clock=self.node.kernel.clock,
            config=bindings.sandbox_execution_config(
                venue=venue,
                starting_balances=["100000 USDT"],
                oms_type="NETTING",
                bar_execution=True,
                trade_execution=True,
                use_random_ids=False,
            ),
        )
        self.sandbox_client.exchange.add_instrument(self.instrument)
        self.sandbox_client.connect()
        self.strategy = _load_generated_strategy(prepared.strategy_spec, warmup_bars=prepared.required_warmup_bars)
        self.node.trader.add_strategy(self.strategy)
        self.strategy.submit_order = self._capture_strategy_order
        self.strategy.on_start()

    def run_bar_batch(
        self,
        runtime: NautilusRuntimeRecord,
        messages: list[NativeMarketMessage],
    ) -> NativeRunResult:
        self.touch()
        if not messages:
            return NativeRunResult(events=[], metrics=self._metrics(message_count=0, bar_count=0, ignored_messages=0))
        if runtime.mode != "paper":
            raise NativeNautilusExecutionError("local Nautilus paper runner only supports paper mode")
        manifest = runtime.manifest_json if isinstance(runtime.manifest_json, dict) else {}
        _assert_live_disabled(manifest)
        strategy_spec = manifest.get("strategy_spec")
        if not isinstance(strategy_spec, dict):
            raise NativeNautilusExecutionError("runtime manifest is missing strategy_spec for local Nautilus paper execution")
        _assert_tick_semantics_supported(strategy_spec, messages)
        prepared = self._prepare_strategy(strategy_spec, manifest)

        bar_messages = [message for message in messages if _is_closed_bar_message(message)]
        tick_count = len([message for message in messages if message.stream_name.endswith(":ticks")])
        self.processed_tick_count += tick_count
        ignored_messages = len(messages) - len(bar_messages)
        if not bar_messages:
            return NativeRunResult(
                events=[],
                metrics=self._metrics(
                    message_count=len(messages),
                    bar_count=0,
                    ignored_messages=ignored_messages,
                ),
            )

        if len({_bar_identity(message) for message in bar_messages}) > 1:
            raise NativeNautilusExecutionError("local Nautilus paper runner supports one bar stream per runtime in V1")
        venue = str(bar_messages[-1].payload.get("venue") or strategy_spec.get("venue") or "").upper()
        symbol = str(bar_messages[-1].payload.get("symbol") or strategy_spec.get("symbol") or "").upper().replace("/", "")
        timeframe = str(bar_messages[-1].payload.get("timeframe") or strategy_spec.get("timeframe") or "")
        contract = prepared.contract
        self.start(prepared=prepared, venue=venue, symbol=symbol, timeframe=timeframe)

        required_warmup_bars = prepared.required_warmup_bars
        self.required_warmup_bars = required_warmup_bars
        events: list[NautilusRuntimeEventInput] = []
        if self.warmup_status == "pending":
            self.warmup_status = "warming_up"
            events.append(_warmup_event(runtime, "warmup_started", messages[0].source_event_id, required_warmup_bars))
            for warmup_message in _manifest_warmup_messages(manifest, bar_messages[-1]):
                self._feed_warmup_bar(warmup_message, contract)
            if len(self.warmup_bars) >= required_warmup_bars:
                self.warmup_status = "complete"
                events.append(_warmup_event(runtime, "warmup_completed", messages[0].source_event_id, required_warmup_bars))

        fresh_bars = [message for message in bar_messages if self._remember_source_event_id(message.source_event_id)]
        if not fresh_bars:
            return NativeRunResult(
                events=self._dedupe(events),
                metrics=self._metrics(
                    message_count=len(messages),
                    bar_count=0,
                    ignored_messages=ignored_messages,
                    required_warmup_bars=required_warmup_bars,
                ),
            )

        trade_bar_count = 0
        for message in fresh_bars:
            if self.warmup_status != "complete":
                self._feed_warmup_bar(message, contract)
                if len(self.warmup_bars) >= required_warmup_bars:
                    self.warmup_status = "complete"
                    events.append(_warmup_event(runtime, "warmup_completed", message.source_event_id, required_warmup_bars))
                continue
            trade_bar_count += 1
            events.extend(self.feed_bar(runtime, message, contract, strategy_spec=strategy_spec))

        return NativeRunResult(
            events=self._dedupe(events),
            metrics=self._metrics(
                message_count=len(messages),
                bar_count=trade_bar_count,
                ignored_messages=ignored_messages,
                required_warmup_bars=required_warmup_bars,
            ),
        )

    def feed_bar(
        self,
        runtime: NautilusRuntimeRecord,
        message: NativeMarketMessage,
        contract: MovingAverageContract,
        *,
        strategy_spec: dict[str, Any],
    ) -> list[NautilusRuntimeEventInput]:
        _ = contract, strategy_spec
        self.processed_bar_count += 1
        self.trade_bar_count += 1
        before = len(self.captured_orders)
        bar = self._nautilus_bar(message)
        self._feed_generated_strategy_bar(message, bar)
        return [
            event
            for order in self.captured_orders[before:]
            for event in self._emit_sandbox_order_events(runtime, message, order)
        ]

    def feed_tick(self, message: NativeMarketMessage) -> NativeRunResult:
        self.processed_tick_count += 1
        return NativeRunResult(
            events=[],
            metrics=self._metrics(message_count=1, bar_count=0, ignored_messages=1)
            | {"unsupported_tick_source_event_id": message.source_event_id},
        )

    def stop(self, reason: str) -> NativeRunResult:
        return NativeRunResult(
            events=[],
            metrics=self._metrics(message_count=0, bar_count=0, ignored_messages=0) | {"stop_reason": reason},
        )

    def metrics(self) -> dict[str, Any]:
        return self._metrics(message_count=0, bar_count=0, ignored_messages=0)

    def _prepare_strategy(self, strategy_spec: dict[str, Any], manifest: dict[str, Any]) -> PreparedStrategy:
        fingerprint = _strategy_fingerprint(strategy_spec, manifest)
        if self.prepared_strategy is not None and self.prepared_strategy.fingerprint == fingerprint:
            return self.prepared_strategy
        validation = validate_nautilus_spec(strategy_spec)
        if validation.get("status") != "pass":
            raise NativeNautilusExecutionError("strategy_spec is outside the Nautilus V1 supported subset")
        contract = moving_average_contract(strategy_spec)
        if contract is None:
            raise NativeNautilusExecutionError("strategy_spec is outside the Nautilus V1 supported subset")
        prepared = PreparedStrategy(
            fingerprint=fingerprint,
            artifact_hash=_artifact_hash(strategy_spec),
            contract=contract,
            required_warmup_bars=_required_warmup_bars(strategy_spec, manifest),
            strategy_spec=strategy_spec,
        )
        self.prepared_strategy = prepared
        return prepared

    def mark_failed(self) -> None:
        self.warmup_status = "failed"

    def touch(self) -> None:
        self.last_access_monotonic = time.monotonic()

    def _metrics(
        self,
        *,
        message_count: int,
        bar_count: int,
        ignored_messages: int,
        required_warmup_bars: int | None = None,
    ) -> dict[str, Any]:
        if required_warmup_bars is not None:
            self.required_warmup_bars = required_warmup_bars
        return {
            "paper_engine": LOCAL_NAUTILUS_SOURCE,
            "engine": LOCAL_NAUTILUS_ENGINE,
            "indicator_owner": "nautilus",
            "strategy_callback_owner": "nautilus",
            "order_owner": "nautilus",
            "execution_owner": "nautilus_sandbox",
            "fill_owner": "nautilus",
            "position_owner": "nautilus",
            "pnl_owner": "nautilus",
            "trading_node_active": self.node is not None,
            "engine_init_count": self.engine_init_count,
            "session_reused": self.engine_init_count == 1 and self.session_started,
            "strategy_module_load_count": self.strategy_module_load_count,
            "artifact_hash": self.artifact_hash,
            "instrument_id": self.instrument_id,
            "bar_type": self.bar_type,
            "warmup_status": self.warmup_status,
            "required_warmup_bars": self.required_warmup_bars,
            "warmup_bar_count": len(self.warmup_bars),
            "message_count": message_count,
            "bar_count": bar_count,
            "ignored_messages": ignored_messages,
            "processed_bar_count": self.processed_bar_count,
            "processed_tick_count": self.processed_tick_count,
            "trade_bar_count": self.trade_bar_count,
            "order_count": self.order_count,
            "fill_count": self.order_count,
            "open_order_count": 0,
            "net_qty": self.sandbox_account.net_quantity,
            "cash_balance": self.sandbox_account.cash_balance,
            "equity": self.sandbox_account.equity,
        }

    def _feed_warmup_bar(self, message: NativeMarketMessage, contract: MovingAverageContract) -> None:
        _ = contract
        self.warmup_bars.append(message)
        self.processed_bar_count += 1
        self._feed_generated_strategy_bar(message, self._nautilus_bar(message))

    def _feed_generated_strategy_bar(self, message: NativeMarketMessage, bar: Any) -> None:
        if self.strategy is None or self.sandbox_client is None:
            raise NativeNautilusExecutionError("generated Nautilus strategy is not initialized")
        self.current_market_message = message
        try:
            self.strategy.fast.handle_bar(bar)
            self.strategy.slow.handle_bar(bar)
            self.strategy.on_bar(bar)
            self.sandbox_client.on_data(bar)
        finally:
            self.current_market_message = None

    def _nautilus_bar(self, message: NativeMarketMessage) -> Any:
        if self.bindings is None or self.bar_type_obj is None or self.instrument is None:
            raise NativeNautilusExecutionError("Nautilus session is not initialized")
        payload = message.payload
        ts_event = _timestamp_ns(payload.get("ts_exchange") or payload.get("timestamp") or payload.get("time"))
        ts_init = _timestamp_ns(payload.get("ts_received")) or _now_ns()
        price_precision = int(getattr(self.instrument, "price_precision", _price_precision(payload.get("close"))))
        size_precision = int(getattr(self.instrument, "size_precision", _quantity_precision(payload.get("volume"))))
        return self.bindings.bar(
            self.bar_type_obj,
            self.bindings.price(float(payload["open"]), price_precision),
            self.bindings.price(float(payload["high"]), price_precision),
            self.bindings.price(float(payload["low"]), price_precision),
            self.bindings.price(float(payload["close"]), price_precision),
            self.bindings.quantity(float(payload.get("volume") or 0), size_precision),
            ts_event or ts_init,
            ts_init,
        )

    def _capture_strategy_order(self, order: Any, *_: Any, **__: Any) -> None:
        if self.bindings is None or self.sandbox_client is None:
            raise NativeNautilusExecutionError("Nautilus sandbox execution is not initialized")
        if self.current_market_message is None:
            raise NativeNautilusExecutionError("generated strategy submitted an order outside a market-data callback")
        self.captured_orders.append(order)
        command = self.bindings.submit_order(
            trader_id=order.trader_id,
            strategy_id=order.strategy_id,
            position_id=None,
            order=order,
            command_id=self.bindings.uuid4(),
            ts_init=order.ts_init,
        )
        self.sandbox_client.submit_order(command)

    def _emit_sandbox_order_events(
        self,
        runtime: NautilusRuntimeRecord,
        message: NativeMarketMessage,
        order: Any,
    ) -> list[NautilusRuntimeEventInput]:
        self.order_count += 1
        side = _order_side(order)
        quantity = _order_quantity(order)
        fill_price = float(message.payload["close"])
        self.sandbox_account.apply_fill(side=side, quantity=quantity, price=fill_price)
        payload = _nautilus_order_payload(
            runtime=runtime,
            message=message,
            side=side,
            quantity=quantity,
            order_index=self.order_count,
            instrument_id=self.instrument_id or "",
            order=order,
            fill_price=fill_price,
            sandbox_account=self.sandbox_account,
        )
        order_event_source = f"{message.source_event_id}:order:{side}:MARKET:{quantity}"
        return [
            (
                "signal",
                {
                    "source": LOCAL_NAUTILUS_SOURCE,
                    "engine": LOCAL_NAUTILUS_ENGINE,
                    "market_data": message.payload,
                    "strategy_ids": runtime.strategy_ids,
                    "signal": {"side": side, "quantity": quantity},
                    "order_count": self.order_count,
                    "callback_owner": "nautilus",
                },
                deterministic_event_key(runtime_id=runtime.id, source_event_id=order_event_source, event_type="signal"),
            ),
            (
                "order_intent",
                payload | {"intent_source": "generated_nautilus_strategy"},
                deterministic_event_key(runtime_id=runtime.id, source_event_id=order_event_source, event_type="order_intent"),
            ),
            (
                "order_submitted",
                payload,
                deterministic_event_key(runtime_id=runtime.id, source_event_id=order_event_source, event_type="order_submitted"),
            ),
            (
                "fill",
                payload,
                deterministic_event_key(runtime_id=runtime.id, source_event_id=order_event_source, event_type="fill"),
            ),
            (
                "position_snapshot",
                {
                    "source": LOCAL_NAUTILUS_SOURCE,
                    "engine": LOCAL_NAUTILUS_ENGINE,
                    "market_data": message.payload,
                    "strategy_ids": runtime.strategy_ids,
                    "position": {
                        "instrument_id": self.instrument_id or "",
                        "side": "FLAT" if self.sandbox_account.net_quantity <= 0 else "LONG",
                        "quantity": str(self.sandbox_account.net_quantity),
                        "signed_qty": str(self.sandbox_account.net_quantity),
                        "average_entry_price": str(self.sandbox_account.average_entry_price),
                    },
                    "owner": "nautilus",
                    "execution_owner": "nautilus_sandbox",
                    "raw": "nautilus_sandbox_position_snapshot",
                },
                deterministic_event_key(
                    runtime_id=runtime.id,
                    source_event_id=f"{order_event_source}:position",
                    event_type="position_snapshot",
                ),
            ),
            (
                "pnl_snapshot",
                {
                    "source": LOCAL_NAUTILUS_SOURCE,
                    "engine": LOCAL_NAUTILUS_ENGINE,
                    "market_data": message.payload,
                    "strategy_ids": runtime.strategy_ids,
                    "pnl": {
                        "realized": str(self.sandbox_account.realized_pnl),
                        "unrealized": str(self.sandbox_account.unrealized_pnl),
                        "equity": str(self.sandbox_account.equity),
                        "cash": str(self.sandbox_account.cash_balance),
                    },
                    "owner": "nautilus",
                    "execution_owner": "nautilus_sandbox",
                    "raw": "nautilus_sandbox_pnl_snapshot",
                },
                deterministic_event_key(
                    runtime_id=runtime.id,
                    source_event_id=f"{order_event_source}:pnl",
                    event_type="pnl_snapshot",
                ),
            ),
        ]

    def _dedupe(self, events: list[NautilusRuntimeEventInput]) -> list[NautilusRuntimeEventInput]:
        deduped: list[NautilusRuntimeEventInput] = []
        for event_type, payload, idempotency_key in events:
            if idempotency_key and idempotency_key in self.emitted_idempotency_keys:
                continue
            if idempotency_key:
                self._remember_idempotency_key(idempotency_key)
            deduped.append((event_type, payload, idempotency_key))
        return deduped

    def _remember_idempotency_key(self, idempotency_key: str) -> None:
        self.emitted_idempotency_keys.add(idempotency_key)
        self.emitted_idempotency_order.append(idempotency_key)
        while len(self.emitted_idempotency_order) > MAX_SESSION_IDEMPOTENCY_KEYS:
            expired = self.emitted_idempotency_order.popleft()
            self.emitted_idempotency_keys.discard(expired)

    def _remember_source_event_id(self, source_event_id: str) -> bool:
        if source_event_id in self.processed_source_event_ids:
            return False
        self.processed_source_event_ids.add(source_event_id)
        self.processed_source_event_order.append(source_event_id)
        while len(self.processed_source_event_order) > MAX_SESSION_SOURCE_EVENT_IDS:
            expired = self.processed_source_event_order.popleft()
            self.processed_source_event_ids.discard(expired)
        return True


NautilusLocalPaperRuntime = NautilusRuntimeSession


class NativeNautilusExecutionRunner:
    def __init__(self) -> None:
        self._sessions: dict[str, NautilusRuntimeSession] = {}

    def run_bar_batch(
        self,
        runtime: NautilusRuntimeRecord,
        messages: list[NativeMarketMessage],
    ) -> NativeRunResult:
        session = self._session_for(runtime)
        if not messages:
            return NativeRunResult(events=[], metrics=session.metrics())
        try:
            return session.run_bar_batch(runtime, messages)
        except Exception as exc:
            session.mark_failed()
            source_event_id = messages[-1].source_event_id
            return NativeRunResult(
                events=[
                    (
                        "runtime_error",
                        {
                            "source": LOCAL_NAUTILUS_SOURCE,
                            "engine": LOCAL_NAUTILUS_ENGINE,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                            "stream": messages[-1].stream_name,
                            "market_data": messages[-1].payload,
                            "strategy_ids": runtime.strategy_ids,
                        },
                        deterministic_event_key(
                            runtime_id=runtime.id,
                            source_event_id=source_event_id,
                            event_type="runtime_error",
                        ),
                    )
                ],
                metrics={
                    **session.metrics(),
                    "warmup_status": "failed",
                    "native_error": type(exc).__name__,
                    "native_error_message": str(exc),
                },
            )

    def _session_for(self, runtime: NautilusRuntimeRecord) -> NautilusRuntimeSession:
        key = _session_key(runtime)
        session = self._sessions.get(key)
        if session is None:
            session = NautilusRuntimeSession(runtime_id=runtime.id, generation=runtime.generation)
            self._sessions[key] = session
            self._evict_sessions()
        session.touch()
        return session

    def drop_runtime(self, runtime_id: str) -> None:
        prefix = f"{runtime_id}:"
        for key in list(self._sessions):
            if key.startswith(prefix):
                del self._sessions[key]

    def _evict_sessions(self) -> None:
        while len(self._sessions) > MAX_LOCAL_PAPER_SESSIONS:
            oldest_key = min(self._sessions, key=lambda key: self._sessions[key].last_access_monotonic)
            del self._sessions[oldest_key]


def _assert_live_disabled(manifest: dict[str, Any]) -> None:
    if manifest.get("live_enabled") is True:
        raise NativeNautilusExecutionError("live broker execution is disabled")
    risk_policy = manifest.get("risk_policy") if isinstance(manifest.get("risk_policy"), dict) else {}
    if risk_policy.get("live_enabled") is True:
        raise NativeNautilusExecutionError("risk policy attempts to enable live execution")
    if str(manifest.get("supported_execution_mode") or "paper").lower() == "live":
        raise NativeNautilusExecutionError("runtime manifest attempts to enable live execution")


def _assert_tick_semantics_supported(strategy_spec: dict[str, Any], messages: list[NativeMarketMessage]) -> None:
    if _spec_declares_tick_semantics(strategy_spec):
        raise NativeNautilusExecutionError("tick-driven Nautilus paper execution is not implemented in V1")
    if any(message.stream_name.endswith(":ticks") for message in messages):
        raise NativeNautilusExecutionError("tick market data is not supported by the Nautilus paper runtime in V1")


def _load_nautilus_bindings() -> NautilusBindings:
    try:
        from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
        from nautilus_trader.adapters.sandbox.execution import SandboxExecutionClient
        from nautilus_trader.common.component import TestClock
        from nautilus_trader.common.config import LoggingConfig
        from nautilus_trader.common.factories import OrderFactory
        from nautilus_trader.config import TradingNodeConfig
        from nautilus_trader.core.uuid import UUID4
        from nautilus_trader.execution.messages import SubmitOrder
        from nautilus_trader.indicators.averages import ExponentialMovingAverage
        from nautilus_trader.indicators.averages import SimpleMovingAverage
        from nautilus_trader.live.node import TradingNode
        from nautilus_trader.model.data import Bar
        from nautilus_trader.model.data import BarType
        from nautilus_trader.model.enums import OrderSide
        from nautilus_trader.model.enums import TimeInForce
        from nautilus_trader.model.identifiers import ClientOrderId
        from nautilus_trader.model.identifiers import InstrumentId
        from nautilus_trader.model.identifiers import StrategyId
        from nautilus_trader.model.identifiers import TraderId
        from nautilus_trader.model.objects import Price
        from nautilus_trader.model.objects import Quantity
        from nautilus_trader.test_kit.providers import TestInstrumentProvider
    except ModuleNotFoundError as exc:
        raise NativeNautilusExecutionError("nautilus-trader is required for Nautilus-owned paper execution") from exc
    return NautilusBindings(
        sandbox_execution_client=SandboxExecutionClient,
        sandbox_execution_config=SandboxExecutionClientConfig,
        trading_node=TradingNode,
        trading_node_config=TradingNodeConfig,
        logging_config=LoggingConfig,
        bar=Bar,
        bar_type=BarType,
        price=Price,
        quantity=Quantity,
        simple_moving_average=SimpleMovingAverage,
        exponential_moving_average=ExponentialMovingAverage,
        order_factory=OrderFactory,
        test_clock=TestClock,
        trader_id=TraderId,
        strategy_id=StrategyId,
        client_order_id=ClientOrderId,
        instrument_id=InstrumentId,
        order_side=OrderSide,
        time_in_force=TimeInForce,
        submit_order=SubmitOrder,
        uuid4=UUID4,
        test_instrument_provider=TestInstrumentProvider,
    )


def _instrument_for(bindings: NautilusBindings, *, venue: str, symbol: str) -> Any | None:
    if venue.upper() != "BINANCE":
        return None
    provider_method = {
        "BTCUSDT": "btcusdt_binance",
        "ETHUSDT": "ethusdt_binance",
    }.get(symbol.upper().replace("/", ""))
    if provider_method is None:
        return None
    provider = getattr(bindings.test_instrument_provider, provider_method, None)
    if provider is None:
        return None
    return provider()


def _load_generated_strategy(strategy_spec: dict[str, Any], *, warmup_bars: int) -> Any:
    bundle = nautilus_artifact_bundle(strategy_spec)
    source = bundle.get(NAUTILUS_STRATEGY_PATH)
    if not isinstance(source, str):
        raise NativeNautilusExecutionError("generated Nautilus artifact bundle is missing nautilus/strategy.py")
    module_name = f"strategy_codebot_generated_nautilus_{_short_hash(json.dumps(strategy_spec, sort_keys=True, default=str))}"
    module = ModuleType(module_name)
    try:
        exec(compile(source, f"<{module_name}:{NAUTILUS_STRATEGY_PATH}>", "exec"), module.__dict__)
    except Exception as exc:
        raise NativeNautilusExecutionError(f"generated Nautilus strategy import failed: {exc}") from exc

    config_class = _find_generated_class(module, suffix="StrategyConfig", base_name="StrategyConfig")
    strategy_class = _find_generated_class(module, suffix="Strategy", base_name="Strategy")
    if config_class is None or strategy_class is None:
        raise NativeNautilusExecutionError("generated Nautilus strategy artifact is missing StrategyConfig/Strategy classes")
    try:
        config = config_class(
            warmup_bars=warmup_bars,
            trade_size=Decimal(str(_trade_size(strategy_spec))),
        )
        return strategy_class(config)
    except Exception as exc:
        raise NativeNautilusExecutionError(f"generated Nautilus strategy attach failed: {exc}") from exc


def _find_generated_class(module: ModuleType, *, suffix: str, base_name: str) -> type[Any] | None:
    candidates = [
        value
        for name, value in module.__dict__.items()
        if isinstance(value, type) and name.endswith(suffix) and name != base_name
    ]
    return candidates[0] if candidates else None


def _order_side(order: Any) -> str:
    order_text = repr(order).upper()
    if "BUY" in order_text:
        return "BUY"
    if "SELL" in order_text:
        return "SELL"
    side = getattr(order, "side", "")
    side_name = getattr(side, "name", None)
    if isinstance(side_name, str) and side_name.upper() in {"BUY", "SELL"}:
        return side_name.upper()
    side_text = str(side).upper()
    if side_text in {"BUY", "1"}:
        return "BUY"
    if side_text in {"SELL", "2"}:
        return "SELL"
    raise NativeNautilusExecutionError(f"unsupported Nautilus order side: {side_text}")


def _order_quantity(order: Any) -> float:
    quantity = getattr(order, "quantity", None)
    try:
        return float(str(quantity))
    except (TypeError, ValueError) as exc:
        raise NativeNautilusExecutionError(f"unsupported Nautilus order quantity: {quantity}") from exc


def _spec_declares_tick_semantics(strategy_spec: dict[str, Any]) -> bool:
    for key in ("execution_semantics", "evaluation_mode", "trigger_mode"):
        value = strategy_spec.get(key)
        if isinstance(value, str) and "tick" in value.lower():
            return True
    for text in _strategy_spec_text(strategy_spec):
        normalized = text.lower()
        if "on every tick" in normalized or "each tick" in normalized or "tick-driven" in normalized:
            return True
    return False


def _required_warmup_bars(strategy_spec: dict[str, Any], manifest: dict[str, Any]) -> int:
    paper_runtime = manifest.get("paper_runtime") if isinstance(manifest.get("paper_runtime"), dict) else {}
    override = paper_runtime.get("warmup_min_bars")
    return nautilus_warmup_bar_count(strategy_spec, override=override if isinstance(override, int) else None)


def _strategy_spec_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        texts: list[str] = []
        for item in value.values():
            texts.extend(_strategy_spec_text(item))
        return texts
    if isinstance(value, list):
        texts = []
        for item in value:
            texts.extend(_strategy_spec_text(item))
        return texts
    return []


def _manifest_warmup_messages(manifest: dict[str, Any], template: NativeMarketMessage) -> list[NativeMarketMessage]:
    paper_runtime = manifest.get("paper_runtime") if isinstance(manifest.get("paper_runtime"), dict) else {}
    raw_bars = paper_runtime.get("warmup_bars") or manifest.get("warmup_bars") or []
    if not isinstance(raw_bars, list):
        return []
    messages: list[NativeMarketMessage] = []
    for index, value in enumerate(raw_bars, start=1):
        if not isinstance(value, dict):
            continue
        payload = {
            "venue": template.payload.get("venue"),
            "symbol": template.payload.get("symbol"),
            "timeframe": template.payload.get("timeframe"),
            "closed": True,
            "event_id": f"warmup-{index}",
            "sequence": -len(raw_bars) + index,
            **value,
        }
        messages.append(
            NativeMarketMessage(
                stream_name=template.stream_name,
                stream_id=f"warmup-{index}",
                source_event_id=str(payload.get("event_id") or f"warmup-{index}"),
                payload=payload,
            )
        )
    return messages


def _warmup_event(
    runtime: NautilusRuntimeRecord,
    event_type: str,
    source_event_id: str,
    required_warmup_bars: int,
) -> NautilusRuntimeEventInput:
    return (
        event_type,
        {
            "source": LOCAL_NAUTILUS_SOURCE,
            "engine": LOCAL_NAUTILUS_ENGINE,
            "generation": runtime.generation,
            "strategy_ids": runtime.strategy_ids,
            "required_warmup_bars": required_warmup_bars,
        },
        deterministic_event_key(
            runtime_id=runtime.id,
            source_event_id=f"generation-{runtime.generation}:{source_event_id}",
            event_type=event_type,
        ),
    )


def _nautilus_order_payload(
    *,
    runtime: NautilusRuntimeRecord,
    message: NativeMarketMessage,
    side: str,
    quantity: float,
    order_index: int,
    instrument_id: str,
    order: Any,
    fill_price: float,
    sandbox_account: NautilusSandboxAccount,
) -> dict[str, Any]:
    order_status = str(getattr(order, "status", "SUBMITTED"))
    return {
        "source": LOCAL_NAUTILUS_SOURCE,
        "engine": LOCAL_NAUTILUS_ENGINE,
        "market_data": message.payload,
        "strategy_ids": runtime.strategy_ids,
        "instrument_id": instrument_id,
        "client_order_id": str(order.client_order_id),
        "venue_order_id": "",
        "position_id": f"{runtime.id}:{instrument_id}",
        "side": side,
        "order_type": "MARKET",
        "quantity": str(quantity),
        "order_status": order_status,
        "execution_owner": "nautilus_sandbox",
        "fill": {
            "price": str(fill_price),
            "quantity": str(quantity),
            "fees": "0",
            "liquidity_side": "UNKNOWN",
        },
        "position": {
            "quantity": str(sandbox_account.net_quantity),
            "average_entry_price": str(sandbox_account.average_entry_price),
            "realized_pnl": str(sandbox_account.realized_pnl),
            "unrealized_pnl": str(sandbox_account.unrealized_pnl),
        },
        "nautilus_order": {
            "client_order_id": str(order.client_order_id),
            "instrument_id": str(order.instrument_id),
            "side": str(order.side),
            "quantity": str(order.quantity),
            "order_type": str(order.order_type),
            "status": order_status,
            "order_index": order_index,
        },
        "raw": "nautilus_sandbox_order_fill_bridge",
    }


def _is_closed_bar_message(message: NativeMarketMessage) -> bool:
    if not message.stream_name.endswith(":bars"):
        return False
    closed = str(message.payload.get("closed")).lower()
    return closed in {"1", "true"}


def _bar_identity(message: NativeMarketMessage) -> tuple[str, str, str]:
    return (
        str(message.payload.get("venue") or "").upper(),
        str(message.payload.get("symbol") or "").upper().replace("/", ""),
        str(message.payload.get("timeframe") or ""),
    )


def _bar_type_timeframe(timeframe: str) -> str:
    text = timeframe.strip().lower()
    units = {"m": "MINUTE", "h": "HOUR", "d": "DAY", "w": "WEEK"}
    if len(text) >= 2 and text[:-1].isdigit() and text[-1] in units:
        return f"{int(text[:-1])}-{units[text[-1]]}"
    return timeframe.upper()


def _trade_size(strategy_spec: dict[str, Any]) -> float:
    sizing = strategy_spec.get("position_sizing") if isinstance(strategy_spec.get("position_sizing"), dict) else {}
    try:
        value = float(sizing.get("value", 1))
    except (TypeError, ValueError):
        value = 1.0
    return max(value, 0.0) or 1.0


def _timestamp_ns(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, int | float):
        number = float(value)
        if number > 1_000_000_000_000_000:
            return int(number)
        if number > 1_000_000_000_000:
            return int(number * 1_000_000)
        return int(number * 1_000_000_000)
    text = str(value).strip()
    if not text:
        return 0
    try:
        return _timestamp_ns(float(text))
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1_000_000_000)


def _now_ns() -> int:
    return int(time.time() * 1_000_000_000)


def _price_precision(value: Any) -> int:
    return min(_decimal_places(value), 8)


def _quantity_precision(value: Any) -> int:
    return min(_decimal_places(value), 8)


def _decimal_places(value: Any) -> int:
    text = str(value)
    if "e" in text.lower():
        text = f"{float(value):.12f}"
    if "." not in text:
        return 0
    return len(text.rstrip("0").split(".", 1)[1])


def _artifact_hash(strategy_spec: dict[str, Any]) -> str:
    bundle = nautilus_artifact_bundle(strategy_spec)
    payload = bundle[NAUTILUS_STRATEGY_PATH].encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _session_key(runtime: NautilusRuntimeRecord) -> str:
    manifest = runtime.manifest_json if isinstance(runtime.manifest_json, dict) else {}
    strategy_spec = manifest.get("strategy_spec") if isinstance(manifest.get("strategy_spec"), dict) else {}
    return ":".join(
        [
            runtime.id,
            str(runtime.generation),
            _strategy_fingerprint(strategy_spec, manifest),
            _short_hash(",".join(sorted(runtime.strategy_ids))),
        ]
    )


def _strategy_fingerprint(strategy_spec: dict[str, Any], manifest: dict[str, Any]) -> str:
    payload = {
        "strategy_spec": strategy_spec,
        "paper_runtime": manifest.get("paper_runtime") if isinstance(manifest.get("paper_runtime"), dict) else {},
        "risk_policy": manifest.get("risk_policy") if isinstance(manifest.get("risk_policy"), dict) else {},
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _nautilus_strategy_id(runtime_id: str) -> str:
    return f"SCB{_short_hash(runtime_id)[:8]}-001"
