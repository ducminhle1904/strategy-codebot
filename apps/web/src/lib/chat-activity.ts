import {
  AGENT_WORKFLOW_OBSERVABILITY_EVENT_TYPES,
  KNOWN_RUN_EVENT_TYPES,
  WORKFLOW_CONTINUATION_EVENT_TYPES,
  type KnownRunEventType,
  type RunEvent,
} from "@/lib/backend-schemas";
import type { ToolPart } from "@/components/ai-elements/tool";
import {
  actionToolActivityLabel,
  type ActionRegistryLookup,
} from "@/lib/action-tool-metadata";
import {
  AUTO_CHAIN_SUMMARY_PENDING_EVENT,
  AUTO_CHAIN_SUMMARY_TIMEOUT_EVENT,
} from "@/lib/auto-chain-continuation";
import { backtestLiveStageLabel, isBacktestLiveStage } from "@/lib/artifact-workspace";
import { WORKFLOW_TOOL_EVENTS } from "@/lib/copilot-workflow-events";
import { getUiCopy, type LanguagePreference } from "@/lib/i18n";
import { userFacingPreviewText } from "@/lib/preview-text";

export type ChatActivityArtifactLink = { artifactId: string; label: string };

export type ChatActivityDetail = { label: string; value: string };

export type ChatActivity = {
  id: string;
  title: string;
  description: string;
  state: ToolPart["state"];
  toolName: string;
  artifactLinks?: ChatActivityArtifactLink[];
  details?: ChatActivityDetail[];
  output?: string;
  errorText?: string;
};

const MAX_ACTIVITIES = 5;
const IGNORED_ACTIVITY_EVENT_TYPES = new Set<KnownRunEventType>([
  "backtest.data.completed",
  "backtest.data.started",
  "backtest.execution.completed",
  "backtest.queued",
  "message.delta",
  ...AGENT_WORKFLOW_OBSERVABILITY_EVENT_TYPES,
  "model_action.executed",
  "model_action.proposed",
  "model_action.rejected",
  "model_action.validated",
  "model.reasoning.delta",
  "observability.stage.completed",
  "provider.started",
  "progress.snapshot",
  "progress.update",
  "run.completed",
  "stage.completed",
  "stage.started",
  "workflow.gate.confirmed",
  "workflow.gate.rejected",
  "workflow.gate.required",
  ...WORKFLOW_CONTINUATION_EVENT_TYPES,
]);

type ActivityFactory = (
  event: RunEvent,
  language: LanguagePreference,
  registry?: ActionRegistryLookup
) => Omit<ChatActivity, "id">;

const WORKFLOW_ACTIVITY_FACTORIES: Record<string, ActivityFactory> = Object.fromEntries(
  Object.entries(WORKFLOW_TOOL_EVENTS).map(([eventType, config]) => [
    eventType,
    (event) => ({
      artifactLinks: artifactLinksFromRunEvent(event),
      description: config.activityLabel,
      details: activityDetails({ Status: activityStatusLabel(config.activityStatus) }),
      state:
        config.activityStatus === "running"
          ? "input-available"
          : config.activityStatus === "failed"
            ? "output-error"
            : "output-available",
      title: config.activityLabel,
      toolName: config.toolName,
    }),
  ])
);

