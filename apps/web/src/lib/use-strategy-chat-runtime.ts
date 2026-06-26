"use client";

import {
  emptyStrategyChatMessageMetadata,
  copilotAgentMessagesToStrategyMessages,
  mergeStrategyChatMessageMetadata,
  metadataPatchFromAgUiCustomEvent,
  metadataPatchFromAgUiReasoningEvent,
  type StrategyChatMessage,
  type StrategyChatMessageMetadata,
} from "@/lib/chat-ui";
import {
  configuredStrategyChatRuntime,
  type StrategyChatRuntimeName,
} from "@/lib/chat-runtime-config";
import { COPILOTKIT_CHAT_RUNTIME_URL, COPILOT_STRATEGY_AGENT_ID } from "@/lib/copilot-constants";
import { runFailureMessage } from "@/lib/chat-stream";
import { createAgUiDebugRecorder } from "@/lib/ag-ui-debug";
import type { LanguagePreference } from "@/lib/i18n";
import { splitCompleteSseFrames } from "@/lib/sse";
import {
  useAgent,
  UseAgentUpdate,
  type AgentSubscriber,
  type Message as AgUiMessage,
} from "@copilotkit/react-core/v2";
import { useEffect, useMemo, useRef, useState } from "react";

export type StrategyChatRuntimeStatus =
  | "error"
  | "ready"
  | "submitted"
  | "streaming";

export type StrategyChatRuntime = {
  configuredRuntime: StrategyChatRuntimeName;
  runtime: StrategyChatRuntimeName;
  messages: StrategyChatMessage[];
  status: StrategyChatRuntimeStatus;
  clearError: () => void;
  regenerate: (options: { body?: Record<string, unknown>; messageId: string }) => Promise<void>;
  sendMessage: (
    message: { text: string },
    options?: { body?: Record<string, unknown> }
  ) => Promise<void>;
  setMessagesFromConversationState: (messages: StrategyChatMessage[]) => void;
  stop: () => void;
};

type UseStrategyChatRuntimeOptions = {
  activeConversationId: string | null;
  initialMessages: StrategyChatMessage[];
  language: LanguagePreference;
  onData: (part: { type: string; data?: unknown }) => void;
  onError: (error: Error) => void;
  onFinish: () => void | Promise<void>;
  webSearchMode: string;
};

export function useStrategyChatRuntime({
  activeConversationId,
  initialMessages,
  language,
  onData,
  onError,
  onFinish,
  webSearchMode,
}: UseStrategyChatRuntimeOptions): StrategyChatRuntime {
  return useCopilotKitStrategyRuntime({
    activeConversationId,
    initialMessages,
    language,
    onData,
    onError,
    onFinish,
    webSearchMode,
  });
}

