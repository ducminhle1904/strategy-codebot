from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from typing import Any, Protocol


NAUTILUS_MARKET_DATA_MAXLEN = 100_000
NAUTILUS_COMMAND_STREAM_PREFIX = "nrt"
NAUTILUS_MARKET_DATA_STREAM_PREFIX = "md"


class RedisStreamClient(Protocol):
    def xadd(
        self,
        name: str,
        fields: dict[str, str],
        *,
        id: str = "*",
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str: ...

    def xread(
        self,
        streams: dict[str, str],
        *,
        count: int | None = None,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]: ...


@dataclass(frozen=True)
class MarketDataStreamSubscription:
    venue: str
    symbol: str
    data_type: str
    timeframe: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "MarketDataStreamSubscription":
        data_type = str(payload.get("data_type") or payload.get("type") or "bar").lower()
        timeframe = str(payload.get("timeframe") or "") or None
        if data_type == "bars":
            data_type = "bar"
        if data_type == "ticks":
            data_type = "tick"
        return cls(
            venue=str(payload.get("venue") or "").upper(),
            symbol=str(payload.get("symbol") or "").upper(),
            data_type=data_type,
            timeframe=timeframe,
        )

    def stream_key(self) -> str:
        if self.data_type == "tick":
            return market_data_stream_key(venue=self.venue, symbol=self.symbol, data_type="tick")
        if not self.timeframe:
            raise ValueError("bar subscriptions require timeframe")
        return market_data_stream_key(
            venue=self.venue,
            symbol=self.symbol,
            data_type="bar",
            timeframe=self.timeframe,
        )


def market_data_stream_key(*, venue: str, symbol: str, data_type: str, timeframe: str | None = None) -> str:
    normalized_type = data_type.lower()
    normalized_venue = venue.upper()
    normalized_symbol = symbol.upper()
    if normalized_type == "tick":
        return f"{NAUTILUS_MARKET_DATA_STREAM_PREFIX}:{normalized_venue}:{normalized_symbol}:ticks"
    if normalized_type == "bar":
        if not timeframe:
            raise ValueError("bar streams require timeframe")
        return f"{NAUTILUS_MARKET_DATA_STREAM_PREFIX}:{normalized_venue}:{normalized_symbol}:{timeframe}:bars"
    raise ValueError(f"unsupported market-data stream type: {data_type}")


def runtime_command_stream_key(runtime_id: str) -> str:
    return f"{NAUTILUS_COMMAND_STREAM_PREFIX}:{runtime_id}:commands"


def deterministic_event_key(*, runtime_id: str, source_event_id: str, event_type: str) -> str:
    payload = f"{runtime_id}:{source_event_id}:{event_type}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def encode_stream_fields(payload: dict[str, Any]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, bool):
            fields[key] = "1" if value else "0"
        elif isinstance(value, int | float | str):
            fields[key] = str(value)
        else:
            fields[key] = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return fields


def decode_stream_fields(fields: dict[str, Any]) -> dict[str, Any]:
    decoded: dict[str, Any] = {}
    for key, value in fields.items():
        if isinstance(value, bytes):
            text = value.decode("utf-8")
        else:
            text = str(value)
        try:
            decoded[key] = json.loads(text)
        except json.JSONDecodeError:
            decoded[key] = text
    return decoded


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def tick_message(
    *,
    event_id: str,
    venue: str,
    symbol: str,
    sequence: int,
    ts_exchange: str | None = None,
    bid: float | None = None,
    ask: float | None = None,
    last: float | None = None,
    size: float | None = None,
) -> dict[str, Any]:
    received = now_iso()
    return {
        "event_id": event_id,
        "venue": venue.upper(),
        "symbol": symbol.upper(),
        "ts_exchange": ts_exchange or received,
        "ts_received": received,
        "sequence": sequence,
        "bid": bid,
        "ask": ask,
        "last": last,
        "size": size,
    }


def bar_message(
    *,
    event_id: str,
    venue: str,
    symbol: str,
    timeframe: str,
    sequence: int,
    open: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    closed: bool,
    ts_exchange: str | None = None,
) -> dict[str, Any]:
    received = now_iso()
    return {
        "event_id": event_id,
        "venue": venue.upper(),
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "ts_exchange": ts_exchange or received,
        "ts_received": received,
        "sequence": sequence,
        "open": open,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "closed": closed,
    }


def runtime_market_streams(data_subscriptions: list[dict[str, Any]]) -> dict[str, str]:
    streams: dict[str, str] = {}
    for payload in data_subscriptions:
        subscription = MarketDataStreamSubscription.from_payload(payload)
        streams.setdefault(subscription.stream_key(), "0-0")
    return streams
