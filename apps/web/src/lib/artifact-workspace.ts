import {
  BACKTEST_RUN_EVENTS,
  BACKTEST_RUN_EVENT_TYPES,
  type Artifact,
  type Run,
  type RunEvent,
} from "@/lib/backend-schemas";
import { getUiCopy, type LanguagePreference } from "@/lib/i18n";
import { userFacingPreviewText } from "@/lib/preview-text";

export type ArtifactWorkspaceStep = {
  label: string;
  state: "done" | "current" | "waiting";
};

export const ARTIFACT_WORKSPACE_TABS = [
  "strategy",
  "code",
  "risk",
  "validation",
  "changes",
] as const;

export type ArtifactWorkspaceTab = (typeof ARTIFACT_WORKSPACE_TABS)[number];

export type ArtifactUserKind = Artifact["presentation"]["user_kind"];

export type ArtifactUserSummary = {
  description: string;
  kind: ArtifactUserKind;
  label: string;
};

export type GroupedArtifacts = {
  code: Artifact[];
  notes: Artifact[];
  report: Artifact[];
  risk: Artifact[];
  validation: Artifact[];
};

export function getPrimaryArtifact(
  artifacts: Artifact[],
  selectedArtifactId: string | null
) {
  return artifacts.find((artifact) => artifact.id === selectedArtifactId) ?? artifacts[0] ?? null;
}

export function getArtifactForTab(
  artifacts: Artifact[],
  selectedArtifactId: string | null,
  tab: ArtifactWorkspaceTab
) {
  const grouped = groupArtifactsByKind(artifacts);
  return getArtifactForGroupedTab(artifacts, grouped, selectedArtifactId, tab);
}

export function getBestArtifactForDrawer(
  artifacts: Artifact[],
  options: { preferredKind?: string | null } = {}
) {
  const userVisibleArtifacts = artifacts.filter(
    (artifact) => artifact.presentation.visibility !== "internal"
  );
  if (options.preferredKind) {
    const preferred = userVisibleArtifacts.find(
      (artifact) => artifact.kind === options.preferredKind
    );
    if (preferred) {
      return preferred;
    }
  }
  const backtestDashboard = userVisibleArtifacts.find(
    (artifact) => artifact.presentation.viewer_kind === "backtest_dashboard"
  );
  if (backtestDashboard) {
    return backtestDashboard;
  }
  const backtestReport = userVisibleArtifacts.find(
    (artifact) => artifact.presentation.viewer_kind === "backtest_report"
  );
  if (backtestReport) {
    return backtestReport;
  }
  const visibleArtifacts = getUserFacingArtifacts(artifacts);
  const grouped = groupArtifactsByKind(visibleArtifacts);
  return grouped.code[0] ?? grouped.report[0] ?? grouped.notes[0] ?? visibleArtifacts[0] ?? null;
}

export function getDefaultArtifactTab(
  artifacts: Artifact[],
  artifactId: string | null
): ArtifactWorkspaceTab {
  const grouped = groupArtifactsByKind(artifacts);
  if (artifactId && grouped.code.some((artifact) => artifact.id === artifactId)) {
    return "code";
  }
  if (artifactId && grouped.risk.some((artifact) => artifact.id === artifactId)) {
    return "risk";
  }
  if (artifactId && grouped.validation.some((artifact) => artifact.id === artifactId)) {
    return "validation";
  }
  if (artifactId && grouped.report.some((artifact) => artifact.id === artifactId)) {
    return "strategy";
  }
  return "strategy";
}

