"use client";

import {
  useConfigureSuggestions,
  useFrontendTool,
  useHumanInTheLoop,
  useRenderTool,
  useSuggestions,
} from "@copilotkit/react-core/v2";
import { z } from "zod";

import {
  ApplyMarketContextHitlCard,
  ArtifactToolCard,
  BacktestPreviewHitlCard,
  RegenerateArtifactHitlCard,
  UnknownToolCard,
  ValidationRepairHitlCard,
} from "@/components/strategy/agent-tools/tool-cards";
import { COPILOT_STRATEGY_AGENT_ID } from "@/lib/copilot-constants";
import type { ChatSuggestionItem } from "@/lib/chat-ui";

const ArtifactSelectorSchema = z.object({
  artifactId: z.string().optional(),
});

const StrategyBlockSchema = z.object({
  slot: z.enum(["entry", "exit", "market", "risk"]),
  template: z.string().min(1),
});

const MarketContextSchema = z.object({
  symbol: z.string().optional(),
});

const BacktestPreviewSchema = z.object({
  symbol: z.string().optional(),
  timeframe: z.string().optional(),
});

type StrategyCopilotToolCallbacks = {
  focusComposer: () => void;
  insertStrategyBlock: (input: z.infer<typeof StrategyBlockSchema>) => void;
  openArtifactWorkspace: () => void;
  openCreateSpec: () => void;
  selectArtifact: (artifactId: string | null) => void;
  useMarketSnapshotForStrategy: (symbol?: string) => void;
};

export function StrategyCopilotTools({
  callbacks,
  suggestions,
  toolsAvailable = true,
}: {
  callbacks: StrategyCopilotToolCallbacks;
  suggestions: ChatSuggestionItem[];
  toolsAvailable?: boolean;
}) {
  useConfigureSuggestions(
    {
      instructions:
        "Suggest concise next actions for review-only trading strategy work. Prefer market research, strategy clarification, artifact review, and validation repair. Never suggest broker execution or live trading.",
      maxSuggestions: 3,
      minSuggestions: 1,
    },
    []
  );

  useFrontendTool(
    {
      agentId: COPILOT_STRATEGY_AGENT_ID,
      available: toolsAvailable,
      description: "Open the review artifact workspace panel.",
      handler: async () => {
        callbacks.openArtifactWorkspace();
        return { opened: true };
      },
      name: "open_artifact_workspace",
    },
    [callbacks, toolsAvailable]
  );

  useFrontendTool(
    {
      agentId: COPILOT_STRATEGY_AGENT_ID,
      available: toolsAvailable,
      description: "Select a visible review artifact in the workspace.",
      handler: async ({ artifactId }) => {
        callbacks.selectArtifact(typeof artifactId === "string" ? artifactId : null);
        return { selectedArtifactId: artifactId ?? null };
      },
      name: "select_artifact",
      parameters: ArtifactSelectorSchema,
    },
    [callbacks, toolsAvailable]
  );

  useFrontendTool(
    {
      agentId: COPILOT_STRATEGY_AGENT_ID,
      available: toolsAvailable,
      description: "Focus the strategy prompt composer.",
      handler: async () => {
        callbacks.focusComposer();
        return { focused: true };
      },
      name: "focus_composer",
    },
    [callbacks, toolsAvailable]
  );

  useFrontendTool(
    {
      agentId: COPILOT_STRATEGY_AGENT_ID,
      available: toolsAvailable,
      description: "Insert or update a structured strategy block in the composer.",
      handler: async (input) => {
        callbacks.insertStrategyBlock(input);
        return { inserted: true, slot: input.slot };
      },
      name: "insert_strategy_block",
      parameters: StrategyBlockSchema,
    },
    [callbacks, toolsAvailable]
  );

  useFrontendTool(
    {
      agentId: COPILOT_STRATEGY_AGENT_ID,
      available: toolsAvailable,
      description: "Open the Create review artifact sheet.",
      handler: async () => {
        callbacks.openCreateSpec();
        return { opened: true };
      },
      name: "open_create_spec",
    },
    [callbacks, toolsAvailable]
  );

  useFrontendTool(
    {
      agentId: COPILOT_STRATEGY_AGENT_ID,
      available: toolsAvailable,
      description: "Use the latest visible market snapshot as strategy context.",
      handler: async ({ symbol }) => {
        callbacks.useMarketSnapshotForStrategy(typeof symbol === "string" ? symbol : undefined);
        return { applied: true, symbol: symbol ?? null };
      },
      name: "use_market_snapshot_for_strategy",
      parameters: MarketContextSchema,
    },
    [callbacks, toolsAvailable]
  );

  useHumanInTheLoop(
    {
      agentId: COPILOT_STRATEGY_AGENT_ID,
      available: toolsAvailable,
      description: "Ask the user to confirm a review-only backtest preview plan.",
      name: "confirm_backtest_preview",
      parameters: BacktestPreviewSchema,
      render: ({ args, respond, status }) => (
        <BacktestPreviewHitlCard
          onRespond={respond ? (response) => void respond(response) : undefined}
          status={status}
          symbol={args.symbol}
          timeframe={args.timeframe}
        />
      ),
    },
    [toolsAvailable]
  );

  useHumanInTheLoop(
    {
      agentId: COPILOT_STRATEGY_AGENT_ID,
      available: toolsAvailable,
      description: "Ask the user how validation blockers should be repaired.",
      name: "confirm_validation_repair",
      parameters: z.object({ reason: z.string().optional() }),
      render: ({ respond, status }) => (
        <ValidationRepairHitlCard
          onRespond={respond ? (response) => void respond(response) : undefined}
          status={status}
        />
      ),
    },
    [toolsAvailable]
  );

  useHumanInTheLoop(
    {
      agentId: COPILOT_STRATEGY_AGENT_ID,
      available: toolsAvailable,
      description: "Ask the user to confirm artifact regeneration.",
      name: "confirm_regenerate_artifact",
      parameters: z.object({ artifactId: z.string().optional() }),
      render: ({ respond, status }) => (
        <RegenerateArtifactHitlCard
          onRespond={respond ? (response) => void respond(response) : undefined}
          status={status}
        />
      ),
    },
    [toolsAvailable]
  );

  useHumanInTheLoop(
    {
      agentId: COPILOT_STRATEGY_AGENT_ID,
      available: toolsAvailable,
      description: "Ask the user to apply market context to a strategy draft.",
      name: "confirm_apply_market_context",
      parameters: MarketContextSchema,
      render: ({ args, respond, status }) => (
        <ApplyMarketContextHitlCard
          onRespond={respond ? (response) => void respond(response) : undefined}
          status={status}
          symbol={args.symbol}
        />
      ),
    },
    [toolsAvailable]
  );

  useRenderTool(
    {
      agentId: COPILOT_STRATEGY_AGENT_ID,
      name: "artifact_anchor",
      parameters: z.object({ artifactId: z.string().optional() }),
      render: ({ status }) => <ArtifactToolCard status={status} />,
    },
    []
  );

  useRenderTool(
    {
      agentId: COPILOT_STRATEGY_AGENT_ID,
      name: "*",
      render: ({ status }) => <UnknownToolCard status={status ?? "inProgress"} />,
    },
    []
  );

  useStrategySuggestionsBridge(suggestions);
  return null;
}

export function useStrategySuggestionsBridge(suggestions: ChatSuggestionItem[]) {
  const copilotSuggestions = useSuggestions({ agentId: COPILOT_STRATEGY_AGENT_ID });
  return {
    ...copilotSuggestions,
    strategySuggestions: suggestions,
  };
}
