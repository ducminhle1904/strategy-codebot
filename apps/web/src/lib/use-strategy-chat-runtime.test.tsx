import { act, cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useStrategyChatRuntime } from "./use-strategy-chat-runtime";

const useAgentMock = vi.fn();
const useCopilotKitMock = vi.fn();
let copilotSubscriber: {
  onEvent?: (params: { event: Record<string, unknown> }) => void;
  onMessagesChanged?: (params: { messages: unknown[] }) => void;
  onRunFinalized?: (params: { messages: unknown[] }) => void;
  onRunInitialized?: (params: { messages: unknown[] }) => void;
} | null = null;

vi.mock("@copilotkit/react-core/v2", () => ({
  UseAgentUpdate: {
    OnMessagesChanged: "OnMessagesChanged",
    OnRunStatusChanged: "OnRunStatusChanged",
    OnStateChanged: "OnStateChanged",
  },
  useAgent: (...args: unknown[]) => useAgentMock(...args),
  useCopilotKit: (...args: unknown[]) => useCopilotKitMock(...args),
}));

function renderRuntime() {
  const result: Array<ReturnType<typeof useStrategyChatRuntime>> = [];
  function Harness() {
    result[0] = useStrategyChatRuntime({
      activeConversationId: "conv_1",
      initialMessages: [],
      language: "en",
      onData: vi.fn(),
      onError: vi.fn(),
      onFinish: vi.fn(),
      webSearchMode: "auto",
    });
    return null;
  }
  render(<Harness />);
  return {
    get current() {
      return result[0];
    },
  };
}

function setupCopilotRuntime() {
  const runAgent = vi.fn();
  const fetchMock = vi.fn(async () => agUiResponse([{ type: "RUN_FINISHED" }]));
  vi.stubGlobal("fetch", fetchMock);
  copilotSubscriber = null;
  useCopilotKitMock.mockReturnValue({
    copilotkit: {
      runAgent,
    },
  });
  useAgentMock.mockReturnValue({
    agent: {
      abortRun: vi.fn(),
      addMessage: vi.fn(),
      isRunning: false,
      messages: [{ content: "Hello from CopilotKit", id: "msg_1", role: "assistant" }],
      setMessages: vi.fn(),
      subscribe: vi.fn((subscriber) => {
        copilotSubscriber = subscriber;
        return { unsubscribe: vi.fn() };
      }),
    },
  });
  return { fetchMock, runAgent };
}

function agUiResponse(events: Array<Record<string, unknown>>) {
  return new Response(
    new ReadableStream({
      start(controller) {
        const encoder = new TextEncoder();
        for (const event of events) {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
        }
        controller.close();
      },
    }),
    { status: 200 }
  );
}

function lastCopilotRunBody(fetchMock: ReturnType<typeof vi.fn>) {
  const body = fetchMock.mock.calls.at(-1)?.[1]?.body;
  if (typeof body !== "string") {
    throw new Error("Expected CopilotKit fetch body to be a JSON string.");
  }
  return JSON.parse(body) as {
    body: {
      forwardedProps: Record<string, unknown>;
      messages: Array<Record<string, unknown>>;
      threadId: string;
    };
  };
}

