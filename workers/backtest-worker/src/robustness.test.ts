import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

process.env.STRATEGY_CODEBOT_API_ARTIFACT_ROOT = await mkdtemp(join(tmpdir(), "backtest-worker-robustness-"));

const { __test } = await import("./index.js");

const config = {
  engine: "pineforge" as const,
  symbol: "BTCUSDT",
  timeframe: "1h",
  candle_timeframe: "1m",
  start: "2024-01-01T00:00:00.000Z",
  end: "2024-04-01T00:00:00.000Z",
  initial_capital: 10000,
  fee_bps: 10,
  slippage_bps: 5,
  data_source: "public-readonly-cache" as const,
};

function trade(pnl: number) {
  return {
    id: null,
    symbol: "BTCUSDT",
    side: "long",
    close_reason: "take_profit",
    opened_at: null,
    closed_at: null,
    entry_bar_index: null,
    exit_bar_index: null,
    duration_bars: null,
    entry_price: 100,
    exit_price: 100 + pnl,
    raw_pnl_percentage: pnl,
    raw_pnl_cost: pnl * 10,
    pnl_percentage: pnl,
    pnl_cost: pnl * 10,
    cost: 1000,
    fee_cost: 0,
    slippage_cost: 0,
    cost_model: __test.buildCostModel({ ...config, fee_bps: 0, slippage_bps: 0 }),
  };
}

test("fee and slippage are applied to normalized trade pnl", () => {
  const trade = __test.normalizeTrade(
    {
      action: "closed",
      symbol: "BTCUSDT",
      closeReason: "take_profit",
      closeTimestamp: Date.parse("2024-01-02T00:00:00.000Z"),
      currentPrice: 110,
      pnl: {
        priceOpen: 100,
        priceClose: 110,
        pnlPercentage: 10,
        pnlCost: 100,
        pnlEntries: 1000,
      },
      signal: {
        id: "signal_1",
        position: "long",
        cost: 1000,
        openTimestamp: Date.parse("2024-01-01T00:00:00.000Z"),
      },
    },
    "BTCUSDT",
    config,
  );

  assert.equal(trade.raw_pnl_cost, 100);
  assert.equal(trade.raw_pnl_percentage, 10);
  assert.equal(trade.fee_cost, 2);
  assert.equal(trade.slippage_cost, 1);
  assert.equal(trade.pnl_cost, 97);
  assert.equal(trade.pnl_percentage, 9.7);
  assert.equal(trade.cost_model.version, "fixed_bps_v1");
});

test("zero fee and slippage preserve normalized pnl", () => {
  const freeConfig = { ...config, fee_bps: 0, slippage_bps: 0 };
  const trade = __test.normalizeTrade(
    {
      action: "closed",
      symbol: "BTCUSDT",
      pnl: {
        pnlPercentage: -5,
        pnlCost: -50,
        pnlEntries: 1000,
      },
      signal: { cost: 1000 },
    },
    "BTCUSDT",
    freeConfig,
  );

  assert.equal(trade.pnl_cost, -50);
  assert.equal(trade.pnl_percentage, -5);
  assert.equal(trade.fee_cost, 0);
  assert.equal(trade.slippage_cost, 0);
});

test("worker config validation rejects unsupported executable timeframe", () => {
  assert.throws(
    () => __test.normalizedConfig({ ...config, timeframe: "4h" }),
    /Unsupported backtest timeframe/,
  );
});

test("worker config validation defaults and rejects candle timeframe", () => {
  assert.equal(__test.normalizedConfig({ ...config, candle_timeframe: undefined }).candle_timeframe, "1m");
  assert.throws(
    () => __test.normalizedConfig({ ...config, candle_timeframe: "5m" }),
    /candle_timeframe/,
  );
});

test("worker config validation rejects excessive cost assumptions", () => {
  assert.throws(
    () => __test.normalizedConfig({ ...config, fee_bps: 1001 }),
    /fee_bps/,
  );
});

test("robustness report rejects zero-trade previews", () => {
  const metrics = {
    pnl: { absolute: 0, percentage: 0 },
    max_drawdown: 0,
    trade_count: 0,
    win_rate: null,
    sharpe: null,
    sortino: null,
  };
  const report = __test.buildRobustnessReport(config, metrics, []);
  const decision = __test.buildPromotionDecision(report);

  assert.equal(report.status, "fail");
  assert.equal(report.checks.sample_size.status, "fail");
  assert.equal(decision.decision, "reject");
  assert.match(decision.boundary, /Local preview evidence only/);
});

test("robustness report sends fragile previews to manual review", () => {
  const trades = [trade(1), trade(-1), trade(1)];
  const metrics = {
    pnl: { absolute: 100, percentage: 1 },
    max_drawdown: 3,
    trade_count: trades.length,
    win_rate: 66.67,
    sharpe: 1.2,
    sortino: 1.4,
  };
  const report = __test.buildRobustnessReport({ ...config, fee_bps: 0, slippage_bps: 0 }, metrics, trades);
  const decision = __test.buildPromotionDecision(report);

  assert.equal(report.status, "warn");
  assert.equal(report.checks.execution_costs.status, "warn");
  assert.equal(report.metrics.sample_size, 3);
  assert.equal(decision.decision, "manual_review");
});

test("robustness report can mark a bounded preview as research candidate", () => {
  const trades = Array.from({ length: 24 }, (_, index) => trade(index % 3 === 0 ? -0.5 : 0.8));
  const metrics = {
    pnl: { absolute: 1200, percentage: 12 },
    max_drawdown: 8,
    trade_count: trades.length,
    win_rate: 66.67,
    sharpe: 1.1,
    sortino: 1.6,
  };
  const report = __test.buildRobustnessReport(config, metrics, trades);
  const decision = __test.buildPromotionDecision(report);

  assert.equal(report.status, "pass");
  assert.equal(report.checks.parameter_sensitivity.status, "not_available");
  assert.equal(decision.decision, "research_candidate");
  assert.notEqual(decision.decision, "live_ready");
  assert.match(decision.boundary, /not .*live-ready/i);
});
