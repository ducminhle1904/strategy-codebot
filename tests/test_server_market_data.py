from dataclasses import dataclass, field
import threading
from typing import Iterable

from fastapi.testclient import TestClient

from strategy_codebot.server import ServerAppConfig, create_app
from strategy_codebot.server.database import create_sqlite_repository
from strategy_codebot.server.llm_clients import LLMClientEvent
from strategy_codebot.server.market_data import AlphaVantageProvider
from strategy_codebot.server.market_data import CcxtMarketDataProvider
from strategy_codebot.server.market_data import MarketDataGateway
from strategy_codebot.server.market_data import MarketPricePoint
from strategy_codebot.server.market_data import MarketQuote
from strategy_codebot.server.market_data import MarketSnapshot
from strategy_codebot.server.market_data import MarketSource
from strategy_codebot.server.market_data import SharedMarketDataFanout
from strategy_codebot.server.market_data import TwelveDataProvider
from strategy_codebot.server.market_data import market_data_context
from server_helpers import parse_sse


AUTH_A = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-a"}


@dataclass
class RecordingLLMClient:
    events: list[LLMClientEvent]
    model: str = "fake-responses-model"
    calls: int = 0
    calls_messages: list[list[dict[str, str]]] = field(default_factory=list)
    calls_tools: list[list[dict]] = field(default_factory=list)

    def ensure_configured(self) -> None:
        return None

    def stream(self, *, messages: list[dict[str, str]], tools: list[dict]) -> Iterable[LLMClientEvent]:
        self.calls += 1
        self.calls_messages.append(messages)
        self.calls_tools.append(tools)
        return list(self.events)


class FakeExchange:
    id = "binance"
    has = {"fetchOHLCV": True}

    def __init__(self, exchange_id: str = "binance", *, markets: dict | None = None, fail: bool = False) -> None:
        self.id = exchange_id
        self.fail = fail
        self.markets = markets
        self.loaded = False
        self.ticker_symbols: list[str] = []
        self.ohlcv_calls: list[tuple[str, str, int]] = []

    def load_markets(self):
        self.loaded = True
        self.markets = self.markets or {"ETH/USDT": {}, "ETH/USD": {}}
        return self.markets

    def fetch_ticker(self, symbol: str):
        if self.fail:
            raise RuntimeError("exchange unavailable")
        self.ticker_symbols.append(symbol)
        return {
            "change": 12.1,
            "datetime": "2026-06-20T08:00:00Z",
            "last": 1728.32,
            "percentage": 0.968,
        }

    def fetch_ohlcv(self, symbol: str, *, timeframe: str, limit: int):
        if self.fail:
            raise RuntimeError("exchange unavailable")
        self.ohlcv_calls.append((symbol, timeframe, limit))
        return [
            [1_787_360_000_000, 1700, 1710, 1690, 1705, 100],
            [1_787_363_600_000, 1705, 1720, 1700, 1715, 120],
            [1_787_367_200_000, 1715, 1730, 1710, 1728.32, 150],
        ]


def test_alpha_vantage_quote_maps_to_market_quote(monkeypatch) -> None:
    def fake_fetch_json(url, query, *, timeout_seconds):
        assert query["function"] == "CURRENCY_EXCHANGE_RATE"
        assert query["from_currency"] == "ETH"
        return {
            "Realtime Currency Exchange Rate": {
                "3. To_Currency Code": "USD",
                "5. Exchange Rate": "1701.43",
                "6. Last Refreshed": "2026-06-20 08:00:00",
            }
        }

    monkeypatch.setattr("strategy_codebot.server.market_data._fetch_json", fake_fetch_json)

    quote = AlphaVantageProvider("key").quote("ETH")

    assert quote is not None
    assert quote.symbol == "ETH"
    assert quote.price == 1701.43
    assert quote.currency == "USD"
    assert quote.provider == "Alpha Vantage"


def test_alpha_vantage_daily_series_maps_to_price_points(monkeypatch) -> None:
    def fake_fetch_json(url, query, *, timeout_seconds):
        assert query["function"] == "DIGITAL_CURRENCY_DAILY"
        assert query["symbol"] == "ETH"
        return {
            "Time Series (Digital Currency Daily)": {
                "2026-06-20": {"4. close": "1701.43", "5. volume": "1200"},
                "2026-06-19": {"4. close": "1688.00", "5. volume": "1100"},
                "2026-06-18": {"4. close": "1695.25", "5. volume": "1000"},
            }
        }

    monkeypatch.setattr("strategy_codebot.server.market_data._fetch_json", fake_fetch_json)

    points = AlphaVantageProvider("key").series("ETH", output_size=3)

    assert [point.time for point in points] == ["2026-06-18", "2026-06-19", "2026-06-20"]
    assert [point.close for point in points] == [1695.25, 1688.0, 1701.43]


