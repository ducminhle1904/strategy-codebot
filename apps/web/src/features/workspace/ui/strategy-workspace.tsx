"use client";

import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtHeader,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";
import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import {
  Message,
  MessageAction,
  MessageActions,
  MessageContent,
  MessageMarkdown,
} from "@/components/ai-elements/message";
import { Shimmer } from "@/components/ai-elements/shimmer";
import {
  Sources,
  SourcesContent,
  SourcesTrigger,
} from "@/components/ai-elements/sources";
import {
  Tool,
  ToolContent,
  ToolHeader,
  ToolOutput,
} from "@/components/ai-elements/tool";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Textarea } from "@/components/ui/textarea";
import {
  StrategyComposer,
  StrategyStartPrompt,
} from "@/components/strategy/start-prompt";
import {
  ArtifactPreviewContent,
  artifactPreviewContent,
} from "@/features/artifacts/ui/artifact-preview-content";
import { WorkflowPanel, WorkflowTaskPrompt } from "@/features/workflow";
import {
  AccountDialogs,
  ConversationSidebar,
  DeleteConversationDialog,
  ReadinessStrip,
  RenameConversationDialog,
  conversationHydrationKey,
  type AccountDialog,
  type SettingsTab,
} from "./conversation-sidebar";
import { StatusPill } from "@/components/strategy/status-pill";
import { BacktestReportCard } from "@/components/strategy/backtest-report-card";
import { BacktestResultInlineCard } from "@/components/strategy/backtest-result-inline-card";
import {
  AUTO_CHAIN_SUMMARY_PENDING_EVENT,
  AUTO_CHAIN_SUMMARY_TIMEOUT_EVENT,
  AUTO_CHAIN_TERMINAL_STATUSES,
  autoChainContinuationFromRunEvents,
  createAutoChainLocalEvent,
  hasAutoChainSummaryCompletedEvent,
  hasAutoChainSummaryMessage,
  mergeRunEvents,
  updateAutoChainContinuationFromRunEvent,
  type AutoChainContinuation,
} from "@/lib/auto-chain-continuation";
import {
  BackendClient,
  BackendClientError,
  parseBackendSseEvents,
} from "@/lib/backend-client";
import {
  actionRegistryLookup,
  actionToolPrompt,
  type ActionRegistryLookup,
} from "@/lib/action-tool-metadata";
import { splitCompleteSseFrames } from "@/lib/sse";
import {
  backtestLiveStageLabel,
  backtestLiveStatusFromRunEvents,
  getArtifactUserSummary,
  getBestArtifactForDrawer,
  getUserFacingArtifacts,
  type BacktestLiveStatus,
  type ArtifactUserKind,
  type ArtifactWorkspaceTab,
} from "@/features/artifacts/model/artifact-workspace";
import {
  backendMessagesToStrategyMessages,
  compactActivityTitle,
  getChatSuggestions,
  hasAssistantText,
  isRenderableMessage,
  mergeStrategyChatMessageMetadata,
  shouldShowStrategyProfile,
  type ChatInlineTable,
  type ChatSuggestionItem,
  type ChatMessageSource,
  type MarketSnapshot,
  type ResponseIntent,
  type StrategyChatMessage,
  groupArtifactsByAnchorMessage,
  latestAssistantAfterLastUser,
  runEventAssistantAnchorMessageId,
  runEventMetadataByAnchorMessage,
} from "@/features/chat/ui/chat-ui";
import {
  isChatResponseIntent,
} from "@/lib/chat-intent-registry-contract";
import {
  providerRouteReady,
} from "@/features/account";
import {
  mapRunEventsToChatActivities,
  mapRunEventsToChatActivitiesByAssistantMessage,
  type ChatActivity,
} from "@/features/chat/model/chat-activity";
import {
  StrategySpecSchema,
  WebSearchModeSchema,
} from "@/lib/backend-schemas";
import {
  getUiCopy,
  type LanguagePreference as UiLanguagePreference,
} from "@/lib/i18n";
import { useI18n } from "@/lib/language";
import { useTheme } from "@/lib/theme";
import { useStrategyChatRuntime } from "@/features/chat";
import {
  StrategyAgentContextProvider,
  useStrategyCopilotCapabilities,
  type StrategyAgentWorkflow,
} from "@/lib/copilot-agent-context";
import { StrategyCopilotTools } from "@/lib/copilot-tools";
import type {
  Artifact,
  ArtifactListResponse,
  Conversation as ChatConversation,
  ConversationSidebarItem,
  Message as BackendMessage,
  ProviderStatusResponse,
  RunEvent,
  RunMode,
  SelectedActionMetadata,
  StrategyProfile,
  StrategySpec,
  WebSearchMode,
  BotProposalConfirmStartRequest,
} from "@/lib/backend-schemas";
import { useStrategyUiStore } from "@/lib/ui-store";
import {
  getArtifactPreviewForViewer,
  useBrowserBackendClient,
} from "@/lib/use-browser-backend-client";
import { cn } from "@/lib/utils";
import {
  errorMessageFromUnknown,
  marketSnapshotFromPayload,
  normalizeWorkflowState,
  runFailureMessage,
  type WorkflowState,
} from "@/features/chat/api/chat-stream";
import { getWorkflowDefinition, type WorkflowAction } from "@/features/workflow";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowUp,
  BookOpen,
  Bot,
  Braces,
  Check,
  ChevronDown,
  CircleHelp,
  Clipboard,
  CornerDownLeft,
  ExternalLink,
  FileCode2,
  FileText,
  Gauge,
  Globe2,
  ListChecks,
  Loader2,
  MessageSquarePlus,
  PanelRight,
  Play,
  RefreshCcw,
  Square,
  ThumbsDown,
  ThumbsUp,
  TrendingUp,
  X,
} from "lucide-react";
import { useRouter } from "next/navigation";
import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

type ChatLoadingState = {
  description: string;
  title: string;
};

const starterSpec = `{
  "target_platform": "pine_v6",
  "script_type": "strategy",
  "market": "crypto",
  "symbol": "BTCUSDT",
  "timeframe": "1h",
  "entry_rules": [
    "Enter long when the 9-period SMA crosses above the 21-period SMA after bar close"
  ],
  "exit_rules": [
    "Exit when the 9-period SMA crosses below the 21-period SMA after bar close"
  ],
  "risk_rules": [
    "Use fixed fractional risk and always attach a stop-loss and take-profit"
  ],
  "position_sizing": "Risk 1% of equity per trade",
  "stop_loss": "2% below average entry price",
  "take_profit": "4% above average entry price",
  "assumptions": [
    "Commission and slippage are modeled in the strategy declaration",
    "Signals are confirmed only after bar close"
  ],
  "constraints": [
    "No live trading automation",
    "No profitability claims"
  ],
  "user_notes": "Dry-run review artifact fixture"
}`;

const MAX_PROGRESS_BUFFER_BYTES = 512 * 1024;
const WEB_SEARCH_STORAGE_KEY = "strategy-codebot-web-search";
const NEW_CHAT_PENDING_PROMPT_KEY_PREFIX = "strategy-codebot-new-chat-prompt:";
const NEW_CHAT_PENDING_PROMPT_TTL_MS = 5 * 60 * 1000;
const WEB_SEARCH_MODE_NEXT: Record<WebSearchMode, WebSearchMode> = {
  auto: "on",
  off: "auto",
  on: "off",
};

type PromptSubmitPayload = {
  text: string;
  selectedAction?: SelectedActionMetadata;
};
type PendingInitialPrompt = {
  clientRequestId: string;
  createdAt: number;
  text: string;
  webSearch: WebSearchMode;
};

function readStoredWebSearchMode(): WebSearchMode {
  if (typeof window === "undefined") {
    return "auto";
  }
  try {
    return WebSearchModeSchema.safeParse(
      window.localStorage.getItem(WEB_SEARCH_STORAGE_KEY)
    ).data ?? "auto";
  } catch {
    return "auto";
  }
}

function pendingInitialPromptStorageKey(conversationId: string) {
  return `${NEW_CHAT_PENDING_PROMPT_KEY_PREFIX}${conversationId}`;
}

function writePendingInitialPrompt(conversationId: string, prompt: PendingInitialPrompt) {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.sessionStorage.setItem(
      pendingInitialPromptStorageKey(conversationId),
      JSON.stringify(prompt)
    );
  } catch {
    // Best-effort handoff only. The caller still routes to the conversation.
  }
}

function takePendingInitialPrompt(conversationId: string): PendingInitialPrompt | null {
  if (typeof window === "undefined") {
    return null;
  }
  const key = pendingInitialPromptStorageKey(conversationId);
  try {
    const raw = window.sessionStorage.getItem(key);
    window.sessionStorage.removeItem(key);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<PendingInitialPrompt>;
    if (
      typeof parsed.text !== "string" ||
      typeof parsed.clientRequestId !== "string" ||
      typeof parsed.createdAt !== "number" ||
      Date.now() - parsed.createdAt > NEW_CHAT_PENDING_PROMPT_TTL_MS
    ) {
      return null;
    }
    return {
      clientRequestId: parsed.clientRequestId,
      createdAt: parsed.createdAt,
      text: parsed.text,
      webSearch: WebSearchModeSchema.safeParse(parsed.webSearch).data ?? "auto",
    };
  } catch {
    return null;
  }
}

