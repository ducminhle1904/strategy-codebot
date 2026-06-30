import { describe, expect, it } from "vitest";
import type { Message as AgUiMessage } from "@copilotkit/react-core/v2";
import type { Message as BackendMessage, RunEvent } from "./backend-schemas";

import {
  CHAT_SUGGESTIONS,
  backendMessagesToStrategyMessages,
  copilotAgentMessageToStrategyMessage,
  compactActivityTitle,
  metadataPatchFromAgUiCustomEvent,
  metadataPatchFromAgUiReasoningEvent,
  getMessageText,
  mergeStrategyChatMessageMetadata,
  groupArtifactsByAnchorMessage,
  hasAssistantText,
  isEmptyConversation,
  isRenderableMessage,
  latestAssistantAfterLastUser,
  runEventMetadataByAnchorMessage,
  shouldShowStrategyProfile,
} from "./chat-ui";
import { normalizeWorkflowState } from "./workflow-ui";

function strategyMessage(role: "assistant" | "user", text: string) {
  return {
    backtestReport: null,
    inlineTables: [],
    id: `${role}-${text || "empty"}`,
    marketSnapshot: null,
    raw: null,
    reasoningSummaries: [],
    responseIntent: null,
    role,
    sources: [],
    suggestions: null,
    text,
    workflow: null,
  };
}

function backendMessage(
  id: string,
  role: BackendMessage["role"],
  createdAt: string
): BackendMessage {
  return {
    content: role,
    conversation_id: "conv_1",
    created_at: createdAt,
    id,
    owner_user_id: "user_1",
    role,
    workspace_id: "workspace_1",
  };
}

function runEvent(
  type: string,
  createdAt: string,
  payload: RunEvent["payload"]
): RunEvent {
  return {
    conversation_id: "conv_1",
    created_at: createdAt,
    event_id: `evt_${type.replaceAll(".", "_")}`,
    payload,
    request_id: null,
    run_id: "run_1",
    sequence: 1,
    trace_id: null,
    type,
  };
}

