import { describe, expect, it } from "vitest";

import { parseBacktestArtifactPreview } from "./backtest-report";

describe("backtest report parser", () => {
  it("parses the current local preview report shape", () => {
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
        candle_timeframe: "1m",
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
    expect(parsed.metrics).toContainEqual(expect.objectContaining({ key: "net_pnl", label: "PnL", value: "123.4568" }));
    expect(parsed.metrics).toContainEqual(expect.objectContaining({ key: "trade_count", label: "Trade count", value: "7" }));
    expect(parsed.assumptions).toContainEqual(expect.objectContaining({ key: "timeframe", label: "Signal timeframe", value: "1h" }));
    expect(parsed.assumptions).toContainEqual(expect.objectContaining({ key: "candle_timeframe", label: "Candle timeframe", value: "1m" }));
    expect(parsed.dataSource).toBe("public-readonly-cache");
    expect(parsed.reproducibilityHash).toBe("abc123");
  });

  it("sanitizes old engine-specific preview copy", () => {
    const parsed = parseBacktestArtifactPreview("backtest_report", {
      metrics: {
        pnl: 10,
      },
      assumptions: {
        symbol: "BTC/USDT",
        timeframe: "1h",
        data_source: "pineforge-engine",
      },
      warnings: [
        "PineForge local Pine preview evidence only.",
        "The model-generated PineScript is statically guarded before PineForge compile/backtest.",
      ],
    });

    expect(parsed?.kind).toBe("report");
    if (parsed?.kind !== "report") {
      throw new Error("expected report");
    }
    expect(parsed.dataSource).toBe("local preview");
    expect(parsed.warnings.join(" ")).not.toContain("PineForge");
    expect(parsed.warnings).toContain("Local sandbox preview evidence only.");
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
    expect(parsed.metrics).toContainEqual(expect.objectContaining({ key: "net_pnl", label: "PnL", value: "25" }));
    expect(parsed.metrics).toContainEqual(expect.objectContaining({ key: "max_drawdown", label: "Max drawdown", value: "-3" }));
    expect(parsed.metrics).toContainEqual(expect.objectContaining({ key: "trade_count", label: "Trade count", value: "2" }));
    expect(parsed.assumptions).toContainEqual(expect.objectContaining({ key: "timeframe", label: "Signal timeframe", value: "4h" }));
    expect(parsed.assumptions).not.toContainEqual(expect.objectContaining({ key: "candle_timeframe" }));
    expect(parsed.assumptions).not.toContainEqual(expect.objectContaining({ key: "fee_bps" }));
    expect([...parsed.metrics, ...parsed.assumptions, ...parsed.robustness]).not.toContainEqual(
      expect.objectContaining({ value: "N/A" })
    );
  });

  it("omits empty report rows instead of rendering N/A placeholders", () => {
    const parsed = parseBacktestArtifactPreview("backtest_report", {
      metrics: {},
      assumptions: {},
      reproducibility_hash: "abc123",
    });

    expect(parsed?.kind).toBe("report");
    if (parsed?.kind !== "report") {
      throw new Error("expected report");
    }
    expect(parsed.metrics).toEqual([]);
    expect(parsed.assumptions).toEqual([]);
    expect(parsed.robustness).toEqual([]);
    expect(parsed.reproducibilityHash).toBe("abc123");
  });

  it("parses robustness and promotion metadata when present", () => {
    const parsed = parseBacktestArtifactPreview("backtest_report", {
      metrics: {
        pnl: { absolute: 100, percentage: 1 },
        trade_count: 3,
      },
      assumptions: {
        symbol: "BTC/USDT",
        timeframe: "1h",
      },
      robustness_report: {
        status: "warn",
        metrics: {
          sample_size: 3,
          backtest_days: 30,
          max_loss_streak: 2,
        },
      },
      promotion_decision: {
        decision: "manual_review",
        reasons: ["sample_size: Low closed-trade sample; keep the result in manual review."],
      },
    });

    expect(parsed?.kind).toBe("report");
    if (parsed?.kind !== "report") {
      throw new Error("expected report");
    }
    expect(parsed.metrics).toContainEqual(expect.objectContaining({ key: "net_pnl", label: "PnL", value: "100 (1%)" }));
    expect(parsed.robustness).toContainEqual(expect.objectContaining({ key: "robustness", label: "Robustness", value: "warn" }));
    expect(parsed.robustness).toContainEqual(expect.objectContaining({ key: "sample_size", label: "Sample size", value: "3" }));
    expect(parsed.promotionDecision).toBe("manual_review");
    expect(parsed.warnings).toContain("sample_size: Low closed-trade sample; keep the result in manual review.");
  });

  it("surfaces invalid sizing quality flags", () => {
    const parsed = parseBacktestArtifactPreview("backtest_report", {
      metrics: {
        pnl: { absolute: -44_688_631.91, percentage: -446_886.3191 },
        max_drawdown: 446_886.3191,
        trade_count: 272,
        quality_status: "fail",
        quality_flags: ["position_sizing_mismatch"],
      },
    });

    expect(parsed?.kind).toBe("report");
    if (parsed?.kind !== "report") {
      throw new Error("expected report");
    }
    expect(parsed.qualityStatus).toBe("fail");
    expect(parsed.qualityFlags).toContain("position_sizing_mismatch");
    expect(parsed.warnings).toContain("Position sizing mismatch: repair sizing before evaluating this preview.");
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

  it("parses standalone robustness report artifacts", () => {
    const parsed = parseBacktestArtifactPreview("robustness_report", {
      boundary: "PineForge local Pine preview evidence only; not broker proof.",
      metrics: {
        trade_count: 236,
        win_rate: 31.77,
        max_drawdown_pct: 3.19,
        profit_factor: 0.91,
        net_profit_pct: -2.22,
      },
      assumption_review: {
        fee_bps: 10,
        slippage_bps: 5,
        warnings: ["Review fee assumptions."],
      },
      checks: [
        { id: "sample_size", status: "pass", message: "Sample is large enough.", observed: 236 },
        { id: "net_profit", status: "fail", message: "Net profit is negative.", observed: -2.22 },
        { id: "fees_slippage", status: "warn", message: "Verify costs.", observed: 2 },
      ],
      trade_samples: {
        sample: [{ id: 1 }, { id: 2 }],
        top_losers: [{ id: 3 }],
        top_winners: [{ id: 4 }],
      },
      recommendation: "reject_preview",
    });

    expect(parsed?.kind).toBe("robustness_report");
    if (parsed?.kind !== "robustness_report") {
      throw new Error("expected robustness report");
    }
    expect(parsed.recommendation).toBe("reject_preview");
    expect(parsed.checkCounts).toEqual({ fail: 1, pass: 1, warn: 1 });
    expect(parsed.metrics).toContainEqual(expect.objectContaining({ key: "trade_count", label: "Trade count", value: "236" }));
    expect(parsed.tradeSampleCount).toBe(2);
    expect(parsed.topLoserCount).toBe(1);
    expect(parsed.topWinnerCount).toBe(1);
    expect(parsed.boundary).not.toContain("PineForge");
  });

  it("omits empty robustness metric rows instead of rendering N/A placeholders", () => {
    const parsed = parseBacktestArtifactPreview("robustness_report", {
      metrics: {},
      assumption_review: {},
    });

    expect(parsed?.kind).toBe("robustness_report");
    if (parsed?.kind !== "robustness_report") {
      throw new Error("expected robustness report");
    }
    expect(parsed.metrics).toEqual([]);
    expect(parsed.checks).toEqual([]);
  });
});