function useCopilotKitStrategyRuntime({
  activeConversationId,
  language,
  onData,
  onError,
  onFinish,
  webSearchMode,
}: UseStrategyChatRuntimeOptions): StrategyChatRuntime {
  const { agent } = useAgent({
    agentId: COPILOT_STRATEGY_AGENT_ID,
    updates: [
      UseAgentUpdate.OnMessagesChanged,
      UseAgentUpdate.OnRunStatusChanged,
      UseAgentUpdate.OnStateChanged,
    ],
  });
  const [messages, setMessages] = useState<AgUiMessage[]>(() => agent.messages);
  const [metadataByMessageId, setMetadataByMessageId] = useState(
    () => new Map<string, StrategyChatMessageMetadata>()
  );
  const [metadataByText, setMetadataByText] = useState(
    () => new Map<string, StrategyChatMessageMetadata>()
  );
  const [status, setStatus] = useState<StrategyChatRuntimeStatus>(
    agent.isRunning ? "streaming" : "ready"
  );
  const metadataByMessageIdRef = useRef(new Map<string, StrategyChatMessageMetadata>());
  const pendingMetadataRef = useRef<StrategyChatMessageMetadata>(
    emptyStrategyChatMessageMetadata()
  );
  const pendingHydrationMetadataRef = useRef<StrategyChatMessageMetadata>(
    emptyStrategyChatMessageMetadata()
  );
  const metadataByTextRef = useRef(new Map<string, StrategyChatMessageMetadata>());
  const textKeyByMessageIdRef = useRef(new Map<string, string>());
  const messagesRef = useRef(messages);
  const activeAssistantMessageIdRef = useRef<string | null>(null);
  const assistantIdsAtRunStartRef = useRef(new Set<string>());
  const streamedTextByMessageIdRef = useRef(new Map<string, string>());
  const finishHandledRef = useRef(false);
  const activeRunAbortRef = useRef<AbortController | null>(null);
  const beginCurrentRunRef = useRef<((nextMessages: AgUiMessage[]) => void) | null>(null);
  const handleAgUiEventRef = useRef<((event: Record<string, unknown>) => void) | null>(null);
  const finishCurrentRunRef = useRef<((nextMessages?: AgUiMessage[]) => Promise<void>) | null>(
    null
  );
  const pendingFailureTextRef = useRef<string | null>(null);
  const normalizedMetadataByMessageId = useMemo(
    () => metadataByMessageIdWithTextFallback(messages, metadataByMessageId, metadataByText),
    [messages, metadataByMessageId, metadataByText]
  );
  const normalizedMessages = useMemo(
    () =>
      copilotAgentMessagesToStrategyMessages(
        messages,
        normalizedMetadataByMessageId
      ),
    [messages, normalizedMetadataByMessageId]
  );
  const debugRecorder = useMemo(() => createAgUiDebugRecorder(), []);
  const callbacksRef = useRef({ onData, onError, onFinish });
  useEffect(() => {
    callbacksRef.current = { onData, onError, onFinish };
  }, [onData, onError, onFinish]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    const publishMetadata = (source = "unknown") => {
      logCopilotRuntimeInsight("info", "publish metadata", {
        idMetadataCount: metadataByMessageIdRef.current.size,
        source,
        textMetadataCount: metadataByTextRef.current.size,
      });
      setMetadataByMessageId(new Map(metadataByMessageIdRef.current));
      setMetadataByText(new Map(metadataByTextRef.current));
    };
    const publishMessages = (nextMessages: AgUiMessage[], source = "unknown") => {
      logCopilotRuntimeInsight("info", "publish messages", {
        messages: agUiMessagesSummary(nextMessages),
        source,
      });
      messagesRef.current = nextMessages;
      setMessages(nextMessages);
    };
    const rememberMetadataForMessageText = (
      messageId: string,
      metadata: StrategyChatMessageMetadata
    ) => {
      const message = messagesRef.current.find((item) => item.id === messageId);
      const textKey = message ? agUiMessageTextKey(message) : "";
      const persistableMetadata = stripLiveOnlyMetadata(metadata);
      if (textKey && hasStrategyMetadata(persistableMetadata)) {
        const previousTextKey = textKeyByMessageIdRef.current.get(messageId);
        if (previousTextKey && previousTextKey !== textKey) {
          metadataByTextRef.current.delete(previousTextKey);
        }
        textKeyByMessageIdRef.current.set(messageId, textKey);
        metadataByTextRef.current.set(textKey, persistableMetadata);
        logCopilotRuntimeInsight("info", "metadata remembered by text", {
          messageId,
          metadata: metadataSummary(persistableMetadata),
          textKeyLength: textKey.length,
        });
      }
    };
    const mergePatchIntoMessage = (
      messageId: string,
      patch: Partial<StrategyChatMessageMetadata>
    ) => {
      const metadata = mergeStrategyChatMessageMetadata(
        metadataByMessageIdRef.current.get(messageId),
        patch
      );
      metadataByMessageIdRef.current.set(messageId, metadata);
      rememberMetadataForMessageText(messageId, metadata);
      publishMetadata("mergePatchIntoMessage");
    };
    const mergePatchIntoPending = (
      patch: Partial<StrategyChatMessageMetadata>
    ) => {
      pendingMetadataRef.current = mergeStrategyChatMessageMetadata(
        pendingMetadataRef.current,
        patch
      );
      logCopilotRuntimeInsight("info", "metadata pending", {
        patch: metadataPatchKeys(patch),
      });
    };
    const flushPendingMetadata = (messageId: string) => {
      const pending = pendingMetadataRef.current;
      if (!hasAnyStrategyMetadata(pending)) {
        return;
      }
      logCopilotRuntimeInsight("info", "metadata attached", {
        messageId,
        metadata: metadataSummary(pending),
      });
      metadataByMessageIdRef.current.set(
        messageId,
        mergeStrategyChatMessageMetadata(
          metadataByMessageIdRef.current.get(messageId),
          pending
        )
      );
      pendingMetadataRef.current = emptyStrategyChatMessageMetadata();
      publishMetadata("flushPendingMetadata");
    };
    const discardPendingMetadata = (reason: string) => {
      if (!hasAnyStrategyMetadata(pendingMetadataRef.current)) {
        return;
      }
      logCopilotRuntimeInsight("warn", "metadata discarded", {
        metadata: metadataSummary(pendingMetadataRef.current),
        reason,
      });
      pendingMetadataRef.current = emptyStrategyChatMessageMetadata();
      publishMetadata(`discardPendingMetadata:${reason}`);
    };
    const mergePatchIntoActiveMessage = (
      patch: Partial<StrategyChatMessageMetadata>
    ) => {
      const messageId = activeAssistantMessageIdRef.current;
      if (messageId) {
        mergePatchIntoMessage(messageId, patch);
        return;
      }
      mergePatchIntoPending(patch);
    };
    const reconcileMetadataToMessages = (nextMessages: AgUiMessage[]) => {
      const messageIds = new Set(nextMessages.map((message) => message.id));
      const activeId = activeAssistantMessageIdRef.current;
      const latestRunAssistantId = latestRunAssistantMessageId(
        nextMessages,
        assistantIdsAtRunStartRef.current
      );
      if (activeId && latestRunAssistantId && !messageIds.has(activeId)) {
        const activeMetadata = metadataByMessageIdRef.current.get(activeId);
        if (activeMetadata) {
          metadataByMessageIdRef.current.set(
            latestRunAssistantId,
            mergeStrategyChatMessageMetadata(
              metadataByMessageIdRef.current.get(latestRunAssistantId),
              activeMetadata
            )
          );
          metadataByMessageIdRef.current.delete(activeId);
          activeAssistantMessageIdRef.current = latestRunAssistantId;
          logCopilotRuntimeInsight("warn", "metadata moved to latest assistant", {
            fromMessageId: activeId,
            metadata: metadataSummary(activeMetadata),
            toMessageId: latestRunAssistantId,
          });
          publishMetadata("reconcileMetadataToMessages:move-active");
        }
      }
      if (latestRunAssistantId) {
        flushPendingMetadata(latestRunAssistantId);
      }
      let changed = false;
      for (const message of nextMessages) {
        if (message.role !== "assistant") {
          continue;
        }
        const messageMetadata = metadataByMessageIdRef.current.get(message.id);
        const textKey = agUiMessageTextKey(message);
        if (messageMetadata && textKey) {
          const persistableMetadata = stripLiveOnlyMetadata(messageMetadata);
          if (hasStrategyMetadata(persistableMetadata)) {
            const previousTextKey = textKeyByMessageIdRef.current.get(message.id);
            if (previousTextKey && previousTextKey !== textKey) {
              metadataByTextRef.current.delete(previousTextKey);
            }
            textKeyByMessageIdRef.current.set(message.id, textKey);
            metadataByTextRef.current.set(textKey, persistableMetadata);
            changed = true;
            logCopilotRuntimeInsight("info", "metadata indexed during reconcile", {
              messageId: message.id,
              metadata: metadataSummary(persistableMetadata),
              textKeyLength: textKey.length,
            });
          }
          continue;
        }
        if (!messageMetadata && textKey) {
          const textMetadata = metadataByTextRef.current.get(textKey);
          if (textMetadata) {
            metadataByMessageIdRef.current.set(message.id, textMetadata);
            changed = true;
            logCopilotRuntimeInsight("warn", "metadata restored from text", {
              messageId: message.id,
              metadata: metadataSummary(textMetadata),
              textKeyLength: textKey.length,
            });
          }
        }
      }
      if (changed) {
        publishMetadata("reconcileMetadataToMessages");
      }
      logCopilotRuntimeInsight("info", "reconcile metadata completed", {
        activeAssistantId: activeAssistantMessageIdRef.current,
        changed,
        idMetadataCount: metadataByMessageIdRef.current.size,
        incomingMessages: agUiMessagesSummary(nextMessages),
        latestRunAssistantId,
        textMetadataCount: metadataByTextRef.current.size,
      });
    };
    const mergeWithOptimisticMessages = (nextMessages: AgUiMessage[]) => {
      const merged = mergeAgUiMessageLists(messagesRef.current, nextMessages);
      const messageIds = new Set(merged.map((message) => message.id));
      let restoredStreamedCount = 0;
      for (const [messageId, text] of streamedTextByMessageIdRef.current) {
        if (messageIds.has(messageId)) {
          continue;
        }
        const existing = messagesRef.current.find((message) => message.id === messageId);
        merged.push(
          existing ?? ({
            content: text,
            id: messageId,
            role: "assistant",
          } satisfies AgUiMessage)
        );
        messageIds.add(messageId);
        restoredStreamedCount += 1;
      }
      logCopilotRuntimeInsight("info", "merge optimistic messages", {
        current: agUiMessagesSummary(messagesRef.current),
        incoming: agUiMessagesSummary(nextMessages),
        merged: agUiMessagesSummary(merged),
        restoredStreamedCount,
        streamedTextIds: [...streamedTextByMessageIdRef.current.keys()],
      });
      return merged;
    };
    const clearLiveReasoningMetadata = () => {
      let changed = pendingMetadataRef.current.reasoningSummaries.length > 0;
      pendingMetadataRef.current = stripLiveOnlyMetadata(pendingMetadataRef.current);
      for (const [messageId, metadata] of metadataByMessageIdRef.current) {
        if (metadata.reasoningSummaries.length === 0) {
          continue;
        }
        metadataByMessageIdRef.current.set(messageId, stripLiveOnlyMetadata(metadata));
        changed = true;
      }
      if (changed) {
        publishMetadata("clearLiveReasoningMetadata");
      }
    };
    const syncAgentMessages = (nextMessages: AgUiMessage[], source = "unknown") => {
      logCopilotRuntimeInsight("info", "sync agent messages", {
        incoming: agUiMessagesSummary(nextMessages),
        source,
      });
      const merged = mergeWithOptimisticMessages(nextMessages);
      reconcileMetadataToMessages(merged);
      publishMessages(merged, `syncAgentMessages:${source}`);
    };
    const beginCurrentRun = (nextMessages: AgUiMessage[]) => {
      debugRecorder?.lifecycle("onRunInitialized", {
        messageCount: nextMessages.length,
      });
      finishHandledRef.current = false;
      assistantIdsAtRunStartRef.current = assistantMessageIds(messagesRef.current);
      streamedTextByMessageIdRef.current = new Map();
      activeAssistantMessageIdRef.current = null;
      pendingFailureTextRef.current = null;
      logCopilotRuntimeInsight("info", "run initialized", {
        assistantCountAtStart: assistantIdsAtRunStartRef.current.size,
        incomingMessageCount: nextMessages.length,
        incoming: agUiMessagesSummary(nextMessages),
        local: agUiMessagesSummary(messagesRef.current),
      });
      syncAgentMessages(nextMessages, "beginCurrentRun");
      setStatus("streaming");
    };
    const ensureAssistantMessage = (messageId: string) => {
      if (messagesRef.current.some((message) => message.id === messageId)) {
        return;
      }
      logCopilotRuntimeInsight("info", "assistant placeholder ensured", {
        messageId,
      });
      publishMessages([
        ...messagesRef.current,
        { content: "", id: messageId, role: "assistant" } satisfies AgUiMessage,
      ], "ensureAssistantMessage");
    };
    const appendAssistantDelta = (messageId: string, delta: string) => {
      const existingText =
        streamedTextByMessageIdRef.current.get(messageId) ??
        agUiMessagePlainText(messagesRef.current.find((message) => message.id === messageId));
      const nextText = mergeAssistantTextDelta(existingText, delta);
      if (nextText === existingText) {
        logCopilotRuntimeInsight("warn", "assistant delta ignored as duplicate", {
          deltaLength: delta.length,
          messageId,
          textLength: existingText.length,
        });
        return;
      }
      streamedTextByMessageIdRef.current.set(messageId, nextText);
      const nextMessages = messagesRef.current.some((message) => message.id === messageId)
        ? messagesRef.current.map((message) =>
            message.id === messageId
              ? ({ content: nextText, id: messageId, role: "assistant" } satisfies AgUiMessage)
              : message
          )
        : [
            ...messagesRef.current,
            { content: nextText, id: messageId, role: "assistant" } satisfies AgUiMessage,
          ];
      logCopilotRuntimeInsight("info", "assistant delta appended", {
        deltaLength: delta.length,
        messageId,
        nextTextLength: nextText.length,
      });
      publishMessages(nextMessages, "appendAssistantDelta");
      const metadata = metadataByMessageIdRef.current.get(messageId);
      if (metadata) {
        rememberMetadataForMessageText(messageId, metadata);
        publishMetadata("appendAssistantDelta:rememberMetadata");
      }
    };
    const finishCurrentRun = async (nextMessages?: AgUiMessage[]) => {
      if (finishHandledRef.current) {
        return;
      }
      finishHandledRef.current = true;
      if (nextMessages) {
        syncAgentMessages(nextMessages, "finishCurrentRun");
      }
      const latestAssistantId = latestRunAssistantMessageId(
        messagesRef.current,
        assistantIdsAtRunStartRef.current
      );
      logCopilotRuntimeInsight(latestAssistantId ? "info" : "warn", "finish current run", {
        latestAssistantId,
        messages: agUiMessagesSummary(messagesRef.current),
        pendingFailure: Boolean(pendingFailureTextRef.current),
      });
      const pendingFailureText = pendingFailureTextRef.current;
      if (!latestAssistantId && pendingFailureText) {
        const messageId = `msg-${crypto.randomUUID()}`;
        publishMessages([
          ...messagesRef.current,
          { content: pendingFailureText, id: messageId, role: "assistant" } satisfies AgUiMessage,
        ], "finishCurrentRun:failureFallback");
        activeAssistantMessageIdRef.current = messageId;
        flushPendingMetadata(messageId);
      }
      if (
        !latestAssistantId &&
        !pendingFailureText &&
        hasAnyStrategyMetadata(pendingMetadataRef.current)
      ) {
        pendingHydrationMetadataRef.current = mergeStrategyChatMessageMetadata(
          pendingHydrationMetadataRef.current,
          pendingMetadataRef.current
        );
        logCopilotRuntimeInsight("warn", "metadata deferred to hydration", {
          metadata: metadataSummary(pendingHydrationMetadataRef.current),
        });
        pendingMetadataRef.current = emptyStrategyChatMessageMetadata();
      }
      pendingFailureTextRef.current = null;
      clearLiveReasoningMetadata();
      discardPendingMetadata("run finalized without new assistant message");
      setStatus("ready");
      activeAssistantMessageIdRef.current = null;
      await callbacksRef.current.onFinish();
    };
    const handleAgUiEvent = (eventRecord: Record<string, unknown>) => {
      debugRecorder?.event(eventRecord);
      logCopilotRuntimeInsight("info", "AG-UI event received", {
        event: agUiEventSummary(eventRecord),
        messages: agUiMessagesSummary(messagesRef.current),
      });
      if (eventRecord.type === "TEXT_MESSAGE_START" && "messageId" in eventRecord) {
        const messageId =
          typeof eventRecord.messageId === "string" ? eventRecord.messageId : null;
        if (messageId) {
          activeAssistantMessageIdRef.current = messageId;
          flushPendingMetadata(messageId);
          ensureAssistantMessage(messageId);
        }
      }
      if (eventRecord.type === "TEXT_MESSAGE_CONTENT" && "messageId" in eventRecord) {
        const messageId =
          typeof eventRecord.messageId === "string" ? eventRecord.messageId : null;
        if (messageId && !activeAssistantMessageIdRef.current) {
          activeAssistantMessageIdRef.current = messageId;
          flushPendingMetadata(messageId);
        }
        if (messageId && typeof eventRecord.delta === "string") {
          appendAssistantDelta(messageId, eventRecord.delta);
        }
      }
      if (eventRecord.type === "REASONING_MESSAGE_CONTENT") {
        const patch = metadataPatchFromAgUiReasoningEvent(eventRecord);
        if (patch) {
          mergePatchIntoActiveMessage(patch);
        }
      }
      if (eventRecord.type === "CUSTOM" && "name" in eventRecord) {
        const patch = metadataPatchFromAgUiCustomEvent(eventRecord);
        if (patch) {
          mergePatchIntoActiveMessage(patch);
        }
      }
      if (eventRecord.type === "CUSTOM" && eventRecord.name === "strategy.runEvent") {
        const failureText = failureTextFromRunEventValue(eventRecord.value, language);
        if (failureText) {
          pendingFailureTextRef.current = failureText;
        }
        callbacksRef.current.onData({
          data: eventRecord.value,
          type: "data-runEvent",
        });
      }
    };
    beginCurrentRunRef.current = beginCurrentRun;
    handleAgUiEventRef.current = handleAgUiEvent;
    finishCurrentRunRef.current = finishCurrentRun;
    const subscriber: AgentSubscriber = {
      onEvent: ({ event }) => {
        handleAgUiEvent(event as Record<string, unknown>);
      },
      onRunFailed: ({ error }) => {
        debugRecorder?.lifecycle("onRunFailed", {
          message: error.message,
        });
        logCopilotRuntimeInsight("warn", "run failed callback", {
          message: error.message,
          messages: agUiMessagesSummary(messagesRef.current),
        });
        clearLiveReasoningMetadata();
        setStatus("error");
        discardPendingMetadata("run failed");
        callbacksRef.current.onError(error);
      },
      onRunFinalized: async ({ messages: nextMessages }) => {
        debugRecorder?.lifecycle("onRunFinalized", {
          messageCount: nextMessages.length,
        });
        logCopilotRuntimeInsight("info", "run finalized callback", {
          incoming: agUiMessagesSummary(nextMessages as AgUiMessage[]),
          local: agUiMessagesSummary(messagesRef.current),
        });
        await finishCurrentRun(nextMessages as AgUiMessage[]);
      },
      onRunInitialized: ({ messages: nextMessages }) => {
        beginCurrentRun(nextMessages as AgUiMessage[]);
      },
      onMessagesChanged: ({ messages: nextMessages }) => {
        debugRecorder?.lifecycle("onMessagesChanged", {
          messageCount: nextMessages.length,
        });
        logCopilotRuntimeInsight("info", "messages changed callback", {
          incoming: agUiMessagesSummary(nextMessages as AgUiMessage[]),
          local: agUiMessagesSummary(messagesRef.current),
        });
        syncAgentMessages(nextMessages as AgUiMessage[], "onMessagesChanged");
      },
    };
    const subscription = agent.subscribe(subscriber);
    return () => {
      if (beginCurrentRunRef.current === beginCurrentRun) {
        beginCurrentRunRef.current = null;
      }
      if (handleAgUiEventRef.current === handleAgUiEvent) {
        handleAgUiEventRef.current = null;
      }
      if (finishCurrentRunRef.current === finishCurrentRun) {
        finishCurrentRunRef.current = null;
      }
      subscription.unsubscribe();
    };
  }, [agent, debugRecorder, language]);

  const runAgent = async (forwardedProps: Record<string, unknown>) => {
    try {
      finishHandledRef.current = false;
      assistantIdsAtRunStartRef.current = assistantMessageIds(messagesRef.current);
      activeRunAbortRef.current?.abort();
      const abortController = new AbortController();
      activeRunAbortRef.current = abortController;
      logCopilotRuntimeInsight("info", "runAgent start", {
        forwardedProps: sanitizeForwardedPropsForLog(forwardedProps),
        messages: agUiMessagesSummary(messagesRef.current),
      });
      beginCurrentRunRef.current?.(messagesRef.current);
      await streamCopilotAgUiRun({
        forwardedProps,
        messages: messagesRef.current,
        onEvent: (event) => {
          if (event.type === "RUN_FINISHED") {
            return;
          }
          if (event.type === "RUN_ERROR") {
            throw new Error(agUiRunErrorMessage(event));
          }
          handleAgUiEventRef.current?.(event);
        },
        signal: abortController.signal,
      });
      if (!finishHandledRef.current) {
        logCopilotRuntimeInsight("info", "direct AG-UI stream completed", {
          messages: agUiMessagesSummary(messagesRef.current),
        });
        await finishCurrentRunRef.current?.();
      }
    } catch (error) {
      if (isAbortError(error)) {
        logCopilotRuntimeInsight("warn", "runAgent aborted", {
          messages: agUiMessagesSummary(messagesRef.current),
        });
        setStatus("ready");
        return;
      }
      const normalizedError = error instanceof Error ? error : new Error(String(error));
      debugRecorder?.lifecycle("onRunFailed", {
        message: normalizedError.message,
      });
      logCopilotRuntimeInsight("warn", "runAgent failed", {
        message: normalizedError.message,
      });
      setStatus("error");
      callbacksRef.current.onError(normalizedError);
    } finally {
      activeRunAbortRef.current = null;
    }
  };

  return {
    clearError: () => setStatus(agent.isRunning ? "streaming" : "ready"),
    configuredRuntime: configuredStrategyChatRuntime(),
    messages: normalizedMessages,
    regenerate: async (options) => {
      setStatus("submitted");
      const conversationId = activeConversationId;
      await runAgent({
        ...(options.body ?? {}),
        conversationId,
        language,
        messageId: options.messageId,
        mode: "agent",
        regenerate: true,
        webSearch: webSearchMode,
      });
    },
    runtime: "copilotkit",
    sendMessage: async (message, options) => {
      const userMessage = {
        content: message.text,
        id: crypto.randomUUID(),
        role: "user",
      } satisfies AgUiMessage;
      assistantIdsAtRunStartRef.current = assistantMessageIds(messagesRef.current);
      const nextMessages = mergeAgUiMessageLists(messagesRef.current, [
        userMessage,
        ...agent.messages,
      ]);
      logCopilotRuntimeInsight("info", "send message optimistic user", {
        agentMessages: agUiMessagesSummary([...agent.messages]),
        nextMessages: agUiMessagesSummary(nextMessages),
        userMessageId: userMessage.id,
      });
      messagesRef.current = nextMessages;
      setMessages(nextMessages);
      setStatus("submitted");
      const requestBody = options?.body ?? {};
      const conversationId =
        typeof requestBody.conversationId === "string"
          ? requestBody.conversationId
          : activeConversationId;
      await runAgent({
        ...requestBody,
        conversationId,
        language,
        mode: "agent",
        webSearch: webSearchMode,
      });
    },
    setMessagesFromConversationState: (nextMessages) => {
      const agentMessages = nextMessages.flatMap(strategyMessageToAgUiMessage);
      const nextMetadata = new Map<string, StrategyChatMessageMetadata>();
      const nextTextMetadata = new Map<string, StrategyChatMessageMetadata>();
      const nextTextKeyByMessageId = new Map<string, string>();
      let preservedMetadataCount = 0;
      let attachedDeferredMetadata = false;
      const deferredMetadata = pendingHydrationMetadataRef.current;
      const deferredTargetId =
        hasAnyStrategyMetadata(deferredMetadata)
          ? latestRunAssistantMessageId(
              agentMessages,
              assistantIdsAtRunStartRef.current
            ) ?? latestAssistantMessageId(agentMessages)
          : null;
      for (const message of nextMessages) {
        const messageMetadata = strategyChatMessageMetadataFromStrategyMessage(message);
        const textKey = strategyMessageTextKey(message);
        const preservedMetadata =
          textKey && !hasStrategyMetadata(messageMetadata)
            ? metadataByTextRef.current.get(textKey)
            : undefined;
        const deferredMessageMetadata =
          message.id === deferredTargetId && !hasStrategyMetadata(messageMetadata)
            ? deferredMetadata
            : undefined;
        if (preservedMetadata || deferredMessageMetadata) {
          preservedMetadataCount += 1;
        }
        if (deferredMessageMetadata) {
          attachedDeferredMetadata = true;
        }
        const metadata = stripLiveOnlyMetadata(
          deferredMessageMetadata ?? preservedMetadata ?? messageMetadata
        );
        nextMetadata.set(message.id, metadata);
        if (textKey && hasStrategyMetadata(metadata)) {
          nextTextMetadata.set(textKey, metadata);
          nextTextKeyByMessageId.set(message.id, textKey);
        }
      }
      metadataByMessageIdRef.current = nextMetadata;
      metadataByTextRef.current = nextTextMetadata;
      textKeyByMessageIdRef.current = nextTextKeyByMessageId;
      pendingMetadataRef.current = emptyStrategyChatMessageMetadata();
      if (attachedDeferredMetadata) {
        pendingHydrationMetadataRef.current = emptyStrategyChatMessageMetadata();
      }
      pendingFailureTextRef.current = null;
      streamedTextByMessageIdRef.current = new Map();
      activeAssistantMessageIdRef.current = null;
      logCopilotRuntimeInsight("warn", "hydrate from conversation state", {
        agentMessages: agUiMessagesSummary(agentMessages),
        incomingStrategyMessages: nextMessages.length,
        nextMetadataCount: nextMetadata.size,
        nextTextMetadataCount: nextTextMetadata.size,
        pendingHydrationMetadata: metadataSummary(pendingHydrationMetadataRef.current),
        preservedMetadataCount,
        previousTextMetadataCount: metadataByTextRef.current.size,
      });
      agent.setMessages(agentMessages);
      messagesRef.current = agentMessages;
      setMessages(agentMessages);
      setMetadataByMessageId(new Map(nextMetadata));
      setMetadataByText(new Map(nextTextMetadata));
    },
    status,
    stop: () => {
      activeRunAbortRef.current?.abort();
      agent.abortRun();
      setStatus("ready");
    },
  };
}

