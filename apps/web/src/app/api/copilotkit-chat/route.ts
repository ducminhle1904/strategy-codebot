import {
  errorMessageFromUnknown,
  knowledgeSourcesFromPythonEvent,
  marketSnapshotFromPythonEvent,
  parseSseFrames,
  reasoningSummaryFromPythonEvent,
  responseIntentFromPythonEvent,
  strategyWorkflowFromPythonEvent,
  suggestionsFromPythonEvent,
  textFromPythonEvent,
  webSourcesFromPythonEvent,
  type PythonSseEvent,
} from "@/lib/chat-stream";
import { agentLog } from "@/lib/agent-log";
import { backtestTradesTableFromToolOutput } from "@/lib/backtest-trades-inline-table";
import { MessageModeSchema, WebSearchModeSchema } from "@/lib/backend-schemas";
import { workflowToolEventConfig } from "@/lib/copilot-workflow-events";
import { normalizeLanguage, type LanguagePreference } from "@/lib/i18n";
import { createServerBackendClient } from "@/lib/server-auth";
import { splitCompleteSseFrames } from "@/lib/sse";
import { copilotRuntimeInfo } from "./runtime-info";

export const runtime = "nodejs";

const MAX_SSE_BUFFER_BYTES = 1024 * 1024;
const FIRST_EVENT_TIMEOUT_MS = readTimeoutMs("STRATEGY_CODEBOT_CHAT_FIRST_EVENT_TIMEOUT_MS", 90_000);
const IDLE_EVENT_TIMEOUT_MS = readTimeoutMs("STRATEGY_CODEBOT_CHAT_IDLE_TIMEOUT_MS", 60_000);
const TOTAL_STREAM_TIMEOUT_MS = readTimeoutMs("STRATEGY_CODEBOT_CHAT_TOTAL_TIMEOUT_MS", 180_000);
const LOGGABLE_BACKEND_SSE_EVENTS = new Set([
  "artifact.created",
  "backtest.preview.approval_required",
  "backtest.preview.failed",
  "backtest.preview.queued",
  "chat.action_plan",
  "knowledge.candidate.created",
  "knowledge.candidate.approved",
  "knowledge.candidate.auto_reviewed",
  "knowledge.candidate.auto_approved",
  "knowledge.candidate.needs_review",
  "knowledge.candidate.auto_rejected",
  "knowledge.candidate.rejected",
  "knowledge.learning.completed",
  "knowledge.learning.failed",
  "run.completed",
  "run.failed",
  "tool.completed",
  "tool.failed",
  "tool.started",
]);

type AgUiRunInput = {
  forwardedProps?: Record<string, unknown>;
  messages?: Array<{
    content?: unknown;
    role?: string;
  }>;
  runId?: string;
  threadId?: string;
};

type CopilotSingleEndpointEnvelope = {
  body?: AgUiRunInput;
  method?: string;
  params?: {
    agentId?: string;
  };
};

type CopilotStreamState = {
  reasoningId?: string;
  reasoningOpen?: boolean;
  textId?: string;
  textOpen?: boolean;
  toolCallIds?: Map<string, string>;
};

const SAFE_TOOL_PAYLOAD_FIELDS = [
  "artifact_id",
  "artifactId",
  "decision",
  "display_name",
  "interval",
  "label",
  "market",
  "message",
  "platform",
  "provider",
  "status",
  "symbol",
  "timeframe",
  "title",
] as const;

const SAFE_RUN_EVENT_PAYLOAD_FIELDS = [
  ...SAFE_TOOL_PAYLOAD_FIELDS,
  "code",
  "approval_mode",
  "candidate_count",
  "candidate_id",
  "retryable",
  "proposed_count",
  "promoted_count",
  "promotion_decision",
  "quality_score",
  "rejected_count",
  "review_required_reason",
  "tool_id",
  "tool_name",
] as const;