const ACTIVITY_FACTORIES: Record<string, ActivityFactory> = {
  ...WORKFLOW_ACTIVITY_FACTORIES,
  "classifier.started": (event) => ({
    description: "Preparing a structured classifier decision.",
    details: classifierActivityDetails(event),
    state: "input-available",
    title: "Classifier started",
    toolName: "classifier",
  }),
  "classifier.route": (event) => ({
    description: "Classifier route selected.",
    details: classifierActivityDetails(event),
    state: "input-available",
    title: "Classifier route selected",
    toolName: "classifier",
  }),
  "classifier.completed": (event) => ({
    description: "Classifier decision completed.",
    details: classifierActivityDetails(event, { includeDuration: true }),
    state: "output-available",
    title: "Classifier completed",
    toolName: "classifier",
  }),
  "classifier.timeout": (event) => ({
    description: "Classifier timed out; safe fallback was used.",
    details: classifierActivityDetails(event, { includeDuration: true }),
    state: "output-available",
    title: "Classifier timed out",
    toolName: "classifier",
  }),
  "classifier.failed": (event) => ({
    description: "Classifier failed; safe fallback was used.",
    details: classifierActivityDetails(event, { includeDuration: true }),
    state: "output-error",
    title: "Classifier failed",
    toolName: "classifier",
  }),
  "artifact.created": (event, language) => ({
    artifactLinks: artifactLinksFromRunEvent(event),
    description: getUiCopy(language).artifactReadyDescription,
    details: activityDetails({
      Artifact: payloadDisplayValue(event, "display_name") ?? payloadDisplayValue(event, "title"),
      Status: "Ready",
    }),
    output: payloadDisplayValue(event, "display_name") ?? getUiCopy(language).artifactReadyOutput,
    state: "output-available",
    title: getUiCopy(language).reviewArtifactReady,
    toolName: "artifact",
  }),
  "knowledge.candidate.created": (_event, _language) => ({
    description: "Knowledge candidate needs review.",
    state: "output-available",
    title: "Knowledge update proposed",
    toolName: "knowledge",
  }),
  "knowledge.candidate.approved": (_event, _language) => ({
    description: "Proposed knowledge update was approved.",
    state: "output-available",
    title: "Knowledge update approved",
    toolName: "knowledge",
  }),
  "knowledge.candidate.auto_reviewed": (_event, _language) => ({
    description: "Knowledge candidate was checked against promotion gates.",
    state: "output-available",
    title: "Knowledge candidate reviewed",
    toolName: "knowledge",
  }),
  "knowledge.candidate.auto_approved": (_event, _language) => ({
    description: "Proposed knowledge update passed promotion gates.",
    state: "output-available",
    title: "Knowledge update auto-approved",
    toolName: "knowledge",
  }),
  "knowledge.candidate.needs_review": (_event, _language) => ({
    description: "Knowledge candidate needs review.",
    state: "output-available",
    title: "Knowledge candidate needs review",
    toolName: "knowledge",
  }),
  "knowledge.candidate.auto_rejected": (_event, _language) => ({
    description: "Proposed knowledge update did not pass safety gates.",
    state: "output-error",
    title: "Knowledge update rejected",
    toolName: "knowledge",
  }),
  "knowledge.candidate.rejected": (_event, _language) => ({
    description: "Proposed knowledge update was rejected.",
    state: "output-available",
    title: "Knowledge update rejected",
    toolName: "knowledge",
  }),
  "knowledge.learning.completed": (_event, _language) => ({
    description: "Knowledge candidates were extracted for review.",
    state: "output-available",
    title: "Knowledge review ready",
    toolName: "knowledge",
  }),
  "knowledge.learning.failed": (_event, _language) => ({
    description: "Knowledge candidate extraction failed without changing the run result.",
    state: "output-error",
    title: "Knowledge review failed",
    toolName: "knowledge",
  }),
  "chat.auto_chain.started": (_event, _language) => ({
    description: "Preparing a local preview plan from this request.",
    state: "input-available",
    title: "Backtest workflow started",
    toolName: "backtest",
  }),
  "chat.auto_chain.step.completed": (event, _language) => ({
    description: autoChainStepDescription(payloadValue(event, "tool_id")),
    state: "output-available",
    title: "Backtest workflow advanced",
    toolName: "backtest",
  }),
  "chat.auto_chain.waiting_for_backtest": (_event, _language) => ({
    description: "Waiting for the preview evidence to finish.",
    state: "input-available",
    title: "Backtest queued",
    toolName: "backtest",
  }),
  "backtest.data.planning": (_event, _language) => ({
    description: "Checking cached candles and missing ranges.",
    state: "input-available",
    title: "Checking cached candles",
    toolName: "backtest",
  }),
  "backtest.data.cache_reusing": (_event, _language) => ({
    description: "Using cached 1m candles for this preview.",
    state: "input-available",
    title: "Reusing market data",
    toolName: "backtest",
  }),
  "backtest.data.fetching": (_event, _language) => ({
    description: "Downloading missing public OHLCV candles.",
    state: "input-available",
    title: "Fetching missing 1m candles",
    toolName: "backtest",
  }),
  "backtest.data.exporting": (_event, _language) => ({
    description: "Preparing validated candles for the local preview.",
    state: "input-available",
    title: "Preparing preview input",
    toolName: "backtest",
  }),
  "backtest.execution.started": (_event, _language) => ({
    description: "Checking the strategy against local preview data.",
    state: "input-available",
    title: "Running local preview",
    toolName: "backtest",
  }),
  "backtest.indexing.started": (_event, _language) => ({
    description: "Persisting bounded metrics and report indexes.",
    state: "input-available",
    title: "Indexing report",
    toolName: "backtest",
  }),
  "backtest.report.completed": (_event, _language) => ({
    description: "Metrics and review artifacts are ready.",
    state: "output-available",
    title: "Preview report ready",
    toolName: "backtest",
  }),
  "backtest.failed": (event, _language) => ({
    description: payloadDisplayValue(event, "message") ?? "The local preview failed.",
    state: "output-error",
    title: "Backtest failed",
    toolName: "backtest",
  }),
  "backtest.preview.heartbeat": (event, _language) => {
    const status = payloadValue(event, "status");
    const stage = payloadValue(event, "stage");
    return {
      description: payloadDisplayValue(event, "message") ?? "Backtest preview status updated.",
      state:
        status === "completed"
          ? "output-available"
          : status === "failed"
            ? "output-error"
            : "input-available",
      title: userFacingPreviewText(backtestLiveStageLabel({
        message: payloadDisplayValue(event, "message") ?? "",
        stage: isBacktestLiveStage(stage) ? stage : "planning",
      })),
      toolName: "backtest",
    };
  },
  "chat.auto_chain.summary.completed": (_event, _language) => ({
    description: "Metrics were added to the conversation.",
    state: "output-available",
    title: "Backtest summary ready",
    toolName: "backtest",
  }),
  [AUTO_CHAIN_SUMMARY_PENDING_EVENT]: (event, _language) => ({
    description:
      payloadDisplayValue(event, "message") ??
      "The report is ready. Waiting for the summary message to appear.",
    state: "input-available",
    title: "Preparing summary",
    toolName: "backtest",
  }),
  [AUTO_CHAIN_SUMMARY_TIMEOUT_EVENT]: (event, _language) => ({
    description:
      payloadDisplayValue(event, "message") ??
      "The report is available, but the summary message is still being prepared.",
    state: "output-available",
    title: "Summary still pending",
    toolName: "backtest",
  }),
  "chat.auto_chain.failed": (event, _language) => ({
    description: payloadDisplayValue(event, "message") ?? "The workflow stopped before queueing a backtest.",
    state: "output-error",
    title: "Backtest workflow stopped",
    toolName: "backtest",
  }),
  "policy.blocked": (event, language) => ({
    description: getUiCopy(language).policyBlockedDescription,
    errorText: payloadDisplayValue(event, "message") ?? getUiCopy(language).policyBlockedError,
    state: "output-denied",
    title: getUiCopy(language).reviewBoundaryReached,
    toolName: "policy",
  }),
  "model.usage": (_event, language) => ({
    description: getUiCopy(language).modelUsageRecordedDescription,
    state: "output-available",
    title: getUiCopy(language).modelUsageRecordedTitle,
    toolName: "model",
  }),
  "provider.route": (event, language) => {
    const fallbackUsed = payloadBoolean(event, "fallback_used");
    return {
      description: fallbackUsed
        ? getUiCopy(language).managedFallbackRouteDescription
        : getUiCopy(language).managedModelRouteDescription,
      state: fallbackUsed ? "input-available" : "output-available",
      title: fallbackUsed
        ? getUiCopy(language).managedFallbackRouteTitle
        : getUiCopy(language).managedModelRouteTitle,
      toolName: "provider",
    };
  },
  "provider.retrying": (_event, language) => ({
    description: getUiCopy(language).providerRetryingDescription,
    state: "input-available",
    title: getUiCopy(language).providerRetryingTitle,
    toolName: "provider",
  }),
  "provider.waiting": (_event, language) => ({
    description: getUiCopy(language).providerWaitingDescription,
    state: "input-available",
    title: getUiCopy(language).providerWaitingTitle,
    toolName: "provider",
  }),
  "review.completed": (event, language) => ({
    description: getUiCopy(language).reviewNotesWerePrepared,
    output: payloadValue(event, "decision") ?? getUiCopy(language).reviewNotesPrepared,
    state: "output-available",
    title: getUiCopy(language).reviewNotesPrepared,
    toolName: "review",
  }),
  "run.cancelled": (event, language) => ({
    description: getUiCopy(language).runCancelledDescription,
    output: payloadDisplayValue(event, "reason") ?? getUiCopy(language).cancelled,
    state: "output-error",
    title: getUiCopy(language).responseCancelledTitle,
    toolName: "run",
  }),
  "run.failed": (event, language) => ({
    description:
      payloadValue(event, "code") === "pine_validation_failed"
        ? "The generated Pine source did not pass local preview validation."
        : payloadValue(event, "code") === "provider_timeout"
        ? getUiCopy(language).providerTimeoutDescription
        : getUiCopy(language).responseFailedDescription,
    artifactLinks: validationFailureArtifactLinks(event),
    details: activityDetails({ Status: "Failed" }),
    errorText: payloadDisplayValue(event, "message") ?? getUiCopy(language).runFailed,
    state: "output-error",
    title:
      payloadValue(event, "code") === "pine_validation_failed"
        ? "Backtest plan failed"
        : payloadValue(event, "code") === "provider_timeout"
          ? getUiCopy(language).providerTimedOutTitle
          : getUiCopy(language).responseFailedTitle,
    toolName: payloadValue(event, "code") === "pine_validation_failed" ? "backtest" : "run",
  }),
  "tool.completed": toolCompletedActivity,
  "tool.started": (event, language, registry) => ({
    description: payloadDisplayValue(event, "input_summary") ?? getUiCopy(language).preparingToolCall,
    details: toolEventDetails(event, "Running"),
    state: "input-available",
    title: payloadDisplayValue(event, "label") ?? toolActivityLabel(payloadValue(event, "tool_id"), language, "started", registry),
    toolName: payloadValue(event, "tool_id") ?? "tool",
  }),
  "validation.completed": (event, language) => ({
    description: getUiCopy(language).validationCompletedDescription,
    output: payloadValue(event, "status") ?? getUiCopy(language).validationCompletedOutput,
    state: "output-available",
    title: getUiCopy(language).checkingBoundaries,
    toolName: "validation",
  }),
};

