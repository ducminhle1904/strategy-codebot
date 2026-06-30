from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.artifact_kinds import INTERNAL_ARTIFACT_KINDS
from strategy_codebot.server.bot_proposal_status import BOT_PROPOSAL_STATUS_STARTED
from strategy_codebot.server.ids import opaque_id
from strategy_codebot.server.models import Artifact
from strategy_codebot.server.models import BacktestEquitySummary
from strategy_codebot.server.models import BacktestReport
from strategy_codebot.server.models import BacktestTradeIndex
from strategy_codebot.server.models import AssistantRun
from strategy_codebot.server.models import BotProposal
from strategy_codebot.server.models import ConversationMemory
from strategy_codebot.server.models import ConversationMessage
from strategy_codebot.server.models import ConversationThread
from strategy_codebot.server.models import Feedback
from strategy_codebot.server.models import NautilusRuntime
from strategy_codebot.server.models import NautilusRuntimeEvent
from strategy_codebot.server.models import ReviewReport
from strategy_codebot.server.models import RunEvent
from strategy_codebot.server.models import RunJob
from strategy_codebot.server.models import StrategySpec
from strategy_codebot.server.models import PolicyFinding
from strategy_codebot.server.models import ToolCall
from strategy_codebot.server.models import UsageLedger
from strategy_codebot.server.models import User
from strategy_codebot.server.models import ValidationReport
from strategy_codebot.server.models import WorkflowTask
from strategy_codebot.server.models import utc_now
from strategy_codebot.server.models import Workspace
from strategy_codebot.server.models import WorkspaceMembership
from strategy_codebot.server.repository import ArtifactRecord
from strategy_codebot.server.repository import ArtifactInput
from strategy_codebot.server.repository import ArtifactPageRecord
from strategy_codebot.server.repository import ArtifactVisibilityFilter
from strategy_codebot.server.repository import AccountUsageSummaryRecord
from strategy_codebot.server.repository import AssistantRunRecord
from strategy_codebot.server.repository import BotProposalCreateInput
from strategy_codebot.server.repository import BotProposalRecord
from strategy_codebot.server.repository import ConversationRecord
from strategy_codebot.server.repository import ConversationMemoryRecord
from strategy_codebot.server.repository import ConversationSidebarRecord
from strategy_codebot.server.repository import ConversationStateSnapshotRecord
from strategy_codebot.server.repository import FeedbackRecord
from strategy_codebot.server.repository import MessageRecord
from strategy_codebot.server.repository import NAUTILUS_HEARTBEAT_RETENTION_MAX_AGE
from strategy_codebot.server.repository import NAUTILUS_HEARTBEAT_RETENTION_MAX_SAMPLES
from strategy_codebot.server.repository import NautilusHeartbeatRecord
from strategy_codebot.server.repository import NautilusRuntimeEventInput
from strategy_codebot.server.repository import NautilusRuntimeEventRecord
from strategy_codebot.server.repository import NautilusRuntimeRecord
from strategy_codebot.server.repository import WORKFLOW_CONTINUATION_EVENT_TYPES
from strategy_codebot.server.repository import _bounded_market_data_subscription_limit
from strategy_codebot.server.repository import nautilus_runtime_state_from_heartbeat_payload
from strategy_codebot.server.repository import normalize_market_data_subscription_payloads
from strategy_codebot.server.repository import should_append_nautilus_heartbeat_event
from strategy_codebot.server.repository import ReviewReportRecord
from strategy_codebot.server.repository import RunEventInput
from strategy_codebot.server.repository import RunEventRecord
from strategy_codebot.server.repository import RunJobRecord
from strategy_codebot.server.repository import RunQueueStatsRecord
from strategy_codebot.server.repository import RunProgressSnapshotRecord
from strategy_codebot.server.repository import RunEventSummaryRecord
from strategy_codebot.server.repository import StrategySpecRecord
from strategy_codebot.server.repository import TERMINAL_RUN_STATUSES
from strategy_codebot.server.repository import bounded_state_message_limit
from strategy_codebot.server.repository import bounded_artifact_page_limit
from strategy_codebot.server.repository import CONVERSATION_ARTIFACT_STATE_LIMIT
from strategy_codebot.server.repository import decode_artifact_page_cursor
from strategy_codebot.server.repository import encode_artifact_page_cursor
from strategy_codebot.server.repository import PolicyFindingRecord
from strategy_codebot.server.repository import ToolCallRecord
from strategy_codebot.server.repository import UsageLedgerRecord
from strategy_codebot.server.repository import ValidationReportRecord
from strategy_codebot.server.repository import WorkflowTaskRecord
from strategy_codebot.server.repository import WorkflowTaskSyncResult
from strategy_codebot.server.repository import _workflow_task_payload_matches
from strategy_codebot.server.run_modes import BACKTEST_JOB_MAX_ATTEMPTS
from strategy_codebot.server.run_modes import backtest_active_limit_from_payload
from strategy_codebot.server.workflow_task_status import WORKFLOW_TASK_RESOLVED_STATUSES


class SQLAlchemyConversationRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_conversation(self, auth: AuthContext, title: str | None = None) -> ConversationRecord:
        now = utc_now()
        with self._session_factory() as session:
            _ensure_auth_entities(session, auth)
            conversation = ConversationThread(
                id=opaque_id("conv"),
                owner_user_id=auth.user_id,
                workspace_id=auth.workspace_id,
                title=title,
                mode="strategy_design",
                created_at=now,
                updated_at=now,
            )
            session.add(conversation)
            session.commit()
            return _conversation_record(conversation)

    def list_conversations(self, auth: AuthContext) -> list[ConversationRecord]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ConversationThread)
                .where(
                    ConversationThread.owner_user_id == auth.user_id,
                    ConversationThread.workspace_id == auth.workspace_id,
                    ConversationThread.deleted_at.is_(None),
                )
                .order_by(ConversationThread.updated_at.desc(), ConversationThread.id.desc())
            ).all()
            return [_conversation_record(row) for row in rows]

    def list_conversation_sidebar(self, auth: AuthContext) -> list[ConversationSidebarRecord]:
        with self._session_factory() as session:
            message_counts = (
                select(
                    ConversationMessage.conversation_id.label("conversation_id"),
                    func.count(ConversationMessage.id).label("message_count"),
                )
                .where(
                    ConversationMessage.owner_user_id == auth.user_id,
                    ConversationMessage.workspace_id == auth.workspace_id,
                )
                .group_by(ConversationMessage.conversation_id)
                .subquery()
            )
            latest_messages = (
                select(
                    ConversationMessage.conversation_id.label("conversation_id"),
                    ConversationMessage.content.label("content"),
                    ConversationMessage.created_at.label("created_at"),
                    func.row_number()
                    .over(
                        partition_by=ConversationMessage.conversation_id,
                        order_by=(ConversationMessage.created_at.desc(), ConversationMessage.id.desc()),
                    )
                    .label("rank"),
                )
                .where(
                    ConversationMessage.owner_user_id == auth.user_id,
                    ConversationMessage.workspace_id == auth.workspace_id,
                )
                .subquery()
            )
            latest_runs = (
                select(
                    AssistantRun.conversation_id.label("conversation_id"),
                    AssistantRun.id.label("id"),
                    AssistantRun.status.label("status"),
                    func.row_number()
                    .over(
                        partition_by=AssistantRun.conversation_id,
                        order_by=(AssistantRun.updated_at.desc(), AssistantRun.created_at.desc(), AssistantRun.id.desc()),
                    )
                    .label("rank"),
                )
                .where(
                    AssistantRun.owner_user_id == auth.user_id,
                    AssistantRun.workspace_id == auth.workspace_id,
                )
                .subquery()
            )
            rows = session.execute(
                select(ConversationThread)
                .add_columns(
                    message_counts.c.message_count,
                    latest_messages.c.content,
                    latest_messages.c.created_at,
                    latest_runs.c.id,
                    latest_runs.c.status,
                )
                .outerjoin(message_counts, message_counts.c.conversation_id == ConversationThread.id)
                .outerjoin(
                    latest_messages,
                    (latest_messages.c.conversation_id == ConversationThread.id) & (latest_messages.c.rank == 1),
                )
                .outerjoin(
                    latest_runs,
                    (latest_runs.c.conversation_id == ConversationThread.id) & (latest_runs.c.rank == 1),
                )
                .where(
                    ConversationThread.owner_user_id == auth.user_id,
                    ConversationThread.workspace_id == auth.workspace_id,
                    ConversationThread.deleted_at.is_(None),
                )
                .order_by(ConversationThread.updated_at.desc(), ConversationThread.id.desc())
            ).all()
            return [
                ConversationSidebarRecord(
                    conversation=_conversation_record(conversation),
                    last_message_content=last_message_content,
                    last_message_at=_utc_aware(last_message_at) if last_message_at is not None else None,
                    message_count=int(message_count or 0),
                    latest_run_id=latest_run_id,
                    latest_run_status=latest_run_status,
                    updated_at=_utc_aware(conversation.updated_at),
                )
                for (
                    conversation,
                    message_count,
                    last_message_content,
                    last_message_at,
                    latest_run_id,
                    latest_run_status,
                ) in rows
            ]

    def get_conversation(self, auth: AuthContext, conversation_id: str) -> ConversationRecord | None:
        with self._session_factory() as session:
            conversation = _authorized_conversation(session, auth, conversation_id)
            if conversation is None:
                return None
            return _conversation_record(conversation)

    def update_conversation_title(
        self,
        auth: AuthContext,
        conversation_id: str,
        title: str,
    ) -> ConversationRecord | None:
        now = utc_now()
        with self._session_factory() as session:
            conversation = _authorized_conversation(session, auth, conversation_id)
            if conversation is None:
                return None
            conversation.title = title
            conversation.updated_at = now
            session.commit()
            return _conversation_record(conversation)

    def delete_conversation(self, auth: AuthContext, conversation_id: str) -> ConversationRecord | None:
        now = utc_now()
        with self._session_factory() as session:
            conversation = _authorized_conversation(session, auth, conversation_id)
            if conversation is None:
                return None
            conversation.deleted_at = now
            conversation.updated_at = now
            session.commit()
            return _conversation_record(conversation)

    def create_message(
        self,
        auth: AuthContext,
        conversation_id: str,
        content: str,
        *,
        role: str = "user",
    ) -> MessageRecord | None:
        now = utc_now()
        with self._session_factory() as session:
            conversation = _authorized_conversation(session, auth, conversation_id)
            if conversation is None:
                return None
            message = ConversationMessage(
                id=opaque_id("msg"),
                conversation_id=conversation.id,
                owner_user_id=auth.user_id,
                workspace_id=auth.workspace_id,
                role=role,
                content=content,
                created_at=now,
            )
            conversation.updated_at = now
            session.add(message)
            session.commit()
            return _message_record(message)

    def list_messages(self, auth: AuthContext, conversation_id: str) -> list[MessageRecord]:
        with self._session_factory() as session:
            conversation = _authorized_conversation(session, auth, conversation_id)
            if conversation is None:
                return []
            rows = session.scalars(
                select(ConversationMessage)
                .where(
                    ConversationMessage.conversation_id == conversation.id,
                    ConversationMessage.owner_user_id == auth.user_id,
                    ConversationMessage.workspace_id == auth.workspace_id,
                )
                .order_by(ConversationMessage.created_at.asc(), ConversationMessage.id.asc())
            ).all()
            return [_message_record(row) for row in rows]

    def list_messages_for_context(self, auth: AuthContext, conversation_id: str, *, limit: int | None = 80) -> list[MessageRecord]:
        with self._session_factory() as session:
            conversation = _authorized_conversation(session, auth, conversation_id)
            if conversation is None:
                return []
            if limit is not None and limit <= 0:
                return []
            query = (
                select(ConversationMessage)
                .where(
                    ConversationMessage.conversation_id == conversation.id,
                    ConversationMessage.owner_user_id == auth.user_id,
                    ConversationMessage.workspace_id == auth.workspace_id,
                )
                .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
            )
            if limit is not None:
                query = query.limit(limit)
            rows = session.scalars(query).all()
            return [_message_record(row) for row in reversed(rows)]

    def get_conversation_memory(self, auth: AuthContext, conversation_id: str) -> ConversationMemoryRecord | None:
        with self._session_factory() as session:
            conversation = _authorized_conversation(session, auth, conversation_id)
            if conversation is None:
                return None
            memory = session.scalar(
                select(ConversationMemory).where(
                    ConversationMemory.conversation_id == conversation.id,
                    ConversationMemory.owner_user_id == auth.user_id,
                    ConversationMemory.workspace_id == auth.workspace_id,
                )
            )
            return _conversation_memory_record(memory) if memory is not None else None

    def upsert_conversation_memory(
        self,
        auth: AuthContext,
        conversation_id: str,
        *,
        summary: str,
        covered_message_id: str | None,
        estimated_tokens: int,
    ) -> ConversationMemoryRecord | None:
        now = utc_now()
        with self._session_factory() as session:
            conversation = _authorized_conversation(session, auth, conversation_id)
            if conversation is None:
                return None
            memory = session.scalar(
                select(ConversationMemory).where(
                    ConversationMemory.conversation_id == conversation.id,
                    ConversationMemory.owner_user_id == auth.user_id,
                    ConversationMemory.workspace_id == auth.workspace_id,
                )
            )
            if memory is None:
                memory = ConversationMemory(
                    id=opaque_id("mem"),
                    conversation_id=conversation.id,
                    owner_user_id=auth.user_id,
                    workspace_id=auth.workspace_id,
                    summary=summary,
                    covered_message_id=covered_message_id,
                    summary_version=1,
                    estimated_tokens=max(0, estimated_tokens),
                    created_at=now,
                    updated_at=now,
                )
                session.add(memory)
            else:
                memory.summary = summary
                memory.covered_message_id = covered_message_id
                memory.summary_version += 1
                memory.estimated_tokens = max(0, estimated_tokens)
                memory.updated_at = now
            session.commit()
            return _conversation_memory_record(memory)

    def get_conversation_state_snapshot(
        self,
        auth: AuthContext,
        conversation_id: str,
        *,
        event_limit: int = 30,
        message_limit: int = 100,
    ) -> ConversationStateSnapshotRecord | None:
        with self._session_factory() as session:
            conversation = _authorized_conversation(session, auth, conversation_id)
            if conversation is None:
                return None
            bounded_message_limit = bounded_state_message_limit(message_limit)
            message_filters = (
                ConversationMessage.conversation_id == conversation.id,
                ConversationMessage.owner_user_id == auth.user_id,
                ConversationMessage.workspace_id == auth.workspace_id,
            )
            message_count = session.scalar(
                select(func.count()).select_from(ConversationMessage).where(*message_filters)
            ) or 0
            if bounded_message_limit > 0:
                latest_messages = session.scalars(
                    select(ConversationMessage)
                    .where(*message_filters)
                    .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
                    .limit(bounded_message_limit)
                ).all()
                messages = list(reversed(latest_messages))
            else:
                messages = []
            latest_run = session.scalars(
                select(AssistantRun)
                .where(
                    AssistantRun.conversation_id == conversation.id,
                    AssistantRun.owner_user_id == auth.user_id,
                    AssistantRun.workspace_id == auth.workspace_id,
                )
                .order_by(AssistantRun.updated_at.desc(), AssistantRun.created_at.desc(), AssistantRun.id.desc())
                .limit(1)
            ).first()
            artifacts: list[ArtifactRecord] = []
            conversation_artifact_rows = session.scalars(
                select(Artifact)
                .where(
                    Artifact.conversation_id == conversation.id,
                    Artifact.owner_user_id == auth.user_id,
                    Artifact.workspace_id == auth.workspace_id,
                    Artifact.kind.notin_(tuple(INTERNAL_ARTIFACT_KINDS)),
                )
                .order_by(Artifact.created_at.desc(), Artifact.id.desc())
                .limit(CONVERSATION_ARTIFACT_STATE_LIMIT + 1)
            ).all()
            conversation_artifacts_page = [_artifact_record(row) for row in conversation_artifact_rows]
            conversation_artifacts = conversation_artifacts_page[:CONVERSATION_ARTIFACT_STATE_LIMIT]
            conversation_artifacts_next_cursor = (
                encode_artifact_page_cursor(conversation_artifacts[-1])
                if len(conversation_artifacts_page) > CONVERSATION_ARTIFACT_STATE_LIMIT and conversation_artifacts
                else None
            )
            events: list[RunEventRecord] = []
            conversation_events: list[RunEventRecord] = []
            latest_strategy_spec = None
            if event_limit > 0:
                conversation_event_rows = session.scalars(
                    select(RunEvent)
                    .where(
                        RunEvent.conversation_id == conversation.id,
                        RunEvent.owner_user_id == auth.user_id,
                        RunEvent.workspace_id == auth.workspace_id,
                    )
                    .order_by(RunEvent.created_at.desc(), RunEvent.sequence.desc(), RunEvent.id.desc())
                    .limit(max(event_limit * 4, event_limit))
                ).all()
                conversation_events = [
                    _run_event_record(row) for row in reversed(conversation_event_rows)
                ]
            if latest_run is not None:
                latest_strategy_spec = session.scalars(
                    select(StrategySpec)
                    .where(
                        StrategySpec.run_id == latest_run.id,
                        StrategySpec.owner_user_id == auth.user_id,
                        StrategySpec.workspace_id == auth.workspace_id,
                    )
                    .order_by(StrategySpec.created_at.desc(), StrategySpec.id.desc())
                    .limit(1)
                ).first()
                artifact_rows = session.scalars(
                    select(Artifact)
                    .where(
                        Artifact.run_id == latest_run.id,
                        Artifact.owner_user_id == auth.user_id,
                        Artifact.workspace_id == auth.workspace_id,
                    )
                    .order_by(Artifact.created_at.asc(), Artifact.storage_key.asc(), Artifact.id.asc())
                ).all()
                artifacts = [_artifact_record(row) for row in artifact_rows]
                if event_limit > 0:
                    event_rows = session.scalars(
                        select(RunEvent)
                        .where(
                            RunEvent.run_id == latest_run.id,
                            RunEvent.owner_user_id == auth.user_id,
                            RunEvent.workspace_id == auth.workspace_id,
                        )
                        .order_by(RunEvent.sequence.desc())
                        .limit(event_limit)
                    ).all()
                    events = [_run_event_record(row, latest_run) for row in reversed(event_rows)]
            return ConversationStateSnapshotRecord(
                conversation=_conversation_record(conversation),
                messages=[_message_record(row) for row in messages],
                message_count=int(message_count),
                messages_truncated=int(message_count) > len(messages),
                message_limit=bounded_message_limit,
                latest_run=_run_record(latest_run) if latest_run is not None else None,
                latest_run_artifacts=artifacts,
                conversation_artifacts=conversation_artifacts,
                conversation_artifacts_next_cursor=conversation_artifacts_next_cursor,
                latest_run_events=events,
                conversation_run_events=conversation_events,
                latest_strategy_spec=(
                    _strategy_spec_record(latest_strategy_spec)
                    if latest_strategy_spec is not None
                    else None
                ),
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
        now = utc_now()
        with self._session_factory() as session:
            conversation = _authorized_conversation(session, auth, conversation_id)
            if conversation is None:
                return None
            run = AssistantRun(
                id=opaque_id("run"),
                conversation_id=conversation.id,
                retry_of_run_id=retry_of_run_id,
                owner_user_id=auth.user_id,
                workspace_id=auth.workspace_id,
                status=status,
                mode=mode,
                started_at=now if status == "running" else None,
                completed_at=now if status in TERMINAL_RUN_STATUSES else None,
                created_at=now,
                updated_at=now,
                request_id=request_id or opaque_id("req"),
                trace_id=trace_id or opaque_id("trace"),
            )
            session.add(run)
            session.commit()
            return _run_record(run)

    def get_run(self, auth: AuthContext, run_id: str) -> AssistantRunRecord | None:
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            return _run_record(run)

    def create_run_job(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        job_type: str,
        payload_json: dict,
        max_attempts: int = BACKTEST_JOB_MAX_ATTEMPTS,
    ) -> RunJobRecord | None:
        now = utc_now()
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            job = RunJob(
                id=opaque_id("job"),
                run_id=run.id,
                owner_user_id=run.owner_user_id,
                workspace_id=run.workspace_id,
                job_type=job_type,
                status="queued",
                payload_json=payload_json,
                attempts=0,
                max_attempts=max_attempts,
                created_at=now,
                updated_at=now,
            )
            session.add(job)
            session.commit()
            return _run_job_record(job)

    def claim_run_job(self, *, job_type: str, worker_id: str, lease_seconds: int = 300) -> RunJobRecord | None:
        now = utc_now()
        with self._session_factory() as session:
            candidate_query = (
                select(RunJob)
                .where(
                    RunJob.job_type == job_type,
                    RunJob.status.in_(("queued", "running")),
                    RunJob.attempts < RunJob.max_attempts,
                    or_(RunJob.status == "queued", RunJob.leased_until < now),
                )
                .order_by(RunJob.created_at.asc(), RunJob.id.asc())
            )
            active_counts = {
                workspace_id: int(count)
                for workspace_id, count in session.execute(
                    select(RunJob.workspace_id, func.count())
                    .where(
                        RunJob.job_type == job_type,
                        RunJob.status == "running",
                        RunJob.leased_until >= now,
                    )
                    .group_by(RunJob.workspace_id)
                ).all()
            }
            job = None
            offset = 0
            batch_size = 100
            while job is None:
                candidates = session.scalars(candidate_query.limit(batch_size).offset(offset)).all()
                if not candidates:
                    return None
                for candidate in candidates:
                    active_limit = _job_workspace_active_limit(candidate.payload_json)
                    if active_counts.get(candidate.workspace_id, 0) >= active_limit:
                        continue
                    job = session.scalars(
                        select(RunJob)
                        .where(
                            RunJob.id == candidate.id,
                            RunJob.job_type == job_type,
                            RunJob.status.in_(("queued", "running")),
                            RunJob.attempts < RunJob.max_attempts,
                            or_(RunJob.status == "queued", RunJob.leased_until < now),
                        )
                        .with_for_update(skip_locked=True)
                    ).first()
                    if job is not None:
                        break
                offset += batch_size
            if job is None:
                return None
            job.status = "running"
            job.attempts += 1
            job.lease_owner = worker_id
            job.leased_until = now + timedelta(seconds=lease_seconds)
            job.updated_at = now
            session.commit()
            return _run_job_record(job)

    def complete_run_job(
        self,
        job_id: str,
        *,
        status: str,
        result_json: dict | None = None,
        error_code: str | None = None,
    ) -> RunJobRecord | None:
        now = utc_now()
        with self._session_factory() as session:
            job = session.get(RunJob, job_id)
            if job is None:
                return None
            job.status = status
            job.result_json = result_json
            job.error_code = error_code
            job.lease_owner = None
            job.leased_until = None
            job.updated_at = now
            session.commit()
            return _run_job_record(job)

    def cancel_run_jobs(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        statuses: tuple[str, ...] = ("queued", "running"),
        result_json: dict | None = None,
        error_code: str | None = None,
    ) -> int:
        now = utc_now()
        with self._session_factory() as session:
            jobs = session.scalars(
                select(RunJob).where(
                    RunJob.run_id == run_id,
                    RunJob.owner_user_id == auth.user_id,
                    RunJob.workspace_id == auth.workspace_id,
                    RunJob.status.in_(statuses),
                )
            ).all()
            for job in jobs:
                job.status = "cancelled"
                job.result_json = result_json
                job.error_code = error_code
                job.lease_owner = None
                job.leased_until = None
                job.updated_at = now
            session.commit()
            return len(jobs)

    def get_run_job(self, job_id: str) -> RunJobRecord | None:
        with self._session_factory() as session:
            job = session.get(RunJob, job_id)
            if job is None:
                return None
            return _run_job_record(job)

    def run_queue_stats(self, *, job_type: str | None = None) -> RunQueueStatsRecord:
        now = utc_now()
        with self._session_factory() as session:
            filters = []
            if job_type is not None:
                filters.append(RunJob.job_type == job_type)
            queued = session.scalar(select(func.count()).select_from(RunJob).where(RunJob.status == "queued", *filters)) or 0
            running = session.scalar(select(func.count()).select_from(RunJob).where(RunJob.status == "running", *filters)) or 0
            active_running = (
                session.scalar(
                    select(func.count())
                    .select_from(RunJob)
                    .where(RunJob.status == "running", RunJob.leased_until >= now, *filters)
                )
                or 0
            )
            failed = session.scalar(select(func.count()).select_from(RunJob).where(RunJob.status == "failed", *filters)) or 0
            oldest = session.scalar(select(func.min(RunJob.created_at)).where(RunJob.status == "queued", *filters))
            oldest_running = session.scalar(
                select(func.min(RunJob.updated_at)).where(RunJob.status == "running", RunJob.leased_until >= now, *filters)
            )
        return RunQueueStatsRecord(
            queued=int(queued),
            running=int(running),
            oldest_queued_seconds=int((now - _utc_aware(oldest)).total_seconds()) if oldest is not None else None,
            oldest_running_seconds=int((now - _utc_aware(oldest_running)).total_seconds()) if oldest_running is not None else None,
            failed=int(failed),
            active_running=int(active_running),
            stale_running=int(running) - int(active_running),
        )

    def list_runs(self, auth: AuthContext, conversation_id: str) -> list[AssistantRunRecord] | None:
        with self._session_factory() as session:
            conversation = _authorized_conversation(session, auth, conversation_id)
            if conversation is None:
                return None
            rows = session.scalars(
                select(AssistantRun)
                .where(
                    AssistantRun.conversation_id == conversation.id,
                    AssistantRun.owner_user_id == auth.user_id,
                    AssistantRun.workspace_id == auth.workspace_id,
                )
                .order_by(AssistantRun.updated_at.desc(), AssistantRun.created_at.desc(), AssistantRun.id.desc())
            ).all()
            return [_run_record(row) for row in rows]

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
        now = utc_now()
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            last_sequence = session.scalar(select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run.id)) or 0
            created = [
                RunEvent(
                    id=opaque_id("evt"),
                    run_id=run.id,
                    conversation_id=run.conversation_id,
                    owner_user_id=run.owner_user_id,
                    workspace_id=run.workspace_id,
                    sequence=last_sequence + index,
                    type=event_type,
                    payload_json=payload,
                    created_at=now,
                )
                for index, (event_type, payload) in enumerate(events, start=1)
            ]
            session.add_all(created)
            session.commit()
            return [_run_event_record(event, run) for event in created]

    def list_run_events(self, auth: AuthContext, run_id: str) -> list[RunEventRecord] | None:
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            rows = session.scalars(
                select(RunEvent)
                .where(
                    RunEvent.run_id == run.id,
                    RunEvent.owner_user_id == auth.user_id,
                    RunEvent.workspace_id == auth.workspace_id,
                )
                .order_by(RunEvent.sequence.asc())
            ).all()
            return [_run_event_record(row, run) for row in rows]

    def list_run_events_after(
        self,
        auth: AuthContext,
        run_id: str,
        last_event_id: str | None = None,
    ) -> list[RunEventRecord] | None:
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            min_sequence = _last_event_sequence(session, run, last_event_id)
            if last_event_id and min_sequence is None:
                return self.list_run_events(auth, run_id)
            conditions = [
                RunEvent.run_id == run.id,
                RunEvent.owner_user_id == auth.user_id,
                RunEvent.workspace_id == auth.workspace_id,
            ]
            if min_sequence is not None:
                conditions.append(RunEvent.sequence > min_sequence)
            rows = session.scalars(
                select(RunEvent)
                .where(*conditions)
                .order_by(RunEvent.sequence.asc())
            ).all()
            return [_run_event_record(row, run) for row in rows]

    def list_workflow_task_continuation_events(
        self,
        auth: AuthContext,
        task_id: str,
    ) -> list[RunEventRecord] | None:
        with self._session_factory() as session:
            task = session.scalar(
                select(WorkflowTask).where(
                    WorkflowTask.id == task_id,
                    WorkflowTask.owner_user_id == auth.user_id,
                    WorkflowTask.workspace_id == auth.workspace_id,
                )
            )
            if task is None:
                return None
            rows = session.scalars(
                select(RunEvent)
                .where(
                    RunEvent.conversation_id == task.conversation_id,
                    RunEvent.owner_user_id == auth.user_id,
                    RunEvent.workspace_id == auth.workspace_id,
                    RunEvent.type.in_(tuple(WORKFLOW_CONTINUATION_EVENT_TYPES)),
                )
                .order_by(
                    RunEvent.created_at.asc(),
                    RunEvent.run_id.asc(),
                    RunEvent.sequence.asc(),
                    RunEvent.id.asc(),
                )
            ).all()
            return [
                _run_event_record(row)
                for row in rows
                if isinstance(row.payload_json, dict) and row.payload_json.get("task_id") == task.id
            ]

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
        now = utc_now()
        with self._session_factory() as session:
            conversation = _authorized_conversation(session, auth, conversation_id)
            if conversation is None:
                return None
            if run_id is not None:
                run = _authorized_run(session, auth, run_id)
                if run is None or run.conversation_id != conversation.id:
                    return None
            task = session.scalars(
                select(WorkflowTask).where(
                    WorkflowTask.conversation_id == conversation.id,
                    WorkflowTask.workflow_id == workflow_id,
                    WorkflowTask.task_template_id == task_template_id,
                    WorkflowTask.owner_user_id == auth.user_id,
                    WorkflowTask.workspace_id == auth.workspace_id,
                )
            ).first()
            if task is None:
                task = WorkflowTask(
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
                    payload_json=payload_json,
                    created_at=now,
                    updated_at=now,
                )
                session.add(task)
            else:
                task.run_id = run_id
                task.step_id = step_id
                task.kind = kind
                task.status = status
                task.payload_json = payload_json
                task.updated_at = now
                if status not in WORKFLOW_TASK_RESOLVED_STATUSES:
                    task.resolved_at = None
            session.commit()
            return _workflow_task_record(task)

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
        now = utc_now()
        with self._session_factory() as session:
            conversation = _authorized_conversation(session, auth, conversation_id)
            if conversation is None:
                return None
            if run_id is not None:
                run = _authorized_run(session, auth, run_id)
                if run is None or run.conversation_id != conversation.id:
                    return None
            rows = session.scalars(
                select(WorkflowTask)
                .where(
                    WorkflowTask.conversation_id == conversation.id,
                    WorkflowTask.workflow_id == workflow_id,
                    WorkflowTask.owner_user_id == auth.user_id,
                    WorkflowTask.workspace_id == auth.workspace_id,
                )
                .order_by(WorkflowTask.created_at.asc(), WorkflowTask.id.asc())
            ).all()
            by_template = {row.task_template_id: row for row in rows}
            created: list[WorkflowTask] = []
            updated: list[WorkflowTask] = []
            unchanged: list[WorkflowTask] = []
            requested_templates: set[str] = set()

            for payload_json in task_payloads:
                task_template_id = payload_json.get("task_template_id")
                status = payload_json.get("status")
                step_id = payload_json.get("step_id")
                kind = payload_json.get("kind")
                if (
                    not isinstance(task_template_id, str)
                    or not isinstance(status, str)
                    or not isinstance(step_id, str)
                    or not isinstance(kind, str)
                ):
                    continue
                requested_templates.add(task_template_id)
                task_payload = dict(payload_json)
                task_payload["status"] = status
                task = by_template.get(task_template_id)
                if task is not None and task.status in WORKFLOW_TASK_RESOLVED_STATUSES:
                    unchanged.append(task)
                    continue
                if task is None:
                    task = WorkflowTask(
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
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(task)
                    rows.append(task)
                    by_template[task_template_id] = task
                    created.append(task)
                    continue
                task_record = _workflow_task_record(task)
                if _workflow_task_payload_matches(task_record, run_id=run_id, payload_json=task_payload):
                    unchanged.append(task)
                    continue
                task.run_id = run_id
                task.step_id = step_id
                task.kind = kind
                task.status = status
                task.payload_json = task_payload
                task.updated_at = now
                if status not in WORKFLOW_TASK_RESOLVED_STATUSES:
                    task.resolved_at = None
                updated.append(task)

            resolved: list[WorkflowTask] = []
            for task in list(by_template.values()):
                if (
                    task.task_template_id not in requested_templates
                    and task.step_id in completed_steps
                    and task.status not in WORKFLOW_TASK_RESOLVED_STATUSES
                ):
                    task.status = "completed"
                    task.updated_at = now
                    task.resolved_at = now
                    resolved.append(task)

            if created or updated or resolved:
                session.commit()
            resolved_ids = {task.id for task in resolved}
            unchanged_records = [_workflow_task_record(task) for task in unchanged if task.id not in resolved_ids]
            records = sorted(rows, key=lambda task: (task.created_at, task.id))
            return WorkflowTaskSyncResult(
                records=[_workflow_task_record(task) for task in records],
                created=[_workflow_task_record(task) for task in created],
                updated=[_workflow_task_record(task) for task in updated],
                resolved=[_workflow_task_record(task) for task in resolved],
                unchanged=unchanged_records,
            )

    def list_workflow_tasks(self, auth: AuthContext, conversation_id: str) -> list[WorkflowTaskRecord] | None:
        with self._session_factory() as session:
            conversation = _authorized_conversation(session, auth, conversation_id)
            if conversation is None:
                return None
            rows = session.scalars(
                select(WorkflowTask)
                .where(
                    WorkflowTask.conversation_id == conversation.id,
                    WorkflowTask.owner_user_id == auth.user_id,
                    WorkflowTask.workspace_id == auth.workspace_id,
                )
                .order_by(WorkflowTask.created_at.asc(), WorkflowTask.id.asc())
            ).all()
            return [_workflow_task_record(row) for row in rows]

    def get_workflow_task(self, auth: AuthContext, task_id: str) -> WorkflowTaskRecord | None:
        with self._session_factory() as session:
            task = session.scalar(
                select(WorkflowTask).where(
                    WorkflowTask.id == task_id,
                    WorkflowTask.owner_user_id == auth.user_id,
                    WorkflowTask.workspace_id == auth.workspace_id,
                )
            )
            return _workflow_task_record(task) if task is not None else None

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
        now = utc_now()
        with self._session_factory() as session:
            task = session.scalar(
                select(WorkflowTask).where(
                    WorkflowTask.id == task_id,
                    WorkflowTask.owner_user_id == auth.user_id,
                    WorkflowTask.workspace_id == auth.workspace_id,
                )
            )
            if task is None:
                return None
            task.status = status
            if response_json is not None:
                task.response_json = response_json
            task.updated_at = now
            task.resolved_at = now if status in WORKFLOW_TASK_RESOLVED_STATUSES else None
            session.commit()
            return _workflow_task_record(task)

    def summarize_run_events(self, auth: AuthContext, run_id: str) -> RunEventSummaryRecord | None:
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            event_count = session.scalar(
                select(func.count())
                .select_from(RunEvent)
                .where(
                    RunEvent.run_id == run.id,
                    RunEvent.owner_user_id == auth.user_id,
                    RunEvent.workspace_id == auth.workspace_id,
                )
            )
            latest = session.scalars(
                select(RunEvent)
                .where(
                    RunEvent.run_id == run.id,
                    RunEvent.owner_user_id == auth.user_id,
                    RunEvent.workspace_id == auth.workspace_id,
                )
                .order_by(RunEvent.sequence.desc())
                .limit(1)
            ).first()
            return RunEventSummaryRecord(
                event_count=int(event_count or 0),
                latest_event=_run_event_record(latest, run) if latest is not None else None,
            )

    def get_run_progress_snapshot(self, auth: AuthContext, run_id: str) -> RunProgressSnapshotRecord | None:
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            event_count = session.scalar(
                select(func.count())
                .select_from(RunEvent)
                .where(
                    RunEvent.run_id == run.id,
                    RunEvent.owner_user_id == auth.user_id,
                    RunEvent.workspace_id == auth.workspace_id,
                )
            )
            latest = session.scalars(
                select(RunEvent)
                .where(
                    RunEvent.run_id == run.id,
                    RunEvent.owner_user_id == auth.user_id,
                    RunEvent.workspace_id == auth.workspace_id,
                )
                .order_by(RunEvent.sequence.desc())
                .limit(1)
            ).first()
            artifacts = session.scalars(
                select(Artifact)
                .where(
                    Artifact.run_id == run.id,
                    Artifact.owner_user_id == auth.user_id,
                    Artifact.workspace_id == auth.workspace_id,
                )
                .order_by(Artifact.created_at.asc(), Artifact.storage_key.asc(), Artifact.id.asc())
            ).all()
            return RunProgressSnapshotRecord(
                run=_run_record(run),
                event_summary=RunEventSummaryRecord(
                    event_count=int(event_count or 0),
                    latest_event=_run_event_record(latest, run) if latest is not None else None,
                ),
                artifacts=[_artifact_record(artifact) for artifact in artifacts],
            )

    def set_run_status(self, auth: AuthContext, run_id: str, status: str) -> AssistantRunRecord | None:
        now = utc_now()
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            run.status = status
            run.updated_at = now
            if status == "running" and run.started_at is None:
                run.started_at = now
            if status in TERMINAL_RUN_STATUSES:
                run.completed_at = now
            session.commit()
            return _run_record(run)

    def create_strategy_spec(
        self,
        auth: AuthContext,
        run_id: str,
        payload: dict,
        schema_version: str,
    ) -> StrategySpecRecord | None:
        now = utc_now()
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            spec = StrategySpec(
                id=opaque_id("spec"),
                run_id=run.id,
                owner_user_id=run.owner_user_id,
                workspace_id=run.workspace_id,
                payload_json=payload,
                schema_version=schema_version,
                created_at=now,
            )
            session.add(spec)
            session.commit()
            return _strategy_spec_record(spec)

    def get_strategy_spec_for_run(self, auth: AuthContext, run_id: str) -> StrategySpecRecord | None:
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            spec = session.scalars(
                select(StrategySpec)
                .where(
                    StrategySpec.run_id == run.id,
                    StrategySpec.owner_user_id == auth.user_id,
                    StrategySpec.workspace_id == auth.workspace_id,
                )
                .order_by(StrategySpec.created_at.desc(), StrategySpec.id.desc())
            ).first()
            return _strategy_spec_record(spec) if spec is not None else None

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
        now = utc_now()
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            artifact = Artifact(
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
            session.add(artifact)
            session.commit()
            return _artifact_record(artifact)

    def create_artifacts(
        self,
        auth: AuthContext,
        run_id: str,
        artifacts: list[ArtifactInput],
    ) -> list[ArtifactRecord] | None:
        now = utc_now()
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            created = [
                Artifact(
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
            session.add_all(created)
            session.commit()
            return [_artifact_record(artifact) for artifact in created]

    def list_artifacts(self, auth: AuthContext, run_id: str) -> list[ArtifactRecord] | None:
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            rows = session.scalars(
                select(Artifact)
                .where(
                    Artifact.run_id == run.id,
                    Artifact.owner_user_id == auth.user_id,
                    Artifact.workspace_id == auth.workspace_id,
                )
                .order_by(Artifact.created_at.asc(), Artifact.storage_key.asc(), Artifact.id.asc())
            ).all()
            return [_artifact_record(row) for row in rows]

    def get_artifact(self, auth: AuthContext, artifact_id: str) -> ArtifactRecord | None:
        with self._session_factory() as session:
            artifact = session.scalar(
                select(Artifact).where(
                    Artifact.id == artifact_id,
                    Artifact.owner_user_id == auth.user_id,
                    Artifact.workspace_id == auth.workspace_id,
                )
            )
            if artifact is None:
                return None
            return _artifact_record(artifact)

    def list_workspace_artifacts_page(
        self,
        auth: AuthContext,
        *,
        limit: int = CONVERSATION_ARTIFACT_STATE_LIMIT,
        cursor: str | None = None,
        visibility: ArtifactVisibilityFilter = "user",
    ) -> ArtifactPageRecord:
        with self._session_factory() as session:
            return self._list_artifact_page(
                session,
                auth,
                limit=limit,
                cursor=cursor,
                visibility=visibility,
            )

    def list_conversation_artifacts_page(
        self,
        auth: AuthContext,
        conversation_id: str,
        *,
        limit: int = CONVERSATION_ARTIFACT_STATE_LIMIT,
        cursor: str | None = None,
        visibility: ArtifactVisibilityFilter = "user",
    ) -> ArtifactPageRecord | None:
        with self._session_factory() as session:
            conversation = _authorized_conversation(session, auth, conversation_id)
            if conversation is None:
                return None
            return self._list_artifact_page(
                session,
                auth,
                conversation_id=conversation.id,
                limit=limit,
                cursor=cursor,
                visibility=visibility,
            )

    def _list_artifact_page(
        self,
        session: Session,
        auth: AuthContext,
        *,
        conversation_id: str | None = None,
        limit: int = CONVERSATION_ARTIFACT_STATE_LIMIT,
        cursor: str | None = None,
        visibility: ArtifactVisibilityFilter = "user",
    ) -> ArtifactPageRecord:
        bounded_limit = bounded_artifact_page_limit(limit)
        cursor_value = decode_artifact_page_cursor(cursor)
        filters = [
            Artifact.owner_user_id == auth.user_id,
            Artifact.workspace_id == auth.workspace_id,
        ]
        if conversation_id is not None:
            filters.append(Artifact.conversation_id == conversation_id)
        if visibility != "all":
            filters.append(Artifact.kind.notin_(tuple(INTERNAL_ARTIFACT_KINDS)))
        if cursor_value is not None:
            cursor_created_at, cursor_id = cursor_value
            filters.append(
                or_(
                    Artifact.created_at < cursor_created_at,
                    (Artifact.created_at == cursor_created_at) & (Artifact.id < cursor_id),
                )
            )
        rows = session.scalars(
            select(Artifact)
            .where(*filters)
            .order_by(Artifact.created_at.desc(), Artifact.id.desc())
            .limit(bounded_limit + 1)
        ).all()
        page = [_artifact_record(row) for row in rows]
        items = page[:bounded_limit]
        next_cursor = encode_artifact_page_cursor(items[-1]) if len(page) > bounded_limit and items else None
        return ArtifactPageRecord(items=items, next_cursor=next_cursor)

    def get_backtest_summary(self, auth: AuthContext, run_id: str) -> dict | None:
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            report = session.scalar(
                select(BacktestReport).where(
                    BacktestReport.run_id == run.id,
                    BacktestReport.owner_user_id == auth.user_id,
                    BacktestReport.workspace_id == auth.workspace_id,
                )
            )
            if report is None:
                return None
            return _backtest_report_summary(report)

    def get_backtest_summaries(self, auth: AuthContext, run_ids: list[str]) -> dict[str, dict]:
        unique_run_ids = list(dict.fromkeys(run_ids))
        if not unique_run_ids:
            return {}
        with self._session_factory() as session:
            rows = session.scalars(
                select(BacktestReport)
                .join(AssistantRun, AssistantRun.id == BacktestReport.run_id)
                .where(
                    BacktestReport.run_id.in_(unique_run_ids),
                    BacktestReport.owner_user_id == auth.user_id,
                    BacktestReport.workspace_id == auth.workspace_id,
                    AssistantRun.owner_user_id == auth.user_id,
                    AssistantRun.workspace_id == auth.workspace_id,
                )
            ).all()
            return {row.run_id: _backtest_report_summary(row) for row in rows}

    def resolve_backtest_report_run_id(self, auth: AuthContext, conversation_id: str, requested_run_id: str) -> str | None:
        with self._session_factory() as session:
            conversation = _authorized_conversation(session, auth, conversation_id)
            if conversation is None:
                return None
            requested = session.scalar(
                select(BacktestReport.run_id)
                .join(AssistantRun, AssistantRun.id == BacktestReport.run_id)
                .where(
                    BacktestReport.run_id == requested_run_id,
                    BacktestReport.owner_user_id == auth.user_id,
                    BacktestReport.workspace_id == auth.workspace_id,
                    AssistantRun.conversation_id == conversation.id,
                    AssistantRun.owner_user_id == auth.user_id,
                    AssistantRun.workspace_id == auth.workspace_id,
                )
            )
            if requested is not None:
                return requested
            return session.scalar(
                select(BacktestReport.run_id)
                .join(AssistantRun, AssistantRun.id == BacktestReport.run_id)
                .where(
                    BacktestReport.owner_user_id == auth.user_id,
                    BacktestReport.workspace_id == auth.workspace_id,
                    AssistantRun.conversation_id == conversation.id,
                    AssistantRun.owner_user_id == auth.user_id,
                    AssistantRun.workspace_id == auth.workspace_id,
                    AssistantRun.status == "completed",
                )
                .order_by(BacktestReport.created_at.desc(), AssistantRun.updated_at.desc(), AssistantRun.id.desc())
                .limit(1)
            )

    def query_backtest_trades(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        bucket: str | None = None,
        limit: int = 20,
    ) -> list[dict] | None:
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            query = select(BacktestTradeIndex).where(
                BacktestTradeIndex.run_id == run.id,
                BacktestTradeIndex.owner_user_id == auth.user_id,
                BacktestTradeIndex.workspace_id == auth.workspace_id,
            )
            if bucket:
                query = query.where(BacktestTradeIndex.bucket == bucket)
            rows = session.scalars(query.order_by(BacktestTradeIndex.trade_rank.asc()).limit(max(1, min(limit, 200)))).all()
            return [
                {
                    "bucket": row.bucket,
                    "trade_rank": row.trade_rank,
                    "opened_at": row.opened_at.isoformat() if row.opened_at else None,
                    "closed_at": row.closed_at.isoformat() if row.closed_at else None,
                    "pnl_cost": float(row.pnl_cost) if row.pnl_cost is not None else None,
                    "pnl_percentage": float(row.pnl_percentage) if row.pnl_percentage is not None else None,
                    "trade": row.payload_json,
                }
                for row in rows
            ]

    def get_backtest_equity_summary(self, auth: AuthContext, run_id: str) -> dict | None:
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            summary = session.scalar(
                select(BacktestEquitySummary).where(
                    BacktestEquitySummary.run_id == run.id,
                    BacktestEquitySummary.owner_user_id == auth.user_id,
                    BacktestEquitySummary.workspace_id == auth.workspace_id,
                )
            )
            if summary is None:
                return None
            return _backtest_equity_summary(summary)

    def get_backtest_equity_summaries(self, auth: AuthContext, run_ids: list[str]) -> dict[str, dict]:
        unique_run_ids = list(dict.fromkeys(run_ids))
        if not unique_run_ids:
            return {}
        with self._session_factory() as session:
            rows = session.scalars(
                select(BacktestEquitySummary)
                .join(AssistantRun, AssistantRun.id == BacktestEquitySummary.run_id)
                .where(
                    BacktestEquitySummary.run_id.in_(unique_run_ids),
                    BacktestEquitySummary.owner_user_id == auth.user_id,
                    BacktestEquitySummary.workspace_id == auth.workspace_id,
                    AssistantRun.owner_user_id == auth.user_id,
                    AssistantRun.workspace_id == auth.workspace_id,
                )
            ).all()
            return {row.run_id: _backtest_equity_summary(row) for row in rows}

    def create_validation_report(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        status: str,
        payload: dict,
    ) -> ValidationReportRecord | None:
        now = utc_now()
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            report = ValidationReport(
                id=opaque_id("val"),
                run_id=run.id,
                owner_user_id=run.owner_user_id,
                workspace_id=run.workspace_id,
                status=status,
                payload_json=payload,
                created_at=now,
            )
            session.add(report)
            session.commit()
            return _validation_report_record(report)

    def create_review_report(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        decision: str,
        payload: dict,
    ) -> ReviewReportRecord | None:
        now = utc_now()
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            report = ReviewReport(
                id=opaque_id("rev"),
                run_id=run.id,
                owner_user_id=run.owner_user_id,
                workspace_id=run.workspace_id,
                decision=decision,
                payload_json=payload,
                created_at=now,
            )
            session.add(report)
            session.commit()
            return _review_report_record(report)

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
        now = utc_now()
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            tool_call = ToolCall(
                id=opaque_id("toolcall"),
                run_id=run.id,
                tool_id=tool_id,
                status=status,
                input_json=input_json,
                output_json=None,
                policy_findings_json=policy_findings_json,
                started_at=now if status == "running" else None,
                created_at=now,
            )
            session.add(tool_call)
            session.commit()
            return _tool_call_record(tool_call)

    def complete_tool_call(
        self,
        auth: AuthContext,
        tool_call_id: str,
        *,
        status: str,
        output_json: dict | None = None,
        policy_findings_json: list | None = None,
    ) -> ToolCallRecord | None:
        now = utc_now()
        with self._session_factory() as session:
            tool_call = session.get(ToolCall, tool_call_id)
            if tool_call is None:
                return None
            run = _authorized_run(session, auth, tool_call.run_id)
            if run is None:
                return None
            tool_call.status = status
            tool_call.output_json = output_json
            if policy_findings_json is not None:
                tool_call.policy_findings_json = policy_findings_json
            tool_call.completed_at = now
            session.commit()
            return _tool_call_record(tool_call)

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
        now = utc_now()
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            finding = PolicyFinding(
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
            session.add(finding)
            session.commit()
            return _policy_finding_record(finding)

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
        now = utc_now()
        with self._session_factory() as session:
            if run_id is not None and _authorized_run(session, auth, run_id) is None:
                return None
            record = UsageLedger(
                id=opaque_id("usage"),
                owner_user_id=auth.user_id,
                workspace_id=auth.workspace_id,
                run_id=run_id,
                model=model,
                tool_id=tool_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_estimate_usd=Decimal(str(cost_estimate_usd)) if cost_estimate_usd is not None else None,
                created_at=now,
            )
            session.add(record)
            session.commit()
            return _usage_ledger_record(record)

    def list_tool_calls(self, auth: AuthContext, run_id: str) -> list[ToolCallRecord] | None:
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            rows = session.scalars(
                select(ToolCall).where(ToolCall.run_id == run.id).order_by(ToolCall.created_at.asc(), ToolCall.id.asc())
            ).all()
            return [_tool_call_record(row) for row in rows]

    def list_policy_findings(self, auth: AuthContext, run_id: str) -> list[PolicyFindingRecord] | None:
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            rows = session.scalars(
                select(PolicyFinding)
                .where(
                    PolicyFinding.run_id == run.id,
                    PolicyFinding.owner_user_id == auth.user_id,
                    PolicyFinding.workspace_id == auth.workspace_id,
                )
                .order_by(PolicyFinding.created_at.asc(), PolicyFinding.id.asc())
            ).all()
            return [_policy_finding_record(row) for row in rows]

    def list_usage_ledger(self, auth: AuthContext, run_id: str) -> list[UsageLedgerRecord] | None:
        with self._session_factory() as session:
            run = _authorized_run(session, auth, run_id)
            if run is None:
                return None
            rows = session.scalars(
                select(UsageLedger)
                .where(
                    UsageLedger.run_id == run.id,
                    UsageLedger.owner_user_id == auth.user_id,
                    UsageLedger.workspace_id == auth.workspace_id,
                )
                .order_by(UsageLedger.created_at.asc(), UsageLedger.id.asc())
            ).all()
            return [_usage_ledger_record(row) for row in rows]

    def summarize_account_usage(self, auth: AuthContext) -> AccountUsageSummaryRecord:
        with self._session_factory() as session:
            messages = session.scalar(
                select(func.count(ConversationMessage.id)).where(
                    ConversationMessage.owner_user_id == auth.user_id,
                    ConversationMessage.workspace_id == auth.workspace_id,
                )
            ) or 0
            runs = session.scalar(
                select(func.count(AssistantRun.id)).where(
                    AssistantRun.owner_user_id == auth.user_id,
                    AssistantRun.workspace_id == auth.workspace_id,
                )
            ) or 0
            artifacts = session.scalar(
                select(func.count(Artifact.id)).where(
                    Artifact.owner_user_id == auth.user_id,
                    Artifact.workspace_id == auth.workspace_id,
                )
            ) or 0
            usage = session.execute(
                select(
                    func.coalesce(func.sum(UsageLedger.input_tokens), 0),
                    func.coalesce(func.sum(UsageLedger.output_tokens), 0),
                    func.sum(UsageLedger.cost_estimate_usd),
                    func.count(UsageLedger.cost_estimate_usd),
                ).where(
                    UsageLedger.owner_user_id == auth.user_id,
                    UsageLedger.workspace_id == auth.workspace_id,
                )
            ).one()
        input_tokens = int(usage[0] or 0)
        output_tokens = int(usage[1] or 0)
        return AccountUsageSummaryRecord(
            messages=int(messages),
            runs=int(runs),
            artifacts=int(artifacts),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            estimated_cost_usd=float(usage[2]) if usage[3] else None,
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
        now = utc_now()
        with self._session_factory() as session:
            _ensure_auth_entities(session, auth)
            runtime = session.scalar(
                select(NautilusRuntime).where(
                    NautilusRuntime.runtime_key == runtime_key,
                    NautilusRuntime.owner_user_id == auth.user_id,
                    NautilusRuntime.workspace_id == auth.workspace_id,
                )
            )
            if runtime is None:
                runtime = NautilusRuntime(
                    id=opaque_id("nrt"),
                    owner_user_id=auth.user_id,
                    workspace_id=auth.workspace_id,
                    runtime_key=runtime_key,
                    broker_connection_id=broker_connection_id,
                    account_id=account_id,
                    mode=mode,
                    risk_policy_id=risk_policy_id,
                    state="requested",
                    strategy_ids_json=[strategy_id],
                    manifest_json=manifest_json,
                    data_subscriptions_json=data_subscriptions_json,
                    last_heartbeat_at=None,
                    heartbeat_count=0,
                    heartbeat_metrics_json=None,
                    last_heartbeat_event_at=None,
                    kill_switch_active=0,
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
                session.add(runtime)
            else:
                strategy_ids = list(runtime.strategy_ids_json or [])
                if strategy_id not in strategy_ids:
                    strategy_ids.append(strategy_id)
                runtime.strategy_ids_json = strategy_ids
                runtime.manifest_json = manifest_json or runtime.manifest_json
                runtime.data_subscriptions_json = data_subscriptions_json or runtime.data_subscriptions_json
                runtime.updated_at = now
            session.commit()
            return _nautilus_runtime_record(runtime)

    def list_nautilus_runtimes(
        self,
        auth: AuthContext,
        *,
        mode: str | None = None,
        limit: int = 100,
    ) -> list[NautilusRuntimeRecord]:
        with self._session_factory() as session:
            conditions = [
                NautilusRuntime.owner_user_id == auth.user_id,
                NautilusRuntime.workspace_id == auth.workspace_id,
            ]
            if mode is not None:
                conditions.append(NautilusRuntime.mode == mode)
            rows = session.scalars(
                select(NautilusRuntime)
                .where(*conditions)
                .order_by(NautilusRuntime.updated_at.desc(), NautilusRuntime.created_at.desc(), NautilusRuntime.id.desc())
                .limit(_bounded_nautilus_limit(limit))
            ).all()
            return [_nautilus_runtime_record(row) for row in rows]

    def get_nautilus_runtime(self, auth: AuthContext, runtime_id: str) -> NautilusRuntimeRecord | None:
        with self._session_factory() as session:
            runtime = _authorized_nautilus_runtime(session, auth, runtime_id)
            if runtime is None:
                return None
            return _nautilus_runtime_record(runtime)

    def set_nautilus_runtime_state(
        self,
        auth: AuthContext,
        runtime_id: str,
        *,
        state: str,
    ) -> NautilusRuntimeRecord | None:
        now = utc_now()
        with self._session_factory() as session:
            runtime = _authorized_nautilus_runtime(session, auth, runtime_id)
            if runtime is None:
                return None
            runtime.state = state
            runtime.updated_at = now
            session.commit()
            return _nautilus_runtime_record(runtime)

    def record_nautilus_runtime_heartbeat(
        self,
        auth: AuthContext,
        runtime_id: str,
        *,
        now: datetime | None = None,
        payload: dict | None = None,
        idempotency_key: str | None = None,
    ) -> NautilusHeartbeatRecord | None:
        now = now or utc_now()
        with self._session_factory() as session:
            runtime = _authorized_nautilus_runtime(session, auth, runtime_id)
            if runtime is None:
                return None
            record_before = _nautilus_runtime_record(runtime)
            if runtime.kill_switch_active:
                runtime.state = runtime.state if runtime.state in {"stopping", "stopped", "failed"} else "stopping"
            else:
                runtime.state = nautilus_runtime_state_from_heartbeat_payload(payload)
            should_append_event = should_append_nautilus_heartbeat_event(record_before, runtime.state, now)
            event = None
            if should_append_event:
                event = _append_nautilus_runtime_event_in_session(
                    session,
                    runtime,
                    "heartbeat",
                    payload,
                    now=now,
                    idempotency_key=idempotency_key,
                )
            runtime.last_heartbeat_at = now
            runtime.heartbeat_count = (runtime.heartbeat_count or 0) + 1
            runtime.heartbeat_metrics_json = payload
            if event is not None and event.created_at == now:
                runtime.last_heartbeat_event_at = now
            runtime.updated_at = now
            session.commit()
            return NautilusHeartbeatRecord(
                runtime=_nautilus_runtime_record(runtime),
                event=_nautilus_runtime_event_record(event) if event is not None else None,
                event_appended=event is not None and event.created_at == now,
            )

    def activate_nautilus_runtime_kill_switch(
        self,
        auth: AuthContext,
        runtime_id: str,
    ) -> NautilusRuntimeRecord | None:
        now = utc_now()
        with self._session_factory() as session:
            runtime = _authorized_nautilus_runtime(session, auth, runtime_id)
            if runtime is None:
                return None
            runtime.kill_switch_active = 1
            runtime.state = "stopping"
            runtime.desired_state = "stopping"
            runtime.updated_at = now
            session.commit()
            return _nautilus_runtime_record(runtime)

    def set_nautilus_runtime_desired_state(
        self,
        auth: AuthContext,
        runtime_id: str,
        *,
        desired_state: str,
    ) -> NautilusRuntimeRecord | None:
        now = utc_now()
        with self._session_factory() as session:
            runtime = _authorized_nautilus_runtime(session, auth, runtime_id)
            if runtime is None:
                return None
            runtime.desired_state = desired_state
            runtime.updated_at = now
            session.commit()
            return _nautilus_runtime_record(runtime)

    def list_desired_nautilus_runtimes(
        self,
        *,
        mode: str = "paper",
        desired_state: str = "running",
        worker_id: str | None = None,
        limit: int = 100,
    ) -> list[NautilusRuntimeRecord]:
        now = utc_now()
        lease_conditions = [NautilusRuntime.lease_until.is_(None), NautilusRuntime.lease_until < now]
        if worker_id is not None:
            lease_conditions.append(NautilusRuntime.worker_id == worker_id)
        with self._session_factory() as session:
            rows = session.scalars(
                select(NautilusRuntime)
                .where(
                    NautilusRuntime.mode == mode,
                    NautilusRuntime.desired_state == desired_state,
                    NautilusRuntime.state.not_in(("stopped", "failed")),
                    or_(*lease_conditions),
                )
                .order_by(NautilusRuntime.updated_at.asc(), NautilusRuntime.created_at.asc(), NautilusRuntime.id.asc())
                .limit(_bounded_nautilus_limit(limit))
            ).all()
            return [_nautilus_runtime_record(row) for row in rows]

    def list_active_nautilus_market_data_subscriptions(
        self,
        *,
        mode: str = "paper",
        desired_state: str = "running",
        limit: int = 5000,
    ) -> list[dict[str, object]]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(NautilusRuntime.data_subscriptions_json)
                .where(
                    NautilusRuntime.mode == mode,
                    NautilusRuntime.desired_state == desired_state,
                    NautilusRuntime.state.not_in(("stopped", "failed")),
                )
                .order_by(NautilusRuntime.updated_at.asc(), NautilusRuntime.created_at.asc(), NautilusRuntime.id.asc())
                .limit(_bounded_market_data_subscription_limit(limit))
            ).all()
        payloads = [payload for row in rows for payload in (row or [])]
        return normalize_market_data_subscription_payloads(payloads, limit=limit)

    def claim_nautilus_runtime_lease(
        self,
        runtime_id: str,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> NautilusRuntimeRecord | None:
        current = now or utc_now()
        with self._session_factory() as session:
            runtime = session.scalars(
                select(NautilusRuntime)
                .where(
                    NautilusRuntime.id == runtime_id,
                    NautilusRuntime.mode == "paper",
                    NautilusRuntime.desired_state == "running",
                    NautilusRuntime.state.not_in(("stopped", "failed")),
                    or_(
                        NautilusRuntime.worker_id == worker_id,
                        NautilusRuntime.lease_until.is_(None),
                        NautilusRuntime.lease_until < current,
                    ),
                )
                .with_for_update(skip_locked=True)
            ).first()
            if runtime is None:
                return None
            generation = runtime.generation if runtime.worker_id == worker_id else (runtime.generation or 0) + 1
            runtime.worker_id = worker_id
            runtime.lease_until = current + timedelta(seconds=lease_seconds)
            runtime.generation = generation
            if runtime.state == "requested":
                runtime.state = "provisioning"
            runtime.started_at = runtime.started_at or current
            runtime.updated_at = current
            session.commit()
            return _nautilus_runtime_record(runtime)

    def renew_nautilus_runtime_lease(
        self,
        runtime_id: str,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> NautilusRuntimeRecord | None:
        current = now or utc_now()
        with self._session_factory() as session:
            runtime = session.scalars(
                select(NautilusRuntime)
                .where(NautilusRuntime.id == runtime_id, NautilusRuntime.worker_id == worker_id)
                .with_for_update(skip_locked=True)
            ).first()
            if runtime is None:
                return None
            runtime.lease_until = current + timedelta(seconds=lease_seconds)
            runtime.updated_at = current
            session.commit()
            return _nautilus_runtime_record(runtime)

    def release_nautilus_runtime_lease(
        self,
        runtime_id: str,
        *,
        worker_id: str,
        state: str | None = None,
        last_error_json: dict | None = None,
        now: datetime | None = None,
    ) -> NautilusRuntimeRecord | None:
        current = now or utc_now()
        with self._session_factory() as session:
            runtime = session.scalars(
                select(NautilusRuntime)
                .where(NautilusRuntime.id == runtime_id, NautilusRuntime.worker_id == worker_id)
                .with_for_update(skip_locked=True)
            ).first()
            if runtime is None:
                return None
            runtime.worker_id = None
            runtime.lease_until = None
            if state is not None:
                runtime.state = state
            if state in {"stopping", "stopped", "failed"}:
                runtime.stopped_at = current
            runtime.last_error_json = last_error_json
            runtime.updated_at = current
            session.commit()
            return _nautilus_runtime_record(runtime)

    def persist_nautilus_runtime_stream_cursor(
        self,
        runtime_id: str,
        *,
        worker_id: str,
        stream_cursor_json: dict,
        now: datetime | None = None,
    ) -> NautilusRuntimeRecord | None:
        current = now or utc_now()
        with self._session_factory() as session:
            runtime = session.scalars(
                select(NautilusRuntime)
                .where(NautilusRuntime.id == runtime_id, NautilusRuntime.worker_id == worker_id)
                .with_for_update(skip_locked=True)
            ).first()
            if runtime is None:
                return None
            runtime.stream_cursor_json = stream_cursor_json
            runtime.updated_at = current
            session.commit()
            return _nautilus_runtime_record(runtime)

    def append_nautilus_runtime_events_for_worker(
        self,
        runtime_id: str,
        *,
        worker_id: str,
        events: list[NautilusRuntimeEventInput],
    ) -> list[NautilusRuntimeEventRecord] | None:
        for _ in range(3):
            now = utc_now()
            with self._session_factory() as session:
                runtime = session.scalars(
                    select(NautilusRuntime)
                    .where(NautilusRuntime.id == runtime_id, NautilusRuntime.worker_id == worker_id)
                    .with_for_update(skip_locked=True)
                ).first()
                if runtime is None:
                    return None
                next_sequence = (
                    session.scalar(
                        select(func.max(NautilusRuntimeEvent.sequence)).where(NautilusRuntimeEvent.runtime_id == runtime.id)
                    )
                    or 0
                ) + 1
                records: list[NautilusRuntimeEvent] = []
                for event_type, payload, idempotency_key in events:
                    if idempotency_key is not None:
                        existing = session.scalar(
                            select(NautilusRuntimeEvent).where(
                                NautilusRuntimeEvent.runtime_id == runtime.id,
                                NautilusRuntimeEvent.idempotency_key == idempotency_key,
                            )
                        )
                        if existing is not None:
                            records.append(existing)
                            continue
                    event = NautilusRuntimeEvent(
                        id=opaque_id("nevt"),
                        runtime_id=runtime.id,
                        owner_user_id=runtime.owner_user_id,
                        workspace_id=runtime.workspace_id,
                        sequence=next_sequence,
                        type=event_type,
                        payload_json=payload,
                        idempotency_key=idempotency_key,
                        created_at=now,
                    )
                    next_sequence += 1
                    session.add(event)
                    records.append(event)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    continue
                return [_nautilus_runtime_event_record(event) for event in records]
        return None

    def append_nautilus_runtime_event(
        self,
        auth: AuthContext,
        runtime_id: str,
        event_type: str,
        payload: dict | None = None,
        idempotency_key: str | None = None,
    ) -> NautilusRuntimeEventRecord | None:
        for _ in range(3):
            now = utc_now()
            with self._session_factory() as session:
                runtime = _authorized_nautilus_runtime(session, auth, runtime_id)
                if runtime is None:
                    return None
                event = _append_nautilus_runtime_event_in_session(
                    session,
                    runtime,
                    event_type,
                    payload,
                    now=now,
                    idempotency_key=idempotency_key,
                )
                if event.created_at != now:
                    return _nautilus_runtime_event_record(event)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    continue
                return _nautilus_runtime_event_record(event)
        return None

    def list_nautilus_runtime_events(
        self,
        auth: AuthContext,
        runtime_id: str,
        *,
        limit: int = 100,
        after_sequence: int | None = None,
    ) -> list[NautilusRuntimeEventRecord] | None:
        with self._session_factory() as session:
            runtime = _authorized_nautilus_runtime(session, auth, runtime_id)
            if runtime is None:
                return None
            conditions = [
                NautilusRuntimeEvent.runtime_id == runtime.id,
                NautilusRuntimeEvent.owner_user_id == auth.user_id,
                NautilusRuntimeEvent.workspace_id == auth.workspace_id,
            ]
            if after_sequence is not None:
                conditions.append(NautilusRuntimeEvent.sequence > after_sequence)
            rows = session.scalars(
                select(NautilusRuntimeEvent)
                .where(*conditions)
                .order_by(NautilusRuntimeEvent.sequence.asc())
                .limit(_bounded_nautilus_limit(limit))
            ).all()
            return [_nautilus_runtime_event_record(row) for row in rows]

    def create_bot_proposal(
        self,
        auth: AuthContext,
        create_input: BotProposalCreateInput,
    ) -> BotProposalRecord:
        now = utc_now()
        with self._session_factory() as session:
            _ensure_auth_entities(session, auth)
            proposal = BotProposal(
                id=opaque_id("botp"),
                owner_user_id=auth.user_id,
                workspace_id=auth.workspace_id,
                status=create_input.status,
                source_conversation_id=create_input.source_conversation_id,
                source_run_id=create_input.source_run_id,
                source_artifact_ids_json=list(create_input.source_artifact_ids),
                strategy_id=create_input.strategy_id,
                strategy_name=create_input.strategy_name,
                manifest_json=create_input.manifest_json,
                data_subscriptions_json=list(create_input.data_subscriptions_json),
                broker_connection_id=create_input.broker_connection_id,
                account_id=create_input.account_id,
                risk_policy_id=create_input.risk_policy_id,
                readiness_checks_json=list(create_input.readiness_checks_json),
                missing_inputs_json=list(create_input.missing_inputs_json),
                created_at=now,
                updated_at=now,
            )
            session.add(proposal)
            session.commit()
            return _bot_proposal_record(proposal)

    def get_bot_proposal(self, auth: AuthContext, proposal_id: str) -> BotProposalRecord | None:
        with self._session_factory() as session:
            proposal = _authorized_bot_proposal(session, auth, proposal_id)
            return _bot_proposal_record(proposal) if proposal is not None else None

    def mark_bot_proposal_started(
        self,
        auth: AuthContext,
        proposal_id: str,
        *,
        runtime_id: str,
    ) -> BotProposalRecord | None:
        now = utc_now()
        with self._session_factory() as session:
            proposal = _authorized_bot_proposal(session, auth, proposal_id)
            if proposal is None:
                return None
            proposal.status = BOT_PROPOSAL_STATUS_STARTED
            proposal.runtime_id = runtime_id
            proposal.updated_at = now
            session.commit()
            return _bot_proposal_record(proposal)

    def cleanup_nautilus_heartbeat_events(
        self,
        auth: AuthContext,
        runtime_id: str,
        *,
        now: datetime | None = None,
        max_age: timedelta = NAUTILUS_HEARTBEAT_RETENTION_MAX_AGE,
        max_samples: int = NAUTILUS_HEARTBEAT_RETENTION_MAX_SAMPLES,
    ) -> int | None:
        current = now or utc_now()
        cutoff = current - max_age
        with self._session_factory() as session:
            runtime = _authorized_nautilus_runtime(session, auth, runtime_id)
            if runtime is None:
                return None
            heartbeat_events = session.scalars(
                select(NautilusRuntimeEvent)
                .where(
                    NautilusRuntimeEvent.runtime_id == runtime.id,
                    NautilusRuntimeEvent.owner_user_id == auth.user_id,
                    NautilusRuntimeEvent.workspace_id == auth.workspace_id,
                    NautilusRuntimeEvent.type == "heartbeat",
                )
                .order_by(NautilusRuntimeEvent.created_at.desc(), NautilusRuntimeEvent.sequence.desc())
            ).all()
            keep_ids = {event.id for event in heartbeat_events[: max(0, max_samples)]}
            removed = 0
            for event in heartbeat_events:
                if event.id in keep_ids and _utc_aware(event.created_at) >= cutoff:
                    continue
                session.delete(event)
                removed += 1
            session.commit()
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
        now = utc_now()
        with self._session_factory() as session:
            conversation = _authorized_conversation(session, auth, conversation_id)
            if conversation is None:
                return None
            run = _authorized_run(session, auth, run_id) if run_id else None
            if run_id is not None and (run is None or run.conversation_id != conversation_id):
                return None
            if message_id is not None:
                message = session.get(ConversationMessage, message_id)
                if (
                    message is None
                    or message.conversation_id != conversation_id
                    or message.owner_user_id != auth.user_id
                    or message.workspace_id != auth.workspace_id
                ):
                    return None
            if artifact_id is not None:
                artifact = session.get(Artifact, artifact_id)
                if (
                    artifact is None
                    or artifact.owner_user_id != auth.user_id
                    or artifact.workspace_id != auth.workspace_id
                    or (artifact.conversation_id is not None and artifact.conversation_id != conversation_id)
                ):
                    return None
            record = Feedback(
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
            session.add(record)
            session.commit()
            return _feedback_record(record)


def _ensure_auth_entities(session: Session, auth: AuthContext) -> None:
    user = session.get(User, auth.user_id)
    if user is None:
        session.add(User(id=auth.user_id, external_subject=auth.user_id, display_name=auth.user_id))
    workspace = session.get(Workspace, auth.workspace_id)
    if workspace is None:
        session.add(Workspace(id=auth.workspace_id, name=auth.workspace_id))
    membership = session.get(WorkspaceMembership, {"workspace_id": auth.workspace_id, "user_id": auth.user_id})
    if membership is None:
        session.add(WorkspaceMembership(workspace_id=auth.workspace_id, user_id=auth.user_id, role="owner"))


def _authorized_conversation(
    session: Session,
    auth: AuthContext,
    conversation_id: str,
) -> ConversationThread | None:
    return session.scalar(
        select(ConversationThread).where(
            ConversationThread.id == conversation_id,
            ConversationThread.owner_user_id == auth.user_id,
            ConversationThread.workspace_id == auth.workspace_id,
            ConversationThread.deleted_at.is_(None),
        )
    )


def _authorized_run(
    session: Session,
    auth: AuthContext,
    run_id: str,
) -> AssistantRun | None:
    return session.scalar(
        select(AssistantRun).where(
            AssistantRun.id == run_id,
            AssistantRun.owner_user_id == auth.user_id,
            AssistantRun.workspace_id == auth.workspace_id,
        )
    )


def _authorized_nautilus_runtime(
    session: Session,
    auth: AuthContext,
    runtime_id: str,
) -> NautilusRuntime | None:
    return session.scalar(
        select(NautilusRuntime).where(
            NautilusRuntime.id == runtime_id,
            NautilusRuntime.owner_user_id == auth.user_id,
            NautilusRuntime.workspace_id == auth.workspace_id,
        )
    )


def _authorized_bot_proposal(
    session: Session,
    auth: AuthContext,
    proposal_id: str,
) -> BotProposal | None:
    return session.scalar(
        select(BotProposal).where(
            BotProposal.id == proposal_id,
            BotProposal.owner_user_id == auth.user_id,
            BotProposal.workspace_id == auth.workspace_id,
        )
    )


def _append_nautilus_runtime_event_in_session(
    session: Session,
    runtime: NautilusRuntime,
    event_type: str,
    payload: dict | None,
    *,
    now: datetime,
    idempotency_key: str | None = None,
) -> NautilusRuntimeEvent:
    if idempotency_key is not None:
        existing = session.scalar(
            select(NautilusRuntimeEvent).where(
                NautilusRuntimeEvent.runtime_id == runtime.id,
                NautilusRuntimeEvent.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing
    last_sequence = (
        session.scalar(select(func.max(NautilusRuntimeEvent.sequence)).where(NautilusRuntimeEvent.runtime_id == runtime.id))
        or 0
    )
    event = NautilusRuntimeEvent(
        id=opaque_id("nevt"),
        runtime_id=runtime.id,
        owner_user_id=runtime.owner_user_id,
        workspace_id=runtime.workspace_id,
        sequence=last_sequence + 1,
        type=event_type,
        payload_json=payload,
        idempotency_key=idempotency_key,
        created_at=now,
    )
    session.add(event)
    return event


def _bounded_nautilus_limit(limit: int | None) -> int:
    if limit is None:
        return 100
    return min(500, max(1, int(limit)))


def _conversation_record(conversation: ConversationThread) -> ConversationRecord:
    return ConversationRecord(
        id=conversation.id,
        owner_user_id=conversation.owner_user_id,
        workspace_id=conversation.workspace_id,
        title=conversation.title,
        created_at=_utc_aware(conversation.created_at),
        updated_at=_utc_aware(conversation.updated_at),
    )


def _message_record(message: ConversationMessage) -> MessageRecord:
    return MessageRecord(
        id=message.id,
        conversation_id=message.conversation_id,
        owner_user_id=message.owner_user_id,
        workspace_id=message.workspace_id,
        role=message.role,
        content=message.content,
        created_at=_utc_aware(message.created_at),
    )


def _conversation_memory_record(memory: ConversationMemory) -> ConversationMemoryRecord:
    return ConversationMemoryRecord(
        id=memory.id,
        conversation_id=memory.conversation_id,
        owner_user_id=memory.owner_user_id,
        workspace_id=memory.workspace_id,
        summary=memory.summary,
        covered_message_id=memory.covered_message_id,
        summary_version=memory.summary_version,
        estimated_tokens=memory.estimated_tokens,
        created_at=_utc_aware(memory.created_at),
        updated_at=_utc_aware(memory.updated_at),
    )


def _run_record(run: AssistantRun) -> AssistantRunRecord:
    return AssistantRunRecord(
        id=run.id,
        conversation_id=run.conversation_id,
        owner_user_id=run.owner_user_id,
        workspace_id=run.workspace_id,
        status=run.status,
        created_at=_utc_aware(run.created_at),
        updated_at=_utc_aware(run.updated_at),
        mode=run.mode,
        retry_of_run_id=run.retry_of_run_id,
        request_id=run.request_id,
        trace_id=run.trace_id,
    )


def _run_job_record(job: RunJob) -> RunJobRecord:
    return RunJobRecord(
        id=job.id,
        run_id=job.run_id,
        owner_user_id=job.owner_user_id,
        workspace_id=job.workspace_id,
        job_type=job.job_type,
        status=job.status,
        payload_json=job.payload_json,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        lease_owner=job.lease_owner,
        leased_until=_utc_aware(job.leased_until) if job.leased_until is not None else None,
        result_json=job.result_json,
        error_code=job.error_code,
        created_at=_utc_aware(job.created_at),
        updated_at=_utc_aware(job.updated_at),
    )


def _job_workspace_active_limit(payload_json: dict) -> int:
    return backtest_active_limit_from_payload(payload_json)


def _run_event_record(event: RunEvent, run: AssistantRun | None = None) -> RunEventRecord:
    return RunEventRecord(
        id=event.id,
        run_id=event.run_id,
        conversation_id=event.conversation_id,
        owner_user_id=event.owner_user_id,
        workspace_id=event.workspace_id,
        sequence=event.sequence,
        type=event.type,
        payload=event.payload_json,
        created_at=_utc_aware(event.created_at),
        request_id=run.request_id if run is not None else None,
        trace_id=run.trace_id if run is not None else None,
    )


def _workflow_task_record(task: WorkflowTask) -> WorkflowTaskRecord:
    return WorkflowTaskRecord(
        id=task.id,
        conversation_id=task.conversation_id,
        run_id=task.run_id,
        owner_user_id=task.owner_user_id,
        workspace_id=task.workspace_id,
        workflow_id=task.workflow_id,
        task_template_id=task.task_template_id,
        step_id=task.step_id,
        kind=task.kind,
        status=task.status,
        payload_json=task.payload_json,
        response_json=task.response_json,
        created_at=_utc_aware(task.created_at),
        updated_at=_utc_aware(task.updated_at),
        resolved_at=_utc_aware(task.resolved_at) if task.resolved_at is not None else None,
    )


def _artifact_record(artifact: Artifact) -> ArtifactRecord:
    return ArtifactRecord(
        id=artifact.id,
        run_id=artifact.run_id,
        conversation_id=artifact.conversation_id,
        owner_user_id=artifact.owner_user_id,
        workspace_id=artifact.workspace_id,
        kind=artifact.kind,
        mime_type=artifact.mime_type,
        display_name=artifact.display_name,
        storage_key=artifact.storage_key,
        metadata_json=artifact.metadata_json,
        created_at=_utc_aware(artifact.created_at),
    )


def _backtest_report_summary(report: BacktestReport) -> dict:
    return {
        "run_id": report.run_id,
        "engine": report.engine,
        "evidence_label": report.evidence_label,
        "execution_semantics": report.execution_semantics,
        "symbol": report.symbol,
        "signal_timeframe": report.signal_timeframe,
        "candle_timeframe": report.candle_timeframe,
        "metrics": report.metrics_json,
        "assumptions": report.assumptions_json or [],
        "warnings": report.warnings_json or [],
        "reproducibility_hash": report.reproducibility_hash,
    }


def _backtest_equity_summary(summary: BacktestEquitySummary) -> dict:
    return {
        "run_id": summary.run_id,
        "sample_resolution": summary.sample_resolution,
        "points": summary.points_json,
        "drawdown_windows": summary.drawdown_windows_json or [],
        "monthly_returns": summary.monthly_returns_json or [],
    }


def _strategy_spec_record(spec: StrategySpec) -> StrategySpecRecord:
    return StrategySpecRecord(
        id=spec.id,
        run_id=spec.run_id,
        owner_user_id=spec.owner_user_id,
        workspace_id=spec.workspace_id,
        payload_json=spec.payload_json,
        schema_version=spec.schema_version,
        created_at=_utc_aware(spec.created_at),
    )


def _validation_report_record(report: ValidationReport) -> ValidationReportRecord:
    return ValidationReportRecord(
        id=report.id,
        run_id=report.run_id,
        owner_user_id=report.owner_user_id,
        workspace_id=report.workspace_id,
        status=report.status,
        payload_json=report.payload_json,
        created_at=_utc_aware(report.created_at),
    )


def _review_report_record(report: ReviewReport) -> ReviewReportRecord:
    return ReviewReportRecord(
        id=report.id,
        run_id=report.run_id,
        owner_user_id=report.owner_user_id,
        workspace_id=report.workspace_id,
        decision=report.decision,
        payload_json=report.payload_json,
        created_at=_utc_aware(report.created_at),
    )


def _tool_call_record(tool_call: ToolCall) -> ToolCallRecord:
    return ToolCallRecord(
        id=tool_call.id,
        run_id=tool_call.run_id,
        tool_id=tool_call.tool_id,
        status=tool_call.status,
        input_json=tool_call.input_json,
        output_json=tool_call.output_json,
        policy_findings_json=tool_call.policy_findings_json,
        created_at=_utc_aware(tool_call.created_at),
        started_at=_utc_aware(tool_call.started_at) if tool_call.started_at else None,
        completed_at=_utc_aware(tool_call.completed_at) if tool_call.completed_at else None,
    )


def _policy_finding_record(finding: PolicyFinding) -> PolicyFindingRecord:
    return PolicyFindingRecord(
        id=finding.id,
        run_id=finding.run_id,
        tool_call_id=finding.tool_call_id,
        owner_user_id=finding.owner_user_id,
        workspace_id=finding.workspace_id,
        severity=finding.severity,
        code=finding.code,
        message=finding.message,
        created_at=_utc_aware(finding.created_at),
    )


def _usage_ledger_record(record: UsageLedger) -> UsageLedgerRecord:
    return UsageLedgerRecord(
        id=record.id,
        owner_user_id=record.owner_user_id,
        workspace_id=record.workspace_id,
        run_id=record.run_id,
        model=record.model,
        tool_id=record.tool_id,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        cost_estimate_usd=float(record.cost_estimate_usd) if record.cost_estimate_usd is not None else None,
        created_at=_utc_aware(record.created_at),
    )


def _nautilus_runtime_record(runtime: NautilusRuntime) -> NautilusRuntimeRecord:
    return NautilusRuntimeRecord(
        id=runtime.id,
        owner_user_id=runtime.owner_user_id,
        workspace_id=runtime.workspace_id,
        runtime_key=runtime.runtime_key,
        broker_connection_id=runtime.broker_connection_id,
        account_id=runtime.account_id,
        mode=runtime.mode,
        risk_policy_id=runtime.risk_policy_id,
        state=runtime.state,
        strategy_ids=list(runtime.strategy_ids_json or []),
        manifest_json=runtime.manifest_json or {},
        data_subscriptions_json=list(runtime.data_subscriptions_json or []),
        last_heartbeat_at=_utc_aware(runtime.last_heartbeat_at) if runtime.last_heartbeat_at else None,
        heartbeat_count=int(runtime.heartbeat_count or 0),
        heartbeat_metrics_json=runtime.heartbeat_metrics_json,
        last_heartbeat_event_at=_utc_aware(runtime.last_heartbeat_event_at) if runtime.last_heartbeat_event_at else None,
        kill_switch_active=bool(runtime.kill_switch_active),
        desired_state=runtime.desired_state,
        worker_id=runtime.worker_id,
        lease_until=_utc_aware(runtime.lease_until) if runtime.lease_until else None,
        generation=int(runtime.generation or 0),
        started_at=_utc_aware(runtime.started_at) if runtime.started_at else None,
        stopped_at=_utc_aware(runtime.stopped_at) if runtime.stopped_at else None,
        last_error_json=runtime.last_error_json,
        stream_cursor_json=runtime.stream_cursor_json,
        created_at=_utc_aware(runtime.created_at),
        updated_at=_utc_aware(runtime.updated_at),
    )


def _nautilus_runtime_event_record(event: NautilusRuntimeEvent) -> NautilusRuntimeEventRecord:
    return NautilusRuntimeEventRecord(
        id=event.id,
        runtime_id=event.runtime_id,
        owner_user_id=event.owner_user_id,
        workspace_id=event.workspace_id,
        sequence=event.sequence,
        type=event.type,
        payload=event.payload_json,
        created_at=_utc_aware(event.created_at),
        idempotency_key=event.idempotency_key,
    )


def _bot_proposal_record(proposal: BotProposal) -> BotProposalRecord:
    return BotProposalRecord(
        id=proposal.id,
        owner_user_id=proposal.owner_user_id,
        workspace_id=proposal.workspace_id,
        status=proposal.status,
        source_conversation_id=proposal.source_conversation_id,
        source_run_id=proposal.source_run_id,
        source_artifact_ids=list(proposal.source_artifact_ids_json or []),
        strategy_id=proposal.strategy_id,
        strategy_name=proposal.strategy_name,
        manifest_json=proposal.manifest_json or {},
        data_subscriptions_json=list(proposal.data_subscriptions_json or []),
        broker_connection_id=proposal.broker_connection_id,
        account_id=proposal.account_id,
        risk_policy_id=proposal.risk_policy_id,
        readiness_checks_json=list(proposal.readiness_checks_json or []),
        missing_inputs_json=list(proposal.missing_inputs_json or []),
        runtime_id=proposal.runtime_id,
        created_at=_utc_aware(proposal.created_at),
        updated_at=_utc_aware(proposal.updated_at),
    )


def _feedback_record(record: Feedback) -> FeedbackRecord:
    return FeedbackRecord(
        id=record.id,
        conversation_id=record.conversation_id,
        run_id=record.run_id,
        message_id=record.message_id,
        artifact_id=record.artifact_id,
        owner_user_id=record.owner_user_id,
        workspace_id=record.workspace_id,
        request_id=record.request_id,
        trace_id=record.trace_id,
        rating=record.rating,
        category=record.category,
        correction=record.correction,
        created_at=_utc_aware(record.created_at),
    )


def _last_event_sequence(session: Session, run: AssistantRun, last_event_id: str | None) -> int | None:
    if not last_event_id:
        return None
    if last_event_id.isdecimal():
        return int(last_event_id)
    return session.scalar(
        select(RunEvent.sequence).where(
            RunEvent.run_id == run.id,
            RunEvent.id == last_event_id,
            RunEvent.owner_user_id == run.owner_user_id,
            RunEvent.workspace_id == run.workspace_id,
        )
    )


def _utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
