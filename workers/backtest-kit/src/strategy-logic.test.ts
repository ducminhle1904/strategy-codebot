import assert from "node:assert/strict";
import test from "node:test";

const { __test } = await import("./index.js");

const strategyLogic = {
  logic_version: "backtest-strategy-logic.v1",
  position: "long",
  indicators: {
    fast_ema: { kind: "ema", period: 3, source: "close" },
    slow_ema: { kind: "ema", period: 5, source: "close" },
    rsi: { kind: "rsi", period: 2, source: "close" },
  },
  entry: {
    all: [
      { type: "crossover", left: "fast_ema", right: "slow_ema" },
      { type: "greater_than", left: "rsi", right: 45 },
    ],
  },
  exit: { take_profit_pct: 4, stop_loss_pct: 2, max_holding_minutes: 1440 },
  risk: { cost: 1000 },
};

test("semantic strategy logic waits for warmup", () => {
  const parsed = __test.parseStrategyLogic(strategyLogic, adapter());
  assert.ok(parsed);

  assert.equal(__test.evaluateStrategyLogic(parsed, candles([10, 11, 12]), new Date("2024-01-01T02:00:00.000Z")), false);
});

test("semantic strategy logic enters only on EMA crossover with RSI filter", () => {
  const parsed = __test.parseStrategyLogic(strategyLogic, adapter());
  assert.ok(parsed);
  const history = candles([10, 10, 10, 10, 10, 9, 8, 7, 6, 20]);

  assert.equal(__test.evaluateStrategyLogic(parsed, history, new Date("2024-01-01T08:00:00.000Z")), false);
  assert.equal(__test.evaluateStrategyLogic(parsed, history, new Date("2024-01-01T09:00:00.000Z")), true);
});

test("invalid strategy logic fails instead of falling back silently", () => {
  assert.throws(
    () => __test.parseStrategyLogic({ ...strategyLogic, position: "short" }, adapter()),
    /only supports long/,
  );
});

function adapter() {
  return {
    strategy_name: "test-strategy",
    exchange_name: "test-exchange",
    frame_name: "test-frame",
    timeframe: "1h",
    position: "long" as const,
    percent_take_profit: 4,
    percent_stop_loss: 2,
    cost: 1000,
    minute_estimated_time: 1440,
  };
}

function candles(closes: number[]) {
  return closes.map((close, index) => ({
    timestamp: Date.parse("2024-01-01T00:00:00.000Z") + index * 3_600_000,
    open: close,
    high: close + 1,
    low: close - 1,
    close,
    volume: 10,
  }));
}
