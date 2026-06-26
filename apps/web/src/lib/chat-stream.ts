import type { BackendApiError } from "@/lib/backend-client";
import type { MessageMode, WebSearchMode } from "@/lib/backend-schemas";
import { normalizeLanguage, type LanguagePreference } from "@/lib/i18n";
import { parseSseMessages } from "@/lib/sse";

export type PythonSseEvent = {
  id?: string;
  event: string;
  data: Record<string, unknown>;
};

export type ChatSource = {
  id: string;
  label?: string;
  title: string;
  type: "external" | "internal";
  url?: string;
};

export type ResponseIntent =
  | "artifact_generation"
  | "capability_help"
  | "docs_research"
  | "general_chat"
  | "market_research"
  | "market_snapshot"
  | "strategy_building";

export type MarketSnapshot = {
  approximate: boolean;
  change?: number | null;
  change_percent?: number | null;
  currency?: string | null;
  freshness: "source_backed";
  generated_at?: string | null;
  label: string;
  price?: string | null;
  price_points: Array<{ label: string; value: number }>;
  provider?: string | null;
  source_count: number;
  sources: ChatSource[];
  symbol: string;
};

export type SafeReasoningSummary = {
  text: string;
};

export type ChatSuggestionKind = "artifact_action" | "chat_action" | "composer_block";

export type ChatSuggestionAction =
  | "insert_or_update_block"
  | "open_artifact"
  | "open_create_spec"
  | "send_prompt";

export type ChatSuggestionCategory =
  | "code"
  | "entry"
  | "exit"
  | "market"
  | "review"
  | "risk"
  | "strategy";

export type ChatSuggestionRiskLevel = "blocked" | "read_only" | "review_required";

export type ChatSuggestionVariant = {
  id: string;
  insert_template: string;
  label: string;
};

export type ChatSuggestionItem = {
  action: ChatSuggestionAction;
  artifact_kind?: string;
  category: ChatSuggestionCategory;
  disabled_reason?: string;
  emphasized?: boolean;
  enabled: boolean;
  id: string;
  insert_template?: string;
  kind: ChatSuggestionKind;
  label: string;
  next_state?: string;
  presentation?: {
    badge_key?: string;
    icon_key?: string;
    visibility_key?: string;
  };
  priority: number;
  prompt?: string;
  reason?: string;
  required_inputs?: string[];
  risk_level?: ChatSuggestionRiskLevel;
  slot?: "entry" | "exit" | "market" | "risk";
  tool_id?: string;
  variants?: ChatSuggestionVariant[];
};

export type ChatSuggestionsPayload = {
  actions: ChatSuggestionItem[];
  composer_blocks: ChatSuggestionItem[];
  context?: {
    artifact_available?: boolean;
    artifact_kinds?: string[];
    intent?: ResponseIntent;
    missing_fields?: string[];
    readiness?: string;
    semantic_action_confidence?: number;
    semantic_action_intent?: string;
    semantic_action_source?: string;
    semantic_suggested_actions?: string[];
  };
  version: 1;
};

export type ChatRequestMetadata = {
  conversationId: string;
  language?: LanguagePreference;
  mode?: MessageMode;
  webSearch?: WebSearchMode;
  userId?: string;
  workspaceId?: string;
};

export type ChatRequestBody = {
  messages: Array<{
    role: string;
    parts?: Array<{ type: string; text?: string }>;
    content?: string;
  }>;
  clientRequestId?: string;
  conversationId?: string;
  language?: LanguagePreference;
  mode?: MessageMode;
  webSearch?: WebSearchMode;
  userId?: string;
  workspaceId?: string;
};

export const extractLastUserText = (messages: ChatRequestBody["messages"]) => {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const last = messages[index];
    if (last.role !== "user") {
      continue;
    }
    if (typeof last.content === "string") {
      return last.content;
    }
    return (
      last.parts
        ?.filter((part) => part.type === "text")
        .map((part) => part.text ?? "")
        .join("") ?? ""
    );
  }
  return "";
};

export const parseSseFrames = (chunk: string): PythonSseEvent[] => {
  return parseSseMessages(chunk).map((message) => ({
    data: message.data && typeof message.data === "object" ? (message.data as Record<string, unknown>) : { raw: message.data },
    event: message.event,
    id: message.id,
  }));
};

