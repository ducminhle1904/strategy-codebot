import {
  errorMessageFromUnknown,
  extractLastUserText,
  isProviderAuthFailure,
  knowledgeSourcesFromPythonEvent,
  marketSnapshotFromPythonEvent,
  parseSseFrames,
  reasoningSummaryFromPythonEvent,
  responseIntentFromPythonEvent,
  runFailureMessage,
  suggestionsFromPythonEvent,
  textFromPythonEvent,
  webSourcesFromPythonEvent,
  type ChatRequestBody,
  type PythonSseEvent,
  type ResponseIntent,
} from "@/lib/chat-stream";
import { WebSearchModeSchema } from "@/lib/backend-schemas";
import { normalizeLanguage, type LanguagePreference } from "@/lib/i18n";
import { createServerBackendClient } from "@/lib/server-auth";
import {
  createUIMessageStream,
  createUIMessageStreamResponse,
  type UIMessage,
  type UIMessageStreamWriter,
} from "ai";

export const runtime = "nodejs";

const MAX_SSE_BUFFER_BYTES = 1024 * 1024;
const DEBUG_CHAT = process.env.STRATEGY_CODEBOT_WEB_DEBUG === "1";
const FIRST_EVENT_TIMEOUT_MS = readTimeoutMs("STRATEGY_CODEBOT_CHAT_FIRST_EVENT_TIMEOUT_MS", 45_000);
const IDLE_EVENT_TIMEOUT_MS = readTimeoutMs("STRATEGY_CODEBOT_CHAT_IDLE_TIMEOUT_MS", 60_000);
const TOTAL_STREAM_TIMEOUT_MS = readTimeoutMs("STRATEGY_CODEBOT_CHAT_TOTAL_TIMEOUT_MS", 180_000);

export async function POST(request: Request) {
  const body = (await request.json()) as ChatRequestBody;
  const content = extractLastUserText(body.messages ?? []);
  const requestStartedAt = Date.now();
  const mode = body.mode ?? "agent";
  const language = normalizeLanguage(body.language);
  const webSearch = normalizeWebSearchMode(body.webSearch);
  debugChat("request", {
    clientRequestId: body.clientRequestId ?? null,
    conversationId: body.conversationId ?? null,
    language,
    messageCount: body.messages?.length ?? 0,
    mode,
    webSearch,
  });

  const stream = createUIMessageStream<UIMessage>({
    async execute({ writer }) {
      const streamPartState: ChatStreamPartState = {};
      if (!content.trim()) {
        writer.write({
          errorText: language === "vi" ? "Nội dung tin nhắn là bắt buộc." : "Message content is required.",
          type: "error",
        });
        return;
      }

      try {
        if (!body.conversationId) {
          throw new Error("conversationId is required.");
        }

        const client = await createServerBackendClient();
        const response = await client.streamMessage(
          body.conversationId,
          { content, language, web_search: webSearch },
          {
            mode,
            signal: request.signal,
          }
        );
        debugChat("backend_stream_response", {
          clientRequestId: body.clientRequestId ?? null,
          conversationId: body.conversationId,
          language,
          mode,
          status: response.status,
          webSearch,
        });

        if (!response.body) {
          throw new Error("Backend did not return a stream.");
        }

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
            const split = buffer.split(/\n\n/);
            buffer = split.pop() ?? "";

            for (const frame of split) {
              for (const event of parseSseFrames(frame)) {
                eventCount += 1;
                logPythonEvent(event, mode, language);
                writePythonEvent(writer, event, streamPartState, language);
              }
            }
          }

          for (const event of parseSseFrames(buffer)) {
            eventCount += 1;
            logPythonEvent(event, mode, language);
            writePythonEvent(writer, event, streamPartState, language);
          }
        } finally {
          if (request.signal.aborted || shouldCancelReader) {
            await reader.cancel().catch(() => undefined);
          }
          reader.releaseLock();
        }

        closeOpenStreamParts(writer, streamPartState);
        writer.write({ finishReason: "stop", type: "finish" });
        debugChat("finish", {
          clientRequestId: body.clientRequestId ?? null,
          durationMs: Date.now() - requestStartedAt,
          eventCount,
          mode,
        });
      } catch (error) {
        const errorText =
          error instanceof ChatStreamTimeoutError
            ? timeoutMessage(error.kind, language)
            : error instanceof Response && error.status === 401
              ? language === "vi"
                ? "Phiên đăng nhập cần được xác thực lại. Hãy thử gửi lại sau vài giây hoặc refresh nếu vẫn bị lỗi."
                : "Your sign-in session needs to be refreshed. Try sending again in a few seconds, or refresh if it keeps failing."
            : errorMessageFromUnknown(error);
        debugChat("error", {
          clientRequestId: body.clientRequestId ?? null,
          durationMs: Date.now() - requestStartedAt,
          error: errorText,
          mode,
        });
        closeOpenStreamParts(writer, streamPartState);
        writer.write({
          errorText,
          type: "error",
        });
      }
    },
    onError: errorMessageFromUnknown,
    originalMessages: body.messages as UIMessage[] | undefined,
  });

  return createUIMessageStreamResponse({ stream });
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

