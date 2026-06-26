"use client";

import { useAgentContext, useCapabilities } from "@copilotkit/react-core/v2";
import { useMemo, type ReactNode } from "react";

import { COPILOT_STRATEGY_AGENT_ID } from "@/lib/copilot-constants";
import type { MarketSnapshot } from "@/lib/chat-ui";

export type StrategyAgentWorkflow =
  | "artifact_workspace"
  | "backtest_preview"
  | "chat"
  | "new_chat"
  | "repair_validation";

export type StrategyAgentContextValue = {
  activeConversationId: string | null;
  artifactPanelOpen: boolean;
  currentWorkflow: StrategyAgentWorkflow;
  language: "en" | "vi";
  latestMarketSnapshot: MarketSnapshot | null;
  providerReady: boolean;
  selectedArtifactId: string | null;
  strategyReadiness: string | null;
  tierLabel: string | null;
  userTier: string | null;
};

export type StrategyCopilotCapabilities = {
  customEvents: boolean;
  frontendTools: boolean;
  hitl: boolean;
  reasoning: boolean;
  sharedState: boolean;
  streaming: boolean;
  suggestions: boolean;
};

export function StrategyAgentContextProvider({
  children,
  value,
}: {
  children: ReactNode;
  value: StrategyAgentContextValue;
}) {
  const safeValue = useMemo(
    () => ({
      activeConversationId: value.activeConversationId,
      artifactPanelOpen: value.artifactPanelOpen,
      currentWorkflow: value.currentWorkflow,
      language: value.language,
      latestMarketSnapshot: value.latestMarketSnapshot
        ? {
            change_percent: value.latestMarketSnapshot.change_percent ?? null,
            label: value.latestMarketSnapshot.label,
            provider: value.latestMarketSnapshot.provider ?? null,
            symbol: value.latestMarketSnapshot.symbol,
          }
        : null,
      providerReady: value.providerReady,
      selectedArtifactId: value.selectedArtifactId,
      strategyReadiness: value.strategyReadiness,
      tierLabel: value.tierLabel,
      userTier: value.userTier,
    }),
    [value]
  );

  useAgentContext({
    description:
      "User-safe Strategy Codebot UI context. Contains visible workspace state only, never prompts, traces, raw provider payloads, or hidden memory.",
    value: safeValue,
  });

  return children;
}

export function useStrategyCopilotCapabilities(): StrategyCopilotCapabilities {
  const capabilities = useCapabilities(COPILOT_STRATEGY_AGENT_ID);
  return {
    customEvents: true,
    frontendTools: capabilities?.tools?.clientProvided ?? true,
    hitl:
      capabilities?.humanInTheLoop?.approvals ??
      capabilities?.humanInTheLoop?.interrupts ??
      true,
    reasoning:
      capabilities?.reasoning?.supported ??
      capabilities?.reasoning?.streaming ??
      true,
    sharedState: capabilities?.state?.snapshots ?? true,
    streaming: capabilities?.transport?.streaming ?? true,
    suggestions: true,
  };
}
