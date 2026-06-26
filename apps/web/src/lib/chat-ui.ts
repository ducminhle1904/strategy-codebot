import type { ChatActivity } from "@/lib/chat-activity";
import type {
  Artifact,
  ConversationSidebarItem,
  Message as BackendMessage,
  RunEvent,
} from "@/lib/backend-schemas";
import {
  getChatSuggestions,
  getUiCopy,
  type ChatSuggestion,
  type LanguagePreference,
} from "@/lib/i18n";
import type {
  ChatSuggestionItem,
  ChatSuggestionsPayload,
  MarketSnapshot,
  PythonSseEvent,
  ResponseIntent,
  SafeReasoningSummary,
} from "@/lib/chat-stream";
import {
  marketSnapshotFromPythonEvent,
  normalizeSuggestionsPayload,
  responseIntentFromPythonEvent,
  suggestionsFromPythonEvent,
} from "@/lib/chat-stream";
import {
  parseBacktestArtifactPreview,
  type BacktestArtifactCardModel,
} from "@/lib/backtest-report";
import {
  backtestTradesColumns,
  backtestTradesTableFromToolOutput,
  type ChatInlineTable,
  type ChatInlineTableColumn,
} from "@/lib/backtest-trades-inline-table";
import type { Message as AgUiMessage } from "@ag-ui/core";

export type { ChatSuggestion };
export type { ChatInlineTable, ChatInlineTableColumn };

export const CHAT_SUGGESTIONS: ChatSuggestion[] = getChatSuggestions("en");

export type ChatMessageSource = {
  id: string;
  title: string;
  type: "external" | "internal";
  url?: string;
};

export type { ChatSuggestionItem, ChatSuggestionsPayload, MarketSnapshot, ResponseIntent };

export type StrategyChatReasoning = {
  id: string;
  text: string;
  state?: "done" | "streaming";
};

export type StrategyChatMessage = {
  id: string;
  role: "assistant" | "user";
  text: string;
  sources: ChatMessageSource[];
  reasoningSummaries: StrategyChatReasoning[];
  backtestReport: BacktestArtifactCardModel | null;
  inlineTables: ChatInlineTable[];
  marketSnapshot: MarketSnapshot | null;
  suggestions: ChatSuggestionsPayload | null;
  responseIntent: ResponseIntent | null;
  raw: AgUiMessage | null;
};

export type StrategyChatMessageMetadata = Pick<
  StrategyChatMessage,
  | "marketSnapshot"
  | "backtestReport"
  | "inlineTables"
  | "reasoningSummaries"
  | "responseIntent"
  | "sources"
  | "suggestions"
>;

export function backendMessagesToStrategyMessages(messages: BackendMessage[]): StrategyChatMessage[] {
  return messages.flatMap((message) => {
    if (message.role !== "user" && message.role !== "assistant") {
      return [];
    }
    return [
      {
        id: message.id,
        backtestReport: null,
        inlineTables: [],
        marketSnapshot: null,
        raw: null,
        reasoningSummaries: [],
        responseIntent: null,
        role: message.role,
        sources: [],
        suggestions: null,
        text: message.content,
      } satisfies StrategyChatMessage,
    ];
  });
}

export function copilotAgentMessageToStrategyMessage(
  message: AgUiMessage,
  metadata?: StrategyChatMessageMetadata
): StrategyChatMessage | null {
  if (message.role !== "user" && message.role !== "assistant") {
    return null;
  }
  return {
    id: message.id,
    backtestReport: metadata?.backtestReport ?? null,
    inlineTables: metadata?.inlineTables ?? [],
    marketSnapshot: metadata?.marketSnapshot ?? null,
    raw: message,
    reasoningSummaries: metadata?.reasoningSummaries ?? [],
    responseIntent: metadata?.responseIntent ?? null,
    role: message.role,
    sources: metadata?.sources ?? [],
    suggestions: metadata?.suggestions ?? null,
    text: agUiMessageText(message),
  };
}