type StreamCopilotAgUiRunOptions = {
  forwardedProps: Record<string, unknown>;
  messages: AgUiMessage[];
  onEvent: (event: Record<string, unknown>) => void;
  signal: AbortSignal;
};

async function streamCopilotAgUiRun({
  forwardedProps,
  messages,
  onEvent,
  signal,
}: StreamCopilotAgUiRunOptions) {
  const conversationId =
    typeof forwardedProps.conversationId === "string" ? forwardedProps.conversationId : null;
  const runId = crypto.randomUUID();
  const response = await fetch(COPILOTKIT_CHAT_RUNTIME_URL, {
    body: JSON.stringify({
      body: {
        forwardedProps,
        messages: messages.map((message) => ({
          content: message.content,
          id: message.id,
          role: message.role,
        })),
        runId,
        threadId: conversationId ?? COPILOT_STRATEGY_AGENT_ID,
      },
      method: "agent/run",
      params: { agentId: COPILOT_STRATEGY_AGENT_ID },
    }),
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    method: "POST",
    signal,
  });
  if (response.status === 204) {
    return;
  }
  if (!response.ok) {
    throw new Error(`CopilotKit stream failed: ${response.status}`);
  }
  if (!response.body) {
    throw new Error("CopilotKit stream did not return a response body.");
  }

  await readAgUiSseStream(response.body, onEvent);
}

