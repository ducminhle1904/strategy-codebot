"use client";

import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  FileCode2,
  Gauge,
  LineChart,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type ToolStatus = "complete" | "executing" | "failed" | "inProgress" | "skipped" | string;

type ToolCardDetail = { label: string; value: string };

export function AgentToolCard({
  action,
  children,
  description,
  details,
  icon,
  status,
  title,
}: {
  action?: ReactNode;
  children?: ReactNode;
  description: string;
  details?: ToolCardDetail[];
  icon?: ReactNode;
  status: ToolStatus;
  title: string;
}) {
  return (
    <div className="rounded-[6px] border border-border bg-[rgba(255,255,255,0.025)] p-3">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-[5px] border border-border text-muted-foreground">
          {icon}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm font-medium text-foreground">{title}</p>
              <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{description}</p>
              {details && details.length > 0 ? (
                <dl className="mt-2 grid gap-1 text-xs">
                  {details.map((detail) => (
                    <div className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-2" key={detail.label}>
                      <dt className="text-muted-foreground">{detail.label}</dt>
                      <dd className="truncate text-foreground">{detail.value}</dd>
                    </div>
                  ))}
                </dl>
              ) : null}
            </div>
            <ToolStatusBadge status={status} />
          </div>
          {children ? <div className="mt-3">{children}</div> : null}
        </div>
        {action}
      </div>
    </div>
  );
}

export function ToolStatusBadge({ status }: { status: ToolStatus }) {
  const label = toolStatusLabel(status);
  return (
    <span
      className={cn(
        "rounded-[4px] border px-2 py-1 text-[10px] font-medium",
        status === "complete" && "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
        status === "executing" && "border-blue-500/40 bg-blue-500/10 text-blue-300",
        status === "failed" && "border-red-500/40 bg-red-500/10 text-red-300",
        status === "skipped" && "border-amber-500/40 bg-amber-500/10 text-amber-200",
        status !== "complete" &&
          status !== "executing" &&
          status !== "failed" &&
          status !== "skipped" &&
          "border-border bg-secondary text-muted-foreground"
      )}
    >
      {label}
    </span>
  );
}

export function BacktestPreviewHitlCard({
  approveLabel = "Start preview",
  disabled = false,
  rejectLabel = "Cancel",
  onRespond,
  status,
  symbol,
  timeframe,
}: {
  approveLabel?: string;
  disabled?: boolean;
  rejectLabel?: string;
  onRespond?: (response: unknown) => void;
  status: ToolStatus;
  symbol?: string;
  timeframe?: string;
}) {
  return (
    <AgentToolCard
      action={
        onRespond ? (
          <div className="flex shrink-0 gap-2">
            <Button
              className="h-8 rounded-[4px] text-xs"
              disabled={disabled}
              onClick={() => onRespond({ approved: true, scope: "preview_only" })}
              type="button"
              variant="outline"
            >
              {approveLabel}
            </Button>
            <Button
              className="h-8 rounded-[4px] text-xs"
              disabled={disabled}
              onClick={() => onRespond({ approved: false })}
              type="button"
              variant="ghost"
            >
              {rejectLabel}
            </Button>
          </div>
        ) : null
      }
      description={`Review-only preview plan${symbol ? ` for ${symbol}` : ""}${timeframe ? ` on ${timeframe}` : ""}. No broker orders or live execution.`}
      icon={<LineChart className="size-4" />}
      status={status}
      title="Backtest preview"
    />
  );
}

export function ValidationRepairHitlCard({
  onRespond,
  status,
}: {
  onRespond?: (response: unknown) => void;
  status: ToolStatus;
}) {
  return (
    <AgentToolCard
      action={
        onRespond ? (
          <div className="flex shrink-0 gap-2">
            <Button
              className="h-8 rounded-[4px] text-xs"
              onClick={() => onRespond({ approach: "minimal_repair" })}
              type="button"
              variant="outline"
            >
              Minimal fix
            </Button>
            <Button
              className="h-8 rounded-[4px] text-xs"
              onClick={() => onRespond({ approach: "rewrite_safely" })}
              type="button"
              variant="outline"
            >
              Safer rewrite
            </Button>
          </div>
        ) : null
      }
      description="Choose how Strategy Codebot should repair validation blockers before producing reviewable output."
      icon={<ShieldCheck className="size-4" />}
      status={status}
      title="Repair validation"
    />
  );
}

