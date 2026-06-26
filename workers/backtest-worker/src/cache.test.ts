import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { BACKTEST_OHLCV_EXCHANGES, BACKTEST_OHLCV_PROVIDER } from "./backtest-ohlcv-contract.js";

const artifactRoot = await mkdtemp(join(tmpdir(), "backtest-worker-cache-"));
process.env.STRATEGY_CODEBOT_API_ARTIFACT_ROOT = artifactRoot;
process.env.BACKTEST_WORKER_CACHE_LOCK_TIMEOUT_MS = "1000";
process.env.BACKTEST_WORKER_MARKET_DATA_MODE = "fixture";
process.env.BACKTEST_WORKER_ALLOWED_EXCHANGES = BACKTEST_OHLCV_EXCHANGES.join(",");
process.env.BACKTEST_WORKER_DATA_FETCH_THROTTLE_MS = "1";

const { __test } = await import("./index.js");

const config = {
  engine: "pineforge" as const,
  exchange: "binance" as const,
  symbol: "BTCUSDT",
  timeframe: "15m",
  start: "2024-01-01T00:00:00.000Z",
  end: "2024-01-02T00:00:00.000Z",
  initial_capital: 10000,
  fee_bps: 10,
  slippage_bps: 5,
  data_source: "public-readonly-cache" as const,
};

const exchangeSymbol = "BTC/USDT";
const timeframe = "15m";
const since = new Date("2024-01-01T00:00:00.000Z");
const limit = 2;

test.after(async () => {
  await rm(artifactRoot, { recursive: true, force: true });
});

test("fixture provider preserves selected exchange without network access", async () => {
  for (const providerExchange of BACKTEST_OHLCV_EXCHANGES) {
    const provider = await __test.createOhlcvProvider({ ...config, exchange: providerExchange }, timeframe);
    assert.equal(provider.exchange, providerExchange);
    assert.equal(provider.exchangeSymbol, "BTC/USDT");
  }
});

test("worker default exchange must be inside effective allowlist", () => {
  assert.throws(
    () => __test.parseDefaultExchange("kraken", new Set(["binance"])),
    /Default backtest exchange kraken is not in BACKTEST_WORKER_ALLOWED_EXCHANGES/,
  );
});

test("worker config rejects invalid provided exchange instead of defaulting", () => {
  assert.throws(
    () => __test.normalizedConfig({ ...config, exchange: "coinbase" as "binance" }),
    /Unsupported backtest exchange: coinbase/,
  );
  assert.equal(__test.normalizedConfig({ ...config, exchange: undefined }).exchange, "binance");
});

test("CCXT symbol resolver tries slash form and quote fallback", () => {
  assert.equal(
    __test.resolveCcxtSymbol({ markets: { "BTC/USD": {} }, fetchOHLCV: async () => [] }, "BTCUSDT", "kraken"),
    "BTC/USD",
  );
  assert.equal(
    __test.resolveCcxtSymbol({ markets: { "ETH/USDT": {} }, fetchOHLCV: async () => [] }, "ETHUSDT", "okx"),
    "ETH/USDT",
  );
  assert.throws(
    () => __test.resolveCcxtSymbol({ markets: { "ETH/USD": {} }, fetchOHLCV: async () => [] }, "BTCUSDT", "kraken"),
    /not available on kraken/,
  );
});

test("cache keys differ by exchange for same symbol and range", () => {
  const binanceKey = __test.rangeCacheDataset({ ...config, exchange: "binance" }, exchangeSymbol, timeframe);
  const bybitKey = __test.rangeCacheDataset({ ...config, exchange: "bybit" }, exchangeSymbol, timeframe);
  const binanceChunk = __test.candleCacheStorageKey({ ...config, exchange: "binance" }, exchangeSymbol, timeframe, since, limit);
  const bybitChunk = __test.candleCacheStorageKey({ ...config, exchange: "bybit" }, exchangeSymbol, timeframe, since, limit);

  assert.notEqual(binanceKey.index_key, bybitKey.index_key);
  assert.notEqual(binanceKey.lock_key, bybitKey.lock_key);
  assert.notEqual(binanceChunk, bybitChunk);
});

