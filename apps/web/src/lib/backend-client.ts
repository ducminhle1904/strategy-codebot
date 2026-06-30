import { z } from "zod";

import {
  AccountUsageResponseSchema,
  ActionRegistryResponseSchema,
  ArtifactContentResponseSchema,
  ArtifactListResponseSchema,
  ArtifactPreviewResponseSchema,
  BacktestApprovalDecisionResponseSchema,
  BackendErrorResponseSchema,
  BotProposalConfirmStartRequestSchema,
  BotProposalConfirmStartResponseSchema,
  BotProposalCreateRequestSchema,
  BotProposalSchema,
  ConversationCreateSchema,
  ConversationListResponseSchema,
  ConversationSchema,
  ConversationSidebarResponseSchema,
  ConversationStateResponseSchema,
  ConversationUpdateSchema,
  FeedbackCreateSchema,
  FeedbackOptionsResponseSchema,
  FeedbackSchema,
  HealthResponseSchema,
  MeResponseSchema,
  MessageCreateSchema,
  MessageListResponseSchema,
  MessageSchema,
  NautilusRuntimeEventSchema,
  NautilusRuntimeKillSwitchRequestSchema,
  NautilusRuntimeListResponseSchema,
  NautilusRuntimeSchema,
  NautilusRuntimeStartRequestSchema,
  ProviderStatusResponseSchema,
  ReadyResponseSchema,
  RunCreateResponseSchema,
  RunCreateSchema,
  RunEventSchema,
  RunObservabilityResponseSchema,
  RunSchema,
  WorkflowTaskListResponseSchema,
  WorkflowTaskContinuationRequestSchema,
  WorkflowTaskResponseRequestSchema,
  WorkflowTaskSchema,
  type BacktestApprovalDecisionRequest,
  type BacktestApprovalDecisionResponse,
  type AccountUsageResponse,
  type ActionRegistryResponse,
  type ArtifactContentResponse,
  type ArtifactListResponse,
  type ArtifactPreviewResponse,
  type BotProposal,
  type BotProposalConfirmStartRequest,
  type BotProposalConfirmStartResponse,
  type BotProposalCreateRequest,
  type Conversation,
  type ConversationCreate,
  type ConversationListResponse,
  type ConversationSidebarResponse,
  type ConversationStateResponse,
  type ConversationUpdate,
  type Feedback,
  type FeedbackCreate,
  type FeedbackOptionsResponse,
  type HealthResponse,
  type MeResponse,
  type Message,
  type MessageCreate,
  type MessageListResponse,
  type NautilusRuntime,
  type NautilusRuntimeEvent,
  type NautilusRuntimeKillSwitchRequest,
  type NautilusRuntimeListResponse,
  type NautilusRuntimeMode,
  type NautilusRuntimeStartRequest,
  type ProviderStatusResponse,
  type ReadyResponse,
  type Run,
  type RunCreate,
  type RunCreateResponse,
  type RunEvent,
  type RunObservabilityResponse,
  type WorkflowTask,
  type WorkflowTaskContinuationRequest,
  type WorkflowTaskListResponse,
  type WorkflowTaskResponseRequest,
} from "./backend-schemas";
import { parseSseJsonPayloads } from "./sse";

type Fetcher = typeof fetch;

export type BackendClientOptions = {
  baseUrl?: string;
  userId?: string;
  workspaceId?: string;
  userTier?: string;
  workspaceRole?: string;
  internalAuthSecret?: string;
  fetcher?: Fetcher;
  idempotencyKeyFactory?: () => string;
};

export type BackendHeaderOptions = {
  userId?: string;
  workspaceId?: string;
  userTier?: string;
  workspaceRole?: string;
  internalAuthSecret?: string;
  body?: unknown;
  requestId?: string;
  traceId?: string;
  idempotencyKey?: string;
  lastEventId?: string;
  createOperation?: boolean;
  idempotencyKeyFactory?: () => string;
};

export const DEFAULT_API_BASE_URL =
  process.env.STRATEGY_CODEBOT_API_BASE_URL ??
  process.env.PYTHON_BACKEND_URL ??
  process.env.NEXT_PUBLIC_STRATEGY_CODEBOT_API_URL ??
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  "http://localhost:8000";

export type RequestOptions = {
  requestId?: string;
  traceId?: string;
  idempotencyKey?: string;
  signal?: AbortSignal;
};

