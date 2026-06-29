from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC
from datetime import datetime
import os
import socket
import time
from typing import Any, Protocol

from strategy_codebot.nautilus_streams import NAUTILUS_MARKET_DATA_MAXLEN
from strategy_codebot.nautilus_streams import MarketDataStreamSubscription
from strategy_codebot.nautilus_streams import bar_message
from strategy_codebot.nautilus_streams import encode_stream_fields
from strategy_codebot.nautilus_streams import market_data_stream_key
from strategy_codebot.nautilus_streams import tick_message
from strategy_codebot.server.database import create_sqlalchemy_repository
from strategy_codebot.server.market_data import _ccxt_symbols
from strategy_codebot.server.repository import ConversationRepository


SUPPORTED_MARKET_DATA_VENUES = ("BINANCE", "BYBIT", "OKX", "KRAKEN")
DEFAULT_BACKFILL_LIMIT = 8
DEFAULT_DISCOVERY_LIMIT = 5000
DEFAULT_FETCH_TIMEOUT_SECONDS = 10.0
DEFAULT_EVENT_ID_CACHE_LIMIT = NAUTILUS_MARKET_DATA_MAXLEN
DEFAULT_LEASE_SECONDS = 30
DEFAULT_POLL_SECONDS = 5.0

_CCXT_EXCHANGE_IDS = {
    "BINANCE": "binance",
    "BYBIT": "bybit",
    "OKX": "okx",
    "KRAKEN": "kraken",
}
_TIMEFRAME_MILLIS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}
@dataclass(frozen=True)
class NormalizedBar:
    event_id: str
    venue: str
    symbol: str
    timeframe: str
    ts_exchange: str
    ts_received: str
    sequence: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    closed: bool
    source: str
    exchange: str
    adapter: str


class ExchangeMarketDataAdapter(Protocol):
    venue: str

    def supports(self, subscription: MarketDataStreamSubscription) -> bool: ...

    def fetch_recent_bars(
        self,
        subscription: MarketDataStreamSubscription,
        *,
        limit: int,
        timeout_seconds: float,
    ) -> list[NormalizedBar]: ...

    def poll_closed_bars(
        self,
        subscription: MarketDataStreamSubscription,
        *,
        since: datetime | None = None,
        limit: int = DEFAULT_BACKFILL_LIMIT,
        timeout_seconds: float = DEFAULT_FETCH_TIMEOUT_SECONDS,
    ) -> list[NormalizedBar]: ...