test("market metadata records mintick and pointvalue source confidence", () => {
  const spot = __test.ccxtMarketMetadataFromMarket("binance", "BTC/USDT", {
    spot: true,
    precision: { amount: 0.000001 },
    info: { filters: [{ filterType: "PRICE_FILTER", tickSize: "0.01" }] },
  });
  assert.equal(spot.mintick, 0.01);
  assert.equal(spot.mintick_source, "info.filters.PRICE_FILTER.tickSize");
  assert.equal(spot.mintick_confidence, "exchange_filter");
  assert.equal(spot.pointvalue, 1);
  assert.equal(spot.pointvalue_confidence, "spot_default");

  const swap = __test.ccxtMarketMetadataFromMarket("bybit", "BTC/USDT", {
    swap: true,
    contractSize: 0.001,
    precision: { price: 2 },
    info: {},
  });
  assert.equal(swap.mintick, 0.01);
  assert.equal(swap.mintick_confidence, "precision");
  assert.equal(swap.pointvalue, 0.001);
  assert.equal(swap.pointvalue_confidence, "contract_size");
});

test("range-v2 reads an existing full range without fetching", async () => {
  await resetCache();
  const first = await __test.getOrFillPublicCandleCache(
    config,
    fakeProvider(),
    timeframe,
    since,
    limit,
    Date.now() + 5_000,
    "job_range_fill",
  );
  assert.equal(first.cacheHit, false);
  let fetchCount = 0;

  const second = await __test.getOrFillPublicCandleCache(
    config,
    fakeProvider(() => {
      fetchCount += 1;
      return [];
    }),
    timeframe,
    since,
    limit,
    Date.now() + 5_000,
    "job_range_hit",
  );

  assert.equal(second.cacheHit, true);
  assert.equal(second.segmentsReused, 1);
  assert.deepEqual(second.candles, first.candles);
  assert.equal(fetchCount, 0);
});

test("range-v2 reuses a partial segment and fetches only the missing interval", async () => {
  await resetCache();
  await __test.getOrFillPublicCandleCache(
    config,
    fakeProvider(),
    timeframe,
    since,
    2,
    Date.now() + 5_000,
    "job_partial_seed",
  );
  const calls: Array<{ since?: number; limit?: number }> = [];

  const result = await __test.getOrFillPublicCandleCache(
    config,
    fakeProvider((_symbol, _timeframe, callSince, callLimit) => {
      calls.push({ since: callSince, limit: callLimit });
      return fixtureOhlcv(callSince ?? since.getTime(), callLimit ?? 1, stepMs(timeframe));
    }),
    timeframe,
    since,
    4,
    Date.now() + 5_000,
    "job_partial_extend",
  );

  assert.equal(result.cacheHit, false);
  assert.equal(result.segmentsReused, 1);
  assert.equal(result.segmentsCreated, 1);
  assert.equal(result.missingIntervalsFetched, 1);
  assert.equal(result.candles.length, 4);
  assert.deepEqual(calls, [{ since: since.getTime() + 2 * stepMs(timeframe), limit: 2 }]);
});

test("range-v2 concurrent cache misses fetch once and share the final segment", async () => {
  await resetCache();
  let fetchCount = 0;
  const provider = fakeProvider(async (_symbol, _timeframe, callSince, callLimit) => {
    fetchCount += 1;
    await sleep(50);
    return fixtureOhlcv(callSince ?? since.getTime(), callLimit ?? 1, stepMs(timeframe));
  });

  const [first, second] = await Promise.all([
    __test.getOrFillPublicCandleCache(config, provider, timeframe, since, limit, Date.now() + 5_000, "job_a"),
    __test.getOrFillPublicCandleCache(config, provider, timeframe, since, limit, Date.now() + 5_000, "job_b"),
  ]);

  assert.equal(fetchCount, 1);
  assert.deepEqual([first.cacheHit, second.cacheHit].sort(), [false, true]);
  assert.deepEqual(first.candles, second.candles);
});

test("range-v2 stale dataset locks are removed and retried", async () => {
  await resetCache();
  const dataset = __test.rangeCacheDataset(config, exchangeSymbol, timeframe);
  const lockPath = join(artifactRoot, dataset.lock_key);
  await mkdir(join(artifactRoot, "cache", "locks-v2"), { recursive: true });
  await writeFile(lockPath, "stale", "utf8");
  const staleTime = new Date(Date.now() - 10_000);
  await utimes(lockPath, staleTime, staleTime);

  const result = await __test.getOrFillPublicCandleCache(
    config,
    fakeProvider(),
    timeframe,
    since,
    1,
    Date.now() + 5_000,
    "job_stale_lock",
  );

  assert.equal(result.cacheHit, false);
  assert.equal(result.candles.length, 1);
});

