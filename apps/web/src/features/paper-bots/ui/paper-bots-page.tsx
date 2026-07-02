"use client";

import { ConversationSidebar } from "@/features/workspace/ui/conversation-sidebar";
import { Button } from "@/components/ui/button";
import type { NautilusRuntime, NautilusRuntimeEvent } from "@/lib/backend-schemas";
import { useI18n } from "@/lib/language";
import { useTheme } from "@/lib/theme";
import { useBrowserBackendClient } from "@/lib/use-browser-backend-client";
import { cn } from "@/lib/utils";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Bot,
  FileStack,
  Loader2,
  RefreshCcw,
  Search,
  ShieldAlert,
  Square,
  X,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { type ReactNode, useMemo, useState } from "react";

import {
  PAPER_BOT_STATUS_FILTERS,
  type PaperBotStatusFilter,
  formatPaperBotTime,
  paperBotArtifactIds,
  paperBotDisplayName,
  paperBotEventSeverity,
  paperBotEventSummary,
  paperBotMatchesFilter,
  paperBotMetricPairs,
  paperBotRuntimeLabel,
  paperBotSearchText,
  paperBotStateTone,
  paperBotSubscriptions,
} from "./paper-bots-page-helpers";

type PaperBotDrawerTab = "overview" | "activity" | "risk" | "artifacts" | "settings";

const DRAWER_TABS: Array<{ label: string; value: PaperBotDrawerTab }> = [
  { label: "Overview", value: "overview" },
  { label: "Activity", value: "activity" },
  { label: "Risk", value: "risk" },
  { label: "Artifacts", value: "artifacts" },
  { label: "Settings", value: "settings" },
];

