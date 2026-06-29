from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
import threading
from typing import Any
from urllib import parse, request


DEFAULT_MARKET_DATA_TIMEOUT_SECONDS = 6.0
DEFAULT_CCXT_EXCHANGES = ("binance", "kraken", "coinbase")
DEFAULT_CCXT_OHLCV_LIMIT = 72
DEFAULT_CCXT_OHLCV_TIMEFRAME = "1h"
MARKET_DATA_PROVIDERS = {"alpha_vantage", "auto", "ccxt", "twelve_data"}
CRYPTO_MARKET_SYMBOLS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "LTC", "MATIC", "TON"}


@dataclass(frozen=True)
class MarketSource:
    id: str
    title: str
    type: str = "internal"
    url: str | None = None

    def to_payload(self) -> dict[str, str]:
        payload = {"id": self.id, "title": self.title, "type": self.type}
        if self.url:
            payload["url"] = self.url
        return payload


@dataclass(frozen=True)
class MarketQuote:
    symbol: str
    price: float
    currency: str
    provider: str
    timestamp: str | None = None
    change: float | None = None
    change_percent: float | None = None
    source: MarketSource | None = None


@dataclass(frozen=True)
class MarketPricePoint:
    time: str
    close: float
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None

    def to_payload(self) -> dict[str, float | str]:
        return {"label": self.time, "value": self.close}


@dataclass(frozen=True)
class MarketSnapshot:
    quote: MarketQuote
    points: tuple[MarketPricePoint, ...] = ()

    def to_chat_payload(self, *, label: str = "Market snapshot") -> dict[str, Any]:
        source = self.quote.source or MarketSource(
            id=_source_id(self.quote.provider),
            title=self.quote.provider,
        )
        return {
            "approximate": True,
            "change": _round_optional(self.quote.change),
            "change_percent": _round_optional(self.quote.change_percent),
            "currency": self.quote.currency,
            "freshness": "source_backed",
            "generated_at": self.quote.timestamp,
            "label": label,
            "price": _format_price(self.quote.price, self.quote.currency),
            "price_points": [point.to_payload() for point in self.points],
            "provider": self.quote.provider,
            "source_count": 1,
            "sources": [source.to_payload()],
            "symbol": self.quote.symbol,
        }


class MarketDataProvider:
    name = "market_data"

    def quote(self, symbol: str) -> MarketQuote | None:
        raise NotImplementedError

    def series(self, symbol: str, *, interval: str = "1h", output_size: int = 24) -> tuple[MarketPricePoint, ...]:
        return ()


class AlphaVantageProvider(MarketDataProvider):
    name = "Alpha Vantage"

    def __init__(self, api_key: str | None, *, timeout_seconds: float = DEFAULT_MARKET_DATA_TIMEOUT_SECONDS) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def quote(self, symbol: str) -> MarketQuote | None:
        if not self.api_key:
            return None
        payload = _fetch_json(
            "https://www.alphavantage.co/query",
            {
                "apikey": self.api_key,
                "from_currency": _base_symbol(symbol),
                "function": "CURRENCY_EXCHANGE_RATE",
                "to_currency": "USD",
            },
            timeout_seconds=self.timeout_seconds,
        )
        rate = payload.get("Realtime Currency Exchange Rate") if isinstance(payload, dict) else None
        if not isinstance(rate, dict):
            return None
        price = _float_value(rate.get("5. Exchange Rate"))
        if price is None:
            return None
        timestamp = _string_value(rate.get("6. Last Refreshed"))
        return MarketQuote(
            symbol=_base_symbol(symbol),
            price=price,
            currency=_string_value(rate.get("3. To_Currency Code")) or "USD",
            provider=self.name,
            timestamp=timestamp,
            source=MarketSource(id="alpha-vantage", title=self.name, url="https://www.alphavantage.co/"),
        )

    def series(self, symbol: str, *, interval: str = "1d", output_size: int = 24) -> tuple[MarketPricePoint, ...]:
        if not self.api_key:
            return ()
        payload = _fetch_json(
            "https://www.alphavantage.co/query",
            {
                "apikey": self.api_key,
                "function": "DIGITAL_CURRENCY_DAILY",
                "market": "USD",
                "symbol": _base_symbol(symbol),
            },
            timeout_seconds=self.timeout_seconds,
        )
        values = payload.get("Time Series (Digital Currency Daily)") if isinstance(payload, dict) else None
        if not isinstance(values, dict):
            return ()
        points: list[MarketPricePoint] = []
        for timestamp, item in sorted(values.items(), reverse=True)[:output_size]:
            if not isinstance(item, dict):
                continue
            close = _float_value(item.get("4. close") or item.get("4a. close (USD)"))
            if close is None:
                continue
            points.append(
                MarketPricePoint(
                    time=str(timestamp),
                    close=close,
                    open=_float_value(item.get("1. open") or item.get("1a. open (USD)")),
                    high=_float_value(item.get("2. high") or item.get("2a. high (USD)")),
                    low=_float_value(item.get("3. low") or item.get("3a. low (USD)")),
                    volume=_float_value(item.get("5. volume")),
                )
            )
        return tuple(reversed(points))


