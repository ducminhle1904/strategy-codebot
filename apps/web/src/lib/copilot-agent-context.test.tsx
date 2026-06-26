import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  StrategyAgentContextProvider,
  useStrategyCopilotCapabilities,
} from "./copilot-agent-context";

const useAgentContextMock = vi.fn();
const useCapabilitiesMock = vi.fn();

vi.mock("@copilotkit/react-core/v2", () => ({
  useAgentContext: (...args: unknown[]) => useAgentContextMock(...args),
  useCapabilities: (...args: unknown[]) => useCapabilitiesMock(...args),
}));

describe("StrategyAgentContextProvider", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it("registers only user-safe visible workspace context", () => {
    render(
      <StrategyAgentContextProvider
        value={{
          activeConversationId: "conv_1",
          artifactPanelOpen: true,
          currentWorkflow: "artifact_workspace",
          language: "vi",
          latestMarketSnapshot: {
            approximate: true,
            change_percent: 1.2,
            freshness: "source_backed",
            label: "Bitcoin",
            price: "63000",
            price_points: [],
            provider: "CCXT",
            source_count: 1,
            sources: [],
            symbol: "BTC",
          },
          providerReady: true,
          selectedArtifactId: "art_1",
          strategyReadiness: "ready_for_artifact",
          tierLabel: "Paid",
          userTier: "paid_high",
        }}
      >
        <div>workspace</div>
      </StrategyAgentContextProvider>
    );

    expect(useAgentContextMock).toHaveBeenCalledWith(
      expect.objectContaining({
        description: expect.stringContaining("User-safe"),
        value: expect.objectContaining({
          activeConversationId: "conv_1",
          latestMarketSnapshot: {
            change_percent: 1.2,
            label: "Bitcoin",
            provider: "CCXT",
            symbol: "BTC",
          },
          selectedArtifactId: "art_1",
        }),
      })
    );
    expect(JSON.stringify(useAgentContextMock.mock.calls)).not.toContain("trace_hidden");
  });
});

describe("useStrategyCopilotCapabilities", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it("maps CopilotKit capabilities into product feature gates", () => {
    useCapabilitiesMock.mockReturnValue({
      humanInTheLoop: { approvals: true, interrupts: true },
      reasoning: { supported: true },
      state: { snapshots: true },
      tools: { clientProvided: true },
      transport: { streaming: true },
    });
    const seen: unknown[] = [];

    function Harness() {
      seen.push(useStrategyCopilotCapabilities());
      return null;
    }

    render(<Harness />);
    expect(seen[0]).toMatchObject({
      frontendTools: true,
      hitl: true,
      reasoning: true,
      sharedState: true,
      streaming: true,
      suggestions: true,
    });
  });
});
