import { createHash, randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createWriteStream } from "node:fs";
import { mkdir, mkdtemp, open, readFile, rename, rm, stat, writeFile } from "node:fs/promises";
import { hostname } from "node:os";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import ccxt from "ccxt";
import pg from "pg";
import {
  BACKTEST_EXECUTABLE_TIMEFRAMES,
  BACKTEST_MAX_COST_BPS,
  BACKTEST_OHLCV_DEFAULT_EXCHANGE,
  BACKTEST_OHLCV_EXCHANGES,
  BACKTEST_OHLCV_PROVIDER,
  BACKTEST_RUN_EVENTS,
} from "./backtest-ohlcv-contract.js";

const { Client } = pg;

const PINEFORGE_ENGINE_PACKAGE = "pineforge-engine";
const PINEFORGE_ENGINE_VERSION = "poc-local";
const PINEFORGE_RUNNER_CACHE_VERSION = process.env.PINEFORGE_RUNNER_VERSION ?? "pineforge-runner-native-contract-v1";
const PINEFORGE_EVIDENCE_LABEL = "Local sandbox preview evidence";
const BACKTEST_EXECUTABLE_TIMEFRAME_SET = new Set<string>(BACKTEST_EXECUTABLE_TIMEFRAMES);
const BACKTEST_EXECUTION_CANDLE_TIMEFRAMES = new Set(["1m"]);
const BACKTEST_COST_MODEL_VERSION = "fixed_bps_v1";
const BACKTEST_DASHBOARD_EQUITY_POINTS = 200;
const BACKTEST_INLINE_EQUITY_POINTS = 80;
const BACKTEST_DASHBOARD_SCATTER_POINTS = 200;
const BACKTEST_DASHBOARD_LOG_ROWS = 80;
const BACKTEST_PREVIEW_HEARTBEAT_EVENT = "backtest.preview.heartbeat";
const PREVIEW_COMPATIBILITY_REPAIR_JOB_TYPE = "preview-compatibility-repair";
const PREVIEW_COMPATIBILITY_REPAIR_MAX_ATTEMPTS = 2;
type BacktestExchange = typeof BACKTEST_OHLCV_EXCHANGES[number];

type BacktestConfig = {
  engine: "pineforge";
  exchange?: BacktestExchange;
  symbol: string;
  timeframe: string;
  candle_timeframe?: string;
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
  pine_code?: string;
  backtest_config: BacktestConfig;
  runtime: {
    engine: "pineforge";
    allowed_api: string[];
    blocked_api: string[];
  };
  limits?: {
    workspace_active_limit?: number;
    max_variants?: number;
  };
  auto_chain?: {
    summary_on_complete?: boolean;
    source_run_id?: string;
    conversation_id?: string;
  } | null;
  compatibility_repair?: {
    attempt?: number;
    max_attempts?: number;
    source?: string;
    source_run_id?: string;
    failed_run_id?: string;
    failed_job_id?: string;
  } | null;
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
  exchange: BacktestExchange;
  symbol: string;
  exchange_symbol: string;
  timeframe: string;
  signal_timeframe?: string;
  candle_timeframe?: string;
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
  validated_at?: string;
  expected_step_ms?: number;
  gap_count?: number;
  first_timestamp?: string;
  last_timestamp?: string;
};

type RangeCacheIndex = {
  cache_version: "range-v2";
  source: typeof BACKTEST_OHLCV_PROVIDER;
  exchange: BacktestExchange;
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
  fetchWindowsTotal?: number;
  fetchWindowsCompleted?: number;
  fetchRetryCount?: number;
  fetchDurationMs?: number;
};

type RangeCoverageResult = {
  complete: boolean;
  candles: Candle[];
  missingIntervals: Array<{ startMs: number; endMs: number }>;
  segmentsReused: number;
  bytesRead: number;
};

type FetchWindow = { startMs: number; endMs: number };

type BacktestProgressCallback = (type: string, payload: Record<string, unknown>) => Promise<void>;
type BacktestHeartbeatStage =
  | "planning"
  | "cache"
  | "fetching"
  | "exporting"
  | "executing"
  | "indexing"
  | "reporting"
  | "completed"
  | "failed";
type BacktestHeartbeatStatus = "queued" | "running" | "completed" | "failed";

class BacktestProgressEmitter {
  private pendingFetchingPayload: Record<string, unknown> | null = null;
  private coalescedFetchingUpdates = 0;
  private lastFetchingEmitAt = 0;
  private lastFetchingPercent = -Infinity;
  private readonly startedAt = Date.now();

  constructor(
    private readonly client: pg.Client,
    private readonly run: AssistantRunRow,
    private readonly jobId: string,
    private readonly append: (
      client: pg.Client,
      run: AssistantRunRow,
      type: string,
      payload: Record<string, unknown>,
    ) => Promise<void> = appendEvent,
  ) {}

  async progress(type: string, payload: Record<string, unknown>) {
    if (type !== BACKTEST_RUN_EVENTS.dataFetching) {
      await this.flush();
      const enriched = { job_id: this.jobId, ...payload };
      await this.append(this.client, this.run, type, enriched);
      await this.appendHeartbeat(type, enriched);
      return;
    }
    if (this.shouldEmitFetching(payload)) {
      await this.emitFetching(payload);
      return;
    }
    this.pendingFetchingPayload = payload;
    this.coalescedFetchingUpdates += 1;
  }

  async flush() {
    if (this.pendingFetchingPayload === null) {
      return;
    }
    await this.emitFetching(this.pendingFetchingPayload);
  }

  private shouldEmitFetching(payload: Record<string, unknown>) {
    const total = numberPayload(payload.fetch_windows_total);
    const completed = numberPayload(payload.fetch_windows_completed);
    if (total === null || completed === null || total <= 0) {
      return true;
    }
    if (completed <= 0 || completed >= total) {
      return true;
    }
    const now = Date.now();
    const percent = (completed / total) * 100;
    return (
      percent - this.lastFetchingPercent >= FETCH_PROGRESS_MIN_PERCENT_STEP ||
      now - this.lastFetchingEmitAt >= FETCH_PROGRESS_MIN_INTERVAL_MS
    );
  }

  private async emitFetching(payload: Record<string, unknown>) {
    const completed = numberPayload(payload.fetch_windows_completed);
    const total = numberPayload(payload.fetch_windows_total);
    const enriched: Record<string, unknown> = { job_id: this.jobId, ...payload };
    if (this.coalescedFetchingUpdates > 0) {
      enriched.coalesced_updates = this.coalescedFetchingUpdates;
    }
    if (completed !== null) {
      enriched.last_fetch_window_completed_at = new Date().toISOString();
    }
    await this.append(this.client, this.run, BACKTEST_RUN_EVENTS.dataFetching, enriched);
    await this.appendHeartbeat(BACKTEST_RUN_EVENTS.dataFetching, enriched);
    this.pendingFetchingPayload = null;
    this.coalescedFetchingUpdates = 0;
    this.lastFetchingEmitAt = Date.now();
    if (total !== null && completed !== null && total > 0) {
      this.lastFetchingPercent = (completed / total) * 100;
    }
  }

  private async appendHeartbeat(type: string, payload: Record<string, unknown>) {
    const heartbeat = heartbeatFromProgressEvent(type, payload, this.startedAt);
    if (!heartbeat) {
      return;
    }
    await this.append(this.client, this.run, BACKTEST_PREVIEW_HEARTBEAT_EVENT, heartbeat);
  }
}

function heartbeatFromProgressEvent(
  type: string,
  payload: Record<string, unknown>,
  startedAt: number,
): Record<string, unknown> | null {
  const elapsedMs = Math.max(0, Date.now() - startedAt);
  const base: Record<string, unknown> = {
    job_id: payload.job_id,
    status: "running" satisfies BacktestHeartbeatStatus,
    elapsed_ms: elapsedMs,
    eta_ms: null,
    updated_at: new Date().toISOString(),
  };
  if (type === BACKTEST_RUN_EVENTS.dataPlanning) {
    return { ...base, stage: "planning" satisfies BacktestHeartbeatStage, progress_pct: 5, message: "Planning candle coverage and cache usage." };
  }
  if (type === BACKTEST_RUN_EVENTS.dataCacheReusing) {
    return { ...base, stage: "cache" satisfies BacktestHeartbeatStage, progress_pct: 18, message: "Reusing cached public OHLCV candles." };
  }
  if (type === BACKTEST_RUN_EVENTS.dataFetching) {
    const total = numberPayload(payload.fetch_windows_total);
    const completed = numberPayload(payload.fetch_windows_completed);
    const ratio = total !== null && completed !== null && total > 0 ? Math.max(0, Math.min(1, completed / total)) : 0;
    const etaMs = ratio > 0 && ratio < 1 ? Math.max(0, Math.round((elapsedMs / ratio) - elapsedMs)) : null;
    return {
      ...base,
      stage: "fetching" satisfies BacktestHeartbeatStage,
      progress_pct: Math.round(25 + ratio * 30),
      eta_ms: etaMs,
      message: "Fetching missing public OHLCV candles.",
      fetch_windows_completed: completed,
      fetch_windows_total: total,
    };
  }
  if (type === BACKTEST_RUN_EVENTS.dataExporting) {
    return { ...base, stage: "exporting" satisfies BacktestHeartbeatStage, progress_pct: 62, message: "Exporting candles for the local preview engine." };
  }
  if (type === BACKTEST_RUN_EVENTS.executionStarted) {
    return { ...base, stage: "executing" satisfies BacktestHeartbeatStage, progress_pct: 72, message: "Running the local preview engine." };
  }
  if (type === BACKTEST_RUN_EVENTS.indexingStarted) {
    return { ...base, stage: "indexing" satisfies BacktestHeartbeatStage, progress_pct: 88, message: "Indexing preview results and artifacts." };
  }
  return null;
}

async function appendBacktestHeartbeat(
  client: pg.Client,
  run: AssistantRunRow,
  payload: {
    job_id: string;
    stage: BacktestHeartbeatStage;
    status: BacktestHeartbeatStatus;
    progress_pct: number;
    elapsed_ms?: number;
    eta_ms?: number | null;
    message: string;
    fetch_windows_completed?: number;
    fetch_windows_total?: number;
  },
) {
  await appendEvent(client, run, BACKTEST_PREVIEW_HEARTBEAT_EVENT, {
    ...payload,
    elapsed_ms: payload.elapsed_ms ?? 0,
    eta_ms: payload.eta_ms ?? null,
    updated_at: new Date().toISOString(),
  });
}

type CandleCacheManifest = {
  source: typeof BACKTEST_OHLCV_PROVIDER;
  exchange: BacktestExchange;
  symbol: string;
  exchange_symbol: string;
  timeframe: string;
  signal_timeframe?: string;
  candle_timeframe?: string;
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
  preloaded_candles: number;
  get_candles_calls: number;
  preloaded_slice_hits: number;
  fallback_fetches: number;
  signal_evaluations: number;
  signal_candles: number;
  signal_aggregate_ms: number;
  indicator_precompute_ms: number;
  backtest_run_ms: number;
  total_frames: number;
  processed_frames: number;
  frames_per_second: number;
  progress_events: number;
  backtest_events: number;
  closed_events: number;
  idle_events: number;
  active_events: number;
  get_signal_calls: number;
  read_only: true;
  fetches: CandleFetchRecord[];
  ohlcv_quality?: OhlcvQualityReport;
  market_data_source?: MarketDataSourceMetadata;
  source_feed_checksum?: string;
  gap_ranges?: OhlcvGapRange[];
  missing_bar_ratio?: number;
  csv_export_ms?: number;
  cache_validation_ms?: number;
  runner_input_bars?: number;
  fetch_windows_total?: number;
  fetch_windows_completed?: number;
  fetch_retry_count?: number;
  fetch_duration_ms?: number;
  artifact_persist_ms?: number;
  db_index_ms?: number;
  raw_artifact_bytes?: number;
  db_index_rows?: number;
  pine_compile_cache_hit?: boolean;
  pine_code_hash?: string;
};

type OhlcvGapRange = {
  start: string;
  end: string;
  missing_bars: number;
};

type OhlcvQualityReport = {
  status: "pass" | "warn";
  timeframe: string;
  expected_step_ms: number;
  expected_bars: number;
  actual_bars: number;
  missing_bars: number;
  duplicate_count: number;
  gap_count: number;
  missing_bar_ratio: number;
  gap_ranges: OhlcvGapRange[];
  first_timestamp: string | null;
  last_timestamp: string | null;
  checksum: string;
  validated_at: string;
  warnings: string[];
};

type MarketDataSourceMetadata = {
  source: typeof BACKTEST_OHLCV_PROVIDER;
  exchange: BacktestExchange;
  symbol: string;
  exchange_symbol: string;
  timeframe: string;
  start: string;
  end: string;
  checksum: string;
  bars: number;
  market?: Record<string, unknown> | null;
};

type BacktestRuntimeDiagnostics = {
  total_frames: number;
  processed_frames: number;
  frames_per_second: number;
  progress_events: number;
  backtest_events: number;
  closed_events: number;
  idle_events: number;
  active_events: number;
  get_candles_calls: number;
  get_signal_calls: number;
  signal_evaluations: number;
  backtest_run_ms: number;
};

type PreviewErrorCode =
  | "preview_compatibility_limit"
  | "preview_runtime_unavailable"
  | "preview_data_error"
  | "preview_execution_error";

type PreviewFailurePublicPayload = {
  preview_error_code: PreviewErrorCode;
  repair_attempts: number;
  compatibility_repair_applied: boolean;
  manual_validation_required: boolean;
  message: string;
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
  entry_bar_index: number | null;
  exit_bar_index: number | null;
  duration_bars: number | null;
  entry_price: number | null;
  exit_price: number | null;
  raw_pnl_percentage: number | null;
  raw_pnl_cost: number | null;
  pnl_percentage: number | null;
  pnl_cost: number | null;
  cost: number | null;
  fee_cost: number;
  slippage_cost: number;
  cost_model: CostModel;
  qty?: number | null;
  commission?: number | null;
  max_runup?: number | null;
  max_drawdown?: number | null;
};

type RawBacktestTrade = Record<string, unknown>;

type RawEquityPoint = Record<string, unknown>;

type CostModel = {
  version: typeof BACKTEST_COST_MODEL_VERSION;
  fee_bps: number;
  slippage_bps: number;
  applied_to_metrics: boolean;
  basis: "round_trip_notional";
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

type RobustnessCheckStatus = "pass" | "warn" | "fail" | "not_available";

type RobustnessReport = {
  status: "pass" | "warn" | "fail";
  checks: Record<string, { status: RobustnessCheckStatus; message: string }>;
  warnings: string[];
  metrics: {
    sample_size: number;
    backtest_days: number;
    max_drawdown_pct: number;
    fee_bps: number;
    slippage_bps: number;
    max_loss_streak: number;
    oos_split_available: boolean;
    parameter_sensitivity_available: boolean;
    suspicious_metric_flags: string[];
  };
};

type PromotionDecision = {
  decision: "reject" | "manual_review" | "research_candidate";
  reasons: string[];
  boundary: string;
};

type StrategyAdapter = {
  strategy_name: string;
  exchange_name: string;
  frame_name: string;
  timeframe: string;
  candle_timeframe: string;
  position: "long" | "short";
  percent_take_profit: number;
  percent_stop_loss: number;
  cost: number;
  minute_estimated_time: number;
};

type IndexedCandleStore = {
  candles: Candle[];
  indexByTimestamp: Map<number, number>;
};

type PrecomputedSignalRuntime = {
  candles: Candle[];
  timestamps: number[];
  series: Record<string, Array<number | null>>;
  aggregateMs: number;
  indicatorMs: number;
};

type BacktestExecutionResult = {
  adapter: StrategyAdapter;
  executionSemantics: "model_generated_pine_pineforge";
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
  metadata_json?: Record<string, unknown>;
};

const JOB_TYPE = "backtest-preview";
const CHAT_BACKTEST_SUMMARY_JOB_TYPE = "chat-backtest-summary";
const WORKER_ID = process.env.BACKTEST_WORKER_ID?.trim() || `backtest-worker-${hostname()}-${process.pid}`;
const POLL_INTERVAL_MS = readIntegerEnv("BACKTEST_WORKER_POLL_INTERVAL_MS", 2000, { min: 1 });
const LEASE_SECONDS = readIntegerEnv("BACKTEST_WORKER_LEASE_SECONDS", 120, { min: 1 });
const HEARTBEAT_SECONDS = Math.max(
  1,
  Math.min(
    readIntegerEnv("BACKTEST_WORKER_HEARTBEAT_SECONDS", 30, { min: 1 }),
    Math.floor(LEASE_SECONDS / 2),
  ),
);
const ARTIFACT_ROOT = process.env.STRATEGY_CODEBOT_API_ARTIFACT_ROOT ?? "/var/lib/strategy-codebot/artifacts";
const MAX_CANDLES_PER_JOB = readIntegerEnv("BACKTEST_WORKER_MAX_CANDLES", 1_578_240, { min: 1 });
const MAX_CANDLES_PER_FETCH = readIntegerEnv("BACKTEST_WORKER_MAX_CANDLES_PER_FETCH", 1000, { min: 1 });
const MAX_BACKTEST_EVENTS = readIntegerEnv("BACKTEST_WORKER_MAX_EVENTS", 10000, { min: 1 });
const MAX_ARTIFACT_BYTES = readIntegerEnv("BACKTEST_WORKER_MAX_ARTIFACT_BYTES", 200_000_000, { min: 1 });
const WORKER_TIMEOUT_MS = readIntegerEnv("BACKTEST_WORKER_TIMEOUT_MS", 600_000, { min: 1 });
const BACKTEST_CHILD_STDIO_LIMIT_BYTES = readIntegerEnv("BACKTEST_WORKER_CHILD_STDIO_LIMIT_BYTES", 20_000_000, { min: 1 });
const PINEFORGE_ENABLED = process.env.BACKTEST_PINEFORGE_ENABLED === "1";
const PINEFORGE_RUNNER_URL = process.env.BACKTEST_PINEFORGE_RUNNER_URL?.replace(/\/+$/, "");
const PINEFORGE_RUNNER_TIMEOUT_MS = readIntegerEnv("BACKTEST_PINEFORGE_RUNNER_TIMEOUT_MS", WORKER_TIMEOUT_MS, { min: 1 });
const PINEFORGE_MAX_BARS = readIntegerEnv("BACKTEST_PINEFORGE_MAX_BARS", MAX_CANDLES_PER_JOB, { min: 1 });
const PINEFORGE_MAX_OUTPUT_BYTES = readIntegerEnv("BACKTEST_PINEFORGE_MAX_OUTPUT_BYTES", 50_000_000, { min: 1 });
const PINEFORGE_EQUITY_DOWNSAMPLE_POINTS = readIntegerEnv("BACKTEST_PINEFORGE_EQUITY_DOWNSAMPLE_POINTS", 5000, { min: 1 });
const PINEFORGE_COMMAND = process.env.BACKTEST_PINEFORGE_COMMAND?.trim();
const PINEFORGE_ARGS = (process.env.BACKTEST_PINEFORGE_ARGS ?? "")
  .split(/\s+/)
  .map((part) => part.trim())
  .filter(Boolean);
const DATA_FETCH_THROTTLE_MS = readIntegerEnv("BACKTEST_WORKER_DATA_FETCH_THROTTLE_MS", 250, { min: 0 });
const DATA_FETCH_CONCURRENCY = readIntegerEnv("BACKTEST_WORKER_DATA_FETCH_CONCURRENCY", 2, { min: 1 });
const FETCH_PROGRESS_MIN_PERCENT_STEP = readNumberEnv("BACKTEST_WORKER_FETCH_PROGRESS_MIN_PERCENT_STEP", 5, { min: 0.1, max: 100 });
const FETCH_PROGRESS_MIN_INTERVAL_MS = readIntegerEnv("BACKTEST_WORKER_FETCH_PROGRESS_MIN_INTERVAL_MS", 5000, { min: 0 });
const GLOBAL_FETCH_ACTIVE_LIMIT = readIntegerEnv("BACKTEST_WORKER_GLOBAL_FETCH_ACTIVE_LIMIT", DATA_FETCH_CONCURRENCY, { min: 1 });
const DATA_FETCH_RETRY_ATTEMPTS = readIntegerEnv("BACKTEST_WORKER_DATA_FETCH_RETRY_ATTEMPTS", 3, { min: 1 });
const DATA_FETCH_RETRY_BASE_MS = readIntegerEnv("BACKTEST_WORKER_DATA_FETCH_RETRY_BASE_MS", 500, { min: 0 });
const DATA_FETCH_THROTTLE_TTL_MS = readIntegerEnv("BACKTEST_WORKER_DATA_FETCH_THROTTLE_TTL_MS", 900_000, { min: 0 });
const DATA_FETCH_THROTTLE_MAX_KEYS = readIntegerEnv("BACKTEST_WORKER_DATA_FETCH_THROTTLE_MAX_KEYS", 10_000, { min: 1 });
const OHLCV_GAP_WARN_RATIO = readNumberEnv("BACKTEST_WORKER_OHLCV_GAP_WARN_RATIO", 0.001, { min: 0 });
const DEFAULT_WORKSPACE_ACTIVE_LIMIT = readIntegerEnv("BACKTEST_WORKER_DEFAULT_WORKSPACE_ACTIVE_LIMIT", 2, { min: 1 });
const CACHE_LOCK_TIMEOUT_MS = readIntegerEnv("BACKTEST_WORKER_CACHE_LOCK_TIMEOUT_MS", 30_000, { min: 1 });
const MARKET_DATA_MODE = process.env.BACKTEST_WORKER_MARKET_DATA_MODE ?? "ccxt";
const CANDLE_CACHE_VERSION = process.env.BACKTEST_WORKER_CANDLE_CACHE_VERSION === "chunk-v1" ? "chunk-v1" : "range-v2";
const CACHE_SEGMENT_POLICY = process.env.BACKTEST_WORKER_CACHE_SEGMENT_POLICY === "legacy-merge" ? "legacy-merge" : "monthly";
const CACHE_SEGMENT_TARGET_BYTES = readIntegerEnv("BACKTEST_WORKER_CACHE_SEGMENT_TARGET_BYTES", 60_000_000, { min: 1 });
const LONG_RANGE_ACTIVE_LIMIT = readIntegerEnv("BACKTEST_WORKER_LONG_RANGE_ACTIVE_LIMIT", 1, { min: 1 });
const ALLOWED_EXCHANGES = parseAllowedExchanges(process.env.BACKTEST_WORKER_ALLOWED_EXCHANGES);
const DEFAULT_EXCHANGE = parseDefaultExchange(process.env.BACKTEST_WORKER_DEFAULT_EXCHANGE, ALLOWED_EXCHANGES);
const TERMINAL_RUN_STATUSES = new Set(["completed", "failed", "blocked", "cancelled"]);
const lastFetchAtByThrottleKey = new Map<string, number>();
const backtestRuntimeDiagnosticsByJobId = new Map<string, BacktestRuntimeDiagnostics>();
let activePublicFetches = 0;
let lastDataFetchThrottlePruneAt = 0;

class StaleJobLeaseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "StaleJobLeaseError";
  }
}

