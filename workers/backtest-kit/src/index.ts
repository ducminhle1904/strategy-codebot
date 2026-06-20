import { createHash, randomUUID } from "node:crypto";
import { mkdir, open, readFile, rename, rm, stat, writeFile } from "node:fs/promises";
import { hostname } from "node:os";
import { dirname, join } from "node:path";
import {
  Backtest,
  Position,
  addExchangeSchema,
  addFrameSchema,
  addStrategySchema,
  overrideExchangeSchema,
  overrideFrameSchema,
  overrideStrategySchema,
  type FrameInterval,
  type SignalInterval,
} from "backtest-kit";
import ccxt from "ccxt";
import pg from "pg";

const { Client } = pg;

type BacktestConfig = {
  engine: "backtest-kit";
  symbol: string;
  timeframe: string;
  start: string;
  end: string;
  initial_capital: number;
  fee_bps: number;
  slippage_bps: number;
  data_source: "public-readonly-cache";
};

type RunJobRow = {
  id: string;
  run_id: string;
  owner_user_id: string;
  workspace_id: string;
  job_type: string;
  status: string;
  payload_json: BacktestJobPayload;
  attempts: number;
  max_attempts: number;
  created_at: Date | string;
  updated_at: Date | string;
};

type BacktestJobPayload = {
  strategy_spec: Record<string, unknown>;
  strategy_logic?: Record<string, unknown>;
  backtest_config: BacktestConfig;
  runtime: {
    engine: "backtest-kit";
    allowed_api: string[];
    blocked_api: string[];
  };
  limits?: {
    workspace_active_limit?: number;
    max_variants?: number;
  };
};

type AssistantRunRow = {
  id: string;
  conversation_id: string;
  owner_user_id: string;
  workspace_id: string;
  status: string;
  request_id: string | null;
  trace_id: string | null;
};

type Candle = {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

type CandleFetchRecord = {
  source: string;
  symbol: string;
  exchange_symbol: string;
  timeframe: string;
  since: string;
  limit: number;
  candles: number;
  checksum: string;
  cache_hit: boolean;
  storage_key?: string;
};

type RangeCacheSegment = {
  range_start: string;
  range_end: string;
  step_ms: number;
  storage_key: string;
  checksum: string;
  created_at: string;
  candle_count: number;
};

type RangeCacheIndex = {
  cache_version: "range-v2";
  source: "ccxt-public-readonly";
  exchange: "binance";
  symbol: string;
  exchange_symbol: string;
  timeframe: string;
  step_ms: number;
  segments: RangeCacheSegment[];
  updated_at: string;
};

type CandleCacheResult = {
  storage_key: string;
  candles: Candle[];
  cacheHit: boolean;
  candlesFetched: number;
  rangeCacheHits: number;
  rangeCacheMisses: number;
  missingIntervalsFetched: number;
  segmentsReused: number;
  segmentsCreated: number;
  bytesRead: number;
  bytesWritten: number;
};

type RangeCoverageResult = {
  complete: boolean;
  candles: Candle[];
  missingIntervals: Array<{ startMs: number; endMs: number }>;
  segmentsReused: number;
  bytesRead: number;
};

type CandleCacheManifest = {
  source: "ccxt-public-readonly";
  exchange: "binance";
  symbol: string;
  exchange_symbol: string;
  timeframe: string;
  cache_version: "chunk-v1" | "range-v2";
  range_start: string;
  range_end: string;
  checksum: string;
  fetch_count: number;
  cache_hits: number;
  candles_fetched: number;
  candles_used: number;
  range_cache_hits: number;
  range_cache_misses: number;
  missing_intervals_fetched: number;
  segments_reused: number;
  segments_created: number;
  bytes_read: number;
  bytes_written: number;
  read_only: true;
  fetches: CandleFetchRecord[];
};

type BacktestClosedEvent = {
  action: "closed";
  signal?: Record<string, unknown>;
  currentPrice?: number;
  closeReason?: string;
  closeTimestamp?: number;
  pnl?: {
    pnlPercentage?: number;
    pnlCost?: number;
    pnlEntries?: number;
    priceOpen?: number;
    priceClose?: number;
  };
  strategyName?: string;
  exchangeName?: string;
  frameName?: string;
  symbol?: string;
  backtest?: boolean;
};

type NormalizedTrade = {
  id: string | null;
  symbol: string;
  side: string | null;
  close_reason: string | null;
  opened_at: string | null;
  closed_at: string | null;
  entry_price: number | null;
  exit_price: number | null;
  pnl_percentage: number | null;
  pnl_cost: number | null;
  cost: number | null;
};

type EquityPoint = {
  index: number;
  timestamp: string | null;
  equity: number;
  pnl_cost: number;
  drawdown_pct: number;
};

type BacktestMetrics = {
  pnl: {
    absolute: number;
    percentage: number;
  };
  max_drawdown: number;
  trade_count: number;
  win_rate: number | null;
  sharpe: number | null;
  sortino: number | null;
};

type StrategyAdapter = {
  strategy_name: string;
  exchange_name: string;
  frame_name: string;
  timeframe: string;
  position: "long" | "short";
  percent_take_profit: number;
  percent_stop_loss: number;
  cost: number;
  minute_estimated_time: number;
};

type StrategyLogicCondition = {
  type: "crossover" | "crossunder" | "greater_than" | "less_than";
  left: string;
  right: string | number;
};

type StrategyLogicV1 = {
  logic_version: "backtest-strategy-logic.v1";
  position: "long";
  indicators: {
    fast_ema: { kind: "ema"; period: number; source: "close" };
    slow_ema: { kind: "ema"; period: number; source: "close" };
    rsi?: { kind: "rsi"; period: number; source: "close" };
  };
  entry: { all: StrategyLogicCondition[] };
  exit: {
    take_profit_pct: number;
    stop_loss_pct: number;
    max_holding_minutes: number;
  };
  risk: { cost: number };
};

type StrategyRuntime = {
  semantics: "semantic_strategy_logic" | "freeform_spec_backtest_adapter";
  strategyLogic: StrategyLogicV1 | null;
  warnings: string[];
  shouldEnter: (when: unknown, currentPrice: number) => boolean;
  source: string;
};

type BacktestExecutionResult = {
  adapter: StrategyAdapter;
  strategyLogic: StrategyLogicV1 | null;
  executionSemantics: StrategyRuntime["semantics"];
  manifest: CandleCacheManifest;
  report: Record<string, unknown>;
  trades: NormalizedTrade[];
  equityCurve: EquityPoint[];
  sourceBundle: Record<string, unknown>;
  metadata: Record<string, unknown>;
};

type ArtifactSpec = {
  kind: string;
  mime_type: string;
  display_name: string;
  storage_key: string;
  content: unknown;
};

const JOB_TYPE = "backtest-preview";
const WORKER_ID = process.env.BACKTEST_WORKER_ID?.trim() || `backtest-worker-${hostname()}-${process.pid}`;
const POLL_INTERVAL_MS = Number(process.env.BACKTEST_WORKER_POLL_INTERVAL_MS ?? 2000);
const LEASE_SECONDS = Number(process.env.BACKTEST_WORKER_LEASE_SECONDS ?? 120);
const HEARTBEAT_SECONDS = Math.max(
  1,
  Math.min(
    Number(process.env.BACKTEST_WORKER_HEARTBEAT_SECONDS ?? 30),
    Math.floor(LEASE_SECONDS / 2),
  ),
);
const ARTIFACT_ROOT = process.env.STRATEGY_CODEBOT_API_ARTIFACT_ROOT ?? "/var/lib/strategy-codebot/artifacts";
const MAX_CANDLES_PER_JOB = Number(process.env.BACKTEST_WORKER_MAX_CANDLES ?? 525600);
const MAX_CANDLES_PER_FETCH = Number(process.env.BACKTEST_WORKER_MAX_CANDLES_PER_FETCH ?? 1000);
const MAX_BACKTEST_EVENTS = Number(process.env.BACKTEST_WORKER_MAX_EVENTS ?? 10000);
const MAX_ARTIFACT_BYTES = Number(process.env.BACKTEST_WORKER_MAX_ARTIFACT_BYTES ?? 5_000_000);
const WORKER_TIMEOUT_MS = Number(process.env.BACKTEST_WORKER_TIMEOUT_MS ?? 120_000);
const DATA_FETCH_THROTTLE_MS = Number(process.env.BACKTEST_WORKER_DATA_FETCH_THROTTLE_MS ?? 250);
const DEFAULT_WORKSPACE_ACTIVE_LIMIT = Number(process.env.BACKTEST_WORKER_DEFAULT_WORKSPACE_ACTIVE_LIMIT ?? 2);
const CACHE_LOCK_TIMEOUT_MS = Number(process.env.BACKTEST_WORKER_CACHE_LOCK_TIMEOUT_MS ?? 30_000);
const MARKET_DATA_MODE = process.env.BACKTEST_WORKER_MARKET_DATA_MODE ?? "ccxt";
const CANDLE_CACHE_VERSION = process.env.BACKTEST_WORKER_CANDLE_CACHE_VERSION === "chunk-v1" ? "chunk-v1" : "range-v2";
const TERMINAL_RUN_STATUSES = new Set(["completed", "failed", "blocked", "cancelled"]);
const lastFetchAtByThrottleKey = new Map<string, number>();

class StaleJobLeaseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "StaleJobLeaseError";
  }
}

async function main() {
  const client = new Client({ connectionString: databaseUrl() });
  await client.connect();
  console.log(JSON.stringify({ level: "info", message: "backtest worker started", worker_id: WORKER_ID }));
  while (true) {
    const job = await claimJob(client);
    if (job === null) {
      await sleep(POLL_INTERVAL_MS);
      continue;
    }
    await processJob(client, job).catch(async (error: unknown) => {
      if (error instanceof StaleJobLeaseError) {
        console.warn(JSON.stringify({ level: "warn", message: error.message, job_id: job.id }));
        return;
      }
      const message = error instanceof Error ? error.message : String(error);
      await markJobFailed(client, job, "worker_error", message);
    });
  }
}

function databaseUrl() {
  if (process.env.STRATEGY_CODEBOT_API_DATABASE_URL) {
    return process.env.STRATEGY_CODEBOT_API_DATABASE_URL;
  }
  const host = process.env.POSTGRES_HOST ?? "postgres";
  const port = process.env.POSTGRES_PORT ?? "5432";
  const database = process.env.POSTGRES_DB ?? "strategy_codebot";
  const user = process.env.POSTGRES_USER ?? "strategy_codebot";
  const password = process.env.POSTGRES_PASSWORD ?? "";
  return `postgresql://${encodeURIComponent(user)}:${encodeURIComponent(password)}@${host}:${port}/${database}`;
}