export function getArtifactForGroupedTab(
  artifacts: Artifact[],
  grouped: GroupedArtifacts,
  selectedArtifactId: string | null,
  tab: ArtifactWorkspaceTab
) {
  if (tab === "code") {
    return getPrimaryArtifact(grouped.code, selectedArtifactId);
  }
  if (tab === "risk") {
    return getPrimaryArtifact(grouped.risk, selectedArtifactId);
  }
  if (tab === "validation") {
    return getPrimaryArtifact(grouped.validation, selectedArtifactId);
  }
  if (tab === "changes") {
    return getPrimaryArtifact(grouped.report, selectedArtifactId);
  }
  if (tab === "strategy") {
    return getPrimaryArtifact(grouped.notes, selectedArtifactId);
  }
  const selected = artifacts.find((artifact) => artifact.id === selectedArtifactId);
  if (selected && !isCodeArtifact(selected)) {
    return selected;
  }
  return (
    grouped.notes[0] ??
    artifacts.find((artifact) => !isCodeArtifact(artifact)) ??
    grouped.code[0] ??
    artifacts[0] ??
    null
  );
}

export function groupArtifactsByKind(artifacts: Artifact[]) {
  const visibleArtifacts = getUserFacingArtifacts(artifacts);
  const risk = visibleArtifacts.filter((artifact) => classifyArtifact(artifact) === "risk");
  const validation = visibleArtifacts.filter((artifact) => classifyArtifact(artifact) === "validation");
  const notes = visibleArtifacts.filter((artifact) =>
    ["risk", "validation", "report"].includes(classifyArtifact(artifact))
  );
  const noteIds = new Set(notes.map((artifact) => artifact.id));
  const riskIds = new Set(risk.map((artifact) => artifact.id));
  const validationIds = new Set(validation.map((artifact) => artifact.id));
  return {
    code: visibleArtifacts.filter((artifact) => !noteIds.has(artifact.id) && classifyArtifact(artifact) === "code"),
    notes,
    report: notes.filter((artifact) => !riskIds.has(artifact.id) && !validationIds.has(artifact.id)),
    risk,
    validation,
  } satisfies GroupedArtifacts;
}

