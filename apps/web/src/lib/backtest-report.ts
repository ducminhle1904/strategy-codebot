export type BacktestReportMetric = {
  label: string;
  value: string;
};

export type BacktestReportCardModel = {
  kind: "report";
  metrics: BacktestReportMetric[];
  warnings: string[];
  assumptions: BacktestReportMetric[];
  dataSource: string | null;
  reproducibilityHash: string | null;
};

export type BacktestVariantCardModel = {
  kind: "variant_comparison";
  variantGroupId: string | null;
  sharedCacheKey: string | null;
  warnings: string[];
  variants: Array<{
    name: string;
    runId: string | null;
    status: string;
    symbol: string;
    timeframe: string;
  }>;
};

export type BacktestArtifactCardModel = BacktestReportCardModel | BacktestVariantCardModel;

export function parseBacktestArtifactPreview(kind: string, value: unknown): BacktestArtifactCardModel | null {
  if (kind === "backtest_report") {
    return parseBacktestReport(value);
  }
  if (kind === "backtest_variant_comparison") {
    return parseBacktestVariantComparison(value);
  }
  return null;
}

export function parseBacktestReport(value: unknown): BacktestReportCardModel | null {
  if (!isRecord(value)) {
    return null;
  }
  const metrics = isRecord(value.metrics) ? value.metrics : {};
  const assumptions = isRecord(value.assumptions)
    ? value.assumptions
    : isRecord(value.config)
      ? value.config
      : {};
  const tradeCount =
    pickValue(metrics, ["trade_count", "trades"]) ??
    (Array.isArray(value.trades) ? value.trades.length : null);
  return {
    kind: "report",
    metrics: [
      { label: "PnL", value: formatBacktestValue(pickValue(metrics, ["pnl", "net_profit", "total_return"])) },
      { label: "Max drawdown", value: formatBacktestValue(pickValue(metrics, ["max_drawdown", "max_drawdown_pct"])) },
      { label: "Trade count", value: formatBacktestValue(tradeCount) },
      { label: "Win rate", value: formatBacktestValue(pickValue(metrics, ["win_rate"])) },
      { label: "Sharpe", value: formatBacktestValue(pickValue(metrics, ["sharpe"])) },
      { label: "Sortino", value: formatBacktestValue(pickValue(metrics, ["sortino"])) },
    ],
    assumptions: [
      { label: "Symbol", value: formatBacktestValue(assumptions.symbol) },
      { label: "Timeframe", value: formatBacktestValue(assumptions.timeframe) },
      {
        label: "Range",
        value: `${formatBacktestValue(assumptions.start)} -> ${formatBacktestValue(assumptions.end)}`,
      },
      { label: "Initial capital", value: formatBacktestValue(assumptions.initial_capital) },
      { label: "Fee bps", value: formatBacktestValue(assumptions.fee_bps) },
      { label: "Slippage bps", value: formatBacktestValue(assumptions.slippage_bps) },
    ],
    warnings: stringArray(value.warnings),
    dataSource:
      typeof assumptions.data_source === "string"
        ? assumptions.data_source
        : typeof value.data_source === "string"
          ? value.data_source
          : null,
    reproducibilityHash:
      typeof value.reproducibility_hash === "string"
        ? value.reproducibility_hash
        : typeof value.reproducibilityHash === "string"
          ? value.reproducibilityHash
          : null,
  };
}

export function parseBacktestVariantComparison(value: unknown): BacktestVariantCardModel | null {
  if (!isRecord(value)) {
    return null;
  }
  const variants = Array.isArray(value.variants) ? value.variants : [];
  return {
    kind: "variant_comparison",
    variantGroupId: typeof value.variant_group_id === "string" ? value.variant_group_id : null,
    sharedCacheKey: typeof value.shared_cache_key === "string" ? value.shared_cache_key : null,
    warnings: stringArray(value.warnings),
    variants: variants.filter(isRecord).map((variant) => {
      const config = isRecord(variant.backtest_config) ? variant.backtest_config : {};
      return {
        name: typeof variant.name === "string" ? variant.name : "Variant",
        runId: typeof variant.run_id === "string" ? variant.run_id : null,
        status: typeof variant.status === "string" ? variant.status : "queued",
        symbol: formatBacktestValue(config.symbol),
        timeframe: formatBacktestValue(config.timeframe),
      };
    }),
  };
}

export function formatBacktestValue(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return Number.isInteger(value)
      ? value.toLocaleString()
      : value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }
  if (typeof value === "string" && value.trim()) {
    return value;
  }
  return "N/A";
}

function pickValue(record: Record<string, unknown>, keys: string[]): unknown {
  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null) {
      return value;
    }
  }
  return null;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    : [];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
