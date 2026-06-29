import {
  WORKFLOW_COMPONENT_KINDS as GENERATED_WORKFLOW_COMPONENT_KINDS,
  WORKFLOW_DEFINITIONS as GENERATED_WORKFLOW_DEFINITIONS,
  WORKFLOW_SCHEMA_VERSION as GENERATED_WORKFLOW_SCHEMA_VERSION,
} from "./workflow-registry-contract";

export const WORKFLOW_SCHEMA_VERSION = GENERATED_WORKFLOW_SCHEMA_VERSION;

export const STRATEGY_BOT_WORKFLOW_ID = "strategy_bot_simulation" as const;

export const WORKFLOW_COMPONENT_KINDS = GENERATED_WORKFLOW_COMPONENT_KINDS;

export type StrategyWorkflowEvidenceStatus =
  | "insufficient_evidence"
  | "reviewable_with_caveats"
  | "needs_validation_or_robustness_check";

export type WorkflowComponentKind = (typeof WORKFLOW_COMPONENT_KINDS)[number];

export type WorkflowStatusTone = "neutral" | "success" | "warning" | "danger";

export type WorkflowStatus = {
  key: string;
  label: string;
  tone: WorkflowStatusTone;
};

export type WorkflowAction = {
  id: string;
  kind: "confirm_start_bot_proposal" | "review" | "custom";
  label: string;
  enabled: boolean;
  disabled_reason?: string;
  target_ref?: string;
};

export type WorkflowSection = {
  id: string;
  component_kind: WorkflowComponentKind;
  title?: string;
  fields?: string[];
  ref_keys?: string[];
  pending_label?: string;
  ready_label?: string;
  locked_message?: string;
  action_id?: string;
  body?: string;
};

export type WorkflowStepDefinition = {
  id: string;
  label: string;
};

export type WorkflowDefinition = {
  id: string;
  intent: string;
  title: string;
  aria_label: string;
  icon_key: "bot" | "checklist";
  badges: string[];
  steps: WorkflowStepDefinition[];
  status_labels: Record<string, WorkflowStatus>;
  default_status_key: string;
  allowed_section_kinds: WorkflowComponentKind[];
  allowed_fields: string[];
  sections: WorkflowSection[];
  actions: WorkflowAction[];
  model_guidance: string[];
};

export type WorkflowState = {
  schema_version: typeof WORKFLOW_SCHEMA_VERSION;
  workflow_id: string;
  intent: string;
  current_step: string;
  completed_steps: string[];
  blocked_reason?: string;
  required_fields: string[];
  missing_fields: string[];
  artifact_refs: Record<string, string>;
  status: WorkflowStatus;
  actions: WorkflowAction[];
  sections: WorkflowSection[];
  evidence_status?: StrategyWorkflowEvidenceStatus;
  bot_proposal_id?: string;
  start_allowed?: boolean;
};

export type WorkflowDefinitions = Record<string, WorkflowDefinition>;

export const WORKFLOW_DEFINITIONS =
  GENERATED_WORKFLOW_DEFINITIONS as unknown as WorkflowDefinitions;

export const STRATEGY_BOT_WORKFLOW_STEP_IDS = WORKFLOW_DEFINITIONS[
  STRATEGY_BOT_WORKFLOW_ID
].steps.map((step) => step.id);

export type StrategyWorkflowStep = (typeof STRATEGY_BOT_WORKFLOW_STEP_IDS)[number];

export type StrategyWorkflowState = WorkflowState & {
  workflow_id: typeof STRATEGY_BOT_WORKFLOW_ID;
  current_step: StrategyWorkflowStep;
  completed_steps: StrategyWorkflowStep[];
  evidence_status: StrategyWorkflowEvidenceStatus;
  start_allowed: boolean;
};

export const STRATEGY_BOT_INPUT_FIELDS =
  WORKFLOW_DEFINITIONS[STRATEGY_BOT_WORKFLOW_ID].sections.find(
    (section) => section.id === "strategy_inputs"
  )?.fields ?? [];