export function getUserFacingArtifacts(artifacts: Artifact[]) {
  const seen = new Set<string>();
  return artifacts.filter((artifact) => {
    if (artifact.presentation.visibility === "internal" || !isPrimaryUserArtifact(artifact)) {
      return false;
    }
    const key = userArtifactDedupeKey(artifact);
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

export function getArtifactUserSummary(
  artifact: Artifact,
  language: LanguagePreference = "en"
): ArtifactUserSummary {
  const t = getUiCopy(language);
  const kind = classifyArtifact(artifact);
  if (kind === "code") {
    return {
      description: t.codeArtifactDescription,
      kind: "code",
      label: t.codeArtifact,
    };
  }
  if (kind === "risk") {
    return {
      description: t.riskNotesDescription,
      kind: "risk",
      label: t.riskNotes,
    };
  }
  if (kind === "validation") {
    return {
      description: t.validationSummaryDescription,
      kind: "validation",
      label: t.validationSummary,
    };
  }
  if (kind === "evidence") {
    return {
      description: t.evidenceSummaryDescription,
      kind: "evidence",
      label: t.evidenceSummary,
    };
  }
  return {
    description: t.reviewReportDescription,
    kind: "report",
    label: t.reviewReport,
  };
}

export function currentProgressStep(steps: ArtifactWorkspaceStep[]) {
  return (
    steps.find((step) => step.state === "current") ??
    [...steps].reverse().find((step) => step.state === "done") ??
    steps[0] ??
    null
  );
}

export function runStatusSummary(
  status: Run["status"],
  language: LanguagePreference = "en"
) {
  const t = getUiCopy(language);
  if (status === "failed") {
    return t.couldNotCreateArtifact;
  }
  if (status === "blocked") {
    return t.reviewBoundaryReached;
  }
  if (status === "cancelled") {
    return t.artifactCreationCancelled;
  }
  if (status === "completed") {
    return t.reviewArtifactReady;
  }
  return t.creatingReviewArtifact;
}

export type BacktestLiveStatus = {
  runId: string;
  status: "queued" | "running" | "completed" | "failed";
  stage: "planning" | "cache" | "fetching" | "exporting" | "executing" | "indexing" | "reporting" | "completed" | "failed";
  progressPct: number;
  elapsedMs: number | null;
  etaMs: number | null;
  message: string;
  updatedAt: string | null;
  isStale: boolean;
  fetchWindowsCompleted: number | null;
  fetchWindowsTotal: number | null;
};

const BACKTEST_LIVE_STALE_MS = 45_000;
const BACKTEST_LIVE_STATUS_EVENT_TYPES = new Set([
  "backtest.preview.heartbeat",
  "backtest.preview.approval_required",
  "backtest.preview.queued",
  "backtest.preview.failed",
  "backtest.preview.rejected",
  "chat.auto_chain.waiting_for_backtest",
]);

function isBacktestLiveStatusEvent(event: RunEvent): boolean {
  if (BACKTEST_LIVE_STATUS_EVENT_TYPES.has(event.type)) {
    return true;
  }
  const payload = recordPayload(event);
  return (
    stringPayload(payload, "card_kind") === "backtest_live_status" ||
    stringPayload(payload, "preferred_artifact_kind") === "backtest_report"
  );
}

export function backtestLiveStatusFromRunEvents(
  events: RunEvent[],
  nowMs = Date.now()
): BacktestLiveStatus | null {
  let latestRelevant: RunEvent | null = null;
  let latestRelevantIndex = -1;
  for (let index = events.length - 1; index >= 0; index -= 1) {
    if (isBacktestLiveStatusEvent(events[index])) {
      latestRelevant = events[index];
      latestRelevantIndex = index;
      break;
    }
  }
  if (!latestRelevant) {
    return null;
  }
  const latestPayload = recordPayload(latestRelevant);
  if (
    latestRelevant.type === "backtest.preview.heartbeat" ||
    stringPayload(latestPayload, "card_kind") === "backtest_live_status"
  ) {
    const payload = latestPayload;
    const status = stringPayload(payload, "status");
    const stage = stringPayload(payload, "stage");
    const updatedAt = stringPayload(payload, "updated_at") ?? latestRelevant.created_at;
    const updatedMs = Date.parse(updatedAt);
    return {
      runId: latestRelevant.run_id,
      status: status === "queued" || status === "completed" || status === "failed" ? status : "running",
      stage: isBacktestLiveStage(stage) ? stage : "planning",
      progressPct: clampPercent(numberPayload(payload, "progress_pct") ?? 0),
      elapsedMs: numberPayload(payload, "elapsed_ms"),
      etaMs: numberPayload(payload, "eta_ms"),
      message: userFacingPreviewText(stringPayload(payload, "message") ?? "Backtest preview is running."),
      updatedAt,
      isStale:
        Number.isFinite(updatedMs) &&
        !["completed", "failed"].includes(String(status)) &&
        nowMs - updatedMs > BACKTEST_LIVE_STALE_MS,
      fetchWindowsCompleted: numberPayload(payload, "fetch_windows_completed"),
      fetchWindowsTotal: numberPayload(payload, "fetch_windows_total"),
    };
  }
  if (latestRelevant.type === "backtest.preview.rejected") {
    return null;
  }
  if (latestRelevant.type === "backtest.preview.failed") {
    const payload = latestPayload;
    return {
      runId: latestRelevant.run_id,
      status: "failed",
      stage: "failed",
      progressPct: 0,
      elapsedMs: null,
      etaMs: null,
      message: userFacingPreviewText(stringPayload(payload, "message") ?? "Backtest preview failed."),
      updatedAt: latestRelevant.created_at,
      isStale: false,
      fetchWindowsCompleted: null,
      fetchWindowsTotal: null,
    };
  }
  const payload = latestPayload;
  const childRunId = stringPayload(payload, "child_run_id");
  if (childRunId) {
    const terminal = backtestChildTerminalStatus(events, latestRelevantIndex, childRunId);
    if (terminal) {
      return terminal;
    }
  }
  return {
    runId: childRunId ?? latestRelevant.run_id,
    status: "queued",
    stage: "planning",
    progressPct: latestRelevant.type === "backtest.preview.approval_required" ? 0 : 2,
    elapsedMs: null,
    etaMs: null,
    message:
      latestRelevant.type === "backtest.preview.approval_required"
        ? "Backtest plan is waiting for approval."
        : "Backtest preview is queued.",
    updatedAt: latestRelevant.created_at,
    isStale: false,
    fetchWindowsCompleted: null,
    fetchWindowsTotal: null,
  };
}

function backtestChildTerminalStatus(
  events: RunEvent[],
  afterIndex: number,
  childRunId: string
): BacktestLiveStatus | null {
  for (let index = events.length - 1; index > afterIndex; index -= 1) {
    const event = events[index];
    if (event.run_id !== childRunId) {
      continue;
    }
    if (event.type === "run.completed") {
      return {
        runId: childRunId,
        status: "completed",
        stage: "completed",
        progressPct: 100,
        elapsedMs: null,
        etaMs: null,
        message: "Backtest preview artifacts are ready.",
        updatedAt: event.created_at,
        isStale: false,
        fetchWindowsCompleted: null,
        fetchWindowsTotal: null,
      };
    }
    if (event.type === "run.failed") {
      return {
        runId: childRunId,
        status: "failed",
        stage: "failed",
        progressPct: 0,
        elapsedMs: null,
        etaMs: null,
        message: stringPayload(recordPayload(event), "message") ?? "Backtest preview failed.",
        updatedAt: event.created_at,
        isStale: false,
        fetchWindowsCompleted: null,
        fetchWindowsTotal: null,
      };
    }
  }
  return null;
}

export function backtestLiveStageLabel(status: Pick<BacktestLiveStatus, "stage" | "message">): string {
  if (status.stage === "fetching") {
    return "Fetching missing candles";
  }
  if (status.stage === "executing") {
    return "Running local preview engine";
  }
  if (status.stage === "indexing" || status.stage === "reporting") {
    return "Building dashboard artifacts";
  }
  if (status.stage === "completed") {
    return "Backtest preview ready";
  }
  if (status.stage === "failed") {
    return status.message.includes("skipped") ? "Backtest preview skipped" : "Backtest failed";
  }
  if (status.stage === "cache") {
    return "Checking cached candles";
  }
  if (status.stage === "exporting") {
    return "Preparing preview input";
  }
  return "Preparing backtest preview";
}

function classifyArtifact(artifact: Artifact): ArtifactUserKind {
  return artifact.presentation.user_kind === "dashboard"
    ? "report"
    : artifact.presentation.user_kind;
}

function isCodeArtifact(artifact: Artifact) {
  return artifact.presentation.user_kind === "code";
}

function isPrimaryUserArtifact(artifact: Artifact) {
  return artifact.presentation.is_primary;
}

function userArtifactDedupeKey(artifact: Artifact) {
  return artifact.presentation.dedupe_key;
}

export function mapRunEventsToUserSteps(
  events: RunEvent[],
  status: Run["status"],
  language: LanguagePreference = "en"
): ArtifactWorkspaceStep[] {
  const t = getUiCopy(language);
  const stages = [
    { label: t.readingStrategy, names: ["model"] },
    { label: t.generatingReviewArtifact, names: ["runner", "tool"] },
    { label: t.checkingReviewBoundaries, names: ["validation", "review"] },
    { label: t.preparingFiles, names: ["artifact"] },
  ];
  const completed = status === "completed";
  const stopped = ["failed", "blocked", "cancelled"].includes(status);
  const backtestStages = [
    {
      label: t.backtestCheckingCachedCandles,
      started: [BACKTEST_RUN_EVENTS.dataPlanning],
      completed: [
        BACKTEST_RUN_EVENTS.dataCacheReusing,
        BACKTEST_RUN_EVENTS.dataFetching,
        BACKTEST_RUN_EVENTS.dataExporting,
        BACKTEST_RUN_EVENTS.dataCompleted,
      ],
    },
    {
      label: t.backtestFetchingMissingCandles,
      started: [BACKTEST_RUN_EVENTS.dataFetching],
      completed: [BACKTEST_RUN_EVENTS.dataExporting, BACKTEST_RUN_EVENTS.dataCompleted],
    },
    {
      label: t.backtestPreparingPineForgeInput,
      started: [BACKTEST_RUN_EVENTS.dataExporting],
      completed: [BACKTEST_RUN_EVENTS.executionStarted, BACKTEST_RUN_EVENTS.executionCompleted],
    },
    {
      label: t.backtestRunning,
      started: [BACKTEST_RUN_EVENTS.executionStarted],
      completed: [BACKTEST_RUN_EVENTS.executionCompleted],
    },
    {
      label: t.backtestIndexingReport,
      started: [BACKTEST_RUN_EVENTS.indexingStarted],
      completed: [BACKTEST_RUN_EVENTS.reportCompleted, "run.completed"],
    },
  ];
  const knownBacktestEvents = new Set<string>(BACKTEST_RUN_EVENT_TYPES);
  if (events.some((event) => knownBacktestEvents.has(event.type))) {
    const eventTypes = new Set(events.map((event) => event.type));
    return backtestStages.map((stage) => {
      const stageDone = completed || stage.completed.some((type) => eventTypes.has(type));
      const stageStarted = stage.started.some((type) => eventTypes.has(type));
      return {
        label: stage.label,
        state: stageDone ? "done" : stageStarted && !stopped ? "current" : "waiting",
      };
    });
  }
  const started = stageSet(events, "stage.started");
  const completedStages = stageSet(events, "stage.completed");

  return stages.map((stage) => {
    const stageDone = stage.names.some((name) => completedStages.has(name));
    const stageStarted = stage.names.some((name) => started.has(name));
    return {
      label: stage.label,
      state: completed || stageDone ? "done" : stageStarted && !stopped ? "current" : "waiting",
    };
  });
}

function stageSet(events: RunEvent[], type: string) {
  const values = new Set<string>();
  for (const event of events) {
    const payload = stagePayload(event, type);
    if (!payload) {
      continue;
    }
    const stage = payload.stage;
    if (typeof stage === "string") {
      values.add(stage);
    }
  }
  return values;
}

function stagePayload(event: RunEvent, type: string): Record<string, unknown> | null {
  if (!event.payload || typeof event.payload !== "object" || Array.isArray(event.payload)) {
    return null;
  }
  const payload = event.payload as Record<string, unknown>;
  if (event.type === type) {
    return payload;
  }
  if (event.type !== "progress.update" || payload.source_event_type !== type) {
    return null;
  }
  const sourcePayload = payload.payload;
  return sourcePayload && typeof sourcePayload === "object" && !Array.isArray(sourcePayload)
    ? (sourcePayload as Record<string, unknown>)
    : null;
}

function recordPayload(event: RunEvent): Record<string, unknown> {
  return event.payload && typeof event.payload === "object" && !Array.isArray(event.payload)
    ? (event.payload as Record<string, unknown>)
    : {};
}

function stringPayload(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function numberPayload(payload: Record<string, unknown>, key: string): number | null {
  const value = payload[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function clampPercent(value: number): number {
  return Math.max(0, Math.min(100, Math.round(value)));
}

export function isBacktestLiveStage(value: string | null): value is BacktestLiveStatus["stage"] {
  return (
    value === "planning" ||
    value === "cache" ||
    value === "fetching" ||
    value === "exporting" ||
    value === "executing" ||
    value === "indexing" ||
    value === "reporting" ||
    value === "completed" ||
    value === "failed"
  );
}
