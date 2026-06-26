from datetime import datetime
import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from strategy_codebot.server.run_modes import RUN_MODE_BACKTEST_PREVIEW
from strategy_codebot.server.run_modes import RUN_MODE_DRY_RUN
from strategy_codebot.server.run_modes import BACKTEST_ENGINE_PINEFORGE
from strategy_codebot.server.run_modes import BACKTEST_ENGINES
from strategy_codebot.server.run_modes import BACKTEST_OHLCV_DEFAULT_EXCHANGE
from strategy_codebot.server.run_modes import BACKTEST_OHLCV_EXCHANGES
from strategy_codebot.server.run_modes import backtest_default_engine
from strategy_codebot.server.run_modes import RUN_MODES
from strategy_codebot.server.run_modes import RUN_MODES_REQUIRING_BACKTEST_CONFIG
from strategy_codebot.server.run_modes import BACKTEST_EXECUTABLE_TIMEFRAMES
from strategy_codebot.server.run_modes import BACKTEST_MAX_COST_BPS


class WorkspaceCapabilityResponse(BaseModel):
    user_id: str
    workspace_id: str
    role: str
    tier: str
    tier_label: str
    allowed_message_modes: list[str]
    allowed_run_modes: list[str]


class MeResponse(BaseModel):
    user: dict[str, str]
    workspace: dict[str, str]
    capability: WorkspaceCapabilityResponse


class ProviderStatusResponse(BaseModel):
    configured: bool
    available: bool
    tier: str
    tier_label: str
    allowed_message_modes: list[str]
    allowed_run_modes: list[str]
    fallback_mode: str
    model_routing_mode: str = "registry"
    model_tier: str | None = None
    selected_stage_defaults: dict[str, str] = Field(default_factory=dict)
    available_gateways: list[str] = Field(default_factory=list)
    route_ready: bool = False
    fallback_enabled: bool = False
    user_message: str | None = None
    status: str
    reason: str | None = None


class AccountUsageResponse(BaseModel):
    tier: str
    tier_label: str
    period_start: datetime
    period_end: datetime
    messages: int
    runs: int
    artifacts: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float | None = None


def _normalize_conversation_title(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _parse_backtest_datetime(value: str, field_name: str) -> float:
    normalized = value.strip()
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00")).timestamp()
    except ValueError as exc:
        raise ValueError(f"backtest_config.{field_name} must be an ISO date or datetime") from exc


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=160)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        return _normalize_conversation_title(value)


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=160)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = _normalize_conversation_title(value)
        if not normalized:
            raise ValueError("title must not be blank")
        return normalized


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    owner_user_id: str
    workspace_id: str
    title: str | None
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    items: list[ConversationResponse]


class MessageCreate(BaseModel):
    content: str = Field(min_length=1)
    language: str = "en"
    web_search: str = Field(default="auto", pattern="^(off|auto|on)$")

    @field_validator("content")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value

    @field_validator("language")
    @classmethod
    def normalize_language(cls, value: str) -> str:
        return "vi" if value == "vi" else "en"

    @field_validator("web_search")
    @classmethod
    def normalize_web_search(cls, value: str) -> str:
        return value if value in {"off", "auto", "on"} else "auto"


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    owner_user_id: str
    workspace_id: str
    role: str
    content: str
    created_at: datetime


class MessageListResponse(BaseModel):
    items: list[MessageResponse]


class ConversationSidebarItem(BaseModel):
    conversation: ConversationResponse
    last_message_preview: str | None
    last_message_at: datetime | None
    message_count: int
    latest_run_id: str | None
    latest_run_status: str | None
    updated_at: datetime


class ConversationSidebarResponse(BaseModel):
    items: list[ConversationSidebarItem]


class ArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str | None
    conversation_id: str | None
    owner_user_id: str
    workspace_id: str
    kind: str
    mime_type: str | None
    display_name: str
    metadata_json: dict | None
    visibility: str | None = None
    category: str | None = None
    presentation: dict[str, Any] = Field(default_factory=dict)
    preview_summary: dict | None = None
    created_at: datetime


class ArtifactContentResponse(ArtifactResponse):
    content: Any


class ArtifactPreviewResponse(ArtifactResponse):
    preview: Any
    raw_available: bool
    truncated: bool
    line_count: int | None
    language: str | None


class ArtifactListResponse(BaseModel):
    items: list[ArtifactResponse]
    next_cursor: str | None = None


