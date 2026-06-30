import base64
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from threading import RLock
from typing import Any, Literal, Protocol

from strategy_codebot.nautilus_streams import MarketDataStreamSubscription
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.artifact_kinds import INTERNAL_ARTIFACT_KINDS
from strategy_codebot.server.bot_proposal_status import BOT_PROPOSAL_STATUS_STARTED
from strategy_codebot.server.bot_proposal_status import BotProposalStatus
from strategy_codebot.server.ids import opaque_id
from strategy_codebot.server.models import utc_now
from strategy_codebot.server.run_modes import BACKTEST_JOB_MAX_ATTEMPTS
from strategy_codebot.server.run_modes import backtest_active_limit_from_payload
from strategy_codebot.server.workflow_task_status import WORKFLOW_TASK_RESOLVED_STATUSES

TERMINAL_RUN_STATUSES = {"completed", "failed", "blocked", "cancelled"}
WORKFLOW_CONTINUATION_EVENT_TYPES = frozenset(
    {
        "workflow.continuation.required",
        "workflow.continuation.started",
        "workflow.continuation.completed",
        "workflow.continuation.failed",
    }
)
RunEventInput = tuple[str, dict | None]
ArtifactInput = tuple[str, str | None, str, str, dict | None]
CONVERSATION_ARTIFACT_STATE_LIMIT = 50
CONVERSATION_ARTIFACT_PAGE_MAX_LIMIT = 100
ArtifactVisibilityFilter = Literal["user", "all"]
NAUTILUS_HEARTBEAT_SAMPLE_INTERVAL = timedelta(minutes=5)
NAUTILUS_HEARTBEAT_RETENTION_MAX_AGE = timedelta(hours=24)
NAUTILUS_HEARTBEAT_RETENTION_MAX_SAMPLES = 288


@dataclass(frozen=True)
class ConversationRecord:
    id: str
    owner_user_id: str
    workspace_id: str
    title: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class MessageRecord:
    id: str
    conversation_id: str
    owner_user_id: str
    workspace_id: str
    role: str
    content: str
    created_at: datetime


@dataclass(frozen=True)
class ConversationMemoryRecord:
    id: str
    conversation_id: str
    owner_user_id: str
    workspace_id: str
    summary: str
    covered_message_id: str | None
    summary_version: int
    estimated_tokens: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AssistantRunRecord:
    id: str
    conversation_id: str
    owner_user_id: str
    workspace_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    mode: str | None = None
    retry_of_run_id: str | None = None
    request_id: str | None = None
    trace_id: str | None = None


@dataclass(frozen=True)
class RunJobRecord:
    id: str
    run_id: str
    owner_user_id: str
    workspace_id: str
    job_type: str
    status: str
    payload_json: dict
    attempts: int
    max_attempts: int
    lease_owner: str | None
    leased_until: datetime | None
    result_json: dict | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class RunQueueStatsRecord:
    queued: int
    running: int
    oldest_queued_seconds: int | None
    oldest_running_seconds: int | None = None
    failed: int = 0
    active_running: int = 0
    stale_running: int = 0


@dataclass(frozen=True)
class RunEventRecord:
    id: str
    run_id: str
    conversation_id: str
    owner_user_id: str
    workspace_id: str
    sequence: int
    type: str
    payload: dict | None
    created_at: datetime
    request_id: str | None = None
    trace_id: str | None = None


@dataclass(frozen=True)
class RunEventSummaryRecord:
    event_count: int
    latest_event: RunEventRecord | None


@dataclass(frozen=True)
class WorkflowTaskRecord:
    id: str
    conversation_id: str
    run_id: str | None
    owner_user_id: str
    workspace_id: str
    workflow_id: str
    task_template_id: str
    step_id: str
    kind: str
    status: str
    payload_json: dict
    response_json: dict | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None


@dataclass(frozen=True)
class WorkflowTaskSyncResult:
    records: list[WorkflowTaskRecord]
    created: list[WorkflowTaskRecord]
    updated: list[WorkflowTaskRecord]
    resolved: list[WorkflowTaskRecord]
    unchanged: list[WorkflowTaskRecord]


@dataclass(frozen=True)
class ArtifactRecord:
    id: str
    run_id: str | None
    conversation_id: str | None
    owner_user_id: str
    workspace_id: str
    kind: str
    mime_type: str | None
    display_name: str
    storage_key: str
    metadata_json: dict | None
    created_at: datetime


@dataclass(frozen=True)
class ArtifactPageRecord:
    items: list[ArtifactRecord]
    next_cursor: str | None


@dataclass(frozen=True)
class RunProgressSnapshotRecord:
    run: AssistantRunRecord
    event_summary: RunEventSummaryRecord
    artifacts: list[ArtifactRecord]


@dataclass(frozen=True)
class ConversationStateSnapshotRecord:
    conversation: ConversationRecord
    messages: list[MessageRecord]
    message_count: int
    messages_truncated: bool
    message_limit: int
    latest_run: AssistantRunRecord | None
    latest_run_artifacts: list[ArtifactRecord]
    conversation_artifacts: list[ArtifactRecord]
    conversation_artifacts_next_cursor: str | None
    latest_run_events: list[RunEventRecord]
    conversation_run_events: list[RunEventRecord]
    latest_strategy_spec: "StrategySpecRecord | None" = None


@dataclass(frozen=True)
class AccountUsageSummaryRecord:
    messages: int
    runs: int
    artifacts: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float | None


@dataclass(frozen=True)
class StrategySpecRecord:
    id: str
    run_id: str
    owner_user_id: str
    workspace_id: str
    payload_json: dict
    schema_version: str
    created_at: datetime


@dataclass(frozen=True)
class ValidationReportRecord:
    id: str
    run_id: str
    owner_user_id: str
    workspace_id: str
    status: str
    payload_json: dict
    created_at: datetime


@dataclass(frozen=True)
class ReviewReportRecord:
    id: str
    run_id: str
    owner_user_id: str
    workspace_id: str
    decision: str
    payload_json: dict
    created_at: datetime


@dataclass(frozen=True)
class ToolCallRecord:
    id: str
    run_id: str
    tool_id: str
    status: str
    input_json: dict | None
    output_json: dict | None
    policy_findings_json: list | None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True)
class PolicyFindingRecord:
    id: str
    run_id: str
    tool_call_id: str | None
    owner_user_id: str
    workspace_id: str
    severity: str
    code: str
    message: str
    created_at: datetime


@dataclass(frozen=True)
class UsageLedgerRecord:
    id: str
    owner_user_id: str
    workspace_id: str
    run_id: str | None
    model: str | None
    tool_id: str | None
    input_tokens: int
    output_tokens: int
    cost_estimate_usd: float | None
    created_at: datetime


@dataclass(frozen=True)
class NautilusRuntimeRecord:
    id: str
    owner_user_id: str
    workspace_id: str
    runtime_key: str
    broker_connection_id: str
    account_id: str
    mode: str
    risk_policy_id: str
    state: str
    strategy_ids: list[str]
    manifest_json: dict
    data_subscriptions_json: list
    last_heartbeat_at: datetime | None
    heartbeat_count: int
    heartbeat_metrics_json: dict | None
    last_heartbeat_event_at: datetime | None
    kill_switch_active: bool
    desired_state: str
    worker_id: str | None
    lease_until: datetime | None
    generation: int
    started_at: datetime | None
    stopped_at: datetime | None
    last_error_json: dict | None
    stream_cursor_json: dict | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class NautilusRuntimeEventRecord:
    id: str
    runtime_id: str
    owner_user_id: str
    workspace_id: str
    sequence: int
    type: str
    payload: dict | None
    created_at: datetime
    idempotency_key: str | None = None


@dataclass(frozen=True)
class NautilusHeartbeatRecord:
    runtime: NautilusRuntimeRecord
    event: NautilusRuntimeEventRecord | None
    event_appended: bool

NautilusRuntimeEventInput = tuple[str, dict | None, str | None]


@dataclass(frozen=True)
class BotProposalRecord:
    id: str
    owner_user_id: str
    workspace_id: str
    status: str
    source_conversation_id: str | None
    source_run_id: str | None
    source_artifact_ids: list[str]
    strategy_id: str
    strategy_name: str
    manifest_json: dict
    data_subscriptions_json: list
    broker_connection_id: str | None
    account_id: str | None
    risk_policy_id: str | None
    readiness_checks_json: list
    missing_inputs_json: list
    runtime_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class BotProposalCreateInput:
    status: BotProposalStatus
    source_conversation_id: str | None
    source_run_id: str | None
    source_artifact_ids: list[str]
    strategy_id: str
    strategy_name: str
    manifest_json: dict
    data_subscriptions_json: list
    broker_connection_id: str | None
    account_id: str | None
    risk_policy_id: str | None
    readiness_checks_json: list
    missing_inputs_json: list


@dataclass(frozen=True)
class FeedbackRecord:
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
    correction: str
    created_at: datetime


@dataclass(frozen=True)
class ConversationSidebarRecord:
    conversation: ConversationRecord
    last_message_content: str | None
    last_message_at: datetime | None
    message_count: int
    latest_run_id: str | None
    latest_run_status: str | None
    updated_at: datetime


