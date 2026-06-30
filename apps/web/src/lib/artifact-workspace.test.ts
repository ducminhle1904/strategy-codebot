import { describe, expect, it } from "vitest";

import { BACKTEST_RUN_EVENTS, type Artifact, type RunEvent } from "@/lib/backend-schemas";

import {
  backtestLiveStatusFromRunEvents,
  currentProgressStep,
  getArtifactForTab,
  getArtifactUserSummary,
  getBestArtifactForDrawer,
  getDefaultArtifactTab,
  getPrimaryArtifact,
  getUserFacingArtifacts,
  groupArtifactsByKind,
  mapRunEventsToUserSteps,
  runStatusSummary,
} from "./artifact-workspace";

const createdAt = "2026-06-17T00:00:00.000Z";

function presentation(
  overrides: Partial<Artifact["presentation"]> = {}
): Artifact["presentation"] {
  return {
    dedupe_key: "code:strategy",
    is_primary: true,
    language_hint: "pine",
    user_kind: "code",
    viewer_kind: "code",
    visibility: "user",
    ...overrides,
  };
}

function artifact(overrides: Partial<Artifact>): Artifact {
  return {
    conversation_id: "conv_1",
    created_at: createdAt,
    display_name: "Artifact",
    id: "artifact_1",
    kind: "pine_file",
    metadata_json: null,
    mime_type: "text/plain",
    owner_user_id: "user_1",
    presentation: presentation(),
    preview_summary: null,
    run_id: "run_1",
    workspace_id: "workspace_1",
    ...overrides,
  };
}

function runEvent(sequence: number, type = "progress.update", payload: RunEvent["payload"] = null): RunEvent {
  return {
    conversation_id: "conv_1",
    created_at: createdAt,
    event_id: `event_${sequence}`,
    payload,
    request_id: "req_1",
    run_id: "run_1",
    sequence,
    trace_id: "trace_1",
    type,
  };
}

