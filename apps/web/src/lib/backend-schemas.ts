import { z } from "zod";
import {
  BACKTEST_EXECUTABLE_TIMEFRAMES,
  BACKTEST_MAX_COST_BPS,
  BACKTEST_OHLCV_DEFAULT_EXCHANGE,
  BACKTEST_OHLCV_EXCHANGES,
  BACKTEST_RUN_EVENTS as GENERATED_BACKTEST_RUN_EVENTS,
} from "./backtest-ohlcv-contract";

export const JsonValueSchema: z.ZodType<unknown> = z.lazy(() =>
  z.union([
    z.string(),
    z.number(),
    z.boolean(),
    z.null(),
    z.array(JsonValueSchema),
    z.record(z.string(), JsonValueSchema),
  ])
);

export const JsonObjectSchema = z.record(z.string(), JsonValueSchema);
export const IsoDateTimeSchema = z.string().datetime({ offset: true });
export const IdSchema = z.string().min(1);

export const HealthResponseSchema = z.object({
  status: z.literal("ok"),
  service: z.literal("strategy-codebot-api"),
  version: z.string().min(1),
});

export const ReadinessCheckSchema = z
  .object({
    status: z.string().min(1),
  })
  .catchall(JsonValueSchema);

export const ReadyResponseSchema = z.object({
  status: z.enum(["ok", "unavailable"]),
  checks: z.record(z.string(), ReadinessCheckSchema),
});

export const TierSchema = z.enum(["free", "paid_low", "paid_medium", "paid_high"]);
export const MessageModeSchema = z.enum(["deterministic", "agent", "workflow_task_continuation"]);
export const LanguagePreferenceSchema = z.enum(["en", "vi"]);
export const RunModeSchema = z.enum(["dry-run", "agent", "live-generation", "backtest-preview"]);
export const CapabilityModeStatusSchema = z.object({
  status: z.enum(["available", "degraded", "blocked"]),
  reason_codes: z.array(z.string()).default([]),
  missing_components: z.array(z.string()).default([]),
});

export const WorkspaceCapabilitySchema = z.object({
  user_id: IdSchema,
  workspace_id: IdSchema,
  role: z.string().min(1),
  tier: TierSchema,
  tier_label: z.string().min(1),
  allowed_message_modes: z.array(MessageModeSchema),
  allowed_run_modes: z.array(RunModeSchema),
  capability_matrix: z.record(z.string(), CapabilityModeStatusSchema).default({}),
});

export const MeResponseSchema = z.object({
  user: z.object({ id: IdSchema }).catchall(z.string()),
  workspace: z.object({ id: IdSchema, role: z.string().min(1) }).catchall(z.string()),
  capability: WorkspaceCapabilitySchema,
});

export const ProviderStatusResponseSchema = z.object({
  configured: z.boolean(),
  available: z.boolean(),
  tier: TierSchema,
  tier_label: z.string().min(1),
  allowed_message_modes: z.array(MessageModeSchema),
  allowed_run_modes: z.array(RunModeSchema),
  capability_matrix: z.record(z.string(), CapabilityModeStatusSchema).default({}),
  fallback_mode: z.enum(["deterministic"]),
  model_routing_mode: z.string().min(1).default("registry"),
  model_tier: z.string().nullable().optional(),
  selected_stage_defaults: z.record(z.string(), z.string()).default({}),
  available_gateways: z.array(z.string()).default([]),
  route_ready: z.boolean().optional(),
  fallback_enabled: z.boolean().optional(),
  user_message: z.string().nullable().optional(),
  status: z.string().min(1),
  reason: z.string().nullable().optional(),
});

export const ActionRegistryEntrySchema = z.object({
  id: z.string().min(1),
  tool_id: z.string().min(1),
  label: z.string().min(1),
  prompt: z.string().min(1),
  category: z.string().min(1),
  risk_level: z.string().min(1),
  next_state: z.string().min(1),
  artifact_kind: z.string().min(1).optional(),
  available: z.boolean(),
  disabled_reason: z.string().min(1).optional(),
  presentation: z.object({
    badge_key: z.string().min(1).optional(),
    icon_key: z.string().min(1).optional(),
    visibility_key: z.string().min(1).optional(),
  }),
  required_inputs: z.array(z.string().min(1)).optional(),
});

export const ActionRegistryResponseSchema = z.object({
  version: z.number().int().positive(),
  actions: z.array(ActionRegistryEntrySchema),
});

