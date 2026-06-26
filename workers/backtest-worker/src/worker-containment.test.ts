import assert from "node:assert/strict";
import test from "node:test";

const { __test } = await import("./index.js");

const pineForgeJob = {
  id: "job_test",
  run_id: "run_test",
  owner_user_id: "user_test",
  workspace_id: "workspace_test",
  job_type: "backtest-preview",
  status: "running",
  payload_json: {
    strategy_spec: {},
    pine_code: "//@version=6\nstrategy(\"POC\")\nstrategy.entry(\"Long\", strategy.long)\nstrategy.close(\"Long\")\n",
    backtest_config: {
      engine: "pineforge" as const,
      symbol: "BTCUSDT",
      timeframe: "1h",
      candle_timeframe: "1m",
      start: "2024-01-01T00:00:00.000Z",
      end: "2024-01-02T00:00:00.000Z",
      initial_capital: 10000,
      fee_bps: 10,
      slippage_bps: 5,
      data_source: "public-readonly-cache" as const,
    },
    runtime: { engine: "pineforge" as const, allowed_api: ["pineforge-runner", "pineforge-engine-native"], blocked_api: [] },
  },
  attempts: 1,
  max_attempts: 1,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

test("backtest worker timeout carries runtime diagnostics", async () => {
  const diagnostics = {
    total_frames: 10,
    processed_frames: 4,
    frames_per_second: 20,
    progress_events: 2,
    backtest_events: 1,
    closed_events: 0,
    idle_events: 1,
    active_events: 0,
    get_candles_calls: 3,
    get_signal_calls: 4,
    signal_evaluations: 1,
    backtest_run_ms: 200,
  };

  await assert.rejects(
    __test.withTimeout(new Promise(() => undefined), 10, "job_test", diagnostics),
    (error: unknown) => {
      assert.equal(__test.classifyBacktestWorkerError(error), "backtest_worker_timeout");
      assert.deepEqual(__test.backtestWorkerErrorDiagnostics(error), diagnostics);
      return true;
    },
  );
});

test("pineforge preview rejects before subprocess when disabled", async () => {
  await assert.rejects(
    __test.runPineForgePreview(pineForgeJob, Date.now() + 10_000),
    /Local preview is disabled/,
  );
});

test("local preview failure classifier separates compatibility, runtime, and data errors", () => {
  assert.equal(
    __test.classifyLocalPreviewFailure(
      "pineforge_compile_failed",
      "Unknown function ta.unsupportedIndicator",
      { compile: { stage: "transpile" } },
    ),
    "preview_compatibility_limit",
  );
  assert.equal(
    __test.classifyLocalPreviewFailure("pineforge_runner_unavailable", "fetch failed"),
    "preview_runtime_unavailable",
  );
  assert.equal(
    __test.classifyLocalPreviewFailure("pineforge_max_bars_exceeded", "bars exceed max_bars"),
    "preview_data_error",
  );
});

test("local preview public messages hide implementation names", () => {
  assert.equal(
    __test.publicPreviewFailureMessage("preview_compatibility_limit"),
    "Local preview cannot run part of this script yet. The Pine code may still require manual platform validation.",
  );
  assert.equal(
    __test.sanitizePreviewFailureText("pineforge runner compile failed"),
    "Local preview failed before it could produce review evidence.",
  );
});

test("repaired preview completion uses compatibility success copy", () => {
  assert.deepEqual(__test.previewCompletionPayload(pineForgeJob), {});
  assert.deepEqual(
    __test.previewCompletionPayload({
      ...pineForgeJob,
      payload_json: {
        ...pineForgeJob.payload_json,
        compatibility_repair: { attempt: 1, max_attempts: 2, source: "local_preview_failure" },
      },
    }),
    {
      preview_error_code: "preview_compatibility_limit",
      repair_attempts: 1,
      compatibility_repair_applied: true,
      manual_validation_required: false,
      message: "Preview completed after compatibility repair.",
    },
  );
});

test("compatibility failure enqueues bounded repair job", async () => {
  const queries: Array<{ sql: string; params: unknown[] }> = [];
  const client = {
    async query(sql: string, params: unknown[]) {
      queries.push({ sql, params });
      return { rows: [], rowCount: 1 };
    },
  };
  const run = {
    id: "run_failed",
    conversation_id: "conversation_test",
    owner_user_id: "user_test",
    workspace_id: "workspace_test",
    status: "failed",
    request_id: null,
    trace_id: null,
  };
  const job = {
    ...pineForgeJob,
    payload_json: {
      ...pineForgeJob.payload_json,
      strategy_spec: { script_type: "strategy" },
      auto_chain: { summary_on_complete: true, source_run_id: "run_parent", conversation_id: "conversation_test" },
    },
  };

  await __test.enqueuePreviewCompatibilityRepairJob(
    client as never,
    run,
    job,
    "preview_compatibility_limit",
    { internal_diagnostics: { raw_runtime_message: "compile failed" } },
    {
      preview_error_code: "preview_compatibility_limit",
      repair_attempts: 0,
      compatibility_repair_applied: false,
      manual_validation_required: true,
      message: "Local preview cannot run part of this script yet. The Pine code may still require manual platform validation.",
    },
  );

  assert.equal(queries.length, 1);
  assert.equal(queries[0].params[4], "preview-compatibility-repair");
  const payload = JSON.parse(String(queries[0].params[5]));
  assert.equal(payload.failed_backtest_run_id, "run_failed");
  assert.equal(payload.failed_job_id, "job_test");
  assert.equal(payload.compatibility_repair.attempt, 1);
  assert.equal(payload.compatibility_repair.max_attempts, 2);
  assert.equal(payload.auto_chain.source_run_id, "run_parent");
  assert.equal(payload.internal_diagnostics.raw_runtime_message, "compile failed");
});

test("non-compatibility failures do not enqueue repair jobs", async () => {
  const client = {
    async query() {
      throw new Error("query should not be called");
    },
  };
  await __test.enqueuePreviewCompatibilityRepairJob(
    client as never,
    {
      id: "run_failed",
      conversation_id: "conversation_test",
      owner_user_id: "user_test",
      workspace_id: "workspace_test",
      status: "failed",
      request_id: null,
      trace_id: null,
    },
    pineForgeJob,
    "preview_data_error",
    {},
    {
      preview_error_code: "preview_data_error",
      repair_attempts: 0,
      compatibility_repair_applied: false,
      manual_validation_required: false,
      message: "Local preview could not prepare the requested market data. Review the symbol, exchange, timeframe, and date range.",
    },
  );
});

test("compatibility repair enqueue respects max attempts", async () => {
  const client = {
    async query() {
      throw new Error("query should not be called");
    },
  };
  await __test.enqueuePreviewCompatibilityRepairJob(
    client as never,
    {
      id: "run_failed",
      conversation_id: "conversation_test",
      owner_user_id: "user_test",
      workspace_id: "workspace_test",
      status: "failed",
      request_id: null,
      trace_id: null,
    },
    {
      ...pineForgeJob,
      payload_json: {
        ...pineForgeJob.payload_json,
        compatibility_repair: { attempt: 2, max_attempts: 2, source: "local_preview_failure" },
      },
    },
    "preview_compatibility_limit",
    {},
    {
      preview_error_code: "preview_compatibility_limit",
      repair_attempts: 2,
      compatibility_repair_applied: true,
      manual_validation_required: true,
      message: "Local preview cannot run part of this script yet. The Pine code may still require manual platform validation.",
    },
  );
});

test("pineforge command runner parses structured successful output", async () => {
  const childScript = `
    process.stdin.resume();
    process.stdin.on("end", () => {
      process.stdout.write(JSON.stringify({
        status: "pass",
        report: { metrics: { trade_count: 0 } },
        trades: [],
        equity_curve: [],
        compile: { status: "pass" }
      }));
    });
  `;

  const result = await __test.runPineForgeCommand(
    {
      job_id: "job_pineforge",
      config: pineForgeJob.payload_json.backtest_config,
      pine_code_path: "strategy.pine",
      candles_path: "candles.json",
      output_dir: "out",
    },
    {
      command: process.execPath,
      args: ["-e", childScript],
      timeoutMs: 1_000,
    },
  );

  assert.equal(result.status, "pass");
  if (result.status === "pass") {
    assert.deepEqual(result.compile, { status: "pass" });
  }
});

test("runner trade normalization maps native pnl fields into persisted contract", () => {
  const [trade] = __test.normalizeRunnerTrades(
    [
      {
        entry_time: 1704200400000,
        exit_time: 1704200400000,
        entry_bar_index: 10,
        exit_bar_index: 18,
        entry_price: 45280.85,
        exit_price: 45323.15,
        pnl: -132904.0000000029,
        pnl_pct: -0.29351039125812106,
        side: "short",
        qty: 1000,
        commission: 90604,
      },
    ],
    pineForgeJob.payload_json.backtest_config,
  );

  assert.equal(trade.pnl_cost, -132904.0000000029);
  assert.equal(trade.pnl_percentage, -0.29351039125812106);
  assert.equal(trade.qty, 1000);
  assert.equal(trade.commission, 90604);
  assert.equal(trade.cost, 45280850);
  assert.equal(trade.duration_bars, 8);
});

test("backtest dashboard artifact aggregates metrics and log rows", () => {
  const trades = __test.normalizeRunnerTrades(
    [
      {
        id: "long-win",
        side: "long",
        entry_time: Date.parse("2024-01-01T00:00:00.000Z"),
        exit_time: Date.parse("2024-01-01T02:00:00.000Z"),
        entry_bar_index: 0,
        exit_bar_index: 2,
        entry_price: 100,
        exit_price: 110,
        pnl: 100,
        pnl_pct: 1,
      },
      {
        id: "short-loss",
        side: "short",
        entry_time: Date.parse("2024-01-02T00:00:00.000Z"),
        exit_time: Date.parse("2024-01-02T03:00:00.000Z"),
        entry_bar_index: 2,
        exit_bar_index: 5,
        entry_price: 120,
        exit_price: 125,
        pnl: -40,
        pnl_pct: -0.4,
      },
    ],
    pineForgeJob.payload_json.backtest_config,
  );
  const equityCurve = __test.normalizeRunnerEquityCurve(
    [
      { index: 0, timestamp: Date.parse("2024-01-01T00:00:00.000Z"), equity: 10_000 },
      { index: 1, timestamp: Date.parse("2024-01-01T02:00:00.000Z"), equity: 10_100 },
      { index: 2, timestamp: Date.parse("2024-01-02T03:00:00.000Z"), equity: 10_060 },
    ],
    10_000,
  );
  const dashboard = __test.buildBacktestDashboard(pineForgeJob, {
    adapter: { timeframe: "1h" },
    executionSemantics: "model_generated_pine_pineforge",
    manifest: {
      candle_timeframe: "1m",
      timeframe: "1h",
      market_data_source: { provider: "ccxt" },
      source_feed_checksum: "checksum",
    },
    report: {
      engine: "pineforge",
      evidence_label: "PineForge local Pine preview evidence",
      metrics: { quality_status: "pass" },
      warnings: ["Local preview only."],
      reproducibility_hash: "hash",
    },
    trades,
    equityCurve,
    sourceBundle: {},
    metadata: {},
  } as never);

  assert.equal(dashboard.kind, "backtest_dashboard");
  assert.equal(dashboard.performance.kpis.net_profit, 60);
  assert.equal(dashboard.performance.kpis.trades, 2);
  assert.equal(dashboard.performance.kpis.winners, 1);
  assert.equal(dashboard.performance.kpis.losers, 1);
  assert.equal(dashboard.performance.kpis.profit_factor, 2.5);
  assert.equal(dashboard.performance.daily_pnl.length, 2);
  assert.equal(dashboard.trades_analysis.duration_vs_pnl[0].duration_bars, 2);
  assert.equal(dashboard.trades_log.total_rows, 2);
  assert.equal(dashboard.trades_log.rows[0].trade_number, 2);
  assert.equal(dashboard.trades_log.rows[0].cumulative_pnl, 60);
});

test("backtest preview summary captures compact metrics and capped equity", () => {
  const trades = __test.normalizeRunnerTrades(
    [
      {
        id: "long-win",
        side: "long",
        entry_time: Date.parse("2024-01-01T00:00:00.000Z"),
        exit_time: Date.parse("2024-01-01T02:00:00.000Z"),
        entry_bar_index: 0,
        exit_bar_index: 2,
        entry_price: 100,
        exit_price: 110,
        pnl: 100,
      },
      {
        id: "short-loss",
        side: "short",
        entry_time: Date.parse("2024-01-02T00:00:00.000Z"),
        exit_time: Date.parse("2024-01-02T03:00:00.000Z"),
        entry_bar_index: 2,
        exit_bar_index: 5,
        entry_price: 120,
        exit_price: 125,
        pnl: -40,
      },
    ],
    pineForgeJob.payload_json.backtest_config,
  );
  const equityCurve = __test.normalizeRunnerEquityCurve(
    Array.from({ length: 100 }, (_, index) => ({
      index,
      timestamp: Date.parse("2024-01-01T00:00:00.000Z") + index * 60_000,
      equity: 10_000 + index,
    })),
    10_000,
  );

  const summary = __test.buildBacktestPreviewSummary(pineForgeJob, {
    adapter: { timeframe: "1h" },
    executionSemantics: "model_generated_pine_pineforge",
    manifest: {},
    report: {},
    trades,
    equityCurve,
    sourceBundle: {},
    metadata: {},
  } as never);

  assert.equal(summary.kind, "backtest_result");
  assert.equal(summary.metrics.net_pnl, 60);
  assert.equal(summary.metrics.trade_count, 2);
  assert.equal(summary.equity_preview.length, 80);
});

test("dashboard duration fallback uses execution candle timeframe", () => {
  const [trade] = __test.normalizeRunnerTrades(
    [
      {
        id: "time-duration",
        side: "long",
        entry_time: Date.parse("2024-01-01T00:00:00.000Z"),
        exit_time: Date.parse("2024-01-01T02:00:00.000Z"),
        entry_price: 100,
        exit_price: 110,
        pnl: 100,
      },
    ],
    pineForgeJob.payload_json.backtest_config,
  );

  assert.equal(trade.duration_bars, 120);
});

test("backtest dashboard artifact caps preview-heavy series", () => {
  const rawTrades = Array.from({ length: 90 }, (_, index) => ({
    id: `trade-${index + 1}`,
    side: index % 2 === 0 ? "long" : "short",
    entry_time: Date.parse("2024-01-01T00:00:00.000Z") + index * 60_000,
    exit_time: Date.parse("2024-01-01T00:01:00.000Z") + index * 60_000,
    entry_bar_index: index,
    exit_bar_index: index + 1,
    entry_price: 100,
    exit_price: 101,
    pnl: 1,
  }));
  const trades = __test.normalizeRunnerTrades(rawTrades, pineForgeJob.payload_json.backtest_config);
  const equityCurve = __test.normalizeRunnerEquityCurve(
    Array.from({ length: 220 }, (_, index) => ({
      index,
      timestamp: Date.parse("2024-01-01T00:00:00.000Z") + index * 60_000,
      equity: 10_000 + index,
    })),
    10_000,
  );
  const dashboard = __test.buildBacktestDashboard(pineForgeJob, {
    adapter: { timeframe: "1h" },
    executionSemantics: "model_generated_pine_pineforge",
    manifest: {
      candle_timeframe: "1m",
      timeframe: "1h",
      market_data_source: { provider: "ccxt" },
      source_feed_checksum: "checksum",
    },
    report: { engine: "pineforge", metrics: {}, warnings: [] },
    trades,
    equityCurve,
    sourceBundle: {},
    metadata: {},
  } as never);

  assert.equal(dashboard.limits.equity_points_total, 220);
  assert.equal(dashboard.performance.equity.length, 200);
  assert.equal(dashboard.trades_log.total_rows, 90);
  assert.equal(dashboard.trades_log.rows.length, 80);
  assert.equal(dashboard.trades_log.rows[0].trade_number, 90);
});

test("runner equity normalization computes drawdown for native equity points", () => {
  const points = __test.normalizeRunnerEquityCurve(
    [
      { index: 0, timestamp: 1704200400000, equity: 10000, open_profit: 0 },
      { index: 1, timestamp: 1704204000000, equity: -122904, open_profit: 0 },
    ],
    10000,
  );

  assert.equal(points[1].pnl_cost, -132904);
  assert.equal(points[1].drawdown_pct, 1329.04);
  assert.equal(__test.buildMetrics(10000, [], points).max_drawdown, 1329.04);
});

test("backtest quality flags catch notional sizing mismatch", () => {
  const trades = __test.normalizeRunnerTrades(
    [{ entry_price: 45_000, qty: 1000, pnl: -90_000, pnl_pct: -0.2, commission: 90_000 }],
    pineForgeJob.payload_json.backtest_config,
  );
  const equity = __test.normalizeRunnerEquityCurve([{ equity: 10_000 }, { equity: -80_000 }], 10_000);
  const metrics = __test.buildMetrics(10_000, trades, equity);

  const quality = __test.backtestQualityFlags(pineForgeJob.payload_json.backtest_config, trades, metrics, { pointvalue: 1 } as never);

  assert.equal(quality.status, "fail");
  assert.ok(quality.flags.includes("position_sizing_mismatch"));
  assert.ok(quality.flags.includes("commission_exceeds_capital"));
});

test("fetch progress emitter coalesces high-frequency windows and preserves final progress", async () => {
  const events: Array<{ type: string; payload: Record<string, unknown> }> = [];
  const emitter = new __test.BacktestProgressEmitter(
    {} as never,
    { id: "run_test" } as never,
    "job_test",
    async (_client, _run, type, payload) => {
      events.push({ type, payload });
    },
  );

  await emitter.progress(__test.BACKTEST_RUN_EVENTS.dataFetching, {
    fetch_windows_total: 100,
    fetch_windows_completed: 0,
  });
  for (let completed = 1; completed <= 100; completed += 1) {
    await emitter.progress(__test.BACKTEST_RUN_EVENTS.dataFetching, {
      fetch_windows_total: 100,
      fetch_windows_completed: completed,
      fetch_retry_count: 2,
    });
  }
  await emitter.flush();

  const progressEvents = events.filter((event) => event.type === __test.BACKTEST_RUN_EVENTS.dataFetching);
  const heartbeatEvents = events.filter((event) => event.type === "backtest.preview.heartbeat");
  assert.ok(progressEvents.length <= 25, `expected coalesced progress events, got ${progressEvents.length}`);
  assert.equal(progressEvents[0].payload.fetch_windows_completed, 0);
  assert.equal(progressEvents.at(-1)?.payload.fetch_windows_completed, 100);
  assert.equal(progressEvents.at(-1)?.payload.fetch_retry_count, 2);
  assert.equal(progressEvents.at(-1)?.payload.job_id, "job_test");
  assert.ok(progressEvents.some((event) => Number(event.payload.coalesced_updates ?? 0) > 0));
  assert.ok(heartbeatEvents.some((event) => event.payload.stage === "fetching" && event.payload.progress_pct === 55));
});

test("fetch progress emitter flushes pending progress before lifecycle events", async () => {
  const events: Array<{ type: string; payload: Record<string, unknown> }> = [];
  const emitter = new __test.BacktestProgressEmitter(
    {} as never,
    { id: "run_test" } as never,
    "job_test",
    async (_client, _run, type, payload) => {
      events.push({ type, payload });
    },
  );

  await emitter.progress(__test.BACKTEST_RUN_EVENTS.dataFetching, {
    fetch_windows_total: 100,
    fetch_windows_completed: 0,
  });
  await emitter.progress(__test.BACKTEST_RUN_EVENTS.dataFetching, {
    fetch_windows_total: 100,
    fetch_windows_completed: 1,
  });
  await emitter.progress(__test.BACKTEST_RUN_EVENTS.dataExporting, { csv_export_ms: 12 });

  assert.deepEqual(
    events.map((event) => event.type),
    [
      __test.BACKTEST_RUN_EVENTS.dataFetching,
      "backtest.preview.heartbeat",
      __test.BACKTEST_RUN_EVENTS.dataFetching,
      "backtest.preview.heartbeat",
      __test.BACKTEST_RUN_EVENTS.dataExporting,
      "backtest.preview.heartbeat",
    ],
  );
  assert.equal(events[0].payload.fetch_windows_completed, 0);
  assert.equal(events[2].payload.fetch_windows_completed, 1);
  assert.equal(events[4].payload.csv_export_ms, 12);
  assert.equal(events[5].payload.stage, "exporting");
});
