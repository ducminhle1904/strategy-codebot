import { afterEach, describe, expect, it, vi } from "vitest";

import { backtestTradesTableFromToolOutput } from "@/lib/backtest-trades-inline-table";

const streamMessageMock = vi.fn();
let cancelCount = 0;

vi.mock("@/lib/server-auth", () => ({
  createServerBackendClient: vi.fn(async () => ({
    streamMessage: streamMessageMock,
  })),
}));

function pythonSse(event: {
  data: Record<string, unknown>;
  event: string;
  id?: string;
}) {
  const lines = [
    event.id ? `id: ${event.id}` : "",
    `event: ${event.event}`,
    `data: ${JSON.stringify(event.data)}`,
  ].filter(Boolean);
  return `${lines.join("\n")}\n\n`;
}

function responseFromSseFrames(frames: string[]) {
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        const encoder = new TextEncoder();
        for (const frame of frames) {
          controller.enqueue(encoder.encode(frame));
        }
        controller.close();
      },
    }),
    { headers: { "Content-Type": "text/event-stream" } }
  );
}

function stalledResponse() {
  cancelCount = 0;
  return new Response(
    new ReadableStream<Uint8Array>({
      cancel() {
        cancelCount += 1;
      },
    }),
    { headers: { "Content-Type": "text/event-stream" } }
  );
}

async function importRouteWithTimeouts() {
  vi.stubEnv("STRATEGY_CODEBOT_CHAT_FIRST_EVENT_TIMEOUT_MS", "5");
  vi.stubEnv("STRATEGY_CODEBOT_CHAT_IDLE_TIMEOUT_MS", "5");
  vi.stubEnv("STRATEGY_CODEBOT_CHAT_TOTAL_TIMEOUT_MS", "20");
  const route = await import("./route");
  return route;
}

async function postCopilotRun(body: Record<string, unknown>) {
  const route = await importRouteWithTimeouts();
  const response = await route.POST(
    new Request("http://localhost/api/copilotkit-chat", {
      body: JSON.stringify(body),
      headers: { "Content-Type": "application/json" },
      method: "POST",
    })
  );
  return eventsFromResponse(response);
}

async function eventsFromResponse(response: Response) {
  const text = await response.text();
  return text
    .split(/\n\n/)
    .filter(Boolean)
    .map((frame) => JSON.parse(frame.replace(/^data: /, "")) as Record<string, unknown>);
}