class TwelveDataProvider(MarketDataProvider):
    name = "Twelve Data"

    def __init__(self, api_key: str | None, *, timeout_seconds: float = DEFAULT_MARKET_DATA_TIMEOUT_SECONDS) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def quote(self, symbol: str) -> MarketQuote | None:
        if not self.api_key:
            return None
        payload = _fetch_json(
            "https://api.twelvedata.com/quote",
            {"apikey": self.api_key, "symbol": _twelve_symbol(symbol)},
            timeout_seconds=self.timeout_seconds,
        )
        if not isinstance(payload, dict) or payload.get("status") == "error":
            return None
        price = _float_value(payload.get("close") or payload.get("price"))
        if price is None:
            return None
        return MarketQuote(
            symbol=_base_symbol(symbol),
            price=price,
            currency=_string_value(payload.get("currency")) or "USD",
            provider=self.name,
            timestamp=_string_value(payload.get("datetime")) or _now_iso(),
            change=_float_value(payload.get("change")),
            change_percent=_float_value(payload.get("percent_change")),
            source=MarketSource(id="twelve-data", title=self.name, url="https://twelvedata.com/"),
        )

    def series(self, symbol: str, *, interval: str = "1h", output_size: int = 24) -> tuple[MarketPricePoint, ...]:
        if not self.api_key:
            return ()
        payload = _fetch_json(
            "https://api.twelvedata.com/time_series",
            {
                "apikey": self.api_key,
                "interval": interval,
                "outputsize": str(output_size),
                "symbol": _twelve_symbol(symbol),
            },
            timeout_seconds=self.timeout_seconds,
        )
        values = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(values, list):
            return ()
        points: list[MarketPricePoint] = []
        for item in reversed(values):
            if not isinstance(item, dict):
                continue
            close = _float_value(item.get("close"))
            timestamp = _string_value(item.get("datetime"))
            if close is None or timestamp is None:
                continue
            points.append(
                MarketPricePoint(
                    time=timestamp,
                    close=close,
                    open=_float_value(item.get("open")),
                    high=_float_value(item.get("high")),
                    low=_float_value(item.get("low")),
                    volume=_float_value(item.get("volume")),
                )
            )
        return tuple(points)