export function StrategyWorkspace({
  initialConversationId = null,
}: {
  initialConversationId?: string | null;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { language, setLanguage } = useI18n();
  const { resolvedTheme, setTheme, theme } = useTheme();
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(
    initialConversationId
  );
  const [runMode, setRunMode] = useState<RunMode>("dry-run");
  const [specDraft, setSpecDraft] = useState(starterSpec);
  const [specDialogOpen, setSpecDialogOpen] = useState(false);
  const [renameTarget, setRenameTarget] = useState<ChatConversation | null>(null);
  const [renameTitle, setRenameTitle] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<ChatConversation | null>(null);
  const [runEvents, setRunEvents] = useState<RunEvent[]>([]);
  const [autoChainContinuation, setAutoChainContinuation] =
    useState<AutoChainContinuation | null>(null);
  const [inlineError, setInlineError] = useState<string | null>(null);
  const [accountDialog, setAccountDialog] = useState<AccountDialog | null>(null);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("general");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [webSearchMode, setWebSearchMode] = useState<WebSearchMode>(() =>
    readStoredWebSearchMode()
  );
  const progressAbortRef = useRef<AbortController | null>(null);
  const autoChainAbortRef = useRef<AbortController | null>(null);
  const stopChatRef = useRef<(() => void) | null>(null);
  const lastHydratedMessagesKeyRef = useRef<string | null>(null);
  const sendingConversationIdRef = useRef<string | null>(null);
  const promptSubmitPendingRef = useRef(false);
  const workflowContinuationTaskIdsRef = useRef(new Set<string>());
  const consumedPendingInitialPromptRef = useRef<string | null>(null);
  const setMessagesFromConversationStateRef = useRef<
    ((messages: StrategyChatMessage[]) => void) | null
  >(null);
  const [promptSubmitPending, setPromptSubmitPending] = useState(false);
  const [pendingPromptText, setPendingPromptText] = useState<string | null>(null);
  const {
    artifactPanelOpen,
    setArtifactPanelOpen,
    selectedArtifactId,
    setSelectedArtifactId,
  } = useStrategyUiStore();
  const copilotCapabilities = useStrategyCopilotCapabilities();

  const client = useBrowserBackendClient();

  const stopRunProgress = useCallback(() => {
    progressAbortRef.current?.abort();
    progressAbortRef.current = null;
  }, []);

  const stopAutoChainContinuation = useCallback(() => {
    autoChainAbortRef.current?.abort();
    autoChainAbortRef.current = null;
    setAutoChainContinuation(null);
  }, []);

  const handleSelectConversation = useCallback(
    (conversationId: string) => {
      stopRunProgress();
      stopAutoChainContinuation();
      setRunEvents([]);
      setSelectedConversationId(conversationId);
      router.push(`/c/${encodeURIComponent(conversationId)}`);
    },
    [router, stopAutoChainContinuation, stopRunProgress]
  );

  const readiness = useQuery({
    queryFn: () => client.ready(),
    queryKey: ["ready"],
    refetchInterval: 15000,
  });

  const me = useQuery({
    queryFn: () => client.me(),
    queryKey: ["me"],
    retry: false,
  });

  const providerStatus = useQuery({
    queryFn: () => client.getProviderStatus(),
    queryKey: ["provider-status"],
    refetchInterval: 30000,
  });
  const actionRegistry = useQuery({
    queryFn: () => client.getActionRegistry(),
    queryKey: ["action-registry"],
    staleTime: 300000,
  });
  const actionRegistryByToolId = useMemo(
    () => actionRegistryLookup(actionRegistry.data?.actions),
    [actionRegistry.data?.actions]
  );
  const accountUsage = useQuery({
    enabled: accountDialog === "settings",
    queryFn: () => client.getAccountUsage(),
    queryKey: ["account-usage"],
  });
  const allowedRunModes = allowedRunModesFromCapability(providerStatus.data);
  const activeRunMode = allowedRunModes.includes(runMode) ? runMode : "dry-run";

  const openAccountDialog = useCallback((dialog: AccountDialog) => {
    if (dialog === "settings") {
      setSettingsTab("general");
    }
    setAccountDialog(dialog);
  }, []);

  const openSettingsTab = useCallback((tab: SettingsTab) => {
    setSettingsTab(tab);
    setAccountDialog("settings");
  }, []);

  const copySettingsValue = useCallback(
    async (value: string, successTitle: string) => {
      const t = getUiCopy(language);
      try {
        await navigator.clipboard.writeText(value);
        showToast({ title: successTitle });
      } catch (error) {
        showToast({
          description: errorMessageFromUnknown(error),
          title: t.copyFailed,
          variant: "error",
        });
      }
    },
    [language, showToast]
  );

  const updateWebSearchMode = useCallback((mode: WebSearchMode) => {
    setWebSearchMode(mode);
    try {
      window.localStorage.setItem(WEB_SEARCH_STORAGE_KEY, mode);
    } catch {
      // Preference persistence is best-effort; the in-memory state still updates.
    }
  }, []);

  const sidebar = useQuery({
    queryFn: () => client.listConversationSidebar(),
    queryKey: ["conversation-sidebar"],
  });
  const sidebarItems = useMemo(() => sidebar.data?.items ?? [], [sidebar.data?.items]);
  const activeConversationId = selectedConversationId;

  const state = useQuery({
    enabled: Boolean(activeConversationId),
    queryFn: async () => {
      try {
        return await client.getConversationState(activeConversationId ?? "");
      } catch (error) {
        if (isConversationNotFoundError(error)) {
          return null;
        }
        throw error;
      }
    },
    queryKey: ["conversation-state", activeConversationId],
  });

  const renameConversation = useMutation({
    mutationFn: ({ conversationId, title }: { conversationId: string; title: string }) =>
      client.updateConversationTitle(conversationId, { title }),
    onError: (error, variables) => {
      if (variables.conversationId === activeConversationId) {
        setInlineError(errorMessage(error));
        return;
      }
      showToast({
        description: errorMessage(error),
        title: "Could not rename chat",
        variant: "error",
      });
    },
    onSuccess: async () => {
      setInlineError(null);
      setRenameTarget(null);
      setRenameTitle("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversation-state"] }),
        queryClient.invalidateQueries({ queryKey: ["conversation-sidebar"] }),
      ]);
    },
  });

  const deleteConversation = useMutation({
    mutationFn: (conversationId: string) => client.deleteConversation(conversationId),
    onError: (error, conversationId) => {
      if (conversationId === activeConversationId) {
        setInlineError(errorMessage(error));
        return;
      }
      showToast({
        description: errorMessage(error),
        title: "Could not delete chat",
        variant: "error",
      });
    },
    onSuccess: async (deletedConversation) => {
      setInlineError(null);
      setDeleteTarget(null);
      if (activeConversationId === deletedConversation.id) {
        const nextConversation = sidebarItems.find(
          (item) => item.conversation.id !== deletedConversation.id
        )?.conversation;
        stopChatRef.current?.();
        stopRunProgress();
        stopAutoChainContinuation();
        setRunEvents([]);
        setSelectedConversationId(nextConversation?.id ?? null);
        if (nextConversation?.id) {
          router.replace(`/c/${encodeURIComponent(nextConversation.id)}`);
        } else {
          router.replace("/");
        }
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversation-state"] }),
        queryClient.invalidateQueries({ queryKey: ["conversation-sidebar"] }),
      ]);
    },
  });

  const createRun = useMutation({
    mutationFn: async () => {
      const parsed = parseStrategySpecDraft(specDraft);
      const conversationId =
        activeConversationId ?? (await client.createConversation({ title: null })).id;
      if (!activeConversationId) {
        handleSelectConversation(conversationId);
      }
      return client.createRun({
        conversation_id: conversationId,
        mode: activeRunMode,
        strategy_spec: parsed,
      });
    },
    onError: (error) => setInlineError(errorMessage(error)),
    onSuccess: async (run) => {
      setInlineError(null);
      setSpecDialogOpen(false);
      setArtifactPanelOpen(false);
      stopAutoChainContinuation();
      setRunEvents([]);
      progressAbortRef.current?.abort();
      const progressAbort = new AbortController();
      progressAbortRef.current = progressAbort;
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversation-state"] }),
        queryClient.invalidateQueries({ queryKey: ["conversation-sidebar"] }),
      ]);
      void consumeRunProgress(client, run.id, setRunEvents, progressAbort.signal);
    },
  });

  const cancelRun = useMutation({
    mutationFn: (runId: string) => client.cancelRun(runId),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversation-state"] }),
        queryClient.invalidateQueries({ queryKey: ["conversation-sidebar"] }),
      ]),
  });

  const retryRun = useMutation({
    mutationFn: (runId: string) => client.retryRun(runId),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversation-state"] }),
        queryClient.invalidateQueries({ queryKey: ["conversation-sidebar"] }),
      ]),
  });

  const stateBelongsToActiveConversation =
    Boolean(activeConversationId) &&
    state.data?.conversation.id === activeConversationId;
  const chatMessages = useMemo(
    () =>
      stateBelongsToActiveConversation
        ? backendMessagesToStrategyMessages(state.data?.messages ?? [])
        : [],
    [state.data?.messages, stateBelongsToActiveConversation]
  );

  const chat = useStrategyChatRuntime({
    activeConversationId,
    initialMessages: chatMessages,
    language,
    onData: (part) => {
      if (part.type === "data-runEvent") {
        const parsed = part.data as RunEvent;
        const expectedConversationId = sendingConversationIdRef.current ?? activeConversationId;
        if (parsed.conversation_id !== expectedConversationId) {
          return;
        }
        if (parsed.type === "message.delta") {
          return;
        }
        setAutoChainContinuation((current) =>
          updateAutoChainContinuationFromRunEvent(current, parsed)
        );
        if (parsed.type === "chat.auto_chain.summary.completed") {
          void queryClient.invalidateQueries({ queryKey: ["conversation-state", parsed.conversation_id] });
          void queryClient.invalidateQueries({ queryKey: ["conversation-sidebar"] });
        }
        setRunEvents((events) => mergeRunEvents(events, [parsed], 60));
        if (parsed.type === "run.failed") {
          const event = { data: parsed as unknown as Record<string, unknown>, event: parsed.type };
          setInlineError(runFailureMessage(event, language));
        }
      }
    },
    onError: (error) => {
      promptSubmitPendingRef.current = false;
      setPromptSubmitPending(false);
      setPendingPromptText(null);
      sendingConversationIdRef.current = null;
      setInlineError(error.message);
      void queryClient.invalidateQueries({ queryKey: ["conversation-state"] });
    },
    onFinish: async () => {
      promptSubmitPendingRef.current = false;
      setPromptSubmitPending(false);
      setPendingPromptText(null);
      sendingConversationIdRef.current = null;
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversation-state"] }),
        queryClient.invalidateQueries({ queryKey: ["conversation-sidebar"] }),
      ]);
    },
    webSearchMode,
  });

  useEffect(() => {
    setMessagesFromConversationStateRef.current = chat.setMessagesFromConversationState;
  }, [chat.setMessagesFromConversationState]);

  useEffect(() => {
    stopChatRef.current = () => {
      void chat.stop();
    };
    return () => {
      stopChatRef.current = null;
    };
  }, [chat]);

  const continueWorkflowTask = useCallback(
    async (taskId: string) => {
      if (!activeConversationId || workflowContinuationTaskIdsRef.current.has(taskId)) {
        return;
      }
      workflowContinuationTaskIdsRef.current.add(taskId);
      sendingConversationIdRef.current = activeConversationId;
      try {
        await chat.continueWorkflowTask(taskId, {
          body: { conversationId: activeConversationId },
        });
      } finally {
        workflowContinuationTaskIdsRef.current.delete(taskId);
      }
    },
    [activeConversationId, chat]
  );

  useEffect(() => {
    const pending = stateBelongsToActiveConversation
      ? state.data?.pending_workflow_continuation
      : null;
    if (!pending?.required || !pending.task_id || chat.status !== "ready" || promptSubmitPendingRef.current) {
      return;
    }
    void continueWorkflowTask(pending.task_id);
  }, [
    chat.status,
    continueWorkflowTask,
    state.data?.pending_workflow_continuation,
    stateBelongsToActiveConversation,
  ]);

  useEffect(() => {
    if (!autoChainContinuation) {
      return;
    }
    if (autoChainContinuation.conversationId === activeConversationId) {
      return;
    }
    autoChainAbortRef.current?.abort();
    autoChainAbortRef.current = null;
    queueMicrotask(() => {
      setAutoChainContinuation(null);
    });
  }, [activeConversationId, autoChainContinuation]);

  useEffect(() => {
    if (!autoChainContinuation || autoChainContinuation.conversationId !== activeConversationId) {
      return;
    }
    if (autoChainContinuation.status !== "queued" && autoChainContinuation.status !== "running") {
      return;
    }
    const abort = new AbortController();
    autoChainAbortRef.current?.abort();
    autoChainAbortRef.current = abort;
    void consumeAutoChainRunEvents(
      client,
      autoChainContinuation.childRunId,
      (events) => {
        setRunEvents((current) => mergeRunEvents(current, events, 60));
        setAutoChainContinuation((current) => {
          let next = current;
          for (const event of events) {
            next = updateAutoChainContinuationFromRunEvent(next, event);
          }
          return next;
        });
        for (const event of events) {
          if (event.type === "run.completed") {
            const pending = createAutoChainLocalEvent(
              autoChainContinuation,
              AUTO_CHAIN_SUMMARY_PENDING_EVENT,
              "The report is ready. Waiting for the summary message to appear."
            );
            setRunEvents((current) => mergeRunEvents(current, [pending], 60));
            void Promise.all([
              queryClient.invalidateQueries({
                queryKey: ["conversation-state", autoChainContinuation.conversationId],
              }),
              queryClient.invalidateQueries({ queryKey: ["conversation-sidebar"] }),
            ]);
          }
          if (event.type === "run.failed") {
            const failureEvent = { data: event as unknown as Record<string, unknown>, event: event.type };
            setInlineError(runFailureMessage(failureEvent, language));
          }
        }
      },
      abort.signal
    );
    return () => {
      abort.abort();
      if (autoChainAbortRef.current === abort) {
        autoChainAbortRef.current = null;
      }
    };
  }, [
    activeConversationId,
    autoChainContinuation?.childRunId,
    autoChainContinuation?.status,
    client,
    language,
    queryClient,
  ]);

  useEffect(() => {
    if (!autoChainContinuation || autoChainContinuation.conversationId !== activeConversationId) {
      return;
    }
    if (autoChainContinuation.status !== "summary_pending") {
      return;
    }
    let cancelled = false;
    const continuation = autoChainContinuation;
    async function pollSummary() {
      const deadline = Date.now() + 120_000;
      while (!cancelled && Date.now() < deadline) {
        try {
          const nextState = await client.getConversationState(continuation.conversationId);
          if (cancelled) {
            return;
          }
          queryClient.setQueryData(["conversation-state", continuation.conversationId], nextState);
          const stateRunEvents = nextState.conversation_run_events.length
            ? nextState.conversation_run_events
            : nextState.latest_run_events;
          if (
            hasAutoChainSummaryCompletedEvent(stateRunEvents, continuation.childRunId) ||
            hasAutoChainSummaryMessage(nextState.messages, continuation.childRunId)
          ) {
            if (cancelled) {
              return;
            }
            setMessagesFromConversationStateRef.current?.(
              backendMessagesToStrategyMessages(nextState.messages)
            );
            setAutoChainContinuation((current) =>
              current?.childRunId === continuation.childRunId
                ? { ...current, status: "summary_ready" }
                : current
            );
            await queryClient.invalidateQueries({ queryKey: ["conversation-sidebar"] });
            return;
          }
        } catch {
          // Best-effort refresh; retry until the bounded deadline.
        }
        await delay(2000);
      }
      if (cancelled) {
        return;
      }
      const timeoutEvent = createAutoChainLocalEvent(
        continuation,
        AUTO_CHAIN_SUMMARY_TIMEOUT_EVENT,
        "The report is available, but the summary message is still being prepared."
      );
      setRunEvents((current) => mergeRunEvents(current, [timeoutEvent], 60));
      setAutoChainContinuation((current) =>
        current?.childRunId === continuation.childRunId
          ? { ...current, status: "summary_timeout" }
          : current
      );
    }
    void pollSummary();
    return () => {
      cancelled = true;
    };
  }, [
    activeConversationId,
    autoChainContinuation?.status,
    autoChainContinuation?.childRunId,
    client,
    queryClient,
  ]);

  useEffect(() => {
    if (!activeConversationId || !state.isSuccess || state.data !== null) {
      return;
    }
    stopChatRef.current?.();
    stopRunProgress();
    queueMicrotask(() => {
      stopAutoChainContinuation();
      setRunEvents([]);
      setInlineError(null);
      setSelectedConversationId(null);
      lastHydratedMessagesKeyRef.current = "empty";
      chat.setMessagesFromConversationState([]);
      queryClient.removeQueries({ queryKey: ["conversation-state", activeConversationId] });
      router.replace("/");
    });
  }, [
    activeConversationId,
    chat,
    queryClient,
    router,
    state.data,
    state.isSuccess,
    stopAutoChainContinuation,
    stopRunProgress,
  ]);

  useEffect(() => {
    if (!activeConversationId) {
      if (lastHydratedMessagesKeyRef.current === "empty") {
        return;
      }
      lastHydratedMessagesKeyRef.current = "empty";
      chat.setMessagesFromConversationState([]);
      return;
    }
    if (!state.isSuccess || !stateBelongsToActiveConversation) {
      return;
    }
    if (
      chat.status === "submitted" ||
      chat.status === "streaming" ||
      sendingConversationIdRef.current === activeConversationId
    ) {
      return;
    }
    const nextHydrationKey = conversationHydrationKey(activeConversationId, chatMessages);
    if (lastHydratedMessagesKeyRef.current === nextHydrationKey) {
      return;
    }
    lastHydratedMessagesKeyRef.current = nextHydrationKey;
    chat.setMessagesFromConversationState(chatMessages);
  }, [
    activeConversationId,
    chat,
    chatMessages,
    chat.status,
    state.isSuccess,
    stateBelongsToActiveConversation,
  ]);

  useEffect(() => {
    if (chat.status === "submitted" || chat.status === "streaming") {
      return;
    }
    if (activeConversationId && !stateBelongsToActiveConversation) {
      return;
    }
    const timeout = window.setTimeout(() => {
      const hydratedRunEvents = mergeRunEvents(
        state.data?.conversation_run_events ?? [],
        state.data?.latest_run_events ?? [],
        120
      );
      setRunEvents(hydratedRunEvents);
      const hydratedContinuation = autoChainContinuationFromRunEvents(hydratedRunEvents);
      setAutoChainContinuation((current) => {
        if (!hydratedContinuation) {
          return current?.conversationId === activeConversationId ? null : current;
        }
        if (
          current?.childRunId === hydratedContinuation.childRunId &&
          current.conversationId === hydratedContinuation.conversationId &&
          current.sourceRunId === hydratedContinuation.sourceRunId &&
          current.status === hydratedContinuation.status
        ) {
          return current;
        }
        return hydratedContinuation;
      });
    }, 0);
    return () => window.clearTimeout(timeout);
  }, [
    activeConversationId,
    chat.status,
    stateBelongsToActiveConversation,
    state.data?.latest_run?.id,
    state.data?.latest_run_events,
    state.data?.conversation_run_events,
  ]);

  useEffect(() => {
    if (readiness.error) {
      console.error("[strategy-web] readiness failed", readiness.error);
    }
    if (me.error) {
      console.error("[strategy-web] identity failed", me.error);
    }
    if (providerStatus.error) {
      console.error("[strategy-web] provider status failed", providerStatus.error);
    }
    if (sidebar.error) {
      console.error("[strategy-web] sidebar failed", sidebar.error);
    }
    if (state.error) {
      console.error("[strategy-web] conversation state failed", state.error);
    }
  }, [me.error, providerStatus.error, readiness.error, sidebar.error, state.error]);

  useEffect(
    () => () => {
      progressAbortRef.current?.abort();
      autoChainAbortRef.current?.abort();
    },
    []
  );

  const handleLanguageChange = useCallback((value: UiLanguagePreference) => {
    setLanguage(value);
  }, [setLanguage]);

  const submitPromptToConversation = useCallback(
    async ({
      clientRequestId,
      conversationId,
      selectedAction,
      text,
      webSearch,
    }: {
      clientRequestId: string;
      conversationId: string;
      selectedAction?: SelectedActionMetadata;
      text: string;
      webSearch: WebSearchMode;
    }) => {
      promptSubmitPendingRef.current = true;
      setPromptSubmitPending(true);
      setPendingPromptText(text);
      setInlineError(null);
      chat.clearError();
      const preserveAutoChainProgress =
        autoChainContinuation?.conversationId === conversationId &&
        !AUTO_CHAIN_TERMINAL_STATUSES.has(autoChainContinuation.status);
      if (!preserveAutoChainProgress) {
        setRunEvents([]);
      }
      console.info("[strategy-web-chat] submit", {
        clientRequestId,
        conversationId,
        mode: "agent",
        webSearch,
      });
      sendingConversationIdRef.current = conversationId;
      try {
        await chat.sendMessage(
          { text },
          {
            body: { clientRequestId, conversationId, selectedAction, webSearch },
          }
        );
      } catch (error) {
        promptSubmitPendingRef.current = false;
        setPromptSubmitPending(false);
        setPendingPromptText(null);
        sendingConversationIdRef.current = null;
        setInlineError(errorMessage(error));
        await queryClient.invalidateQueries({ queryKey: ["conversation-state"] });
        chat.setMessagesFromConversationState(chatMessages);
      }
    },
    [autoChainContinuation, chat, chatMessages, queryClient]
  );

  const handlePromptSubmit = useCallback(
    async ({ selectedAction, text }: PromptSubmitPayload) => {
      if (promptSubmitPendingRef.current || chat.status !== "ready") {
        return;
      }
      const submittedText = text.trim();
      if (!submittedText) {
        return;
      }
      promptSubmitPendingRef.current = true;
      setPromptSubmitPending(true);
      setPendingPromptText(submittedText);
      setInlineError(null);
      chat.clearError();
      const clientRequestId = crypto.randomUUID();
      let conversationId = activeConversationId;
      if (!conversationId) {
        try {
          const conversation = await client.createConversation({ title: null });
          conversationId = conversation.id;
          writePendingInitialPrompt(conversationId, {
            clientRequestId,
            createdAt: Date.now(),
            text: submittedText,
            webSearch: webSearchMode,
          });
          setInlineError(null);
          setSelectedConversationId(conversationId);
          router.push(`/c/${encodeURIComponent(conversationId)}`);
          await queryClient.invalidateQueries({ queryKey: ["conversation-sidebar"] });
          return;
        } catch (error) {
          promptSubmitPendingRef.current = false;
          setPromptSubmitPending(false);
          setPendingPromptText(null);
          setInlineError(errorMessage(error));
          return;
        }
      }
      await submitPromptToConversation({
        clientRequestId,
        conversationId,
        selectedAction,
        text: submittedText,
        webSearch: webSearchMode,
      });
    },
    [
      activeConversationId,
      chat,
      client,
      queryClient,
      router,
      submitPromptToConversation,
      webSearchMode,
    ]
  );

  useEffect(() => {
    if (
      !activeConversationId ||
      initialConversationId !== activeConversationId ||
      chat.status !== "ready" ||
      promptSubmitPendingRef.current ||
      consumedPendingInitialPromptRef.current === activeConversationId
    ) {
      return;
    }
    const pendingPrompt = takePendingInitialPrompt(activeConversationId);
    if (!pendingPrompt) {
      return;
    }
    consumedPendingInitialPromptRef.current = activeConversationId;
    queueMicrotask(() => {
      void submitPromptToConversation({
        clientRequestId: pendingPrompt.clientRequestId,
        conversationId: activeConversationId,
        text: pendingPrompt.text,
        webSearch: pendingPrompt.webSearch,
      });
    });
  }, [activeConversationId, chat.status, initialConversationId, submitPromptToConversation]);

  const latestRun = state.data?.latest_run ?? null;
  const decideBacktestApproval = useMutation({
    mutationFn: async ({
      approvalId,
      conversationId,
      decision,
    }: {
      approvalId: string;
      conversationId: string;
      decision: "approved" | "rejected";
    }) => {
      const response = await client.decideBacktestApproval(conversationId, approvalId, {
        decision,
      });
      const sourceRunId = latestRun?.id ?? `local-${approvalId}`;
      const localEvents = backtestApprovalDecisionLocalEvents({
        approvalId,
        childRunId: response.run_id ?? null,
        conversationId,
        decision,
        sourceRunId,
      });
      setRunEvents((current) => mergeRunEvents(current, localEvents, 60));
      if (decision === "approved" && response.run_id) {
        setAutoChainContinuation({
          childRunId: response.run_id,
          conversationId,
          sourceRunId,
          status: "queued",
        });
      }
      if (decision === "rejected") {
        setAutoChainContinuation(null);
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversation-state", conversationId] }),
        queryClient.invalidateQueries({ queryKey: ["conversation-sidebar"] }),
      ]);
      return response;
    },
    onError: (error) => {
      showToast({
        description: errorMessage(error),
        title: "Backtest approval failed",
        variant: "error",
      });
    },
  });
  const submitFeedback = useMutation({
    mutationFn: ({
      messageId,
      rating,
    }: {
      messageId: string;
      rating: "up" | "down";
    }) => {
      if (!activeConversationId) {
        throw new Error("Create or select a conversation first.");
      }
      return client.createFeedback({
        category: rating === "up" ? "helpful" : "needs_correction",
        conversation_id: activeConversationId,
        correction:
          rating === "up"
            ? "Marked as helpful from the chat response action."
            : "Marked as needing correction from the chat response action.",
        message_id: messageId,
        rating,
        run_id: latestRun?.id ?? null,
      });
    },
  });
  const handleAssistantFeedback = useCallback(
    async (messageId: string, rating: "up" | "down") => {
      await submitFeedback.mutateAsync({ messageId, rating });
    },
    [submitFeedback]
  );
  const handleRegenerateMessage = useCallback(
    async (messageId: string) => {
      const clientRequestId = crypto.randomUUID();
      await chat.regenerate({
        body: { clientRequestId },
        messageId,
      });
    },
    [chat]
  );
  const initialArtifactCursor = state.data?.conversation_artifacts_next_cursor ?? null;
  const olderConversationArtifacts = useInfiniteQuery({
    enabled: false,
    getNextPageParam: (lastPage: ArtifactListResponse) => lastPage.next_cursor,
    initialPageParam: initialArtifactCursor,
    queryFn: ({ pageParam }: { pageParam: string | null }) => {
      if (!activeConversationId || !pageParam) {
        return Promise.resolve({ items: [], next_cursor: null });
      }
      return client.listConversationArtifacts(activeConversationId, { cursor: pageParam });
    },
    queryKey: ["conversation-artifacts", activeConversationId, initialArtifactCursor],
  });
  const conversationArtifacts = useMemo(
    () =>
      dedupeArtifactsById([
        ...(state.data?.conversation_artifacts ?? []),
        ...((olderConversationArtifacts.data?.pages ?? []).flatMap((page) => page.items)),
      ]),
    [olderConversationArtifacts.data?.pages, state.data?.conversation_artifacts]
  );
  const hasOlderConversationArtifacts =
    (olderConversationArtifacts.data?.pages.length ?? 0) === 0
      ? Boolean(initialArtifactCursor)
      : olderConversationArtifacts.hasNextPage;
  const visibleArtifacts = useMemo(
    () =>
      getUserFacingArtifacts(
        dedupeArtifactsById([
          ...(state.data?.latest_run_artifacts ?? []),
          ...conversationArtifacts,
        ])
      ),
    [conversationArtifacts, state.data?.latest_run_artifacts]
  );
  const strategyProfile = state.data?.strategy_profile ?? null;
  const workspaceRunIntent = useMemo(() => responseIntentFromRunEvents(runEvents), [runEvents]);
  const preferredDrawerArtifactKind = useMemo(
    () => preferredArtifactKindFromRunEvents(runEvents),
    [runEvents]
  );
  const activeRunSignal =
    latestRun &&
    latestRun.status !== "completed" &&
    (visibleArtifacts.length > 0 ||
      shouldShowStrategyProfile(workspaceRunIntent) ||
      (workspaceRunIntent === null && Boolean(latestRun.mode)))
      ? latestRun.id
      : null;
  const workspaceSignal = visibleArtifacts[0]?.id ?? activeRunSignal;
  const hasArtifactWorkspace = Boolean(activeRunSignal || visibleArtifacts.length > 0);
  const showArtifactWorkspace = hasArtifactWorkspace && artifactPanelOpen;
  const renderArtifactWorkspace = hasArtifactWorkspace;
  const latestMarketSnapshotForContext = useMemo(
    () => marketSnapshotFromRunEvents(runEvents),
    [runEvents]
  );
  const openArtifactDrawer = useCallback(() => {
    const bestArtifact = getBestArtifactForDrawer(visibleArtifacts, {
      preferredKind: preferredDrawerArtifactKind,
    });
    setSelectedArtifactId(bestArtifact?.id ?? null);
    setArtifactPanelOpen(true);
  }, [preferredDrawerArtifactKind, setArtifactPanelOpen, setSelectedArtifactId, visibleArtifacts]);

  const activeSidebarItem = sidebarItems.find(
    (item) => item.conversation.id === activeConversationId
  );
  const activeConversation = activeSidebarItem?.conversation ?? null;
  const activeConversationTitle =
    activeConversation?.title ?? getUiCopy(language).newChat;
  const conversationStateError =
    activeConversationId && state.error ? errorMessage(state.error) : null;
  const currentWorkflow: StrategyAgentWorkflow = showArtifactWorkspace
    ? "artifact_workspace"
    : activeConversationId
      ? "chat"
      : "new_chat";
  const copilotContextValue = useMemo(
    () => ({
      activeConversationId,
      artifactPanelOpen,
      currentWorkflow,
      language,
      latestMarketSnapshot: latestMarketSnapshotForContext,
      providerReady: providerRouteReady(providerStatus.data),
      selectedArtifactId,
      strategyReadiness: strategyProfile?.snapshot.completeness ?? null,
      tierLabel: me.data?.capability.tier_label ?? null,
      userTier: me.data?.capability.tier ?? null,
    }),
    [
      activeConversationId,
      artifactPanelOpen,
      currentWorkflow,
      language,
      latestMarketSnapshotForContext,
      me.data,
      providerStatus.data,
      selectedArtifactId,
      strategyProfile,
    ]
  );
  const focusComposerInput = useCallback(() => {
    document
      .querySelector<HTMLTextAreaElement>("[data-strategy-composer-input]")
      ?.focus();
  }, []);
  const copilotSuggestionActions: ChatSuggestionItem[] = [];
  const copilotToolCallbacks = useMemo(
    () => ({
      focusComposer: focusComposerInput,
      insertStrategyBlock: focusComposerInput,
      openArtifactWorkspace: openArtifactDrawer,
      openCreateSpec: () => setSpecDialogOpen(true),
      selectArtifact: (artifactId: string | null) => {
        setSelectedArtifactId(artifactId);
        if (artifactId) {
          setArtifactPanelOpen(true);
        }
      },
      useMarketSnapshotForStrategy: focusComposerInput,
    }),
    [focusComposerInput, openArtifactDrawer, setArtifactPanelOpen, setSelectedArtifactId]
  );

  const requestCreateConversation = useCallback(() => {
    stopChatRef.current?.();
    stopRunProgress();
    stopAutoChainContinuation();
    setInlineError(null);
    setRunEvents([]);
    setSelectedConversationId(null);
    router.push("/");
    lastHydratedMessagesKeyRef.current = "empty";
    chat.setMessagesFromConversationState([]);
    setArtifactPanelOpen(false);
    setSelectedArtifactId(null);
  }, [
    chat,
    router,
    setArtifactPanelOpen,
    setSelectedArtifactId,
    stopAutoChainContinuation,
    stopRunProgress,
  ]);
  const openRenameDialog = useCallback((conversation: ChatConversation) => {
    setRenameTarget(conversation);
    setRenameTitle(conversation.title ?? "New chat");
  }, []);
  const openDeleteDialog = useCallback((conversation: ChatConversation) => {
    setDeleteTarget(conversation);
  }, []);

  useEffect(() => {
    if (!workspaceSignal) {
      setArtifactPanelOpen(false);
    }
  }, [setArtifactPanelOpen, workspaceSignal]);

  return (
    <StrategyAgentContextProvider value={copilotContextValue}>
      <StrategyCopilotTools
        callbacks={copilotToolCallbacks}
        suggestions={copilotSuggestionActions}
        toolsAvailable={copilotCapabilities.frontendTools}
      />
      <main className="apple-page-shell flex h-[100dvh] overflow-hidden text-foreground">
      <ConversationSidebar
        accountUsage={accountUsage.data}
        conversations={sidebarItems}
        isLoading={sidebar.isLoading}
        language={language}
        me={me.data}
        onCreate={requestCreateConversation}
        onDelete={openDeleteDialog}
        onLanguageChange={handleLanguageChange}
        onOpenAccountDialog={openAccountDialog}
        onOpenSettingsTab={openSettingsTab}
        onOpenArtifacts={() => router.push("/artifacts")}
        onOpenPaperBots={() => router.push("/paper-bots")}
        onRename={openRenameDialog}
        onSelect={handleSelectConversation}
        onToggleCollapsed={() => setSidebarCollapsed((collapsed) => !collapsed)}
        onThemeChange={setTheme}
        providerStatus={providerStatus.data}
        collapsed={sidebarCollapsed}
        selectedConversationId={activeConversationId}
        theme={theme}
        isCreating={false}
        isNewChatDisabled={false}
      />
      <section className="grid min-h-0 min-w-0 flex-1 grid-rows-[auto_1fr] overflow-hidden bg-transparent">
        <ReadinessStrip
          title={activeConversationTitle}
          conversation={activeConversation}
          onDelete={openDeleteDialog}
          onRename={openRenameDialog}
        />
        <div className="flex min-h-0 min-w-0 overflow-hidden">
          <div
            className={cn(
              "min-h-0 min-w-0 flex-none basis-full overflow-hidden transition-[flex-basis] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]",
              renderArtifactWorkspace && (showArtifactWorkspace ? "lg:basis-1/2" : "lg:basis-full")
            )}
          >
            <ChatColumn
              actionRegistry={actionRegistryByToolId}
              artifacts={visibleArtifacts}
              artifactWorkspaceOpen={showArtifactWorkspace}
              chatStatus={chat.status}
              conversations={sidebarItems}
              hasArtifactWorkspace={hasArtifactWorkspace}
              isCreatingConversation={chat.status !== "ready" || promptSubmitPending}
              isStartingChat={promptSubmitPending}
              isLoadingConversation={Boolean(
                activeConversationId &&
                  !state.isError &&
                  (state.isPending || !stateBelongsToActiveConversation)
              )}
              backendMessages={
                stateBelongsToActiveConversation ? state.data?.messages ?? [] : []
              }
              conversationRunEvents={
                stateBelongsToActiveConversation ? state.data?.conversation_run_events ?? [] : []
              }
              pendingUserText={pendingPromptText}
              language={language}
              disabled={chat.status !== "ready" || promptSubmitPending}
              error={inlineError ?? conversationStateError}
              messages={chat.messages}
              onCreateConversation={requestCreateConversation}
              onFeedback={handleAssistantFeedback}
              onBacktestApprovalDecision={async ({ approvalId, conversationId, decision }) => {
                await decideBacktestApproval.mutateAsync({
                  approvalId,
                  conversationId,
                  decision,
                });
              }}
              onPromptSubmit={handlePromptSubmit}
              onRegenerate={handleRegenerateMessage}
              onSelectConversation={handleSelectConversation}
              onSelectArtifact={(artifactId) => {
                setSelectedArtifactId(artifactId);
                setArtifactPanelOpen(true);
              }}
              onOpenCreateSpec={() => setSpecDialogOpen(true)}
              onContinueWorkflowTask={continueWorkflowTask}
              onViewArtifactWorkspace={openArtifactDrawer}
              onStop={() => void chat.stop()}
              isBacktestApprovalSubmitting={decideBacktestApproval.isPending}
              selectedConversationId={activeConversationId}
              runEvents={runEvents}
              strategyProfile={strategyProfile}
              webSearchMode={webSearchMode}
              onWebSearchModeChange={updateWebSearchMode}
            />
          </div>
          {renderArtifactWorkspace && (
            <div
              className={cn(
                "min-h-0 min-w-0 flex-none overflow-hidden transition-[flex-basis] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]",
                showArtifactWorkspace ? "basis-full lg:basis-1/2" : "basis-full lg:basis-0"
              )}
            >
              <ArtifactDrawerPanel
                artifacts={visibleArtifacts}
                authKey={me.data?.capability.workspace_id ?? "workspace"}
                client={client}
                language={language}
                hasOlderArtifacts={hasOlderConversationArtifacts}
                isLoadingOlderArtifacts={olderConversationArtifacts.isFetchingNextPage}
                onLoadOlderArtifacts={() => void olderConversationArtifacts.fetchNextPage()}
                onBacktestQueued={({ childRunId, conversationId, sourceRunId }) => {
                  const continuation: AutoChainContinuation = {
                    childRunId,
                    conversationId,
                    sourceRunId,
                    status: "queued",
                  };
                  setAutoChainContinuation(continuation);
                  setRunEvents((current) =>
                    mergeRunEvents(
                      current,
                      [
                        createAutoChainLocalEvent(
                          continuation,
                          "chat.auto_chain.waiting_for_backtest",
                          "Waiting for the preview evidence to finish."
                        ),
                      ],
                      60
                    )
                  );
                }}
                onClose={() => setArtifactPanelOpen(false)}
                open={showArtifactWorkspace}
                preferredArtifactKind={preferredDrawerArtifactKind}
                runEvents={runEvents}
              />
            </div>
          )}
        </div>
      </section>
      <CreateFromSpecDialog
        allowedRunModes={allowedRunModes}
        createRun={() => createRun.mutate()}
        disabled={createRun.isPending}
        isCreatingRun={createRun.isPending}
        language={language}
        onOpenChange={setSpecDialogOpen}
        open={specDialogOpen}
        runMode={activeRunMode}
        setRunMode={setRunMode}
        setSpecDraft={setSpecDraft}
        specDraft={specDraft}
      />
      <AccountDialogs
        accountUsage={accountUsage.data}
        dialog={accountDialog}
        healthChecking={providerStatus.isFetching || readiness.isFetching}
        language={language}
        lastHealthCheckedAt={Math.max(providerStatus.dataUpdatedAt, readiness.dataUpdatedAt)}
        me={me.data}
        onLanguageChange={handleLanguageChange}
        onOpenChange={(open) => {
          if (!open) {
            setAccountDialog(null);
          }
        }}
        onCopyValue={copySettingsValue}
        onHealthCheck={() => {
          void Promise.all([providerStatus.refetch(), readiness.refetch()]);
        }}
        onRetryUsage={() => void accountUsage.refetch()}
        onSettingsTabChange={setSettingsTab}
        providerStatus={providerStatus.data}
        readiness={readiness.data}
        resolvedTheme={resolvedTheme}
        settingsTab={settingsTab}
        theme={theme}
        onThemeChange={setTheme}
        usageError={accountUsage.isError}
        usageLoading={accountUsage.isLoading || accountUsage.isFetching}
      />
      <RenameConversationDialog
        disabled={renameConversation.isPending}
        language={language}
        onOpenChange={(open) => {
          if (!open) {
            setRenameTarget(null);
            setRenameTitle("");
          }
        }}
        onRename={() => {
          if (!renameTarget) {
            return;
          }
          renameConversation.mutate({
            conversationId: renameTarget.id,
            title: renameTitle,
          });
        }}
        open={Boolean(renameTarget)}
        setTitle={setRenameTitle}
        title={renameTitle}
      />
      <DeleteConversationDialog
        conversationTitle={deleteTarget?.title ?? getUiCopy(language).newChat}
        disabled={deleteConversation.isPending}
        language={language}
        onDelete={() => {
          if (deleteTarget) {
            deleteConversation.mutate(deleteTarget.id);
          }
        }}
        onOpenChange={(open) => {
          if (!open) {
            setDeleteTarget(null);
          }
        }}
        open={Boolean(deleteTarget)}
      />
      </main>
    </StrategyAgentContextProvider>
  );
}

