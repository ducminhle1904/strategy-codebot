import { z } from "zod";

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
export const MessageModeSchema = z.enum(["deterministic", "agent"]);
export const LanguagePreferenceSchema = z.enum(["en", "vi"]);
export const RunModeSchema = z.enum(["dry-run", "agent", "live-generation", "backtest-preview"]);

export const WorkspaceCapabilitySchema = z.object({
  user_id: IdSchema,
  workspace_id: IdSchema,
  role: z.string().min(1),
  tier: TierSchema,
  tier_label: z.string().min(1),
  allowed_message_modes: z.array(MessageModeSchema),
  allowed_run_modes: z.array(RunModeSchema),
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
  fallback_mode: z.enum(["deterministic"]),
  status: z.string().min(1),
  reason: z.string().nullable().optional(),
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
  engine: z.literal("backtest-kit").default("backtest-kit"),
  symbol: z.string().min(1),
  timeframe: z.string().min(1),
  start: z.string().min(1),
  end: z.string().min(1),
  initial_capital: z.number().positive(),
  fee_bps: z.number().nonnegative().default(0),
  slippage_bps: z.number().nonnegative().default(0),
  data_source: z.literal("public-readonly-cache").default("public-readonly-cache"),
});

export const RunCreateSchema = z
  .object({
    conversation_id: IdSchema,
    strategy_spec: StrategySpecSchema,
    strategy_logic: z.record(z.string(), z.unknown()).optional(),
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

export const KNOWN_RUN_EVENT_TYPES = [
  "message.delta",
  "provider.started",
  "provider.waiting",
  "provider.retrying",
  "model.reasoning.delta",
  "model.usage",
  "tool.started",
  "tool.completed",
  "validation.completed",
  "review.completed",
  "artifact.created",
  "policy.blocked",
  "observability.stage.completed",
  "progress.snapshot",
  "progress.update",
  "stage.started",
  "stage.completed",
  "backtest.queued",
  "backtest.data.started",
  "backtest.data.completed",
  "backtest.execution.started",
  "backtest.execution.completed",
  "backtest.report.completed",
  "backtest.failed",
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
  latest_run_events: z.array(RunEventSchema),
  feedback_targets: FeedbackTargetSchema,
  strategy_profile: StrategyProfileSchema.nullable().optional(),
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
export type RunMode = z.infer<typeof RunModeSchema>;
export type BacktestConfig = z.infer<typeof BacktestConfigSchema>;
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