class BacktestWorkerTimeoutError extends Error {
  diagnostics?: BacktestRuntimeDiagnostics;

  constructor(message: string, diagnostics?: BacktestRuntimeDiagnostics) {
    super(message);
    this.name = "BacktestWorkerTimeoutError";
    this.diagnostics = diagnostics;
  }
}

class LocalPreviewError extends Error {
  previewErrorCode: PreviewErrorCode;
  rawCode?: string;
  rawMessage?: string;
  rawDiagnostics?: Record<string, unknown>;
  compileStage?: string | null;

  constructor(
    previewErrorCode: PreviewErrorCode,
    message: string,
    options: {
      rawCode?: string;
      rawMessage?: string;
      rawDiagnostics?: Record<string, unknown>;
      compileStage?: string | null;
    } = {},
  ) {
    super(message);
    this.name = "LocalPreviewError";
    this.previewErrorCode = previewErrorCode;
    this.rawCode = options.rawCode;
    this.rawMessage = options.rawMessage;
    this.rawDiagnostics = options.rawDiagnostics;
    this.compileStage = options.compileStage ?? null;
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
      console.error(
        JSON.stringify({
          level: "error",
          message,
          stack: error instanceof Error ? error.stack : undefined,
          job_id: job.id,
        }),
      );
      await markJobFailed(client, job, classifyBacktestWorkerError(error), message, backtestWorkerErrorDiagnostics(error), error);
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

function readIntegerEnv(name: string, defaultValue: number, options: { min?: number; max?: number } = {}) {
  const value = readNumberEnv(name, defaultValue, options);
  if (!Number.isInteger(value)) {
    throw new Error(`${name} must be an integer, got ${process.env[name]}`);
  }
  return value;
}

function readNumberEnv(name: string, defaultValue: number, options: { min?: number; max?: number } = {}) {
  const raw = process.env[name];
  if (raw === undefined || raw.trim() === "") {
    return defaultValue;
  }
  const value = Number(raw);
  if (!Number.isFinite(value)) {
    throw new Error(`${name} must be a finite number, got ${raw}`);
  }
  if (options.min !== undefined && value < options.min) {
    throw new Error(`${name} must be >= ${options.min}, got ${raw}`);
  }
  if (options.max !== undefined && value > options.max) {
    throw new Error(`${name} must be <= ${options.max}, got ${raw}`);
  }
  return value;
}

function numberPayload(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
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
        AND (
          NOT (
            COALESCE(
              CASE
                WHEN (job.payload_json #>> '{backtest_config,start}') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T'
                 AND (job.payload_json #>> '{backtest_config,end}') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T'
                THEN (job.payload_json #>> '{backtest_config,end}')::timestamptz - (job.payload_json #>> '{backtest_config,start}')::timestamptz
                ELSE NULL
              END,
              interval '0 seconds'
            )
              > interval '365 days'
          )
          OR (
            SELECT count(*)
            FROM run_jobs AS active_long
            WHERE active_long.job_type = job.job_type
              AND active_long.workspace_id = job.workspace_id
              AND active_long.status = 'running'
              AND active_long.leased_until >= now()
              AND (
                COALESCE(
                  CASE
                    WHEN (active_long.payload_json #>> '{backtest_config,start}') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T'
                     AND (active_long.payload_json #>> '{backtest_config,end}') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T'
                    THEN (active_long.payload_json #>> '{backtest_config,end}')::timestamptz - (active_long.payload_json #>> '{backtest_config,start}')::timestamptz
                    ELSE NULL
                  END,
                  interval '0 seconds'
                )
                  > interval '365 days'
              )
          ) < $5
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
    [JOB_TYPE, WORKER_ID, LEASE_SECONDS, DEFAULT_WORKSPACE_ACTIVE_LIMIT, LONG_RANGE_ACTIVE_LIMIT],
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
  await appendEvent(client, run, BACKTEST_RUN_EVENTS.dataStarted, {
    job_id: job.id,
    data_source: job.payload_json.backtest_config.data_source,
    exchange: job.payload_json.backtest_config.exchange ?? DEFAULT_EXCHANGE,
    symbol: job.payload_json.backtest_config.symbol,
    timeframe: job.payload_json.backtest_config.candle_timeframe ?? "1m",
    signal_timeframe: job.payload_json.backtest_config.timeframe,
    candle_timeframe: job.payload_json.backtest_config.candle_timeframe ?? "1m",
    job_wait_ms: jobWaitMs,
    range_class: rangeClassForConfig(job.payload_json.backtest_config),
  });
  await appendBacktestHeartbeat(client, run, {
    job_id: job.id,
    stage: "planning",
    status: "running",
    progress_pct: 2,
    elapsed_ms: Math.max(0, Date.now() - processStartedAt),
    message: "Backtest worker picked up the local preview job.",
  });
  const progressEmitter = new BacktestProgressEmitter(client, run, job.id);
  const progress: BacktestProgressCallback = progressEmitter.progress.bind(progressEmitter);
  await progress(BACKTEST_RUN_EVENTS.dataPlanning, {
    exchange: job.payload_json.backtest_config.exchange ?? DEFAULT_EXCHANGE,
    symbol: job.payload_json.backtest_config.symbol,
    timeframe: job.payload_json.backtest_config.candle_timeframe ?? "1m",
    range_class: rangeClassForConfig(job.payload_json.backtest_config),
  });
  const heartbeat = await startJobHeartbeat(job.id);
  let execution: BacktestExecutionResult & { metrics: BacktestMetrics };
  const executionStartedAt = Date.now();
  try {
    execution = await withTimeout(
      runBacktestExecution(job, executionStartedAt + WORKER_TIMEOUT_MS, progress),
      WORKER_TIMEOUT_MS,
      job.id,
    );
  } finally {
    await progressEmitter.flush();
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
  const artifactPersistStartedAt = Date.now();
  const artifacts = await writeBacktestArtifacts(job, execution);
  const artifactPersistMs = Date.now() - artifactPersistStartedAt;
  const rawArtifactBytes = artifactBytesTotal(artifacts);
  execution.manifest.artifact_persist_ms = artifactPersistMs;
  execution.manifest.raw_artifact_bytes = rawArtifactBytes;
  const reportGenerationMs = artifactPersistMs;
  const cacheHitRate = cacheHitRateForManifest(execution.manifest);
  const evidenceLabel = String(execution.report.evidence_label ?? PINEFORGE_EVIDENCE_LABEL);

  await appendEvent(client, run, BACKTEST_RUN_EVENTS.dataCompleted, {
    job_id: job.id,
    cache_manifest: artifacts.cacheManifest.storage_key,
    exchange: execution.manifest.exchange,
    exchange_symbol: execution.manifest.exchange_symbol,
    timeframe: execution.manifest.timeframe,
    signal_timeframe: execution.manifest.signal_timeframe,
    candle_timeframe: execution.manifest.candle_timeframe ?? execution.manifest.timeframe,
    candles_fetched: execution.manifest.candles_fetched,
    candles_used: execution.manifest.candles_used,
    cache_hits: execution.manifest.cache_hits,
    fetch_count: execution.manifest.fetch_count,
    cache_hit_rate: cacheHitRate,
    cache_version: execution.manifest.cache_version,
    range_cache_hit_rate: rangeCacheHitRateForManifest(execution.manifest),
    segments_reused: execution.manifest.segments_reused,
    segments_created: execution.manifest.segments_created,
    ohlcv_quality: execution.manifest.ohlcv_quality,
    gap_count: execution.manifest.ohlcv_quality?.gap_count ?? 0,
    missing_bar_ratio: execution.manifest.missing_bar_ratio ?? 0,
    source_feed_checksum: execution.manifest.source_feed_checksum,
    total_frames: execution.manifest.total_frames,
    processed_frames: execution.manifest.processed_frames,
    frames_per_second: execution.manifest.frames_per_second,
    progress_events: execution.manifest.progress_events,
    fetch_windows_total: execution.manifest.fetch_windows_total,
    fetch_windows_completed: execution.manifest.fetch_windows_completed,
    fetch_retry_count: execution.manifest.fetch_retry_count,
    fetch_duration_ms: execution.manifest.fetch_duration_ms,
  });
  await appendEvent(client, run, BACKTEST_RUN_EVENTS.executionCompleted, {
    job_id: job.id,
    status: "completed",
    trade_count: execution.metrics.trade_count,
    pnl: execution.metrics.pnl,
    max_drawdown: execution.metrics.max_drawdown,
    execution_duration_ms: executionDurationMs,
    backtest_run_ms: execution.manifest.backtest_run_ms,
    backtest_events: execution.manifest.backtest_events,
    closed_events: execution.manifest.closed_events,
    idle_events: execution.manifest.idle_events,
    active_events: execution.manifest.active_events,
    get_signal_calls: execution.manifest.get_signal_calls,
    get_candles_calls: execution.manifest.get_candles_calls,
    signal_evaluations: execution.manifest.signal_evaluations,
    frames_per_second: execution.manifest.frames_per_second,
  });
  await appendEvent(client, run, BACKTEST_RUN_EVENTS.indexingStarted, {
    job_id: job.id,
    artifact_count: Object.keys(artifacts).length,
  });
  await appendBacktestHeartbeat(client, run, {
    job_id: job.id,
    stage: "indexing",
    status: "running",
    progress_pct: 88,
    elapsed_ms: Math.max(0, Date.now() - processStartedAt),
    message: "Indexing preview results and artifacts.",
  });
  const dbIndexStartedAt = Date.now();
  await persistArtifacts(client, run, artifacts, evidenceLabel);
  const dbIndexRows = await persistBacktestIndexes(client, run, job, execution, artifacts);
  const dbIndexMs = Date.now() - dbIndexStartedAt;
  execution.manifest.db_index_ms = dbIndexMs;
  execution.manifest.db_index_rows = dbIndexRows;
  artifacts.cacheManifest.content = execution.manifest;
  await writeArtifactFile(artifacts.cacheManifest);
  await appendBacktestHeartbeat(client, run, {
    job_id: job.id,
    stage: "reporting",
    status: "running",
    progress_pct: 96,
    elapsed_ms: Math.max(0, Date.now() - processStartedAt),
    message: "Building dashboard and review artifacts.",
  });
  await appendEvent(client, run, BACKTEST_RUN_EVENTS.reportCompleted, {
    job_id: job.id,
    report_artifact: artifacts.report.storage_key,
    evidence_label: evidenceLabel,
    signal_timeframe: execution.manifest.signal_timeframe,
    candle_timeframe: execution.manifest.candle_timeframe ?? execution.manifest.timeframe,
    report_generation_ms: reportGenerationMs,
    artifact_persist_ms: artifactPersistMs,
    db_index_ms: dbIndexMs,
    raw_artifact_bytes: rawArtifactBytes,
    db_index_rows: dbIndexRows,
  });
  await appendBacktestHeartbeat(client, run, {
    job_id: job.id,
    stage: "completed",
    status: "completed",
    progress_pct: 100,
    elapsed_ms: Math.max(0, Date.now() - processStartedAt),
    message: "Backtest preview artifacts are ready.",
    fetch_windows_total: execution.manifest.fetch_windows_total,
    fetch_windows_completed: execution.manifest.fetch_windows_completed,
  });
  await completeJob(client, job.id, "completed", {
    report_storage_key: artifacts.report.storage_key,
    trades_storage_key: artifacts.trades.storage_key,
    equity_curve_storage_key: artifacts.equityCurve.storage_key,
    cache_manifest_storage_key: artifacts.cacheManifest.storage_key,
    evidence_label: evidenceLabel,
    observability: {
      job_wait_ms: jobWaitMs,
      execution_duration_ms: executionDurationMs,
      report_generation_ms: reportGenerationMs,
      artifact_persist_ms: artifactPersistMs,
      db_index_ms: dbIndexMs,
      raw_artifact_bytes: rawArtifactBytes,
      db_index_rows: dbIndexRows,
      cache_hit_rate: cacheHitRate,
      range_cache_hit_rate: rangeCacheHitRateForManifest(execution.manifest),
      cache_hits: execution.manifest.cache_hits,
      fetch_count: execution.manifest.fetch_count,
      segments_reused: execution.manifest.segments_reused,
      segments_created: execution.manifest.segments_created,
      ohlcv_quality: execution.manifest.ohlcv_quality,
      missing_bar_ratio: execution.manifest.missing_bar_ratio,
      source_feed_checksum: execution.manifest.source_feed_checksum,
      csv_export_ms: execution.manifest.csv_export_ms,
      cache_validation_ms: execution.manifest.cache_validation_ms,
      runner_input_bars: execution.manifest.runner_input_bars,
      total_frames: execution.manifest.total_frames,
      processed_frames: execution.manifest.processed_frames,
      frames_per_second: execution.manifest.frames_per_second,
      progress_events: execution.manifest.progress_events,
      backtest_events: execution.manifest.backtest_events,
      closed_events: execution.manifest.closed_events,
      idle_events: execution.manifest.idle_events,
      active_events: execution.manifest.active_events,
      get_signal_calls: execution.manifest.get_signal_calls,
      get_candles_calls: execution.manifest.get_candles_calls,
      signal_evaluations: execution.manifest.signal_evaluations,
      backtest_run_ms: execution.manifest.backtest_run_ms,
      fetch_windows_total: execution.manifest.fetch_windows_total,
      fetch_windows_completed: execution.manifest.fetch_windows_completed,
      fetch_retry_count: execution.manifest.fetch_retry_count,
      fetch_duration_ms: execution.manifest.fetch_duration_ms,
      pine_compile_cache_hit: execution.manifest.pine_compile_cache_hit,
      pine_code_hash: execution.manifest.pine_code_hash,
    },
  });
  await setRunStatus(client, run, "completed");
  await appendEvent(client, run, "run.completed", {
    status: "completed",
    mode: "backtest-preview",
    ...previewCompletionPayload(job),
  });
  await enqueueChatBacktestSummaryJob(client, run, job, execution.metrics);
}

async function runBacktestExecution(
  job: RunJobRow,
  deadlineMs: number,
  progress?: BacktestProgressCallback,
): Promise<BacktestExecutionResult & { metrics: BacktestMetrics }> {
  if (job.payload_json.backtest_config.engine !== "pineforge") {
    throw new Error("Unsupported backtest engine for local preview.");
  }
  return runPineForgePreview(job, deadlineMs, progress);
}

type PineForgeCommandInput = {
  job_id: string;
  config: BacktestConfig;
  pine_code_path: string;
  candles_path: string;
  output_dir: string;
};

type PineForgeCommandResult =
  | {
      status: "pass";
      report: Record<string, unknown>;
      trades?: NormalizedTrade[];
      equity_curve?: EquityPoint[];
      compile?: Record<string, unknown>;
    }
  | { status: "fail"; error: { code: string; message: string; diagnostics?: Record<string, unknown> } };

type PineForgeRunnerResult =
  | {
      status: "pass";
      report: Record<string, unknown>;
      trades?: RawBacktestTrade[];
      equity_curve?: RawEquityPoint[];
      compile?: Record<string, unknown>;
      artifact_manifest?: Record<string, unknown>;
      runner?: string;
      runner_version?: string;
      stats?: {
        bars_processed?: number;
        compile_ms?: number;
        run_ms?: number;
        output_bytes?: number;
      };
    }
  | { status: "fail"; error: { code: string; message: string; diagnostics?: Record<string, unknown> } };

type PineForgeCommandOptions = {
  command?: string;
  args?: string[];
  timeoutMs?: number;
  env?: NodeJS.ProcessEnv;
};

type CcxtMarketMetadata = {
  exchange: BacktestExchange;
  exchange_symbol: string;
  active: boolean | null;
  type: string | null;
  spot: boolean | null;
  swap: boolean | null;
  contract: boolean | null;
  contract_size: number | null;
  mintick: number | null;
  mintick_source: string | null;
  mintick_confidence: "exchange_filter" | "precision" | "missing";
  pointvalue: number | null;
  pointvalue_source: string | null;
  pointvalue_confidence: "contract_size" | "spot_default" | "missing";
  precision: Record<string, unknown> | null;
  limits: Record<string, unknown> | null;
};

type CcxtExchangeLike = {
  id?: string;
  has?: Record<string, unknown>;
  timeframes?: Record<string, unknown>;
  loadMarkets?: () => Promise<unknown>;
  market?: (symbol: string) => unknown;
  markets?: Record<string, unknown>;
  symbols?: string[];
  fetchOHLCV: (symbol: string, timeframe: string, since?: number, limit?: number) => Promise<unknown[]>;
};

type OhlcvProvider = {
  source: typeof BACKTEST_OHLCV_PROVIDER;
  exchange: BacktestExchange;
  exchangeSymbol: string;
  client: CcxtExchangeLike;
};

async function runPineForgePreview(
  job: RunJobRow,
  deadlineMs: number,
  progress?: BacktestProgressCallback,
): Promise<BacktestExecutionResult & { metrics: BacktestMetrics }> {
  if (!PINEFORGE_ENABLED) {
    throw new Error("Local preview is disabled.");
  }
  const pineCode = typeof job.payload_json.pine_code === "string" ? job.payload_json.pine_code.trim() : "";
  if (!pineCode) {
    throw new Error("Backtest preview requires PineScript v6 strategy source.");
  }
  const config = normalizedConfig(job.payload_json.backtest_config);
  const candleTimeframe = executionCandleTimeframe(config);
  const provider = await createOhlcvProvider(config, candleTimeframe);
  const exchangeSymbol = provider.exchangeSymbol;
  const pineCodeHash = checksum(pineCode);
  const compileCache = await readPineCompileCache(pineCodeHash);
  const preload = await preloadExecutionCandles(config, provider, candleTimeframe, deadlineMs, job.id, progress);
  const cacheValidationStarted = Date.now();
  const ohlcvQuality = validateOhlcvFeed(preload.cacheResult.candles, {
    timeframe: candleTimeframe,
    startIso: config.start,
    endIso: config.end,
    context: `run ${job.run_id}`,
  });
  const cacheValidationMs = Date.now() - cacheValidationStarted;
  const marketMetadata = await resolveCcxtMarketMetadata(provider.client, provider.exchange, exchangeSymbol);
  if (!PINEFORGE_RUNNER_URL) {
    throw new Error("Local preview runner is unavailable.");
  }
  if (preload.cacheResult.candles.length > PINEFORGE_MAX_BARS) {
    throw new Error(`Local preview max bars exceeded: ${preload.cacheResult.candles.length} > ${PINEFORGE_MAX_BARS}`);
  }
  const runBase = `runs/${job.run_id}`;
  const runnerInputDir = join(ARTIFACT_ROOT, runBase, "pineforge-input");
  const runnerOutputDir = join(ARTIFACT_ROOT, runBase, "pineforge-output");
  const pinePath = join(runnerInputDir, "strategy.pine");
  const ohlcvCsvPath = join(runnerInputDir, "ohlcv.csv");
  await mkdir(runnerInputDir, { recursive: true });
  await mkdir(runnerOutputDir, { recursive: true });
  await writeFile(pinePath, `${pineCode}\n`, "utf8");
  const csvExportStarted = Date.now();
  await progress?.(BACKTEST_RUN_EVENTS.dataExporting, {
    bars: preload.cacheResult.candles.length,
    output: "ohlcv.csv",
  });
  const csvExport = await writeCandlesCsv(ohlcvCsvPath, preload.cacheResult.candles);
  const csvExportMs = Date.now() - csvExportStarted;
  const started = Date.now();
  {
    await progress?.(BACKTEST_RUN_EVENTS.executionStarted, {
      engine: "local_preview",
      preview_runtime: "local_preview",
    });
    const result = await runPineForgeRunner({
      job_id: job.id,
      run_id: job.run_id,
      config,
      pine_code_path: pinePath,
      ohlcv_csv_path: ohlcvCsvPath,
      output_dir: runnerOutputDir,
      market_metadata: marketMetadata,
      limits: {
        timeout_ms: Math.max(1, Math.min(PINEFORGE_RUNNER_TIMEOUT_MS, deadlineMs - Date.now())),
        max_bars: PINEFORGE_MAX_BARS,
        max_output_bytes: PINEFORGE_MAX_OUTPUT_BYTES,
        equity_downsample_points: PINEFORGE_EQUITY_DOWNSAMPLE_POINTS,
      },
    });
    if (result.status === "fail") {
      throw localPreviewErrorFromRunnerResult(result, job);
    }
    const trades = normalizeRunnerTrades(result.trades ?? [], config);
    const equityCurve = result.equity_curve?.length
      ? normalizeRunnerEquityCurve(result.equity_curve, config.initial_capital)
      : buildEquityCurve(config.initial_capital, trades);
    const metrics = buildMetrics(config.initial_capital, trades, equityCurve);
    const qualityFlags = backtestQualityFlags(config, trades, metrics, marketMetadata);
    const reportMetrics = {
      ...metrics,
      quality_flags: qualityFlags.flags,
      quality_status: qualityFlags.status,
    };
    const manifest = pineForgeManifest(config, exchangeSymbol, candleTimeframe, preload, Date.now() - started, {
      ohlcvQuality,
      csvExportMs,
      cacheValidationMs,
      marketMetadata,
      runnerInputBars: result.stats?.bars_processed ?? preload.cacheResult.candles.length,
      pineCompileCacheHit: compileCache !== null,
      pineCodeHash,
      csvBytesWritten: csvExport.bytesWritten,
    });
    const adapter = buildStrategyAdapter({
      ...job,
      payload_json: {
        ...job.payload_json,
        backtest_config: config,
      },
    });
    const report = {
      ...result.report,
      engine: "pineforge",
      evidence_label: PINEFORGE_EVIDENCE_LABEL,
      execution_semantics: "model_generated_pine_pineforge",
      signal_timeframe: config.timeframe,
      candle_timeframe: candleTimeframe,
      metrics: reportMetrics,
      ohlcv_quality: ohlcvQuality,
      market_data_source: manifest.market_data_source,
      source_feed_checksum: ohlcvQuality.checksum,
      pine_compile_cache_hit: compileCache !== null,
      pine_code_hash: pineCodeHash,
      gap_ranges: ohlcvQuality.gap_ranges,
      applied_cost_model: asRecord(result.report).applied_cost_model ?? asRecord(result.report).cost_model ?? null,
      pineforge_runtime: asRecord(result.report).pineforge_runtime ?? asRecord(result.report).applied_runtime ?? null,
      warnings: [
        "Local sandbox preview evidence only; not TradingView official validation, broker proof, live-trading evidence, or a profitability claim.",
        ...qualityFlags.warnings,
        ...stringList(asRecord(result.report).warnings),
        ...ohlcvQuality.warnings,
      ],
      quality_flags: qualityFlags.flags,
      quality_status: qualityFlags.status,
    };
    if (compileCache === null) {
      await writePineCompileCache(pineCodeHash, {
        pine_code_hash: pineCodeHash,
        runner_version: PINEFORGE_RUNNER_CACHE_VERSION,
        engine_version: PINEFORGE_ENGINE_VERSION,
        compile: result.compile ?? null,
        created_at: new Date().toISOString(),
      });
    }
    return {
      adapter,
      executionSemantics: "model_generated_pine_pineforge",
      manifest,
      report,
      trades,
      equityCurve,
      sourceBundle: {
        engine: "pineforge",
        exchange: config.exchange,
        pine_code: pineCode,
        pine_code_path: "strategy.pine",
        ohlcv_csv_storage_key: `${runBase}/pineforge-input/ohlcv.csv`,
        source_feed_checksum: ohlcvQuality.checksum,
      },
      metadata: {
        engine_package: PINEFORGE_ENGINE_PACKAGE,
        engine_version: result.runner_version ?? PINEFORGE_ENGINE_VERSION,
        evidence_label: PINEFORGE_EVIDENCE_LABEL,
        compile: result.compile ?? null,
        compile_cache: compileCache,
        pine_compile_cache_hit: compileCache !== null,
        pine_code_hash: pineCodeHash,
        artifact_manifest: result.artifact_manifest ?? null,
        runner: result.runner ?? "pineforge-runner",
        runner_stats: result.stats ?? null,
        signal_timeframe: config.timeframe,
        candle_timeframe: candleTimeframe,
        ohlcv_quality: ohlcvQuality,
        source_feed_checksum: ohlcvQuality.checksum,
      },
      metrics,
    };
  }
}

function pineForgeManifest(
  config: BacktestConfig,
  exchangeSymbol: string,
  candleTimeframe: string,
  preload: Awaited<ReturnType<typeof preloadExecutionCandles>>,
  runtimeMs: number,
  context: {
    ohlcvQuality: OhlcvQualityReport;
    csvExportMs: number;
    cacheValidationMs: number;
    marketMetadata: CcxtMarketMetadata | null;
    runnerInputBars: number;
    pineCompileCacheHit: boolean;
    pineCodeHash: string;
    csvBytesWritten: number;
  },
): CandleCacheManifest {
  const exchange = config.exchange ?? DEFAULT_EXCHANGE;
  const marketDataSource: MarketDataSourceMetadata = {
    source: BACKTEST_OHLCV_PROVIDER,
    exchange,
    symbol: config.symbol,
    exchange_symbol: exchangeSymbol,
    timeframe: candleTimeframe,
    start: config.start,
    end: config.end,
    checksum: context.ohlcvQuality.checksum,
    bars: preload.cacheResult.candles.length,
    market: context.marketMetadata,
  };
  const manifest: CandleCacheManifest = {
    source: BACKTEST_OHLCV_PROVIDER,
    exchange,
    symbol: config.symbol,
    exchange_symbol: exchangeSymbol,
    timeframe: candleTimeframe,
    signal_timeframe: config.timeframe,
    candle_timeframe: candleTimeframe,
    cache_version: CANDLE_CACHE_VERSION,
    range_start: config.start,
    range_end: config.end,
    checksum: checksumCandles(preload.cacheResult.candles),
    fetch_count: preload.cacheResult.cacheHit ? 0 : Math.max(1, preload.cacheResult.missingIntervalsFetched),
    cache_hits: preload.cacheResult.cacheHit ? 1 : 0,
    candles_fetched: preload.cacheResult.candlesFetched,
    candles_used: preload.cacheResult.candles.length,
    range_cache_hits: preload.cacheResult.rangeCacheHits,
    range_cache_misses: preload.cacheResult.rangeCacheMisses,
    missing_intervals_fetched: preload.cacheResult.missingIntervalsFetched,
    segments_reused: preload.cacheResult.segmentsReused,
    segments_created: preload.cacheResult.segmentsCreated,
    bytes_read: preload.cacheResult.bytesRead,
    bytes_written: preload.cacheResult.bytesWritten,
    preloaded_candles: preload.cacheResult.candles.length,
    get_candles_calls: 0,
    preloaded_slice_hits: 0,
    fallback_fetches: 0,
    signal_evaluations: 0,
    signal_candles: 0,
    signal_aggregate_ms: 0,
    indicator_precompute_ms: 0,
    backtest_run_ms: runtimeMs,
    total_frames: preload.cacheResult.candles.length,
    processed_frames: preload.cacheResult.candles.length,
    frames_per_second: runtimeMs > 0 ? Number((preload.cacheResult.candles.length / (runtimeMs / 1000)).toFixed(2)) : 0,
    progress_events: 0,
    backtest_events: 0,
    closed_events: 0,
    idle_events: 0,
    active_events: 0,
    get_signal_calls: 0,
    read_only: true,
    ohlcv_quality: context.ohlcvQuality,
    market_data_source: marketDataSource,
    source_feed_checksum: context.ohlcvQuality.checksum,
    gap_ranges: context.ohlcvQuality.gap_ranges,
    missing_bar_ratio: context.ohlcvQuality.missing_bar_ratio,
    csv_export_ms: context.csvExportMs,
    cache_validation_ms: context.cacheValidationMs,
    runner_input_bars: context.runnerInputBars,
    fetch_windows_total: preload.cacheResult.fetchWindowsTotal ?? 0,
    fetch_windows_completed: preload.cacheResult.fetchWindowsCompleted ?? 0,
    fetch_retry_count: preload.cacheResult.fetchRetryCount ?? 0,
    fetch_duration_ms: preload.cacheResult.fetchDurationMs ?? 0,
    pine_compile_cache_hit: context.pineCompileCacheHit,
    pine_code_hash: context.pineCodeHash,
    raw_artifact_bytes: context.csvBytesWritten,
    fetches: [
      fetchRecord(
        config,
        exchangeSymbol,
        candleTimeframe,
        preload.since,
        preload.limit,
        preload.cacheResult.candles,
        preload.cacheResult.cacheHit,
        preload.cacheResult.storage_key,
      ),
    ],
  };
  return manifest;
}

async function runPineForgeRunner(input: {
  job_id: string;
  run_id: string;
  config: BacktestConfig;
  pine_code_path: string;
  ohlcv_csv_path: string;
  output_dir: string;
  market_metadata?: CcxtMarketMetadata | null;
  limits: Record<string, number>;
}): Promise<PineForgeRunnerResult> {
  if (!PINEFORGE_RUNNER_URL) {
    throw new Error("Local preview runner is unavailable.");
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), input.limits.timeout_ms ?? PINEFORGE_RUNNER_TIMEOUT_MS);
  try {
    const response = await fetch(`${PINEFORGE_RUNNER_URL}/v1/pineforge/backtests`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(input),
      signal: controller.signal,
    });
    const payload = await response.json().catch(() => null) as PineForgeRunnerResult | null;
    if (!response.ok) {
      const message = payload?.status === "fail" ? payload.error.message : `HTTP ${response.status}`;
      return { status: "fail", error: { code: "pineforge_runner_http_error", message } };
    }
    if (payload?.status === "pass" || payload?.status === "fail") {
      return payload;
    }
    return { status: "fail", error: { code: "pineforge_runner_invalid_response", message: "Local preview runner returned invalid JSON" } };
  } catch (error) {
    return {
      status: "fail",
      error: {
        code: "pineforge_runner_unavailable",
        message: error instanceof Error ? error.message : String(error),
      },
    };
  } finally {
    clearTimeout(timer);
  }
}

function localPreviewErrorFromRunnerResult(
  result: Extract<PineForgeRunnerResult, { status: "fail" }>,
  job: RunJobRow,
): LocalPreviewError {
  const rawCode = result.error.code;
  const rawMessage = result.error.message;
  const rawDiagnostics = result.error.diagnostics;
  const compile = asRecord(rawDiagnostics?.compile);
  const compileStage = typeof compile.stage === "string" ? compile.stage : null;
  const previewErrorCode = classifyLocalPreviewFailure(rawCode, rawMessage, rawDiagnostics);
  return new LocalPreviewError(
    previewErrorCode,
    publicPreviewFailurePayload(job, previewErrorCode).message,
    {
      rawCode,
      rawMessage,
      rawDiagnostics,
      compileStage,
    },
  );
}

function classifyLocalPreviewFailure(
  rawCode: string,
  rawMessage: string,
  rawDiagnostics?: Record<string, unknown>,
): PreviewErrorCode {
  const compile = asRecord(rawDiagnostics?.compile);
  const compileStage = typeof compile.stage === "string" ? compile.stage : "";
  const text = `${rawCode} ${compileStage} ${rawMessage}`;
  if (/timeout|unavailable|invalid_response|invalid output|http_error|connection|fetch failed|aborted/i.test(text)) {
    return "preview_runtime_unavailable";
  }
  if (/max_bars|ohlcv|candles|market|exchange|symbol|timeframe|cache|data/i.test(text)) {
    return "preview_data_error";
  }
  if (/compile|transpile|unsupported|not implemented|unknown function|unknown identifier|no viable alternative|syntax/i.test(text)) {
    return "preview_compatibility_limit";
  }
  return "preview_execution_error";
}

function publicPreviewFailurePayload(job: RunJobRow, previewErrorCode: PreviewErrorCode): PreviewFailurePublicPayload {
  const repairAttempts = compatibilityRepairAttempts(job);
  const manualValidationRequired = previewErrorCode === "preview_compatibility_limit";
  return {
    preview_error_code: previewErrorCode,
    repair_attempts: repairAttempts,
    compatibility_repair_applied: repairAttempts > 0,
    manual_validation_required: manualValidationRequired,
    message: publicPreviewFailureMessage(previewErrorCode),
  };
}

function publicPreviewFailureMessage(previewErrorCode: PreviewErrorCode): string {
  if (previewErrorCode === "preview_compatibility_limit") {
    return "Local preview cannot run part of this script yet. The Pine code may still require manual platform validation.";
  }
  if (previewErrorCode === "preview_runtime_unavailable") {
    return "Local preview is temporarily unavailable. Try again later.";
  }
  if (previewErrorCode === "preview_data_error") {
    return "Local preview could not prepare the requested market data. Review the symbol, exchange, timeframe, and date range.";
  }
  return "Local preview failed before it could produce review evidence.";
}

function previewCompletionPayload(job: RunJobRow): Record<string, unknown> {
  const repairAttempts = compatibilityRepairAttempts(job);
  if (repairAttempts <= 0) {
    return {};
  }
  return {
    preview_error_code: "preview_compatibility_limit" satisfies PreviewErrorCode,
    repair_attempts: repairAttempts,
    compatibility_repair_applied: true,
    manual_validation_required: false,
    message: "Preview completed after compatibility repair.",
  };
}

function compatibilityRepairAttempts(job: RunJobRow): number {
  const repair = job.payload_json.compatibility_repair;
  if (!repair || typeof repair !== "object") {
    return 0;
  }
  const attempt = repair.attempt;
  return typeof attempt === "number" && Number.isFinite(attempt) && attempt > 0 ? Math.floor(attempt) : 0;
}

function validateOhlcvFeed(
  candles: Candle[],
  options: {
    timeframe: string;
    startIso: string;
    endIso: string;
    context: string;
  },
): OhlcvQualityReport {
  const stepMs = timeframeToMs(options.timeframe);
  const startMs = Date.parse(options.startIso);
  const endMs = Date.parse(options.endIso);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) {
    throw new Error(`Invalid OHLCV validation range for ${options.context}`);
  }
  if (candles.length === 0) {
    throw new Error(`Invalid OHLCV feed: no candles available for ${options.context}`);
  }
  const expectedBars = Math.ceil((endMs - startMs) / stepMs);
  const gapRanges: OhlcvGapRange[] = [];
  const seen = new Set<number>();
  let previousTimestamp: number | null = null;
  let duplicateCount = 0;

  for (let index = 0; index < candles.length; index += 1) {
    const candle = candles[index];
    const label = `${options.context} candle ${index}`;
    if (!isCandle(candle)) {
      throw new Error(`Invalid OHLCV feed: ${label} contains non-finite values`);
    }
    if (seen.has(candle.timestamp)) {
      duplicateCount += 1;
      throw new Error(`Invalid OHLCV feed: duplicate timestamp ${candle.timestamp} in ${options.context}`);
    }
    seen.add(candle.timestamp);
    if (previousTimestamp !== null && candle.timestamp <= previousTimestamp) {
      throw new Error(`Invalid OHLCV feed: timestamps must be strictly ascending in ${options.context}`);
    }
    if (candle.timestamp % stepMs !== 0) {
      throw new Error(`Invalid OHLCV feed: timestamp ${candle.timestamp} is not aligned to ${options.timeframe}`);
    }
    if (candle.high < Math.max(candle.open, candle.close) || candle.low > Math.min(candle.open, candle.close)) {
      throw new Error(`Invalid OHLCV feed: malformed OHLC at timestamp ${candle.timestamp}`);
    }
    if (!Number.isFinite(candle.volume)) {
      throw new Error(`Invalid OHLCV feed: non-finite volume at timestamp ${candle.timestamp}`);
    }
    if (previousTimestamp !== null && candle.timestamp > previousTimestamp + stepMs) {
      gapRanges.push(gapRange(previousTimestamp + stepMs, candle.timestamp, stepMs));
    }
    previousTimestamp = candle.timestamp;
  }

  const first = candles[0]?.timestamp;
  const last = candles[candles.length - 1]?.timestamp;
  if (Number.isFinite(first) && first > startMs) {
    gapRanges.unshift(gapRange(startMs, first, stepMs));
  }
  if (Number.isFinite(last) && last + stepMs < endMs) {
    gapRanges.push(gapRange(last + stepMs, endMs, stepMs));
  }
  const missingBars = gapRanges.reduce((total, gap) => total + gap.missing_bars, 0);
  const missingBarRatio = expectedBars <= 0 ? 0 : Number((missingBars / expectedBars).toFixed(8));
  const warnings = missingBars > 0
    ? [`OHLCV feed has ${missingBars} missing ${options.timeframe} bars across ${gapRanges.length} gap range(s).`]
    : [];
  return {
    status: missingBarRatio > OHLCV_GAP_WARN_RATIO ? "warn" : "pass",
    timeframe: options.timeframe,
    expected_step_ms: stepMs,
    expected_bars: expectedBars,
    actual_bars: candles.length,
    missing_bars: missingBars,
    duplicate_count: duplicateCount,
    gap_count: gapRanges.length,
    missing_bar_ratio: missingBarRatio,
    gap_ranges: gapRanges,
    first_timestamp: Number.isFinite(first) ? new Date(first).toISOString() : null,
    last_timestamp: Number.isFinite(last) ? new Date(last).toISOString() : null,
    checksum: checksumCandles(candles),
    validated_at: new Date().toISOString(),
    warnings,
  };
}

function gapRange(startMs: number, endMs: number, stepMs: number): OhlcvGapRange {
  return {
    start: new Date(startMs).toISOString(),
    end: new Date(endMs).toISOString(),
    missing_bars: Math.max(0, Math.ceil((endMs - startMs) / stepMs)),
  };
}

function candlesToCsv(candles: Candle[]): string {
  const rows = [OHLCV_CSV_HEADER];
  for (const candle of candles) {
    rows.push(formatCandleCsvRow(candle));
  }
  return `${rows.join("\n")}\n`;
}

async function writeCandlesCsv(path: string, candles: Candle[]): Promise<{ bytesWritten: number; bars: number }> {
  await mkdir(dirname(path), { recursive: true });
  const stream = createWriteStream(path, { encoding: "utf8" });
  let bytesWritten = 0;
  const writeChunk = async (chunk: string) => {
    bytesWritten += Buffer.byteLength(chunk, "utf8");
    if (!stream.write(chunk)) {
      await once(stream, "drain");
    }
  };
  try {
    await writeChunk(`${OHLCV_CSV_HEADER}\n`);
    for (const candle of candles) {
      await writeChunk(`${formatCandleCsvRow(candle)}\n`);
    }
    stream.end();
    await once(stream, "finish");
    return { bytesWritten, bars: candles.length };
  } catch (error) {
    stream.destroy();
    throw error;
  }
}

const OHLCV_CSV_HEADER = "timestamp,open,high,low,close,volume";

function formatCandleCsvRow(candle: Candle) {
  return `${candle.timestamp},${candle.open},${candle.high},${candle.low},${candle.close},${candle.volume}`;
}

async function runPineForgeCommand(
  input: PineForgeCommandInput,
  options: PineForgeCommandOptions = {},
): Promise<PineForgeCommandResult> {
  const command = options.command ?? PINEFORGE_COMMAND;
  if (!command) {
    throw new Error("Local preview runner is unavailable.");
  }
  const child = spawn(command, options.args ?? PINEFORGE_ARGS, {
    env: options.env ?? process.env,
    stdio: ["pipe", "pipe", "pipe"],
  });
  let stdout = "";
  let stderr = "";
  let settled = false;
  let truncated = false;
  const timeoutMs = options.timeoutMs ?? WORKER_TIMEOUT_MS;
  child.stdin.end(`${JSON.stringify(input)}\n`);
  return await new Promise((resolve, reject) => {
    const stopChild = () => {
      if (!child.killed) {
        child.kill("SIGKILL");
      }
    };
    const appendOutput = (current: string, chunk: Buffer): string => {
      if (current.length + chunk.length > BACKTEST_CHILD_STDIO_LIMIT_BYTES) {
        truncated = true;
        stopChild();
        return current;
      }
      return current + chunk.toString("utf8");
    };
    const finish = (error?: Error) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      if (error) {
        reject(error);
        return;
      }
      const parsed = parsePineForgeCommandOutput(stdout);
      if (!parsed) {
        reject(new Error(`Local preview runner returned invalid output${truncated ? " (truncated)" : ""}: ${stderr || stdout}`));
        return;
      }
      resolve(parsed);
    };
    const timer = setTimeout(() => {
      stopChild();
      finish(new Error(`Local preview runner timeout exceeded for ${input.job_id}: ${timeoutMs}ms`));
    }, timeoutMs);
    child.stdout.on("data", (chunk: Buffer) => {
      stdout = appendOutput(stdout, chunk);
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr = appendOutput(stderr, chunk);
    });
    child.on("error", (error) => finish(error));
    child.on("close", (code) => {
      if (code !== 0 && !stdout.trim()) {
        finish(new Error(`Local preview runner exited with code ${code}: ${stderr}`));
        return;
      }
      finish();
    });
  });
}

function parsePineForgeCommandOutput(output: string): PineForgeCommandResult | null {
  for (const line of output.trim().split(/\r?\n/).reverse()) {
    if (!line.trim().startsWith("{")) {
      continue;
    }
    try {
      const parsed = JSON.parse(line) as PineForgeCommandResult;
      if (parsed.status === "pass" || parsed.status === "fail") {
        return parsed;
      }
    } catch {
      continue;
    }
  }
  return null;
}


async function fetchPublicCandles(
  provider: OhlcvProvider,
  timeframe: string,
  since: Date,
  limit: number,
  deadlineMs: number,
  jobId: string,
  stats?: { retryCount: number },
): Promise<Candle[]> {
  const requestedLimit = Math.max(1, Math.trunc(limit));
  const candles: Candle[] = [];
  const seen = new Set<number>();
  const stepMs = timeframeToMs(timeframe);
  let cursor = since.getTime();
  while (candles.length < requestedLimit) {
    assertBeforeDeadline(deadlineMs, jobId);
    const batchLimit = Math.min(MAX_CANDLES_PER_FETCH, requestedLimit - candles.length);
    await throttleDataFetch(BACKTEST_OHLCV_PROVIDER, provider.exchange, provider.exchangeSymbol, timeframe);
    const ohlcv = await fetchOhlcvWithRetry(provider, timeframe, cursor, batchLimit, deadlineMs, jobId, stats);
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

async function fetchOhlcvWithRetry(
  provider: OhlcvProvider,
  timeframe: string,
  cursor: number,
  batchLimit: number,
  deadlineMs: number,
  jobId: string,
  stats?: { retryCount: number },
): Promise<unknown[]> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= DATA_FETCH_RETRY_ATTEMPTS; attempt += 1) {
    assertBeforeDeadline(deadlineMs, jobId);
    const release = await acquireGlobalFetchSlot(deadlineMs, jobId);
    try {
      return await provider.client.fetchOHLCV(provider.exchangeSymbol, timeframe, cursor, batchLimit);
    } catch (error) {
      lastError = error;
      if (!isTransientFetchError(error) || attempt >= DATA_FETCH_RETRY_ATTEMPTS) {
        throw error;
      }
      if (stats) {
        stats.retryCount += 1;
      }
      await sleep(DATA_FETCH_RETRY_BASE_MS * attempt);
    } finally {
      release();
    }
  }
  throw lastError instanceof Error ? lastError : new Error(String(lastError));
}

function isTransientFetchError(error: unknown): boolean {
  const message = error instanceof Error ? error.message.toLowerCase() : String(error).toLowerCase();
  return (
    message.includes("rate") ||
    message.includes("timeout") ||
    message.includes("timed out") ||
    message.includes("network") ||
    message.includes("econnreset") ||
    message.includes("fetch failed")
  );
}

async function preloadExecutionCandles(
  config: BacktestConfig,
  provider: OhlcvProvider,
  timeframe: string,
  deadlineMs: number,
  jobId: string,
  progress?: BacktestProgressCallback,
) {
  const stepMs = timeframeToMs(timeframe);
  const startMs = Date.parse(config.start);
  const endMs = Date.parse(config.end);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) {
    throw new Error("Backtest candle preload range is invalid");
  }
  const since = new Date(startMs);
  const limit = Math.ceil((endMs - since.getTime()) / stepMs);
  const cacheResult = await getOrFillPublicCandleCache(config, provider, timeframe, since, limit, deadlineMs, jobId, progress);
  return { since, limit, cacheResult };
}

function sliceCandles(candles: Candle[], since: Date, limit: number, timeframe: string): Candle[] | null {
  return sliceIndexedCandles(buildIndexedCandleStore(candles), since, limit, timeframe);
}

function buildIndexedCandleStore(candles: Candle[]): IndexedCandleStore {
  const sorted = [...candles].sort((a, b) => a.timestamp - b.timestamp);
  const indexByTimestamp = new Map<number, number>();
  sorted.forEach((candle, index) => {
    indexByTimestamp.set(candle.timestamp, index);
  });
  return { candles: sorted, indexByTimestamp };
}

function sliceIndexedCandles(store: IndexedCandleStore, since: Date, limit: number, timeframe: string): Candle[] | null {
  const requestedLimit = Math.max(0, Math.trunc(limit));
  if (requestedLimit === 0) {
    return [];
  }
  const stepMs = timeframeToMs(timeframe);
  const startMs = since.getTime();
  const startIndex = store.indexByTimestamp.get(startMs);
  if (startIndex === undefined) {
    return null;
  }
  const endIndex = startIndex + requestedLimit;
  if (endIndex > store.candles.length) {
    return null;
  }
  const sliced = store.candles.slice(startIndex, endIndex);
  for (let index = 0; index < sliced.length; index += 1) {
    if (sliced[index].timestamp !== startMs + index * stepMs) {
      return null;
    }
  }
  return sliced;
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

async function createOhlcvProvider(config: BacktestConfig, timeframe: string): Promise<OhlcvProvider> {
  const exchange = config.exchange ?? DEFAULT_EXCHANGE;
  if (MARKET_DATA_MODE === "fixture") {
    return {
      source: BACKTEST_OHLCV_PROVIDER,
      exchange,
      exchangeSymbol: normalizeCcxtSymbol(config.symbol),
      client: fixtureExchange(),
    };
  }
  const exchangeClass = (ccxt as unknown as Record<string, new (options: Record<string, unknown>) => CcxtExchangeLike>)[exchange];
  if (typeof exchangeClass !== "function") {
    throw new Error(`CCXT exchange is unavailable: ${exchange}`);
  }
  const client = new exchangeClass({ enableRateLimit: true });
  if (client.has?.fetchOHLCV === false) {
    throw new Error(`CCXT exchange ${exchange} does not support fetchOHLCV`);
  }
  if (typeof client.loadMarkets === "function") {
    await client.loadMarkets();
  }
  if (client.timeframes && !Object.prototype.hasOwnProperty.call(client.timeframes, timeframe)) {
    throw new Error(`CCXT exchange ${exchange} does not support timeframe ${timeframe}`);
  }
  return {
    source: BACKTEST_OHLCV_PROVIDER,
    exchange,
    exchangeSymbol: resolveCcxtSymbol(client, config.symbol, exchange),
    client,
  };
}

function resolveCcxtSymbol(exchange: CcxtExchangeLike, symbol: string, exchangeId: BacktestExchange): string {
  const candidates = ccxtSymbolCandidates(symbol);
  const markets = asRecord(exchange.markets);
  for (const candidate of candidates) {
    if (Object.prototype.hasOwnProperty.call(markets, candidate)) {
      return candidate;
    }
  }
  const symbols = Array.isArray(exchange.symbols) ? exchange.symbols : [];
  for (const candidate of candidates) {
    if (symbols.includes(candidate)) {
      return candidate;
    }
  }
  if (Object.keys(markets).length === 0 && typeof exchange.market !== "function") {
    return candidates[0] ?? normalizeCcxtSymbol(symbol);
  }
  throw new Error(`Symbol ${symbol} is not available on ${exchangeId}. Tried: ${candidates.join(", ")}`);
}

function ccxtSymbolCandidates(symbol: string): string[] {
  const normalized = normalizeCcxtSymbol(symbol);
  const compact = normalized.replace("/", "");
  const candidates = [normalized];
  for (const quote of ["USDT", "USD", "USDC", "BTC", "ETH"]) {
    if (compact.endsWith(quote) && compact.length > quote.length) {
      const base = compact.slice(0, -quote.length);
      candidates.push(`${base}/${quote}`);
    }
  }
  if (normalized.endsWith("/USDT")) {
    candidates.push(normalized.replace("/USDT", "/USD"));
  } else if (normalized.endsWith("/USD")) {
    candidates.push(normalized.replace("/USD", "/USDT"));
  }
  return [...new Set(candidates)];
}

async function resolveCcxtMarketMetadata(exchange: unknown, exchangeId: BacktestExchange, exchangeSymbol: string): Promise<CcxtMarketMetadata | null> {
  if (MARKET_DATA_MODE === "fixture") {
    return null;
  }
  const candidate = exchange as {
    loadMarkets?: () => Promise<unknown>;
    market?: (symbol: string) => unknown;
  };
  try {
    const hasLoadedMarkets = Object.keys(asRecord((candidate as CcxtExchangeLike).markets)).length > 0;
    if (!hasLoadedMarkets && typeof candidate.loadMarkets === "function") {
      await candidate.loadMarkets();
    }
    const market = typeof candidate.market === "function" ? asRecord(candidate.market(exchangeSymbol)) : {};
    return ccxtMarketMetadataFromMarket(exchangeId, exchangeSymbol, market);
  } catch {
    return null;
  }
}

function ccxtMarketMetadataFromMarket(exchangeId: BacktestExchange, exchangeSymbol: string, market: Record<string, unknown>): CcxtMarketMetadata {
  const precision = asNullableRecord(market.precision);
  const limits = asNullableRecord(market.limits);
  const info = asRecord(market.info);
  const mintick = marketMintick(market, info);
  const mintickFieldSource = mintickSource(market, info);
  const contractSize = numberOrNull(market.contractSize);
  const spot = booleanOrNull(market.spot);
  const pointvalue = contractSize ?? (spot === true ? 1 : null);
  return {
    exchange: exchangeId,
    exchange_symbol: exchangeSymbol,
    active: booleanOrNull(market.active),
    type: stringOrNull(market.type),
    spot,
    swap: booleanOrNull(market.swap),
    contract: booleanOrNull(market.contract),
    contract_size: contractSize,
    mintick,
    mintick_source: mintickFieldSource,
    mintick_confidence: mintick === null ? "missing" : mintickFieldSource === "precision.price" ? "precision" : "exchange_filter",
    pointvalue,
    pointvalue_source: contractSize !== null ? "market.contractSize" : spot === true ? "spot_default" : null,
    pointvalue_confidence: contractSize !== null ? "contract_size" : spot === true ? "spot_default" : "missing",
    precision,
    limits,
  };
}

function marketMintick(market: Record<string, unknown>, info: Record<string, unknown>): number | null {
  const filters = Array.isArray(info.filters) ? info.filters.map(asRecord) : [];
  for (const filter of filters) {
    if (filter.filterType === "PRICE_FILTER") {
      const tickSize = numberOrNull(filter.tickSize);
      if (tickSize !== null && tickSize > 0) {
        return tickSize;
      }
    }
  }
  const precision = asRecord(market.precision);
  const pricePrecision = numberOrNull(precision.price);
  if (pricePrecision === null || pricePrecision <= 0) {
    return null;
  }
  if (pricePrecision < 1) {
    return pricePrecision;
  }
  return 1 / (10 ** Math.trunc(pricePrecision));
}

function mintickSource(market: Record<string, unknown>, info: Record<string, unknown>): string | null {
  const filters = Array.isArray(info.filters) ? info.filters.map(asRecord) : [];
  for (const filter of filters) {
    if (filter.filterType === "PRICE_FILTER" && numberOrNull(filter.tickSize) !== null) {
      return "info.filters.PRICE_FILTER.tickSize";
    }
  }
  const precision = asRecord(market.precision);
  return numberOrNull(precision.price) !== null ? "precision.price" : null;
}

function asNullableRecord(value: unknown): Record<string, unknown> | null {
  const record = asRecord(value);
  return Object.keys(record).length > 0 ? record : null;
}

function booleanOrNull(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.length > 0) : [];
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
    source: BACKTEST_OHLCV_PROVIDER,
    exchange: config.exchange ?? DEFAULT_EXCHANGE,
    symbol: config.symbol,
    exchange_symbol: exchangeSymbol,
    timeframe,
    since: since.toISOString(),
    limit,
    candles: candles.length,
    checksum: checksumCandles(candles),
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
  provider: OhlcvProvider,
  timeframe: string,
  since: Date,
  limit: number,
  deadlineMs: number,
  jobId: string,
  progress?: BacktestProgressCallback,
): Promise<CandleCacheResult> {
  if (CANDLE_CACHE_VERSION === "chunk-v1") {
    return getOrFillPublicCandleCacheV1(config, provider, timeframe, since, limit, deadlineMs, jobId);
  }
  return getOrFillPublicCandleCacheV2(config, provider, timeframe, since, limit, deadlineMs, jobId, progress);
}

async function getOrFillPublicCandleCacheV1(
  config: BacktestConfig,
  provider: OhlcvProvider,
  timeframe: string,
  since: Date,
  limit: number,
  deadlineMs: number,
  jobId: string,
  progress?: BacktestProgressCallback,
): Promise<CandleCacheResult> {
  const exchangeSymbol = provider.exchangeSymbol;
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
        const candles = await fetchPublicCandles(provider, timeframe, since, limit, deadlineMs, jobId);
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
  provider: OhlcvProvider,
  timeframe: string,
  since: Date,
  limit: number,
  deadlineMs: number,
  jobId: string,
  progress?: BacktestProgressCallback,
): Promise<CandleCacheResult> {
  const exchangeSymbol = provider.exchangeSymbol;
  const requested = requestedRange(timeframe, since, limit);
  const dataset = rangeCacheDataset(config, exchangeSymbol, timeframe);
  const existing = await readRangeCacheCoverage(config, exchangeSymbol, timeframe, requested);
  if (existing.complete) {
    await progress?.(BACKTEST_RUN_EVENTS.dataCacheReusing, {
      segments_reused: existing.segmentsReused,
      bytes_read: existing.bytesRead,
      cache_hit: true,
    });
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
          await progress?.(BACKTEST_RUN_EVENTS.dataCacheReusing, {
            segments_reused: winner.segmentsReused,
            bytes_read: winner.bytesRead,
            cache_hit: true,
          });
          return candleCacheResult(dataset.index_key, winner.candles, true, {
            rangeCacheHits: 1,
            segmentsReused: winner.segmentsReused,
            bytesRead: winner.bytesRead,
          });
        }
        const fetchResult = await fetchAndStoreMissingRangeSegments(
          config,
          provider,
          timeframe,
          requested,
          winner,
          deadlineMs,
          jobId,
          progress,
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
          fetchWindowsTotal: fetchResult.fetchWindowsTotal,
          fetchWindowsCompleted: fetchResult.fetchWindowsCompleted,
          fetchRetryCount: fetchResult.fetchRetryCount,
          fetchDurationMs: fetchResult.fetchDurationMs,
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
      await progress?.(BACKTEST_RUN_EVENTS.dataCacheReusing, {
        segments_reused: waited.segmentsReused,
        bytes_read: waited.bytesRead,
        cache_hit: true,
      });
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
  provider: OhlcvProvider,
  timeframe: string,
  requested: { startMs: number; endMs: number; stepMs: number },
  coverage: RangeCoverageResult,
  deadlineMs: number,
  jobId: string,
  progress?: BacktestProgressCallback,
) {
  const fetchedCandles: Candle[] = [];
  let candlesFetched = 0;
  let segmentsCreated = 0;
  let bytesWritten = 0;
  let fetchRetryCount = 0;
  const fetchStartedAt = Date.now();
  const newSegments: RangeCacheSegment[] = [];
  const windows = coverage.missingIntervals.flatMap((interval) => cacheFetchWindows(interval, requested.stepMs));
  let windowsCompleted = 0;
  await progress?.(BACKTEST_RUN_EVENTS.dataFetching, {
    fetch_windows_total: windows.length,
    fetch_windows_completed: 0,
    concurrency: DATA_FETCH_CONCURRENCY,
  });
  const fetchedByWindow = await mapWithConcurrency(windows, DATA_FETCH_CONCURRENCY, async (interval) => {
    assertBeforeDeadline(deadlineMs, jobId);
    const intervalLimit = Math.max(1, Math.ceil((interval.endMs - interval.startMs) / requested.stepMs));
    const stats = { retryCount: 0 };
    const candles = await fetchPublicCandles(provider, timeframe, new Date(interval.startMs), intervalLimit, deadlineMs, jobId, stats);
    fetchRetryCount += stats.retryCount;
    windowsCompleted += 1;
    await progress?.(BACKTEST_RUN_EVENTS.dataFetching, {
      fetch_windows_total: windows.length,
      fetch_windows_completed: windowsCompleted,
      fetch_retry_count: fetchRetryCount,
    });
    const sliced = sliceAndDedupeCandles(candles, interval.startMs, interval.endMs);
    return { interval, sliced };
  });
  for (const { interval, sliced } of fetchedByWindow.sort((a, b) => a.interval.startMs - b.interval.startMs)) {
    candlesFetched += sliced.length;
    if (sliced.length === 0) {
      continue;
    }
    const writeResult = await writeRangeCacheSegment(config, provider.exchangeSymbol, timeframe, requested.stepMs, sliced);
    newSegments.push(writeResult.segment);
    appendCandles(fetchedCandles, sliced);
    segmentsCreated += 1;
    bytesWritten += writeResult.bytesWritten;
  }
  await upsertRangeCacheSegments(config, provider.exchangeSymbol, timeframe, requested.stepMs, newSegments);
  const combined = sliceAndDedupeCandles(combineCandles(coverage.candles, fetchedCandles), requested.startMs, requested.endMs);
  return {
    candles: combined,
    candlesFetched,
    missingIntervalsFetched: coverage.missingIntervals.length,
    segmentsCreated,
    bytesRead: 0,
    bytesWritten,
    fetchWindowsTotal: windows.length,
    fetchWindowsCompleted: windowsCompleted,
    fetchRetryCount,
    fetchDurationMs: Date.now() - fetchStartedAt,
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
  const selectedSegments = selectRangeCacheSegments(index.segments, requested);
  const segmentReads = await mapWithConcurrency(selectedSegments, DATA_FETCH_CONCURRENCY, async (segment) => ({
    segment,
    read: await readRangeCacheSegment(segment),
  }));
  for (const { segment, read: segmentRead } of segmentReads) {
    const startMs = Date.parse(segment.range_start);
    const endMs = Date.parse(segment.range_end);
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= requested.startMs || startMs >= requested.endMs) {
      continue;
    }
    if (segmentRead === null) {
      continue;
    }
    const sliced = sliceAndDedupeCandles(segmentRead.candles, requested.startMs, requested.endMs);
    if (sliced.length === 0) {
      continue;
    }
    appendCandles(candles, sliced);
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

function selectRangeCacheSegments(
  segments: RangeCacheSegment[],
  requested: { startMs: number; endMs: number; stepMs: number },
): RangeCacheSegment[] {
  const candidates = segments
    .filter((segment) => segment.step_ms === requested.stepMs)
    .map((segment) => ({ segment, startMs: Date.parse(segment.range_start), endMs: Date.parse(segment.range_end) }))
    .filter((item) => Number.isFinite(item.startMs) && Number.isFinite(item.endMs))
    .filter((item) => item.endMs > requested.startMs && item.startMs < requested.endMs)
    .sort((a, b) => a.startMs - b.startMs || b.endMs - a.endMs);
  const selected: RangeCacheSegment[] = [];
  let cursor = requested.startMs;
  while (cursor < requested.endMs) {
    const covering = candidates
      .filter((item) => item.startMs <= cursor && item.endMs > cursor)
      .sort((a, b) => b.endMs - a.endMs || a.startMs - b.startMs)[0];
    if (covering !== undefined) {
      selected.push(covering.segment);
      cursor = Math.min(covering.endMs, requested.endMs);
      continue;
    }
    const next = candidates.find((item) => item.startMs > cursor);
    if (next === undefined) {
      break;
    }
    selected.push(next.segment);
    cursor = Math.min(next.endMs, requested.endMs);
  }
  const seen = new Set<string>();
  return selected.filter((segment) => {
    if (seen.has(segment.storage_key)) {
      return false;
    }
    seen.add(segment.storage_key);
    return true;
  });
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
      source: BACKTEST_OHLCV_PROVIDER,
      exchange: config.exchange ?? DEFAULT_EXCHANGE,
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
    if (checksumCandles(candles) !== segment.checksum) {
      return null;
    }
    try {
      validateOhlcvFeed(candles, {
        timeframe: timeframeFromStepMs(segment.step_ms),
        startIso: segment.range_start,
        endIso: segment.range_end,
        context: `cache segment ${segment.storage_key}`,
      });
    } catch (error) {
      if (error instanceof Error && error.message.startsWith("Invalid OHLCV feed")) {
        return null;
      }
      throw error;
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
  const segmentChecksum = checksumCandles(normalized);
  const quality = validateOhlcvFeed(normalized, {
    timeframe: timeframeFromStepMs(stepMs),
    startIso: new Date(rangeStart).toISOString(),
    endIso: new Date(rangeEnd).toISOString(),
    context: `cache segment ${config.symbol} ${timeframe}`,
  });
  const storageKey = rangeCacheSegmentStorageKey(config, exchangeSymbol, timeframe, rangeStart, rangeEnd, segmentChecksum);
  const segment: RangeCacheSegment = {
    range_start: new Date(rangeStart).toISOString(),
    range_end: new Date(rangeEnd).toISOString(),
    step_ms: stepMs,
    storage_key: storageKey,
    checksum: segmentChecksum,
    created_at: new Date().toISOString(),
    candle_count: normalized.length,
    validated_at: quality.validated_at,
    expected_step_ms: stepMs,
    gap_count: quality.gap_count,
    first_timestamp: quality.first_timestamp ?? undefined,
    last_timestamp: quality.last_timestamp ?? undefined,
  };
  const payload = {
    cache_version: "range-v2",
    source: BACKTEST_OHLCV_PROVIDER,
    exchange: config.exchange ?? DEFAULT_EXCHANGE,
    symbol: config.symbol,
    exchange_symbol: exchangeSymbol,
    timeframe,
    ...segment,
    candles: normalized,
  };
  const content = `${JSON.stringify(payload)}\n`;
  const bytesWritten = Buffer.byteLength(content, "utf8");
  const segmentByteLimit = Math.min(MAX_ARTIFACT_BYTES, Math.max(1, CACHE_SEGMENT_TARGET_BYTES));
  if (bytesWritten > segmentByteLimit) {
    throw new Error(`Backtest candle cache segment size limit exceeded: ${bytesWritten} > ${segmentByteLimit}`);
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
  await upsertRangeCacheSegments(config, exchangeSymbol, timeframe, stepMs, [segment]);
}

async function upsertRangeCacheSegments(
  config: BacktestConfig,
  exchangeSymbol: string,
  timeframe: string,
  stepMs: number,
  newSegments: RangeCacheSegment[],
) {
  if (newSegments.length === 0) {
    return;
  }
  const dataset = rangeCacheDataset(config, exchangeSymbol, timeframe);
  const existing = await readRangeCacheIndex(config, exchangeSymbol, timeframe);
  const pruned = newSegments.reduce(
    (segments, segment) => pruneCoveredRangeCacheSegments(segments, segment, stepMs),
    existing?.segments ?? [],
  );
  const segments = await compactRangeCacheSegments(
    config,
    exchangeSymbol,
    timeframe,
    stepMs,
    pruned,
  );
  const index: RangeCacheIndex = {
    cache_version: "range-v2",
    source: BACKTEST_OHLCV_PROVIDER,
    exchange: config.exchange ?? DEFAULT_EXCHANGE,
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

function pruneCoveredRangeCacheSegments(
  segments: RangeCacheSegment[],
  newSegment: RangeCacheSegment,
  stepMs: number,
): RangeCacheSegment[] {
  return [...segments.filter((item) => item.storage_key !== newSegment.storage_key), newSegment]
    .filter((item) => item.step_ms === stepMs)
    .filter((item) => item.storage_key === newSegment.storage_key || !rangeCacheSegmentCovers(newSegment, item))
    .sort((a, b) => Date.parse(a.range_start) - Date.parse(b.range_start) || Date.parse(a.range_end) - Date.parse(b.range_end));
}

function rangeCacheSegmentCovers(outer: RangeCacheSegment, inner: RangeCacheSegment): boolean {
  if (outer.step_ms !== inner.step_ms) {
    return false;
  }
  const outerStartMs = Date.parse(outer.range_start);
  const outerEndMs = Date.parse(outer.range_end);
  const innerStartMs = Date.parse(inner.range_start);
  const innerEndMs = Date.parse(inner.range_end);
  return (
    Number.isFinite(outerStartMs) &&
    Number.isFinite(outerEndMs) &&
    Number.isFinite(innerStartMs) &&
    Number.isFinite(innerEndMs) &&
    outerStartMs <= innerStartMs &&
    outerEndMs >= innerEndMs
  );
}

async function compactRangeCacheSegments(
  config: BacktestConfig,
  exchangeSymbol: string,
  timeframe: string,
  stepMs: number,
  segments: RangeCacheSegment[],
): Promise<RangeCacheSegment[]> {
  if (CACHE_SEGMENT_POLICY === "monthly") {
    return segments
      .filter((segment) => segment.step_ms === stepMs)
      .sort((a, b) => Date.parse(a.range_start) - Date.parse(b.range_start));
  }
  const sorted = segments
    .filter((segment) => segment.step_ms === stepMs)
    .sort((a, b) => Date.parse(a.range_start) - Date.parse(b.range_start));
  const compacted: RangeCacheSegment[] = [];
  let group: RangeCacheSegment[] = [];
  for (const segment of sorted) {
    if (group.length === 0) {
      group = [segment];
      continue;
    }
    const previous = group[group.length - 1];
    if (Date.parse(previous.range_end) === Date.parse(segment.range_start)) {
      group.push(segment);
      continue;
    }
    compacted.push(...(await mergeContiguousRangeCacheGroup(config, exchangeSymbol, timeframe, stepMs, group)));
    group = [segment];
  }
  compacted.push(...(await mergeContiguousRangeCacheGroup(config, exchangeSymbol, timeframe, stepMs, group)));
  return compacted.sort((a, b) => Date.parse(a.range_start) - Date.parse(b.range_start));
}

async function mergeContiguousRangeCacheGroup(
  config: BacktestConfig,
  exchangeSymbol: string,
  timeframe: string,
  stepMs: number,
  group: RangeCacheSegment[],
): Promise<RangeCacheSegment[]> {
  if (group.length < 2) {
    return group;
  }
  const totalCandles = group.reduce((sum, segment) => sum + segment.candle_count, 0);
  if (totalCandles > MAX_CANDLES_PER_JOB) {
    return group;
  }
  const candles: Candle[] = [];
  for (const segment of group) {
    const read = await readRangeCacheSegment(segment);
    if (read === null) {
      return group;
    }
    appendCandles(candles, read.candles);
  }
  try {
    const merged = await writeRangeCacheSegment(config, exchangeSymbol, timeframe, stepMs, candles);
    return [merged.segment];
  } catch (error) {
    if (error instanceof Error && error.message.includes("cache segment size limit exceeded")) {
      return group;
    }
    throw error;
  }
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

function cacheFetchWindows(interval: FetchWindow, stepMs: number): FetchWindow[] {
  if (CACHE_SEGMENT_POLICY !== "monthly" || stepMs !== 60_000) {
    return [interval];
  }
  const windows: FetchWindow[] = [];
  let cursor = interval.startMs;
  while (cursor < interval.endMs) {
    const nextMonth = nextUtcMonthStart(cursor);
    const endMs = Math.min(interval.endMs, nextMonth);
    if (endMs > cursor) {
      windows.push({ startMs: cursor, endMs });
    }
    cursor = endMs;
  }
  return windows;
}

function nextUtcMonthStart(timestampMs: number): number {
  const date = new Date(timestampMs);
  return Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1);
}

async function mapWithConcurrency<T, R>(
  items: T[],
  concurrency: number,
  worker: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let nextIndex = 0;
  const workers = Array.from({ length: Math.min(Math.max(1, concurrency), items.length) }, async () => {
    while (nextIndex < items.length) {
      const index = nextIndex;
      nextIndex += 1;
      results[index] = await worker(items[index], index);
    }
  });
  await Promise.all(workers);
  return results;
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
    source: BACKTEST_OHLCV_PROVIDER,
    exchange: config.exchange ?? DEFAULT_EXCHANGE,
    symbol: config.symbol,
    exchange_symbol: exchangeSymbol,
    timeframe,
    since: since.toISOString(),
    limit,
    checksum: checksumCandles(candles),
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
    source: BACKTEST_OHLCV_PROVIDER,
    exchange: config.exchange ?? DEFAULT_EXCHANGE,
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
  const exchange = config.exchange ?? DEFAULT_EXCHANGE;
  const directory = [
    "cache",
    "index-v2",
    BACKTEST_OHLCV_PROVIDER,
    exchange,
    pathToken(exchangeSymbol),
    pathToken(timeframe),
  ].join("/");
  return {
    index_key: `${directory}/manifest.json`,
    lock_key: `cache/locks-v2/${checksum({ source: BACKTEST_OHLCV_PROVIDER, exchange, symbol: config.symbol, exchange_symbol: exchangeSymbol, timeframe })}.lock`,
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
    source: BACKTEST_OHLCV_PROVIDER,
    exchange: config.exchange ?? DEFAULT_EXCHANGE,
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
    BACKTEST_OHLCV_PROVIDER,
    config.exchange ?? DEFAULT_EXCHANGE,
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
  const symbol = config.symbol.trim().toUpperCase();
  if (!symbol || [...symbol].some((character) => character.charCodeAt(0) < 32)) {
    throw new Error("Invalid backtest symbol");
  }
  const timeframe = normalizeTimeframe(config.timeframe);
  if (!BACKTEST_EXECUTABLE_TIMEFRAME_SET.has(timeframe)) {
    throw new Error(`Unsupported backtest timeframe for semantic preview: ${timeframe}`);
  }
  const candleTimeframe = normalizeTimeframe(config.candle_timeframe ?? "1m");
  if (!BACKTEST_EXECUTION_CANDLE_TIMEFRAMES.has(candleTimeframe)) {
    throw new Error(`Unsupported backtest candle_timeframe for semantic preview: ${candleTimeframe}`);
  }
  for (const [name, value] of Object.entries({ initial_capital: config.initial_capital, fee_bps: config.fee_bps, slippage_bps: config.slippage_bps })) {
    if (!Number.isFinite(value)) {
      throw new Error(`Invalid backtest ${name}: must be finite`);
    }
  }
  if (config.initial_capital <= 0) {
    throw new Error("Invalid backtest initial_capital: must be positive");
  }
  if (config.fee_bps < 0 || config.fee_bps > BACKTEST_MAX_COST_BPS) {
    throw new Error(`Invalid backtest fee_bps: must be between 0 and ${BACKTEST_MAX_COST_BPS}`);
  }
  if (config.slippage_bps < 0 || config.slippage_bps > BACKTEST_MAX_COST_BPS) {
    throw new Error(`Invalid backtest slippage_bps: must be between 0 and ${BACKTEST_MAX_COST_BPS}`);
  }
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
  const estimatedCandles = Math.ceil((end.getTime() - start.getTime()) / timeframeToMs(candleTimeframe));
  if (estimatedCandles > MAX_CANDLES_PER_JOB) {
    throw new Error(`Backtest candle limit exceeded: ${estimatedCandles} > ${MAX_CANDLES_PER_JOB}`);
  }
  const requestedExchange = typeof config.exchange === "string" && config.exchange.trim() ? config.exchange : null;
  const exchange = requestedExchange === null ? DEFAULT_EXCHANGE : normalizeExchange(requestedExchange);
  if (exchange === null) {
    throw new Error(`Unsupported backtest exchange: ${requestedExchange}. Allowed exchanges: ${[...ALLOWED_EXCHANGES].join(", ")}`);
  }
  if (!ALLOWED_EXCHANGES.has(exchange)) {
    throw new Error(`Unsupported backtest exchange: ${exchange}. Allowed exchanges: ${[...ALLOWED_EXCHANGES].join(", ")}`);
  }
  return {
    ...config,
    exchange,
    symbol,
    timeframe,
    candle_timeframe: candleTimeframe,
    start: start.toISOString(),
    end: end.toISOString(),
  };
}

function parseAllowedExchanges(value: string | undefined): Set<BacktestExchange> {
  const parsed = (value ?? BACKTEST_OHLCV_EXCHANGES.join(","))
    .split(",")
    .map((item) => normalizeExchange(item))
    .filter((item): item is BacktestExchange => item !== null);
  return new Set(parsed.length > 0 ? parsed : BACKTEST_OHLCV_EXCHANGES);
}

function parseDefaultExchange(value: string | undefined, allowed: Set<BacktestExchange>): BacktestExchange {
  const requested = normalizeExchange(value ?? BACKTEST_OHLCV_DEFAULT_EXCHANGE);
  if (requested === null) {
    throw new Error(`Unsupported default backtest exchange: ${value ?? BACKTEST_OHLCV_DEFAULT_EXCHANGE}`);
  }
  if (!allowed.has(requested)) {
    throw new Error(`Default backtest exchange ${requested} is not in BACKTEST_WORKER_ALLOWED_EXCHANGES`);
  }
  return requested;
}

function normalizeExchange(value: unknown): BacktestExchange | null {
  if (typeof value !== "string") {
    return null;
  }
  const normalized = value.trim().toLowerCase();
  return BACKTEST_OHLCV_EXCHANGES.includes(normalized as BacktestExchange) ? normalized as BacktestExchange : null;
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
  const byTimestamp = new Map<number, Candle>();
  for (const candle of history.get(key) ?? []) {
    byTimestamp.set(candle.timestamp, candle);
  }
  for (const candle of candles) {
    byTimestamp.set(candle.timestamp, candle);
  }
  history.set(key, [...byTimestamp.values()].sort((a, b) => a.timestamp - b.timestamp));
}

function appendCandles(target: Candle[], source: Candle[]) {
  for (const candle of source) {
    target.push(candle);
  }
}

function combineCandles(first: Candle[], second: Candle[]): Candle[] {
  const combined: Candle[] = [];
  appendCandles(combined, first);
  appendCandles(combined, second);
  return combined;
}

function aggregateCandles(candles: Candle[], timeframe: string): Candle[] {
  const stepMs = timeframeToMs(timeframe);
  const buckets = new Map<number, Candle>();
  for (const candle of [...candles].sort((a, b) => a.timestamp - b.timestamp)) {
    const timestamp = Math.floor(candle.timestamp / stepMs) * stepMs;
    const existing = buckets.get(timestamp);
    if (!existing) {
      buckets.set(timestamp, { ...candle, timestamp });
      continue;
    }
    existing.high = Math.max(existing.high, candle.high);
    existing.low = Math.min(existing.low, candle.low);
    existing.close = candle.close;
    existing.volume += candle.volume;
  }
  return [...buckets.values()].sort((a, b) => a.timestamp - b.timestamp);
}

function aggregateClosedSignalCandles(candles: Candle[], timeframe: string, whenMs: number): Candle[] {
  const signalStepMs = timeframeToMs(timeframe);
  const currentBucketStart = Math.floor(whenMs / signalStepMs) * signalStepMs;
  return aggregateCandles(
    candles.filter((candle) => candle.timestamp < currentBucketStart),
    timeframe,
  );
}

function isSignalDecisionBoundary(whenMs: number, signalTimeframe: string, candleTimeframe: string): boolean {
  const signalStepMs = timeframeToMs(signalTimeframe);
  const candleStepMs = timeframeToMs(candleTimeframe);
  if (signalStepMs <= candleStepMs) {
    return true;
  }
  return whenMs % signalStepMs === 0;
}

function candleHistoryKey(exchangeSymbol: string, timeframe: string): string {
  return `${exchangeSymbol}:${normalizeTimeframe(timeframe)}`;
}

function executionCandleTimeframe(config: BacktestConfig): string {
  return normalizeTimeframe(config.candle_timeframe ?? "1m");
}

function capCandleRequestLimit(timeframe: string, since: Date, limit: number, endIso: string): number {
  const requested = Number.isFinite(limit) ? Math.max(0, Math.trunc(limit)) : MAX_CANDLES_PER_FETCH;
  if (requested <= 0) {
    return 0;
  }
  const endMs = Date.parse(endIso);
  if (!Number.isFinite(endMs)) {
    return requested;
  }
  const remainingMs = endMs - since.getTime();
  if (remainingMs <= 0) {
    return 0;
  }
  return Math.min(requested, Math.ceil(remainingMs / timeframeToMs(timeframe)));
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
  const position =
    findString(strategySpec, ["position", "side", "direction"], "long").toLowerCase() === "short"
      ? "short"
      : "long";
  return {
    strategy_name: safeRegistryName(`scb-strategy-${job.id}`),
    exchange_name: safeRegistryName(`scb-exchange-${job.id}`),
    frame_name: safeRegistryName(`scb-frame-${job.id}`),
    timeframe: config.timeframe,
    candle_timeframe: executionCandleTimeframe(config),
    position,
    percent_take_profit: clamp(findNumber(strategySpec, ["take_profit_pct", "takeProfitPct", "tp_pct", "take_profit"], 2), 0.01, 100),
    percent_stop_loss: clamp(findNumber(strategySpec, ["stop_loss_pct", "stopLossPct", "sl_pct", "stop_loss"], 1), 0.01, 100),
    cost: clamp(findNumber(strategySpec, ["cost", "trade_cost", "position_size"], config.initial_capital / 10), 1, config.initial_capital),
    minute_estimated_time: Math.max(1, Math.trunc(findNumber(strategySpec, ["minute_estimated_time", "holding_minutes"], 1440))),
  };
}

function rangeClassForConfig(config: BacktestConfig): "short" | "medium" | "long" {
  const candleTimeframe = executionCandleTimeframe(config);
  const stepMs = timeframeToMs(candleTimeframe);
  const startMs = Date.parse(config.start);
  const endMs = Date.parse(config.end);
  const estimatedCandles = Number.isFinite(startMs) && Number.isFinite(endMs) && endMs > startMs
    ? Math.ceil((endMs - startMs) / stepMs)
    : 0;
  if (estimatedCandles > 525_600) {
    return "long";
  }
  if (estimatedCandles > 129_600) {
    return "medium";
  }
  return "short";
}

function artifactBytesTotal(artifacts: Record<string, ArtifactSpec>): number {
  return Object.values(artifacts).reduce((total, artifact) => {
    return total + Buffer.byteLength(serializeArtifactContent(artifact), "utf8");
  }, 0);
}

async function writeBacktestArtifacts(job: RunJobRow, execution: BacktestExecutionResult) {
  const runBase = `runs/${job.run_id}`;
  const artifacts: Record<string, ArtifactSpec> = {
    plan: artifact(runBase, "backtest_plan", "backtest_plan.json", {
      backtest_config: job.payload_json.backtest_config,
      signal_timeframe: execution.adapter.timeframe,
      candle_timeframe: execution.manifest.candle_timeframe ?? execution.manifest.timeframe,
      strategy_spec: job.payload_json.strategy_spec,
      execution_semantics: execution.executionSemantics,
      adapter: execution.adapter,
    }),
    dashboard: artifact(
      runBase,
      "backtest_dashboard",
      "backtest-dashboard.json",
      buildBacktestDashboard(job, execution),
      { preview_summary: buildBacktestPreviewSummary(job, execution) },
    ),
    report: artifact(runBase, "backtest_report", "backtest-report.json", execution.report),
    trades: artifact(runBase, "backtest_trades", "trades.json", execution.trades),
    equityCurve: artifact(runBase, "backtest_equity_curve", "equity-curve.json", execution.equityCurve),
    cacheManifest: artifact(runBase, "market_data_cache_manifest", "candle-cache-manifest.json", execution.manifest),
    ohlcvMetadata: artifact(runBase, "market_data_ohlcv_metadata", "ohlcv-metadata.json", {
      storage_key: `${runBase}/pineforge-input/ohlcv.csv`,
      source_feed_checksum: execution.manifest.source_feed_checksum,
      ohlcv_quality: execution.manifest.ohlcv_quality,
      market_data_source: execution.manifest.market_data_source,
    }),
    sourceBundle: artifact(runBase, "backtest_source_bundle", "strategy-adapter-source.json", execution.sourceBundle),
    metadata: artifact(runBase, "backtest_run_metadata", "run-metadata.json", execution.metadata),
  };
  const pineCode = typeof execution.sourceBundle.pine_code === "string" ? execution.sourceBundle.pine_code : "";
  if (pineCode) {
    artifacts.pine = textArtifact(runBase, "pine_strategy_source", "strategy.pine", pineCode);
    artifacts.pineforgeCompile = artifact(runBase, "pineforge_compile_report", "pineforge-compile.json", {
      engine: "pineforge",
      compile: execution.metadata.compile ?? null,
      compile_cache: execution.metadata.compile_cache ?? null,
      pine_compile_cache_hit: execution.metadata.pine_compile_cache_hit ?? false,
      pine_code_hash: execution.metadata.pine_code_hash ?? null,
      evidence_label: PINEFORGE_EVIDENCE_LABEL,
    });
    artifacts.pineforgeRunnerManifest = artifact(runBase, "pineforge_runner_manifest", "pineforge-runner-manifest.json", {
      engine: "pineforge",
      runner: execution.metadata.runner ?? "pineforge-runner",
      runner_stats: execution.metadata.runner_stats ?? null,
      artifact_manifest: execution.metadata.artifact_manifest ?? null,
    });
  }
  await mkdir(join(ARTIFACT_ROOT, runBase), { recursive: true });
  await Promise.all(Object.values(artifacts).map((item) => writeArtifactFile(item)));
  return artifacts;
}

function buildBacktestDashboard(job: RunJobRow, execution: BacktestExecutionResult) {
  const config = job.payload_json.backtest_config;
  const report = asRecord(execution.report);
  const reportMetrics = asRecord(report.metrics);
  const warnings = stringList(report.warnings);
  const initialCapital = config.initial_capital;
  const backtestDays = backtestDurationDays(config.start, config.end);
  const allStats = tradeSegmentStats(execution.trades, initialCapital, backtestDays, execution.equityCurve);
  const longStats = tradeSegmentStats(
    execution.trades.filter((trade) => tradeSideBucket(trade) === "long"),
    initialCapital,
    backtestDays,
  );
  const shortStats = tradeSegmentStats(
    execution.trades.filter((trade) => tradeSideBucket(trade) === "short"),
    initialCapital,
    backtestDays,
  );
  const equitySeries = sampleItems(
    execution.equityCurve.map((point) => ({
    index: point.index,
    timestamp: point.timestamp,
    equity: roundMetric(point.equity),
    pnl: roundMetric(point.equity - initialCapital),
    drawdown_pct: point.drawdown_pct,
    })),
    BACKTEST_DASHBOARD_EQUITY_POINTS,
  );
  const durationVsPnl = sampleItems(durationScatter(execution.trades, config), BACKTEST_DASHBOARD_SCATTER_POINTS);
  const logRows = tradeLogRows(execution.trades, config);
  return {
    kind: "backtest_dashboard",
    version: 1,
    limits: {
      equity_points: equitySeries.length,
      equity_points_total: execution.equityCurve.length,
      duration_points: durationVsPnl.length,
      duration_points_total: execution.trades.length,
      trades_log_rows: Math.min(logRows.length, BACKTEST_DASHBOARD_LOG_ROWS),
      trades_log_rows_total: logRows.length,
    },
    summary: {
      title: strategyTitle(job.payload_json.strategy_spec, config.symbol),
      symbol: config.symbol,
      timeframe: config.timeframe,
      candle_timeframe: config.candle_timeframe,
      engine: String(report.engine ?? "pineforge"),
      evidence_label: String(report.evidence_label ?? PINEFORGE_EVIDENCE_LABEL),
      execution_semantics: execution.executionSemantics,
      warnings,
      assumptions: {
        initial_capital: initialCapital,
        fee_bps: config.fee_bps,
        slippage_bps: config.slippage_bps,
        start: config.start,
        end: config.end,
        data_source: config.data_source,
        market_data_source: execution.manifest.market_data_source ?? null,
        source_feed_checksum: execution.manifest.source_feed_checksum ?? null,
      },
      quality_status: String(reportMetrics.quality_status ?? report.quality_status ?? "not_available"),
      quality_flags: stringList(reportMetrics.quality_flags).concat(stringList(report.quality_flags)),
      reproducibility_hash: stringOrNull(report.reproducibility_hash),
    },
    performance: {
      equity: equitySeries,
      daily_pnl: dailyPnlSeries(execution.trades),
      weekday_pnl: weekdayPnlSeries(execution.trades),
      kpis: {
        net_profit: allStats.net_profit,
        trades: allStats.closed_trades,
        win_rate: allStats.win_rate,
        winners: allStats.winning_trades,
        losers: allStats.losing_trades,
        max_drawdown: allStats.max_drawdown,
        max_drawdown_pct: allStats.max_drawdown_pct,
        profit_factor: allStats.profit_factor,
      },
      matrix: performanceMatrix(allStats, longStats, shortStats),
    },
    trades_analysis: {
      pnl_distribution: pnlDistribution(execution.trades),
      winrate: {
        winners: allStats.winning_trades,
        losers: allStats.losing_trades,
        win_rate: allStats.win_rate,
      },
      duration_vs_pnl: durationVsPnl,
      trade_stats: tradeStatsMatrix(allStats, longStats, shortStats),
      duration_stats: durationStatsMatrix(allStats, longStats, shortStats),
    },
    trades_log: {
      total_rows: logRows.length,
      rows: logRows.slice(0, BACKTEST_DASHBOARD_LOG_ROWS),
    },
  };
}

function buildBacktestPreviewSummary(job: RunJobRow, execution: BacktestExecutionResult) {
  const config = job.payload_json.backtest_config;
  const initialCapital = config.initial_capital;
  const backtestDays = backtestDurationDays(config.start, config.end);
  const allStats = tradeSegmentStats(execution.trades, initialCapital, backtestDays, execution.equityCurve);
  return {
    kind: "backtest_result",
    run_id: job.run_id,
    symbol: config.symbol,
    timeframe: config.timeframe,
    metrics: {
      net_pnl: allStats.net_profit,
      return_pct: initialCapital === 0 ? null : roundMetric((allStats.net_profit / initialCapital) * 100),
      max_drawdown: allStats.max_drawdown,
      max_drawdown_pct: allStats.max_drawdown_pct,
      win_rate: allStats.win_rate,
      winning_trades: allStats.winning_trades,
      losing_trades: allStats.losing_trades,
      trade_count: allStats.closed_trades,
      profit_factor: allStats.profit_factor,
    },
    equity_preview: sampleItems(
      execution.equityCurve.map((point) => ({
        index: point.index,
        timestamp: point.timestamp,
        equity: roundMetric(point.equity),
        pnl: roundMetric(point.equity - initialCapital),
      })),
      BACKTEST_INLINE_EQUITY_POINTS,
    ),
    generated_at: new Date().toISOString(),
  };
}

type TradeSegmentStats = {
  closed_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number | null;
  net_profit: number;
  gross_profit: number;
  gross_loss: number;
  profit_factor: number | null;
  cagr: number | null;
  average_pnl: number | null;
  average_pnl_per_day: number | null;
  average_pnl_per_week: number | null;
  average_winning_trade: number | null;
  average_losing_trade: number | null;
  largest_winning_trade: number | null;
  largest_losing_trade: number | null;
  max_drawdown: number;
  max_drawdown_pct: number;
  avg_trade_duration_bars: number | null;
  avg_winning_trade_duration_bars: number | null;
  avg_losing_trade_duration_bars: number | null;
  avg_trades_per_day: number | null;
  avg_trades_per_week: number | null;
  longest_trade_bars: number | null;
  shortest_trade_bars: number | null;
  longest_winning_streak: number;
  longest_losing_streak: number;
};

function tradeSegmentStats(
  trades: NormalizedTrade[],
  initialCapital: number,
  backtestDays: number,
  equityCurve?: EquityPoint[],
): TradeSegmentStats {
  const pnlValues = trades.map(tradePnl);
  const winners = pnlValues.filter((value) => value > 0);
  const losers = pnlValues.filter((value) => value < 0);
  const grossProfit = roundMetric(winners.reduce((sum, value) => sum + value, 0));
  const grossLoss = roundMetric(Math.abs(losers.reduce((sum, value) => sum + value, 0)));
  const netProfit = roundMetric(pnlValues.reduce((sum, value) => sum + value, 0));
  const durations = trades.map((trade) => trade.duration_bars).filter((value): value is number => value !== null);
  const winningDurations = trades
    .filter((trade) => tradePnl(trade) > 0)
    .map((trade) => trade.duration_bars)
    .filter((value): value is number => value !== null);
  const losingDurations = trades
    .filter((trade) => tradePnl(trade) < 0)
    .map((trade) => trade.duration_bars)
    .filter((value): value is number => value !== null);
  const drawdown = equityCurve?.length ? equityDrawdown(equityCurve, initialCapital) : tradeDrawdown(pnlValues, initialCapital);
  return {
    closed_trades: trades.length,
    winning_trades: winners.length,
    losing_trades: losers.length,
    win_rate: pnlValues.length === 0 ? null : roundMetric((winners.length / pnlValues.length) * 100),
    net_profit: netProfit,
    gross_profit: grossProfit,
    gross_loss: grossLoss,
    profit_factor: grossLoss === 0 ? null : roundMetric(grossProfit / grossLoss),
    cagr: cagrPercent(initialCapital, netProfit, backtestDays),
    average_pnl: average(pnlValues),
    average_pnl_per_day: backtestDays <= 0 ? null : roundMetric(netProfit / backtestDays),
    average_pnl_per_week: backtestDays <= 0 ? null : roundMetric(netProfit / (backtestDays / 7)),
    average_winning_trade: average(winners),
    average_losing_trade: average(losers),
    largest_winning_trade: winners.length ? roundMetric(Math.max(...winners)) : null,
    largest_losing_trade: losers.length ? roundMetric(Math.min(...losers)) : null,
    max_drawdown: drawdown.absolute,
    max_drawdown_pct: drawdown.percentage,
    avg_trade_duration_bars: average(durations),
    avg_winning_trade_duration_bars: average(winningDurations),
    avg_losing_trade_duration_bars: average(losingDurations),
    avg_trades_per_day: backtestDays <= 0 ? null : roundMetric(trades.length / backtestDays),
    avg_trades_per_week: backtestDays <= 0 ? null : roundMetric(trades.length / (backtestDays / 7)),
    longest_trade_bars: durations.length ? roundMetric(Math.max(...durations)) : null,
    shortest_trade_bars: durations.length ? roundMetric(Math.min(...durations)) : null,
    longest_winning_streak: longestStreak(pnlValues, (value) => value > 0),
    longest_losing_streak: longestStreak(pnlValues, (value) => value < 0),
  };
}

function performanceMatrix(all: TradeSegmentStats, long: TradeSegmentStats, short: TradeSegmentStats) {
  return [
    matrixRow("Net Profit", "currency", all.net_profit, long.net_profit, short.net_profit),
    matrixRow("CAGR", "percent", all.cagr, long.cagr, short.cagr),
    matrixRow("Gross Profit", "currency", all.gross_profit, long.gross_profit, short.gross_profit),
    matrixRow("Gross Loss", "currency", all.gross_loss, long.gross_loss, short.gross_loss),
    matrixRow("Profit Factor", "number", all.profit_factor, long.profit_factor, short.profit_factor),
    matrixRow("Average P&L per Day", "currency", all.average_pnl_per_day, long.average_pnl_per_day, short.average_pnl_per_day),
    matrixRow("Average P&L per Week", "currency", all.average_pnl_per_week, long.average_pnl_per_week, short.average_pnl_per_week),
    matrixRow("Drawdown", "drawdown", all.max_drawdown, long.max_drawdown, short.max_drawdown, {
      all: all.max_drawdown_pct,
      long: long.max_drawdown_pct,
      short: short.max_drawdown_pct,
    }),
  ];
}

function tradeStatsMatrix(all: TradeSegmentStats, long: TradeSegmentStats, short: TradeSegmentStats) {
  return [
    matrixRow("Closed Trades", "number", all.closed_trades, long.closed_trades, short.closed_trades),
    matrixRow("Winning Trades", "number", all.winning_trades, long.winning_trades, short.winning_trades),
    matrixRow("Losing Trades", "number", all.losing_trades, long.losing_trades, short.losing_trades),
    matrixRow("Win Rate", "percent", all.win_rate, long.win_rate, short.win_rate),
    matrixRow("Avg P&L", "currency", all.average_pnl, long.average_pnl, short.average_pnl),
    matrixRow("Avg Winning Trade", "currency", all.average_winning_trade, long.average_winning_trade, short.average_winning_trade),
    matrixRow("Avg Losing Trade", "currency", all.average_losing_trade, long.average_losing_trade, short.average_losing_trade),
    matrixRow("Largest Winning Trade", "currency", all.largest_winning_trade, long.largest_winning_trade, short.largest_winning_trade),
    matrixRow("Largest Losing Trade", "currency", all.largest_losing_trade, long.largest_losing_trade, short.largest_losing_trade),
  ];
}

function durationStatsMatrix(all: TradeSegmentStats, long: TradeSegmentStats, short: TradeSegmentStats) {
  return [
    matrixRow("Avg Trade Duration (bars)", "number", all.avg_trade_duration_bars, long.avg_trade_duration_bars, short.avg_trade_duration_bars),
    matrixRow(
      "Avg Winning Trade Duration (bars)",
      "number",
      all.avg_winning_trade_duration_bars,
      long.avg_winning_trade_duration_bars,
      short.avg_winning_trade_duration_bars,
    ),
    matrixRow(
      "Avg Losing Trade Duration (bars)",
      "number",
      all.avg_losing_trade_duration_bars,
      long.avg_losing_trade_duration_bars,
      short.avg_losing_trade_duration_bars,
    ),
    matrixRow("Avg Trades per Day", "number", all.avg_trades_per_day, long.avg_trades_per_day, short.avg_trades_per_day),
    matrixRow("Avg Trades per Week", "number", all.avg_trades_per_week, long.avg_trades_per_week, short.avg_trades_per_week),
    matrixRow("Longest Trade (bars)", "number", all.longest_trade_bars, long.longest_trade_bars, short.longest_trade_bars),
    matrixRow("Shortest Trade (bars)", "number", all.shortest_trade_bars, long.shortest_trade_bars, short.shortest_trade_bars),
    matrixRow("Longest Winning Streak (bars)", "number", all.longest_winning_streak, long.longest_winning_streak, short.longest_winning_streak),
    matrixRow("Longest Losing Streak (bars)", "number", all.longest_losing_streak, long.longest_losing_streak, short.longest_losing_streak),
  ];
}

function matrixRow(
  label: string,
  format: string,
  all: number | null,
  long: number | null,
  short: number | null,
  extra?: { all: number | null; long: number | null; short: number | null },
) {
  return { label, format, all, long, short, extra };
}

function pnlDistribution(trades: NormalizedTrade[]) {
  const values = trades.map(tradePnl);
  if (!values.length) {
    return { bins: [], references: { average_trade: null, average_winning_trade: null, average_losing_trade: null } };
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const binCount = Math.min(18, Math.max(8, Math.ceil(Math.sqrt(values.length))));
  const width = min === max ? 1 : (max - min) / binCount;
  const bins = Array.from({ length: min === max ? 1 : binCount }, (_, index) => {
    const start = min + width * index;
    const end = index === binCount - 1 ? max : start + width;
    return { index, start: roundMetric(start), end: roundMetric(end), profit: 0, loss: 0, count: 0 };
  });
  values.forEach((value) => {
    const index = min === max ? 0 : Math.min(bins.length - 1, Math.floor((value - min) / width));
    bins[index].count += 1;
    if (value >= 0) {
      bins[index].profit += 1;
    } else {
      bins[index].loss += 1;
    }
  });
  return {
    bins,
    references: {
      average_trade: average(values),
      average_winning_trade: average(values.filter((value) => value > 0)),
      average_losing_trade: average(values.filter((value) => value < 0)),
    },
  };
}

function dailyPnlSeries(trades: NormalizedTrade[]) {
  const groups = new Map<string, { date: string; profit: number; loss: number; pnl: number }>();
  trades.forEach((trade) => {
    const date = (trade.closed_at ?? trade.opened_at ?? "").slice(0, 10);
    if (!date) {
      return;
    }
    const pnl = tradePnl(trade);
    const current = groups.get(date) ?? { date, profit: 0, loss: 0, pnl: 0 };
    current.pnl = roundMetric(current.pnl + pnl);
    if (pnl >= 0) {
      current.profit = roundMetric(current.profit + pnl);
    } else {
      current.loss = roundMetric(current.loss + pnl);
    }
    groups.set(date, current);
  });
  return Array.from(groups.values()).sort((left, right) => left.date.localeCompare(right.date));
}

function weekdayPnlSeries(trades: NormalizedTrade[]) {
  const labels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const groups = labels.map((weekday, weekday_index) => ({ weekday, weekday_index, profit: 0, loss: 0, pnl: 0 }));
  trades.forEach((trade) => {
    const timestamp = Date.parse(trade.closed_at ?? trade.opened_at ?? "");
    if (!Number.isFinite(timestamp)) {
      return;
    }
    const pnl = tradePnl(trade);
    const current = groups[new Date(timestamp).getUTCDay()];
    current.pnl = roundMetric(current.pnl + pnl);
    if (pnl >= 0) {
      current.profit = roundMetric(current.profit + pnl);
    } else {
      current.loss = roundMetric(current.loss + pnl);
    }
  });
  return groups;
}

function sampleItems<T>(items: T[], maxItems: number): T[] {
  if (items.length <= maxItems || maxItems <= 0) {
    return items;
  }
  if (maxItems === 1) {
    return [items[items.length - 1]];
  }
  const step = (items.length - 1) / (maxItems - 1);
  return Array.from({ length: maxItems }, (_, index) => items[Math.round(index * step)]);
}

function durationScatter(trades: NormalizedTrade[], config: BacktestConfig) {
  return trades.map((trade, index) => ({
    trade_number: index + 1,
    side: tradeSideBucket(trade),
    duration_bars: durationBarsForTrade(trade, config),
    pnl: tradePnl(trade),
  }));
}

function tradeLogRows(trades: NormalizedTrade[], config: BacktestConfig) {
  let cumulativePnl = 0;
  return trades
    .map((trade, index) => {
      const pnl = tradePnl(trade);
      cumulativePnl = roundMetric(cumulativePnl + pnl);
      return {
        trade_number: index + 1,
        id: trade.id,
        side: tradeSideBucket(trade),
        entry: { timestamp: trade.opened_at, price: trade.entry_price },
        exit: { timestamp: trade.closed_at, price: trade.exit_price },
        net_pnl: pnl,
        cumulative_pnl: cumulativePnl,
        duration_bars: durationBarsForTrade(trade, config),
      };
    })
    .reverse();
}

function strategyTitle(strategySpec: unknown, fallbackSymbol: string) {
  const spec = asRecord(strategySpec);
  return (
    stringOrNull(spec.name) ??
    stringOrNull(spec.title) ??
    stringOrNull(spec.strategy_name) ??
    `${fallbackSymbol} strategy`
  );
}

function tradePnl(trade: NormalizedTrade) {
  return roundMetric(trade.pnl_cost ?? 0);
}

function tradeSideBucket(trade: NormalizedTrade): "long" | "short" | "unknown" {
  const side = trade.side?.toLowerCase() ?? "";
  if (side.includes("short")) {
    return "short";
  }
  if (side.includes("long")) {
    return "long";
  }
  return "unknown";
}

function durationBarsForTrade(trade: NormalizedTrade, config: BacktestConfig) {
  if (trade.duration_bars !== null) {
    return trade.duration_bars;
  }
  return durationFromBarsOrTime(
    trade.entry_bar_index,
    trade.exit_bar_index,
    trade.opened_at,
    trade.closed_at,
    executionCandleTimeframe(config),
  );
}

function durationFromBarsOrTime(
  entryBarIndex: number | null,
  exitBarIndex: number | null,
  openedAt: string | null,
  closedAt: string | null,
  timeframe: string,
) {
  if (entryBarIndex !== null && exitBarIndex !== null) {
    return Math.max(0, exitBarIndex - entryBarIndex);
  }
  const openedAtMs = Date.parse(openedAt ?? "");
  const closedAtMs = Date.parse(closedAt ?? "");
  const timeframeMs = timeframeToMs(timeframe);
  if (!Number.isFinite(openedAtMs) || !Number.isFinite(closedAtMs) || timeframeMs <= 0) {
    return null;
  }
  return roundMetric(Math.max(0, (closedAtMs - openedAtMs) / timeframeMs));
}

function backtestDurationDays(start: string, end: string) {
  const startMs = Date.parse(start);
  const endMs = Date.parse(end);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) {
    return 0;
  }
  return (endMs - startMs) / 86_400_000;
}

function cagrPercent(initialCapital: number, netProfit: number, days: number) {
  if (initialCapital <= 0 || days <= 0) {
    return null;
  }
  const endingCapital = initialCapital + netProfit;
  if (endingCapital <= 0) {
    return null;
  }
  return roundMetric((endingCapital / initialCapital) ** (365 / days) * 100 - 100);
}

function average(values: number[]) {
  return values.length ? roundMetric(values.reduce((sum, value) => sum + value, 0) / values.length) : null;
}

function longestStreak(values: number[], predicate: (value: number) => boolean) {
  let longest = 0;
  let current = 0;
  values.forEach((value) => {
    if (predicate(value)) {
      current += 1;
      longest = Math.max(longest, current);
    } else {
      current = 0;
    }
  });
  return longest;
}

function tradeDrawdown(pnlValues: number[], initialCapital: number) {
  let equity = initialCapital;
  let peak = initialCapital;
  let maxDrawdown = 0;
  pnlValues.forEach((pnl) => {
    equity += pnl;
    peak = Math.max(peak, equity);
    maxDrawdown = Math.max(maxDrawdown, peak - equity);
  });
  return {
    absolute: roundMetric(maxDrawdown),
    percentage: peak <= 0 ? 0 : roundMetric((maxDrawdown / peak) * 100),
  };
}

function equityDrawdown(equityCurve: EquityPoint[], initialCapital: number) {
  let peak = initialCapital;
  let maxDrawdown = 0;
  equityCurve.forEach((point) => {
    peak = Math.max(peak, point.equity);
    maxDrawdown = Math.max(maxDrawdown, peak - point.equity);
  });
  return {
    absolute: roundMetric(maxDrawdown),
    percentage: peak <= 0 ? 0 : roundMetric((maxDrawdown / peak) * 100),
  };
}

function artifact(
  runBase: string,
  kind: string,
  displayName: string,
  content: unknown,
  metadataJson?: Record<string, unknown>,
): ArtifactSpec {
  return {
    kind,
    mime_type: "application/json",
    display_name: displayName,
    storage_key: `${runBase}/${displayName}`,
    content,
    metadata_json: metadataJson,
  };
}

function textArtifact(runBase: string, kind: string, displayName: string, content: string): ArtifactSpec {
  return {
    kind,
    mime_type: "text/plain",
    display_name: displayName,
    storage_key: `${runBase}/${displayName}`,
    content,
  };
}

async function writeArtifactFile(artifact: ArtifactSpec) {
  const path = join(ARTIFACT_ROOT, artifact.storage_key);
  const content = serializeArtifactContent(artifact);
  const bytes = Buffer.byteLength(content, "utf8");
  if (bytes > MAX_ARTIFACT_BYTES) {
    throw new Error(`Backtest artifact size limit exceeded for ${artifact.display_name}: ${bytes} > ${MAX_ARTIFACT_BYTES}`);
  }
  await writeFile(path, content, "utf8");
}

function serializeArtifactContent(artifact: ArtifactSpec) {
  return artifact.mime_type === "application/json"
    ? `${JSON.stringify(artifact.content, null, 2)}\n`
    : `${String(artifact.content)}\n`;
}

function normalizeTrade(event: BacktestClosedEvent, fallbackSymbol: string, config: BacktestConfig): NormalizedTrade {
  const signal = event.signal ?? {};
  const eventRecord = asRecord(event);
  const rawPnlPercentage = numberOrNull(event.pnl?.pnlPercentage);
  const rawPnlCost = numberOrNull(event.pnl?.pnlCost);
  const cost = numberOrNull(event.pnl?.pnlEntries ?? signal.cost);
  const inferredCost = inferTradeCost(cost, rawPnlCost, rawPnlPercentage);
  const feeCost = inferredCost === null ? 0 : roundMetric(inferredCost * (config.fee_bps / 10_000) * 2);
  const slippageCost = inferredCost === null ? 0 : roundMetric(inferredCost * (config.slippage_bps / 10_000) * 2);
  const adjustedPnlCost = rawPnlCost === null ? null : roundMetric(rawPnlCost - feeCost - slippageCost);
  const adjustedPnlPercentage = adjustedTradePnlPercentage(rawPnlPercentage, adjustedPnlCost, inferredCost, config);
  const openedAt = timestampToIso(signal.openTimestamp ?? signal.createdAt ?? signal.openedAt);
  const closedAt = timestampToIso(event.closeTimestamp);
  const entryBarIndex = numberOrNull(signal.entry_bar_index ?? signal.entryBarIndex ?? signal.open_bar_index ?? signal.openBarIndex);
  const exitBarIndex = numberOrNull(
    eventRecord.exit_bar_index ?? eventRecord.exitBarIndex ?? eventRecord.close_bar_index ?? eventRecord.closeBarIndex,
  );
  return {
    id: stringOrNull(signal.id ?? signal.signalId),
    symbol: event.symbol ?? stringOrNull(signal.symbol) ?? fallbackSymbol,
    side: stringOrNull(signal.position),
    close_reason: event.closeReason ?? null,
    opened_at: openedAt,
    closed_at: closedAt,
    entry_bar_index: entryBarIndex,
    exit_bar_index: exitBarIndex,
    duration_bars: durationFromBarsOrTime(entryBarIndex, exitBarIndex, openedAt, closedAt, executionCandleTimeframe(config)),
    entry_price: numberOrNull(event.pnl?.priceOpen ?? signal.priceOpen),
    exit_price: numberOrNull(event.pnl?.priceClose ?? event.currentPrice),
    raw_pnl_percentage: rawPnlPercentage,
    raw_pnl_cost: rawPnlCost,
    pnl_percentage: adjustedPnlPercentage,
    pnl_cost: adjustedPnlCost,
    cost: inferredCost,
    fee_cost: feeCost,
    slippage_cost: slippageCost,
    cost_model: buildCostModel(config),
  };
}

function normalizeRunnerTrades(trades: RawBacktestTrade[], config: BacktestConfig): NormalizedTrade[] {
  return trades.map((trade, index) => {
    const entryPrice = numberOrNull(trade.entry_price ?? trade.entry);
    const exitPrice = numberOrNull(trade.exit_price ?? trade.exit);
    const qty = numberOrNull(trade.qty ?? trade.quantity ?? trade.contracts);
    const pointvalue = 1;
    const notionalCost = qty !== null && entryPrice !== null ? Math.abs(qty * entryPrice * pointvalue) : null;
    const rawPnlCost = numberOrNull(trade.pnl_cost ?? trade.pnl ?? trade.profit ?? trade.net_profit ?? trade.net_pnl);
    const rawPnlPercentage = numberOrNull(
      trade.pnl_percentage ?? trade.pnl_pct ?? trade.profit_percent ?? trade.net_profit_percent,
    );
    const inferredCost = inferTradeCost(numberOrNull(trade.cost) ?? notionalCost, rawPnlCost, rawPnlPercentage);
    const commission = numberOrNull(trade.commission ?? trade.fee_cost);
    const openedAt = timestampToIso(trade.opened_at ?? trade.entry_time ?? trade.entry_time_ms);
    const closedAt = timestampToIso(trade.closed_at ?? trade.exit_time ?? trade.exit_time_ms);
    const entryBarIndex = numberOrNull(trade.entry_bar_index ?? trade.entryBarIndex);
    const exitBarIndex = numberOrNull(trade.exit_bar_index ?? trade.exitBarIndex);
    return {
      id: stringOrNull(trade.id) ?? `pineforge-${index + 1}`,
      symbol: stringOrNull(trade.symbol) ?? config.symbol,
      side: stringOrNull(trade.side),
      close_reason: stringOrNull(trade.close_reason),
      opened_at: openedAt,
      closed_at: closedAt,
      entry_bar_index: entryBarIndex,
      exit_bar_index: exitBarIndex,
      duration_bars: durationFromBarsOrTime(entryBarIndex, exitBarIndex, openedAt, closedAt, executionCandleTimeframe(config)),
      entry_price: entryPrice,
      exit_price: exitPrice,
      raw_pnl_percentage: rawPnlPercentage,
      raw_pnl_cost: rawPnlCost,
      pnl_percentage: rawPnlPercentage,
      pnl_cost: rawPnlCost,
      cost: inferredCost,
      fee_cost: commission ?? 0,
      slippage_cost: numberOrNull(trade.slippage_cost) ?? 0,
      cost_model: {
        ...buildCostModel(config),
        basis: "round_trip_notional",
      },
      qty,
      commission,
      max_runup: numberOrNull(trade.max_runup),
      max_drawdown: numberOrNull(trade.max_drawdown),
    };
  });
}

function normalizeRunnerEquityCurve(points: RawEquityPoint[], initialCapital: number): EquityPoint[] {
  let previousEquity = initialCapital;
  let peak = initialCapital;
  return points.map((point, index) => {
    const equity = numberOrNull(point.equity ?? point.value) ?? previousEquity;
    const pnlCost = index === 0 ? equity - initialCapital : equity - previousEquity;
    peak = Math.max(peak, equity);
    const drawdownPct = peak <= 0 ? 0 : ((peak - equity) / peak) * 100;
    previousEquity = equity;
    return {
      index: numberOrNull(point.index) ?? index,
      timestamp: timestampToIso(point.timestamp ?? point.time ?? point.time_ms),
      equity,
      pnl_cost: roundMetric(pnlCost),
      drawdown_pct: roundMetric(drawdownPct),
    };
  });
}

function backtestQualityFlags(
  config: BacktestConfig,
  trades: NormalizedTrade[],
  metrics: BacktestMetrics,
  marketMetadata: CcxtMarketMetadata | null | undefined,
): { status: "pass" | "warn" | "fail"; flags: string[]; warnings: string[] } {
  const flags = new Set<string>();
  const warnings: string[] = [];
  const pointvalue = marketMetadata?.pointvalue ?? 1;
  const maxNotional = Math.max(
    0,
    ...trades.map((trade) => {
      const qty = trade.qty ?? null;
      const entryPrice = trade.entry_price ?? null;
      return qty !== null && entryPrice !== null ? Math.abs(qty * entryPrice * pointvalue) : 0;
    }),
  );
  const maxCommission = Math.max(0, ...trades.map((trade) => trade.commission ?? trade.fee_cost ?? 0));
  const notionalRatio = config.initial_capital > 0 ? maxNotional / config.initial_capital : 0;
  const commissionRatio = config.initial_capital > 0 ? maxCommission / config.initial_capital : 0;

  if (notionalRatio > 20) {
    flags.add("position_sizing_mismatch");
    warnings.push("Position sizing mismatch: preview trade notional is far larger than initial capital. Repair sizing before evaluating results.");
  } else if (notionalRatio > 5) {
    flags.add("large_trade_notional");
    warnings.push("Trade notional is large versus initial capital; review position sizing before using this preview.");
  }
  if (commissionRatio > 1) {
    flags.add("commission_exceeds_capital");
    warnings.push("Per-trade commission exceeds initial capital; this usually indicates invalid quantity sizing.");
  }
  if (metrics.pnl.percentage <= -100 || metrics.pnl.percentage >= 200) {
    flags.add("extreme_return");
  }

  const status = flags.has("position_sizing_mismatch") || flags.has("commission_exceeds_capital") ? "fail" : flags.size ? "warn" : "pass";
  return { status, flags: [...flags], warnings };
}

function buildCostModel(config: BacktestConfig): CostModel {
  return {
    version: BACKTEST_COST_MODEL_VERSION,
    fee_bps: config.fee_bps,
    slippage_bps: config.slippage_bps,
    applied_to_metrics: true,
    basis: "round_trip_notional",
  };
}

function inferTradeCost(cost: number | null, rawPnlCost: number | null, rawPnlPercentage: number | null): number | null {
  if (cost !== null && cost > 0) {
    return cost;
  }
  if (rawPnlCost !== null && rawPnlPercentage !== null && rawPnlPercentage !== 0) {
    return Math.abs(rawPnlCost / (rawPnlPercentage / 100));
  }
  return null;
}

function adjustedTradePnlPercentage(
  rawPnlPercentage: number | null,
  adjustedPnlCost: number | null,
  inferredCost: number | null,
  config: BacktestConfig,
): number | null {
  if (adjustedPnlCost !== null && inferredCost !== null && inferredCost > 0) {
    return roundMetric((adjustedPnlCost / inferredCost) * 100);
  }
  if (rawPnlPercentage === null) {
    return null;
  }
  const roundTripCostPct = ((config.fee_bps + config.slippage_bps) * 2) / 100;
  return roundMetric(rawPnlPercentage - roundTripCostPct);
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

function buildRobustnessReport(
  config: BacktestConfig,
  metrics: BacktestMetrics,
  trades: NormalizedTrade[],
): RobustnessReport {
  const warnings: string[] = [];
  const checks: RobustnessReport["checks"] = {};
  const backtestDays = Math.max(0, (Date.parse(config.end) - Date.parse(config.start)) / 86_400_000);
  const sampleSize = metrics.trade_count;
  const maxLossStreak = maxConsecutiveLosses(trades);
  const suspiciousMetricFlags = suspiciousMetricFlagsFor(metrics);
  const oosSplitAvailable = backtestDays >= 60 && sampleSize >= 20;

  if (sampleSize === 0) {
    checks.sample_size = { status: "fail", message: "No closed trades; preview cannot support a strategy review." };
  } else if (sampleSize < 20) {
    checks.sample_size = { status: "warn", message: "Low closed-trade sample; keep the result in manual review." };
  } else {
    checks.sample_size = { status: "pass", message: "Closed-trade sample is sufficient for a first local preview review." };
  }

  if (config.fee_bps === 0 && config.slippage_bps === 0) {
    checks.execution_costs = { status: "warn", message: "No fee or slippage stress was configured." };
  } else {
    checks.execution_costs = { status: "pass", message: "Fee or slippage assumptions are present in the preview config." };
  }

  if (metrics.max_drawdown >= 50) {
    checks.drawdown = { status: "fail", message: "Max drawdown is too high for promotion from a local preview." };
  } else if (metrics.max_drawdown >= 25) {
    checks.drawdown = { status: "warn", message: "Max drawdown needs manual risk review." };
  } else {
    checks.drawdown = { status: "pass", message: "Max drawdown did not trip the preview risk threshold." };
  }

  checks.loss_streak = maxLossStreak >= 5
    ? { status: "warn", message: "Loss streak needs manual risk and sizing review." }
    : { status: "pass", message: "Loss streak did not trip the preview risk threshold." };
  checks.oos_window = oosSplitAvailable
    ? { status: "pass", message: "Date range and sample can support an out-of-sample split." }
    : { status: "warn", message: "Preview does not have enough range and sample for out-of-sample review." };
  checks.parameter_sensitivity = {
    status: "not_available",
    message: "Parameter sensitivity requires a variant lab or follow-up preview run.",
  };
  checks.suspicious_metrics = suspiciousMetricFlags.length
    ? { status: "warn", message: `Suspicious preview metric flags: ${suspiciousMetricFlags.join(", ")}.` }
    : { status: "pass", message: "No suspicious preview metric flags were detected." };

  for (const [name, check] of Object.entries(checks)) {
    if (check.status === "warn" || check.status === "fail") {
      warnings.push(`${name}: ${check.message}`);
    }
  }
  const statuses = Object.values(checks).map((check) => check.status);
  const status = statuses.includes("fail") ? "fail" : statuses.includes("warn") ? "warn" : "pass";
  return {
    status,
    checks,
    warnings,
    metrics: {
      sample_size: sampleSize,
      backtest_days: roundMetric(backtestDays),
      max_drawdown_pct: metrics.max_drawdown,
      fee_bps: config.fee_bps,
      slippage_bps: config.slippage_bps,
      max_loss_streak: maxLossStreak,
      oos_split_available: oosSplitAvailable,
      parameter_sensitivity_available: checks.parameter_sensitivity.status !== "not_available",
      suspicious_metric_flags: suspiciousMetricFlags,
    },
  };
}

function buildPromotionDecision(robustnessReport: RobustnessReport): PromotionDecision {
  const failed = Object.entries(robustnessReport.checks)
    .filter(([, check]) => check.status === "fail")
    .map(([name, check]) => `${name}: ${check.message}`);
  if (failed.length) {
    return {
      decision: "reject",
      reasons: failed,
      boundary: "Local preview evidence only; reject does not imply future versions cannot be reviewed.",
    };
  }
  if (robustnessReport.status === "warn") {
    return {
      decision: "manual_review",
      reasons: robustnessReport.warnings,
      boundary: "Local preview evidence only; manual review is required before any further promotion.",
    };
  }
  return {
    decision: "research_candidate",
    reasons: ["Static preview and robustness checks did not raise blockers; this is not live-ready approval."],
    boundary: "Research candidate only; not TradingView proof, broker proof, profitability evidence, or live-ready certification.",
  };
}

function maxConsecutiveLosses(trades: NormalizedTrade[]): number {
  let current = 0;
  let longest = 0;
  for (const trade of trades) {
    if ((trade.pnl_percentage ?? 0) < 0 || (trade.pnl_cost ?? 0) < 0) {
      current += 1;
      longest = Math.max(longest, current);
    } else {
      current = 0;
    }
  }
  return longest;
}

function suspiciousMetricFlagsFor(metrics: BacktestMetrics): string[] {
  const flags: string[] = [];
  if (metrics.trade_count > 0 && metrics.trade_count < 50 && metrics.win_rate !== null && metrics.win_rate >= 90) {
    flags.push("high_win_rate_low_sample");
  }
  if (metrics.sharpe !== null && metrics.sharpe >= 5) {
    flags.push("extreme_sharpe");
  }
  if (metrics.sortino !== null && metrics.sortino >= 7) {
    flags.push("extreme_sortino");
  }
  if (metrics.pnl.percentage >= 200) {
    flags.push("extreme_return");
  }
  if (metrics.pnl.percentage <= -100) {
    flags.push("extreme_loss");
  }
  return flags;
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

function timeframeFromStepMs(stepMs: number): string {
  if (stepMs % 604_800_000 === 0) {
    return `${stepMs / 604_800_000}w`;
  }
  if (stepMs % 86_400_000 === 0) {
    return `${stepMs / 86_400_000}d`;
  }
  if (stepMs % 3_600_000 === 0) {
    return `${stepMs / 3_600_000}h`;
  }
  return `${Math.max(1, Math.trunc(stepMs / 60_000))}m`;
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
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
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

function checksumCandles(candles: Candle[]): string {
  const hash = createHash("sha256");
  for (const candle of candles) {
    hash.update(String(candle.timestamp));
    hash.update("|");
    hash.update(String(candle.open));
    hash.update("|");
    hash.update(String(candle.high));
    hash.update("|");
    hash.update(String(candle.low));
    hash.update("|");
    hash.update(String(candle.close));
    hash.update("|");
    hash.update(String(candle.volume));
    hash.update("\n");
  }
  return hash.digest("hex");
}

async function readPineCompileCache(pineCodeHash: string): Promise<Record<string, unknown> | null> {
  try {
    const raw = await readFile(join(ARTIFACT_ROOT, "cache", "pine-compile", `${pineCompileCacheKey(pineCodeHash)}.json`), "utf8");
    const parsed = JSON.parse(raw);
    return asRecord(parsed);
  } catch (error) {
    if (errorCode(error) === "ENOENT" || error instanceof SyntaxError) {
      return null;
    }
    throw error;
  }
}

async function writePineCompileCache(pineCodeHash: string, payload: Record<string, unknown>) {
  const dir = join(ARTIFACT_ROOT, "cache", "pine-compile");
  await mkdir(dir, { recursive: true });
  const path = join(dir, `${pineCompileCacheKey(pineCodeHash)}.json`);
  const tempPath = `${path}.${WORKER_ID}.${randomUUID()}.tmp`;
  await writeFile(tempPath, `${JSON.stringify(payload)}\n`, "utf8");
  await rename(tempPath, path);
}

function pineCompileCacheKey(pineCodeHash: string): string {
  return checksum({
    pine_code_hash: pineCodeHash,
    runner_version: PINEFORGE_RUNNER_CACHE_VERSION,
    engine_version: PINEFORGE_ENGINE_VERSION,
  });
}

async function acquireGlobalFetchSlot(deadlineMs: number, jobId: string): Promise<() => void> {
  while (activePublicFetches >= GLOBAL_FETCH_ACTIVE_LIMIT) {
    assertBeforeDeadline(deadlineMs, jobId);
    await sleep(50);
  }
  activePublicFetches += 1;
  return () => {
    activePublicFetches = Math.max(0, activePublicFetches - 1);
  };
}

async function throttleDataFetch(source: string, exchange: BacktestExchange, symbol: string, timeframe: string) {
  if (DATA_FETCH_THROTTLE_MS <= 0) {
    return;
  }
  const key = `${source}:${exchange}:${symbol}:${timeframe}`;
  const now = Date.now();
  maybePruneDataFetchThrottle(now);
  const previous = lastFetchAtByThrottleKey.get(key) ?? 0;
  const waitMs = previous + DATA_FETCH_THROTTLE_MS - now;
  if (waitMs > 0) {
    await sleep(waitMs);
  }
  lastFetchAtByThrottleKey.set(key, Date.now());
  if (lastFetchAtByThrottleKey.size > DATA_FETCH_THROTTLE_MAX_KEYS) {
    pruneDataFetchThrottle(Date.now());
  }
}

function maybePruneDataFetchThrottle(now: number) {
  if (lastFetchAtByThrottleKey.size > DATA_FETCH_THROTTLE_MAX_KEYS) {
    pruneDataFetchThrottle(now);
    return;
  }
  if (DATA_FETCH_THROTTLE_TTL_MS <= 0) {
    return;
  }
  const cadenceMs = Math.max(1_000, Math.floor(DATA_FETCH_THROTTLE_TTL_MS / 4));
  if (now - lastDataFetchThrottlePruneAt >= cadenceMs) {
    pruneDataFetchThrottle(now);
  }
}

function pruneDataFetchThrottle(now = Date.now()) {
  lastDataFetchThrottlePruneAt = now;
  if (DATA_FETCH_THROTTLE_TTL_MS > 0) {
    for (const [key, timestamp] of lastFetchAtByThrottleKey) {
      if (now - timestamp > DATA_FETCH_THROTTLE_TTL_MS) {
        lastFetchAtByThrottleKey.delete(key);
      }
    }
  }
  if (lastFetchAtByThrottleKey.size <= DATA_FETCH_THROTTLE_MAX_KEYS) {
    return;
  }
  const overflow = lastFetchAtByThrottleKey.size - DATA_FETCH_THROTTLE_MAX_KEYS;
  const oldest = [...lastFetchAtByThrottleKey.entries()].sort((a, b) => a[1] - b[1]).slice(0, overflow);
  for (const [key] of oldest) {
    lastFetchAtByThrottleKey.delete(key);
  }
}

async function withTimeout<T>(
  promise: Promise<T>,
  timeoutMs: number,
  jobId: string,
  diagnostics?: BacktestRuntimeDiagnostics,
): Promise<T> {
  let timer: NodeJS.Timeout | null = null;
  try {
    return await Promise.race([
      promise,
      new Promise<T>((_resolve, reject) => {
        timer = setTimeout(() => {
          reject(new BacktestWorkerTimeoutError(
            `Backtest worker timeout exceeded for ${jobId}: ${timeoutMs}ms`,
            diagnostics ?? backtestRuntimeDiagnosticsByJobId.get(jobId),
          ));
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
    throw new BacktestWorkerTimeoutError(
      `Backtest worker timeout exceeded for ${jobId}: ${WORKER_TIMEOUT_MS}ms`,
      backtestRuntimeDiagnosticsByJobId.get(jobId),
    );
  }
}

function runtimeDiagnosticsFromManifest(manifest: CandleCacheManifest): BacktestRuntimeDiagnostics {
  return {
    total_frames: manifest.total_frames,
    processed_frames: manifest.processed_frames,
    frames_per_second: manifest.frames_per_second,
    progress_events: manifest.progress_events,
    backtest_events: manifest.backtest_events,
    closed_events: manifest.closed_events,
    idle_events: manifest.idle_events,
    active_events: manifest.active_events,
    get_candles_calls: manifest.get_candles_calls,
    get_signal_calls: manifest.get_signal_calls,
    signal_evaluations: manifest.signal_evaluations,
    backtest_run_ms: manifest.backtest_run_ms,
  };
}

function backtestWorkerErrorDiagnostics(error: unknown): BacktestRuntimeDiagnostics | undefined {
  if (error instanceof BacktestWorkerTimeoutError) {
    return error.diagnostics;
  }
  const diagnostics = asRecord(error).diagnostics;
  return isBacktestRuntimeDiagnostics(diagnostics) ? diagnostics : undefined;
}

function isBacktestRuntimeDiagnostics(value: unknown): value is BacktestRuntimeDiagnostics {
  const record = asRecord(value);
  return Number.isFinite(record.processed_frames)
    && Number.isFinite(record.total_frames)
    && Number.isFinite(record.backtest_run_ms);
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

function classifyBacktestWorkerError(error: unknown): string {
  if (error instanceof LocalPreviewError) {
    return error.previewErrorCode;
  }
  const name = error instanceof Error ? error.name : "";
  const message = error instanceof Error ? error.message : String(error);
  const text = `${name} ${message}`;
  if (/timeout exceeded|child timeout/i.test(text)) {
    return "backtest_worker_timeout";
  }
  if (/ScopeContextError|ContextReferer|child crashed/i.test(text)) {
    return "backtest_worker_crash";
  }
  return "worker_error";
}

function publicBacktestFailurePayload(job: RunJobRow, errorCode: string, message: string) {
  if (isPreviewErrorCode(errorCode)) {
    return publicPreviewFailurePayload(job, errorCode);
  }
  return {
    preview_error_code: "preview_execution_error" satisfies PreviewErrorCode,
    repair_attempts: compatibilityRepairAttempts(job),
    compatibility_repair_applied: compatibilityRepairAttempts(job) > 0,
    manual_validation_required: false,
    message: sanitizePreviewFailureText(message),
  };
}

function isPreviewErrorCode(value: string): value is PreviewErrorCode {
  return (
    value === "preview_compatibility_limit"
    || value === "preview_runtime_unavailable"
    || value === "preview_data_error"
    || value === "preview_execution_error"
  );
}

function sanitizePreviewFailureText(message: string): string {
  if (/pineforge|runner|engine|compile|transpile/i.test(message)) {
    return "Local preview failed before it could produce review evidence.";
  }
  return message;
}

function internalFailureResult(message: string, diagnostics?: BacktestRuntimeDiagnostics, error?: unknown) {
  const result: Record<string, unknown> = diagnostics === undefined ? { message } : { message, diagnostics };
  if (error instanceof LocalPreviewError) {
    result.internal_diagnostics = {
      raw_runtime_error_code: error.rawCode ?? null,
      raw_runtime_message: error.rawMessage ?? null,
      compile_stage: error.compileStage ?? null,
      raw_runtime_diagnostics: error.rawDiagnostics ?? null,
    };
  }
  return result;
}

async function markJobFailed(
  client: pg.Client,
  job: RunJobRow,
  errorCode: string,
  message: string,
  diagnostics?: BacktestRuntimeDiagnostics,
  error?: unknown,
) {
  const run = await getRun(client, job.run_id);
  const result = internalFailureResult(message, diagnostics, error);
  const publicPayload = publicBacktestFailurePayload(job, errorCode, message);
  if (job.attempts < job.max_attempts) {
    await requeueJob(
      client,
      job.id,
      { ...result, retrying: true, attempt: job.attempts, max_attempts: job.max_attempts },
      errorCode,
    );
    await setRunStatus(client, run, "queued");
    await appendEvent(client, run, BACKTEST_RUN_EVENTS.failed, {
      job_id: job.id,
      error_code: errorCode,
      ...publicPayload,
      retrying: true,
      attempt: job.attempts,
      max_attempts: job.max_attempts,
    });
    await appendBacktestHeartbeat(client, run, {
      job_id: job.id,
      stage: "failed",
      status: "queued",
      progress_pct: 0,
      message: "Backtest preview failed and will retry.",
    });
    return;
  }
  await completeJob(client, job.id, "failed", result, errorCode);
  await setRunStatus(client, run, "failed", errorCode);
  await appendEvent(client, run, BACKTEST_RUN_EVENTS.failed, {
    job_id: job.id,
    error_code: errorCode,
    ...publicPayload,
    retrying: false,
    attempt: job.attempts,
    max_attempts: job.max_attempts,
  });
  await appendBacktestHeartbeat(client, run, {
    job_id: job.id,
    stage: "failed",
    status: "failed",
    progress_pct: 0,
    message: publicPayload.message,
  });
  await appendEvent(client, run, "run.failed", { error: errorCode, ...publicPayload, mode: "backtest-preview" });
  await enqueuePreviewCompatibilityRepairJob(client, run, job, errorCode, result, publicPayload);
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

function reproducibilityHashFor(job: RunJobRow, execution: BacktestExecutionResult): string {
  return createHash("sha256")
    .update(JSON.stringify({
      engine: job.payload_json.backtest_config.engine,
      config: job.payload_json.backtest_config,
      manifest_checksum: execution.manifest.checksum,
      source_checksum: createHash("sha256").update(JSON.stringify(execution.sourceBundle)).digest("hex"),
    }))
    .digest("hex");
}

function indexedTrades(trades: NormalizedTrade[]): Array<{ bucket: string; trade: NormalizedTrade }> {
  const ranked = trades
    .map((trade, index) => ({ trade, index, pnl: trade.pnl_cost ?? 0 }))
    .sort((a, b) => a.pnl - b.pnl);
  const selected = new Map<number, { bucket: string; trade: NormalizedTrade }>();
  for (const item of ranked.slice(0, 25)) {
    selected.set(item.index, { bucket: "top_loser", trade: item.trade });
  }
  for (const item of ranked.slice(-25).reverse()) {
    selected.set(item.index, { bucket: "top_winner", trade: item.trade });
  }
  for (const item of trades.slice(0, 50).map((trade, index) => ({ trade, index }))) {
    if (!selected.has(item.index)) {
      selected.set(item.index, { bucket: "sample", trade: item.trade });
    }
  }
  return [...selected.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([, value]) => value);
}

function downsampleEquity(points: EquityPoint[], maxPoints: number): EquityPoint[] {
  if (points.length <= maxPoints || maxPoints <= 0) {
    return points;
  }
  const step = (points.length - 1) / Math.max(1, maxPoints - 1);
  const sampled: EquityPoint[] = [];
  for (let index = 0; index < maxPoints; index += 1) {
    sampled.push(points[Math.round(index * step)]);
  }
  return sampled;
}

function drawdownWindows(points: EquityPoint[]): Array<Record<string, unknown>> {
  const worst = [...points].sort((a, b) => b.drawdown_pct - a.drawdown_pct).slice(0, 10);
  return worst.map((point) => ({
    timestamp: normalizedTimestamp(point.timestamp),
    equity: point.equity,
    drawdown_pct: point.drawdown_pct,
  }));
}

function monthlyReturns(points: EquityPoint[]): Array<Record<string, unknown>> {
  const byMonth = new Map<string, { first: EquityPoint; last: EquityPoint }>();
  for (const point of points) {
    const timestamp = normalizedTimestamp(point.timestamp);
    if (!timestamp) {
      continue;
    }
    const month = timestamp.slice(0, 7);
    const existing = byMonth.get(month);
    byMonth.set(month, { first: existing?.first ?? point, last: point });
  }
  return [...byMonth.entries()].map(([month, value]) => ({
    month,
    pnl_cost: roundMetric(value.last.equity - value.first.equity),
    return_pct: value.first.equity === 0 ? 0 : roundMetric(((value.last.equity - value.first.equity) / value.first.equity) * 100),
  }));
}

function normalizedTimestamp(value: unknown): string | null {
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    const milliseconds = Math.abs(value) < 1_000_000_000_000 ? value * 1000 : value;
    const date = new Date(milliseconds);
    return Number.isNaN(date.getTime()) ? null : date.toISOString();
  }
  return null;
}

async function persistBacktestIndexes(
  client: pg.Client,
  run: AssistantRunRow,
  job: RunJobRow,
  execution: BacktestExecutionResult & { metrics: BacktestMetrics },
  artifacts: Record<string, ArtifactSpec>,
): Promise<number> {
  const report = execution.report;
  const config = job.payload_json.backtest_config;
  const metricsForPersistence = asRecord(report.metrics);
  const evidenceLabel = String(report.evidence_label ?? PINEFORGE_EVIDENCE_LABEL);
  const warnings = Array.isArray(report.warnings) ? report.warnings : [];
  const assumptions = Array.isArray(report.assumptions) ? report.assumptions : [];
  const reproducibilityHash = reproducibilityHashFor(job, execution);
  const selectedTrades = indexedTrades(execution.trades);
  const equityPoints = downsampleEquity(execution.equityCurve, PINEFORGE_EQUITY_DOWNSAMPLE_POINTS);
  const runnerStats = asRecord(execution.metadata.runner_stats);
  const artifactManifest = {
    report: artifacts.report.storage_key,
    trades: artifacts.trades.storage_key,
    equity_curve: artifacts.equityCurve.storage_key,
    cache_manifest: artifacts.cacheManifest.storage_key,
    runner_manifest: execution.metadata.artifact_manifest ?? null,
  };
  let rowsWritten = 0;

  await client.query("BEGIN");
  try {
    await client.query(
      `
      INSERT INTO backtest_reports (
        id, run_id, owner_user_id, workspace_id, engine, evidence_label, execution_semantics,
        symbol, signal_timeframe, candle_timeframe, metrics_json, assumptions_json, warnings_json,
        reproducibility_hash, created_at
      )
      VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::json,$12::json,$13::json,$14,now())
      ON CONFLICT (run_id) DO UPDATE SET
        engine = EXCLUDED.engine,
        evidence_label = EXCLUDED.evidence_label,
        execution_semantics = EXCLUDED.execution_semantics,
        symbol = EXCLUDED.symbol,
        signal_timeframe = EXCLUDED.signal_timeframe,
        candle_timeframe = EXCLUDED.candle_timeframe,
        metrics_json = EXCLUDED.metrics_json,
        assumptions_json = EXCLUDED.assumptions_json,
        warnings_json = EXCLUDED.warnings_json,
        reproducibility_hash = EXCLUDED.reproducibility_hash
      `,
      [
        opaqueId("btr"),
        run.id,
        run.owner_user_id,
        run.workspace_id,
        config.engine,
        evidenceLabel,
        execution.executionSemantics,
        config.symbol,
        config.timeframe,
        execution.manifest.candle_timeframe ?? execution.manifest.timeframe,
        jsonParam(Object.keys(metricsForPersistence).length ? metricsForPersistence : execution.metrics),
        jsonParam(assumptions),
        jsonParam(warnings),
        reproducibilityHash,
      ],
    );
    rowsWritten += 1;
    await client.query("DELETE FROM backtest_trade_index WHERE run_id = $1", [run.id]);
    if (selectedTrades.length > 0) {
      const values: unknown[] = [];
      const placeholders = selectedTrades.map((item, index) => {
        const offset = index * 11;
        values.push(
          opaqueId("btti"),
          run.id,
          run.owner_user_id,
          run.workspace_id,
          index + 1,
          item.bucket,
          item.trade.opened_at,
          item.trade.closed_at,
          item.trade.pnl_cost,
          item.trade.pnl_percentage,
          jsonParam(item.trade),
        );
        return `($${offset + 1},$${offset + 2},$${offset + 3},$${offset + 4},$${offset + 5},$${offset + 6},$${offset + 7},$${offset + 8},$${offset + 9},$${offset + 10},$${offset + 11}::json,now())`;
      });
      await client.query(
        `
        INSERT INTO backtest_trade_index (
          id, run_id, owner_user_id, workspace_id, trade_rank, bucket, opened_at, closed_at,
          pnl_cost, pnl_percentage, payload_json, created_at
        )
        VALUES ${placeholders.join(", ")}
        `,
        values,
      );
      rowsWritten += selectedTrades.length;
    }
    await client.query(
      `
      INSERT INTO backtest_equity_summary (
        id, run_id, owner_user_id, workspace_id, sample_resolution, points_json,
        drawdown_windows_json, monthly_returns_json, created_at
      )
      VALUES ($1,$2,$3,$4,$5,$6::json,$7::json,$8::json,now())
      ON CONFLICT (run_id) DO UPDATE SET
        sample_resolution = EXCLUDED.sample_resolution,
        points_json = EXCLUDED.points_json,
        drawdown_windows_json = EXCLUDED.drawdown_windows_json,
        monthly_returns_json = EXCLUDED.monthly_returns_json
      `,
      [
        opaqueId("btes"),
        run.id,
        run.owner_user_id,
        run.workspace_id,
        equityPoints.length < execution.equityCurve.length ? "downsampled" : "full",
        jsonParam(equityPoints),
        jsonParam(drawdownWindows(execution.equityCurve)),
        jsonParam(monthlyReturns(execution.equityCurve)),
      ],
    );
    rowsWritten += 1;
    await client.query(
      `
      INSERT INTO backtest_runner_stats (
        id, run_id, owner_user_id, workspace_id, runner, runner_version, bars_processed,
        compile_ms, run_ms, output_bytes, artifact_manifest_json, created_at
      )
      VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::json,now())
      ON CONFLICT (run_id) DO UPDATE SET
        runner = EXCLUDED.runner,
        runner_version = EXCLUDED.runner_version,
        bars_processed = EXCLUDED.bars_processed,
        compile_ms = EXCLUDED.compile_ms,
        run_ms = EXCLUDED.run_ms,
        output_bytes = EXCLUDED.output_bytes,
        artifact_manifest_json = EXCLUDED.artifact_manifest_json
      `,
      [
        opaqueId("btrs"),
        run.id,
        run.owner_user_id,
        run.workspace_id,
        String(execution.metadata.runner ?? "pineforge-runner"),
        stringOrNull(execution.metadata.engine_version),
        numberValue(runnerStats.bars_processed, execution.manifest.processed_frames),
        numberValue(runnerStats.compile_ms, 0),
        numberValue(runnerStats.run_ms, execution.manifest.backtest_run_ms),
        numberValue(runnerStats.output_bytes, 0),
        jsonParam(artifactManifest),
      ],
    );
    rowsWritten += 1;
    await client.query("COMMIT");
    return rowsWritten;
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  }
}

function jsonParam(value: unknown) {
  return JSON.stringify(value ?? null);
}

async function persistArtifacts(client: pg.Client, run: AssistantRunRow, artifacts: Record<string, ArtifactSpec>, evidenceLabel: string) {
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
          source: "backtest-worker",
          evidence_label: evidenceLabel,
          ...(artifact.metadata_json ?? {}),
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

async function enqueueChatBacktestSummaryJob(
  client: pg.Client,
  run: AssistantRunRow,
  job: RunJobRow,
  metrics: BacktestMetrics,
) {
  const autoChain = job.payload_json.auto_chain;
  if (autoChain?.summary_on_complete !== true) {
    return;
  }
  await client.query(
    `
    INSERT INTO run_jobs (
      id, run_id, owner_user_id, workspace_id, job_type, status, payload_json,
      attempts, max_attempts, lease_owner, leased_until, result_json, error_code, created_at, updated_at
    )
    SELECT
      $1::text, $2::text, $3::text, $4::text, $5::text, 'queued', $6::json,
      0, 2, NULL, NULL, NULL, NULL, now(), now()
    WHERE NOT EXISTS (
      SELECT 1
      FROM run_jobs
      WHERE job_type = $5::text
        AND run_id = $2::text
        AND payload_json #>> '{backtest_run_id}' = $2::text
        AND status IN ('queued', 'running', 'completed')
    )
    `,
    [
      opaqueId("job"),
      run.id,
      run.owner_user_id,
      run.workspace_id,
      CHAT_BACKTEST_SUMMARY_JOB_TYPE,
      jsonParam({
        backtest_run_id: run.id,
        source_run_id: typeof autoChain.source_run_id === "string" ? autoChain.source_run_id : null,
        conversation_id: typeof autoChain.conversation_id === "string" ? autoChain.conversation_id : run.conversation_id,
        summary_on_complete: true,
        metrics_hint: {
          trade_count: metrics.trade_count,
          pnl: metrics.pnl,
          max_drawdown: metrics.max_drawdown,
        },
      }),
    ],
  );
}

async function enqueuePreviewCompatibilityRepairJob(
  client: pg.Client,
  run: AssistantRunRow,
  job: RunJobRow,
  errorCode: string,
  internalResult: Record<string, unknown>,
  publicPayload: ReturnType<typeof publicBacktestFailurePayload>,
) {
  if (errorCode !== "preview_compatibility_limit") {
    return;
  }
  if (compatibilityRepairAttempts(job) >= PREVIEW_COMPATIBILITY_REPAIR_MAX_ATTEMPTS) {
    return;
  }
  const pineCode = typeof job.payload_json.pine_code === "string" ? job.payload_json.pine_code.trim() : "";
  if (!pineCode) {
    return;
  }
  const autoChain = job.payload_json.auto_chain;
  const sourceRunId = typeof autoChain?.source_run_id === "string" ? autoChain.source_run_id : run.id;
  const conversationId = typeof autoChain?.conversation_id === "string" ? autoChain.conversation_id : run.conversation_id;
  const nextAttempt = compatibilityRepairAttempts(job) + 1;
  await client.query(
    `
    INSERT INTO run_jobs (
      id, run_id, owner_user_id, workspace_id, job_type, status, payload_json,
      attempts, max_attempts, lease_owner, leased_until, result_json, error_code, created_at, updated_at
    )
    SELECT
      $1::text, $2::text, $3::text, $4::text, $5::text, 'queued', $6::json,
      0, 2, NULL, NULL, NULL, NULL, now(), now()
    WHERE NOT EXISTS (
      SELECT 1
      FROM run_jobs
      WHERE job_type = $5::text
        AND payload_json #>> '{failed_job_id}' = $7::text
        AND payload_json #>> '{compatibility_repair,attempt}' = $8::text
        AND status IN ('queued', 'running', 'completed')
    )
    `,
    [
      opaqueId("job"),
      run.id,
      run.owner_user_id,
      run.workspace_id,
      PREVIEW_COMPATIBILITY_REPAIR_JOB_TYPE,
      jsonParam({
        failed_backtest_run_id: run.id,
        failed_job_id: job.id,
        source_run_id: sourceRunId,
        conversation_id: conversationId,
        preview_error_code: "preview_compatibility_limit",
        public_message: publicPayload.message,
        strategy_spec: job.payload_json.strategy_spec,
        pine_code: pineCode,
        backtest_config: job.payload_json.backtest_config,
        runtime: job.payload_json.runtime,
        limits: job.payload_json.limits ?? null,
        auto_chain: {
          summary_on_complete: autoChain?.summary_on_complete === true,
          source_run_id: sourceRunId,
          conversation_id: conversationId,
        },
        compatibility_repair: {
          attempt: nextAttempt,
          max_attempts: PREVIEW_COMPATIBILITY_REPAIR_MAX_ATTEMPTS,
          source: "local_preview_failure",
          source_run_id: sourceRunId,
          failed_run_id: run.id,
          failed_job_id: job.id,
        },
        internal_diagnostics: internalResult.internal_diagnostics ?? null,
      }),
      job.id,
      String(nextAttempt),
    ],
  );
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
  BacktestProgressEmitter,
  BACKTEST_RUN_EVENTS,
  aggregateCandles,
  aggregateClosedSignalCandles,
  buildStrategyAdapter,
  buildIndexedCandleStore,
  buildCostModel,
  buildBacktestDashboard,
  buildBacktestPreviewSummary,
  buildEquityCurve,
  buildMetrics,
  buildPromotionDecision,
  buildRobustnessReport,
  capCandleRequestLimit,
  candleCacheLockKey,
  candleCacheStorageKey,
  cacheFetchWindows,
  checksumCandles,
  ccxtSymbolCandidates,
  createOhlcvProvider,
  ccxtMarketMetadataFromMarket,
  emaSeries,
  validateOhlcvFeed,
  getOrFillPublicCandleCache,
  getOrFillPublicCandleCacheV1,
  getOrFillPublicCandleCacheV2,
  isSignalDecisionBoundary,
  lastFetchAtByThrottleKey,
  pruneDataFetchThrottle,
  readRangeCacheCoverage,
  readRangeCacheIndex,
  rangeCacheDataset,
  requestedRange,
  selectRangeCacheSegments,
  throttleDataFetch,
  upsertRangeCacheSegment,
  normalizeTrade,
  normalizeRunnerEquityCurve,
  normalizeRunnerTrades,
  backtestQualityFlags,
  normalizedConfig,
  parseDefaultExchange,
  preloadExecutionCandles,
  readCachedPublicCandles,
  rsiSeries,
  sliceIndexedCandles,
  sliceCandles,
  writeRangeCacheSegment,
  writeCandlesCsv,
  writeCachedPublicCandles,
  backtestWorkerErrorDiagnostics,
  classifyBacktestWorkerError,
  classifyLocalPreviewFailure,
  publicPreviewFailureMessage,
  previewCompletionPayload,
  sanitizePreviewFailureText,
  parsePineForgeCommandOutput,
  enqueuePreviewCompatibilityRepairJob,
  runPineForgeCommand,
  runPineForgePreview,
  runtimeDiagnosticsFromManifest,
  resolveCcxtSymbol,
  resolveCcxtMarketMetadata,
  withTimeout,
};

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error: unknown) => {
    const message = error instanceof Error ? error.stack ?? error.message : String(error);
    console.error(JSON.stringify({ level: "error", message }));
    process.exit(1);
  });
}
