import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const artifactRoot = await mkdtemp(join(tmpdir(), "backtest-worker-cache-"));
process.env.STRATEGY_CODEBOT_API_ARTIFACT_ROOT = artifactRoot;
process.env.BACKTEST_WORKER_CACHE_LOCK_TIMEOUT_MS = "1000";

const { __test } = await import("./index.js");

const config = {
  engine: "backtest-kit" as const,
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

test("range-v2 reads an existing full range without fetching", async () => {
  await resetCache();
  const first = await __test.getOrFillPublicCandleCache(
    config,
    fakeExchange(),
    exchangeSymbol,
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
    fakeExchange(() => {
      fetchCount += 1;
      return [];
    }),
    exchangeSymbol,
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
    fakeExchange(),
    exchangeSymbol,
    timeframe,
    since,
    2,
    Date.now() + 5_000,
    "job_partial_seed",
  );
  const calls: Array<{ since?: number; limit?: number }> = [];

  const result = await __test.getOrFillPublicCandleCache(
    config,
    fakeExchange((_symbol, _timeframe, callSince, callLimit) => {
      calls.push({ since: callSince, limit: callLimit });
      return fixtureOhlcv(callSince ?? since.getTime(), callLimit ?? 1, stepMs(timeframe));
    }),
    exchangeSymbol,
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
  const exchange = fakeExchange(async (_symbol, _timeframe, callSince, callLimit) => {
    fetchCount += 1;
    await sleep(50);
    return fixtureOhlcv(callSince ?? since.getTime(), callLimit ?? 1, stepMs(timeframe));
  });

  const [first, second] = await Promise.all([
    __test.getOrFillPublicCandleCache(config, exchange, exchangeSymbol, timeframe, since, limit, Date.now() + 5_000, "job_a"),
    __test.getOrFillPublicCandleCache(config, exchange, exchangeSymbol, timeframe, since, limit, Date.now() + 5_000, "job_b"),
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
    fakeExchange(),
    exchangeSymbol,
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
    fakeExchange((_symbol, _timeframe, callSince, callLimit) => {
      fetchCount += 1;
      return fixtureOhlcv(callSince ?? since.getTime(), callLimit ?? 1, stepMs(timeframe));
    }),
    exchangeSymbol,
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

test("chunk-v1 compatibility path still reads existing exact chunk cache", async () => {
  await resetCache();
  const candles = fixtureCandles(since.getTime(), 2, stepMs(timeframe));
  const storageKey = await __test.writeCachedPublicCandles(config, exchangeSymbol, timeframe, since, limit, candles);
  let fetchCount = 0;

  const result = await __test.getOrFillPublicCandleCacheV1(
    config,
    fakeExchange(() => {
      fetchCount += 1;
      return [];
    }),
    exchangeSymbol,
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

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
