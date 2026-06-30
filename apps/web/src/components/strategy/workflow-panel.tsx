import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import {
  Bot,
  Check,
  ChevronLeft,
  ChevronRight,
  CornerDownLeft,
  ListChecks,
  Pencil,
  Play,
  SendHorizontal,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type { ChatActivity, ChatActivityArtifactLink } from "@/lib/chat-activity";
import {
  compactWorkflowTaskValues,
  getWorkflowDefinition,
  type WorkflowAction,
  type WorkflowDefinition,
  type WorkflowDefinitions,
  type WorkflowInputRequest,
  type WorkflowOption,
  type WorkflowSection,
  type WorkflowStepDefinition,
  type WorkflowState,
  type WorkflowTask,
} from "@/lib/workflow-ui";

type WorkflowStepStatus = "completed" | "current" | "skipped" | "pending";

type WorkflowPanelProps = {
  activities?: ChatActivity[];
  definitions?: WorkflowDefinitions;
  onSelectArtifact?: (artifactId: string) => void;
  onSubmitTask?: (taskId: string, values: Record<string, unknown>) => void;
  onTaskAction?: (taskId: string, action: WorkflowAction, values?: Record<string, unknown>) => void;
  showTaskControls?: boolean;
  workflow: WorkflowState;
};

const WORKFLOW_STEP_MARKER_CLASS_NAMES: Record<WorkflowStepStatus, string> = {
  completed: "border-emerald-500 bg-emerald-500 text-white",
  current: "border-primary text-primary",
  skipped: "border-dashed border-muted-foreground/50 text-muted-foreground",
  pending: "border-border",
};

export function WorkflowRail({
  activities,
  onSelectArtifact,
  onSubmitTask,
  onTaskAction,
  workflow,
}: {
  activities?: ChatActivity[];
  onSelectArtifact?: (artifactId: string) => void;
  onSubmitTask?: (taskId: string, values: Record<string, unknown>) => void;
  onTaskAction?: (taskId: string, action: WorkflowAction, values?: Record<string, unknown>) => void;
  workflow: WorkflowState;
}) {
  return (
    <aside className="pointer-events-auto hidden min-[1440px]:absolute min-[1440px]:left-[calc(50%+24rem+1rem)] min-[1440px]:top-8 min-[1440px]:z-10 min-[1440px]:block min-[1440px]:max-h-[calc(100vh-4rem)] min-[1440px]:w-72 min-[1440px]:overflow-y-auto min-[1536px]:left-[calc(50%+24rem+1.5rem)] min-[1536px]:w-80">
      <WorkflowPanel
        activities={activities}
        onSelectArtifact={onSelectArtifact}
        onSubmitTask={onSubmitTask}
        onTaskAction={onTaskAction}
        showTaskControls={false}
        workflow={workflow}
      />
    </aside>
  );
}

export function WorkflowTaskPrompt({
  definitions,
  onSubmitTask,
  workflow,
}: Pick<WorkflowPanelProps, "definitions" | "onSubmitTask" | "workflow">) {
  const definition = getWorkflowDefinition(workflow.workflow_id, definitions);
  const [optimisticValues, setOptimisticValues] = useState<Record<string, unknown>>({});
  const promptItems = useMemo(
    () => workflowPromptItems(workflow, optimisticValues),
    [optimisticValues, workflow]
  );
  const [currentIndex, setCurrentIndex] = useState(0);
  useEffect(() => {
    setCurrentIndex((index) => Math.min(index, Math.max(0, promptItems.length - 1)));
  }, [promptItems.length]);
  if (!definition) {
    return null;
  }
  const current = promptItems[currentIndex];
  if (!current) {
    return null;
  }
  return (
    <WorkflowQuestionPromptCard
      current={current}
      currentIndex={currentIndex}
      onSubmitTask={onSubmitTask}
      promptItems={promptItems}
      setCurrentIndex={setCurrentIndex}
      setOptimisticValues={setOptimisticValues}
    />
  );
}

function WorkflowQuestionPromptCard({
  current,
  currentIndex,
  onSubmitTask,
  promptItems,
  setCurrentIndex,
  setOptimisticValues,
}: {
  current: WorkflowPromptItem;
  currentIndex: number;
  onSubmitTask?: (taskId: string, values: Record<string, unknown>) => void;
  promptItems: WorkflowPromptItem[];
  setCurrentIndex: Dispatch<SetStateAction<number>>;
  setOptimisticValues: Dispatch<SetStateAction<Record<string, unknown>>>;
}) {
  const options = useMemo(() => orderedPromptOptions(current.request), [current.request]);
  const existingValue = current.values[current.request.id];
  const defaultSelection = useMemo(
    () => promptDefaultSelection(current.request, options, existingValue),
    [current.request, existingValue, options]
  );
  const [selection, setSelection] = useState<PromptSelection>(defaultSelection);
  const selectionKey = `${current.task.id}:${current.request.id}:${stringifyPromptValue(existingValue)}`;
  useEffect(() => {
    setSelection(defaultSelection);
  }, [selectionKey, defaultSelection]);
  const canSubmit =
    Boolean(onSubmitTask) &&
    !workflowTaskIsClosed(current.task) &&
    current.task.status !== "blocked" &&
    promptSelectionHasValue(selection);
  const canSkip = current.request.required !== true && Boolean(onSubmitTask);
  const submitValue = promptSelectionValue(selection);
  return (
    <section
      aria-label="Workflow task prompt"
      className="rounded-[8px] border border-border bg-background/95 p-3 text-foreground shadow-sm"
    >
      <div className="mb-3 flex items-start justify-between gap-4">
        <h2 className="min-w-0 text-pretty font-semibold text-sm leading-snug">
          {current.request.question ?? current.request.label}
        </h2>
        <div className="flex shrink-0 items-center gap-1.5 text-muted-foreground text-xs">
          <button
            aria-label="Previous question"
            className="rounded-[4px] p-1 hover:bg-muted disabled:opacity-35"
            disabled={currentIndex === 0}
            onClick={() => setCurrentIndex((index) => Math.max(0, index - 1))}
            type="button"
          >
            <ChevronLeft className="size-4" />
          </button>
          <span>{currentIndex + 1} of {promptItems.length}</span>
          <button
            aria-label="Next question"
            className="rounded-[4px] p-1 hover:bg-muted disabled:opacity-35"
            disabled={currentIndex >= promptItems.length - 1}
            onClick={() => setCurrentIndex((index) => Math.min(promptItems.length - 1, index + 1))}
            type="button"
          >
            <ChevronRight className="size-4" />
          </button>
        </div>
      </div>

      <div className="grid gap-2">
        {options.map((option, index) => {
          const selected = selection.kind === "option" && selection.value === option.value;
          return (
            <button
              className={cn(
                "grid grid-cols-[2rem_minmax(0,1fr)] items-center gap-3 rounded-[6px] border px-2.5 py-2 text-left text-sm transition-colors",
                selected
                  ? "border-border/70 bg-muted/70 text-foreground"
                  : "border-transparent text-muted-foreground hover:bg-muted/35 hover:text-foreground"
              )}
              key={option.id}
              onClick={() => setSelection({ kind: "option", value: option.value })}
              type="button"
            >
              <span
                className={cn(
                  "flex size-5 items-center justify-center rounded-full border text-[11px]",
                  selected
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border text-muted-foreground"
                )}
              >
                {index + 1}
              </span>
              <span className="min-w-0">
                <span className={cn("font-medium", selected ? "text-foreground" : "text-foreground/90")}>
                  {option.label}
                </span>
                {option.description ? (
                  <span className="ml-2 text-muted-foreground">{option.description}</span>
                ) : null}
              </span>
            </button>
          );
        })}

        {current.request.allow_custom ? (
          <label
            className={cn(
              "grid grid-cols-[2rem_minmax(0,1fr)] items-center gap-3 rounded-[6px] border px-2.5 py-2 transition-colors",
              selection.kind === "custom"
                ? "border-border/70 bg-muted/70"
                : "border-transparent text-muted-foreground hover:bg-muted/35"
            )}
          >
            <span className="flex size-5 items-center justify-center rounded-full border border-border text-muted-foreground">
              <Pencil className="size-3.5" />
            </span>
            <input
              className="min-w-0 bg-transparent text-sm outline-none placeholder:text-muted-foreground/80"
              onChange={(event) => setSelection({ kind: "custom", value: event.target.value })}
              onFocus={() =>
                setSelection((currentSelection) => ({
                  kind: "custom",
                  value: currentSelection.kind === "custom" ? currentSelection.value : "",
                }))
              }
              placeholder={current.request.custom_option_label ?? current.request.placeholder ?? "Custom value"}
              value={selection.kind === "custom" ? selection.value : ""}
            />
          </label>
        ) : null}
      </div>

      <div className="mt-3 flex items-center justify-end gap-2">
        {canSkip ? (
          <button
            className="rounded-[4px] px-3 py-1.5 text-muted-foreground text-xs hover:bg-muted/50 hover:text-foreground"
            onClick={() => {
              onSubmitTask?.(current.task.id, {});
              setCurrentIndex((index) => Math.min(promptItems.length - 1, index + 1));
            }}
            type="button"
          >
            Skip
          </button>
        ) : null}
        <button
          className="inline-flex items-center gap-1.5 rounded-[4px] bg-primary px-3 py-1.5 font-medium text-primary-foreground text-xs disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!canSubmit}
          onClick={() => {
            if (!canSubmit || submitValue === undefined) {
              return;
            }
            setOptimisticValues((currentValues) => ({
              ...currentValues,
              [current.request.id]: submitValue,
            }));
            onSubmitTask?.(current.task.id, { [current.request.id]: submitValue });
            setCurrentIndex((index) => Math.min(promptItems.length - 1, index + 1));
          }}
          type="button"
        >
          Submit
          <CornerDownLeft className="size-4" />
        </button>
      </div>
    </section>
  );
}

type WorkflowPromptItem = {
  request: WorkflowInputRequest;
  task: WorkflowTask;
  values: Record<string, unknown>;
};

type PromptSelection =
  | { kind: "option"; value: string }
  | { kind: "custom"; value: string }
  | { kind: "empty"; value?: undefined };

function workflowPromptItems(
  workflow: WorkflowState,
  optimisticValues: Record<string, unknown>
): WorkflowPromptItem[] {
  const openBlockingTasks = workflow.tasks.filter(
    (task) => task.blocking && !workflowTaskIsClosed(task) && task.status !== "blocked"
  );
  const tasks = openBlockingTasks.length > 0
    ? openBlockingTasks
    : workflow.tasks.filter((task) => !workflowTaskIsClosed(task) && task.status !== "blocked");
  return tasks.flatMap((task) => {
    const values = { ...workflow.task_values, ...task.values, ...optimisticValues };
    return task.input_requests.flatMap((request) =>
      promptValueAnswered(values[request.id]) ? [] : [{ task, request, values }]
    );
  });
}

function orderedPromptOptions(request: WorkflowInputRequest): WorkflowOption[] {
  const options = request.options ?? [];
  const recommended = request.recommended_option_id;
  if (!recommended) {
    return options;
  }
  const recommendedOption = options.find((option) => option.id === recommended);
  if (!recommendedOption) {
    return options;
  }
  return [
    recommendedOption,
    ...options.filter((option) => option.id !== recommendedOption.id),
  ];
}

function promptDefaultSelection(
  request: WorkflowInputRequest,
  options: WorkflowOption[],
  existingValue: unknown
): PromptSelection {
  const textValue = typeof existingValue === "string" ? existingValue : "";
  if (textValue) {
    const option = options.find((item) => item.value === textValue || item.id === textValue);
    return option ? { kind: "option", value: option.value } : { kind: "custom", value: textValue };
  }
  if (options.length > 0) {
    return { kind: "option", value: options[0].value };
  }
  return request.allow_custom ? { kind: "custom", value: "" } : { kind: "empty" };
}

function promptSelectionHasValue(selection: PromptSelection): boolean {
  if (selection.kind === "option") {
    return selection.value.trim().length > 0;
  }
  if (selection.kind === "custom") {
    return selection.value.trim().length > 0;
  }
  return false;
}

function promptSelectionValue(selection: PromptSelection): string | undefined {
  if (selection.kind === "option" || selection.kind === "custom") {
    return selection.value.trim() || undefined;
  }
  return undefined;
}

function promptValueAnswered(value: unknown): boolean {
  if (typeof value === "string") {
    return value.trim().length > 0;
  }
  if (typeof value === "boolean") {
    return true;
  }
  if (Array.isArray(value)) {
    return value.length > 0;
  }
  return false;
}

function stringifyPromptValue(value: unknown): string {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
export function WorkflowPanel({
  activities = [],
  definitions,
  onSelectArtifact,
  onSubmitTask,
  onTaskAction,
  showTaskControls = false,
  workflow,
}: WorkflowPanelProps) {
  const definition = getWorkflowDefinition(workflow.workflow_id, definitions);
  if (!definition) {
    return (
      <section
        aria-label="Workflow"
        className="rounded-[8px] border border-border bg-background/95 p-3 shadow-sm"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-foreground text-sm font-semibold">
              <ListChecks className="size-4" />
              Workflow
            </div>
            <p className="mt-1 text-muted-foreground text-xs">{workflow.status.label}</p>
          </div>
        </div>
      </section>
    );
  }

  const completedSteps = new Set(workflow.completed_steps);
  const skippedSteps = new Set(workflow.skipped_steps);
  const missingFields = new Set(workflow.missing_fields);
  const requiredFields = new Set(workflow.required_fields);
  const fieldSections = workflow.sections.filter(
    (section) => section.component_kind === "field_status_section"
  );
  const actionGateSections = workflow.sections.filter(
    (section) => section.component_kind === "action_gate_section"
  );
  const artifactLinks = workflowArtifactLinks(workflow.artifact_refs);
  const progressSummary = workflowProgressSummary(definition, workflow);
  const recentActivities = workflowRecentActivities(activities);

  return (
    <section
      aria-label={definition.aria_label}
      className="rounded-[8px] border border-border bg-background/95 p-3 shadow-sm"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-foreground text-sm font-semibold">
            {definition.icon_key === "bot" ? (
              <Bot className="size-4" />
            ) : (
              <ListChecks className="size-4" />
            )}
            {definition.title}
          </div>
          <p className="mt-1 text-muted-foreground text-xs">{workflow.status.label}</p>
        </div>
        <div className="shrink-0 text-right text-[11px] text-muted-foreground">
          <div className="font-medium text-foreground">{progressSummary.stepLabel}</div>
          <div>{progressSummary.statusLabel}</div>
        </div>
      </div>

      <ol className="mt-3 space-y-1.5">
        {definition.steps.map((step, index) => {
          const stepStatus = workflowStepStatus(step.id, {
            completedSteps,
            currentStep: workflow.current_step,
            skippedSteps,
          });
          const isCurrent = stepStatus === "current";
          const isSkipped = stepStatus === "skipped";
          return (
            <li
              className={cn(
                "grid grid-cols-[1.25rem_minmax(0,1fr)_auto] items-start gap-2 rounded-[6px] px-2 py-1.5 text-xs",
                isCurrent && "bg-primary/10 text-foreground",
                !isCurrent && "text-muted-foreground"
              )}
              data-workflow-step-id={step.id}
              data-workflow-step-status={stepStatus}
              key={step.id}
              title={isSkipped ? workflow.step_reasons[step.id] : undefined}
            >
              <span
                className={cn(
                  "mt-0.5 flex size-4 items-center justify-center rounded-full border text-[10px]",
                  WORKFLOW_STEP_MARKER_CLASS_NAMES[stepStatus]
                )}
              >
                {workflowStepMarker(stepStatus, index)}
              </span>
              <span className={cn(isCurrent && "font-medium")}>{step.label}</span>
              {isSkipped ? (
                <span className="rounded-[4px] bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                  {workflowStepSkipLabel(step)}
                </span>
              ) : null}
            </li>
          );
        })}
      </ol>

      {workflow.blocked_reason ? (
        <div className="mt-3 rounded-[6px] border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-amber-700 text-xs dark:text-amber-300">
          {workflow.blocked_reason}
        </div>
      ) : null}

      {showTaskControls ? (
        <WorkflowTaskInboxSection
          definition={definition}
          onSubmitTask={onSubmitTask}
          onTaskAction={onTaskAction}
          workflow={workflow}
        />
      ) : (
        <WorkflowTaskSummarySection workflow={workflow} />
      )}

      <WorkflowArtifactRefsSection
        links={artifactLinks}
        onSelectArtifact={onSelectArtifact}
      />

      <WorkflowRecentActivitySection
        activities={recentActivities}
        onSelectArtifact={onSelectArtifact}
      />

      {fieldSections.map((section) => (
        <WorkflowFieldStatusSection
          key={section.id}
          missingFields={missingFields}
          requiredFields={requiredFields}
          section={section}
        />
      ))}

      {actionGateSections.map((section) => (
        <WorkflowActionGateSection
          action={workflow.actions.find((item) => item.id === section.action_id) ?? null}
          key={section.id}
          missingFields={missingFields}
          section={section}
        />
      ))}
    </section>
  );
}

function WorkflowTaskSummarySection({ workflow }: { workflow: WorkflowState }) {
  if (workflow.tasks.length === 0) {
    return null;
  }
  const openTaskCount = workflow.tasks.filter((task) => !workflowTaskIsClosed(task)).length;
  return (
    <div className="mt-3 border-border border-t pt-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-muted-foreground text-xs font-medium">
          <ListChecks className="size-3.5" />
          Tasks
        </div>
        <span className="text-[10px] text-muted-foreground">{openTaskCount} open</span>
      </div>
      <div className="grid gap-1.5">
        {workflow.tasks.map((task) => (
          <div
            className="rounded-[6px] border border-border/70 px-2 py-1.5"
            data-workflow-task-id={task.id}
            data-workflow-task-status={task.status}
            key={task.id}
          >
            <div className="flex items-start justify-between gap-2">
              <span className="min-w-0 truncate text-[11px] text-foreground">{task.title}</span>
              <span className="shrink-0 text-[10px] text-muted-foreground">
                {workflowTaskStatusLabel(task.status)}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function workflowStepStatus(
  stepId: string,
  context: {
    completedSteps: Set<string>;
    currentStep: string;
    skippedSteps: Set<string>;
  }
): WorkflowStepStatus {
  if (context.completedSteps.has(stepId)) {
    return "completed";
  }
  if (context.currentStep === stepId) {
    return "current";
  }
  if (context.skippedSteps.has(stepId)) {
    return "skipped";
  }
  return "pending";
}

function workflowStepMarker(status: WorkflowStepStatus, index: number) {
  if (status === "completed") {
    return <Check className="size-3" />;
  }
  if (status === "skipped") {
    return "-";
  }
  return index + 1;
}

function workflowStepSkipLabel(step: WorkflowStepDefinition): string {
  return step.skip_label ?? "Skipped";
}

function WorkflowTaskInboxSection({
  definition,
  onSubmitTask,
  onTaskAction,
  workflow,
}: {
  definition: WorkflowDefinition;
  onSubmitTask?: (taskId: string, values: Record<string, unknown>) => void;
  onTaskAction?: (taskId: string, action: WorkflowAction, values?: Record<string, unknown>) => void;
  workflow: WorkflowState;
}) {
  if (workflow.tasks.length === 0) {
    return null;
  }
  return (
    <div className="mt-3 border-border border-t pt-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-muted-foreground text-xs font-medium">
          <ListChecks className="size-3.5" />
          Tasks
        </div>
        <span className="text-[10px] text-muted-foreground">
          {workflow.tasks.filter((task) => task.status === "pending_user").length} open
        </span>
      </div>
      <div className="grid gap-2">
        {workflow.tasks.map((task) => (
          <WorkflowTaskCard
            definition={definition}
            key={task.id}
            onSubmitTask={onSubmitTask}
            onTaskAction={onTaskAction}
            task={task}
            workflowValues={workflow.task_values}
          />
        ))}
      </div>
    </div>
  );
}

function WorkflowTaskCard({
  definition,
  onSubmitTask,
  onTaskAction,
  task,
  variant = "compact",
  workflowValues,
}: {
  definition: WorkflowDefinition;
  onSubmitTask?: (taskId: string, values: Record<string, unknown>) => void;
  onTaskAction?: (taskId: string, action: WorkflowAction, values?: Record<string, unknown>) => void;
  task: WorkflowTask;
  variant?: "compact" | "prompt";
  workflowValues: Record<string, unknown>;
}) {
  const initialValues = useMemo(
    () => ({ ...workflowValues, ...task.values }),
    [task.values, workflowValues]
  );
  const [values, setValues] = useState<Record<string, unknown>>(initialValues);
  const initialValuesKey = useMemo(
    () => workflowTaskDraftKey(task, initialValues),
    [initialValues, task]
  );
  useEffect(() => {
    setValues(initialValues);
  }, [initialValuesKey, initialValues]);
  const isClosed = workflowTaskIsClosed(task);
  const isBlocked = task.status === "blocked";
  const canSubmit = !isClosed && !isBlocked && Boolean(onSubmitTask) && task.input_requests.length > 0;
  const canAct = !isClosed && Boolean(onTaskAction);
  const isPrompt = variant === "prompt";
  return (
    <div
      className={cn(
        "border bg-background/80 text-xs",
        isPrompt
          ? "rounded-[8px] border-border/80 px-3 py-3"
          : "rounded-[6px] border-border/70 px-2 py-2",
        isBlocked && "bg-muted/40",
        isClosed && "opacity-75"
      )}
      data-workflow-task-id={task.id}
      data-workflow-task-status={task.status}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className={cn("font-medium text-foreground", isPrompt ? "text-sm" : "truncate")}>
            {task.title}
          </div>
          {task.reason ? (
            <div
              className={cn(
                "mt-0.5 text-muted-foreground",
                isPrompt ? "text-xs leading-relaxed" : "line-clamp-2 text-[11px]"
              )}
            >
              {task.reason}
            </div>
          ) : null}
        </div>
        <span className="shrink-0 rounded-[4px] bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
          {workflowTaskStatusLabel(task.status)}
        </span>
      </div>

      {task.input_requests.length > 0 ? (
        <div className={cn("mt-3 grid gap-3", isPrompt && "sm:grid-cols-2")}>
          {task.input_requests.map((request) => (
            <WorkflowInputControl
              definition={definition}
              disabled={isClosed || isBlocked}
              key={request.id}
              onChange={(value) => setValues((current) => ({ ...current, [request.id]: value }))}
              request={request}
              value={values[request.id]}
              variant={isPrompt ? "prompt" : "compact"}
            />
          ))}
        </div>
      ) : null}

      {!isClosed && (task.input_requests.length > 0 || task.actions.length > 0) ? (
        <div className={cn("mt-3 flex flex-wrap gap-2", isPrompt && "justify-end")}>
          {task.actions.map((action) => (
            <button
              className={cn(
                "rounded-[4px] border border-border/70 text-foreground disabled:cursor-not-allowed disabled:opacity-50",
                isPrompt ? "px-3 py-1.5 text-xs" : "px-2 py-1 text-[11px]"
              )}
              disabled={!canAct || action.enabled === false}
              key={action.id}
              onClick={() =>
                onTaskAction?.(task.id, action, compactWorkflowTaskValues(values, task.input_requests))
              }
              title={action.disabled_reason}
              type="button"
            >
              {action.label}
            </button>
          ))}
          {task.input_requests.length > 0 ? (
            <button
              className={cn(
                "inline-flex items-center gap-1.5 rounded-[4px] bg-primary font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50",
                isPrompt ? "px-3 py-1.5 text-xs" : "px-2 py-1 text-[11px]"
              )}
              disabled={!canSubmit}
              onClick={() => onSubmitTask?.(task.id, compactWorkflowTaskValues(values, task.input_requests))}
              type="button"
            >
              Submit
              <SendHorizontal className={cn(isPrompt ? "size-3.5" : "size-3")} />
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function WorkflowInputControl({
  definition,
  disabled,
  onChange,
  request,
  value,
  variant = "compact",
}: {
  definition: WorkflowDefinition;
  disabled: boolean;
  onChange: (value: unknown) => void;
  request: WorkflowInputRequest;
  value: unknown;
  variant?: "compact" | "prompt";
}) {
  const options = request.option_set_id ? definition.option_sets[request.option_set_id] ?? [] : [];
  const textValue = typeof value === "string" ? value : "";
  const isPrompt = variant === "prompt";
  const controlClassName = cn(
    "rounded-[4px] border border-border/70 bg-background outline-none focus:border-primary disabled:opacity-60",
    isPrompt ? "px-2.5 py-2 text-sm" : "px-2 py-1.5 text-xs"
  );
  return (
    <label className={cn("grid gap-1", request.kind === "textarea" && isPrompt && "sm:col-span-2")}>
      <span className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
        <span>{request.label}</span>
        {request.required ? <span>Required</span> : null}
      </span>
      {request.kind === "textarea" ? (
        <textarea
          className={cn(controlClassName, isPrompt ? "min-h-20" : "min-h-16")}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          placeholder={request.placeholder}
          value={textValue}
        />
      ) : request.kind === "single_select" ? (
        <WorkflowSelect
          className={controlClassName}
          disabled={disabled}
          onChange={onChange}
          options={options}
          placeholder={request.placeholder}
          value={textValue}
        />
      ) : request.kind === "multi_select" ? (
        <WorkflowMultiSelect
          disabled={disabled}
          onChange={onChange}
          options={options}
          value={Array.isArray(value) ? value : []}
          variant={variant}
        />
      ) : request.kind === "select_or_text" ? (
        <div className="grid gap-1">
          <WorkflowSelect
            className={controlClassName}
            disabled={disabled}
            onChange={onChange}
            options={options}
            placeholder={request.placeholder}
            value={textValue}
          />
          {request.allow_custom ? (
            <input
              className={controlClassName}
              disabled={disabled}
              onChange={(event) => onChange(event.target.value)}
              placeholder={request.placeholder ?? "Custom value"}
              value={isKnownOptionValue(options, textValue) ? "" : textValue}
            />
          ) : null}
        </div>
      ) : request.kind === "boolean" ? (
        <input
          checked={value === true}
          className="h-4 w-4"
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
          type="checkbox"
        />
      ) : (
        <input
          className={controlClassName}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          placeholder={request.placeholder}
          value={textValue}
        />
      )}
      {request.helper_text ? (
        <span className="text-[10px] text-muted-foreground">{request.helper_text}</span>
      ) : null}
    </label>
  );
}

function WorkflowSelect({
  className,
  disabled,
  onChange,
  options,
  placeholder,
  value,
}: {
  className?: string;
  disabled: boolean;
  onChange: (value: string) => void;
  options: WorkflowOption[];
  placeholder?: string;
  value: string;
}) {
  const selectedValue = isKnownOptionValue(options, value) ? value : "";
  return (
    <select
      className={cn(
        "rounded-[4px] border border-border/70 bg-background px-2 py-1.5 text-xs outline-none focus:border-primary disabled:opacity-60",
        className
      )}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      value={selectedValue}
    >
      <option value="">{placeholder ?? "Select"}</option>
      {options.map((option) => (
        <option disabled={option.disabled} key={option.id} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

function WorkflowMultiSelect({
  disabled,
  onChange,
  options,
  value,
  variant = "compact",
}: {
  disabled: boolean;
  onChange: (value: string[]) => void;
  options: WorkflowOption[];
  value: unknown[];
  variant?: "compact" | "prompt";
}) {
  const selected = new Set(value.filter((item): item is string => typeof item === "string"));
  return (
    <div className="grid gap-1">
      {options.map((option) => (
        <label
          className={cn(
            "flex items-center gap-2 rounded-[4px] border border-border/70",
            variant === "prompt" ? "px-2.5 py-2 text-xs" : "px-2 py-1 text-[11px]"
          )}
          key={option.id}
        >
          <input
            checked={selected.has(option.value)}
            disabled={disabled || option.disabled}
            onChange={(event) => {
              const next = new Set(selected);
              if (event.target.checked) {
                next.add(option.value);
              } else {
                next.delete(option.value);
              }
              onChange([...next]);
            }}
            type="checkbox"
          />
          <span>{option.label}</span>
        </label>
      ))}
    </div>
  );
}

function workflowTaskIsClosed(task: WorkflowTask) {
  return ["completed", "approved", "rejected", "cancelled"].includes(task.status);
}

function isKnownOptionValue(options: WorkflowOption[], value: string) {
  return options.some((option) => option.value === value || option.id === value);
}

function workflowTaskStatusLabel(status: WorkflowTask["status"]) {
  if (status === "pending_user") {
    return "Needs input";
  }
  if (status === "blocked") {
    return "Blocked";
  }
  if (status === "approved") {
    return "Approved";
  }
  if (status === "rejected") {
    return "Rejected";
  }
  if (status === "cancelled") {
    return "Cancelled";
  }
  return "Done";
}

function workflowTaskDraftKey(task: WorkflowTask, values: Record<string, unknown>) {
  const sortedValues = Object.keys(values)
    .sort()
    .map((key) => [key, values[key]]);
  return JSON.stringify({
    id: task.id,
    input_request_ids: task.input_request_ids,
    status: task.status,
    values: sortedValues,
  });
}

function WorkflowArtifactRefsSection({
  links,
  onSelectArtifact,
}: {
  links: ChatActivityArtifactLink[];
  onSelectArtifact?: (artifactId: string) => void;
}) {
  if (links.length === 0) {
    return null;
  }
  return (
    <div className="mt-3 border-border border-t pt-3">
      <div className="mb-2 flex items-center gap-2 text-muted-foreground text-xs font-medium">
        <ListChecks className="size-3.5" />
        Artifacts
      </div>
      <div className="flex flex-wrap gap-1.5">
        {links.map((link) =>
          onSelectArtifact ? (
            <button
              className="rounded-[4px] border border-border/70 px-2 py-1 text-[11px] text-foreground hover:bg-muted"
              key={link.artifactId}
              onClick={() => onSelectArtifact(link.artifactId)}
              type="button"
            >
              {link.label}
            </button>
          ) : (
            <span
              className="rounded-[4px] border border-border/70 px-2 py-1 text-[11px] text-foreground"
              key={link.artifactId}
            >
              {link.label}
            </span>
          )
        )}
      </div>
    </div>
  );
}

function WorkflowRecentActivitySection({
  activities,
  onSelectArtifact,
}: {
  activities: ChatActivity[];
  onSelectArtifact?: (artifactId: string) => void;
}) {
  if (activities.length === 0) {
    return null;
  }
  return (
    <div className="mt-3 border-border border-t pt-3">
      <div className="mb-2 flex items-center gap-2 text-muted-foreground text-xs font-medium">
        <Play className="size-3.5" />
        Recent activity
      </div>
      <div className="grid gap-1.5">
        {activities.map((activity) => (
          <div
            className="rounded-[6px] border border-border/70 px-2 py-1.5"
            key={activity.id}
          >
            <div className="flex items-start justify-between gap-2">
              <span className="min-w-0 truncate text-[11px] text-foreground">
                {activity.title}
              </span>
              <span className="shrink-0 text-[10px] uppercase tracking-[0.08em] text-muted-foreground">
                {workflowActivityStateLabel(activity.state)}
              </span>
            </div>
            {activity.artifactLinks && activity.artifactLinks.length > 0 ? (
              <div className="mt-1 flex flex-wrap gap-1">
                {activity.artifactLinks.map((link) =>
                  onSelectArtifact ? (
                    <button
                      className="rounded-[4px] bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground hover:text-foreground"
                      key={`${activity.id}-${link.artifactId}`}
                      onClick={() => onSelectArtifact(link.artifactId)}
                      type="button"
                    >
                      {link.label}
                    </button>
                  ) : (
                    <span
                      className="rounded-[4px] bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
                      key={`${activity.id}-${link.artifactId}`}
                    >
                      {link.label}
                    </span>
                  )
                )}
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function WorkflowFieldStatusSection({
  missingFields,
  requiredFields,
  section,
}: {
  missingFields: Set<string>;
  requiredFields: Set<string>;
  section: WorkflowSection;
}) {
  const fields = section.fields ?? [];
  if (fields.length === 0) {
    return null;
  }
  return (
    <div className="mt-3 border-border border-t pt-3">
      <div className="mb-2 flex items-center gap-2 text-muted-foreground text-xs font-medium">
        <ListChecks className="size-3.5" />
        {section.title ?? "Fields"}
      </div>
      <div className="grid gap-1.5">
        {fields.map((field) => {
          const isMissing = missingFields.has(field);
          const isRequired = requiredFields.has(field);
          const status = isMissing ? "Missing" : isRequired ? "Provided" : "Optional";
          return (
            <div
              className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-[6px] border border-border/70 px-2 py-1.5"
              key={field}
            >
              <span className="truncate text-[11px] text-muted-foreground">{readableKey(field)}</span>
              <span
                className={cn(
                  "rounded-[4px] px-2 py-0.5 text-[11px] font-medium",
                  isMissing
                    ? "bg-amber-500/10 text-amber-700 dark:text-amber-300"
                    : isRequired
                      ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                      : "bg-muted text-muted-foreground"
                )}
              >
                {status}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function WorkflowActionGateSection({
  action,
  missingFields,
  section,
}: {
  action: WorkflowState["actions"][number] | null;
  missingFields: Set<string>;
  section: WorkflowSection;
}) {
  const sectionMissingFields = (section.fields ?? []).filter((field) => missingFields.has(field));
  const isReady = action?.enabled === true;
  return (
    <div className="mt-3 border-border border-t pt-3 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-foreground">{section.title ?? "Action gate"}</span>
        <span className="text-muted-foreground">
          {isReady ? section.ready_label ?? "Ready" : section.pending_label ?? "Waiting"}
        </span>
      </div>
      {sectionMissingFields.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {sectionMissingFields.map((field) => (
            <span
              className="rounded-[4px] border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-amber-700 dark:text-amber-300"
              key={field}
            >
              {readableKey(field)}
            </span>
          ))}
        </div>
      ) : null}
      <div className="mt-2 flex items-center gap-2 rounded-[6px] bg-muted/60 px-2 py-1.5 text-muted-foreground">
        <Play className="size-3.5" />
        <span>
          {isReady
            ? `${action?.label ?? "Action"} requires confirmation.`
            : section.locked_message ?? action?.disabled_reason ?? "Action is locked."}
        </span>
      </div>
    </div>
  );
}

function workflowProgressSummary(definition: WorkflowDefinition, workflow: WorkflowState) {
  const total = definition.steps.length;
  const currentIndex = definition.steps.findIndex((step) => step.id === workflow.current_step);
  const completedCount = workflow.completed_steps.length;
  const skippedCount = workflow.skipped_steps.length;
  const stepLabel =
    currentIndex >= 0 ? `Step ${currentIndex + 1}/${total}` : `${completedCount}/${total}`;
  const statusParts = [`${completedCount} done`];
  if (skippedCount > 0) {
    statusParts.push(`${skippedCount} skipped`);
  }
  return {
    stepLabel,
    statusLabel: statusParts.join(" / "),
  };
}

function workflowArtifactLinks(artifactRefs: WorkflowState["artifact_refs"]): ChatActivityArtifactLink[] {
  return Object.entries(artifactRefs).flatMap(([key, artifactId]) =>
    artifactId
      ? [
          {
            artifactId,
            label: readableKey(key),
          },
        ]
      : []
  );
}

function workflowRecentActivities(activities: ChatActivity[]): ChatActivity[] {
  return activities
    .filter((activity) => activity.toolName !== "provider" && activity.toolName !== "model")
    .slice(-3)
    .reverse();
}

function workflowActivityStateLabel(state: ChatActivity["state"]) {
  if (state === "input-available") {
    return "Running";
  }
  if (state === "output-error" || state === "output-denied") {
    return "Failed";
  }
  return "Done";
}

function readableKey(key: string) {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}