async function readAgUiSseStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: Record<string, unknown>) => void
) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const split = splitCompleteSseFrames(buffer);
    buffer = split.remaining;
    for (const frame of split.frames) {
      const event = parseAgUiSseFrame(frame);
      if (event) {
        onEvent(event);
      }
    }
  }
  buffer += decoder.decode();
  for (const frame of splitCompleteSseFrames(`${buffer}\n\n`).frames) {
    const event = parseAgUiSseFrame(frame);
    if (event) {
      onEvent(event);
    }
  }
}

function parseAgUiSseFrame(frame: string): Record<string, unknown> | null {
  const data = frame
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n")
    .trim();
  if (!data || data === "[DONE]") {
    return null;
  }
  try {
    const parsed = JSON.parse(data);
    return parsed && typeof parsed === "object" ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

function agUiRunErrorMessage(event: Record<string, unknown>) {
  if (typeof event.message === "string" && event.message.trim()) {
    return event.message.trim();
  }
  if (typeof event.error === "string" && event.error.trim()) {
    return event.error.trim();
  }
  return "CopilotKit run failed.";
}

function strategyMessageToAgUiMessage(message: StrategyChatMessage): AgUiMessage[] {
  if (message.role !== "user" && message.role !== "assistant") {
    return [];
  }
  return [
    {
      content: message.text,
      id: message.id,
      role: message.role,
    } satisfies AgUiMessage,
  ];
}

function strategyChatMessageMetadataFromStrategyMessage(
  message: StrategyChatMessage
): StrategyChatMessageMetadata {
  return {
    backtestReport: message.backtestReport,
    inlineTables: message.inlineTables,
    marketSnapshot: message.marketSnapshot,
    reasoningSummaries: message.reasoningSummaries,
    responseIntent: message.responseIntent,
    sources: message.sources,
    suggestions: message.suggestions,
  };
}

function metadataByMessageIdWithTextFallback(
  messages: AgUiMessage[],
  metadataByMessageId: ReadonlyMap<string, StrategyChatMessageMetadata>,
  metadataByText: ReadonlyMap<string, StrategyChatMessageMetadata>
) {
  const merged = new Map(metadataByMessageId);
  for (const message of messages) {
    if (message.role !== "assistant" || merged.has(message.id)) {
      continue;
    }
    const textKey = agUiMessageTextKey(message);
    const metadata = textKey ? metadataByText.get(textKey) : undefined;
    if (metadata) {
      merged.set(message.id, metadata);
    }
  }
  return merged;
}

function hasStrategyMetadata(metadata: StrategyChatMessageMetadata) {
  return Boolean(
    metadata.backtestReport ||
      metadata.inlineTables.length > 0 ||
      metadata.marketSnapshot ||
      metadata.responseIntent ||
      metadata.sources.length > 0 ||
      metadata.suggestions
  );
}

function hasAnyStrategyMetadata(metadata: StrategyChatMessageMetadata) {
  return Boolean(
    metadata.backtestReport ||
      metadata.inlineTables.length > 0 ||
      metadata.marketSnapshot ||
      metadata.responseIntent ||
      metadata.sources.length > 0 ||
      metadata.reasoningSummaries.length > 0 ||
      metadata.suggestions
  );
}

function stripLiveOnlyMetadata(
  metadata: StrategyChatMessageMetadata
): StrategyChatMessageMetadata {
  return {
    ...metadata,
    reasoningSummaries: [],
  };
}

function assistantMessageIds(messages: AgUiMessage[]) {
  return new Set(
    messages
      .filter((message) => message.role === "assistant")
      .map((message) => message.id)
  );
}

function latestRunAssistantMessageId(
  messages: AgUiMessage[],
  assistantIdsAtRunStart: ReadonlySet<string>
): string | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (
      message.role === "assistant" &&
      !assistantIdsAtRunStart.has(message.id)
    ) {
      return message.id;
    }
  }
  return null;
}

