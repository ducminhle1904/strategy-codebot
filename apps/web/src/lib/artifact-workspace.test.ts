import { describe, expect, it } from "vitest";

import type { Artifact, RunEvent } from "@/lib/backend-schemas";

import {
  currentProgressStep,
  getArtifactForTab,
  getArtifactUserSummary,
  getPrimaryArtifact,
  getUserFacingArtifacts,
  groupArtifactsByKind,
  mapRunEventsToUserSteps,
  runStatusSummary,
} from "./artifact-workspace";

const createdAt = "2026-06-17T00:00:00.000Z";

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
      artifact({ id: "review_1", display_name: "Review report", kind: "review_report" }),
    ];

    expect(getPrimaryArtifact(artifacts, "review_1")?.display_name).toBe("Review report");
    expect(getPrimaryArtifact(artifacts, "missing")?.display_name).toBe("Pine draft");
    expect(getPrimaryArtifact([], null)).toBeNull();
  });

  it("chooses user-facing strategy and review artifacts before code artifacts", () => {
    const artifacts = [
      artifact({ id: "pine_1", display_name: "strategy.pine", kind: "pine_file" }),
      artifact({ id: "review_1", display_name: "Review report", kind: "review_report" }),
      artifact({ id: "validation_1", display_name: "Validation report", kind: "validation_report" }),
    ];

    expect(getArtifactForTab(artifacts, null, "strategy")?.id).toBe("review_1");
    expect(getArtifactForTab(artifacts, "pine_1", "strategy")?.id).toBe("review_1");
    expect(getArtifactForTab(artifacts, null, "code")?.id).toBe("pine_1");
    expect(getArtifactForTab(artifacts, null, "validation")?.id).toBe("validation_1");
  });

  it("maps raw artifact kinds to readable user summaries", () => {
    expect(getArtifactUserSummary(artifact({ kind: "pine_file" }))).toMatchObject({
      kind: "code",
      label: "Code artifact",
    });
    expect(getArtifactUserSummary(artifact({ category: "report", kind: "validation_report" }))).toMatchObject({
      kind: "validation",
      label: "Validation summary",
    });
    expect(getArtifactUserSummary(artifact({ kind: "risk_report" }))).toMatchObject({
      kind: "risk",
      label: "Risk notes",
    });
  });

  it("groups review and validation reports separately from code artifacts", () => {
    const grouped = groupArtifactsByKind([
      artifact({ id: "pine_1", kind: "pine_file" }),
      artifact({ id: "review_1", kind: "review_report" }),
      artifact({ id: "validation_1", kind: "validation_report" }),
      artifact({ id: "trace_1", kind: "runtime_trace_summary", visibility: "internal" }),
    ]);

    expect(grouped.code.map((item) => item.id)).toEqual(["pine_1"]);
    expect(grouped.notes.map((item) => item.id)).toEqual(["review_1", "validation_1"]);
    expect(grouped.validation.map((item) => item.id)).toEqual(["validation_1"]);
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
        }),
        artifact({
          id: "trace_1",
          kind: "runtime_trace_summary",
          display_name: "runtime-trace.json",
          visibility: "internal",
        }),
      ]).map((item) => item.id)
    ).toEqual(["pine_1"]);
  });

  it("keeps old artifacts user-facing when backend visibility is absent", () => {
    expect(
      getUserFacingArtifacts([
        artifact({ id: "legacy_trace", kind: "runtime_trace_summary", display_name: "runtime-trace.json" }),
      ]).map((item) => item.id)
    ).toEqual(["legacy_trace"]);
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