export function knowledgeSourcesFromPythonEvent(event: PythonSseEvent): ChatSource[] {
  if (event.event !== "tool.completed") {
    return [];
  }
  const payload = event.data.payload;
  if (!payload || typeof payload !== "object") {
    return [];
  }
  const record = payload as Record<string, unknown>;
  if (record.tool_id !== "knowledge_check") {
    return [];
  }
  const output = record.output;
  if (!output || typeof output !== "object") {
    return [];
  }
  const summary = (output as Record<string, unknown>).knowledge_context_summary;
  if (!summary || typeof summary !== "object") {
    return [];
  }
  const sources = (summary as Record<string, unknown>).sources;
  if (!Array.isArray(sources)) {
    return [];
  }
  const seen = new Set<string>();
  const normalized: ChatSource[] = [];
  for (const source of sources) {
    if (!source || typeof source !== "object") {
      continue;
    }
    const sourceRecord = source as Record<string, unknown>;
    const id = sourceText(sourceRecord.id);
    const title = sourceText(sourceRecord.title);
    const type = sourceRecord.type === "external" ? "external" : sourceRecord.type === "internal" ? "internal" : null;
    if (!id || !title || !type || seen.has(id)) {
      continue;
    }
    const url = sourceText(sourceRecord.url);
    if (type === "external" && !url) {
      continue;
    }
    seen.add(id);
    normalized.push({
      id,
      label: sourceText(sourceRecord.label),
      title,
      type,
      ...(url ? { url } : {}),
    });
  }
  return normalized;
}

export function webSourcesFromPythonEvent(event: PythonSseEvent): ChatSource[] {
  if (event.event !== "web.sources") {
    return [];
  }
  const payload = event.data.payload;
  if (!payload || typeof payload !== "object") {
    return [];
  }
  return normalizeSources((payload as Record<string, unknown>).sources);
}

export function responseIntentFromPythonEvent(event: PythonSseEvent): ResponseIntent | null {
  if (event.event !== "chat.response_intent") {
    return null;
  }
  const payload = event.data.payload;
  if (!payload || typeof payload !== "object") {
    return null;
  }
  return normalizeResponseIntent((payload as Record<string, unknown>).intent);
}

export function marketSnapshotFromPythonEvent(event: PythonSseEvent): MarketSnapshot | null {
  if (event.event !== "chat.market_snapshot") {
    return null;
  }
  return marketSnapshotFromPayload(event.data.payload);
}

export function marketSnapshotFromPayload(value: unknown): MarketSnapshot | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const symbol = sourceText(record.symbol);
  const label = sourceText(record.label);
  const sources = normalizeSources(record.sources);
  if (!symbol || !label || sources.length === 0) {
    return null;
  }
  return {
    approximate: record.approximate === true,
    change: sourceNumber(record.change),
    change_percent: sourceNumber(record.change_percent),
    currency: sourceText(record.currency) ?? null,
    freshness: "source_backed",
    generated_at: sourceText(record.generated_at) ?? null,
    label,
    price: sourceText(record.price) ?? null,
    price_points: normalizePricePoints(record.price_points),
    provider: sourceText(record.provider) ?? null,
    source_count: typeof record.source_count === "number" ? record.source_count : sources.length,
    sources,
    symbol,
  };
}

export function suggestionsFromPythonEvent(event: PythonSseEvent): ChatSuggestionsPayload | null {
  if (event.event !== "chat.suggestions.updated") {
    return null;
  }
  return normalizeSuggestionsPayload(event.data.payload);
}

export function normalizeSuggestionsPayload(value: unknown): ChatSuggestionsPayload | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const record = value as Record<string, unknown>;
  const actions = normalizeSuggestions(record.actions);
  const composerBlocks = normalizeSuggestions(record.composer_blocks).filter(
    (suggestion) => suggestion.kind === "composer_block" && Boolean(suggestion.slot)
  );
  return {
    actions,
    composer_blocks: composerBlocks,
    context: normalizeSuggestionContext(record.context),
    version: 1,
  };
}

export function reasoningSummaryFromPythonEvent(
  event: PythonSseEvent
): SafeReasoningSummary | null {
  if (event.event !== "model.reasoning.delta") {
    return null;
  }
  const payload = event.data.payload;
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const record = payload as Record<string, unknown>;
  if (record.safe !== true) {
    return null;
  }
  const text = sourceText(record.text);
  if (!text) {
    return null;
  }
  return {
    text,
  };
}

export const textFromPythonEvent = (
  event: PythonSseEvent,
  language: LanguagePreference = "en"
): string => {
  const payload = event.data.payload;
  if (event.event === "message.delta") {
    if (typeof payload === "string") {
      return payload;
    }
    if (payload && typeof payload === "object") {
      const record = payload as Record<string, unknown>;
      for (const key of ["delta", "content", "text"]) {
        if (typeof record[key] === "string") {
          return record[key] as string;
        }
      }
    }
  }

  if (event.event === "run.failed") {
    const payload = event.data.payload;
    if (
      payload &&
      typeof payload === "object" &&
      (payload as Record<string, unknown>).assistant_message_persisted === true
    ) {
      return "";
    }
    if (
      payload &&
      typeof payload === "object" &&
      ((payload as Record<string, unknown>).dimension === "workflow" ||
        (payload as Record<string, unknown>).code === "pine_validation_failed")
    ) {
      return runFailureMessage(event, language);
    }
    return `\n\n${runFailureMessage(event, language)}`;
  }

  if (event.event === "run.completed") {
    return "";
  }

  return "";
};

