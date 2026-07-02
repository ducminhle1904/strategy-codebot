import { Button } from "@/components/ui/button";
import type { BacktestArtifactCardModel } from "@/lib/backtest-report";

const SUPPRESSED_BACKTEST_BOUNDARY =
  [
    "Local sandbox preview only",
    "not TradingView proof, broker proof, live trading evidence, or a profitability claim.",
  ].join("; ");

function visibleBacktestBoundary(value: string | null | undefined) {
  const text = value?.trim();
  if (!text || text === SUPPRESSED_BACKTEST_BOUNDARY) {
    return null;
  }
  return text;
}

export function BacktestReportCard({
  isSubmittingFeedback,
  onFeedback,
  report,
}: {
  isSubmittingFeedback: boolean;
  onFeedback?: (rating: "up" | "down") => Promise<void>;
  report: BacktestArtifactCardModel;
}) {
  if (report.kind === "robustness_report") {
    const boundary = visibleBacktestBoundary(report.boundary);
    return (
      <div className="space-y-3 rounded-[6px] border border-border bg-muted/20 p-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="font-medium text-sm">Robustness report</p>
            <p className="text-muted-foreground text-xs">
              Review sample quality, assumptions, drawdown, and suspicious metrics.
            </p>
          </div>
          <span className={recommendationClassName(report.recommendation)}>
            {readableLabel(report.recommendation ?? "needs_review")}
          </span>
        </div>
        <div className="grid gap-2 sm:grid-cols-3">
          <div className="rounded-[4px] border border-red-500/30 bg-red-500/10 p-2">
            <p className="text-[11px] text-red-200">Fail</p>
            <p className="font-semibold text-sm">{report.checkCounts.fail}</p>
          </div>
          <div className="rounded-[4px] border border-amber-500/30 bg-amber-500/10 p-2">
            <p className="text-[11px] text-amber-100">Warn</p>
            <p className="font-semibold text-sm">{report.checkCounts.warn}</p>
          </div>
          <div className="rounded-[4px] border border-emerald-500/30 bg-emerald-500/10 p-2">
            <p className="text-[11px] text-emerald-100">Pass</p>
            <p className="font-semibold text-sm">{report.checkCounts.pass}</p>
          </div>
        </div>
        {report.metrics.length > 0 ? (
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {report.metrics.map((metric) => (
              <div className="rounded-[4px] border border-border bg-background p-2" key={metric.label}>
                <p className="text-muted-foreground text-[11px]">{metric.label}</p>
                <p className="font-medium text-sm">{metric.value}</p>
              </div>
            ))}
          </div>
        ) : null}
        {report.checks.length > 0 ? (
          <div className="space-y-1">
            <p className="font-medium text-xs">Key checks</p>
            <div className="divide-y divide-border rounded-[4px] border border-border bg-background">
              {report.checks.slice(0, 6).map((check) => (
              <div className="grid gap-2 p-2 text-xs sm:grid-cols-[7rem_1fr_6rem]" key={check.id}>
                <span className={checkStatusClassName(check.status)}>{check.status}</span>
                <span className="text-muted-foreground">{check.message}</span>
                <span className="font-medium sm:text-right">{check.observed}</span>
              </div>
              ))}
            </div>
          </div>
        ) : null}
        {report.metrics.length === 0 && report.checks.length === 0 ? (
          <p className="rounded-[4px] border border-border bg-background p-2 text-muted-foreground text-xs">
            No robustness metrics recorded for this artifact.
          </p>
        ) : null}
        <div className="grid gap-2 text-xs sm:grid-cols-3">
          <div>
            <p className="text-muted-foreground">Trade sample</p>
            <p className="font-medium">{report.tradeSampleCount} rows</p>
          </div>
          <div>
            <p className="text-muted-foreground">Top losers</p>
            <p className="font-medium">{report.topLoserCount} rows</p>
          </div>
          <div>
            <p className="text-muted-foreground">Top winners</p>
            <p className="font-medium">{report.topWinnerCount} rows</p>
          </div>
        </div>
        <BacktestWarnings warnings={report.warnings} />
        {boundary ? (
          <p className="border-border border-t pt-2 text-muted-foreground text-xs">
            {boundary}
          </p>
        ) : null}
      </div>
    );
  }

  if (report.kind === "variant_comparison") {
    return (
      <div className="space-y-3 rounded-[6px] border border-border bg-muted/20 p-3">
        <div>
          <p className="font-medium text-sm">Backtest variant lab</p>
          <p className="text-muted-foreground text-xs">
            Compare preview variants after every run completes.
          </p>
        </div>
        {report.variants.length > 0 ? (
          <div className="grid gap-2 sm:grid-cols-2">
            {report.variants.map((variant) => (
              <div className="rounded-[4px] border border-border bg-background p-2" key={`${variant.name}-${variant.runId}`}>
                <div className="flex items-start justify-between gap-2">
                  <p className="font-medium text-sm">{variant.name}</p>
                  <span className="rounded-[3px] border px-1.5 py-0.5 text-[10px] text-muted-foreground">
                    {variant.status}
                  </span>
                </div>
                <p className="mt-1 text-muted-foreground text-xs">
                  {variant.symbol} · {variant.timeframe}
                </p>
                {variant.runId && <p className="mt-1 break-all font-mono text-[10px] text-muted-foreground">{variant.runId}</p>}
              </div>
            ))}
          </div>
        ) : (
          <p className="rounded-[4px] border border-border bg-background p-2 text-muted-foreground text-xs">
            No variants recorded in this artifact.
          </p>
        )}
        <BacktestWarnings warnings={report.warnings} />
        <div className="grid gap-2 text-xs sm:grid-cols-2">
          <div>
            <p className="text-muted-foreground">Variant group</p>
            <p className="break-all font-mono text-[11px]">{report.variantGroupId ?? "Unavailable"}</p>
          </div>
          <div>
            <p className="text-muted-foreground">Shared cache key</p>
            <p className="break-all font-mono text-[11px]">{report.sharedCacheKey ?? "Unavailable"}</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded-[6px] border border-border bg-muted/20 p-3">
      <div>
        <p className="font-medium text-sm">Local preview evidence</p>
        <p className="text-muted-foreground text-xs">
          Review-only evidence. Not TradingView proof, MQL5 proof, or live-trading evidence.
        </p>
      </div>
      {(report.qualityStatus === "fail" || report.qualityFlags.includes("position_sizing_mismatch")) && (
        <div className="rounded-[4px] border border-amber-400/40 bg-amber-400/10 p-2 text-amber-100 text-xs">
          Position sizing needs repair before this preview can be evaluated.
        </div>
      )}
      {report.metrics.length > 0 ? (
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {report.metrics.map((metric) => (
            <div className="rounded-[4px] border border-border bg-background p-2" key={metric.label}>
              <p className="text-muted-foreground text-[11px]">{metric.label}</p>
              <p className="font-medium text-sm">{metric.value}</p>
            </div>
          ))}
        </div>
      ) : (
        <p className="rounded-[4px] border border-border bg-background p-2 text-muted-foreground text-xs">
          No performance metrics recorded for this artifact.
        </p>
      )}
      {report.assumptions.length > 0 || report.robustness.length > 0 ? (
        <div className="grid gap-3 lg:grid-cols-2">
          {report.assumptions.length > 0 ? (
            <div className="space-y-1">
              <p className="font-medium text-xs">Assumptions</p>
              <dl className="space-y-1 text-xs">
                {report.assumptions.map((item) => (
                  <div className="flex justify-between gap-3" key={item.label}>
                    <dt className="text-muted-foreground">{item.label}</dt>
                    <dd className="truncate font-medium">{item.value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ) : null}
          {report.robustness.length > 0 ? (
            <div className="space-y-1">
              <p className="font-medium text-xs">Robustness</p>
              <dl className="space-y-1 text-xs">
                {report.robustness.map((item) => (
                  <div className="flex justify-between gap-3" key={item.label}>
                    <dt className="text-muted-foreground">{item.label}</dt>
                    <dd className="truncate font-medium">{item.value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ) : null}
        </div>
      ) : null}
      <BacktestWarnings warnings={report.warnings} />
      {report.dataSource || report.reproducibilityHash ? (
        <div className="grid gap-2 text-xs sm:grid-cols-2">
          {report.dataSource ? (
            <div>
              <p className="text-muted-foreground">Data source</p>
              <p className="font-medium">{report.dataSource}</p>
            </div>
          ) : null}
          {report.reproducibilityHash ? (
            <div>
              <p className="text-muted-foreground">Reproducibility hash</p>
              <p className="break-all font-mono text-[11px]">{report.reproducibilityHash}</p>
            </div>
          ) : null}
        </div>
      ) : null}
      {onFeedback ? (
        <div className="flex flex-wrap gap-2 border-border border-t pt-3">
          <Button
            disabled={isSubmittingFeedback}
            onClick={() => void onFeedback("up")}
            size="sm"
            type="button"
            variant="outline"
          >
            Useful
          </Button>
          <Button
            disabled={isSubmittingFeedback}
            onClick={() => void onFeedback("down")}
            size="sm"
            type="button"
            variant="outline"
          >
            Needs iteration
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function readableLabel(value: string) {
  return value.replace(/_/g, " ");
}

function recommendationClassName(recommendation: string | null) {
  const base = "shrink-0 rounded-[4px] border px-2 py-1 text-[10px] font-medium";
  if (recommendation === "reject_preview") {
    return `${base} border-red-500/40 bg-red-500/10 text-red-300`;
  }
  if (recommendation === "candidate_for_review") {
    return `${base} border-emerald-500/40 bg-emerald-500/10 text-emerald-300`;
  }
  return `${base} border-amber-500/40 bg-amber-500/10 text-amber-200`;
}

function checkStatusClassName(status: "fail" | "pass" | "warn") {
  const base = "w-fit rounded-[3px] px-1.5 py-0.5 font-medium";
  if (status === "fail") {
    return `${base} bg-red-500/10 text-red-300`;
  }
  if (status === "warn") {
    return `${base} bg-amber-500/10 text-amber-200`;
  }
  return `${base} bg-emerald-500/10 text-emerald-300`;
}

function BacktestWarnings({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) {
    return null;
  }
  return (
    <div className="space-y-1">
      <p className="font-medium text-xs">Warnings</p>
      <ul className="space-y-1 text-muted-foreground text-xs">
        {warnings.map((warning) => <li key={warning}>{warning}</li>)}
      </ul>
    </div>
  );
}
