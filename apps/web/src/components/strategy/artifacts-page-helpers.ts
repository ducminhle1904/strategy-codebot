import type { Artifact } from "@/lib/backend-schemas";

function isPreviewValue(value: unknown): value is string | number | boolean {
  if (!["string", "number", "boolean"].includes(typeof value)) {
    return false;
  }
  const normalized = String(value).trim();
  return normalized.length > 0 && normalized !== "N/A" && normalized !== "N/A -> N/A";
}

export function getArtifactCardPreviewLines(artifact: Artifact, limit = 8) {
  const summary = artifact.preview_summary;
  if (!summary || typeof summary !== "object" || Array.isArray(summary)) {
    return [
      artifact.presentation.language_hint ?? artifact.presentation.viewer_kind,
      artifact.kind.replaceAll("_", " "),
      artifact.display_name,
    ].slice(0, limit);
  }
  const metricEntries =
    typeof summary.metrics === "object" && summary.metrics !== null && !Array.isArray(summary.metrics)
      ? Object.entries(summary.metrics)
      : [];
  const summaryEntries = Object.entries(summary).filter(
    ([key]) => !["equity_preview", "kind", "metrics", "run_id", "symbol", "timeframe"].includes(key)
  );
  const entries = [
    ["symbol", summary.symbol],
    ["timeframe", summary.timeframe],
    ...metricEntries,
    ...summaryEntries,
  ]
    .filter(([, value]) => isPreviewValue(value))
    .slice(0, limit);
  return entries.map(([key, value]) => `${String(key).replaceAll("_", " ")}: ${String(value)}`);
}

export function getWorkspaceInventoryArtifacts(artifacts: Artifact[]) {
  const seen = new Set<string>();
  return artifacts.filter((artifact) => {
    if (artifact.presentation.visibility === "internal" || seen.has(artifact.presentation.dedupe_key)) {
      return false;
    }
    seen.add(artifact.presentation.dedupe_key);
    return true;
  });
}