describe("chat UI helpers", () => {
  it("maps persisted backend chat messages into renderable UI messages", () => {
    const uiMessages = backendMessagesToStrategyMessages([
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
    expect(isRenderableMessage(strategyMessage("assistant", ""))).toBe(false);
    expect(isRenderableMessage(strategyMessage("assistant", "Ready"))).toBe(true);
    expect(isRenderableMessage(strategyMessage("user", ""))).toBe(true);
  });

  it("keeps assistant reasoning visible before text arrives", () => {
    const assistant = {
      ...strategyMessage("assistant", ""),
      reasoningSummaries: [{ id: "reasoning_1", state: "streaming" as const, text: "Checking market context." }],
    };

    expect(isRenderableMessage(assistant)).toBe(true);
  });

  it("keeps workflow-only assistant messages visible before text arrives", () => {
    const workflow = normalizeWorkflowState({
      current_step: "collect_strategy_inputs",
      workflow_id: "strategy_bot_simulation",
    });
    const assistant = {
      ...strategyMessage("assistant", ""),
      workflow,
    };

    expect(workflow).not.toBeNull();
    expect(isRenderableMessage(assistant)).toBe(true);
  });

  it("finds only the assistant response for the latest user turn", () => {
    const oldAssistant = strategyMessage("assistant", "Hi back");
    const latestUser = strategyMessage("user", "analyze current ETH market");
    expect(
      latestAssistantAfterLastUser([
        strategyMessage("user", "hi"),
        oldAssistant,
        latestUser,
      ])
    ).toBeNull();

    const currentAssistant = strategyMessage("assistant", "ETH market update");
    expect(
      latestAssistantAfterLastUser([
        strategyMessage("user", "hi"),
        oldAssistant,
        latestUser,
        currentAssistant,
      ])
    ).toBe(currentAssistant);
  });

  it("anchors persisted run metadata to the assistant message after the event", () => {
    const metadata = runEventMetadataByAnchorMessage({
      backendMessages: [
        backendMessage("msg_user", "user", "2026-06-21T11:44:44.000Z"),
        backendMessage("msg_assistant", "assistant", "2026-06-21T11:44:58.000Z"),
        backendMessage("msg_next_user", "user", "2026-06-21T11:45:26.000Z"),
      ],
      events: [
        runEvent("chat.market_snapshot", "2026-06-21T11:44:55.000Z", {
          approximate: true,
          freshness: "source_backed",
          label: "Market snapshot",
          price: "$1,726.64",
          price_points: [{ label: "ETH", value: 1726.64 }],
          source_count: 1,
          sources: [{ id: "binance", title: "Binance", type: "external", url: "https://example.com" }],
          symbol: "ETH",
        }),
      ],
    });

    expect(metadata.get("msg_assistant")?.marketSnapshot?.symbol).toBe("ETH");
    expect(metadata.has("msg_user")).toBe(false);
  });

  it("anchors persisted backtest summary tool output to the assistant message", () => {
    const metadata = runEventMetadataByAnchorMessage({
      backendMessages: [
        backendMessage("msg_user", "user", "2026-06-21T11:44:44.000Z"),
        backendMessage("msg_assistant", "assistant", "2026-06-21T11:44:58.000Z"),
      ],
      events: [
        runEvent("tool.completed", "2026-06-21T11:44:55.000Z", {
          output: {
            status: "ok",
            summary: {
              assumptions: { symbol: "BTC/USDT", timeframe: "1h" },
              metrics: {
                max_drawdown: 1.7561,
                pnl: { absolute: -19.1541, percentage: -0.1915 },
                quality_flags: [],
                quality_status: "pass",
                trade_count: 9,
                win_rate: 33.3333,
              },
            },
          },
          tool_id: "get_backtest_summary",
        }),
      ],
    });

    expect(metadata.get("msg_assistant")?.backtestReport).toMatchObject({
      kind: "report",
      qualityStatus: "pass",
    });
  });

  it("anchors persisted safe reasoning deltas to the assistant message", () => {
    const metadata = runEventMetadataByAnchorMessage({
      backendMessages: [
        backendMessage("msg_user", "user", "2026-06-21T11:44:44.000Z"),
        backendMessage("msg_assistant", "assistant", "2026-06-21T11:44:58.000Z"),
      ],
      events: [
        runEvent("model.reasoning.delta", "2026-06-21T11:44:55.000Z", {
          safe: true,
          text: "Checking workflow state.",
        }),
      ],
    });

    expect(metadata.get("msg_assistant")?.reasoningSummaries).toEqual([
      {
        id: "evt_model_reasoning_delta",
        state: "done",
        text: "Checking workflow state.",
      },
    ]);
  });

  it("does not attach cancelled-run metadata to a previous assistant", () => {
    const metadata = runEventMetadataByAnchorMessage({
      backendMessages: [
        backendMessage("msg_user", "user", "2026-06-21T11:44:44.000Z"),
        backendMessage("msg_assistant", "assistant", "2026-06-21T11:44:58.000Z"),
        backendMessage("msg_next_user", "user", "2026-06-21T11:45:26.000Z"),
      ],
      events: [
        runEvent("chat.response_intent", "2026-06-21T11:46:24.000Z", {
          intent: "general_chat",
        }),
      ],
    });

    expect(metadata.size).toBe(0);
  });

  it("reads assistant markdown text from strategy chat messages", () => {
    const assistant = strategyMessage("assistant", "## Title\n- Item");

    expect(getMessageText(assistant)).toBe("## Title\n- Item");
    expect(hasAssistantText([strategyMessage("user", "hello"), assistant])).toBe(true);
  });

  it("normalizes CopilotKit AG-UI messages into strategy chat messages", () => {
    const assistant = {
      content: "AG-UI answer",
      id: "msg_ag_ui",
      role: "assistant",
    } satisfies AgUiMessage;

    expect(copilotAgentMessageToStrategyMessage(assistant)).toMatchObject({
      id: "msg_ag_ui",
      role: "assistant",
      text: "AG-UI answer",
    });
  });

  it("keeps CopilotKit metadata on normalized strategy chat messages", () => {
    const assistant = {
      content: "BTC context",
      id: "msg_ag_ui_market",
      role: "assistant",
    } satisfies AgUiMessage;

    expect(
      copilotAgentMessageToStrategyMessage(assistant, {
        marketSnapshot: {
          approximate: true,
          change: null,
          change_percent: 1.2,
          currency: "USD",
          freshness: "source_backed",
          generated_at: null,
          label: "BTC",
          price: "$63,000",
          price_points: [],
          provider: "Binance (CCXT)",
          source_count: 1,
          sources: [
            {
              id: "binance-btc",
              title: "Binance BTC/USDT",
              type: "external",
              url: "https://www.binance.com",
            },
          ],
          symbol: "BTC",
        },
        backtestReport: {
          kind: "report",
          assumptions: [],
          dataSource: null,
          metrics: [{ key: "net_pnl", label: "PnL", value: "-19.15" }],
          promotionDecision: null,
          promotionReasons: [],
          qualityFlags: [],
          qualityStatus: "pass",
          reproducibilityHash: null,
          robustness: [],
          warnings: [],
        },
        inlineTables: [],
        reasoningSummaries: [{ id: "reasoning-1", text: "Reading market context" }],
        responseIntent: "market_snapshot",
        sources: [
          {
            id: "binance-btc",
            title: "Binance BTC/USDT",
            type: "external",
            url: "https://www.binance.com",
          },
        ],
        suggestions: null,
        workflow: null,
      })
    ).toMatchObject({
      backtestReport: {
        kind: "report",
        metrics: [{ key: "net_pnl", label: "PnL", value: "-19.15" }],
      },
      marketSnapshot: { price: "$63,000", symbol: "BTC" },
      reasoningSummaries: [{ text: "Reading market context" }],
      responseIntent: "market_snapshot",
      sources: [{ id: "binance-btc" }],
      text: "BTC context",
    });
  });

  it("maps CopilotKit custom events into strategy metadata patches", () => {
    const metadata = mergeStrategyChatMessageMetadata(undefined, {
      responseIntent: "market_snapshot",
    });
    const marketPatch = metadataPatchFromAgUiCustomEvent({
      name: "strategy.marketSnapshot",
      value: {
        approximate: true,
        freshness: "source_backed",
        label: "BTC",
        price: "$63,000",
        price_points: [],
        source_count: 1,
        sources: [{ id: "src", title: "Source", type: "external", url: "https://example.com" }],
        symbol: "BTC",
      },
    });
    const sourcePatch = metadataPatchFromAgUiCustomEvent({
      name: "strategy.sources",
      value: {
        sources: [
          { id: "src", title: "Source", type: "external", url: "https://example.com" },
          { id: "internal", title: "Internal note", type: "internal" },
        ],
      },
    });
    const reasoningPatch = metadataPatchFromAgUiReasoningEvent({
      delta: "- Preparing response\n",
      messageId: "reasoning-live",
    });
    const backtestPatch = metadataPatchFromAgUiCustomEvent({
      name: "strategy.backtestReport",
      value: {
        report: {
          assumptions: { symbol: "BTC/USDT", timeframe: "1h" },
          metrics: {
            max_drawdown: 1.7561,
            pnl: { absolute: -19.1541, percentage: -0.1915 },
            quality_flags: [],
            quality_status: "pass",
            trade_count: 9,
            win_rate: 33.3333,
          },
          warnings: ["Local sandbox preview evidence only."],
        },
      },
    });

    const merged = mergeStrategyChatMessageMetadata(
      mergeStrategyChatMessageMetadata(
        mergeStrategyChatMessageMetadata(
          mergeStrategyChatMessageMetadata(metadata, marketPatch ?? {}),
          sourcePatch ?? {}
        ),
        reasoningPatch ?? {}
      ),
      backtestPatch ?? {}
    );

    expect(merged).toMatchObject({
      backtestReport: { kind: "report", qualityStatus: "pass" },
      marketSnapshot: { price: "$63,000", symbol: "BTC" },
      reasoningSummaries: [{ text: "Preparing response" }],
      responseIntent: "market_snapshot",
      sources: [{ id: "src" }, { id: "internal" }],
    });
  });

  it("extracts sources from CopilotKit custom source events", () => {
    const patch = metadataPatchFromAgUiCustomEvent({
      name: "strategy.sources",
      value: {
        sources: [
          {
            id: "tradingview-pine-strategies",
            title: "TradingView Pine strategies",
            type: "external",
            url: "https://www.tradingview.com/pine-script-docs/",
          },
          {
            id: "pine_v6_rules",
            title: "Pine v6 rules",
            type: "internal",
          },
          {
            id: "pine_v6_rules",
            title: "Duplicate Pine v6 rules",
            type: "internal",
          },
        ],
      },
    });

    expect(patch?.sources).toEqual([
      {
        id: "tradingview-pine-strategies",
        title: "TradingView Pine strategies",
        type: "external",
        url: "https://www.tradingview.com/pine-script-docs/",
      },
      { id: "pine_v6_rules", title: "Pine v6 rules", type: "internal" },
    ]);
  });

  it("extracts trading data custom events and gates strategy cards by intent", () => {
    const intentPatch = metadataPatchFromAgUiCustomEvent({
      name: "strategy.responseIntent",
      value: { intent: "market_snapshot" },
    });
    const marketPatch = metadataPatchFromAgUiCustomEvent({
      name: "strategy.marketSnapshot",
      value: {
        approximate: true,
        freshness: "source_backed",
        label: "Market snapshot",
        price: "$1,705.40",
        price_points: [],
        source_count: 1,
        sources: [{ id: "src", title: "Source", type: "external", url: "https://example.com" }],
        symbol: "ETH",
      },
    });

    expect(intentPatch?.responseIntent).toBe("market_snapshot");
    expect(shouldShowStrategyProfile(intentPatch?.responseIntent ?? null)).toBe(false);
    expect(marketPatch?.marketSnapshot).toMatchObject({ price: "$1,705.40", symbol: "ETH" });
    expect(shouldShowStrategyProfile("strategy_building")).toBe(true);
    expect(shouldShowStrategyProfile("artifact_generation")).toBe(true);
    expect(shouldShowStrategyProfile("pine_generation")).toBe(true);
    expect(shouldShowStrategyProfile("backtest_preview")).toBe(true);
  });

  it("does not expose sources for normal text-only messages", () => {
    expect(strategyMessage("assistant", "Plain answer").sources).toEqual([]);
  });

  it("reads context-aware suggestions from CopilotKit custom events", () => {
    const patch = metadataPatchFromAgUiCustomEvent({
      name: "strategy.suggestions",
      value: {
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
    });

    expect(patch?.suggestions?.actions[0]).toMatchObject({
      id: "review-risk",
      prompt: "Review risk rules.",
    });
  });

  it("reads workflow state from CopilotKit custom events", () => {
    const patch = metadataPatchFromAgUiCustomEvent({
      name: "strategy.workflow",
      value: {
        workflow_id: "strategy_bot_simulation",
        current_step: "collect_strategy_inputs",
        completed_steps: [],
        required_fields: ["market", "symbol", "timeframe", "style", "risk_preference"],
        missing_fields: ["symbol", "timeframe"],
        artifact_refs: {},
        evidence_status: "insufficient_evidence",
        start_allowed: false,
      },
    });

    expect(patch?.workflow).toMatchObject({
      workflow_id: "strategy_bot_simulation",
      current_step: "collect_strategy_inputs",
      missing_fields: ["symbol", "timeframe"],
      start_allowed: false,
    });
  });

  it("maps inline table custom events into strategy metadata", () => {
    const patch = metadataPatchFromAgUiCustomEvent({
      name: "strategy.inlineTable",
      value: {
        kind: "backtest_trades",
        title: "Backtest trades",
        columns: [
          { key: "trade_rank", label: "#", align: "right" },
          { key: "pnl_cost", label: "P&L", align: "right", tone: "profit_loss" },
        ],
        rows: [{ trade_rank: 1, pnl_cost: -12.5 }],
        source_tool_id: "query_backtest_trades",
        run_id: "run_backtest",
        row_count: 1,
      },
    });

    expect(patch?.inlineTables?.[0]).toMatchObject({
      kind: "backtest_trades",
      run_id: "run_backtest",
      rows: [{ trade_rank: 1, pnl_cost: -12.5 }],
    });
  });

  it("derives backtest trade tables from completed tool run events", () => {
    const grouped = runEventMetadataByAnchorMessage({
      backendMessages: [
        backendMessage("msg_user", "user", "2026-01-01T00:00:00.000Z"),
        backendMessage("msg_assistant", "assistant", "2026-01-01T00:00:03.000Z"),
      ],
      events: [
        runEvent("tool.completed", "2026-01-01T00:00:01.000Z", {
          tool_id: "query_backtest_trades",
          output: {
            status: "ok",
            run_id: "run_backtest",
            trades: [
              {
                bucket: "sample",
                trade_rank: 1,
                opened_at: "2024-01-01T00:00:00+00:00",
                closed_at: "2024-01-01T01:00:00+00:00",
                pnl_cost: -12.5,
                pnl_percentage: -0.12,
                trade: { side: "long" },
              },
            ],
          },
        }),
      ],
    });

    expect(grouped.get("msg_assistant")?.inlineTables[0]).toMatchObject({
      kind: "backtest_trades",
      rows: [
        expect.objectContaining({
          bucket: "sample",
          side: "long",
          pnl_cost: -12.5,
        }),
      ],
    });
  });

  it("uses the latest suggestions payload when the server refreshes actions", () => {
    const firstPatch = metadataPatchFromAgUiCustomEvent({
      name: "strategy.suggestions",
      value: {
        actions: [
          {
            action: "send_prompt",
            category: "code",
            enabled: true,
            id: "generate-pine-v6",
            kind: "chat_action",
            label: "Generate Pine v6",
            priority: 1,
            prompt: "Generate Pine v6 artifact.",
          },
        ],
        composer_blocks: [],
        version: 1,
      },
    });
    const secondPatch = metadataPatchFromAgUiCustomEvent({
      name: "strategy.suggestions",
      value: {
        actions: [
          {
            action: "send_prompt",
            category: "review",
            enabled: true,
            id: "run-backtest-preview",
            kind: "chat_action",
            label: "Backtest Preview",
            priority: -10,
            prompt: "Prepare local preview evidence.",
          },
        ],
        composer_blocks: [],
        version: 1,
      },
    });
    const merged = mergeStrategyChatMessageMetadata(
      mergeStrategyChatMessageMetadata(undefined, firstPatch ?? {}),
      secondPatch ?? {}
    );

    expect(merged.suggestions?.actions[0]).toMatchObject({
      id: "run-backtest-preview",
      prompt: "Prepare local preview evidence.",
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

  it("anchors an existing artifact to the assistant turn that created it", () => {
    const backendMessages = [
      {
        content: "give me a pinescript for Breakout continuation",
        conversation_id: "conv_1",
        created_at: "2026-01-01T00:00:00.000Z",
        id: "msg_user_breakout",
        owner_user_id: "user_1",
        role: "user" as const,
        workspace_id: "workspace_1",
      },
      {
        content: "Generated review-only Pine v6 code.",
        conversation_id: "conv_1",
        created_at: "2026-01-01T00:00:10.000Z",
        id: "msg_assistant_breakout",
        owner_user_id: "user_1",
        role: "assistant" as const,
        workspace_id: "workspace_1",
      },
      {
        content: "now make Range / mean reversion",
        conversation_id: "conv_1",
        created_at: "2026-01-01T00:05:00.000Z",
        id: "msg_user_range",
        owner_user_id: "user_1",
        role: "user" as const,
        workspace_id: "workspace_1",
      },
      {
        content: "Working on range / mean reversion.",
        conversation_id: "conv_1",
        created_at: "2026-01-01T00:05:12.000Z",
        id: "msg_assistant_range",
        owner_user_id: "user_1",
        role: "assistant" as const,
        workspace_id: "workspace_1",
      },
    ];

    const grouped = groupArtifactsByAnchorMessage({
      artifacts: [
        {
          category: "code",
          conversation_id: "conv_1",
          created_at: "2026-01-01T00:00:08.000Z",
          display_name: "breakout-continuation.pine",
          id: "artifact_breakout",
          kind: "pine_file",
          metadata_json: null,
          mime_type: "text/plain",
          owner_user_id: "user_1",
          presentation: {
            dedupe_key: "code:breakout-continuation.pine",
            is_primary: true,
            language_hint: "pine",
            user_kind: "code",
            viewer_kind: "code",
            visibility: "user",
          },
          preview_summary: null,
          run_id: "run_breakout",
          visibility: "user",
          workspace_id: "workspace_1",
        },
      ],
      backendMessages,
    });

    expect(grouped.get("msg_assistant_breakout")?.map((artifact) => artifact.id)).toEqual([
      "artifact_breakout",
    ]);
    expect(grouped.has("msg_assistant_range")).toBe(false);
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