export async function POST(request: Request) {
  const rawBody = await parseRequestBody(request);
  if (!rawBody) {
    return runtimeInfoResponse();
  }
  if (isRuntimeInfoRequest(rawBody)) {
    return runtimeInfoResponse();
  }
  if (isStopRequest(rawBody)) {
    return new Response(null, { status: 204 });
  }

  const body = unwrapRunInput(rawBody);
  const forwardedProps = body.forwardedProps ?? {};
  const language = normalizeLanguage(forwardedProps.language);
  const conversationId =
    stringValue(forwardedProps.conversationId) ?? conversationIdFromThreadId(body.threadId);
  const content = extractLastAgUiUserText(body.messages ?? []);
  const mode = modeValue(forwardedProps.mode);
  const webSearch = webSearchValue(forwardedProps.webSearch);
  const clientRequestId = stringValue(forwardedProps.clientRequestId);
  const headerTraceId = stringValue(request.headers.get("X-Trace-Id"));
  const headerRequestId = stringValue(request.headers.get("X-Request-Id"));
  const traceId =
    stringValue(forwardedProps.traceId) ??
    headerTraceId ??
    opaqueTraceId();
  const requestId = clientRequestId ?? headerRequestId ?? opaqueRequestId();
  const regenerateMessageId = stringValue(forwardedProps.messageId);
  const runId = body.runId ?? crypto.randomUUID();
  const idempotencyKey = clientRequestId ?? stringValue(body.runId) ?? requestId;
  const threadId = conversationId ?? body.threadId ?? "strategy-codebot";
  const routeStartedAt = Date.now();
  let agUiEventCount = 0;
  let customEventCount = 0;
  let textDeltaCount = 0;
  let finished = false;
  const pythonEventTypes = new Map<string, number>();
  const customEventNames = new Map<string, number>();
  const marketSnapshotSymbols = new Set<string>();
  let backendRunFailed = false;
  agentLog("info", "copilot.run.requested", {
    client_request_id: clientRequestId ?? null,
    component: "copilotkit",
    conversation_id: conversationId ?? null,
    copilot_run_id: runId,
    has_conversation_id: Boolean(conversationId),
    message_count: body.messages?.length ?? 0,
    method: requestMethod(rawBody),
    regenerate: forwardedProps.regenerate === true,
    regenerate_message_id: regenerateMessageId ?? null,
    request_id: requestId,
    text_len: content.length,
    thread_id: threadId,
    trace_id: traceId,
    web_search: webSearch,
  });

  if (!conversationId) {
    agentLog("warn", "copilot.run.ignored", {
      component: "copilotkit",
      copilot_run_id: runId,
      message_count: body.messages?.length ?? 0,
      method: requestMethod(rawBody),
      reason: "missing_conversation_id",
      request_id: requestId,
      thread_id: body.threadId ?? null,
      trace_id: traceId,
    });
    return new Response(null, { status: 204 });
  }

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const encoder = new TextEncoder();
      const write = (event: Record<string, unknown>) => {
        agUiEventCount += 1;
        if (event.type === "CUSTOM") {
          customEventCount += 1;
          const name = typeof event.name === "string" ? event.name : "unknown";
          incrementCount(customEventNames, name);
          if (name === "strategy.marketSnapshot") {
            const symbol = marketSnapshotSymbol(event.value);
            if (symbol) {
              marketSnapshotSymbols.add(symbol);
            }
          }
        }
        if (event.type === "TEXT_MESSAGE_CONTENT") {
          textDeltaCount += 1;
        }
        logAgUiServerDebug("emit AG-UI event", {
          event: agUiServerEventSummary(event),
          runId,
          threadId,
        });
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
      };
      const state: CopilotStreamState = {};

      write({
        input: body,
        runId,
        threadId,
        timestamp: Date.now(),
        type: "RUN_STARTED",
      });

      write({
        snapshot: {
          activeThreadId: threadId,
          capabilities: {
            customEvents: true,
            frontendTools: true,
            hitl: true,
            reasoning: true,
            sharedState: true,
            suggestions: true,
          },
          conversationId,
          workflow: "chat",
        },
        timestamp: Date.now(),
        type: "STATE_SNAPSHOT",
      });
      ensureAssistantTextMessageStarted(write, state);

      try {
        if (!conversationId) {
          throw new Error("conversationId is required.");
        }
        if (!content.trim()) {
          throw new Error(
            language === "vi" ? "Nội dung tin nhắn là bắt buộc." : "Message content is required."
          );
        }

        const client = await createServerBackendClient();
        const response = await client.streamMessage(
          conversationId,
          { content, language, web_search: webSearch },
          {
            idempotencyKey,
            mode,
            requestId,
            signal: request.signal,
            traceId,
          }
        );
        if (!response.body) {
          throw new Error("Backend did not return a stream.");
        }
        agentLog("info", "backend.stream.opened", {
          component: "copilotkit",
          conversation_id: conversationId,
          copilot_run_id: runId,
          request_id: requestId,
          status: response.status,
          thread_id: threadId,
          trace_id: traceId,
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let shouldCancelReader = false;
        let eventCount = 0;
        const streamStartedAt = Date.now();
        try {
          while (true) {
            const timeoutMs = eventCount === 0 ? FIRST_EVENT_TIMEOUT_MS : IDLE_EVENT_TIMEOUT_MS;
            const totalRemainingMs = TOTAL_STREAM_TIMEOUT_MS - (Date.now() - streamStartedAt);
            if (totalRemainingMs <= 0) {
              shouldCancelReader = true;
              throw new ChatStreamTimeoutError("total");
            }
            const { done, value } = await readWithTimeout(
              reader,
              Math.min(timeoutMs, totalRemainingMs),
              eventCount === 0 ? "first_event" : "idle"
            ).catch((error: unknown) => {
              if (error instanceof ChatStreamTimeoutError) {
                shouldCancelReader = true;
              }
              throw error;
            });
            if (done) {
              break;
            }
            buffer += decoder.decode(value, { stream: true });
            if (buffer.length > MAX_SSE_BUFFER_BYTES) {
              shouldCancelReader = true;
              throw new Error("Backend stream frame exceeded the maximum size.");
            }
            const split = splitCompleteSseFrames(buffer);
            buffer = split.remaining;
            for (const frame of split.frames) {
              for (const event of parseSseFrames(frame)) {
                eventCount += 1;
                recordPythonStreamEvent(event, pythonEventTypes);
                logPythonLifecycleEvent(event, {
                  conversationId,
                  requestId,
                  runId,
                  threadId,
                  traceId,
                });
                logAgUiServerDebug("received Python SSE event", {
                  event: pythonEventSummary(event),
                  runId,
                  threadId,
                });
                if (event.event === "run.failed") {
                  backendRunFailed = true;
                }
                writePythonEventAsAgUi(write, event, state, language);
              }
            }
          }
          for (const event of parseSseFrames(buffer)) {
            eventCount += 1;
            recordPythonStreamEvent(event, pythonEventTypes);
            logPythonLifecycleEvent(event, {
              conversationId,
              requestId,
              runId,
              threadId,
              traceId,
            });
            logAgUiServerDebug("received Python SSE event", {
              event: pythonEventSummary(event),
              runId,
              threadId,
            });
            if (event.event === "run.failed") {
              backendRunFailed = true;
            }
            writePythonEventAsAgUi(write, event, state, language);
          }
        } finally {
          if (request.signal.aborted || shouldCancelReader) {
            await reader.cancel().catch(() => undefined);
          }
          reader.releaseLock();
        }

        closeOpenAgUiMessages(write, state);
        finished = true;
        write({
          outcome: { type: "success" },
          runId,
          threadId,
          timestamp: Date.now(),
          type: "RUN_FINISHED",
        });
        agentLog("info", "copilot.run.finished", {
          ...terminalCopilotLogFields({
            agUiEventCount,
            backendRunFailed,
            conversationId,
            customEventCount,
            customEventNames,
            finished,
            marketSnapshotSymbols,
            pythonEventTypes,
            requestId,
            routeStartedAt,
            runId,
            textDeltaCount,
            threadId,
            traceId,
          }),
          agent_status: backendRunFailed ? "failed" : "completed",
          status: backendRunFailed ? "backend_failed" : "success",
        });
        controller.close();
      } catch (error) {
        closeOpenAgUiMessages(write, state);
        const isTimeout = error instanceof ChatStreamTimeoutError;
        agentLog(isTimeout ? "warn" : "error", "copilot.run.failed", {
          ...terminalCopilotLogFields({
            agUiEventCount,
            backendRunFailed,
            conversationId,
            customEventCount,
            customEventNames,
            finished,
            marketSnapshotSymbols,
            pythonEventTypes,
            requestId,
            routeStartedAt,
            runId,
            textDeltaCount,
            threadId,
            traceId,
          }),
          error: errorMessageFromUnknown(error),
          error_class: error instanceof Error ? error.name : typeof error,
          status: isTimeout ? "timeout" : "failed",
        });
        if (isTimeout) {
          writeAssistantTextAsAgUi(write, state, timeoutMessage(error.kind, language));
          closeOpenAgUiMessages(write, state);
          write({
            outcome: { reason: "provider_timeout", type: "interrupted" },
            runId,
            threadId,
            timestamp: Date.now(),
            type: "RUN_FINISHED",
          });
          controller.close();
          return;
        }
        write({
          code: "strategy_codebot_stream_error",
          message: errorMessageFromUnknown(error),
          timestamp: Date.now(),
          type: "RUN_ERROR",
        });
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Cache-Control": "no-cache, no-transform",
      "Content-Type": "text/event-stream; charset=utf-8",
    },
  });
}

