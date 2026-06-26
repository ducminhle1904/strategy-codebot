import { describe, expect, it } from "vitest";

import {
  isProviderAuthFailure,
  isProviderTimeoutFailure,
  knowledgeSourcesFromPythonEvent,
  marketSnapshotFromPayload,
  marketSnapshotFromPythonEvent,
  reasoningSummaryFromPythonEvent,
  responseIntentFromPythonEvent,
  runFailureMessage,
  suggestionsFromPythonEvent,
  textFromPythonEvent,
  webSourcesFromPythonEvent,
  type PythonSseEvent,
} from "./chat-stream";

function runFailed(error: string): PythonSseEvent {
  return {
    data: {
      payload: {
        error,
        message: "Provider execution failed",
      },
    },
    event: "run.failed",
    id: "evt_1",
  };
}

function providerTimeout(): PythonSseEvent {
  return {
    data: {
      payload: {
        code: "provider_timeout",
        error: "ProviderTimeoutError",
        message: "The AI provider took too long to respond.",
      },
    },
    event: "run.failed",
    id: "evt_timeout",
  };
}

describe("chat stream helpers", () => {
  it("renders provider authentication failures as user-facing guidance", () => {
    const event = runFailed("AuthenticationError");

    expect(isProviderAuthFailure(event)).toBe(true);
    expect(runFailureMessage(event)).toContain("API key");
    expect(textFromPythonEvent(event)).toContain("try again");
  });

  it("normalizes context-aware suggestions", () => {
    expect(
      suggestionsFromPythonEvent({
        data: {
          payload: {
            actions: [
              {
                action: "send_prompt",
                artifact_kind: "risk_gate_report",
                category: "strategy",
                enabled: true,
                id: "use-market",
                kind: "chat_action",
                label: "Use for strategy",
                next_state: "risk_gate",
                presentation: {
                  badge_key: "review_required",
                  icon_key: "gauge",
                  visibility_key: "default",
                },
                priority: 1,
                prompt: "Use this context for a strategy.",
                reason: "Strategy context is ready for deterministic review.",
                required_inputs: ["stale_after"],
                risk_level: "review_required",
                tool_id: "run_risk_gate",
              },
            ],
            composer_blocks: [
              {
                action: "insert_or_update_block",
                category: "risk",
                enabled: true,
                id: "block-risk",
                kind: "composer_block",
                label: "Risk",
                priority: 1,
                slot: "risk",
                variants: [
                  {
                    id: "balanced",
                    insert_template: "Risk rules:\n- Risk 1%",
                    label: "Balanced",
                  },
                ],
              },
            ],
            context: {
              artifact_kinds: ["pine_file"],
              intent: "strategy_building",
              missing_fields: ["risk"],
              semantic_action_confidence: 0.82,
              semantic_action_intent: "strategy_evidence_review",
              semantic_action_source: "deterministic",
              semantic_suggested_actions: ["review_risk"],
            },
            version: 1,
          },
        },
        event: "chat.suggestions.updated",
      })
    ).toMatchObject({
      actions: [
        {
          artifact_kind: "risk_gate_report",
          id: "use-market",
          prompt: "Use this context for a strategy.",
          reason: "Strategy context is ready for deterministic review.",
          required_inputs: ["stale_after"],
          risk_level: "review_required",
          presentation: {
            badge_key: "review_required",
            icon_key: "gauge",
            visibility_key: "default",
          },
          tool_id: "run_risk_gate",
        },
      ],
      composer_blocks: [{ slot: "risk", variants: [{ label: "Balanced" }] }],
      context: {
        artifact_kinds: ["pine_file"],
        intent: "strategy_building",
        missing_fields: ["risk"],
        semantic_action_confidence: 0.82,
        semantic_action_intent: "strategy_evidence_review",
        semantic_action_source: "deterministic",
        semantic_suggested_actions: ["review_risk"],
      },
    });
  });

  it("renders generic run failures without technical detail references", () => {
    const message = textFromPythonEvent(runFailed("RuntimeError"));

    expect(message).toContain("The AI run failed");
    expect(message).not.toContain("technical details");
  });

  it("renders provider timeout failures as retryable guidance", () => {
    const event = providerTimeout();

    expect(isProviderTimeoutFailure(event)).toBe(true);
    expect(runFailureMessage(event)).toContain("took too long");
    expect(textFromPythonEvent(event)).toContain("try again");
  });

  it("renders workflow run failures without provider prefix", () => {
    const event: PythonSseEvent = {
      data: {
        payload: {
          code: "pine_validation_failed",
          dimension: "workflow",
          message: "Backtest plan failed because local Pine validation failed.",
        },
      },
      event: "run.failed",
      id: "evt_pine_validation_failed",
    };

    expect(runFailureMessage(event)).toBe("Backtest plan failed because local Pine validation failed.");
    expect(textFromPythonEvent(event)).toBe("Backtest plan failed because local Pine validation failed.");
  });

  it("does not duplicate run failure text when the server persisted an assistant message", () => {
    const event = {
      data: {
        payload: {
          assistant_message_persisted: true,
          error: "RuntimeError",
          message: "Provider execution failed",
        },
      },
      event: "run.failed",
      id: "evt_failed_persisted",
    };

    expect(runFailureMessage(event)).toContain("Provider execution failed");
    expect(textFromPythonEvent(event)).toBe("");
  });

  it("extracts user-facing knowledge sources from completed tool events", () => {
    expect(
      knowledgeSourcesFromPythonEvent({
        data: {
          payload: {
            output: {
              knowledge_context_summary: {
                sources: [
                  {
                    id: "tradingview-pine-strategies",
                    title: "TradingView Pine strategies",
                    type: "external",
                    url: "https://www.tradingview.com/pine-script-docs/",
                  },
                  {
                    id: "pine_v6_rules",
                    title: "Pine v6 rules",
                    type: "internal",
                  },
                ],
              },
            },
            tool_id: "knowledge_check",
          },
        },
        event: "tool.completed",
        id: "evt_sources",
      })
    ).toEqual([
      {
        id: "tradingview-pine-strategies",
        title: "TradingView Pine strategies",
        type: "external",
        url: "https://www.tradingview.com/pine-script-docs/",
      },
      {
        id: "pine_v6_rules",
        title: "Pine v6 rules",
        type: "internal",
      },
    ]);
  });

  it("ignores malformed knowledge source payloads", () => {
    expect(
      knowledgeSourcesFromPythonEvent({
        data: {
          payload: {
            output: {
              knowledge_context_summary: {
                sources: [
                  { id: "missing-title", type: "internal" },
                  { id: "external-without-url", title: "External", type: "external" },
                  { id: "valid", title: "Valid", type: "internal" },
                  { id: "valid", title: "Duplicate", type: "internal" },
                ],
              },
            },
            tool_id: "knowledge_check",
          },
        },
        event: "tool.completed",
      })
    ).toEqual([{ id: "valid", title: "Valid", type: "internal" }]);
  });

  it("extracts only safe reasoning summaries", () => {
    expect(
      reasoningSummaryFromPythonEvent({
        data: {
          payload: {
            phase: "context",
            safe: true,
            text: "Reading conversation context.",
          },
        },
        event: "model.reasoning.delta",
        id: "evt_reasoning",
      })
    ).toEqual({
      text: "Reading conversation context.",
    });

    expect(
      reasoningSummaryFromPythonEvent({
        data: {
          payload: {
            safe: false,
            text: "raw chain of thought",
          },
        },
        event: "model.reasoning.delta",
      })
    ).toBeNull();
  });

  it("extracts response intent and market snapshot data events", () => {
    const marketPayload = {
      approximate: true,
      change: 12.1,
      change_percent: 0.71,
      currency: "USD",
      freshness: "source_backed",
      label: "Market snapshot",
      provider: "Twelve Data",
      price: "$1,721.95",
      price_points: [
        { label: "A", value: 1 },
        { label: "B", value: 2 },
      ],
      source_count: 1,
      sources: [
        {
          id: "coindesk-eth",
          title: "ETH price source",
          type: "external",
          url: "https://example.com/eth",
        },
      ],
      symbol: "ETH",
    };

    expect(
      responseIntentFromPythonEvent({
        data: { payload: { intent: "market_snapshot", safe: true } },
        event: "chat.response_intent",
      })
    ).toBe("market_snapshot");

    expect(
      marketSnapshotFromPythonEvent({
        data: { payload: marketPayload },
        event: "chat.market_snapshot",
      })
    ).toMatchObject({
      approximate: true,
      label: "Market snapshot",
      source_count: 1,
      change_percent: 0.71,
      provider: "Twelve Data",
      price: "$1,721.95",
      symbol: "ETH",
    });
    expect(marketSnapshotFromPayload(marketPayload)).toMatchObject({
      price_points: [
        { label: "A", value: 1 },
        { label: "B", value: 2 },
      ],
      symbol: "ETH",
    });
  });

  it("extracts web sources from provider source events", () => {
    expect(
      webSourcesFromPythonEvent({
        data: {
          payload: {
            sources: [
              {
                id: "source-1",
                title: "Provider citation",
                type: "external",
                url: "https://example.com",
              },
            ],
          },
        },
        event: "web.sources",
      })
    ).toEqual([
      {
        id: "source-1",
        title: "Provider citation",
        type: "external",
        url: "https://example.com",
      },
    ]);
  });
});
