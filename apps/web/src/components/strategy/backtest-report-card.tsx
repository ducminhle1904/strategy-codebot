import { Button } from "@/components/ui/button";
import type { BacktestArtifactCardModel } from "@/lib/backtest-report";

export function BacktestReportCard({
  isSubmittingFeedback,
  onFeedback,
  report,
}: {
  isSubmittingFeedback: boolean;
  onFeedback: (rating: "up" | "down") => Promise<void>;
  report: BacktestArtifactCardModel;
}) {
  if (report.kind === "variant_comparison") {
    return (
      <div className="space-y-3 rounded-[6px] border border-border bg-muted/20 p-3">
        <div>
          <p className="font-medium text-sm">Backtest variant lab</p>
          <p className="text-muted-foreground text-xs">
            Comparable child runs share cache metadata. Compare reports after every run completes.
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
        <p className="font-medium text-sm">Backtest Kit local preview</p>
        <p className="text-muted-foreground text-xs">
          Review-only evidence. Not TradingView proof, MQL5 proof, or live-trading evidence.
        </p>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {report.metrics.map((metric) => (
          <div className="rounded-[4px] border border-border bg-background p-2" key={metric.label}>
            <p className="text-muted-foreground text-[11px]">{metric.label}</p>
            <p className="font-medium text-sm">{metric.value}</p>
          </div>
        ))}
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
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
        <BacktestWarnings warnings={report.warnings} />
      </div>
      <div className="grid gap-2 text-xs sm:grid-cols-2">
        <div>
          <p className="text-muted-foreground">Data source</p>
          <p className="font-medium">{report.dataSource ?? "Unknown"}</p>
        </div>
        <div>
          <p className="text-muted-foreground">Reproducibility hash</p>
          <p className="break-all font-mono text-[11px]">{report.reproducibilityHash ?? "Unavailable"}</p>
        </div>
      </div>
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
    </div>
  );
}

function BacktestWarnings({ warnings }: { warnings: string[] }) {
  return (
    <div className="space-y-1">
      <p className="font-medium text-xs">Warnings</p>
      <ul className="space-y-1 text-muted-foreground text-xs">
        {warnings.length > 0 ? warnings.map((warning) => <li key={warning}>{warning}</li>) : <li>No report warnings.</li>}
      </ul>
    </div>
  );
}