export async function GET() {
  return runtimeInfoResponse();
}

function runtimeInfoResponse() {
  return Response.json(copilotRuntimeInfo());
}

function isRuntimeInfoRequest(body: AgUiRunInput | CopilotSingleEndpointEnvelope) {
  return (
    typeof body === "object" &&
    body !== null &&
    "method" in body &&
    body.method === "info"
  );
}

async function parseRequestBody(
  request: Request
): Promise<AgUiRunInput | CopilotSingleEndpointEnvelope | null> {
  try {
    return (await request.json()) as AgUiRunInput | CopilotSingleEndpointEnvelope;
  } catch {
    return null;
  }
}

function isStopRequest(body: AgUiRunInput | CopilotSingleEndpointEnvelope) {
  return (
    typeof body === "object" &&
    body !== null &&
    "method" in body &&
    body.method === "agent/stop"
  );
}

function unwrapRunInput(body: AgUiRunInput | CopilotSingleEndpointEnvelope): AgUiRunInput {
  if (
    typeof body === "object" &&
    body !== null &&
    "method" in body &&
    (body.method === "agent/run" || body.method === "agent/connect")
  ) {
    return body.body ?? {};
  }
  return body as AgUiRunInput;
}

function requestMethod(body: AgUiRunInput | CopilotSingleEndpointEnvelope) {
  return typeof body === "object" && body !== null && "method" in body
    ? body.method
    : "direct-run";
}