export const AccountUsageResponseSchema = z.object({
  tier: TierSchema,
  tier_label: z.string().min(1),
  period_start: IsoDateTimeSchema,
  period_end: IsoDateTimeSchema,
  messages: z.number().int().nonnegative(),
  runs: z.number().int().nonnegative(),
  artifacts: z.number().int().nonnegative(),
  input_tokens: z.number().int().nonnegative(),
  output_tokens: z.number().int().nonnegative(),
  total_tokens: z.number().int().nonnegative(),
  estimated_cost_usd: z.number().nullable(),
});

const normalizeConversationTitle = (title: string) => {
  const normalized = title.trim();
  return normalized.length > 0 ? normalized : null;
};

export const ConversationCreateSchema = z.object({
  title: z
    .string()
    .max(160)
    .transform(normalizeConversationTitle)
    .nullable()
    .optional(),
});

export const ConversationUpdateSchema = z.object({
  title: z.string().max(160).transform((title, ctx) => {
    const normalized = normalizeConversationTitle(title);
    if (normalized === null) {
      ctx.addIssue({
        code: "custom",
        message: "title must not be blank",
      });
      return z.NEVER;
    }
    return normalized;
  }),
});

export const ConversationSchema = z.object({
  id: IdSchema,
  owner_user_id: IdSchema,
  workspace_id: IdSchema,
  title: z.string().nullable(),
  created_at: IsoDateTimeSchema,
  updated_at: IsoDateTimeSchema,
});

export const ConversationListResponseSchema = z.object({
  items: z.array(ConversationSchema),
});

export const WebSearchModeSchema = z.enum(["off", "auto", "on"]);

export const MessageCreateSchema = z.object({
  content: z.string().min(1).refine((value) => value.trim().length > 0, {
    message: "content must not be blank",
  }),
  language: LanguagePreferenceSchema.optional(),
  web_search: WebSearchModeSchema.default("auto"),
});

export const MessageRoleSchema = z.enum(["user", "assistant", "system", "tool"]);

export const AuthHeadersSchema = z.object({
  userId: IdSchema,
  workspaceId: IdSchema,
});

export const MessageSchema = z.object({
  id: IdSchema,
  conversation_id: IdSchema,
  owner_user_id: IdSchema,
  workspace_id: IdSchema,
  role: MessageRoleSchema,
  content: z.string(),
  created_at: IsoDateTimeSchema,
});

export const MessageListResponseSchema = z.object({
  items: z.array(MessageSchema),
});

export const RunStatusSchema = z.enum([
  "queued",
  "running",
  "completed",
  "failed",
  "blocked",
  "cancelled",
]);

export const ArtifactSchema = z.object({
  id: IdSchema,
  run_id: IdSchema.nullable(),
  conversation_id: IdSchema.nullable(),
  owner_user_id: IdSchema,
  workspace_id: IdSchema,
  kind: z.string().min(1),
  mime_type: z.string().nullable(),
  display_name: z.string().min(1),
  metadata_json: JsonObjectSchema.nullable(),
  visibility: z.enum(["user", "internal"]).nullable().optional(),
  category: z.enum(["code", "report", "evidence", "trace", "other"]).nullable().optional(),
  presentation: z.object({
    dedupe_key: z.string().min(1),
    is_primary: z.boolean(),
    language_hint: z.string().min(1).nullable(),
    user_kind: z.enum(["code", "dashboard", "report", "risk", "validation", "evidence", "raw"]),
    viewer_kind: z.enum(["code", "backtest_dashboard", "backtest_plan", "backtest_report", "trades", "json"]),
    visibility: z.enum(["user", "internal"]),
  }),
  preview_summary: JsonObjectSchema.nullable().default(null),
  created_at: IsoDateTimeSchema,
});

export const ArtifactContentResponseSchema = ArtifactSchema.extend({
  content: JsonValueSchema,
});

export const ArtifactPreviewResponseSchema = ArtifactSchema.extend({
  preview: JsonValueSchema,
  raw_available: z.boolean(),
  truncated: z.boolean(),
  line_count: z.number().int().nonnegative().nullable(),
  language: z.string().nullable(),
});

export const ArtifactListResponseSchema = z.object({
  items: z.array(ArtifactSchema),
  next_cursor: z.string().min(1).nullable(),
});

export const NautilusRuntimeStateSchema = z.enum([
  "requested",
  "provisioning",
  "warming_up",
  "running",
  "degraded",
  "stopping",
  "stopped",
  "failed",
]);

export const NautilusRuntimeDesiredStateSchema = z.enum([
  "requested",
  "running",
  "stopping",
  "stopped",
]);

export const NautilusRuntimeModeSchema = z.enum(["paper", "live"]);