export type MessageCreateOptions = RequestOptions & {
  mode?: "deterministic" | "agent" | "workflow_task_continuation";
};

export type StreamOptions = RequestOptions & {
  lastEventId?: string;
};

type ArtifactListOptions = {
  cursor?: string | null;
  limit?: number;
  visibility?: "user" | "all";
};

export type NautilusPaperRuntimeStartRequest = Omit<NautilusRuntimeStartRequest, "mode"> & {
  mode?: "paper";
};

type NautilusRuntimeListOptions = RequestOptions & {
  limit?: number;
  mode?: NautilusRuntimeMode;
};

type NautilusRuntimeEventListOptions = RequestOptions & {
  afterSequence?: number | null;
  limit?: number;
};

export class BackendClientError extends Error {
  readonly status: number;
  readonly payload: unknown;

  constructor(message: string, status: number, payload: unknown) {
    super(message);
    this.name = "BackendClientError";
    this.status = status;
    this.payload = payload;
  }
}

export type BackendApiError = BackendClientError;

export class BackendClient {
  private readonly baseUrl: string;
  private readonly userId?: string;
  private readonly workspaceId?: string;
  private readonly userTier?: string;
  private readonly workspaceRole?: string;
  private readonly internalAuthSecret?: string;
  private readonly fetcher: Fetcher;
  private readonly idempotencyKeyFactory: () => string;