test("range-v2 corrupt segments fall back to a fresh missing-interval fill", async () => {
  await resetCache();
  const candles = fixtureCandles(since.getTime(), 2, stepMs(timeframe));
  const written = await __test.writeRangeCacheSegment(config, exchangeSymbol, timeframe, stepMs(timeframe), candles);
  await __test.upsertRangeCacheSegment(config, exchangeSymbol, timeframe, stepMs(timeframe), written.segment);
  await writeFile(join(artifactRoot, written.segment.storage_key), "{not-json", "utf8");
  let fetchCount = 0;

  const result = await __test.getOrFillPublicCandleCache(
    config,
    fakeProvider((_symbol, _timeframe, callSince, callLimit) => {
      fetchCount += 1;
      return fixtureOhlcv(callSince ?? since.getTime(), callLimit ?? 1, stepMs(timeframe));
    }),
    timeframe,
    since,
    2,
    Date.now() + 5_000,
    "job_corrupt_segment",
  );

  assert.equal(fetchCount, 1);
  assert.equal(result.cacheHit, false);
  assert.equal(result.candles.length, 2);
});

test("range-v2 coverage selects minimal ordered segments for requested range", async () => {
  const requested = { startMs: since.getTime(), endMs: since.getTime() + 4 * stepMs(timeframe), stepMs: stepMs(timeframe) };
  const full = makeRangeSegment(since.getTime(), 4, "full");
  const firstHalf = makeRangeSegment(since.getTime(), 2, "first");
  const secondHalf = makeRangeSegment(since.getTime() + 2 * stepMs(timeframe), 2, "second");

  assert.deepEqual(
    __test.selectRangeCacheSegments([firstHalf, full, secondHalf], requested).map((segment) => segment.storage_key),
    ["full"],
  );
});

test("range-v2 monthly fetch windows split 1m intervals by UTC month", () => {
  const windows = __test.cacheFetchWindows(
    {
      startMs: Date.parse("2024-01-15T00:00:00.000Z"),
      endMs: Date.parse("2024-04-10T00:00:00.000Z"),
    },
    60_000,
  );

  assert.deepEqual(
    windows.map((window) => [new Date(window.startMs).toISOString(), new Date(window.endMs).toISOString()]),
    [
      ["2024-01-15T00:00:00.000Z", "2024-02-01T00:00:00.000Z"],
      ["2024-02-01T00:00:00.000Z", "2024-03-01T00:00:00.000Z"],
      ["2024-03-01T00:00:00.000Z", "2024-04-01T00:00:00.000Z"],
      ["2024-04-01T00:00:00.000Z", "2024-04-10T00:00:00.000Z"],
    ],
  );
});

test("range-v2 new covering segment prunes covered manifest entries", async () => {
  await resetCache();
  const partial = await __test.writeRangeCacheSegment(
    config,
    exchangeSymbol,
    timeframe,
    stepMs(timeframe),
    fixtureCandles(since.getTime(), 2, stepMs(timeframe)),
  );
  await __test.upsertRangeCacheSegment(config, exchangeSymbol, timeframe, stepMs(timeframe), partial.segment);
  const full = await __test.writeRangeCacheSegment(
    config,
    exchangeSymbol,
    timeframe,
    stepMs(timeframe),
    fixtureCandles(since.getTime(), 4, stepMs(timeframe)),
  );
  await __test.upsertRangeCacheSegment(config, exchangeSymbol, timeframe, stepMs(timeframe), full.segment);

  const index = await __test.readRangeCacheIndex(config, exchangeSymbol, timeframe);

  assert.deepEqual(index?.segments.map((segment) => segment.storage_key), [full.segment.storage_key]);
});

test("range-v2 monthly policy preserves contiguous manifest segments", async () => {
  await resetCache();
  const first = await __test.writeRangeCacheSegment(
    config,
    exchangeSymbol,
    timeframe,
    stepMs(timeframe),
    fixtureCandles(since.getTime(), 2, stepMs(timeframe)),
  );
  await __test.upsertRangeCacheSegment(config, exchangeSymbol, timeframe, stepMs(timeframe), first.segment);
  const second = await __test.writeRangeCacheSegment(
    config,
    exchangeSymbol,
    timeframe,
    stepMs(timeframe),
    fixtureCandles(since.getTime() + 2 * stepMs(timeframe), 2, stepMs(timeframe)),
  );
  await __test.upsertRangeCacheSegment(config, exchangeSymbol, timeframe, stepMs(timeframe), second.segment);

  const index = await __test.readRangeCacheIndex(config, exchangeSymbol, timeframe);
  const coverage = await __test.readRangeCacheCoverage(config, exchangeSymbol, timeframe, {
    startMs: since.getTime(),
    endMs: since.getTime() + 4 * stepMs(timeframe),
    stepMs: stepMs(timeframe),
  });

  assert.equal(index?.segments.length, 2);
  assert.deepEqual(
    index?.segments.map((segment) => segment.candle_count),
    [2, 2],
  );
  assert.equal(coverage.complete, true);
  assert.equal(coverage.candles.length, 4);
});