function conversationIdFromThreadId(threadId: string | undefined) {
  if (!threadId || !threadId.startsWith("conv_")) {
    return null;
  }
  return threadId;
}

function incrementCount(map: Map<string, number>, key: string) {
  map.set(key, (map.get(key) ?? 0) + 1);
}

function countMapSummary(map: ReadonlyMap<string, number>) {
  return [...map.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, count]) => `${key}:${count}`)
    .join(",");
}

function terminalCopilotLogFields(input: {
  agUiEventCount: number;
  backendRunFailed: boolean;
  conversationId: string;
  customEventCount: number;
  customEventNames: ReadonlyMap<string, number>;
  finished: boolean;
  marketSnapshotSymbols: ReadonlySet<string>;
  pythonEventTypes: ReadonlyMap<string, number>;
  requestId: string;
  routeStartedAt: number;
  runId: string;
  textDeltaCount: number;
  threadId: string;
  traceId: string;
}) {
  return {
    agui_events: input.agUiEventCount,
    backend_run_failed: input.backendRunFailed,
    component: "copilotkit",
    conversation_id: input.conversationId,
    copilot_run_id: input.runId,
    custom_event_count: input.customEventCount,
    custom_event_names: countMapSummary(input.customEventNames),
    duration_ms: Date.now() - input.routeStartedAt,
    finished: input.finished,
    market_symbols: [...input.marketSnapshotSymbols].join(","),
    python_event_types: countMapSummary(input.pythonEventTypes),
    request_id: input.requestId,
    text_deltas: input.textDeltaCount,
    thread_id: input.threadId,
    trace_id: input.traceId,
  };
}

function logPythonLifecycleEvent(
  event: PythonSseEvent,
  context: {
    conversationId: string;
    requestId: string;
    runId: string;
    threadId: string;
    traceId: string;
  }
) {
  const eventType = event.event || "unknown";
  if (!LOGGABLE_BACKEND_SSE_EVENTS.has(eventType)) {
    return;
  }
  if (
    eventType === "message.delta" ||
    eventType === "model.reasoning.delta" ||
    eventType === "progress.update"
  ) {
    return;
  }
  const data = event.data && typeof event.data === "object" ? event.data : {};
  const payload =
    "payload" in data && data.payload && typeof data.payload === "object"
      ? (data.payload as Record<string, unknown>)
      : {};
  const backendRunId = stringValue((data as Record<string, unknown>).run_id) ?? stringValue(payload.run_id);
  const backendTraceId =
    stringValue((data as Record<string, unknown>).trace_id) ?? stringValue(payload.trace_id);
  const toolId = stringValue(payload.tool_id) ?? stringValue(payload.tool_name);
  const artifactKind = stringValue(payload.artifact_kind) ?? stringValue(payload.kind);
  const status = stringValue(payload.status);
  const decision = stringValue(payload.decision);
  const failureCode = stringValue(payload.code);
  const confidence =
    typeof payload.confidence === "number" ? Math.round(payload.confidence * 1000) / 1000 : undefined;
  agentLog("info", "backend.sse.event", {
    artifact_kind: artifactKind,
    backend_run_id: backendRunId,
    component: "copilotkit",
    confidence,
    conversation_id: context.conversationId,
    copilot_run_id: context.runId,
    decision,
    error_class: stringValue(payload.error),
    event_type: eventType,
    failure_code: eventType === "run.failed" ? failureCode : undefined,
    failure_message: eventType === "run.failed" ? stringValue(payload.message) : undefined,
    output_status: stringValue(payload.output_status),
    request_id: context.requestId,
    status,
    thread_id: context.threadId,
    tool_id: toolId,
    trace_id: backendTraceId ?? context.traceId,
  });
}

function opaqueTraceId() {
  return `trace_${crypto.randomUUID().replace(/-/g, "")}`;
}

function opaqueRequestId() {
  return `req_${crypto.randomUUID().replace(/-/g, "")}`;
}