async function claimJob(client: pg.Client): Promise<RunJobRow | null> {
  const result = await client.query<RunJobRow>(
    `
    WITH candidate AS (
      SELECT job.id
      FROM run_jobs AS job
      WHERE job.job_type = $1
        AND job.status IN ('queued', 'running')
        AND job.attempts < job.max_attempts
        AND (job.status = 'queued' OR job.leased_until < now())
        AND (
          SELECT count(*)
          FROM run_jobs AS active
          WHERE active.job_type = job.job_type
            AND active.workspace_id = job.workspace_id
            AND active.status = 'running'
            AND active.leased_until >= now()
        ) < GREATEST(
          1,
          COALESCE(
            CASE
              WHEN (job.payload_json #>> '{limits,workspace_active_limit}') ~ '^-?[0-9]+$'
              THEN (job.payload_json #>> '{limits,workspace_active_limit}')::int
              ELSE NULL
            END,
            $4
          )
        )
      ORDER BY job.created_at ASC, job.id ASC
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    )
    UPDATE run_jobs
    SET status = 'running',
        attempts = attempts + 1,
        lease_owner = $2,
        leased_until = now() + ($3::int * interval '1 second'),
        updated_at = now()
    WHERE id = (SELECT id FROM candidate)
    RETURNING *
    `,
    [JOB_TYPE, WORKER_ID, LEASE_SECONDS, DEFAULT_WORKSPACE_ACTIVE_LIMIT],
  );
  return result.rows[0] ?? null;
}

async function processJob(client: pg.Client, job: RunJobRow) {
  const processStartedAt = Date.now();
  const jobWaitMs = Math.max(0, processStartedAt - timestampMs(job.created_at));
  const run = await getRun(client, job.run_id);
  if (isTerminalRunStatus(run.status)) {
    await completeJob(client, job.id, "cancelled", {
      reason: "run_already_terminal",
      run_status: run.status,
    });
    return;
  }
  await setRunStatus(client, run, "running");
  await appendEvent(client, run, "backtest.data.started", {
    job_id: job.id,
    data_source: job.payload_json.backtest_config.data_source,
    symbol: job.payload_json.backtest_config.symbol,
    timeframe: job.payload_json.backtest_config.timeframe,
    job_wait_ms: jobWaitMs,
  });
  await appendEvent(client, run, "backtest.execution.started", {
    job_id: job.id,
    engine: "backtest-kit",
    allowed_api: ["Backtest.run"],
  });

  const heartbeat = await startJobHeartbeat(job.id);
  let execution: BacktestExecutionResult & { metrics: BacktestMetrics };
  const executionStartedAt = Date.now();
  try {
    execution = await withTimeout(runBacktestKit(job, executionStartedAt + WORKER_TIMEOUT_MS), WORKER_TIMEOUT_MS, job.id);
  } finally {
    await heartbeat.stop();
  }
  const executionDurationMs = Date.now() - executionStartedAt;
  heartbeat.assertLease();
  await renewJobLeaseOrThrow(client, job.id);
  const latestRun = await getRun(client, job.run_id);
  if (isTerminalRunStatus(latestRun.status)) {
    await completeJob(client, job.id, "cancelled", {
      reason: "run_cancelled_during_execution",
      run_status: latestRun.status,
    });
    return;
  }
  const reportStartedAt = Date.now();
  const artifacts = await writeBacktestArtifacts(job, execution);
  const reportGenerationMs = Date.now() - reportStartedAt;
  const cacheHitRate = cacheHitRateForManifest(execution.manifest);

  await appendEvent(client, run, "backtest.data.completed", {
    job_id: job.id,
    cache_manifest: artifacts.cacheManifest.storage_key,
    candles_fetched: execution.manifest.candles_fetched,
    candles_used: execution.manifest.candles_used,
    cache_hits: execution.manifest.cache_hits,
    fetch_count: execution.manifest.fetch_count,
    cache_hit_rate: cacheHitRate,
    cache_version: execution.manifest.cache_version,
    range_cache_hit_rate: rangeCacheHitRateForManifest(execution.manifest),
    segments_reused: execution.manifest.segments_reused,
    segments_created: execution.manifest.segments_created,
  });
  await appendEvent(client, run, "backtest.execution.completed", {
    job_id: job.id,
    status: "completed",
    trade_count: execution.metrics.trade_count,
    pnl: execution.metrics.pnl,
    max_drawdown: execution.metrics.max_drawdown,
    execution_duration_ms: executionDurationMs,
  });
  await persistArtifacts(client, run, artifacts);
  await appendEvent(client, run, "backtest.report.completed", {
    job_id: job.id,
    report_artifact: artifacts.report.storage_key,
    evidence_label: "Backtest Kit local preview evidence",
    report_generation_ms: reportGenerationMs,
  });
  await completeJob(client, job.id, "completed", {
    report_storage_key: artifacts.report.storage_key,
    trades_storage_key: artifacts.trades.storage_key,
    equity_curve_storage_key: artifacts.equityCurve.storage_key,
    cache_manifest_storage_key: artifacts.cacheManifest.storage_key,
    evidence_label: "Backtest Kit local preview evidence",
    observability: {
      job_wait_ms: jobWaitMs,
      execution_duration_ms: executionDurationMs,
      report_generation_ms: reportGenerationMs,
      cache_hit_rate: cacheHitRate,
      range_cache_hit_rate: rangeCacheHitRateForManifest(execution.manifest),
      cache_hits: execution.manifest.cache_hits,
      fetch_count: execution.manifest.fetch_count,
      segments_reused: execution.manifest.segments_reused,
      segments_created: execution.manifest.segments_created,
    },
  });
  await setRunStatus(client, run, "completed");
  await appendEvent(client, run, "run.completed", { status: "completed", mode: "backtest-preview" });
}

async function runBacktestKit(job: RunJobRow, deadlineMs: number): Promise<BacktestExecutionResult & { metrics: BacktestMetrics }> {
  const config = normalizedConfig(job.payload_json.backtest_config);
  const adapter = buildStrategyAdapter(job);
  const candleHistory = new Map<string, Candle[]>();
  const strategyRuntime = buildStrategyRuntime(job, adapter, candleHistory);
  const manifest: CandleCacheManifest = {
    source: "ccxt-public-readonly",
    exchange: "binance",
    symbol: config.symbol,
    exchange_symbol: normalizeCcxtSymbol(config.symbol),
    timeframe: config.timeframe,
    cache_version: CANDLE_CACHE_VERSION,
    range_start: config.start,
    range_end: config.end,
    checksum: "",
    fetch_count: 0,
    cache_hits: 0,
    candles_fetched: 0,
    candles_used: 0,
    range_cache_hits: 0,
    range_cache_misses: 0,
    missing_intervals_fetched: 0,
    segments_reused: 0,
    segments_created: 0,
    bytes_read: 0,
    bytes_written: 0,
    read_only: true,
    fetches: [],
  };
  const requestCache = new Map<string, Candle[]>();
  const exchange = MARKET_DATA_MODE === "fixture"
    ? fixtureExchange()
    : new ccxt.binance({ enableRateLimit: true });

  await registerExchangeSchema({
    exchangeName: adapter.exchange_name,
    getCandles: async (symbol, interval, since, limit) => {
      assertBeforeDeadline(deadlineMs, job.id);
      const timeframe = normalizeTimeframe(interval);
      const exchangeSymbol = normalizeCcxtSymbol(symbol);
      const cacheKey = `${exchangeSymbol}:${timeframe}:${since.toISOString()}:${limit}`;
      const cached = requestCache.get(cacheKey);
      if (cached) {
        recordCandlesUsed(manifest, cached.length);
        rememberCandles(candleHistory, exchangeSymbol, timeframe, cached);
        manifest.cache_hits += 1;
        manifest.fetches.push(fetchRecord(config, exchangeSymbol, timeframe, since, limit, cached, true));
        return cached;
      }
      const cacheResult = await getOrFillPublicCandleCache(config, exchange, exchangeSymbol, timeframe, since, limit, deadlineMs, job.id);
      if (!cacheResult.cacheHit && manifest.candles_fetched + cacheResult.candlesFetched > MAX_CANDLES_PER_JOB) {
        throw new Error(`Backtest public candle fetch limit exceeded: ${MAX_CANDLES_PER_JOB}`);
      }
      recordCandlesUsed(manifest, cacheResult.candles.length);
      rememberCandles(candleHistory, exchangeSymbol, timeframe, cacheResult.candles);
      recordCacheResult(manifest, cacheResult);
      if (cacheResult.cacheHit) {
        manifest.cache_hits += 1;
      } else {
        manifest.candles_fetched += cacheResult.candlesFetched;
        manifest.fetch_count += Math.max(1, cacheResult.missingIntervalsFetched);
      }
      manifest.fetches.push(
        fetchRecord(config, exchangeSymbol, timeframe, since, limit, cacheResult.candles, cacheResult.cacheHit, cacheResult.storage_key),
      );
      requestCache.set(cacheKey, cacheResult.candles);
      return cacheResult.candles;
    },
    formatPrice: async (_symbol, price) => price.toFixed(8),
    formatQuantity: async (_symbol, quantity) => quantity.toFixed(8),
  });
  await registerFrameSchema({
    frameName: adapter.frame_name,
    interval: config.timeframe as FrameInterval,
    startDate: new Date(config.start),
    endDate: new Date(config.end),
  });
  await registerStrategySchema({
    strategyName: adapter.strategy_name,
    interval: adapter.timeframe as SignalInterval,
    getSignal: async (_symbol, _when, currentPrice) => {
      if (!Number.isFinite(currentPrice) || currentPrice <= 0) {
        return null;
      }
      if (!strategyRuntime.shouldEnter(_when, currentPrice)) {
        return null;
      }
      const bracket = Position.bracket({
        position: adapter.position,
        currentPrice,
        percentTakeProfit: adapter.percent_take_profit,
        percentStopLoss: adapter.percent_stop_loss,
      });
      return {
        ...bracket,
        minuteEstimatedTime: adapter.minute_estimated_time,
        cost: adapter.cost,
      };
    },
  });

  const trades: NormalizedTrade[] = [];
  const eventSamples: Record<string, unknown>[] = [];
  let eventCount = 0;
  for await (const event of Backtest.run(config.symbol, {
    strategyName: adapter.strategy_name,
    exchangeName: adapter.exchange_name,
    frameName: adapter.frame_name,
  })) {
    assertBeforeDeadline(deadlineMs, job.id);
    eventCount += 1;
    if (eventSamples.length < 50) {
      eventSamples.push(summarizeBacktestEvent(event));
    }
    if (isClosedEvent(event)) {
      trades.push(normalizeTrade(event, config.symbol));
    }
    if (eventCount > MAX_BACKTEST_EVENTS) {
      throw new Error(`Backtest event limit exceeded: ${MAX_BACKTEST_EVENTS}`);
    }
  }

  manifest.checksum = checksum(manifest.fetches);
  const equityCurve = buildEquityCurve(config.initial_capital, trades);
  const metrics = buildMetrics(config.initial_capital, trades, equityCurve);
  const warnings = [
    "This artifact is Backtest Kit local preview evidence only.",
    "It is not TradingView proof, MQL5 proof, live-trading evidence, broker execution evidence, or a profitability claim.",
    "Backtest Kit fee/slippage semantics are engine-owned; requested fee_bps and slippage_bps are preserved as report assumptions.",
    ...strategyRuntime.warnings,
  ];
  if (trades.length === 0) {
    warnings.push("Backtest Kit completed without closed trades for this strategy/configuration window.");
  }

  const report = {
    status: "completed",
    evidence_label: "Backtest Kit local preview evidence",
    preview_runtime: "backtest-kit@14.0.0",
    execution_semantics: strategyRuntime.semantics,
    metrics,
    assumptions: {
      engine: config.engine,
      symbol: config.symbol,
      timeframe: config.timeframe,
      start: config.start,
      end: config.end,
      initial_capital: config.initial_capital,
      fee_bps: config.fee_bps,
      slippage_bps: config.slippage_bps,
      data_source: config.data_source,
      exchange: "binance",
      exchange_symbol: manifest.exchange_symbol,
      strategy_adapter: adapter,
      execution_semantics: strategyRuntime.semantics,
      strategy_logic: strategyRuntime.strategyLogic,
    },
    warnings,
    reproducibility_hash: checksum({
      backtest_config: config,
      strategy_adapter: adapter,
      strategy_logic: strategyRuntime.strategyLogic,
      execution_semantics: strategyRuntime.semantics,
      candle_manifest_checksum: manifest.checksum,
    }),
    event_count: eventCount,
    event_samples: eventSamples,
  };
  const sourceBundle = {
    generated_strategy_adapter: generatedAdapterSource(adapter),
    generated_signal_evaluator: strategyRuntime.source,
    adapter,
    strategy_logic: strategyRuntime.strategyLogic,
    execution_semantics: strategyRuntime.semantics,
    policy: {
      allowed_api: job.payload_json.runtime.allowed_api,
      blocked_api: job.payload_json.runtime.blocked_api,
    },
  };
  const metadata = {
    worker_id: WORKER_ID,
    job_id: job.id,
    engine_package: "backtest-kit",
    engine_version: "14.0.0",
    evidence_label: "Backtest Kit local preview evidence",
    execution_semantics: strategyRuntime.semantics,
    blocked_runtime: job.payload_json.runtime.blocked_api,
  };
  return {
    adapter,
    strategyLogic: strategyRuntime.strategyLogic,
    executionSemantics: strategyRuntime.semantics,
    manifest,
    report,
    trades,
    equityCurve,
    metrics,
    sourceBundle,
    metadata,
  };
}