type ChatStreamPartState = {
  reasoningId?: string;
  reasoningOpen?: boolean;
  responseIntent?: ResponseIntent;
  textId?: string;
  textOpen?: boolean;
};

function writePythonEvent(
  writer: UIMessageStreamWriter<UIMessage>,
  event: PythonSseEvent,
  streamPartState: ChatStreamPartState,
  language: LanguagePreference
) {
  const responseIntent = responseIntentFromPythonEvent(event);
  if (responseIntent) {
    streamPartState.responseIntent = responseIntent;
    writer.write({
      data: { intent: responseIntent },
      type: "data-responseIntent",
    });
  }

  const reasoning = reasoningSummaryFromPythonEvent(event);
  if (reasoning) {
    if (!streamPartState.reasoningId) {
      streamPartState.reasoningId = `rsn-${crypto.randomUUID()}`;
    }
    if (!streamPartState.reasoningOpen) {
      writer.write({ id: streamPartState.reasoningId, type: "reasoning-start" });
      streamPartState.reasoningOpen = true;
    }
    writer.write({
      delta: `- ${reasoning.text}\n`,
      id: streamPartState.reasoningId,
      type: "reasoning-delta",
    });
    return;
  }

  const text = textFromPythonEvent(event, language);
  if (text) {
    if (!streamPartState.textId) {
      streamPartState.textId = `txt-${crypto.randomUUID()}`;
    }
    if (!streamPartState.textOpen) {
      writer.write({ id: streamPartState.textId, type: "text-start" });
      streamPartState.textOpen = true;
    }
    writer.write({ delta: text, id: streamPartState.textId, type: "text-delta" });
    if (event.event === "message.delta") {
      return;
    }
  }
  const eventSources = [
    ...knowledgeSourcesFromPythonEvent(event),
    ...webSourcesFromPythonEvent(event),
  ];
  for (const source of eventSources) {
    if (source.type === "external" && source.url) {
      writer.write({
        sourceId: source.id,
        title: source.title,
        type: "source-url",
        url: source.url,
      });
      continue;
    }
    writer.write({
      mediaType: "text/plain",
      sourceId: source.id,
      title: source.title,
      type: "source-document",
    });
  }
  const marketSnapshot = marketSnapshotFromPythonEvent(event);
  if (marketSnapshot) {
    writer.write({
      data: marketSnapshot,
      type: "data-marketSnapshot",
    });
  }
  const suggestions = suggestionsFromPythonEvent(event);
  if (suggestions) {
    writer.write({
      data: suggestions,
      type: "data-suggestions",
    });
  }
  if (streamPartState.responseIntent === "docs_research" && eventSources.length > 0) {
    writer.write({
      data: { sources: eventSources.slice(0, 5) },
      transient: true,
      type: "data-docReference",
    });
  }
  writer.write({
    data: event.data,
    id: event.id,
    transient: true,
    type: "data-runEvent",
  });
}

function closeOpenStreamParts(
  writer: UIMessageStreamWriter<UIMessage>,
  streamPartState: ChatStreamPartState
) {
  if (streamPartState.reasoningOpen && streamPartState.reasoningId) {
    writer.write({ id: streamPartState.reasoningId, type: "reasoning-end" });
    streamPartState.reasoningOpen = false;
  }
  if (streamPartState.textOpen && streamPartState.textId) {
    writer.write({ id: streamPartState.textId, type: "text-end" });
    streamPartState.textOpen = false;
  }
}

function logPythonEvent(
  event: PythonSseEvent,
  mode: string,
  language: LanguagePreference = "en"
) {
  if (!DEBUG_CHAT) {
    return;
  }
  if (event.event === "run.failed") {
    console.warn(
      "[strategy-web-chat] run_failed",
      JSON.stringify({
        error: (event.data.payload as Record<string, unknown> | undefined)?.error ?? null,
        message: runFailureMessage(event, language),
        mode,
        providerAuthFailure: isProviderAuthFailure(event),
        runId: event.data.run_id ?? null,
      })
    );
    return;
  }
  console.info(
    "[strategy-web-chat] event",
    JSON.stringify({
      event: event.event,
      mode,
      runId: event.data.run_id ?? null,
    })
  );
}

function debugChat(message: string, payload: Record<string, unknown>) {
  if (!DEBUG_CHAT) {
    return;
  }
  console.info("[strategy-web-chat]", message, JSON.stringify(payload));
}

function normalizeWebSearchMode(value: unknown) {
  return WebSearchModeSchema.safeParse(value).data ?? "auto";
}
