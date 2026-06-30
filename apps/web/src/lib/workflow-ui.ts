import {
  WORKFLOW_COMPONENT_KINDS as GENERATED_WORKFLOW_COMPONENT_KINDS,
  WORKFLOW_DEFINITIONS as GENERATED_WORKFLOW_DEFINITIONS,
  WORKFLOW_SCHEMA_VERSION as GENERATED_WORKFLOW_SCHEMA_VERSION,
  WORKFLOW_TASK_KINDS as GENERATED_WORKFLOW_TASK_KINDS,
} from "./workflow-registry-contract";

export const WORKFLOW_SCHEMA_VERSION = GENERATED_WORKFLOW_SCHEMA_VERSION;

export const STRATEGY_BOT_WORKFLOW_ID = "strategy_bot_simulation" as const;

export const WORKFLOW_COMPONENT_KINDS = GENERATED_WORKFLOW_COMPONENT_KINDS;
export const WORKFLOW_TASK_KINDS = GENERATED_WORKFLOW_TASK_KINDS;

export type StrategyWorkflowEvidenceStatus =
  | "insufficient_evidence"
  | "reviewable_with_caveats"
  | "needs_validation_or_robustness_check";

export type WorkflowComponentKind = (typeof WORKFLOW_COMPONENT_KINDS)[number];
export type WorkflowTaskKind = (typeof WORKFLOW_TASK_KINDS)[number];

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

export type WorkflowOption = {
  id: string;
  value: string;
  label: string;
  description?: string;
  disabled?: boolean;
  tone?: "neutral" | "success" | "warning" | "danger";
};

export type WorkflowInputRequest = {
  id: string;
  field: string;
  label: string;
  question?: string;
  kind: "text" | "textarea" | "single_select" | "multi_select" | "select_or_text" | "boolean";
  required: boolean;
  placeholder?: string;
  helper_text?: string;
  option_set_id?: string;
  allow_custom?: boolean;
  options?: WorkflowOption[];
  recommended_option_id?: string;
  custom_option_label?: string;
};

export type WorkflowTaskTemplate = {
  id: string;
  step_id: string;
  kind: WorkflowTaskKind;
  title: string;
  blocking: boolean;
  input_request_ids: string[];
  action_ids: string[];
  default_status: WorkflowTaskStatus;
};

export type WorkflowTaskStatus =
  | "pending_user"
  | "blocked"
  | "completed"
  | "approved"
  | "rejected"
  | "cancelled";

export type WorkflowTask = {
  id: string;
  task_template_id: string;
  step_id: string;
  kind: WorkflowTaskKind;
  title: string;
  blocking: boolean;
  status: WorkflowTaskStatus;
  input_request_ids: string[];
  action_ids: string[];
  input_requests: WorkflowInputRequest[];
  actions: WorkflowAction[];
  values: Record<string, unknown>;
  reason?: string;
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
  optional?: boolean;
  skip_label?: string;
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
  option_sets: Record<string, WorkflowOption[]>;
  input_request_templates: WorkflowInputRequest[];
  task_templates: WorkflowTaskTemplate[];
  model_guidance: string[];
};

