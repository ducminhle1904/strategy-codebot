import { describe, expect, it } from "vitest";

import {
  actionRegistryLookup,
  actionToolActivityLabel,
  actionToolLabel,
  actionToolPrompt,
} from "./action-tool-metadata";

describe("action tool metadata", () => {
  it("does not maintain local prompt truth without registry metadata", () => {
    expect(actionToolPrompt("market_research")).toBeNull();
    expect(actionToolPrompt("repair")).toBeNull();
    expect(actionToolPrompt("run_backtest_preview")).toBeNull();
  });

  it("keeps unknown tool ids unavailable", () => {
    expect(actionToolPrompt("unknown_tool")).toBeNull();
    expect(actionToolActivityLabel("unknown_tool", "en", "started")).toBeNull();
  });

  it("requires backend registry metadata for activity labels", () => {
    expect(actionToolActivityLabel("run_risk_gate", "en", "started")).toBeNull();
    expect(actionToolActivityLabel("run_backtest_preview", "vi", "started")).toBeNull();
  });

  it("prefers backend registry metadata when available", () => {
    const registry = actionRegistryLookup([
      {
        available: true,
        artifact_kind: "robustness_report",
        category: "backtest",
        id: "robustness",
        label: "Backend robustness",
        next_state: "robustness_ready",
        presentation: {},
        prompt: "Use the backend prompt.",
        risk_level: "read_only",
        tool_id: "build_robustness_report",
      },
    ]);

    expect(actionToolLabel("build_robustness_report", registry)).toBe("Backend robustness");
    expect(actionToolPrompt("build_robustness_report", registry)).toBe("Use the backend prompt.");
    expect(actionToolActivityLabel("build_robustness_report", "en", "completed", registry)).toBe(
      "Backend robustness"
    );
  });
});