export function PaperBotsPage() {
  const client = useBrowserBackendClient();
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const { language, setLanguage } = useI18n();
  const { setTheme, theme } = useTheme();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<PaperBotStatusFilter>("all");
  const selectedRuntimeId = searchParams.get("runtime");
  const [activeTab, setActiveTab] = useState<PaperBotDrawerTab>("overview");
  const [killSwitchReason, setKillSwitchReason] = useState("");

  const sidebar = useQuery({
    queryFn: () => client.listConversationSidebar(),
    queryKey: ["conversation-sidebar"],
  });
  const runtimes = useQuery({
    queryFn: ({ signal }) => client.listNautilusRuntimes({ limit: 500, mode: "paper", signal }),
    queryKey: ["paper-bot-runtimes"],
    refetchInterval: 15_000,
  });
  const selectedRuntime = useMemo(
    () => runtimes.data?.items.find((runtime) => runtime.id === selectedRuntimeId) ?? null,
    [runtimes.data?.items, selectedRuntimeId]
  );
  const events = useQuery({
    enabled: Boolean(selectedRuntimeId),
    queryFn: ({ signal }) =>
      selectedRuntimeId
        ? client.listNautilusRuntimeEvents(selectedRuntimeId, { limit: 200, signal })
        : Promise.reject(new Error("No paper runtime selected")),
    queryKey: ["paper-bot-runtime-events", selectedRuntimeId],
    refetchInterval: selectedRuntime ? 10_000 : false,
  });
  const stopRuntime = useMutation({
    mutationFn: (runtimeId: string) => client.stopNautilusRuntime(runtimeId),
    onSuccess: (runtime) => updateRuntimeCache(queryClient, runtime),
  });
  const killSwitchRuntime = useMutation({
    mutationFn: ({ reason, runtimeId }: { reason: string; runtimeId: string }) =>
      client.killSwitchNautilusRuntime(runtimeId, { reason }),
    onSuccess: (runtime) => {
      setKillSwitchReason("");
      updateRuntimeCache(queryClient, runtime);
      void queryClient.invalidateQueries({ queryKey: ["paper-bot-runtime-events", runtime.id] });
    },
  });

  const filteredRuntimes = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return (runtimes.data?.items ?? [])
      .filter((runtime) => paperBotMatchesFilter(runtime, statusFilter))
      .filter((runtime) => !query || paperBotSearchText(runtime).includes(query));
  }, [runtimes.data?.items, searchQuery, statusFilter]);

  const selectRuntime = (runtimeId: string | null) => {
    setActiveTab("overview");
    const target = runtimeId ? `/paper-bots?runtime=${encodeURIComponent(runtimeId)}` : "/paper-bots";
    router.replace(target, { scroll: false });
  };

  return (
    <main className="apple-page-shell flex h-[100dvh] overflow-hidden text-foreground">
      <ConversationSidebar
        activeView="paper-bots"
        collapsed={sidebarCollapsed}
        conversations={sidebar.data?.items ?? []}
        isCreating={false}
        isLoading={sidebar.isLoading}
        isNewChatDisabled={false}
        language={language}
        onCreate={() => router.push("/")}
        onDelete={() => undefined}
        onLanguageChange={setLanguage}
        onOpenAccountDialog={() => undefined}
        onOpenArtifacts={() => router.push("/artifacts")}
        onOpenPaperBots={() => undefined}
        onOpenSettingsTab={() => undefined}
        onRename={() => undefined}
        onSelect={(conversationId) => router.push(`/c/${encodeURIComponent(conversationId)}`)}
        onThemeChange={setTheme}
        onToggleCollapsed={() => setSidebarCollapsed((collapsed) => !collapsed)}
        selectedConversationId={null}
        theme={theme}
      />
      <section className="flex min-h-0 min-w-0 flex-1 overflow-hidden bg-transparent lg:flex-row">
        <div className="min-h-0 min-w-0 flex-1 overflow-y-auto">
          <div
            className={cn(
              "mx-auto w-full px-4 py-8 transition-[max-width,padding] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] md:px-8 md:py-12",
              selectedRuntime ? "max-w-none" : "max-w-7xl"
            )}
          >
            <header className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <h1 className="apple-section-title">Bots</h1>
                <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
                  Monitor simulated runtimes, order intents, risk gates, and worker health. No broker execution.
                </p>
              </div>
              <Button
                className="self-start sm:self-auto"
                disabled={runtimes.isFetching}
                onClick={() => runtimes.refetch()}
                size="sm"
                type="button"
                variant="outline"
              >
                {runtimes.isFetching ? <Loader2 className="size-4 animate-spin" /> : <RefreshCcw className="size-4" />}
                Refresh
              </Button>
            </header>

            <div className="mt-6 flex flex-col gap-3 lg:flex-row lg:items-center">
              <label className="apple-search-shell flex h-11 min-w-0 flex-1 items-center gap-3 px-4 text-sm text-muted-foreground focus-within:border-primary/40">
                <Search className="size-4 shrink-0" />
                <span className="sr-only">Search bots</span>
                <input
                  className="h-full min-w-0 flex-1 bg-transparent text-foreground outline-none placeholder:text-muted-foreground"
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="Search bots..."
                  type="search"
                  value={searchQuery}
                />
              </label>
              <div className="apple-frosted flex flex-wrap gap-1 rounded-full border p-1">
                {PAPER_BOT_STATUS_FILTERS.map((filter) => (
                  <button
                    className={cn(
                      "h-8 rounded-full px-3 text-xs font-medium transition",
                      statusFilter === filter.value
                        ? "bg-background text-foreground"
                        : "text-muted-foreground hover:text-foreground"
                    )}
                    key={filter.value}
                    onClick={() => setStatusFilter(filter.value)}
                    type="button"
                  >
                    {filter.label}
                  </button>
                ))}
              </div>
            </div>

            <section className="mt-7">
              {runtimes.isLoading ? (
                <PaperBotsLoading />
              ) : runtimes.isError ? (
                <PaperBotsEmpty
                  description="The paper runtime inventory could not be loaded."
                  icon={<AlertTriangle className="size-5" />}
                  title="Could not load bots"
                />
              ) : filteredRuntimes.length === 0 ? (
                <PaperBotsEmpty
                  description="Create or start a paper runtime from chat to see it here."
                  icon={<Bot className="size-5" />}
                  title="No bots found"
                />
              ) : (
                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                  {filteredRuntimes.map((runtime) => (
                    <PaperBotCard
                      key={runtime.id}
                      onSelect={() => selectRuntime(runtime.id)}
                      runtime={runtime}
                      selected={selectedRuntimeId === runtime.id}
                    />
                  ))}
                </div>
              )}
            </section>
          </div>
        </div>
        <PaperBotDrawer
          activeTab={activeTab}
          events={events.data ?? []}
          eventsLoading={events.isLoading}
          killSwitchPending={killSwitchRuntime.isPending}
          killSwitchReason={killSwitchReason}
          onClose={() => selectRuntime(null)}
          onKillSwitch={(runtimeId) => {
            if (killSwitchReason.trim()) {
              killSwitchRuntime.mutate({ reason: killSwitchReason.trim(), runtimeId });
            }
          }}
          onKillSwitchReasonChange={setKillSwitchReason}
          onStop={(runtimeId) => stopRuntime.mutate(runtimeId)}
          onTabChange={setActiveTab}
          runtime={selectedRuntime}
          stopPending={stopRuntime.isPending}
        />
      </section>
    </main>
  );
}