function sourceText(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const text = value.trim();
  return text || undefined;
}

function sourceNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function normalizeResponseIntent(value: unknown): ResponseIntent | null {
  const intents = new Set<ResponseIntent>([
    "artifact_generation",
    "capability_help",
    "docs_research",
    "general_chat",
    "market_research",
    "market_snapshot",
    "strategy_building",
  ]);
  return typeof value === "string" && intents.has(value as ResponseIntent)
    ? (value as ResponseIntent)
    : null;
}

function normalizeSuggestionContext(value: unknown): ChatSuggestionsPayload["context"] {
  if (!value || typeof value !== "object") {
    return {};
  }
  const record = value as Record<string, unknown>;
  const missingFields = Array.isArray(record.missing_fields)
    ? record.missing_fields.filter((field): field is string => typeof field === "string").slice(0, 4)
    : [];
  return {
    artifact_available: record.artifact_available === true,
    artifact_kinds: normalizeStringList(record.artifact_kinds),
    intent: normalizeResponseIntent(record.intent) ?? undefined,
    missing_fields: missingFields,
    readiness: sourceText(record.readiness),
    semantic_action_confidence: sourceNumber(record.semantic_action_confidence) ?? undefined,
    semantic_action_intent: sourceText(record.semantic_action_intent),
    semantic_action_source: sourceText(record.semantic_action_source),
    semantic_suggested_actions: normalizeStringList(record.semantic_suggested_actions),
  };
}

function normalizeSuggestions(value: unknown): ChatSuggestionItem[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const suggestions: ChatSuggestionItem[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object") {
      continue;
    }
    const record = item as Record<string, unknown>;
    const id = sourceText(record.id);
    const label = sourceText(record.label);
    const kind = normalizeSuggestionKind(record.kind);
    const action = normalizeSuggestionAction(record.action);
    const category = normalizeSuggestionCategory(record.category);
    if (!id || !label || !kind || !action || !category) {
      continue;
    }
    const slot = normalizeSuggestionSlot(record.slot);
    suggestions.push({
      action,
      category,
      disabled_reason: sourceText(record.disabled_reason),
      emphasized: record.emphasized === true,
      enabled: record.enabled !== false,
      id,
      insert_template: sourceText(record.insert_template),
      kind,
      label,
      next_state: sourceText(record.next_state),
      presentation: normalizeSuggestionPresentation(record.presentation),
      priority: typeof record.priority === "number" ? record.priority : 100,
      prompt: sourceText(record.prompt),
      reason: sourceText(record.reason),
      required_inputs: normalizeStringList(record.required_inputs),
      risk_level: normalizeSuggestionRiskLevel(record.risk_level),
      ...(slot ? { slot } : {}),
      artifact_kind: sourceText(record.artifact_kind),
      tool_id: sourceText(record.tool_id),
      variants: normalizeSuggestionVariants(record.variants),
    });
  }
  return suggestions.sort((left, right) => left.priority - right.priority).slice(0, 8);
}

function normalizeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((item) => {
    const text = sourceText(item);
    return text ? [text] : [];
  });
}

function normalizeSuggestionPresentation(value: unknown): ChatSuggestionItem["presentation"] | undefined {
  if (!value || typeof value !== "object") {
    return undefined;
  }
  const record = value as Record<string, unknown>;
  const presentation = {
    badge_key: sourceText(record.badge_key),
    icon_key: sourceText(record.icon_key),
    visibility_key: sourceText(record.visibility_key),
  };
  return presentation.badge_key || presentation.icon_key || presentation.visibility_key
    ? presentation
    : undefined;
}

function normalizeSuggestionVariants(value: unknown): ChatSuggestionVariant[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") {
      return [];
    }
    const record = item as Record<string, unknown>;
    const id = sourceText(record.id);
    const label = sourceText(record.label);
    const insertTemplate = sourceText(record.insert_template);
    if (!id || !label || !insertTemplate) {
      return [];
    }
    return [{ id, insert_template: insertTemplate, label }];
  });
}

function normalizeSuggestionKind(value: unknown): ChatSuggestionKind | null {
  return value === "artifact_action" || value === "chat_action" || value === "composer_block"
    ? value
    : null;
}

