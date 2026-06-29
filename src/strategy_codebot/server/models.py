from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from strategy_codebot.nautilus_runtime import RUNTIME_STATES
from strategy_codebot.server.bot_proposal_status import BOT_PROPOSAL_STATUSES
from strategy_codebot.server.bot_proposal_status import BOT_PROPOSAL_STATUS_DRAFT


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class TenantMixin(TimestampMixin):
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


def _sql_string_set(values: set[str]) -> str:
    return ",".join(f"'{value}'" for value in sorted(values))


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    external_subject: Mapped[str | None] = mapped_column(String(255), unique=True)
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkspaceMembership(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_memberships_workspace_user"),
        CheckConstraint("role IN ('owner','admin','member')", name="ck_workspace_memberships_role"),
    )

    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ConversationThread(TenantMixin, Base):
    __tablename__ = "conversation_threads"
    __table_args__ = (
        Index("ix_conversation_threads_workspace_owner", "workspace_id", "owner_user_id"),
        CheckConstraint(
            "mode IN ('strategy_design','pine_generation','mql5_design','review','validation','education')",
            name="ck_conversation_threads_mode",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str | None] = mapped_column(String(160))
    mode: Mapped[str] = mapped_column(String(40), nullable=False, default="strategy_design")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationMessage(TenantMixin, Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        Index("ix_conversation_messages_workspace_owner", "workspace_id", "owner_user_id"),
        CheckConstraint("role IN ('user','assistant','system','tool')", name="ck_conversation_messages_role"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversation_threads.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)


class ConversationMemory(TenantMixin, Base):
    __tablename__ = "conversation_memories"
    __table_args__ = (
        UniqueConstraint("conversation_id", name="uq_conversation_memories_conversation"),
        Index("ix_conversation_memories_workspace_owner", "workspace_id", "owner_user_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversation_threads.id"), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    covered_message_id: Mapped[str | None] = mapped_column(ForeignKey("conversation_messages.id"), index=True)
    summary_version: Mapped[int] = mapped_column(nullable=False, default=1)
    estimated_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AssistantRun(TenantMixin, Base):
    __tablename__ = "assistant_runs"
    __table_args__ = (
        Index("ix_assistant_runs_workspace_owner", "workspace_id", "owner_user_id"),
        CheckConstraint(
            "status IN ('queued','running','completed','failed','blocked','cancelled')",
            name="ck_assistant_runs_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversation_threads.id"), nullable=False, index=True)
    retry_of_run_id: Mapped[str | None] = mapped_column(ForeignKey("assistant_runs.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    mode: Mapped[str | None] = mapped_column(String(40))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    request_id: Mapped[str | None] = mapped_column(String(120), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(120), index=True)


class RunEvent(TenantMixin, Base):
    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_run_events_run_sequence"),
        Index("ix_run_events_workspace_owner", "workspace_id", "owner_user_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("assistant_runs.id"), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversation_threads.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(nullable=False)
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_json: Mapped[dict | None] = mapped_column(JSON)


class RunJob(TenantMixin, Base):
    __tablename__ = "run_jobs"
    __table_args__ = (
        Index("ix_run_jobs_status_type_created", "status", "job_type", "created_at"),
        Index("ix_run_jobs_workspace_status", "workspace_id", "status"),
        CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled')",
            name="ck_run_jobs_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("assistant_runs.id"), nullable=False, index=True)
    job_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    lease_owner: Mapped[str | None] = mapped_column(String(120), index=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    result_json: Mapped[dict | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ToolCall(TimestampMixin, Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','completed','failed','blocked')", name="ck_tool_calls_status"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("assistant_runs.id"), nullable=False, index=True)
    tool_id: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    input_json: Mapped[dict | None] = mapped_column(JSON)
    output_json: Mapped[dict | None] = mapped_column(JSON)
    policy_findings_json: Mapped[list | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Artifact(TenantMixin, Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        Index("ix_artifacts_workspace_owner", "workspace_id", "owner_user_id"),
        Index("ix_artifacts_workspace_owner_created_id", "workspace_id", "owner_user_id", "created_at", "id"),
        Index(
            "ix_artifacts_conversation_workspace_owner_created_id",
            "conversation_id",
            "workspace_id",
            "owner_user_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("assistant_runs.id"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversation_threads.id"), index=True)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(120))
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)


class BacktestReport(TenantMixin, Base):
    __tablename__ = "backtest_reports"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_backtest_reports_run"),
        Index("ix_backtest_reports_workspace_owner", "workspace_id", "owner_user_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("assistant_runs.id"), nullable=False, index=True)
    engine: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_label: Mapped[str] = mapped_column(String(160), nullable=False)
    execution_semantics: Mapped[str] = mapped_column(String(80), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    signal_timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    candle_timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    assumptions_json: Mapped[list | None] = mapped_column(JSON)
    warnings_json: Mapped[list | None] = mapped_column(JSON)
    reproducibility_hash: Mapped[str | None] = mapped_column(String(160), index=True)


class BacktestTradeIndex(TenantMixin, Base):
    __tablename__ = "backtest_trade_index"
    __table_args__ = (
        UniqueConstraint("run_id", "trade_rank", name="uq_backtest_trade_index_run_rank"),
        Index("ix_backtest_trade_index_workspace_owner", "workspace_id", "owner_user_id"),
        Index("ix_backtest_trade_index_run_bucket", "run_id", "bucket"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("assistant_runs.id"), nullable=False, index=True)
    trade_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    bucket: Mapped[str] = mapped_column(String(40), nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pnl_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    pnl_percentage: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)


class BacktestEquitySummary(TenantMixin, Base):
    __tablename__ = "backtest_equity_summary"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_backtest_equity_summary_run"),
        Index("ix_backtest_equity_summary_workspace_owner", "workspace_id", "owner_user_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("assistant_runs.id"), nullable=False, index=True)
    sample_resolution: Mapped[str] = mapped_column(String(40), nullable=False)
    points_json: Mapped[list] = mapped_column(JSON, nullable=False)
    drawdown_windows_json: Mapped[list | None] = mapped_column(JSON)
    monthly_returns_json: Mapped[list | None] = mapped_column(JSON)


class BacktestRunnerStats(TenantMixin, Base):
    __tablename__ = "backtest_runner_stats"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_backtest_runner_stats_run"),
        Index("ix_backtest_runner_stats_workspace_owner", "workspace_id", "owner_user_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("assistant_runs.id"), nullable=False, index=True)
    runner: Mapped[str] = mapped_column(String(80), nullable=False)
    runner_version: Mapped[str | None] = mapped_column(String(120))
    bars_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    compile_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    run_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    artifact_manifest_json: Mapped[dict | None] = mapped_column(JSON)


class StrategySpec(TenantMixin, Base):
    __tablename__ = "strategy_specs"
    __table_args__ = (
        Index("ix_strategy_specs_workspace_owner", "workspace_id", "owner_user_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("assistant_runs.id"), nullable=False, index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False)


class NautilusRuntime(TenantMixin, Base):
    __tablename__ = "nautilus_runtimes"
    __table_args__ = (
        UniqueConstraint("workspace_id", "owner_user_id", "runtime_key", name="uq_nautilus_runtimes_tenant_key"),
        Index("ix_nautilus_runtimes_workspace_owner", "workspace_id", "owner_user_id"),
        Index("ix_nautilus_runtimes_workspace_state", "workspace_id", "state"),
        Index("ix_nautilus_runtimes_desired_lease", "mode", "desired_state", "lease_until"),
        CheckConstraint("mode IN ('paper','live')", name="ck_nautilus_runtimes_mode"),
        CheckConstraint(
            f"state IN ({_sql_string_set(RUNTIME_STATES)})",
            name="ck_nautilus_runtimes_state",
        ),
        CheckConstraint(
            "desired_state IN ('requested','running','stopping','stopped')",
            name="ck_nautilus_runtimes_desired_state",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    runtime_key: Mapped[str] = mapped_column(String(512), nullable=False)
    broker_connection_id: Mapped[str] = mapped_column(String(120), nullable=False)
    account_id: Mapped[str] = mapped_column(String(120), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    risk_policy_id: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="requested")
    strategy_ids_json: Mapped[list] = mapped_column(JSON, nullable=False)
    manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    data_subscriptions_json: Mapped[list] = mapped_column(JSON, nullable=False)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    heartbeat_metrics_json: Mapped[dict | None] = mapped_column(JSON)
    last_heartbeat_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    kill_switch_active: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    desired_state: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    worker_id: Mapped[str | None] = mapped_column(String(120), index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_json: Mapped[dict | None] = mapped_column(JSON)
    stream_cursor_json: Mapped[dict | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class NautilusRuntimeEvent(TenantMixin, Base):
    __tablename__ = "nautilus_runtime_events"
    __table_args__ = (
        UniqueConstraint("runtime_id", "sequence", name="uq_nautilus_runtime_events_runtime_sequence"),
        UniqueConstraint("runtime_id", "idempotency_key", name="uq_nautilus_runtime_events_runtime_idempotency"),
        Index("ix_nautilus_runtime_events_workspace_owner", "workspace_id", "owner_user_id"),
        Index("ix_nautilus_runtime_events_runtime_sequence", "runtime_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    runtime_id: Mapped[str] = mapped_column(ForeignKey("nautilus_runtimes.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_json: Mapped[dict | None] = mapped_column(JSON)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), index=True)


class BotProposal(TenantMixin, Base):
    __tablename__ = "bot_proposals"
    __table_args__ = (
        Index("ix_bot_proposals_workspace_owner", "workspace_id", "owner_user_id"),
        Index("ix_bot_proposals_workspace_status", "workspace_id", "status"),
        CheckConstraint(
            f"status IN ({_sql_string_set(BOT_PROPOSAL_STATUSES)})",
            name="ck_bot_proposals_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=BOT_PROPOSAL_STATUS_DRAFT)
    source_conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversation_threads.id"), index=True)
    source_run_id: Mapped[str | None] = mapped_column(ForeignKey("assistant_runs.id"), index=True)
    source_artifact_ids_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    strategy_id: Mapped[str] = mapped_column(String(160), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(240), nullable=False)
    manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    data_subscriptions_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    broker_connection_id: Mapped[str | None] = mapped_column(String(120))
    account_id: Mapped[str | None] = mapped_column(String(120))
    risk_policy_id: Mapped[str | None] = mapped_column(String(120))
    readiness_checks_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    missing_inputs_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    runtime_id: Mapped[str | None] = mapped_column(ForeignKey("nautilus_runtimes.id"), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ValidationReport(TenantMixin, Base):
    __tablename__ = "validation_reports"
    __table_args__ = (
        Index("ix_validation_reports_workspace_owner", "workspace_id", "owner_user_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("assistant_runs.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)


class ReviewReport(TenantMixin, Base):
    __tablename__ = "review_reports"
    __table_args__ = (
        Index("ix_review_reports_workspace_owner", "workspace_id", "owner_user_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("assistant_runs.id"), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)


class PolicyFinding(TenantMixin, Base):
    __tablename__ = "policy_findings"
    __table_args__ = (
        Index("ix_policy_findings_workspace_owner", "workspace_id", "owner_user_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("assistant_runs.id"), nullable=False, index=True)
    tool_call_id: Mapped[str | None] = mapped_column(ForeignKey("tool_calls.id"), index=True)
    severity: Mapped[str] = mapped_column(String(40), nullable=False)
    code: Mapped[str] = mapped_column(String(120), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)


class UsageLedger(TenantMixin, Base):
    __tablename__ = "usage_ledger"
    __table_args__ = (
        Index("ix_usage_ledger_workspace_owner", "workspace_id", "owner_user_id"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("assistant_runs.id"), index=True)
    model: Mapped[str | None] = mapped_column(String(120))
    tool_id: Mapped[str | None] = mapped_column(String(120))
    input_tokens: Mapped[int] = mapped_column(default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(default=0, nullable=False)
    cost_estimate_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))


class Feedback(TenantMixin, Base):
    __tablename__ = "feedback"
    __table_args__ = (
        Index("ix_feedback_workspace_owner", "workspace_id", "owner_user_id"),
        CheckConstraint("rating IN ('up','down','neutral')", name="ck_feedback_rating"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversation_threads.id"), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("assistant_runs.id"), index=True)
    message_id: Mapped[str | None] = mapped_column(ForeignKey("conversation_messages.id"), index=True)
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"), index=True)
    request_id: Mapped[str | None] = mapped_column(String(120), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(120), index=True)
    rating: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str | None] = mapped_column(String(80))
    correction: Mapped[str] = mapped_column(Text, nullable=False)