async function fetchPublicCandles(
  exchange: { fetchOHLCV: (symbol: string, timeframe: string, since?: number, limit?: number) => Promise<unknown[]> },
  exchangeSymbol: string,
  timeframe: string,
  since: Date,
  limit: number,
  deadlineMs: number,
  jobId: string,
): Promise<Candle[]> {
  const requestedLimit = Math.max(1, Math.trunc(limit));
  const candles: Candle[] = [];
  const seen = new Set<number>();
  const stepMs = timeframeToMs(timeframe);
  let cursor = since.getTime();
  while (candles.length < requestedLimit) {
    assertBeforeDeadline(deadlineMs, jobId);
    const batchLimit = Math.min(MAX_CANDLES_PER_FETCH, requestedLimit - candles.length);
    await throttleDataFetch("ccxt-public-readonly", exchangeSymbol, timeframe);
    const ohlcv = await exchange.fetchOHLCV(exchangeSymbol, timeframe, cursor, batchLimit);
    const batch = ohlcv
      .map((row) => {
        const [timestamp, open, high, low, close, volume] = Array.isArray(row) ? row : [];
        return {
          timestamp: Number(timestamp),
          open: Number(open),
          high: Number(high),
          low: Number(low),
          close: Number(close),
          volume: Number(volume ?? 0),
        };
      })
      .filter((candle) =>
        Number.isFinite(candle.timestamp) &&
        candle.timestamp >= cursor &&
        Number.isFinite(candle.open) &&
        Number.isFinite(candle.high) &&
        Number.isFinite(candle.low) &&
        Number.isFinite(candle.close),
      );
    if (batch.length === 0) {
      break;
    }
    for (const candle of batch) {
      if (seen.has(candle.timestamp)) {
        continue;
      }
      seen.add(candle.timestamp);
      candles.push(candle);
    }
    const nextCursor = batch[batch.length - 1].timestamp + stepMs;
    if (nextCursor <= cursor) {
      break;
    }
    cursor = nextCursor;
    if (batch.length < batchLimit) {
      break;
    }
  }
  return candles;
}

function fixtureExchange() {
  return {
    async fetchOHLCV(_symbol: string, timeframe: string, since?: number, limit?: number): Promise<unknown[]> {
      const stepMs = timeframeToMs(timeframe);
      const start = since ?? Date.parse("2024-01-01T00:00:00Z");
      const count = Math.max(1, Math.min(Math.trunc(limit ?? 100), MAX_CANDLES_PER_FETCH));
      return Array.from({ length: count }, (_unused, index) => {
        const timestamp = start + index * stepMs;
        const wave = Math.sin(index / 3) * 35;
        const trend = index * 1.5;
        const open = 42000 + trend + wave;
        const close = open + Math.cos(index / 2) * 18;
        const high = Math.max(open, close) + 12;
        const low = Math.min(open, close) - 12;
        const volume = 100 + index;
        return [timestamp, open, high, low, close, volume];
      });
    },
  };
}

function fetchRecord(
  config: BacktestConfig,
  exchangeSymbol: string,
  timeframe: string,
  since: Date,
  limit: number,
  candles: Candle[],
  cacheHit: boolean,
  storageKey?: string,
): CandleFetchRecord {
  return {
    source: "ccxt-public-readonly",
    symbol: config.symbol,
    exchange_symbol: exchangeSymbol,
    timeframe,
    since: since.toISOString(),
    limit,
    candles: candles.length,
    checksum: checksum(candles),
    cache_hit: cacheHit,
    storage_key: storageKey,
  };
}

async function readCachedPublicCandles(
  config: BacktestConfig,
  exchangeSymbol: string,
  timeframe: string,
  since: Date,
  limit: number,
): Promise<{ storage_key: string; candles: Candle[] } | null> {
  const storageKey = candleCacheStorageKey(config, exchangeSymbol, timeframe, since, limit);
  try {
    const raw = await readFile(join(ARTIFACT_ROOT, storageKey), "utf8");
    const parsed = JSON.parse(raw) as { candles?: unknown };
    if (!Array.isArray(parsed.candles)) {
      return null;
    }
    const candles = parsed.candles.filter(isCandle);
    return { storage_key: storageKey, candles };
  } catch (error) {
    if (errorCode(error) === "ENOENT" || error instanceof SyntaxError) {
      return null;
    }
    throw error;
  }
}

async function getOrFillPublicCandleCache(
  config: BacktestConfig,
  exchange: { fetchOHLCV: (symbol: string, timeframe: string, since?: number, limit?: number) => Promise<unknown[]> },
  exchangeSymbol: string,
  timeframe: string,
  since: Date,
  limit: number,
  deadlineMs: number,
  jobId: string,
): Promise<CandleCacheResult> {
  if (CANDLE_CACHE_VERSION === "chunk-v1") {
    return getOrFillPublicCandleCacheV1(config, exchange, exchangeSymbol, timeframe, since, limit, deadlineMs, jobId);
  }
  return getOrFillPublicCandleCacheV2(config, exchange, exchangeSymbol, timeframe, since, limit, deadlineMs, jobId);
}

async function getOrFillPublicCandleCacheV1(
  config: BacktestConfig,
  exchange: { fetchOHLCV: (symbol: string, timeframe: string, since?: number, limit?: number) => Promise<unknown[]> },
  exchangeSymbol: string,
  timeframe: string,
  since: Date,
  limit: number,
  deadlineMs: number,
  jobId: string,
): Promise<CandleCacheResult> {
  const cached = await readCachedPublicCandles(config, exchangeSymbol, timeframe, since, limit);
  if (cached !== null) {
    return candleCacheResult(cached.storage_key, cached.candles, true, {
      rangeCacheHits: 1,
      segmentsReused: 1,
    });
  }
  const storageKey = candleCacheStorageKey(config, exchangeSymbol, timeframe, since, limit);
  const lockKey = candleCacheLockKey(storageKey);
  let staleLockRemoved = false;
  while (true) {
    assertBeforeDeadline(deadlineMs, jobId);
    const lock = await tryAcquireCacheLock(lockKey);
    if (lock.acquired) {
      try {
        const winnerCached = await readCachedPublicCandles(config, exchangeSymbol, timeframe, since, limit);
        if (winnerCached !== null) {
          return candleCacheResult(winnerCached.storage_key, winnerCached.candles, true, {
            rangeCacheHits: 1,
            segmentsReused: 1,
          });
        }
        const candles = await fetchPublicCandles(exchange, exchangeSymbol, timeframe, since, limit, deadlineMs, jobId);
        await writeCachedPublicCandles(config, exchangeSymbol, timeframe, since, limit, candles);
        return candleCacheResult(storageKey, candles, false, {
          candlesFetched: candles.length,
          rangeCacheMisses: 1,
          missingIntervalsFetched: 1,
          segmentsCreated: 1,
        });
      } finally {
        await releaseCacheLock(lockKey);
      }
    }
    if (lock.stale && !staleLockRemoved) {
      await releaseCacheLock(lockKey);
      staleLockRemoved = true;
      continue;
    }
    const waited = await waitForCachedPublicCandles(config, exchangeSymbol, timeframe, since, limit, deadlineMs, jobId);
    if (waited !== null) {
      return candleCacheResult(waited.storage_key, waited.candles, true, {
        rangeCacheHits: 1,
        segmentsReused: 1,
      });
    }
    if (staleLockRemoved) {
      throw new Error(`Backtest candle cache lock timeout exceeded for ${storageKey}`);
    }
    await releaseCacheLock(lockKey);
    staleLockRemoved = true;
  }
}