@dataclass(frozen=True)
class CcxtOhlcvAdapter:
    venue: str
    exchange_id: str
    adapter_name: str = "ccxt_ohlcv_rest"

    def supports(self, subscription: MarketDataStreamSubscription) -> bool:
        return subscription.venue == self.venue and subscription.data_type == "bar" and bool(subscription.timeframe)

    def fetch_recent_bars(
        self,
        subscription: MarketDataStreamSubscription,
        *,
        limit: int,
        timeout_seconds: float,
    ) -> list[NormalizedBar]:
        return asyncio.run(
            self._fetch_recent_bars_async(
                subscription,
                since=None,
                limit=limit,
                timeout_seconds=timeout_seconds,
            )
        )

    async def _fetch_recent_bars_async(
        self,
        subscription: MarketDataStreamSubscription,
        *,
        since: datetime | None,
        limit: int,
        timeout_seconds: float,
    ) -> list[NormalizedBar]:
        if not self.supports(subscription):
            return []
        ccxt = _ccxt_async_module()
        exchange_cls = getattr(ccxt, self.exchange_id)
        exchange = exchange_cls({"enableRateLimit": True, "timeout": int(timeout_seconds * 1000)})
        try:
            market_symbol = await _resolve_async_ccxt_symbol(exchange, subscription.symbol)
            if market_symbol is None:
                return []
            raw_bars = await exchange.fetch_ohlcv(
                market_symbol,
                timeframe=subscription.timeframe,
                since=int(since.timestamp() * 1000) if since is not None else None,
                limit=limit,
            )
        finally:
            close = getattr(exchange, "close", None)
            if callable(close):
                await close()
        return [
            bar
            for bar in (
                self._normalize_ohlcv(subscription, raw_bar, now_ms=int(time.time() * 1000))
                for raw_bar in raw_bars
            )
            if bar is not None
        ]

    def poll_closed_bars(
        self,
        subscription: MarketDataStreamSubscription,
        *,
        since: datetime | None = None,
        limit: int = DEFAULT_BACKFILL_LIMIT,
        timeout_seconds: float = DEFAULT_FETCH_TIMEOUT_SECONDS,
    ) -> list[NormalizedBar]:
        bars = asyncio.run(
            self._fetch_recent_bars_async(
                subscription,
                since=since,
                limit=limit,
                timeout_seconds=timeout_seconds,
            )
        )
        if since is None:
            return bars
        since_ms = int(since.timestamp() * 1000)
        return [bar for bar in bars if _parse_iso_ms(bar.ts_exchange) > since_ms]

    def _normalize_ohlcv(
        self,
        subscription: MarketDataStreamSubscription,
        raw_bar: Any,
        *,
        now_ms: int,
    ) -> NormalizedBar | None:
        if not isinstance(raw_bar, list | tuple) or len(raw_bar) < 6:
            return None
        open_time = int(raw_bar[0])
        timeframe_ms = _timeframe_millis(subscription.timeframe or "")
        if timeframe_ms is not None and open_time + timeframe_ms > now_ms:
            return None
        ts_exchange = datetime.fromtimestamp(open_time / 1000, UTC).isoformat()
        ts_received = datetime.now(UTC).isoformat()
        symbol = subscription.symbol.upper()
        timeframe = str(subscription.timeframe)
        return NormalizedBar(
            event_id=f"exchange_collector:{self.exchange_id}:{symbol}:{timeframe}:{open_time}",
            venue=self.venue,
            symbol=symbol,
            timeframe=timeframe,
            ts_exchange=ts_exchange,
            ts_received=ts_received,
            sequence=open_time,
            open=float(raw_bar[1]),
            high=float(raw_bar[2]),
            low=float(raw_bar[3]),
            close=float(raw_bar[4]),
            volume=float(raw_bar[5] or 0),
            closed=True,
            source="exchange_collector",
            exchange=self.exchange_id,
            adapter=self.adapter_name,
        )


def default_exchange_adapter_registry() -> dict[str, ExchangeMarketDataAdapter]:
    return {
        venue: CcxtOhlcvAdapter(venue=venue, exchange_id=exchange_id)
        for venue, exchange_id in _CCXT_EXCHANGE_IDS.items()
    }


