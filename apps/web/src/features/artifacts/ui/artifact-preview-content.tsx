"use client";

import { useMemo, type ComponentProps } from "react";
import type { BundledLanguage } from "shiki";

import { CodeBlock } from "@/components/ai-elements/code-block";
import {
  MessageResponse,
  type MessageResponseProps,
} from "@/components/ai-elements/message";
import { BacktestPreviewHitlCard } from "@/components/strategy/agent-tools/tool-cards";
import { BacktestDashboardArtifact } from "@/components/strategy/backtest-dashboard-artifact";
import { BacktestReportCard } from "@/components/strategy/backtest-report-card";
import type { ArtifactPreviewResponse, RunEvent } from "@/lib/backend-schemas";
import { parseBacktestDashboardArtifactPreview } from "@/lib/backtest-dashboard";
import { parseBacktestArtifactPreview } from "@/lib/backtest-report";
import { cn } from "@/lib/utils";

type BacktestApprovalDecision = {
  approvalId: string;
  conversationId: string;
  decision: "approved" | "rejected";
};

type BacktestPlanApprovalPreview = {
  approvalId: string;
  status: "pending" | "approved" | "queued" | "rejected" | "failed";
  conversationId: string;
  symbol?: string;
  timeframe?: string;
  boundary?: string | null;
  backtestConfig?: Record<string, unknown>;
  warnings: string[];
  assumptions: string[];
};

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

const artifactPreviewMarkdownComponents = {
  table: ({ className, ...props }: ComponentProps<"table">) => (
    <div className="w-full overflow-x-auto">
      <table
        className={cn("w-full border-collapse text-sm", className)}
        {...props}
      />
    </div>
  ),
  th: ({ className, ...props }: ComponentProps<"th">) => (
    <th
      className={cn(
        "bg-muted px-3 py-2 text-left font-semibold text-foreground",
        className
      )}
      {...props}
    />
  ),
  td: ({ className, ...props }: ComponentProps<"td">) => (
    <td
      className={cn("border-border border-t px-3 py-2 align-top", className)}
      {...props}
    />
  ),
};

export function ArtifactPreviewContent({
  approvalDecisionPending = false,
  onBacktestApprovalDecision,
  preview,
  runEvents = [],
}: {
  approvalDecisionPending?: boolean;
  onBacktestApprovalDecision?: (payload: BacktestApprovalDecision) => void;
  preview: ArtifactPreviewResponse;
  runEvents?: RunEvent[];
}) {
  const backtestPlanApproval = useMemo(
    () =>
      preview.presentation.viewer_kind === "backtest_plan"
        ? backtestPlanApprovalFromPreview(preview, runEvents)
        : null,
    [preview, runEvents]
  );
  const backtestDashboard = useMemo(
    () =>
      preview.presentation.viewer_kind === "backtest_dashboard"
        ? parseBacktestDashboardArtifactPreview(preview.kind, preview.preview)
        : null,
    [preview]
  );
  const backtestReport = useMemo(
    () =>
      preview.presentation.viewer_kind === "backtest_report"
        ? parseBacktestArtifactPreview(preview.kind, preview.preview)
        : null,
    [preview]
  );
  if (backtestPlanApproval) {
    return (
      <BacktestPlanApprovalArtifact
        approval={backtestPlanApproval}
        disabled={approvalDecisionPending}
        onDecision={onBacktestApprovalDecision}
      />
    );
  }
  if (backtestDashboard) {
    return <BacktestDashboardArtifact dashboard={backtestDashboard} />;
  }
  if (backtestReport) {
    return (
      <div className="mx-auto max-w-4xl">
        <BacktestReportCard
          isSubmittingFeedback={false}
          report={backtestReport}
        />
      </div>
    );
  }
  if (preview.presentation.viewer_kind === "trades" && Array.isArray(preview.preview)) {
    return <BacktestTradesPreview trades={preview.preview} />;
  }
  const content = artifactPreviewContent(preview);
  const codeLanguage = artifactLanguage(preview);

  if (codeLanguage === "markdown") {
    return (
      <article className="mx-auto max-w-3xl">
        <MessageResponse
          className="apple-utility-card p-5 text-base leading-7"
          components={
            artifactPreviewMarkdownComponents as MessageResponseProps["components"]
          }
          tableStyle="plain"
        >
          {content}
        </MessageResponse>
      </article>
    );
  }

  return (
    <CodeBlock
      className="apple-dark-tile mx-auto max-w-5xl border-0 p-5 shadow-none [&>div]:overflow-x-auto [&>div]:overflow-y-visible [&_pre]:!bg-transparent [&_pre]:px-0 [&_pre]:py-0"
      code={content}
      language={codeLanguage}
      showLineNumbers
    />
  );
}