export function copilotAgentMessagesToStrategyMessages(
  messages: AgUiMessage[],
  metadataByMessageId?: ReadonlyMap<string, StrategyChatMessageMetadata>
): StrategyChatMessage[] {
  return messages.flatMap((message) => {
    const normalized = copilotAgentMessageToStrategyMessage(
      message,
      metadataByMessageId?.get(message.id)
    );
    return normalized ? [normalized] : [];
  });
}

export function emptyStrategyChatMessageMetadata(): StrategyChatMessageMetadata {
  return {
    backtestReport: null,
    inlineTables: [],
    marketSnapshot: null,
    reasoningSummaries: [],
    responseIntent: null,
    sources: [],
    suggestions: null,
  };
}

export function latestAssistantAfterLastUser(
  messages: StrategyChatMessage[]
): StrategyChatMessage | null {
  let lastUserIndex = -1;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index]?.role === "user") {
      lastUserIndex = index;
      break;
    }
  }
  for (let index = messages.length - 1; index > lastUserIndex; index -= 1) {
    const message = messages[index];
    if (message?.role === "assistant") {
      return message;
    }
  }
  return null;
}

export function runEventMetadataByAnchorMessage({
  backendMessages,
  events,
}: {
  backendMessages: BackendMessage[];
  events: RunEvent[];
}) {
  const grouped = new Map<string, StrategyChatMessageMetadata>();
  for (const event of events) {
    const anchorId = runEventAnchorMessageId({ event, backendMessages });
    if (!anchorId) {
      continue;
    }
    const patch = metadataPatchFromRunEvent(event);
    if (!patch) {
      continue;
    }
    grouped.set(
      anchorId,
      mergeStrategyChatMessageMetadata(grouped.get(anchorId), patch)
    );
  }
  return grouped;
}

function runEventAnchorMessageId({
  backendMessages,
  event,
}: {
  backendMessages: BackendMessage[];
  event: RunEvent;
}) {
  const eventTime = Date.parse(event.created_at);
  if (!Number.isFinite(eventTime)) {
    return null;
  }
  const sortedMessages = [...backendMessages].sort(
    (left, right) => Date.parse(left.created_at) - Date.parse(right.created_at)
  );
  for (const message of sortedMessages) {
    const messageTime = Date.parse(message.created_at);
    if (!Number.isFinite(messageTime) || messageTime < eventTime) {
      continue;
    }
    return message.role === "assistant" ? message.id : null;
  }
  return null;
}

function metadataPatchFromRunEvent(
  event: RunEvent
): Partial<StrategyChatMessageMetadata> | null {
  const pythonEvent = {
    data: { payload: event.payload },
    event: event.type,
    id: event.event_id,
  } satisfies PythonSseEvent;
  const marketSnapshot = marketSnapshotFromPythonEvent(pythonEvent);
  if (marketSnapshot) {
    return { marketSnapshot };
  }
  const backtestReport = backtestReportFromPythonEvent(pythonEvent);
  if (backtestReport) {
    return { backtestReport };
  }
  const inlineTable = inlineTableFromPythonEvent(pythonEvent);
  if (inlineTable) {
    return { inlineTables: [inlineTable] };
  }
  const responseIntent = responseIntentFromPythonEvent(pythonEvent);
  if (responseIntent) {
    return { responseIntent };
  }
  const suggestions = suggestionsFromPythonEvent(pythonEvent);
  if (suggestions) {
    return { suggestions };
  }
  return null;
}

function mergeInlineTables(
  current: ChatInlineTable[],
  next: ChatInlineTable[]
): ChatInlineTable[] {
  const merged = new Map<string, ChatInlineTable>();
  for (const table of current) {
    merged.set(inlineTableDedupeKey(table), table);
  }
  for (const table of next) {
    merged.set(inlineTableDedupeKey(table), table);
  }
  return [...merged.values()];
}

function inlineTableDedupeKey(table: ChatInlineTable) {
  return [
    table.kind,
    table.source_tool_id,
    table.run_id ?? "",
    table.row_count ?? table.rows.length,
  ].join(":");
}

