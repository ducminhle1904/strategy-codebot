export type BacktestDashboardMatrixRow = {
  label: string;
  format: string;
  all: number | null;
  long: number | null;
  short: number | null;
  extra?: {
    all: number | null;
    long: number | null;
    short: number | null;
  };
};

export type BacktestDashboardModel = {
  kind: "dashboard";
  summary: {
    title: string;
    symbol: string;
    timeframe: string;
    candleTimeframe: string | null;
    engine: string;
    evidenceLabel: string;
    assumptions: Record<string, unknown>;
    warnings: string[];
  };
  performance: {
    equity: Array<Record<string, unknown>>;
    dailyPnl: Array<Record<string, unknown>>;
    weekdayPnl: Array<Record<string, unknown>>;
    kpis: Record<string, unknown>;
    matrix: BacktestDashboardMatrixRow[];
  };
  tradesAnalysis: {
    pnlDistribution: {
      bins: Array<Record<string, unknown>>;
      references: Record<string, unknown>;
    };
    winrate: Record<string, unknown>;
    durationVsPnl: Array<Record<string, unknown>>;
    tradeStats: BacktestDashboardMatrixRow[];
    durationStats: BacktestDashboardMatrixRow[];
  };
  tradesLog: {
    rows: Array<Record<string, unknown>>;
    totalRows: number | null;
  };
};

export function parseBacktestDashboardArtifactPreview(kind: string, value: unknown): BacktestDashboardModel | null {
  if (kind !== "backtest_dashboard" || !isRecord(value)) {
    return null;
  }
  const summary = isRecord(value.summary) ? value.summary : {};
  const performance = isRecord(value.performance) ? value.performance : {};
  const tradesAnalysis = isRecord(value.trades_analysis) ? value.trades_analysis : {};
  const tradesLog = isRecord(value.trades_log) ? value.trades_log : {};
  const pnlDistribution = isRecord(tradesAnalysis.pnl_distribution) ? tradesAnalysis.pnl_distribution : {};
  return {
    kind: "dashboard",
    summary: {
      title: stringValue(summary.title, "Backtest result"),
      symbol: stringValue(summary.symbol, "Unknown"),
      timeframe: stringValue(summary.timeframe, "N/A"),
      candleTimeframe: stringOrNull(summary.candle_timeframe),
      engine: "local preview",
      evidenceLabel: userFacingBacktestDashboardText(stringValue(summary.evidence_label, "Local preview evidence")),
      assumptions: isRecord(summary.assumptions) ? summary.assumptions : {},
      warnings: stringArray(summary.warnings).map(userFacingBacktestDashboardText),
    },
    performance: {
      equity: recordArray(performance.equity),
      dailyPnl: recordArray(performance.daily_pnl),
      weekdayPnl: recordArray(performance.weekday_pnl),
      kpis: isRecord(performance.kpis) ? performance.kpis : {},
      matrix: matrixRows(performance.matrix),
    },
    tradesAnalysis: {
      pnlDistribution: {
        bins: recordArray(pnlDistribution.bins),
        references: isRecord(pnlDistribution.references) ? pnlDistribution.references : {},
      },
      winrate: isRecord(tradesAnalysis.winrate) ? tradesAnalysis.winrate : {},
      durationVsPnl: recordArray(tradesAnalysis.duration_vs_pnl),
      tradeStats: matrixRows(tradesAnalysis.trade_stats),
      durationStats: matrixRows(tradesAnalysis.duration_stats),
    },
    tradesLog: {
      rows: recordArray(tradesLog.rows),
      totalRows: numberOrNull(tradesLog.total_rows),
    },
  };
}

function matrixRows(value: unknown): BacktestDashboardMatrixRow[] {
  return recordArray(value).map((row) => {
    const extra = isRecord(row.extra) ? row.extra : null;
    return {
      label: stringValue(row.label, "Metric"),
      format: stringValue(row.format, "number"),
      all: numberOrNull(row.all),
      long: numberOrNull(row.long),
      short: numberOrNull(row.short),
      ...(extra
        ? {
            extra: {
              all: numberOrNull(extra.all),
              long: numberOrNull(extra.long),
              short: numberOrNull(extra.short),
            },
          }
        : {}),
    };
  });
}

function recordArray(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function stringValue(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function userFacingBacktestDashboardText(value: string): string {
  return value
    .replace(/PineForge local Pine preview evidence only/gi, "Local sandbox preview evidence only")
    .replace(/PineForge local Pine preview evidence/gi, "Local sandbox preview evidence")
    .replace(/PineForge Preview/gi, "Backtest Preview")
    .replace(/PineForge compile\/backtest/gi, "local preview")
    .replace(/PineForge local Pine preview/gi, "local sandbox preview")
    .replace(/PineForge output/gi, "Local sandbox preview output")
    .replace(/pineforge-engine/gi, "local preview")
    .replace(/pineforge-runner/gi, "local preview")
    .replace(/PineForge/gi, "local preview");
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
