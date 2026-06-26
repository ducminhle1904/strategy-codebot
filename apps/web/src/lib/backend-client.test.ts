import { describe, expect, it, vi } from "vitest";

import { BackendClient, buildBackendHeaders, parseBackendSseEvents } from "./backend-client";
import {
  BACKTEST_RUN_EVENT_TYPES,
  BacktestApprovalDecisionRequestSchema,
  BacktestConfigSchema,
  ConversationStateResponseSchema,
  KNOWN_RUN_EVENT_TYPES,
  RunCreateSchema,
} from "./backend-schemas";
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
      expect.arrayContaining([...BACKTEST_RUN_EVENT_TYPES])
    );
    expect(KNOWN_RUN_EVENT_TYPES).toEqual(
      expect.arrayContaining([
        "backtest.preview.approval_required",
        "backtest.preview.approved",
        "backtest.preview.rejected",
        "backtest.preview.queued",
      ])
    );
    expect(BacktestApprovalDecisionRequestSchema.parse({ decision: "approved" }).decision).toBe(
      "approved"
    );
  });

  it("validates executable backtest config bounds", () => {
    expect(
      BacktestConfigSchema.parse({
        engine: "pineforge",
        symbol: "BTC/USDT",
        timeframe: "30m",
        candle_timeframe: "1m",
        start: "2024-01-01",
        end: "2024-02-01",
        initial_capital: 10000,
        fee_bps: 10,
        slippage_bps: 5,
        data_source: "public-readonly-cache",
      }).timeframe
    ).toBe("30m");
    expect(
      BacktestConfigSchema.parse({
        engine: "pineforge",
        exchange: "okx",
        symbol: "BTC/USDT",
        timeframe: "1h",
        start: "2024-01-01",
        end: "2024-02-01",
        initial_capital: 10000,
      }).exchange
    ).toBe("okx");
    expect(() =>
      BacktestConfigSchema.parse({
        engine: "pineforge",
        exchange: "coinbase",
        symbol: "BTC/USDT",
        timeframe: "1h",
        start: "2024-01-01",
        end: "2024-02-01",
        initial_capital: 10000,
      })
    ).toThrow();

    expect(() =>
      BacktestConfigSchema.parse({
        engine: "pineforge",
        symbol: "BTC/USDT",
        timeframe: "4h",
        candle_timeframe: "1m",
        start: "2024-01-01",
        end: "2024-02-01",
        initial_capital: 10000,
        fee_bps: 10,
        slippage_bps: 5,
        data_source: "public-readonly-cache",
      })
    ).toThrow();
    expect(() =>
      BacktestConfigSchema.parse({
        engine: "pineforge",
        symbol: "BTC/USDT",
        timeframe: "1h",
        candle_timeframe: "1m",
        start: "2024-02-01",
        end: "2024-01-01",
        initial_capital: 10000,
        fee_bps: 1001,
        slippage_bps: 5,
        data_source: "public-readonly-cache",
      })
    ).toThrow();
    expect(
      BacktestConfigSchema.parse({
        engine: "pineforge",
        symbol: "BTC/USDT",
        timeframe: "1h",
        start: "2024-01-01",
        end: "2024-02-01",
        initial_capital: 10000,
        fee_bps: 10,
        slippage_bps: 5,
        data_source: "public-readonly-cache",
      }).candle_timeframe
    ).toBe("1m");
    expect(() =>
      BacktestConfigSchema.parse({
        engine: "pineforge",
        symbol: "BTC/USDT",
        timeframe: "1h",
        candle_timeframe: "5m",
        start: "2024-01-01",
        end: "2024-02-01",
        initial_capital: 10000,
        fee_bps: 10,
        slippage_bps: 5,
        data_source: "public-readonly-cache",
      })
    ).toThrow();
  });

  it("requires Pine source for PineForge backtest-preview run creation", () => {
    const backtestConfig = {
      engine: "pineforge" as const,
      symbol: "BTC/USDT",
      timeframe: "1h" as const,
      start: "2024-01-01",
      end: "2024-02-01",
      initial_capital: 10000,
    };

    expect(() =>
      RunCreateSchema.parse({
        conversation_id: "conv_1",
        strategy_spec: validStrategySpec,
        mode: "backtest-preview",
        backtest_config: backtestConfig,
      })
    ).toThrow();
    expect(
      RunCreateSchema.parse({
        conversation_id: "conv_1",
        strategy_spec: validStrategySpec,
        pine_code: "//@version=6\nstrategy(\"x\")",
        mode: "backtest-preview",
        backtest_config: backtestConfig,
      }).pine_code
    ).toContain("strategy");
  });

  it("builds tenant headers through the shared header helper", () => {
    const headers = buildBackendHeaders({
      body: { ok: true },
      createOperation: true,
      idempotencyKeyFactory: () => "idem_1",
      internalAuthSecret: "secret",
      lastEventId: "evt_1",
      requestId: "req_1",
      traceId: "trace_1",
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
    expect(headers.get("X-Request-Id")).toBe("req_1");
    expect(headers.get("X-Trace-Id")).toBe("trace_1");
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

  it("parses unavailable readiness responses without treating them as request failures", async () => {
    const fetcher = vi.fn(async () =>
      jsonResponse(
        {
          status: "unavailable",
          checks: {
            pineforge_runner: {
              reason: "runner unavailable",
              status: "unavailable",
            },
          },
        },
        { status: 503 }
      )
    );
    const client = new BackendClient({
      baseUrl: "/api/backend",
      fetcher,
      userId: "usr_1",
      workspaceId: "wsp_1",
    });

    await expect(client.ready()).resolves.toEqual({
      status: "unavailable",
      checks: {
        pineforge_runner: {
          reason: "runner unavailable",
          status: "unavailable",
        },
      },
    });
  });

  it("fetches backend action registry metadata", async () => {
    const fetcher = vi.fn(async () =>
      jsonResponse({
        version: 1,
        actions: [
          {
            id: "build-robustness-report",
            tool_id: "build_robustness_report",
            label: "Robustness Report",
            prompt: "Build a review-only robustness report.",
            category: "review",
            risk_level: "read_only",
            next_state: "robustness_review",
            artifact_kind: "robustness_report",
            available: true,
            presentation: {
              badge_key: "read_only",
              icon_key: "checklist",
              visibility_key: "default",
            },
          },
        ],
      })
    );
    const client = new BackendClient({
      baseUrl: "/api/backend",
      fetcher,
      userId: "usr_1",
      workspaceId: "wsp_1",
    });

    await expect(client.getActionRegistry()).resolves.toMatchObject({
      actions: [
        {
          presentation: { icon_key: "checklist" },
          tool_id: "build_robustness_report",
        },
      ],
    });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/backend/v1/action-registry",
      expect.objectContaining({ method: "GET" })
    );
  });

  it("fetches paginated conversation artifacts with preview summaries", async () => {
    const fetcher = vi.fn(async () =>
      jsonResponse({
        items: [
          {
            category: "report",
            conversation_id: "conv_1",
            created_at: isoNow,
            display_name: "backtest-dashboard.json",
            id: "artifact_1",
            kind: "backtest_dashboard",
            metadata_json: null,
            mime_type: "application/json",
            owner_user_id: "usr_1",
            presentation: {
              dedupe_key: "backtest_dashboard:backtest-dashboard.json",
              is_primary: true,
              language_hint: "json",
              user_kind: "dashboard",
              viewer_kind: "backtest_dashboard",
              visibility: "user",
            },
            preview_summary: {
              kind: "backtest_result",
              metrics: { net_pnl: -1 },
              symbol: "BNBUSDT",
              timeframe: "1h",
            },
            run_id: "run_1",
            visibility: "user",
            workspace_id: "wsp_1",
          },
        ],
        next_cursor: "cursor_2",
      })
    );
    const client = new BackendClient({
      baseUrl: "/api/backend",
      fetcher,
      userId: "usr_1",
      workspaceId: "wsp_1",
    });

    await expect(
      client.listConversationArtifacts("conv_1", {
        cursor: "cursor_1",
        limit: 25,
      })
    ).resolves.toMatchObject({
      items: [
        {
          preview_summary: { kind: "backtest_result" },
        },
      ],
      next_cursor: "cursor_2",
    });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/backend/v1/conversations/conv_1/artifacts?cursor=cursor_1&limit=25",
      expect.objectContaining({ method: "GET" })
    );
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

    await client.createConversation(
      { title: " Draft " },
      { idempotencyKey: "idem_create", requestId: "req_create", traceId: "trace_create" }
    );

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
    expect(headers.get("Idempotency-Key")).toBe("idem_create");
    expect(headers.get("X-Request-Id")).toBe("req_create");
    expect(headers.get("X-Trace-Id")).toBe("trace_create");
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

    await client.updateConversationTitle(
      "cnv_1",
      { title: "  Renamed  " },
      { requestId: "req_patch", traceId: "trace_patch" }
    );

    expect(fetcher).toHaveBeenCalledWith(
      "https://api.example.test/v1/conversations/cnv_1",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ title: "Renamed" }),
      })
    );
    const init = fetcher.mock.calls[0]?.[1] as RequestInit | undefined;
    const headers = new Headers(init?.headers);
    expect(headers.get("X-Request-Id")).toBe("req_patch");
    expect(headers.get("X-Trace-Id")).toBe("trace_patch");
    expect(headers.get("Idempotency-Key")).toBeNull();
  });

  it("passes stream correlation headers through shared stream options", async () => {
    const fetcher = vi.fn(async () => new Response("event: ping\ndata: {}\n\n"));
    const client = new BackendClient({
      baseUrl: "https://api.example.test",
      fetcher,
    });

    await client.streamRunEvents("run_1", {
      lastEventId: "evt_9",
      requestId: "req_stream",
      traceId: "trace_stream",
    });

    expect(fetcher).toHaveBeenCalledWith(
      "https://api.example.test/v1/runs/run_1/events",
      expect.objectContaining({ method: "GET" })
    );
    const init = fetcher.mock.calls[0]?.[1] as RequestInit | undefined;
    const headers = new Headers(init?.headers);
    expect(headers.get("Last-Event-ID")).toBe("evt_9");
    expect(headers.get("X-Request-Id")).toBe("req_stream");
    expect(headers.get("X-Trace-Id")).toBe("trace_stream");
  });

  it("posts backtest approval decisions to the backend approval endpoint", async () => {
    const fetcher = vi.fn(async () =>
      jsonResponse({
        approval_id: "approval_1",
        conversation_id: "cnv_1",
        decision: "approved",
        status: "queued",
        run_id: "run_2",
        job_id: "job_1",
        backtest_config: { symbol: "BTC/USDT", timeframe: "1h" },
      })
    );
    const client = new BackendClient({
      baseUrl: "https://api.example.test",
      userId: "usr_1",
      workspaceId: "wsp_1",
      fetcher,
    });

    const result = await client.decideBacktestApproval(
      "cnv_1",
      "approval_1",
      { decision: "approved" }
    );

    expect(result.status).toBe("queued");
    expect(fetcher).toHaveBeenCalledWith(
      "https://api.example.test/v1/conversations/cnv_1/backtest-approvals/approval_1",
      expect.objectContaining({
        body: JSON.stringify({ decision: "approved" }),
        method: "POST",
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

  it("lists workspace artifacts with cursor pagination", async () => {
    const fetcher = vi.fn(async () =>
      jsonResponse({
        items: [
          {
            conversation_id: "conv_1",
            created_at: isoNow,
            display_name: "strategy.pine",
            id: "art_1",
            kind: "pine_file",
            metadata_json: null,
            mime_type: "text/x-pine",
            owner_user_id: "usr_1",
            presentation: {
              dedupe_key: "code:strategy.pine",
              is_primary: true,
              language_hint: "pine",
              user_kind: "code",
              viewer_kind: "code",
              visibility: "user",
            },
            preview_summary: null,
            run_id: "run_1",
            storage_key: "runs/run_1/strategy.pine",
            workspace_id: "wsp_1",
          },
        ],
        next_cursor: null,
      })
    );
    const client = new BackendClient({
      baseUrl: "/api/backend",
      fetcher,
      userId: "usr_1",
      workspaceId: "wsp_1",
    });

    await expect(
      client.listWorkspaceArtifacts({ cursor: "cursor_1", limit: 20 })
    ).resolves.toMatchObject({
      items: [{ id: "art_1", kind: "pine_file" }],
      next_cursor: null,
    });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/backend/v1/artifacts?cursor=cursor_1&limit=20",
      expect.any(Object)
    );
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