class CcxtMarketDataProvider(MarketDataProvider):
    name = "CCXT"

    def __init__(
        self,
        exchange_ids: tuple[str, ...] = DEFAULT_CCXT_EXCHANGES,
        *,
        exchange_factory: Any | None = None,
        output_size: int = DEFAULT_CCXT_OHLCV_LIMIT,
        timeframe: str = DEFAULT_CCXT_OHLCV_TIMEFRAME,
        timeout_seconds: float = DEFAULT_MARKET_DATA_TIMEOUT_SECONDS,
    ) -> None:
        self.exchange_factory = exchange_factory
        self.exchange_ids = tuple(exchange_id for exchange_id in exchange_ids if exchange_id) or DEFAULT_CCXT_EXCHANGES
        self.output_size = output_size
        self.timeframe = timeframe
        self.timeout_seconds = timeout_seconds

    def quote(self, symbol: str) -> MarketQuote | None:
        for exchange in self._exchanges():
            try:
                market_symbol = _resolve_ccxt_symbol(exchange, symbol)
                if market_symbol is None:
                    continue
                ticker = exchange.fetch_ticker(market_symbol)
            except Exception:
                continue
            if not isinstance(ticker, dict):
                continue
            price = _float_value(ticker.get("last") or ticker.get("close") or ticker.get("bid") or ticker.get("ask"))
            if price is None:
                continue
            provider = _ccxt_provider_label(exchange)
            return MarketQuote(
                symbol=_base_symbol(symbol),
                price=price,
                currency=_quote_currency_from_market_symbol(market_symbol),
                provider=provider,
                timestamp=_ccxt_timestamp(ticker),
                change=_float_value(ticker.get("change")),
                change_percent=_float_value(ticker.get("percentage")),
                source=MarketSource(
                    id=_source_id(provider),
                    title=provider,
                    url="https://docs.ccxt.com/",
                ),
            )
        return None

    def series(self, symbol: str, *, interval: str = "1h", output_size: int = 24) -> tuple[MarketPricePoint, ...]:
        timeframe = interval or self.timeframe
        limit = output_size or self.output_size
        for exchange in self._exchanges():
            if getattr(exchange, "has", {}).get("fetchOHLCV") is False:
                continue
            try:
                market_symbol = _resolve_ccxt_symbol(exchange, symbol)
                if market_symbol is None:
                    continue
                candles = exchange.fetch_ohlcv(market_symbol, timeframe=timeframe, limit=limit)
            except Exception:
                continue
            if not isinstance(candles, list):
                continue
            points: list[MarketPricePoint] = []
            for candle in candles:
                if not isinstance(candle, (list, tuple)) or len(candle) < 5:
                    continue
                timestamp = _float_value(candle[0])
                close = _float_value(candle[4])
                if timestamp is None or close is None:
                    continue
                points.append(
                    MarketPricePoint(
                        time=datetime.fromtimestamp(timestamp / 1000, UTC).isoformat(),
                        close=close,
                        open=_float_value(candle[1]),
                        high=_float_value(candle[2]),
                        low=_float_value(candle[3]),
                        volume=_float_value(candle[5]) if len(candle) > 5 else None,
                    )
                )
            if points:
                return tuple(points)
        return ()

    def _exchanges(self):
        for exchange_id in self.exchange_ids:
            exchange = _create_ccxt_exchange(
                exchange_id,
                exchange_factory=self.exchange_factory,
                timeout_seconds=self.timeout_seconds,
            )
            if exchange is not None:
                yield exchange


class MarketDataGateway:
    def __init__(
        self,
        *,
        alpha_vantage: AlphaVantageProvider | None = None,
        ccxt: CcxtMarketDataProvider | None = None,
        preferred_provider: str = "auto",
        twelve_data: TwelveDataProvider | None = None,
    ) -> None:
        self.alpha_vantage = alpha_vantage
        self.ccxt = ccxt
        self.preferred_provider = preferred_provider if preferred_provider in MARKET_DATA_PROVIDERS else "auto"
        self.twelve_data = twelve_data

    @classmethod
    def from_env(cls) -> MarketDataGateway:
        timeout_seconds = _timeout_seconds(os.environ.get("MARKET_DATA_TIMEOUT_MS"))
        return cls(
            alpha_vantage=AlphaVantageProvider(
                os.environ.get("ALPHA_VANTAGE_API_KEY"),
                timeout_seconds=timeout_seconds,
            ),
            ccxt=CcxtMarketDataProvider(
                _ccxt_exchange_ids(os.environ.get("CCXT_EXCHANGE_ID"), os.environ.get("CCXT_FALLBACK_EXCHANGES")),
                output_size=_positive_int(os.environ.get("CCXT_OHLCV_LIMIT"), DEFAULT_CCXT_OHLCV_LIMIT),
                timeframe=os.environ.get("CCXT_OHLCV_TIMEFRAME") or DEFAULT_CCXT_OHLCV_TIMEFRAME,
                timeout_seconds=timeout_seconds,
            ),
            preferred_provider=os.environ.get("MARKET_DATA_PROVIDER", "auto"),
            twelve_data=TwelveDataProvider(
                os.environ.get("TWELVE_DATA_API_KEY"),
                timeout_seconds=timeout_seconds,
            ),
        )

    def snapshot(self, symbol: str, *, include_series: bool = False, tier: str = "free") -> MarketSnapshot | None:
        for provider in self._provider_order(symbol, include_series=include_series, tier=tier):
            quote = _safe_quote(provider, symbol)
            if quote is None:
                continue
            points = (
                _safe_series(provider, symbol, interval=getattr(provider, "timeframe", "1h"), output_size=getattr(provider, "output_size", 24))
                if include_series
                else ()
            )
            return MarketSnapshot(quote=quote, points=points if len(points) >= 3 else ())
        return None

    def _provider_order(self, symbol: str, *, include_series: bool, tier: str) -> tuple[MarketDataProvider, ...]:
        providers = {
            "alpha_vantage": self.alpha_vantage,
            "ccxt": self.ccxt,
            "twelve_data": self.twelve_data,
        }
        if self.preferred_provider in providers:
            preferred = providers[self.preferred_provider]
            return _dedupe_providers((preferred, self.twelve_data, self.alpha_vantage))
        if _is_crypto_market_symbol(symbol):
            if include_series:
                return _dedupe_providers((self.ccxt, self.twelve_data, self.alpha_vantage))
            return _dedupe_providers((self.ccxt, self.alpha_vantage, self.twelve_data))
        if include_series or tier in {"paid_medium", "paid_high"}:
            return _dedupe_providers((self.twelve_data, self.alpha_vantage))
        return _dedupe_providers((self.alpha_vantage, self.twelve_data))