function ChatColumn({
  actionRegistry,
  artifacts,
  artifactWorkspaceOpen,
  backendMessages,
  chatStatus,
  conversationRunEvents,
  conversations,
  disabled,
  error,
  hasArtifactWorkspace,
  isCreatingConversation,
  isStartingChat,
  isBacktestApprovalSubmitting,
  isLoadingConversation,
  language,
  messages,
  onBacktestApprovalDecision,
  onCreateConversation,
  onFeedback,
  onContinueWorkflowTask,
  onPromptSubmit,
  onOpenCreateSpec,
  onWebSearchModeChange,
  pendingUserText,
  onRegenerate,
  onSelectConversation,
  onSelectArtifact,
  onStop,
  onViewArtifactWorkspace,
  selectedConversationId,
  runEvents,
  strategyProfile,
  webSearchMode,
}: {
  actionRegistry: ActionRegistryLookup;
  artifacts: Artifact[];
  artifactWorkspaceOpen: boolean;
  backendMessages: BackendMessage[];
  chatStatus: string;
  conversationRunEvents: RunEvent[];
  conversations: ConversationSidebarItem[];
  disabled: boolean;
  error: string | null;
  hasArtifactWorkspace: boolean;
  isCreatingConversation: boolean;
  isStartingChat: boolean;
  isBacktestApprovalSubmitting: boolean;
  isLoadingConversation: boolean;
  language: UiLanguagePreference;
  messages: StrategyChatMessage[];
  onBacktestApprovalDecision: (input: {
    approvalId: string;
    conversationId: string;
    decision: "approved" | "rejected";
  }) => Promise<void>;
  onCreateConversation: () => void;
  onFeedback: (messageId: string, rating: "up" | "down") => Promise<void>;
  onContinueWorkflowTask: (taskId: string) => Promise<void>;
  onOpenCreateSpec: () => void;
  onPromptSubmit: (message: PromptSubmitPayload) => Promise<void>;
  onWebSearchModeChange: (mode: WebSearchMode) => void;
  pendingUserText: string | null;
  onRegenerate: (messageId: string) => Promise<void>;
  onSelectConversation: (conversationId: string) => void;
  onSelectArtifact: (artifactId: string) => void;
  onStop: () => void;
  onViewArtifactWorkspace: () => void;
  selectedConversationId: string | null;
  runEvents: RunEvent[];
  strategyProfile: StrategyProfile | null;
  webSearchMode: WebSearchMode;
}) {
  const client = useBrowserBackendClient();
  const queryClient = useQueryClient();
  const router = useRouter();
  const [workflowTaskOverrides, setWorkflowTaskOverrides] = useState<Record<string, unknown>>({});
  const [backtestStatusNowMs, setBacktestStatusNowMs] = useState(() => Date.now());
  const backtestLiveStatus = useMemo(
    () => backtestLiveStatusFromRunEvents(runEvents, backtestStatusNowMs),
    [backtestStatusNowMs, runEvents]
  );
  const pendingBacktestApproval = useMemo(
    () => pendingBacktestApprovalFromRunEvents(runEvents),
    [runEvents]
  );
  useEffect(() => {
    queueMicrotask(() => {
      setWorkflowTaskOverrides((current) => (
        Object.keys(current).length > 0 ? {} : current
      ));
    });
  }, [selectedConversationId]);
  useEffect(() => {
    if (
      !backtestLiveStatus ||
      backtestLiveStatus.status === "completed" ||
      backtestLiveStatus.status === "failed"
    ) {
      return;
    }
    const intervalId = window.setInterval(() => setBacktestStatusNowMs(Date.now()), 15_000);
    return () => window.clearInterval(intervalId);
  }, [backtestLiveStatus?.runId, backtestLiveStatus?.status]);
  const isChatWorking = chatStatus === "streaming" || chatStatus === "submitted";
  const renderableMessages = useMemo(
    () => messages.filter((message) => isRenderableMessage(message)),
    [messages]
  );
  const pendingUserMessage = useMemo(() => {
    const text = pendingUserText?.trim();
    if (!text) {
      return null;
    }
    const alreadyRendered = renderableMessages.some(
      (message) =>
        message.role === "user" && message.text.trim() === text
    );
    if (alreadyRendered) {
      return null;
    }
    return {
      id: "pending-user-message",
      role: "user",
      text,
      sources: [],
      reasoningSummaries: [],
      backtestReport: null,
      inlineTables: [],
      marketSnapshot: null,
      suggestions: null,
      responseIntent: null,
      workflow: null,
      raw: null,
    } satisfies StrategyChatMessage;
  }, [pendingUserText, renderableMessages]);
  const baseDisplayMessages = useMemo(
    () =>
      pendingUserMessage
        ? [...renderableMessages, pendingUserMessage]
        : renderableMessages,
    [pendingUserMessage, renderableMessages]
  );
  const runSources = useMemo(() => knowledgeSourcesFromRunEvents(runEvents), [runEvents]);
  const latestMarketSnapshot = useMemo(
    () => marketSnapshotFromRunEvents(runEvents),
    [runEvents]
  );
  const latestRunWorkflow = useMemo(
    () =>
      strategyWorkflowFromRunEvents(runEvents) ??
      strategyWorkflowFromRunEvents(conversationRunEvents),
    [conversationRunEvents, runEvents]
  );
  const preferredDrawerArtifactKind = useMemo(
    () => preferredArtifactKindFromRunEvents(conversationRunEvents),
    [conversationRunEvents]
  );
  const waitingElapsedSeconds = useElapsedSeconds(isChatWorking || isStartingChat);
  const turnAssistantMessage = useMemo(
    () => latestAssistantAfterLastUser(baseDisplayMessages),
    [baseDisplayMessages]
  );
  const pendingAssistantMessage = useMemo(() => {
    if (!(isChatWorking || isStartingChat) || turnAssistantMessage) {
      return null;
    }
    return {
      id: "pending-assistant-message",
      role: "assistant",
      text: "",
      sources: [],
      reasoningSummaries: [],
      backtestReport: null,
      inlineTables: [],
      marketSnapshot: null,
      suggestions: null,
      responseIntent: null,
      workflow: null,
      raw: null,
    } satisfies StrategyChatMessage;
  }, [isChatWorking, isStartingChat, turnAssistantMessage]);
  const displayMessages = useMemo(
    () =>
      pendingAssistantMessage
        ? [...baseDisplayMessages, pendingAssistantMessage]
        : baseDisplayMessages,
    [baseDisplayMessages, pendingAssistantMessage]
  );
  const activeAssistantMessageId = (turnAssistantMessage ?? pendingAssistantMessage)?.id;
  const hasStreamingAssistantText = isChatWorking && hasAssistantText(messages);
  const activitiesByMessageId = useMemo(
    () =>
      mapRunEventsToChatActivitiesByAssistantMessage({
        activeAssistantMessageId,
        conversationEvents: conversationRunEvents,
        language,
        liveEvents: runEvents,
        registry: actionRegistry,
        resolveAnchorMessageId: (event) =>
          runEventAssistantAnchorMessageId({ backendMessages, event }),
      }),
    [
      actionRegistry,
      activeAssistantMessageId,
      backendMessages,
      conversationRunEvents,
      language,
      runEvents,
    ]
  );
  const anchoredActivityCount = useMemo(
    () =>
      Array.from(activitiesByMessageId.values()).reduce(
        (total, activities) => total + activities.length,
        0
      ),
    [activitiesByMessageId]
  );
  const fallbackActivities = useMemo(
    () =>
      anchoredActivityCount === 0 && (isChatWorking || isStartingChat)
        ? mapRunEventsToChatActivities(runEvents, language, actionRegistry)
        : [],
    [actionRegistry, anchoredActivityCount, isChatWorking, isStartingChat, language, runEvents]
  );
  const artifactGroups = useMemo(
    () => groupArtifactsByAnchorMessage({ artifacts, backendMessages }),
    [artifacts, backendMessages]
  );
  const backtestSummaryRunIds = useMemo(
    () =>
      backtestSummaryRunIdsByAnchorMessage({
        backendMessages,
        events: conversationRunEvents,
      }),
    [backendMessages, conversationRunEvents]
  );
  const backtestResultArtifacts = useMemo(
    () => backtestResultArtifactsByRunId(artifacts),
    [artifacts]
  );
  const persistedRunMetadata = useMemo(
    () =>
      runEventMetadataByAnchorMessage({
        backendMessages,
        events: conversationRunEvents,
      }),
    [backendMessages, conversationRunEvents]
  );
  const activeWorkflow = useMemo<WorkflowState | null>(() => {
    const assistantMessage = turnAssistantMessage ?? pendingAssistantMessage;
    if (!assistantMessage) {
      return latestRunWorkflow;
    }
    const anchoredWorkflow =
      persistedRunMetadata.get(assistantMessage.id)?.workflow ?? assistantMessage.workflow ?? null;
    return latestRunWorkflow ?? anchoredWorkflow;
  }, [
    latestRunWorkflow,
    pendingAssistantMessage,
    persistedRunMetadata,
    turnAssistantMessage,
  ]);
  const activeWorkflowTaskIdentity = useMemo(
    () =>
      activeWorkflow
        ? `${activeWorkflow.workflow_id}:${activeWorkflow.tasks.map((task) => task.id).join("|")}`
        : "none",
    [activeWorkflow]
  );
  useEffect(() => {
    queueMicrotask(() => {
      setWorkflowTaskOverrides((current) => (
        Object.keys(current).length > 0 ? {} : current
      ));
    });
  }, [activeWorkflowTaskIdentity]);
  const activeWorkflowWithTaskOverrides = useMemo<WorkflowState | null>(() => {
    if (!activeWorkflow || Object.keys(workflowTaskOverrides).length === 0) {
      return activeWorkflow;
    }
    const overriddenTasks = activeWorkflow.tasks.map((task) =>
      workflowTaskOverrides[task.id]
        ? { ...task, ...(workflowTaskOverrides[task.id] as Record<string, unknown>) }
        : task
    );
    return (
      normalizeWorkflowState({
        ...activeWorkflow,
        tasks: overriddenTasks,
      }) ?? activeWorkflow
    );
  }, [activeWorkflow, workflowTaskOverrides]);
  const waitingSlowState = useMemo(
    () => workflowAwareSlowProviderState(waitingElapsedSeconds, language, activeWorkflowWithTaskOverrides),
    [activeWorkflowWithTaskOverrides, language, waitingElapsedSeconds]
  );
  const handleWorkflowTaskSubmit = useCallback(
    async (taskId: string, values: Record<string, unknown>) => {
      const task = await client.submitWorkflowTaskResponse(taskId, { values, status: "completed" });
      setWorkflowTaskOverrides((current) => ({ ...current, [task.id]: task }));
      if (selectedConversationId) {
        await queryClient.invalidateQueries({ queryKey: ["conversation-state", selectedConversationId] });
      }
      if (task.continuation?.required && task.continuation.task_id) {
        await onContinueWorkflowTask(task.continuation.task_id);
      }
    },
    [client, onContinueWorkflowTask, queryClient, selectedConversationId]
  );
  const handleWorkflowTaskAction = useCallback(
    async (taskId: string, action: WorkflowAction, values: Record<string, unknown> = {}) => {
      if (action.kind === "confirm_start_bot_proposal") {
        const proposalId =
          action.target_ref ??
          activeWorkflowWithTaskOverrides?.actions.find((item) => item.id === action.id)?.target_ref ??
          activeWorkflowWithTaskOverrides?.bot_proposal_id;
        if (!proposalId) {
          const task = await client.submitWorkflowTaskResponse(taskId, {
            values,
            status: "completed",
          });
          setWorkflowTaskOverrides((current) => ({ ...current, [task.id]: task }));
          return;
        }
        const result = await client.confirmStartBotProposal(
          proposalId,
          botProposalConfirmStartPayloadFromWorkflowValues(values)
        );
        const task = action.enabled
          ? await client.submitWorkflowTaskAction(taskId, action.id, {
              values,
              status: "approved",
            })
          : await client.submitWorkflowTaskResponse(taskId, {
              values,
              status: "completed",
            });
        setWorkflowTaskOverrides((current) => ({ ...current, [task.id]: task }));
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["paper-bot-runtimes"] }),
          selectedConversationId
            ? queryClient.invalidateQueries({ queryKey: ["conversation-state", selectedConversationId] })
            : Promise.resolve(),
        ]);
        router.push(`/paper-bots?runtime=${encodeURIComponent(result.runtime.id)}`);
        return;
      }
      const task = await client.submitWorkflowTaskAction(taskId, action.id, {
        values,
        status: "approved",
      });
      setWorkflowTaskOverrides((current) => ({ ...current, [task.id]: task }));
      if (selectedConversationId) {
        await queryClient.invalidateQueries({ queryKey: ["conversation-state", selectedConversationId] });
      }
    },
    [activeWorkflowWithTaskOverrides, client, queryClient, router, selectedConversationId]
  );
  const staticSuggestions = useMemo(() => getChatSuggestions(language), [language]);
  const isEmptyChat = !isLoadingConversation && displayMessages.length === 0;
  const shouldHideComposerForWorkflowTask = hasBlockingWorkflowTask(activeWorkflowWithTaskOverrides);
  const shouldHideComposerForBacktestApproval = Boolean(pendingBacktestApproval && selectedConversationId);
  const visibleBacktestRailStatus =
    backtestLiveStatus?.status === "completed" ? null : backtestLiveStatus;
  const hasRightRail = !artifactWorkspaceOpen && Boolean(activeWorkflowWithTaskOverrides || visibleBacktestRailStatus);
  const chatRailOffsetClass = hasRightRail
    ? "min-[1440px]:-translate-x-40 min-[1536px]:-translate-x-44"
    : "";

  return (
    <section
      className={cn(
        "together-technical-canvas relative grid h-full min-h-0 grid-rows-[auto_1fr_auto] overflow-hidden border-r border-border md:grid-rows-[1fr_auto]",
        isEmptyChat && "md:grid-rows-[1fr]"
      )}
    >
      <MobileConversationBar
        conversations={conversations}
        disabled={isCreatingConversation}
        language={language}
        onCreate={onCreateConversation}
        onSelect={onSelectConversation}
        selectedConversationId={selectedConversationId}
      />
      {!artifactWorkspaceOpen ? (
        <WorkspaceRightRail
          backtestStatus={visibleBacktestRailStatus}
          isWorking={isChatWorking || isStartingChat}
          workflow={activeWorkflowWithTaskOverrides}
        />
      ) : null}
      <Conversation className="min-h-0 overflow-hidden">
        <ConversationContent
          className={cn(
            "mx-auto w-full max-w-3xl px-4 py-8 transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none",
            chatRailOffsetClass
          )}
        >
          <div className="min-w-0 space-y-4">
          {isLoadingConversation ? (
            <ConversationLoadingState language={language} />
          ) : isEmptyChat ? (
            <EmptyChatStart
              disabled={disabled}
              isStartingChat={isStartingChat}
              language={language}
              onPromptSubmit={onPromptSubmit}
              onWebSearchModeChange={onWebSearchModeChange}
              suggestions={staticSuggestions}
              webSearchMode={webSearchMode}
            />
          ) : (
            displayMessages.map((message) => {
              const persistedMetadata = persistedRunMetadata.get(message.id);
              const mergedMetadata = persistedMetadata
                ? mergeStrategyChatMessageMetadata(
                    {
                      backtestReport: message.backtestReport,
                      inlineTables: message.inlineTables,
                      marketSnapshot: message.marketSnapshot,
                      reasoningSummaries: message.reasoningSummaries,
                      responseIntent: message.responseIntent,
                      sources: message.sources,
                      suggestions: message.suggestions,
                      workflow: message.workflow,
                    },
                    persistedMetadata
                  )
                : null;
              const renderedMessage = mergedMetadata
                ? {
                    ...message,
                    backtestReport: mergedMetadata.backtestReport,
                    inlineTables: mergedMetadata.inlineTables,
                    marketSnapshot: mergedMetadata.marketSnapshot,
                    reasoningSummaries: mergedMetadata.reasoningSummaries,
                    responseIntent: mergedMetadata.responseIntent,
                    sources: mergedMetadata.sources,
                    suggestions: mergedMetadata.suggestions,
                    workflow: mergedMetadata.workflow,
                  }
                : message;
              const anchoredArtifacts = artifactGroups.get(message.id) ?? [];
              const summaryRunId = backtestSummaryRunIds.get(message.id) ?? null;
              const messageArtifacts =
                anchoredArtifacts.length > 0
                  ? anchoredArtifacts
                  : summaryRunId
                    ? backtestResultArtifacts.get(summaryRunId) ?? []
                    : [];
              const messageActivities =
                renderedMessage.role === "assistant"
                  ? activitiesByMessageId.get(renderedMessage.id) ?? []
                  : [];
              return (
                <Fragment key={renderedMessage.id}>
                  <StrategyMessage
                    actionRegistry={actionRegistry}
                    artifact={getBestArtifactForDrawer(messageArtifacts, {
                      preferredKind: summaryRunId ? "backtest_dashboard" : preferredDrawerArtifactKind,
                    })}
                    artifactCount={messageArtifacts.length}
                    fallbackSources={
                      renderedMessage.sources.length > 0
                        ? []
                        : renderedMessage.id === activeAssistantMessageId ? runSources : []
                    }
                    isTransient={renderedMessage.id === "pending-assistant-message"}
                    language={language}
                    message={renderedMessage}
                    onFeedback={onFeedback}
                    onOpenCreateSpec={onOpenCreateSpec}
                    onRegenerate={onRegenerate}
                    onSuggestionSubmit={onPromptSubmit}
                    onViewArtifactWorkspace={onViewArtifactWorkspace}
                    fallbackMarketSnapshot={
                      renderedMessage.marketSnapshot ??
                      persistedMetadata?.marketSnapshot ??
                      (renderedMessage.id === activeAssistantMessageId ? latestMarketSnapshot : null)
                    }
                    waitingSlowState={waitingSlowState}
                  />
                  {messageActivities.length > 0 ? (
                    <AssistantActivity
                      activities={messageActivities}
                      isWorking={renderedMessage.id === activeAssistantMessageId && (isChatWorking || isStartingChat)}
                      language={language}
                      onSelectArtifact={onSelectArtifact}
                    />
                  ) : null}
                </Fragment>
              );
            })
          )}
          <AssistantActivity
            activities={fallbackActivities}
            isWorking={
              (isChatWorking || isStartingChat) &&
              !hasStreamingAssistantText &&
              !pendingAssistantMessage &&
              anchoredActivityCount === 0
            }
            language={language}
            onSelectArtifact={onSelectArtifact}
          />
          </div>
        </ConversationContent>
        <ConversationScrollButton />
      </Conversation>
      <div
        className={cn(
          "mx-auto w-full max-w-3xl shrink-0 px-4 pb-[max(1rem,env(safe-area-inset-bottom))] transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none",
          chatRailOffsetClass,
          isEmptyChat &&
            !shouldHideComposerForWorkflowTask &&
            !shouldHideComposerForBacktestApproval &&
            !error &&
            "hidden"
        )}
      >
        {error && (
          <div className="mb-3 flex items-start gap-2 rounded-[4px] border border-red-500/30 bg-red-500/10 p-3 text-red-700 text-sm dark:text-red-300">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}
        {shouldHideComposerForWorkflowTask && activeWorkflowWithTaskOverrides ? (
          <WorkflowTaskPrompt
            onSubmitTask={handleWorkflowTaskSubmit}
            workflow={activeWorkflowWithTaskOverrides}
          />
        ) : shouldHideComposerForBacktestApproval && pendingBacktestApproval && selectedConversationId ? (
          <BacktestApprovalPrompt
            disabled={isBacktestApprovalSubmitting}
            onDecision={(decision) =>
              onBacktestApprovalDecision({
                approvalId: pendingBacktestApproval.approvalId,
                conversationId: selectedConversationId,
                decision,
              })
            }
          />
        ) : (
          <div className={cn(isEmptyChat && "pointer-events-auto")}>
            <ChatPromptComposer
              chatStatus={chatStatus}
              disabled={disabled}
              language={language}
              onPromptSubmit={onPromptSubmit}
              onWebSearchModeChange={onWebSearchModeChange}
              onStop={onStop}
              webSearchMode={webSearchMode}
            />
          </div>
        )}
      </div>
    </section>
  );
}