function PaperBotCard({
  onSelect,
  runtime,
  selected,
}: {
  onSelect: () => void;
  runtime: NautilusRuntime;
  selected: boolean;
}) {
  const metrics = paperBotMetricPairs(runtime);
  const tone = paperBotStateTone(runtime);
  return (
    <button
      className={cn(
        "apple-utility-card group flex min-h-[190px] flex-col p-4 text-left transition hover:-translate-y-0.5 hover:border-primary/40",
        selected && "border-primary/60 ring-1 ring-primary/30",
        tone === "danger" && "border-red-500/50",
        tone === "warning" && "border-amber-500/40"
      )}
      onClick={onSelect}
      type="button"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold">{paperBotDisplayName(runtime)}</h2>
          <p className="mt-1 truncate text-xs text-muted-foreground">{paperBotRuntimeLabel(runtime)}</p>
        </div>
        <StatusBadge runtime={runtime} />
      </div>
      <div className="mt-4 grid grid-cols-2 gap-2">
        {(metrics.length > 0 ? metrics : [{ label: "Heartbeat", value: String(runtime.heartbeat_count) }]).map((metric) => (
          <div className="rounded-[8px] border border-border bg-background/60 px-3 py-2" key={metric.label}>
            <p className="text-[11px] text-muted-foreground">{metric.label}</p>
            <p className="mt-0.5 truncate text-sm font-medium">{metric.value}</p>
          </div>
        ))}
      </div>
      <div className="mt-auto space-y-2 pt-4 text-xs text-muted-foreground">
        <p className="truncate">Risk: {runtime.risk_policy_id}</p>
        <p>Heartbeat: {formatPaperBotTime(runtime.last_heartbeat_at)}</p>
        {runtime.last_error ? <p className="truncate text-red-300">Last error recorded</p> : null}
      </div>
    </button>
  );
}