async function getOrFillPublicCandleCacheV2(
  config: BacktestConfig,
  exchange: { fetchOHLCV: (symbol: string, timeframe: string, since?: number, limit?: number) => Promise<unknown[]> },
  exchangeSymbol: string,
  timeframe: string,
  since: Date,
  limit: number,
  deadlineMs: number,
  jobId: string,
): Promise<CandleCacheResult> {
  const requested = requestedRange(timeframe, since, limit);
  const dataset = rangeCacheDataset(config, exchangeSymbol, timeframe);
  const existing = await readRangeCacheCoverage(config, exchangeSymbol, timeframe, requested);
  if (existing.complete) {
    return candleCacheResult(dataset.index_key, existing.candles, true, {
      rangeCacheHits: 1,
      segmentsReused: existing.segmentsReused,
      bytesRead: existing.bytesRead,
    });
  }

  const lockKey = rangeCacheLockKey(dataset);
  let staleLockRemoved = false;
  while (true) {
    assertBeforeDeadline(deadlineMs, jobId);
    const lock = await tryAcquireCacheLock(lockKey);
    if (lock.acquired) {
      try {
        const winner = await readRangeCacheCoverage(config, exchangeSymbol, timeframe, requested);
        if (winner.complete) {
          return candleCacheResult(dataset.index_key, winner.candles, true, {
            rangeCacheHits: 1,
            segmentsReused: winner.segmentsReused,
            bytesRead: winner.bytesRead,
          });
        }
        const fetchResult = await fetchAndStoreMissingRangeSegments(
          config,
          exchange,
          exchangeSymbol,
          timeframe,
          requested,
          winner,
          deadlineMs,
          jobId,
        );
        return candleCacheResult(dataset.index_key, fetchResult.candles, fetchResult.missingIntervalsFetched === 0, {
          candlesFetched: fetchResult.candlesFetched,
          rangeCacheHits: winner.segmentsReused > 0 ? 1 : 0,
          rangeCacheMisses: fetchResult.missingIntervalsFetched,
          missingIntervalsFetched: fetchResult.missingIntervalsFetched,
          segmentsReused: winner.segmentsReused,
          segmentsCreated: fetchResult.segmentsCreated,
          bytesRead: winner.bytesRead + fetchResult.bytesRead,
          bytesWritten: fetchResult.bytesWritten,
        });
      } finally {
        await releaseCacheLock(lockKey);
      }
    }
    if (lock.stale && !staleLockRemoved) {
      await releaseCacheLock(lockKey);
      staleLockRemoved = true;
      continue;
    }
    const waited = await waitForRangeCachedPublicCandles(config, exchangeSymbol, timeframe, requested, deadlineMs, jobId);
    if (waited !== null) {
      return candleCacheResult(dataset.index_key, waited.candles, true, {
        rangeCacheHits: 1,
        segmentsReused: waited.segmentsReused,
        bytesRead: waited.bytesRead,
      });
    }
    if (staleLockRemoved) {
      throw new Error(`Backtest range candle cache lock timeout exceeded for ${dataset.index_key}`);
    }
    await releaseCacheLock(lockKey);
    staleLockRemoved = true;
  }
}

async function fetchAndStoreMissingRangeSegments(
  config: BacktestConfig,
  exchange: { fetchOHLCV: (symbol: string, timeframe: string, since?: number, limit?: number) => Promise<unknown[]> },
  exchangeSymbol: string,
  timeframe: string,
  requested: { startMs: number; endMs: number; stepMs: number },
  coverage: RangeCoverageResult,
  deadlineMs: number,
  jobId: string,
) {
  const fetchedCandles: Candle[] = [];
  let candlesFetched = 0;
  let segmentsCreated = 0;
  let bytesWritten = 0;
  for (const interval of coverage.missingIntervals) {
    assertBeforeDeadline(deadlineMs, jobId);
    const intervalLimit = Math.max(1, Math.ceil((interval.endMs - interval.startMs) / requested.stepMs));
    const candles = await fetchPublicCandles(exchange, exchangeSymbol, timeframe, new Date(interval.startMs), intervalLimit, deadlineMs, jobId);
    const sliced = sliceAndDedupeCandles(candles, interval.startMs, interval.endMs);
    candlesFetched += sliced.length;
    if (sliced.length === 0) {
      continue;
    }
    const writeResult = await writeRangeCacheSegment(config, exchangeSymbol, timeframe, requested.stepMs, sliced);
    await upsertRangeCacheSegment(config, exchangeSymbol, timeframe, requested.stepMs, writeResult.segment);
    fetchedCandles.push(...sliced);
    segmentsCreated += 1;
    bytesWritten += writeResult.bytesWritten;
  }
  const combined = sliceAndDedupeCandles([...coverage.candles, ...fetchedCandles], requested.startMs, requested.endMs);
  return {
    candles: combined,
    candlesFetched,
    missingIntervalsFetched: coverage.missingIntervals.length,
    segmentsCreated,
    bytesRead: 0,
    bytesWritten,
  };
}

async function waitForRangeCachedPublicCandles(
  config: BacktestConfig,
  exchangeSymbol: string,
  timeframe: string,
  requested: { startMs: number; endMs: number; stepMs: number },
  deadlineMs: number,
  jobId: string,
): Promise<RangeCoverageResult | null> {
  const waitUntil = Date.now() + CACHE_LOCK_TIMEOUT_MS;
  while (Date.now() < waitUntil) {
    assertBeforeDeadline(deadlineMs, jobId);
    const coverage = await readRangeCacheCoverage(config, exchangeSymbol, timeframe, requested);
    if (coverage.complete) {
      return coverage;
    }
    await sleep(Math.min(250, Math.max(25, waitUntil - Date.now())));
  }
  return null;
}

async function readRangeCacheCoverage(
  config: BacktestConfig,
  exchangeSymbol: string,
  timeframe: string,
  requested: { startMs: number; endMs: number; stepMs: number },
): Promise<RangeCoverageResult> {
  const index = await readRangeCacheIndex(config, exchangeSymbol, timeframe);
  if (index === null) {
    return {
      complete: false,
      candles: [],
      missingIntervals: [{ startMs: requested.startMs, endMs: requested.endMs }],
      segmentsReused: 0,
      bytesRead: 0,
    };
  }
  const intervals: Array<{ startMs: number; endMs: number }> = [];
  const candles: Candle[] = [];
  let segmentsReused = 0;
  let bytesRead = 0;
  for (const segment of index.segments.sort((a, b) => Date.parse(a.range_start) - Date.parse(b.range_start))) {
    const startMs = Date.parse(segment.range_start);
    const endMs = Date.parse(segment.range_end);
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= requested.startMs || startMs >= requested.endMs) {
      continue;
    }
    const segmentRead = await readRangeCacheSegment(segment);
    if (segmentRead === null) {
      continue;
    }
    const sliced = sliceAndDedupeCandles(segmentRead.candles, requested.startMs, requested.endMs);
    if (sliced.length === 0) {
      continue;
    }
    candles.push(...sliced);
    intervals.push({ startMs: Math.max(startMs, requested.startMs), endMs: Math.min(endMs, requested.endMs) });
    segmentsReused += 1;
    bytesRead += segmentRead.bytesRead;
  }
  const missingIntervals = missingIntervalsForCoverage(requested, intervals);
  return {
    complete: missingIntervals.length === 0,
    candles: sliceAndDedupeCandles(candles, requested.startMs, requested.endMs),
    missingIntervals,
    segmentsReused,
    bytesRead,
  };
}

async function readRangeCacheIndex(
  config: BacktestConfig,
  exchangeSymbol: string,
  timeframe: string,
): Promise<RangeCacheIndex | null> {
  const dataset = rangeCacheDataset(config, exchangeSymbol, timeframe);
  try {
    const raw = await readFile(join(ARTIFACT_ROOT, dataset.index_key), "utf8");
    const parsed = JSON.parse(raw) as Partial<RangeCacheIndex>;
    if (parsed.cache_version !== "range-v2" || !Array.isArray(parsed.segments)) {
      return null;
    }
    return {
      cache_version: "range-v2",
      source: "ccxt-public-readonly",
      exchange: "binance",
      symbol: config.symbol,
      exchange_symbol: exchangeSymbol,
      timeframe,
      step_ms: timeframeToMs(timeframe),
      segments: parsed.segments.filter(isRangeCacheSegment),
      updated_at: typeof parsed.updated_at === "string" ? parsed.updated_at : new Date(0).toISOString(),
    };
  } catch (error) {
    if (errorCode(error) === "ENOENT" || error instanceof SyntaxError) {
      return null;
    }
    throw error;
  }
}

async function readRangeCacheSegment(segment: RangeCacheSegment): Promise<{ candles: Candle[]; bytesRead: number } | null> {
  try {
    const raw = await readFile(join(ARTIFACT_ROOT, segment.storage_key), "utf8");
    const parsed = JSON.parse(raw) as { candles?: unknown; checksum?: unknown };
    if (!Array.isArray(parsed.candles)) {
      return null;
    }
    const candles = parsed.candles.filter(isCandle);
    if (checksum(candles) !== segment.checksum) {
      return null;
    }
    return { candles, bytesRead: Buffer.byteLength(raw, "utf8") };
  } catch (error) {
    if (errorCode(error) === "ENOENT" || error instanceof SyntaxError) {
      return null;
    }
    throw error;
  }
}

async function writeRangeCacheSegment(
  config: BacktestConfig,
  exchangeSymbol: string,
  timeframe: string,
  stepMs: number,
  candles: Candle[],
): Promise<{ segment: RangeCacheSegment; bytesWritten: number }> {
  const normalized = sliceAndDedupeCandles(candles, Number.NEGATIVE_INFINITY, Number.POSITIVE_INFINITY);
  const rangeStart = normalized[0]?.timestamp;
  const rangeEnd = normalized[normalized.length - 1]?.timestamp + stepMs;
  if (!Number.isFinite(rangeStart) || !Number.isFinite(rangeEnd) || normalized.length === 0) {
    throw new Error("Cannot write empty Backtest candle range segment");
  }
  const segmentChecksum = checksum(normalized);
  const storageKey = rangeCacheSegmentStorageKey(config, exchangeSymbol, timeframe, rangeStart, rangeEnd, segmentChecksum);
  const segment: RangeCacheSegment = {
    range_start: new Date(rangeStart).toISOString(),
    range_end: new Date(rangeEnd).toISOString(),
    step_ms: stepMs,
    storage_key: storageKey,
    checksum: segmentChecksum,
    created_at: new Date().toISOString(),
    candle_count: normalized.length,
  };
  const payload = {
    cache_version: "range-v2",
    source: "ccxt-public-readonly",
    exchange: "binance",
    symbol: config.symbol,
    exchange_symbol: exchangeSymbol,
    timeframe,
    ...segment,
    candles: normalized,
  };
  const content = `${JSON.stringify(payload)}\n`;
  const bytesWritten = Buffer.byteLength(content, "utf8");
  if (bytesWritten > MAX_ARTIFACT_BYTES) {
    throw new Error(`Backtest candle cache segment size limit exceeded: ${bytesWritten} > ${MAX_ARTIFACT_BYTES}`);
  }
  const absolutePath = join(ARTIFACT_ROOT, storageKey);
  await mkdir(dirname(absolutePath), { recursive: true });
  const tempPath = `${absolutePath}.${WORKER_ID}.${randomUUID()}.tmp`;
  await writeFile(tempPath, content, "utf8");
  await rename(tempPath, absolutePath);
  return { segment, bytesWritten };
}

async function upsertRangeCacheSegment(
  config: BacktestConfig,
  exchangeSymbol: string,
  timeframe: string,
  stepMs: number,
  segment: RangeCacheSegment,
) {
  const dataset = rangeCacheDataset(config, exchangeSymbol, timeframe);
  const existing = await readRangeCacheIndex(config, exchangeSymbol, timeframe);
  const segments = [...(existing?.segments ?? []).filter((item) => item.storage_key !== segment.storage_key), segment]
    .filter((item) => item.step_ms === stepMs)
    .sort((a, b) => Date.parse(a.range_start) - Date.parse(b.range_start));
  const index: RangeCacheIndex = {
    cache_version: "range-v2",
    source: "ccxt-public-readonly",
    exchange: "binance",
    symbol: config.symbol,
    exchange_symbol: exchangeSymbol,
    timeframe,
    step_ms: stepMs,
    segments,
    updated_at: new Date().toISOString(),
  };
  const absolutePath = join(ARTIFACT_ROOT, dataset.index_key);
  await mkdir(dirname(absolutePath), { recursive: true });
  const content = `${JSON.stringify(index)}\n`;
  const tempPath = `${absolutePath}.${WORKER_ID}.${randomUUID()}.tmp`;
  await writeFile(tempPath, content, "utf8");
  await rename(tempPath, absolutePath);
}