export const NautilusRuntimeStartRequestSchema = z.object({
  broker_connection_id: z.string().min(1).max(120),
  account_id: z.string().min(1).max(120),
  mode: NautilusRuntimeModeSchema.default("paper"),
  risk_policy_id: z.string().min(1).max(120),
  strategy_id: z.string().min(1).max(160),
  manifest: JsonObjectSchema.default({}),
  data_subscriptions: z.array(JsonObjectSchema).max(100).default([]),
});

export const NautilusRuntimeKillSwitchRequestSchema = z.object({
  reason: z.string().min(1).max(500),
});

export const NautilusRuntimeEventSchema = z.object({
  event_id: IdSchema,
  runtime_id: IdSchema,
  sequence: z.number().int().nonnegative(),
  type: z.string().min(1),
  payload: JsonObjectSchema.nullable(),
  created_at: IsoDateTimeSchema,
});

export const NautilusRuntimeSchema = z.object({
  id: IdSchema,
  runtime_key: z.string().min(1),
  broker_connection_id: z.string().min(1),
  account_id: z.string().min(1),
  mode: NautilusRuntimeModeSchema,
  risk_policy_id: z.string().min(1),
  state: NautilusRuntimeStateSchema,
  strategy_ids: z.array(z.string().min(1)),
  manifest: JsonObjectSchema,
  data_subscriptions: z.array(JsonObjectSchema),
  last_heartbeat_at: IsoDateTimeSchema.nullable().default(null),
  heartbeat_count: z.number().int().nonnegative().default(0),
  heartbeat_metrics: JsonObjectSchema.nullable().default(null),
  last_heartbeat_event_at: IsoDateTimeSchema.nullable().default(null),
  kill_switch_active: z.boolean(),
  desired_state: NautilusRuntimeDesiredStateSchema.default("running"),
  worker_id: z.string().nullable().default(null),
  lease_until: IsoDateTimeSchema.nullable().default(null),
  generation: z.number().int().nonnegative().default(0),
  started_at: IsoDateTimeSchema.nullable().default(null),
  stopped_at: IsoDateTimeSchema.nullable().default(null),
  last_error: JsonObjectSchema.nullable().default(null),
  stream_cursor: JsonObjectSchema.nullable().default(null),
  created_at: IsoDateTimeSchema,
  updated_at: IsoDateTimeSchema,
});

export const NautilusRuntimeListResponseSchema = z.object({
  items: z.array(NautilusRuntimeSchema),
});

export const BOT_PROPOSAL_STATUSES = [
  "draft",
  "missing_inputs",
  "ready",
  "started",
  "rejected",
] as const;

export const BotProposalCreateRequestSchema = z.object({
  strategy_artifact_id: z.string().min(1).max(120).optional(),
  run_id: z.string().min(1).max(120).optional(),
  broker_connection_id: z.string().max(120).optional(),
  account_id: z.string().max(120).optional(),
  risk_policy_id: z.string().max(120).optional(),
  strategy_id: z.string().max(160).optional(),
  strategy_name: z.string().max(240).optional(),
  manifest: JsonObjectSchema.default({}),
  data_subscriptions: z.array(JsonObjectSchema).max(100).default([]),
  readiness_checks: z.array(z.string()).max(50).default([]),
});

export const BotProposalSchema = z.object({
  id: IdSchema,
  status: z.enum(BOT_PROPOSAL_STATUSES),
  source_conversation_id: IdSchema.nullable().default(null),
  source_run_id: IdSchema.nullable().default(null),
  source_artifact_ids: z.array(IdSchema).default([]),
  strategy_id: z.string().min(1),
  strategy_name: z.string().min(1),
  manifest: JsonObjectSchema,
  data_subscriptions: z.array(JsonObjectSchema),
  broker_connection_id: z.string().nullable().default(null),
  account_id: z.string().nullable().default(null),
  risk_policy_id: z.string().nullable().default(null),
  readiness_checks: z.array(z.string()).default([]),
  missing_inputs: z.array(z.string()).default([]),
  runtime_id: IdSchema.nullable().default(null),
  created_at: IsoDateTimeSchema,
  updated_at: IsoDateTimeSchema,
});

export const BotProposalConfirmStartRequestSchema = z.object({
  broker_connection_id: z.string().max(120).optional(),
  account_id: z.string().max(120).optional(),
  risk_policy_id: z.string().max(120).optional(),
});

export const BotProposalConfirmStartResponseSchema = z.object({
  proposal: BotProposalSchema,
  runtime: NautilusRuntimeSchema,
});

export const WorkflowTaskResponseRequestSchema = z.object({
  values: JsonObjectSchema.default({}),
  action_id: z.string().min(1).max(120).optional(),
  status: z.enum(["completed", "approved", "rejected", "cancelled", "blocked"]).default("completed"),
});

