"use client";

import { Area, AreaChart, ResponsiveContainer, Tooltip } from "recharts";

import { Button } from "@/components/ui/button";
import type { BacktestDashboardModel } from "@/lib/backtest-dashboard";
import type { BacktestReportCardModel } from "@/lib/backtest-report";
import { cn } from "@/lib/utils";

const PROFIT_COLOR = "#00bfa5";
const LOSS_COLOR = "#ff4050";
const GRID_COLOR = "rgba(148, 163, 184, 0.16)";

type BacktestResultInlineCardProps = {
  dashboard?: BacktestDashboardModel | null;
  error?: string | null;
  isLoading?: boolean;
  onBuildRobustness: () => void;
  onOpenDashboard: () => void;
  onShowTrades: () => void;
  previewSummary?: Record<string, unknown> | null;
  report?: BacktestReportCardModel | null;
};

export function BacktestResultInlineCard({
  dashboard = null,
  error = null,
  isLoading = false,
  onBuildRobustness,
  onOpenDashboard,
  onShowTrades,
  previewSummary = null,
  report = null,
}: BacktestResultInlineCardProps) {
  const summary = buildInlineSummary(dashboard, report, previewSummary);
  const sparkline = dashboard ? equitySparkline(dashboard) : previewSummarySparkline(previewSummary);
  const tone = summary.netPnlNumber !== null && summary.netPnlNumber < 0 ? "loss" : "profit";
  const hasMetrics = summary.hasMetrics;

  return (
    <section className="overflow-hidden rounded-[6px] border border-border bg-[#101416] text-foreground">
      <div className="border-border border-b px-3 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              {summary.symbol ? (
                <span className="rounded-full bg-emerald-500/15 px-2 py-1 font-medium text-emerald-200 text-[11px]">
                  {summary.symbol}
                </span>
              ) : null}
              {summary.timeframe ? (
                <span className="rounded-full bg-muted px-2 py-1 font-medium text-[11px]">
                  {summary.timeframe}
                </span>
              ) : null}
            </div>
          </div>
        </div>
      </div>

      <div className="grid gap-3 p-3 lg:grid-cols-[minmax(0,1fr)_13rem]">
        {hasMetrics ? (
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {summary.kpis.map((metric) => (
              <div className="rounded-[4px] border border-border bg-background/65 p-2" key={metric.label}>
                <p className="text-muted-foreground text-[11px]">{metric.label}</p>
                <p className={cn("mt-1 font-semibold text-sm", metric.tone === "profit" && "text-[#00bfa5]", metric.tone === "loss" && "text-[#ff4050]")}>
                  {metric.value}
                </p>
              </div>
            ))}
          </div>
        ) : (
          <div className="flex min-h-28 items-center rounded-[4px] border border-border bg-background/65 p-3 text-muted-foreground text-sm">
            Backtest dashboard is available.
          </div>
        )}

        <div className="min-h-28 rounded-[4px] border border-border bg-background/65 p-2">
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="font-medium text-xs">Equity preview</p>
            {isLoading && <span className="text-muted-foreground text-[10px]">Loading</span>}
          </div>
          {sparkline.length > 1 ? (
            <ResponsiveContainer height={84} width="100%">
              <AreaChart data={sparkline} margin={{ bottom: 2, left: 0, right: 0, top: 4 }}>
                <Tooltip contentStyle={tooltipStyle} formatter={(value) => formatCurrency(numberValue(value))} />
                <Area
                  dataKey="pnl"
                  fill={tone === "loss" ? "rgba(255, 64, 80, 0.18)" : "rgba(0, 191, 165, 0.18)"}
                  stroke={tone === "loss" ? LOSS_COLOR : PROFIT_COLOR}
                  strokeWidth={2}
                  type="monotone"
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex h-[84px] items-center justify-center rounded-[4px] border border-dashed border-border text-muted-foreground text-xs">
              {error ? "Dashboard preview unavailable" : "Equity curve pending"}
            </div>
          )}
        </div>
      </div>

      <div className="flex flex-wrap gap-2 border-border border-t px-3 py-3">
        <Button onClick={onOpenDashboard} size="sm" type="button" variant="outline">
          Open dashboard
        </Button>
        <Button onClick={onShowTrades} size="sm" type="button" variant="outline">
          Show trades
        </Button>
        <Button onClick={onBuildRobustness} size="sm" type="button" variant="outline">
          Build robustness report
        </Button>
      </div>
    </section>
  );
}