function ConversationLoadingState({
  language,
}: {
  language: UiLanguagePreference;
}) {
  const t = getUiCopy(language);
  return (
    <div className="flex min-h-[240px] items-center justify-center">
      <div className="flex items-center gap-2 text-muted-foreground text-sm">
        <Loader2 className="size-4 animate-spin" />
        <Shimmer>{t.loadingConversation}</Shimmer>
      </div>
    </div>
  );
}

function WebSearchToggle({
  disabled,
  language,
  mode,
  onChange,
}: {
  disabled: boolean;
  language: UiLanguagePreference;
  mode: WebSearchMode;
  onChange: (mode: WebSearchMode) => void;
}) {
  const t = getUiCopy(language);
  const labels: Record<WebSearchMode, string> = {
    auto: t.webSearchAuto,
    off: t.webSearchOff,
    on: t.webSearchOn,
  };
  const label = labels[mode];
  const nextMode = WEB_SEARCH_MODE_NEXT[mode];

  return (
    <Button
      aria-label={`${t.webSearchToggleTitle}: ${label}`}
      className={cn(
        "h-8 rounded-full px-2.5 text-xs",
        mode === "off" && "text-muted-foreground"
      )}
      disabled={disabled}
      onClick={() => onChange(nextMode)}
      title={`${t.webSearchToggleTitle}: ${label}`}
      type="button"
      variant={mode === "off" ? "outline" : "secondary"}
    >
      <Globe2 className="size-3.5" />
      <span>{label}</span>
    </Button>
  );
}