export const WorkflowTaskContinuationStateSchema = z.object({
  required: z.boolean().default(false),
  task_id: IdSchema,
  workflow_id: z.string().min(1),
  task_template_id: z.string().min(1),
  resume_intent: z.string().nullable().default(null),
  reason: z.string().nullable().default(null),
});

export const WorkflowTaskContinuationRequestSchema = z.object({
  language: z.string().default("en"),
  web_search: z.enum(["off", "auto", "on"]).default("auto"),
});

export const WorkflowTaskSchema = z.object({
  id: IdSchema,
  workflow_id: z.string().min(1),
  task_template_id: z.string().min(1),
  step_id: z.string().min(1),
  kind: z.string().min(1),
  status: z.string().min(1),
  title: z.string().min(1),
  blocking: z.boolean(),
  input_request_ids: z.array(z.string()).default([]),
  action_ids: z.array(z.string()).default([]),
  input_requests: z.array(JsonObjectSchema).default([]),
  actions: z.array(JsonObjectSchema).default([]),
  values: JsonObjectSchema.default({}),
  response: JsonObjectSchema.nullable().default(null),
  reason: z.string().nullable().default(null),
  continuation: WorkflowTaskContinuationStateSchema.nullable().default(null),
  created_at: IsoDateTimeSchema,
  updated_at: IsoDateTimeSchema,
  resolved_at: IsoDateTimeSchema.nullable().default(null),
});

export const WorkflowTaskListResponseSchema = z.object({
  items: z.array(WorkflowTaskSchema),
});

export const ConversationSidebarItemSchema = z.object({
  conversation: ConversationSchema,
  last_message_preview: z.string().nullable(),
  last_message_at: IsoDateTimeSchema.nullable(),
  message_count: z.number().int().nonnegative(),
  latest_run_id: IdSchema.nullable(),
  latest_run_status: RunStatusSchema.nullable(),
  updated_at: IsoDateTimeSchema,
});

export const ConversationSidebarResponseSchema = z.object({
  items: z.array(ConversationSidebarItemSchema),
});

export const StrategySpecSchema = z
  .object({
    target_platform: z.enum(["pine_v6", "mql5", "both"]),
    script_type: z.enum(["indicator", "strategy", "expert_advisor"]),
    market: z.string().min(1),
    symbol: z.string().optional(),
    timeframe: z.string().min(1),
    entry_rules: z.array(z.string().min(1)).min(1),
    exit_rules: z.array(z.string().min(1)).min(1),
    risk_rules: z.array(z.string().min(1)).min(1),
    position_sizing: z.string().optional(),
    stop_loss: z.string().optional(),
    take_profit: z.string().optional(),
    assumptions: z.array(z.string()).optional(),
    constraints: z.array(z.string()).optional(),
    user_notes: z.string().optional(),
  })
  .strict();

export const BacktestConfigSchema = z.object({
  engine: z.literal("pineforge").default("pineforge"),
  exchange: z.enum(BACKTEST_OHLCV_EXCHANGES).default(BACKTEST_OHLCV_DEFAULT_EXCHANGE),
  symbol: z.string().trim().min(1).max(64),
  timeframe: z.enum(BACKTEST_EXECUTABLE_TIMEFRAMES),
  candle_timeframe: z.literal("1m").default("1m"),
  start: z.string().min(1),
  end: z.string().min(1),
  initial_capital: z.number().finite().positive(),
  fee_bps: z.number().finite().nonnegative().max(BACKTEST_MAX_COST_BPS).default(0),
  slippage_bps: z.number().finite().nonnegative().max(BACKTEST_MAX_COST_BPS).default(0),
  data_source: z.literal("public-readonly-cache").default("public-readonly-cache"),
}).refine((value) => {
  const start = Date.parse(value.start);
  const end = Date.parse(value.end);
  return Number.isFinite(start) && Number.isFinite(end) && end > start;
}, "end must be after start");

export const BacktestApprovalDecisionRequestSchema = z.object({
  decision: z.enum(["approved", "rejected"]),
});

export const BacktestApprovalDecisionResponseSchema = z.object({
  approval_id: IdSchema,
  conversation_id: IdSchema,
  decision: z.enum(["approved", "rejected"]),
  status: z.enum(["queued", "rejected"]),
  run_id: IdSchema.nullable().optional(),
  job_id: IdSchema.nullable().optional(),
  backtest_config: JsonObjectSchema.nullable().optional(),
});

