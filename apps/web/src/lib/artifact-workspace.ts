import type { Artifact, Run, RunEvent } from "@/lib/backend-schemas";
import { getUiCopy, type LanguagePreference } from "@/lib/i18n";

export type ArtifactWorkspaceStep = {
  label: string;
  state: "done" | "current" | "waiting";
};

export const ARTIFACT_WORKSPACE_TABS = [
  "strategy",
  "code",
  "risk",
  "validation",
  "changes",
] as const;

export type ArtifactWorkspaceTab = (typeof ARTIFACT_WORKSPACE_TABS)[number];

export type ArtifactUserKind =
  | "code"
  | "review"
  | "risk"
  | "validation"
  | "evidence"
  | "report";

export type ArtifactUserSummary = {
  description: string;
  kind: ArtifactUserKind;
  label: string;
};

export type GroupedArtifacts = {
  code: Artifact[];
  notes: Artifact[];
  report: Artifact[];
  risk: Artifact[];
  validation: Artifact[];
};

export function getPrimaryArtifact(
  artifacts: Artifact[],
  selectedArtifactId: string | null
) {
  return artifacts.find((artifact) => artifact.id === selectedArtifactId) ?? artifacts[0] ?? null;
}

export function getArtifactForTab(
  artifacts: Artifact[],
  selectedArtifactId: string | null,
  tab: ArtifactWorkspaceTab
) {
  const grouped = groupArtifactsByKind(artifacts);
  return getArtifactForGroupedTab(artifacts, grouped, selectedArtifactId, tab);
}

export function getArtifactForGroupedTab(
  artifacts: Artifact[],
  grouped: GroupedArtifacts,
  selectedArtifactId: string | null,
  tab: ArtifactWorkspaceTab
) {
  if (tab === "code") {
    return getPrimaryArtifact(grouped.code, selectedArtifactId);
  }
  if (tab === "risk") {
    return getPrimaryArtifact(grouped.risk, selectedArtifactId);
  }
  if (tab === "validation") {
    return getPrimaryArtifact(grouped.validation, selectedArtifactId);
  }
  if (tab === "changes") {
    return getPrimaryArtifact(grouped.report, selectedArtifactId);
  }
  if (tab === "strategy") {
    return getPrimaryArtifact(grouped.notes, selectedArtifactId);
  }
  const selected = artifacts.find((artifact) => artifact.id === selectedArtifactId);
  if (selected && !isCodeArtifact(selected)) {
    return selected;
  }
  return (
    grouped.notes[0] ??
    artifacts.find((artifact) => !isCodeArtifact(artifact)) ??
    grouped.code[0] ??
    artifacts[0] ??
    null
  );
}

export function groupArtifactsByKind(artifacts: Artifact[]) {
  const visibleArtifacts = getUserFacingArtifacts(artifacts);
  const risk = visibleArtifacts.filter((artifact) => classifyArtifact(artifact) === "risk");
  const validation = visibleArtifacts.filter((artifact) => classifyArtifact(artifact) === "validation");
  const notes = visibleArtifacts.filter((artifact) =>
    ["review", "risk", "validation", "report"].includes(classifyArtifact(artifact))
  );
  const noteIds = new Set(notes.map((artifact) => artifact.id));
  const riskIds = new Set(risk.map((artifact) => artifact.id));
  const validationIds = new Set(validation.map((artifact) => artifact.id));
  return {
    code: visibleArtifacts.filter((artifact) => !noteIds.has(artifact.id) && classifyArtifact(artifact) === "code"),
    notes,
    report: notes.filter((artifact) => !riskIds.has(artifact.id) && !validationIds.has(artifact.id)),
    risk,
    validation,
  } satisfies GroupedArtifacts;
}

export function getUserFacingArtifacts(artifacts: Artifact[]) {
  return artifacts.filter((artifact) => artifact.visibility !== "internal");
}