function logAgUiServerDebug(message: string, details: Record<string, unknown>) {
  if (
    process.env.STRATEGY_CODEBOT_DEBUG_AG_UI !== "true" &&
    process.env.NEXT_PUBLIC_DEBUG_AG_UI !== "true"
  ) {
    return;
  }
  const { event, ...rest } = details;
  agentLog("info", "copilot.debug", {
    component: "copilotkit",
    debug_message: message,
    debug_event: event,
    ...rest,
  });
}

function agUiServerEventSummary(event: Record<string, unknown>) {
  const summary: Record<string, unknown> = {
    messageId: typeof event.messageId === "string" ? event.messageId : null,
    name: typeof event.name === "string" ? event.name : null,
    type: typeof event.type === "string" ? event.type : "unknown",
  };
  if (typeof event.delta === "string") {
    summary.deltaLength = event.delta.length;
  }
  if (event.type === "CUSTOM" && event.value && typeof event.value === "object") {
    const value = event.value as Record<string, unknown>;
    summary.valueKeys = Object.keys(value).sort();
    if (typeof value.symbol === "string") {
      summary.symbol = value.symbol;
    }
    if (typeof value.type === "string") {
      summary.runEventType = value.type;
    }
  }
  return summary;
}

function pythonEventSummary(event: PythonSseEvent) {
  const data = event.data && typeof event.data === "object" ? event.data : {};
  return {
    event: event.event || "unknown",
    hasData: Boolean(event.data),
    id: event.id ?? null,
    keys: Object.keys(data).sort(),
    runId: typeof data.run_id === "string" ? data.run_id : null,
    sequence: typeof data.sequence === "number" ? data.sequence : null,
  };
}

function recordPythonStreamEvent(
  event: PythonSseEvent,
  pythonEventTypes: Map<string, number>
) {
  incrementCount(pythonEventTypes, event.event || "unknown");
}

function marketSnapshotSymbol(value: unknown) {
  if (!value || typeof value !== "object") {
    return null;
  }
  const symbol = (value as Record<string, unknown>).symbol;
  return typeof symbol === "string" && symbol.trim() ? symbol.trim() : null;
}

function writePythonEventAsAgUi(
  write: (event: Record<string, unknown>) => void,
  event: PythonSseEvent,
  state: CopilotStreamState,
  language: LanguagePreference
) {
  const reasoning = reasoningSummaryFromPythonEvent(event);
  writePythonEventActivityAsAgUi(write, event);
  writePythonToolEventAsAgUi(write, event, state);
  writePythonEventMetadataAsAgUi(write, event);

  if (event.event !== "message.delta" && !reasoning) {
    write({
      name: "strategy.runEvent",
      timestamp: Date.now(),
      type: "CUSTOM",
      value: userSafeRunEventValue(event),
    });
  }

  if (reasoning) {
    if (!state.reasoningId) {
      state.reasoningId = `reasoning-${crypto.randomUUID()}`;
    }
    if (!state.reasoningOpen) {
      write({
        messageId: state.reasoningId,
        timestamp: Date.now(),
        type: "REASONING_START",
      });
      write({
        messageId: state.reasoningId,
        role: "reasoning",
        timestamp: Date.now(),
        type: "REASONING_MESSAGE_START",
      });
      state.reasoningOpen = true;
    }
    write({
      delta: `- ${reasoning.text}\n`,
      messageId: state.reasoningId,
      timestamp: Date.now(),
      type: "REASONING_MESSAGE_CONTENT",
    });
    return;
  }

  const text = textFromPythonEvent(event, language);
  if (!text) {
    return;
  }
  ensureAssistantTextMessageStarted(write, state);
  write({
    delta: text,
    messageId: state.textId,
    timestamp: Date.now(),
    type: "TEXT_MESSAGE_CONTENT",
  });
}

function ensureAssistantTextMessageStarted(
  write: (event: Record<string, unknown>) => void,
  state: CopilotStreamState
) {
  if (!state.textId) {
    state.textId = `msg-${crypto.randomUUID()}`;
  }
  if (state.textOpen) {
    return;
  }
  write({
    messageId: state.textId,
    role: "assistant",
    timestamp: Date.now(),
    type: "TEXT_MESSAGE_START",
  });
  state.textOpen = true;
}

function writePythonEventActivityAsAgUi(
  write: (event: Record<string, unknown>) => void,
  event: PythonSseEvent
) {
  const label = userSafeActivityLabel(event);
  if (!label) {
    return;
  }
  write({
    delta: [
      {
        op: "add",
        path: "/-",
        value: {
          id: event.data.event_id ?? event.id ?? `${event.event}-${Date.now()}`,
          label,
          status: activityStatus(event),
        },
      },
    ],
    timestamp: Date.now(),
    type: "ACTIVITY_DELTA",
  });
}