export const RunCreateSchema = z
  .object({
    conversation_id: IdSchema,
    strategy_spec: StrategySpecSchema,
    pine_code: z.string().optional(),
    mode: RunModeSchema.default("dry-run"),
    web_search: WebSearchModeSchema.default("auto"),
    backtest_config: BacktestConfigSchema.optional(),
  })
  .superRefine((value, context) => {
    if (value.mode === "backtest-preview" && !value.backtest_config) {
      context.addIssue({
        code: "custom",
        message: "backtest_config is required when mode is backtest-preview",
        path: ["backtest_config"],
      });
    }
    if (value.mode !== "backtest-preview" && value.backtest_config) {
      context.addIssue({
        code: "custom",
        message: "backtest_config is only supported when mode is backtest-preview",
        path: ["backtest_config"],
      });
    }
    if (
      value.mode === "backtest-preview" &&
      value.backtest_config?.engine === "pineforge" &&
      (!value.pine_code || value.pine_code.trim().length === 0)
    ) {
      context.addIssue({
        code: "custom",
        message: "Local preview requires PineScript v6 strategy source",
        path: ["pine_code"],
      });
    }
  });

export const RunSchema = z.object({
  id: IdSchema,
  conversation_id: IdSchema,
  owner_user_id: IdSchema,
  workspace_id: IdSchema,
  status: RunStatusSchema,
  mode: RunModeSchema.nullable().optional(),
  created_at: IsoDateTimeSchema,
  updated_at: IsoDateTimeSchema,
  retry_of_run_id: IdSchema.nullable(),
  request_id: z.string().nullable(),
  trace_id: z.string().nullable(),
});

export const RunCreateResponseSchema = RunSchema.extend({
  artifacts: z.array(ArtifactSchema),
});

export const FeedbackTargetSchema = z
  .object({
    conversation_id: IdSchema,
    message_ids: z.array(IdSchema),
    latest_run_id: IdSchema.nullable(),
    artifact_ids: z.array(IdSchema),
    ratings: z.array(z.string().min(1)),
    categories: z.array(z.string().min(1)),
  })
  .catchall(JsonValueSchema);

export const FeedbackRatingSchema = z.enum(["up", "down", "neutral"]);

export const FeedbackCreateSchema = z.object({
  conversation_id: IdSchema,
  run_id: IdSchema.nullable().optional(),
  message_id: IdSchema.nullable().optional(),
  artifact_id: IdSchema.nullable().optional(),
  rating: FeedbackRatingSchema,
  category: z.string().max(80).nullable().optional(),
  correction: z.string().min(1).refine((value) => value.trim().length > 0, {
    message: "correction must not be blank",
  }),
});

export const FeedbackSchema = z.object({
  id: IdSchema,
  conversation_id: IdSchema,
  run_id: IdSchema.nullable(),
  message_id: IdSchema.nullable(),
  artifact_id: IdSchema.nullable(),
  owner_user_id: IdSchema,
  workspace_id: IdSchema,
  request_id: z.string().nullable(),
  trace_id: z.string().nullable(),
  rating: FeedbackRatingSchema,
  category: z.string().nullable(),
  created_at: IsoDateTimeSchema,
});

export const FeedbackOptionSchema = z.object({
  value: z.string().min(1),
  label: z.string().min(1),
});

export const FeedbackOptionsResponseSchema = z.object({
  ratings: z.array(FeedbackOptionSchema),
  categories: z.array(FeedbackOptionSchema),
});

export const BACKTEST_RUN_EVENTS = GENERATED_BACKTEST_RUN_EVENTS;

export const BACKTEST_PROGRESS_EVENT_TYPES = [
  BACKTEST_RUN_EVENTS.dataPlanning,
  BACKTEST_RUN_EVENTS.dataCacheReusing,
  BACKTEST_RUN_EVENTS.dataFetching,
  BACKTEST_RUN_EVENTS.dataExporting,
  BACKTEST_RUN_EVENTS.indexingStarted,
] as const;

export const BACKTEST_RUN_EVENT_TYPES = [
  BACKTEST_RUN_EVENTS.queued,
  BACKTEST_RUN_EVENTS.dataStarted,
  ...BACKTEST_PROGRESS_EVENT_TYPES,
  BACKTEST_RUN_EVENTS.dataCompleted,
  BACKTEST_RUN_EVENTS.executionStarted,
  BACKTEST_RUN_EVENTS.executionCompleted,
  BACKTEST_RUN_EVENTS.reportCompleted,
  BACKTEST_RUN_EVENTS.failed,
] as const;

export const CHAT_AUTO_CHAIN_EVENT_TYPES = [
  "chat.auto_chain.started",
  "chat.auto_chain.step.completed",
  "chat.auto_chain.waiting_for_backtest",
  "chat.auto_chain.summary.completed",
  "chat.auto_chain.failed",
] as const;