describe("/api/copilotkit-chat", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.resetModules();
    vi.clearAllMocks();
    vi.unstubAllEnvs();
    cancelCount = 0;
  });

  it("returns runtime info for CopilotKit single-endpoint discovery", async () => {
    const route = await importRouteWithTimeouts();

    const response = await route.POST(
      new Request("http://localhost/api/copilotkit-chat", {
        body: JSON.stringify({ method: "info" }),
        headers: { "Content-Type": "application/json" },
        method: "POST",
      })
    );

    await expect(response.json()).resolves.toMatchObject({
      agents: {
        default: {
          description: expect.any(String),
        },
        "strategy-codebot": {
          capabilities: expect.objectContaining({
            humanInTheLoop: expect.objectContaining({ interrupts: true }),
            state: expect.objectContaining({ snapshots: true }),
            tools: expect.objectContaining({ clientProvided: true }),
            transport: expect.objectContaining({ streaming: true }),
          }),
          description: expect.any(String),
        },
      },
      mode: "sse",
    });
    expect(streamMessageMock).not.toHaveBeenCalled();
  });

  it("returns default and strategy agents for CopilotKit info discovery", async () => {
    const infoRoute = await import("./info/route");

    const response = await infoRoute.GET();

    await expect(response.json()).resolves.toMatchObject({
      agents: {
        default: {
          description: expect.any(String),
        },
        "strategy-codebot": {
          description: expect.any(String),
        },
      },
      mode: "sse",
    });
    expect(streamMessageMock).not.toHaveBeenCalled();
  });

  it("returns an empty CopilotKit thread list for runtime thread discovery", async () => {
    const threadsRoute = await import("./threads/route");

    const response = await threadsRoute.GET();

    await expect(response.json()).resolves.toEqual({
      nextCursor: null,
      threads: [],
    });
    expect(streamMessageMock).not.toHaveBeenCalled();
  });

  it("handles empty runtime probes and stop requests without starting backend chat", async () => {
    const route = await importRouteWithTimeouts();

    const emptyResponse = await route.POST(
      new Request("http://localhost/api/copilotkit-chat", {
        body: "",
        method: "POST",
      })
    );
    await expect(emptyResponse.json()).resolves.toMatchObject({ mode: "sse" });

    const stopResponse = await route.POST(
      new Request("http://localhost/api/copilotkit-chat", {
        body: JSON.stringify({ method: "agent/stop" }),
        headers: { "Content-Type": "application/json" },
        method: "POST",
      })
    );

    expect(stopResponse.status).toBe(204);
    expect(streamMessageMock).not.toHaveBeenCalled();
  });

  it("emits AG-UI state, activity, and tool lifecycle events", async () => {
    streamMessageMock.mockResolvedValue(
      responseFromSseFrames([
        pythonSse({
          data: {
            payload: {
              input: { symbol: "BTC", trace_id: "trace_hidden" },
              tool_id: "run_backtest_preview",
            },
          },
          event: "tool.started",
          id: "evt_tool_started",
        }),
        pythonSse({
          data: {
            payload: {
              output: {
                artifact_id: "art_1",
                display_name: "preview.json",
                trace_id: "trace_hidden",
              },
              tool_id: "run_backtest_preview",
            },
          },
          event: "tool.completed",
          id: "evt_tool_completed",
        }),
      ])
    );

    const events = await postCopilotRun({
      forwardedProps: { conversationId: "conv_1" },
      messages: [{ content: "run preview", role: "user" }],
    });

    expect(events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ type: "STATE_SNAPSHOT" }),
        expect.objectContaining({ type: "ACTIVITY_DELTA" }),
        expect.objectContaining({
          toolCallName: "run_backtest_preview",
          type: "TOOL_CALL_START",
        }),
        expect.objectContaining({ type: "TOOL_CALL_ARGS" }),
        expect.objectContaining({ type: "TOOL_CALL_END" }),
        expect.objectContaining({
          result: expect.stringContaining("preview.json"),
          type: "TOOL_CALL_RESULT",
        }),
      ])
    );
    const textStartIndex = events.findIndex((event) => event.type === "TEXT_MESSAGE_START");
    const toolStartIndex = events.findIndex((event) => event.type === "TOOL_CALL_START");
    const textStart = events[textStartIndex] as Record<string, unknown> | undefined;
    const toolStart = events[toolStartIndex] as Record<string, unknown> | undefined;
    expect(textStartIndex).toBeGreaterThan(-1);
    expect(toolStartIndex).toBeGreaterThan(textStartIndex);
    expect(toolStart?.parentMessageId).toBe(textStart?.messageId);
    expect(JSON.stringify(events)).not.toContain("trace_hidden");
  });

  it("maps workflow HITL events into sanitized AG-UI tool lifecycle events", async () => {
    streamMessageMock.mockResolvedValue(
      responseFromSseFrames([
        pythonSse({
          data: {
            payload: {
              platform: "TradingView",
              symbol: "BTC",
              timeframe: "1h",
              trace_id: "trace_hidden",
            },
          },
          event: "backtest.preview.requested",
          id: "evt_preview_requested",
        }),
        pythonSse({
          data: {
            payload: {
              display_name: "preview.json",
              status: "ready",
              trace_id: "trace_hidden",
            },
          },
          event: "backtest.preview.completed",
          id: "evt_preview_completed",
        }),
      ])
    );

    const events = await postCopilotRun({
      forwardedProps: { conversationId: "conv_1" },
      messages: [{ content: "preview", role: "user" }],
    });

    expect(events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          toolCallName: "confirm_backtest_preview",
          type: "TOOL_CALL_START",
        }),
        expect.objectContaining({
          delta: expect.stringContaining("BTC"),
          type: "TOOL_CALL_ARGS",
        }),
        expect.objectContaining({ type: "TOOL_CALL_END" }),
        expect.objectContaining({
          result: expect.stringContaining("preview.json"),
          type: "TOOL_CALL_RESULT",
        }),
      ])
    );
    expect(JSON.stringify(events)).toContain("Preparing backtest preview");
    expect(JSON.stringify(events)).toContain("TradingView");
    expect(JSON.stringify(events)).toContain("1h");
    expect(events.filter((event) => event.type === "TOOL_CALL_ARGS")).toHaveLength(1);
    expect(JSON.stringify(events)).not.toContain("trace_hidden");
  });

  it("marks failed tool.completed activity as failed", async () => {
    streamMessageMock.mockResolvedValue(
      responseFromSseFrames([
        pythonSse({
          data: {
            payload: {
              status: "failed",
              tool_id: "run_backtest_preview",
            },
          },
          event: "tool.completed",
          id: "evt_tool_failed",
        }),
      ])
    );

    const events = await postCopilotRun({
      forwardedProps: { conversationId: "conv_1" },
      messages: [{ content: "run preview", role: "user" }],
    });
    const activityEvent = events.find((event) => event.type === "ACTIVITY_DELTA");

    expect(JSON.stringify(activityEvent)).toContain("Agent step failed");
    expect(JSON.stringify(activityEvent)).toContain("\"status\":\"failed\"");
  });

  it("emits backtest report metadata for completed backtest summary tools", async () => {
    streamMessageMock.mockResolvedValue(
      responseFromSseFrames([
        pythonSse({
          data: {
            payload: {
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
            },
          },
          event: "tool.completed",
          id: "evt_tool_summary",
        }),
      ])
    );

    const events = await postCopilotRun({
      forwardedProps: { conversationId: "conv_1" },
      messages: [{ content: "show result", role: "user" }],
    });

    expect(events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          name: "strategy.backtestReport",
          type: "CUSTOM",
          value: expect.objectContaining({
            report: expect.objectContaining({
              metrics: expect.objectContaining({
                trade_count: 9,
              }),
            }),
          }),
        }),
      ])
    );
  });

  it("emits inline table metadata for completed backtest trade tools", async () => {
    const toolOutput = {
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
    };
    streamMessageMock.mockResolvedValue(
      responseFromSseFrames([
        pythonSse({
          data: {
            payload: {
              output: toolOutput,
              tool_id: "query_backtest_trades",
            },
          },
          event: "tool.completed",
          id: "evt_tool_trades",
        }),
      ])
    );

    const events = await postCopilotRun({
      forwardedProps: { conversationId: "conv_1" },
      messages: [{ content: "give me first trades", role: "user" }],
    });

    expect(events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          name: "strategy.inlineTable",
          type: "CUSTOM",
          value: backtestTradesTableFromToolOutput(toolOutput),
        }),
      ])
    );
    const inlineTableEvent = events.find((event) => event.name === "strategy.inlineTable");
    expect(inlineTableEvent?.value).not.toHaveProperty("caption");
  });

  it("parses backend SSE frames with CRLF and repeated separators", async () => {
    const frame = pythonSse({
      data: { payload: { delta: "Hello" } },
      event: "message.delta",
    })
      .replace(/\n/g, "\r\n")
      .replace(/\r\n\r\n$/, "\r\n\r\n\r\n");
    streamMessageMock.mockResolvedValue(responseFromSseFrames([frame]));

    const events = await postCopilotRun({
      forwardedProps: { conversationId: "conv_1" },
      messages: [{ content: "hello", role: "user" }],
    });

    expect(events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ delta: "Hello", type: "TEXT_MESSAGE_CONTENT" }),
      ])
    );
  });

  it("parses backend SSE frames split across stream chunks", async () => {
    const frame = pythonSse({
      data: { payload: { delta: "Split" } },
      event: "message.delta",
    });
    streamMessageMock.mockResolvedValue(
      responseFromSseFrames([frame.slice(0, 17), frame.slice(17)])
    );

    const events = await postCopilotRun({
      forwardedProps: { conversationId: "conv_1" },
      messages: [{ content: "hello", role: "user" }],
    });

    expect(events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ delta: "Split", type: "TEXT_MESSAGE_CONTENT" }),
      ])
    );
  });

  it("maps Python SSE text and safe reasoning into AG-UI events", async () => {
    const infoSpy = vi.spyOn(console, "info").mockImplementation(() => undefined);
    streamMessageMock.mockResolvedValue(
      responseFromSseFrames([
        pythonSse({
          data: { payload: { safe: true, text: "Reading conversation context" } },
          event: "model.reasoning.delta",
          id: "evt_reasoning",
        }),
        pythonSse({
          data: { payload: { delta: "Hello" } },
          event: "message.delta",
          id: "evt_delta_1",
        }),
        pythonSse({
          data: { payload: { delta: " world" } },
          event: "message.delta",
          id: "evt_delta_2",
        }),
        pythonSse({
          data: { payload: { status: "completed" }, run_id: "run_1" },
          event: "run.completed",
          id: "evt_done",
        }),
      ])
    );

    const events = await postCopilotRun({
      forwardedProps: {
        conversationId: "conv_1",
        language: "en",
        mode: "agent",
        webSearch: "on",
      },
      messages: [{ content: [{ text: "hello", type: "text" }], role: "user" }],
      runId: "run_client",
    });

    expect(streamMessageMock).toHaveBeenCalledWith(
      "conv_1",
      { content: "hello", language: "en", web_search: "on" },
      expect.objectContaining({
        idempotencyKey: "run_client",
        mode: "agent",
        requestId: expect.stringMatching(/^req_/),
        traceId: expect.stringMatching(/^trace_/),
      })
    );
    expect(events.map((event) => event.type)).toEqual([
      "RUN_STARTED",
      "STATE_SNAPSHOT",
      "TEXT_MESSAGE_START",
      "REASONING_START",
      "REASONING_MESSAGE_START",
      "REASONING_MESSAGE_CONTENT",
      "TEXT_MESSAGE_CONTENT",
      "TEXT_MESSAGE_CONTENT",
      "CUSTOM",
      "TEXT_MESSAGE_END",
      "REASONING_MESSAGE_END",
      "REASONING_END",
      "RUN_FINISHED",
    ]);
    expect(events.filter((event) => event.type === "CUSTOM")).toHaveLength(1);
    expect(events.filter((event) => event.type === "CUSTOM")).toEqual([
      expect.objectContaining({ name: "strategy.runEvent" }),
    ]);
    expect(events.filter((event) => event.type === "TEXT_MESSAGE_CONTENT")).toEqual([
      expect.objectContaining({ delta: "Hello" }),
      expect.objectContaining({ delta: " world" }),
    ]);
    expect(infoSpy).toHaveBeenCalledWith(
      expect.stringContaining("event=copilot.run.finished")
    );
    expect(infoSpy).toHaveBeenCalledWith(expect.stringContaining("copilot_run_id=run_client"));
    expect(infoSpy).toHaveBeenCalledWith(expect.stringContaining("request_id=req_"));
    expect(infoSpy).toHaveBeenCalledWith(expect.stringContaining("trace_id=trace_"));
    expect(infoSpy).toHaveBeenCalledWith(expect.stringContaining("custom_event_count=1"));
    expect(infoSpy).toHaveBeenCalledWith(expect.stringContaining("agent_status=completed"));
    expect(infoSpy).toHaveBeenCalledWith(expect.stringContaining("status=success"));
  });

  it("logs backend run failures as failed agent outcomes with failure code", async () => {
    const infoSpy = vi.spyOn(console, "info").mockImplementation(() => undefined);
    streamMessageMock.mockResolvedValue(
      responseFromSseFrames([
        pythonSse({
          data: {
            payload: {
              code: "pine_validation_failed",
              dimension: "workflow",
              error: "ToolExecutionError",
              message: "Backtest plan failed because local Pine validation failed.",
            },
            run_id: "run_backend",
          },
          event: "run.failed",
          id: "evt_failed",
        }),
      ])
    );

    await postCopilotRun({
      forwardedProps: { conversationId: "conv_1", language: "en", mode: "agent" },
      messages: [{ content: "backtest for it again", role: "user" }],
      runId: "run_client",
    });

    expect(infoSpy).toHaveBeenCalledWith(
      expect.stringContaining("event=backend.sse.event")
    );
    expect(infoSpy).toHaveBeenCalledWith(
      expect.stringContaining("failure_code=pine_validation_failed")
    );
    expect(infoSpy).toHaveBeenCalledWith(
      expect.stringContaining("event=copilot.run.finished")
    );
    expect(infoSpy).toHaveBeenCalledWith(expect.stringContaining("agent_status=failed"));
    expect(infoSpy).toHaveBeenCalledWith(expect.stringContaining("status=backend_failed"));
  });

  it("preserves forwarded conversation id for new-chat sends and normalizes invalid options", async () => {
    streamMessageMock.mockResolvedValue(
      responseFromSseFrames([
        pythonSse({
          data: { payload: { delta: "Created" } },
          event: "message.delta",
        }),
      ])
    );

    await postCopilotRun({
      forwardedProps: {
        clientRequestId: "req_new_chat",
        conversationId: "conv_created_before_send",
        language: "vi",
        mode: "unknown",
        webSearch: "bad-value",
      },
      messages: [{ content: "xin chao", role: "user" }],
      threadId: "thread_1",
    });

    expect(streamMessageMock).toHaveBeenCalledWith(
      "conv_created_before_send",
      { content: "xin chao", language: "vi", web_search: "auto" },
      expect.objectContaining({
        idempotencyKey: "req_new_chat",
        mode: "agent",
        requestId: "req_new_chat",
        traceId: expect.stringMatching(/^trace_/),
      })
    );
  });

  it("maps market, source, and suggestion events into named AG-UI custom events", async () => {
    streamMessageMock.mockResolvedValue(
      responseFromSseFrames([
        pythonSse({
          data: { payload: { intent: "market_snapshot" } },
          event: "chat.response_intent",
        }),
        pythonSse({
          data: {
            payload: {
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
          event: "chat.market_snapshot",
        }),
        pythonSse({
          data: {
            payload: {
              sources: [
                {
                  id: "doc-1",
                  title: "Docs",
                  type: "external",
                  url: "https://example.com",
                },
              ],
            },
          },
          event: "web.sources",
        }),
        pythonSse({
          data: {
            payload: {
              actions: [
                {
                  action: "send_prompt",
                  category: "market",
                  enabled: true,
                  id: "compare-btc",
                  kind: "chat_action",
                  label: "Compare with ETH",
                  priority: 1,
                  prompt: "Compare BTC with ETH.",
                },
              ],
              composer_blocks: [],
              version: 1,
            },
          },
          event: "chat.suggestions.updated",
        }),
        pythonSse({
          data: { payload: { delta: "BTC update" } },
          event: "message.delta",
        }),
      ])
    );

    const events = await postCopilotRun({
      forwardedProps: { conversationId: "conv_1", language: "en" },
      messages: [{ content: "analyze BTC", role: "user" }],
    });
    const customEvents = events.filter((event) => event.type === "CUSTOM");

    expect(customEvents).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ name: "strategy.responseIntent" }),
        expect.objectContaining({ name: "strategy.marketSnapshot" }),
        expect.objectContaining({ name: "strategy.sources" }),
        expect.objectContaining({ name: "strategy.suggestions" }),
      ])
    );
    expect(events.filter((event) => event.type === "TEXT_MESSAGE_CONTENT")).toEqual([
      expect.objectContaining({ delta: "BTC update" }),
    ]);
  });

  it("unwraps CopilotKit single-endpoint agent/run envelopes", async () => {
    streamMessageMock.mockResolvedValue(
      responseFromSseFrames([
        pythonSse({
          data: { payload: { delta: "Created" } },
          event: "message.delta",
        }),
      ])
    );

    await postCopilotRun({
      body: {
        forwardedProps: {
          conversationId: "conv_from_forwarded_props",
          language: "en",
          mode: "agent",
          webSearch: "auto",
        },
        messages: [{ content: "hello from envelope", role: "user" }],
        runId: "client_run",
        threadId: "conv_from_forwarded_props",
      },
      method: "agent/run",
      params: { agentId: "strategy-codebot" },
    });

    expect(streamMessageMock).toHaveBeenCalledWith(
      "conv_from_forwarded_props",
      { content: "hello from envelope", language: "en", web_search: "auto" },
      expect.objectContaining({
        idempotencyKey: "client_run",
        mode: "agent",
        traceId: expect.stringMatching(/^trace_/),
      })
    );
  });

  it("ignores CopilotKit runs that do not include a backend conversation id", async () => {
    const route = await importRouteWithTimeouts();

    const response = await route.POST(
      new Request("http://localhost/api/copilotkit-chat", {
        body: JSON.stringify({
          body: {
            messages: [{ content: "hello", role: "user" }],
            runId: "client_run",
            threadId: "random-copilot-thread",
          },
          method: "agent/run",
          params: { agentId: "strategy-codebot" },
        }),
        headers: { "Content-Type": "application/json" },
        method: "POST",
      })
    );

    expect(response.status).toBe(204);
    expect(streamMessageMock).not.toHaveBeenCalled();
  });

  it("surfaces timeout guidance without triggering CopilotKit agent errors", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    streamMessageMock.mockResolvedValue(stalledResponse());

    const events = await postCopilotRun({
      forwardedProps: { conversationId: "conv_timeout", language: "en" },
      messages: [{ content: "hello", role: "user" }],
    });

    expect(events.some((event) => event.type === "RUN_ERROR")).toBe(false);
    expect(events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          delta: expect.stringContaining("taking longer than usual"),
          type: "TEXT_MESSAGE_CONTENT",
        }),
        expect.objectContaining({
          outcome: { reason: "provider_timeout", type: "interrupted" },
          type: "RUN_FINISHED",
        }),
      ])
    );
    expect(cancelCount).toBe(1);
    expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining("event=copilot.run.failed"));
    expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining("request_id=req_"));
    expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining("trace_id=trace_"));
  });
});