MarketDataRequestKey = tuple[str, bool, str]


@dataclass(frozen=True)
class MarketDataSubscription:
    key: MarketDataRequestKey
    _future: Future[MarketSnapshot | None]

    def result(self, timeout: float | None = None) -> MarketSnapshot | None:
        return self._future.result(timeout=timeout)


class SharedMarketDataFanout:
    def __init__(self, collector: MarketDataGateway, *, max_workers: int = 4) -> None:
        self.collector = collector
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="market-data-fanout")
        self._lock = threading.Lock()
        self._inflight: dict[MarketDataRequestKey, Future[MarketSnapshot | None]] = {}

    def subscribe(
        self,
        symbol: str,
        *,
        include_series: bool = False,
        tier: str = "free",
    ) -> MarketDataSubscription:
        key = (symbol.upper(), include_series, tier)
        with self._lock:
            future = self._inflight.get(key)
            if future is None:
                future = self._executor.submit(self.collector.snapshot, key[0], include_series=include_series, tier=tier)
                self._inflight[key] = future
                future.add_done_callback(lambda completed, request_key=key: self._clear_inflight(request_key, completed))
        return MarketDataSubscription(key=key, _future=future)

    def snapshot(self, symbol: str, *, include_series: bool = False, tier: str = "free") -> MarketSnapshot | None:
        return self.subscribe(symbol, include_series=include_series, tier=tier).result()

    def inflight_count(self) -> int:
        with self._lock:
            return len(self._inflight)

    def _clear_inflight(self, key: MarketDataRequestKey, future: Future[MarketSnapshot | None]) -> None:
        with self._lock:
            if self._inflight.get(key) is future:
                self._inflight.pop(key, None)


def market_data_context(snapshot: MarketSnapshot | None) -> str | None:
    if snapshot is None:
        return None
    quote = snapshot.quote
    parts = [
        f"{quote.symbol} quote",
        f"price={_format_price(quote.price, quote.currency)}",
        f"provider={quote.provider}",
    ]
    if quote.change_percent is not None:
        parts.append(f"change_percent={quote.change_percent:.2f}%")
    if quote.timestamp:
        parts.append(f"timestamp={quote.timestamp}")
    if snapshot.points:
        parts.append(f"series_points={len(snapshot.points)}")
    return "Market data context for this answer: " + ", ".join(parts) + "."


def _safe_quote(provider: MarketDataProvider, symbol: str) -> MarketQuote | None:
    try:
        return provider.quote(symbol)
    except Exception:
        return None


def _safe_series(
    provider: MarketDataProvider,
    symbol: str,
    *,
    interval: str = "1h",
    output_size: int = 24,
) -> tuple[MarketPricePoint, ...]:
    try:
        return provider.series(symbol, interval=interval, output_size=output_size)
    except Exception:
        return ()