export const BACKTEST_PREVIEW_APPROVAL_EVENT_TYPES = [
  "backtest.preview.approval_required",
  "backtest.preview.approved",
  "backtest.preview.rejected",
  "backtest.preview.queued",
  "backtest.preview.failed",
  "backtest.preview.heartbeat",
] as const;

export const PROMPT_CHAIN_RUN_EVENT_TYPES = [
  "prompt_chain.started",
  "prompt_chain.stage_completed",
  "prompt_chain.completed",
  "prompt_chain.fallback",
  "prompt_chain.failed",
] as const;

export const EVALUATOR_OPTIMIZER_RUN_EVENT_TYPES = ["evaluator_optimizer.summary"] as const;

export const AGENT_LOOP_RUN_EVENT_TYPES = [
  "agent_loop.started",
  "agent_loop.llm_completed",
  "agent_loop.tool_checked",
  "agent_loop.completed",
] as const;

export const AGENT_WORKFLOW_OBSERVABILITY_EVENT_TYPES = [
  ...PROMPT_CHAIN_RUN_EVENT_TYPES,
  ...EVALUATOR_OPTIMIZER_RUN_EVENT_TYPES,
  ...AGENT_LOOP_RUN_EVENT_TYPES,
] as const;

export const WORKFLOW_CONTINUATION_EVENT_TYPES = [
  "workflow.continuation.required",
  "workflow.continuation.started",
  "workflow.continuation.completed",
  "workflow.continuation.failed",
] as const;

export const KNOWN_RUN_EVENT_TYPES = [
  "message.delta",
  "provider.started",
  "provider.route",
  "provider.waiting",
  "provider.retrying",
  "classifier.started",
  "classifier.route",
  "classifier.completed",
  "classifier.timeout",
  "classifier.failed",
  "model.reasoning.delta",
  "model.usage",
  "tool.started",
  "tool.completed",
  "validation.completed",
  "review.completed",
  "artifact.created",
  "knowledge.candidate.created",
  "knowledge.candidate.approved",
  "knowledge.candidate.auto_reviewed",
  "knowledge.candidate.auto_approved",
  "knowledge.candidate.needs_review",
  "knowledge.candidate.auto_rejected",
  "knowledge.candidate.rejected",
  "knowledge.learning.completed",
  "knowledge.learning.failed",
  "model_action.proposed",
  "model_action.validated",
  "model_action.rejected",
  "model_action.executed",
  ...AGENT_WORKFLOW_OBSERVABILITY_EVENT_TYPES,
  "policy.blocked",
  "workflow.gate.required",
  "workflow.gate.confirmed",
  "workflow.gate.rejected",
  ...WORKFLOW_CONTINUATION_EVENT_TYPES,
  "observability.stage.completed",
  "progress.snapshot",
  "progress.update",
  "stage.started",
  "stage.completed",
  ...CHAT_AUTO_CHAIN_EVENT_TYPES,
  ...BACKTEST_PREVIEW_APPROVAL_EVENT_TYPES,
  ...BACKTEST_RUN_EVENT_TYPES,
  "run.completed",
  "run.failed",
  "run.cancelled",
] as const;

export type KnownRunEventType = (typeof KNOWN_RUN_EVENT_TYPES)[number];

export const RunEventTypeSchema = z.union([
  z.enum(KNOWN_RUN_EVENT_TYPES),
  z.string().min(1),
]);

export const RunEventSchema = z.object({
  event_id: IdSchema,
  conversation_id: IdSchema,
  run_id: IdSchema,
  request_id: z.string().nullable(),
  trace_id: z.string().nullable(),
  sequence: z.number().int().nonnegative(),
  type: RunEventTypeSchema,
  payload: JsonObjectSchema.nullable(),
  created_at: IsoDateTimeSchema,
});

export const StrategyBriefSchema = z.object({
  market: z.string().nullable().optional(),
  symbol: z.string().nullable().optional(),
  timeframe: z.string().nullable().optional(),
  platform: z.string().nullable().optional(),
  strategy_type: z.string().nullable().optional(),
  entry_rules: z.array(z.string()),
  exit_rules: z.array(z.string()),
  risk_rules: z.array(z.string()),
});

export const StrategySnapshotSchema = z.object({
  completeness: z.enum(["draft", "needs_risk", "ready_for_artifact"]),
  missing_fields: z.array(z.string()),
  next_actions: z.array(z.string()),
  boundary_flags: z.array(z.string()),
});