export type WorkflowState = {
  schema_version: typeof WORKFLOW_SCHEMA_VERSION;
  workflow_id: string;
  intent: string;
  current_step: string;
  completed_steps: string[];
  skipped_steps: string[];
  step_reasons: Record<string, string>;
  blocked_reason?: string;
  required_fields: string[];
  missing_fields: string[];
  artifact_refs: Record<string, string>;
  status: WorkflowStatus;
  actions: WorkflowAction[];
  sections: WorkflowSection[];
  tasks: WorkflowTask[];
  input_requests: WorkflowInputRequest[];
  task_values: Record<string, unknown>;
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
  const skippedSteps = normalizeSkippedSteps(record.skipped_steps, definition, {
    completedSteps,
    currentStep,
  });
  const stepReasons = normalizeStepReasons(record.step_reasons, skippedSteps);
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
  const tasks = normalizeWorkflowTasks(record.tasks, definition, {
    botProposalId,
    startAllowed,
  });
  const inputRequests = normalizeWorkflowInputRequests(record.input_requests, definition, tasks);

  return {
    schema_version: WORKFLOW_SCHEMA_VERSION,
    workflow_id: definition.id,
    intent: definition.intent,
    current_step: currentStep,
    completed_steps: completedSteps,
    skipped_steps: skippedSteps,
    step_reasons: stepReasons,
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
    tasks,
    input_requests: inputRequests,
    task_values: normalizeTaskValues(record.task_values, inputRequests, tasks),
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

export function compactWorkflowTaskValues(
  values: Record<string, unknown>,
  inputRequests: WorkflowInputRequest[]
): Record<string, unknown> {
  const allowed = new Set(inputRequests.map((request) => request.id));
  const normalized: Record<string, unknown> = {};
  for (const [key, rawValue] of Object.entries(values)) {
    if (!allowed.has(key)) {
      continue;
    }
    const value = normalizeTaskValue(rawValue);
    if (value !== undefined) {
      normalized[key] = value;
    }
  }
  return normalized;
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

function normalizeSkippedSteps(
  value: unknown,
  definition: WorkflowDefinition,
  context: { completedSteps: string[]; currentStep: string }
): string[] {
  const requested = new Set(normalizeStringList(value));
  const completed = new Set(context.completedSteps);
  return definition.steps
    .filter(
      (step) =>
        requested.has(step.id) &&
        step.optional === true &&
        !completed.has(step.id) &&
        step.id !== context.currentStep
    )
    .map((step) => step.id);
}

function normalizeStepReasons(value: unknown, skippedSteps: string[]): Record<string, string> {
  const reasons = normalizeStringRecord(value);
  const skipped = new Set(skippedSteps);
  return Object.fromEntries(Object.entries(reasons).filter(([step]) => skipped.has(step)));
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

function normalizeWorkflowTasks(
  value: unknown,
  definition: WorkflowDefinition,
  context: { botProposalId?: string; startAllowed: boolean }
): WorkflowTask[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const templateById = new Map(definition.task_templates.map((template) => [template.id, template]));
  const inputById = new Map(
    definition.input_request_templates.map((request) => [request.id, request])
  );
  const actionById = new Map(definition.actions.map((action) => [action.id, action]));
  return value.flatMap((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      return [];
    }
    const record = item as Record<string, unknown>;
    let taskTemplateId = sourceText(record.task_template_id) ?? sourceText(record.template_id);
    let id = sourceText(record.id);
    if (!taskTemplateId && id && templateById.has(id)) {
      taskTemplateId = id;
      id = undefined;
    }
    const template = taskTemplateId ? templateById.get(taskTemplateId) : null;
    if (!taskTemplateId || !template || !isWorkflowTaskKind(template.kind)) {
      return [];
    }
    const allowedInputIds = new Set(
      template.input_request_ids.filter((requestId) => inputById.has(requestId))
    );
    const requestedInputIds = normalizeStringList(record.input_request_ids);
    const inputRequestIds = (requestedInputIds.length > 0 ? requestedInputIds : template.input_request_ids)
      .filter((requestId) => allowedInputIds.has(requestId));
    const allowedActionIds = new Set(
      template.action_ids.filter((actionId) => actionById.has(actionId))
    );
    const requestedActionIds = normalizeStringList(record.action_ids);
    const actionIds = (requestedActionIds.length > 0 ? requestedActionIds : template.action_ids)
      .filter((actionId) => allowedActionIds.has(actionId));
    const status = normalizeWorkflowTaskStatus(record.status) ?? template.default_status;
    const requestOverrides = normalizeInputRequestOverrides(record.input_requests, inputById);
    const inputRequests = inputRequestIds.flatMap((requestId) => {
      const request = inputById.get(requestId);
      return request
        ? [normalizeWorkflowInputRequest(request, requestOverrides.get(requestId), definition)]
        : [];
    });
    const recordActions = normalizeTaskActions(record.actions, actionById);
    return [
      {
        id: id ?? taskTemplateId,
        task_template_id: taskTemplateId,
        step_id: template.step_id,
        kind: template.kind,
        title: sourceText(record.title) ?? template.title,
        blocking: template.blocking,
        status,
        input_request_ids: inputRequestIds,
        action_ids: actionIds,
        input_requests: inputRequests,
        actions: actionIds.flatMap((actionId) => {
          const action = actionById.get(actionId);
          const override = recordActions.get(actionId);
          const isConfirmStart = action?.kind === "confirm_start_bot_proposal";
          return action
            ? [
                {
                  ...action,
                  label: override?.label ?? action.label,
                  enabled: isConfirmStart
                    ? context.startAllowed
                    : override?.enabled ?? action.enabled,
                  disabled_reason:
                    isConfirmStart && context.startAllowed
                      ? undefined
                      : override?.disabled_reason ?? action.disabled_reason,
                  target_ref: isConfirmStart
                    ? context.botProposalId
                    : override?.target_ref ?? action.target_ref,
                },
              ]
            : [];
        }),
        values: normalizeTaskValues(record.values, inputRequests, []),
        reason: sourceText(record.reason),
      },
    ];
  });
}

function normalizeWorkflowInputRequests(
  value: unknown,
  definition: WorkflowDefinition,
  tasks: WorkflowTask[]
): WorkflowInputRequest[] {
  const templateById = new Map(
    definition.input_request_templates.map((request) => [request.id, request])
  );
  const requested = normalizeStringList(value);
  const dictRequested = Array.isArray(value)
    ? value.flatMap((item) => {
        if (!item || typeof item !== "object" || Array.isArray(item)) {
          return [];
        }
        const id = sourceText((item as Record<string, unknown>).id);
        return id ? [id] : [];
      })
    : [];
  const taskRequestIds = tasks.flatMap((task) => task.input_request_ids);
  const ordered = requested.length > 0 ? requested : dictRequested.length > 0 ? dictRequested : taskRequestIds;
  const requestOverrides = normalizeInputRequestOverrides(value, templateById);
  const seen = new Set<string>();
  return ordered.flatMap((requestId) => {
    if (seen.has(requestId)) {
      return [];
    }
    const request = templateById.get(requestId);
    if (!request) {
      return [];
    }
    seen.add(requestId);
    return [normalizeWorkflowInputRequest(request, requestOverrides.get(requestId), definition)];
  });
}

function normalizeWorkflowInputRequest(
  template: WorkflowInputRequest,
  override: Record<string, unknown> | undefined,
  definition: WorkflowDefinition
): WorkflowInputRequest {
  const normalized: WorkflowInputRequest = {
    ...template,
    question: sourceText(override?.question) ?? template.question,
    placeholder: sourceText(override?.placeholder) ?? template.placeholder,
    helper_text: sourceText(override?.helper_text) ?? template.helper_text,
  };
  const options = normalizeWorkflowPromptOptions(
    override?.options ?? template.options ?? optionSetOptions(definition, template.option_set_id)
  );
  if (options.length > 0) {
    normalized.options = options;
    const requestedRecommended =
      sourceText(override?.recommended_option_id) ?? template.recommended_option_id;
    normalized.recommended_option_id = options.some((option) => option.id === requestedRecommended)
      ? requestedRecommended
      : options[0]?.id;
  } else {
    delete normalized.options;
    delete normalized.recommended_option_id;
  }
  if (template.allow_custom === true) {
    normalized.allow_custom = true;
    normalized.custom_option_label =
      sourceText(override?.custom_option_label) ?? template.custom_option_label;
  } else {
    delete normalized.allow_custom;
    delete normalized.custom_option_label;
  }
  return normalized;
}

function normalizeInputRequestOverrides(
  value: unknown,
  templateById: Map<string, WorkflowInputRequest>
): Map<string, Record<string, unknown>> {
  const overrides = new Map<string, Record<string, unknown>>();
  if (!Array.isArray(value)) {
    return overrides;
  }
  for (const item of value) {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      continue;
    }
    const record = item as Record<string, unknown>;
    const id = sourceText(record.id);
    if (!id || !templateById.has(id)) {
      continue;
    }
    overrides.set(id, record);
  }
  return overrides;
}

function normalizeWorkflowPromptOptions(value: unknown): WorkflowOption[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const seenIds = new Set<string>();
  const seenValues = new Set<string>();
  const options: WorkflowOption[] = [];
  for (const item of value) {
    if (options.length >= 3) {
      break;
    }
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      continue;
    }
    const record = item as Record<string, unknown>;
    const id = sourceText(record.id);
    const optionValue = sourceText(record.value);
    const label = sourceText(record.label);
    if (!id || !optionValue || !label || seenIds.has(id) || seenValues.has(optionValue)) {
      continue;
    }
    if (record.disabled === true) {
      continue;
    }
    const option: WorkflowOption = { id, value: optionValue, label };
    const description = sourceText(record.description);
    if (description) {
      option.description = description;
    }
    const tone = sourceText(record.tone);
    if (tone === "neutral" || tone === "success" || tone === "warning" || tone === "danger") {
      option.tone = tone;
    }
    options.push(option);
    seenIds.add(id);
    seenValues.add(optionValue);
  }
  return options;
}

function optionSetOptions(
  definition: WorkflowDefinition,
  optionSetId: string | undefined
): WorkflowOption[] {
  return optionSetId ? definition.option_sets[optionSetId] ?? [] : [];
}

function normalizeTaskActions(
  value: unknown,
  actionById: Map<string, WorkflowAction>
): Map<string, Partial<WorkflowAction>> {
  const normalized = new Map<string, Partial<WorkflowAction>>();
  if (!Array.isArray(value)) {
    return normalized;
  }
  for (const item of value) {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      continue;
    }
    const record = item as Record<string, unknown>;
    const id = sourceText(record.id);
    if (!id || !actionById.has(id)) {
      continue;
    }
    normalized.set(id, {
      enabled: typeof record.enabled === "boolean" ? record.enabled : undefined,
      label: sourceText(record.label),
      disabled_reason: sourceText(record.disabled_reason),
      target_ref: sourceText(record.target_ref),
    });
  }
  return normalized;
}

