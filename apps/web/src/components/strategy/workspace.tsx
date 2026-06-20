"use client";

import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import { CodeBlock } from "@/components/ai-elements/code-block";
import {
  Artifact as AiArtifact,
  ArtifactAction,
  ArtifactActions,
  ArtifactContent,
  ArtifactDescription,
  ArtifactHeader,
  ArtifactTitle,
} from "@/components/ai-elements/artifact";
import {
  Message,
  MessageAction,
  MessageActions,
  MessageContent,
  MessageMarkdown,
  MessageResponse,
  type MessageResponseProps,
} from "@/components/ai-elements/message";
import {
  PromptInput,
  PromptInputBody,
  PromptInputFooter,
  PromptInputProvider,
  PromptInputTextarea,
  usePromptInputController,
} from "@/components/ai-elements/prompt-input";
import {
  Reasoning,
  ReasoningContent,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning";
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
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  strategyPromptInputShellClassName,
  StrategyStartPrompt,
  strategyPromptTextareaClassName,
} from "@/components/strategy/start-prompt";
import { StatusPill } from "@/components/strategy/status-pill";
import { BacktestReportCard } from "@/components/strategy/backtest-report-card";
import {
  BackendClient,
  BackendClientError,
  parseBackendSseEvents,
} from "@/lib/backend-client";
import { splitCompleteSseFrames } from "@/lib/sse";
import {
  ARTIFACT_WORKSPACE_TABS,
  currentProgressStep,
  getArtifactForGroupedTab,
  getArtifactUserSummary,
  getUserFacingArtifacts,
  groupArtifactsByKind,
  mapRunEventsToUserSteps,
  runStatusSummary,
  type ArtifactUserKind,
  type ArtifactWorkspaceTab,
} from "@/lib/artifact-workspace";
import {
  backendMessagesToUiMessages,
  compactActivityTitle,
  getChatSuggestions,
  getMessageMarketSnapshot,
  getMessageResponseIntent,
  getMessageSuggestions,
  getMessageSources,
  getMessageText,
  hasAssistantText,
  isRenderableMessage,
  shouldShowStrategyProfile,
  type ChatSuggestionItem,
  type ChatMessageSource,
  type MarketSnapshot,
  type ResponseIntent,
} from "@/lib/chat-ui";
import {
  accountInitial,
  accountName,
  accountSubtitle,
  formatUsageCost,
  formatUsageNumber,
  providerDisplay,
} from "@/lib/account-ui";
import {
  mapRunEventsToChatActivities,
  type ChatActivity,
} from "@/lib/chat-activity";
import {
  StrategySpecSchema,
  WebSearchModeSchema,
} from "@/lib/backend-schemas";
import { parseBacktestArtifactPreview } from "@/lib/backtest-report";
import {
  getUiCopy,
  languageLabel,
  languageLocale,
  type LanguagePreference as UiLanguagePreference,
} from "@/lib/i18n";
import { useI18n } from "@/lib/language";
import { useTheme, type ResolvedTheme, type ThemePreference } from "@/lib/theme";
import type {
  AccountUsageResponse,
  Artifact,
  ArtifactPreviewResponse,
  Conversation as ChatConversation,
  ConversationSidebarItem,
  MeResponse,
  ProviderStatusResponse,
  ReadyResponse,
  Run,
  RunEvent,
  RunMode,
  StrategyProfile,
  StrategySpec,
  WebSearchMode,
} from "@/lib/backend-schemas";
import { useStrategyUiStore } from "@/lib/ui-store";
import { cn } from "@/lib/utils";
import { errorMessageFromUnknown, runFailureMessage } from "@/lib/chat-stream";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useChat } from "@ai-sdk/react";
import {
  AlertTriangle,
  ArrowUp,
  BookOpen,
  Bot,
  Braces,
  Building2,
  Check,
  ChevronDown,
  ChevronsUpDown,
  CircleHelp,
  Clipboard,
  CreditCard,
  Download,
  ExternalLink,
  FileCode2,
  Gauge,
  Globe2,
  ListChecks,
  Loader2,
  LogOut,
  MoreHorizontal,
  MonitorCog,
  MessageSquarePlus,
  PanelLeft,
  PanelRight,
  Pencil,
  Play,
  RefreshCcw,
  Search,
  Settings,
  Square,
  ThumbsDown,
  ThumbsUp,
  TrendingUp,
  Trash2,
  X,
} from "lucide-react";
import { DefaultChatTransport, type UIMessage } from "ai";
import { useClerk, useUser } from "@clerk/nextjs";
import Image from "next/image";
import type { BundledLanguage } from "shiki";
import {
  type ComponentProps,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

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
const BROWSER_API_BASE_URL = "/api/backend";
const WEB_SEARCH_STORAGE_KEY = "strategy-codebot-web-search";
const WEB_SEARCH_MODE_NEXT: Record<WebSearchMode, WebSearchMode> = {
  auto: "on",
  off: "auto",
  on: "off",
};

type AccountDialog = "settings" | "language" | "appearance" | "help";
type SettingsTab = "general" | "provider" | "usage" | "workspace";

const artifactPreviewMarkdownComponents = {
  table: ({ className, ...props }: ComponentProps<"table">) => (
    <div className="w-full overflow-x-auto">
      <table
        className={cn("w-full border-collapse text-sm", className)}
        {...props}
      />
    </div>
  ),
  th: ({ className, ...props }: ComponentProps<"th">) => (
    <th
      className={cn(
        "bg-muted px-3 py-2 text-left font-semibold text-foreground",
        className
      )}
      {...props}
    />
  ),
  td: ({ className, ...props }: ComponentProps<"td">) => (
    <td
      className={cn("border-border border-t px-3 py-2 align-top", className)}
      {...props}
    />
  ),
};
type ClerkBrowserWindow = Window & {
  Clerk?: {
    session?: {
      getToken?: () => Promise<string | null>;
    };
  };
};

async function getBrowserClerkToken() {
  if (typeof window === "undefined") {
    return null;
  }
  const clerk = (window as ClerkBrowserWindow).Clerk;
  return (await clerk?.session?.getToken?.().catch(() => null)) ?? null;
}

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

export function StrategyWorkspace() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { language, setLanguage } = useI18n();
  const { resolvedTheme, setTheme, theme } = useTheme();
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [runMode, setRunMode] = useState<RunMode>("dry-run");
  const [specDraft, setSpecDraft] = useState(starterSpec);
  const [specDialogOpen, setSpecDialogOpen] = useState(false);
  const [renameTarget, setRenameTarget] = useState<ChatConversation | null>(null);
  const [renameTitle, setRenameTitle] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<ChatConversation | null>(null);
  const [runEvents, setRunEvents] = useState<RunEvent[]>([]);
  const [inlineError, setInlineError] = useState<string | null>(null);
  const [accountDialog, setAccountDialog] = useState<AccountDialog | null>(null);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("general");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [webSearchMode, setWebSearchMode] = useState<WebSearchMode>(() =>
    readStoredWebSearchMode()
  );
  const progressAbortRef = useRef<AbortController | null>(null);
  const stopChatRef = useRef<(() => void) | null>(null);
  const lastOpenedWorkspaceRef = useRef<string | null>(null);
  const lastHydratedMessagesKeyRef = useRef<string | null>(null);
  const sendingConversationIdRef = useRef<string | null>(null);
  const promptSubmitPendingRef = useRef(false);
  const [promptSubmitPending, setPromptSubmitPending] = useState(false);
  const [pendingPromptText, setPendingPromptText] = useState<string | null>(null);
  const {
    artifactPanelOpen,
    setArtifactPanelOpen,
  } = useStrategyUiStore();

  const authenticatedFetch = useCallback<typeof fetch>(
    async (input, init) => {
      const headers = new Headers(init?.headers);
      const token = await getBrowserClerkToken();
      if (token) {
        headers.set("Authorization", `Bearer ${token}`);
      }
      return fetch(input, {
        ...init,
        credentials: init?.credentials ?? "same-origin",
        headers,
      });
    },
    []
  );

  const client = useMemo(
    () =>
      new BackendClient({
        baseUrl: BROWSER_API_BASE_URL,
        fetcher: authenticatedFetch,
      }),
    [authenticatedFetch]
  );

  const stopRunProgress = useCallback(() => {
    progressAbortRef.current?.abort();
    progressAbortRef.current = null;
  }, []);

  const handleSelectConversation = useCallback(
    (conversationId: string) => {
      stopRunProgress();
      setRunEvents([]);
      setSelectedConversationId(conversationId);
    },
    [stopRunProgress]
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
  const accountUsage = useQuery({
    enabled: accountDialog === "settings",
    queryFn: () => client.getAccountUsage(),
    queryKey: ["account-usage"],
  });
  const allowedRunModes = providerStatus.data?.allowed_run_modes ?? ["dry-run", "agent", "live-generation"];
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
    queryFn: () => client.getConversationState(activeConversationId ?? ""),
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
        setRunEvents([]);
        setSelectedConversationId(nextConversation?.id ?? null);
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
      setArtifactPanelOpen(true);
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
        ? backendMessagesToUiMessages(state.data?.messages ?? [])
        : [],
    [state.data?.messages, stateBelongsToActiveConversation]
  );

  const chatTransport = useMemo(
    () =>
      new DefaultChatTransport({
        api: "/api/chat",
        credentials: "same-origin",
        fetch: authenticatedFetch,
        prepareSendMessagesRequest: ({ body, messages }) => ({
          body: {
            ...body,
            conversationId:
              typeof body?.conversationId === "string"
                ? body.conversationId
                : activeConversationId,
            language,
            messages,
            mode: "agent",
            webSearch: webSearchMode,
          },
        }),
      }),
    [activeConversationId, authenticatedFetch, language, webSearchMode]
  );

  const chat = useChat({
    id: activeConversationId ?? "strategy-codebot-empty",
    messages: chatMessages,
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
        setRunEvents((events) => [...events.slice(-24), parsed]);
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
    transport: chatTransport,
  });

  useEffect(() => {
    stopChatRef.current = () => {
      void chat.stop();
    };
    return () => {
      stopChatRef.current = null;
    };
  }, [chat]);

  useEffect(() => {
    if (!activeConversationId) {
      if (lastHydratedMessagesKeyRef.current === "empty") {
        return;
      }
      lastHydratedMessagesKeyRef.current = "empty";
      chat.setMessages([]);
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
    chat.setMessages(chatMessages);
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
    const timeout = window.setTimeout(() => {
      setRunEvents(state.data?.latest_run_events ?? []);
    }, 0);
    return () => window.clearTimeout(timeout);
  }, [chat.status, state.data?.latest_run?.id, state.data?.latest_run_events]);

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
    },
    []
  );

  const handleLanguageChange = useCallback((value: UiLanguagePreference) => {
    setLanguage(value);
  }, [setLanguage]);

  const ensureConversation = useCallback(async () => {
    if (activeConversationId) {
      return activeConversationId;
    }
    const conversation = await client.createConversation({ title: null });
    setInlineError(null);
    handleSelectConversation(conversation.id);
    await queryClient.invalidateQueries({ queryKey: ["conversation-sidebar"] });
    return conversation.id;
  }, [activeConversationId, client, handleSelectConversation, queryClient]);

  const handlePromptSubmit = useCallback(
    async ({ text }: { text: string }) => {
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
      setRunEvents([]);
      chat.clearError();
      const clientRequestId = crypto.randomUUID();
      let conversationId = activeConversationId;
      if (!conversationId) {
        try {
          conversationId = await ensureConversation();
        } catch (error) {
          promptSubmitPendingRef.current = false;
          setPromptSubmitPending(false);
          setPendingPromptText(null);
          setInlineError(errorMessage(error));
          return;
        }
      }
      console.info("[strategy-web-chat] submit", {
        clientRequestId,
        conversationId,
        mode: "agent",
        webSearch: webSearchMode,
      });
      sendingConversationIdRef.current = conversationId;
      try {
        await chat.sendMessage(
          { text: submittedText },
          {
            body: { clientRequestId, conversationId, webSearch: webSearchMode },
          }
        );
      } catch (error) {
        promptSubmitPendingRef.current = false;
        setPromptSubmitPending(false);
        setPendingPromptText(null);
        sendingConversationIdRef.current = null;
        setInlineError(errorMessage(error));
        await queryClient.invalidateQueries({ queryKey: ["conversation-state"] });
        chat.setMessages(chatMessages);
      }
    },
    [activeConversationId, chat, chatMessages, ensureConversation, queryClient, webSearchMode]
  );

  const latestRun = state.data?.latest_run ?? null;
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
  const visibleArtifacts = useMemo(
    () => getUserFacingArtifacts(state.data?.latest_run_artifacts ?? []),
    [state.data?.latest_run_artifacts]
  );
  const strategyProfile = state.data?.strategy_profile ?? null;
  const workspaceRunIntent = useMemo(() => responseIntentFromRunEvents(runEvents), [runEvents]);
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
  const activeSidebarItem = sidebarItems.find(
    (item) => item.conversation.id === activeConversationId
  );
  const activeConversation = activeSidebarItem?.conversation ?? null;
  const activeConversationTitle =
    activeConversation?.title ?? getUiCopy(language).newChat;
  const conversationStateError =
    activeConversationId && state.error ? errorMessage(state.error) : null;

  const requestCreateConversation = useCallback(() => {
    stopChatRef.current?.();
    stopRunProgress();
    setInlineError(null);
    setRunEvents([]);
    setSelectedConversationId(null);
    lastHydratedMessagesKeyRef.current = "empty";
    chat.setMessages([]);
    setArtifactPanelOpen(false);
  }, [chat, setArtifactPanelOpen, stopRunProgress]);
  const openRenameDialog = useCallback((conversation: ChatConversation) => {
    setRenameTarget(conversation);
    setRenameTitle(conversation.title ?? "New chat");
  }, []);
  const openDeleteDialog = useCallback((conversation: ChatConversation) => {
    setDeleteTarget(conversation);
  }, []);

  useEffect(() => {
    if (!workspaceSignal) {
      lastOpenedWorkspaceRef.current = null;
      setArtifactPanelOpen(false);
      return;
    }
    if (lastOpenedWorkspaceRef.current !== workspaceSignal) {
      lastOpenedWorkspaceRef.current = workspaceSignal;
      setArtifactPanelOpen(true);
    }
  }, [setArtifactPanelOpen, workspaceSignal]);

  return (
    <main className="flex h-[100dvh] overflow-hidden bg-background text-foreground">
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
      <section className="grid min-h-0 min-w-0 flex-1 grid-rows-[auto_1fr] overflow-hidden bg-background">
        <ReadinessStrip
          title={activeConversationTitle}
          conversation={activeConversation}
          onDelete={openDeleteDialog}
          onRename={openRenameDialog}
        />
        <div
          className={cn(
            "grid min-h-0 overflow-hidden grid-cols-1",
            showArtifactWorkspace && "lg:grid-cols-[minmax(0,1fr)_minmax(360px,420px)]"
          )}
        >
          <ChatColumn
            artifacts={visibleArtifacts}
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
            pendingUserText={pendingPromptText}
            language={language}
            disabled={chat.status !== "ready" || promptSubmitPending}
            error={inlineError ?? conversationStateError}
            messages={chat.messages}
            onCreateConversation={requestCreateConversation}
            onFeedback={handleAssistantFeedback}
            onPromptSubmit={handlePromptSubmit}
            onRegenerate={handleRegenerateMessage}
            onSelectConversation={handleSelectConversation}
            onViewArtifactWorkspace={() => setArtifactPanelOpen(true)}
            onStop={() => void chat.stop()}
            selectedConversationId={activeConversationId}
            showArtifactWorkspace={showArtifactWorkspace}
            runEvents={runEvents}
            strategyProfile={strategyProfile}
            webSearchMode={webSearchMode}
            onWebSearchModeChange={updateWebSearchMode}
          />
          {showArtifactWorkspace && (
            <ArtifactWorkspacePanel
              artifacts={visibleArtifacts}
              authKey={me.data?.capability.workspace_id ?? "workspace"}
              cancelRun={(runId) => cancelRun.mutate(runId)}
              client={client}
              events={runEvents}
              language={language}
              onClose={() => setArtifactPanelOpen(false)}
              retryRun={(runId) => retryRun.mutate(runId)}
              run={latestRun}
              strategyProfile={strategyProfile}
            />
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
  );
}

function ConversationSidebar({
  accountUsage,
  collapsed,
  conversations,
  isCreating,
  isNewChatDisabled,
  isLoading,
  language,
  me,
  onCreate,
  onDelete,
  onLanguageChange,
  onOpenAccountDialog,
  onOpenSettingsTab,
  onRename,
  onSelect,
  onToggleCollapsed,
  onThemeChange,
  providerStatus,
  selectedConversationId,
  theme,
}: {
  accountUsage?: AccountUsageResponse;
  collapsed: boolean;
  conversations: ConversationSidebarItem[];
  isCreating: boolean;
  isNewChatDisabled: boolean;
  isLoading: boolean;
  language: UiLanguagePreference;
  me?: MeResponse;
  onCreate: () => void;
  onDelete: (conversation: ChatConversation) => void;
  onLanguageChange: (language: UiLanguagePreference) => void;
  onOpenAccountDialog: (dialog: AccountDialog) => void;
  onOpenSettingsTab: (tab: SettingsTab) => void;
  onRename: (conversation: ChatConversation) => void;
  onSelect: (conversationId: string) => void;
  onToggleCollapsed: () => void;
  onThemeChange: (theme: ThemePreference) => void;
  providerStatus?: ProviderStatusResponse;
  selectedConversationId: string | null;
  theme: ThemePreference;
}) {
  const t = getUiCopy(language);
  return (
    <aside
      className={cn(
        "hidden shrink-0 overflow-hidden border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-[width] duration-300 ease-out md:grid",
        collapsed ? "w-14" : "w-[288px]"
      )}
    >
      <div className="relative min-h-0">
        <div
          aria-hidden={!collapsed}
          className={cn(
            "absolute inset-0 flex flex-col items-center px-2 py-3 transition-[opacity,transform] duration-200 ease-out",
            collapsed
              ? "pointer-events-auto translate-x-0 opacity-100 delay-100"
              : "pointer-events-none -translate-x-2 opacity-0"
          )}
        >
          <button
            aria-label={t.expandSidebar}
            className="group flex size-9 items-center justify-center rounded-[8px] transition hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
            onClick={onToggleCollapsed}
            title={t.expandSidebar}
            type="button"
          >
            <span className="block group-hover:hidden">
              <StrategyLogoMark compact />
            </span>
            <PanelRight className="hidden size-4 group-hover:block" />
          </button>
          <nav className="mt-5 flex flex-col items-center gap-3">
            <SidebarRailButton
              disabled={isCreating || isNewChatDisabled}
              icon={<MessageSquarePlus className="size-4" />}
              label={t.newChat}
              onClick={onCreate}
            />
            <SidebarRailButton
              icon={<Search className="size-4" />}
              label={t.searchChats}
            />
            <SidebarRailButton
              disabled={!selectedConversationId}
              icon={<Bot className="size-4" />}
              label={activeConversationLabel(conversations, selectedConversationId, t)}
              onClick={() => {
                if (selectedConversationId) {
                  onSelect(selectedConversationId);
                }
              }}
            />
          </nav>
        </div>
        <div
          aria-hidden={collapsed}
          className={cn(
            "absolute inset-0 flex w-[288px] flex-col p-3 transition-[opacity,transform] duration-200 ease-out",
            collapsed
              ? "pointer-events-none translate-x-2 opacity-0"
              : "pointer-events-auto translate-x-0 opacity-100 delay-100"
          )}
        >
          <div className="flex items-center justify-between px-2 py-2">
            <StrategyLogoMark />
            <Button
              className="text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              onClick={onToggleCollapsed}
              size="icon-sm"
              title={t.collapseSidebar}
              type="button"
              variant="ghost"
            >
              <PanelLeft className="size-4" />
            </Button>
          </div>
          <div className="mt-4 space-y-1">
            <button
              className="flex h-11 w-full items-center justify-start gap-2 rounded-[4px] bg-sidebar-primary px-3 text-sm font-medium text-sidebar-primary-foreground transition hover:bg-sidebar-primary/90 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={isCreating || isNewChatDisabled}
              onClick={onCreate}
              title={t.newConversation}
              type="button"
            >
              <MessageSquarePlus className="size-4" />
              {t.newChat}
            </button>
            <button
              className="flex h-10 w-full items-center justify-start gap-2 rounded-[4px] px-3 text-sm font-medium text-sidebar-foreground transition hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              title={t.searchChats}
              type="button"
            >
              <Search className="size-4" />
              {t.searchChats}
            </button>
          </div>
          <div className="mt-6 min-h-0 flex-1 overflow-y-auto">
            <p className="px-3 pb-2 text-[11px] font-medium text-sidebar-foreground/55">{t.recents}</p>
            {isLoading ? (
              <SidebarSkeleton />
            ) : conversations.length === 0 ? (
              <div className="rounded-[4px] border border-dashed border-sidebar-border p-3 text-sm text-sidebar-foreground/65">
                {t.noConversations}
              </div>
            ) : (
              <div className="space-y-0.5">
                {conversations.map((item) => (
                  <div
                    className={cn(
                      "group flex items-center rounded-[4px] text-sm text-sidebar-foreground/70 transition hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-within:bg-sidebar-accent",
                      selectedConversationId === item.conversation.id &&
                        "bg-sidebar-accent text-sidebar-accent-foreground"
                    )}
                    key={item.conversation.id}
                  >
                    <button
                      className="min-w-0 flex-1 px-3 py-2 text-left"
                      onClick={() => onSelect(item.conversation.id)}
                      type="button"
                    >
                      <span className="block truncate">
                        {item.conversation.title ??
                          item.last_message_preview ??
                          t.newChat}
                      </span>
                    </button>
                    <ConversationActionMenu
                      conversation={item.conversation}
                      onDelete={onDelete}
                      onRename={onRename}
                      triggerClassName={cn(
                        "mr-1 size-7 text-sidebar-foreground/55 opacity-0 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:opacity-100 group-hover:opacity-100",
                        selectedConversationId === item.conversation.id && "opacity-100"
                      )}
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
          <SidebarAccountMenu
            accountUsage={accountUsage}
            language={language}
            me={me}
            onLanguageChange={onLanguageChange}
            onOpenDialog={onOpenAccountDialog}
            onOpenSettingsTab={onOpenSettingsTab}
            onThemeChange={onThemeChange}
            providerStatus={providerStatus}
            theme={theme}
          />
        </div>
      </div>
    </aside>
  );
}

function SidebarAccountMenu(props: {
  accountUsage?: AccountUsageResponse;
  language: UiLanguagePreference;
  me?: MeResponse;
  onLanguageChange: (language: UiLanguagePreference) => void;
  onOpenDialog: (dialog: AccountDialog) => void;
  onOpenSettingsTab: (tab: SettingsTab) => void;
  onThemeChange: (theme: ThemePreference) => void;
  providerStatus?: ProviderStatusResponse;
  theme: ThemePreference;
}) {
  return <ClerkSidebarAccountMenu {...props} />;
}

function ClerkSidebarAccountMenu(props: {
  accountUsage?: AccountUsageResponse;
  language: UiLanguagePreference;
  me?: MeResponse;
  onLanguageChange: (language: UiLanguagePreference) => void;
  onOpenDialog: (dialog: AccountDialog) => void;
  onOpenSettingsTab: (tab: SettingsTab) => void;
  onThemeChange: (theme: ThemePreference) => void;
  providerStatus?: ProviderStatusResponse;
  theme: ThemePreference;
}) {
  const { signOut } = useClerk();
  const { isLoaded, user } = useUser();
  const email = user?.primaryEmailAddress?.emailAddress ?? user?.emailAddresses[0]?.emailAddress ?? null;
  const displayName = accountName(props.me, user?.fullName ?? user?.username ?? null, email, props.language);
  const t = getUiCopy(props.language);
  return (
    <SidebarAccountMenuView
      {...props}
      avatarUrl={user?.imageUrl}
      displayName={isLoaded ? displayName : t.loadingAccount}
      onSignOut={() => signOut({ redirectUrl: "/sign-in" })}
      subtitle={isLoaded ? accountSubtitle(props.me, email, props.language) : t.checking}
    />
  );
}

function SidebarAccountMenuView({
  accountUsage,
  avatarUrl,
  displayName,
  language,
  me,
  onLanguageChange,
  onOpenDialog,
  onOpenSettingsTab,
  onSignOut,
  onThemeChange,
  subtitle,
  theme,
}: {
  accountUsage?: AccountUsageResponse;
  avatarUrl?: string;
  displayName: string;
  language: UiLanguagePreference;
  me?: MeResponse;
  onLanguageChange: (language: UiLanguagePreference) => void;
  onOpenDialog: (dialog: AccountDialog) => void;
  onOpenSettingsTab: (tab: SettingsTab) => void;
  onSignOut?: () => Promise<unknown>;
  onThemeChange: (theme: ThemePreference) => void;
  subtitle: string;
  theme: ThemePreference;
}) {
  const isFree = me?.capability.tier === "free";
  const t = getUiCopy(language);
  const themeLabel = themePreferenceLabel(theme, language);
  const usageLabel = accountUsage
    ? `${formatUsageNumber(accountUsage.total_tokens)} tokens this period`
    : me?.capability.tier_label ?? t.workspace;

  return (
    <div className="relative mt-3 border-t border-sidebar-border pt-3">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            className="flex w-full items-center gap-3 rounded-[6px] px-2 py-2 text-left transition hover:bg-sidebar-accent data-[state=open]:bg-sidebar-accent"
            type="button"
          >
            <span className="flex size-9 shrink-0 items-center justify-center overflow-hidden rounded-full bg-sidebar-primary text-sm font-medium text-sidebar-primary-foreground">
              {avatarUrl ? (
                <Image
                  alt=""
                  className="size-full object-cover"
                  height={36}
                  src={avatarUrl}
                  width={36}
                />
              ) : (
                accountInitial(displayName)
              )}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium text-sidebar-foreground">{displayName}</span>
              <span className="block truncate text-xs text-sidebar-foreground/55">{subtitle}</span>
            </span>
            <ChevronsUpDown className="size-4 shrink-0 text-sidebar-foreground/45" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="start"
          className="z-[60] w-[268px] overflow-visible rounded-[6px] border-sidebar-border bg-sidebar p-2 text-sidebar-foreground shadow-xl ring-0"
          side="top"
          sideOffset={8}
        >
          <div className="px-2 py-1.5">
            <span className="block truncate text-sm font-medium text-sidebar-foreground">{displayName}</span>
            <span className="block truncate text-xs font-normal text-sidebar-foreground/55">{usageLabel}</span>
          </div>
          <SidebarMenuSeparator />
          <SidebarAccountMenuItem
            onSelect={() => {
              onOpenDialog("settings");
            }}
          >
            <Settings className="size-4" />
            {t.settings}
          </SidebarAccountMenuItem>
          <SidebarPreferenceFlyout
            icon={<Globe2 className="size-4" />}
            label={t.language}
            valueLabel={languageLabel(language)}
          >
            <SidebarPreferenceOption
              active={isActivePreference(language, "en")}
              label="English"
              onSelect={() => onLanguageChange("en")}
            />
            <SidebarPreferenceOption
              active={isActivePreference(language, "vi")}
              label="Tiếng Việt"
              onSelect={() => onLanguageChange("vi")}
            />
          </SidebarPreferenceFlyout>
          <SidebarPreferenceFlyout
            icon={<MonitorCog className="size-4" />}
            label={t.appearance}
            valueLabel={themeLabel}
          >
            <SidebarPreferenceOption
              active={isActivePreference(theme, "system")}
              label={themePreferenceLabel("system", language)}
              onSelect={() => onThemeChange("system")}
            />
            <SidebarPreferenceOption
              active={isActivePreference(theme, "light")}
              label={themePreferenceLabel("light", language)}
              onSelect={() => onThemeChange("light")}
            />
            <SidebarPreferenceOption
              active={isActivePreference(theme, "dark")}
              label={themePreferenceLabel("dark", language)}
              onSelect={() => onThemeChange("dark")}
            />
          </SidebarPreferenceFlyout>
          <SidebarAccountMenuItem
            onSelect={() => {
              onOpenDialog("help");
            }}
          >
            <CircleHelp className="size-4" />
            {t.getHelp}
          </SidebarAccountMenuItem>
          {isFree && (
            <>
              <SidebarMenuSeparator />
              <SidebarAccountMenuItem
                className="text-[var(--together-accent-periwinkle)]"
                onSelect={() => {
                  onOpenSettingsTab("usage");
                }}
              >
                <CreditCard className="size-4" />
                {t.upgradePlan}
              </SidebarAccountMenuItem>
            </>
          )}
          <SidebarMenuSeparator />
          <SidebarAccountMenuItem
            disabled={!onSignOut}
            onSelect={() => {
              if (!onSignOut) {
                return;
              }
              void onSignOut();
            }}
          >
            <LogOut className="size-4" />
            {t.logOut}
            {!onSignOut && <span className="ml-auto text-xs text-sidebar-foreground/40">{t.local}</span>}
          </SidebarAccountMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

function SidebarMenuSeparator() {
  return <div className="-mx-1 my-1 h-px bg-sidebar-border" />;
}

function SidebarAccountMenuItem({
  children,
  className,
  disabled,
  onSelect,
}: {
  children: ReactNode;
  className?: string;
  disabled?: boolean;
  onSelect: () => void;
}) {
  return (
    <DropdownMenuItem
      className={cn(
        "flex cursor-default items-center gap-2 rounded-md px-1.5 py-1 text-sm text-sidebar-foreground outline-none transition focus:bg-sidebar-accent focus:text-sidebar-accent-foreground data-disabled:pointer-events-none data-disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0",
        className
      )}
      disabled={disabled}
      onSelect={onSelect}
    >
      {children}
    </DropdownMenuItem>
  );
}

function SidebarPreferenceFlyout({
  children,
  icon,
  label,
  valueLabel,
}: {
  children: ReactNode;
  icon: ReactNode;
  label: string;
  valueLabel: string;
}) {
  return (
    <DropdownMenuSub>
      <DropdownMenuSubTrigger className="rounded-md px-1.5 py-1 text-sidebar-foreground focus:bg-sidebar-accent focus:text-sidebar-accent-foreground data-open:bg-sidebar-accent data-open:text-sidebar-accent-foreground">
        {icon}
        <span>{label}</span>
        <span className="ml-auto truncate text-xs text-sidebar-foreground/45">{valueLabel}</span>
      </DropdownMenuSubTrigger>
      <DropdownMenuSubContent
        className="z-[70] w-44 rounded-[6px] border-sidebar-border bg-sidebar p-1 text-sidebar-foreground shadow-xl ring-0"
        sideOffset={8}
      >
        {children}
      </DropdownMenuSubContent>
    </DropdownMenuSub>
  );
}

function SidebarPreferenceOption({
  active,
  label,
  onSelect,
}: {
  active: boolean;
  label: string;
  onSelect: () => void;
}) {
  return (
    <DropdownMenuItem
      className="flex cursor-default items-center gap-2 rounded-[4px] px-2 py-1.5 text-sm text-sidebar-foreground outline-none transition focus:bg-sidebar-accent focus:text-sidebar-accent-foreground"
      onSelect={onSelect}
    >
      <span className="flex size-4 items-center justify-center">
        {active && <Check className="size-3.5" />}
      </span>
      <span>{label}</span>
    </DropdownMenuItem>
  );
}

function isActivePreference<T extends string>(current: T, expected: T) {
  return current === expected;
}

function conversationHydrationKey(conversationId: string, messages: UIMessage[]) {
  return [
    conversationId,
    ...messages.map(
      (message) => `${message.id}:${message.role}:${getMessageText(message)}`
    ),
  ].join("|");
}

function ConversationActionMenu({
  conversation,
  onDelete,
  onRename,
  triggerClassName,
}: {
  conversation: ChatConversation;
  onDelete: (conversation: ChatConversation) => void;
  onRename: (conversation: ChatConversation) => void;
  triggerClassName?: string;
}) {
  const { t } = useI18n();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          aria-label={`${t.openActionsFor} ${conversation.title ?? t.newChat}`}
          className={cn("rounded-[4px]", triggerClassName)}
          onClick={(event) => event.stopPropagation()}
          size="icon-sm"
          type="button"
          variant="ghost"
        >
          <MoreHorizontal className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44">
        <DropdownMenuItem onSelect={() => onRename(conversation)}>
          <Pencil className="size-4" />
          {t.rename}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={() => onDelete(conversation)}
          variant="destructive"
        >
          <Trash2 className="size-4" />
          {t.delete}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function AccountDialogs({
  accountUsage,
  dialog,
  healthChecking,
  language,
  lastHealthCheckedAt,
  me,
  onCopyValue,
  onHealthCheck,
  onLanguageChange,
  onOpenChange,
  onRetryUsage,
  onSettingsTabChange,
  onThemeChange,
  providerStatus,
  readiness,
  resolvedTheme,
  settingsTab,
  theme,
  usageError,
  usageLoading,
}: {
  accountUsage?: AccountUsageResponse;
  dialog: AccountDialog | null;
  healthChecking: boolean;
  language: UiLanguagePreference;
  lastHealthCheckedAt: number;
  me?: MeResponse;
  onCopyValue: (value: string, successTitle: string) => void;
  onHealthCheck: () => void;
  onLanguageChange: (language: UiLanguagePreference) => void;
  onOpenChange: (open: boolean) => void;
  onRetryUsage: () => void;
  onSettingsTabChange: (tab: SettingsTab) => void;
  onThemeChange: (theme: ThemePreference) => void;
  providerStatus?: ProviderStatusResponse;
  readiness?: ReadyResponse;
  resolvedTheme: ResolvedTheme;
  settingsTab: SettingsTab;
  theme: ThemePreference;
  usageError: boolean;
  usageLoading: boolean;
}) {
  const provider = providerDisplay(providerStatus, me, language);
  const t = getUiCopy(language);
  const title = accountDialogTitle(dialog, language);
  const workspaceId = me?.workspace.id ?? "local-workspace";
  const userId = me?.user.id ?? t.localWorkspace;
  const providerUnavailable = providerStatus ? !providerStatus.available : false;
  const llmReadinessStatus = readiness?.checks.llm_provider?.status ?? readiness?.status;

  if (dialog === "settings") {
    const tabs: Array<{ icon: ReactNode; label: string; value: SettingsTab }> = [
      { icon: <Settings className="size-4" />, label: t.general, value: "general" },
      { icon: <Gauge className="size-4" />, label: t.aiProvider, value: "provider" },
      { icon: <CreditCard className="size-4" />, label: t.usageAndPlan, value: "usage" },
      { icon: <Building2 className="size-4" />, label: t.workspace, value: "workspace" },
    ];
    const activeTitle = tabs.find((tab) => tab.value === settingsTab)?.label ?? t.settings;

    return (
      <Dialog onOpenChange={onOpenChange} open>
        <DialogContent
          className="overflow-hidden p-0 sm:max-w-3xl"
          onFocusOutside={preventDialogOutsideInteraction}
          onInteractOutside={preventDialogOutsideInteraction}
          onPointerDownOutside={preventDialogOutsideInteraction}
        >
          <div className="grid min-h-[460px] grid-cols-1 md:grid-cols-[190px_1fr]">
            <nav className="border-border border-b bg-muted/30 p-3 md:border-r md:border-b-0">
              <div className="flex gap-1 md:grid">
                {tabs.map((tab) => (
                  <button
                    className={cn(
                      "flex min-w-0 items-center gap-2 rounded-[6px] px-3 py-2 text-left text-sm transition",
                      settingsTab === tab.value
                        ? "bg-background text-foreground shadow-sm"
                        : "text-muted-foreground hover:bg-background/70 hover:text-foreground"
                    )}
                    key={tab.value}
                    onClick={() => onSettingsTabChange(tab.value)}
                    type="button"
                  >
                    {tab.icon}
                    <span className="truncate">{tab.label}</span>
                  </button>
                ))}
              </div>
            </nav>
            <section className="min-w-0 p-5">
              <DialogHeader>
                <DialogTitle>{activeTitle}</DialogTitle>
              </DialogHeader>
              <div className="mt-5">
                {settingsTab === "general" && (
                  <div className="space-y-3">
                    <AccountInfoRow label={t.accountLabel} value={me?.user.id ?? t.localWorkspace} />
                    <AccountInfoRow label={t.plan} value={me?.capability.tier_label ?? t.workspace} />
                    <SettingsSelectRow
                      label={t.theme}
                      onValueChange={(value) => onThemeChange(value as ThemePreference)}
                      value={theme}
                    >
                      <SelectItem value="system">{themePreferenceLabel("system", language)}</SelectItem>
                      <SelectItem value="light">{themePreferenceLabel("light", language)}</SelectItem>
                      <SelectItem value="dark">{themePreferenceLabel("dark", language)}</SelectItem>
                    </SettingsSelectRow>
                    <SettingsSelectRow
                      label={t.language}
                      onValueChange={(value) => onLanguageChange(value as UiLanguagePreference)}
                      value={language}
                    >
                      <SelectItem value="en">English</SelectItem>
                      <SelectItem value="vi">Tiếng Việt</SelectItem>
                    </SettingsSelectRow>
                  </div>
                )}
                {settingsTab === "provider" && (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-muted-foreground text-sm">{t.providerStatus}</span>
                      <Button
                        disabled={healthChecking}
                        onClick={onHealthCheck}
                        size="sm"
                        type="button"
                        variant="outline"
                      >
                        <RefreshCcw className={cn("size-3.5", healthChecking && "animate-spin")} />
                        {t.runHealthCheck}
                      </Button>
                    </div>
                    {providerUnavailable && (
                      <div className="flex items-center justify-between gap-3 rounded-[6px] border border-destructive/30 bg-destructive/5 px-3 py-2">
                        <span className="truncate text-sm text-destructive">
                          {providerStatus?.reason ?? t.providerUnavailable}
                        </span>
                      </div>
                    )}
                    <AccountInfoRow label={t.providerStatus} value={provider.title} />
                    <AccountInfoRow label={t.readinessStatus} value={statusLabel(llmReadinessStatus, language)} />
                    <AccountInfoRow label={t.lastChecked} value={formatLastChecked(lastHealthCheckedAt, language)} />
                    <AccountInfoRow label={t.currentPlan} value={providerStatus?.tier_label ?? me?.capability.tier_label ?? t.workspace} />
                    <AccountInfoRow label={t.runModes} value={formatModeList(providerStatus?.allowed_run_modes)} />
                  </div>
                )}
                {settingsTab === "usage" && (
                  <UsageDialogContent
                    accountUsage={accountUsage}
                    loading={usageLoading}
                    onRetry={onRetryUsage}
                    showUpgrade={false}
                    language={language}
                    usageError={usageError}
                  />
                )}
                {settingsTab === "workspace" && (
                  <div className="space-y-3">
                    <CopyInfoRow
                      label={t.workspace}
                      onCopy={() => onCopyValue(workspaceId, t.copiedWorkspaceId)}
                      value={workspaceId}
                      copyLabel={t.copy}
                    />
                    <CopyInfoRow
                      label={t.userId}
                      onCopy={() => onCopyValue(userId, t.copiedUserId)}
                      value={userId}
                      copyLabel={t.copy}
                    />
                    <AccountInfoRow label={t.role} value={me?.workspace.role ?? "owner"} />
                    <AccountInfoRow label={t.plan} value={me?.capability.tier_label ?? t.workspace} />
                  </div>
                )}
              </div>
            </section>
          </div>
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <Dialog onOpenChange={onOpenChange} open={dialog !== null}>
      <DialogContent
        className="sm:max-w-lg"
        onFocusOutside={preventDialogOutsideInteraction}
        onInteractOutside={preventDialogOutsideInteraction}
        onPointerDownOutside={preventDialogOutsideInteraction}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        {dialog === "language" && (
          <div className="space-y-3">
            <Select value={language} onValueChange={(value) => onLanguageChange(value as UiLanguagePreference)}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="en">English</SelectItem>
                <SelectItem value="vi">Tiếng Việt</SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}
        {dialog === "appearance" && (
          <div className="space-y-3">
            <Select value={theme} onValueChange={(value) => onThemeChange(value as ThemePreference)}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="system">System</SelectItem>
                <SelectItem value="light">Light</SelectItem>
                <SelectItem value="dark">Dark</SelectItem>
              </SelectContent>
            </Select>
            <AccountInfoRow label={t.currentlyUsing} value={resolvedThemeLabel(resolvedTheme, language)} />
          </div>
        )}
        {dialog === "help" && (
          <div className="space-y-3">
            <AccountInfoRow label={t.docs} value={t.comingSoon} />
            <AccountInfoRow label={t.support} value={t.comingSoon} />
            <AccountInfoRow label={t.reportIssue} value={t.comingSoon} />
            <p className="text-muted-foreground text-sm">
              {t.reviewOnlyBoundary}
            </p>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function UsageDialogContent({
  accountUsage,
  language,
  loading,
  onRetry,
  showUpgrade,
  usageError,
}: {
  accountUsage?: AccountUsageResponse;
  language: UiLanguagePreference;
  loading: boolean;
  onRetry: () => void;
  showUpgrade: boolean;
  usageError: boolean;
}) {
  const t = getUiCopy(language);
  if (loading) {
    return <p className="text-muted-foreground text-sm">{t.loadingUsage}</p>;
  }
  if (usageError) {
    return (
      <div className="space-y-3">
        <p className="text-muted-foreground text-sm">{t.usageUnavailable}</p>
        <Button onClick={onRetry} size="sm" type="button" variant="outline">
          {t.tryAgain}
        </Button>
      </div>
    );
  }
  if (!accountUsage) {
    return <p className="text-muted-foreground text-sm">{t.noUsage}</p>;
  }
  return (
    <div className="space-y-4">
      <div className="rounded-[6px] border border-border p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="font-medium text-sm">{accountUsage.tier_label}</p>
            <p className="text-muted-foreground text-xs">
              {formatUsagePeriod(accountUsage)}
            </p>
          </div>
              {showUpgrade && (
            <Button size="sm" type="button" variant="outline">
              {t.upgradePlan}
            </Button>
          )}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <UsageStat label={t.messages} value={accountUsage.messages} />
        <UsageStat label={t.runs} value={accountUsage.runs} />
        <UsageStat label={t.artifacts} value={accountUsage.artifacts} />
        <UsageStat label={t.tokens} value={accountUsage.total_tokens} />
      </div>
      <AccountInfoRow label={t.estimatedCost} value={formatUsageCost(accountUsage, language)} />
    </div>
  );
}

function UsageStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-[6px] border border-border p-3">
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className="mt-1 font-medium text-sm">{formatUsageNumber(value)}</p>
    </div>
  );
}

function AccountInfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-[6px] border border-border px-3 py-2">
      <span className="text-muted-foreground text-sm">{label}</span>
      <span className="truncate text-sm">{value}</span>
    </div>
  );
}

function preventDialogOutsideInteraction(event: Event) {
  event.preventDefault();
}

function CopyInfoRow({
  copyLabel,
  label,
  onCopy,
  value,
}: {
  copyLabel: string;
  label: string;
  onCopy: () => void;
  value: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-[6px] border border-border px-3 py-2">
      <span className="text-muted-foreground text-sm">{label}</span>
      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate text-sm">{value}</span>
        <Button
          aria-label={`${copyLabel} ${label}`}
          className="shrink-0"
          onClick={onCopy}
          size="icon-xs"
          type="button"
          variant="ghost"
        >
          <Clipboard className="size-3.5" />
        </Button>
      </div>
    </div>
  );
}

function SettingsSelectRow({
  children,
  label,
  onValueChange,
  value,
}: {
  children: ReactNode;
  label: string;
  onValueChange: (value: string) => void;
  value: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-[6px] border border-border px-3 py-2">
      <span className="text-muted-foreground text-sm">{label}</span>
      <Select onValueChange={onValueChange} value={value}>
        <SelectTrigger className="h-8 w-40">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>{children}</SelectContent>
      </Select>
    </div>
  );
}

function formatModeList(modes?: string[]) {
  return modes?.length ? modes.join(", ") : "-";
}

function accountDialogTitle(dialog: AccountDialog | null, language: UiLanguagePreference = "en") {
  const t = getUiCopy(language);
  switch (dialog) {
    case "settings":
      return t.settings;
    case "language":
      return t.language;
    case "appearance":
      return t.appearance;
    case "help":
      return t.getHelp;
    default:
      return t.accountLabel;
  }
}

function statusLabel(status: string | undefined, language: UiLanguagePreference = "en") {
  const t = getUiCopy(language);
  if (!status) {
    return "-";
  }
  const normalized = status.toLowerCase();
  if (normalized === "ok" || normalized === "ready" || normalized === "pass") {
    return t.readyStatus;
  }
  if (normalized === "unavailable" || normalized === "failed" || normalized === "fail") {
    return t.needsSetupStatus;
  }
  return status;
}

function formatLastChecked(timestamp: number, language: UiLanguagePreference = "en") {
  if (!timestamp) {
    return "-";
  }
  return new Intl.DateTimeFormat(languageLocale(language), {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(timestamp));
}

function themePreferenceLabel(theme: ThemePreference, language: UiLanguagePreference = "en") {
  const t = getUiCopy(language);
  switch (theme) {
    case "dark":
      return t.themeDark;
    case "light":
      return t.themeLight;
    case "system":
      return t.themeSystem;
  }
}

function resolvedThemeLabel(theme: ResolvedTheme, language: UiLanguagePreference = "en") {
  const t = getUiCopy(language);
  return theme === "dark" ? t.dark : t.light;
}

function formatUsagePeriod(usage: AccountUsageResponse) {
  const formatter = new Intl.DateTimeFormat("en-US", {
    day: "numeric",
    month: "short",
  });
  return `${formatter.format(new Date(usage.period_start))} - ${formatter.format(new Date(usage.period_end))}`;
}

function RenameConversationDialog({
  disabled,
  language,
  onOpenChange,
  onRename,
  open,
  setTitle,
  title,
}: {
  disabled: boolean;
  language: UiLanguagePreference;
  onOpenChange: (open: boolean) => void;
  onRename: () => void;
  open: boolean;
  setTitle: (title: string) => void;
  title: string;
}) {
  const t = getUiCopy(language);
  const canSubmit = title.trim().length > 0 && !disabled;
  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{t.renameChat}</DialogTitle>
          <DialogDescription>
            {t.renameDescription}
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (canSubmit) {
              onRename();
            }
          }}
        >
          <Input
            autoFocus
            maxLength={160}
            onChange={(event) => setTitle(event.target.value)}
            value={title}
          />
          <DialogFooter>
            <Button
              disabled={disabled}
              onClick={() => onOpenChange(false)}
              type="button"
              variant="outline"
            >
              {t.cancel}
            </Button>
            <Button disabled={!canSubmit} type="submit">
              {t.rename}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteConversationDialog({
  conversationTitle,
  disabled,
  language,
  onDelete,
  onOpenChange,
  open,
}: {
  conversationTitle: string;
  disabled: boolean;
  language: UiLanguagePreference;
  onDelete: () => void;
  onOpenChange: (open: boolean) => void;
  open: boolean;
}) {
  const t = getUiCopy(language);
  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{t.deleteThisChat}</DialogTitle>
          <DialogDescription>
            {t.deleteChatDescription.replace("{title}", conversationTitle)}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            disabled={disabled}
            onClick={() => onOpenChange(false)}
            type="button"
            variant="outline"
          >
            {t.cancel}
          </Button>
          <Button
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            disabled={disabled}
            onClick={onDelete}
            type="button"
          >
            {t.delete}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SidebarRailButton({
  disabled,
  icon,
  label,
  onClick,
}: {
  disabled?: boolean;
  icon: ReactNode;
  label: string;
  onClick?: () => void;
}) {
  return (
    <button
      aria-label={label}
      className="flex size-9 items-center justify-center rounded-[8px] text-sidebar-foreground/85 transition hover:bg-sidebar-accent hover:text-sidebar-accent-foreground disabled:cursor-not-allowed disabled:opacity-40"
      disabled={disabled}
      onClick={onClick}
      title={label}
      type="button"
    >
      {icon}
    </button>
  );
}

function activeConversationLabel(
  conversations: ConversationSidebarItem[],
  selectedConversationId: string | null,
  t: ReturnType<typeof getUiCopy>
) {
  if (!selectedConversationId) {
    return t.selectConversation;
  }
  const item = conversations.find(
    (conversation) => conversation.conversation.id === selectedConversationId
  );
  return item?.conversation.title ?? item?.last_message_preview ?? t.selectConversation;
}

function StrategyLogoMark({ compact = false }: { compact?: boolean }) {
  return (
    <div
      aria-label="Strategy Codebot"
      className={cn(
        "flex items-center justify-center overflow-hidden bg-white",
        compact ? "size-5 rounded-[7px]" : "size-8 rounded-[12px]"
      )}
      title="Strategy Codebot"
    >
      <Image
        alt=""
        className="size-full object-cover"
        height={compact ? 20 : 32}
        src="/brand/strategy-codebot-icon-192.png"
        width={compact ? 20 : 32}
      />
    </div>
  );
}

function ReadinessStrip({
  conversation,
  onDelete,
  onRename,
  title,
}: {
  conversation: ChatConversation | null;
  onDelete: (conversation: ChatConversation) => void;
  onRename: (conversation: ChatConversation) => void;
  title: string;
}) {
  return (
    <header className="relative flex min-h-16 items-center justify-between gap-3 border-b border-sidebar-border bg-sidebar px-4 text-sidebar-foreground">
      <div className="together-gradient-ribbon absolute inset-x-0 top-0 h-1" />
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <Bot className="size-4 text-sidebar-foreground/64" />
          <p className="truncate font-medium text-sm tracking-[-0.01em]">{title}</p>
          {conversation ? (
            <ConversationActionMenu
              conversation={conversation}
              onDelete={onDelete}
              onRename={onRename}
              triggerClassName="size-7 text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
            />
          ) : null}
        </div>
      </div>
    </header>
  );
}

function ChatColumn({
  artifacts,
  chatStatus,
  conversations,
  disabled,
  error,
  hasArtifactWorkspace,
  isCreatingConversation,
  isStartingChat,
  isLoadingConversation,
  language,
  messages,
  onCreateConversation,
  onFeedback,
  onPromptSubmit,
  onWebSearchModeChange,
  pendingUserText,
  onRegenerate,
  onSelectConversation,
  onStop,
  onViewArtifactWorkspace,
  selectedConversationId,
  showArtifactWorkspace,
  runEvents,
  strategyProfile,
  webSearchMode,
}: {
  artifacts: Artifact[];
  chatStatus: string;
  conversations: ConversationSidebarItem[];
  disabled: boolean;
  error: string | null;
  hasArtifactWorkspace: boolean;
  isCreatingConversation: boolean;
  isStartingChat: boolean;
  isLoadingConversation: boolean;
  language: UiLanguagePreference;
  messages: UIMessage[];
  onCreateConversation: () => void;
  onFeedback: (messageId: string, rating: "up" | "down") => Promise<void>;
  onPromptSubmit: (message: { text: string }) => Promise<void>;
  onWebSearchModeChange: (mode: WebSearchMode) => void;
  pendingUserText: string | null;
  onRegenerate: (messageId: string) => Promise<void>;
  onSelectConversation: (conversationId: string) => void;
  onStop: () => void;
  onViewArtifactWorkspace: () => void;
  selectedConversationId: string | null;
  showArtifactWorkspace: boolean;
  runEvents: RunEvent[];
  strategyProfile: StrategyProfile | null;
  webSearchMode: WebSearchMode;
}) {
  const activities = useMemo(
    () => mapRunEventsToChatActivities(runEvents, language),
    [language, runEvents]
  );
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
        message.role === "user" && getMessageText(message).trim() === text
    );
    if (alreadyRendered) {
      return null;
    }
    return {
      id: "pending-user-message",
      parts: [{ text, type: "text" }],
      role: "user",
    } satisfies UIMessage;
  }, [pendingUserText, renderableMessages]);
  const displayMessages = useMemo(
    () =>
      pendingUserMessage
        ? [...renderableMessages, pendingUserMessage]
        : renderableMessages,
    [pendingUserMessage, renderableMessages]
  );
  const runSources = useMemo(() => knowledgeSourcesFromRunEvents(runEvents), [runEvents]);
  const latestRunIntent = useMemo(() => responseIntentFromRunEvents(runEvents), [runEvents]);
  const latestMarketSnapshot = useMemo(
    () => marketSnapshotFromRunEvents(runEvents),
    [runEvents]
  );
  const { latestAssistantMessage, latestUserMessageId } = useMemo(() => {
    let assistant: UIMessage | null = null;
    let userId: string | undefined;
    for (let index = displayMessages.length - 1; index >= 0; index -= 1) {
      const message = displayMessages[index];
      if (!assistant && message.role === "assistant") {
        assistant = message;
      }
      if (!userId && message.role === "user") {
        userId = message.id;
      }
      if (assistant && userId) {
        break;
      }
    }
    return { latestAssistantMessage: assistant, latestUserMessageId: userId };
  }, [displayMessages]);
  const latestAssistantMessageId = latestAssistantMessage?.id;
  const hasStreamingAssistantText = isChatWorking && hasAssistantText(messages);
  const readyArtifact = artifacts[0] ?? null;
  const staticSuggestions = useMemo(() => getChatSuggestions(language), [language]);
  const fallbackSuggestionPayload = useMemo(
    () =>
      buildFallbackSuggestionPayload({
        artifactAvailable: artifacts.length > 0,
        intent: latestRunIntent,
        language,
        strategyProfile,
      }),
    [artifacts.length, language, latestRunIntent, strategyProfile]
  );
  const streamedComposerBlocks = latestAssistantMessage
    ? getMessageSuggestions(latestAssistantMessage)?.composer_blocks
    : null;
  const composerBlocks =
    streamedComposerBlocks && streamedComposerBlocks.length > 0
      ? streamedComposerBlocks
      : fallbackSuggestionPayload.composer_blocks;
  const isEmptyChat = !isLoadingConversation && displayMessages.length === 0;

  return (
    <section
      className={cn(
        "together-technical-canvas grid h-full min-h-0 grid-rows-[auto_1fr_auto] overflow-hidden border-r border-border md:grid-rows-[1fr_auto]",
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
      <Conversation className="min-h-0 overflow-hidden">
        <ConversationContent className="mx-auto w-full max-w-3xl px-4 py-8">
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
            displayMessages.map((message) => (
              <StrategyMessage
                fallbackSources={message.id === latestAssistantMessageId ? runSources : []}
                key={message.id}
                language={language}
                message={message}
                onFeedback={onFeedback}
                onRegenerate={onRegenerate}
                onSuggestionSubmit={onPromptSubmit}
                onViewArtifactWorkspace={onViewArtifactWorkspace}
                fallbackSuggestions={
                  message.id === latestAssistantMessageId ? fallbackSuggestionPayload.actions : []
                }
                fallbackMarketSnapshot={
                  message.id === latestAssistantMessageId ? latestMarketSnapshot : null
                }
                fallbackResponseIntent={
                  message.id === latestAssistantMessageId ? latestRunIntent : null
                }
                showStrategyProfile={
                  latestAssistantMessageId
                    ? message.id === latestAssistantMessageId
                    : message.id === latestUserMessageId
                }
                strategyProfile={strategyProfile}
              />
            ))
          )}
          <AssistantActivity
            activities={activities}
            artifact={readyArtifact}
            isWorking={(isChatWorking || isStartingChat) && !hasStreamingAssistantText}
            language={language}
            onViewArtifactWorkspace={onViewArtifactWorkspace}
            showArtifactWorkspace={showArtifactWorkspace}
          />
        </ConversationContent>
        <ConversationScrollButton />
      </Conversation>
      <div
        className={cn(
          "mx-auto w-full max-w-3xl shrink-0 px-4 pb-[max(1rem,env(safe-area-inset-bottom))]",
          isEmptyChat && "hidden"
        )}
      >
        {error && (
          <div className="mb-3 flex items-start gap-2 rounded-[4px] border border-red-500/30 bg-red-500/10 p-3 text-red-700 text-sm dark:text-red-300">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}
        <div className={cn(isEmptyChat && "pointer-events-auto")}>
          <ChatPromptComposer
            chatStatus={chatStatus}
            disabled={disabled}
            hasArtifactWorkspace={hasArtifactWorkspace}
            language={language}
            onPromptSubmit={onPromptSubmit}
            onWebSearchModeChange={onWebSearchModeChange}
            onStop={onStop}
          onViewArtifactWorkspace={onViewArtifactWorkspace}
          showArtifactWorkspace={showArtifactWorkspace}
          suggestionBlocks={composerBlocks}
          webSearchMode={webSearchMode}
        />
        </div>
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
  onPromptSubmit: (message: { text: string }) => Promise<void>;
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
  hasArtifactWorkspace,
  language,
  onPromptSubmit,
  onWebSearchModeChange,
  onStop,
  onViewArtifactWorkspace,
  showArtifactWorkspace,
  suggestionBlocks,
  webSearchMode,
}: {
  chatStatus: string;
  disabled: boolean;
  hasArtifactWorkspace: boolean;
  language: UiLanguagePreference;
  onPromptSubmit: (message: { text: string }) => Promise<void>;
  onWebSearchModeChange: (mode: WebSearchMode) => void;
  onStop: () => void;
  onViewArtifactWorkspace: () => void;
  showArtifactWorkspace: boolean;
  suggestionBlocks: ChatSuggestionItem[];
  webSearchMode: WebSearchMode;
}) {
  const t = getUiCopy(language);
  return (
    <PromptInputProvider>
      <SmartStrategyBlocks
        disabled={disabled}
        language={language}
        blocks={suggestionBlocks}
      />
      <PromptInput
        className="w-full"
        inputGroupClassName={cn("border", strategyPromptInputShellClassName, "pb-2")}
        onSubmit={onPromptSubmit}
      >
        <PromptInputBody>
          <PromptInputTextarea
            className={strategyPromptTextareaClassName}
            disabled={disabled}
            placeholder={t.signedOutPlaceholder}
          />
        </PromptInputBody>
        <PromptInputFooter
          align="block-end"
          className="px-3 pb-3"
        >
          <WebSearchToggle
            disabled={disabled}
            language={language}
            mode={webSearchMode}
            onChange={onWebSearchModeChange}
          />
          <div className="flex items-center gap-2">
            {hasArtifactWorkspace && !showArtifactWorkspace && (
              <Button
                className="rounded-full"
                onClick={onViewArtifactWorkspace}
                size="icon-sm"
                type="button"
                variant="outline"
                title={t.viewArtifact}
              >
                <PanelRight className="size-3" />
                <span className="sr-only">{t.viewArtifact}</span>
              </Button>
            )}
            {chatStatus === "streaming" || chatStatus === "submitted" ? (
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
                disabled={disabled}
                size="icon-sm"
                title={t.send}
                type="submit"
              >
                <ArrowUp className="size-4" />
                <span className="sr-only">{t.send}</span>
              </Button>
            )}
          </div>
        </PromptInputFooter>
      </PromptInput>
    </PromptInputProvider>
  );
}

function SmartStrategyBlocks({
  blocks,
  disabled,
  language,
}: {
  blocks: ChatSuggestionItem[];
  disabled: boolean;
  language: UiLanguagePreference;
}) {
  const { textInput } = usePromptInputController();
  const t = getUiCopy(language);
  const [feedback, setFeedback] = useState<{ label: string; previous: string } | null>(null);
  const chips = blocks;

  if (chips.length === 0) {
    return null;
  }

  const applyBlock = (block: ChatSuggestionItem, template: string, label: string) => {
    const previous = textInput.value;
    textInput.setInput(insertOrUpdateStrategyBlock(previous, block.slot, template));
    setFeedback({ label, previous });
  };

  return (
    <div className="mb-2 flex flex-wrap items-center gap-1.5 text-xs">
      <span className="mr-1 text-muted-foreground">{t.signalGrammarHint}</span>
      {chips.map((block) => (
        <DropdownMenu key={block.id}>
          <DropdownMenuTrigger asChild>
            <Button
              className={cn(
                "h-7 rounded-[4px] px-2 text-xs normal-case",
                block.emphasized && "border-[var(--together-accent-blue)] text-[var(--together-accent-blue)]"
              )}
              disabled={disabled || block.enabled === false}
              type="button"
              variant="outline"
            >
              {block.label}
              <ChevronDown className="ml-1 size-3" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-56 border-border bg-popover">
            {(block.variants ?? []).map((variant) => (
              <DropdownMenuItem
                className="rounded-[4px] text-xs"
                key={variant.id}
                onSelect={() => applyBlock(block, variant.insert_template, variant.label)}
              >
                {variant.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      ))}
      {feedback && (
        <span className="ml-1 flex items-center gap-1 text-muted-foreground">
          {localizedSuggestionCopy(language, "Đã thêm", "Inserted")} {feedback.label}
          <button
            className="text-[var(--together-accent-blue)] hover:underline"
            onClick={() => {
              textInput.setInput(feedback.previous);
              setFeedback(null);
            }}
            type="button"
          >
            Undo
          </button>
        </span>
      )}
    </div>
  );
}

function StrategyMessage({
  fallbackMarketSnapshot = null,
  fallbackResponseIntent = null,
  fallbackSources = [],
  language,
  message,
  onFeedback,
  onRegenerate,
  onSuggestionSubmit,
  onViewArtifactWorkspace,
  fallbackSuggestions = [],
  showStrategyProfile,
  strategyProfile,
}: {
  fallbackMarketSnapshot?: MarketSnapshot | null;
  fallbackResponseIntent?: ResponseIntent | null;
  fallbackSources?: ChatMessageSource[];
  language: UiLanguagePreference;
  message: UIMessage;
  onFeedback: (messageId: string, rating: "up" | "down") => Promise<void>;
  onRegenerate: (messageId: string) => Promise<void>;
  onSuggestionSubmit: (message: { text: string }) => Promise<void>;
  onViewArtifactWorkspace: () => void;
  fallbackSuggestions?: ChatSuggestionItem[];
  showStrategyProfile: boolean;
  strategyProfile: StrategyProfile | null;
}) {
  const [actionState, setActionState] = useState<{
    kind: "idle" | "loading" | "success" | "error";
    message?: string;
  }>({ kind: "idle" });
  const [externalSource, setExternalSource] = useState<{ title: string; url: string } | null>(null);
  const text = getMessageText(message);
  const sources = mergeMessageSources(getMessageSources(message), fallbackSources);
  const responseIntent = getMessageResponseIntent(message) ?? fallbackResponseIntent;
  const marketSnapshot = getMessageMarketSnapshot(message) ?? fallbackMarketSnapshot;
  const suggestionPayload = getMessageSuggestions(message);
  const suggestions =
    suggestionPayload?.actions && suggestionPayload.actions.length > 0
      ? suggestionPayload.actions
      : fallbackSuggestions;
  const renderStrategyProfile =
    showStrategyProfile && shouldShowStrategyProfile(responseIntent) && strategyProfile;
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
      <MessageContent className="rounded-[4px] border border-border bg-background px-4 py-3">
        {message.parts.map((part, index) => {
          if (part.type === "text") {
            return (
              <MessageMarkdown
                content={part.text}
                key={`${message.id}-${index}`}
                linkSafety={{
                  enabled: true,
                  renderModal: renderExternalLinkModal,
                }}
              />
            );
          }
          if (part.type === "reasoning") {
            const reasoning = reasoningPartText(part);
            if (!reasoning) {
              return null;
            }
            const isStreaming = part.state === "streaming";
            return (
              <Reasoning
                className="mb-3 rounded-[4px] border border-border/70 bg-muted/25 px-3 py-2"
                defaultOpen={false}
                isStreaming={isStreaming}
                key={`${message.id}-${index}`}
              >
                <ReasoningTrigger
                  getThinkingMessage={() => (
                    <span>{isStreaming ? t.slowProviderInitialTitle : t.modelReasoningTitle}</span>
                  )}
                />
                <ReasoningContent className="mt-2">{reasoning}</ReasoningContent>
              </Reasoning>
            );
          }
          return null;
        })}
        {message.role === "assistant" && marketSnapshot && (
          <MarketSnapshotCard language={language} snapshot={marketSnapshot} />
        )}
        {renderStrategyProfile && (
          <div className="mt-4 space-y-3 border-border border-t pt-4">
            <StrategyBriefCard language={language} profile={strategyProfile} />
            {message.role === "assistant" && (
              <>
                <StrategySnapshotCard language={language} profile={strategyProfile} />
              </>
            )}
          </div>
        )}
      </MessageContent>
      {message.role === "assistant" && suggestions.length > 0 && (
        <SuggestionRail
          disabled={actionState.kind === "loading"}
          onSubmit={onSuggestionSubmit}
          onViewArtifactWorkspace={onViewArtifactWorkspace}
          suggestions={suggestions}
        />
      )}
      {message.role === "assistant" && (
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

function SuggestionRail({
  disabled,
  onSubmit,
  onViewArtifactWorkspace,
  suggestions,
}: {
  disabled: boolean;
  onSubmit: (message: { text: string }) => Promise<void>;
  onViewArtifactWorkspace: () => void;
  suggestions: ChatSuggestionItem[];
}) {
  const visibleSuggestions = suggestions
    .filter((suggestion) => suggestion.kind !== "composer_block")
    .sort((left, right) => left.priority - right.priority)
    .slice(0, 3);

  if (visibleSuggestions.length === 0) {
    return null;
  }

  const runSuggestion = (suggestion: ChatSuggestionItem) => {
    if (suggestion.action === "open_artifact") {
      onViewArtifactWorkspace();
      return;
    }
    if (suggestion.action === "send_prompt" && suggestion.prompt) {
      void onSubmit({ text: suggestion.prompt });
    }
  };

  return (
    <div className="flex flex-wrap gap-2 pt-1">
      {visibleSuggestions.map((suggestion) => (
        <Button
          className="h-8 rounded-[4px] text-xs normal-case"
          disabled={disabled || suggestion.enabled === false}
          key={suggestion.id}
          onClick={() => runSuggestion(suggestion)}
          title={suggestion.disabled_reason}
          type="button"
          variant="outline"
        >
          {suggestion.label}
        </Button>
      ))}
    </div>
  );
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
          <div className="h-px w-full bg-gradient-to-r from-[var(--together-accent-blue)]/35 via-border to-transparent" />
        </div>

        <div
          className={cn(
            "min-w-[128px] rounded-[4px] border px-3 py-2 sm:text-right",
            hasPrice ? accentClass : "border-border/70 bg-muted/20"
          )}
        >
          <p className="text-muted-foreground text-[10px] uppercase tracking-[0.12em]">
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
  const isUp = points.at(-1)!.value >= points[0]!.value;
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
        <span>{formatCompactPrice(min)}</span>
        <span>{points.length} points</span>
        <span>{formatCompactPrice(max)}</span>
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

function reasoningPartText(part: UIMessage["parts"][number]) {
  const record = part as { text?: unknown; content?: unknown; reasoning?: unknown };
  const value = record.text ?? record.content ?? record.reasoning;
  return typeof value === "string" ? value.trim() : "";
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

function responseIntentFromRunEvents(events: RunEvent[]): ResponseIntent | null {
  for (const event of [...events].reverse()) {
    if (event.type !== "chat.response_intent") {
      continue;
    }
    const intent = event.payload?.intent;
    if (
      intent === "artifact_generation" ||
      intent === "capability_help" ||
      intent === "docs_research" ||
      intent === "general_chat" ||
      intent === "market_research" ||
      intent === "market_snapshot" ||
      intent === "strategy_building"
    ) {
      return intent;
    }
  }
  return null;
}

function marketSnapshotFromRunEvents(events: RunEvent[]): MarketSnapshot | null {
  for (const event of [...events].reverse()) {
    if (event.type !== "chat.market_snapshot") {
      continue;
    }
    const snapshot = marketSnapshotFromUnknown(event.payload);
    if (snapshot) {
      return snapshot;
    }
  }
  return null;
}

function marketSnapshotFromUnknown(value: unknown): MarketSnapshot | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const symbol = sourceText(record.symbol);
  const label = sourceText(record.label);
  const sourceItems = Array.isArray(record.sources) ? record.sources : [];
  const sources = sourceItems
    .map((item) => chatMessageSourceFromUnknown(item))
    .filter((source): source is ChatMessageSource => Boolean(source));
  if (!symbol || !label || sources.length === 0) {
    return null;
  }
  return {
    approximate: record.approximate === true,
    change: numberFromUnknown(record.change),
    change_percent: numberFromUnknown(record.change_percent),
    currency: sourceText(record.currency) ?? null,
    freshness: "source_backed",
    generated_at: sourceText(record.generated_at) ?? null,
    label,
    price: sourceText(record.price) ?? null,
    price_points: pricePointsFromUnknown(record.price_points),
    provider: sourceText(record.provider) ?? null,
    source_count: typeof record.source_count === "number" ? record.source_count : sources.length,
    sources,
    symbol,
  };
}

function numberFromUnknown(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function pricePointsFromUnknown(value: unknown): Array<{ label: string; value: number }> {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      return [];
    }
    const record = item as Record<string, unknown>;
    const label = sourceText(record.label);
    const numericValue = record.value;
    if (!label || typeof numericValue !== "number" || !Number.isFinite(numericValue)) {
      return [];
    }
    return [{ label, value: numericValue }];
  });
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

function AssistantActivity({
  activities,
  artifact,
  isWorking,
  language,
  onViewArtifactWorkspace,
  showArtifactWorkspace,
}: {
  activities: ChatActivity[];
  artifact: Artifact | null;
  isWorking: boolean;
  language: UiLanguagePreference;
  onViewArtifactWorkspace: () => void;
  showArtifactWorkspace: boolean;
}) {
  const [activityOpen, setActivityOpen] = useState(false);
  const elapsedSeconds = useElapsedSeconds(isWorking);
  if (!isWorking && activities.length === 0 && (!artifact || showArtifactWorkspace)) {
    return null;
  }

  return (
    <div className="space-y-3">
      {isWorking && (
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
                    <ToolOutput
                      errorText={activity.errorText}
                      output={activity.output}
                    />
                  </ToolContent>
                </Tool>
              ))}
            </div>
          )}
        </div>
      )}
      {artifact && !showArtifactWorkspace && (
        <AiArtifact className="rounded-[4px]">
          <ArtifactHeader>
            <div className="min-w-0">
              <ArtifactTitle className="truncate">{artifact.display_name}</ArtifactTitle>
              <ArtifactDescription>{getUiCopy(language).reviewArtifactReady}.</ArtifactDescription>
            </div>
            <ArtifactActions>
              <ArtifactAction
                icon={PanelRight}
                label={getUiCopy(language).viewArtifact}
                onClick={onViewArtifactWorkspace}
                tooltip={getUiCopy(language).viewArtifact}
              />
            </ArtifactActions>
          </ArtifactHeader>
          <ArtifactContent className="py-3 text-muted-foreground text-sm">
            {getUiCopy(language).artifactReadyDescription}
          </ArtifactContent>
        </AiArtifact>
      )}
    </div>
  );
}

function FirstTokenLoader({
  slowState,
}: {
  slowState: ReturnType<typeof slowProviderState>;
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

function ArtifactWorkspacePanel({
  artifacts,
  authKey,
  cancelRun,
  client,
  events,
  language,
  onClose,
  retryRun,
  run,
  strategyProfile,
}: {
  artifacts: Artifact[];
  authKey: string;
  cancelRun: (runId: string) => void;
  client: BackendClient;
  events: RunEvent[];
  language: UiLanguagePreference;
  onClose: () => void;
  retryRun: (runId: string) => void;
  run: Run | null;
  strategyProfile: StrategyProfile | null;
}) {
  const {
    artifactWorkspaceTab,
    selectedArtifactId,
    setArtifactWorkspaceTab,
    setSelectedArtifactId,
  } = useStrategyUiStore();
  const grouped = useMemo(() => groupArtifactsByKind(artifacts), [artifacts]);
  const activeArtifact = getArtifactForGroupedTab(artifacts, grouped, selectedArtifactId, artifactWorkspaceTab);
  const strategyArtifact = getArtifactForGroupedTab(artifacts, grouped, selectedArtifactId, "strategy");
  const codeArtifact = getArtifactForGroupedTab(artifacts, grouped, selectedArtifactId, "code");
  const riskArtifact = getArtifactForGroupedTab(artifacts, grouped, selectedArtifactId, "risk");
  const t = getUiCopy(language);
  const activeSummary = activeArtifact ? getArtifactUserSummary(activeArtifact, language) : null;
  const artifactTitle =
    activeArtifact?.display_name ??
    (run ? runStatusSummary(run.status, language) : t.reviewWorkspaceTitle);
  const tabs = ARTIFACT_WORKSPACE_TABS.map((value) => [value, artifactTabLabel(value, language)] as const);
  const canRetry = run && ["failed", "blocked", "cancelled", "completed"].includes(run.status);

  return (
    <aside className="fixed inset-0 z-40 grid h-[100dvh] min-h-0 grid-rows-[auto_1fr] overflow-hidden border-l border-border bg-background lg:relative lg:inset-auto lg:z-auto lg:h-full">
      <div className="border-b border-border p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              {activeSummary ? (
                <ArtifactKindIcon kind={activeSummary.kind} />
              ) : (
                <PanelRight className="size-4 shrink-0 text-muted-foreground" />
              )}
              <p className="truncate font-medium text-sm tracking-[-0.01em]">{artifactTitle}</p>
            </div>
            <p className="mt-1 text-muted-foreground text-xs">
              {activeSummary?.description ??
                t.reviewOnlyBoundary}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {run && <StatusPill status={run.status} />}
            <Button onClick={onClose} size="icon-sm" type="button" variant="ghost">
              <X className="size-4" />
              <span className="sr-only">{t.closeArtifactWorkspace}</span>
            </Button>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap gap-1 rounded-[4px] border border-border bg-muted p-1">
          {tabs.map(([value, label]) => (
            <button
              className={cn(
                "together-mono-label rounded-[3.25px] px-2 py-1 text-[10px] transition",
                artifactWorkspaceTab === value
                  ? "bg-background text-foreground"
                  : "text-muted-foreground hover:bg-background/70"
              )}
              key={value}
              onClick={() => setArtifactWorkspaceTab(value)}
              type="button"
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="min-h-0 space-y-4 overflow-y-auto overscroll-contain p-4">
        {run && run.status !== "completed" && (
          <UserProgressView cancelRun={cancelRun} events={events} language={language} retryRun={retryRun} run={run} />
        )}
        {artifacts.length > 1 && (
          <ArtifactSwitcher
            artifacts={artifacts}
            language={language}
            selectedArtifactId={activeArtifact?.id ?? null}
            setSelectedArtifactId={setSelectedArtifactId}
          />
        )}
        {artifactWorkspaceTab === "strategy" && (
          <StrategyWorkspaceSummary
            artifact={strategyArtifact}
            authKey={authKey}
            client={client}
            language={language}
            profile={strategyProfile}
            run={run}
          />
        )}
        {artifactWorkspaceTab === "code" && (
          <CodeReviewWorkspace
            artifact={codeArtifact}
            authKey={authKey}
            client={client}
            language={language}
            profile={strategyProfile}
          />
        )}
        {artifactWorkspaceTab === "risk" && (
          <RiskReviewWorkspace
            artifact={riskArtifact}
            authKey={authKey}
            client={client}
            language={language}
            profile={strategyProfile}
          />
        )}
        {artifactWorkspaceTab === "validation" && (
          <ReviewNotes
            artifacts={grouped.validation.length > 0 ? grouped.validation : grouped.notes}
            authKey={authKey}
            client={client}
            language={language}
          />
        )}
        {artifactWorkspaceTab === "changes" && (
          <ChangesWorkspace artifacts={artifacts} language={language} profile={strategyProfile} />
        )}
        <div className="flex flex-wrap gap-2">
          {run && canRetry && (
            <Button onClick={() => retryRun(run.id)} size="sm" type="button" variant="outline">
              <RefreshCcw className="size-3" />
              {run.status === "completed" ? t.regenerate : t.tryAgain}
            </Button>
          )}
        </div>
      </div>
    </aside>
  );
}

function StrategyWorkspaceSummary({
  artifact,
  authKey,
  client,
  language,
  profile,
  run,
}: {
  artifact: Artifact | null;
  authKey: string;
  client: BackendClient;
  language: UiLanguagePreference;
  profile: StrategyProfile | null;
  run: Run | null;
}) {
  const t = getUiCopy(language);
  if (!profile && !artifact) {
    return (
      <EmptyInspector
        icon={<Gauge className="size-5" />}
        text={run ? emptyArtifactText(run.status, language) : t.noStrategyContext}
      />
    );
  }
  return (
    <div className="space-y-4">
      {profile && (
        <>
          <StrategyBriefCard language={language} profile={profile} />
          <StrategySnapshotCard language={language} profile={profile} />
          <AssumptionsLedger language={language} profile={profile} />
        </>
      )}
      {artifact && (
        <ArtifactPreview artifact={artifact} authKey={authKey} client={client} language={language} />
      )}
    </div>
  );
}

function CodeReviewWorkspace({
  artifact,
  authKey,
  client,
  language,
  profile,
}: {
  artifact: Artifact | null;
  authKey: string;
  client: BackendClient;
  language: UiLanguagePreference;
  profile: StrategyProfile | null;
}) {
  const t = getUiCopy(language);
  return (
    <div className="space-y-4">
      {profile?.code_outline.length ? (
        <div className="rounded-[4px] border border-border p-3">
          <div className="mb-3 flex items-center gap-2">
            <Braces className="size-4 text-muted-foreground" />
            <p className="font-medium text-sm">{t.codeOutline}</p>
          </div>
          <div className="grid gap-2">
            {profile.code_outline.map((item) => (
              <div
                className="flex items-center justify-between gap-3 rounded-[4px] border border-border bg-muted/20 px-3 py-2 text-sm"
                key={item.id}
              >
                <span>{item.label}</span>
                <span className="text-muted-foreground text-xs">{item.kind}</span>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <EmptyInspector icon={<Braces className="size-5" />} text={t.noCodeOutline} />
      )}
      {artifact ? (
        <ArtifactPreview artifact={artifact} authKey={authKey} client={client} language={language} />
      ) : (
        <EmptyInspector icon={<FileCode2 className="size-5" />} text={t.noCodeArtifact} />
      )}
    </div>
  );
}

function RiskReviewWorkspace({
  artifact,
  authKey,
  client,
  language,
  profile,
}: {
  artifact: Artifact | null;
  authKey: string;
  client: BackendClient;
  language: UiLanguagePreference;
  profile: StrategyProfile | null;
}) {
  const t = getUiCopy(language);
  const riskRules = profile?.brief.risk_rules ?? [];
  return (
    <div className="space-y-4">
      <div className="rounded-[4px] border border-border p-3">
        <div className="mb-3 flex items-center gap-2">
          <Gauge className="size-4 text-muted-foreground" />
          <p className="font-medium text-sm">{t.riskReviewTab}</p>
        </div>
        {riskRules.length > 0 ? (
          <ul className="space-y-2 text-sm">
            {riskRules.map((rule) => (
              <li className="flex gap-2" key={rule}>
                <span className="mt-2 size-1 rounded-full bg-muted-foreground" />
                <span>{rule}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground text-sm">{nextActionLabel("add_risk_rules", language)}</p>
        )}
      </div>
      {artifact && (
        <ArtifactPreview artifact={artifact} authKey={authKey} client={client} language={language} />
      )}
    </div>
  );
}

function ChangesWorkspace({
  artifacts,
  language,
  profile,
}: {
  artifacts: Artifact[];
  language: UiLanguagePreference;
  profile: StrategyProfile | null;
}) {
  const t = getUiCopy(language);
  return (
    <div className="space-y-4">
      <div className="rounded-[4px] border border-border p-3">
        <div className="mb-3 flex items-center gap-2">
          <ListChecks className="size-4 text-muted-foreground" />
          <p className="font-medium text-sm">{t.changesTab}</p>
        </div>
        {artifacts.length > 0 ? (
          <div className="grid gap-2">
            {artifacts.map((artifact) => (
              <div
                className="rounded-[4px] border border-border bg-muted/20 px-3 py-2"
                key={artifact.id}
              >
                <p className="truncate font-medium text-sm">{artifact.display_name}</p>
                <p className="text-muted-foreground text-xs">
                  {new Date(artifact.created_at).toLocaleString(languageLocale(language))}
                </p>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">{t.noDetailsAvailable}</p>
        )}
      </div>
      {profile && <StrategySnapshotCard language={language} profile={profile} />}
    </div>
  );
}

function ArtifactSwitcher({
  artifacts,
  language,
  selectedArtifactId,
  setSelectedArtifactId,
}: {
  artifacts: Artifact[];
  language: UiLanguagePreference;
  selectedArtifactId: string | null;
  setSelectedArtifactId: (artifactId: string | null) => void;
}) {
  return (
    <div className="grid gap-1">
      {artifacts.map((artifact) => (
        <ArtifactSwitcherItem
          artifact={artifact}
          isSelected={selectedArtifactId === artifact.id}
          key={artifact.id}
          language={language}
          onSelect={() => setSelectedArtifactId(artifact.id)}
        />
      ))}
    </div>
  );
}

function ArtifactSwitcherItem({
  artifact,
  isSelected,
  language,
  onSelect,
}: {
  artifact: Artifact;
  isSelected: boolean;
  language: UiLanguagePreference;
  onSelect: () => void;
}) {
  const summary = getArtifactUserSummary(artifact, language);
  return (
    <button
      className={cn(
        "flex w-full items-center gap-3 rounded-[4px] border border-border px-3 py-2 text-left transition hover:bg-muted",
        isSelected && "border-foreground/30 bg-muted"
      )}
      onClick={onSelect}
      type="button"
    >
      <ArtifactKindIcon kind={summary.kind} />
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium text-sm">{artifact.display_name}</p>
        <p className="text-muted-foreground text-xs">{summary.label}</p>
      </div>
    </button>
  );
}

function ArtifactPreview({
  artifact,
  authKey,
  client,
  language,
}: {
  artifact: Artifact;
  authKey: string;
  client: BackendClient;
  language: UiLanguagePreference;
}) {
  const t = getUiCopy(language);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [isDownloading, setIsDownloading] = useState(false);
  const [isSubmittingFeedback, setIsSubmittingFeedback] = useState(false);
  const preview = useQuery({
    queryFn: () => client.getArtifactPreview(artifact.id, { maxBytes: 50000 }),
    queryKey: ["artifact-preview", authKey, artifact.id],
  });

  if (preview.isLoading) {
    return <PreviewLoading label={t.artifactPreviewLoading} />;
  }
  if (preview.error) {
    return (
      <div className="space-y-2">
        <ErrorBlock message={errorMessage(preview.error)} />
        <Button onClick={() => void preview.refetch()} size="sm" type="button" variant="outline">
          <RefreshCcw className="size-3" />
          {t.tryAgain}
        </Button>
      </div>
    );
  }
  if (!preview.data) {
    return null;
  }
  const content =
    typeof preview.data.preview === "string"
      ? preview.data.preview
      : JSON.stringify(preview.data.preview, null, 2);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setActionError(null);
      setActionMessage(t.copied);
    } catch {
      setActionError(t.copyArtifactFailure);
      setActionMessage(null);
    }
  };

  const handleDownload = async () => {
    try {
      setIsDownloading(true);
      const raw = await client.getArtifactContent(artifact.id);
      const rawContent =
        typeof raw.content === "string" ? raw.content : JSON.stringify(raw.content, null, 2);
      const blob = new Blob([rawContent], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${artifact.display_name.replace(/[^a-z0-9._-]+/gi, "-").toLowerCase()}.txt`;
      link.click();
      URL.revokeObjectURL(url);
      setActionError(null);
      setActionMessage(t.downloadStarted);
    } catch (error) {
      setActionError(errorMessage(error));
      setActionMessage(null);
    } finally {
      setIsDownloading(false);
    }
  };

  const handleReportFeedback = async (rating: "up" | "down") => {
    if (!artifact.conversation_id) {
      setActionError("Report feedback target is missing a conversation.");
      setActionMessage(null);
      return;
    }
    try {
      setIsSubmittingFeedback(true);
      await client.createFeedback({
        conversation_id: artifact.conversation_id,
        run_id: artifact.run_id,
        artifact_id: artifact.id,
        rating,
        category: "backtest_report",
        correction:
          rating === "up"
            ? "Backtest report reviewed as useful."
            : "Backtest report needs strategy iteration.",
      });
      setActionError(null);
      setActionMessage(rating === "up" ? "Feedback saved." : "Feedback saved for iteration.");
    } catch (error) {
      setActionError(errorMessage(error));
      setActionMessage(null);
    } finally {
      setIsSubmittingFeedback(false);
    }
  };

  return (
    <ArtifactPreviewContent
      actionError={actionError}
      actionMessage={actionMessage}
      isDownloading={isDownloading}
      isSubmittingFeedback={isSubmittingFeedback}
      onCopy={handleCopy}
      onDownload={handleDownload}
      onReportFeedback={handleReportFeedback}
      preview={preview.data}
      language={language}
    />
  );
}

function ArtifactPreviewContent({
  actionError,
  actionMessage,
  isDownloading,
  isSubmittingFeedback,
  language,
  onCopy,
  onDownload,
  onReportFeedback,
  preview,
}: {
  actionError: string | null;
  actionMessage: string | null;
  isDownloading: boolean;
  isSubmittingFeedback: boolean;
  language: UiLanguagePreference;
  onCopy: () => Promise<void>;
  onDownload: () => Promise<void>;
  onReportFeedback: (rating: "up" | "down") => Promise<void>;
  preview: ArtifactPreviewResponse;
}) {
  const t = getUiCopy(language);
  const content =
    typeof preview.preview === "string"
      ? preview.preview
      : JSON.stringify(preview.preview, null, 2);
  const codeLanguage = artifactLanguage(preview);
  const backtestReport = parseBacktestArtifactPreview(preview.kind, preview.preview);
  return (
    <div className="relative space-y-2">
      <div className="absolute top-2 right-2 z-10 flex gap-1 rounded-[4px] border border-border bg-background/90 p-1 shadow-sm backdrop-blur">
        {preview.raw_available && (
          <Button
            disabled={isDownloading}
            onClick={() => void onDownload()}
            size="icon-sm"
            title={isDownloading ? t.downloading : t.downloadRaw}
            type="button"
            variant="ghost"
          >
            {isDownloading ? (
              <RefreshCcw className="size-4 animate-spin" />
            ) : (
              <Download className="size-4" />
            )}
            <span className="sr-only">{isDownloading ? t.downloading : t.downloadRaw}</span>
          </Button>
        )}
        <Button
          onClick={() => void onCopy()}
          size="icon-sm"
          title={t.copy}
          type="button"
          variant="ghost"
        >
          <Clipboard className="size-4" />
          <span className="sr-only">{t.copy}</span>
        </Button>
      </div>
      <div className="min-w-0 pr-20">
          <p className="truncate font-medium text-sm">{preview.display_name}</p>
          {preview.truncated && (
            <span className="together-mono-label mt-1 inline-flex rounded-[3.25px] border px-2 py-0.5 text-[10px] text-muted-foreground">
              {t.truncated}
            </span>
          )}
      </div>
      {actionError && <ErrorBlock message={actionError} />}
      {actionMessage && !actionError && (
        <p className="px-1 text-muted-foreground text-xs">{actionMessage}</p>
      )}
      {backtestReport && (
        <BacktestReportCard
          isSubmittingFeedback={isSubmittingFeedback}
          onFeedback={onReportFeedback}
          report={backtestReport}
        />
      )}
      {codeLanguage === "markdown" && !backtestReport ? (
        <MessageResponse
          className="text-sm"
          components={
            artifactPreviewMarkdownComponents as MessageResponseProps["components"]
          }
          tableStyle="plain"
        >
          {content}
        </MessageResponse>
      ) : (
        <CodeBlock
          className="max-h-[420px]"
          code={content}
          language={codeLanguage}
          showLineNumbers
        />
      )}
    </div>
  );
}

function UserProgressView({
  cancelRun,
  events,
  language,
  retryRun,
  run,
}: {
  cancelRun: (runId: string) => void;
  events: RunEvent[];
  language: UiLanguagePreference;
  retryRun: (runId: string) => void;
  run: Run;
}) {
  const [open, setOpen] = useState(false);
  const steps = mapRunEventsToUserSteps(events, run.status, language);
  const currentStep = currentProgressStep(steps);
  const isActive = !["completed", "failed", "blocked", "cancelled"].includes(run.status);
  const canRetry = ["failed", "blocked", "cancelled"].includes(run.status);

  return (
    <div className="rounded-[4px] border border-border bg-background">
      <div className="flex items-center justify-between gap-3 px-3 py-2">
        <button
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
          onClick={() => setOpen((value) => !value)}
          type="button"
        >
          <span
            className={cn(
              "size-2 rounded-full border",
              isActive ? "border-foreground bg-background" : "border-foreground bg-foreground"
            )}
          />
          <div className="min-w-0">
            <p className="truncate font-medium text-sm">{runStatusSummary(run.status, language)}</p>
            {currentStep && (
              <p className="truncate text-muted-foreground text-xs">{currentStep.label}</p>
            )}
          </div>
          <ChevronDown
            className={cn(
              "ml-auto size-4 shrink-0 text-muted-foreground transition-transform",
              open && "rotate-180"
            )}
          />
        </button>
        <div className="flex shrink-0 gap-1">
          {isActive && (
            <Button onClick={() => cancelRun(run.id)} size="sm" type="button" variant="outline">
              <X className="size-3" />
              {getUiCopy(language).cancel}
            </Button>
          )}
          {canRetry && (
            <Button onClick={() => retryRun(run.id)} size="sm" type="button" variant="outline">
              <RefreshCcw className="size-3" />
              {getUiCopy(language).tryAgain}
            </Button>
          )}
        </div>
      </div>
      {open && (
        <div className="space-y-3 border-t border-border p-3">
          {steps.map((step) => (
            <div className="flex items-center gap-3" key={step.label}>
              <span
                className={cn(
                  "size-2 rounded-full border",
                  step.state === "done" && "border-foreground bg-foreground",
                  step.state === "current" && "border-foreground bg-background",
                  step.state === "waiting" && "border-border bg-muted"
                )}
              />
              <span
                className={cn(
                  "text-sm",
                  step.state === "waiting" ? "text-muted-foreground" : "text-foreground"
                )}
              >
                {step.label}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ReviewNotes({
  artifacts,
  authKey,
  client,
  language,
}: {
  artifacts: Artifact[];
  authKey: string;
  client: BackendClient;
  language: UiLanguagePreference;
}) {
  const t = getUiCopy(language);
  const previews = useQueries({
    queries: artifacts.map((artifact) => ({
      queryFn: () => client.getArtifactPreview(artifact.id, { maxBytes: 50000 }),
      queryKey: ["artifact-preview", authKey, artifact.id],
    })),
  });

  if (artifacts.length === 0) {
    return <EmptyInspector icon={<Clipboard className="size-5" />} text={t.noReviewNotes} />;
  }
  if (previews.some((preview) => preview.isLoading)) {
    return <PreviewLoading label={t.loadingReviewNotes} />;
  }
  const firstError = previews.find((preview) => preview.error);
  if (firstError?.error) {
    return (
      <div className="space-y-2">
        <ErrorBlock message={errorMessage(firstError.error)} />
        <Button
          onClick={() => previews.forEach((preview) => void preview.refetch())}
          size="sm"
          type="button"
          variant="outline"
        >
          <RefreshCcw className="size-3" />
          {t.tryAgain}
        </Button>
      </div>
    );
  }
  const markdown = mergedReviewNotes(
    previews
      .map((preview) => preview.data)
      .filter((preview): preview is ArtifactPreviewResponse => Boolean(preview)),
    language
  );
  return (
    <div className="rounded-[4px] border border-border bg-background p-3 text-sm">
      <MessageResponse>{markdown}</MessageResponse>
    </div>
  );
}

function PreviewLoading({ label }: { label: string }) {
  return (
    <div className="rounded-[4px] border border-border bg-background p-3 text-sm">
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

function emptyArtifactText(status: Run["status"], language: UiLanguagePreference = "en") {
  const t = getUiCopy(language);
  if (status === "failed" || status === "blocked") {
    return t.couldNotCreateArtifact;
  }
  if (status === "cancelled") {
    return t.artifactCreationCancelled;
  }
  return t.preparingReviewArtifact;
}

function mergedReviewNotes(previews: ArtifactPreviewResponse[], language: UiLanguagePreference = "en") {
  const t = getUiCopy(language);
  if (previews.length === 0) {
    return t.noReviewNotes;
  }
  const sections = previews.map((preview) => {
    const summary = getArtifactUserSummary(preview, language);
    const heading =
      summary.kind === "validation"
        ? t.boundaryChecks
        : summary.kind === "risk"
          ? t.riskNotes
          : t.reviewSummary;
    return `## ${heading}\n\n${previewToReadableMarkdown(preview.preview, language)}`;
  });
  return [
    t.reviewNotesHeading,
    "",
    ...sections.flatMap((section) => [section, ""]),
    t.suggestedNextStepsHeading,
    "",
    t.reviewArtifactsBeforeUseStep,
    t.confirmAssumptionsStep,
    t.treatAsDraftStep,
  ].join("\n");
}

function previewToReadableMarkdown(value: unknown, language: UiLanguagePreference = "en"): string {
  const t = getUiCopy(language);
  if (typeof value === "string") {
    return value.trim() || t.noDetailsAvailable;
  }
  if (!value || typeof value !== "object") {
    return t.noDetailsAvailable;
  }
  if (Array.isArray(value)) {
    return value.length
      ? value.map((item) => `- ${inlineReadableValue(item, language)}`).join("\n")
      : t.noDetailsAvailable;
  }
  const entries = Object.entries(value as Record<string, unknown>).filter(
    ([, entryValue]) => entryValue !== null && entryValue !== undefined
  );
  if (entries.length === 0) {
    return t.noDetailsAvailable;
  }
  return entries
    .map(([key, entryValue]) => `- **${readableKey(key)}:** ${inlineReadableValue(entryValue, language)}`)
    .join("\n");
}

function inlineReadableValue(value: unknown, language: UiLanguagePreference = "en"): string {
  const t = getUiCopy(language);
  if (Array.isArray(value)) {
    return value.map((item) => inlineReadableValue(item, language)).join(", ") || t.none;
  }
  if (value && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, entryValue]) => `${readableKey(key)}: ${inlineReadableValue(entryValue, language)}`)
      .join("; ");
  }
  if (typeof value === "boolean") {
    return value ? t.yes : t.no;
  }
  return String(value ?? t.none);
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

function EmptyInspector({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <div className="flex min-h-32 flex-col items-center justify-center rounded-[4px] border border-dashed border-border p-4 text-center text-muted-foreground text-sm">
      {icon}
      <p className="mt-2">{text}</p>
    </div>
  );
}

function buildFallbackSuggestionPayload({
  artifactAvailable,
  intent,
  language,
  strategyProfile,
}: {
  artifactAvailable: boolean;
  intent: ResponseIntent | null;
  language: UiLanguagePreference;
  strategyProfile: StrategyProfile | null;
}) {
  const t = getUiCopy(language);
  const strategyRelevant =
    intent === "strategy_building" ||
    intent === "artifact_generation" ||
    (intent === null && Boolean(strategyProfile));
  const composerBlocks = strategyRelevant ? fallbackComposerBlocks(language, strategyProfile) : [];
  const actions: ChatSuggestionItem[] = [];
  const ready = strategyProfile?.snapshot.completeness === "ready_for_artifact";

  if (artifactAvailable) {
    actions.push({
      action: "open_artifact",
      category: "code",
      enabled: true,
      id: "fallback-view-artifact",
      kind: "artifact_action",
      label: t.viewArtifact,
      priority: 1,
    });
  } else if (intent === "market_snapshot" || intent === "market_research") {
    actions.push({
      action: "send_prompt",
      category: "strategy",
      enabled: true,
      id: "fallback-market-to-strategy",
      kind: "chat_action",
      label: localizedSuggestionCopy(language, "Dùng cho strategy", "Use for strategy"),
      priority: 1,
      prompt:
        isVietnameseUi(language)
          ? "Dùng market context này để bắt đầu xây strategy review-only."
          : "Use this market context to start a review-only strategy.",
    });
  } else if (
    !ready &&
    (intent === "strategy_building" || intent === "artifact_generation") &&
    strategyProfile?.snapshot.missing_fields?.length
  ) {
    actions.push(
      ...strategyProfile.snapshot.missing_fields
        .slice(0, 2)
        .map((field, index) => fallbackMissingFieldAction(field, language, index + 1))
    );
  } else if (ready && strategyRelevant) {
    actions.push(
      {
        action: "send_prompt",
        category: "code",
        enabled: true,
        id: "fallback-generate-pine",
        kind: "chat_action",
        label: localizedSuggestionCopy(language, "Tạo Pine v6", "Generate Pine v6"),
        priority: 1,
        prompt:
          isVietnameseUi(language)
            ? "Tạo artifact Pine v6 review-only từ strategy context hiện tại."
            : "Generate a review-only Pine v6 artifact from the current strategy context.",
      },
      {
        action: "send_prompt",
        category: "risk",
        enabled: true,
        id: "fallback-review-risk",
        kind: "chat_action",
        label: localizedSuggestionCopy(language, "Review risk", "Review risk"),
        priority: 2,
        prompt:
          isVietnameseUi(language)
            ? "Review risk rules trong strategy context hiện tại."
            : "Review the risk rules in the current strategy context.",
      }
    );
  }

  return { actions, composer_blocks: composerBlocks, version: 1 as const };
}

function fallbackMissingFieldAction(
  field: string,
  language: UiLanguagePreference,
  priority: number
): ChatSuggestionItem {
  const normalized = field.toLowerCase();
  const category = suggestionCategoryForMissingField(normalized);
  const label = localizedSuggestionCopy(
    language,
    `Thêm ${readableKey(field).toLowerCase()}`,
    `Add ${readableKey(field).toLowerCase()}`
  );
  return {
    action: "send_prompt",
    category,
    enabled: true,
    id: `fallback-add-${normalized.replace(/[^a-z0-9]+/g, "-")}`,
    kind: "chat_action",
    label,
    priority,
    prompt: localizedSuggestionCopy(
      language,
      `Thêm ${readableKey(field).toLowerCase()} rõ ràng cho strategy context hiện tại.`,
      `Add clear ${readableKey(field).toLowerCase()} to the current strategy context.`
    ),
  };
}

function suggestionCategoryForMissingField(field: string): ChatSuggestionItem["category"] {
  if (field.includes("entry")) {
    return "entry";
  }
  if (field.includes("exit")) {
    return "exit";
  }
  if (field.includes("risk")) {
    return "risk";
  }
  if (field.includes("market") || field.includes("symbol") || field.includes("timeframe")) {
    return "market";
  }
  return "strategy";
}

function fallbackComposerBlocks(
  language: UiLanguagePreference,
  profile: StrategyProfile | null
): ChatSuggestionItem[] {
  const t = getUiCopy(language);
  const symbol = profile?.brief.symbol || "ETHUSDT";
  const timeframe = profile?.brief.timeframe || "1h";
  return [
    composerBlock("market", t.signalGrammarMarketLabel, [
      {
        id: "detected-market",
        insert_template: t.signalGrammarMarketTemplate
          .replace("{symbol}", symbol)
          .replace("{timeframe}", timeframe),
        label: `${symbol} / ${timeframe}`,
      },
      {
        id: "btc-4h",
        insert_template: t.signalGrammarMarketTemplate
          .replace("{symbol}", "BTCUSDT")
          .replace("{timeframe}", "4h"),
        label: "BTC / 4h",
      },
    ]),
    composerBlock("entry", t.signalGrammarEntryLabel, [
      {
        id: "ema-crossover",
        insert_template:
          isVietnameseUi(language)
            ? "Entry rules:\n- Long khi EMA 20 cắt lên EMA 50\n- Xác nhận RSI trên 50"
            : "Entry rules:\n- Long when EMA 20 crosses above EMA 50\n- Confirm RSI is above 50",
        label: "EMA crossover",
      },
      {
        id: "breakout",
        insert_template:
          isVietnameseUi(language)
            ? "Entry rules:\n- Long khi giá phá vùng kháng cự gần nhất\n- Xác nhận bằng volume tăng"
            : "Entry rules:\n- Long when price breaks the nearest resistance\n- Confirm with rising volume",
        label: "Breakout",
      },
    ]),
    composerBlock("exit", t.signalGrammarExitLabel, [
      {
        id: "atr-stop",
        insert_template:
          isVietnameseUi(language)
            ? "Exit rules:\n- Stop-loss: 2 ATR\n- Take-profit: 2R\n- Thoát khi tín hiệu đảo chiều"
            : "Exit rules:\n- Stop-loss: 2 ATR\n- Take-profit: 2R\n- Exit on opposite signal",
        label: "ATR stop",
      },
      {
        id: "trailing-stop",
        insert_template:
          isVietnameseUi(language)
            ? "Exit rules:\n- Dùng trailing stop theo swing low/high\n- Chốt một phần ở 1R"
            : "Exit rules:\n- Use a trailing stop by swing low/high\n- Take partial profit at 1R",
        label: "Trailing stop",
      },
    ]),
    composerBlock("risk", t.signalGrammarRiskLabel, [
      {
        id: "balanced-risk",
        insert_template:
          isVietnameseUi(language)
            ? "Risk rules:\n- Risk 1% equity mỗi lệnh\n- Max 1 vị thế mở\n- Không vào lệnh khi biến động bất thường"
            : "Risk rules:\n- Risk 1% equity per trade\n- Max 1 open position\n- Avoid entries during abnormal volatility",
        label: "Balanced",
      },
      {
        id: "conservative-risk",
        insert_template:
          isVietnameseUi(language)
            ? "Risk rules:\n- Risk 0.5% equity mỗi lệnh\n- Stop-loss bắt buộc\n- Bỏ qua setup nếu R:R dưới 1.5"
            : "Risk rules:\n- Risk 0.5% equity per trade\n- Stop-loss is required\n- Skip setups below 1.5R",
        label: "Conservative",
      },
    ]),
  ];
}

function composerBlock(
  slot: NonNullable<ChatSuggestionItem["slot"]>,
  label: string,
  variants: NonNullable<ChatSuggestionItem["variants"]>
): ChatSuggestionItem {
  return {
    action: "insert_or_update_block",
    category: slot,
    enabled: true,
    id: `fallback-block-${slot}`,
    kind: "composer_block",
    label,
    priority: ["market", "entry", "exit", "risk"].indexOf(slot),
    slot,
    variants,
  };
}

function insertOrUpdateStrategyBlock(
  currentValue: string,
  slot: ChatSuggestionItem["slot"],
  template: string
) {
  const trimmedTemplate = template.trim();
  if (!slot) {
    return appendPromptBlock(currentValue, trimmedTemplate);
  }
  const pattern = strategyBlockPattern(slot);
  if (pattern.test(currentValue)) {
    return currentValue.replace(pattern, trimmedTemplate);
  }
  return appendPromptBlock(currentValue, trimmedTemplate);
}

function appendPromptBlock(currentValue: string, block: string) {
  const trimmed = currentValue.trimEnd();
  return trimmed ? `${trimmed}\n\n${block}` : block;
}

function strategyBlockPattern(slot: ChatSuggestionItem["slot"]) {
  const headers: Record<NonNullable<ChatSuggestionItem["slot"]>, string> = {
    entry: "(?:Entry rules|Vào lệnh)",
    exit: "(?:Exit rules|Thoát lệnh)",
    market: "(?:Market|Thị trường)",
    risk: "(?:Risk rules|Risk)",
  };
  const header = slot ? headers[slot] : "";
  return new RegExp(`(^${header}:?[\\s\\S]*?)(?=\\n\\n(?:Market|Thị trường|Entry rules|Vào lệnh|Exit rules|Thoát lệnh|Risk rules|Risk):?|$)`, "im");
}

function localizedSuggestionCopy(language: UiLanguagePreference, vi: string, en: string) {
  return isVietnameseUi(language) ? vi : en;
}

function isVietnameseUi(language: UiLanguagePreference) {
  return languageLocale(language).startsWith("vi");
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

function slowProviderState(elapsedSeconds: number, language: UiLanguagePreference = "en") {
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

function ErrorBlock({ message }: { message: string }) {
  return (
    <div className="rounded-[4px] border border-red-500/30 bg-red-500/10 p-3 text-red-700 text-sm dark:text-red-300">
      {message}
    </div>
  );
}

function SidebarSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 5 }).map((_, index) => (
        <div className="h-16 rounded-[4px] bg-muted" key={index} />
      ))}
    </div>
  );
}

function artifactLanguage(preview: ArtifactPreviewResponse): BundledLanguage | "markdown" {
  if (preview.mime_type === "text/markdown") {
    return "markdown";
  }
  if (preview.language === "json" || preview.mime_type === "application/json") {
    return "json";
  }
  if (preview.language === "pine" || preview.kind.includes("pine")) {
    return "javascript";
  }
  if (preview.language === "mql5" || preview.kind.includes("mql5")) {
    return "c";
  }
  return "json";
}

async function consumeRunProgress(
  client: BackendClient,
  runId: string,
  setRunEvents: (updater: (events: RunEvent[]) => RunEvent[]) => void,
  signal: AbortSignal
) {
  try {
    const response = await client.streamRunProgress(runId, { signal });
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
          throw new Error("Run progress stream frame exceeded the maximum size.");
        }
        const { frames, remaining } = splitCompleteSseFrames(buffer);
        buffer = remaining;
        for (const chunk of frames) {
          const events = parseBackendSseEvents(chunk);
          if (events.length > 0) {
            setRunEvents((current) => [...current, ...events].slice(-30));
          }
        }
      }
      const finalEvents = parseBackendSseEvents(buffer);
      if (finalEvents.length > 0) {
        setRunEvents((current) => [...current, ...finalEvents].slice(-30));
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
    // Progress streaming is supplemental; the run state query remains authoritative.
  }
}

function errorMessage(error: unknown) {
  if (error instanceof BackendClientError) {
    return error.message;
  }
  return errorMessageFromUnknown(error);
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
