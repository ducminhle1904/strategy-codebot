import { describe, expect, it } from "vitest";

import type { NautilusRuntime, NautilusRuntimeEvent } from "@/lib/backend-schemas";

import {
  paperBotArtifactIds,
  paperBotDisplayName,
  paperBotEventSeverity,
  paperBotEventSummary,
  paperBotMatchesFilter,
  paperBotMetricPairs,
  paperBotRuntimeLabel,
  paperBotStateTone,
} from "./paper-bots-page-helpers";

const isoNow = "2026-06-17T12:00:00+00:00";

function runtime(overrides: Partial<NautilusRuntime> = {}): NautilusRuntime {
  return {
    account_id: "acct_paper",
    broker_connection_id: "paper",
    created_at: isoNow,
    data_subscriptions: [{ symbol: "BTC/USDT", timeframe: "1h" }],
    desired_state: "running",
    generation: 0,
    heartbeat_count: 3,
    heartbeat_metrics: { max_drawdown: 2.4, net_pnl: 120.5, ignored: { nested: true } },
    id: "rt_1",
    kill_switch_active: false,
    last_error: null,
    last_heartbeat_at: isoNow,
    last_heartbeat_event_at: isoNow,
    lease_until: null,
    manifest: { artifact_id: "art_manifest", name: "BTC paper bot" },
    mode: "paper",
    risk_policy_id: "risk_default",
    runtime_key: "runtime_key",
    started_at: isoNow,
    state: "running",
    stopped_at: null,
    strategy_ids: ["strategy_1"],
    stream_cursor: null,
    updated_at: isoNow,
    worker_id: "worker_1",
    ...overrides,
  };
}

function event(overrides: Partial<NautilusRuntimeEvent> = {}): NautilusRuntimeEvent {
  return {
    created_at: isoNow,
    event_id: "evt_1",
    payload: { message: "Simulated order intent emitted", order_artifact_id: "art_order" },
    runtime_id: "rt_1",
    sequence: 1,
    type: "order_intent",
    ...overrides,
  };
}

describe("PaperBotsPage helpers", () => {
  it("derives card labels and compact metrics from runtime data", () => {
    const bot = runtime();

    expect(paperBotDisplayName(bot)).toBe("BTC paper bot");
    expect(paperBotRuntimeLabel(bot)).toBe("BTC/USDT 1h");
    expect(
      paperBotRuntimeLabel(runtime({ data_subscriptions: [{ instrument: "ETH/USDT", interval: "5m" }] }))
    ).toBe("ETH/USDT 5m");
    expect(paperBotMetricPairs(bot)).toEqual([
      { label: "Net Pnl", value: "120.5" },
      { label: "Max Drawdown", value: "2.4" },
    ]);
  });

  it("filters paper bot status groups", () => {
    expect(paperBotMatchesFilter(runtime({ state: "running" }), "running")).toBe(true);
    expect(paperBotMatchesFilter(runtime({ desired_state: "stopping", state: "running" }), "paused")).toBe(true);
    expect(paperBotMatchesFilter(runtime({ last_error: { message: "worker failed" } }), "needs_attention")).toBe(true);
    expect(paperBotMatchesFilter(runtime({ state: "stopped" }), "stopped")).toBe(true);
  });

  it("maps risk and error events to prominent severity", () => {
    expect(paperBotStateTone(runtime({ kill_switch_active: true }))).toBe("danger");
    expect(paperBotEventSeverity(event({ type: "risk_block" }))).toBe("high");
    expect(paperBotEventSummary(event({ payload: { reason: "daily loss cap" }, type: "risk_block" }))).toBe(
      "daily loss cap"
    );
  });

  it("discovers runtime artifact ids from manifest and event payloads", () => {
    expect(
      paperBotArtifactIds(runtime({ manifest: { artifact_ids: ["art_manifest", "art_extra"] } }), [event()])
    ).toEqual(["art_manifest", "art_extra", "art_order"]);
  });
});