test("streaming CSV export writes sorted validated candles without buffering a string artifact", async () => {
  await resetCache();
  const dir = await mkdtemp(join(tmpdir(), "backtest-worker-csv-"));
  const path = join(dir, "ohlcv.csv");
  const candles = fixtureCandles(since.getTime(), 2, stepMs(timeframe));

  const result = await __test.writeCandlesCsv(path, candles);
  const content = await readFile(path, "utf8");

  assert.equal(result.bars, 2);
  assert.equal(result.bytesWritten, Buffer.byteLength(content, "utf8"));
  assert.equal(
    content,
    [
      "timestamp,open,high,low,close,volume",
      `${candles[0].timestamp},${candles[0].open},${candles[0].high},${candles[0].low},${candles[0].close},${candles[0].volume}`,
      `${candles[1].timestamp},${candles[1].open},${candles[1].high},${candles[1].low},${candles[1].close},${candles[1].volume}`,
      "",
    ].join("\n"),
  );

  await rm(dir, { recursive: true, force: true });
});

test("throttle cleanup evicts stale and overflow keys", () => {
  const throttleMap = __test.lastFetchAtByThrottleKey as Map<string, number>;
  throttleMap.clear();
  const now = Date.now();
  throttleMap.set("fresh", now);
  throttleMap.set("stale", now - 1_000_000);

  __test.pruneDataFetchThrottle(now);
  assert.equal(throttleMap.has("fresh"), true);
  assert.equal(throttleMap.has("stale"), false);

  throttleMap.clear();
  for (let index = 0; index < 10_005; index += 1) {
    throttleMap.set(`key-${index}`, now + index);
  }
  __test.pruneDataFetchThrottle(now + 10_005);

  assert.equal(throttleMap.size, 10_000);
  assert.equal(throttleMap.has("key-0"), false);
  assert.equal(throttleMap.has("key-10004"), true);
  throttleMap.clear();
});

test("chunk-v1 compatibility path still reads existing exact chunk cache", async () => {
  await resetCache();
  const candles = fixtureCandles(since.getTime(), 2, stepMs(timeframe));
  const storageKey = await __test.writeCachedPublicCandles(config, exchangeSymbol, timeframe, since, limit, candles);
  let fetchCount = 0;

  const result = await __test.getOrFillPublicCandleCacheV1(
    config,
    fakeProvider(() => {
      fetchCount += 1;
      return [];
    }),
    timeframe,
    since,
    limit,
    Date.now() + 5_000,
    "job_chunk_v1",
  );

  assert.equal(result.cacheHit, true);
  assert.equal(result.storage_key, storageKey);
  assert.deepEqual(result.candles, candles);
  assert.equal(fetchCount, 0);
});

test("candle checksum handles one year of 1m candles without recursive stable json", () => {
  const candles = Array.from({ length: 525_600 }, (_unused, index) => ({
    timestamp: Date.parse("2025-01-01T00:00:00.000Z") + index * 60_000,
    open: 100 + index,
    high: 101 + index,
    low: 99 + index,
    close: 100.5 + index,
    volume: 10,
  }));

  const digest = __test.checksumCandles(candles);

  assert.equal(digest.length, 64);
});

test("OHLCV validator accepts valid feed and reports checksum", () => {
  const candles = fixtureCandles(since.getTime(), 2, stepMs(timeframe));

  const quality = __test.validateOhlcvFeed(candles, {
    timeframe,
    startIso: since.toISOString(),
    endIso: new Date(since.getTime() + 2 * stepMs(timeframe)).toISOString(),
    context: "test valid feed",
  });

  assert.equal(quality.status, "pass");
  assert.equal(quality.expected_bars, 2);
  assert.equal(quality.actual_bars, 2);
  assert.equal(quality.gap_count, 0);
  assert.equal(quality.checksum, __test.checksumCandles(candles));
});