function missingIntervalsForCoverage(
  requested: { startMs: number; endMs: number },
  intervals: Array<{ startMs: number; endMs: number }>,
): Array<{ startMs: number; endMs: number }> {
  const missing: Array<{ startMs: number; endMs: number }> = [];
  let cursor = requested.startMs;
  for (const interval of intervals
    .filter((item) => item.endMs > requested.startMs && item.startMs < requested.endMs)
    .sort((a, b) => a.startMs - b.startMs)) {
    const startMs = Math.max(interval.startMs, requested.startMs);
    const endMs = Math.min(interval.endMs, requested.endMs);
    if (startMs > cursor) {
      missing.push({ startMs: cursor, endMs: startMs });
    }
    cursor = Math.max(cursor, endMs);
  }
  if (cursor < requested.endMs) {
    missing.push({ startMs: cursor, endMs: requested.endMs });
  }
  return missing.filter((interval) => interval.endMs > interval.startMs);
}

function sliceAndDedupeCandles(candles: Candle[], startMs: number, endMs: number): Candle[] {
  const byTimestamp = new Map<number, Candle>();
  for (const candle of candles) {
    if (candle.timestamp >= startMs && candle.timestamp < endMs) {
      byTimestamp.set(candle.timestamp, candle);
    }
  }
  return [...byTimestamp.values()].sort((a, b) => a.timestamp - b.timestamp);
}

async function writeCachedPublicCandles(
  config: BacktestConfig,
  exchangeSymbol: string,
  timeframe: string,
  since: Date,
  limit: number,
  candles: Candle[],
): Promise<string> {
  const storageKey = candleCacheStorageKey(config, exchangeSymbol, timeframe, since, limit);
  const payload = {
    source: "ccxt-public-readonly",
    exchange: "binance",
    symbol: config.symbol,
    exchange_symbol: exchangeSymbol,
    timeframe,
    since: since.toISOString(),
    limit,
    checksum: checksum(candles),
    candles,
    created_at: new Date().toISOString(),
  };
  const absolutePath = join(ARTIFACT_ROOT, storageKey);
  const content = `${JSON.stringify(payload)}\n`;
  const bytes = Buffer.byteLength(content, "utf8");
  if (bytes > MAX_ARTIFACT_BYTES) {
    throw new Error(`Backtest candle cache size limit exceeded: ${bytes} > ${MAX_ARTIFACT_BYTES}`);
  }
  await mkdir(join(ARTIFACT_ROOT, "cache", "candles"), { recursive: true });
  const tempPath = join(ARTIFACT_ROOT, `${storageKey}.${WORKER_ID}.${randomUUID()}.tmp`);
  await writeFile(tempPath, content, "utf8");
  await rename(tempPath, absolutePath);
  return storageKey;
}

async function tryAcquireCacheLock(lockKey: string): Promise<{ acquired: boolean; stale: boolean }> {
  const lockPath = join(ARTIFACT_ROOT, lockKey);
  await mkdir(dirname(lockPath), { recursive: true });
  try {
    const handle = await open(lockPath, "wx");
    try {
      await handle.writeFile(
        JSON.stringify({ worker_id: WORKER_ID, pid: process.pid, created_at: new Date().toISOString() }),
        "utf8",
      );
    } finally {
      await handle.close();
    }
    return { acquired: true, stale: false };
  } catch (error) {
    if (errorCode(error) !== "EEXIST") {
      throw error;
    }
    const stale = await isCacheLockStale(lockPath);
    return { acquired: false, stale };
  }
}

async function waitForCachedPublicCandles(
  config: BacktestConfig,
  exchangeSymbol: string,
  timeframe: string,
  since: Date,
  limit: number,
  deadlineMs: number,
  jobId: string,
): Promise<{ storage_key: string; candles: Candle[] } | null> {
  const waitUntil = Date.now() + CACHE_LOCK_TIMEOUT_MS;
  while (Date.now() < waitUntil) {
    assertBeforeDeadline(deadlineMs, jobId);
    const cached = await readCachedPublicCandles(config, exchangeSymbol, timeframe, since, limit);
    if (cached !== null) {
      return cached;
    }
    await sleep(Math.min(250, Math.max(25, waitUntil - Date.now())));
  }
  return null;
}

async function isCacheLockStale(lockPath: string): Promise<boolean> {
  try {
    const info = await stat(lockPath);
    return Date.now() - info.mtimeMs > CACHE_LOCK_TIMEOUT_MS;
  } catch (error) {
    if (errorCode(error) === "ENOENT") {
      return false;
    }
    throw error;
  }
}

async function releaseCacheLock(lockKey: string) {
  await rm(join(ARTIFACT_ROOT, lockKey), { force: true });
}

function candleCacheStorageKey(
  config: BacktestConfig,
  exchangeSymbol: string,
  timeframe: string,
  since: Date,
  limit: number,
): string {
  const key = checksum({
    source: "ccxt-public-readonly",
    exchange: "binance",
    symbol: config.symbol,
    exchange_symbol: exchangeSymbol,
    timeframe,
    range_start: config.start,
    range_end: config.end,
    since: since.toISOString(),
    limit,
  });
  return `cache/candles/${key}.json`;
}

function candleCacheLockKey(storageKey: string): string {
  const fileName = storageKey.split("/").pop() ?? checksum(storageKey);
  return `cache/locks/${fileName}.lock`;
}

function requestedRange(timeframe: string, since: Date, limit: number): { startMs: number; endMs: number; stepMs: number } {
  const stepMs = timeframeToMs(timeframe);
  const requestedLimit = Math.max(1, Math.trunc(limit));
  const startMs = since.getTime();
  return { startMs, endMs: startMs + requestedLimit * stepMs, stepMs };
}

function rangeCacheDataset(config: BacktestConfig, exchangeSymbol: string, timeframe: string) {
  const directory = [
    "cache",
    "index-v2",
    "ccxt-public-readonly",
    "binance",
    pathToken(exchangeSymbol),
    pathToken(timeframe),
  ].join("/");
  return {
    index_key: `${directory}/manifest.json`,
    lock_key: `cache/locks-v2/${checksum({ source: "ccxt-public-readonly", exchange: "binance", symbol: config.symbol, exchange_symbol: exchangeSymbol, timeframe })}.lock`,
  };
}

function rangeCacheLockKey(dataset: ReturnType<typeof rangeCacheDataset>): string {
  return dataset.lock_key;
}

function rangeCacheSegmentStorageKey(
  config: BacktestConfig,
  exchangeSymbol: string,
  timeframe: string,
  rangeStart: number,
  rangeEnd: number,
  segmentChecksum: string,
): string {
  const rangeHash = checksum({
    source: "ccxt-public-readonly",
    exchange: "binance",
    symbol: config.symbol,
    exchange_symbol: exchangeSymbol,
    timeframe,
    range_start: new Date(rangeStart).toISOString(),
    range_end: new Date(rangeEnd).toISOString(),
    checksum: segmentChecksum,
  });
  return [
    "cache",
    "candles-v2",
    "ccxt-public-readonly",
    "binance",
    pathToken(exchangeSymbol),
    pathToken(timeframe),
    `${rangeHash}.json`,
  ].join("/");
}

function pathToken(value: string): string {
  return value.trim().replace(/[^a-zA-Z0-9._=-]+/g, "_") || "unknown";
}

function isRangeCacheSegment(value: unknown): value is RangeCacheSegment {
  const record = asRecord(value);
  return (
    typeof record.range_start === "string" &&
    typeof record.range_end === "string" &&
    typeof record.storage_key === "string" &&
    typeof record.checksum === "string" &&
    typeof record.created_at === "string" &&
    typeof record.candle_count === "number" &&
    typeof record.step_ms === "number" &&
    Number.isFinite(record.candle_count) &&
    Number.isFinite(record.step_ms)
  );
}

function candleCacheResult(
  storageKey: string,
  candles: Candle[],
  cacheHit: boolean,
  overrides: Partial<Omit<CandleCacheResult, "storage_key" | "candles" | "cacheHit">> = {},
): CandleCacheResult {
  return {
    storage_key: storageKey,
    candles,
    cacheHit,
    candlesFetched: 0,
    rangeCacheHits: 0,
    rangeCacheMisses: 0,
    missingIntervalsFetched: 0,
    segmentsReused: 0,
    segmentsCreated: 0,
    bytesRead: 0,
    bytesWritten: 0,
    ...overrides,
  };
}

function errorCode(error: unknown): string {
  return typeof error === "object" && error !== null && "code" in error ? String((error as { code?: unknown }).code) : "";
}

function isCandle(value: unknown): value is Candle {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candle = value as Candle;
  return (
    Number.isFinite(candle.timestamp) &&
    Number.isFinite(candle.open) &&
    Number.isFinite(candle.high) &&
    Number.isFinite(candle.low) &&
    Number.isFinite(candle.close) &&
    Number.isFinite(candle.volume)
  );
}

function normalizedConfig(config: BacktestConfig): BacktestConfig {
  const start = new Date(config.start);
  const end = new Date(config.end);
  if (!Number.isFinite(start.getTime())) {
    throw new Error(`Invalid backtest start date: ${config.start}`);
  }
  if (!Number.isFinite(end.getTime())) {
    throw new Error(`Invalid backtest end date: ${config.end}`);
  }
  if (end.getTime() <= start.getTime()) {
    throw new Error("Backtest end must be after start");
  }
  return {
    ...config,
    timeframe: normalizeTimeframe(config.timeframe),
    start: start.toISOString(),
    end: end.toISOString(),
  };
}

function buildStrategyRuntime(job: RunJobRow, adapter: StrategyAdapter, candleHistory: Map<string, Candle[]>): StrategyRuntime {
  const logic = parseStrategyLogic(job.payload_json.strategy_logic, adapter);
  if (!logic) {
    return {
      semantics: "freeform_spec_backtest_adapter",
      strategyLogic: null,
      warnings: [
        "No executable strategy_logic was provided; worker used the compatibility freeform strategy adapter.",
        "Freeform strategy_spec text is preserved as evidence input but is not fully semantically executed.",
      ],
      shouldEnter: () => true,
      source: generatedAdapterSource(adapter),
    };
  }
  const key = candleHistoryKey(normalizeCcxtSymbol(job.payload_json.backtest_config.symbol), adapter.timeframe);
  return {
    semantics: "semantic_strategy_logic",
    strategyLogic: logic,
    warnings: ["Worker executed constrained strategy_logic DSL v1; no model-generated code was executed."],
    shouldEnter: (when) => evaluateStrategyLogic(logic, candleHistory.get(key) ?? [], when),
    source: generatedStrategyLogicSource(logic),
  };
}

