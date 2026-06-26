import assert from "node:assert/strict";
import test from "node:test";

const { __test } = await import("./pineforge-mcp-adapter.js");

test("pineforge adapter exports candles to PineForge CSV format", () => {
  const csv = __test.candlesToCsv([
    { timestamp: 1, open: 2, high: 3, low: 1, close: 2.5, volume: 10 },
    { timestamp: 2, open: 2.5, high: 4, low: 2, close: 3.5, volume: 11 },
  ]);

  assert.equal(
    csv,
    [
      "timestamp,open,high,low,close,volume",
      "1,2,3,1,2.5,10",
      "2,2.5,4,2,3.5,11",
      "",
    ].join("\n"),
  );
});

test("pineforge adapter maps signal and execution timeframes to PineForge runtime args", () => {
  const args = __test.pineForgeBacktestArguments(
    {
      symbol: "BTCUSDT",
      timeframe: "1h",
      candle_timeframe: "1m",
      initial_capital: 10_000,
      fee_bps: 10,
      slippage_bps: 5,
    },
    "//@version=6\nstrategy(\"x\")",
    "./ohlcv.csv",
  );

  assert.equal(args.runtime.input_tf, "1");
  assert.equal(args.runtime.script_tf, "60");
  assert.equal(args.overrides.initial_capital, 10_000);
  assert.equal(args.overrides.commission_type, "percent");
  assert.equal(args.overrides.commission_value, 0.1);
  assert.equal(args.overrides.slippage, 0);
  assert.equal(args.runtime.bar_magnifier, true);
});

test("pineforge adapter extracts JSON payload from MCP text response", () => {
  const payload = __test.extractBacktestPayload({
    content: [
      {
        type: "text",
        text: JSON.stringify({ engine: "pineforge", summary: { total_trades: 2, net_pnl: 42 } }),
      },
    ],
  });

  assert.deepEqual(payload, { engine: "pineforge", summary: { total_trades: 2, net_pnl: 42 } });
});

test("pineforge adapter converts bps to PineForge commission percent", () => {
  assert.equal(__test.bpsToPercent(1), 0.01);
  assert.equal(__test.bpsToPercent(10), 0.1);
  assert.equal(__test.bpsToPercent(0), 0);
});