export const StrategyAssumptionsSchema = z.object({
  confirmed: z.array(z.string()),
  open_questions: z.array(z.string()),
  constraints: z.array(z.string()),
});

export const StrategyMemorySchema = z.object({
  has_context: z.boolean(),
  summary: z.string().nullable().optional(),
  last_artifact_id: z.string().nullable().optional(),
  open_questions: z.array(z.string()),
});

export const StrategyCodeOutlineItemSchema = z.object({
  id: IdSchema,
  label: z.string().min(1),
  kind: z.string().min(1),
  artifact_id: IdSchema.nullable().optional(),
  anchor: z.string().nullable().optional(),
});

export const StrategyProfileSchema = z.object({
  source: z.enum(["strategy_spec", "conversation"]),
  updated_at: IsoDateTimeSchema.nullable().optional(),
  brief: StrategyBriefSchema,
  snapshot: StrategySnapshotSchema,
  assumptions: StrategyAssumptionsSchema,
  memory: StrategyMemorySchema,
  code_outline: z.array(StrategyCodeOutlineItemSchema),
});

export const ConversationStateResponseSchema = z.object({
  conversation: ConversationSchema,
  messages: z.array(MessageSchema),
  message_count: z.number().int().nonnegative(),
  messages_truncated: z.boolean(),
  message_limit: z.number().int().nonnegative(),
  latest_run: RunSchema.nullable(),
  latest_run_artifacts: z.array(ArtifactSchema),
  conversation_artifacts: z.array(ArtifactSchema).default([]),
  conversation_artifacts_next_cursor: z.string().min(1).nullable().default(null),
  latest_run_events: z.array(RunEventSchema),
  conversation_run_events: z.array(RunEventSchema).default([]),
  feedback_targets: FeedbackTargetSchema,
  strategy_profile: StrategyProfileSchema.nullable().optional(),
  pending_workflow_continuation: WorkflowTaskContinuationStateSchema.nullable().default(null),
});

export const ObservabilityToolCallSchema = z.object({
  id: IdSchema,
  tool_id: z.string().min(1),
  status: z.string().min(1),
  created_at: IsoDateTimeSchema,
  started_at: IsoDateTimeSchema.nullable(),
  completed_at: IsoDateTimeSchema.nullable(),
});

export const ObservabilityPolicyFindingSchema = z.object({
  id: IdSchema,
  severity: z.string().min(1),
  code: z.string().min(1),
  message: z.string(),
  created_at: IsoDateTimeSchema,
});

export const ObservabilityUsageSchema = z.object({
  input_tokens: z.number().nonnegative(),
  output_tokens: z.number().nonnegative(),
  total_tokens: z.number().nonnegative(),
  cost_estimate_usd: z.number().nonnegative(),
});

export const RunObservabilityResponseSchema = z
  .object({
    request_id: z.string().nullable(),
    conversation_id: IdSchema,
    run_id: IdSchema,
    trace_id: z.string().nullable(),
    status: RunStatusSchema,
    event_count: z.number().int().nonnegative(),
    artifact_count: z.number().int().nonnegative(),
    tool_calls: z.array(ObservabilityToolCallSchema),
    policy_findings: z.array(ObservabilityPolicyFindingSchema),
    usage: ObservabilityUsageSchema,
    latency_by_stage: z.record(z.string(), z.number().int().nonnegative()),
    harness_evidence_artifact_id: IdSchema.optional(),
  })
  .catchall(JsonValueSchema);

export const BackendErrorResponseSchema = z.object({
  error: z
    .object({
      code: z.string().min(1),
      dimension: z.string().nullable().optional(),
      retry_after_seconds: z.number().nullable().optional(),
    })
    .catchall(JsonValueSchema),
});

export type JsonValue = z.infer<typeof JsonValueSchema>;
export type JsonObject = z.infer<typeof JsonObjectSchema>;
export type HealthResponse = z.infer<typeof HealthResponseSchema>;
export type ReadyResponse = z.infer<typeof ReadyResponseSchema>;
export type Tier = z.infer<typeof TierSchema>;
export type WorkspaceCapability = z.infer<typeof WorkspaceCapabilitySchema>;
export type MeResponse = z.infer<typeof MeResponseSchema>;
export type ProviderStatusResponse = z.infer<typeof ProviderStatusResponseSchema>;
export type ActionRegistryEntry = z.infer<typeof ActionRegistryEntrySchema>;
export type ActionRegistryResponse = z.infer<typeof ActionRegistryResponseSchema>;
export type AccountUsageResponse = z.infer<typeof AccountUsageResponseSchema>;
export type ConversationCreate = z.input<typeof ConversationCreateSchema>;
export type ConversationUpdate = z.input<typeof ConversationUpdateSchema>;
export type Conversation = z.infer<typeof ConversationSchema>;
export type ConversationListResponse = z.infer<
  typeof ConversationListResponseSchema