function BacktestPlanApprovalArtifact({
  approval,
  disabled,
  onDecision,
}: {
  approval: BacktestPlanApprovalPreview;
  disabled: boolean;
  onDecision?: (payload: BacktestApprovalDecision) => void;
}) {
  const canDecide =
    approval.status === "pending" && Boolean(approval.conversationId) && Boolean(onDecision);
  const status = {
    approved: "complete",
    failed: "failed",
    pending: "inProgress",
    queued: "executing",
    rejected: "skipped",
  }[approval.status];
  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <BacktestPreviewHitlCard
        approveLabel={disabled ? "Submitting..." : "Approve preview"}
        disabled={disabled}
        onRespond={
          canDecide && onDecision
            ? (response) => {
                const approved = isRecord(response) && response.approved === true;
                onDecision({
                  approvalId: approval.approvalId,
                  conversationId: approval.conversationId,
                  decision: approved ? "approved" : "rejected",
                });
              }
            : undefined
        }
        rejectLabel="Skip"
        status={status}
        symbol={approval.symbol}
        timeframe={approval.timeframe}
      />
      <section className="apple-utility-card p-4">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span className="rounded-[4px] border border-border px-2 py-1 font-mono">
            {approval.symbol ?? "Symbol pending"}
          </span>
          <span className="rounded-[4px] border border-border px-2 py-1 font-mono">
            {approval.timeframe ?? "Timeframe pending"}
          </span>
          <span className="rounded-[4px] border border-border px-2 py-1">
            {approval.status === "rejected"
              ? "Backtest preview skipped"
              : approval.status === "queued"
                ? "Queued"
                : approval.status === "approved"
                  ? "Approved"
                  : "Approval required"}
          </span>
        </div>
        {approval.boundary ? (
          <p className="mt-3 text-muted-foreground text-sm">{approval.boundary}</p>
        ) : null}
        {approval.backtestConfig ? (
          <dl className="mt-4 grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
            {Object.entries(approval.backtestConfig).slice(0, 8).map(([key, value]) => (
              <div className="min-w-0" key={key}>
                <dt className="text-muted-foreground text-xs uppercase">{key.replaceAll("_", " ")}</dt>
                <dd className="truncate font-mono text-foreground">{formatCell(value)}</dd>
              </div>
            ))}
          </dl>
        ) : null}
        {approval.warnings.length ? (
          <div className="mt-4 border-border border-t pt-3 text-sm">
            <p className="font-medium">Warnings</p>
            <ul className="mt-2 space-y-1 text-muted-foreground">
              {approval.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {approval.assumptions.length ? (
          <div className="mt-4 border-border border-t pt-3 text-sm">
            <p className="font-medium">Assumptions</p>
            <ul className="mt-2 space-y-1 text-muted-foreground">
              {approval.assumptions.map((assumption) => (
                <li key={assumption}>{assumption}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>
    </div>
  );
}

function BacktestTradesPreview({ trades }: { trades: unknown[] }) {
  const rows = trades.filter(isRecord).slice(0, 20);
  if (rows.length === 0) {
    return (
          <p className="apple-utility-card mx-auto max-w-3xl p-3 text-muted-foreground text-sm">
        No trades recorded in this preview.
      </p>
    );
  }
  return (
    <div className="apple-utility-card mx-auto max-w-5xl overflow-x-auto">
      <table className="w-full min-w-[680px] text-left text-sm">
        <thead className="border-border border-b text-muted-foreground text-xs">
          <tr>
            <th className="px-3 py-2 font-medium">#</th>
            <th className="px-3 py-2 font-medium">Side</th>
            <th className="px-3 py-2 font-medium">Entry</th>
            <th className="px-3 py-2 font-medium">Exit</th>
            <th className="px-3 py-2 font-medium">Qty</th>
            <th className="px-3 py-2 font-medium">PnL</th>
            <th className="px-3 py-2 font-medium">Commission</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((trade, index) => (
            <tr className="border-border/60 border-b last:border-b-0" key={`${index}-${String(trade.entry_time ?? trade.opened_at ?? "")}`}>
              <td className="px-3 py-2 text-muted-foreground">{index + 1}</td>
              <td className="px-3 py-2">{formatCell(trade.side)}</td>
              <td className="px-3 py-2 font-mono">{formatCell(trade.entry_price)}</td>
              <td className="px-3 py-2 font-mono">{formatCell(trade.exit_price)}</td>
              <td className="px-3 py-2 font-mono">{formatCell(trade.qty ?? trade.cost)}</td>
              <td className="px-3 py-2 font-mono">{formatCell(trade.pnl_cost ?? trade.pnl)}</td>
              <td className="px-3 py-2 font-mono">{formatCell(trade.commission ?? trade.fee_cost)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function backtestPlanApprovalFromPreview(
  preview: ArtifactPreviewResponse,
  events: RunEvent[]
): BacktestPlanApprovalPreview | null {
  if (preview.presentation.viewer_kind !== "backtest_plan" || !isRecord(preview.preview)) {
    return null;
  }
  const approvalId = preview.preview.approval_id;
  if (typeof approvalId !== "string" || !approvalId.trim()) {
    return null;
  }
  const config = isRecord(preview.preview.backtest_config)
    ? preview.preview.backtest_config
    : undefined;
  const rawWarnings = Array.isArray(preview.preview.warnings)
    ? preview.preview.warnings.filter((item): item is string => typeof item === "string")
    : [];
  const warnings = rawWarnings.filter((warning) => visibleBacktestBoundary(warning));
  const assumptions = Array.isArray(preview.preview.assumptions)
    ? preview.preview.assumptions.filter((item): item is string => typeof item === "string")
    : [];
  return {
    approvalId,
    assumptions,
    backtestConfig: config,
    boundary: visibleBacktestBoundary(rawWarnings[0]),
    conversationId: preview.conversation_id ?? "",
    status: approvalStatusFromEvents(approvalId, events, preview.preview.approval_status),
    symbol: typeof config?.symbol === "string" ? config.symbol : undefined,
    timeframe: typeof config?.timeframe === "string" ? config.timeframe : undefined,
    warnings,
  };
}

function approvalStatusFromEvents(
  approvalId: string,
  events: RunEvent[],
  fallback: unknown
): BacktestPlanApprovalPreview["status"] {
  let status: BacktestPlanApprovalPreview["status"] =
    fallback === "approved" ||
    fallback === "queued" ||
    fallback === "rejected" ||
    fallback === "failed"
      ? fallback
      : "pending";
  for (const event of events) {
    const payload = isRecord(event.payload) ? event.payload : {};
    if (payload.approval_id !== approvalId) {
      continue;
    }
    if (event.type === "backtest.preview.failed") {
      status = "failed";
    }
    if (event.type === "backtest.preview.rejected") {
      status = "rejected";
    }
    if (event.type === "backtest.preview.approved") {
      status = "approved";
    }
    if (event.type === "backtest.preview.queued") {
      status = "queued";
    }
  }
  return status;
}

export function artifactPreviewContent(preview: ArtifactPreviewResponse) {
  return typeof preview.preview === "string"
    ? preview.preview
    : JSON.stringify(preview.preview, null, 2);
}

function artifactLanguage(preview: ArtifactPreviewResponse): BundledLanguage | "markdown" {
  if (preview.presentation.language_hint === "markdown") {
    return "markdown";
  }
  if (preview.presentation.language_hint === "json") {
    return "json";
  }
  if (preview.presentation.language_hint === "pine") {
    return "javascript";
  }
  if (preview.presentation.language_hint === "mql5") {
    return "c";
  }
  return "json";
}

function formatCell(value: unknown) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return Math.abs(value) >= 1000 ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  }
  return typeof value === "string" && value.trim() ? value : "-";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
