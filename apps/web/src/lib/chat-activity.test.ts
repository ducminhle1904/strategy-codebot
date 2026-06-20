import { describe, expect, it } from "vitest";

import type { RunEvent } from "@/lib/backend-schemas";

import {
  CHAT_ACTIVITY_COVERED_EVENT_TYPES,
  KNOWN_CHAT_ACTIVITY_EVENT_TYPES,
  mapRunEventsToChatActivities,
} from "./chat-activity";

const createdAt = "2026-06-18T00:00:00.000Z";

function runEvent(type: string, payload: RunEvent["payload"] = null): RunEvent {
  return {
    conversation_id: "conv_1",
    created_at: createdAt,
    event_id: `evt_${type.replaceAll(".", "_")}`,
    payload,
    request_id: "req_1",
    run_id: "run_1",
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
      state: "output-available",
      title: "Check knowledge context",
      toolName: "knowledge_check",
    });
    expect(activities[0]).not.toHaveProperty("details");
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

  it("keeps every known event type mapped or explicitly ignored", () => {
    expect(
      KNOWN_CHAT_ACTIVITY_EVENT_TYPES.filter(
        (eventType) => !CHAT_ACTIVITY_COVERED_EVENT_TYPES.has(eventType)
      )
    ).toEqual([]);
  });

  it("keeps technical usage and stage events out of the chat transcript", () => {
    expect(
      mapRunEventsToChatActivities([
        runEvent("stage.started", { stage: "model" }),
        runEvent("model.usage", { total_tokens: 42 }),
        runEvent("run.cancelled", { reason: "api_cancelled" }),
      ]).map((activity) => activity.title)
    ).toEqual(["Response cancelled"]);
  });
});
