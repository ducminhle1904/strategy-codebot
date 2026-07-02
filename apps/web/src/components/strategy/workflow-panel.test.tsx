import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  normalizeWorkflowState,
  WORKFLOW_DEFINITIONS,
  type WorkflowDefinitions,
} from "@/lib/workflow-ui";
import { WorkflowPanel, WorkflowRail, WorkflowTaskPrompt } from "./workflow-panel";

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
      { id: "review_evidence", label: "Review evidence", optional: true, skip_label: "Not needed" },
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
    option_sets: {},
    input_request_templates: [
      {
        id: "symbol",
        field: "symbol",
        label: "Symbol",
        kind: "text",
        required: true,
      },
    ],
    task_templates: [
      {
        id: "collect_review_context",
        step_id: "collect_context",
        kind: "input_request",
        title: "Review context",
        blocking: true,
        input_request_ids: ["symbol"],
        action_ids: [],
        default_status: "pending_user",
      },
    ],
    model_guidance: ["Test-only workflow fixture."],
  },
};

describe("WorkflowPanel", () => {
  it("renders Strategy to Paper Bot workflow sections from the registry", () => {
    const workflow = normalizeWorkflowState({
      workflow_id: "strategy_bot_simulation",
      current_step: "draft_strategy_spec",
      completed_steps: ["collect_strategy_inputs"],
      skipped_steps: ["generate_pine"],
      step_reasons: { generate_pine: "User asked to skip Pine." },
      required_fields: ["market", "symbol", "timeframe", "style", "risk_preference"],
      missing_fields: ["account_id"],
      blocked_reason: "missing_strategy_inputs",
      evidence_status: "insufficient_evidence",
      start_allowed: false,
    });

    expect(workflow).not.toBeNull();
    const { container } = render(<WorkflowPanel workflow={workflow!} />);

    expect(screen.getByText("Strategy -> Paper Bot")).toBeInTheDocument();
    expect(screen.queryByText("Review gated")).not.toBeInTheDocument();
    expect(screen.queryByText("Paper simulation only")).not.toBeInTheDocument();
    expect(screen.queryByText("No broker execution")).not.toBeInTheDocument();
    expect(screen.queryByText("Review-only evidence")).not.toBeInTheDocument();
    expect(screen.queryByText("missing_strategy_inputs")).not.toBeInTheDocument();
    expect(screen.getByText("Draft strategy spec")).toBeInTheDocument();
    expect(screen.getByText("Skipped")).toBeInTheDocument();
    const skippedRow = container.querySelector('[data-workflow-step-id="generate_pine"]');
    const skippedMarker = skippedRow?.querySelector("span");
    expect(skippedRow?.getAttribute("data-workflow-step-status")).toBe("skipped");
    expect(skippedMarker?.className).not.toContain("bg-emerald-500");
    expect(screen.queryByText("Strategy inputs")).not.toBeInTheDocument();
    expect(screen.queryByText("Risk Preference")).not.toBeInTheDocument();
    expect(screen.queryByText("Paper setup")).not.toBeInTheDocument();
    expect(screen.queryByText("Waiting for fields")).not.toBeInTheDocument();
    expect(screen.queryByText("Account Id")).not.toBeInTheDocument();
  });

  it("renders the active workflow step as running while the chat run is active", () => {
    const workflow = normalizeWorkflowState({
      workflow_id: "strategy_bot_simulation",
      current_step: "draft_strategy_spec",
      completed_steps: ["collect_strategy_inputs"],
      evidence_status: "insufficient_evidence",
      start_allowed: false,
    });

    expect(workflow).not.toBeNull();
    const { container } = render(<WorkflowPanel isWorking workflow={workflow!} />);
    const currentRow = container.querySelector('[data-workflow-step-id="draft_strategy_spec"]');

    expect(currentRow?.getAttribute("data-workflow-step-status")).toBe("running");
    expect(currentRow?.querySelector(".animate-spin")).not.toBeNull();
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

  it("keeps workflow rail free of artifact and recent activity sections", () => {
    const onSelectArtifact = vi.fn();
    const workflow = normalizeWorkflowState({
      workflow_id: "strategy_bot_simulation",
      current_step: "draft_strategy_spec",
      completed_steps: ["collect_strategy_inputs"],
      required_fields: ["market", "symbol", "timeframe", "style", "risk_preference"],
      missing_fields: [],
      artifact_refs: { pine_code_artifact_id: "artifact_pine" },
      evidence_status: "insufficient_evidence",
      start_allowed: false,
    });

    expect(workflow).not.toBeNull();
    render(
      <WorkflowPanel
        activities={[
          {
            artifactLinks: [{ artifactId: "artifact_validation", label: "validation.json" }],
            description: "Review artifact is available in the workspace.",
            details: [{ label: "Status", value: "Ready" }],
            id: "evt_artifact",
            state: "output-available",
            title: "Review artifact ready",
            toolName: "artifact",
          },
        ]}
        onSelectArtifact={onSelectArtifact}
        workflow={workflow!}
      />
    );

    expect(screen.queryByText("Artifacts")).not.toBeInTheDocument();
    expect(screen.queryByText("Recent activity")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pine Code Artifact Id" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "validation.json" })).not.toBeInTheDocument();
    expect(onSelectArtifact).not.toHaveBeenCalled();
  });

  it("keeps workflow rail free of task controls and task summaries", () => {
    const workflow = normalizeWorkflowState({
      workflow_id: "strategy_bot_simulation",
      current_step: "collect_strategy_inputs",
      tasks: [
        {
          id: "wft_collect",
          task_template_id: "collect_strategy_inputs",
          status: "pending_user",
          input_request_ids: ["market", "symbol"],
          values: { market: "crypto" },
        },
      ],
      task_values: { market: "crypto" },
    });

    expect(workflow).not.toBeNull();
    render(<WorkflowRail workflow={workflow!} />);

    expect(screen.queryByText("Tasks")).not.toBeInTheDocument();
    expect(screen.queryByText("Strategy inputs")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Symbol/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Submit/i })).not.toBeInTheDocument();
  });

  it("renders task prompt controls and submits structured values", () => {
    const onSubmitTask = vi.fn();
    const workflow = normalizeWorkflowState({
      workflow_id: "strategy_bot_simulation",
      current_step: "collect_strategy_inputs",
      tasks: [
        {
          id: "wft_collect",
          task_template_id: "collect_strategy_inputs",
          status: "pending_user",
          input_request_ids: ["market", "symbol"],
          values: { market: "crypto" },
        },
      ],
      task_values: { market: "crypto" },
    });

    expect(workflow).not.toBeNull();
    render(<WorkflowTaskPrompt onSubmitTask={onSubmitTask} workflow={workflow!} />);

    expect(screen.getByText("Which symbol should the strategy watch?")).toBeInTheDocument();
    expect(screen.getByText("1 of 1")).toBeInTheDocument();
    expect(screen.queryByText("Waiting for your input")).not.toBeInTheDocument();
    expect(screen.queryByText("Human review")).not.toBeInTheDocument();
    expect(screen.queryByText("Needs input")).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("Enter another symbol"), {
      target: { value: "  SOLUSDT  " },
    });
    fireEvent.click(screen.getByRole("button", { name: /Submit/i }));

    expect(onSubmitTask).toHaveBeenCalledWith("wft_collect", {
      symbol: "SOLUSDT",
    });
  });

  it("renders the post-spec next action prompt from the workflow registry", () => {
    const onSubmitTask = vi.fn();
    const workflow = normalizeWorkflowState({
      workflow_id: "strategy_bot_simulation",
      current_step: "generate_pine",
      completed_steps: ["collect_strategy_inputs", "draft_strategy_spec"],
      tasks: [
        {
          id: "wft_next",
          task_template_id: "review_strategy_spec_next_step",
          status: "pending_user",
        },
      ],
    });

    expect(workflow).not.toBeNull();
    render(<WorkflowTaskPrompt onSubmitTask={onSubmitTask} workflow={workflow!} />);

    expect(screen.getByText("Strategy spec is ready. What should we do next?")).toBeInTheDocument();
    expect(screen.getByText("1 of 1")).toBeInTheDocument();
    expect(screen.getByText("Generate Pine Script")).toBeInTheDocument();
    expect(screen.getByText("Revise strategy spec")).toBeInTheDocument();
    expect(screen.getByText("Skip Pine")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Tell the model what to do differently")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Submit/i }));

    expect(onSubmitTask).toHaveBeenCalledWith("wft_next", {
      next_after_strategy_spec: "generate_pine",
    });
  });

  it("renders the backtest preview choice as a blocking workflow prompt", () => {
    const onSubmitTask = vi.fn();
    const workflow = normalizeWorkflowState({
      workflow_id: "strategy_bot_simulation",
      current_step: "backtest_preview",
      completed_steps: ["collect_strategy_inputs", "draft_strategy_spec", "generate_pine", "static_validation"],
      tasks: [
        {
          id: "wft_backtest_choice",
          task_template_id: "draft_only_backtest_choice",
          status: "pending_user",
        },
      ],
    });

    expect(workflow).not.toBeNull();
    render(<WorkflowTaskPrompt onSubmitTask={onSubmitTask} workflow={workflow!} />);

    expect(screen.getByText("How should we handle the backtest preview for this draft?")).toBeInTheDocument();
    expect(screen.getByText("Run preview")).toBeInTheDocument();
    expect(screen.getByText("Draft only")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Submit/i }));

    expect(onSubmitTask).toHaveBeenCalledWith("wft_backtest_choice", {
      draft_only_choice: "run_preview",
    });
  });

  it("renders a second registered workflow without Strategy Bot-specific sections", () => {
    const workflow = normalizeWorkflowState(
      {
        workflow_id: "strategy_review",
        current_step: "collect_context",
        skipped_steps: ["review_evidence"],
        step_reasons: { review_evidence: "Review not needed." },
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
    expect(screen.getByText("Not needed")).toBeInTheDocument();
    expect(screen.queryByText("Review inputs")).not.toBeInTheDocument();
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