>;
export type MessageCreate = z.infer<typeof MessageCreateSchema>;
export type WebSearchMode = z.infer<typeof WebSearchModeSchema>;
export type MessageRole = z.infer<typeof MessageRoleSchema>;
export type MessageMode = z.infer<typeof MessageModeSchema>;
export type LanguagePreference = z.infer<typeof LanguagePreferenceSchema>;
export type AuthHeaders = z.infer<typeof AuthHeadersSchema>;
export type Message = z.infer<typeof MessageSchema>;
export type MessageListResponse = z.infer<typeof MessageListResponseSchema>;
export type ConversationSidebarItem = z.infer<
  typeof ConversationSidebarItemSchema
>;
export type ConversationSidebarResponse = z.infer<
  typeof ConversationSidebarResponseSchema
>;
export type Artifact = z.infer<typeof ArtifactSchema>;
export type ArtifactContentResponse = z.infer<
  typeof ArtifactContentResponseSchema
>;
export type ArtifactPreviewResponse = z.infer<
  typeof ArtifactPreviewResponseSchema
>;
export type ArtifactListResponse = z.infer<typeof ArtifactListResponseSchema>;
export type NautilusRuntimeMode = z.infer<typeof NautilusRuntimeModeSchema>;
export type NautilusRuntimeState = z.infer<typeof NautilusRuntimeStateSchema>;
export type NautilusRuntimeStartRequest = z.input<
  typeof NautilusRuntimeStartRequestSchema
>;
export type NautilusRuntimeKillSwitchRequest = z.infer<
  typeof NautilusRuntimeKillSwitchRequestSchema
>;
export type NautilusRuntimeEvent = z.infer<typeof NautilusRuntimeEventSchema>;
export type NautilusRuntime = z.infer<typeof NautilusRuntimeSchema>;
export type NautilusRuntimeListResponse = z.infer<
  typeof NautilusRuntimeListResponseSchema
>;
export type BotProposalCreateRequest = z.input<
  typeof BotProposalCreateRequestSchema
>;
export type BotProposal = z.infer<typeof BotProposalSchema>;
export type BotProposalConfirmStartRequest = z.input<
  typeof BotProposalConfirmStartRequestSchema
>;
export type BotProposalConfirmStartResponse = z.infer<
  typeof BotProposalConfirmStartResponseSchema
>;
export type WorkflowTaskResponseRequest = z.input<
  typeof WorkflowTaskResponseRequestSchema
>;
export type WorkflowTaskContinuationRequest = z.input<
  typeof WorkflowTaskContinuationRequestSchema
>;
export type WorkflowTaskContinuationState = z.infer<
  typeof WorkflowTaskContinuationStateSchema
>;
export type WorkflowTask = z.infer<typeof WorkflowTaskSchema>;
export type WorkflowTaskListResponse = z.infer<
  typeof WorkflowTaskListResponseSchema
>;
export type RunMode = z.infer<typeof RunModeSchema>;
export type BacktestConfig = z.infer<typeof BacktestConfigSchema>;
export type BacktestApprovalDecisionRequest = z.infer<
  typeof BacktestApprovalDecisionRequestSchema
>;
export type BacktestApprovalDecisionResponse = z.infer<
  typeof BacktestApprovalDecisionResponseSchema
>;
export type StrategySpec = z.infer<typeof StrategySpecSchema>;
export type StrategyProfile = z.infer<typeof StrategyProfileSchema>;
export type RunCreate = z.input<typeof RunCreateSchema>;
export type RunStatus = z.infer<typeof RunStatusSchema>;
export type Run = z.infer<typeof RunSchema>;
export type RunCreateResponse = z.infer<typeof RunCreateResponseSchema>;
export type ConversationStateResponse = z.infer<
  typeof ConversationStateResponseSchema
>;
export type FeedbackRating = z.infer<typeof FeedbackRatingSchema>;
export type FeedbackCreate = z.infer<typeof FeedbackCreateSchema>;
export type Feedback = z.infer<typeof FeedbackSchema>;
export type FeedbackOption = z.infer<typeof FeedbackOptionSchema>;
export type FeedbackOptionsResponse = z.infer<
  typeof FeedbackOptionsResponseSchema
>;
export type RunEvent = z.infer<typeof RunEventSchema>;
export type RunObservabilityResponse = z.infer<
  typeof RunObservabilityResponseSchema
>;
export type BackendErrorResponse = z.infer<typeof BackendErrorResponseSchema>;