function EmptyChatStart({
  disabled,
  isStartingChat,
  language,
  onPromptSubmit,
  onWebSearchModeChange,
  suggestions,
  webSearchMode,
}: {
  disabled: boolean;
  isStartingChat: boolean;
  language: UiLanguagePreference;
  onPromptSubmit: (message: PromptSubmitPayload) => Promise<void>;
  onWebSearchModeChange: (mode: WebSearchMode) => void;
  suggestions: ReturnType<typeof getChatSuggestions>;
  webSearchMode: WebSearchMode;
}) {
  const t = getUiCopy(language);
  const elapsedSeconds = useElapsedSeconds(isStartingChat);
  const suggestionLabels = [
    t.signedOutSuggestionSpec,
    t.signedOutSuggestionPine,
    t.signedOutSuggestionRisk,
  ];

  return (
    <StrategyStartPrompt
      className="min-h-[calc(100dvh-7rem)]"
      disabled={disabled}
      onSubmit={(text) => onPromptSubmit({ text })}
      placeholder={t.signedOutPlaceholder}
      requireText
      startAction={
        <WebSearchToggle
          disabled={disabled}
          language={language}
          mode={webSearchMode}
          onChange={onWebSearchModeChange}
        />
      }
      submitLabel={t.send}
      status={
        isStartingChat ? (
          <FirstTokenLoader
            slowState={slowProviderState(elapsedSeconds, language)}
          />
        ) : undefined
      }
      suggestions={suggestions.slice(0, 3).map((suggestion, index) => ({
        label: suggestionLabels[index] ?? suggestion.label,
        disabled,
        onSelect: () => void onPromptSubmit({ text: suggestion.prompt }),
      }))}
      title={t.signedOutTitle}
    />
  );
}

function ChatPromptComposer({
  chatStatus,
  disabled,
  language,
  onPromptSubmit,
  onWebSearchModeChange,
  onStop,
  webSearchMode,
}: {
  chatStatus: string;
  disabled: boolean;
  language: UiLanguagePreference;
  onPromptSubmit: (message: PromptSubmitPayload) => Promise<void>;
  onWebSearchModeChange: (mode: WebSearchMode) => void;
  onStop: () => void;
  webSearchMode: WebSearchMode;
}) {
  const t = getUiCopy(language);
  const [value, setValue] = useState("");
  const isChatWorking = chatStatus === "streaming" || chatStatus === "submitted";
  return (
    <StrategyComposer
      disabled={disabled}
      endAction={({ submitDisabled }) => (
        <>
          {isChatWorking ? (
            <Button
              className="rounded-full"
              onClick={onStop}
              size="icon-sm"
              type="button"
              variant="outline"
              title={t.stop}
            >
              <Square className="size-3" />
              <span className="sr-only">{t.stop}</span>
            </Button>
          ) : (
            <Button
              aria-label={t.send}
              className="rounded-full"
              disabled={submitDisabled}
              size="icon-sm"
              title={t.send}
              type="submit"
            >
              <ArrowUp className="size-4" />
              <span className="sr-only">{t.send}</span>
            </Button>
          )}
        </>
      )}
      onSubmit={async (text) => {
        setValue("");
        await onPromptSubmit({ text });
      }}
      onValueChange={setValue}
      placeholder={t.signedOutPlaceholder}
      requireText
      startAction={
        <WebSearchToggle
          disabled={disabled}
          language={language}
          mode={webSearchMode}
          onChange={onWebSearchModeChange}
        />
      }
      value={value}
    />
  );
}

function StrategyMessage({
  actionRegistry,
  artifact = null,
  artifactCount = 0,
  fallbackMarketSnapshot = null,
  fallbackSources = [],
  isTransient = false,
  language,
  message,
  onFeedback,
  onOpenCreateSpec,
  onRegenerate,
  onSuggestionSubmit,
  onViewArtifactWorkspace,
  waitingSlowState,
}: {
  actionRegistry: ActionRegistryLookup;
  artifact?: Artifact | null;
  artifactCount?: number;
  fallbackMarketSnapshot?: MarketSnapshot | null;
  fallbackSources?: ChatMessageSource[];
  isTransient?: boolean;
  language: UiLanguagePreference;
  message: StrategyChatMessage;
  onFeedback: (messageId: string, rating: "up" | "down") => Promise<void>;
  onOpenCreateSpec: () => void;
  onRegenerate: (messageId: string) => Promise<void>;
  onSuggestionSubmit: (message: PromptSubmitPayload) => Promise<void>;
  onViewArtifactWorkspace: () => void;
  waitingSlowState?: ChatLoadingState;
}) {
  const [actionState, setActionState] = useState<{
    kind: "idle" | "loading" | "success" | "error";
    message?: string;
  }>({ kind: "idle" });
  const [externalSource, setExternalSource] = useState<{ title: string; url: string } | null>(null);
  const text = message.text;
  const sources = mergeMessageSources(message.sources, fallbackSources);
  const marketSnapshot = message.marketSnapshot ?? fallbackMarketSnapshot;
  const backtestReport = message.backtestReport;
  const inlineTables = message.inlineTables;
  const hasDashboardArtifact = artifact?.presentation.viewer_kind === "backtest_dashboard";
  const dashboardPreviewSummary = hasDashboardArtifact ? artifact?.preview_summary ?? null : null;
  const t = getUiCopy(language);
  const { showToast } = useToast();

  const runAction = async (label: string, action: () => Promise<void>) => {
    setActionState({ kind: "loading", message: label });
    try {
      await action();
      setActionState({ kind: "success", message: label });
    } catch (error) {
      setActionState({ kind: "error", message: errorMessage(error) });
    }
  };

  const copyExternalSourceUrl = async () => {
    if (!externalSource) {
      return;
    }
    try {
      await navigator.clipboard.writeText(externalSource.url);
      showToast({ title: t.copied });
    } catch {
      showToast({ title: t.copyFailed, variant: "error" });
    }
  };

  const openExternalSourceUrl = () => {
    if (!externalSource) {
      return;
    }
    window.open(externalSource.url, "_blank", "noopener,noreferrer");
    setExternalSource(null);
  };

  const renderExternalLinkModal = ({
    isOpen,
    onClose,
    onConfirm,
    url,
  }: {
    isOpen: boolean;
    onClose: () => void;
    onConfirm: () => void;
    url: string;
  }) => {
    const copyUrl = async () => {
      try {
        await navigator.clipboard.writeText(url);
        showToast({ title: t.copied });
      } catch {
        showToast({ title: t.copyFailed, variant: "error" });
      }
    };

    return (
      <Dialog onOpenChange={(open) => !open && onClose()} open={isOpen}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <ExternalLink className="size-4" />
              {t.externalLinkTitle}
            </DialogTitle>
            <DialogDescription>{t.externalLinkDescription}</DialogDescription>
          </DialogHeader>
          <div className="min-w-0 rounded-[4px] border border-border bg-muted/50 p-3">
            <p className="break-all font-mono text-muted-foreground text-xs">{url}</p>
          </div>
          <DialogFooter className="sm:justify-between">
            <Button onClick={copyUrl} type="button" variant="outline">
              <Clipboard className="size-4" />
              {t.copyLink}
            </Button>
            <Button onClick={onConfirm} type="button">
              <ExternalLink className="size-4" />
              {t.openLink}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    );
  };
  const reasoningWorkflowStep =
    [...message.reasoningSummaries].reverse().find((reasoning) => reasoning.workflowStep)
      ?.workflowStep ?? null;
  const workflowCurrentStepId = reasoningWorkflowStep ?? message.workflow?.current_step ?? null;
  const workflowCurrentStepLabel =
    message.workflow && workflowCurrentStepId
      ? getWorkflowDefinition(message.workflow.workflow_id)?.steps.find(
          (step) => step.id === workflowCurrentStepId
        )?.label
      : null;
  const reasoningNodes =
    message.reasoningSummaries.length > 0 ? (
      <ChainOfThought className="mb-2" defaultOpen>
        <ChainOfThoughtHeader className="h-8 px-3">
          <Shimmer as="span" className="max-w-full truncate" duration={1.8}>
            {t.modelReasoningTitle}
          </Shimmer>
        </ChainOfThoughtHeader>
        <ChainOfThoughtContent>
          {message.reasoningSummaries.map((reasoning, index) => (
            <ChainOfThoughtStep
              key={reasoning.id}
              {...chainOfThoughtStepCopy(reasoning.text)}
              status={
                reasoning.state === "streaming" &&
                index === message.reasoningSummaries.length - 1
                  ? "active"
                  : "complete"
              }
            />
          ))}
          {workflowCurrentStepLabel && !reasoningWorkflowStep ? (
            <ChainOfThoughtStep label={workflowCurrentStepLabel} status="pending" />
          ) : null}
        </ChainOfThoughtContent>
      </ChainOfThought>
    ) : null;
  const normalBacktestReport = backtestReport?.kind === "report" ? backtestReport : null;
  const backtestResultNode =
    message.role === "assistant" && (normalBacktestReport || (hasDashboardArtifact && inlineTables.length === 0)) ? (
      <BacktestResultInlineCard
        previewSummary={dashboardPreviewSummary}
        onBuildRobustness={() =>
          void onSuggestionSubmit({
            text:
              actionToolPrompt("build_robustness_report", actionRegistry) ??
              "Build a review-only robustness report for the current preview evidence. Summarize sample size, fees, slippage, drawdown, OOS concerns, and suspicious metrics.",
          })
        }
        onOpenDashboard={onViewArtifactWorkspace}
        onShowTrades={() =>
          void onSuggestionSubmit({
            text:
              actionToolPrompt("query_backtest_trades", actionRegistry) ??
              "Show me the trades for the latest completed backtest.",
          })
        }
        report={normalBacktestReport}
      />
    ) : null;
  const backtestReportNode =
    message.role === "assistant" && backtestReport && backtestReport.kind !== "report" ? (
      <BacktestReportCard
        isSubmittingFeedback={false}
        onFeedback={async () => undefined}
        report={backtestReport}
      />
    ) : null;
  const inlineTableNodes =
    message.role === "assistant"
      ? inlineTables.map((table, index) => (
          <ChatInlineTableCard key={`${table.kind}-${table.run_id ?? "run"}-${index}`} table={table} />
        ))
      : [];
  const suppressArtifactCard =
    hasDashboardArtifact && (Boolean(backtestResultNode) || inlineTables.length > 0);

  return (
    <>
    <Message from={message.role}>
      {message.role === "assistant" && sources.length > 0 && (
        <Sources className="mb-3 text-xs">
          <SourcesTrigger
            className="group mb-1 inline-flex items-center gap-1.5 text-[var(--together-accent-blue)] transition hover:text-[var(--together-accent-periwinkle)]"
            count={sources.length}
          >
            <span className="truncate">
              {t.usedSources.replace("{count}", String(sources.length))}
            </span>
            <ChevronDown className="size-3 shrink-0 transition-transform group-data-[state=open]:rotate-180" />
          </SourcesTrigger>
          <SourcesContent className="mt-1 w-fit gap-1">
            {sources.map((source) =>
              source.type === "external" && source.url ? (
                <button
                  className="flex max-w-full items-center gap-2 text-left text-[var(--together-accent-blue)] transition hover:text-[var(--together-accent-periwinkle)] hover:underline"
                  key={source.id}
                  onClick={() => setExternalSource({ title: source.title, url: source.url ?? "" })}
                  title={source.title}
                  type="button"
                >
                  <BookOpen className="size-3.5 shrink-0" />
                  <span className="truncate font-medium">{source.title}</span>
                </button>
              ) : (
                <div
                  className="flex items-center gap-2 text-[var(--together-accent-blue)]"
                  key={source.id}
                >
                  <BookOpen className="size-3.5 shrink-0" />
                  <span className="truncate font-medium">{source.title}</span>
                </div>
              )
            )}
          </SourcesContent>
        </Sources>
      )}
      {text ? (
        <MessageContent className="rounded-[4px] border border-border bg-background px-4 py-3">
          <MessageMarkdown
            content={text}
            linkSafety={{
              enabled: true,
              renderModal: renderExternalLinkModal,
            }}
          />
          {reasoningNodes}
          {message.role === "assistant" && marketSnapshot && (
            <MarketSnapshotCard language={language} snapshot={marketSnapshot} />
          )}
          {inlineTableNodes.map((node, index) => (
            <div className="mt-4" key={index}>
              {node}
            </div>
          ))}
          {backtestResultNode && <div className="mt-4">{backtestResultNode}</div>}
          {backtestReportNode && <div className="mt-4">{backtestReportNode}</div>}
        </MessageContent>
      ) : (
        <>
          {!reasoningNodes && isTransient && waitingSlowState && (
            <FirstTokenLoader slowState={waitingSlowState} />
          )}
          {reasoningNodes}
          {message.role === "assistant" && marketSnapshot && (
            <MarketSnapshotCard language={language} snapshot={marketSnapshot} />
          )}
          {inlineTableNodes}
          {backtestResultNode}
          {backtestReportNode}
        </>
      )}
      {!isTransient && message.role === "assistant" && artifact && !suppressArtifactCard && (
        <ArtifactTranscriptCard
          artifact={artifact}
          artifactCount={artifactCount}
          language={language}
          onOpen={onViewArtifactWorkspace}
        />
      )}
      {!isTransient && message.role === "assistant" && (
        <div className="space-y-1">
          <MessageActions>
            <MessageAction
              disabled={!text || actionState.kind === "loading"}
              label="Copy response"
              onClick={() =>
                void runAction("Copied", () => navigator.clipboard.writeText(text))
              }
              tooltip="Copy response"
            >
              {actionState.kind === "success" &&
              actionState.message === "Copied" ? (
                <Check className="size-4" />
              ) : (
                <Clipboard className="size-4" />
              )}
            </MessageAction>
            <MessageAction
              disabled={actionState.kind === "loading"}
              label="Good response"
              onClick={() =>
                void runAction("Feedback saved", () =>
                  onFeedback(message.id, "up")
                )
              }
              tooltip="Good response"
            >
              <ThumbsUp className="size-4" />
            </MessageAction>
            <MessageAction
              disabled={actionState.kind === "loading"}
              label="Needs correction"
              onClick={() =>
                void runAction("Feedback saved", () =>
                  onFeedback(message.id, "down")
                )
              }
              tooltip="Needs correction"
            >
              <ThumbsDown className="size-4" />
            </MessageAction>
            <MessageAction
              disabled={actionState.kind === "loading"}
              label="Regenerate response"
              onClick={() =>
                void runAction("Regenerating", () => onRegenerate(message.id))
              }
              tooltip="Regenerate response"
            >
              <RefreshCcw className="size-4" />
            </MessageAction>
          </MessageActions>
          {actionState.kind !== "idle" && (
            <p
              className={cn(
                "px-1 text-xs",
                actionState.kind === "error"
                  ? "text-red-600 dark:text-red-300"
                  : "text-muted-foreground"
              )}
            >
              {actionState.message}
            </p>
          )}
        </div>
      )}
    </Message>
    <Dialog onOpenChange={(open) => !open && setExternalSource(null)} open={Boolean(externalSource)}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ExternalLink className="size-4" />
            {t.externalLinkTitle}
          </DialogTitle>
          <DialogDescription>{t.externalLinkDescription}</DialogDescription>
        </DialogHeader>
        <div className="min-w-0 space-y-2 rounded-[4px] border border-border bg-muted/50 p-3">
          <p className="truncate font-medium text-sm">{externalSource?.title}</p>
          <p className="break-all font-mono text-muted-foreground text-xs">{externalSource?.url}</p>
        </div>
        <DialogFooter className="sm:justify-between">
          <Button onClick={copyExternalSourceUrl} type="button" variant="outline">
            <Clipboard className="size-4" />
            {t.copyLink}
          </Button>
          <Button onClick={openExternalSourceUrl} type="button">
            <ExternalLink className="size-4" />
            {t.openLink}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    </>
  );
}

