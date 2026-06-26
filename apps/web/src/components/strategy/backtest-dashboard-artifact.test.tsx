import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { BacktestDashboardArtifact } from "./backtest-dashboard-artifact";

vi.mock("recharts", () => {
  const Chart = (_props: { children?: ReactNode }) => <div />;
  return {
    Area: Chart,
    AreaChart: Chart,
    Bar: Chart,
    BarChart: Chart,
    CartesianGrid: Chart,
    Cell: () => null,
    Pie: Chart,
    PieChart: Chart,
    ReferenceLine: Chart,
    ResponsiveContainer: Chart,
    Scatter: Chart,
    ScatterChart: Chart,
    Tooltip: Chart,
    XAxis: Chart,
    YAxis: Chart,
  };
});

describe("BacktestDashboardArtifact", () => {
  it("renders performance, analysis, and log tabs", () => {
    render(
      <BacktestDashboardArtifact
        dashboard={{
          kind: "dashboard",
          summary: {
            title: "Confirmation Normal",
            symbol: "ZS",
            timeframe: "1h",
            candleTimeframe: "1m",
            engine: "pineforge",
            evidenceLabel: "PineForge local Pine preview evidence",
            assumptions: {},
            warnings: ["Local preview only."],
          },
          performance: {
            equity: [{ index: 0, pnl: 0 }],
            dailyPnl: [{ date: "2024-01-01", pnl: 100 }],
            weekdayPnl: [{ weekday: "Mon", pnl: 100 }],
            kpis: {
              net_profit: 100,
              trades: 2,
              win_rate: 50,
              winners: 1,
              losers: 1,
              max_drawdown: 10,
              max_drawdown_pct: 1,
              profit_factor: 2.5,
            },
            matrix: [{ label: "Net Profit", format: "currency", all: 100, long: 100, short: 0 }],
          },
          tradesAnalysis: {
            pnlDistribution: { bins: [{ start: -10, count: 1 }], references: {} },
            winrate: { winners: 1, losers: 1, win_rate: 50 },
            durationVsPnl: [{ trade_number: 1, duration_bars: 2, pnl: 100 }],
            tradeStats: [{ label: "Closed Trades", format: "number", all: 2, long: 1, short: 1 }],
            durationStats: [{ label: "Avg Trade Duration (bars)", format: "number", all: 2, long: 2, short: 3 }],
          },
          tradesLog: {
            totalRows: 1,
            rows: [
              {
                trade_number: 2,
                side: "long",
                entry: { timestamp: "2024-01-01T00:00:00.000Z", price: 100 },
                exit: { timestamp: "2024-01-01T02:00:00.000Z", price: 110 },
                net_pnl: 100,
                cumulative_pnl: 100,
              },
            ],
          },
        }}
      />
    );

    expect(screen.queryByText("Confirmation Normal")).not.toBeInTheDocument();
    expect(screen.queryByText("candles 1m")).not.toBeInTheDocument();
    expect(screen.queryByText("Local preview only.")).not.toBeInTheDocument();
    expect(screen.getByText("Net Daily P&L (USD)")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Trades Analysis" }));
    expect(screen.getByText("P&L Distribution (USD)")).toBeVisible();
    expect(screen.getByText("Closed Trades")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Trades Log" }));
    expect(screen.getByText("Trade #")).toBeVisible();
    expect(screen.getByRole("button", { name: "Calendar" })).toBeDisabled();
    expect(screen.getAllByText("+100 USD").length).toBeGreaterThan(0);
  });
});
