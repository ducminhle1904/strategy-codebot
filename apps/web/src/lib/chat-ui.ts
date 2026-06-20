import type { UIMessage } from "ai";

import type { ChatActivity } from "@/lib/chat-activity";
import type {
  ConversationSidebarItem,
  Message as BackendMessage,
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
  ResponseIntent,
} from "@/lib/chat-stream";
import { normalizeSuggestionsPayload } from "@/lib/chat-stream";

export type { ChatSuggestion };

export const CHAT_SUGGESTIONS: ChatSuggestion[] = getChatSuggestions("en");

export type ChatMessageSource = {
  id: string;
  title: string;
  type: "external" | "internal";
  url?: string;
};

export type { ChatSuggestionItem, ChatSuggestionsPayload, MarketSnapshot, ResponseIntent };

export function backendMessagesToUiMessages(messages: BackendMessage[]): UIMessage[] {
  return messages.flatMap((message) => {
    if (message.role !== "user" && message.role !== "assistant") {
      return [];
    }
    return [
      {
        id: message.id,
        parts: [{ text: message.content, type: "text" }],
        role: message.role,
      } satisfies UIMessage,
    ];
  });
}

export function getMessageText(message: UIMessage): string {
  return message.parts
    .map((part) => (part.type === "text" ? part.text ?? "" : ""))
    .join("");
}

export function getMessageSources(message: UIMessage): ChatMessageSource[] {
  const seen = new Set<string>();
  const sources: ChatMessageSource[] = [];
  for (const part of message.parts) {
    if (part.type !== "source-url" && part.type !== "source-document") {
      continue;
    }
    const id = sourceText("sourceId" in part ? part.sourceId : undefined);
    const title = sourceText("title" in part ? part.title : undefined);
    if (!id || !title || seen.has(id)) {
      continue;
    }
    seen.add(id);
    if (part.type === "source-url") {
      const url = sourceText(part.url);
      if (!url) {
        continue;
      }
      sources.push({ id, title, type: "external", url });
      continue;
    }
    sources.push({ id, title, type: "internal" });
  }
  return sources;
}

export function getMessageResponseIntent(message: UIMessage): ResponseIntent | null {
  for (const part of message.parts) {
    if (part.type !== "data-responseIntent") {
      continue;
    }
    const data = "data" in part ? part.data : undefined;
    if (!data || typeof data !== "object") {
      continue;
    }
    const intent = (data as Record<string, unknown>).intent;
    if (isResponseIntent(intent)) {
      return intent;
    }
  }
  return null;
}

export function getMessageMarketSnapshot(message: UIMessage): MarketSnapshot | null {
  let latestSnapshot: MarketSnapshot | null = null;
  for (const part of message.parts) {
    if (part.type !== "data-marketSnapshot") {
      continue;
    }
    const data = "data" in part ? part.data : undefined;
    if (!data || typeof data !== "object") {
      continue;
    }
    const snapshot = data as MarketSnapshot;
    if (snapshot.symbol && snapshot.sources?.length) {
      latestSnapshot = snapshot;
    }
  }
  return latestSnapshot;
}

export function getMessageSuggestions(message: UIMessage): ChatSuggestionsPayload | null {
  for (const part of message.parts) {
    if (part.type !== "data-suggestions") {
      continue;
    }
    const data = "data" in part ? part.data : undefined;
    if (!data || typeof data !== "object") {
      continue;
    }
    const payload = normalizeSuggestionsPayload(data);
    if (payload) {
      return payload;
    }
  }
  return null;
}

export function shouldShowStrategyProfile(intent: ResponseIntent | null): boolean {
  return intent === "strategy_building" || intent === "artifact_generation";
}

export function isRenderableMessage(message: UIMessage): boolean {
  return message.role !== "assistant" || getMessageText(message).trim().length > 0;
}

export function hasAssistantText(messages: UIMessage[]): boolean {
  return messages.some(
    (message) =>
      message.role === "assistant" && getMessageText(message).trim().length > 0
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