export function getArtifactUserSummary(
  artifact: Artifact,
  language: LanguagePreference = "en"
): ArtifactUserSummary {
  const t = getUiCopy(language);
  const kind = classifyArtifact(artifact);
  if (kind === "code") {
    return {
      description: t.codeArtifactDescription,
      kind: "code",
      label: t.codeArtifact,
    };
  }
  if (kind === "risk") {
    return {
      description: t.riskNotesDescription,
      kind: "risk",
      label: t.riskNotes,
    };
  }
  if (kind === "validation") {
    return {
      description: t.validationSummaryDescription,
      kind: "validation",
      label: t.validationSummary,
    };
  }
  if (kind === "evidence") {
    return {
      description: t.evidenceSummaryDescription,
      kind: "evidence",
      label: t.evidenceSummary,
    };
  }
  return {
    description: t.reviewReportDescription,
    kind: "report",
    label: t.reviewReport,
  };
}

export function currentProgressStep(steps: ArtifactWorkspaceStep[]) {
  return (
    steps.find((step) => step.state === "current") ??
    [...steps].reverse().find((step) => step.state === "done") ??
    steps[0] ??
    null
  );
}

export function runStatusSummary(
  status: Run["status"],
  language: LanguagePreference = "en"
) {
  const t = getUiCopy(language);
  if (status === "failed") {
    return t.couldNotCreateArtifact;
  }
  if (status === "blocked") {
    return t.reviewBoundaryReached;
  }
  if (status === "cancelled") {
    return t.artifactCreationCancelled;
  }
  if (status === "completed") {
    return t.reviewArtifactReady;
  }
  return t.creatingReviewArtifact;
}

function classifyArtifact(artifact: Artifact): ArtifactUserKind {
  const kind = artifact.kind.toLowerCase();
  if (
    kind.includes("risk")
  ) {
    return "risk";
  }
  if (kind.includes("validation") || kind.includes("checklist")) {
    return "validation";
  }
  if (kind.includes("review")) {
    return "review";
  }
  if (artifact.category === "evidence" || kind.includes("evidence")) {
    return "evidence";
  }
  if (artifact.category === "report" || kind.includes("report")) {
    return "report";
  }
  if (
    artifact.category === "code" ||
    kind === "pine_file" ||
    kind.includes("mql5") ||
    artifact.mime_type === "text/plain"
  ) {
    return "code";
  }
  return "report";
}

function isCodeArtifact(artifact: Artifact) {
  return classifyArtifact(artifact) === "code";
}

export function mapRunEventsToUserSteps(
  events: RunEvent[],
  status: Run["status"],
  language: LanguagePreference = "en"
): ArtifactWorkspaceStep[] {
  const t = getUiCopy(language);
  const stages = [
    { label: t.readingStrategy, names: ["model"] },
    { label: t.generatingReviewArtifact, names: ["runner", "tool"] },
    { label: t.checkingReviewBoundaries, names: ["validation", "review"] },
    { label: t.preparingFiles, names: ["artifact"] },
  ];
  const started = stageSet(events, "stage.started");
  const completedStages = stageSet(events, "stage.completed");
  const completed = status === "completed";
  const stopped = ["failed", "blocked", "cancelled"].includes(status);

  return stages.map((stage) => {
    const stageDone = stage.names.some((name) => completedStages.has(name));
    const stageStarted = stage.names.some((name) => started.has(name));
    return {
      label: stage.label,
      state: completed || stageDone ? "done" : stageStarted && !stopped ? "current" : "waiting",
    };
  });
}

function stageSet(events: RunEvent[], type: string) {
  const values = new Set<string>();
  for (const event of events) {
    const payload = stagePayload(event, type);
    if (!payload) {
      continue;
    }
    const stage = payload.stage;
    if (typeof stage === "string") {
      values.add(stage);
    }
  }
  return values;
}

function stagePayload(event: RunEvent, type: string): Record<string, unknown> | null {
  if (!event.payload || typeof event.payload !== "object" || Array.isArray(event.payload)) {
    return null;
  }
  const payload = event.payload as Record<string, unknown>;
  if (event.type === type) {
    return payload;
  }
  if (event.type !== "progress.update" || payload.source_event_type !== type) {
    return null;
  }
  const sourcePayload = payload.payload;
  return sourcePayload && typeof sourcePayload === "object" && !Array.isArray(sourcePayload)
    ? (sourcePayload as Record<string, unknown>)
    : null;
}
