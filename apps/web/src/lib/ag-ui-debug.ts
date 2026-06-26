"use client";

import { isAgUiDebugEnabled } from "@/lib/chat-runtime-config";
import { useSyncExternalStore } from "react";

export type AgUiDebugRecord = {
  customName?: string;
  durationMs?: number;
  eventCount: number;
  hasText: boolean;
  messageId?: string;
  runId?: string;
  timestamp: number;
  type: string;
};

type AgUiDebugSnapshot = {
  customEventCount: number;
  events: AgUiDebugRecord[];
  firstTokenMs: number | null;
  lastRunId: string | null;
  textEventCount: number;
  totalDurationMs: number | null;
};

type SanitizedAgUiEvent = {
  customName?: string;
  messageId?: string;
  runId?: string;
  type: string;
};

const MAX_DEBUG_EVENTS = 80;
const EMPTY_SNAPSHOT: AgUiDebugSnapshot = {
  customEventCount: 0,
  events: [],
  firstTokenMs: null,
  lastRunId: null,
  textEventCount: 0,
  totalDurationMs: null,
};

let snapshot = EMPTY_SNAPSHOT;
const listeners = new Set<() => void>();

export function useAgUiDebugSnapshot() {
  return useSyncExternalStore(subscribeAgUiDebug, getAgUiDebugSnapshot, getAgUiDebugSnapshot);
}

export function createAgUiDebugRecorder() {
  return isAgUiDebugEnabled() ? new AgUiDebugRecorder() : null;
}

export class AgUiDebugRecorder {
  private customEventCount = 0;
  private eventCount = 0;
  private firstTokenAt: number | null = null;
  private lastRunId: string | undefined;
  private messagesChangedCount = 0;
  private runStartedAt: number | null = null;
  private textContentCount = 0;
  private textStartCount = 0;

  lifecycle(type: string, params: Record<string, unknown> = {}) {
    if (!isAgUiDebugEnabled()) {
      return;
    }
    if (type === "onRunInitialized") {
      this.resetRun();
      this.runStartedAt = Date.now();
      console.info("[strategy-ag-ui] run started", this.safeLog(params));
    }
    if (type === "onMessagesChanged") {
      this.messagesChangedCount += 1;
    }
    if (type === "onRunFinalized") {
      this.finalize("run finished", params);
    }
    if (type === "onRunFailed") {
      console.warn("[strategy-ag-ui] run error", this.safeLog(params));
      this.finalize("run error", params);
    }
    this.push(type, params);
  }

  event(event: unknown) {
    if (!isAgUiDebugEnabled()) {
      return;
    }
    const record = eventRecord(event);
    if (!record) {
      return;
    }

    this.eventCount += 1;
    this.lastRunId = record.runId ?? this.lastRunId;
    if (record.type === "RUN_STARTED") {
      this.resetRun();
      this.runStartedAt = Date.now();
      console.info("[strategy-ag-ui] run started", this.safeLog(record));
    }
    if (record.type === "TEXT_MESSAGE_START") {
      this.textStartCount += 1;
    }
    if (record.type === "TEXT_MESSAGE_CONTENT") {
      this.textContentCount += 1;
      if (this.firstTokenAt === null) {
        this.firstTokenAt = Date.now();
      }
      console.info("[strategy-ag-ui] text content received", this.safeLog(record));
    }
    if (record.type === "CUSTOM") {
      this.customEventCount += 1;
      if (record.customName !== "strategy.runEvent") {
        console.warn("[strategy-ag-ui] custom event not mapped", this.safeLog(record));
      }
    }
    if (record.type === "RUN_FINISHED") {
      this.finalize("run finished", record);
    }
    if (record.type === "RUN_ERROR") {
      if (this.textContentCount > 0) {
        console.warn("[strategy-ag-ui] run error after text content", this.safeLog(record));
      }
      this.finalize("run error", record);
    }
    this.push(record.type, record);
  }

  private finalize(label: "run error" | "run finished", params: Record<string, unknown>) {
    if (this.textStartCount > 0 && this.textContentCount === 0) {
      console.warn("[strategy-ag-ui] text started but no content received", this.safeLog(params));
    }
    if (label === "run finished" && this.textContentCount === 0) {
      console.warn("[strategy-ag-ui] run finished without assistant text", this.safeLog(params));
    }
    if (this.textContentCount > 0 && this.messagesChangedCount === 0) {
      console.warn("[strategy-ag-ui] text received but messages never changed", this.safeLog(params));
    }
    console.info(`[strategy-ag-ui] ${label}`, {
      customEventCount: this.customEventCount,
      durationMs: this.durationMs(),
      eventCount: this.eventCount,
      firstTokenMs: this.firstTokenMs(),
      runId: this.lastRunId,
      textEventCount: this.textContentCount,
    });
  }

  private firstTokenMs() {
    return this.runStartedAt !== null && this.firstTokenAt !== null
      ? this.firstTokenAt - this.runStartedAt
      : null;
  }

  private durationMs() {
    return this.runStartedAt !== null ? Date.now() - this.runStartedAt : null;
  }

  private push(type: string, params: Record<string, unknown>) {
    const record: AgUiDebugRecord = {
      customName: stringValue(params.customName),
      durationMs: this.durationMs() ?? undefined,
      eventCount: this.eventCount,
      hasText: this.textContentCount > 0,
      messageId: stringValue(params.messageId),
      runId: stringValue(params.runId) ?? this.lastRunId,
      timestamp: Date.now(),
      type,
    };
    const events = [...snapshot.events, record].slice(-MAX_DEBUG_EVENTS);
    snapshot = {
      customEventCount: this.customEventCount,
      events,
      firstTokenMs: this.firstTokenMs(),
      lastRunId: this.lastRunId ?? null,
      textEventCount: this.textContentCount,
      totalDurationMs: this.durationMs(),
    };
    emitAgUiDebugUpdate();
  }

  private resetRun() {
    this.customEventCount = 0;
    this.eventCount = 0;
    this.firstTokenAt = null;
    this.messagesChangedCount = 0;
    this.runStartedAt = Date.now();
    this.textContentCount = 0;
    this.textStartCount = 0;
  }

  private safeLog(params: Record<string, unknown>) {
    return {
      customName: stringValue(params.customName),
      durationMs: this.durationMs(),
      eventCount: this.eventCount,
      hasText: this.textContentCount > 0,
      messageId: stringValue(params.messageId),
      runId: stringValue(params.runId) ?? this.lastRunId,
    };
  }
}

function eventRecord(event: unknown): SanitizedAgUiEvent | null {
  if (!event || typeof event !== "object") {
    return null;
  }
  const record = event as Record<string, unknown>;
  const type = stringValue(record.type);
  if (!type) {
    return null;
  }
  return {
    customName: stringValue(record.name),
    messageId: stringValue(record.messageId),
    runId: stringValue(record.runId),
    type,
  };
}

function getAgUiDebugSnapshot() {
  return snapshot;
}

function subscribeAgUiDebug(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function emitAgUiDebugUpdate() {
  for (const listener of listeners) {
    listener();
  }
}

function stringValue(value: unknown) {
  return typeof value === "string" && value.trim() ? value : undefined;
}