def test_twelve_data_quote_and_series_map_to_normalized_shapes(monkeypatch) -> None:
    def fake_fetch_json(url, query, *, timeout_seconds):
        if url.endswith("/quote"):
            return {
                "change": "12.10",
                "close": "1721.95",
                "currency": "USD",
                "datetime": "2026-06-20",
                "percent_change": "0.71",
            }
        return {
            "values": [
                {"close": "1721.95", "datetime": "2026-06-20 08:00:00"},
                {"close": "1710.00", "datetime": "2026-06-20 07:00:00"},
                {"close": "1699.50", "datetime": "2026-06-20 06:00:00"},
            ]
        }

    monkeypatch.setattr("strategy_codebot.server.market_data._fetch_json", fake_fetch_json)
    provider = TwelveDataProvider("key")

    quote = provider.quote("ETH")
    points = provider.series("ETH", interval="1h")

    assert quote is not None
    assert quote.price == 1721.95
    assert quote.change_percent == 0.71
    assert quote.provider == "Twelve Data"
    assert [point.close for point in points] == [1699.5, 1710.0, 1721.95]


def test_ccxt_quote_maps_to_market_quote() -> None:
    exchange = FakeExchange(markets={"ETH/USDT": {}})
    provider = CcxtMarketDataProvider(("binance",), exchange_factory=lambda exchange_id: exchange)

    quote = provider.quote("ETH")

    assert quote is not None
    assert quote.symbol == "ETH"
    assert quote.price == 1728.32
    assert quote.currency == "USDT"
    assert quote.provider == "Binance (CCXT)"
    assert quote.change_percent == 0.968
    assert exchange.ticker_symbols == ["ETH/USDT"]


def test_ccxt_series_maps_ohlcv_to_chronological_price_points() -> None:
    exchange = FakeExchange(markets={"ETH/USDT": {}})
    provider = CcxtMarketDataProvider(
        ("binance",),
        exchange_factory=lambda exchange_id: exchange,
        output_size=72,
        timeframe="1h",
    )

    points = provider.series("ETH", interval="1h", output_size=72)

    assert [point.close for point in points] == [1705, 1715, 1728.32]
    assert exchange.ohlcv_calls == [("ETH/USDT", "1h", 72)]


def test_ccxt_symbol_fallback_tries_usdt_then_usd() -> None:
    exchange = FakeExchange(markets={"ETH/USD": {}})
    provider = CcxtMarketDataProvider(("coinbase",), exchange_factory=lambda exchange_id: exchange)

    quote = provider.quote("ETH")

    assert quote is not None
    assert quote.currency == "USD"
    assert exchange.ticker_symbols == ["ETH/USD"]


def test_ccxt_exchange_failure_falls_back_to_next_exchange() -> None:
    exchanges = {
        "binance": FakeExchange("binance", fail=True),
        "kraken": FakeExchange("kraken", markets={"ETH/USD": {}}),
    }
    provider = CcxtMarketDataProvider(
        ("binance", "kraken"),
        exchange_factory=lambda exchange_id: exchanges[exchange_id],
    )

    quote = provider.quote("ETH")

    assert quote is not None
    assert quote.provider == "Kraken (CCXT)"
    assert exchanges["kraken"].ticker_symbols == ["ETH/USD"]


def test_market_gateway_auto_prefers_ccxt_for_crypto_analysis() -> None:
    gateway = MarketDataGateway(
        alpha_vantage=AlphaVantageProvider("key"),
        ccxt=CcxtMarketDataProvider(("binance",), exchange_factory=lambda exchange_id: FakeExchange(exchange_id)),
        preferred_provider="auto",
        twelve_data=TwelveDataProvider("key"),
    )

    providers = gateway._provider_order("ETH", include_series=True, tier="free")

    assert [provider.name for provider in providers] == ["CCXT", "Twelve Data", "Alpha Vantage"]


def test_market_snapshot_context_is_user_safe_and_concise() -> None:
    snapshot = MarketSnapshot(
        quote=MarketQuote(symbol="ETH", price=1721.95, currency="USD", provider="Twelve Data"),
        points=(MarketPricePoint(time="08:00", close=1721.95),),
    )

    context = market_data_context(snapshot)

    assert context is not None
    assert "ETH quote" in context
    assert "raw" not in context.lower()