  constructor(options: BackendClientOptions) {
    this.baseUrl = options.baseUrl ?? DEFAULT_API_BASE_URL;
    this.userId = options.userId;
    this.workspaceId = options.workspaceId;
    this.userTier = options.userTier;
    this.workspaceRole = options.workspaceRole;
    this.internalAuthSecret = options.internalAuthSecret;
    this.fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis);
    this.idempotencyKeyFactory =
      options.idempotencyKeyFactory ?? defaultIdempotencyKey;
  }

  health(): Promise<HealthResponse> {
    return this.request("/health", {
      responseSchema: HealthResponseSchema,
    });
  }

  ready(): Promise<ReadyResponse> {
    return this.readinessRequest("/ready");
  }

  me(): Promise<MeResponse> {
    return this.request("/v1/me", {
      responseSchema: MeResponseSchema,
    });
  }

  getProviderStatus(): Promise<ProviderStatusResponse> {
    return this.request("/v1/provider/status", {
      responseSchema: ProviderStatusResponseSchema,
    });
  }

  getActionRegistry(): Promise<ActionRegistryResponse> {
    return this.request("/v1/action-registry", {
      responseSchema: ActionRegistryResponseSchema,
    });
  }

  getAccountUsage(): Promise<AccountUsageResponse> {
    return this.request("/v1/account/usage", {
      responseSchema: AccountUsageResponseSchema,
    });
  }

  createConversation(
    payload: ConversationCreate = {},
    options: RequestOptions = {}
  ): Promise<Conversation> {
    return this.request("/v1/conversations", {
      method: "POST",
      body: ConversationCreateSchema.parse(payload),
      responseSchema: ConversationSchema,
      ...createOperationOptions(options),
    });
  }

  listConversations(): Promise<ConversationListResponse> {
    return this.request("/v1/conversations", {
      responseSchema: ConversationListResponseSchema,
    });
  }

  updateConversationTitle(
    conversationId: string,
    payload: ConversationUpdate,
    options: RequestOptions = {}
  ): Promise<Conversation> {
    return this.request(`/v1/conversations/${encodePath(conversationId)}`, {
      method: "PATCH",
      body: ConversationUpdateSchema.parse(payload),
      responseSchema: ConversationSchema,
      ...correlationOptions(options),
    });
  }

  deleteConversation(
    conversationId: string,
    options: RequestOptions = {}
  ): Promise<Conversation> {
    return this.request(`/v1/conversations/${encodePath(conversationId)}`, {
      method: "DELETE",
      responseSchema: ConversationSchema,
      ...correlationOptions(options),
    });
  }

  listConversationSidebar(): Promise<ConversationSidebarResponse> {
    return this.request("/v1/conversations/sidebar", {
      responseSchema: ConversationSidebarResponseSchema,
    });
  }

  getConversationState(
    conversationId: string
  ): Promise<ConversationStateResponse> {
    return this.request(`/v1/conversations/${encodePath(conversationId)}/state`, {
      responseSchema: ConversationStateResponseSchema,
    });
  }

  decideBacktestApproval(
    conversationId: string,
    approvalId: string,
    payload: BacktestApprovalDecisionRequest,
    options: RequestOptions = {}
  ): Promise<BacktestApprovalDecisionResponse> {
    return this.request(
      `/v1/conversations/${encodePath(
        conversationId
      )}/backtest-approvals/${encodePath(approvalId)}`,
      {
        body: payload,
        method: "POST",
        responseSchema: BacktestApprovalDecisionResponseSchema,
        ...correlationOptions(options),
      }
    );
  }

  listMessages(conversationId: string): Promise<MessageListResponse> {
    return this.request(
      `/v1/conversations/${encodePath(conversationId)}/messages`,
      {
        responseSchema: MessageListResponseSchema,
      }
    );
  }

  createMessage(
    conversationId: string,
    payload: MessageCreate,
    options: MessageCreateOptions = {}
  ): Promise<Message> {
    const query = new URLSearchParams();
    if (options.mode) {
      query.set("mode", options.mode);
    }
    return this.request(
      `/v1/conversations/${encodePath(conversationId)}/messages${queryString(
        query
      )}`,
      {
        method: "POST",
        body: MessageCreateSchema.parse(payload),
        responseSchema: MessageSchema,
        ...createOperationOptions(options),
      }
    );
  }

  streamMessage(
    conversationId: string,
    payload: MessageCreate,
    options: MessageCreateOptions = {}
  ): Promise<Response> {
    const query = new URLSearchParams({ stream: "true" });
    if (options.mode) {
      query.set("mode", options.mode);
    }
    return this.streamRequest(
      `/v1/conversations/${encodePath(conversationId)}/messages${queryString(
        query
      )}`,
      {
        method: "POST",
        body: MessageCreateSchema.parse(payload),
        signal: options.signal,
        ...createOperationOptions(options),
      }
    );
  }

  createRun(
    payload: RunCreate,
    options: RequestOptions = {}
  ): Promise<RunCreateResponse> {
    return this.request("/v1/runs", {
      method: "POST",
      body: RunCreateSchema.parse(payload),
      responseSchema: RunCreateResponseSchema,
      ...createOperationOptions(options),
    });
  }

  streamRunEvents(runId: string, options: StreamOptions = {}): Promise<Response> {
    return this.streamRequest(`/v1/runs/${encodePath(runId)}/events`, {
      signal: options.signal,
      ...streamCorrelationOptions(options),
    });
  }

  streamRunProgress(
    runId: string,
    options: StreamOptions = {}
  ): Promise<Response> {
    return this.streamRequest(`/v1/runs/${encodePath(runId)}/progress`, {
      signal: options.signal,
      ...streamCorrelationOptions(options),
    });
  }

  getRunObservability(runId: string): Promise<RunObservabilityResponse> {
    return this.request(`/v1/runs/${encodePath(runId)}/observability`, {
      responseSchema: RunObservabilityResponseSchema,
    });
  }

  cancelRun(runId: string): Promise<Run> {
    return this.request(`/v1/runs/${encodePath(runId)}/cancel`, {
      method: "POST",
      responseSchema: RunSchema,
    });
  }

  retryRun(
    runId: string,
    options: RequestOptions = {}
  ): Promise<Run> {
    return this.request(`/v1/runs/${encodePath(runId)}/retry`, {
      method: "POST",
      responseSchema: RunSchema,
      ...createOperationOptions(options),
    });
  }

  getArtifactPreview(
    artifactId: string,
    options: { maxBytes?: number } = {}
  ): Promise<ArtifactPreviewResponse> {
    const query = new URLSearchParams();
    if (options.maxBytes !== undefined) {
      query.set("max_bytes", String(options.maxBytes));
    }
    return this.request(
      `/v1/artifacts/${encodePath(artifactId)}/preview${queryString(query)}`,
      {
        responseSchema: ArtifactPreviewResponseSchema,
      }
    );
  }

  getArtifactContent(artifactId: string): Promise<ArtifactContentResponse> {
    return this.request(`/v1/artifacts/${encodePath(artifactId)}`, {
      responseSchema: ArtifactContentResponseSchema,
    });
  }

  listConversationArtifacts(
    conversationId: string,
    options: ArtifactListOptions = {}
  ): Promise<ArtifactListResponse> {
    return this.request(
      `/v1/conversations/${encodePath(conversationId)}/artifacts${artifactListQuery(options)}`,
      {
        responseSchema: ArtifactListResponseSchema,
      }
    );
  }

  listWorkspaceArtifacts(
    options: ArtifactListOptions = {}
  ): Promise<ArtifactListResponse> {
    return this.request(`/v1/artifacts${artifactListQuery(options)}`, {
      responseSchema: ArtifactListResponseSchema,
    });
  }

  createBotProposal(
    payload: BotProposalCreateRequest,
    options: RequestOptions = {}
  ): Promise<BotProposal> {
    return this.request("/v1/bots/proposals", {
      method: "POST",
      body: BotProposalCreateRequestSchema.parse(payload),
      responseSchema: BotProposalSchema,
      ...createOperationOptions(options),
    });
  }

  getBotProposal(proposalId: string): Promise<BotProposal> {
    return this.request(`/v1/bots/proposals/${encodePath(proposalId)}`, {
      responseSchema: BotProposalSchema,
    });
  }

  confirmStartBotProposal(
    proposalId: string,
    payload: BotProposalConfirmStartRequest = {},
    options: RequestOptions = {}
  ): Promise<BotProposalConfirmStartResponse> {
    return this.request(`/v1/bots/proposals/${encodePath(proposalId)}/confirm-start`, {
      method: "POST",
      body: BotProposalConfirmStartRequestSchema.parse(payload),
      responseSchema: BotProposalConfirmStartResponseSchema,
      ...createOperationOptions(options),
    });
  }

  listWorkflowTasks(conversationId: string): Promise<WorkflowTaskListResponse> {
    return this.request(`/v1/conversations/${encodePath(conversationId)}/workflow-tasks`, {
      responseSchema: WorkflowTaskListResponseSchema,
    });
  }

  submitWorkflowTaskResponse(
    taskId: string,
    payload: WorkflowTaskResponseRequest,
    options: RequestOptions = {}
  ): Promise<WorkflowTask> {
    return this.request(`/v1/workflow-tasks/${encodePath(taskId)}/responses`, {
      method: "POST",
      body: WorkflowTaskResponseRequestSchema.parse(payload),
      responseSchema: WorkflowTaskSchema,
      ...createOperationOptions(options),
    });
  }

  streamWorkflowTaskContinuation(
    taskId: string,
    payload: WorkflowTaskContinuationRequest,
    options: RequestOptions = {}
  ): Promise<Response> {
    return this.streamRequest(
      `/v1/workflow-tasks/${encodePath(taskId)}/continuations?stream=true`,
      {
        method: "POST",
        body: WorkflowTaskContinuationRequestSchema.parse(payload),
        signal: options.signal,
        ...createOperationOptions(options),
      }
    );
  }

  submitWorkflowTaskAction(
    taskId: string,
    actionId: string,
    payload: WorkflowTaskResponseRequest = { values: {}, status: "approved" },
    options: RequestOptions = {}
  ): Promise<WorkflowTask> {
    return this.request(
      `/v1/workflow-tasks/${encodePath(taskId)}/actions/${encodePath(actionId)}`,
      {
        method: "POST",
        body: WorkflowTaskResponseRequestSchema.parse(payload),
        responseSchema: WorkflowTaskSchema,
        ...createOperationOptions(options),
      }
    );
  }

  startNautilusRuntime(
    payload: NautilusPaperRuntimeStartRequest,
    options: RequestOptions = {}
  ): Promise<NautilusRuntime> {
    return this.request("/v1/nautilus/runtimes", {
      method: "POST",
      body: NautilusRuntimeStartRequestSchema.parse({
        ...payload,
        mode: "paper",
      }),
      responseSchema: NautilusRuntimeSchema,
      ...createOperationOptions(options),
    });
  }

  listNautilusRuntimes(
    options: NautilusRuntimeListOptions = {}
  ): Promise<NautilusRuntimeListResponse> {
    return this.request(`/v1/nautilus/runtimes${nautilusRuntimeListQuery(options)}`, {
      responseSchema: NautilusRuntimeListResponseSchema,
      signal: options.signal,
    });
  }

  getNautilusRuntime(runtimeId: string): Promise<NautilusRuntime> {
    return this.request(`/v1/nautilus/runtimes/${encodePath(runtimeId)}`, {
      responseSchema: NautilusRuntimeSchema,
    });
  }

  listNautilusRuntimeEvents(
    runtimeId: string,
    options: NautilusRuntimeEventListOptions = {}
  ): Promise<NautilusRuntimeEvent[]> {
    return this.request(
      `/v1/nautilus/runtimes/${encodePath(runtimeId)}/events${nautilusRuntimeEventsQuery(options)}`,
      {
        responseSchema: z.array(NautilusRuntimeEventSchema),
        signal: options.signal,
      }
    );
  }

  stopNautilusRuntime(
    runtimeId: string,
    options: RequestOptions = {}
  ): Promise<NautilusRuntime> {
    return this.request(`/v1/nautilus/runtimes/${encodePath(runtimeId)}/stop`, {
      method: "POST",
      responseSchema: NautilusRuntimeSchema,
      ...createOperationOptions(options),
    });
  }

  killSwitchNautilusRuntime(
    runtimeId: string,
    payload: NautilusRuntimeKillSwitchRequest,
    options: RequestOptions = {}
  ): Promise<NautilusRuntime> {
    return this.request(
      `/v1/nautilus/runtimes/${encodePath(runtimeId)}/kill-switch`,
      {
        method: "POST",
        body: NautilusRuntimeKillSwitchRequestSchema.parse(payload),
        responseSchema: NautilusRuntimeSchema,
        ...createOperationOptions(options),
      }
    );
  }

  getFeedbackOptions(): Promise<FeedbackOptionsResponse> {
    return this.request("/v1/feedback/options", {
      responseSchema: FeedbackOptionsResponseSchema,
    });
  }

  createFeedback(
    payload: FeedbackCreate,
    options: RequestOptions = {}
  ): Promise<Feedback> {
    return this.request("/v1/feedback", {
      method: "POST",
      body: FeedbackCreateSchema.parse(payload),
      responseSchema: FeedbackSchema,
      ...createOperationOptions(options),
    });
  }

  private async request<T>(
    path: string,
    options: JsonRequestOptions<T>
  ): Promise<T> {
    const response = await this.fetcher(this.buildUrl(path), {
      method: options.method ?? "GET",
      headers: this.headers(options),
      body:
        options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
    const payload = await readJson(response);
    if (!response.ok) {
      throw backendError(response.status, payload);
    }
    return options.responseSchema.parse(payload);
  }

  private async readinessRequest(path: string): Promise<ReadyResponse> {
    const response = await this.fetcher(this.buildUrl(path), {
      method: "GET",
      headers: this.headers(),
    });
    const payload = await readJson(response);
    const parsed = ReadyResponseSchema.safeParse(payload);
    if (parsed.success) {
      return parsed.data;
    }
    if (!response.ok) {
      throw backendError(response.status, payload);
    }
    return ReadyResponseSchema.parse(payload);
  }

  private async streamRequest(
    path: string,
    options: StreamRequestOptions = {}
  ): Promise<Response> {
    const response = await this.fetcher(this.buildUrl(path), {
      method: options.method ?? "GET",
      headers: this.headers(options),
      body:
        options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
    if (!response.ok) {
      throw backendError(response.status, await readJson(response));
    }
    return response;
  }

  private headers(options: HeaderOptions = {}): Headers {
    return buildBackendHeaders({
      ...options,
      internalAuthSecret: this.internalAuthSecret,
      idempotencyKeyFactory: this.idempotencyKeyFactory,
      userId: this.userId,
      userTier: this.userTier,
      workspaceId: this.workspaceId,
      workspaceRole: this.workspaceRole,
    });
  }

  private buildUrl(path: string): string {
    if (!this.baseUrl) {
      return path;
    }
    return `${this.baseUrl.replace(/\/$/, "")}${path}`;
  }
}

type JsonRequestOptions<T> = HeaderOptions & {
  method?: "DELETE" | "GET" | "PATCH" | "POST";
  responseSchema: z.ZodType<T>;
};

type StreamRequestOptions = HeaderOptions & {
  method?: "GET" | "POST";
};

type HeaderOptions = {
  body?: unknown;
  requestId?: string;
  traceId?: string;
  idempotencyKey?: string;
  lastEventId?: string;
  createOperation?: boolean;
  signal?: AbortSignal;
};

function correlationOptions(options: RequestOptions = {}): Pick<HeaderOptions, "idempotencyKey" | "requestId" | "traceId"> {
  return {
    idempotencyKey: options.idempotencyKey,
    requestId: options.requestId,
    traceId: options.traceId,
  };
}

function createOperationOptions(
  options: RequestOptions = {}
): Pick<HeaderOptions, "createOperation" | "idempotencyKey" | "requestId" | "traceId"> {
  return {
    ...correlationOptions(options),
    createOperation: true,
  };
}

function streamCorrelationOptions(
  options: StreamOptions = {}
): Pick<HeaderOptions, "lastEventId" | "requestId" | "traceId"> {
  return {
    lastEventId: options.lastEventId,
    requestId: options.requestId,
    traceId: options.traceId,
  };
}

export function parseBackendSseEvents(text: string): RunEvent[] {
  return parseSseJsonPayloads(text).map((payload) => RunEventSchema.parse(payload));
}

export function buildBackendHeaders(options: BackendHeaderOptions = {}): Headers {
  const headers = new Headers({
    Accept: "application/json",
  });
  if (options.userId) {
    headers.set("X-User-Id", options.userId);
  }
  if (options.workspaceId) {
    headers.set("X-Workspace-Id", options.workspaceId);
  }
  if (options.userTier) {
    headers.set("X-User-Tier", options.userTier);
  }
  if (options.workspaceRole) {
    headers.set("X-Workspace-Role", options.workspaceRole);
  }
  if (options.internalAuthSecret) {
    headers.set("X-Strategy-Codebot-Internal-Secret", options.internalAuthSecret);
  }
  if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  if (options.requestId) {
    headers.set("X-Request-Id", options.requestId);
  }
  if (options.traceId) {
    headers.set("X-Trace-Id", options.traceId);
  }
  if (options.lastEventId) {
    headers.set("Last-Event-ID", options.lastEventId);
  }
  if (options.createOperation) {
    headers.set(
      "Idempotency-Key",
      options.idempotencyKey ??
        options.idempotencyKeyFactory?.() ??
        defaultIdempotencyKey()
    );
  }
  return headers;
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) {
    return null;
  }
  return JSON.parse(text);
}

function backendError(status: number, payload: unknown): BackendClientError {
  const parsed = BackendErrorResponseSchema.safeParse(payload);
  const reason = parsed.success
    ? parsed.data.error.code
    : backendErrorDetail(payload) ?? "request_failed";
  return new BackendClientError(`Backend request failed: ${reason}`, status, payload);
}

function backendErrorDetail(payload: unknown): string | null {
  if (!payload || typeof payload !== "object" || !("detail" in payload)) {
    return null;
  }
  const detail = (payload as { detail: unknown }).detail;
  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }
  if (Array.isArray(detail)) {
    const firstMessage = detail
      .map((item) => {
        if (!item || typeof item !== "object" || !("msg" in item)) {
          return null;
        }
        const message = (item as { msg: unknown }).msg;
        return typeof message === "string" ? message : null;
      })
      .find(Boolean);
    return firstMessage ?? null;
  }
  return null;
}

function defaultIdempotencyKey(): string {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID();
  }
  return `idem_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function encodePath(value: string): string {
  return encodeURIComponent(value);
}

function queryString(params: URLSearchParams): string {
  const serialized = params.toString();
  return serialized ? `?${serialized}` : "";
}

function artifactListQuery(options: ArtifactListOptions): string {
  const query = new URLSearchParams();
  if (options.cursor) {
    query.set("cursor", options.cursor);
  }
  if (options.limit !== undefined) {
    query.set("limit", String(options.limit));
  }
  if (options.visibility) {
    query.set("visibility", options.visibility);
  }
  return queryString(query);
}

function nautilusRuntimeListQuery(options: NautilusRuntimeListOptions): string {
  const query = new URLSearchParams();
  if (options.mode) {
    query.set("mode", options.mode);
  }
  if (options.limit !== undefined) {
    query.set("limit", String(options.limit));
  }
  return queryString(query);
}

function nautilusRuntimeEventsQuery(options: NautilusRuntimeEventListOptions): string {
  const query = new URLSearchParams();
  if (options.afterSequence !== null && options.afterSequence !== undefined) {
    query.set("after_sequence", String(options.afterSequence));
  }
  if (options.limit !== undefined) {
    query.set("limit", String(options.limit));
  }
  return queryString(query);
}
