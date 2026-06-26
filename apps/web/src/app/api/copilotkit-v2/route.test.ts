import { afterEach, describe, expect, it, vi } from "vitest";

import { COPILOTKIT_V2_RUNTIME_URL, COPILOT_STRATEGY_AGENT_ID } from "@/lib/copilot-constants";

const streamMessageMock = vi.fn();

vi.mock("@/lib/server-auth", () => ({
  createServerBackendClient: vi.fn(async () => ({
    streamMessage: streamMessageMock,
  })),
}));

function pythonSse(event: { data: Record<string, unknown>; event: string; id?: string }) {
  const lines = [event.id ? `id: ${event.id}` : "", `event: ${event.event}`, `data: ${JSON.stringify(event.data)}`].filter(Boolean);
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

async function importRoute() {
  vi.stubEnv("STRATEGY_CODEBOT_CHAT_FIRST_EVENT_TIMEOUT_MS", "1000");
  vi.stubEnv("STRATEGY_CODEBOT_CHAT_IDLE_TIMEOUT_MS", "1000");
  vi.stubEnv("STRATEGY_CODEBOT_CHAT_TOTAL_TIMEOUT_MS", "1000");
  return import("./route");
}

async function postCopilotRun(body: Record<string, unknown>) {
  const route = await importRoute();
  const response = await route.POST(
    new Request(`http://localhost${COPILOTKIT_V2_RUNTIME_URL}`, {
      body: JSON.stringify({
        body,
        method: "agent/run",
        params: { agentId: COPILOT_STRATEGY_AGENT_ID },
      }),
      headers: { "Content-Type": "application/json" },
      method: "POST",
    })
  );
  const text = await response.text();
  return text
    .split(/\n\n/)
    .filter(Boolean)
    .map((frame) => JSON.parse(frame.replace(/^data: /, "")) as Record<string, unknown>);
}

describe(COPILOTKIT_V2_RUNTIME_URL, () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.resetModules();
    vi.clearAllMocks();
    vi.unstubAllEnvs();
  });

  it("returns strategy runtime metadata with adapter probe marker", async () => {
    const route = await importRoute();

    const response = await route.GET();

    await expect(response.json()).resolves.toMatchObject({
      agents: {
        [COPILOT_STRATEGY_AGENT_ID]: {
          capabilities: expect.objectContaining({
            customEvents: true,
            tools: expect.objectContaining({ clientProvided: true }),
          }),
        },
      },
      mode: "sse",
      copilotkit_v2_adapter: {
        adapter: "ag-ui-sse",
        agent: COPILOT_STRATEGY_AGENT_ID,
        mode: "single-route",
        status: "adapter_probe",
      },
    });
    expect(streamMessageMock).not.toHaveBeenCalled();
  });

  it("keeps missing backend conversation ids as safe no-op runs", async () => {
    const route = await importRoute();

    const response = await route.POST(
      new Request(`http://localhost${COPILOTKIT_V2_RUNTIME_URL}`, {
        body: JSON.stringify({
          body: {
            forwardedProps: {},
            messages: [{ content: "hello", role: "user" }],
            runId: "run_client",
          },
          method: "agent/run",
          params: { agentId: COPILOT_STRATEGY_AGENT_ID },
        }),
        headers: { "Content-Type": "application/json" },
        method: "POST",
      })
    );

    expect(response.status).toBe(204);
    expect(streamMessageMock).not.toHaveBeenCalled();
  });

  it("streams AG-UI events through the existing Strategy backend bridge", async () => {
    streamMessageMock.mockResolvedValue(
      responseFromSseFrames([
        pythonSse({
          data: { payload: { delta: "Hello" } },
          event: "message.delta",
          id: "evt_delta_1",
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
      expect.objectContaining({ mode: "agent" })
    );
    expect(events.map((event) => event.type)).toEqual([
      "RUN_STARTED",
      "STATE_SNAPSHOT",
      "TEXT_MESSAGE_START",
      "TEXT_MESSAGE_CONTENT",
      "CUSTOM",
      "TEXT_MESSAGE_END",
      "RUN_FINISHED",
    ]);
  });
});
