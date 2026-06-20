import { describe, expect, it } from "vitest";

import { parseBacktestArtifactPreview } from "./backtest-report";

describe("backtest report parser", () => {
  it("parses the current Backtest Kit report shape", () => {
    const parsed = parseBacktestArtifactPreview("backtest_report", {
      metrics: {
        pnl: 123.45678,
        max_drawdown: -12.5,
        trade_count: 7,
        win_rate: 0.57,
        sharpe: 1.25,
        sortino: 1.7,
      },
      assumptions: {
        symbol: "BTC/USDT",
        timeframe: "1h",
        start: "2024-01-01",
        end: "2024-02-01",
        initial_capital: 10000,
        fee_bps: 10,
        slippage_bps: 5,
        data_source: "public-readonly-cache",
      },
      warnings: ["Local preview only."],
      reproducibility_hash: "abc123",
    });

    expect(parsed?.kind).toBe("report");
    if (parsed?.kind !== "report") {
      throw new Error("expected report");
    }
    expect(parsed.metrics).toContainEqual({ label: "PnL", value: "123.4568" });
    expect(parsed.metrics).toContainEqual({ label: "Trade count", value: "7" });
    expect(parsed.dataSource).toBe("public-readonly-cache");
    expect(parsed.reproducibilityHash).toBe("abc123");
  });

  it("handles alternate metric keys and missing values defensively", () => {
    const parsed = parseBacktestArtifactPreview("backtest_report", {
      metrics: {
        net_profit: 25,
        max_drawdown_pct: -3,
      },
      config: {
        symbol: "ETH/USDT",
        timeframe: "4h",
      },
      trades: [{ id: "1" }, { id: "2" }],
    });

    expect(parsed?.kind).toBe("report");
    if (parsed?.kind !== "report") {
      throw new Error("expected report");
    }
    expect(parsed.metrics).toContainEqual({ label: "PnL", value: "25" });
    expect(parsed.metrics).toContainEqual({ label: "Max drawdown", value: "-3" });
    expect(parsed.metrics).toContainEqual({ label: "Trade count", value: "2" });
    expect(parsed.assumptions).toContainEqual({ label: "Fee bps", value: "N/A" });
  });

  it("parses variant comparison artifacts", () => {
    const parsed = parseBacktestArtifactPreview("backtest_variant_comparison", {
      variant_group_id: "variant_1",
      shared_cache_key: "cache_1",
      warnings: ["Compare after completion."],
      variants: [
        {
          name: "base",
          run_id: "run_1",
          status: "queued",
          backtest_config: { symbol: "BTC/USDT", timeframe: "1h" },
        },
      ],
    });

    expect(parsed).toMatchObject({
      kind: "variant_comparison",
      variantGroupId: "variant_1",
      sharedCacheKey: "cache_1",
      variants: [{ name: "base", runId: "run_1", status: "queued" }],
    });
  });
});