@dataclass
class MarketDataCollector:
    redis_client: Any
    repository: ConversationRepository | None = None
    worker_id: str = field(default_factory=lambda: f"{socket.gethostname()}:{os.getpid()}")
    adapters: dict[str, ExchangeMarketDataAdapter] = field(default_factory=default_exchange_adapter_registry)
    upstream_collectors: set[str] = field(default_factory=set)
    published_event_ids: set[str] = field(default_factory=set)
    published_event_order: deque[str] = field(default_factory=deque)
    next_poll_after_ms_by_stream: dict[str, int] = field(default_factory=dict)
    poll_errors: dict[str, str] = field(default_factory=dict)
    sequence: int = 0

    def ensure_subscription(self, subscription: MarketDataStreamSubscription) -> str:
        stream = subscription.stream_key()
        self.upstream_collectors.add(stream)
        return stream

    def desired_subscriptions(self, *, limit: int = DEFAULT_DISCOVERY_LIMIT) -> list[MarketDataStreamSubscription]:
        if self.repository is None:
            return []
        payloads = self.repository.list_active_nautilus_market_data_subscriptions(
            mode="paper",
            desired_state="running",
            limit=limit,
        )
        subscriptions: list[MarketDataStreamSubscription] = []
        for payload in payloads:
            try:
                subscriptions.append(MarketDataStreamSubscription.from_payload(payload))
            except Exception:
                continue
        return subscriptions

    def reconcile_desired_subscriptions(
        self,
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        limit: int = DEFAULT_DISCOVERY_LIMIT,
    ) -> list[MarketDataStreamSubscription]:
        owned: list[MarketDataStreamSubscription] = []
        for subscription in self.desired_subscriptions(limit=limit):
            stream = self.ensure_subscription(subscription)
            if self.adapter_for(subscription) is None:
                continue
            if self.acquire_stream_lease(stream, lease_seconds=lease_seconds):
                owned.append(subscription)
        return owned

    def poll_once(
        self,
        *,
        backfill_limit: int = DEFAULT_BACKFILL_LIMIT,
        timeout_seconds: float = DEFAULT_FETCH_TIMEOUT_SECONDS,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> int:
        published = 0
        for subscription in self.reconcile_desired_subscriptions(lease_seconds=lease_seconds):
            adapter = self.adapter_for(subscription)
            if adapter is None:
                continue
            stream = subscription.stream_key()
            if not self._should_poll_stream(stream):
                continue
            try:
                since = self._load_stream_cursor(stream)
                bars = adapter.poll_closed_bars(
                    subscription,
                    since=since,
                    limit=backfill_limit,
                    timeout_seconds=timeout_seconds,
                )
            except Exception as exc:
                self.poll_errors[stream] = f"{type(exc).__name__}: {exc}"
                continue
            self.poll_errors.pop(stream, None)
            last_exchange_ms = _parse_iso_ms(bars[-1].ts_exchange) if bars else None
            for bar in bars:
                if self.publish_normalized_bar(bar) is not None:
                    published += 1
            if last_exchange_ms is not None:
                self._persist_stream_cursor(stream, last_exchange_ms)
            self._schedule_next_poll(stream, subscription, last_exchange_ms)
        return published

    def adapter_for(self, subscription: MarketDataStreamSubscription) -> ExchangeMarketDataAdapter | None:
        adapter = self.adapters.get(subscription.venue)
        if adapter is None or not adapter.supports(subscription):
            return None
        return adapter

    def acquire_stream_lease(self, stream_key: str, *, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> bool:
        lease_key = f"mdlease:{stream_key}"
        try:
            owner = self.redis_client.get(lease_key)
            if isinstance(owner, bytes):
                owner = owner.decode("utf-8")
            if owner == self.worker_id:
                expire = getattr(self.redis_client, "expire", None)
                if callable(expire):
                    renewed = bool(expire(lease_key, lease_seconds))
                    if renewed:
                        return True
                    return bool(self.redis_client.set(lease_key, self.worker_id, ex=lease_seconds, nx=True))
                return True
            if owner:
                return False
            acquired = self.redis_client.set(lease_key, self.worker_id, ex=lease_seconds, nx=True)
        except AttributeError:
            acquired = True
        return bool(acquired)

    def publish_tick(
        self,
        *,
        venue: str,
        symbol: str,
        bid: float | None = None,
        ask: float | None = None,
        last: float | None = None,
        size: float | None = None,
        event_id: str | None = None,
    ) -> str:
        self.sequence += 1
        stream = market_data_stream_key(venue=venue, symbol=symbol, data_type="tick")
        payload = tick_message(
            event_id=event_id or f"tick-{self.sequence}",
            venue=venue,
            symbol=symbol,
            sequence=self.sequence,
            bid=bid,
            ask=ask,
            last=last,
            size=size,
        )
        self.upstream_collectors.add(stream)
        return self.redis_client.xadd(
            stream,
            encode_stream_fields(payload),
            maxlen=NAUTILUS_MARKET_DATA_MAXLEN,
            approximate=True,
        )

    def publish_bar(
        self,
        *,
        venue: str,
        symbol: str,
        timeframe: str,
        open: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        closed: bool,
        event_id: str | None = None,
        ts_exchange: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        self.sequence += 1
        stream = market_data_stream_key(venue=venue, symbol=symbol, data_type="bar", timeframe=timeframe)
        payload = bar_message(
            event_id=event_id or f"bar-{self.sequence}",
            venue=venue,
            symbol=symbol,
            timeframe=timeframe,
            sequence=self.sequence,
            open=open,
            high=high,
            low=low,
            close=close,
            volume=volume,
            closed=closed,
            ts_exchange=ts_exchange,
        )
        if metadata:
            payload.update(metadata)
        self.upstream_collectors.add(stream)
        return self.redis_client.xadd(
            stream,
            encode_stream_fields(payload),
            maxlen=NAUTILUS_MARKET_DATA_MAXLEN,
            approximate=True,
        )

    def publish_normalized_bar(self, bar: NormalizedBar) -> str | None:
        if bar.event_id in self.published_event_ids:
            return None
        stream_id = self.publish_bar(
            venue=bar.venue,
            symbol=bar.symbol,
            timeframe=bar.timeframe,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            closed=bar.closed,
            event_id=bar.event_id,
            ts_exchange=bar.ts_exchange,
            metadata={
                "source": bar.source,
                "exchange": bar.exchange,
                "adapter": bar.adapter,
                "ts_received": bar.ts_received,
                "collector_worker_id": self.worker_id,
            },
        )
        self._remember_published_event_id(bar.event_id)
        return stream_id

    def _remember_published_event_id(self, event_id: str) -> None:
        self.published_event_ids.add(event_id)
        self.published_event_order.append(event_id)
        while len(self.published_event_order) > DEFAULT_EVENT_ID_CACHE_LIMIT:
            expired = self.published_event_order.popleft()
            self.published_event_ids.discard(expired)

    def _load_stream_cursor(self, stream_key: str) -> datetime | None:
        try:
            raw = self.redis_client.get(_stream_cursor_key(stream_key))
        except AttributeError:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        return datetime.fromtimestamp(value / 1000, UTC)

    def _persist_stream_cursor(self, stream_key: str, exchange_ts_ms: int) -> None:
        try:
            self.redis_client.set(_stream_cursor_key(stream_key), str(exchange_ts_ms))
        except AttributeError:
            pass

    def _should_poll_stream(self, stream_key: str) -> bool:
        return int(time.time() * 1000) >= int(self.next_poll_after_ms_by_stream.get(stream_key, 0))

    def _schedule_next_poll(
        self,
        stream_key: str,
        subscription: MarketDataStreamSubscription,
        last_exchange_ms: int | None,
    ) -> None:
        timeframe_ms = _timeframe_millis(subscription.timeframe or "")
        if timeframe_ms is None:
            return
        now_ms = int(time.time() * 1000)
        if last_exchange_ms is None:
            self.next_poll_after_ms_by_stream[stream_key] = now_ms + min(timeframe_ms, int(DEFAULT_POLL_SECONDS * 1000))
            return
        next_closed_bar_ms = last_exchange_ms + (2 * timeframe_ms)
        self.next_poll_after_ms_by_stream[stream_key] = max(now_ms + 250, next_closed_bar_ms + 1000)


def create_redis_client(redis_url: str) -> Any:
    from redis import Redis

    return Redis.from_url(redis_url, decode_responses=True)


def main() -> None:
    redis_url = os.getenv("STRATEGY_CODEBOT_REDIS_URL") or (
        f"redis://:{os.getenv('REDIS_PASSWORD', '')}@{os.getenv('REDIS_HOST', 'redis')}:{os.getenv('REDIS_PORT', '6379')}/0"
    )
    database_url = _required_env("STRATEGY_CODEBOT_API_DATABASE_URL")
    collector = MarketDataCollector(
        create_redis_client(redis_url),
        repository=create_sqlalchemy_repository(database_url),
    )
    while True:
        collector.poll_once(
            backfill_limit=_positive_int_env("MARKET_DATA_COLLECTOR_BACKFILL_LIMIT", DEFAULT_BACKFILL_LIMIT),
            timeout_seconds=_positive_float_env("MARKET_DATA_COLLECTOR_FETCH_TIMEOUT_SECONDS", DEFAULT_FETCH_TIMEOUT_SECONDS),
            lease_seconds=_positive_int_env("MARKET_DATA_COLLECTOR_LEASE_SECONDS", DEFAULT_LEASE_SECONDS),
        )
        time.sleep(_positive_float_env("MARKET_DATA_COLLECTOR_POLL_SECONDS", DEFAULT_POLL_SECONDS))


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _positive_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    value = float(raw)
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    value = int(raw)
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def _ccxt_async_module() -> Any:
    import ccxt.async_support as ccxt

    return ccxt


async def _resolve_async_ccxt_symbol(exchange: Any, symbol: str) -> str | None:
    markets = getattr(exchange, "markets", None)
    if not isinstance(markets, dict):
        try:
            markets = await exchange.load_markets()
        except Exception:
            markets = None
    candidates = _ccxt_symbols(symbol)
    if isinstance(markets, dict):
        for candidate in candidates:
            if candidate in markets:
                return candidate
        return None
    return candidates[0] if candidates else None


def _timeframe_millis(timeframe: str) -> int | None:
    return _TIMEFRAME_MILLIS.get(timeframe.strip().lower())


def _parse_iso_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def _stream_cursor_key(stream_key: str) -> str:
    return f"mdcursor:{stream_key}:exchange_ts_ms"


if __name__ == "__main__":
    main()
