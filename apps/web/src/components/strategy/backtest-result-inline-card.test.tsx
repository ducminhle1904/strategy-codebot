import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BacktestResultInlineCard } from "./backtest-result-inline-card";

vi.mock("recharts", () => {
  const Chart = (_props: { children?: ReactNode }) => <div />;
  return {
    Area: Chart,
    AreaChart: Chart,
    ResponsiveContainer: Chart,
    Tooltip: Chart,
  };
});

afterEach(() => cleanup());

describe("BacktestResultInlineCard", () => {
  it("renders dashboard KPIs and action CTAs", () => {
    const onOpenDashboard = vi.fn();
    const onShowTrades = vi.fn();
    const onBuildRobustness = vi.fn();

    render(
      <BacktestResultInlineCard
        dashboard={{
          kind: "dashboard",
          summary: {
            title: "Confirmation Normal",
            symbol: "BNBUSDT",
            timeframe: "1h",
            candleTimeframe: "1m",
            engine: "local preview",
            evidenceLabel: "Local preview evidence",
            assumptions: { initial_capital: 10000 },
            warnings: ["Review fees before using this preview."],
          },
          performance: {
            dailyPnl: [],
            equity: [
              { index: 0, pnl: 0 },
              { index: 1, pnl: -222.533 },
            ],
            kpis: {
              max_drawdown: 321.1654,
              max_drawdown_pct: 3.1913,
              net_profit: -222.533,
              profit_factor: 0.91,
              trades: 236,
              win_rate: 31.7797,
            },
            matrix: [],
            weekdayPnl: [],
          },
          tradesAnalysis: {
            durationStats: [],
            durationVsPnl: [],
            pnlDistribution: { bins: [], references: {} },
            tradeStats: [],
            winrate: {},
          },
          tradesLog: {
            rows: [],
            totalRows: 236,
          },
        }}
        onBuildRobustness={onBuildRobustness}
        onOpenDashboard={onOpenDashboard}
        onShowTrades={onShowTrades}
      />
    );

    expect(screen.queryByText("Confirmation Normal")).not.toBeInTheDocument();
    expect(screen.getByText("BNBUSDT")).toBeVisible();
    expect(screen.getByText("-222.53 USD")).toBeVisible();
    expect(screen.getByText("-2.23%")).toBeVisible();
    expect(screen.getByText("-321.17 USD 3.19%")).toBeVisible();
    expect(screen.getByText("31.78%")).toBeVisible();
    expect(screen.getByText("236")).toBeVisible();
    expect(screen.queryByText("Review fees before using this preview.")).not.toBeInTheDocument();
    expect(screen.queryByText("Review only")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Open dashboard" }));
    fireEvent.click(screen.getByRole("button", { name: "Show trades" }));
    fireEvent.click(screen.getByRole("button", { name: "Build robustness report" }));

    expect(onOpenDashboard).toHaveBeenCalledTimes(1);
    expect(onShowTrades).toHaveBeenCalledTimes(1);
    expect(onBuildRobustness).toHaveBeenCalledTimes(1);
  });

  it("falls back to report metrics when dashboard preview is unavailable", () => {
    render(
      <BacktestResultInlineCard
        onBuildRobustness={vi.fn()}
        onOpenDashboard={vi.fn()}
        onShowTrades={vi.fn()}
        report={{
          assumptions: [
            { key: "symbol", label: "Symbol", value: "BNBUSDT" },
            { key: "timeframe", label: "Signal timeframe", value: "1h" },
          ],
          dataSource: null,
          kind: "report",
          metrics: [
            { key: "net_pnl", label: "PnL", value: "-222.533 (-2.2253%)" },
            { key: "max_drawdown", label: "Max drawdown", value: "3.1913" },
            { key: "trade_count", label: "Trade count", value: "236" },
            { key: "win_rate", label: "Win rate", value: "31.7797%" },
          ],
          promotionDecision: null,
          promotionReasons: [],
          qualityFlags: [],
          qualityStatus: null,
          reproducibilityHash: null,
          robustness: [],
          warnings: [],
        }}
      />
    );

    expect(screen.queryByText("Backtest result")).not.toBeInTheDocument();
    expect(screen.getByText("BNBUSDT")).toBeVisible();
    expect(screen.getByText("-222.533 (-2.2253%)")).toBeVisible();
    expect(screen.getByText("-2.23%")).toBeVisible();
    expect(screen.getByText("Equity curve pending")).toBeVisible();
  });

  it("renders compact dashboard preview summary without a full dashboard payload", () => {
    render(
      <BacktestResultInlineCard
        onBuildRobustness={vi.fn()}
        onOpenDashboard={vi.fn()}
        onShowTrades={vi.fn()}
        previewSummary={{
          kind: "backtest_result",
          symbol: "BNBUSDT",
          timeframe: "1h",
          metrics: {
            net_pnl: -222.533,
            return_pct: -2.2253,
            max_drawdown: 321.1654,
            max_drawdown_pct: 3.1913,
            win_rate: 31.7797,
            winning_trades: 75,
            losing_trades: 161,
            trade_count: 236,
            profit_factor: 0.82,
          },
          equity_preview: [
            { index: 0, pnl: 0 },
            { index: 1, pnl: -222.533 },
          ],
        }}
      />
    );

    expect(screen.getByText("BNBUSDT")).toBeVisible();
    expect(screen.getByText("-222.53 USD")).toBeVisible();
    expect(screen.getByText("-2.23%")).toBeVisible();
    expect(screen.getByText("-321.17 USD 3.19%")).toBeVisible();
    expect(screen.getByText("31.78% 75 | 161")).toBeVisible();
    expect(screen.getByText("236")).toBeVisible();
    expect(screen.getByText("0.82")).toBeVisible();
  });

  it("renders a CTA-only fallback for legacy dashboard artifacts without preview summary", () => {
    render(
      <BacktestResultInlineCard
        onBuildRobustness={vi.fn()}
        onOpenDashboard={vi.fn()}
        onShowTrades={vi.fn()}
      />
    );

    expect(screen.getByText("Backtest dashboard is available.")).toBeVisible();
    expect(screen.queryByText("N/A")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open dashboard" })).toBeVisible();
  });
});
