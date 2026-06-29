import { describe, expect, it } from "vitest";

import {
  normalizeWorkflowState,
  STRATEGY_BOT_WORKFLOW_ID,
  WORKFLOW_DEFINITIONS,
  WORKFLOW_SCHEMA_VERSION,
  type WorkflowDefinitions,
} from "./workflow-ui";

const TEST_WORKFLOW_DEFINITIONS: WorkflowDefinitions = {
  ...WORKFLOW_DEFINITIONS,
  strategy_review: {
    id: "strategy_review",
    intent: "strategy_review",
    title: "Strategy Review",
    aria_label: "Strategy review workflow",
    icon_key: "checklist",
    badges: ["Review-only"],
    steps: [
      { id: "collect_context", label: "Collect context" },
      { id: "review_evidence", label: "Review evidence" },
    ],
    status_labels: {
      reviewable: { key: "reviewable", label: "Reviewable", tone: "success" },
    },
    default_status_key: "reviewable",
    allowed_section_kinds: ["field_status_section"],
    allowed_fields: ["symbol", "artifact_id"],
    sections: [
      {
        id: "review_inputs",
        component_kind: "field_status_section",
        title: "Review inputs",
        fields: ["symbol", "artifact_id"],
      },
    ],
    actions: [],
    model_guidance: ["Test-only workflow fixture."],
  },
};

describe("workflow-ui", () => {
  it("ships only production workflow definitions in the default registry", () => {
    expect(Object.keys(WORKFLOW_DEFINITIONS)).toEqual([STRATEGY_BOT_WORKFLOW_ID]);
    expect(WORKFLOW_DEFINITIONS.strategy_review).toBeUndefined();
  });

  it("normalizes legacy Strategy to Paper Bot payloads into generic workflow state", () => {
    const workflow = normalizeWorkflowState({
      workflow_id: STRATEGY_BOT_WORKFLOW_ID,
      current_step: "complete_setup_confirm_start",
      completed_steps: ["collect_strategy_inputs", "bad-step", "draft_bot_proposal"],
      required_fields: ["broker_connection_id", "account_id", "unknown_field"],
      missing_fields: ["account_id"],
      artifact_refs: { bot_proposal_id: "botp_1", ignored: 42 },
      evidence_status: "reviewable_with_caveats",
      bot_proposal_id: "botp_1",
      start_allowed: false,
    });

    expect(workflow).toMatchObject({
      schema_version: WORKFLOW_SCHEMA_VERSION,
      workflow_id: STRATEGY_BOT_WORKFLOW_ID,
      intent: "strategy_to_paper_bot_simulation",
      current_step: "complete_setup_confirm_start",
      completed_steps: ["collect_strategy_inputs", "draft_bot_proposal"],
      required_fields: ["broker_connection_id", "account_id"],
      missing_fields: ["account_id"],
      artifact_refs: { bot_proposal_id: "botp_1" },
      status: {
        key: "reviewable_with_caveats",
        label: "Reviewable with caveats",
      },
      actions: [
        expect.objectContaining({
          id: "confirm_paper_start",
          enabled: false,
          kind: "confirm_start_bot_proposal",
        }),
      ],
      sections: [
        expect.objectContaining({ component_kind: "field_status_section", id: "strategy_inputs" }),
        expect.objectContaining({ component_kind: "action_gate_section", id: "paper_setup" }),
      ],
      evidence_status: "reviewable_with_caveats",
      start_allowed: false,
    });
  });

  it("drops model-proposed sections and actions that are not in the registry", () => {
    const workflow = normalizeWorkflowState({
      workflow_id: STRATEGY_BOT_WORKFLOW_ID,
      current_step: "collect_strategy_inputs",
      evidence_status: "profitable",
      intent: "live_trading",
      sections: [
        { id: "strategy_inputs", component_kind: "field_status_section" },
        { id: "paper_setup", component_kind: "custom_adapter_section" },
        { id: "unregistered", component_kind: "field_status_section" },
      ],
      actions: [
        { id: "confirm_paper_start", enabled: true, label: "Confirm" },
        { id: "runtime_start", enabled: true, label: "Start live runtime" },
      ],
      required_fields: ["market", "live_broker_secret"],
      missing_fields: ["market", "live_broker_secret"],
      start_allowed: true,
    });

    expect(workflow?.sections).toEqual([
      expect.objectContaining({ id: "strategy_inputs", component_kind: "field_status_section" }),
    ]);
    expect(workflow?.actions).toEqual([
      expect.objectContaining({ id: "confirm_paper_start", enabled: false }),
    ]);
    expect(workflow?.required_fields).toEqual(["market"]);
    expect(workflow?.missing_fields).toEqual(["market"]);
    expect(workflow?.intent).toBe("strategy_to_paper_bot_simulation");
    expect(workflow?.evidence_status).toBe("insufficient_evidence");
    expect(workflow?.start_allowed).toBe(false);
  });

  it("normalizes a second registry workflow without Strategy Bot-specific rendering data", () => {
    const workflow = normalizeWorkflowState(
      {
        workflow_id: "strategy_review",
        current_step: "review_evidence",
        completed_steps: ["collect_context"],
        required_fields: ["symbol", "artifact_id"],
        missing_fields: ["artifact_id"],
        status: "reviewable",
      },
      TEST_WORKFLOW_DEFINITIONS
    );

    expect(workflow).toMatchObject({
      workflow_id: "strategy_review",
      intent: "strategy_review",
      current_step: "review_evidence",
      completed_steps: ["collect_context"],
      status: { key: "reviewable", label: "Reviewable" },
      sections: [expect.objectContaining({ id: "review_inputs" })],
    });
  });
});