function writePythonToolEventAsAgUi(
  write: (event: Record<string, unknown>) => void,
  event: PythonSseEvent,
  state: CopilotStreamState
) {
  const phase = toolEventPhase(event.event);
  if (!phase) {
    return;
  }
  const payload = eventPayload(event);
  const toolName =
    toolNameForWorkflowEvent(event.event) ??
    safeToolName(payload.tool_id) ??
    safeToolName(payload.tool_name);
  if (!toolName) {
    return;
  }
  const key =
    typeof payload.tool_call_id === "string"
      ? payload.tool_call_id
      : `${event.data.run_id ?? "run"}:${toolName}`;
  state.toolCallIds ??= new Map();
  let toolCallId = state.toolCallIds.get(key);
  if (!toolCallId) {
    ensureAssistantTextMessageStarted(write, state);
    toolCallId = `tool-${crypto.randomUUID()}`;
    state.toolCallIds.set(key, toolCallId);
    write({
      parentMessageId: state.textId,
      timestamp: Date.now(),
      toolCallId,
      toolCallName: toolName,
      type: "TOOL_CALL_START",
    });
  }
  const shouldUsePayloadAsArgs =
    phase === "started" &&
    event.event !== "tool.started" &&
    payload.input === undefined &&
    payload.args === undefined &&
    payload.parameters === undefined;
  const args = sanitizeToolArgs(
    payload.input ??
      payload.args ??
      payload.parameters ??
      (shouldUsePayloadAsArgs ? payload : undefined)
  );
  if (args) {
    write({
      delta: JSON.stringify(args),
      timestamp: Date.now(),
      toolCallId,
      type: "TOOL_CALL_ARGS",
    });
  }
  if (phase === "completed" || phase === "failed") {
    write({
      timestamp: Date.now(),
      toolCallId,
      type: "TOOL_CALL_END",
    });
    const backtestReport = backtestReportCustomValue(toolName, payload);
    if (backtestReport) {
      write({
        name: "strategy.backtestReport",
        timestamp: Date.now(),
        type: "CUSTOM",
        value: backtestReport,
      });
    }
    const inlineTable = inlineTableCustomValue(toolName, payload);
    if (inlineTable) {
      write({
        name: "strategy.inlineTable",
        timestamp: Date.now(),
        type: "CUSTOM",
        value: inlineTable,
      });
    }
    write({
      messageId: `tool-result-${crypto.randomUUID()}`,
      result: JSON.stringify(
        sanitizeToolResult(
          payload.output ??
            (phase === "failed"
              ? {
                  message: payload.message ?? "Action failed",
                  status: "failed",
                }
              : payload)
        )
      ),
      role: "tool",
      timestamp: Date.now(),
      toolCallId,
      toolName,
      type: "TOOL_CALL_RESULT",
    });
  }
}

function backtestReportCustomValue(toolName: string, payload: Record<string, unknown>) {
  if (toolName !== "get_backtest_summary") {
    return null;
  }
  const output = payload.output;
  if (!output || typeof output !== "object") {
    return null;
  }
  const summary = (output as Record<string, unknown>).summary;
  if (!summary || typeof summary !== "object") {
    return null;
  }
  return { report: summary };
}

function inlineTableCustomValue(toolName: string, payload: Record<string, unknown>) {
  if (toolName !== "query_backtest_trades") {
    return null;
  }
  return backtestTradesTableFromToolOutput(payload.output);
}

function toolEventPhase(eventType: string): "completed" | "failed" | "started" | null {
  if (eventType === "tool.started") {
    return "started";
  }
  if (eventType === "tool.completed") {
    return "completed";
  }
  return workflowToolEventConfig(eventType)?.phase ?? null;
}

function toolNameForWorkflowEvent(eventType: string) {
  return workflowToolEventConfig(eventType)?.toolName ?? null;
}

function writePythonEventMetadataAsAgUi(
  write: (event: Record<string, unknown>) => void,
  event: PythonSseEvent
) {
  const responseIntent = responseIntentFromPythonEvent(event);
  if (responseIntent) {
    write({
      name: "strategy.responseIntent",
      timestamp: Date.now(),
      type: "CUSTOM",
      value: { intent: responseIntent },
    });
  }

  const sources = [
    ...knowledgeSourcesFromPythonEvent(event),
    ...webSourcesFromPythonEvent(event),
  ];
  if (sources.length > 0) {
    write({
      name: "strategy.sources",
      timestamp: Date.now(),
      type: "CUSTOM",
      value: { sources },
    });
  }

  const marketSnapshot = marketSnapshotFromPythonEvent(event);
  if (marketSnapshot) {
    write({
      name: "strategy.marketSnapshot",
      timestamp: Date.now(),
      type: "CUSTOM",
      value: marketSnapshot,
    });
  }

  const suggestions = suggestionsFromPythonEvent(event);
  if (suggestions) {
    write({
      name: "strategy.suggestions",
      timestamp: Date.now(),
      type: "CUSTOM",
      value: suggestions,
    });
  }

  const workflow = strategyWorkflowFromPythonEvent(event);
  if (workflow) {
    write({
      name: "strategy.workflow",
      timestamp: Date.now(),
      type: "CUSTOM",
      value: workflow,
    });
  }

}