function parseStrategyLogic(value: unknown, adapter: StrategyAdapter): StrategyLogicV1 | null {
  const record = asRecord(value);
  if (Object.keys(record).length === 0) {
    return null;
  }
  if (record.logic_version !== "backtest-strategy-logic.v1") {
    throw new Error("strategy_logic.logic_version must be backtest-strategy-logic.v1");
  }
  const indicators = asRecord(record.indicators);
  const fastEma = parseIndicator(indicators.fast_ema, "ema", "strategy_logic.indicators.fast_ema");
  const slowEma = parseIndicator(indicators.slow_ema, "ema", "strategy_logic.indicators.slow_ema");
  const rawRsi = indicators.rsi === undefined ? undefined : parseIndicator(indicators.rsi, "rsi", "strategy_logic.indicators.rsi");
  const entry = asRecord(record.entry);
  const rawConditions = Array.isArray(entry.all) ? entry.all : [];
  if (rawConditions.length === 0) {
    throw new Error("strategy_logic.entry.all must contain at least one condition");
  }
  const conditions = rawConditions.map(parseCondition);
  const exit = asRecord(record.exit);
  const risk = asRecord(record.risk);
  const takeProfit = clamp(numberValue(exit.take_profit_pct, adapter.percent_take_profit), 0.01, 100);
  const stopLoss = clamp(numberValue(exit.stop_loss_pct, adapter.percent_stop_loss), 0.01, 100);
  const maxHolding = Math.max(1, Math.trunc(numberValue(exit.max_holding_minutes, adapter.minute_estimated_time)));
  const cost = clamp(numberValue(risk.cost ?? risk.position_size, adapter.cost), 1, adapter.cost * 1000);
  if (record.position !== undefined && record.position !== "long") {
    throw new Error("strategy_logic.position only supports long in v1");
  }
  return {
    logic_version: "backtest-strategy-logic.v1",
    position: "long",
    indicators: {
      fast_ema: { kind: "ema", period: fastEma.period, source: "close" },
      slow_ema: { kind: "ema", period: slowEma.period, source: "close" },
      ...(rawRsi ? { rsi: { kind: "rsi" as const, period: rawRsi.period, source: "close" as const } } : {}),
    },
    entry: { all: conditions },
    exit: {
      take_profit_pct: takeProfit,
      stop_loss_pct: stopLoss,
      max_holding_minutes: maxHolding,
    },
    risk: { cost },
  };
}

function parseIndicator(value: unknown, expectedKind: "ema" | "rsi", path: string) {
  const record = asRecord(value);
  if (record.kind !== expectedKind) {
    throw new Error(`${path}.kind must be ${expectedKind}`);
  }
  const period = Math.trunc(numberValue(record.period, NaN));
  if (!Number.isFinite(period) || period < 2 || period > 500) {
    throw new Error(`${path}.period must be between 2 and 500`);
  }
  if (record.source !== undefined && record.source !== "close") {
    throw new Error(`${path}.source only supports close`);
  }
  return { period };
}

function parseCondition(value: unknown): StrategyLogicCondition {
  const record = asRecord(value);
  const type = record.type;
  if (type !== "crossover" && type !== "crossunder" && type !== "greater_than" && type !== "less_than") {
    throw new Error("strategy_logic entry condition type is unsupported");
  }
  const left = stringOrNull(record.left);
  if (!left) {
    throw new Error("strategy_logic entry condition left is required");
  }
  const right = typeof record.right === "number" ? record.right : stringOrNull(record.right);
  if (right === null) {
    throw new Error("strategy_logic entry condition right is required");
  }
  return { type, left, right };
}

function evaluateStrategyLogic(logic: StrategyLogicV1, candles: Candle[], when: unknown): boolean {
  const timestamp = signalTimestamp(when);
  if (timestamp === null) {
    return false;
  }
  const history = candles.filter((candle) => candle.timestamp <= timestamp).sort((a, b) => a.timestamp - b.timestamp);
  if (history.length < Math.max(logic.indicators.fast_ema.period, logic.indicators.slow_ema.period, logic.indicators.rsi?.period ?? 0) + 1) {
    return false;
  }
  const closes = history.map((candle) => candle.close);
  const series: Record<string, Array<number | null>> = {
    close: closes,
    fast_ema: emaSeries(closes, logic.indicators.fast_ema.period),
    slow_ema: emaSeries(closes, logic.indicators.slow_ema.period),
  };
  if (logic.indicators.rsi) {
    series.rsi = rsiSeries(closes, logic.indicators.rsi.period);
  }
  const currentIndex = closes.length - 1;
  return logic.entry.all.every((condition) => evaluateCondition(condition, series, currentIndex));
}

function evaluateCondition(condition: StrategyLogicCondition, series: Record<string, Array<number | null>>, index: number): boolean {
  const leftNow = seriesValue(condition.left, series, index);
  const rightNow = conditionRightValue(condition.right, series, index);
  if (leftNow === null || rightNow === null) {
    return false;
  }
  if (condition.type === "greater_than") {
    return leftNow > rightNow;
  }
  if (condition.type === "less_than") {
    return leftNow < rightNow;
  }
  const leftPrev = seriesValue(condition.left, series, index - 1);
  const rightPrev = conditionRightValue(condition.right, series, index - 1);
  if (leftPrev === null || rightPrev === null) {
    return false;
  }
  if (condition.type === "crossover") {
    return leftPrev <= rightPrev && leftNow > rightNow;
  }
  return leftPrev >= rightPrev && leftNow < rightNow;
}

