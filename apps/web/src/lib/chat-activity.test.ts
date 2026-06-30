import { describe, expect, it } from "vitest";

import type { RunEvent } from "@/lib/backend-schemas";
import {
  AUTO_CHAIN_SUMMARY_PENDING_EVENT,
  AUTO_CHAIN_SUMMARY_TIMEOUT_EVENT,
} from "@/lib/auto-chain-continuation";

import {
  CHAT_ACTIVITY_COVERED_EVENT_TYPES,
  KNOWN_CHAT_ACTIVITY_EVENT_TYPES,
  mapRunEventsToChatActivities,
} from "./chat-activity";

const createdAt = "2026-06-18T00:00:00.000Z";

function runEvent(type: string, payload: RunEvent["payload"] = null, runId = "run_1"): RunEvent {
  return {
    conversation_id: "conv_1",
    created_at: createdAt,
    event_id: `evt_${type.replaceAll(".", "_")}`,
    payload,
    request_id: "req_1",
    run_id: runId,
    sequence: 1,
    trace_id: "trace_1",
    type,
  };
}

describe("chat activity mapper", () => {
  it("maps backend run events to user-facing activities", () => {
    const activities = mapRunEventsToChatActivities([
      runEvent("message.delta", { text: "hello" }),
      runEvent("model.reasoning.delta", {
        phase: "context",
        safe: true,
        text: "Reading conversation context.",
      }),
      runEvent("provider.started", { model: "free-model" }),
      runEvent("tool.started", { label: "Read strategy context", tool_id: "strategy_context" }),
      runEvent("validation.completed", { status: "passed" }),
      runEvent("review.completed", { decision: "reviewed" }),
      runEvent("run.completed", { status: "completed" }),
    ]);

    expect(activities.map((activity) => activity.title)).toEqual([
      "Read strategy context",
      "Checked review boundaries",
      "Review notes prepared",
    ]);
    expect(activities.every((activity) => activity.title !== "message.delta")).toBe(true);
    expect(activities.every((activity) => activity.title !== "Starting model")).toBe(true);
    expect(activities.every((activity) => activity.title !== "Response ready")).toBe(true);
    expect(activities.every((activity) => activity.toolName !== "reasoning")).toBe(true);
  });

  it("prefers backend presentation metadata over event-name factories", () => {
    const activities = mapRunEventsToChatActivities([
      runEvent("tool.started", {
        activity_label: "Registry activity label",
        activity_state: "running",
        label: "Old tool label",
        tool_id: "generate_pine",
        tool_name: "registry_tool",
      }),
    ]);

    expect(activities[0]).toMatchObject({
      state: "input-available",
      title: "Registry activity label",
      toolName: "registry_tool",
    });
  });

  it("renders run failures as error activities", () => {
    expect(
      mapRunEventsToChatActivities([
        runEvent("run.failed", { error: "AuthenticationError", message: "Provider execution failed" }),
      ])[0]
    ).toMatchObject({
      errorText: "Provider execution failed",
      state: "output-error",
      title: "Response failed",
    });
  });

  it("renders provider timeout failures as timeout activities", () => {
    expect(
      mapRunEventsToChatActivities([
        runEvent("run.failed", {
          code: "provider_timeout",
          error: "ProviderTimeoutError",
          message: "The AI provider took too long to respond.",
        }),
      ])[0]
    ).toMatchObject({
      errorText: "The AI provider took too long to respond.",
      state: "output-error",
      title: "Provider timed out",
    });
  });

  it("renders Pine validation failures as backtest plan failure activities", () => {
    expect(
      mapRunEventsToChatActivities([
        runEvent("run.failed", {
          code: "pine_validation_failed",
          dimension: "workflow",
          message: "Backtest plan failed because local Pine validation failed.",
          pine_code_artifact_id: "artifact_pine",
          validation_artifact_id: "artifact_validation",
        }),
      ])[0]
    ).toMatchObject({
      artifactLinks: [
        { artifactId: "artifact_pine", label: "Open Pine code" },
        { artifactId: "artifact_validation", label: "Open validation report" },
      ],
      errorText: "Backtest plan failed because local Pine validation failed.",
      state: "output-error",
      title: "Backtest plan failed",
      toolName: "backtest",
    });
  });

  it("renders local auto-chain summary continuation states", () => {
    const activities = mapRunEventsToChatActivities([
      runEvent(AUTO_CHAIN_SUMMARY_PENDING_EVENT, { child_run_id: "run_child" }),
      runEvent(AUTO_CHAIN_SUMMARY_TIMEOUT_EVENT, { child_run_id: "run_child" }),
    ]);

    expect(activities.map((activity) => activity.title)).toEqual([
      "Preparing summary",
      "Summary still pending",
    ]);
  });

  it("renders auto-chain child backtest progress", () => {
    const activities = mapRunEventsToChatActivities([
      runEvent("backtest.data.planning", { child_run_id: "run_child" }),
      runEvent("backtest.data.fetching", { child_run_id: "run_child" }),
      runEvent("backtest.data.exporting", { child_run_id: "run_child" }),
      runEvent("backtest.execution.started", { child_run_id: "run_child" }),
      runEvent("backtest.indexing.started", { child_run_id: "run_child" }),
      runEvent("backtest.report.completed", { child_run_id: "run_child" }),
    ]);

    expect(activities.map((activity) => activity.title)).toEqual([
      "Fetching missing 1m candles",
      "Preparing preview input",
      "Running local preview",
      "Indexing report",
      "Preview report ready",
    ]);
  });

  it("dedupes repeated artifact and report readiness rows", () => {
    const activities = mapRunEventsToChatActivities([
      runEvent("artifact.created", {
        artifact_id: "art_1",
        display_name: "strategy.pine",
      }),
      runEvent("artifact.created", {
        artifact_id: "art_1",
        display_name: "strategy.pine",
      }),
      runEvent("backtest.report.completed"),
      runEvent("backtest.report.completed"),
    ]);

    expect(activities.map((activity) => activity.title)).toEqual([
      "Review artifact ready",
      "Preview report ready",
    ]);
  });

  it("renders failed completed tools as error activities", () => {
    expect(
      mapRunEventsToChatActivities([
        runEvent("tool.completed", {
          error: "RuntimeError",
          label: "Generating review artifact",
          message: "tool backend unavailable",
          output_summary: "Tool failed: RuntimeError",
          status: "failed",
          tool_id: "generate_pine",
        }),
      ])[0]
    ).toMatchObject({
      errorText: "tool backend unavailable",
      state: "output-error",
      title: "Generating review artifact failed",
      toolName: "generate_pine",
    });
  });

  it("sanitizes implementation names from failed tool activity messages", () => {
    const activities = mapRunEventsToChatActivities([
      runEvent("tool.completed", {
        label: "Run preview",
        message: "pineforge runner compile failed",
        status: "failed",
        tool_id: "run_backtest_preview",
      }),
    ]);

    expect(activities[0]).toMatchObject({
      errorText: "local preview compatibility failed",
      state: "output-error",
    });
    expect(JSON.stringify(activities)).not.toMatch(/pineforge|runner|compile/i);
  });

  it("merges started and completed events for the same tool", () => {
    const activities = mapRunEventsToChatActivities([
      runEvent("tool.started", {
        input_summary: "{'prompt': '[REDACTED]'}",
        label: "Check knowledge context",
        tool_id: "knowledge_check",
      }),
      runEvent("tool.completed", {
        label: "Check knowledge context",
        output_summary: "{'knowledge_context': {'index_ref': 'postgres://internal'}}",
        tool_id: "knowledge_check",
        tool_user_summary: "Checked knowledge context: 1 internal docs, 2 retrieved chunks, 1 external refs.",
      }),
    ]);

    expect(activities).toHaveLength(1);
    expect(activities[0]).toMatchObject({
      description: "Checked knowledge context: 1 internal docs, 2 retrieved chunks, 1 external refs.",
      details: [
        { label: "Status", value: "Complete" },
        { label: "Tool", value: "Check knowledge context" },
      ],
      state: "output-available",
      title: "Check knowledge context",
      toolName: "knowledge_check",
    });
    expect(JSON.stringify(activities[0].details)).not.toContain("postgres://internal");
  });

  it("uses backend registry labels for tool activity fallback copy", () => {
    const activities = mapRunEventsToChatActivities(
      [
        runEvent("tool.completed", {
          output_summary: "Artifact ready.",
          tool_id: "build_robustness_report",
        }),
      ],
      "en",
      new Map([
        [
          "build_robustness_report",
          {
            available: true,
            artifact_kind: "robustness_report",
            category: "backtest",
            id: "robustness",
            label: "Backend robustness report",
            next_state: "robustness_ready",
            presentation: {},
            prompt: "Build robustness.",
            risk_level: "read_only",
            tool_id: "build_robustness_report",
          },
        ],
      ])
    );

    expect(activities[0]?.title).toBe("Backend robustness report");
  });

  it("shows market research as one safe user-facing tool activity", () => {
    const activities = mapRunEventsToChatActivities([
      runEvent("tool.started", {
        input_summary: "Search current public sources and collect citations.",
        label: "Search market sources",
        tool_id: "market_research",
      }),
      runEvent("tool.completed", {
        label: "Market research ready",
        output_summary: "Market research: 2 cited source(s).",
        tool_id: "market_research",
        tool_user_summary: "Market research ready: 2 cited source(s).",
      }),
    ]);

    expect(activities).toHaveLength(1);
    expect(activities[0]).toMatchObject({
      description: "Market research ready: 2 cited source(s).",
      state: "output-available",
      title: "Market research ready",
      toolName: "market_research",
    });
    expect(JSON.stringify(activities[0])).not.toContain("chain");
  });

  it("maps bot-boundary tool ids to action-aware activity labels", () => {
    const registry = {
      build_robustness_report: {
        available: true,
        category: "backtest",
        id: "build_robustness_report",
        label: "Building robustness report",
        next_state: "robustness_ready",
        presentation: {},
        prompt: "Build robustness report.",
        risk_level: "read_only",
        tool_id: "build_robustness_report",
      },
      create_proposed_intent: {
        available: true,
        category: "strategy",
        id: "create_proposed_intent",
        label: "Proposed intent ready",
        next_state: "intent_ready",
        presentation: {},
        prompt: "Create proposed intent.",
        risk_level: "review_required",
        tool_id: "create_proposed_intent",
      },
      run_backtest_preview: {
        available: true,
        category: "backtest",
        id: "run_backtest_preview",
        label: "Preparing preview evidence",
        next_state: "preview_queued",
        presentation: {},
        prompt: "Run backtest preview.",
        risk_level: "review_required",
        tool_id: "run_backtest_preview",
      },
      run_backtest_variant_lab: {
        available: true,
        category: "backtest",
        id: "run_backtest_variant_lab",
        label: "Queueing variant lab",
        next_state: "variant_lab_queued",
        presentation: {},
        prompt: "Run variant lab.",
        risk_level: "review_required",
        tool_id: "run_backtest_variant_lab",
      },
      run_risk_gate: {
        available: true,
        category: "risk",
        id: "run_risk_gate",
        label: "Checking risk gate",
        next_state: "risk_gate_ready",
        presentation: {},
        prompt: "Run risk gate.",
        risk_level: "read_only",
        tool_id: "run_risk_gate",
      },
    } as const;
    const activities = mapRunEventsToChatActivities([
      runEvent("tool.started", {
        input_summary: "Draft review-only intent.",
        tool_id: "create_proposed_intent",
      }),
      runEvent("tool.completed", {
        output_summary: "Intent artifact ready.",
        tool_id: "create_proposed_intent",
      }),
      runEvent("tool.started", {
        input_summary: "Check deterministic risk controls.",
        tool_id: "run_risk_gate",
      }),
      runEvent("tool.started", {
        input_summary: "Queue comparable variants.",
        tool_id: "run_backtest_variant_lab",
      }),
      runEvent("tool.started", {
        input_summary: "Queue local preview evidence.",
        tool_id: "run_backtest_preview",
      }),
      runEvent("tool.started", {
        input_summary: "Build robustness review.",
        tool_id: "build_robustness_report",
      }),
    ], "en", registry);

    expect(activities).toHaveLength(5);
    expect(activities[0]).toMatchObject({
      title: "Proposed intent ready",
      toolName: "create_proposed_intent",
    });
    expect(activities[1]).toMatchObject({
      title: "Checking risk gate",
      toolName: "run_risk_gate",
    });
    expect(activities[2]).toMatchObject({
      title: "Queueing variant lab",
      toolName: "run_backtest_variant_lab",
    });
    expect(activities[3]).toMatchObject({
      title: "Preparing preview evidence",
      toolName: "run_backtest_preview",
    });
    expect(activities[4]).toMatchObject({
      title: "Building robustness report",
      toolName: "build_robustness_report",
    });
    expect(JSON.stringify(activities)).not.toContain("chain");
  });

  it("hides technical completed tool summaries from the main description", () => {
    const activities = mapRunEventsToChatActivities([
      runEvent("tool.completed", {
        label: "Check knowledge context",
        output_summary: "{\"knowledge_context\":{\"index_ref\":\"postgres://internal\"}}",
        tool_id: "knowledge_check",
      }),
    ]);

    expect(activities[0]).toMatchObject({
      description: "Tool output is ready.",
    });
    expect(JSON.stringify(activities[0])).not.toContain("postgres://internal");
  });

  it("maps workflow HITL events from the shared workflow config", () => {
    const activities = mapRunEventsToChatActivities([
      runEvent("backtest.preview.approval_required", { approval_id: "approval_1" }),
      runEvent("backtest.preview.queued", { approval_id: "approval_1", child_run_id: "run_2" }),
      runEvent("backtest.preview.rejected", { approval_id: "approval_2" }),
      runEvent("validation.repair.failed", { message: "Validation still blocked" }),
    ]);

    expect(activities.map((activity) => activity.title)).toEqual([
      "Backtest approval required",
      "Backtest preview queued",
      "Backtest preview skipped",
      "Validation repair failed",
    ]);
    expect(activities.at(-1)).toMatchObject({
      state: "output-error",
      toolName: "confirm_validation_repair",
    });
  });

  it("dedupes backtest heartbeat activities by run", () => {
    const activities = mapRunEventsToChatActivities([
      runEvent("backtest.preview.heartbeat", {
        stage: "fetching",
        status: "running",
        message: "Fetching missing public OHLCV candles.",
      }),
      runEvent("backtest.preview.heartbeat", {
        stage: "executing",
        status: "running",
        message: "Running the local preview engine.",
      }),
    ]);

    expect(activities).toHaveLength(1);
    expect(activities[0]).toMatchObject({
      description: "Running the local preview.",
      state: "input-available",
      title: "Running local preview",
    });
  });

  it("sanitizes implementation names from visible preview failure activity", () => {
    const activities = mapRunEventsToChatActivities([
      runEvent("backtest.failed", { message: "pineforge runner compile failed" }),
    ]);

    expect(activities[0]).toMatchObject({
      description: "local preview compatibility failed",
      state: "output-error",
    });
    expect(JSON.stringify(activities)).not.toMatch(/pineforge|runner|compile/i);
  });

  it("dedupes repeated artifact-ready activity rows", () => {
    const activities = mapRunEventsToChatActivities([
      runEvent("artifact.created", { artifact_id: "artifact_1", display_name: "strategy.pine" }),
      runEvent("artifact.created", { artifact_id: "artifact_2", display_name: "validation.json" }),
    ]);

    expect(activities).toHaveLength(1);
    expect(activities[0].title).toBe("Review artifact ready");
    expect(activities[0].artifactLinks).toEqual([
      { artifactId: "artifact_1", label: "strategy.pine" },
      { artifactId: "artifact_2", label: "validation.json" },
    ]);
  });

  it("keeps every known event type mapped or explicitly ignored", () => {
    expect(
      KNOWN_CHAT_ACTIVITY_EVENT_TYPES.filter(
        (eventType) => !CHAT_ACTIVITY_COVERED_EVENT_TYPES.has(eventType)
      )
    ).toEqual([]);
  });

  it("renders provider route and usage events without exposing raw aliases", () => {
    const activities = mapRunEventsToChatActivities([
      runEvent("provider.route", {
        attempt_count: 2,
        fallback_used: true,
        model: "paid-high-secret-alias",
        provider_route: "litellm_proxy/paid_high.pine_code_generation",
      }),
      runEvent("classifier.route", {
        classifier_name: "chat_intent_decision",
        model: "paid-low-secret-alias",
        provider: "litellm_proxy",
        provider_route: "litellm_proxy/paid_low.strategy_reasoning_gemini_lite",
        stage: "classifier",
        status: "route",
      }),
      runEvent("model.usage", { total_tokens: 42 }),
    ]);

    expect(activities.map((activity) => activity.title)).toEqual([
      "Trying fallback route",
      "Classifier route selected",
      "Model usage recorded",
    ]);
    expect(JSON.stringify(activities)).not.toContain("paid_high");
    expect(JSON.stringify(activities)).not.toContain("paid_low");
    expect(JSON.stringify(activities)).not.toContain("paid-high-secret-alias");
    expect(JSON.stringify(activities)).not.toContain("paid-low-secret-alias");
    expect(JSON.stringify(activities)).not.toContain("litellm_proxy");
  });

  it("dedupes mirrored auto-chain summary completion by child run", () => {
    const activities = mapRunEventsToChatActivities([
      runEvent("chat.auto_chain.summary.completed", { backtest_run_id: "run_child" }, "run_source"),
      runEvent("chat.auto_chain.summary.completed", { backtest_run_id: "run_child" }, "run_child"),
    ]);

    expect(activities).toHaveLength(1);
    expect(activities[0]?.title).toBe("Backtest summary ready");
  });

  it("maps auto-chain backtest events to user-facing progress", () => {
    const activities = mapRunEventsToChatActivities([
      runEvent("chat.auto_chain.started", {}),
      runEvent("chat.auto_chain.step.completed", { tool_id: "create_backtest_plan" }),
      runEvent("chat.auto_chain.waiting_for_backtest", { child_run_id: "run_child" }),
      runEvent("chat.auto_chain.summary.completed", { backtest_run_id: "run_child" }),
    ]);

    expect(activities.map((activity) => activity.title)).toEqual([
      "Backtest workflow started",
      "Backtest workflow advanced",
      "Backtest queued",
      "Backtest summary ready",
    ]);
    expect(activities[1].description).toBe("Backtest plan is validated.");
    expect(JSON.stringify(activities)).not.toContain("run_child");
  });

  it("keeps stage events out of the chat transcript", () => {
    expect(
      mapRunEventsToChatActivities([
        runEvent("stage.started", { stage: "model" }),
        runEvent("model.usage", { total_tokens: 42 }),
        runEvent("run.cancelled", { reason: "api_cancelled" }),
      ]).map((activity) => activity.title)
    ).toEqual(["Model usage recorded", "Response cancelled"]);
  });
});