export const STRATEGY_BOT_SETUP_FIELDS =
  WORKFLOW_DEFINITIONS[STRATEGY_BOT_WORKFLOW_ID].sections.find(
    (section) => section.id === "paper_setup"
  )?.fields ?? [];

export function getWorkflowDefinition(
  workflowId: string,
  definitions: WorkflowDefinitions = WORKFLOW_DEFINITIONS
): WorkflowDefinition | null {
  return definitions[workflowId] ?? null;
}

export function normalizeWorkflowState(
  value: unknown,
  definitions: WorkflowDefinitions = WORKFLOW_DEFINITIONS
): WorkflowState | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const workflowId = sourceText(record.workflow_id) ?? sourceText(record.workflow);
  if (!workflowId) {
    return null;
  }
  const definition = getWorkflowDefinition(workflowId, definitions);
  if (!definition) {
    return null;
  }
  const stepIds = new Set(definition.steps.map((step) => step.id));
  const currentStep = sourceText(record.current_step) ?? definition.steps[0]?.id;
  if (!currentStep || !stepIds.has(currentStep)) {
    return null;
  }
  const allowedFields = new Set(definition.allowed_fields);
  const completedSteps = normalizeStringList(record.completed_steps).filter((step) =>
    stepIds.has(step)
  );
  const requiredFields = normalizeStringList(record.required_fields).filter((field) =>
    allowedFields.has(field)
  );
  const missingFields = normalizeStringList(record.missing_fields).filter((field) =>
    allowedFields.has(field)
  );
  const artifactRefs = normalizeStringRecord(record.artifact_refs);
  const evidenceStatus = normalizeStrategyWorkflowEvidenceStatus(record.evidence_status);
  const status = normalizeWorkflowStatus(record.status, definition, evidenceStatus);
  const normalizedEvidenceStatus =
    evidenceStatus ?? normalizeStrategyWorkflowEvidenceStatus(status.key);
  const botProposalId = sourceText(record.bot_proposal_id) ?? artifactRefs.bot_proposal_id;
  const startAllowed = workflowStartAllowed(definition, {
    botProposalId,
    currentStep,
    missingFields,
    requested: record.start_allowed === true,
  });

  return {
    schema_version: WORKFLOW_SCHEMA_VERSION,
    workflow_id: definition.id,
    intent: definition.intent,
    current_step: currentStep,
    completed_steps: completedSteps,
    blocked_reason: sourceText(record.blocked_reason),
    required_fields: requiredFields,
    missing_fields: missingFields,
    artifact_refs: artifactRefs,
    status,
    actions: normalizeWorkflowActions(record.actions, definition, {
      botProposalId,
      startAllowed,
    }),
    sections: normalizeWorkflowSections(record.sections, definition),
    evidence_status: normalizedEvidenceStatus ?? undefined,
    bot_proposal_id: botProposalId,
    start_allowed: startAllowed,
  };
}

export function normalizeStrategyWorkflowState(value: unknown): StrategyWorkflowState | null {
  const workflow = normalizeWorkflowState(value);
  if (workflow?.workflow_id !== STRATEGY_BOT_WORKFLOW_ID) {
    return null;
  }
  const evidenceStatus =
    normalizeStrategyWorkflowEvidenceStatus(workflow.evidence_status) ?? "insufficient_evidence";
  return {
    ...workflow,
    workflow_id: STRATEGY_BOT_WORKFLOW_ID,
    current_step: workflow.current_step as StrategyWorkflowStep,
    completed_steps: workflow.completed_steps as StrategyWorkflowStep[],
    evidence_status: evidenceStatus,
    start_allowed: workflow.start_allowed === true,
  };
}

function normalizeWorkflowStatus(
  value: unknown,
  definition: WorkflowDefinition,
  legacyStatus?: string | null
): WorkflowStatus {
  const requestedKey =
    sourceText(value) ??
    (value && typeof value === "object" && !Array.isArray(value)
      ? sourceText((value as Record<string, unknown>).key)
      : undefined) ??
    legacyStatus ??
    definition.default_status_key;
  return (
    definition.status_labels[requestedKey] ??
    definition.status_labels[definition.default_status_key] ?? {
      key: "unknown",
      label: "Unknown",
      tone: "neutral",
    }
  );
}

