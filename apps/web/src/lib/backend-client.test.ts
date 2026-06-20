import { describe, expect, it, vi } from "vitest";

import { BackendClient, buildBackendHeaders, parseBackendSseEvents } from "./backend-client";
import { ConversationStateResponseSchema, KNOWN_RUN_EVENT_TYPES, RunCreateSchema } from "./backend-schemas";
import type { StrategySpec } from "./backend-schemas";

const isoNow = "2026-06-17T12:00:00+00:00";
const validStrategySpec: StrategySpec = {
  target_platform: "pine_v6",
  script_type: "strategy",
  market: "crypto",
  timeframe: "1h",
  entry_rules: ["Enter when fast SMA crosses above slow SMA"],
  exit_rules: ["Exit when fast SMA crosses below slow SMA"],
  risk_rules: ["Attach a stop-loss before review"],
};

describe("BackendClient", () => {
  it("defaults run creation web search to auto", () => {
    const payload = RunCreateSchema.parse({
      conversation_id: "conv_1",
      strategy_spec: validStrategySpec,
    });

    expect(payload.web_search).toBe("auto");
  });

  it("keeps backtest progress events in the known event contract", () => {
    expect(KNOWN_RUN_EVENT_TYPES).toEqual(
      expect.arrayContaining([
        "backtest.queued",
        "backtest.data.started",
        "backtest.data.completed",
        "backtest.execution.started",
        "backtest.execution.completed",
        "backtest.report.completed",
        "backtest.failed",
      ])
    );
  });

  it("builds tenant headers through the shared header helper", () => {
    const headers = buildBackendHeaders({
      body: { ok: true },
      createOperation: true,
      idempotencyKeyFactory: () => "idem_1",
      internalAuthSecret: "secret",
      lastEventId: "evt_1",
      requestId: "req_1",
      userId: "user_1",
      userTier: "free",
      workspaceId: "workspace_1",
      workspaceRole: "owner",
    });

    expect(headers.get("X-User-Id")).toBe("user_1");
    expect(headers.get("X-Workspace-Id")).toBe("workspace_1");
    expect(headers.get("X-User-Tier")).toBe("free");
    expect(headers.get("X-Workspace-Role")).toBe("owner");
    expect(headers.get("X-Strategy-Codebot-Internal-Secret")).toBe("secret");
    expect(headers.get("Idempotency-Key")).toBe("idem_1");
    expect(headers.get("Last-Event-ID")).toBe("evt_1");
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("binds the default fetcher for browser runtimes", async () => {
    const originalFetch = globalThis.fetch;
    const fetchCalls: Array<[Parameters<typeof fetch>[0], Parameters<typeof fetch>[1]?]> = [];

    globalThis.fetch = vi.fn(function (
      this: typeof globalThis,
      input: Parameters<typeof fetch>[0],
      init?: Parameters<typeof fetch>[1]
    ) {
      expect(this).toBe(globalThis);
      fetchCalls.push([input, init]);
      return Promise.resolve(
        jsonResponse({
          status: "ok",
          checks: {},
        })
      );
    }) as typeof fetch;

    try {
      const client = new BackendClient({
        baseUrl: "/api/backend",
        userId: "usr_1",
        workspaceId: "wsp_1",
      });

      await client.ready();

      expect(fetchCalls[0]?.[0]).toBe("/api/backend/ready");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("injects tenant and idempotency headers for create operations", async () => {
    const fetcher = vi.fn(
      async (input: Parameters<typeof fetch>[0], init?: Parameters<typeof fetch>[1]) => {
        void input;
        void init;
        return jsonResponse({
          id: "cnv_1",
          owner_user_id: "usr_1",
          workspace_id: "wsp_1",
          title: "Draft",
          created_at: isoNow,
          updated_at: isoNow,
        });
      }
    );
    const client = new BackendClient({
      baseUrl: "https://api.example.test",
      userId: "usr_1",
      workspaceId: "wsp_1",
      fetcher,
      idempotencyKeyFactory: () => "idem_1",
    });

    await client.createConversation({ title: " Draft " });

    expect(fetcher).toHaveBeenCalledWith(
      "https://api.example.test/v1/conversations",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ title: "Draft" }),
      })
    );
    const init = fetcher.mock.calls[0]?.[1] as RequestInit | undefined;
    const headers = new Headers(init?.headers);
    expect(headers.get("X-User-Id")).toBe("usr_1");
    expect(headers.get("X-Workspace-Id")).toBe("wsp_1");
    expect(headers.get("Idempotency-Key")).toBe("idem_1");
  });

  it("updates conversation titles with normalized payloads", async () => {
    const fetcher = vi.fn(async () =>
      jsonResponse({
        id: "cnv_1",
        owner_user_id: "usr_1",
        workspace_id: "wsp_1",
        title: "Renamed",
        created_at: isoNow,
        updated_at: isoNow,
      })
    );
    const client = new BackendClient({
      baseUrl: "https://api.example.test",
      userId: "usr_1",
      workspaceId: "wsp_1",
      fetcher,
    });

    await client.updateConversationTitle("cnv_1", { title: "  Renamed  " });

    expect(fetcher).toHaveBeenCalledWith(
      "https://api.example.test/v1/conversations/cnv_1",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ title: "Renamed" }),
      })
    );
  });

  it("deletes conversations through the backend API", async () => {
    const fetcher = vi.fn(async () =>
      jsonResponse({
        id: "cnv_1",
        owner_user_id: "usr_1",
        workspace_id: "wsp_1",
        title: "Deleted",
        created_at: isoNow,
        updated_at: isoNow,
      })
    );
    const client = new BackendClient({
      baseUrl: "https://api.example.test",
      userId: "usr_1",
      workspaceId: "wsp_1",
      fetcher,
    });

    await client.deleteConversation("cnv_1");

    expect(fetcher).toHaveBeenCalledWith(
      "https://api.example.test/v1/conversations/cnv_1",
      expect.objectContaining({ method: "DELETE" })
    );
  });

  it("fetches account usage through the typed backend client", async () => {
    const fetcher = vi.fn(
      async (input: Parameters<typeof fetch>[0], init?: Parameters<typeof fetch>[1]) => {
        void input;
        void init;
        return jsonResponse({
        artifacts: 1,
        estimated_cost_usd: null,
        input_tokens: 20,
        messages: 3,
        output_tokens: 10,
        period_end: isoNow,
        period_start: isoNow,
        runs: 2,
        tier: "free",
        tier_label: "Free",
        total_tokens: 30,
        });
      }
    );
    const client = new BackendClient({
      baseUrl: "https://api.example.test",
      fetcher,
      userId: "usr_1",
      workspaceId: "wsp_1",
    });

    const usage = await client.getAccountUsage();

    expect(fetcher).toHaveBeenCalledWith(
      "https://api.example.test/v1/account/usage",
      expect.objectContaining({ method: "GET" })
    );
    const init = fetcher.mock.calls[0]?.[1] as RequestInit | undefined;
    const headers = new Headers(init?.headers);
    expect(headers.get("X-User-Id")).toBe("usr_1");
    expect(headers.get("X-Workspace-Id")).toBe("wsp_1");
    expect(usage.total_tokens).toBe(30);
  });

  it("validates run creation requests before sending", async () => {
    const fetcher = vi.fn();
    const client = new BackendClient({
      userId: "usr_1",
      workspaceId: "wsp_1",
      fetcher,
    });

    expect(() =>
      client.createRun({
        conversation_id: "cnv_1",
        strategy_spec: validStrategySpec,
        mode: "unsupported" as "dry-run",
      })
    ).toThrow();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("validates strategy specs before sending run creation requests", async () => {
    const fetcher = vi.fn();
    const client = new BackendClient({
      userId: "usr_1",
      workspaceId: "wsp_1",
      fetcher,
    });

    expect(() =>
      client.createRun({
        conversation_id: "cnv_1",
        strategy_spec: {} as StrategySpec,
      })
    ).toThrow();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("accepts persisted latest run events in conversation state responses", () => {
    const parsed = ConversationStateResponseSchema.parse({
      conversation: {
        created_at: isoNow,
        id: "cnv_1",
        owner_user_id: "usr_1",
        title: "Strategy review",
        updated_at: isoNow,
        workspace_id: "wsp_1",
      },
      feedback_targets: {
        artifact_ids: [],
        categories: [],
        conversation_id: "cnv_1",
        latest_run_id: "run_1",
        message_ids: [],
        ratings: [],
      },
      latest_run: {
        conversation_id: "cnv_1",
        created_at: isoNow,
        id: "run_1",
        owner_user_id: "usr_1",
        request_id: null,
        retry_of_run_id: null,
        status: "failed",
        trace_id: "trace_1",
        updated_at: isoNow,
        workspace_id: "wsp_1",
      },
      latest_run_artifacts: [],
      latest_run_events: [
        {
          conversation_id: "cnv_1",
          created_at: isoNow,
          event_id: "evt_1",
          payload: { message: "Provider execution failed" },
          request_id: null,
          run_id: "run_1",
          sequence: 1,
          trace_id: "trace_1",
          type: "run.failed",
        },
      ],
      messages: [],
      message_count: 0,
      messages_truncated: false,
      message_limit: 100,
    });

    expect(parsed.latest_run_events[0]?.type).toBe("run.failed");
    expect(parsed.message_limit).toBe(100);
  });

  it("surfaces FastAPI validation details from failed requests", async () => {
    const fetcher = vi.fn(async () =>
      jsonResponse(
        { detail: "'target_platform' is a required property" },
        { status: 422 }
      )
    );
    const client = new BackendClient({
      userId: "usr_1",
      workspaceId: "wsp_1",
      fetcher,
    });

    await expect(
      client.createRun({
        conversation_id: "cnv_1",
        strategy_spec: validStrategySpec,
      })
    ).rejects.toThrow(
      "Backend request failed: 'target_platform' is a required property"
    );
  });

  it("parses and validates backend SSE event payloads", () => {
    expect(
      parseBackendSseEvents(
        [
          "id: evt_1",
          "event: progress.snapshot",
          `data: ${JSON.stringify({
            event_id: "evt_1",
            conversation_id: "cnv_1",
            run_id: "run_1",
            request_id: null,
            trace_id: null,
            sequence: 0,
            type: "progress.snapshot",
            payload: {
              status: "running",
              event_count: 0,
              artifact_count: 0,
            },
            created_at: isoNow,
          })}`,
          "",
        ].join("\n")
      )
    ).toEqual([
      expect.objectContaining({
        event_id: "evt_1",
        run_id: "run_1",
        type: "progress.snapshot",
      }),
    ]);
  });
});

function jsonResponse(payload: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: {
      "Content-Type": "application/json",
    },
    ...init,
  });
}