export function mergeStrategyChatMessageMetadata(
  current: StrategyChatMessageMetadata | undefined,
  next: Partial<StrategyChatMessageMetadata>
): StrategyChatMessageMetadata {
  const base = current ?? emptyStrategyChatMessageMetadata();
  return {
    backtestReport:
      next.backtestReport !== undefined ? next.backtestReport : base.backtestReport,
    inlineTables:
      next.inlineTables !== undefined
        ? mergeInlineTables(base.inlineTables, next.inlineTables)
        : base.inlineTables,
    marketSnapshot:
      next.marketSnapshot !== undefined ? next.marketSnapshot : base.marketSnapshot,
    reasoningSummaries:
      next.reasoningSummaries !== undefined
        ? mergeReasoningSummaries(base.reasoningSummaries, next.reasoningSummaries)
        : base.reasoningSummaries,
    responseIntent:
      next.responseIntent !== undefined ? next.responseIntent : base.responseIntent,
    sources:
      next.sources !== undefined ? mergeSources(base.sources, next.sources) : base.sources,
    suggestions: next.suggestions !== undefined ? next.suggestions : base.suggestions,
  };
}

export function metadataPatchFromAgUiCustomEvent(event: {
  name?: unknown;
  value?: unknown;
}): Partial<StrategyChatMessageMetadata> | null {
  const name = typeof event.name === "string" ? event.name : "";
  const value = event.value;
  if (name === "strategy.responseIntent") {
    const intent = responseIntentFromCustomValue(value);
    return intent ? { responseIntent: intent } : null;
  }
  if (name === "strategy.marketSnapshot" && value && typeof value === "object") {
    return { marketSnapshot: value as MarketSnapshot };
  }
  if (name === "strategy.backtestReport") {
    const backtestReport = backtestReportFromCustomValue(value);
    return backtestReport ? { backtestReport } : null;
  }
  if (name === "strategy.inlineTable") {
    const inlineTable = inlineTableFromCustomValue(value);
    return inlineTable ? { inlineTables: [inlineTable] } : null;
  }
  if (name === "strategy.suggestions") {
    const suggestions = normalizeSuggestionsPayload(value);
    return suggestions ? { suggestions } : null;
  }
  if (name === "strategy.sources" && value && typeof value === "object") {
    const sources = (value as Record<string, unknown>).sources;
    return Array.isArray(sources) ? { sources: normalizeChatSources(sources) } : null;
  }
  if (name === "strategy.reasoningSummary" && value && typeof value === "object") {
    const reasoning = reasoningSummaryFromCustomValue(value);
    return reasoning ? { reasoningSummaries: [reasoning] } : null;
  }
  return null;
}

export function metadataPatchFromAgUiReasoningEvent(event: {
  delta?: unknown;
  messageId?: unknown;
}): Partial<StrategyChatMessageMetadata> | null {
  if (typeof event.delta !== "string" || !event.delta.trim()) {
    return null;
  }
  return {
    reasoningSummaries: [
      {
        id:
          typeof event.messageId === "string"
            ? event.messageId
            : `reasoning-${crypto.randomUUID()}`,
        state: "streaming",
        text: event.delta.replace(/^-\s*/, "").trim(),
      },
    ],
  };
}

export function groupArtifactsByAnchorMessage({
  artifacts,
  backendMessages,
}: {
  artifacts: Artifact[];
  backendMessages: BackendMessage[];
}) {
  const grouped = new Map<string, Artifact[]>();
  for (const artifact of artifacts) {
    const anchorId = artifactAnchorMessageId({ artifact, backendMessages });
    if (!anchorId) {
      continue;
    }
    grouped.set(anchorId, [...(grouped.get(anchorId) ?? []), artifact]);
  }
  return grouped;
}

