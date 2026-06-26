export const WORKFLOW_TOOL_EVENTS = {
  "artifact.regenerate.completed": {
    activityLabel: "Artifact regenerated",
    activityStatus: "complete",
    phase: "completed",
    toolName: "confirm_regenerate_artifact",
  },
  "artifact.regenerate.failed": {
    activityLabel: "Artifact regeneration failed",
    activityStatus: "failed",
    phase: "failed",
    toolName: "confirm_regenerate_artifact",
  },
  "artifact.regenerate.requested": {
    activityLabel: "Regenerating artifact",
    activityStatus: "running",
    phase: "started",
    toolName: "confirm_regenerate_artifact",
  },
  "backtest.preview.completed": {
    activityLabel: "Backtest preview ready",
    activityStatus: "complete",
    phase: "completed",
    toolName: "confirm_backtest_preview",
  },
  "backtest.preview.approval_required": {
    activityLabel: "Backtest approval required",
    activityStatus: "running",
    phase: "started",
    toolName: "confirm_backtest_preview",
  },
  "backtest.preview.approved": {
    activityLabel: "Backtest preview approved",
    activityStatus: "complete",
    phase: "completed",
    toolName: "confirm_backtest_preview",
  },
  "backtest.preview.failed": {
    activityLabel: "Backtest preview failed",
    activityStatus: "failed",
    phase: "failed",
    toolName: "confirm_backtest_preview",
  },
  "backtest.preview.queued": {
    activityLabel: "Backtest preview queued",
    activityStatus: "running",
    phase: "started",
    toolName: "confirm_backtest_preview",
  },
  "backtest.preview.rejected": {
    activityLabel: "Backtest preview skipped",
    activityStatus: "complete",
    phase: "completed",
    toolName: "confirm_backtest_preview",
  },
  "backtest.preview.requested": {
    activityLabel: "Preparing backtest preview",
    activityStatus: "running",
    phase: "started",
    toolName: "confirm_backtest_preview",
  },
  "market_context.apply.completed": {
    activityLabel: "Market context applied",
    activityStatus: "complete",
    phase: "completed",
    toolName: "confirm_apply_market_context",
  },
  "market_context.apply.failed": {
    activityLabel: "Market context failed",
    activityStatus: "failed",
    phase: "failed",
    toolName: "confirm_apply_market_context",
  },
  "market_context.apply.requested": {
    activityLabel: "Applying market context",
    activityStatus: "running",
    phase: "started",
    toolName: "confirm_apply_market_context",
  },
  "validation.repair.completed": {
    activityLabel: "Validation repair ready",
    activityStatus: "complete",
    phase: "completed",
    toolName: "confirm_validation_repair",
  },
  "validation.repair.failed": {
    activityLabel: "Validation repair failed",
    activityStatus: "failed",
    phase: "failed",
    toolName: "confirm_validation_repair",
  },
  "validation.repair.requested": {
    activityLabel: "Repairing validation",
    activityStatus: "running",
    phase: "started",
    toolName: "confirm_validation_repair",
  },
} as const satisfies Record<
  string,
  {
    activityLabel: string;
    activityStatus: "complete" | "failed" | "running";
    phase: "completed" | "failed" | "started";
    toolName: string;
  }
>;

export type WorkflowToolEventType = keyof typeof WORKFLOW_TOOL_EVENTS;

export function workflowToolEventConfig(eventType: string) {
  return WORKFLOW_TOOL_EVENTS[eventType as WorkflowToolEventType] ?? null;
}