function ChatInlineTableCard({ table }: { table: ChatInlineTable }) {
  if (table.kind !== "backtest_trades" || table.rows.length === 0) {
    return null;
  }
  return (
    <section className="overflow-hidden rounded-[6px] border border-border bg-[#101416] text-foreground">
      <div className="flex flex-wrap items-center justify-between gap-2 border-border border-b px-3 py-2">
        <div className="min-w-0">
          <p className="font-semibold text-sm">{table.title}</p>
        </div>
        <span className="rounded-full bg-muted px-2 py-1 font-medium text-[10px] uppercase tracking-wide">
          {table.row_count ?? table.rows.length} rows
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-[760px] w-full border-collapse text-left text-xs">
          <thead>
            <tr className="border-border border-b text-muted-foreground">
              {table.columns.map((column) => (
                <th
                  className={cn(
                    "whitespace-nowrap px-3 py-2 font-medium",
                    column.align === "right" && "text-right"
                  )}
                  key={column.key}
                  scope="col"
                >
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, rowIndex) => (
              <tr className="border-border/60 border-b last:border-b-0" key={rowIndex}>
                {table.columns.map((column) => (
                  <td
                    className={cn(
                      "whitespace-nowrap px-3 py-2 align-middle",
                      column.align === "right" && "text-right",
                      tableCellClass(column, row[column.key])
                    )}
                    key={column.key}
                  >
                    {formatInlineTableCell(column, row[column.key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {table.truncated ? (
        <div className="border-border border-t px-3 py-2 text-muted-foreground text-xs">
          Showing the first {table.rows.length} rows.
        </div>
      ) : null}
    </section>
  );
}

function tableCellClass(column: ChatInlineTable["columns"][number], value: unknown) {
  if (column.tone === "profit_loss") {
    const numeric = typeof value === "number" ? value : Number(value);
    if (Number.isFinite(numeric)) {
      return numeric < 0 ? "font-semibold text-[#ff4050]" : "font-semibold text-[#00bfa5]";
    }
  }
  if (column.tone === "side" && typeof value === "string") {
    const side = value.toLowerCase();
    if (side === "long") {
      return "font-medium text-[#00bfa5]";
    }
    if (side === "short") {
      return "font-medium text-[#ff4050]";
    }
  }
  return "";
}

function formatInlineTableCell(column: ChatInlineTable["columns"][number], value: unknown) {
  if (value === null || value === undefined || value === "") {
    return "N/A";
  }
  if (column.key === "bucket" && typeof value === "string") {
    return value.replaceAll("_", " ");
  }
  if (column.key === "pnl_cost") {
    return formatSignedNumber(value, { maximumFractionDigits: 2 });
  }
  if (column.key === "pnl_percentage") {
    const formatted = formatSignedNumber(value, { maximumFractionDigits: 2 });
    return formatted === "N/A" ? formatted : `${formatted}%`;
  }
  if (column.key === "opened_at" || column.key === "closed_at") {
    return formatCompactTimestamp(value);
  }
  if (typeof value === "number") {
    return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  return String(value);
}

function formatSignedNumber(value: unknown, options: Intl.NumberFormatOptions = {}) {
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) {
    return "N/A";
  }
  return numeric.toLocaleString(undefined, {
    maximumFractionDigits: options.maximumFractionDigits ?? 2,
    minimumFractionDigits: options.minimumFractionDigits,
    signDisplay: "always",
  });
}

function formatCompactTimestamp(value: unknown) {
  if (typeof value !== "string" || !value.trim()) {
    return "N/A";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
  }).format(parsed);
}

function botProposalConfirmStartPayloadFromWorkflowValues(
  values: Record<string, unknown>
): BotProposalConfirmStartRequest {
  return {
    account_id: workflowStringValue(values.account_id),
    broker_connection_id: workflowStringValue(values.broker_connection_id),
    risk_policy_id: workflowStringValue(values.risk_policy_id),
  };
}

function workflowStringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function chainOfThoughtStepCopy(text: string): { label: string; description?: string } {
  const normalized = text.trim();
  if (normalized.length <= 72) {
    return { label: normalized };
  }
  const firstLine = normalized.split(/\n+/)[0]?.trim() ?? normalized;
  const label = firstLine.length <= 72 ? firstLine : `${firstLine.slice(0, 69).trimEnd()}...`;
  return { label, description: normalized };
}

function StrategyBriefCard({
  language,
  profile,
}: {
  language: UiLanguagePreference;
  profile: StrategyProfile;
}) {
  const t = getUiCopy(language);
  const fields = [
    [t.platform, profile.brief.platform],
    [t.timeframe, profile.brief.timeframe],
    [t.strategyType, profile.brief.strategy_type],
  ].filter(([, value]) => Boolean(value));
  return (
    <div className="rounded-[4px] border border-border/70 bg-muted/20 p-3">
      <div className="mb-2 flex items-center gap-2">
        <Gauge className="size-4 text-muted-foreground" />
        <p className="font-medium text-sm">{t.strategyBrief}</p>
      </div>
      {profile.memory.summary ? (
        <p className="text-sm">{profile.memory.summary}</p>
      ) : (
        <p className="text-muted-foreground text-sm">{t.noStrategyContext}</p>
      )}
      {fields.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {fields.map(([label, value]) => (
            <span
              className="rounded-[3.25px] border border-border bg-background px-2 py-1 text-xs"
              key={label}
            >
              <span className="text-muted-foreground">{label}: </span>
              {value}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function MarketSnapshotCard({
  language,
  snapshot,
}: {
  language: UiLanguagePreference;
  snapshot: MarketSnapshot;
}) {
  const t = getUiCopy(language);
  const hasPrice = Boolean(snapshot.price);
  const hasSparkline = snapshot.price_points.length >= 3;
  const primarySource = snapshot.provider ?? snapshot.sources[0]?.title;
  const changePercent = snapshot.change_percent;
  const priceTone =
    typeof changePercent === "number"
      ? changePercent > 0
        ? "up"
        : changePercent < 0
          ? "down"
          : "neutral"
      : "neutral";
  const priceToneClass =
    priceTone === "up"
      ? "text-emerald-300"
      : priceTone === "down"
        ? "text-rose-300"
        : "text-[var(--together-accent-blue)]";
  const accentClass =
    priceTone === "up"
      ? "border-emerald-400/25 bg-emerald-400/10"
      : priceTone === "down"
        ? "border-rose-400/25 bg-rose-400/10"
        : "border-[var(--together-accent-blue)]/25 bg-[var(--together-accent-blue)]/10";
  return (
    <div className="mt-4 overflow-hidden rounded-[6px] border border-border/80 bg-background/70 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
      <div className="grid gap-3 p-3 sm:grid-cols-[1fr_auto] sm:items-start">
        <div className="min-w-0 space-y-2">
          <div className="flex min-w-0 items-center gap-2">
            <span className="flex size-7 shrink-0 items-center justify-center rounded-[4px] border border-[var(--together-accent-blue)]/25 bg-[var(--together-accent-blue)]/10">
              <TrendingUp className="size-3.5 text-[var(--together-accent-blue)]" />
            </span>
            <div className="min-w-0">
              <p className="truncate font-semibold text-sm leading-5">{snapshot.symbol}</p>
              {primarySource && (
                <p className="truncate text-[11px] text-muted-foreground leading-4">
                  {primarySource}
                </p>
              )}
            </div>
          </div>
          <div className="h-px w-full bg-border" />
        </div>

        <div
          className={cn(
            "min-w-[128px] rounded-[4px] border px-3 py-2 sm:text-right",
            hasPrice ? accentClass : "border-border/70 bg-muted/20"
          )}
        >
          <p className="text-muted-foreground text-[10px] font-medium">
            {t.currentPrice}
          </p>
          <p
            className={cn(
              "mt-1 font-mono text-lg leading-none",
              hasPrice ? priceToneClass : "text-muted-foreground"
            )}
          >
            {hasPrice ? snapshot.price : "--"}
          </p>
          {typeof changePercent === "number" && (
            <p className={cn("mt-1 font-mono text-[11px]", priceToneClass)}>
              {changePercent > 0 ? "+" : ""}
              {changePercent.toFixed(2)}%
            </p>
          )}
        </div>
      </div>

      {hasSparkline && <MiniPriceContext points={snapshot.price_points} />}
    </div>
  );
}

function MiniPriceContext({ points }: { points: Array<{ label: string; value: number }> }) {
  const values = points.map((point) => point.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const coordinates = points.map((point, index) => {
    const x = points.length === 1 ? 0 : (index / (points.length - 1)) * 100;
    const y = 34 - ((point.value - min) / range) * 28;
    return { x, y };
  });
  const linePath = coordinates
    .map((point, index) => {
      const command = index === 0 ? "M" : "L";
      return `${command} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`;
    })
    .join(" ");
  const areaPath = `${linePath} L 100 38 L 0 38 Z`;
  const startValue = points[0]!.value;
  const endValue = points.at(-1)!.value;
  const isUp = endValue >= startValue;
  const chartColor = isUp ? "rgb(110 231 183)" : "rgb(253 164 175)";
  return (
    <div className="border-border/70 border-t px-3 py-3">
      <svg
        aria-label="Recent price movement"
        className="h-16 w-full overflow-visible"
        preserveAspectRatio="none"
        role="img"
        viewBox="0 0 100 40"
      >
        <path d={areaPath} fill={chartColor} opacity="0.12" />
        <path
          d={linePath}
          fill="none"
          stroke={chartColor}
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="2.4"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      <div className="mt-2 flex items-center justify-between font-mono text-[10px] text-muted-foreground">
        <span>{formatCompactPrice(startValue)}</span>
        <span>{formatCompactPrice(endValue)}</span>
      </div>
    </div>
  );
}

function formatCompactPrice(value: number): string {
  return `$${Math.round(value).toLocaleString("en-US")}`;
}

function StrategySnapshotCard({
  language,
  profile,
}: {
  language: UiLanguagePreference;
  profile: StrategyProfile;
}) {
  const t = getUiCopy(language);
  const label = strategyCompletenessLabel(profile.snapshot.completeness, language);
  return (
    <div className="grid gap-2 rounded-[4px] border border-border/70 bg-muted/10 p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <ListChecks className="size-4 text-muted-foreground" />
          <p className="font-medium text-sm">{t.strategySnapshot}</p>
        </div>
        <span className="rounded-[3.25px] border border-border bg-background px-2 py-1 text-xs">
          {label}
        </span>
      </div>
      {profile.snapshot.missing_fields.length > 0 && (
        <div className="space-y-1">
          <p className="text-muted-foreground text-xs">{t.missingFields}</p>
          <div className="flex flex-wrap gap-1">
            {profile.snapshot.missing_fields.map((field) => (
              <span
                className="rounded-[3.25px] border border-border bg-background px-2 py-1 text-xs"
                key={field}
              >
                {fieldLabel(field, language)}
              </span>
            ))}
          </div>
        </div>
      )}
      {profile.snapshot.next_actions.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {profile.snapshot.next_actions.map((action) => (
            <span
              className="rounded-[3.25px] border border-border bg-background px-2 py-1 text-xs"
              key={action}
            >
              {nextActionLabel(action, language)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function AssumptionsLedger({
  language,
  profile,
}: {
  language: UiLanguagePreference;
  profile: StrategyProfile;
}) {
  const t = getUiCopy(language);
  return (
    <div className="space-y-3 rounded-[4px] border border-border p-3">
      <div className="flex items-center gap-2">
        <CircleHelp className="size-4 text-muted-foreground" />
        <p className="font-medium text-sm">{t.assumptions}</p>
      </div>
      <LedgerSection title={t.confirmedFacts} values={profile.assumptions.confirmed} />
      <LedgerSection title={t.openQuestions} values={profile.assumptions.open_questions} />
      <LedgerSection title={t.constraints} values={profile.assumptions.constraints} />
    </div>
  );
}

function LedgerSection({ title, values }: { title: string; values: string[] }) {
  if (values.length === 0) {
    return null;
  }
  return (
    <div className="space-y-1">
      <p className="text-muted-foreground text-xs">{title}</p>
      <ul className="space-y-1 text-sm">
        {values.map((value) => (
          <li className="flex gap-2" key={value}>
            <span className="mt-2 size-1 rounded-full bg-muted-foreground" />
            <span>{value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function mergeMessageSources(
  primarySources: ChatMessageSource[],
  fallbackSources: ChatMessageSource[]
): ChatMessageSource[] {
  const seen = new Set<string>();
  const merged: ChatMessageSource[] = [];
  for (const source of [...primarySources, ...fallbackSources]) {
    if (seen.has(source.id)) {
      continue;
    }
    seen.add(source.id);
    merged.push(source);
  }
  return merged;
}

function knowledgeSourcesFromRunEvents(events: RunEvent[]): ChatMessageSource[] {
  const seen = new Set<string>();
  const sources: ChatMessageSource[] = [];
  for (const event of events) {
    if (event.type === "web.sources") {
      const sourceItems = Array.isArray(event.payload?.sources) ? event.payload.sources : [];
      for (const item of sourceItems) {
        const source = chatMessageSourceFromUnknown(item);
        if (!source || seen.has(source.id)) {
          continue;
        }
        seen.add(source.id);
        sources.push(source);
      }
      continue;
    }
    if (event.type !== "tool.completed") {
      continue;
    }
    const payload = event.payload;
    if (!payload || payload.tool_id !== "knowledge_check") {
      continue;
    }
    const output = payload.output;
    if (!output || typeof output !== "object" || Array.isArray(output)) {
      continue;
    }
    const summary = (output as Record<string, unknown>).knowledge_context_summary;
    if (!summary || typeof summary !== "object" || Array.isArray(summary)) {
      continue;
    }
    const sourceItems = (summary as Record<string, unknown>).sources;
    if (!Array.isArray(sourceItems)) {
      continue;
    }
    for (const item of sourceItems) {
      const source = chatMessageSourceFromUnknown(item);
      if (!source || seen.has(source.id)) {
        continue;
      }
      seen.add(source.id);
      sources.push(source);
    }
  }
  return sources;
}

function allowedRunModesFromCapability(providerStatus?: ProviderStatusResponse): RunMode[] {
  const matrix = providerStatus?.capability_matrix;
  if (matrix && Object.keys(matrix).length > 0) {
    return (Object.entries(matrix)
      .filter(([, capability]) => capability.status === "available" || capability.status === "degraded")
      .map(([mode]) => mode) as RunMode[]);
  }
  return providerStatus?.allowed_run_modes ?? ["dry-run", "agent", "live-generation"];
}

function hasBlockingWorkflowTask(workflow: WorkflowState | null): boolean {
  return Boolean(
    workflow?.tasks.some((task) => {
      if (!task.blocking || task.status !== "pending_user" || task.input_requests.length === 0) {
        return false;
      }
      const values = { ...workflow.task_values, ...task.values };
      return task.input_requests.some((request) => !workflowTaskPromptValueAnswered(values[request.id]));
    })
  );
}

function workflowTaskPromptValueAnswered(value: unknown): boolean {
  if (typeof value === "string") {
    return value.trim().length > 0;
  }
  if (typeof value === "boolean") {
    return true;
  }
  if (Array.isArray(value)) {
    return value.length > 0;
  }
  return false;
}

function responseIntentFromRunEvents(events: RunEvent[]): ResponseIntent | null {
  for (const event of [...events].reverse()) {
    if (event.type !== "chat.response_intent") {
      continue;
    }
    const intent = event.payload?.intent;
    if (isChatResponseIntent(intent)) {
      return intent;
    }
  }
  return null;
}

function strategyWorkflowFromRunEvents(events: RunEvent[]): WorkflowState | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.type !== "chat.workflow.updated") {
      continue;
    }
    const workflow = normalizeWorkflowState(event.payload);
    if (workflow) {
      return workflow;
    }
  }
  return null;
}

function preferredArtifactKindFromRunEvents(events: RunEvent[]): string | null {
  for (const event of [...events].reverse()) {
    if (event.type !== "tool.completed" && event.type !== "tool.started") {
      continue;
    }
    const payload = event.payload;
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      continue;
    }
    const toolId = (payload as Record<string, unknown>).tool_id;
    if (toolId === "build_robustness_report") {
      return "robustness_report";
    }
    return null;
  }
  return null;
}

function marketSnapshotFromRunEvents(events: RunEvent[]): MarketSnapshot | null {
  for (const event of [...events].reverse()) {
    if (event.type !== "chat.market_snapshot") {
      continue;
    }
    const snapshot = marketSnapshotFromPayload(event.payload);
    if (snapshot) {
      return snapshot;
    }
  }
  return null;
}

function chatMessageSourceFromUnknown(value: unknown): ChatMessageSource | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const id = sourceText(record.id);
  const title = sourceText(record.title);
  if (!id || !title) {
    return null;
  }
  if (record.type === "external") {
    const url = sourceText(record.url);
    return url ? { id, title, type: "external", url } : null;
  }
  if (record.type === "internal") {
    return { id, title, type: "internal" };
  }
  return null;
}

function sourceText(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const text = value.trim();
  return text || undefined;
}

function ArtifactTranscriptCard({
  artifact,
  artifactCount,
  language,
  onOpen,
}: {
  artifact: Artifact;
  artifactCount: number;
  language: UiLanguagePreference;
  onOpen: () => void;
}) {
  const t = getUiCopy(language);
  const artifactSummary = getArtifactUserSummary(artifact, language);

  return (
    <button
      className="group relative mt-3 flex w-full cursor-pointer items-center gap-4 overflow-hidden rounded-[8px] border border-border bg-background px-4 py-3 text-left transition hover:border-foreground/30 hover:bg-muted/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      onClick={onOpen}
      type="button"
    >
      <span className="flex size-9 shrink-0 items-center justify-center rounded-[6px] border border-border bg-muted/40">
        {artifactSummary ? (
          <ArtifactKindIcon kind={artifactSummary.kind} />
        ) : (
          <PanelRight className="size-4 text-muted-foreground" />
        )}
      </span>
      <span className="min-w-0 flex-1 pr-16">
        <span className="block truncate font-medium text-sm">{artifact.display_name}</span>
        <span className="mt-0.5 block truncate text-muted-foreground text-xs">
          {artifactCount > 1
            ? `${artifactSummary?.label ?? t.artifactReady} · ${artifactCount} ${t.artifacts}`
            : artifactSummary?.label ?? t.artifactReady}
        </span>
      </span>
      <FileText
        className="-right-2 -top-3 absolute hidden size-20 rotate-[-8deg] text-muted-foreground/70 transition group-hover:rotate-[-4deg] group-hover:text-muted-foreground sm:block"
        strokeWidth={1.35}
      />
    </button>
  );
}

function AssistantActivity({
  activities,
  isWorking,
  language,
  onSelectArtifact,
}: {
  activities: ChatActivity[];
  isWorking: boolean;
  language: UiLanguagePreference;
  onSelectArtifact?: (artifactId: string) => void;
}) {
  const [activityOpen, setActivityOpen] = useState(false);
  const elapsedSeconds = useElapsedSeconds(isWorking);
  if (!isWorking && activities.length === 0) {
    return null;
  }

  return (
    <div className="space-y-3">
      {isWorking && activities.length === 0 && (
        <FirstTokenLoader
          slowState={slowProviderState(elapsedSeconds, language)}
        />
      )}
      {activities.length > 0 && (
        <div className="border-border/70 border-t">
          <button
            className="flex w-full items-center justify-between gap-3 py-2 text-left text-sm"
            onClick={() => setActivityOpen((open) => !open)}
            type="button"
          >
            <span className="flex min-w-0 items-center gap-2">
              <Bot className="size-4 shrink-0 text-muted-foreground" />
              <span className="truncate">{compactActivityTitle(activities, language)}</span>
              <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                {activities.length}
              </span>
            </span>
            <ChevronDown
              className={cn(
                "size-4 shrink-0 text-muted-foreground transition-transform",
                activityOpen && "rotate-180"
              )}
            />
          </button>
          {activityOpen && (
            <div className="border-border/60 border-t">
              {activities.map((activity) => (
                <Tool
                  className="!mb-0 !rounded-none !border-0 !border-border/50 !border-b !bg-transparent last:!border-b-0"
                  defaultOpen={false}
                  key={activity.id}
                >
                  <ToolHeader
                    className="px-0 py-2"
                    state={activity.state}
                    title={activity.title}
                    toolName={activity.toolName}
                    type="dynamic-tool"
                  />
                  <ToolContent className="max-h-48 space-y-2 overflow-y-auto border-border/40 border-t px-6 py-2">
                    <p className="text-muted-foreground text-sm">
                      {activity.description}
                    </p>
                    {activity.details && activity.details.length > 0 ? (
                      <dl className="grid gap-1 rounded-[4px] border border-border/60 bg-muted/30 p-2 text-xs">
                        {activity.details.map((detail) => (
                          <div
                            className="grid grid-cols-[5rem_minmax(0,1fr)] gap-2"
                            key={`${activity.id}-${detail.label}`}
                          >
                            <dt className="text-muted-foreground">{detail.label}</dt>
                            <dd className="truncate text-foreground">{detail.value}</dd>
                          </div>
                        ))}
                      </dl>
                    ) : null}
                    <ToolOutput
                      errorText={activity.errorText}
                      output={activity.output}
                    />
                    {activity.artifactLinks && activity.artifactLinks.length > 0 ? (
                      <div className="flex flex-wrap gap-2">
                        {activity.artifactLinks.map((link) => (
                          <Button
                            className="h-7 rounded-[4px] px-2 text-[11px] font-medium"
                            key={`${activity.id}-${link.artifactId}`}
                            onClick={() => onSelectArtifact?.(link.artifactId)}
                            size="sm"
                            type="button"
                            variant="outline"
                          >
                            {link.label}
                          </Button>
                        ))}
                      </div>
                    ) : null}
                  </ToolContent>
                </Tool>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function FirstTokenLoader({
  slowState,
}: {
  slowState: ChatLoadingState;
}) {
  return (
    <div className="rounded-[4px] border border-border bg-background px-3 py-2">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2 text-muted-foreground text-sm">
          <Loader2 className="size-4 shrink-0 animate-spin" />
          <span className="truncate">{slowState.title}</span>
        </div>
      </div>
      {slowState.description && (
        <p className="mt-1 text-muted-foreground text-xs">
          {slowState.description}
        </p>
      )}
    </div>
  );
}

function MobileConversationBar({
  conversations,
  disabled,
  language,
  onCreate,
  onSelect,
  selectedConversationId,
}: {
  conversations: ConversationSidebarItem[];
  disabled: boolean;
  language: UiLanguagePreference;
  onCreate: () => void;
  onSelect: (conversationId: string) => void;
  selectedConversationId: string | null;
}) {
  const t = getUiCopy(language);
  return (
    <div className="flex items-center gap-2 border-b border-sidebar-border bg-sidebar px-3 py-2 text-sidebar-foreground md:hidden">
      <select
        className="min-w-0 flex-1 rounded-[4px] border border-sidebar-border bg-sidebar-accent px-2 py-2 text-sm text-sidebar-accent-foreground outline-none"
        onChange={(event) => onSelect(event.currentTarget.value)}
        value={selectedConversationId ?? ""}
      >
        <option value="" disabled>
          {t.selectConversation}
        </option>
        {conversations.map((item) => (
          <option key={item.conversation.id} value={item.conversation.id}>
            {item.conversation.title ?? t.unknownStrategy}
          </option>
        ))}
      </select>
      <Button disabled={disabled} onClick={onCreate} size="sm" type="button">
        <MessageSquarePlus className="size-4" />
        {t.newBadge}
      </Button>
    </div>
  );
}

type PendingBacktestApproval = {
  approvalId: string;
  boundary: string | null;
  symbol: string | null;
  timeframe: string | null;
};

function WorkspaceRightRail({
  backtestStatus,
  isWorking,
  workflow,
}: {
  backtestStatus: BacktestLiveStatus | null;
  isWorking: boolean;
  workflow: WorkflowState | null;
}) {
  const visibleBacktestStatus =
    backtestStatus?.status === "completed" ? null : backtestStatus;
  if (!workflow && !visibleBacktestStatus) {
    return null;
  }
  return (
    <aside className="pointer-events-auto hidden min-[1440px]:absolute min-[1440px]:left-[calc(50%+24rem+1rem)] min-[1440px]:top-8 min-[1440px]:z-10 min-[1440px]:block min-[1440px]:max-h-[calc(100vh-4rem)] min-[1440px]:w-72 min-[1440px]:overflow-y-auto min-[1536px]:left-[calc(50%+24rem+1.5rem)] min-[1536px]:w-80">
      <div className="space-y-3">
        {workflow ? (
          <WorkflowPanel
            isWorking={isWorking}
            showTaskControls={false}
            workflow={workflow}
          />
        ) : null}
        <BacktestPreviewRail status={visibleBacktestStatus} />
      </div>
    </aside>
  );
}

function BacktestApprovalPrompt({
  disabled,
  onDecision,
}: {
  disabled: boolean;
  onDecision: (decision: "approved" | "rejected") => Promise<void>;
}) {
  const [selection, setSelection] = useState<"approved" | "rejected">("approved");
  const options: Array<{ decision: "approved" | "rejected"; label: string }> = [
    { decision: "approved", label: "Approve & run" },
    { decision: "rejected", label: "Skip preview" },
  ];
  return (
    <section
      aria-label="Backtest approval prompt"
      className="apple-frosted rounded-[8px] border p-3 text-foreground shadow-sm"
    >
      <div className="mb-3 flex items-start justify-between gap-4">
        <h2 className="min-w-0 text-pretty font-semibold text-sm leading-snug">
          Run the review-only backtest preview?
        </h2>
        <div className="flex shrink-0 items-center gap-1.5 text-muted-foreground text-xs">
          <span>1 of 1</span>
        </div>
      </div>

      <div className="grid gap-2">
        {options.map((option, index) => {
          const selected = selection === option.decision;
          return (
            <button
              className={cn(
                "grid grid-cols-[2rem_minmax(0,1fr)] items-center gap-3 rounded-[8px] border px-2.5 py-2 text-left text-sm transition-colors",
                selected
                  ? "border-border/70 bg-muted/70 text-foreground"
                  : "border-transparent text-muted-foreground hover:bg-muted/35 hover:text-foreground"
              )}
              disabled={disabled}
              key={option.decision}
              onClick={() => setSelection(option.decision)}
              type="button"
            >
              <span
                className={cn(
                  "flex size-5 items-center justify-center rounded-full border text-[11px]",
                  selected
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border text-muted-foreground"
                )}
              >
                {index + 1}
              </span>
              <span className={cn("font-medium", selected ? "text-foreground" : "text-foreground/90")}>
                {option.label}
              </span>
            </button>
          );
        })}
      </div>

      <div className="mt-3 flex items-center justify-end">
        <button
          className="inline-flex items-center gap-1.5 rounded-full bg-primary px-3 py-1.5 font-medium text-primary-foreground text-xs disabled:cursor-not-allowed disabled:opacity-50"
          disabled={disabled}
          onClick={() => void onDecision(selection)}
          type="button"
        >
          Submit
          <CornerDownLeft className="size-4" />
        </button>
      </div>
    </section>
  );
}

function BacktestPreviewRail({ status }: { status: BacktestLiveStatus | null }) {
  if (!status) {
    return null;
  }
  const progress = Math.max(0, Math.min(100, status.progressPct));
  const windowProgress =
    status.fetchWindowsCompleted !== null &&
    status.fetchWindowsTotal !== null &&
    status.fetchWindowsTotal > 0
      ? `${status.fetchWindowsCompleted}/${status.fetchWindowsTotal} windows`
      : null;
  return (
    <section className="rounded-[8px] border border-border bg-background/95 p-3 text-sm shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-2 font-semibold text-foreground">
            <Play className="size-3.5 text-muted-foreground" />
            Backtest Preview
          </p>
          <p className="mt-1 text-muted-foreground text-xs">{backtestLiveStageLabel(status)}</p>
          <p className="mt-1 text-muted-foreground text-xs">{status.message}</p>
        </div>
        <span
          className={cn(
            "shrink-0 rounded-[4px] border px-2 py-1 text-[10px] font-medium",
            status.status === "failed" && "border-red-500/40 bg-red-500/10 text-red-300",
            status.status !== "failed" && "border-border bg-secondary text-muted-foreground"
          )}
        >
          {status.status}
        </span>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-[var(--together-accent-blue)] transition-[width] duration-300"
          style={{ width: `${progress}%` }}
        />
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground text-xs">
        <span>{progress}%</span>
        {status.elapsedMs !== null && <span>Elapsed {formatDurationMs(status.elapsedMs)}</span>}
        {status.etaMs !== null && status.etaMs > 0 && <span>ETA {formatDurationMs(status.etaMs)}</span>}
        {windowProgress && <span>{windowProgress}</span>}
        {status.isStale && <span className="text-amber-300">Status heartbeat delayed</span>}
      </div>
    </section>
  );
}

function pendingBacktestApprovalFromRunEvents(events: RunEvent[]): PendingBacktestApproval | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (
      event.type === "backtest.preview.approved" ||
      event.type === "backtest.preview.rejected" ||
      event.type === "backtest.preview.queued" ||
      event.type === "backtest.preview.failed"
    ) {
      return null;
    }
    if (event.type !== "backtest.preview.approval_required") {
      continue;
    }
    const payload = runEventPayload(event);
    const approvalId = stringFromPayload(payload, "approval_id");
    if (!approvalId) {
      return null;
    }
    return {
      approvalId,
      boundary: stringFromPayload(payload, "boundary"),
      symbol: stringFromPayload(payload, "symbol"),
      timeframe: stringFromPayload(payload, "timeframe"),
    };
  }
  return null;
}

function backtestSummaryRunIdsByAnchorMessage({
  backendMessages,
  events,
}: {
  backendMessages: BackendMessage[];
  events: RunEvent[];
}) {
  const grouped = new Map<string, string>();
  for (const event of events) {
    if (event.type !== "tool.completed") {
      continue;
    }
    const payload = runEventPayload(event);
    if (payload.tool_id !== "get_backtest_summary") {
      continue;
    }
    const output = payload.output;
    const runId =
      output && typeof output === "object" && !Array.isArray(output)
        ? stringFromPayload(output as Record<string, unknown>, "run_id") ??
          stringFromPayload(
            ((output as Record<string, unknown>).summary as Record<string, unknown>) ?? {},
            "run_id"
          )
        : null;
    if (!runId) {
      continue;
    }
    const anchorId = runEventAssistantAnchorMessageId({ backendMessages, event });
    if (anchorId) {
      grouped.set(anchorId, runId);
    }
  }
  return grouped;
}

function backtestResultArtifactsByRunId(artifacts: Artifact[]) {
  const grouped = new Map<string, Artifact[]>();
  for (const artifact of artifacts) {
    if (
      artifact.presentation.viewer_kind !== "backtest_dashboard" &&
      artifact.presentation.viewer_kind !== "backtest_report"
    ) {
      continue;
    }
    if (!artifact.run_id) {
      continue;
    }
    grouped.set(artifact.run_id, [...(grouped.get(artifact.run_id) ?? []), artifact]);
  }
  return grouped;
}

function dedupeArtifactsById(artifacts: Artifact[]) {
  const seen = new Set<string>();
  return artifacts.filter((artifact) => {
    if (seen.has(artifact.id)) {
      return false;
    }
    seen.add(artifact.id);
    return true;
  });
}

function backtestApprovalDecisionLocalEvents({
  approvalId,
  childRunId,
  conversationId,
  decision,
  sourceRunId,
}: {
  approvalId: string;
  childRunId: string | null;
  conversationId: string;
  decision: "approved" | "rejected";
  sourceRunId: string;
}): RunEvent[] {
  const createdAt = new Date().toISOString();
  const status = decision === "approved" ? "approved" : "rejected";
  const decisionEvent: RunEvent = {
    conversation_id: conversationId,
    created_at: createdAt,
    event_id: `local-backtest-preview-${status}-${approvalId}`,
    payload: { approval_id: approvalId, status },
    request_id: null,
    run_id: sourceRunId,
    sequence: Number.MAX_SAFE_INTEGER - 2,
    trace_id: null,
    type: decision === "approved" ? "backtest.preview.approved" : "backtest.preview.rejected",
  };
  if (decision !== "approved" || !childRunId) {
    return [decisionEvent];
  }
  return [
    decisionEvent,
    {
      conversation_id: conversationId,
      created_at: createdAt,
      event_id: `local-backtest-preview-queued-${approvalId}`,
      payload: { approval_id: approvalId, child_run_id: childRunId, status: "queued" },
      request_id: null,
      run_id: sourceRunId,
      sequence: Number.MAX_SAFE_INTEGER - 1,
      trace_id: null,
      type: "backtest.preview.queued",
    },
    {
      conversation_id: conversationId,
      created_at: createdAt,
      event_id: `local-backtest-waiting-${childRunId}`,
      payload: {
        approval_id: approvalId,
        child_run_id: childRunId,
        message: "Waiting for the preview evidence to finish.",
      },
      request_id: null,
      run_id: sourceRunId,
      sequence: Number.MAX_SAFE_INTEGER,
      trace_id: null,
      type: "chat.auto_chain.waiting_for_backtest",
    },
  ];
}

function runEventPayload(event: RunEvent): Record<string, unknown> {
  return event.payload && typeof event.payload === "object" && !Array.isArray(event.payload)
    ? event.payload
    : {};
}

function stringFromPayload(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function formatDurationMs(value: number): string {
  const seconds = Math.max(0, Math.round(value / 1000));
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
}

function ArtifactDrawerPanel({
  artifacts,
  authKey,
  client,
  hasOlderArtifacts = false,
  isLoadingOlderArtifacts = false,
  language,
  onBacktestQueued,
  onClose,
  onLoadOlderArtifacts,
  open,
  preferredArtifactKind,
  runEvents,
}: {
  artifacts: Artifact[];
  authKey: string;
  client: BackendClient;
  hasOlderArtifacts?: boolean;
  isLoadingOlderArtifacts?: boolean;
  language: UiLanguagePreference;
  onBacktestQueued?: (payload: {
    childRunId: string;
    conversationId: string;
    sourceRunId: string;
  }) => void;
  onClose: () => void;
  onLoadOlderArtifacts?: () => void;
  open: boolean;
  preferredArtifactKind?: string | null;
  runEvents: RunEvent[];
}) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { selectedArtifactId, setSelectedArtifactId } = useStrategyUiStore();
  const t = getUiCopy(language);
  const appliedPreferredArtifactRef = useRef<string | null>(null);
  useEffect(() => {
    if (!preferredArtifactKind) {
      appliedPreferredArtifactRef.current = null;
      return;
    }
    const preferred = artifacts.find((artifact) => artifact.kind === preferredArtifactKind);
    const preferredKey = preferred ? `${preferredArtifactKind}:${preferred.id}` : null;
    if (!preferred || appliedPreferredArtifactRef.current === preferredKey) {
      return;
    }
    appliedPreferredArtifactRef.current = preferredKey;
    if (selectedArtifactId !== preferred.id) {
      setSelectedArtifactId(preferred.id);
    }
  }, [artifacts, preferredArtifactKind, selectedArtifactId, setSelectedArtifactId]);
  const activeArtifact =
    artifacts.find((artifact) => artifact.id === selectedArtifactId) ??
    getBestArtifactForDrawer(artifacts, { preferredKind: preferredArtifactKind });
  const preview = useQuery({
    enabled: open && Boolean(activeArtifact),
    queryFn: async () => {
      return getArtifactPreviewForViewer(client, activeArtifact!);
    },
    queryKey: [
      activeArtifact?.presentation.viewer_kind === "backtest_dashboard" ? "artifact-content" : "artifact-preview",
      authKey,
      activeArtifact?.id,
    ],
  });
  const approvalMutation = useMutation({
    mutationFn: ({
      approvalId,
      conversationId,
      decision,
    }: {
      approvalId: string;
      conversationId: string;
      decision: "approved" | "rejected";
    }) => client.decideBacktestApproval(conversationId, approvalId, { decision }),
    onError: (error) => {
      showToast({
        title: "Backtest approval failed",
        description: errorMessageFromUnknown(error),
        variant: "error",
      });
    },
    onSuccess: async (result) => {
      showToast({
        title:
          result.status === "queued"
            ? "Backtest preview queued."
            : "Backtest preview skipped.",
      });
      if (result.status === "queued" && result.run_id) {
        onBacktestQueued?.({
          childRunId: result.run_id,
          conversationId: result.conversation_id,
          sourceRunId: preview.data?.run_id ?? result.run_id,
        });
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversation-state", result.conversation_id] }),
        queryClient.invalidateQueries({ queryKey: ["conversation-sidebar"] }),
        preview.data
          ? queryClient.invalidateQueries({ queryKey: ["artifact-preview", authKey, preview.data.id] })
          : Promise.resolve(),
      ]);
    },
  });
  const content = preview.data ? artifactPreviewContent(preview.data) : "";

  const handleCopy = async () => {
    if (!content) {
      return;
    }
    try {
      await navigator.clipboard.writeText(content);
    } catch {
      // Keep the artifact chrome minimal; copy failures remain non-blocking.
    }
  };

  return (
    <aside
      className={cn(
        "apple-product-tile fixed inset-0 z-40 h-[100dvh] min-h-0 min-w-0 overflow-hidden border-l border-border shadow-2xl transition-[opacity,transform] duration-300 ease-out will-change-transform lg:relative lg:inset-auto lg:z-auto lg:h-full lg:shadow-none",
        open
          ? "translate-x-0 opacity-100"
          : "pointer-events-none translate-x-4 opacity-0 lg:translate-x-2"
      )}
    >
      <div className="pointer-events-none absolute inset-x-0 top-0 z-20 p-3">
        <div className="flex items-start justify-between gap-3">
          {artifacts.length > 1 || hasOlderArtifacts ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  className="apple-frosted pointer-events-auto shadow-sm hover:bg-muted"
                  size="icon-sm"
                  type="button"
                  variant="ghost"
                >
                  <ListChecks className="size-4" />
                  <span className="sr-only">{t.artifacts}</span>
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-72">
                {artifacts.map((artifact) => {
                  const summary = getArtifactUserSummary(artifact, language);
                  return (
                    <DropdownMenuItem
                      className="items-start gap-3"
                      key={artifact.id}
                      onClick={() => setSelectedArtifactId(artifact.id)}
                    >
                      <ArtifactKindIcon kind={summary.kind} />
                      <span className="min-w-0">
                        <span className="block truncate text-sm">{artifact.display_name}</span>
                        <span className="block text-muted-foreground text-xs">{summary.label}</span>
                      </span>
                    </DropdownMenuItem>
                  );
                })}
                {hasOlderArtifacts && (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      disabled={isLoadingOlderArtifacts}
                      onClick={(event) => {
                        event.preventDefault();
                        onLoadOlderArtifacts?.();
                      }}
                    >
                      {isLoadingOlderArtifacts ? "Loading older artifacts..." : "Load older artifacts"}
                    </DropdownMenuItem>
                  </>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            <span aria-hidden="true" className="size-8" />
          )}
          <div className="flex shrink-0 items-center gap-2">
            <Button
              className="apple-frosted pointer-events-auto shadow-sm hover:bg-muted"
              disabled={!content}
              onClick={() => void handleCopy()}
              size="sm"
              type="button"
              variant="outline"
            >
              {t.copy}
            </Button>
            <Button
              className="apple-frosted pointer-events-auto shadow-sm hover:bg-muted"
              onClick={onClose}
              size="icon-sm"
              type="button"
              variant="ghost"
            >
              <X className="size-4" />
              <span className="sr-only">{t.closeArtifactWorkspace}</span>
            </Button>
          </div>
        </div>
      </div>
      <div className="h-full min-h-0 overflow-y-auto overscroll-contain px-5 pt-14 pb-6 md:px-8 lg:px-10">
        {preview.isLoading && <PreviewLoading label={t.artifactPreviewLoading} />}
        {preview.error && (
          <div className="space-y-2">
            <ErrorBlock message={errorMessage(preview.error)} />
            <Button onClick={() => void preview.refetch()} size="sm" type="button" variant="outline">
              <RefreshCcw className="size-3" />
              {t.tryAgain}
            </Button>
          </div>
        )}
        {preview.data && (
          <ArtifactPreviewContent
            onBacktestApprovalDecision={({ approvalId, conversationId, decision }) =>
              approvalMutation.mutate({ approvalId, conversationId, decision })
            }
            preview={preview.data}
            approvalDecisionPending={approvalMutation.isPending}
            runEvents={runEvents}
          />
        )}
      </div>
    </aside>
  );
}

function PreviewLoading({ label }: { label: string }) {
  return (
    <div className="apple-utility-card p-3 text-sm">
      <Shimmer className="text-muted-foreground" duration={1.8}>
        {label}
      </Shimmer>
    </div>
  );
}

function ArtifactKindIcon({ kind }: { kind: ArtifactUserKind }) {
  const className = "size-4 shrink-0 text-muted-foreground";
  if (kind === "code") {
    return <Braces className={className} />;
  }
  if (kind === "validation") {
    return <Check className={className} />;
  }
  if (kind === "risk") {
    return <AlertTriangle className={className} />;
  }
  if (kind === "evidence") {
    return <FileCode2 className={className} />;
  }
  return <Clipboard className={className} />;
}

function readableKey(key: string) {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function CreateFromSpecDialog({
  allowedRunModes,
  createRun,
  disabled,
  isCreatingRun,
  language,
  onOpenChange,
  open,
  runMode,
  setRunMode,
  setSpecDraft,
  specDraft,
}: {
  allowedRunModes: RunMode[];
  createRun: () => void;
  disabled: boolean;
  isCreatingRun: boolean;
  language: UiLanguagePreference;
  onOpenChange: (open: boolean) => void;
  open: boolean;
  runMode: RunMode;
  setRunMode: (mode: RunMode) => void;
  setSpecDraft: (value: string) => void;
  specDraft: string;
}) {
  const t = getUiCopy(language);
  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t.createReviewArtifact}</DialogTitle>
          <DialogDescription>
            {t.createFromSpecDescription}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <label className="grid gap-2">
            <span className="font-medium text-sm">{t.strategySpecJson}</span>
            <Textarea
              className="min-h-72 resize-none rounded-[4px] font-mono text-xs"
              onChange={(event) => setSpecDraft(event.currentTarget.value)}
              value={specDraft}
            />
          </label>
          <details className="rounded-[4px] border border-border p-3">
            <summary className="cursor-pointer font-medium text-sm">{t.advanced}</summary>
            <div className="mt-3 flex rounded-[4px] border border-border bg-muted p-1">
              {(["dry-run", "agent", "live-generation"] as const).map((mode) => {
                const disabledMode = !allowedRunModes.includes(mode);
                return (
                  <button
                    className={cn(
                      "together-mono-label flex-1 rounded-[3.25px] px-2 py-1 text-[10px] transition",
                      runMode === mode ? "bg-background text-foreground" : "text-muted-foreground",
                      disabledMode && "cursor-not-allowed opacity-40"
                    )}
                    disabled={disabledMode}
                    key={mode}
                    onClick={() => setRunMode(mode)}
                    title={disabledMode ? t.apiModeUnavailable : undefined}
                    type="button"
                  >
                    {mode}
                  </button>
                );
              })}
            </div>
          </details>
        </div>
        <DialogFooter>
          <Button disabled={disabled || isCreatingRun} onClick={createRun} type="button">
            {isCreatingRun ? <RefreshCcw className="size-4 animate-spin" /> : <Play className="size-4" />}
            {t.createReviewArtifact}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function artifactTabLabel(tab: ArtifactWorkspaceTab, language: UiLanguagePreference) {
  const t = getUiCopy(language);
  const labels: Record<ArtifactWorkspaceTab, string> = {
    changes: t.changesTab,
    code: "Code",
    risk: t.riskReviewTab,
    strategy: t.strategyTab,
    validation: t.validationNotesTab,
  };
  return labels[tab];
}

function strategyCompletenessLabel(
  completeness: StrategyProfile["snapshot"]["completeness"],
  language: UiLanguagePreference
) {
  const t = getUiCopy(language);
  if (completeness === "ready_for_artifact") {
    return t.readyForArtifact;
  }
  if (completeness === "needs_risk") {
    return t.addRiskRules;
  }
  return t.draftStrategy;
}

function fieldLabel(field: string, language: UiLanguagePreference) {
  const t = getUiCopy(language);
  const labels: Record<string, string> = {
    entry_rules: t.entryRules,
    exit_rules: t.exitRules,
    market: "Market",
    platform: t.platform,
    risk_rules: t.riskNotes,
    timeframe: t.timeframe,
  };
  return labels[field] ?? field.replaceAll("_", " ");
}

function nextActionLabel(action: string, language: UiLanguagePreference) {
  const t = getUiCopy(language);
  const labels: Record<string, string> = {
    add_risk_rules: t.addRiskRules,
    generate_pine_artifact: t.generatePineArtifact,
    review_assumptions: t.reviewAssumptions,
    turn_into_strategy_spec: t.turnIntoSpec,
  };
  return labels[action] ?? action.replaceAll("_", " ");
}

function useElapsedSeconds(active: boolean) {
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  useEffect(() => {
    if (!active) {
      const resetTimeout = window.setTimeout(() => setElapsedSeconds(0), 0);
      return () => window.clearTimeout(resetTimeout);
    }
    let seconds = 0;
    const resetTimeout = window.setTimeout(() => setElapsedSeconds(0), 0);
    const interval = window.setInterval(() => {
      seconds += 1;
      setElapsedSeconds(seconds);
    }, 1000);
    return () => {
      window.clearTimeout(resetTimeout);
      window.clearInterval(interval);
    };
  }, [active]);
  return active ? elapsedSeconds : 0;
}

function slowProviderState(
  elapsedSeconds: number,
  language: UiLanguagePreference = "en"
): ChatLoadingState {
  const t = getUiCopy(language);
  if (elapsedSeconds >= 45) {
    return {
      description: t.slowProviderStillWaitingDescription,
      title: t.slowProviderStillWaitingTitle,
    };
  }
  if (elapsedSeconds >= 20) {
    return {
      description: t.slowProviderWaitingDescription,
      title: t.slowProviderWaitingTitle,
    };
  }
  if (elapsedSeconds >= 8) {
    return {
      description: t.slowProviderStartedDescription,
      title: t.slowProviderStartedTitle,
    };
  }
  return {
    description: t.slowProviderInitialDescription,
    title: t.slowProviderInitialTitle,
  };
}

function workflowAwareSlowProviderState(
  elapsedSeconds: number,
  language: UiLanguagePreference,
  workflow: WorkflowState | null
) {
  const fallback = slowProviderState(elapsedSeconds, language);
  const workflowState = workflowStepWorkingState(workflow, language);
  if (!workflowState) {
    return fallback;
  }
  return {
    description: elapsedSeconds >= 8 ? fallback.description : workflowState.description,
    title: workflowState.title,
  };
}

function workflowStepWorkingState(workflow: WorkflowState | null, language: UiLanguagePreference) {
  if (!workflow?.current_step) {
    return null;
  }
  const definition = getWorkflowDefinition(workflow.workflow_id);
  const currentStep = definition?.steps.find((step) => step.id === workflow.current_step);
  if (!currentStep) {
    return null;
  }
  return {
    description:
      language === "vi"
        ? "Model đang xử lý bước workflow hiện tại. Rail bên phải đang hiển thị cùng trạng thái."
        : "The model is working through the active workflow step. The rail shows the same state.",
    title:
      language === "vi"
        ? `Đang xử lý: ${currentStep.label}`
        : `Working on ${currentStep.label.toLowerCase()}...`,
  };
}

function ErrorBlock({ message }: { message: string }) {
  return (
    <div className="rounded-[4px] border border-red-500/30 bg-red-500/10 p-3 text-red-700 text-sm dark:text-red-300">
      {message}
    </div>
  );
}

async function consumeRunProgress(
  client: BackendClient,
  runId: string,
  setRunEvents: (updater: (events: RunEvent[]) => RunEvent[]) => void,
  signal: AbortSignal
) {
  await consumeRunEventStream({
    onEvents: (events) => setRunEvents((current) => mergeRunEvents(current, events, 30)),
    oversizedFrameMessage: "Run progress stream frame exceeded the maximum size.",
    signal,
    stream: () => client.streamRunProgress(runId, { signal }),
  });
}

async function consumeAutoChainRunEvents(
  client: BackendClient,
  runId: string,
  onEvents: (events: RunEvent[]) => void,
  signal: AbortSignal
) {
  let lastEventId: string | undefined;
  let terminal = false;
  while (!signal.aborted && !terminal) {
    let receivedEvents = false;
    await consumeRunEventStream({
      onEvents: (events) => {
        if (!events.length) {
          return;
        }
        receivedEvents = true;
        lastEventId = events.at(-1)?.event_id ?? lastEventId;
        terminal = events.some((event) =>
          ["run.completed", "run.failed", "run.cancelled"].includes(event.type)
        );
        onEvents(events);
      },
      oversizedFrameMessage: "Auto-chain run event stream frame exceeded the maximum size.",
      signal,
      stopOnTerminalRunEvent: true,
      stream: () => client.streamRunEvents(runId, { lastEventId, signal }),
    });
    if (!terminal && !signal.aborted) {
      await delay(receivedEvents ? 750 : 1500);
    }
  }
}

async function consumeRunEventStream({
  onEvents,
  oversizedFrameMessage,
  signal,
  stopOnTerminalRunEvent = false,
  stream,
}: {
  onEvents: (events: RunEvent[]) => void;
  oversizedFrameMessage: string;
  signal: AbortSignal;
  stopOnTerminalRunEvent?: boolean;
  stream: () => Promise<Response>;
}) {
  try {
    const response = await stream();
    if (!response.body) {
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let shouldCancelReader = false;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        if (buffer.length > MAX_PROGRESS_BUFFER_BYTES) {
          shouldCancelReader = true;
          throw new Error(oversizedFrameMessage);
        }
        const { frames, remaining } = splitCompleteSseFrames(buffer);
        buffer = remaining;
        for (const chunk of frames) {
          const events = parseBackendSseEvents(chunk);
          if (events.length > 0) {
            onEvents(events);
          }
          if (
            stopOnTerminalRunEvent &&
            events.some((event) =>
              ["run.completed", "run.failed", "run.cancelled"].includes(event.type)
            )
          ) {
            shouldCancelReader = true;
            return;
          }
        }
      }
      const finalEvents = parseBackendSseEvents(buffer);
      if (finalEvents.length > 0) {
        onEvents(finalEvents);
      }
    } finally {
      if (signal.aborted || shouldCancelReader) {
        await reader.cancel().catch(() => undefined);
      }
      reader.releaseLock();
    }
  } catch {
    if (signal.aborted) {
      return;
    }
    // Run-event streaming is supplemental; state queries remain authoritative.
  }
}

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function errorMessage(error: unknown) {
  if (error instanceof BackendClientError) {
    return error.message;
  }
  return errorMessageFromUnknown(error);
}

function isConversationNotFoundError(error: unknown) {
  return error instanceof BackendClientError && error.status === 404;
}

function parseStrategySpecDraft(value: string): StrategySpec {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error("Strategy spec must be valid JSON.");
  }

  const result = StrategySpecSchema.safeParse(parsed);
  if (!result.success) {
    const details = result.error.issues
      .slice(0, 4)
      .map((issue) => {
        const path = issue.path.join(".");
        return path ? `${path}: ${issue.message}` : issue.message;
      })
      .join("; ");
    throw new Error(`Strategy spec does not match the review schema. ${details}`);
  }
  return result.data;
}