export const CHAT_ACTIVITY_COVERED_EVENT_TYPES = new Set([
  ...Object.keys(ACTIVITY_FACTORIES),
  ...IGNORED_ACTIVITY_EVENT_TYPES,
]);

export const KNOWN_CHAT_ACTIVITY_EVENT_TYPES = KNOWN_RUN_EVENT_TYPES;

export function mapRunEventsToChatActivities(
  events: RunEvent[],
  language: LanguagePreference = "en",
  registry?: ActionRegistryLookup
): ChatActivity[] {
  const activities: ChatActivity[] = [];
  const activityIndexes = new Map<string, number>();
  for (const event of events) {
    const activity = activityFromRunEvent(event, language, registry);
    if (!activity) {
      continue;
    }
    const key = activityDedupeKey(event);
    if (key) {
      const existingIndex = activityIndexes.get(key);
      if (existingIndex !== undefined) {
        activities[existingIndex] = mergeChatActivities(activities[existingIndex], activity);
      } else {
        activityIndexes.set(key, activities.length);
        activities.push(activity);
      }
      continue;
    }
    activities.push(activity);
  }
  return activities.slice(-MAX_ACTIVITIES);
}

function activityDedupeKey(event: RunEvent): string | null {
  const toolId = payloadValue(event, "tool_id");
  if ((event.type === "tool.started" || event.type === "tool.completed") && toolId) {
    return `${event.run_id}:tool:${toolId}`;
  }
  if (event.type === "artifact.created") {
    return `${event.run_id}:artifact:created`;
  }
  if (
    event.type === "backtest.preview.heartbeat" ||
    event.type === "backtest.report.completed" ||
    event.type === AUTO_CHAIN_SUMMARY_PENDING_EVENT ||
    event.type === AUTO_CHAIN_SUMMARY_TIMEOUT_EVENT
  ) {
    return `${event.run_id}:${event.type}`;
  }
  if (event.type === "chat.auto_chain.summary.completed") {
    return `${payloadValue(event, "backtest_run_id") ?? payloadValue(event, "source_run_id") ?? event.run_id}:${event.type}`;
  }
  return null;
}