describe("artifact workspace helpers", () => {
  it("selects the requested artifact before falling back to the first artifact", () => {
    const artifacts = [
      artifact({ id: "pine_1", display_name: "Pine draft" }),
      artifact({
        id: "review_1",
        display_name: "Review report",
        kind: "review_report",
        presentation: presentation({
          dedupe_key: "report:review",
          is_primary: false,
          language_hint: "json",
          user_kind: "report",
          viewer_kind: "backtest_plan",
        }),
      }),
    ];

    expect(getPrimaryArtifact(artifacts, "review_1")?.display_name).toBe("Review report");
    expect(getPrimaryArtifact(artifacts, "missing")?.display_name).toBe("Pine draft");
    expect(getPrimaryArtifact([], null)).toBeNull();
  });

  it("keeps artifact tabs focused on pine code unless a dashboard is available", () => {
    const artifacts = [
      artifact({ id: "pine_1", display_name: "strategy.pine", kind: "pine_file" }),
      artifact({
        id: "review_1",
        display_name: "Review report",
        kind: "review_report",
        presentation: presentation({
          dedupe_key: "report:review",
          is_primary: false,
          language_hint: "json",
          user_kind: "report",
          viewer_kind: "json",
        }),
      }),
      artifact({
        id: "validation_1",
        display_name: "Validation report",
        kind: "validation_report",
        presentation: presentation({
          dedupe_key: "validation:report",
          is_primary: false,
          language_hint: "json",
          user_kind: "validation",
          viewer_kind: "json",
        }),
      }),
    ];

    expect(getArtifactForTab(artifacts, null, "strategy")).toBeNull();
    expect(getArtifactForTab(artifacts, "pine_1", "strategy")).toBeNull();
    expect(getArtifactForTab(artifacts, null, "code")?.id).toBe("pine_1");
    expect(getArtifactForTab(artifacts, null, "validation")).toBeNull();
  });

  it("chooses the best drawer artifact by dashboard, then pine code", () => {
    const artifacts = [
      artifact({
        id: "validation_1",
        kind: "validation_report",
        presentation: presentation({
          dedupe_key: "validation:report",
          is_primary: false,
          language_hint: "json",
          user_kind: "validation",
          viewer_kind: "json",
        }),
      }),
      artifact({
        id: "review_1",
        kind: "review_report",
        presentation: presentation({
          dedupe_key: "report:review",
          is_primary: false,
          language_hint: "json",
          user_kind: "report",
          viewer_kind: "json",
        }),
      }),
      artifact({ id: "pine_1", display_name: "strategy.pine", kind: "pine_file" }),
    ];

    expect(getBestArtifactForDrawer(artifacts)?.id).toBe("pine_1");
    expect(getBestArtifactForDrawer(artifacts.filter((item) => item.id !== "pine_1"))).toBeNull();
    expect(getDefaultArtifactTab(artifacts, "pine_1")).toBe("code");
    expect(getDefaultArtifactTab(artifacts, "validation_1")).toBe("strategy");
    expect(getDefaultArtifactTab(artifacts, "review_1")).toBe("strategy");
  });

  it("opens backtest metrics before code artifacts when a backtest report is available", () => {
    const artifacts = [
      artifact({
        id: "plan_1",
        kind: "backtest_plan",
        presentation: presentation({
          dedupe_key: "report:backtest_plan",
          is_primary: false,
          language_hint: "json",
          user_kind: "report",
          viewer_kind: "json",
        }),
      }),
      artifact({ id: "pine_1", display_name: "strategy.pine", kind: "pine_file" }),
      artifact({
        id: "dashboard_1",
        display_name: "backtest-dashboard.json",
        kind: "backtest_dashboard",
        presentation: presentation({
          dedupe_key: "dashboard:backtest",
          language_hint: "json",
          user_kind: "dashboard",
          viewer_kind: "backtest_dashboard",
        }),
      }),
      artifact({
        id: "report_1",
        display_name: "backtest-report.json",
        kind: "backtest_report",
        presentation: presentation({
          dedupe_key: "report:backtest",
          is_primary: false,
          language_hint: "json",
          user_kind: "report",
          viewer_kind: "backtest_report",
        }),
      }),
      artifact({
        id: "trades_1",
        display_name: "trades.json",
        kind: "backtest_trades",
        presentation: presentation({
          dedupe_key: "raw:trades",
          is_primary: false,
          language_hint: "json",
          user_kind: "raw",
          viewer_kind: "trades",
        }),
      }),
    ];

    expect(getBestArtifactForDrawer(artifacts)?.id).toBe("dashboard_1");
    expect(getBestArtifactForDrawer(artifacts.filter((item) => item.id !== "dashboard_1"))?.id).toBe("report_1");
  });

  it("honors preferred report artifacts while keeping dashboard as the default", () => {
    const artifacts = [
      artifact({
        id: "dashboard_1",
        display_name: "backtest-dashboard.json",
        kind: "backtest_dashboard",
        presentation: presentation({
          dedupe_key: "dashboard:backtest",
          language_hint: "json",
          user_kind: "dashboard",
          viewer_kind: "backtest_dashboard",
        }),
      }),
      artifact({
        id: "robustness_1",
        display_name: "robustness-report.json",
        kind: "robustness_report",
        presentation: presentation({
          dedupe_key: "report:robustness",
          is_primary: false,
          language_hint: "json",
          user_kind: "report",
          viewer_kind: "backtest_report",
        }),
      }),
    ];

    expect(getBestArtifactForDrawer(artifacts)?.id).toBe("dashboard_1");
    expect(getBestArtifactForDrawer(artifacts, { preferredKind: "robustness_report" })?.id).toBe("robustness_1");
  });

  it("maps raw artifact kinds to readable user summaries", () => {
    expect(getArtifactUserSummary(artifact({ kind: "pine_file" }))).toMatchObject({
      kind: "code",
      label: "Code artifact",
    });
    expect(getArtifactUserSummary(artifact({
      category: "report",
      kind: "validation_report",
      presentation: presentation({
        dedupe_key: "validation:summary",
        language_hint: "json",
        user_kind: "validation",
        viewer_kind: "json",
      }),
    }))).toMatchObject({
      kind: "validation",
      label: "Validation summary",
    });
    expect(getArtifactUserSummary(artifact({
      kind: "risk_report",
      presentation: presentation({
        dedupe_key: "risk:report",
        language_hint: "json",
        user_kind: "risk",
        viewer_kind: "json",
      }),
    }))).toMatchObject({
      kind: "risk",
      label: "Risk notes",
    });
  });

  it("groups only drawer-visible artifacts", () => {
    const grouped = groupArtifactsByKind([
      artifact({ id: "pine_1", kind: "pine_file" }),
      artifact({
        id: "review_1",
        kind: "review_report",
        presentation: presentation({
          dedupe_key: "report:review",
          is_primary: false,
          language_hint: "json",
          user_kind: "report",
          viewer_kind: "json",
        }),
      }),
      artifact({
        id: "validation_1",
        kind: "validation_report",
        presentation: presentation({
          dedupe_key: "validation:report",
          is_primary: false,
          language_hint: "json",
          user_kind: "validation",
          viewer_kind: "json",
        }),
      }),
      artifact({
        id: "trace_1",
        kind: "runtime_trace_summary",
        visibility: "internal",
        presentation: presentation({
          dedupe_key: "raw:trace",
          is_primary: false,
          language_hint: "json",
          user_kind: "raw",
          viewer_kind: "json",
          visibility: "internal",
        }),
      }),
    ]);

    expect(grouped.code.map((item) => item.id)).toEqual(["pine_1"]);
    expect(grouped.notes.map((item) => item.id)).toEqual([]);
    expect(grouped.validation.map((item) => item.id)).toEqual([]);
  });

  it("filters internal evidence artifacts out of the user workspace", () => {
    expect(
      getUserFacingArtifacts([
        artifact({ id: "pine_1", kind: "pine_file", display_name: "strategy.pine" }),
        artifact({
          id: "harness_1",
          kind: "harness_evidence_summary",
          display_name: "harness-evidence-summary.json",
          visibility: "internal",
          presentation: presentation({
            dedupe_key: "raw:harness",
            is_primary: false,
            language_hint: "json",
            user_kind: "raw",
            viewer_kind: "json",
            visibility: "internal",
          }),
        }),
        artifact({
          id: "trace_1",
          kind: "runtime_trace_summary",
          display_name: "runtime-trace.json",
          visibility: "internal",
          presentation: presentation({
            dedupe_key: "raw:trace",
            is_primary: false,
            language_hint: "json",
            user_kind: "raw",
            viewer_kind: "json",
            visibility: "internal",
          }),
        }),
      ]).map((item) => item.id)
    ).toEqual(["pine_1"]);
  });

  it("filters raw audit artifacts using backend presentation metadata", () => {
    expect(
      getUserFacingArtifacts([
        artifact({
          id: "legacy_trace",
          kind: "runtime_trace_summary",
          display_name: "runtime-trace.json",
          presentation: presentation({
            dedupe_key: "raw:trace",
            is_primary: false,
            language_hint: "json",
            user_kind: "raw",
            viewer_kind: "json",
          }),
        }),
      ]).map((item) => item.id)
    ).toEqual([]);
  });

  it("keeps only pine code and dashboard from backtest artifacts", () => {
    expect(
      getUserFacingArtifacts([
        artifact({ id: "pine_1", kind: "pine_file", display_name: "strategy.pine" }),
        artifact({ id: "pine_2", kind: "pine_file", display_name: "strategy.pine" }),
        artifact({
          id: "validation_1",
          kind: "validation_report",
          display_name: "validation.json",
          presentation: presentation({
            dedupe_key: "validation:backtest",
            is_primary: false,
            language_hint: "json",
            user_kind: "validation",
            viewer_kind: "json",
          }),
        }),
        artifact({
          id: "plan_1",
          kind: "backtest_plan",
          display_name: "Backtest plan",
          presentation: presentation({
            dedupe_key: "report:backtest_plan",
            is_primary: false,
            language_hint: "json",
            user_kind: "report",
            viewer_kind: "backtest_plan",
          }),
        }),
        artifact({
          id: "dashboard_1",
          kind: "backtest_dashboard",
          display_name: "backtest-dashboard.json",
          presentation: presentation({
            dedupe_key: "dashboard:backtest",
            language_hint: "json",
            user_kind: "dashboard",
            viewer_kind: "backtest_dashboard",
          }),
        }),
        artifact({
          id: "report_1",
          kind: "backtest_report",
          display_name: "backtest-report.json",
          presentation: presentation({
            dedupe_key: "report:backtest",
            is_primary: false,
            language_hint: "json",
            user_kind: "report",
            viewer_kind: "backtest_report",
          }),
        }),
        artifact({
          id: "trades_1",
          kind: "backtest_trades",
          display_name: "trades.json",
          presentation: presentation({
            dedupe_key: "raw:trades",
            is_primary: false,
            language_hint: "json",
            user_kind: "raw",
            viewer_kind: "trades",
          }),
        }),
        ...[
          ["equity_1", "backtest_equity_curve", "equity-curve.json"],
          ["cache_1", "candle_cache_manifest", "candle-cache-manifest.json"],
          ["ohlcv_1", "backtest_ohlcv_metadata", "ohlcv-metadata.json"],
          ["run_1", "backtest_run_metadata", "run-metadata.json"],
          ["adapter_1", "backtest_strategy_adapter_source", "strategy-adapter-source.json"],
          ["runner_1", "pineforge_runner_manifest", "pineforge-runner-manifest.json"],
        ].map(([id, kind, displayName]) =>
          artifact({
            id,
            kind,
            display_name: displayName,
            presentation: presentation({
              dedupe_key: `raw:${id}`,
              is_primary: false,
              language_hint: "json",
              user_kind: "raw",
              viewer_kind: "json",
            }),
          })
        ),
      ]).map((item) => item.id)
    ).toEqual(["pine_1", "dashboard_1"]);
  });

  it("uses backend presentation metadata for artifact visibility and grouping", () => {
    const artifacts = [
      artifact({
        id: "code_1",
        display_name: "generated.txt",
        kind: "custom_strategy_source",
        presentation: {
          dedupe_key: "code:strategy",
          is_primary: true,
          language_hint: "pine",
          user_kind: "code",
          viewer_kind: "code",
          visibility: "user",
        },
      }),
      artifact({
        id: "dashboard_1",
        display_name: "dashboard-data.json",
        kind: "custom_dashboard_payload",
        presentation: {
          dedupe_key: "dashboard:latest",
          is_primary: true,
          language_hint: "json",
          user_kind: "dashboard",
          viewer_kind: "backtest_dashboard",
          visibility: "user",
        },
      }),
      artifact({
        id: "raw_1",
        kind: "custom_raw_payload",
        presentation: {
          dedupe_key: "raw:1",
          is_primary: false,
          language_hint: "json",
          user_kind: "raw",
          viewer_kind: "json",
          visibility: "user",
        },
      }),
    ];

    expect(getUserFacingArtifacts(artifacts).map((item) => item.id)).toEqual([
      "code_1",
      "dashboard_1",
    ]);
    expect(getBestArtifactForDrawer(artifacts)?.id).toBe("dashboard_1");
    expect(groupArtifactsByKind(artifacts).code.map((item) => item.id)).toEqual(["code_1"]);
  });

  it("maps backend events to user-facing progress steps", () => {
    expect(mapRunEventsToUserSteps([], "running")).toEqual([
      { label: "Reading strategy", state: "waiting" },
      { label: "Generating review artifact", state: "waiting" },
      { label: "Checking review boundaries", state: "waiting" },
      { label: "Preparing files", state: "waiting" },
    ]);

    expect(mapRunEventsToUserSteps([
      runEvent(1, "stage.completed", { stage: "model", duration_ms: 10, status: "completed" }),
      runEvent(2, "stage.started", { stage: "runner", status: "running" }),
    ], "running")).toEqual([
      { label: "Reading strategy", state: "done" },
      { label: "Generating review artifact", state: "current" },
      { label: "Checking review boundaries", state: "waiting" },
      { label: "Preparing files", state: "waiting" },
    ]);

    expect(mapRunEventsToUserSteps([runEvent(1)], "completed").every((step) => step.state === "done")).toBe(true);
  });

  it("maps progress update wrappers to user-facing progress steps", () => {
    expect(
      mapRunEventsToUserSteps(
        [
          runEvent(1, "progress.update", {
            source_event_type: "stage.started",
            payload: { stage: "runner" },
          }),
          runEvent(2, "progress.update", {
            source_event_type: "stage.completed",
            payload: { stage: "runner" },
          }),
        ],
        "running"
      )[1]
    ).toEqual({ label: "Generating review artifact", state: "done" });
  });

  it("maps backtest events to user-facing progress steps", () => {
    expect(
      mapRunEventsToUserSteps(
        [
          runEvent(1, BACKTEST_RUN_EVENTS.dataPlanning),
          runEvent(2, BACKTEST_RUN_EVENTS.dataFetching, { fetch_windows_completed: 2, fetch_windows_total: 12 }),
          runEvent(3, BACKTEST_RUN_EVENTS.dataExporting),
          runEvent(4, BACKTEST_RUN_EVENTS.executionStarted),
          runEvent(5, BACKTEST_RUN_EVENTS.executionCompleted),
          runEvent(6, BACKTEST_RUN_EVENTS.indexingStarted),
        ],
        "running"
      )
    ).toEqual([
      { label: "Checking cached candles", state: "done" },
      { label: "Fetching missing 1m candles", state: "done" },
      { label: "Preparing preview input", state: "done" },
      { label: "Running backtest", state: "done" },
      { label: "Indexing report", state: "current" },
    ]);
  });

  it("parses backtest heartbeat events into live status", () => {
    const status = backtestLiveStatusFromRunEvents(
      [
        runEvent(1, "backtest.preview.heartbeat", {
          stage: "fetching",
          status: "running",
          progress_pct: 42,
          elapsed_ms: 12_000,
          eta_ms: 18_000,
          message: "Fetching missing public OHLCV candles.",
          fetch_windows_completed: 4,
          fetch_windows_total: 10,
          updated_at: createdAt,
        }),
      ],
      Date.parse(createdAt) + 1_000
    );

    expect(status).toMatchObject({
      etaMs: 18_000,
      fetchWindowsCompleted: 4,
      fetchWindowsTotal: 10,
      isStale: false,
      progressPct: 42,
      stage: "fetching",
      status: "running",
    });
  });

  it("uses presentation metadata to identify backtest live status events", () => {
    const status = backtestLiveStatusFromRunEvents([
      runEvent(1, "workflow.gate.updated", {
        card_kind: "backtest_live_status",
        stage: "reporting",
        status: "running",
        progress_pct: 88,
        message: "Preparing review-only report.",
      }),
    ]);

    expect(status).toMatchObject({
      message: "Preparing review-only report.",
      progressPct: 88,
      stage: "reporting",
      status: "running",
    });
  });

  it("falls back to approval and queued events before heartbeat arrives", () => {
    expect(
      backtestLiveStatusFromRunEvents([
        runEvent(1, "backtest.preview.approval_required", { approval_id: "approval_1" }),
      ])?.message
    ).toBe("Backtest plan is waiting for approval.");
    expect(
      backtestLiveStatusFromRunEvents([
        runEvent(1, "backtest.preview.queued", { approval_id: "approval_1", child_run_id: "run_child" }),
      ])?.runId
    ).toBe("run_child");
  });

  it("uses child terminal events after queued fallback events", () => {
    const status = backtestLiveStatusFromRunEvents([
      runEvent(1, "chat.auto_chain.waiting_for_backtest", { child_run_id: "run_child" }),
      {
        ...runEvent(2, "run.completed", { status: "completed" }),
        run_id: "run_child",
      },
    ]);

    expect(status).toMatchObject({
      message: "Backtest preview artifacts are ready.",
      progressPct: 100,
      runId: "run_child",
      stage: "completed",
      status: "completed",
    });
  });

  it("does not render a live progress status for rejected previews", () => {
    expect(
      backtestLiveStatusFromRunEvents([
        runEvent(1, "backtest.preview.rejected", { approval_id: "approval_1" }),
      ])
    ).toBeNull();
  });

  it("renders failed preview events as terminal live status", () => {
    expect(
      backtestLiveStatusFromRunEvents([
        runEvent(1, "backtest.preview.queued", { child_run_id: "run_child" }),
        runEvent(2, "backtest.preview.failed", { message: "No candles were available." }),
      ])
    ).toMatchObject({
      isStale: false,
      message: "No candles were available.",
      stage: "failed",
      status: "failed",
    });
  });

  it("sanitizes implementation names from failed preview live status", () => {
    const status = backtestLiveStatusFromRunEvents([
      runEvent(1, "backtest.preview.failed", { message: "pineforge runner compile failed" }),
    ]);

    expect(status?.message).toBe("local preview compatibility failed");
    expect(status?.message).not.toMatch(/pineforge|runner|compile/i);
  });

  it("uses newer approval events instead of stale older heartbeats", () => {
    const status = backtestLiveStatusFromRunEvents([
      runEvent(1, "backtest.preview.heartbeat", {
        stage: "completed",
        status: "completed",
        progress_pct: 100,
        message: "Old run complete.",
        updated_at: createdAt,
      }),
      runEvent(2, "backtest.preview.approval_required", { approval_id: "approval_new" }),
    ]);

    expect(status).toMatchObject({
      message: "Backtest plan is waiting for approval.",
      progressPct: 0,
      runId: "run_1",
      status: "queued",
    });
  });

  it("localizes backtest progress steps", () => {
    expect(
      mapRunEventsToUserSteps([runEvent(1, BACKTEST_RUN_EVENTS.dataPlanning)], "running", "vi")[0]
    ).toEqual({ label: "Đang kiểm tra candle cache", state: "current" });
  });

  it("summarizes run progress in user-facing copy", () => {
    const steps = mapRunEventsToUserSteps(
      [runEvent(1, "stage.started", { stage: "runner" })],
      "running"
    );

    expect(currentProgressStep(steps)?.label).toBe("Generating review artifact");
    expect(runStatusSummary("running")).toBe("Creating review artifact...");
    expect(runStatusSummary("failed")).toBe("Could not create artifact");
  });
});
