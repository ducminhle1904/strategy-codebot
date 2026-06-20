import {
  KNOWN_RUN_EVENT_TYPES,
  type KnownRunEventType,
  type RunEvent,
} from "@/lib/backend-schemas";
import type { ToolPart } from "@/components/ai-elements/tool";
import { getUiCopy, type LanguagePreference } from "@/lib/i18n";

export type ChatActivity = {
  id: string;
  title: string;
  description: string;
  state: ToolPart["state"];
  toolName: string;
  output?: string;
  errorText?: string;
};

const MAX_ACTIVITIES = 5;
const IGNORED_ACTIVITY_EVENT_TYPES = new Set<KnownRunEventType>([
  "backtest.data.completed",
  "backtest.data.started",
  "backtest.execution.completed",
  "backtest.execution.started",
  "backtest.failed",
  "backtest.queued",
  "backtest.report.completed",
  "message.delta",
  "model.reasoning.delta",
  "model.usage",
  "observability.stage.completed",
  "provider.started",
  "progress.snapshot",
  "progress.update",
  "run.completed",
  "stage.completed",
  "stage.started",
]);

type ActivityFactory = (event: RunEvent, language: LanguagePreference) => Omit<ChatActivity, "id">;

const ACTIVITY_FACTORIES: Record<string, ActivityFactory> = {
  "artifact.created": (event, language) => ({
    description: getUiCopy(language).artifactReadyDescription,
    output: payloadValue(event, "display_name") ?? getUiCopy(language).artifactReadyOutput,
    state: "output-available",
    title: getUiCopy(language).reviewArtifactReady,
    toolName: "artifact",
  }),
  "policy.blocked": (event, language) => ({
    description: getUiCopy(language).policyBlockedDescription,
    errorText: payloadValue(event, "message") ?? getUiCopy(language).policyBlockedError,
    state: "output-denied",
    title: getUiCopy(language).reviewBoundaryReached,
    toolName: "policy",
  }),
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
    output: payloadValue(event, "reason") ?? getUiCopy(language).cancelled,
    state: "output-error",
    title: getUiCopy(language).responseCancelledTitle,
    toolName: "run",
  }),
  "run.failed": (event, language) => ({
    description:
      payloadValue(event, "code") === "provider_timeout"
        ? getUiCopy(language).providerTimeoutDescription
        : getUiCopy(language).responseFailedDescription,
    errorText: payloadValue(event, "message") ?? getUiCopy(language).runFailed,
    state: "output-error",
    title: payloadValue(event, "code") === "provider_timeout" ? getUiCopy(language).providerTimedOutTitle : getUiCopy(language).responseFailedTitle,
    toolName: "run",
  }),
  "tool.completed": toolCompletedActivity,
  "tool.started": (event, language) => ({
    description: payloadValue(event, "input_summary") ?? getUiCopy(language).preparingToolCall,
    state: "input-available",
    title: payloadValue(event, "label") ?? getUiCopy(language).runningTool,
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
  language: LanguagePreference = "en"
): ChatActivity[] {
  const activities: ChatActivity[] = [];
  const toolIndexes = new Map<string, number>();
  for (const event of events) {
    const activity = activityFromRunEvent(event, language);
    if (!activity) {
      continue;
    }
    const toolId = payloadValue(event, "tool_id");
    if ((event.type === "tool.started" || event.type === "tool.completed") && toolId) {
      const key = `${event.run_id}:${toolId}`;
      const existingIndex = toolIndexes.get(key);
      if (existingIndex !== undefined) {
        activities[existingIndex] = activity;
      } else {
        toolIndexes.set(key, activities.length);
        activities.push(activity);
      }
      continue;
    }
    activities.push(activity);
  }
  return activities.slice(-MAX_ACTIVITIES);
}

function activityFromRunEvent(event: RunEvent, language: LanguagePreference): ChatActivity | null {
  const factory = ACTIVITY_FACTORIES[event.type];
  return factory ? { id: event.event_id, ...factory(event, language) } : null;
}

function toolCompletedActivity(event: RunEvent, language: LanguagePreference): Omit<ChatActivity, "id"> {
  const label = payloadValue(event, "label") ?? getUiCopy(language).toolCompleted;
  const outputSummary = payloadValue(event, "output_summary");
  const userSummary = payloadValue(event, "tool_user_summary");
  if (payloadValue(event, "status") === "failed") {
    return {
      description: userSummary ?? outputSummary ?? getUiCopy(language).toolFailedBeforeArtifact,
      errorText: payloadValue(event, "message") ?? getUiCopy(language).toolFailed,
      state: "output-error",
      title: `${label} ${getUiCopy(language).failedSuffix}`,
      toolName: payloadValue(event, "tool_id") ?? "tool",
    };
  }
  return {
    description: userSummary ?? (outputSummary && !isTechnicalSummary(outputSummary) ? outputSummary : null) ?? getUiCopy(language).toolOutputReady,
    state: "output-available",
    title: label,
    toolName: payloadValue(event, "tool_id") ?? "tool",
  };
}

function payloadValue(event: RunEvent, key: string): string | null {
  const payload = event.payload;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return null;
  }
  const value = (payload as Record<string, unknown>)[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function isTechnicalSummary(value: string): boolean {
  const trimmed = value.trim();
  return trimmed.startsWith("{") || trimmed.startsWith("[");
}