function normalizeSuggestionAction(value: unknown): ChatSuggestionAction | null {
  return value === "insert_or_update_block" ||
    value === "open_artifact" ||
    value === "open_create_spec" ||
    value === "send_prompt"
    ? value
    : null;
}

function normalizeSuggestionCategory(value: unknown): ChatSuggestionCategory | null {
  return value === "code" ||
    value === "entry" ||
    value === "exit" ||
    value === "market" ||
    value === "review" ||
    value === "risk" ||
    value === "strategy"
    ? value
    : null;
}

function normalizeSuggestionRiskLevel(value: unknown): ChatSuggestionRiskLevel | undefined {
  return value === "blocked" || value === "read_only" || value === "review_required"
    ? value
    : undefined;
}

function normalizeSuggestionSlot(value: unknown): ChatSuggestionItem["slot"] | null {
  return value === "entry" || value === "exit" || value === "market" || value === "risk"
    ? value
    : null;
}

function normalizeSources(value: unknown): ChatSource[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const seen = new Set<string>();
  const normalized: ChatSource[] = [];
  for (const source of value) {
    if (!source || typeof source !== "object") {
      continue;
    }
    const sourceRecord = source as Record<string, unknown>;
    const id = sourceText(sourceRecord.id);
    const title = sourceText(sourceRecord.title);
    const type = sourceRecord.type === "external" ? "external" : sourceRecord.type === "internal" ? "internal" : null;
    if (!id || !title || !type || seen.has(id)) {
      continue;
    }
    const url = sourceText(sourceRecord.url);
    if (type === "external" && !url) {
      continue;
    }
    seen.add(id);
    normalized.push({
      id,
      label: sourceText(sourceRecord.label),
      title,
      type,
      ...(url ? { url } : {}),
    });
  }
  return normalized;
}

function normalizePricePoints(value: unknown): Array<{ label: string; value: number }> {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((point) => {
    if (!point || typeof point !== "object") {
      return [];
    }
    const record = point as Record<string, unknown>;
    const label = sourceText(record.label);
    const numericValue = record.value;
    if (!label || typeof numericValue !== "number" || !Number.isFinite(numericValue)) {
      return [];
    }
    return [{ label, value: numericValue }];
  });
}

export const runFailureMessage = (
  event: PythonSseEvent,
  language: LanguagePreference = "en"
): string => {
  const isVi = normalizeLanguage(language) === "vi";
  const payload = event.data.payload;
  if (payload && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    if (
      (record.dimension === "workflow" || record.code === "pine_validation_failed") &&
      typeof record.message === "string" &&
      record.message.trim()
    ) {
      return record.message.trim();
    }
    if (record.code === "provider_timeout" || record.error === "ProviderTimeoutError") {
      return isVi
        ? "AI provider phản hồi quá lâu. Bạn có thể thử lại sau khi provider ổn định hơn."
        : "The AI provider took too long to respond. You can try again after the provider stabilizes.";
    }
    if (record.error === "AuthenticationError") {
      return isVi
        ? "AI provider từ chối API key hiện tại. Hãy kiểm tra provider key rồi thử lại."
        : "The AI provider rejected the configured API key. Check the provider key and try again.";
    }
    if (record.error === "RateLimitError") {
      return isVi
        ? "AI provider đang bị rate limit. Hãy thử lại sau khi limit reset."
        : "The AI provider is rate-limited right now. Retry after the provider limit resets.";
    }
    if (typeof record.message === "string" && record.message.trim()) {
      return isVi ? `AI run thất bại: ${record.message}` : `The AI run failed: ${record.message}`;
    }
  }
  return isVi
    ? "AI run thất bại trước khi tạo response. Hãy kiểm tra provider configuration rồi retry."
    : "The AI run failed before it could produce a response. Check provider configuration and retry.";
};

export const isProviderAuthFailure = (event: PythonSseEvent): boolean => {
  const payload = event.data.payload;
  return Boolean(
    event.event === "run.failed" &&
      payload &&
      typeof payload === "object" &&
      (payload as Record<string, unknown>).error === "AuthenticationError"
  );
};

export const isProviderTimeoutFailure = (event: PythonSseEvent): boolean => {
  const payload = event.data.payload;
  return Boolean(
    event.event === "run.failed" &&
      payload &&
      typeof payload === "object" &&
      ((payload as Record<string, unknown>).code === "provider_timeout" ||
        (payload as Record<string, unknown>).error === "ProviderTimeoutError")
  );
};

export const errorMessageFromUnknown = (error: unknown) => {
  if (error instanceof Error) {
    return error.message;
  }
  if (
    error &&
    typeof error === "object" &&
    "status" in error &&
    "message" in error
  ) {
    const apiError = error as BackendApiError;
    return `${apiError.status}: ${apiError.message}`;
  }
  return "The backend request failed.";
};