function activityFromRunEvent(
  event: RunEvent,
  language: LanguagePreference,
  registry?: ActionRegistryLookup
): ChatActivity | null {
  const metadataActivity = activityFromEventMetadata(event);
  if (metadataActivity) {
    return { id: event.event_id, ...metadataActivity };
  }
  const factory = ACTIVITY_FACTORIES[event.type];
  if (factory) {
    return { id: event.event_id, ...factory(event, language, registry) };
  }
  return null;
}

function activityFromEventMetadata(event: RunEvent): Omit<ChatActivity, "id"> | null {
  const activityState = payloadValue(event, "activity_state") ?? activityStateFromLifecyclePhase(payloadValue(event, "lifecycle_phase"));
  if (activityState !== "running" && activityState !== "failed" && activityState !== "complete") {
    return null;
  }
  const title = payloadDisplayValue(event, "activity_label") ?? payloadDisplayValue(event, "title") ?? event.type;
  const state =
    activityState === "running"
      ? "input-available"
      : activityState === "failed"
        ? "output-error"
        : "output-available";
  return {
    artifactLinks: artifactLinksFromRunEvent(event),
    description: title,
    details: activityDetails({ Status: activityStatusLabel(activityState) }),
    state,
    title,
    toolName: payloadValue(event, "tool_name") ?? payloadValue(event, "tool_id") ?? "event",
  };
}