function seriesValue(name: string, series: Record<string, Array<number | null>>, index: number): number | null {
  if (index < 0) {
    return null;
  }
  const values = series[name];
  const value = values?.[index];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function conditionRightValue(value: string | number, series: Record<string, Array<number | null>>, index: number): number | null {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  return seriesValue(value, series, index);
}

function emaSeries(values: number[], period: number): Array<number | null> {
  const result: Array<number | null> = Array(values.length).fill(null);
  if (values.length < period) {
    return result;
  }
  let ema = values.slice(0, period).reduce((sum, value) => sum + value, 0) / period;
  result[period - 1] = ema;
  const multiplier = 2 / (period + 1);
  for (let index = period; index < values.length; index += 1) {
    ema = (values[index] - ema) * multiplier + ema;
    result[index] = ema;
  }
  return result;
}

function rsiSeries(values: number[], period: number): Array<number | null> {
  const result: Array<number | null> = Array(values.length).fill(null);
  if (values.length <= period) {
    return result;
  }
  let gains = 0;
  let losses = 0;
  for (let index = 1; index <= period; index += 1) {
    const delta = values[index] - values[index - 1];
    gains += Math.max(delta, 0);
    losses += Math.max(-delta, 0);
  }
  let averageGain = gains / period;
  let averageLoss = losses / period;
  result[period] = rsiFromAverages(averageGain, averageLoss);
  for (let index = period + 1; index < values.length; index += 1) {
    const delta = values[index] - values[index - 1];
    averageGain = (averageGain * (period - 1) + Math.max(delta, 0)) / period;
    averageLoss = (averageLoss * (period - 1) + Math.max(-delta, 0)) / period;
    result[index] = rsiFromAverages(averageGain, averageLoss);
  }
  return result;
}

function rsiFromAverages(averageGain: number, averageLoss: number): number {
  if (averageLoss === 0) {
    return 100;
  }
  const relativeStrength = averageGain / averageLoss;
  return 100 - 100 / (1 + relativeStrength);
}

function rememberCandles(history: Map<string, Candle[]>, exchangeSymbol: string, timeframe: string, candles: Candle[]) {
  const key = candleHistoryKey(exchangeSymbol, timeframe);
  const merged = [...(history.get(key) ?? []), ...candles];
  const byTimestamp = new Map<number, Candle>();
  for (const candle of merged) {
    byTimestamp.set(candle.timestamp, candle);
  }
  history.set(key, [...byTimestamp.values()].sort((a, b) => a.timestamp - b.timestamp));
}

function candleHistoryKey(exchangeSymbol: string, timeframe: string): string {
  return `${exchangeSymbol}:${normalizeTimeframe(timeframe)}`;
}

function signalTimestamp(value: unknown): number | null {
  if (value instanceof Date) {
    return Number.isFinite(value.getTime()) ? value.getTime() : null;
  }
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value === "string") {
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function buildStrategyAdapter(job: RunJobRow): StrategyAdapter {
  const config = normalizedConfig(job.payload_json.backtest_config);
  const strategySpec = asRecord(job.payload_json.strategy_spec);
  const strategyLogic = asRecord(job.payload_json.strategy_logic);
  const position =
    strategyLogic.logic_version === "backtest-strategy-logic.v1"
      ? "long"
      : findString(strategySpec, ["position", "side", "direction"], "long").toLowerCase() === "short"
        ? "short"
        : "long";
  const exit = asRecord(strategyLogic.exit);
  const risk = asRecord(strategyLogic.risk);
  return {
    strategy_name: safeRegistryName(`scb-strategy-${job.id}`),
    exchange_name: safeRegistryName(`scb-exchange-${job.id}`),
    frame_name: safeRegistryName(`scb-frame-${job.id}`),
    timeframe: config.timeframe,
    position,
    percent_take_profit: clamp(numberValue(exit.take_profit_pct, findNumber(strategySpec, ["take_profit_pct", "takeProfitPct", "tp_pct", "take_profit"], 2)), 0.01, 100),
    percent_stop_loss: clamp(numberValue(exit.stop_loss_pct, findNumber(strategySpec, ["stop_loss_pct", "stopLossPct", "sl_pct", "stop_loss"], 1)), 0.01, 100),
    cost: clamp(numberValue(risk.cost ?? risk.position_size, findNumber(strategySpec, ["cost", "trade_cost", "position_size"], config.initial_capital / 10)), 1, config.initial_capital),
    minute_estimated_time: Math.max(1, Math.trunc(numberValue(exit.max_holding_minutes, findNumber(strategySpec, ["minute_estimated_time", "holding_minutes"], 1440)))),
  };
}

async function registerExchangeSchema(schema: Parameters<typeof addExchangeSchema>[0]) {
  try {
    addExchangeSchema(schema);
  } catch (error) {
    if (!isRegistryAlreadyExistsError(error)) {
      throw error;
    }
    await overrideExchangeSchema(schema);
  }
}

async function registerFrameSchema(schema: Parameters<typeof addFrameSchema>[0]) {
  try {
    addFrameSchema(schema);
  } catch (error) {
    if (!isRegistryAlreadyExistsError(error)) {
      throw error;
    }
    await overrideFrameSchema(schema);
  }
}

async function registerStrategySchema(schema: Parameters<typeof addStrategySchema>[0]) {
  try {
    addStrategySchema(schema);
  } catch (error) {
    if (!isRegistryAlreadyExistsError(error)) {
      throw error;
    }
    await overrideStrategySchema(schema);
  }
}

function isRegistryAlreadyExistsError(error: unknown) {
  return error instanceof Error && /\balready exist\b/i.test(error.message);
}

async function writeBacktestArtifacts(job: RunJobRow, execution: BacktestExecutionResult) {
  const runBase = `runs/${job.run_id}`;
  const artifacts = {
    plan: artifact(runBase, "backtest_plan", "backtest_plan.json", {
      backtest_config: job.payload_json.backtest_config,
      strategy_spec: job.payload_json.strategy_spec,
      strategy_logic: execution.strategyLogic,
      execution_semantics: execution.executionSemantics,
      adapter: execution.adapter,
    }),
    report: artifact(runBase, "backtest_report", "backtest-report.json", execution.report),
    strategyLogic: artifact(runBase, "backtest_strategy_logic", "strategy-logic.json", {
      strategy_logic: execution.strategyLogic,
      execution_semantics: execution.executionSemantics,
    }),
    trades: artifact(runBase, "backtest_trades", "trades.json", execution.trades),
    equityCurve: artifact(runBase, "backtest_equity_curve", "equity-curve.json", execution.equityCurve),
    cacheManifest: artifact(runBase, "market_data_cache_manifest", "candle-cache-manifest.json", execution.manifest),
    sourceBundle: artifact(runBase, "backtest_source_bundle", "strategy-adapter-source.json", execution.sourceBundle),
    metadata: artifact(runBase, "backtest_run_metadata", "run-metadata.json", execution.metadata),
  };
  await mkdir(join(ARTIFACT_ROOT, runBase), { recursive: true });
  await Promise.all(Object.values(artifacts).map((item) => writeArtifactFile(item)));
  return artifacts;
}

function artifact(runBase: string, kind: string, displayName: string, content: unknown): ArtifactSpec {
  return {
    kind,
    mime_type: "application/json",
    display_name: displayName,
    storage_key: `${runBase}/${displayName}`,
    content,
  };
}

async function writeArtifactFile(artifact: ArtifactSpec) {
  const path = join(ARTIFACT_ROOT, artifact.storage_key);
  const content = `${JSON.stringify(artifact.content, null, 2)}\n`;
  const bytes = Buffer.byteLength(content, "utf8");
  if (bytes > MAX_ARTIFACT_BYTES) {
    throw new Error(`Backtest artifact size limit exceeded for ${artifact.display_name}: ${bytes} > ${MAX_ARTIFACT_BYTES}`);
  }
  await writeFile(path, content, "utf8");
}

function normalizeTrade(event: BacktestClosedEvent, fallbackSymbol: string): NormalizedTrade {
  const signal = event.signal ?? {};
  const openedAt = timestampToIso(signal.openTimestamp ?? signal.createdAt ?? signal.openedAt);
  return {
    id: stringOrNull(signal.id ?? signal.signalId),
    symbol: event.symbol ?? stringOrNull(signal.symbol) ?? fallbackSymbol,
    side: stringOrNull(signal.position),
    close_reason: event.closeReason ?? null,
    opened_at: openedAt,
    closed_at: timestampToIso(event.closeTimestamp),
    entry_price: numberOrNull(event.pnl?.priceOpen ?? signal.priceOpen),
    exit_price: numberOrNull(event.pnl?.priceClose ?? event.currentPrice),
    pnl_percentage: numberOrNull(event.pnl?.pnlPercentage),
    pnl_cost: numberOrNull(event.pnl?.pnlCost),
    cost: numberOrNull(event.pnl?.pnlEntries ?? signal.cost),
  };
}

function buildEquityCurve(initialCapital: number, trades: NormalizedTrade[]): EquityPoint[] {
  let equity = initialCapital;
  let peak = initialCapital;
  return [
    { index: 0, timestamp: null, equity, pnl_cost: 0, drawdown_pct: 0 },
    ...trades.map((trade, index) => {
      const pnlCost = trade.pnl_cost ?? (trade.pnl_percentage === null ? 0 : initialCapital * (trade.pnl_percentage / 100));
      equity += pnlCost;
      peak = Math.max(peak, equity);
      const drawdownPct = peak <= 0 ? 0 : ((peak - equity) / peak) * 100;
      return {
        index: index + 1,
        timestamp: trade.closed_at,
        equity,
        pnl_cost: pnlCost,
        drawdown_pct: drawdownPct,
      };
    }),
  ];
}

function buildMetrics(initialCapital: number, trades: NormalizedTrade[], equityCurve: EquityPoint[]): BacktestMetrics {
  const pnlAbsolute = equityCurve[equityCurve.length - 1]?.equity - initialCapital;
  const returns = trades.map((trade) => trade.pnl_percentage).filter((value): value is number => value !== null);
  const wins = returns.filter((value) => value > 0).length;
  return {
    pnl: {
      absolute: roundMetric(pnlAbsolute),
      percentage: roundMetric(initialCapital === 0 ? 0 : (pnlAbsolute / initialCapital) * 100),
    },
    max_drawdown: roundMetric(Math.max(0, ...equityCurve.map((point) => point.drawdown_pct))),
    trade_count: trades.length,
    win_rate: returns.length === 0 ? null : roundMetric((wins / returns.length) * 100),
    sharpe: ratioMetric(returns, false),
    sortino: ratioMetric(returns, true),
  };
}

function summarizeBacktestEvent(event: unknown): Record<string, unknown> {
  const record = asRecord(event);
  const pnl = asRecord(record.pnl);
  return {
    action: stringOrNull(record.action),
    symbol: stringOrNull(record.symbol),
    close_reason: stringOrNull(record.closeReason),
    close_timestamp: numberOrNull(record.closeTimestamp),
    pnl_percentage: numberOrNull(pnl.pnlPercentage),
    pnl_cost: numberOrNull(pnl.pnlCost),
  };
}

function isClosedEvent(event: unknown): event is BacktestClosedEvent {
  const record = asRecord(event);
  return record.action === "closed" && typeof record.pnl === "object" && record.pnl !== null;
}

function normalizeTimeframe(value: string): string {
  const normalized = value.trim();
  const aliases: Record<string, string> = {
    M1: "1m",
    M5: "5m",
    M15: "15m",
    M30: "30m",
    H1: "1h",
    H4: "4h",
    D1: "1d",
    W1: "1w",
  };
  return aliases[normalized.toUpperCase()] ?? normalized.toLowerCase();
}

function timeframeToMs(value: string): number {
  const match = /^(\d+)([mhdw])$/.exec(normalizeTimeframe(value));
  if (!match) {
    return 60_000;
  }
  const amount = Number(match[1]);
  const unit = match[2];
  const unitMs = unit === "m" ? 60_000 : unit === "h" ? 3_600_000 : unit === "d" ? 86_400_000 : 604_800_000;
  return amount * unitMs;
}

function normalizeCcxtSymbol(symbol: string): string {
  const trimmed = symbol.trim().toUpperCase();
  if (trimmed.includes("/")) {
    return trimmed;
  }
  for (const quote of ["USDT", "USDC", "USD", "BTC", "ETH"]) {
    if (trimmed.endsWith(quote) && trimmed.length > quote.length) {
      return `${trimmed.slice(0, -quote.length)}/${quote}`;
    }
  }
  return trimmed;
}

function safeRegistryName(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]/g, "-");
}

function generatedAdapterSource(adapter: StrategyAdapter): string {
  return [
    "addStrategySchema({",
    `  strategyName: ${JSON.stringify(adapter.strategy_name)},`,
    `  interval: ${JSON.stringify(adapter.timeframe)},`,
    "  getSignal: async (_symbol, _when, currentPrice) => {",
    "    if (!Number.isFinite(currentPrice) || currentPrice <= 0) return null;",
    "    const bracket = Position.bracket({",
    `      position: ${JSON.stringify(adapter.position)},`,
    "      currentPrice,",
    `      percentTakeProfit: ${adapter.percent_take_profit},`,
    `      percentStopLoss: ${adapter.percent_stop_loss},`,
    "    });",
    `    return { ...bracket, minuteEstimatedTime: ${adapter.minute_estimated_time}, cost: ${adapter.cost} };`,
    "  },",
    "});",
  ].join("\n");
}

function generatedStrategyLogicSource(logic: StrategyLogicV1): string {
  return [
    "strategy_logic evaluator v1:",
    `logic_version=${logic.logic_version}`,
    `entry=${JSON.stringify(logic.entry.all)}`,
    `indicators=${JSON.stringify(logic.indicators)}`,
    `exit=${JSON.stringify(logic.exit)}`,
    "runtime policy: deterministic DSL evaluation only; no model-generated code execution",
  ].join("\n");
}

function numberValue(value: unknown, fallback: number): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number(value.match(/-?\d+(?:\.\d+)?/)?.[0]);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return fallback;
}

function findNumber(source: unknown, keys: string[], fallback: number): number {
  const record = asRecord(source);
  const found = keys.find((key) => Object.prototype.hasOwnProperty.call(record, key));
  return found === undefined ? fallback : numberValue(record[found], fallback);
}

function findString(source: unknown, keys: string[], fallback: string): string {
  const record = asRecord(source);
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(record, key)) {
      const value = record[key];
      return typeof value === "string" && value.trim() ? value : fallback;
    }
  }
  return fallback;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function timestampToIso(value: unknown): string | null {
  const timestamp = numberOrNull(value);
  if (timestamp === null) {
    return stringOrNull(value);
  }
  return new Date(timestamp).toISOString();
}