function latestAssistantMessageId(messages: AgUiMessage[]): string | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === "assistant") {
      return message.id;
    }
  }
  return null;
}

function metadataPatchKeys(patch: Partial<StrategyChatMessageMetadata>) {
  return Object.entries(patch)
    .filter(([, value]) => {
      if (Array.isArray(value)) {
        return value.length > 0;
      }
      return Boolean(value);
    })
    .map(([key]) => key);
}

function metadataSummary(metadata: StrategyChatMessageMetadata) {
  return {
    hasBacktestReport: Boolean(metadata.backtestReport),
    inlineTableCount: metadata.inlineTables.length,
    hasMarketSnapshot: Boolean(metadata.marketSnapshot),
    marketSymbol: metadata.marketSnapshot?.symbol ?? null,
    reasoningCount: metadata.reasoningSummaries.length,
    responseIntent: metadata.responseIntent,
    sourceCount: metadata.sources.length,
    suggestionCount: metadata.suggestions?.actions.length ?? 0,
  };
}

function agUiMessagesSummary(messages: AgUiMessage[]) {
  const lastMessages = messages.slice(-4).map((message) => ({
    id: message.id,
    role: message.role,
    textLength: agUiMessagePlainText(message).length,
  }));
  return {
    assistantCount: messages.filter((message) => message.role === "assistant").length,
    count: messages.length,
    lastMessages,
    userCount: messages.filter((message) => message.role === "user").length,
  };
}