function activityStateFromLifecyclePhase(phase: string | null): string | null {
  if (phase === "started") {
    return "running";
  }
  if (phase === "failed") {
    return "failed";
  }
  if (phase === "completed") {
    return "complete";
  }
  return null;
}

function mergeChatActivities(current: ChatActivity, next: ChatActivity): ChatActivity {
  return {
    ...next,
    artifactLinks: mergeArtifactLinks(current.artifactLinks, next.artifactLinks),
    details: mergeActivityDetails(current.details, next.details),
  };
}

function mergeArtifactLinks(
  current: ChatActivityArtifactLink[] | undefined,
  next: ChatActivityArtifactLink[] | undefined
): ChatActivityArtifactLink[] | undefined {
  const links: ChatActivityArtifactLink[] = [];
  const seen = new Set<string>();
  for (const link of [...(current ?? []), ...(next ?? [])]) {
    if (seen.has(link.artifactId)) {
      continue;
    }
    seen.add(link.artifactId);
    links.push(link);
  }
  return links.length > 0 ? links : undefined;
}

function mergeActivityDetails(
  current: ChatActivityDetail[] | undefined,
  next: ChatActivityDetail[] | undefined
): ChatActivityDetail[] | undefined {
  const details = new Map<string, ChatActivityDetail>();
  for (const detail of [...(current ?? []), ...(next ?? [])]) {
    details.set(detail.label, detail);
  }
  return details.size > 0 ? [...details.values()] : undefined;
}

function toolCompletedActivity(
  event: RunEvent,
  language: LanguagePreference,
  registry?: ActionRegistryLookup
): Omit<ChatActivity, "id"> {
  const toolId = payloadValue(event, "tool_id");
  const label = payloadValue(event, "label") ?? toolActivityLabel(toolId, language, "completed", registry);
  const outputSummary = payloadValue(event, "output_summary");
  const userSummary = payloadValue(event, "tool_user_summary");
  if (payloadValue(event, "status") === "failed") {
    const isPineValidationFailure = payloadValue(event, "code") === "pine_validation_failed";
    return {
      artifactLinks: validationFailureArtifactLinks(event) ?? artifactLinksFromRunEvent(event),
      description: isPineValidationFailure
        ? "The generated Pine source did not pass local preview validation."
        : userSummary ?? outputSummary ?? getUiCopy(language).toolFailedBeforeArtifact,
      details: toolEventDetails(event, "Failed"),
      errorText: payloadDisplayValue(event, "message") ?? getUiCopy(language).toolFailed,
      state: "output-error",
      title: isPineValidationFailure ? "Backtest plan failed" : `${label} ${getUiCopy(language).failedSuffix}`,
      toolName: toolId ?? "tool",
    };
  }
  return {
    artifactLinks: artifactLinksFromRunEvent(event),
    description: userSummary ?? (outputSummary && !isTechnicalSummary(outputSummary) ? outputSummary : null) ?? getUiCopy(language).toolOutputReady,
    details: toolEventDetails(event, "Complete"),
    state: "output-available",
    title: label,
    toolName: toolId ?? "tool",
  };
}

function toolActivityLabel(
  toolId: string | null,
  language: LanguagePreference,
  state: "completed" | "started",
  registry?: ActionRegistryLookup
): string {
  const label = actionToolActivityLabel(toolId, language, state, registry);
  if (label) {
    return label;
  }
  return state === "started" ? getUiCopy(language).runningTool : getUiCopy(language).toolCompleted;
}