function PaperBotDrawer({
  activeTab,
  events,
  eventsLoading,
  killSwitchPending,
  killSwitchReason,
  onClose,
  onKillSwitch,
  onKillSwitchReasonChange,
  onStop,
  onTabChange,
  runtime,
  stopPending,
}: {
  activeTab: PaperBotDrawerTab;
  events: NautilusRuntimeEvent[];
  eventsLoading: boolean;
  killSwitchPending: boolean;
  killSwitchReason: string;
  onClose: () => void;
  onKillSwitch: (runtimeId: string) => void;
  onKillSwitchReasonChange: (value: string) => void;
  onStop: (runtimeId: string) => void;
  onTabChange: (tab: PaperBotDrawerTab) => void;
  runtime: NautilusRuntime | null;
  stopPending: boolean;
}) {
  const artifactIds = useMemo(() => (runtime ? paperBotArtifactIds(runtime, events) : []), [events, runtime]);
  return (
    <aside
      aria-hidden={!runtime}
      className={cn(
        "apple-product-tile shrink-0 overflow-hidden transition-[width,max-height,opacity,transform,border-color] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]",
        runtime
          ? "w-full border-t border-border opacity-100 lg:max-h-none lg:w-[min(1040px,64vw)] lg:border-l lg:border-t-0 xl:w-[1040px]"
          : "w-0 translate-x-4 border-transparent opacity-0"
      )}
    >
      <div className="h-full max-h-[82dvh] overflow-y-auto p-4 lg:max-h-none lg:p-6">
        {runtime ? (
          <>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 className="truncate text-lg font-semibold">{paperBotDisplayName(runtime)}</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  Simulated order intents and runtime evidence. No broker execution.
                </p>
              </div>
              <Button aria-label="Close bot drawer" onClick={onClose} size="icon-sm" type="button" variant="ghost">
                <X className="size-4" />
              </Button>
            </div>
            <div className="apple-frosted mt-4 flex flex-wrap gap-1 rounded-full border p-1">
              {DRAWER_TABS.map((tab) => (
                <button
                  className={cn(
                    "h-8 rounded-full px-3 text-xs font-medium transition",
                    activeTab === tab.value
                      ? "bg-background text-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                  key={tab.value}
                  onClick={() => onTabChange(tab.value)}
                  type="button"
                >
                  {tab.label}
                </button>
              ))}
            </div>
            <div className="mt-5">
              {activeTab === "overview" ? <OverviewTab runtime={runtime} /> : null}
              {activeTab === "activity" ? <ActivityTab events={events} loading={eventsLoading} /> : null}
              {activeTab === "risk" ? (
                <RiskTab
                  events={events}
                  killSwitchPending={killSwitchPending}
                  killSwitchReason={killSwitchReason}
                  onKillSwitch={() => onKillSwitch(runtime.id)}
                  onKillSwitchReasonChange={onKillSwitchReasonChange}
                  runtime={runtime}
                />
              ) : null}
              {activeTab === "artifacts" ? <ArtifactsTab artifactIds={artifactIds} /> : null}
              {activeTab === "settings" ? (
                <SettingsTabContent onStop={() => onStop(runtime.id)} runtime={runtime} stopPending={stopPending} />
              ) : null}
            </div>
          </>
        ) : null}
      </div>
    </aside>
  );
}

function OverviewTab({ runtime }: { runtime: NautilusRuntime }) {
  return (
    <div className="space-y-5">
      <div className="grid gap-3 md:grid-cols-3">
        <InfoTile label="State" value={runtime.state} />
        <InfoTile label="Desired state" value={runtime.desired_state} />
        <InfoTile label="Last heartbeat" value={formatPaperBotTime(runtime.last_heartbeat_at)} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Runtime identity">
          <DescriptionList
            rows={[
              ["Runtime", runtime.id],
              ["Broker connection", runtime.broker_connection_id],
              ["Account", runtime.account_id],
              ["Risk policy", runtime.risk_policy_id],
              ["Worker", runtime.worker_id ?? "Unassigned"],
            ]}
          />
        </Panel>
        <Panel title="Strategy and data">
          <DescriptionList
            rows={[
              ["Strategies", runtime.strategy_ids.join(", ") || "None"],
              ["Subscriptions", paperBotSubscriptions(runtime).join(", ") || "None"],
              ["Started", formatPaperBotTime(runtime.started_at)],
              ["Updated", formatPaperBotTime(runtime.updated_at)],
            ]}
          />
        </Panel>
      </div>
      <Panel title="Paper runtime metrics">
        <MetricGrid runtime={runtime} />
      </Panel>
    </div>
  );
}

function ActivityTab({ events, loading }: { events: NautilusRuntimeEvent[]; loading: boolean }) {
  if (loading) {
    return <PaperBotsEmpty description="Loading runtime events..." icon={<Loader2 className="size-5 animate-spin" />} title="Loading activity" />;
  }
  if (events.length === 0) {
    return <PaperBotsEmpty description="Routine heartbeat and paper runtime events will appear here." icon={<Bot className="size-5" />} title="No activity yet" />;
  }
  return (
    <div className="space-y-3">
      {events.map((event) => (
        <div className="apple-utility-card p-3" key={event.event_id}>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-medium">{readableEventType(event.type)}</p>
              <p className="mt-1 text-xs text-muted-foreground">{paperBotEventSummary(event)}</p>
            </div>
            <EventSeverityBadge event={event} />
          </div>
          <p className="mt-3 text-[11px] text-muted-foreground">
            #{event.sequence} · {formatPaperBotTime(event.created_at)}
          </p>
        </div>
      ))}
    </div>
  );
}

function RiskTab({
  events,
  killSwitchPending,
  killSwitchReason,
  onKillSwitch,
  onKillSwitchReasonChange,
  runtime,
}: {
  events: NautilusRuntimeEvent[];
  killSwitchPending: boolean;
  killSwitchReason: string;
  onKillSwitch: () => void;
  onKillSwitchReasonChange: (value: string) => void;
  runtime: NautilusRuntime;
}) {
  const riskEvents = events.filter((event) => event.type === "risk_block" || event.type === "error");
  return (
    <div className="space-y-5">
      <div className="grid gap-3 md:grid-cols-3">
        <InfoTile label="Risk policy" value={runtime.risk_policy_id} />
        <InfoTile label="Kill switch" value={runtime.kill_switch_active ? "Active" : "Inactive"} />
        <InfoTile label="Runtime state" value={runtime.state} />
      </div>
      <Panel title="Prominent risk events">
        {riskEvents.length === 0 ? (
          <p className="text-sm text-muted-foreground">No risk blocks or runtime errors recorded.</p>
        ) : (
          <div className="space-y-2">
            {riskEvents.map((event) => (
                <p className="rounded-[8px] border border-border bg-background/60 px-3 py-2 text-sm" key={event.event_id}>
                {readableEventType(event.type)}: {paperBotEventSummary(event)}
              </p>
            ))}
          </div>
        )}
      </Panel>
      <Panel title="Kill switch">
        <p className="text-sm text-muted-foreground">
          Use this only to stop a paper runtime for safety review. It records a risk block event and does not place broker orders.
        </p>
        <div className="mt-3 flex flex-col gap-2 sm:flex-row">
          <input
            className="h-10 min-w-0 flex-1 rounded-full border border-border bg-background px-4 text-sm outline-none focus:border-primary/50"
            onChange={(event) => onKillSwitchReasonChange(event.target.value)}
            placeholder="Required reason"
            value={killSwitchReason}
          />
          <Button
            disabled={killSwitchPending || !killSwitchReason.trim() || runtime.kill_switch_active}
            onClick={onKillSwitch}
            type="button"
            variant="destructive"
          >
            {killSwitchPending ? <Loader2 className="size-4 animate-spin" /> : <ShieldAlert className="size-4" />}
            Kill switch
          </Button>
        </div>
      </Panel>
    </div>
  );
}

function ArtifactsTab({ artifactIds }: { artifactIds: string[] }) {
  if (artifactIds.length === 0) {
    return <PaperBotsEmpty description="Linked runtime artifacts will appear when events include artifact ids." icon={<FileStack className="size-5" />} title="No linked artifacts" />;
  }
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {artifactIds.map((artifactId) => (
        <Link
          className="apple-utility-card p-3 text-sm transition hover:border-primary/40"
          href={`/artifacts?artifact=${encodeURIComponent(artifactId)}`}
          key={artifactId}
        >
          <p className="font-medium">Linked artifact</p>
          <p className="mt-1 truncate text-xs text-muted-foreground">{artifactId}</p>
        </Link>
      ))}
    </div>
  );
}

function SettingsTabContent({
  onStop,
  runtime,
  stopPending,
}: {
  onStop: () => void;
  runtime: NautilusRuntime;
  stopPending: boolean;
}) {
  return (
    <div className="space-y-5">
      <Panel title="Runtime configuration">
        <DescriptionList
          rows={[
            ["Mode", "Simulation"],
            ["Runtime key", runtime.runtime_key],
            ["Generation", String(runtime.generation)],
            ["Lease until", formatPaperBotTime(runtime.lease_until)],
            ["Stream cursor", runtime.stream_cursor ? "Recorded" : "None"],
          ]}
        />
      </Panel>
      <Panel title="Stop bot">
        <p className="text-sm text-muted-foreground">
          Requests the simulated runtime to stop. This is runtime control and not broker execution.
        </p>
        <Button
          className="mt-3"
          disabled={stopPending || runtime.desired_state === "stopping" || runtime.state === "stopped"}
          onClick={onStop}
          type="button"
          variant="outline"
        >
          {stopPending ? <Loader2 className="size-4 animate-spin" /> : <Square className="size-4" />}
          Stop bot
        </Button>
      </Panel>
    </div>
  );
}

function StatusBadge({ runtime }: { runtime: NautilusRuntime }) {
  const tone = paperBotStateTone(runtime);
  return (
    <span
      className={cn(
        "shrink-0 rounded-[4px] border px-2 py-1 text-[11px] font-medium uppercase",
        tone === "success" && "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
        tone === "warning" && "border-amber-500/30 bg-amber-500/10 text-amber-300",
        tone === "danger" && "border-red-500/30 bg-red-500/10 text-red-300",
        tone === "neutral" && "border-border bg-secondary text-muted-foreground"
      )}
    >
      {runtime.kill_switch_active ? "Kill switch" : runtime.state}
    </span>
  );
}

function EventSeverityBadge({ event }: { event: NautilusRuntimeEvent }) {
  const severity = paperBotEventSeverity(event);
  return (
    <span
      className={cn(
        "shrink-0 rounded-[4px] border px-2 py-1 text-[11px] font-medium uppercase",
        severity === "high" && "border-red-500/30 bg-red-500/10 text-red-300",
        severity === "medium" && "border-amber-500/30 bg-amber-500/10 text-amber-300",
        severity === "low" && "border-border bg-secondary text-muted-foreground"
      )}
    >
      {severity}
    </span>
  );
}

function MetricGrid({ runtime }: { runtime: NautilusRuntime }) {
  const metrics = paperBotMetricPairs(runtime);
  if (metrics.length === 0) {
    return <p className="text-sm text-muted-foreground">No paper runtime metrics recorded yet.</p>;
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {metrics.map((metric) => (
        <InfoTile key={metric.label} label={metric.label} value={metric.value} />
      ))}
    </div>
  );
}

function InfoTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="apple-utility-card p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 break-words text-sm font-medium">{value}</p>
    </div>
  );
}