class KnowledgeCandidateCreate(BaseModel):
    lesson: str = Field(min_length=1, max_length=4000)
    evidence_ref: str = Field(min_length=1, max_length=1000)
    candidate_type: str = Field(default="episodic", min_length=1, max_length=64)
    confidence: str | None = Field(default=None, max_length=64)
    trust_level: str | None = Field(default="agent_reviewed", max_length=64)
    source_uri: str | None = Field(default=None, max_length=1000)
    metadata: dict[str, Any] | None = None


class KnowledgeCandidateResponse(BaseModel):
    candidate_id: str
    status: str
    candidate_type: str | None = None
    confidence: str | None = None
    trust_level: str | None = None
    evidence_ref: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    deduped: bool = False
    promotion_decision: str | None = None
    quality_score: float | None = None
    gate_summary: list[str] | None = None
    review_required_reason: str | None = None
    result_status: str | None = None
    store: str | None = None


class KnowledgeCandidateListResponse(BaseModel):
    status: str
    store: str
    candidates: list[KnowledgeCandidateResponse]


class KnowledgeLearningRequest(BaseModel):
    approval_mode: Literal["manual", "agent-auto", "guarded-auto"] = "guarded-auto"


class KnowledgeAutoReviewRequest(BaseModel):
    candidate_ids: list[str] | None = None


class KnowledgeLearningResponse(BaseModel):
    status: str
    approval_mode: str
    store: str
    index_ref: str | None = None
    extracted_count: int
    candidate_count: int
    proposed_count: int
    promoted_count: int
    skipped_count: int
    rejected_count: int
    candidates: list[KnowledgeCandidateResponse]
    promoted: list[KnowledgeCandidateResponse]
    skipped: list[KnowledgeCandidateResponse]
    rejected: list[KnowledgeCandidateResponse]


class RunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    owner_user_id: str
    workspace_id: str
    status: str
    mode: str | None = None
    created_at: datetime
    updated_at: datetime
    retry_of_run_id: str | None
    request_id: str | None
    trace_id: str | None