export function RegenerateArtifactHitlCard({
  onRespond,
  status,
}: {
  onRespond?: (response: unknown) => void;
  status: ToolStatus;
}) {
  return (
    <AgentToolCard
      action={
        onRespond ? (
          <Button
            className="h-8 rounded-[4px] text-xs"
            onClick={() => onRespond({ approved: true, preserve_user_rules: true })}
            type="button"
            variant="outline"
          >
            Regenerate
          </Button>
        ) : null
      }
      description="Create a new review artifact while preserving the visible strategy rules."
      icon={<RefreshCw className="size-4" />}
      status={status}
      title="Regenerate artifact"
    />
  );
}

export function ApplyMarketContextHitlCard({
  onRespond,
  status,
  symbol,
}: {
  onRespond?: (response: unknown) => void;
  status: ToolStatus;
  symbol?: string;
}) {
  return (
    <AgentToolCard
      action={
        onRespond ? (
          <Button
            className="h-8 rounded-[4px] text-xs"
            onClick={() => onRespond({ approved: true, use_as_context: true })}
            type="button"
            variant="outline"
          >
            Apply
          </Button>
        ) : null
      }
      description={`Use ${symbol ?? "the latest market snapshot"} as context for a strategy draft.`}
      icon={<Gauge className="size-4" />}
      status={status}
      title="Apply market context"
    />
  );
}

export function PaperBotProposalCard({
  actionLabel,
  disabled = false,
  missingFields = [],
  onStart,
  pending = false,
  readiness = [],
  status,
  strategyName,
  subscriptions = [],
}: {
  actionLabel?: string;
  disabled?: boolean;
  missingFields?: string[];
  onStart?: () => void;
  pending?: boolean;
  readiness?: string[];
  status: ToolStatus;
  strategyName?: string;
  subscriptions?: string[];
}) {
  const canStart = missingFields.length === 0 && Boolean(onStart);
  return (
    <AgentToolCard
      action={
        onStart ? (
          <Button
            className="h-8 rounded-[4px] text-xs"
            disabled={disabled || pending || !canStart}
            onClick={onStart}
            type="button"
            variant="outline"
          >
            {pending ? "Working..." : canStart ? actionLabel ?? "Prepare bot" : "Draft only"}
          </Button>
        ) : null
      }
      description={`${strategyName ?? "Bot proposal"} uses simulated order intents only. No broker execution.`}
      icon={<Bot className="size-4" />}
      status={status}
      title="Bot proposal"
    >
      <div className="space-y-2 text-xs">
        <div className="flex flex-wrap gap-1.5">
          {(subscriptions.length > 0 ? subscriptions : ["No market subscription"]).slice(0, 3).map((item) => (
            <span className="rounded-[4px] bg-muted px-2 py-0.5 text-muted-foreground" key={item}>
              {item}
            </span>
          ))}
        </div>
        {missingFields.length > 0 ? (
          <p className="text-amber-300">Missing: {missingFields.join(", ")}</p>
        ) : null}
        {readiness.length > 0 ? (
          <ul className="space-y-1 text-muted-foreground">
            {readiness.slice(0, 3).map((item) => (
              <li className="flex items-center gap-1.5" key={item}>
                <CheckCircle2 className="size-3 text-emerald-300" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </AgentToolCard>
  );
}

export function ArtifactToolCard({ status }: { status: ToolStatus }) {
  return (
    <AgentToolCard
      description="Review artifact is available in the workspace."
      details={[{ label: "Status", value: toolStatusLabel(status) }]}
      icon={<FileCode2 className="size-4" />}
      status={status}
      title="Artifact ready"
    />
  );
}

export function UnknownToolCard({ status }: { status: ToolStatus }) {
  return (
    <AgentToolCard
      description="A background agent step is running."
      details={[{ label: "Status", value: toolStatusLabel(status) }]}
      icon={<AlertTriangle className="size-4" />}
      status={status}
      title="Agent step"
    />
  );
}

export function CompletedInline({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 text-xs text-emerald-300">
      <CheckCircle2 className="size-3" />
      {children}
    </span>
  );
}

function toolStatusLabel(status: ToolStatus) {
  if (status === "complete") {
    return "Done";
  }
  if (status === "executing") {
    return "Running";
  }
  if (status === "failed") {
    return "Failed";
  }
  if (status === "skipped") {
    return "Skipped";
  }
  return "Needs input";
}