type InlineMetric = {
  label: string;
  tone?: "loss" | "profit";
  value: string;
};

function buildInlineSummary(
  dashboard: BacktestDashboardModel | null,
  report: BacktestReportCardModel | null,
  previewSummary: Record<string, unknown> | null
) {
  const kpis = dashboard?.performance.kpis ?? {};
  const previewMetrics = isRecord(previewSummary?.metrics) ? previewSummary.metrics : {};
  const reportMetrics = new Map((report?.metrics ?? []).map((metric) => [metric.key, metric.value]));
  const netPnl = firstNumber(kpis, ["net_profit", "pnl"]) ?? firstNumber(previewMetrics, ["net_pnl"]);
  const initialCapital = firstNumber(dashboard?.summary.assumptions ?? {}, ["initial_capital", "capital"]);
  const returnPct =
    firstNumber(kpis, ["net_profit_pct", "return_pct", "total_return"]) ??
    firstNumber(previewMetrics, ["return_pct"]) ??
    (netPnl !== null && initialCapital !== null && initialCapital !== 0 ? (netPnl / initialCapital) * 100 : null) ??
    percentageFromText(reportMetrics.get("return_pct") ?? reportMetrics.get("net_pnl"));
  const maxDrawdown = firstNumber(kpis, ["max_drawdown"]) ?? firstNumber(previewMetrics, ["max_drawdown"]);
  const maxDrawdownPct =
    firstNumber(kpis, ["max_drawdown_pct", "drawdown_pct"]) ??
    firstNumber(previewMetrics, ["max_drawdown_pct"]) ??
    percentageFromText(reportMetrics.get("max_drawdown"));
  const winRate = firstNumber(kpis, ["win_rate"]) ?? firstNumber(previewMetrics, ["win_rate"]) ?? percentageFromText(reportMetrics.get("win_rate"));
  const trades = firstNumber(kpis, ["trades", "trade_count", "closed_trades"]) ?? firstNumber(previewMetrics, ["trade_count"]) ?? integerFromText(reportMetrics.get("trade_count"));
  const profitFactor = firstNumber(kpis, ["profit_factor"]) ?? firstNumber(previewMetrics, ["profit_factor"]);
  const winners = firstNumber(kpis, ["winners", "winning_trades"]) ?? firstNumber(previewMetrics, ["winning_trades"]);
  const losers = firstNumber(kpis, ["losers", "losing_trades"]) ?? firstNumber(previewMetrics, ["losing_trades"]);
  const netPnlValue = netPnl !== null ? formatCurrency(netPnl) : reportMetrics.get("net_pnl") ?? "N/A";
  const maxDrawdownValue =
    maxDrawdown !== null && maxDrawdownPct !== null
      ? `${formatCurrency(-Math.abs(maxDrawdown))} ${formatPercent(maxDrawdownPct)}`
      : maxDrawdownPct !== null
        ? formatPercent(maxDrawdownPct)
        : reportMetrics.get("max_drawdown") ?? "N/A";
  const winRateValue =
    winRate !== null
      ? `${formatPercent(winRate)}${winners !== null && losers !== null ? ` ${formatCompactInteger(winners)} | ${formatCompactInteger(losers)}` : ""}`
      : reportMetrics.get("win_rate") ?? "N/A";

  const kpiList: InlineMetric[] = [
    { label: "Net P&L", value: netPnlValue, tone: netPnl !== null && netPnl < 0 ? "loss" : "profit" },
    { label: "Return", value: returnPct !== null ? formatPercent(returnPct) : "N/A", tone: returnPct !== null && returnPct < 0 ? "loss" : "profit" },
    { label: "Max drawdown", value: maxDrawdownValue, tone: "loss" },
    { label: "Win rate", value: winRateValue },
    { label: "Trades", value: trades !== null ? formatCompactInteger(trades) : "N/A" },
    { label: "Profit factor", value: profitFactor !== null ? profitFactor.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "N/A" },
  ];

  return {
    hasMetrics: dashboard !== null || report !== null || isRecord(previewSummary?.metrics),
    kpis: kpiList,
    netPnlNumber: netPnl,
    symbol: dashboard?.summary.symbol ?? stringValue(previewSummary?.symbol) ?? reportMetricValue(report, "symbol") ?? null,
    timeframe: dashboard?.summary.timeframe ?? stringValue(previewSummary?.timeframe) ?? reportMetricValue(report, "timeframe") ?? null,
    warnings: [...(dashboard?.summary.warnings ?? []), ...(report?.warnings ?? [])],
  };
}

