import { COPILOT_STRATEGY_AGENT_ID } from "@/lib/copilot-constants";

export function copilotRuntimeInfo() {
  const strategyAgent = {
    capabilities: {
      customEvents: true,
      extensions: [
        {
          description: "Strategy Codebot custom user-safe UI metadata events.",
          uri: "strategy.custom-events",
        },
        {
          description: "Context-aware strategy suggestions.",
          uri: "strategy.suggestions",
        },
      ],
      humanInTheLoop: {
        approval: true,
        interrupts: true,
      },
      reasoning: {
        summaries: true,
      },
      state: {
        deltas: true,
        memory: true,
        persistentState: false,
        snapshots: true,
      },
      tools: {
        clientProvided: true,
        definitions: [
          {
            description: "Open the review artifact workspace panel.",
            name: "open_artifact_workspace",
          },
          {
            description: "Select a visible review artifact in the workspace.",
            name: "select_artifact",
          },
          {
            description: "Focus the strategy prompt composer.",
            name: "focus_composer",
          },
          {
            description: "Insert or update a structured strategy block in the composer.",
            name: "insert_strategy_block",
          },
          {
            description: "Open the Create review artifact sheet.",
            name: "open_create_spec",
          },
          {
            description: "Use a visible market snapshot as strategy context.",
            name: "use_market_snapshot_for_strategy",
          },
        ],
      },
      transport: {
        streaming: true,
      },
    },
    description:
      "Strategy Codebot chat runtime for review-only strategy assistance and artifacts.",
  };
  const agents = {
    default: strategyAgent,
    [COPILOT_STRATEGY_AGENT_ID]: strategyAgent,
  };

  return {
    agents,
    mode: "sse",
    version: "strategy-codebot-copilotkit-v1",
  };
}
