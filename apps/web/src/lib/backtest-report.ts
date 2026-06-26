export type BacktestReportMetric = {
  key: string;
  label: string;
  value: string;
};

export type BacktestReportCardModel = {
  kind: "report";
  metrics: BacktestReportMetric[];
  warnings: string[];
  qualityStatus: string | null;
  qualityFlags: string[];
  assumptions: BacktestReportMetric[];
  robustness: BacktestReportMetric[];
  promotionDecision: string | null;
  promotionReasons: string[];
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

export type BacktestRobustnessCardModel = {
  kind: "robustness_report";
  recommendation: string | null;
  boundary: string | null;
  metrics: BacktestReportMetric[];
  checkCounts: {
    fail: number;
    pass: number;
    warn: number;
  };
  checks: Array<{
    id: string;
    message: string;
    observed: string;
    status: "fail" | "pass" | "warn";
  }>;
  tradeSampleCount: number;
  topLoserCount: number;
  topWinnerCount: number;
  warnings: string[];
};

type RobustnessCheckStatus = BacktestRobustnessCardModel["checks"][number]["status"];

export type BacktestArtifactCardModel =
  | BacktestReportCardModel
  | BacktestVariantCardModel
  | BacktestRobustnessCardModel;

export function parseBacktestArtifactPreview(kind: string, value: unknown): BacktestArtifactCardModel | null {
  if (kind === "backtest_report") {
    return parseBacktestReport(value);
  }
  if (kind === "backtest_variant_comparison") {
    return parseBacktestVariantComparison(value);
  }
  if (kind === "robustness_report") {
    return parseBacktestRobustnessReport(value);
  }
  return null;
}

function metric(key: string, label: string, value: string): BacktestReportMetric {
  return { key, label, value };
}

function visibleMetrics(metrics: BacktestReportMetric[]) {
  return metrics.filter((item) => item.value !== "N/A" && item.value !== "N/A -> N/A");
}

export function parseBacktestReport(value: unknown): BacktestReportCardModel | null {
  if (!isRecord(value)) {
    return null;
  }
  const metrics = isRecord(value.metrics) ? value.metrics : {};
  const qualityStatus =
    typeof metrics.quality_status === "string"
      ? metrics.quality_status
      : typeof value.quality_status === "string"
        ? value.quality_status
        : null;
  const qualityFlags = uniqueStrings([
    ...stringArray(metrics.quality_flags),
    ...stringArray(value.quality_flags),
  ]);
  const assumptions = isRecord(value.assumptions)
    ? value.assumptions
    : isRecord(value.config)
      ? value.config
      : {};
  const robustnessReport = isRecord(value.robustness_report) ? value.robustness_report : {};
  const robustnessMetrics = isRecord(robustnessReport.metrics) ? robustnessReport.metrics : {};
  const promotionDecision = isRecord(value.promotion_decision) ? value.promotion_decision : {};
  const tradeCount =
    pickValue(metrics, ["trade_count", "trades"]) ??
    (Array.isArray(value.trades) ? value.trades.length : null);
  const promotionReasons = stringArray(promotionDecision.reasons);
  const warnings = uniqueStrings([
    ...qualityFlags.map((flag) => qualityWarning(flag)),
    ...stringArray(value.warnings),
    ...promotionReasons,
  ].map(userFacingBacktestText));
  return {
    kind: "report",
    metrics: visibleMetrics([
      metric("net_pnl", "PnL", formatPnlMetric(pickValue(metrics, ["pnl", "net_profit", "total_return"]))),
      metric("max_drawdown", "Max drawdown", formatBacktestValue(pickValue(metrics, ["max_drawdown", "max_drawdown_pct"]))),
      metric("trade_count", "Trade count", formatBacktestValue(tradeCount)),
      metric("win_rate", "Win rate", formatBacktestValue(pickValue(metrics, ["win_rate"]))),
      metric("sharpe", "Sharpe", formatBacktestValue(pickValue(metrics, ["sharpe"]))),
      metric("sortino", "Sortino", formatBacktestValue(pickValue(metrics, ["sortino"]))),
    ]),
    assumptions: visibleMetrics([
      metric("symbol", "Symbol", formatBacktestValue(assumptions.symbol)),
      metric("timeframe", "Signal timeframe", formatBacktestValue(pickValue(assumptions, ["signal_timeframe", "timeframe"]))),
      metric("candle_timeframe", "Candle timeframe", formatBacktestValue(pickValue(assumptions, ["candle_timeframe"]))),
      metric("range", "Range", `${formatBacktestValue(assumptions.start)} -> ${formatBacktestValue(assumptions.end)}`),
      metric("initial_capital", "Initial capital", formatBacktestValue(assumptions.initial_capital)),
      metric("fee_bps", "Fee bps", formatBacktestValue(assumptions.fee_bps)),
      metric("slippage_bps", "Slippage bps", formatBacktestValue(assumptions.slippage_bps)),
    ]),
    robustness: visibleMetrics([
      metric("robustness", "Robustness", formatBacktestValue(robustnessReport.status)),
      metric("sample_size", "Sample size", formatBacktestValue(robustnessMetrics.sample_size)),
      metric("backtest_days", "Backtest days", formatBacktestValue(robustnessMetrics.backtest_days)),
      metric("max_loss_streak", "Max loss streak", formatBacktestValue(robustnessMetrics.max_loss_streak)),
    ]),
    promotionDecision:
      typeof promotionDecision.decision === "string"
        ? promotionDecision.decision
        : null,
    promotionReasons: promotionReasons.map(userFacingBacktestText),
    qualityStatus,
    qualityFlags,
    warnings,
    dataSource:
      typeof assumptions.data_source === "string"
        ? userFacingBacktestText(assumptions.data_source)
        : typeof value.data_source === "string"
          ? userFacingBacktestText(value.data_source)
          : null,
    reproducibilityHash:
      typeof value.reproducibility_hash === "string"
        ? value.reproducibility_hash
        : typeof value.reproducibilityHash === "string"
          ? value.reproducibilityHash
          : null,
  };
}

function qualityWarning(flag: string) {
  if (flag === "position_sizing_mismatch") {
    return "Position sizing mismatch: repair sizing before evaluating this preview.";
  }
  if (flag === "commission_exceeds_capital") {
    return "Commission exceeds initial capital; quantity sizing is likely invalid.";
  }
  if (flag === "large_trade_notional") {
    return "Trade notional is large versus initial capital; review position sizing.";
  }
  return flag.replace(/_/g, " ");
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

export function parseBacktestRobustnessReport(value: unknown): BacktestRobustnessCardModel | null {
  if (!isRecord(value)) {
    return null;
  }
  const metrics = isRecord(value.metrics) ? value.metrics : {};
  const assumptionReview = isRecord(value.assumption_review) ? value.assumption_review : {};
  const tradeSamples = isRecord(value.trade_samples) ? value.trade_samples : {};
  const checks = (Array.isArray(value.checks) ? value.checks : [])
    .filter(isRecord)
    .map((check) => {
      const status: RobustnessCheckStatus =
        check.status === "fail" || check.status === "warn" ? check.status : "pass";
      return {
        id: typeof check.id === "string" && check.id.trim() ? check.id : "check",
        message: formatBacktestValue(check.message),
        observed: formatBacktestValue(check.observed),
        status,
      };
    });
  const checkCounts = checks.reduce(
    (counts, check) => {
      counts[check.status] += 1;
      return counts;
    },
    { fail: 0, pass: 0, warn: 0 } satisfies BacktestRobustnessCardModel["checkCounts"]
  );
  return {
    kind: "robustness_report",
    recommendation: typeof value.recommendation === "string" ? value.recommendation : null,
    boundary: typeof value.boundary === "string" ? userFacingBacktestText(value.boundary) : null,
    metrics: visibleMetrics([
      metric("trade_count", "Trade count", formatBacktestValue(metrics.trade_count)),
      metric("win_rate", "Win rate", formatBacktestValue(metrics.win_rate)),
      metric("max_drawdown", "Max drawdown", formatBacktestValue(metrics.max_drawdown_pct)),
      metric("profit_factor", "Profit factor", formatBacktestValue(metrics.profit_factor)),
      metric("net_profit", "Net profit", formatBacktestValue(metrics.net_profit_pct)),
      metric("fee_bps", "Fee bps", formatBacktestValue(assumptionReview.fee_bps)),
      metric("slippage_bps", "Slippage bps", formatBacktestValue(assumptionReview.slippage_bps)),
    ]),
    checkCounts,
    checks,
    tradeSampleCount: Array.isArray(tradeSamples.sample) ? tradeSamples.sample.length : 0,
    topLoserCount: Array.isArray(tradeSamples.top_losers) ? tradeSamples.top_losers.length : 0,
    topWinnerCount: Array.isArray(tradeSamples.top_winners) ? tradeSamples.top_winners.length : 0,
    warnings: stringArray(assumptionReview.warnings).map(userFacingBacktestText),
  };
}

export function formatBacktestValue(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return Number.isInteger(value)
      ? value.toLocaleString()
      : value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }
  if (typeof value === "string" && value.trim()) {
    return userFacingBacktestText(value);
  }
  return "N/A";
}

function userFacingBacktestText(value: string): string {
  return value
    .replace(/PineForge local Pine preview evidence only/gi, "Local sandbox preview evidence only")
    .replace(/PineForge local Pine preview evidence/gi, "Local sandbox preview evidence")
    .replace(/PineForge \(Local Preview\)/gi, "Local preview")
    .replace(/PineForge Preview/gi, "Backtest Preview")
    .replace(/PineForge compile\/backtest/gi, "local preview")
    .replace(/PineForge local Pine preview/gi, "local sandbox preview")
    .replace(/PineForge output/gi, "Local sandbox preview output")
    .replace(/pineforge-engine/gi, "local preview")
    .replace(/pineforge-runner/gi, "local preview")
    .replace(/PineForge/gi, "local preview");
}

function formatPnlMetric(value: unknown): string {
  if (isRecord(value)) {
    const absolute = formatBacktestValue(value.absolute);
    const percentage = formatBacktestValue(value.percentage);
    if (absolute !== "N/A" && percentage !== "N/A") {
      return `${absolute} (${percentage}%)`;
    }
    return absolute !== "N/A" ? absolute : percentage;
  }
  return formatBacktestValue(value);
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

function uniqueStrings(values: string[]): string[] {
  return Array.from(new Set(values));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