test("OHLCV validator rejects unsorted duplicate and malformed candles", () => {
  const candles = fixtureCandles(since.getTime(), 2, stepMs(timeframe));
  assert.throws(
    () => __test.validateOhlcvFeed([...candles].reverse(), {
      timeframe,
      startIso: since.toISOString(),
      endIso: new Date(since.getTime() + 2 * stepMs(timeframe)).toISOString(),
      context: "test unsorted feed",
    }),
    /strictly ascending/,
  );
  assert.throws(
    () => __test.validateOhlcvFeed([candles[0], candles[0]], {
      timeframe,
      startIso: since.toISOString(),
      endIso: new Date(since.getTime() + 2 * stepMs(timeframe)).toISOString(),
      context: "test duplicate feed",
    }),
    /duplicate timestamp/,
  );
  assert.throws(
    () => __test.validateOhlcvFeed([{ ...candles[0], high: candles[0].low - 1 }], {
      timeframe,
      startIso: since.toISOString(),
      endIso: new Date(since.getTime() + stepMs(timeframe)).toISOString(),
      context: "test malformed feed",
    }),
    /malformed OHLC/,
  );
});

test("OHLCV validator preserves exchange gaps as warnings", () => {
  const candles = [
    ...fixtureCandles(since.getTime(), 1, stepMs(timeframe)),
    ...fixtureCandles(since.getTime() + 2 * stepMs(timeframe), 1, stepMs(timeframe)),
  ];

  const quality = __test.validateOhlcvFeed(candles, {
    timeframe,
    startIso: since.toISOString(),
    endIso: new Date(since.getTime() + 3 * stepMs(timeframe)).toISOString(),
    context: "test gapped feed",
  });

  assert.equal(quality.status, "warn");
  assert.equal(quality.missing_bars, 1);
  assert.equal(quality.gap_count, 1);
  assert.deepEqual(quality.gap_ranges.map((gap) => gap.missing_bars), [1]);
});

test("range-v2 rejects malformed segments before cache write", async () => {
  await resetCache();
  const candles = [{ ...fixtureCandles(since.getTime(), 1, stepMs(timeframe))[0], low: 200 }];

  await assert.rejects(
    __test.writeRangeCacheSegment(config, exchangeSymbol, timeframe, stepMs(timeframe), candles),
    /malformed OHLC/,
  );
});

async function resetCache() {
  await rm(join(artifactRoot, "cache"), { recursive: true, force: true });
}

function fakeExchange(
  fetchOHLCV: (
    symbol: string,
    timeframe: string,
    since?: number,
    limit?: number,
) => Promise<unknown[]> | unknown[] = (_symbol, callTimeframe, callSince, callLimit) =>
    fixtureOhlcv(callSince ?? since.getTime(), callLimit ?? 1, stepMs(callTimeframe)),
) {
  return {
    async fetchOHLCV(symbol: string, callTimeframe: string, callSince?: number, callLimit?: number) {
      return await fetchOHLCV(symbol, callTimeframe, callSince, callLimit);
    },
  };
}

function fakeProvider(fetchOHLCV?: Parameters<typeof fakeExchange>[0], providerExchange: "binance" | "bybit" | "okx" | "kraken" = "binance") {
  return {
    source: BACKTEST_OHLCV_PROVIDER,
    exchange: providerExchange,
    exchangeSymbol,
    client: fakeExchange(fetchOHLCV),
  };
}

function fixtureCandles(start: number, count: number, step: number) {
  return fixtureOhlcv(start, count, step).map(([timestamp, open, high, low, close, volume]) => ({
    timestamp: Number(timestamp),
    open: Number(open),
    high: Number(high),
    low: Number(low),
    close: Number(close),
    volume: Number(volume),
  }));
}

function fixtureOhlcv(start: number, count: number, step: number) {
  return Array.from({ length: count }, (_, index) => [
    start + index * step,
    100 + index,
    101 + index,
    99 + index,
    100.5 + index,
    10 + index,
  ]);
}

function stepMs(value: string) {
  const amount = Number(value.match(/^\d+/)?.[0] ?? 1);
  return amount * 60_000;
}

function makeRangeSegment(start: number, count: number, suffix: string) {
  return {
    range_start: new Date(start).toISOString(),
    range_end: new Date(start + count * stepMs(timeframe)).toISOString(),
    step_ms: stepMs(timeframe),
    storage_key: suffix,
    checksum: suffix,
    created_at: new Date(start).toISOString(),
    candle_count: count,
  };
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
