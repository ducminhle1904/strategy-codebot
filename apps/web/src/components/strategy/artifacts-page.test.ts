import { describe, expect, it } from "vitest";

import type { Artifact } from "@/lib/backend-schemas";

import { getArtifactCardPreviewLines, getWorkspaceInventoryArtifacts } from "./artifacts-page-helpers";

function artifact(overrides: Partial<Artifact>): Artifact {
  return {
    conversation_id: "conv_1",
    created_at: "2026-06-17T12:00:00+00:00",
    display_name: "Artifact",
    id: "artifact_1",
    kind: "backtest_report",
    metadata_json: null,
    mime_type: "application/json",
    owner_user_id: "usr_1",
    presentation: {
      dedupe_key: "report:run_1",
      is_primary: false,
      language_hint: null,
      user_kind: "report",
      viewer_kind: "backtest_report",
      visibility: "user",
    },
    preview_summary: null,
    run_id: "run_1",
    workspace_id: "wsp_1",
    ...overrides,
  };
}

describe("ArtifactsPage helpers", () => {
  it("keeps user-visible report artifacts even when they are not primary drawer artifacts", () => {
    expect(
      getWorkspaceInventoryArtifacts([
        artifact({ id: "report_1" }),
        artifact({
          id: "trace_1",
          presentation: {
            dedupe_key: "trace:run_1",
            is_primary: false,
            language_hint: null,
            user_kind: "evidence",
            viewer_kind: "json",
            visibility: "internal",
          },
        }),
      ]).map((item) => item.id)
    ).toEqual(["report_1"]);
  });

  it("dedupes workspace inventory by artifact presentation key", () => {
    expect(
      getWorkspaceInventoryArtifacts([
        artifact({ id: "report_1" }),
        artifact({ id: "report_2" }),
        artifact({ id: "report_3", presentation: { ...artifact({}).presentation, dedupe_key: "report:run_2" } }),
      ]).map((item) => item.id)
    ).toEqual(["report_1", "report_3"]);
  });

  it("builds useful card preview lines without placeholder values", () => {
    expect(
      getArtifactCardPreviewLines(
        artifact({
          display_name: "Backtest report",
          preview_summary: {
            kind: "backtest_result",
            metrics: {
              max_drawdown_pct: "N/A",
              net_pnl: -12.5,
              trade_count: 14,
            },
            run_id: "run_1",
            symbol: "BTCUSDT",
            timeframe: "1h",
          },
        })
      )
    ).toEqual(["symbol: BTCUSDT", "timeframe: 1h", "net pnl: -12.5", "trade count: 14"]);

    expect(getArtifactCardPreviewLines(artifact({ kind: "pine_file", preview_summary: null }), 2)).toEqual([
      "backtest_report",
      "pine file",
    ]);
  });
});
