"use client";

import { useState, type ReactNode } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { BacktestDashboardMatrixRow, BacktestDashboardModel } from "@/lib/backtest-dashboard";
import { cn } from "@/lib/utils";

type DashboardTab = "performance" | "analysis" | "log";

const DASHBOARD_TABS = [
  { label: "Performance", value: "performance" },
  { label: "Trades Analysis", value: "analysis" },
  { label: "Trades Log", value: "log" },
] satisfies Array<{ label: string; value: DashboardTab }>;

const PROFIT_COLOR = "#00bfa5";
const LOSS_COLOR = "#ff4050";
const GRID_COLOR = "rgba(148, 163, 184, 0.14)";
const AXIS_COLOR = "rgba(226, 232, 240, 0.55)";

export function BacktestDashboardArtifact({ dashboard }: { dashboard: BacktestDashboardModel }) {
  const [tab, setTab] = useState<DashboardTab>("performance");
  return (
    <div className="mx-auto w-full max-w-7xl overflow-hidden rounded-[6px] border border-border bg-[#111416] text-foreground">
      <header className="border-border border-b px-4 pt-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full bg-emerald-500/15 px-2 py-1 font-medium text-emerald-200 text-xs">
            {dashboard.summary.symbol}
          </span>
          <span className="rounded-full bg-muted px-2 py-1 font-medium text-xs">{dashboard.summary.timeframe}</span>
        </div>
        <nav className="mt-4 flex gap-4 text-sm">
          {DASHBOARD_TABS.map(({ label, value }) => (
            <button
              className={cn(
                "border-b-2 px-0 pb-3 text-muted-foreground transition-colors",
                tab === value ? "border-foreground text-foreground" : "border-transparent hover:text-foreground"
              )}
              key={value}
              onClick={() => setTab(value)}
              type="button"
            >
              {label}
            </button>
          ))}
        </nav>
      </header>
      <div className="p-4">
        {tab === "performance" && <PerformanceTab dashboard={dashboard} />}
        {tab === "analysis" && <TradesAnalysisTab dashboard={dashboard} />}
        {tab === "log" && <TradesLogTab dashboard={dashboard} />}
      </div>
    </div>
  );
}

function PerformanceTab({ dashboard }: { dashboard: BacktestDashboardModel }) {
  const kpis = dashboard.performance.kpis;
  return (
    <div className="space-y-5">
      <ChartPanel className="h-[330px]">
        <ResponsiveContainer height="100%" width="100%">
          <AreaChart data={dashboard.performance.equity} margin={{ bottom: 8, left: 10, right: 10, top: 16 }}>
            <defs>
              <linearGradient id="equityFill" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor={PROFIT_COLOR} stopOpacity={0.76} />
                <stop offset="100%" stopColor={PROFIT_COLOR} stopOpacity={0.12} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke={GRID_COLOR} vertical={false} />
            <XAxis dataKey="index" hide />
            <YAxis stroke={AXIS_COLOR} tickFormatter={compactNumber} width={72} />
            <Tooltip contentStyle={tooltipStyle} formatter={currencyTooltip} />
            <ReferenceLine stroke={AXIS_COLOR} y={0} />
            <Area dataKey="pnl" fill="url(#equityFill)" stroke={PROFIT_COLOR} strokeWidth={2} type="monotone" />
          </AreaChart>
        </ResponsiveContainer>
      </ChartPanel>
      <div className="grid gap-2 rounded-[6px] border border-border bg-background/35 p-3 sm:grid-cols-2 lg:grid-cols-5">
        <Kpi label="Net Profit" value={formatCurrency(numberValue(kpis.net_profit))} tone="profit" />
        <Kpi label="Trades" value={formatNumber(numberValue(kpis.trades))} />
        <Kpi
          label="Win Rate"
          value={`${formatPercent(numberValue(kpis.win_rate))} ${formatNumber(numberValue(kpis.winners))} | ${formatNumber(numberValue(kpis.losers))}`}
        />
        <Kpi
          label="Max Drawdown"
          value={`${formatCurrency(numberValue(kpis.max_drawdown))} ${formatPercent(numberValue(kpis.max_drawdown_pct))}`}
        />
        <Kpi label="Profit Factor" value={formatNumber(numberValue(kpis.profit_factor))} tone="profit" />
      </div>
      <section className="space-y-3">
        <h3 className="font-semibold text-base">Performance</h3>
        <div className="grid gap-4 lg:grid-cols-2">
          <ChartBlock title="Net Daily P&L (USD)">
            <BarSeriesChart data={dashboard.performance.dailyPnl} xKey="date" />
          </ChartBlock>
          <ChartBlock title="Weekday Performance (USD)">
            <BarSeriesChart data={dashboard.performance.weekdayPnl} xKey="weekday" />
          </ChartBlock>
        </div>
        <MetricMatrix rows={dashboard.performance.matrix} />
      </section>
    </div>
  );
}

