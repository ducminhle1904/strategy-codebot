import type {
  AccountUsageResponse,
  MeResponse,
  ProviderStatusResponse,
} from "./backend-schemas";
import { getUiCopy, type LanguagePreference } from "./i18n";

export type ProviderDisplay = {
  description: string;
  status: "ready" | "limited" | "needs-setup" | "checking";
  title: string;
};

export function accountName(
  me?: MeResponse,
  clerkName?: string | null,
  clerkEmail?: string | null,
  language: LanguagePreference = "en"
) {
  return clerkName || clerkEmail || me?.user.id || getUiCopy(language).localWorkspace;
}

export function accountSubtitle(
  me?: MeResponse,
  clerkEmail?: string | null,
  language: LanguagePreference = "en"
) {
  return clerkEmail || me?.capability.tier_label || getUiCopy(language).localWorkspace;
}

export function accountInitial(name: string) {
  return (name.trim()[0] || "S").toUpperCase();
}

export function providerDisplay(
  provider?: ProviderStatusResponse,
  me?: MeResponse,
  language: LanguagePreference = "en"
): ProviderDisplay {
  const t = getUiCopy(language);
  if (!provider) {
    return {
      description: t.checkingModelAvailability,
      status: "checking",
      title: t.checking,
    };
  }
  if (!provider.configured) {
    return {
      description:
        me?.capability.tier === "free"
          ? t.managedModelNotReady
          : t.providerNeedsSetup,
      status: "needs-setup",
      title: t.needsSetupStatus,
    };
  }
  if (!provider.available) {
    return {
      description: t.managedFallbackAvailable,
      status: "limited",
      title: t.limitedStatus,
    };
  }
  return {
    description:
      me?.capability.tier === "free"
        ? t.managedModelIncluded
        : t.providerEnabled,
    status: "ready",
    title: t.readyStatus,
  };
}

export function providerRouteReady(provider?: ProviderStatusResponse) {
  if (!provider) {
    return false;
  }
  if (typeof provider.route_ready === "boolean") {
    return provider.route_ready;
  }
  return provider.configured && provider.available;
}

export function providerFallbackEnabled(provider?: ProviderStatusResponse) {
  if (!provider) {
    return false;
  }
  if (typeof provider.fallback_enabled === "boolean") {
    return provider.fallback_enabled;
  }
  return provider.model_routing_mode === "registry";
}

export function formatUsageNumber(value: number) {
  return new Intl.NumberFormat("en-US").format(value);
}

export function formatUsageCost(
  usage?: AccountUsageResponse,
  language: LanguagePreference = "en"
) {
  if (!usage || usage.estimated_cost_usd === null) {
    return getUiCopy(language).notEstimated;
  }
  return new Intl.NumberFormat("en-US", {
    currency: "USD",
    maximumFractionDigits: 4,
    style: "currency",
  }).format(usage.estimated_cost_usd);
}
