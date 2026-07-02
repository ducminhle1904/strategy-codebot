import type { NautilusRuntime, NautilusRuntimeEvent } from "@/lib/backend-schemas";

export type PaperBotStatusFilter =
  | "all"
  | "draft"
  | "running"
  | "paused"
  | "needs_attention"
  | "stopped";

export const PAPER_BOT_STATUS_FILTERS: Array<{
  label: string;
  value: PaperBotStatusFilter;
}> = [
  { label: "All", value: "all" },
  { label: "Draft", value: "draft" },
  { label: "Running", value: "running" },
  { label: "Paused", value: "paused" },
  { label: "Needs attention", value: "needs_attention" },
  { label: "Stopped", value: "stopped" },
];

const METRIC_KEYS = [
  "pnl",
  "net_pnl",
  "net_profit",
  "drawdown",
  "max_drawdown",
  "positions",
  "open_positions",
  "orders",
  "fills",
  "last_price",
  "bar_count",
];

export function paperBotDisplayName(runtime: NautilusRuntime) {
  const manifestName = stringField(runtime.manifest, "name") ?? stringField(runtime.manifest, "strategy_name");
  return manifestName ?? runtime.strategy_ids[0] ?? runtime.id;
}

export function paperBotRuntimeLabel(runtime: NautilusRuntime) {
  const subscriptions = paperBotSubscriptions(runtime);
  if (subscriptions.length === 0) {
    return "No market subscription";
  }
  return subscriptions.slice(0, 2).join(", ");
}

export function paperBotSubscriptions(runtime: NautilusRuntime) {
  return runtime.data_subscriptions.flatMap((subscription) => paperBotSubscriptionLabel(subscription) ?? []);
}

export function paperBotSubscriptionLabel(subscription: Record<string, unknown>) {
  const symbol =
    stringField(subscription, "symbol") ??
    stringField(subscription, "instrument_id") ??
    stringField(subscription, "instrument");
  const timeframe =
    stringField(subscription, "timeframe") ??
    stringField(subscription, "bar_type") ??
    stringField(subscription, "interval");
  if (!symbol && !timeframe) {
    return null;
  }
  return `${symbol ?? "Unknown"}${timeframe ? ` ${timeframe}` : ""}`;
}

export function paperBotSearchText(runtime: NautilusRuntime) {
  return [
    paperBotDisplayName(runtime),
    runtime.id,
    runtime.state,
    runtime.desired_state,
    runtime.risk_policy_id,
    runtime.account_id,
    runtime.broker_connection_id,
    runtime.strategy_ids.join(" "),
    paperBotRuntimeLabel(runtime),
  ]
    .join(" ")
    .toLowerCase();
}

export function paperBotMatchesFilter(runtime: NautilusRuntime, filter: PaperBotStatusFilter) {
  if (filter === "all") {
    return true;
  }
  if (filter === "draft") {
    return runtime.state === "requested" || runtime.state === "provisioning" || runtime.state === "warming_up";
  }
  if (filter === "running") {
    return runtime.state === "running";
  }
  if (filter === "paused") {
    return runtime.state === "stopping" || runtime.desired_state === "stopping";
  }
  if (filter === "needs_attention") {
    return runtime.kill_switch_active || runtime.state === "degraded" || runtime.state === "failed" || Boolean(runtime.last_error);
  }
  return runtime.state === "stopped";
}

export function paperBotMetricPairs(runtime: NautilusRuntime) {
  const metrics = runtime.heartbeat_metrics ?? {};
  return METRIC_KEYS.flatMap((key) => {
    const value = metrics[key];
    if (value === undefined || value === null || typeof value === "object") {
      return [];
    }
    return [{ label: readableKey(key), value: String(value) }];
  }).slice(0, 4);
}

export function paperBotStateTone(runtime: NautilusRuntime) {
  if (runtime.kill_switch_active || runtime.state === "failed") {
    return "danger";
  }
  if (runtime.state === "degraded" || runtime.state === "stopping" || runtime.desired_state === "stopping") {
    return "warning";
  }
  if (runtime.state === "running") {
    return "success";
  }
  return "neutral";
}

export function paperBotEventSeverity(event: NautilusRuntimeEvent) {
  if (event.type === "error" || event.type === "risk_block") {
    return "high";
  }
  if (event.type === "stop_requested" || event.type === "heartbeat_missed") {
    return "medium";
  }
  return "low";
}

export function paperBotEventSummary(event: NautilusRuntimeEvent) {
  const payload = event.payload ?? {};
  const message = stringField(payload, "message") ?? stringField(payload, "reason") ?? stringField(payload, "status");
  if (message) {
    return message;
  }
  const keys = Object.keys(payload).slice(0, 3);
  return keys.length > 0 ? keys.map((key) => `${readableKey(key)}: ${String(payload[key])}`).join(" · ") : "No event details";
}

export function paperBotArtifactIds(runtime: NautilusRuntime, events: NautilusRuntimeEvent[] = []) {
  const ids = new Set<string>();
  collectArtifactIds(runtime.manifest, ids);
  for (const event of events) {
    collectArtifactIds(event.payload, ids);
  }
  return Array.from(ids);
}

export function formatPaperBotTime(value: string | null) {
  if (!value) {
    return "Never";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function stringField(record: Record<string, unknown>, key: string) {
  const value = record[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function readableKey(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (match) => match.toUpperCase());
}

function collectArtifactIds(value: unknown, ids: Set<string>) {
  if (!value || typeof value !== "object") {
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      collectArtifactIds(item, ids);
    }
    return;
  }
  for (const [key, item] of Object.entries(value)) {
    if ((key === "artifact_ids" || key.endsWith("_artifact_ids")) && Array.isArray(item)) {
      for (const artifactId of item) {
        if (typeof artifactId === "string" && artifactId.trim()) {
          ids.add(artifactId.trim());
        }
      }
      continue;
    }
    if ((key === "artifact_id" || key.endsWith("_artifact_id")) && typeof item === "string" && item.trim()) {
      ids.add(item.trim());
      continue;
    }
    if (Array.isArray(item) || (item && typeof item === "object")) {
      collectArtifactIds(item, ids);
    }
  }
}
