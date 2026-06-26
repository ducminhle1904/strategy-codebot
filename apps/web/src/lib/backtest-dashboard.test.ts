import { describe, expect, it } from "vitest";

import { parseBacktestDashboardArtifactPreview } from "./backtest-dashboard";

describe("backtest dashboard parser", () => {
  it("parses dashboard artifacts defensively", () => {
    const parsed = parseBacktestDashboardArtifactPreview("backtest_dashboard", {
      summary: {
        title: "Confirmation Normal",
        symbol: "ZS",
        timeframe: "1h",
        candle_timeframe: "1m",
        engine: "pineforge",
        evidence_label: "PineForge local Pine preview evidence",
        assumptions: { initial_capital: 10000 },
        warnings: ["Local preview only."],
      },
      performance: {
        equity: [{ index: 0, pnl: 0 }],
        daily_pnl: [{ date: "2024-01-01", pnl: 100 }],
        weekday_pnl: [{ weekday: "Mon", pnl: 100 }],
        kpis: { net_profit: 100, trades: 2 },
        matrix: [{ label: "Net Profit", format: "currency", all: 100, long: 100, short: 0 }],
      },
      trades_analysis: {
        pnl_distribution: { bins: [{ start: -10, end: 10, count: 2 }], references: { average_trade: 50 } },
        winrate: { winners: 1, losers: 1, win_rate: 50 },
        duration_vs_pnl: [{ duration_bars: 2, pnl: 100 }],
        trade_stats: [{ label: "Closed Trades", format: "number", all: 2, long: 1, short: 1 }],
        duration_stats: [{ label: "Avg Trade Duration (bars)", format: "number", all: 2, long: 2, short: 3 }],
      },
      trades_log: {
        total_rows: 2,
        rows: [{ trade_number: 2, side: "long", net_pnl: 100, cumulative_pnl: 100 }],
      },
    });

    expect(parsed?.kind).toBe("dashboard");
    expect(parsed?.summary.title).toBe("Confirmation Normal");
    expect(parsed?.summary.engine).toBe("local preview");
    expect(parsed?.summary.evidenceLabel).toBe("Local sandbox preview evidence");
    expect(parsed?.summary.assumptions.initial_capital).toBe(10000);
    expect(parsed?.performance.equity).toHaveLength(1);
    expect(parsed?.performance.matrix[0]).toMatchObject({ label: "Net Profit", all: 100 });
    expect(parsed?.tradesAnalysis.durationVsPnl[0]).toMatchObject({ duration_bars: 2 });
    expect(parsed?.tradesLog.totalRows).toBe(2);
    expect(parsed?.tradesLog.rows[0]).toMatchObject({ trade_number: 2 });
  });

  it("returns null for raw report artifacts", () => {
    expect(parseBacktestDashboardArtifactPreview("backtest_report", {})).toBeNull();
  });
});
