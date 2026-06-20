import { describe, expect, it } from "vitest";
import type { UIMessage } from "ai";

import {
  CHAT_SUGGESTIONS,
  backendMessagesToUiMessages,
  compactActivityTitle,
  getMessageMarketSnapshot,
  getMessageResponseIntent,
  getMessageSuggestions,
  getMessageSources,
  getMessageText,
  hasAssistantText,
  isEmptyConversation,
  isRenderableMessage,
  shouldShowStrategyProfile,
} from "./chat-ui";

function message(role: UIMessage["role"], text: string): UIMessage {
  return {
    id: `${role}-${text || "empty"}`,
    parts: text ? [{ text, type: "text" }] : [],
    role,
  };
}

describe("chat UI helpers", () => {
  it("maps persisted backend chat messages into renderable UI messages", () => {
    const uiMessages = backendMessagesToUiMessages([
      {
        content: "hello",
        conversation_id: "conv_1",
        created_at: "2026-01-01T00:00:00.000Z",
        id: "msg_user",
        owner_user_id: "user_1",
        role: "user",
        workspace_id: "workspace_1",
      },
      {
        content: "hi back",
        conversation_id: "conv_1",
        created_at: "2026-01-01T00:00:01.000Z",
        id: "msg_assistant",
        owner_user_id: "user_1",
        role: "assistant",
        workspace_id: "workspace_1",
      },
      {
        content: "internal context",
        conversation_id: "conv_1",
        created_at: "2026-01-01T00:00:02.000Z",
        id: "msg_system",
        owner_user_id: "user_1",
        role: "system",
        workspace_id: "workspace_1",
      },
    ]);

    expect(uiMessages).toHaveLength(2);
    expect(uiMessages.map((item) => [item.id, item.role, getMessageText(item)])).toEqual([
      ["msg_user", "user", "hello"],
      ["msg_assistant", "assistant", "hi back"],
    ]);
  });

  it("filters empty assistant placeholders while keeping user messages", () => {
    expect(isRenderableMessage(message("assistant", ""))).toBe(false);
    expect(isRenderableMessage(message("assistant", "Ready"))).toBe(true);
    expect(isRenderableMessage(message("user", ""))).toBe(true);
  });

  it("extracts assistant markdown text from text parts", () => {
    const assistant: UIMessage = {
      id: "msg_1",
      parts: [
        { text: "## Title\n", type: "text" },
        { text: "- Item", type: "text" },
      ],
      role: "assistant",
    };

    expect(getMessageText(assistant)).toBe("## Title\n- Item");
    expect(hasAssistantText([message("user", "hello"), assistant])).toBe(true);
  });

  it("extracts source-url and source-document parts for assistant sources", () => {
    const assistant: UIMessage = {
      id: "msg_sources",
      parts: [
        { text: "Answer", type: "text" },
        {
          sourceId: "tradingview-pine-strategies",
          title: "TradingView Pine strategies",
          type: "source-url",
          url: "https://www.tradingview.com/pine-script-docs/",
        },
        {
          mediaType: "text/plain",
          sourceId: "pine_v6_rules",
          title: "Pine v6 rules",
          type: "source-document",
        },
        {
          mediaType: "text/plain",
          sourceId: "pine_v6_rules",
          title: "Duplicate Pine v6 rules",
          type: "source-document",
        },
      ],
      role: "assistant",
    };

    expect(getMessageSources(assistant)).toEqual([
      {
        id: "tradingview-pine-strategies",
        title: "TradingView Pine strategies",
        type: "external",
        url: "https://www.tradingview.com/pine-script-docs/",
      },
      { id: "pine_v6_rules", title: "Pine v6 rules", type: "internal" },
    ]);
  });

  it("extracts trading data parts and gates strategy cards by intent", () => {
    const assistant: UIMessage = {
      id: "msg_market",
      parts: [
        { text: "ETH snapshot", type: "text" },
        { data: { intent: "market_snapshot" }, type: "data-responseIntent" },
        {
          data: {
            approximate: true,
            freshness: "source_backed",
            label: "Market snapshot",
            price: null,
            price_points: [],
            source_count: 1,
            sources: [{ id: "src", title: "Source", type: "external", url: "https://example.com" }],
            symbol: "ETH",
          },
          type: "data-marketSnapshot",
        },
        {
          data: {
            approximate: true,
            freshness: "source_backed",
            label: "Market snapshot",
            price: "$1,705.40",
            price_points: [],
            source_count: 1,
            sources: [{ id: "src", title: "Source", type: "external", url: "https://example.com" }],
            symbol: "ETH",
          },
          type: "data-marketSnapshot",
        },
      ],
      role: "assistant",
    };

    expect(getMessageResponseIntent(assistant)).toBe("market_snapshot");
    expect(shouldShowStrategyProfile(getMessageResponseIntent(assistant))).toBe(false);
    expect(getMessageMarketSnapshot(assistant)).toMatchObject({ price: "$1,705.40", symbol: "ETH" });
    expect(shouldShowStrategyProfile("strategy_building")).toBe(true);
    expect(shouldShowStrategyProfile("artifact_generation")).toBe(true);
  });

  it("does not expose sources for normal text-only messages", () => {
    expect(getMessageSources(message("assistant", "Plain answer"))).toEqual([]);
  });

  it("reads context-aware suggestions from assistant data parts", () => {
    const assistant: UIMessage = {
      id: "msg_suggestions",
      parts: [
        {
          data: {
            actions: [
              {
                action: "send_prompt",
                category: "risk",
                enabled: true,
                id: "review-risk",
                kind: "chat_action",
                label: "Review risk",
                priority: 1,
                prompt: "Review risk rules.",
              },
            ],
            composer_blocks: [],
            version: 1,
          },
          type: "data-suggestions",
        },
      ],
      role: "assistant",
    };

    expect(getMessageSuggestions(assistant)?.actions[0]).toMatchObject({
      id: "review-risk",
      prompt: "Review risk rules.",
    });
  });

  it("keeps suggestion chips on the normal chat path", () => {
    expect(CHAT_SUGGESTIONS.map((suggestion) => suggestion.label)).toEqual([
      "Turn into strategy spec",
      "Generate Pine v6 artifact",
      "Review risk rules",
      "Review assumptions",
    ]);
    expect(
      CHAT_SUGGESTIONS.every((suggestion) => suggestion.prompt.length > 0)
    ).toBe(true);
  });

  it("summarizes compact activity without exposing technical event names", () => {
    expect(
      compactActivityTitle([
        {
          description: "Retrying with an available provider route.",
          id: "evt_1",
          state: "input-available",
          title: "Retrying provider",
          toolName: "provider",
        },
      ])
    ).toBe("Retrying provider");
  });

  it("treats only message-free conversations without runs as empty", () => {
    const baseItem = {
      conversation: {
        created_at: "2026-01-01T00:00:00.000Z",
        id: "conv_1",
        metadata: {},
        owner_user_id: "user_1",
        title: null,
        updated_at: "2026-01-01T00:00:00.000Z",
        workspace_id: "workspace_1",
      },
      last_message_at: null,
      last_message_preview: null,
      latest_run_id: null,
      latest_run_status: null,
      message_count: 0,
      updated_at: "2026-01-01T00:00:00.000Z",
    };

    expect(isEmptyConversation(baseItem)).toBe(true);
    expect(isEmptyConversation({ ...baseItem, message_count: 1 })).toBe(false);
    expect(isEmptyConversation({ ...baseItem, latest_run_id: "run_1" })).toBe(false);
    expect(isEmptyConversation(null)).toBe(false);
  });
});