function Panel({ children, title }: { children: ReactNode; title: string }) {
  return (
    <section className="apple-utility-card p-4">
      <h3 className="text-sm font-semibold">{title}</h3>
      <div className="mt-3">{children}</div>
    </section>
  );
}

function DescriptionList({ rows }: { rows: Array<[string, string]> }) {
  return (
    <dl className="space-y-2">
      {rows.map(([label, value]) => (
        <div className="grid gap-1 text-sm sm:grid-cols-[140px_1fr]" key={label}>
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="min-w-0 break-words font-medium">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function PaperBotsEmpty({
  description,
  icon,
  title,
}: {
  description: string;
  icon: ReactNode;
  title: string;
}) {
  return (
    <div className="apple-utility-card flex min-h-[220px] flex-col items-center justify-center border-dashed px-4 text-center">
      <div className="mb-3 flex size-10 items-center justify-center rounded-full border border-border bg-background text-muted-foreground">
        {icon}
      </div>
      <h2 className="text-sm font-semibold">{title}</h2>
      <p className="mt-1 max-w-md text-sm text-muted-foreground">{description}</p>
    </div>
  );
}

function PaperBotsLoading() {
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 6 }).map((_, index) => (
        <div className="apple-utility-card h-[190px] animate-pulse" key={index} />
      ))}
    </div>
  );
}

function readableEventType(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (match) => match.toUpperCase());
}

function updateRuntimeCache(queryClient: ReturnType<typeof useQueryClient>, runtime: NautilusRuntime) {
  queryClient.setQueryData<{ items: NautilusRuntime[] }>(["paper-bot-runtimes"], (current) => {
    if (!current) {
      return { items: [runtime] };
    }
    const items = current.items.some((item) => item.id === runtime.id)
      ? current.items.map((item) => (item.id === runtime.id ? runtime : item))
      : [runtime, ...current.items];
    return { items };
  });
}