export function artifactAnchorMessageId({
  artifact,
  backendMessages,
}: {
  artifact: Artifact;
  backendMessages: BackendMessage[];
}) {
  const artifactTime = Date.parse(artifact.created_at);
  if (!Number.isFinite(artifactTime)) {
    return null;
  }
  let closestAssistant: { id: string; distance: number } | null = null;
  for (const message of backendMessages) {
    if (message.role !== "assistant") {
      continue;
    }
    const messageTime = Date.parse(message.created_at);
    if (!Number.isFinite(messageTime)) {
      continue;
    }
    const distance = Math.abs(messageTime - artifactTime);
    if (!closestAssistant || distance <= closestAssistant.distance) {
      closestAssistant = { id: message.id, distance };
    }
  }
  return closestAssistant?.id ?? null;
}

export function getMessageText(message: StrategyChatMessage): string {
  return message.text;
}

export function shouldShowStrategyProfile(intent: ResponseIntent | null): boolean {
  return intent === "strategy_building" || intent === "artifact_generation";
}

export function isRenderableMessage(message: StrategyChatMessage): boolean {
  return (
    message.role !== "assistant" ||
    message.text.trim().length > 0 ||
    message.reasoningSummaries.length > 0 ||
    message.sources.length > 0 ||
    Boolean(message.backtestReport) ||
    Boolean(message.marketSnapshot) ||
    Boolean(message.suggestions)
  );
}

export function hasAssistantText(messages: StrategyChatMessage[]): boolean {
  return messages.some(
    (message) => message.role === "assistant" && message.text.trim().length > 0
  );
}

export function compactActivityTitle(
  activities: ChatActivity[],
  language: LanguagePreference = "en"
): string {
  const running = activities.find(
    (activity) => activity.state === "input-available"
  );
  const latest = running ?? activities.at(-1);
  return latest?.title ?? getUiCopy(language).workingThroughRequest;
}

export { getChatSuggestions };

export function isEmptyConversation(item: ConversationSidebarItem | null | undefined): boolean {
  return Boolean(item && item.message_count === 0 && !item.latest_run_id);
}

function sourceText(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const text = value.trim();
  return text || undefined;
}

function responseIntentFromCustomValue(value: unknown): ResponseIntent | null {
  if (isResponseIntent(value)) {
    return value;
  }
  if (!value || typeof value !== "object") {
    return null;
  }
  const intent = (value as Record<string, unknown>).intent;
  return isResponseIntent(intent) ? intent : null;
}

function reasoningSummaryFromCustomValue(
  value: unknown
): StrategyChatReasoning | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const text = sourceText((value as SafeReasoningSummary).text);
  if (!text) {
    return null;
  }
  return {
    id: `reasoning-${stableStringId(text)}`,
    state: "streaming",
    text,
  };
}

function backtestReportFromPythonEvent(
  event: PythonSseEvent
): BacktestArtifactCardModel | null {
  if (event.event !== "tool.completed") {
    return null;
  }
  const payload =
    event.data && typeof event.data === "object"
      ? (event.data as Record<string, unknown>).payload
      : null;
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const record = payload as Record<string, unknown>;
  if (record.tool_id !== "get_backtest_summary") {
    return null;
  }
  const output = record.output;
  if (!output || typeof output !== "object") {
    return null;
  }
  return backtestReportFromCustomValue((output as Record<string, unknown>).summary);
}

function backtestReportFromCustomValue(value: unknown): BacktestArtifactCardModel | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const record = value as Record<string, unknown>;
  const report = record.report ?? record.summary ?? value;
  return parseBacktestArtifactPreview("backtest_report", report);
}

function inlineTableFromPythonEvent(event: PythonSseEvent): ChatInlineTable | null {
  if (event.event !== "tool.completed") {
    return null;
  }
  const payload =
    event.data && typeof event.data === "object"
      ? (event.data as Record<string, unknown>).payload
      : null;
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const record = payload as Record<string, unknown>;
  if (record.tool_id !== "query_backtest_trades") {
    return null;
  }
  return backtestTradesTableFromToolOutput(record.output);
}