def _fetch_json(url: str, query: dict[str, str], *, timeout_seconds: float) -> dict[str, Any]:
    encoded = parse.urlencode(query)
    req = request.Request(f"{url}?{encoded}", headers={"User-Agent": "strategy-codebot-market-data/1.0"})
    with request.urlopen(req, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _base_symbol(symbol: str) -> str:
    upper = symbol.upper()
    if "/" in upper:
        upper = upper.split("/", 1)[0]
    if upper.endswith("USDT"):
        upper = upper[:-4]
    if upper.endswith("USD") and len(upper) > 3:
        upper = upper[:-3]
    return upper or "ETH"


def _ccxt_exchange_ids(primary: str | None, fallback: str | None) -> tuple[str, ...]:
    exchange_ids: list[str] = []
    for value in (primary, fallback):
        if not value:
            continue
        exchange_ids.extend(item.strip() for item in value.split(",") if item.strip())
    return tuple(dict.fromkeys(exchange_ids)) or DEFAULT_CCXT_EXCHANGES


def _ccxt_provider_label(exchange: Any) -> str:
    exchange_id = _string_value(getattr(exchange, "id", None)) or exchange.__class__.__name__
    return f"{exchange_id.title()} (CCXT)"


def _ccxt_timestamp(ticker: dict[str, Any]) -> str | None:
    datetime_value = _string_value(ticker.get("datetime"))
    if datetime_value:
        return datetime_value
    timestamp = _float_value(ticker.get("timestamp"))
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp / 1000, UTC).isoformat()


def _ccxt_symbols(symbol: str) -> tuple[str, ...]:
    base = _base_symbol(symbol)
    if "/" in symbol:
        return (symbol.upper(),)
    return (f"{base}/USDT", f"{base}/USD")


def _create_ccxt_exchange(exchange_id: str, *, exchange_factory: Any | None, timeout_seconds: float) -> Any | None:
    if exchange_factory is not None:
        return exchange_factory(exchange_id)
    try:
        import ccxt  # type: ignore[import-untyped]

        exchange_cls = getattr(ccxt, exchange_id)
        return exchange_cls(
            {
                "enableRateLimit": True,
                "timeout": int(timeout_seconds * 1000),
            }
        )
    except Exception:
        return None


def _dedupe_providers(providers: tuple[MarketDataProvider | None, ...]) -> tuple[MarketDataProvider, ...]:
    deduped: list[MarketDataProvider] = []
    seen: set[int] = set()
    for provider in providers:
        if provider is None:
            continue
        provider_id = id(provider)
        if provider_id in seen:
            continue
        seen.add(provider_id)
        deduped.append(provider)
    return tuple(deduped)


def _is_crypto_market_symbol(symbol: str) -> bool:
    return _base_symbol(symbol) in CRYPTO_MARKET_SYMBOLS


def _positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value or "")
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _quote_currency_from_market_symbol(symbol: str) -> str:
    if "/" not in symbol:
        return "USD"
    return symbol.rsplit("/", 1)[1].upper()


def _resolve_ccxt_symbol(exchange: Any, symbol: str) -> str | None:
    markets = getattr(exchange, "markets", None)
    if not isinstance(markets, dict):
        try:
            markets = exchange.load_markets()
        except Exception:
            markets = None
    candidates = _ccxt_symbols(symbol)
    if isinstance(markets, dict):
        for candidate in candidates:
            if candidate in markets:
                return candidate
        return None
    return candidates[0] if candidates else None


def _twelve_symbol(symbol: str) -> str:
    base = _base_symbol(symbol)
    if base in {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"}:
        return f"{base}/USD"
    return symbol.upper()


def _source_id(provider: str) -> str:
    return provider.lower().replace(" ", "-")


def _float_value(value: Any) -> float | None:
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _format_price(value: float, currency: str) -> str:
    prefix = "$" if currency.upper() in {"USD", "USDT"} else f"{currency.upper()} "
    return f"{prefix}{value:,.2f}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _round_optional(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _string_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _timeout_seconds(value: str | None) -> float:
    try:
        timeout_ms = int(value or "")
    except ValueError:
        return DEFAULT_MARKET_DATA_TIMEOUT_SECONDS
    if timeout_ms <= 0:
        return DEFAULT_MARKET_DATA_TIMEOUT_SECONDS
    return max(0.5, timeout_ms / 1000)