function agUiEventSummary(event: Record<string, unknown>) {
  const summary: Record<string, unknown> = {
    messageId: typeof event.messageId === "string" ? event.messageId : null,
    name: typeof event.name === "string" ? event.name : null,
    type: typeof event.type === "string" ? event.type : "unknown",
  };
  if (typeof event.delta === "string") {
    summary.deltaLength = event.delta.length;
  }
  if (typeof event.role === "string") {
    summary.role = event.role;
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

function sanitizeForwardedPropsForLog(forwardedProps: Record<string, unknown>) {
  return {
    conversationId:
      typeof forwardedProps.conversationId === "string" ? forwardedProps.conversationId : null,
    hasRegenerate: Boolean(forwardedProps.regenerate),
    hasMessageId: typeof forwardedProps.messageId === "string",
    language: typeof forwardedProps.language === "string" ? forwardedProps.language : null,
    mode: typeof forwardedProps.mode === "string" ? forwardedProps.mode : null,
    webSearch: forwardedProps.webSearch,
  };
}

function logCopilotRuntimeInsight(
  level: "info" | "warn",
  message: string,
  details: Record<string, unknown>
) {
  if (!isCopilotRuntimeDebugEnabled()) {
    return;
  }
  console[level](`[strategy-copilot-runtime] ${message}`, details);
}

function isCopilotRuntimeDebugEnabled() {
  if (process.env.NODE_ENV !== "production") {
    return true;
  }
  if (process.env.NEXT_PUBLIC_DEBUG_AG_UI === "true") {
    return true;
  }
  if (typeof window === "undefined") {
    return false;
  }
  return window.localStorage.getItem("strategy:debug-ag-ui") === "true";
}

function mergeAgUiMessageLists(
  currentMessages: AgUiMessage[],
  nextMessages: AgUiMessage[]
): AgUiMessage[] {
  const merged = [...currentMessages];
  for (const nextMessage of nextMessages) {
    const idIndex = merged.findIndex((message) => message.id === nextMessage.id);
    if (idIndex >= 0) {
      merged[idIndex] = mergeAgUiMessageById(merged[idIndex], nextMessage);
      continue;
    }
    if (nextMessage.role !== "assistant") {
      merged.push(nextMessage);
      continue;
    }
    const nextTextKey = agUiMessageTextKey(nextMessage);
    const textIndex = nextTextKey
      ? merged.findIndex(
          (message) =>
            message.role === nextMessage.role &&
            agUiMessageTextKey(message) === nextTextKey
        )
      : -1;
    if (textIndex >= 0) {
      merged[textIndex] = nextMessage;
      continue;
    }
    merged.push(nextMessage);
  }
  return merged;
}

function mergeAgUiMessageById(currentMessage: AgUiMessage, nextMessage: AgUiMessage): AgUiMessage {
  const currentText = agUiMessagePlainText(currentMessage);
  const nextText = agUiMessagePlainText(nextMessage);
  if (
    currentText &&
    (!nextText || (currentText.length > nextText.length && currentText.startsWith(nextText)))
  ) {
    return {
      ...nextMessage,
      content: currentMessage.content,
    } as AgUiMessage;
  }
  return nextMessage;
}

function strategyMessageTextKey(message: StrategyChatMessage) {
  return messageTextKey(message.text);
}

function agUiMessageTextKey(message: AgUiMessage) {
  return messageTextKey(agUiMessagePlainText(message));
}

function mergeAssistantTextDelta(existingText: string, delta: string) {
  if (!delta) {
    return existingText;
  }
  if (!existingText) {
    return delta;
  }
  if (delta.startsWith(existingText)) {
    return delta;
  }
  if (existingText.endsWith(delta)) {
    return existingText;
  }
  return `${existingText}${delta}`;
}

function agUiMessagePlainText(message: AgUiMessage | undefined) {
  if (!message || !("content" in message)) {
    return "";
  }
  const content = message.content;
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
      return record.type === "text" && typeof record.text === "string"
        ? record.text
        : "";
    })
    .join("");
}

function messageTextKey(text: string) {
  const normalized = text.replace(/\s+/g, " ").trim();
  return normalized ? `${normalized.length}:${normalized.slice(0, 160)}` : "";
}

function failureTextFromRunEventValue(
  value: unknown,
  language: LanguagePreference
): string | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const record = value as Record<string, unknown>;
  if (record.event !== "run.failed" && record.type !== "run.failed") {
    return null;
  }
  const payload =
    record.payload && typeof record.payload === "object"
      ? (record.payload as Record<string, unknown>)
      : {};
  return runFailureMessage(
    {
      data: { payload },
      event: "run.failed",
    },
    language
  );
}
