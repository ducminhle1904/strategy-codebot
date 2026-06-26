import type { Message, RunEvent } from "@/lib/backend-schemas";

export const AUTO_CHAIN_SUMMARY_PENDING_EVENT = "chat.auto_chain.summary.pending";
export const AUTO_CHAIN_SUMMARY_TIMEOUT_EVENT = "chat.auto_chain.summary.timeout";

export type AutoChainContinuationStatus =
  | "queued"
  | "running"
  | "summary_pending"
  | "summary_ready"
  | "summary_timeout"
  | "failed"
  | "cancelled";

export type AutoChainContinuation = {
  childRunId: string;
  conversationId: string;
  sourceRunId: string;
  status: AutoChainContinuationStatus;
};

export const AUTO_CHAIN_TERMINAL_STATUSES = new Set<AutoChainContinuationStatus>([
  "summary_ready",
  "summary_timeout",
  "failed",
  "cancelled",
]);

export function autoChainContinuationFromRunEvent(
  event: RunEvent
): AutoChainContinuation | null {
  if (
    event.type !== "chat.auto_chain.waiting_for_backtest" &&
    event.type !== "chat.auto_chain.step.completed"
  ) {
    return null;
  }
  const childRunId = payloadString(event, "child_run_id");
  if (!childRunId) {
    return null;
  }
  return {
    childRunId,
    conversationId: event.conversation_id,
    sourceRunId: event.run_id,
    status: "queued",
  };
}

export function updateAutoChainContinuationFromRunEvent(
  current: AutoChainContinuation | null,
  event: RunEvent
): AutoChainContinuation | null {
  if (!current) {
    return autoChainContinuationFromRunEvent(event);
  }
  const detected = autoChainContinuationFromRunEvent(event);
  if (detected) {
    return detected;
  }
  if (
    event.type === "chat.auto_chain.summary.completed" &&
    (event.run_id === current.sourceRunId ||
      event.run_id === current.childRunId ||
      payloadString(event, "backtest_run_id") === current.childRunId)
  ) {
    return withStatus(current, "summary_ready");
  }
  if (event.run_id !== current.childRunId) {
    return current;
  }
  if (event.type === "run.completed") {
    return withStatus(current, "summary_pending");
  }
  if (event.type === "run.failed") {
    return withStatus(current, "failed");
  }
  if (event.type === "run.cancelled") {
    return withStatus(current, "cancelled");
  }
  if (
    event.type === "backtest.data.planning" ||
    event.type === "backtest.data.cache_reusing" ||
    event.type === "backtest.data.fetching" ||
    event.type === "backtest.data.exporting" ||
    event.type === "backtest.execution.started" ||
    event.type === "backtest.indexing.started" ||
    event.type === "backtest.report.completed"
  ) {
    return withStatus(current, "running");
  }
  return current;
}

export function autoChainContinuationFromRunEvents(
  events: RunEvent[]
): AutoChainContinuation | null {
  let continuation: AutoChainContinuation | null = null;
  for (const event of events) {
    continuation = updateAutoChainContinuationFromRunEvent(continuation, event);
  }
  return continuation;
}

export function hasAutoChainSummaryCompletedEvent(
  events: RunEvent[],
  childRunId: string
): boolean {
  return events.some(
    (event) =>
      event.type === "chat.auto_chain.summary.completed" &&
      (event.run_id === childRunId || payloadString(event, "backtest_run_id") === childRunId)
  );
}

export function hasAutoChainSummaryMessage(messages: Message[], childRunId: string): boolean {
  return messages.some((message) => {
    if (message.role !== "assistant") {
      return false;
    }
    const content = message.content;
    return (
      content.includes(childRunId) &&
      content.includes("Backtest completed for") &&
      content.includes("not TradingView official validation")
    );
  });
}

export function createAutoChainLocalEvent(
  continuation: AutoChainContinuation,
  type:
    | typeof AUTO_CHAIN_SUMMARY_PENDING_EVENT
    | typeof AUTO_CHAIN_SUMMARY_TIMEOUT_EVENT
    | "chat.auto_chain.waiting_for_backtest",
  message: string
): RunEvent {
  return {
    conversation_id: continuation.conversationId,
    created_at: new Date().toISOString(),
    event_id: `local-${type}-${continuation.childRunId}`,
    payload: {
      child_run_id: continuation.childRunId,
      message,
    },
    request_id: null,
    run_id: type === "chat.auto_chain.waiting_for_backtest" ? continuation.sourceRunId : continuation.childRunId,
    sequence: Number.MAX_SAFE_INTEGER,
    trace_id: null,
    type,
  };
}

function withStatus(
  continuation: AutoChainContinuation,
  status: AutoChainContinuationStatus
): AutoChainContinuation {
  return continuation.status === status ? continuation : { ...continuation, status };
}

export function mergeRunEvents(current: RunEvent[], incoming: RunEvent[], limit = 60): RunEvent[] {
  if (!incoming.length) {
    return current;
  }
  const merged = new Map<string, RunEvent>();
  for (const event of [...current, ...incoming]) {
    merged.set(event.event_id, event);
  }
  const next = [...merged.values()]
    .sort((left, right) => {
      const leftTime = Date.parse(left.created_at);
      const rightTime = Date.parse(right.created_at);
      if (leftTime !== rightTime) {
        return leftTime - rightTime;
      }
      return left.sequence - right.sequence;
    })
    .slice(-limit);
  if (
    next.length === current.length &&
    next.every((event, index) => event.event_id === current[index]?.event_id)
  ) {
    return current;
  }
  return next;
}

function payloadString(event: RunEvent, key: string): string | null {
  const value = event.payload?.[key];
  return typeof value === "string" && value.trim() ? value : null;
}
