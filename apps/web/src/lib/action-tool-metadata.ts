import type { ActionRegistryEntry } from "@/lib/backend-schemas";

export type ActionRegistryLookup =
  | Map<string, ActionRegistryEntry>
  | Record<string, ActionRegistryEntry>
  | null
  | undefined;

export function actionRegistryLookup(
  actions: ActionRegistryEntry[] | null | undefined
): Map<string, ActionRegistryEntry> {
  return new Map((actions ?? []).map((action) => [action.tool_id, action]));
}

export function actionToolActivityLabel(
  toolId: string | null,
  language: "en" | "vi",
  state: "completed" | "started",
  registry?: ActionRegistryLookup
): string | null {
  const registryLabel = actionRegistryEntry(toolId, registry)?.label;
  if (registryLabel) {
    if (state === "started") {
      return language === "vi" ? `Đang ${registryLabel.toLowerCase()}` : registryLabel;
    }
    return registryLabel;
  }
  return null;
}

export function actionToolLabel(
  toolId: string | null | undefined,
  registry?: ActionRegistryLookup
): string | null {
  return actionRegistryEntry(toolId, registry)?.label ?? null;
}

export function actionToolPrompt(
  toolId: string | null | undefined,
  registry?: ActionRegistryLookup
): string | null {
  return actionRegistryEntry(toolId, registry)?.prompt ?? null;
}

export function actionRegistryEntry(
  toolId: string | null | undefined,
  registry?: ActionRegistryLookup
): ActionRegistryEntry | null {
  if (!toolId || !registry) {
    return null;
  }
  if (registry instanceof Map) {
    return registry.get(toolId) ?? null;
  }
  return registry[toolId] ?? null;
}