function payloadValue(event: RunEvent, key: string): string | null {
  const payload = event.payload;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return null;
  }
  const value = (payload as Record<string, unknown>)[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function payloadDisplayValue(event: RunEvent, key: string): string | null {
  const value = payloadValue(event, key);
  return value ? userFacingPreviewText(value) : null;
}

function payloadRecord(event: RunEvent): Record<string, unknown> | null {
  const payload = event.payload;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return null;
  }
  return payload as Record<string, unknown>;
}

function artifactLinksFromRunEvent(event: RunEvent): ChatActivityArtifactLink[] | undefined {
  const payload = payloadRecord(event);
  const output = payload?.output;
  const records = [
    payload,
    output && typeof output === "object" && !Array.isArray(output)
      ? (output as Record<string, unknown>)
      : null,
  ];
  const links = records.flatMap((record) => (record ? artifactLinksFromRecord(record) : []));
  return mergeArtifactLinks(undefined, links);
}

function artifactLinksFromRecord(record: Record<string, unknown>): ChatActivityArtifactLink[] {
  const artifactId = stringFromRecord(record, "artifact_id") ?? stringFromRecord(record, "artifactId");
  if (!artifactId) {
    return [];
  }
  return [
    {
      artifactId,
      label:
        displayStringFromRecord(record, "display_name") ??
        displayStringFromRecord(record, "title") ??
        "Open artifact",
    },
  ];
}

function classifierActivityDetails(
  event: RunEvent,
  options: { includeDuration?: boolean; includeProvider?: boolean } = {}
): ChatActivityDetail[] | undefined {
  const payload = payloadRecord(event);
  const duration =
    options.includeDuration && typeof payload?.duration_ms === "number"
      ? `${Math.round(payload.duration_ms)} ms`
      : null;
  return activityDetails({
    Classifier: payloadDisplayValue(event, "classifier_name"),
    Provider: options.includeProvider ? payloadDisplayValue(event, "provider") : null,
    Stage: payloadDisplayValue(event, "stage"),
    Status: payloadDisplayValue(event, "status"),
    Duration: duration,
  });
}

function toolEventDetails(event: RunEvent, status: string): ChatActivityDetail[] | undefined {
  return activityDetails({
    Status: status,
    Tool: payloadDisplayValue(event, "label"),
  });
}

function activityDetails(values: Record<string, string | null | undefined>): ChatActivityDetail[] | undefined {
  const details = Object.entries(values).flatMap(([label, value]) =>
    value ? [{ label, value }] : []
  );
  return details.length > 0 ? details : undefined;
}

function activityStatusLabel(status: string): string {
  if (status === "running") {
    return "Running";
  }
  if (status === "failed") {
    return "Failed";
  }
  if (status === "skipped") {
    return "Skipped";
  }
  return "Complete";
}

function stringFromRecord(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function displayStringFromRecord(record: Record<string, unknown>, key: string): string | null {
  const value = stringFromRecord(record, key);
  return value ? userFacingPreviewText(value) : null;
}

function payloadBoolean(event: RunEvent, key: string): boolean {
  const payload = event.payload;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return false;
  }
  return (payload as Record<string, unknown>)[key] === true;
}

function validationFailureArtifactLinks(event: RunEvent): ChatActivityArtifactLink[] | undefined {
  if (payloadValue(event, "code") !== "pine_validation_failed") {
    return undefined;
  }
  const links: ChatActivityArtifactLink[] = [];
  const pineArtifactId = payloadValue(event, "pine_code_artifact_id");
  const validationArtifactId = payloadValue(event, "validation_artifact_id");
  if (pineArtifactId) {
    links.push({ artifactId: pineArtifactId, label: "Open Pine code" });
  }
  if (validationArtifactId) {
    links.push({ artifactId: validationArtifactId, label: "Open validation report" });
  }
  return links.length > 0 ? links : undefined;
}

function autoChainStepDescription(toolId: string | null): string {
  if (toolId === "generate_pine") {
    return "Strategy source is ready.";
  }
  if (toolId === "create_backtest_plan") {
    return "Backtest plan is validated.";
  }
  if (toolId === "run_backtest_preview") {
    return "Local preview evidence is queued.";
  }
  return "Workflow step completed.";
}

function isTechnicalSummary(value: string): boolean {
  const trimmed = value.trim();
  return trimmed.startsWith("{") || trimmed.startsWith("[");
}