function ratioMetric(values: number[], downsideOnly: boolean): number | null {
  if (values.length < 2) {
    return null;
  }
  const selected = downsideOnly ? values.filter((value) => value < 0) : values;
  if (selected.length < 2) {
    return null;
  }
  const mean = selected.reduce((sum, value) => sum + value, 0) / selected.length;
  const variance = selected.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (selected.length - 1);
  const deviation = Math.sqrt(variance);
  if (deviation === 0) {
    return null;
  }
  return roundMetric(mean / deviation);
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function roundMetric(value: number): number {
  return Math.round(value * 10000) / 10000;
}

function checksum(value: unknown): string {
  return createHash("sha256").update(stableJson(value)).digest("hex");
}

async function throttleDataFetch(source: string, symbol: string, timeframe: string) {
  if (DATA_FETCH_THROTTLE_MS <= 0) {
    return;
  }
  const key = `${source}:${symbol}:${timeframe}`;
  const now = Date.now();
  const previous = lastFetchAtByThrottleKey.get(key) ?? 0;
  const waitMs = previous + DATA_FETCH_THROTTLE_MS - now;
  if (waitMs > 0) {
    await sleep(waitMs);
  }
  lastFetchAtByThrottleKey.set(key, Date.now());
}

async function withTimeout<T>(promise: Promise<T>, timeoutMs: number, jobId: string): Promise<T> {
  let timer: NodeJS.Timeout | null = null;
  try {
    return await Promise.race([
      promise,
      new Promise<T>((_resolve, reject) => {
        timer = setTimeout(() => {
          reject(new Error(`Backtest worker timeout exceeded for ${jobId}: ${timeoutMs}ms`));
        }, timeoutMs);
      }),
    ]);
  } finally {
    if (timer !== null) {
      clearTimeout(timer);
    }
  }
}

function assertBeforeDeadline(deadlineMs: number, jobId: string) {
  if (Date.now() > deadlineMs) {
    throw new Error(`Backtest worker timeout exceeded for ${jobId}: ${WORKER_TIMEOUT_MS}ms`);
  }
}

function cacheHitRateForManifest(manifest: CandleCacheManifest): number {
  const total = manifest.cache_hits + manifest.fetch_count;
  return total <= 0 ? 0 : Number((manifest.cache_hits / total).toFixed(4));
}

function rangeCacheHitRateForManifest(manifest: CandleCacheManifest): number {
  const total = manifest.range_cache_hits + manifest.range_cache_misses;
  return total <= 0 ? 0 : Number((manifest.range_cache_hits / total).toFixed(4));
}

function recordCacheResult(manifest: CandleCacheManifest, result: CandleCacheResult) {
  manifest.range_cache_hits += result.rangeCacheHits;
  manifest.range_cache_misses += result.rangeCacheMisses;
  manifest.missing_intervals_fetched += result.missingIntervalsFetched;
  manifest.segments_reused += result.segmentsReused;
  manifest.segments_created += result.segmentsCreated;
  manifest.bytes_read += result.bytesRead;
  manifest.bytes_written += result.bytesWritten;
}

function recordCandlesUsed(manifest: CandleCacheManifest, count: number) {
  if (manifest.candles_used + count > MAX_CANDLES_PER_JOB) {
    throw new Error(`Backtest candle use limit exceeded: ${MAX_CANDLES_PER_JOB}`);
  }
  manifest.candles_used += count;
}

function timestampMs(value: Date | string): number {
  const parsed = value instanceof Date ? value.getTime() : Date.parse(value);
  return Number.isFinite(parsed) ? parsed : Date.now();
}

function stableJson(value: unknown): string {
  if (value === undefined) {
    return "null";
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableJson(item)).join(",")}]`;
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableJson(record[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

async function markJobFailed(client: pg.Client, job: RunJobRow, errorCode: string, message: string) {
  const run = await getRun(client, job.run_id);
  if (job.attempts < job.max_attempts) {
    await requeueJob(client, job.id, { message, retrying: true, attempt: job.attempts, max_attempts: job.max_attempts }, errorCode);
    await setRunStatus(client, run, "queued");
    await appendEvent(client, run, "backtest.failed", {
      job_id: job.id,
      error_code: errorCode,
      message,
      retrying: true,
      attempt: job.attempts,
      max_attempts: job.max_attempts,
    });
    return;
  }
  await completeJob(client, job.id, "failed", { message }, errorCode);
  await setRunStatus(client, run, "failed", errorCode);
  await appendEvent(client, run, "backtest.failed", {
    job_id: job.id,
    error_code: errorCode,
    message,
    retrying: false,
    attempt: job.attempts,
    max_attempts: job.max_attempts,
  });
  await appendEvent(client, run, "run.failed", { error: errorCode, message, mode: "backtest-preview" });
}

async function getRun(client: pg.Client, runId: string): Promise<AssistantRunRow> {
  const result = await client.query<AssistantRunRow>("SELECT * FROM assistant_runs WHERE id = $1", [runId]);
  const run = result.rows[0];
  if (!run) {
    throw new Error(`Run not found: ${runId}`);
  }
  return run;
}

async function setRunStatus(client: pg.Client, run: AssistantRunRow, status: string, errorCode: string | null = null) {
  await client.query(
    `
    UPDATE assistant_runs
    SET status = $2::text,
        error_code = $3::text,
        started_at = CASE WHEN $2::text = 'running' AND started_at IS NULL THEN now() ELSE started_at END,
        completed_at = CASE WHEN $2::text IN ('completed', 'failed', 'blocked', 'cancelled') THEN now() ELSE completed_at END,
        updated_at = now()
    WHERE id = $1
    `,
    [run.id, status, errorCode],
  );
}

async function completeJob(
  client: pg.Client,
  jobId: string,
  status: "completed" | "failed" | "cancelled",
  resultJson: Record<string, unknown>,
  errorCode: string | null = null,
) {
  await client.query(
    `
    UPDATE run_jobs
    SET status = $2,
        result_json = $3,
        error_code = $4,
        lease_owner = NULL,
        leased_until = NULL,
        updated_at = now()
    WHERE id = $1
    `,
    [jobId, status, resultJson, errorCode],
  );
}

async function requeueJob(
  client: pg.Client,
  jobId: string,
  resultJson: Record<string, unknown>,
  errorCode: string | null = null,
) {
  await client.query(
    `
    UPDATE run_jobs
    SET status = 'queued',
        result_json = $2,
        error_code = $3,
        lease_owner = NULL,
        leased_until = NULL,
        updated_at = now()
    WHERE id = $1
    `,
    [jobId, resultJson, errorCode],
  );
}

async function startJobHeartbeat(jobId: string) {
  const heartbeatClient = new Client({ connectionString: databaseUrl() });
  await heartbeatClient.connect();
  let leaseLostError: StaleJobLeaseError | null = null;
  let renewing = false;
  let currentRenewal: Promise<void> | null = null;
  const renew = async () => {
    if (renewing || leaseLostError !== null) {
      return;
    }
    renewing = true;
    try {
      await renewJobLeaseOrThrow(heartbeatClient, jobId);
      console.log(
        JSON.stringify({
          level: "debug",
          message: "backtest job lease heartbeat renewed",
          job_id: jobId,
          worker_id: WORKER_ID,
          heartbeat_seconds: HEARTBEAT_SECONDS,
        }),
      );
    } catch (error) {
      leaseLostError =
        error instanceof StaleJobLeaseError
          ? error
          : new StaleJobLeaseError(`Backtest job lease heartbeat failed for ${jobId}: ${String(error)}`);
      console.warn(JSON.stringify({ level: "warn", message: leaseLostError.message, job_id: jobId }));
    } finally {
      renewing = false;
    }
  };
  const timer = setInterval(() => {
    if (currentRenewal === null) {
      currentRenewal = renew().finally(() => {
        currentRenewal = null;
      });
    }
  }, HEARTBEAT_SECONDS * 1000);
  return {
    assertLease() {
      if (leaseLostError !== null) {
        throw leaseLostError;
      }
    },
    async stop() {
      clearInterval(timer);
      if (currentRenewal !== null) {
        await currentRenewal;
      }
      await heartbeatClient.end();
    },
  };
}

async function renewJobLeaseOrThrow(client: pg.Client, jobId: string) {
  const result = await client.query(
    `
    UPDATE run_jobs
    SET leased_until = now() + ($2::int * interval '1 second'),
        updated_at = now()
    WHERE id = $1
      AND status = 'running'
      AND lease_owner = $3
      AND NOT EXISTS (
        SELECT 1
        FROM assistant_runs
        WHERE assistant_runs.id = run_jobs.run_id
          AND assistant_runs.status = ANY($4::text[])
      )
    `,
    [jobId, LEASE_SECONDS, WORKER_ID, [...TERMINAL_RUN_STATUSES]],
  );
  if (result.rowCount !== 1) {
    throw new StaleJobLeaseError(`Backtest job lease is no longer owned by this worker: ${jobId}`);
  }
}

function isTerminalRunStatus(status: string) {
  return TERMINAL_RUN_STATUSES.has(status);
}

async function persistArtifacts(client: pg.Client, run: AssistantRunRow, artifacts: Record<string, ArtifactSpec>) {
  const artifactEntries = Object.values(artifacts).map((artifact) => ({
    ...artifact,
    id: opaqueId("art"),
  }));
  if (artifactEntries.length === 0) {
    return;
  }
  await client.query("BEGIN");
  try {
    const artifactValues: unknown[] = [];
    const artifactPlaceholders = artifactEntries.map((artifact, index) => {
      const offset = index * 10;
      artifactValues.push(
        artifact.id,
        run.id,
        run.conversation_id,
        run.owner_user_id,
        run.workspace_id,
        artifact.kind,
        artifact.mime_type,
        artifact.display_name,
        artifact.storage_key,
        {
          source: "backtest-kit-worker",
          evidence_label: "Backtest Kit local preview evidence",
        },
      );
      return `($${offset + 1}, $${offset + 2}, $${offset + 3}, $${offset + 4}, $${offset + 5}, $${offset + 6}, $${offset + 7}, $${offset + 8}, $${offset + 9}, $${offset + 10}, now())`;
    });
    await client.query<{ id: string; kind: string; display_name: string }>(
      `
      INSERT INTO artifacts (
        id, run_id, conversation_id, owner_user_id, workspace_id, kind, mime_type,
        display_name, storage_key, metadata_json, created_at
      )
      VALUES ${artifactPlaceholders.join(", ")}
      RETURNING id, kind, display_name
      `,
      artifactValues,
    );
    await client.query("SELECT id FROM assistant_runs WHERE id = $1 FOR UPDATE", [run.id]);
    const sequenceResult = await client.query<{ last_sequence: number | null }>(
      "SELECT max(sequence) AS last_sequence FROM run_events WHERE run_id = $1",
      [run.id],
    );
    const lastSequence = Number(sequenceResult.rows[0]?.last_sequence ?? 0);
    const eventValues: unknown[] = [];
    const eventPlaceholders = artifactEntries.map((artifact, index) => {
      const offset = index * 8;
      eventValues.push(
        opaqueId("evt"),
        run.id,
        run.conversation_id,
        run.owner_user_id,
        run.workspace_id,
        lastSequence + index + 1,
        "artifact.created",
        {
          artifact_id: artifact.id,
          kind: artifact.kind,
          display_name: artifact.display_name,
        },
      );
      return `($${offset + 1}::text, $${offset + 2}::text, $${offset + 3}::text, $${offset + 4}::text, $${offset + 5}::text, $${offset + 6}::integer, $${offset + 7}::text, $${offset + 8}::json, now())`;
    });
    await client.query(
      `
      INSERT INTO run_events (
        id, run_id, conversation_id, owner_user_id, workspace_id, sequence, type, payload_json, created_at
      )
      VALUES ${eventPlaceholders.join(", ")}
      `,
      eventValues,
    );
    await client.query("COMMIT");
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  }
}

async function appendEvent(client: pg.Client, run: AssistantRunRow, type: string, payload: Record<string, unknown>) {
  await client.query(
    `
    INSERT INTO run_events (
      id, run_id, conversation_id, owner_user_id, workspace_id, sequence, type, payload_json, created_at
    )
    VALUES (
      $1::text, $2::text, $3::text, $4::text, $5::text,
      COALESCE((SELECT max(sequence) + 1 FROM run_events WHERE run_id = $2::text), 1),
      $6::text, $7::json, now()
    )
    `,
    [opaqueId("evt"), run.id, run.conversation_id, run.owner_user_id, run.workspace_id, type, payload],
  );
}

function opaqueId(prefix: string) {
  return `${prefix}_${randomUUID().replaceAll("-", "")}`;
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export const __test = {
  buildStrategyAdapter,
  buildStrategyRuntime,
  candleCacheLockKey,
  candleCacheStorageKey,
  emaSeries,
  evaluateStrategyLogic,
  getOrFillPublicCandleCache,
  getOrFillPublicCandleCacheV1,
  getOrFillPublicCandleCacheV2,
  readRangeCacheCoverage,
  rangeCacheDataset,
  requestedRange,
  upsertRangeCacheSegment,
  parseStrategyLogic,
  readCachedPublicCandles,
  rsiSeries,
  writeRangeCacheSegment,
  writeCachedPublicCandles,
};

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error: unknown) => {
    const message = error instanceof Error ? error.stack ?? error.message : String(error);
    console.error(JSON.stringify({ level: "error", message }));
    process.exit(1);
  });
}