function TradesAnalysisTab({ dashboard }: { dashboard: BacktestDashboardModel }) {
  const winrate = dashboard.tradesAnalysis.winrate;
  const pieData = [
    { name: "Winners", value: numberValue(winrate.winners) ?? 0, fill: PROFIT_COLOR },
    { name: "Losers", value: numberValue(winrate.losers) ?? 0, fill: LOSS_COLOR },
  ];
  return (
    <div className="space-y-5">
      <div className="grid gap-4 lg:grid-cols-[1fr_0.9fr]">
        <ChartBlock title="P&L Distribution (USD)">
          <ResponsiveContainer height="100%" width="100%">
            <BarChart data={dashboard.tradesAnalysis.pnlDistribution.bins} margin={{ bottom: 26, left: 6, right: 8, top: 10 }}>
              <CartesianGrid stroke={GRID_COLOR} vertical={false} />
              <XAxis dataKey="start" stroke={AXIS_COLOR} tickFormatter={compactNumber} angle={-38} textAnchor="end" />
              <YAxis stroke={AXIS_COLOR} />
              <Tooltip contentStyle={tooltipStyle} />
              <Bar dataKey="count">
                {dashboard.tradesAnalysis.pnlDistribution.bins.map((item, index) => (
                  <Cell fill={(numberValue(item.start) ?? 0) < 0 ? LOSS_COLOR : PROFIT_COLOR} key={index} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartBlock>
        <ChartBlock title="Winrate">
          <div className="grid h-full grid-cols-[1fr_auto] items-center gap-4">
            <ResponsiveContainer height="100%" width="100%">
              <PieChart>
                <Pie data={pieData} dataKey="value" innerRadius="58%" outerRadius="82%" paddingAngle={1}>
                  {pieData.map((item) => (
                    <Cell fill={item.fill} key={item.name} />
                  ))}
                </Pie>
                <text dominantBaseline="middle" fill="currentColor" fontSize="28" fontWeight="700" textAnchor="middle" x="50%" y="47%">
                  {formatPercent(numberValue(winrate.win_rate))}
                </text>
                <text dominantBaseline="middle" fill={AXIS_COLOR} fontSize="11" fontWeight="700" textAnchor="middle" x="50%" y="58%">
                  WINRATE
                </text>
              </PieChart>
            </ResponsiveContainer>
            <div className="space-y-3 pr-4 text-sm">
              <LegendDot label="winners" value={formatNumber(numberValue(winrate.winners))} />
              <LegendDot label="losers" value={formatNumber(numberValue(winrate.losers))} tone="loss" />
            </div>
          </div>
        </ChartBlock>
      </div>
      <MetricMatrix rows={dashboard.tradesAnalysis.tradeStats} />
      <ChartBlock panelClassName="h-[300px]" title="Duration vs P&L (USD)">
        <ResponsiveContainer height="100%" width="100%">
          <ScatterChart margin={{ bottom: 12, left: 8, right: 12, top: 10 }}>
            <CartesianGrid stroke={GRID_COLOR} />
            <XAxis dataKey="duration_bars" name="Duration" stroke={AXIS_COLOR} tickFormatter={compactNumber} type="number" />
            <YAxis dataKey="pnl" name="P&L" stroke={AXIS_COLOR} tickFormatter={compactNumber} />
            <Tooltip contentStyle={tooltipStyle} formatter={currencyTooltip} />
            <ReferenceLine stroke={AXIS_COLOR} y={0} />
            <Scatter data={dashboard.tradesAnalysis.durationVsPnl}>
              {dashboard.tradesAnalysis.durationVsPnl.map((item, index) => (
                <Cell fill={(numberValue(item.pnl) ?? 0) < 0 ? LOSS_COLOR : PROFIT_COLOR} key={index} />
              ))}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </ChartBlock>
      <MetricMatrix rows={dashboard.tradesAnalysis.durationStats} />
    </div>
  );
}

function TradesLogTab({ dashboard }: { dashboard: BacktestDashboardModel }) {
  const totalRows = dashboard.tradesLog.totalRows;
  const isTruncated = totalRows !== null && totalRows > dashboard.tradesLog.rows.length;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2 text-muted-foreground text-sm">
        <p className="text-xs">
          {isTruncated
            ? `Showing latest ${dashboard.tradesLog.rows.length} of ${totalRows} trades`
            : `${dashboard.tradesLog.rows.length} trades`}
        </p>
        <div className="flex items-center gap-2">
          <span>View Mode</span>
          <button
            aria-pressed="true"
            className="rounded-[5px] border border-border bg-muted px-2 py-1 text-xs text-foreground"
            type="button"
          >
            List
          </button>
          <button
            className="cursor-not-allowed rounded-[5px] border border-border px-2 py-1 text-xs opacity-50"
            disabled
            title="Calendar view is not available in this preview yet"
            type="button"
          >
            Calendar
          </button>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[920px] text-left text-sm">
          <thead className="text-muted-foreground">
            <tr className="border-border border-b">
              <th className="px-4 py-3">Trade #</th>
              <th className="px-4 py-3">Entry</th>
              <th className="px-4 py-3">Exit</th>
              <th className="px-4 py-3 text-right">Net P&L</th>
              <th className="px-4 py-3 text-right">Cumulative P&L</th>
            </tr>
          </thead>
          <tbody>
            {dashboard.tradesLog.rows.map((row) => {
              const entry = recordValue(row.entry);
              const exit = recordValue(row.exit);
              const pnl = numberValue(row.net_pnl);
              return (
                <tr className="border-border border-b" key={String(row.trade_number)}>
                  <td className="px-4 py-3 font-medium">
                    {formatNumber(numberValue(row.trade_number))}
                    <span
                      className={cn(
                        "ml-2 rounded-[4px] px-1.5 py-0.5 text-xs",
                        row.side === "short" ? "bg-red-500/20 text-red-200" : "bg-emerald-500/20 text-emerald-200"
                      )}
                    >
                      {String(row.side ?? "long")}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <p className="text-muted-foreground">{formatTimestamp(entry.timestamp)}</p>
                    <p className="font-medium">{formatPrice(numberValue(entry.price))} USD</p>
                  </td>
                  <td className="px-4 py-3">
                    <p className="text-muted-foreground">{formatTimestamp(exit.timestamp)}</p>
                    <p className="font-medium">{formatPrice(numberValue(exit.price))} USD</p>
                  </td>
                  <td className={cn("px-4 py-3 text-right font-medium", toneClass(pnl))}>{formatCurrency(pnl)}</td>
                  <td className="px-4 py-3 text-right font-medium">{formatCurrency(numberValue(row.cumulative_pnl))}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ChartBlock({
  children,
  className,
  panelClassName,
  title,
}: {
  children: ReactNode;
  className?: string;
  panelClassName?: string;
  title: string;
}) {
  return (
    <section className={cn("space-y-3", className)}>
      <h3 className="font-semibold text-sm">{title}</h3>
      <ChartPanel className={panelClassName}>{children}</ChartPanel>
    </section>
  );
}

function ChartPanel({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("h-[260px] min-h-[220px] rounded-[6px] border border-border bg-background/35 p-3", className)}>{children}</div>;
}

function BarSeriesChart({ data, xKey }: { data: Array<Record<string, unknown>>; xKey: string }) {
  return (
    <ResponsiveContainer height="100%" width="100%">
      <BarChart data={data} margin={{ bottom: 20, left: 8, right: 8, top: 10 }}>
        <CartesianGrid stroke={GRID_COLOR} vertical={false} />
        <XAxis dataKey={xKey} stroke={AXIS_COLOR} tick={{ fontSize: 11 }} />
        <YAxis stroke={AXIS_COLOR} tickFormatter={compactNumber} width={70} />
        <Tooltip contentStyle={tooltipStyle} formatter={currencyTooltip} />
        <ReferenceLine stroke={AXIS_COLOR} y={0} />
        <Bar dataKey="pnl">
          {data.map((item, index) => (
            <Cell fill={(numberValue(item.pnl) ?? 0) < 0 ? LOSS_COLOR : PROFIT_COLOR} key={index} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function MetricMatrix({ rows }: { rows: BacktestDashboardMatrixRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-sm">
        <thead className="text-muted-foreground">
          <tr className="border-border border-b">
            <th className="px-4 py-2 text-left" />
            <th className="px-4 py-2 text-right">All</th>
            <th className="px-4 py-2 text-right">Long</th>
            <th className="px-4 py-2 text-right">Short</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr className="border-border border-b" key={row.label}>
              <td className="px-4 py-2 text-muted-foreground">{row.label}</td>
              <td className={cn("px-4 py-2 text-right font-medium", row.format === "currency" && toneClass(row.all))}>
                {formatMatrixValue(row, "all")}
              </td>
              <td className={cn("px-4 py-2 text-right font-medium", row.format === "currency" && toneClass(row.long))}>
                {formatMatrixValue(row, "long")}
              </td>
              <td className={cn("px-4 py-2 text-right font-medium", row.format === "currency" && toneClass(row.short))}>
                {formatMatrixValue(row, "short")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Kpi({ label, tone, value }: { label: string; tone?: "profit"; value: string }) {
  return (
    <div>
      <p className="text-muted-foreground text-xs uppercase">{label}</p>
      <p className={cn("mt-1 font-semibold text-sm", tone === "profit" && "text-[#00bfa5]")}>{value}</p>
    </div>
  );
}

function LegendDot({ label, tone, value }: { label: string; tone?: "loss"; value: string }) {
  return (
    <div>
      <p className={cn("font-semibold", tone === "loss" ? "text-[#ff4050]" : "text-[#00bfa5]")}>{value}</p>
      <p className="text-muted-foreground text-xs">{label}</p>
    </div>
  );
}

function formatMatrixValue(row: BacktestDashboardMatrixRow, key: "all" | "long" | "short") {
  const value = row[key];
  if (row.format === "currency") {
    return formatCurrency(value);
  }
  if (row.format === "percent") {
    return formatPercent(value);
  }
  if (row.format === "drawdown") {
    const pct = row.extra?.[key] ?? null;
    return `${formatCurrency(value)} ${formatPercent(pct)}`;
  }
  return formatNumber(value);
}

function formatCurrency(value: number | null) {
  if (value === null) {
    return "N/A";
  }
  const sign = value > 0 ? "+" : "";
  return `${sign}${compactNumber(value)} USD`;
}

function formatPercent(value: number | null) {
  return value === null ? "N/A" : `${formatNumber(value)}%`;
}

function formatNumber(value: number | null) {
  return value === null ? "N/A" : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function formatPrice(value: number | null) {
  return value === null ? "N/A" : value.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function compactNumber(value: unknown) {
  const number = numberValue(value);
  if (number === null) {
    return "N/A";
  }
  return Intl.NumberFormat(undefined, { maximumFractionDigits: 1, notation: "compact" }).format(number);
}

function formatTimestamp(value: unknown) {
  if (typeof value !== "string" || !value) {
    return "N/A";
  }
  return value.replace("T", " ").replace(".000Z", " UTC");
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function recordValue(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function toneClass(value: number | null) {
  if (value === null) {
    return "";
  }
  return value < 0 ? "text-[#ff4050]" : "text-[#00bfa5]";
}

const tooltipStyle = {
  backgroundColor: "#111416",
  border: "1px solid rgba(148, 163, 184, 0.2)",
  borderRadius: 6,
  color: "#f8fafc",
};

function currencyTooltip(value: unknown) {
  return formatCurrency(numberValue(value));
}