class BacktestConfig(BaseModel):
    engine: str = Field(default_factory=backtest_default_engine)
    exchange: str = Field(default=BACKTEST_OHLCV_DEFAULT_EXCHANGE)
    symbol: str = Field(min_length=1, max_length=64)
    timeframe: str = Field(min_length=1)
    candle_timeframe: str = Field(default="1m")
    start: str = Field(min_length=1)
    end: str = Field(min_length=1)
    initial_capital: float = Field(gt=0)
    fee_bps: float = Field(default=0, ge=0, le=BACKTEST_MAX_COST_BPS)
    slippage_bps: float = Field(default=0, ge=0, le=BACKTEST_MAX_COST_BPS)
    data_source: str = Field(default="public-readonly-cache", pattern="^public-readonly-cache$")

    @field_validator("engine")
    @classmethod
    def validate_engine(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in BACKTEST_ENGINES:
            raise ValueError(f"engine must be one of: {', '.join(BACKTEST_ENGINES)}")
        if normalized == BACKTEST_ENGINE_PINEFORGE and os.getenv("BACKTEST_PINEFORGE_ENABLED") != "1":
            raise ValueError("backtest preview is disabled")
        return normalized

    @field_validator("exchange")
    @classmethod
    def validate_exchange(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in BACKTEST_OHLCV_EXCHANGES:
            allowed = ", ".join(BACKTEST_OHLCV_EXCHANGES)
            raise ValueError(f"exchange must be one of: {allowed}")
        return normalized

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized or any(ord(character) < 32 for character in normalized):
            raise ValueError("symbol must be a non-empty printable value")
        return normalized

    @field_validator("timeframe")
    @classmethod
    def validate_timeframe(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in BACKTEST_EXECUTABLE_TIMEFRAMES:
            allowed = ", ".join(BACKTEST_EXECUTABLE_TIMEFRAMES)
            raise ValueError(f"timeframe must be one of: {allowed}")
        return normalized

    @field_validator("candle_timeframe")
    @classmethod
    def validate_candle_timeframe(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized != "1m":
            raise ValueError("candle_timeframe must be 1m")
        return normalized

    @model_validator(mode="after")
    def validate_date_range(self) -> "BacktestConfig":
        start = _parse_backtest_datetime(self.start, "start")
        end = _parse_backtest_datetime(self.end, "end")
        if end <= start:
            raise ValueError("backtest_config.end must be after backtest_config.start")
        return self


class RunCreate(BaseModel):
    conversation_id: str = Field(min_length=1)
    strategy_spec: dict[str, Any]
    pine_code: str | None = None
    mode: str = Field(default=RUN_MODE_DRY_RUN)
    web_search: str = Field(default="auto", pattern="^(off|auto|on)$")
    backtest_config: BacktestConfig | None = None

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value not in RUN_MODES:
            raise ValueError(f"mode must be one of: {', '.join(RUN_MODES)}")
        return value

    @model_validator(mode="after")
    def validate_backtest_config(self) -> "RunCreate":
        if self.mode in RUN_MODES_REQUIRING_BACKTEST_CONFIG and self.backtest_config is None:
            raise ValueError("backtest_config is required when mode is backtest-preview")
        if self.mode != RUN_MODE_BACKTEST_PREVIEW and self.backtest_config is not None:
            raise ValueError("backtest_config is only supported when mode is backtest-preview")
        if (
            self.mode == RUN_MODE_BACKTEST_PREVIEW
            and self.backtest_config is not None
            and self.backtest_config.engine == BACKTEST_ENGINE_PINEFORGE
            and not self.pine_code
        ):
            raise ValueError("Backtest preview requires PineScript v6 strategy source")
        return self


class RunCreateResponse(RunResponse):
    artifacts: list[ArtifactResponse]


class BacktestApprovalDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]


class BacktestApprovalDecisionResponse(BaseModel):
    approval_id: str
    conversation_id: str
    decision: Literal["approved", "rejected"]
    status: Literal["queued", "rejected"]
    run_id: str | None = None
    job_id: str | None = None
    backtest_config: dict[str, Any] | None = None


class RunEventResponse(BaseModel):
    event_id: str
    conversation_id: str
    run_id: str
    request_id: str | None
    trace_id: str | None
    sequence: int
    type: str
    payload: dict | None
    created_at: datetime


class StrategyBriefResponse(BaseModel):
    market: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    platform: str | None = None
    strategy_type: str | None = None
    entry_rules: list[str] = Field(default_factory=list)
    exit_rules: list[str] = Field(default_factory=list)
    risk_rules: list[str] = Field(default_factory=list)


class StrategySnapshotResponse(BaseModel):
    completeness: Literal["draft", "needs_risk", "ready_for_artifact"]
    missing_fields: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    boundary_flags: list[str] = Field(default_factory=list)


class StrategyAssumptionsResponse(BaseModel):
    confirmed: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class StrategyMemoryResponse(BaseModel):
    has_context: bool = False
    summary: str | None = None
    last_artifact_id: str | None = None
    open_questions: list[str] = Field(default_factory=list)


class StrategyCodeOutlineItemResponse(BaseModel):
    id: str
    label: str
    kind: str
    artifact_id: str | None = None
    anchor: str | None = None


class StrategyProfileResponse(BaseModel):
    source: Literal["strategy_spec", "conversation"]
    updated_at: datetime | None = None
    brief: StrategyBriefResponse
    snapshot: StrategySnapshotResponse
    assumptions: StrategyAssumptionsResponse
    memory: StrategyMemoryResponse
    code_outline: list[StrategyCodeOutlineItemResponse] = Field(default_factory=list)


class ConversationStateResponse(BaseModel):
    conversation: ConversationResponse
    messages: list[MessageResponse]
    message_count: int
    messages_truncated: bool
    message_limit: int
    latest_run: RunResponse | None
    latest_run_artifacts: list[ArtifactResponse]
    conversation_artifacts: list[ArtifactResponse] = Field(default_factory=list)
    conversation_artifacts_next_cursor: str | None = None
    latest_run_events: list[RunEventResponse]
    conversation_run_events: list[RunEventResponse] = Field(default_factory=list)
    feedback_targets: dict[str, Any]
    strategy_profile: StrategyProfileResponse | None = None


class FeedbackCreate(BaseModel):
    conversation_id: str = Field(min_length=1)
    run_id: str | None = None
    message_id: str | None = None
    artifact_id: str | None = None
    rating: str = Field(pattern="^(up|down|neutral)$")
    category: str | None = Field(default=None, max_length=80)
    correction: str = Field(min_length=1)

    @field_validator("correction")
    @classmethod
    def reject_blank_correction(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("correction must not be blank")
        return value


class FeedbackResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    run_id: str | None
    message_id: str | None
    artifact_id: str | None
    owner_user_id: str
    workspace_id: str
    request_id: str | None
    trace_id: str | None
    rating: str
    category: str | None
    created_at: datetime


class FeedbackOption(BaseModel):
    value: str
    label: str


class FeedbackOptionsResponse(BaseModel):
    ratings: list[FeedbackOption]
    categories: list[FeedbackOption]