describe("useStrategyChatRuntime", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.clearAllMocks();
    vi.unstubAllEnvs();
  });

  it("initializes CopilotKit as the only chat runtime", () => {
    setupCopilotRuntime();

    const runtime = renderRuntime();

    expect(runtime.current.runtime).toBe("copilotkit");
    expect(runtime.current.messages[0]).toMatchObject({ text: "Hello from CopilotKit" });
    expect(useAgentMock).toHaveBeenCalledTimes(1);
  });

  it("keeps the per-send conversation id when CopilotKit sends a new-chat message", async () => {
    const { fetchMock } = setupCopilotRuntime();

    const runtime = renderRuntime();
    await runtime.current.sendMessage(
      { text: "hello" },
      { body: { conversationId: "conv_created_for_new_chat" } }
    );

    expect(fetchMock).toHaveBeenCalledWith("/api/copilotkit-chat", expect.any(Object));
    const runBody = lastCopilotRunBody(fetchMock);
    expect(runBody.body.forwardedProps).toMatchObject({
      conversationId: "conv_created_for_new_chat",
      language: "en",
      mode: "agent",
      webSearch: "auto",
    });
    expect(runBody.body.messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ content: "hello", role: "user" }),
      ])
    );
    expect(useAgentMock.mock.results[0]?.value.agent.addMessage).not.toHaveBeenCalled();
  });

  it("adds the submitted user message to local transcript before CopilotKit syncs", async () => {
    setupCopilotRuntime();

    const runtime = renderRuntime();
    await act(async () => {
      await runtime.current.sendMessage({ text: "what should I do now?" });
    });

    expect(runtime.current.messages.at(-1)).toMatchObject({
      role: "user",
      text: "what should I do now?",
    });
  });

  it("keeps repeated user messages with the same text as separate turns", async () => {
    setupCopilotRuntime();

    const runtime = renderRuntime();
    act(() => {
      runtime.current.setMessagesFromConversationState([
        {
          id: "persisted_user_repeat",
          backtestReport: null,
          inlineTables: [],
          marketSnapshot: null,
          raw: null,
          reasoningSummaries: [],
          responseIntent: null,
          role: "user",
          sources: [],
          suggestions: null,
          text: "you haven't show it",
        },
      ]);
    });

    await act(async () => {
      await runtime.current.sendMessage({ text: "you haven't show it" });
    });

    expect(
      runtime.current.messages.filter(
        (message) => message.role === "user" && message.text === "you haven't show it"
      )
    ).toHaveLength(2);
  });

  it("updates rendered messages when CopilotKit emits message changes", () => {
    setupCopilotRuntime();

    const runtime = renderRuntime();
    expect(runtime.current.messages[0]).toMatchObject({ text: "Hello from CopilotKit" });

    act(() => {
      copilotSubscriber?.onMessagesChanged?.({
        messages: [
          { content: "Hello from CopilotKit", id: "msg_1", role: "assistant" },
          { content: "Streaming BTC market update", id: "msg_2", role: "assistant" },
        ],
      });
    });

    expect(runtime.current.messages[1]).toMatchObject({ text: "Streaming BTC market update" });
  });

  it("renders streamed CopilotKit text before the agent message list syncs", () => {
    setupCopilotRuntime();

    const runtime = renderRuntime();

    act(() => {
      copilotSubscriber?.onRunInitialized?.({
        messages: [{ content: "analyze BTC", id: "msg_user", role: "user" }],
      });
      copilotSubscriber?.onEvent?.({
        event: {
          messageId: "msg_live",
          role: "assistant",
          type: "TEXT_MESSAGE_START",
        },
      });
      copilotSubscriber?.onEvent?.({
        event: {
          delta: "BTC market is consolidating",
          messageId: "msg_live",
          type: "TEXT_MESSAGE_CONTENT",
        },
      });
    });

    expect(
      runtime.current.messages.find(
        (message) => message.text === "BTC market is consolidating"
      )
    ).toMatchObject({
      text: "BTC market is consolidating",
    });
  });

  it("does not duplicate assistant text when the stream replays cumulative content", () => {
    setupCopilotRuntime();

    const runtime = renderRuntime();

    act(() => {
      copilotSubscriber?.onRunInitialized?.({
        messages: [{ content: "run preview", id: "msg_user", role: "user" }],
      });
      copilotSubscriber?.onEvent?.({
        event: {
          messageId: "msg_live",
          role: "assistant",
          type: "TEXT_MESSAGE_START",
        },
      });
      copilotSubscriber?.onEvent?.({
        event: {
          delta: "Understood.",
          messageId: "msg_live",
          type: "TEXT_MESSAGE_CONTENT",
        },
      });
      copilotSubscriber?.onEvent?.({
        event: {
          delta: "Understood. I will prepare local preview evidence.",
          messageId: "msg_live",
          type: "TEXT_MESSAGE_CONTENT",
        },
      });
      copilotSubscriber?.onEvent?.({
        event: {
          delta: " I will prepare local preview evidence.",
          messageId: "msg_live",
          type: "TEXT_MESSAGE_CONTENT",
        },
      });
    });

    expect(runtime.current.messages.find((message) => message.id === "msg_live")).toMatchObject({
      text: "Understood. I will prepare local preview evidence.",
    });
  });

  it("preserves streamed text when CopilotKit syncs a tool-only message with the same id", () => {
    setupCopilotRuntime();

    const runtime = renderRuntime();

    act(() => {
      copilotSubscriber?.onRunInitialized?.({
        messages: [{ content: "analyze BTC", id: "msg_user", role: "user" }],
      });
      copilotSubscriber?.onEvent?.({
        event: {
          messageId: "msg_live",
          role: "assistant",
          type: "TEXT_MESSAGE_START",
        },
      });
      copilotSubscriber?.onEvent?.({
        event: {
          delta: "BTC market is consolidating",
          messageId: "msg_live",
          type: "TEXT_MESSAGE_CONTENT",
        },
      });
      copilotSubscriber?.onMessagesChanged?.({
        messages: [
          { content: "analyze BTC", id: "msg_user", role: "user" },
          { content: "", id: "msg_live", role: "assistant" },
        ],
      });
    });

    expect(runtime.current.messages.find((message) => message.id === "msg_live")).toMatchObject({
      text: "BTC market is consolidating",
    });
  });

  it("renders the assistant answer in the same send even before CopilotKit message list syncs", async () => {
    const { fetchMock } = setupCopilotRuntime();
    fetchMock.mockResolvedValueOnce(
      agUiResponse([
        {
          messageId: "msg_live",
          role: "assistant",
          type: "TEXT_MESSAGE_START",
        },
        {
          delta: "BTC is consolidating near resistance.",
          messageId: "msg_live",
          type: "TEXT_MESSAGE_CONTENT",
        },
        { type: "RUN_FINISHED" },
      ])
    );

    const runtime = renderRuntime();
    await act(async () => {
      await runtime.current.sendMessage({ text: "analyze BTC" });
    });

    expect(runtime.current.messages.find((message) => message.id === "msg_live")).toMatchObject({
      role: "assistant",
      text: "BTC is consolidating near resistance.",
    });
  });

  it("attaches CopilotKit custom metadata to the active assistant message", () => {
    setupCopilotRuntime();

    const runtime = renderRuntime();

    act(() => {
      copilotSubscriber?.onRunInitialized?.({
        messages: [{ content: "current BTC market", id: "msg_user", role: "user" }],
      });
      copilotSubscriber?.onEvent?.({
        event: {
          name: "strategy.responseIntent",
          type: "CUSTOM",
          value: { intent: "market_snapshot" },
        },
      });
      copilotSubscriber?.onEvent?.({
        event: {
          messageId: "msg_assistant_live",
          role: "assistant",
          type: "TEXT_MESSAGE_START",
        },
      });
      copilotSubscriber?.onEvent?.({
        event: {
          name: "strategy.marketSnapshot",
          type: "CUSTOM",
          value: {
            approximate: true,
            freshness: "source_backed",
            label: "BTC",
            price: "$63,000",
            price_points: [],
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
        },
      });
      copilotSubscriber?.onMessagesChanged?.({
        messages: [
          { content: "current BTC market", id: "msg_user", role: "user" },
          { content: "BTC is firm today.", id: "msg_assistant_live", role: "assistant" },
        ],
      });
    });

    expect(
      runtime.current.messages.find((message) => message.text === "BTC is firm today.")
    ).toMatchObject({
      marketSnapshot: { price: "$63,000", symbol: "BTC" },
      responseIntent: "market_snapshot",
      text: "BTC is firm today.",
    });
  });

  it("preserves CopilotKit metadata for an older assistant message during a follow-up stream", () => {
    setupCopilotRuntime();

    const runtime = renderRuntime();

    act(() => {
      copilotSubscriber?.onEvent?.({
        event: {
          messageId: "msg_market",
          role: "assistant",
          type: "TEXT_MESSAGE_START",
        },
      });
      copilotSubscriber?.onEvent?.({
        event: {
          name: "strategy.marketSnapshot",
          type: "CUSTOM",
          value: {
            approximate: true,
            freshness: "source_backed",
            label: "BTC",
            price: "$63,000",
            price_points: [],
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
        },
      });
      copilotSubscriber?.onMessagesChanged?.({
        messages: [{ content: "BTC market card", id: "msg_market", role: "assistant" }],
      });
      copilotSubscriber?.onRunFinalized?.({
        messages: [{ content: "BTC market card", id: "msg_market", role: "assistant" }],
      });
      copilotSubscriber?.onRunInitialized?.({
        messages: [
          { content: "BTC market card", id: "msg_market_recreated", role: "assistant" },
          { content: "follow-up", id: "msg_user_2", role: "user" },
        ],
      });
      copilotSubscriber?.onMessagesChanged?.({
        messages: [
          { content: "BTC market card", id: "msg_market_recreated", role: "assistant" },
          { content: "follow-up", id: "msg_user_2", role: "user" },
          { content: "Strategy answer", id: "msg_assistant_2", role: "assistant" },
        ],
      });
    });

    expect(
      runtime.current.messages.find((message) => message.text === "BTC market card")
    ).toMatchObject({
      marketSnapshot: { price: "$63,000", symbol: "BTC" },
      text: "BTC market card",
    });
  });

  it("keeps old market cards when a follow-up run initializes with only the new user message", () => {
    setupCopilotRuntime();

    const runtime = renderRuntime();

    act(() => {
      copilotSubscriber?.onEvent?.({
        event: {
          messageId: "msg_market",
          role: "assistant",
          type: "TEXT_MESSAGE_START",
        },
      });
      copilotSubscriber?.onEvent?.({
        event: {
          name: "strategy.marketSnapshot",
          type: "CUSTOM",
          value: {
            approximate: true,
            freshness: "source_backed",
            label: "BTC",
            price: "$63,000",
            price_points: [],
            source_count: 1,
            sources: [],
            symbol: "BTC",
          },
        },
      });
      copilotSubscriber?.onMessagesChanged?.({
        messages: [{ content: "BTC market card", id: "msg_market", role: "assistant" }],
      });
      copilotSubscriber?.onRunFinalized?.({
        messages: [{ content: "BTC market card", id: "msg_market", role: "assistant" }],
      });
      copilotSubscriber?.onRunInitialized?.({
        messages: [{ content: "what should I do", id: "msg_user_2", role: "user" }],
      });
    });

    expect(
      runtime.current.messages.find((message) => message.text === "BTC market card")
    ).toMatchObject({
      marketSnapshot: expect.objectContaining({ price: "$63,000", symbol: "BTC" }),
      text: "BTC market card",
    });
    expect(
      runtime.current.messages.find((message) => message.text === "what should I do")
    ).toMatchObject({ role: "user", text: "what should I do" });
  });

  it("does not attach pending market metadata to an older assistant when a run has no assistant text yet", () => {
    setupCopilotRuntime();

    const runtime = renderRuntime();

    act(() => {
      copilotSubscriber?.onMessagesChanged?.({
        messages: [{ content: "ETH answer", id: "msg_eth", role: "assistant" }],
      });
      copilotSubscriber?.onRunInitialized?.({
        messages: [
          { content: "ETH answer", id: "msg_eth", role: "assistant" },
          { content: "analyze BNB", id: "msg_bnb_user", role: "user" },
        ],
      });
      copilotSubscriber?.onEvent?.({
        event: {
          name: "strategy.marketSnapshot",
          type: "CUSTOM",
          value: {
            approximate: true,
            freshness: "source_backed",
            label: "BNB",
            price: "$589",
            price_points: [],
            source_count: 1,
            sources: [],
            symbol: "BNB",
          },
        },
      });
      copilotSubscriber?.onMessagesChanged?.({
        messages: [
          { content: "ETH answer", id: "msg_eth", role: "assistant" },
          { content: "analyze BNB", id: "msg_bnb_user", role: "user" },
        ],
      });
    });

    expect(runtime.current.messages.find((message) => message.id === "msg_eth")).toMatchObject({
      marketSnapshot: null,
      text: "ETH answer",
    });
  });

  it("keeps pending market metadata visible when a provider failure persisted no live text", () => {
    setupCopilotRuntime();

    const runtime = renderRuntime();

    act(() => {
      copilotSubscriber?.onRunInitialized?.({
        messages: [{ content: "analyze BNB", id: "msg_bnb_user", role: "user" }],
      });
      copilotSubscriber?.onEvent?.({
        event: {
          name: "strategy.marketSnapshot",
          type: "CUSTOM",
          value: {
            approximate: true,
            freshness: "source_backed",
            label: "BNB",
            price: "$589",
            price_points: [],
            source_count: 1,
            sources: [],
            symbol: "BNB",
          },
        },
      });
      copilotSubscriber?.onEvent?.({
        event: {
          name: "strategy.runEvent",
          type: "CUSTOM",
          value: {
            event: "run.failed",
            payload: {
              assistant_message_persisted: true,
              message: "Provider execution failed",
            },
          },
        },
      });
      copilotSubscriber?.onRunFinalized?.({
        messages: [{ content: "analyze BNB", id: "msg_bnb_user", role: "user" }],
      });
    });

    expect(runtime.current.messages.at(-1)).toMatchObject({
      marketSnapshot: expect.objectContaining({ price: "$589", symbol: "BNB" }),
      role: "assistant",
      text: "The AI run failed: Provider execution failed",
    });
  });

  it("keeps pending market metadata when CopilotKit resolves without run finalization", async () => {
    const { fetchMock } = setupCopilotRuntime();
    fetchMock.mockResolvedValueOnce(
      agUiResponse([
        {
          name: "strategy.marketSnapshot",
          type: "CUSTOM",
          value: {
            approximate: true,
            freshness: "source_backed",
            label: "BNB",
            price: "$589",
            price_points: [],
            source_count: 1,
            sources: [],
            symbol: "BNB",
          },
        },
        {
          name: "strategy.runEvent",
          type: "CUSTOM",
          value: {
            event: "run.failed",
            payload: {
              assistant_message_persisted: true,
              message: "Provider execution failed",
            },
          },
        },
      ])
    );

    const runtime = renderRuntime();
    await act(async () => {
      await runtime.current.sendMessage({ text: "analyze BNB" });
    });

    expect(runtime.current.messages.at(-1)).toMatchObject({
      marketSnapshot: expect.objectContaining({ price: "$589", symbol: "BNB" }),
      role: "assistant",
      text: "The AI run failed: Provider execution failed",
    });
  });

  it("clears live reasoning summaries after a CopilotKit run finishes", () => {
    setupCopilotRuntime();

    const runtime = renderRuntime();

    act(() => {
      copilotSubscriber?.onRunInitialized?.({
        messages: [{ content: "hello", id: "msg_user", role: "user" }],
      });
      copilotSubscriber?.onEvent?.({
        event: {
          delta: "- Preparing response...\n",
          messageId: "reasoning_1",
          type: "REASONING_MESSAGE_CONTENT",
        },
      });
      copilotSubscriber?.onEvent?.({
        event: {
          messageId: "msg_assistant",
          role: "assistant",
          type: "TEXT_MESSAGE_START",
        },
      });
      copilotSubscriber?.onEvent?.({
        event: {
          delta: "Hello",
          messageId: "msg_assistant",
          type: "TEXT_MESSAGE_CONTENT",
        },
      });
    });

    expect(runtime.current.messages.at(-1)).toMatchObject({
      reasoningSummaries: [expect.objectContaining({ text: "Preparing response..." })],
      text: "Hello",
    });

    act(() => {
      copilotSubscriber?.onRunFinalized?.({
        messages: [
          { content: "hello", id: "msg_user", role: "user" },
          { content: "Hello", id: "msg_assistant", role: "assistant" },
        ],
      });
    });

    expect(runtime.current.messages.at(-1)).toMatchObject({
      reasoningSummaries: [],
      text: "Hello",
    });
  });

  it("preserves CopilotKit metadata when backend hydration returns text-only messages", () => {
    setupCopilotRuntime();

    const runtime = renderRuntime();

    act(() => {
      copilotSubscriber?.onEvent?.({
        event: {
          messageId: "msg_market",
          role: "assistant",
          type: "TEXT_MESSAGE_START",
        },
      });
      copilotSubscriber?.onEvent?.({
        event: {
          name: "strategy.marketSnapshot",
          type: "CUSTOM",
          value: {
            approximate: true,
            freshness: "source_backed",
            label: "BTC",
            price: "$63,000",
            price_points: [],
            source_count: 1,
            sources: [],
            symbol: "BTC",
          },
        },
      });
      copilotSubscriber?.onMessagesChanged?.({
        messages: [{ content: "BTC market card", id: "msg_market", role: "assistant" }],
      });
      runtime.current.setMessagesFromConversationState([
        {
          id: "persisted_msg_market",
          backtestReport: null,
          inlineTables: [],
          marketSnapshot: null,
          raw: null,
          reasoningSummaries: [],
          responseIntent: null,
          role: "assistant",
          sources: [],
          suggestions: null,
          text: "BTC market card",
        },
      ]);
    });

    expect(runtime.current.messages[0]).toMatchObject({
      marketSnapshot: { price: "$63,000", symbol: "BTC" },
      text: "BTC market card",
    });
  });

  it("preserves backtest report metadata when backend hydration returns text-only messages", () => {
    setupCopilotRuntime();

    const runtime = renderRuntime();

    act(() => {
      copilotSubscriber?.onEvent?.({
        event: {
          messageId: "msg_backtest",
          role: "assistant",
          type: "TEXT_MESSAGE_START",
        },
      });
      copilotSubscriber?.onEvent?.({
        event: {
          name: "strategy.backtestReport",
          type: "CUSTOM",
          value: {
            report: {
              assumptions: { symbol: "BNBUSDT", timeframe: "1h" },
              metrics: {
                pnl: { absolute: -222.53, percentage: -2.23 },
                trade_count: 236,
              },
              quality_status: "pass",
            },
          },
        },
      });
      copilotSubscriber?.onMessagesChanged?.({
        messages: [{ content: "Backtest summary for BNBUSDT", id: "msg_backtest", role: "assistant" }],
      });
      runtime.current.setMessagesFromConversationState([
        {
          id: "persisted_msg_backtest",
          backtestReport: null,
          inlineTables: [],
          marketSnapshot: null,
          raw: null,
          reasoningSummaries: [],
          responseIntent: null,
          role: "assistant",
          sources: [],
          suggestions: null,
          text: "Backtest summary for BNBUSDT",
        },
      ]);
    });

    const hydrated = runtime.current.messages[0];
    expect(hydrated).toMatchObject({ text: "Backtest summary for BNBUSDT" });
    expect(hydrated?.backtestReport?.metrics).toContainEqual(
      { key: "net_pnl", label: "PnL", value: "-222.53 (-2.23%)" }
    );
    expect(hydrated?.backtestReport?.metrics).toContainEqual(
      { key: "trade_count", label: "Trade count", value: "236" }
    );
  });

  it("preserves inline table metadata when backend hydration returns text-only messages", () => {
    setupCopilotRuntime();

    const runtime = renderRuntime();

    act(() => {
      copilotSubscriber?.onEvent?.({
        event: {
          messageId: "msg_trades",
          role: "assistant",
          type: "TEXT_MESSAGE_START",
        },
      });
      copilotSubscriber?.onEvent?.({
        event: {
          name: "strategy.inlineTable",
          type: "CUSTOM",
          value: {
            kind: "backtest_trades",
            title: "Backtest trades",
            columns: [{ key: "trade_rank", label: "#" }],
            rows: [{ trade_rank: 1 }],
            source_tool_id: "query_backtest_trades",
            run_id: "run_backtest",
            row_count: 1,
          },
        },
      });
      copilotSubscriber?.onMessagesChanged?.({
        messages: [{ content: "Loaded 1 indexed trades. See the table below.", id: "msg_trades", role: "assistant" }],
      });
      runtime.current.setMessagesFromConversationState([
        {
          id: "persisted_msg_trades",
          backtestReport: null,
          inlineTables: [],
          marketSnapshot: null,
          raw: null,
          reasoningSummaries: [],
          responseIntent: null,
          role: "assistant",
          sources: [],
          suggestions: null,
          text: "Loaded 1 indexed trades. See the table below.",
        },
      ]);
    });

    expect(runtime.current.messages[0]?.inlineTables[0]).toMatchObject({
      kind: "backtest_trades",
      rows: [{ trade_rank: 1 }],
    });
  });

  it("remembers inline table metadata when table arrives before final assistant text", () => {
    setupCopilotRuntime();

    const runtime = renderRuntime();

    act(() => {
      copilotSubscriber?.onEvent?.({
        event: {
          messageId: "msg_trades_stream",
          role: "assistant",
          type: "TEXT_MESSAGE_START",
        },
      });
      copilotSubscriber?.onEvent?.({
        event: {
          name: "strategy.inlineTable",
          type: "CUSTOM",
          value: {
            kind: "backtest_trades",
            title: "Backtest trades",
            columns: [{ key: "trade_rank", label: "#" }],
            rows: [{ trade_rank: 1 }],
            source_tool_id: "query_backtest_trades",
            run_id: "run_backtest",
            row_count: 1,
          },
        },
      });
      copilotSubscriber?.onEvent?.({
        event: {
          delta: "Loaded 1 indexed trades. See the table below.",
          messageId: "msg_trades_stream",
          type: "TEXT_MESSAGE_CONTENT",
        },
      });
      runtime.current.setMessagesFromConversationState([
        {
          id: "persisted_msg_trades_after_stream",
          backtestReport: null,
          inlineTables: [],
          marketSnapshot: null,
          raw: null,
          reasoningSummaries: [],
          responseIntent: null,
          role: "assistant",
          sources: [],
          suggestions: null,
          text: "Loaded 1 indexed trades. See the table below.",
        },
      ]);
    });

    expect(runtime.current.messages[0]?.inlineTables[0]).toMatchObject({
      kind: "backtest_trades",
      rows: [{ trade_rank: 1 }],
    });
  });

  it("attaches pending market metadata to a newly hydrated assistant after early finalization", () => {
    setupCopilotRuntime();

    const runtime = renderRuntime();

    act(() => {
      copilotSubscriber?.onRunInitialized?.({
        messages: [{ content: "analyze ETH", id: "msg_eth_user", role: "user" }],
      });
      copilotSubscriber?.onEvent?.({
        event: {
          name: "strategy.marketSnapshot",
          type: "CUSTOM",
          value: {
            approximate: true,
            freshness: "source_backed",
            label: "ETH",
            price: "$1,730",
            price_points: [],
            source_count: 1,
            sources: [],
            symbol: "ETH",
          },
        },
      });
      copilotSubscriber?.onRunFinalized?.({
        messages: [{ content: "analyze ETH", id: "msg_eth_user", role: "user" }],
      });
      runtime.current.setMessagesFromConversationState([
        {
          id: "msg_eth_user",
          backtestReport: null,
          inlineTables: [],
          marketSnapshot: null,
          raw: null,
          reasoningSummaries: [],
          responseIntent: null,
          role: "user",
          sources: [],
          suggestions: null,
          text: "analyze ETH",
        },
        {
          id: "msg_eth_answer",
          backtestReport: null,
          inlineTables: [],
          marketSnapshot: null,
          raw: null,
          reasoningSummaries: [],
          responseIntent: null,
          role: "assistant",
          sources: [],
          suggestions: null,
          text: "ETH is holding near resistance.",
        },
      ]);
    });

    expect(runtime.current.messages.find((message) => message.id === "msg_eth_answer")).toMatchObject({
      marketSnapshot: { price: "$1,730", symbol: "ETH" },
      text: "ETH is holding near resistance.",
    });
  });

  it("forwards regenerate message id and body through CopilotKit", async () => {
    const { fetchMock } = setupCopilotRuntime();

    const runtime = renderRuntime();
    await runtime.current.regenerate({
      body: { clientRequestId: "req_regenerate", conversationId: "conv_override" },
      messageId: "msg_assistant_to_retry",
    });

    expect(lastCopilotRunBody(fetchMock).body.forwardedProps).toMatchObject({
      clientRequestId: "req_regenerate",
      conversationId: "conv_1",
      messageId: "msg_assistant_to_retry",
      regenerate: true,
    });
  });

  it("records compact AG-UI lifecycle logs only when debug is enabled", () => {
    const infoSpy = vi.spyOn(console, "info").mockImplementation(() => undefined);
    vi.stubEnv("NEXT_PUBLIC_DEBUG_AG_UI", "true");
    setupCopilotRuntime();

    renderRuntime();

    act(() => {
      copilotSubscriber?.onRunInitialized?.({ messages: [] });
      copilotSubscriber?.onEvent?.({
        event: { messageId: "msg_1", runId: "run_1", type: "TEXT_MESSAGE_CONTENT" },
      });
      copilotSubscriber?.onMessagesChanged?.({
        messages: [{ content: "debug text", id: "msg_1", role: "assistant" }],
      });
      copilotSubscriber?.onRunFinalized?.({
        messages: [{ content: "debug text", id: "msg_1", role: "assistant" }],
      });
    });

    expect(infoSpy).toHaveBeenCalledWith(
      "[strategy-ag-ui] run started",
      expect.objectContaining({ hasText: false })
    );
    expect(infoSpy).toHaveBeenCalledWith(
      "[strategy-ag-ui] text content received",
      expect.objectContaining({ hasText: true, messageId: "msg_1", runId: "run_1" })
    );
    expect(infoSpy).toHaveBeenCalledWith(
      "[strategy-ag-ui] run finished",
      expect.objectContaining({ textEventCount: 1 })
    );
  });

  it("does not record AG-UI logs when debug is disabled", () => {
    const infoSpy = vi.spyOn(console, "info").mockImplementation(() => undefined);
    vi.stubEnv("NEXT_PUBLIC_DEBUG_AG_UI", "false");
    setupCopilotRuntime();

    renderRuntime();

    act(() => {
      copilotSubscriber?.onRunInitialized?.({ messages: [] });
      copilotSubscriber?.onEvent?.({
        event: { messageId: "msg_1", runId: "run_1", type: "TEXT_MESSAGE_CONTENT" },
      });
    });

    expect(infoSpy).not.toHaveBeenCalledWith(
      expect.stringContaining("[strategy-ag-ui]"),
      expect.anything()
    );
  });
});
