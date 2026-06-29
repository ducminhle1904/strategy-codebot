import { Bot, Check, ListChecks, Play } from "lucide-react";

import { cn } from "@/lib/utils";
import {
  getWorkflowDefinition,
  type WorkflowDefinition,
  type WorkflowDefinitions,
  type WorkflowSection,
  type WorkflowState,
} from "@/lib/workflow-ui";

export function WorkflowRail({ workflow }: { workflow: WorkflowState }) {
  return (
    <aside className="pointer-events-auto hidden min-[1440px]:absolute min-[1440px]:left-[calc(50%+24rem+1rem)] min-[1440px]:top-8 min-[1440px]:z-10 min-[1440px]:block min-[1440px]:max-h-[calc(100vh-4rem)] min-[1440px]:w-72 min-[1440px]:overflow-y-auto min-[1536px]:left-[calc(50%+24rem+1.5rem)] min-[1536px]:w-80">
      <WorkflowPanel workflow={workflow} />
    </aside>
  );
}

export function WorkflowPanel({
  definitions,
  workflow,
}: {
  definitions?: WorkflowDefinitions;
  workflow: WorkflowState;
}) {
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
  const missingFields = new Set(workflow.missing_fields);
  const requiredFields = new Set(workflow.required_fields);
  const fieldSections = workflow.sections.filter(
    (section) => section.component_kind === "field_status_section"
  );
  const actionGateSections = workflow.sections.filter(
    (section) => section.component_kind === "action_gate_section"
  );
  const primaryAction = workflow.actions[0] ?? null;
  const Icon = workflowIcon(definition);

  return (
    <section
      aria-label={definition.aria_label}
      className="rounded-[8px] border border-border bg-background/95 p-3 shadow-sm"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-foreground text-sm font-semibold">
            <Icon className="size-4" />
            {definition.title}
          </div>
          <p className="mt-1 text-muted-foreground text-xs">{workflow.status.label}</p>
        </div>
        <div
          className={cn(
            "rounded-[4px] px-2 py-1 text-[11px] font-medium",
            primaryAction?.enabled
              ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
              : "bg-muted text-muted-foreground"
          )}
        >
          {primaryAction?.enabled ? "Confirm-ready" : "Review gated"}
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {definition.badges.map((badge) => (
          <span
            className="rounded-[4px] border border-border bg-muted/60 px-2 py-1 text-[11px] text-muted-foreground"
            key={badge}
          >
            {badge}
          </span>
        ))}
      </div>

      <ol className="mt-3 space-y-1.5">
        {definition.steps.map((step, index) => {
          const isComplete = completedSteps.has(step.id);
          const isCurrent = workflow.current_step === step.id;
          return (
            <li
              className={cn(
                "grid grid-cols-[1.25rem_1fr] items-start gap-2 rounded-[6px] px-2 py-1.5 text-xs",
                isCurrent && "bg-primary/10 text-foreground",
                !isCurrent && "text-muted-foreground"
              )}
              key={step.id}
            >
              <span
                className={cn(
                  "mt-0.5 flex size-4 items-center justify-center rounded-full border text-[10px]",
                  isComplete
                    ? "border-emerald-500 bg-emerald-500 text-white"
                    : isCurrent
                      ? "border-primary text-primary"
                      : "border-border"
                )}
              >
                {isComplete ? <Check className="size-3" /> : index + 1}
              </span>
              <span className={cn(isCurrent && "font-medium")}>{step.label}</span>
            </li>
          );
        })}
      </ol>

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

function workflowIcon(definition: WorkflowDefinition) {
  return definition.icon_key === "bot" ? Bot : ListChecks;
}

function readableKey(key: string) {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}