function writeAssistantTextAsAgUi(
  write: (event: Record<string, unknown>) => void,
  state: CopilotStreamState,
  text: string
) {
  if (!state.textId) {
    state.textId = `msg-${crypto.randomUUID()}`;
  }
  if (!state.textOpen) {
    write({
      messageId: state.textId,
      role: "assistant",
      timestamp: Date.now(),
      type: "TEXT_MESSAGE_START",
    });
    state.textOpen = true;
  }
  write({
    delta: text,
    messageId: state.textId,
    timestamp: Date.now(),
    type: "TEXT_MESSAGE_CONTENT",
  });
}

function closeOpenAgUiMessages(
  write: (event: Record<string, unknown>) => void,
  state: CopilotStreamState
) {
  if (state.textOpen && state.textId) {
    write({
      messageId: state.textId,
      timestamp: Date.now(),
      type: "TEXT_MESSAGE_END",
    });
    state.textOpen = false;
  }
  if (state.reasoningOpen) {
    const reasoningId = state.reasoningId;
    if (reasoningId) {
      write({
        messageId: reasoningId,
        timestamp: Date.now(),
        type: "REASONING_MESSAGE_END",
      });
      write({
        messageId: reasoningId,
        timestamp: Date.now(),
        type: "REASONING_END",
      });
    }
    state.reasoningOpen = false;
  }
}

function extractLastAgUiUserText(messages: NonNullable<AgUiRunInput["messages"]>) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role !== "user") {
      continue;
    }
    return agUiContentText(message.content);
  }
  return "";
}

function agUiContentText(content: unknown): string {
  if (typeof content === "string") {
    return content;
  }
  if (!Array.isArray(content)) {
    return "";
  }
  return content
    .map((part) => {
      if (!part || typeof part !== "object") {
        return "";
      }
      const record = part as Record<string, unknown>;
      return record.type === "text" && typeof record.text === "string" ? record.text : "";
    })
    .join("");
}

function stringValue(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  return trimmed ? trimmed : undefined;
}

class ChatStreamTimeoutError extends Error {
  constructor(readonly kind: "first_event" | "idle" | "total") {
    super(`chat stream ${kind} timeout`);
    this.name = "ChatStreamTimeoutError";
  }
}

async function readWithTimeout(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  timeoutMs: number,
  kind: ChatStreamTimeoutError["kind"]
) {
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      reader.read(),
      new Promise<never>((_, reject) => {
        timeoutId = setTimeout(() => reject(new ChatStreamTimeoutError(kind)), timeoutMs);
      }),
    ]);
  } finally {
    if (timeoutId) {
      clearTimeout(timeoutId);
    }
  }
}

function timeoutMessage(
  kind: ChatStreamTimeoutError["kind"],
  language: LanguagePreference = "en"
) {
  const isVi = language === "vi";
  if (kind === "first_event") {
    return isVi
      ? "AI provider khởi động lâu hơn bình thường. Hãy thử lại sau khi model warm up."
      : "The AI provider is taking longer than usual to start. Try again after the model warms up.";
  }
  if (kind === "idle") {
    return isVi
      ? "AI provider ngừng gửi progress quá lâu. Hãy thử lại."
      : "The AI provider stopped sending progress for too long. Try again.";
  }
  return isVi
    ? "AI response vượt quá thời gian chờ tối đa. Hãy thử request ngắn hơn."
    : "The AI response exceeded the maximum wait time. Try again with a shorter request.";
}