class ConversationRepository(Protocol):
    def create_conversation(self, auth: AuthContext, title: str | None = None) -> ConversationRecord: ...

    def list_conversations(self, auth: AuthContext) -> list[ConversationRecord]: ...

    def list_conversation_sidebar(self, auth: AuthContext) -> list[ConversationSidebarRecord]: ...

    def get_conversation(self, auth: AuthContext, conversation_id: str) -> ConversationRecord | None: ...

    def update_conversation_title(
        self,
        auth: AuthContext,
        conversation_id: str,
        title: str,
    ) -> ConversationRecord | None: ...

    def delete_conversation(self, auth: AuthContext, conversation_id: str) -> ConversationRecord | None: ...

    def create_message(
        self,
        auth: AuthContext,
        conversation_id: str,
        content: str,
        *,
        role: str = "user",
    ) -> MessageRecord | None: ...

    def list_messages(self, auth: AuthContext, conversation_id: str) -> list[MessageRecord]: ...

    def list_messages_for_context(self, auth: AuthContext, conversation_id: str, *, limit: int | None = 80) -> list[MessageRecord]: ...

    def get_conversation_memory(self, auth: AuthContext, conversation_id: str) -> ConversationMemoryRecord | None: ...

    def upsert_conversation_memory(
        self,
        auth: AuthContext,
        conversation_id: str,
        *,
        summary: str,
        covered_message_id: str | None,
        estimated_tokens: int,
    ) -> ConversationMemoryRecord | None: ...

    def get_conversation_state_snapshot(
        self,
        auth: AuthContext,
        conversation_id: str,
        *,
        event_limit: int = 30,
        message_limit: int = 100,
    ) -> ConversationStateSnapshotRecord | None: ...

    def create_run(
        self,
        auth: AuthContext,
        conversation_id: str,
        *,
        status: str = "running",
        mode: str | None = None,
        retry_of_run_id: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> AssistantRunRecord | None: ...

    def list_runs(self, auth: AuthContext, conversation_id: str) -> list[AssistantRunRecord] | None: ...

    def get_run(self, auth: AuthContext, run_id: str) -> AssistantRunRecord | None: ...

    def create_run_job(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        job_type: str,
        payload_json: dict,
        max_attempts: int = BACKTEST_JOB_MAX_ATTEMPTS,
    ) -> RunJobRecord | None: ...

    def claim_run_job(self, *, job_type: str, worker_id: str, lease_seconds: int = 300) -> RunJobRecord | None: ...

    def complete_run_job(
        self,
        job_id: str,
        *,
        status: str,
        result_json: dict | None = None,
        error_code: str | None = None,
    ) -> RunJobRecord | None: ...

    def cancel_run_jobs(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        statuses: tuple[str, ...] = ("queued", "running"),
        result_json: dict | None = None,
        error_code: str | None = None,
    ) -> int: ...

    def get_run_job(self, job_id: str) -> RunJobRecord | None: ...

    def run_queue_stats(self, *, job_type: str | None = None) -> RunQueueStatsRecord: ...

    def append_run_event(
        self,
        auth: AuthContext,
        run_id: str,
        event_type: str,
        payload: dict | None = None,
    ) -> RunEventRecord | None: ...

    def append_run_events(
        self,
        auth: AuthContext,
        run_id: str,
        events: list[RunEventInput],
    ) -> list[RunEventRecord] | None: ...

    def list_run_events(self, auth: AuthContext, run_id: str) -> list[RunEventRecord] | None: ...

    def list_run_events_after(
        self,
        auth: AuthContext,
        run_id: str,
        last_event_id: str | None = None,
    ) -> list[RunEventRecord] | None: ...

    def list_workflow_task_continuation_events(
        self,
        auth: AuthContext,
        task_id: str,
    ) -> list[RunEventRecord] | None: ...

    def upsert_workflow_task(
        self,
        auth: AuthContext,
        *,
        conversation_id: str,
        workflow_id: str,
        task_template_id: str,
        step_id: str,
        kind: str,
        status: str,
        payload_json: dict,
        run_id: str | None = None,
    ) -> WorkflowTaskRecord | None: ...

    def sync_workflow_tasks(
        self,
        auth: AuthContext,
        *,
        conversation_id: str,
        run_id: str | None,
        workflow_id: str,
        task_payloads: list[dict],
        completed_steps: set[str],
    ) -> WorkflowTaskSyncResult | None: ...

    def list_workflow_tasks(self, auth: AuthContext, conversation_id: str) -> list[WorkflowTaskRecord] | None: ...

    def get_workflow_task(self, auth: AuthContext, task_id: str) -> WorkflowTaskRecord | None: ...

    def submit_workflow_task_response(
        self,
        auth: AuthContext,
        task_id: str,
        *,
        response_json: dict,
        status: str = "completed",
    ) -> WorkflowTaskRecord | None: ...

    def resolve_workflow_task(
        self,
        auth: AuthContext,
        task_id: str,
        *,
        status: str,
        response_json: dict | None = None,
    ) -> WorkflowTaskRecord | None: ...

    def summarize_run_events(self, auth: AuthContext, run_id: str) -> RunEventSummaryRecord | None: ...

    def get_run_progress_snapshot(self, auth: AuthContext, run_id: str) -> RunProgressSnapshotRecord | None: ...

    def set_run_status(self, auth: AuthContext, run_id: str, status: str) -> AssistantRunRecord | None: ...

    def create_strategy_spec(
        self,
        auth: AuthContext,
        run_id: str,
        payload: dict,
        schema_version: str,
    ) -> StrategySpecRecord | None: ...

    def create_artifact(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        kind: str,
        mime_type: str | None,
        display_name: str,
        storage_key: str,
        metadata_json: dict | None = None,
    ) -> ArtifactRecord | None: ...

    def create_artifacts(
        self,
        auth: AuthContext,
        run_id: str,
        artifacts: list[ArtifactInput],
    ) -> list[ArtifactRecord] | None: ...

    def list_artifacts(self, auth: AuthContext, run_id: str) -> list[ArtifactRecord] | None: ...

    def get_artifact(self, auth: AuthContext, artifact_id: str) -> ArtifactRecord | None: ...

    def list_workspace_artifacts_page(
        self,
        auth: AuthContext,
        *,
        limit: int = CONVERSATION_ARTIFACT_STATE_LIMIT,
        cursor: str | None = None,
        visibility: ArtifactVisibilityFilter = "user",
    ) -> ArtifactPageRecord: ...

    def list_conversation_artifacts_page(
        self,
        auth: AuthContext,
        conversation_id: str,
        *,
        limit: int = CONVERSATION_ARTIFACT_STATE_LIMIT,
        cursor: str | None = None,
        visibility: ArtifactVisibilityFilter = "user",
    ) -> ArtifactPageRecord | None: ...

    def get_backtest_summary(self, auth: AuthContext, run_id: str) -> dict | None: ...

    def get_backtest_summaries(self, auth: AuthContext, run_ids: list[str]) -> dict[str, dict]: ...

    def resolve_backtest_report_run_id(self, auth: AuthContext, conversation_id: str, requested_run_id: str) -> str | None:
        ...

    def query_backtest_trades(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        bucket: str | None = None,
        limit: int = 20,
    ) -> list[dict] | None: ...

    def get_backtest_equity_summary(self, auth: AuthContext, run_id: str) -> dict | None: ...

    def get_backtest_equity_summaries(self, auth: AuthContext, run_ids: list[str]) -> dict[str, dict]: ...

    def create_validation_report(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        status: str,
        payload: dict,
    ) -> ValidationReportRecord | None: ...

    def create_review_report(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        decision: str,
        payload: dict,
    ) -> ReviewReportRecord | None: ...

    def create_tool_call(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        tool_id: str,
        status: str,
        input_json: dict | None = None,
        policy_findings_json: list | None = None,
    ) -> ToolCallRecord | None: ...

    def complete_tool_call(
        self,
        auth: AuthContext,
        tool_call_id: str,
        *,
        status: str,
        output_json: dict | None = None,
        policy_findings_json: list | None = None,
    ) -> ToolCallRecord | None: ...

    def create_policy_finding(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        severity: str,
        code: str,
        message: str,
        tool_call_id: str | None = None,
    ) -> PolicyFindingRecord | None: ...

    def create_usage_ledger(
        self,
        auth: AuthContext,
        *,
        run_id: str | None,
        model: str | None,
        tool_id: str | None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_estimate_usd: float | None = None,
    ) -> UsageLedgerRecord | None: ...

    def list_tool_calls(self, auth: AuthContext, run_id: str) -> list[ToolCallRecord] | None: ...

    def list_policy_findings(self, auth: AuthContext, run_id: str) -> list[PolicyFindingRecord] | None: ...

    def list_usage_ledger(self, auth: AuthContext, run_id: str) -> list[UsageLedgerRecord] | None: ...

    def summarize_account_usage(self, auth: AuthContext) -> AccountUsageSummaryRecord: ...

    def upsert_nautilus_runtime(
        self,
        auth: AuthContext,
        *,
        runtime_key: str,
        broker_connection_id: str,
        account_id: str,
        mode: str,
        risk_policy_id: str,
        strategy_id: str,
        manifest_json: dict,
        data_subscriptions_json: list,
    ) -> NautilusRuntimeRecord: ...

    def list_nautilus_runtimes(
        self,
        auth: AuthContext,
        *,
        mode: str | None = None,
        limit: int = 100,
    ) -> list[NautilusRuntimeRecord]: ...

    def get_nautilus_runtime(self, auth: AuthContext, runtime_id: str) -> NautilusRuntimeRecord | None: ...

    def set_nautilus_runtime_state(
        self,
        auth: AuthContext,
        runtime_id: str,
        *,
        state: str,
    ) -> NautilusRuntimeRecord | None: ...

    def record_nautilus_runtime_heartbeat(
        self,
        auth: AuthContext,
        runtime_id: str,
        *,
        now: datetime | None = None,
        payload: dict | None = None,
        idempotency_key: str | None = None,
    ) -> NautilusHeartbeatRecord | None: ...

    def activate_nautilus_runtime_kill_switch(
        self,
        auth: AuthContext,
        runtime_id: str,
    ) -> NautilusRuntimeRecord | None: ...

    def set_nautilus_runtime_desired_state(
        self,
        auth: AuthContext,
        runtime_id: str,
        *,
        desired_state: str,
    ) -> NautilusRuntimeRecord | None: ...

    def list_desired_nautilus_runtimes(
        self,
        *,
        mode: str = "paper",
        desired_state: str = "running",
        worker_id: str | None = None,
        limit: int = 100,
    ) -> list[NautilusRuntimeRecord]: ...

    def list_active_nautilus_market_data_subscriptions(
        self,
        *,
        mode: str = "paper",
        desired_state: str = "running",
        limit: int = 5000,
    ) -> list[dict[str, Any]]: ...

    def claim_nautilus_runtime_lease(
        self,
        runtime_id: str,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> NautilusRuntimeRecord | None: ...

    def renew_nautilus_runtime_lease(
        self,
        runtime_id: str,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> NautilusRuntimeRecord | None: ...

    def release_nautilus_runtime_lease(
        self,
        runtime_id: str,
        *,
        worker_id: str,
        state: str | None = None,
        last_error_json: dict | None = None,
        now: datetime | None = None,
    ) -> NautilusRuntimeRecord | None: ...

    def persist_nautilus_runtime_stream_cursor(
        self,
        runtime_id: str,
        *,
        worker_id: str,
        stream_cursor_json: dict,
        now: datetime | None = None,
    ) -> NautilusRuntimeRecord | None: ...

    def append_nautilus_runtime_events_for_worker(
        self,
        runtime_id: str,
        *,
        worker_id: str,
        events: list[NautilusRuntimeEventInput],
    ) -> list[NautilusRuntimeEventRecord] | None: ...

    def append_nautilus_runtime_event(
        self,
        auth: AuthContext,
        runtime_id: str,
        event_type: str,
        payload: dict | None = None,
        idempotency_key: str | None = None,
    ) -> NautilusRuntimeEventRecord | None: ...

    def list_nautilus_runtime_events(
        self,
        auth: AuthContext,
        runtime_id: str,
        *,
        limit: int = 100,
        after_sequence: int | None = None,
    ) -> list[NautilusRuntimeEventRecord] | None: ...

    def create_bot_proposal(
        self,
        auth: AuthContext,
        proposal: BotProposalCreateInput,
    ) -> BotProposalRecord: ...

    def get_bot_proposal(self, auth: AuthContext, proposal_id: str) -> BotProposalRecord | None: ...

    def mark_bot_proposal_started(
        self,
        auth: AuthContext,
        proposal_id: str,
        *,
        runtime_id: str,
    ) -> BotProposalRecord | None: ...

    def get_strategy_spec_for_run(self, auth: AuthContext, run_id: str) -> StrategySpecRecord | None: ...

    def cleanup_nautilus_heartbeat_events(
        self,
        auth: AuthContext,
        runtime_id: str,
        *,
        now: datetime | None = None,
        max_age: timedelta = timedelta(hours=24),
        max_samples: int = 288,
    ) -> int | None: ...

    def create_feedback(
        self,
        auth: AuthContext,
        *,
        conversation_id: str,
        rating: str,
        correction: str,
        category: str | None = None,
        run_id: str | None = None,
        message_id: str | None = None,
        artifact_id: str | None = None,
    ) -> FeedbackRecord | None: ...


class InMemoryConversationRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._conversations: dict[str, ConversationRecord] = {}
        self._messages: dict[str, list[MessageRecord]] = {}
        self._conversation_memories: dict[str, ConversationMemoryRecord] = {}
        self._runs: dict[str, AssistantRunRecord] = {}
        self._run_jobs: dict[str, RunJobRecord] = {}
        self._run_events: dict[str, list[RunEventRecord]] = {}
        self._workflow_tasks: dict[str, WorkflowTaskRecord] = {}
        self._artifacts: dict[str, ArtifactRecord] = {}
        self._strategy_specs: dict[str, StrategySpecRecord] = {}
        self._validation_reports: dict[str, ValidationReportRecord] = {}
        self._review_reports: dict[str, ReviewReportRecord] = {}
        self._tool_calls: dict[str, ToolCallRecord] = {}
        self._policy_findings: dict[str, PolicyFindingRecord] = {}
        self._usage_ledger: dict[str, UsageLedgerRecord] = {}
        self._nautilus_runtimes: dict[str, NautilusRuntimeRecord] = {}
        self._nautilus_runtime_events: dict[str, list[NautilusRuntimeEventRecord]] = {}
        self._bot_proposals: dict[str, BotProposalRecord] = {}
        self._feedback: dict[str, FeedbackRecord] = {}

    def create_conversation(self, auth: AuthContext, title: str | None = None) -> ConversationRecord:
        now = _now()
        conversation = ConversationRecord(
            id=opaque_id("conv"),
            owner_user_id=auth.user_id,
            workspace_id=auth.workspace_id,
            title=title,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._conversations[conversation.id] = conversation
            self._messages[conversation.id] = []
        return conversation

    def list_conversations(self, auth: AuthContext) -> list[ConversationRecord]:
        with self._lock:
            conversations = [
                conversation
                for conversation in self._conversations.values()
                if _is_authorized(auth, conversation)
            ]
        return sorted(conversations, key=lambda conversation: conversation.updated_at, reverse=True)

    def list_conversation_sidebar(self, auth: AuthContext) -> list[ConversationSidebarRecord]:
        conversations = self.list_conversations(auth)
        with self._lock:
            rows = [
                _sidebar_record(
                    conversation,
                    self._messages.get(conversation.id, []),
                    [
                        run
                        for run in self._runs.values()
                        if run.conversation_id == conversation.id
                        and run.owner_user_id == auth.user_id
                        and run.workspace_id == auth.workspace_id
                    ],
                )
                for conversation in conversations
            ]
        return rows

    def get_conversation(self, auth: AuthContext, conversation_id: str) -> ConversationRecord | None:
        with self._lock:
            conversation = self._conversations.get(conversation_id)
        if conversation is None or not _is_authorized(auth, conversation):
            return None
        return conversation

    def update_conversation_title(
        self,
        auth: AuthContext,
        conversation_id: str,
        title: str,
    ) -> ConversationRecord | None:
        now = _now()
        with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or not _is_authorized(auth, conversation):
                return None
            updated = ConversationRecord(
                id=conversation.id,
                owner_user_id=conversation.owner_user_id,
                workspace_id=conversation.workspace_id,
                title=title,
                created_at=conversation.created_at,
                updated_at=now,
            )
            self._conversations[conversation.id] = updated
            return updated

    def delete_conversation(self, auth: AuthContext, conversation_id: str) -> ConversationRecord | None:
        with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or not _is_authorized(auth, conversation):
                return None
            return self._conversations.pop(conversation_id)

    def create_message(
        self,
        auth: AuthContext,
        conversation_id: str,
        content: str,
        *,
        role: str = "user",
    ) -> MessageRecord | None:
        now = _now()
        with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or not _is_authorized(auth, conversation):
                return None

            message = MessageRecord(
                id=opaque_id("msg"),
                conversation_id=conversation.id,
                owner_user_id=auth.user_id,
                workspace_id=auth.workspace_id,
                role=role,
                content=content,
                created_at=now,
            )
            self._messages[conversation.id].append(message)
            self._conversations[conversation.id] = ConversationRecord(
                id=conversation.id,
                owner_user_id=conversation.owner_user_id,
                workspace_id=conversation.workspace_id,
                title=conversation.title,
                created_at=conversation.created_at,
                updated_at=now,
            )
        return message

    def list_messages(self, auth: AuthContext, conversation_id: str) -> list[MessageRecord]:
        conversation = self.get_conversation(auth, conversation_id)
        if conversation is None:
            return []
        with self._lock:
            return list(self._messages.get(conversation_id, []))

    def list_messages_for_context(self, auth: AuthContext, conversation_id: str, *, limit: int | None = 80) -> list[MessageRecord]:
        messages = self.list_messages(auth, conversation_id)
        if limit is not None:
            if limit <= 0:
                return []
            return messages[-limit:]
        return messages

    def get_conversation_memory(self, auth: AuthContext, conversation_id: str) -> ConversationMemoryRecord | None:
        conversation = self.get_conversation(auth, conversation_id)
        if conversation is None:
            return None
        with self._lock:
            memory = self._conversation_memories.get(conversation_id)
        if memory is None or not _is_authorized(auth, memory):
            return None
        return memory

    def upsert_conversation_memory(
        self,
        auth: AuthContext,
        conversation_id: str,
        *,
        summary: str,
        covered_message_id: str | None,
        estimated_tokens: int,
    ) -> ConversationMemoryRecord | None:
        conversation = self.get_conversation(auth, conversation_id)
        if conversation is None:
            return None
        now = _now()
        with self._lock:
            existing = self._conversation_memories.get(conversation_id)
            memory = ConversationMemoryRecord(
                id=existing.id if existing is not None else opaque_id("mem"),
                conversation_id=conversation.id,
                owner_user_id=auth.user_id,
                workspace_id=auth.workspace_id,
                summary=summary,
                covered_message_id=covered_message_id,
                summary_version=(existing.summary_version + 1) if existing is not None else 1,
                estimated_tokens=max(0, estimated_tokens),
                created_at=existing.created_at if existing is not None else now,
                updated_at=now,
            )
            self._conversation_memories[conversation_id] = memory
        return memory

    def get_conversation_state_snapshot(
        self,
        auth: AuthContext,
        conversation_id: str,
        *,
        event_limit: int = 30,
        message_limit: int = 100,
    ) -> ConversationStateSnapshotRecord | None:
        conversation = self.get_conversation(auth, conversation_id)
        if conversation is None:
            return None
        bounded_message_limit = _bounded_state_message_limit(message_limit)
        with self._lock:
            all_messages = list(self._messages.get(conversation.id, []))
            message_count = len(all_messages)
            messages = all_messages[-bounded_message_limit:] if bounded_message_limit > 0 else []
            runs = [
                run
                for run in self._runs.values()
                if run.conversation_id == conversation.id
                and run.owner_user_id == auth.user_id
                and run.workspace_id == auth.workspace_id
            ]
            sorted_runs = sorted(runs, key=lambda run: (run.updated_at, run.created_at, run.id), reverse=True)
            latest_run = sorted_runs[0] if sorted_runs else None
            artifacts = (
                [
                    artifact
                    for artifact in self._artifacts.values()
                    if artifact.run_id == latest_run.id
                    and artifact.owner_user_id == auth.user_id
                    and artifact.workspace_id == auth.workspace_id
                ]
                if latest_run is not None
                else []
            )
            all_conversation_artifacts = sorted(
                [
                    artifact
                    for artifact in self._artifacts.values()
                    if artifact.conversation_id == conversation.id
                    and artifact.owner_user_id == auth.user_id
                    and artifact.workspace_id == auth.workspace_id
                    and is_user_visible_artifact_kind(artifact.kind)
                ],
                key=lambda artifact: (artifact.created_at, artifact.id),
                reverse=True,
            )
            conversation_artifacts_page = all_conversation_artifacts[: CONVERSATION_ARTIFACT_STATE_LIMIT + 1]
            conversation_artifacts = conversation_artifacts_page[:CONVERSATION_ARTIFACT_STATE_LIMIT]
            conversation_artifacts_next_cursor = (
                encode_artifact_page_cursor(conversation_artifacts[-1])
                if len(conversation_artifacts_page) > CONVERSATION_ARTIFACT_STATE_LIMIT and conversation_artifacts
                else None
            )
            events = list(self._run_events.get(latest_run.id, [])) if latest_run is not None else []
            conversation_events = sorted(
                [
                    event
                    for run in runs
                    for event in self._run_events.get(run.id, [])
                ],
                key=lambda event: (event.created_at, event.run_id, event.sequence, event.id),
            )
            latest_strategy_spec = (
                next(
                    (
                        spec
                        for spec in self._strategy_specs.values()
                        if spec.run_id == latest_run.id
                        and spec.owner_user_id == auth.user_id
                        and spec.workspace_id == auth.workspace_id
                    ),
                    None,
                )
                if latest_run is not None
                else None
            )
        bounded_events = events[-max(event_limit, 0) :] if event_limit > 0 else []
        bounded_conversation_events = (
            conversation_events[-max(event_limit * 4, event_limit, 0) :] if event_limit > 0 else []
        )
        return ConversationStateSnapshotRecord(
            conversation=conversation,
            messages=messages,
            message_count=message_count,
            messages_truncated=message_count > len(messages),
            message_limit=bounded_message_limit,
            latest_run=latest_run,
            latest_run_artifacts=artifacts,
            conversation_artifacts=conversation_artifacts,
            conversation_artifacts_next_cursor=conversation_artifacts_next_cursor,
            latest_run_events=bounded_events,
            conversation_run_events=bounded_conversation_events,
            latest_strategy_spec=latest_strategy_spec,
        )

    def create_run(
        self,
        auth: AuthContext,
        conversation_id: str,
        *,
        status: str = "running",
        mode: str | None = None,
        retry_of_run_id: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> AssistantRunRecord | None:
        now = _now()
        with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or not _is_authorized(auth, conversation):
                return None
            run = AssistantRunRecord(
                id=opaque_id("run"),
                conversation_id=conversation.id,
                owner_user_id=auth.user_id,
                workspace_id=auth.workspace_id,
                status=status,
                created_at=now,
                updated_at=now,
                mode=mode,
                retry_of_run_id=retry_of_run_id,
                request_id=request_id or opaque_id("req"),
                trace_id=trace_id or opaque_id("trace"),
            )
            self._runs[run.id] = run
            self._run_events[run.id] = []
        return run

    def get_run(self, auth: AuthContext, run_id: str) -> AssistantRunRecord | None:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None or not _is_run_authorized(auth, run):
            return None
        return run

    def create_run_job(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        job_type: str,
        payload_json: dict,
        max_attempts: int = BACKTEST_JOB_MAX_ATTEMPTS,
    ) -> RunJobRecord | None:
        now = _now()
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or not _is_run_authorized(auth, run):
                return None
            job = RunJobRecord(
                id=opaque_id("job"),
                run_id=run.id,
                owner_user_id=run.owner_user_id,
                workspace_id=run.workspace_id,
                job_type=job_type,
                status="queued",
                payload_json=payload_json,
                attempts=0,
                max_attempts=max_attempts,
                lease_owner=None,
                leased_until=None,
                result_json=None,
                error_code=None,
                created_at=now,
                updated_at=now,
            )
            self._run_jobs[job.id] = job
            return job

    def claim_run_job(self, *, job_type: str, worker_id: str, lease_seconds: int = 300) -> RunJobRecord | None:
        now = _now()
        with self._lock:
            queued = [
                job
                for job in self._run_jobs.values()
                if job.job_type == job_type
                and job.attempts < job.max_attempts
                and (
                    job.status == "queued"
                    or (job.status == "running" and job.leased_until is not None and job.leased_until < now)
                )
            ]
            if not queued:
                return None
            job = None
            for candidate in sorted(queued, key=lambda item: (item.created_at, item.id)):
                active_limit = _job_workspace_active_limit(candidate)
                running_count = sum(
                    1
                    for item in self._run_jobs.values()
                    if item.job_type == candidate.job_type
                    and item.workspace_id == candidate.workspace_id
                    and item.status == "running"
                    and item.leased_until is not None
                    and item.leased_until >= now
                )
                if running_count < active_limit:
                    job = candidate
                    break
            if job is None:
                return None
            updated = RunJobRecord(
                id=job.id,
                run_id=job.run_id,
                owner_user_id=job.owner_user_id,
                workspace_id=job.workspace_id,
                job_type=job.job_type,
                status="running",
                payload_json=job.payload_json,
                attempts=job.attempts + 1,
                max_attempts=job.max_attempts,
                lease_owner=worker_id,
                leased_until=now + timedelta(seconds=lease_seconds),
                result_json=job.result_json,
                error_code=job.error_code,
                created_at=job.created_at,
                updated_at=now,
            )
            self._run_jobs[job.id] = updated
            return updated

    def complete_run_job(
        self,
        job_id: str,
        *,
        status: str,
        result_json: dict | None = None,
        error_code: str | None = None,
    ) -> RunJobRecord | None:
        now = _now()
        with self._lock:
            job = self._run_jobs.get(job_id)
            if job is None:
                return None
            updated = RunJobRecord(
                id=job.id,
                run_id=job.run_id,
                owner_user_id=job.owner_user_id,
                workspace_id=job.workspace_id,
                job_type=job.job_type,
                status=status,
                payload_json=job.payload_json,
                attempts=job.attempts,
                max_attempts=job.max_attempts,
                lease_owner=None,
                leased_until=None,
                result_json=result_json,
                error_code=error_code,
                created_at=job.created_at,
                updated_at=now,
            )
            self._run_jobs[job.id] = updated
            return updated

    def cancel_run_jobs(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        statuses: tuple[str, ...] = ("queued", "running"),
        result_json: dict | None = None,
        error_code: str | None = None,
    ) -> int:
        now = _now()
        cancelled = 0
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or not _is_run_authorized(auth, run):
                return 0
            for job in list(self._run_jobs.values()):
                if job.run_id != run_id or job.status not in statuses:
                    continue
                self._run_jobs[job.id] = RunJobRecord(
                    id=job.id,
                    run_id=job.run_id,
                    owner_user_id=job.owner_user_id,
                    workspace_id=job.workspace_id,
                    job_type=job.job_type,
                    status="cancelled",
                    payload_json=job.payload_json,
                    attempts=job.attempts,
                    max_attempts=job.max_attempts,
                    lease_owner=None,
                    leased_until=None,
                    result_json=result_json,
                    error_code=error_code,
                    created_at=job.created_at,
                    updated_at=now,
                )
                cancelled += 1
        return cancelled

    def get_run_job(self, job_id: str) -> RunJobRecord | None:
        with self._lock:
            return self._run_jobs.get(job_id)

    def run_queue_stats(self, *, job_type: str | None = None) -> RunQueueStatsRecord:
        now = _now()
        with self._lock:
            jobs = [job for job in self._run_jobs.values() if job_type is None or job.job_type == job_type]
            queued = [job for job in jobs if job.status == "queued"]
            running = [job for job in jobs if job.status == "running"]
            active_running = [
                job for job in running if job.leased_until is not None and job.leased_until >= now
            ]
            failed = [job for job in jobs if job.status == "failed"]
            oldest = min((job.created_at for job in queued), default=None)
            oldest_running = min((job.updated_at for job in active_running), default=None)
        return RunQueueStatsRecord(
            queued=len(queued),
            running=len(running),
            oldest_queued_seconds=int((now - oldest).total_seconds()) if oldest is not None else None,
            oldest_running_seconds=int((now - oldest_running).total_seconds()) if oldest_running is not None else None,
            failed=len(failed),
            active_running=len(active_running),
            stale_running=len(running) - len(active_running),
        )

    def list_runs(self, auth: AuthContext, conversation_id: str) -> list[AssistantRunRecord] | None:
        conversation = self.get_conversation(auth, conversation_id)
        if conversation is None:
            return None
        with self._lock:
            rows = [
                run
                for run in self._runs.values()
                if run.conversation_id == conversation.id
                and run.owner_user_id == auth.user_id
                and run.workspace_id == auth.workspace_id
            ]
        return sorted(rows, key=lambda run: (run.updated_at, run.created_at, run.id), reverse=True)

    def append_run_event(
        self,
        auth: AuthContext,
        run_id: str,
        event_type: str,
        payload: dict | None = None,
    ) -> RunEventRecord | None:
        events = self.append_run_events(auth, run_id, [(event_type, payload)])
        if not events:
            return None
        return events[0]

    def append_run_events(
        self,
        auth: AuthContext,
        run_id: str,
        events: list[RunEventInput],
    ) -> list[RunEventRecord] | None:
        now = _now()
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or not _is_run_authorized(auth, run):
                return None
            existing = self._run_events.setdefault(run.id, [])
            created = [
                RunEventRecord(
                    id=opaque_id("evt"),
                    run_id=run.id,
                    conversation_id=run.conversation_id,
                    owner_user_id=run.owner_user_id,
                    workspace_id=run.workspace_id,
                    sequence=len(existing) + index,
                    type=event_type,
                    payload=payload,
                    created_at=now,
                    request_id=run.request_id,
                    trace_id=run.trace_id,
                )
                for index, (event_type, payload) in enumerate(events, start=1)
            ]
            existing.extend(created)
        return created

    def list_run_events(self, auth: AuthContext, run_id: str) -> list[RunEventRecord] | None:
        run = self.get_run(auth, run_id)
        if run is None:
            return None
        with self._lock:
            return list(self._run_events.get(run.id, []))

    def list_run_events_after(
        self,
        auth: AuthContext,
        run_id: str,
        last_event_id: str | None = None,
    ) -> list[RunEventRecord] | None:
        events = self.list_run_events(auth, run_id)
        if events is None:
            return None
        return _events_after_last_id(events, last_event_id)

    def list_workflow_task_continuation_events(
        self,
        auth: AuthContext,
        task_id: str,
    ) -> list[RunEventRecord] | None:
        task = self.get_workflow_task(auth, task_id)
        if task is None:
            return None
        with self._lock:
            events = [
                event
                for event_list in self._run_events.values()
                for event in event_list
                if event.conversation_id == task.conversation_id
                and event.owner_user_id == auth.user_id
                and event.workspace_id == auth.workspace_id
                and event.type in WORKFLOW_CONTINUATION_EVENT_TYPES
                and isinstance(event.payload, dict)
                and event.payload.get("task_id") == task.id
            ]
        return sorted(events, key=lambda event: (event.created_at, event.run_id, event.sequence, event.id))

    def upsert_workflow_task(
        self,
        auth: AuthContext,
        *,
        conversation_id: str,
        workflow_id: str,
        task_template_id: str,
        step_id: str,
        kind: str,
        status: str,
        payload_json: dict,
        run_id: str | None = None,
    ) -> WorkflowTaskRecord | None:
        conversation = self.get_conversation(auth, conversation_id)
        if conversation is None:
            return None
        if run_id is not None and self.get_run(auth, run_id) is None:
            return None
        now = _now()
        with self._lock:
            existing = next(
                (
                    task
                    for task in self._workflow_tasks.values()
                    if task.conversation_id == conversation.id
                    and task.workflow_id == workflow_id
                    and task.task_template_id == task_template_id
                    and task.owner_user_id == auth.user_id
                    and task.workspace_id == auth.workspace_id
                ),
                None,
            )
            task = WorkflowTaskRecord(
                id=existing.id if existing is not None else opaque_id("wft"),
                conversation_id=conversation.id,
                run_id=run_id,
                owner_user_id=auth.user_id,
                workspace_id=auth.workspace_id,
                workflow_id=workflow_id,
                task_template_id=task_template_id,
                step_id=step_id,
                kind=kind,
                status=status,
                payload_json=payload_json,
                response_json=existing.response_json if existing is not None else None,
                created_at=existing.created_at if existing is not None else now,
                updated_at=now,
                resolved_at=(
                    existing.resolved_at
                    if existing is not None and status in WORKFLOW_TASK_RESOLVED_STATUSES
                    else None
                ),
            )
            self._workflow_tasks[task.id] = task
            return task

    def sync_workflow_tasks(
        self,
        auth: AuthContext,
        *,
        conversation_id: str,
        run_id: str | None,
        workflow_id: str,
        task_payloads: list[dict],
        completed_steps: set[str],
    ) -> WorkflowTaskSyncResult | None:
        conversation = self.get_conversation(auth, conversation_id)
        if conversation is None:
            return None
        if run_id is not None:
            run = self.get_run(auth, run_id)
            if run is None or run.conversation_id != conversation.id:
                return None
        now = _now()
        with self._lock:
            existing_records = [
                task
                for task in self._workflow_tasks.values()
                if task.conversation_id == conversation.id
                and task.workflow_id == workflow_id
                and _is_authorized(auth, task)
            ]
            existing_by_template = {task.task_template_id: task for task in existing_records}
            created: list[WorkflowTaskRecord] = []
            updated: list[WorkflowTaskRecord] = []
            unchanged: list[WorkflowTaskRecord] = []
            requested_templates: set[str] = set()

            for payload_json in task_payloads:
                task_template_id = payload_json.get("task_template_id")
                if not isinstance(task_template_id, str):
                    continue
                requested_templates.add(task_template_id)
                existing = existing_by_template.get(task_template_id)
                if existing is not None and existing.status in WORKFLOW_TASK_RESOLVED_STATUSES:
                    unchanged.append(existing)
                    continue

                status = payload_json.get("status")
                step_id = payload_json.get("step_id")
                kind = payload_json.get("kind")
                if not isinstance(status, str) or not isinstance(step_id, str) or not isinstance(kind, str):
                    continue
                task_payload = dict(payload_json)
                task_payload["status"] = status
                if existing is None:
                    task = WorkflowTaskRecord(
                        id=opaque_id("wft"),
                        conversation_id=conversation.id,
                        run_id=run_id,
                        owner_user_id=auth.user_id,
                        workspace_id=auth.workspace_id,
                        workflow_id=workflow_id,
                        task_template_id=task_template_id,
                        step_id=step_id,
                        kind=kind,
                        status=status,
                        payload_json=task_payload,
                        response_json=None,
                        created_at=now,
                        updated_at=now,
                        resolved_at=None,
                    )
                    self._workflow_tasks[task.id] = task
                    existing_by_template[task_template_id] = task
                    created.append(task)
                    continue

                if _workflow_task_payload_matches(existing, run_id=run_id, payload_json=task_payload):
                    unchanged.append(existing)
                    continue

                task = replace(
                    existing,
                    run_id=run_id,
                    step_id=step_id,
                    kind=kind,
                    status=status,
                    payload_json=task_payload,
                    updated_at=now,
                    resolved_at=existing.resolved_at if status in WORKFLOW_TASK_RESOLVED_STATUSES else None,
                )
                self._workflow_tasks[task.id] = task
                existing_by_template[task_template_id] = task
                updated.append(task)

            resolved: list[WorkflowTaskRecord] = []
            for task in list(existing_by_template.values()):
                if (
                    task.task_template_id not in requested_templates
                    and task.step_id in completed_steps
                    and task.status not in WORKFLOW_TASK_RESOLVED_STATUSES
                ):
                    resolved_task = replace(
                        task,
                        status="completed",
                        updated_at=now,
                        resolved_at=now,
                    )
                    self._workflow_tasks[task.id] = resolved_task
                    existing_by_template[task.task_template_id] = resolved_task
                    resolved.append(resolved_task)

            records = sorted(existing_by_template.values(), key=lambda task: (task.created_at, task.id))
            resolved_ids = {task.id for task in resolved}
            unchanged = [task for task in unchanged if task.id not in resolved_ids]
            return WorkflowTaskSyncResult(
                records=records,
                created=created,
                updated=updated,
                resolved=resolved,
                unchanged=unchanged,
            )

    def list_workflow_tasks(self, auth: AuthContext, conversation_id: str) -> list[WorkflowTaskRecord] | None:
        conversation = self.get_conversation(auth, conversation_id)
        if conversation is None:
            return None
        with self._lock:
            tasks = [
                task
                for task in self._workflow_tasks.values()
                if task.conversation_id == conversation.id and _is_authorized(auth, task)
            ]
        return sorted(tasks, key=lambda task: (task.created_at, task.id))

    def get_workflow_task(self, auth: AuthContext, task_id: str) -> WorkflowTaskRecord | None:
        with self._lock:
            task = self._workflow_tasks.get(task_id)
        if task is None or not _is_authorized(auth, task):
            return None
        return task

    def submit_workflow_task_response(
        self,
        auth: AuthContext,
        task_id: str,
        *,
        response_json: dict,
        status: str = "completed",
    ) -> WorkflowTaskRecord | None:
        return self.resolve_workflow_task(auth, task_id, status=status, response_json=response_json)

    def resolve_workflow_task(
        self,
        auth: AuthContext,
        task_id: str,
        *,
        status: str,
        response_json: dict | None = None,
    ) -> WorkflowTaskRecord | None:
        now = _now()
        with self._lock:
            task = self._workflow_tasks.get(task_id)
            if task is None or not _is_authorized(auth, task):
                return None
            updated = replace(
                task,
                status=status,
                response_json=response_json if response_json is not None else task.response_json,
                updated_at=now,
                resolved_at=now if status in WORKFLOW_TASK_RESOLVED_STATUSES else None,
            )
            self._workflow_tasks[task.id] = updated
            return updated

    def summarize_run_events(self, auth: AuthContext, run_id: str) -> RunEventSummaryRecord | None:
        run = self.get_run(auth, run_id)
        if run is None:
            return None
        with self._lock:
            events = self._run_events.get(run.id, [])
            return RunEventSummaryRecord(event_count=len(events), latest_event=events[-1] if events else None)

    def get_run_progress_snapshot(self, auth: AuthContext, run_id: str) -> RunProgressSnapshotRecord | None:
        run = self.get_run(auth, run_id)
        if run is None:
            return None
        event_summary = self.summarize_run_events(auth, run.id)
        artifacts = self.list_artifacts(auth, run.id)
        if event_summary is None or artifacts is None:
            return None
        return RunProgressSnapshotRecord(run=run, event_summary=event_summary, artifacts=artifacts)

    def set_run_status(self, auth: AuthContext, run_id: str, status: str) -> AssistantRunRecord | None:
        now = _now()
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or not _is_run_authorized(auth, run):
                return None
            updated = AssistantRunRecord(
                id=run.id,
                conversation_id=run.conversation_id,
                owner_user_id=run.owner_user_id,
                workspace_id=run.workspace_id,
                status=status,
                created_at=run.created_at,
                updated_at=now,
                mode=run.mode,
                retry_of_run_id=run.retry_of_run_id,
                request_id=run.request_id,
                trace_id=run.trace_id,
            )
            self._runs[run.id] = updated
        return updated

    def create_strategy_spec(
        self,
        auth: AuthContext,
        run_id: str,
        payload: dict,
        schema_version: str,
    ) -> StrategySpecRecord | None:
        now = _now()
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or not _is_run_authorized(auth, run):
                return None
            spec = StrategySpecRecord(
                id=opaque_id("spec"),
                run_id=run.id,
                owner_user_id=run.owner_user_id,
                workspace_id=run.workspace_id,
                payload_json=payload,
                schema_version=schema_version,
                created_at=now,
            )
            self._strategy_specs[spec.id] = spec
        return spec

    def get_strategy_spec_for_run(self, auth: AuthContext, run_id: str) -> StrategySpecRecord | None:
        run = self.get_run(auth, run_id)
        if run is None:
            return None
        with self._lock:
            rows = [
                spec
                for spec in self._strategy_specs.values()
                if spec.run_id == run.id
                and spec.owner_user_id == auth.user_id
                and spec.workspace_id == auth.workspace_id
            ]
        return sorted(rows, key=lambda spec: (spec.created_at, spec.id), reverse=True)[0] if rows else None

    def create_artifact(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        kind: str,
        mime_type: str | None,
        display_name: str,
        storage_key: str,
        metadata_json: dict | None = None,
    ) -> ArtifactRecord | None:
        now = _now()
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or not _is_run_authorized(auth, run):
                return None
            artifact = ArtifactRecord(
                id=opaque_id("art"),
                run_id=run.id,
                conversation_id=run.conversation_id,
                owner_user_id=run.owner_user_id,
                workspace_id=run.workspace_id,
                kind=kind,
                mime_type=mime_type,
                display_name=display_name,
                storage_key=storage_key,
                metadata_json=metadata_json,
                created_at=now,
            )
            self._artifacts[artifact.id] = artifact
        return artifact

    def create_artifacts(
        self,
        auth: AuthContext,
        run_id: str,
        artifacts: list[ArtifactInput],
    ) -> list[ArtifactRecord] | None:
        now = _now()
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or not _is_run_authorized(auth, run):
                return None
            created = [
                ArtifactRecord(
                    id=opaque_id("art"),
                    run_id=run.id,
                    conversation_id=run.conversation_id,
                    owner_user_id=run.owner_user_id,
                    workspace_id=run.workspace_id,
                    kind=kind,
                    mime_type=mime_type,
                    display_name=display_name,
                    storage_key=storage_key,
                    metadata_json=metadata_json,
                    created_at=now,
                )
                for kind, mime_type, display_name, storage_key, metadata_json in artifacts
            ]
            for artifact in created:
                self._artifacts[artifact.id] = artifact
        return created

    def list_artifacts(self, auth: AuthContext, run_id: str) -> list[ArtifactRecord] | None:
        run = self.get_run(auth, run_id)
        if run is None:
            return None
        with self._lock:
            return sorted(
                [
                artifact
                for artifact in self._artifacts.values()
                if artifact.run_id == run.id
                and artifact.owner_user_id == auth.user_id
                and artifact.workspace_id == auth.workspace_id
                ],
                key=lambda artifact: (artifact.created_at, artifact.storage_key, artifact.id),
            )

    def get_artifact(self, auth: AuthContext, artifact_id: str) -> ArtifactRecord | None:
        with self._lock:
            artifact = self._artifacts.get(artifact_id)
        if artifact is None:
            return None
        if artifact.owner_user_id != auth.user_id or artifact.workspace_id != auth.workspace_id:
            return None
        return artifact

    def list_workspace_artifacts_page(
        self,
        auth: AuthContext,
        *,
        limit: int = CONVERSATION_ARTIFACT_STATE_LIMIT,
        cursor: str | None = None,
        visibility: ArtifactVisibilityFilter = "user",
    ) -> ArtifactPageRecord:
        return self._list_artifact_page(auth, limit=limit, cursor=cursor, visibility=visibility)

    def list_conversation_artifacts_page(
        self,
        auth: AuthContext,
        conversation_id: str,
        *,
        limit: int = CONVERSATION_ARTIFACT_STATE_LIMIT,
        cursor: str | None = None,
        visibility: ArtifactVisibilityFilter = "user",
    ) -> ArtifactPageRecord | None:
        conversation = self.get_conversation(auth, conversation_id)
        if conversation is None:
            return None
        return self._list_artifact_page(
            auth,
            conversation_id=conversation.id,
            limit=limit,
            cursor=cursor,
            visibility=visibility,
        )

    def _list_artifact_page(
        self,
        auth: AuthContext,
        *,
        conversation_id: str | None = None,
        limit: int = CONVERSATION_ARTIFACT_STATE_LIMIT,
        cursor: str | None = None,
        visibility: ArtifactVisibilityFilter = "user",
    ) -> ArtifactPageRecord:
        bounded_limit = bounded_artifact_page_limit(limit)
        cursor_value = decode_artifact_page_cursor(cursor)
        with self._lock:
            artifacts = [
                artifact
                for artifact in self._artifacts.values()
                if (conversation_id is None or artifact.conversation_id == conversation_id)
                and artifact.owner_user_id == auth.user_id
                and artifact.workspace_id == auth.workspace_id
                and (visibility == "all" or is_user_visible_artifact_kind(artifact.kind))
                and (
                    cursor_value is None
                    or (artifact.created_at, artifact.id) < cursor_value
                )
            ]
        page = sorted(artifacts, key=lambda artifact: (artifact.created_at, artifact.id), reverse=True)[
            : bounded_limit + 1
        ]
        items = page[:bounded_limit]
        next_cursor = encode_artifact_page_cursor(items[-1]) if len(page) > bounded_limit and items else None
        return ArtifactPageRecord(items=items, next_cursor=next_cursor)

    def get_backtest_summary(self, auth: AuthContext, run_id: str) -> dict | None:
        return None if self.get_run(auth, run_id) is not None else None

    def get_backtest_summaries(self, auth: AuthContext, run_ids: list[str]) -> dict[str, dict]:
        summaries: dict[str, dict] = {}
        for run_id in dict.fromkeys(run_ids):
            summary = self.get_backtest_summary(auth, run_id)
            if isinstance(summary, dict):
                summaries[run_id] = summary
        return summaries

    def resolve_backtest_report_run_id(self, auth: AuthContext, conversation_id: str, requested_run_id: str) -> str | None:
        requested_run = self.get_run(auth, requested_run_id)
        if requested_run is not None and requested_run.conversation_id == conversation_id:
            return requested_run.id
        runs = self.list_runs(auth, conversation_id)
        if runs is None:
            return None
        backtest_runs = [run for run in runs if run.mode == "backtest-preview" and run.status == "completed"]
        if not backtest_runs:
            return None
        return max(backtest_runs, key=lambda run: (run.updated_at, run.created_at, run.id)).id

    def query_backtest_trades(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        bucket: str | None = None,
        limit: int = 20,
    ) -> list[dict] | None:
        return [] if self.get_run(auth, run_id) is not None else None

    def get_backtest_equity_summary(self, auth: AuthContext, run_id: str) -> dict | None:
        return None if self.get_run(auth, run_id) is not None else None

    def get_backtest_equity_summaries(self, auth: AuthContext, run_ids: list[str]) -> dict[str, dict]:
        summaries: dict[str, dict] = {}
        for run_id in dict.fromkeys(run_ids):
            summary = self.get_backtest_equity_summary(auth, run_id)
            if isinstance(summary, dict):
                summaries[run_id] = summary
        return summaries

    def create_validation_report(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        status: str,
        payload: dict,
    ) -> ValidationReportRecord | None:
        now = _now()
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or not _is_run_authorized(auth, run):
                return None
            report = ValidationReportRecord(
                id=opaque_id("val"),
                run_id=run.id,
                owner_user_id=run.owner_user_id,
                workspace_id=run.workspace_id,
                status=status,
                payload_json=payload,
                created_at=now,
            )
            self._validation_reports[report.id] = report
        return report

    def create_review_report(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        decision: str,
        payload: dict,
    ) -> ReviewReportRecord | None:
        now = _now()
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or not _is_run_authorized(auth, run):
                return None
            report = ReviewReportRecord(
                id=opaque_id("rev"),
                run_id=run.id,
                owner_user_id=run.owner_user_id,
                workspace_id=run.workspace_id,
                decision=decision,
                payload_json=payload,
                created_at=now,
            )
            self._review_reports[report.id] = report
        return report

    def create_tool_call(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        tool_id: str,
        status: str,
        input_json: dict | None = None,
        policy_findings_json: list | None = None,
    ) -> ToolCallRecord | None:
        now = _now()
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or not _is_run_authorized(auth, run):
                return None
            tool_call = ToolCallRecord(
                id=opaque_id("toolcall"),
                run_id=run.id,
                tool_id=tool_id,
                status=status,
                input_json=input_json,
                output_json=None,
                policy_findings_json=policy_findings_json,
                created_at=now,
                started_at=now if status == "running" else None,
            )
            self._tool_calls[tool_call.id] = tool_call
        return tool_call

    def complete_tool_call(
        self,
        auth: AuthContext,
        tool_call_id: str,
        *,
        status: str,
        output_json: dict | None = None,
        policy_findings_json: list | None = None,
    ) -> ToolCallRecord | None:
        now = _now()
        with self._lock:
            tool_call = self._tool_calls.get(tool_call_id)
            if tool_call is None:
                return None
            run = self._runs.get(tool_call.run_id)
            if run is None or not _is_run_authorized(auth, run):
                return None
            updated = ToolCallRecord(
                id=tool_call.id,
                run_id=tool_call.run_id,
                tool_id=tool_call.tool_id,
                status=status,
                input_json=tool_call.input_json,
                output_json=output_json,
                policy_findings_json=policy_findings_json or tool_call.policy_findings_json,
                created_at=tool_call.created_at,
                started_at=tool_call.started_at,
                completed_at=now,
            )
            self._tool_calls[updated.id] = updated
        return updated

    def create_policy_finding(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        severity: str,
        code: str,
        message: str,
        tool_call_id: str | None = None,
    ) -> PolicyFindingRecord | None:
        now = _now()
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or not _is_run_authorized(auth, run):
                return None
            finding = PolicyFindingRecord(
                id=opaque_id("pol"),
                run_id=run.id,
                tool_call_id=tool_call_id,
                owner_user_id=run.owner_user_id,
                workspace_id=run.workspace_id,
                severity=severity,
                code=code,
                message=message,
                created_at=now,
            )
            self._policy_findings[finding.id] = finding
        return finding

    def create_usage_ledger(
        self,
        auth: AuthContext,
        *,
        run_id: str | None,
        model: str | None,
        tool_id: str | None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_estimate_usd: float | None = None,
    ) -> UsageLedgerRecord | None:
        now = _now()
        with self._lock:
            if run_id is not None:
                run = self._runs.get(run_id)
                if run is None or not _is_run_authorized(auth, run):
                    return None
            record = UsageLedgerRecord(
                id=opaque_id("usage"),
                owner_user_id=auth.user_id,
                workspace_id=auth.workspace_id,
                run_id=run_id,
                model=model,
                tool_id=tool_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_estimate_usd=cost_estimate_usd,
                created_at=now,
            )
            self._usage_ledger[record.id] = record
        return record

    def list_tool_calls(self, auth: AuthContext, run_id: str) -> list[ToolCallRecord] | None:
        run = self.get_run(auth, run_id)
        if run is None:
            return None
        with self._lock:
            rows = [record for record in self._tool_calls.values() if record.run_id == run.id]
        return sorted(rows, key=lambda record: (record.created_at, record.id))

    def list_policy_findings(self, auth: AuthContext, run_id: str) -> list[PolicyFindingRecord] | None:
        run = self.get_run(auth, run_id)
        if run is None:
            return None
        with self._lock:
            rows = [record for record in self._policy_findings.values() if record.run_id == run.id]
        return sorted(rows, key=lambda record: (record.created_at, record.id))

    def list_usage_ledger(self, auth: AuthContext, run_id: str) -> list[UsageLedgerRecord] | None:
        run = self.get_run(auth, run_id)
        if run is None:
            return None
        with self._lock:
            rows = [record for record in self._usage_ledger.values() if record.run_id == run.id]
        return sorted(rows, key=lambda record: (record.created_at, record.id))

    def summarize_account_usage(self, auth: AuthContext) -> AccountUsageSummaryRecord:
        with self._lock:
            messages = [
                message
                for thread_messages in self._messages.values()
                for message in thread_messages
                if message.owner_user_id == auth.user_id and message.workspace_id == auth.workspace_id
            ]
            runs = [
                run
                for run in self._runs.values()
                if run.owner_user_id == auth.user_id and run.workspace_id == auth.workspace_id
            ]
            artifacts = [
                artifact
                for artifact in self._artifacts.values()
                if artifact.owner_user_id == auth.user_id and artifact.workspace_id == auth.workspace_id
            ]
            usage_rows = [
                record
                for record in self._usage_ledger.values()
                if record.owner_user_id == auth.user_id and record.workspace_id == auth.workspace_id
            ]
        input_tokens = sum(record.input_tokens for record in usage_rows)
        output_tokens = sum(record.output_tokens for record in usage_rows)
        cost_values = [record.cost_estimate_usd for record in usage_rows if record.cost_estimate_usd is not None]
        return AccountUsageSummaryRecord(
            messages=len(messages),
            runs=len(runs),
            artifacts=len(artifacts),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            estimated_cost_usd=sum(cost_values) if cost_values else None,
        )

    def upsert_nautilus_runtime(
        self,
        auth: AuthContext,
        *,
        runtime_key: str,
        broker_connection_id: str,
        account_id: str,
        mode: str,
        risk_policy_id: str,
        strategy_id: str,
        manifest_json: dict,
        data_subscriptions_json: list,
    ) -> NautilusRuntimeRecord:
        now = _now()
        with self._lock:
            existing = next(
                (
                    runtime
                    for runtime in self._nautilus_runtimes.values()
                    if runtime.runtime_key == runtime_key
                    and runtime.owner_user_id == auth.user_id
                    and runtime.workspace_id == auth.workspace_id
                ),
                None,
            )
            if existing is None:
                runtime = NautilusRuntimeRecord(
                    id=opaque_id("nrt"),
                    owner_user_id=auth.user_id,
                    workspace_id=auth.workspace_id,
                    runtime_key=runtime_key,
                    broker_connection_id=broker_connection_id,
                    account_id=account_id,
                    mode=mode,
                    risk_policy_id=risk_policy_id,
                    state="requested",
                    strategy_ids=[strategy_id],
                    manifest_json=manifest_json,
                    data_subscriptions_json=data_subscriptions_json,
                    last_heartbeat_at=None,
                    heartbeat_count=0,
                    heartbeat_metrics_json=None,
                    last_heartbeat_event_at=None,
                    kill_switch_active=False,
                    desired_state="running" if mode == "paper" else "requested",
                    worker_id=None,
                    lease_until=None,
                    generation=0,
                    started_at=None,
                    stopped_at=None,
                    last_error_json=None,
                    stream_cursor_json=None,
                    created_at=now,
                    updated_at=now,
                )
                self._nautilus_runtimes[runtime.id] = runtime
                self._nautilus_runtime_events[runtime.id] = []
                return runtime
            strategy_ids = list(existing.strategy_ids)
            if strategy_id not in strategy_ids:
                strategy_ids.append(strategy_id)
            updated = replace(
                existing,
                strategy_ids=strategy_ids,
                manifest_json=manifest_json or existing.manifest_json,
                data_subscriptions_json=data_subscriptions_json or existing.data_subscriptions_json,
                updated_at=now,
            )
            self._nautilus_runtimes[updated.id] = updated
            return updated

    def list_nautilus_runtimes(
        self,
        auth: AuthContext,
        *,
        mode: str | None = None,
        limit: int = 100,
    ) -> list[NautilusRuntimeRecord]:
        with self._lock:
            rows = [
                runtime
                for runtime in self._nautilus_runtimes.values()
                if _is_nautilus_runtime_authorized(auth, runtime)
                and (mode is None or runtime.mode == mode)
            ]
        return sorted(rows, key=lambda runtime: (runtime.updated_at, runtime.created_at, runtime.id), reverse=True)[
            : _bounded_nautilus_limit(limit)
        ]

    def get_nautilus_runtime(self, auth: AuthContext, runtime_id: str) -> NautilusRuntimeRecord | None:
        with self._lock:
            runtime = self._nautilus_runtimes.get(runtime_id)
        if runtime is None or not _is_nautilus_runtime_authorized(auth, runtime):
            return None
        return runtime

    def set_nautilus_runtime_state(
        self,
        auth: AuthContext,
        runtime_id: str,
        *,
        state: str,
    ) -> NautilusRuntimeRecord | None:
        now = _now()
        with self._lock:
            runtime = self._nautilus_runtimes.get(runtime_id)
            if runtime is None or not _is_nautilus_runtime_authorized(auth, runtime):
                return None
            updated = replace(runtime, state=state, updated_at=now)
            self._nautilus_runtimes[runtime.id] = updated
            return updated

    def record_nautilus_runtime_heartbeat(
        self,
        auth: AuthContext,
        runtime_id: str,
        *,
        now: datetime | None = None,
        payload: dict | None = None,
        idempotency_key: str | None = None,
    ) -> NautilusHeartbeatRecord | None:
        now = now or _now()
        with self._lock:
            runtime = self._nautilus_runtimes.get(runtime_id)
            if runtime is None or not _is_nautilus_runtime_authorized(auth, runtime):
                return None
            previous_state = runtime.state
            if runtime.kill_switch_active:
                state = runtime.state if runtime.state in {"stopping", "stopped", "failed"} else "stopping"
            else:
                state = nautilus_runtime_state_from_heartbeat_payload(payload)
            should_append_event = should_append_nautilus_heartbeat_event(runtime, state, now)
            event = None
            if should_append_event:
                event = self._append_nautilus_runtime_event_locked(
                    runtime,
                    "heartbeat",
                    payload,
                    now=now,
                    idempotency_key=idempotency_key,
                )
            updated = replace(
                runtime,
                state=state,
                last_heartbeat_at=now,
                heartbeat_count=runtime.heartbeat_count + 1,
                heartbeat_metrics_json=payload,
                last_heartbeat_event_at=now if event is not None else runtime.last_heartbeat_event_at,
                updated_at=now,
            )
            self._nautilus_runtimes[runtime.id] = updated
            return NautilusHeartbeatRecord(
                runtime=updated,
                event=event,
                event_appended=event is not None and event.created_at == now and (state != previous_state or should_append_event),
            )

    def activate_nautilus_runtime_kill_switch(
        self,
        auth: AuthContext,
        runtime_id: str,
    ) -> NautilusRuntimeRecord | None:
        now = _now()
        with self._lock:
            runtime = self._nautilus_runtimes.get(runtime_id)
            if runtime is None or not _is_nautilus_runtime_authorized(auth, runtime):
                return None
            updated = replace(
                runtime,
                state="stopping",
                desired_state="stopping",
                kill_switch_active=True,
                updated_at=now,
            )
            self._nautilus_runtimes[runtime.id] = updated
            return updated

    def set_nautilus_runtime_desired_state(
        self,
        auth: AuthContext,
        runtime_id: str,
        *,
        desired_state: str,
    ) -> NautilusRuntimeRecord | None:
        now = _now()
        with self._lock:
            runtime = self._nautilus_runtimes.get(runtime_id)
            if runtime is None or not _is_nautilus_runtime_authorized(auth, runtime):
                return None
            updated = replace(runtime, desired_state=desired_state, updated_at=now)
            self._nautilus_runtimes[runtime.id] = updated
            return updated

    def list_desired_nautilus_runtimes(
        self,
        *,
        mode: str = "paper",
        desired_state: str = "running",
        worker_id: str | None = None,
        limit: int = 100,
    ) -> list[NautilusRuntimeRecord]:
        now = _now()
        with self._lock:
            rows = [
                runtime
                for runtime in self._nautilus_runtimes.values()
                if runtime.mode == mode
                and runtime.desired_state == desired_state
                and runtime.state not in {"stopped", "failed"}
                and (
                    runtime.lease_until is None
                    or runtime.lease_until < now
                    or (worker_id is not None and runtime.worker_id == worker_id)
                )
            ]
        return sorted(rows, key=lambda runtime: (runtime.updated_at, runtime.created_at, runtime.id))[
            : _bounded_nautilus_limit(limit)
        ]

    def list_active_nautilus_market_data_subscriptions(
        self,
        *,
        mode: str = "paper",
        desired_state: str = "running",
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        with self._lock:
            payloads = [
                payload
                for runtime in self._nautilus_runtimes.values()
                if runtime.mode == mode
                and runtime.desired_state == desired_state
                and runtime.state not in {"stopped", "failed"}
                for payload in runtime.data_subscriptions_json
            ]
        return normalize_market_data_subscription_payloads(payloads, limit=limit)

    def claim_nautilus_runtime_lease(
        self,
        runtime_id: str,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> NautilusRuntimeRecord | None:
        current = now or _now()
        with self._lock:
            runtime = self._nautilus_runtimes.get(runtime_id)
            if runtime is None:
                return None
            lease_active = runtime.lease_until is not None and runtime.lease_until >= current
            if lease_active and runtime.worker_id != worker_id:
                return None
            generation = runtime.generation if runtime.worker_id == worker_id else runtime.generation + 1
            updated = replace(
                runtime,
                worker_id=worker_id,
                lease_until=current + timedelta(seconds=lease_seconds),
                generation=generation,
                state="provisioning" if runtime.state == "requested" else runtime.state,
                started_at=runtime.started_at or current,
                updated_at=current,
            )
            self._nautilus_runtimes[runtime.id] = updated
            return updated

    def renew_nautilus_runtime_lease(
        self,
        runtime_id: str,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> NautilusRuntimeRecord | None:
        current = now or _now()
        with self._lock:
            runtime = self._nautilus_runtimes.get(runtime_id)
            if runtime is None or runtime.worker_id != worker_id:
                return None
            updated = replace(
                runtime,
                lease_until=current + timedelta(seconds=lease_seconds),
                updated_at=current,
            )
            self._nautilus_runtimes[runtime.id] = updated
            return updated

    def release_nautilus_runtime_lease(
        self,
        runtime_id: str,
        *,
        worker_id: str,
        state: str | None = None,
        last_error_json: dict | None = None,
        now: datetime | None = None,
    ) -> NautilusRuntimeRecord | None:
        current = now or _now()
        with self._lock:
            runtime = self._nautilus_runtimes.get(runtime_id)
            if runtime is None or runtime.worker_id != worker_id:
                return None
            updated = replace(
                runtime,
                worker_id=None,
                lease_until=None,
                state=state or runtime.state,
                stopped_at=current if state in {"stopping", "stopped", "failed"} else runtime.stopped_at,
                last_error_json=last_error_json,
                updated_at=current,
            )
            self._nautilus_runtimes[runtime.id] = updated
            return updated

    def persist_nautilus_runtime_stream_cursor(
        self,
        runtime_id: str,
        *,
        worker_id: str,
        stream_cursor_json: dict,
        now: datetime | None = None,
    ) -> NautilusRuntimeRecord | None:
        current = now or _now()
        with self._lock:
            runtime = self._nautilus_runtimes.get(runtime_id)
            if runtime is None or runtime.worker_id != worker_id:
                return None
            updated = replace(runtime, stream_cursor_json=stream_cursor_json, updated_at=current)
            self._nautilus_runtimes[runtime.id] = updated
            return updated

    def append_nautilus_runtime_events_for_worker(
        self,
        runtime_id: str,
        *,
        worker_id: str,
        events: list[NautilusRuntimeEventInput],
    ) -> list[NautilusRuntimeEventRecord] | None:
        now = _now()
        with self._lock:
            runtime = self._nautilus_runtimes.get(runtime_id)
            if runtime is None or runtime.worker_id != worker_id:
                return None
            appended = [
                self._append_nautilus_runtime_event_locked(
                    runtime,
                    event_type,
                    payload,
                    now=now,
                    idempotency_key=idempotency_key,
                )
                for event_type, payload, idempotency_key in events
            ]
            return appended

    def append_nautilus_runtime_event(
        self,
        auth: AuthContext,
        runtime_id: str,
        event_type: str,
        payload: dict | None = None,
        idempotency_key: str | None = None,
    ) -> NautilusRuntimeEventRecord | None:
        now = _now()
        with self._lock:
            runtime = self._nautilus_runtimes.get(runtime_id)
            if runtime is None or not _is_nautilus_runtime_authorized(auth, runtime):
                return None
            return self._append_nautilus_runtime_event_locked(
                runtime,
                event_type,
                payload,
                now=now,
                idempotency_key=idempotency_key,
            )

    def _append_nautilus_runtime_event_locked(
        self,
        runtime: NautilusRuntimeRecord,
        event_type: str,
        payload: dict | None,
        *,
        now: datetime,
        idempotency_key: str | None = None,
    ) -> NautilusRuntimeEventRecord:
        existing = self._nautilus_runtime_events.setdefault(runtime.id, [])
        if idempotency_key is not None:
            for event in existing:
                if event.idempotency_key == idempotency_key:
                    return event
        event = NautilusRuntimeEventRecord(
            id=opaque_id("nevt"),
            runtime_id=runtime.id,
            owner_user_id=runtime.owner_user_id,
            workspace_id=runtime.workspace_id,
            sequence=len(existing) + 1,
            type=event_type,
            payload=payload,
            created_at=now,
            idempotency_key=idempotency_key,
        )
        existing.append(event)
        return event

    def list_nautilus_runtime_events(
        self,
        auth: AuthContext,
        runtime_id: str,
        *,
        limit: int = 100,
        after_sequence: int | None = None,
    ) -> list[NautilusRuntimeEventRecord] | None:
        runtime = self.get_nautilus_runtime(auth, runtime_id)
        if runtime is None:
            return None
        with self._lock:
            events = [
                event
                for event in self._nautilus_runtime_events.get(runtime.id, [])
                if after_sequence is None or event.sequence > after_sequence
            ]
        return events[: _bounded_nautilus_limit(limit)]

    def create_bot_proposal(
        self,
        auth: AuthContext,
        proposal: BotProposalCreateInput,
    ) -> BotProposalRecord:
        now = _now()
        record = BotProposalRecord(
            id=opaque_id("botp"),
            owner_user_id=auth.user_id,
            workspace_id=auth.workspace_id,
            status=proposal.status,
            source_conversation_id=proposal.source_conversation_id,
            source_run_id=proposal.source_run_id,
            source_artifact_ids=list(proposal.source_artifact_ids),
            strategy_id=proposal.strategy_id,
            strategy_name=proposal.strategy_name,
            manifest_json=proposal.manifest_json,
            data_subscriptions_json=list(proposal.data_subscriptions_json),
            broker_connection_id=proposal.broker_connection_id,
            account_id=proposal.account_id,
            risk_policy_id=proposal.risk_policy_id,
            readiness_checks_json=list(proposal.readiness_checks_json),
            missing_inputs_json=list(proposal.missing_inputs_json),
            runtime_id=None,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._bot_proposals[record.id] = record
        return record

    def get_bot_proposal(self, auth: AuthContext, proposal_id: str) -> BotProposalRecord | None:
        with self._lock:
            proposal = self._bot_proposals.get(proposal_id)
        if proposal is None or proposal.owner_user_id != auth.user_id or proposal.workspace_id != auth.workspace_id:
            return None
        return proposal

    def mark_bot_proposal_started(
        self,
        auth: AuthContext,
        proposal_id: str,
        *,
        runtime_id: str,
    ) -> BotProposalRecord | None:
        now = _now()
        with self._lock:
            proposal = self._bot_proposals.get(proposal_id)
            if proposal is None or proposal.owner_user_id != auth.user_id or proposal.workspace_id != auth.workspace_id:
                return None
            updated = replace(proposal, status=BOT_PROPOSAL_STATUS_STARTED, runtime_id=runtime_id, updated_at=now)
            self._bot_proposals[proposal_id] = updated
            return updated

    def cleanup_nautilus_heartbeat_events(
        self,
        auth: AuthContext,
        runtime_id: str,
        *,
        now: datetime | None = None,
        max_age: timedelta = NAUTILUS_HEARTBEAT_RETENTION_MAX_AGE,
        max_samples: int = NAUTILUS_HEARTBEAT_RETENTION_MAX_SAMPLES,
    ) -> int | None:
        runtime = self.get_nautilus_runtime(auth, runtime_id)
        if runtime is None:
            return None
        current = now or _now()
        cutoff = current - max_age
        with self._lock:
            events = self._nautilus_runtime_events.get(runtime.id, [])
            heartbeat_events = [event for event in events if event.type == "heartbeat"]
            keep_ids = {
                event.id
                for event in sorted(heartbeat_events, key=lambda event: (event.created_at, event.sequence), reverse=True)[
                    : max(0, max_samples)
                ]
            }
            retained = []
            removed = 0
            for event in events:
                if event.type != "heartbeat" or (event.id in keep_ids and event.created_at >= cutoff):
                    retained.append(event)
                else:
                    removed += 1
            self._nautilus_runtime_events[runtime.id] = retained
        return removed

    def create_feedback(
        self,
        auth: AuthContext,
        *,
        conversation_id: str,
        rating: str,
        correction: str,
        category: str | None = None,
        run_id: str | None = None,
        message_id: str | None = None,
        artifact_id: str | None = None,
    ) -> FeedbackRecord | None:
        now = _now()
        with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or not _is_authorized(auth, conversation):
                return None
            run = self._runs.get(run_id) if run_id else None
            if run_id is not None and (
                run is None or not _is_run_authorized(auth, run) or run.conversation_id != conversation_id
            ):
                return None
            if message_id is not None and not any(
                message.id == message_id
                and message.owner_user_id == auth.user_id
                and message.workspace_id == auth.workspace_id
                for message in self._messages.get(conversation_id, [])
            ):
                return None
            artifact = self._artifacts.get(artifact_id) if artifact_id else None
            if artifact_id is not None and (
                artifact is None
                or artifact.owner_user_id != auth.user_id
                or artifact.workspace_id != auth.workspace_id
                or (artifact.conversation_id is not None and artifact.conversation_id != conversation_id)
            ):
                return None
            record = FeedbackRecord(
                id=opaque_id("fb"),
                conversation_id=conversation_id,
                run_id=run_id,
                message_id=message_id,
                artifact_id=artifact_id,
                owner_user_id=auth.user_id,
                workspace_id=auth.workspace_id,
                request_id=run.request_id if run else None,
                trace_id=run.trace_id if run else None,
                rating=rating,
                category=category,
                correction=correction,
                created_at=now,
            )
            self._feedback[record.id] = record
        return record


def _now() -> datetime:
    return utc_now()


def encode_artifact_page_cursor(artifact: ArtifactRecord) -> str:
    payload = {
        "created_at": artifact.created_at.isoformat(),
        "id": artifact.id,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_artifact_page_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if cursor is None or not cursor.strip():
        return None
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode((cursor + padding).encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        created_at = datetime.fromisoformat(str(payload["created_at"]))
        artifact_id = str(payload["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid artifact cursor") from exc
    if not artifact_id:
        raise ValueError("invalid artifact cursor")
    return created_at, artifact_id


def bounded_artifact_page_limit(limit: int | None) -> int:
    if limit is None:
        return CONVERSATION_ARTIFACT_STATE_LIMIT
    return min(CONVERSATION_ARTIFACT_PAGE_MAX_LIMIT, max(1, int(limit)))


def is_user_visible_artifact_kind(kind: str) -> bool:
    return kind not in INTERNAL_ARTIFACT_KINDS


def _is_authorized(auth: AuthContext, conversation: ConversationRecord) -> bool:
    return conversation.owner_user_id == auth.user_id and conversation.workspace_id == auth.workspace_id


def _is_run_authorized(auth: AuthContext, run: AssistantRunRecord) -> bool:
    return run.owner_user_id == auth.user_id and run.workspace_id == auth.workspace_id


def _is_nautilus_runtime_authorized(auth: AuthContext, runtime: NautilusRuntimeRecord) -> bool:
    return runtime.owner_user_id == auth.user_id and runtime.workspace_id == auth.workspace_id


def _bounded_nautilus_limit(limit: int | None) -> int:
    if limit is None:
        return 100
    return min(500, max(1, int(limit)))


def _bounded_market_data_subscription_limit(limit: int | None) -> int:
    if limit is None:
        return 5000
    return min(5000, max(1, int(limit)))


def normalize_market_data_subscription_payloads(
    payloads: list[Any],
    *,
    limit: int | None = 5000,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        try:
            subscription = MarketDataStreamSubscription.from_payload(payload)
            subscription.stream_key()
        except Exception:
            continue
        key = (
            subscription.venue,
            subscription.symbol,
            subscription.data_type,
            subscription.timeframe or "",
        )
        if key in seen:
            continue
        seen.add(key)
        item: dict[str, Any] = {
            "venue": subscription.venue,
            "symbol": subscription.symbol,
            "data_type": subscription.data_type,
        }
        if subscription.timeframe:
            item["timeframe"] = subscription.timeframe
        normalized.append(item)
        if len(normalized) >= _bounded_market_data_subscription_limit(limit):
            break
    return normalized


def should_append_nautilus_heartbeat_event(
    runtime: NautilusRuntimeRecord,
    next_state: str,
    now: datetime,
) -> bool:
    if runtime.state != next_state:
        return True
    if runtime.last_heartbeat_event_at is None:
        return True
    return now - runtime.last_heartbeat_event_at >= NAUTILUS_HEARTBEAT_SAMPLE_INTERVAL


def nautilus_runtime_state_from_heartbeat_payload(payload: dict | None) -> str:
    metrics = payload.get("metrics") if isinstance(payload, dict) else None
    warmup_status = metrics.get("warmup_status") if isinstance(metrics, dict) else None
    if warmup_status in {"pending", "warming_up"}:
        return "warming_up"
    return "running"


def _job_workspace_active_limit(job: RunJobRecord) -> int:
    return backtest_active_limit_from_payload(job.payload_json)


def bounded_state_message_limit(message_limit: int | None) -> int:
    if message_limit is None:
        return 100
    return min(500, max(0, int(message_limit)))


def _bounded_state_message_limit(message_limit: int | None) -> int:
    return bounded_state_message_limit(message_limit)


def _sidebar_record(
    conversation: ConversationRecord,
    messages: list[MessageRecord],
    runs: list[AssistantRunRecord],
) -> ConversationSidebarRecord:
    latest_run = max(runs, key=lambda run: (run.updated_at, run.created_at, run.id), default=None)
    last_message = max(messages, key=lambda message: (message.created_at, message.id), default=None)
    return ConversationSidebarRecord(
        conversation=conversation,
        last_message_content=last_message.content if last_message is not None else None,
        last_message_at=last_message.created_at if last_message is not None else None,
        message_count=len(messages),
        latest_run_id=latest_run.id if latest_run is not None else None,
        latest_run_status=latest_run.status if latest_run is not None else None,
        updated_at=conversation.updated_at,
    )


def _events_after_last_id(events: list[RunEventRecord], last_event_id: str | None) -> list[RunEventRecord]:
    if not last_event_id:
        return events
    if last_event_id.isdecimal():
        sequence = int(last_event_id)
        return [event for event in events if event.sequence > sequence]
    for index, event in enumerate(events):
        if event.id == last_event_id:
            return events[index + 1 :]
    return events


def _workflow_task_payload_matches(
    task: WorkflowTaskRecord,
    *,
    run_id: str | None,
    payload_json: dict,
) -> bool:
    return (
        task.run_id == run_id
        and task.step_id == payload_json.get("step_id")
        and task.kind == payload_json.get("kind")
        and task.status == payload_json.get("status")
        and task.payload_json == payload_json
    )
