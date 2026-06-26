import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StrategyCopilotTools } from "./copilot-tools";

const useConfigureSuggestionsMock = vi.fn();
const useFrontendToolMock = vi.fn();
const useHumanInTheLoopMock = vi.fn();
const useRenderToolMock = vi.fn();
const useSuggestionsMock = vi.fn(() => ({
  clearSuggestions: vi.fn(),
  isLoading: false,
  reloadSuggestions: vi.fn(),
  suggestions: [],
}));

vi.mock("@copilotkit/react-core/v2", () => ({
  useConfigureSuggestions: (...args: unknown[]) => useConfigureSuggestionsMock(...args),
  useFrontendTool: (...args: unknown[]) => useFrontendToolMock(...args),
  useHumanInTheLoop: (...args: unknown[]) => useHumanInTheLoopMock(...args),
  useRenderTool: (...args: unknown[]) => useRenderToolMock(...args),
  useSuggestions: () => useSuggestionsMock(),
}));

describe("StrategyCopilotTools", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it("registers frontend tools, HITL tools, renderers, and suggestions", async () => {
    const callbacks = {
      focusComposer: vi.fn(),
      insertStrategyBlock: vi.fn(),
      openArtifactWorkspace: vi.fn(),
      openCreateSpec: vi.fn(),
      selectArtifact: vi.fn(),
      useMarketSnapshotForStrategy: vi.fn(),
    };

    render(<StrategyCopilotTools callbacks={callbacks} suggestions={[]} />);

    expect(useConfigureSuggestionsMock).toHaveBeenCalledWith(
      expect.objectContaining({ maxSuggestions: 3, minSuggestions: 1 }),
      []
    );
    expect(useFrontendToolMock.mock.calls.map((call) => call[0].name)).toEqual(
      expect.arrayContaining([
        "focus_composer",
        "insert_strategy_block",
        "open_artifact_workspace",
        "open_create_spec",
        "select_artifact",
        "use_market_snapshot_for_strategy",
      ])
    );
    expect(useHumanInTheLoopMock.mock.calls.map((call) => call[0].name)).toEqual(
      expect.arrayContaining([
        "confirm_apply_market_context",
        "confirm_backtest_preview",
        "confirm_regenerate_artifact",
        "confirm_validation_repair",
      ])
    );
    expect(useRenderToolMock.mock.calls.map((call) => call[0].name)).toEqual(
      expect.arrayContaining(["*", "artifact_anchor"])
    );

    const openArtifactTool = useFrontendToolMock.mock.calls
      .map((call) => call[0])
      .find((tool) => tool.name === "open_artifact_workspace");
    await openArtifactTool.handler({}, { signal: new AbortController().signal });
    expect(callbacks.openArtifactWorkspace).toHaveBeenCalledTimes(1);
  });
});