function readTimeoutMs(name: string, fallback: number) {
  const value = Number(process.env[name]);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function webSearchValue(value: unknown) {
  return WebSearchModeSchema.safeParse(value).data ?? "auto";
}

function modeValue(value: unknown) {
  return MessageModeSchema.safeParse(value).data ?? "agent";
}

function eventPayload(event: PythonSseEvent): Record<string, unknown> {
  const payload = event.data.payload;
  return payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
}

function safeToolName(value: unknown) {
  if (typeof value !== "string" || !value.trim()) {
    return null;
  }
  const toolName = value.trim();
  return /^[a-zA-Z0-9_.-]{1,80}$/.test(toolName) ? toolName : null;
}

function sanitizeToolArgs(value: unknown) {
  const record = value && typeof value === "object" ? (value as Record<string, unknown>) : null;
  if (!record) {
    return null;
  }
  return pickUserSafeFields(record, SAFE_TOOL_PAYLOAD_FIELDS);
}

function sanitizeToolResult(value: unknown) {
  const record = value && typeof value === "object" ? (value as Record<string, unknown>) : null;
  if (!record) {
    return typeof value === "string" ? { summary: value.slice(0, 240) } : {};
  }
  return pickUserSafeFields(record, SAFE_TOOL_PAYLOAD_FIELDS);
}

function pickUserSafeFields(
  record: Record<string, unknown>,
  allowed: readonly string[]
) {
  const result: Record<string, unknown> = {};
  for (const key of allowed) {
    const value = record[key];
    if (
      typeof value === "string" ||
      typeof value === "number" ||
      typeof value === "boolean" ||
      value === null
    ) {
      result[key] = value;
    }
  }
  return Object.keys(result).length > 0 ? result : null;
}

function userSafeActivityLabel(event: PythonSseEvent) {
  if (event.event === "tool.started") {
    return "Running agent step";
  }
  if (event.event === "tool.completed") {
    const payload = eventPayload(event);
    if (payload.status === "failed" || payload.status === "error") {
      return "Agent step failed";
    }
    return "Agent step complete";
  }
  const workflowEvent = workflowToolEventConfig(event.event);
  if (workflowEvent) {
    return workflowEvent.activityLabel;
  }
  if (event.event === "chat.market_snapshot") {
    return "Preparing market snapshot";
  }
  if (event.event === "chat.suggestions.updated") {
    return "Updating suggested actions";
  }
  if (event.event === "artifact.created") {
    return "Review artifact ready";
  }
  if (event.event === "knowledge.candidate.created") {
    return "Knowledge update proposed";
  }
  if (event.event === "knowledge.candidate.approved") {
    return "Knowledge update approved";
  }
  if (event.event === "knowledge.candidate.auto_reviewed") {
    return "Knowledge candidate reviewed";
  }
  if (event.event === "knowledge.candidate.auto_approved") {
    return "Knowledge update auto-approved";
  }
  if (event.event === "knowledge.candidate.needs_review") {
    return "Knowledge candidate needs review";
  }
  if (event.event === "knowledge.candidate.auto_rejected") {
    return "Knowledge update rejected";
  }
  if (event.event === "knowledge.candidate.rejected") {
    return "Knowledge update rejected";
  }
  if (event.event === "knowledge.learning.completed") {
    return "Knowledge review ready";
  }
  if (event.event === "knowledge.learning.failed") {
    return "Knowledge review failed";
  }
  if (event.event === "run.failed") {
    return "Response failed";
  }
  return null;
}

function activityStatus(event: PythonSseEvent) {
  const eventType = event.event;
  const payload = eventPayload(event);
  if (
    eventType === "tool.completed" &&
    (payload.status === "failed" || payload.status === "error")
  ) {
    return "failed";
  }
  const workflowEvent = workflowToolEventConfig(eventType);
  if (workflowEvent) {
    return workflowEvent.activityStatus;
  }
  if (eventType === "run.failed" || eventType.endsWith(".failed")) {
    return "failed";
  }
  if (eventType === "tool.started" || eventType.endsWith(".requested")) {
    return "running";
  }
  return "complete";
}

function userSafeRunEventValue(event: PythonSseEvent) {
  const value: Record<string, unknown> = {
    event: event.event,
  };
  const data = event.data && typeof event.data === "object" ? event.data : {};
  for (const key of ["event_id", "conversation_id", "run_id", "sequence", "type", "created_at"]) {
    const fieldValue = (data as Record<string, unknown>)[key];
    if (
      typeof fieldValue === "string" ||
      typeof fieldValue === "number" ||
      typeof fieldValue === "boolean"
    ) {
      value[key] = fieldValue;
    }
  }

  const payload = (data as Record<string, unknown>).payload;
  if (payload && typeof payload === "object") {
    const safePayload = sanitizeRunEventPayload(payload as Record<string, unknown>);
    if (safePayload) {
      value.payload = safePayload;
    }
  }
  return value;
}

function sanitizeRunEventPayload(payload: Record<string, unknown>) {
  const result = pickUserSafeFields(payload, SAFE_RUN_EVENT_PAYLOAD_FIELDS) ?? {};

  const input = sanitizeToolArgs(payload.input ?? payload.args ?? payload.parameters);
  if (input) {
    result.input = input;
  }
  const output = sanitizeToolResult(payload.output);
  if (output && Object.keys(output).length > 0) {
    result.output = output;
  }
  return Object.keys(result).length > 0 ? result : null;
}