function normalizeTaskValues(
  value: unknown,
  inputRequests: WorkflowInputRequest[],
  tasks: WorkflowTask[]
): Record<string, unknown> {
  const allowedIds = new Set(inputRequests.map((request) => request.id));
  for (const task of tasks) {
    for (const requestId of task.input_request_ids) {
      allowedIds.add(requestId);
    }
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  const normalized: Record<string, unknown> = {};
  for (const [key, rawValue] of Object.entries(value)) {
    if (!allowedIds.has(key)) {
      continue;
    }
    const value = normalizeTaskValue(rawValue);
    if (value !== undefined) {
      normalized[key] = value;
    }
  }
  return normalized;
}

function normalizeTaskValue(value: unknown): unknown {
  if (typeof value === "string") {
    return value.trim() || undefined;
  }
  if (typeof value === "boolean") {
    return value;
  }
  if (Array.isArray(value)) {
    const items = value.flatMap((item) => {
      const text = sourceText(item);
      return text ? [text] : [];
    });
    return items.length > 0 ? items : undefined;
  }
  return undefined;
}

function normalizeWorkflowTaskStatus(value: unknown): WorkflowTaskStatus | null {
  const text = sourceText(value);
  if (
    text === "pending_user" ||
    text === "blocked" ||
    text === "completed" ||
    text === "approved" ||
    text === "rejected" ||
    text === "cancelled"
  ) {
    return text;
  }
  return null;
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

function isWorkflowTaskKind(value: string): value is WorkflowTaskKind {
  return WORKFLOW_TASK_KINDS.includes(value as WorkflowTaskKind);
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
