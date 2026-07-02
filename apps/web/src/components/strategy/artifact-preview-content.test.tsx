import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import type { ArtifactPreviewResponse } from "@/lib/backend-schemas";

import { ArtifactPreviewContent } from "./artifact-preview-content";

vi.mock("@/components/ai-elements/code-block", () => ({
  CodeBlock: ({ code, language }: { code: string; language: string }) => (
    <pre data-language={language}>{code}</pre>
  ),
}));

vi.mock("recharts", () => {
  const Chart = (_props: { children?: ReactNode }) => <div />;
  return {
    Area: Chart,
    AreaChart: Chart,
    Bar: Chart,
    BarChart: Chart,
    CartesianGrid: Chart,
    Cell: () => null,
    Pie: Chart,
    PieChart: Chart,
    ReferenceLine: Chart,
    ResponsiveContainer: Chart,
    Scatter: Chart,
    ScatterChart: Chart,
    Tooltip: Chart,
    XAxis: Chart,
    YAxis: Chart,
  };
});

function preview(overrides: Partial<ArtifactPreviewResponse>): ArtifactPreviewResponse {
  return {
    conversation_id: "conv_1",
    created_at: "2026-06-17T12:00:00+00:00",
    display_name: "Artifact",
    id: "artifact_1",
    kind: "pine_code",
    language: "pine",
    line_count: 1,
    metadata_json: null,
    mime_type: "text/plain",
    owner_user_id: "usr_1",
    presentation: {
      dedupe_key: "code:run_1",
      is_primary: true,
      language_hint: "pine",
      user_kind: "code",
      viewer_kind: "code",
      visibility: "user",
    },
    preview: "strategy('Demo')",
    preview_summary: null,
    raw_available: true,
    run_id: "run_1",
    truncated: false,
    workspace_id: "wsp_1",
    ...overrides,
  };
}

describe("ArtifactPreviewContent", () => {
  it("renders code artifacts through the shared code branch", () => {
    render(<ArtifactPreviewContent preview={preview({ preview: "plot(close)" })} />);

    expect(screen.getByText("plot(close)")).toBeVisible();
    expect(screen.getByText("plot(close)")).toHaveAttribute("data-language", "javascript");
  });

  it("renders trade previews as a bounded table", () => {
    render(
      <ArtifactPreviewContent
        preview={preview({
          kind: "backtest_trades",
          presentation: {
            dedupe_key: "trades:run_1",
            is_primary: false,
            language_hint: "json",
            user_kind: "report",
            viewer_kind: "trades",
            visibility: "user",
          },
          preview: [
            {
              commission: 1.25,
              entry_price: 100,
              entry_time: "2026-06-17T12:00:00Z",
              exit_price: 110,
              exit_time: "2026-06-17T13:00:00Z",
              pnl: 10,
              qty: 2,
              side: "long",
            },
          ],
        })}
      />
    );

    expect(screen.getByText("Side")).toBeVisible();
    expect(screen.getByText("long")).toBeVisible();
    expect(screen.getByText("10")).toBeVisible();
  });

  it("renders backtest report previews with the report card", () => {
    render(
      <ArtifactPreviewContent
        preview={preview({
          kind: "backtest_report",
          presentation: {
            dedupe_key: "report:run_1",
            is_primary: false,
            language_hint: "json",
            user_kind: "report",
            viewer_kind: "backtest_report",
            visibility: "user",
          },
          preview: {
            assumptions: { data_source: "public-readonly-cache", symbol: "BTC/USDT", timeframe: "1h" },
            metrics: { pnl: 123.45, trade_count: 4 },
            warnings: ["Local preview only."],
          },
        })}
      />
    );

    expect(screen.getByText("Local preview evidence")).toBeVisible();
    expect(screen.getByText("Trade count")).toBeVisible();
    expect(screen.getByText("Local preview only.")).toBeVisible();
  });

  it("suppresses the legacy backtest boundary sentence in plan previews", () => {
    const boundary = [
      "Local sandbox preview only",
      "not TradingView proof, broker proof, live trading evidence, or a profitability claim.",
    ].join("; ");

    render(
      <ArtifactPreviewContent
        preview={preview({
          kind: "backtest_plan",
          presentation: {
            dedupe_key: "plan:approval_1",
            is_primary: true,
            language_hint: "json",
            user_kind: "report",
            viewer_kind: "backtest_plan",
            visibility: "user",
          },
          preview: {
            approval_id: "approval_1",
            backtest_config: { symbol: "BTCUSDT", timeframe: "1h" },
            warnings: [boundary],
          },
        })}
      />
    );

    expect(screen.getByText("Approval required")).toBeVisible();
    expect(screen.queryByText(boundary)).not.toBeInTheDocument();
  });
});
