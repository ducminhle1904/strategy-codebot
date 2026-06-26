import { describe, expect, it } from "vitest";

import type { Message, RunEvent } from "@/lib/backend-schemas";

import {
  AUTO_CHAIN_SUMMARY_TIMEOUT_EVENT,
  autoChainContinuationFromRunEvents,
  autoChainContinuationFromRunEvent,
  createAutoChainLocalEvent,
  hasAutoChainSummaryCompletedEvent,
  hasAutoChainSummaryMessage,
  mergeRunEvents,
  updateAutoChainContinuationFromRunEvent,
} from "./auto-chain-continuation";

const createdAt = "2026-06-22T00:00:00.000Z";

function runEvent(
  type: string,
  payload: RunEvent["payload"] = null,
  runId = "run_source",
  eventId = `evt_${type}`
): RunEvent {
  return {
    conversation_id: "conv_1",
    created_at: createdAt,
    event_id: eventId,
    payload,
    request_id: null,
    run_id: runId,
    sequence: 1,
    trace_id: null,
    type,
  };
}

function message(content: string, id = "msg_summary"): Message {
  return {
    content,
    conversation_id: "conv_1",
    created_at: createdAt,
    id,
    owner_user_id: "user_1",
    role: "assistant",
    workspace_id: "workspace_1",
  };
}

describe("auto-chain continuation", () => {
  it("starts from waiting event with child run id", () => {
    expect(
      autoChainContinuationFromRunEvent(
        runEvent("chat.auto_chain.waiting_for_backtest", {
          child_run_id: "run_child",
          status: "queued",
        })
      )
    ).toEqual({
      childRunId: "run_child",
      conversationId: "conv_1",
      sourceRunId: "run_source",
      status: "queued",
    });
  });

  it("tracks child run terminal status and summary completion", () => {
    const queued = autoChainContinuationFromRunEvent(
      runEvent("chat.auto_chain.waiting_for_backtest", { child_run_id: "run_child" })
    );

    const pending = updateAutoChainContinuationFromRunEvent(
      queued,
      runEvent("run.completed", { status: "completed" }, "run_child")
    );
    expect(pending?.status).toBe("summary_pending");

    const ready = updateAutoChainContinuationFromRunEvent(
      pending,
      runEvent("chat.auto_chain.summary.completed", { backtest_run_id: "run_child" }, "run_source")
    );
    expect(ready?.status).toBe("summary_ready");
  });

  it("derives continuation from hydrated event history", () => {
    const continuation = autoChainContinuationFromRunEvents([
      runEvent("chat.auto_chain.waiting_for_backtest", { child_run_id: "run_child" }),
      runEvent("backtest.execution.started", null, "run_child"),
      runEvent("run.completed", null, "run_child"),
    ]);

    expect(continuation).toMatchObject({
      childRunId: "run_child",
      status: "summary_pending",
    });
  });

  it("detects structured summary completion events", () => {
    expect(
      hasAutoChainSummaryCompletedEvent(
        [runEvent("chat.auto_chain.summary.completed", { backtest_run_id: "run_child" }, "run_source")],
        "run_child"
      )
    ).toBe(true);
  });

  it("detects failed child run and summary messages", () => {
    const queued = autoChainContinuationFromRunEvent(
      runEvent("chat.auto_chain.waiting_for_backtest", { child_run_id: "run_child" })
    );
    expect(
      updateAutoChainContinuationFromRunEvent(
        queued,
        runEvent("run.failed", { message: "worker failed" }, "run_child")
      )?.status
    ).toBe("failed");

    expect(
      hasAutoChainSummaryMessage(
        [
          message(
            "Backtest completed for BTC/USDT (run `run_child`): PnL 10, max drawdown 1, 3 trades, win rate 50. not TradingView official validation"
          ),
        ],
        "run_child"
      )
    ).toBe(true);
  });

  it("creates local timeout event and dedupes merged events", () => {
    const continuation = {
      childRunId: "run_child",
      conversationId: "conv_1",
      sourceRunId: "run_source",
      status: "summary_timeout" as const,
    };
    const timeoutEvent = createAutoChainLocalEvent(
      continuation,
      AUTO_CHAIN_SUMMARY_TIMEOUT_EVENT,
      "Summary is still being prepared."
    );
    const merged = mergeRunEvents(
      [runEvent("run.completed", null, "run_child", "evt_terminal")],
      [timeoutEvent, timeoutEvent]
    );

    expect(timeoutEvent.type).toBe(AUTO_CHAIN_SUMMARY_TIMEOUT_EVENT);
    expect(merged.filter((event) => event.event_id === timeoutEvent.event_id)).toHaveLength(1);
  });

  it("keeps the same merged event reference for duplicate-only batches", () => {
    const current = [runEvent("run.completed", null, "run_child", "evt_terminal")];

    expect(mergeRunEvents(current, [runEvent("run.completed", null, "run_child", "evt_terminal")])).toBe(
      current
    );
  });
});