def test_market_data_fanout_shares_one_upstream_collector_for_100_subscribers() -> None:
    release = threading.Event()
    started = threading.Event()

    class BlockingGateway:
        def __init__(self) -> None:
            self.calls = 0

        def snapshot(self, symbol: str, *, include_series: bool = False, tier: str = "free"):
            self.calls += 1
            started.set()
            assert symbol == "ETH"
            assert include_series is True
            assert tier == "free"
            release.wait(timeout=2)
            return MarketSnapshot(
                quote=MarketQuote(symbol="ETH", price=1721.95, currency="USD", provider="test"),
            )

    gateway = BlockingGateway()
    fanout = SharedMarketDataFanout(gateway)

    subscribers = [fanout.subscribe("ETH", include_series=True, tier="free") for _ in range(100)]

    assert started.wait(timeout=1)
    assert gateway.calls == 1

    release.set()
    results = [subscriber.result(timeout=1) for subscriber in subscribers]

    assert gateway.calls == 1
    assert len(results) == 100
    assert all(result is results[0] for result in results)


class FakeMarketDataGateway:
    def __init__(self) -> None:
        self.include_series: list[bool] = []

    def snapshot(self, symbol: str, *, include_series: bool = False, tier: str = "free"):
        self.include_series.append(include_series)
        return MarketSnapshot(
            quote=MarketQuote(
                symbol=symbol,
                price=1721.95,
                currency="USD",
                provider="Twelve Data",
                timestamp="2026-06-20T08:00:00Z",
                change=12.1,
                change_percent=0.71,
                source=MarketSource(id="twelve-data", title="Twelve Data", url="https://twelvedata.com/"),
            ),
            points=(
                MarketPricePoint(time="06:00", close=1699.5),
                MarketPricePoint(time="07:00", close=1710.0),
                MarketPricePoint(time="08:00", close=1721.95),
            ),
        )


def test_market_snapshot_uses_gateway_without_web_search_tool(tmp_path) -> None:
    llm = RecordingLLMClient([LLMClientEvent(type="message.delta", text="ETH is firming from the latest quote.")])
    market_gateway = FakeMarketDataGateway()
    client = TestClient(
        create_app(
            config=ServerAppConfig(
                repository=create_sqlite_repository(),
                artifact_root=tmp_path,
                llm_client=llm,
                market_data_gateway=market_gateway,
            )
        )
    )
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "analyze current ETH market", "web_search": "on"},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    snapshot = next(frame for frame in frames if frame["event"] == "chat.market_snapshot")
    provider = next(frame for frame in frames if frame["event"] == "provider.started")
    terminal = next(frame for frame in frames if frame["event"] == "run.completed")

    assert snapshot["data"]["payload"]["provider"] == "Twelve Data"
    assert snapshot["data"]["payload"]["price"] == "$1,721.95"
    assert snapshot["data"]["payload"]["change_percent"] == 0.71
    assert len(snapshot["data"]["payload"]["price_points"]) == 3
    assert provider["data"]["payload"]["web_search_enabled"] is False
    assert terminal["data"]["payload"]["status"] == "completed"
    assert llm.calls_tools[-1] == []
    assert market_gateway.include_series == [True]
    assert "Market data context" in llm.calls_messages[-1][0]["content"]


def test_market_snapshot_uses_ccxt_provider_with_series(tmp_path) -> None:
    exchange = FakeExchange(markets={"ETH/USDT": {}})
    llm = RecordingLLMClient([LLMClientEvent(type="message.delta", text="ETH has a provider-backed snapshot.")])
    client = TestClient(
        create_app(
            config=ServerAppConfig(
                repository=create_sqlite_repository(),
                artifact_root=tmp_path,
                llm_client=llm,
                market_data_gateway=MarketDataGateway(
                    ccxt=CcxtMarketDataProvider(
                        ("binance",),
                        exchange_factory=lambda exchange_id: exchange,
                        output_size=72,
                        timeframe="1h",
                    ),
                    preferred_provider="auto",
                ),
            )
        )
    )
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "analyze current ETH market", "web_search": "on"},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    snapshot = next(frame for frame in frames if frame["event"] == "chat.market_snapshot")
    provider = next(frame for frame in frames if frame["event"] == "provider.started")

    assert snapshot["data"]["payload"]["provider"] == "Binance (CCXT)"
    assert snapshot["data"]["payload"]["price"] == "$1,728.32"
    assert len(snapshot["data"]["payload"]["price_points"]) == 3
    assert provider["data"]["payload"]["web_search_enabled"] is False
    assert exchange.ohlcv_calls == [("ETH/USDT", "1h", 72)]


def test_market_price_snapshot_does_not_request_series(tmp_path) -> None:
    llm = RecordingLLMClient([LLMClientEvent(type="message.delta", text="ETH quote is available.")])
    market_gateway = FakeMarketDataGateway()
    client = TestClient(
        create_app(
            config=ServerAppConfig(
                repository=create_sqlite_repository(),
                artifact_root=tmp_path,
                llm_client=llm,
                market_data_gateway=market_gateway,
            )
        )
    )
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "current ETH price", "web_search": "on"},
    )

    assert stream.status_code == 200, stream.text
    assert market_gateway.include_series == [False]
