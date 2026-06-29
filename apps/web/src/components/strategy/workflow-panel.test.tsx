import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  normalizeWorkflowState,
  WORKFLOW_DEFINITIONS,
  type WorkflowDefinitions,
} from "@/lib/workflow-ui";
import { WorkflowPanel, WorkflowRail } from "./workflow-panel";

afterEach(() => cleanup());

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

describe("WorkflowPanel", () => {
  it("renders Strategy to Paper Bot workflow sections from the registry", () => {
    const workflow = normalizeWorkflowState({
      workflow_id: "strategy_bot_simulation",
      current_step: "draft_strategy_spec",
      completed_steps: ["collect_strategy_inputs"],
      required_fields: ["market", "symbol", "timeframe", "style", "risk_preference"],
      missing_fields: ["account_id"],
      evidence_status: "insufficient_evidence",
      start_allowed: false,
    });

    expect(workflow).not.toBeNull();
    render(<WorkflowPanel workflow={workflow!} />);

    expect(screen.getByText("Strategy -> Paper Bot")).toBeInTheDocument();
    expect(screen.getByText("Paper simulation only")).toBeInTheDocument();
    expect(screen.getByText("Draft strategy spec")).toBeInTheDocument();
    expect(screen.getByText("Strategy inputs")).toBeInTheDocument();
    expect(screen.getByText("Risk Preference")).toBeInTheDocument();
    expect(screen.getByText("Paper setup")).toBeInTheDocument();
    expect(screen.getByText("Waiting for fields")).toBeInTheDocument();
    expect(screen.getByText("Account Id")).toBeInTheDocument();
  });

  it("keeps the rail anchored outside the chat lane without fixed viewport positioning", () => {
    const workflow = normalizeWorkflowState({
      workflow_id: "strategy_bot_simulation",
      current_step: "draft_strategy_spec",
      completed_steps: ["collect_strategy_inputs"],
      evidence_status: "insufficient_evidence",
      start_allowed: false,
    });

    expect(workflow).not.toBeNull();
    const { container } = render(<WorkflowRail workflow={workflow!} />);
    const rail = container.querySelector("aside");

    expect(rail?.className).toContain("min-[1440px]:absolute");
    expect(rail?.className).not.toContain("fixed");
  });

  it("renders a second registered workflow without Strategy Bot-specific sections", () => {
    const workflow = normalizeWorkflowState(
      {
        workflow_id: "strategy_review",
        current_step: "review_evidence",
        completed_steps: ["collect_context"],
        required_fields: ["symbol"],
        missing_fields: ["artifact_id"],
        status: "reviewable",
      },
      TEST_WORKFLOW_DEFINITIONS
    );

    expect(workflow).not.toBeNull();
    render(<WorkflowPanel definitions={TEST_WORKFLOW_DEFINITIONS} workflow={workflow!} />);

    expect(screen.getByText("Strategy Review")).toBeInTheDocument();
    expect(screen.getByText("Reviewable")).toBeInTheDocument();
    expect(screen.getByText("Review inputs")).toBeInTheDocument();
    expect(screen.queryByText("Paper setup")).not.toBeInTheDocument();
  });

  it("does not fall back to an unrelated primary action for action gates", () => {
    const workflow = normalizeWorkflowState({
      workflow_id: "strategy_bot_simulation",
      current_step: "complete_setup_confirm_start",
      sections: [{ id: "paper_setup", component_kind: "action_gate_section" }],
      start_allowed: false,
    });

    expect(workflow).not.toBeNull();
    render(
      <WorkflowPanel
        workflow={{
          ...workflow!,
          actions: [{ id: "other_action", kind: "review", label: "Wrong action", enabled: true }],
          sections: [
            {
              ...workflow!.sections.find((section) => section.id === "paper_setup")!,
              action_id: "missing_action",
            },
          ],
        }}
      />
    );

    expect(screen.getByText("Waiting for fields")).toBeInTheDocument();
    expect(screen.queryByText("Wrong action requires confirmation.")).not.toBeInTheDocument();
  });
});