function inlineTableFromCustomValue(value: unknown): ChatInlineTable | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const record = value as Record<string, unknown>;
  if (record.kind !== "backtest_trades") {
    return null;
  }
  const rows = Array.isArray(record.rows)
    ? record.rows.filter((row): row is Record<string, unknown> => Boolean(row && typeof row === "object"))
    : [];
  if (rows.length === 0) {
    return null;
  }
  const columns = Array.isArray(record.columns)
    ? record.columns
        .map((column) => normalizeInlineTableColumn(column))
        .filter((column): column is ChatInlineTableColumn => column !== null)
    : backtestTradesColumns();
  return {
    kind: "backtest_trades",
    title: stringValue(record.title) ?? "Backtest trades",
    caption: stringValue(record.caption) ?? undefined,
    columns: columns.length > 0 ? columns : backtestTradesColumns(),
    rows,
    source_tool_id: stringValue(record.source_tool_id) ?? "query_backtest_trades",
    run_id: stringValue(record.run_id) ?? undefined,
    row_count: numberValue(record.row_count) ?? rows.length,
    truncated: Boolean(record.truncated),
  };
}

function normalizeInlineTableColumn(value: unknown): ChatInlineTableColumn | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const record = value as Record<string, unknown>;
  const key = stringValue(record.key);
  const label = stringValue(record.label);
  if (!key || !label) {
    return null;
  }
  const align = record.align === "right" ? "right" : "left";
  const tone =
    record.tone === "profit_loss" || record.tone === "side" ? record.tone : "default";
  return { key, label, align, tone };
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function numberValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function normalizeChatSources(value: unknown[]): ChatMessageSource[] {
  const seen = new Set<string>();
  const sources: ChatMessageSource[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object") {
      continue;
    }
    const record = item as Record<string, unknown>;
    const id = sourceText(record.id);
    const title = sourceText(record.title);
    const type =
      record.type === "external"
        ? "external"
        : record.type === "internal"
          ? "internal"
          : null;
    if (!id || !title || !type || seen.has(id)) {
      continue;
    }
    const url = sourceText(record.url);
    if (type === "external" && !url) {
      continue;
    }
    seen.add(id);
    sources.push({
      id,
      title,
      type,
      ...(url ? { url } : {}),
    });
  }
  return sources;
}

function mergeSources(
  current: ChatMessageSource[],
  next: ChatMessageSource[]
): ChatMessageSource[] {
  const merged = [...current];
  const seen = new Set(current.map((source) => source.id));
  for (const source of next) {
    if (seen.has(source.id)) {
      continue;
    }
    seen.add(source.id);
    merged.push(source);
  }
  return merged;
}

function mergeReasoningSummaries(
  current: StrategyChatReasoning[],
  next: StrategyChatReasoning[]
): StrategyChatReasoning[] {
  const merged = [...current];
  for (const reasoning of next) {
    const existingIndex = merged.findIndex((item) => item.id === reasoning.id);
    if (existingIndex >= 0) {
      const existing = merged[existingIndex];
      if (
        existing?.state === reasoning.state &&
        existing.text === reasoning.text
      ) {
        continue;
      }
      merged[existingIndex] = reasoning;
      continue;
    }
    merged.push(reasoning);
  }
  return merged;
}

function stableStringId(value: string): string {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) >>> 0;
  }
  return hash.toString(16);
}

function agUiMessageText(message: AgUiMessage): string {
  if (!("content" in message)) {
    return "";
  }
  const content = message.content;
  if (typeof content === "string") {
    return content;
  }
  if (!Array.isArray(content)) {
    return "";
  }
  return content
    .map((part) => {
      if (part && typeof part === "object" && "type" in part && part.type === "text") {
        return "text" in part && typeof part.text === "string" ? part.text : "";
      }
      return "";
    })
    .join("");
}

function isResponseIntent(value: unknown): value is ResponseIntent {
  return (
    value === "artifact_generation" ||
    value === "capability_help" ||
    value === "docs_research" ||
    value === "general_chat" ||
    value === "market_research" ||
    value === "market_snapshot" ||
    value === "strategy_building"
  );
}