function equitySparkline(dashboard: BacktestDashboardModel) {
  return dashboard.performance.equity
    .map((point, index) => {
      const pnl = firstNumber(point, ["pnl", "equity", "value"]);
      if (pnl === null) {
        return null;
      }
      return {
        index: firstNumber(point, ["index", "bar_index"]) ?? index,
        pnl,
      };
    })
    .filter((point): point is { index: number; pnl: number } => point !== null);
}

function previewSummarySparkline(previewSummary: Record<string, unknown> | null) {
  const points = Array.isArray(previewSummary?.equity_preview) ? previewSummary.equity_preview : [];
  return points
    .map((point, index) => {
      if (!isRecord(point)) {
        return null;
      }
      const pnl = firstNumber(point, ["pnl", "equity", "value"]);
      if (pnl === null) {
        return null;
      }
      return {
        index: firstNumber(point, ["index", "bar_index"]) ?? index,
        pnl,
      };
    })
    .filter((point): point is { index: number; pnl: number } => point !== null);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function firstNumber(record: Record<string, unknown>, keys: string[]): number | null {
  for (const key of keys) {
    const value = numberValue(record[key]);
    if (value !== null) {
      return value;
    }
  }
  return null;
}

function numberValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value.replace(/[%,$\s]/g, ""));
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function percentageFromText(value: string | undefined): number | null {
  if (!value) {
    return null;
  }
  const match = value.match(/-?\d+(?:\.\d+)?(?=%)/);
  return match ? numberValue(match[0]) : null;
}

function integerFromText(value: string | undefined): number | null {
  if (!value) {
    return null;
  }
  const match = value.match(/-?\d+(?:,\d{3})*/);
  return match ? numberValue(match[0]) : null;
}

function reportMetricValue(report: BacktestReportCardModel | null, key: string) {
  return [...(report?.assumptions ?? []), ...(report?.metrics ?? [])].find(
    (metric) => metric.key === key
  )?.value;
}

function formatCurrency(value: number | null) {
  if (value === null) {
    return "N/A";
  }
  const sign = value > 0 ? "+" : "";
  const absolute = Math.abs(value);
  if (absolute >= 1_000_000) {
    return `${sign}${(value / 1_000_000).toLocaleString(undefined, { maximumFractionDigits: 1 })}M USD`;
  }
  if (absolute >= 1_000) {
    return `${sign}${(value / 1_000).toLocaleString(undefined, { maximumFractionDigits: 1 })}K USD`;
  }
  return `${sign}${value.toLocaleString(undefined, { maximumFractionDigits: 2 })} USD`;
}

function formatPercent(value: number) {
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
}

function formatCompactInteger(value: number) {
  return Math.round(value).toLocaleString();
}

const tooltipStyle = {
  backgroundColor: "#121619",
  border: `1px solid ${GRID_COLOR}`,
  borderRadius: 4,
  color: "#f8fafc",
};