function normalizeWorkflowActions(
  value: unknown,
  definition: WorkflowDefinition,
  context: { botProposalId?: string; startAllowed: boolean }
): WorkflowAction[] {
  const defaults = definition.actions.map((action) => ({
    ...action,
    enabled:
      action.kind === "confirm_start_bot_proposal" ? context.startAllowed : action.enabled,
    target_ref:
      action.kind === "confirm_start_bot_proposal"
        ? context.botProposalId
        : action.target_ref,
    disabled_reason:
      action.kind === "confirm_start_bot_proposal" && context.startAllowed
        ? undefined
        : action.disabled_reason,
  }));
  if (!Array.isArray(value)) {
    return defaults;
  }
  const defaultById = new Map(defaults.map((action) => [action.id, action]));
  const normalized = value.flatMap((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      return [];
    }
    const record = item as Record<string, unknown>;
    const id = sourceText(record.id);
    const base = id ? defaultById.get(id) : null;
    if (!id || !base) {
      return [];
    }
    return [
      {
        ...base,
        label: sourceText(record.label) ?? base.label,
        enabled: record.enabled === true && base.enabled,
        disabled_reason: sourceText(record.disabled_reason) ?? base.disabled_reason,
      },
    ];
  });
  return normalized.length > 0 ? normalized : defaults;
}

function workflowStartAllowed(
  definition: WorkflowDefinition,
  context: {
    botProposalId?: string;
    currentStep: string;
    missingFields: string[];
    requested: boolean;
  }
): boolean {
  const hasConfirmStartAction = definition.actions.some(
    (action) => action.kind === "confirm_start_bot_proposal"
  );
  if (!hasConfirmStartAction) {
    return context.requested;
  }
  const finalStep = definition.steps.at(-1)?.id;
  return (
    context.requested &&
    Boolean(context.botProposalId) &&
    context.missingFields.length === 0 &&
    context.currentStep === finalStep
  );
}

function normalizeWorkflowSections(
  value: unknown,
  definition: WorkflowDefinition
): WorkflowSection[] {
  if (!Array.isArray(value)) {
    return cloneSections(definition.sections);
  }
  const byId = new Map(definition.sections.map((section) => [section.id, section]));
  const normalized = value.flatMap((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      return [];
    }
    const record = item as Record<string, unknown>;
    const id = sourceText(record.id);
    const base = id ? byId.get(id) : null;
    if (!id || !base) {
      return [];
    }
    const componentKind = sourceText(record.component_kind);
    if (
      componentKind &&
      (componentKind !== base.component_kind || !isWorkflowComponentKind(componentKind))
    ) {
      return [];
    }
    return [{ ...base }];
  });
  return normalized.length > 0 ? normalized : cloneSections(definition.sections);
}

function cloneSections(sections: WorkflowSection[]): WorkflowSection[] {
  return sections.map((section) => ({
    ...section,
    fields: section.fields ? [...section.fields] : undefined,
    ref_keys: section.ref_keys ? [...section.ref_keys] : undefined,
  }));
}

function isWorkflowComponentKind(value: string): value is WorkflowComponentKind {
  return WORKFLOW_COMPONENT_KINDS.includes(value as WorkflowComponentKind);
}

function normalizeStrategyWorkflowEvidenceStatus(
  value: unknown
): StrategyWorkflowEvidenceStatus | null {
  const text = sourceText(value);
  if (
    text === "insufficient_evidence" ||
    text === "reviewable_with_caveats" ||
    text === "needs_validation_or_robustness_check"
  ) {
    return text;
  }
  return null;
}

function normalizeStringRecord(value: unknown): Record<string, string> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  const normalized: Record<string, string> = {};
  for (const [key, rawValue] of Object.entries(value)) {
    const text = sourceText(rawValue);
    if (text) {
      normalized[key] = text;
    }
  }
  return normalized;
}

function normalizeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((item) => {
    const text = sourceText(item);
    return text ? [text] : [];
  });
}

function sourceText(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const text = value.trim();
  return text || undefined;
}
