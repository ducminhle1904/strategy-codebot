from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
from typing import Any, Literal, TypedDict

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.responses import StreamingResponse

from strategy_codebot import __version__
from strategy_codebot.nautilus_runtime import RuntimeKey
from strategy_codebot.nautilus_runtime import runtime_state_after_event
from strategy_codebot.pine import validate_pineforge_pine
from strategy_codebot.schemas import validate_payload as validate_schema_payload
from strategy_codebot.server.action_registry import ACTION_REGISTRY
from strategy_codebot.server.agent_logging import agent_log
from strategy_codebot.server.artifact_kinds import BACKTEST_DASHBOARD_ARTIFACT_KIND
from strategy_codebot.server.artifact_kinds import BACKTEST_PLAN_ARTIFACT_KIND
from strategy_codebot.server.artifact_kinds import BACKTEST_REPORT_ARTIFACT_KIND
from strategy_codebot.server.artifact_kinds import BACKTEST_TRADES_ARTIFACT_KIND
from strategy_codebot.server.artifact_kinds import BACKTEST_VARIANT_COMPARISON_ARTIFACT_KIND
from strategy_codebot.server.artifact_kinds import EVIDENCE_ARTIFACT_KINDS
from strategy_codebot.server.artifact_kinds import INTERNAL_ARTIFACT_KINDS
from strategy_codebot.server.artifact_kinds import REPORT_ARTIFACT_KINDS
from strategy_codebot.server.artifact_kinds import ROBUSTNESS_REPORT_ARTIFACT_KIND
from strategy_codebot.server.artifact_kinds import TRACE_ARTIFACT_KINDS
from strategy_codebot.server.artifact_kinds import USER_ARTIFACT_KINDS
from strategy_codebot.server.artifact_store import LocalArtifactStore
from strategy_codebot.server.auth import AuthContext, require_auth_context
from strategy_codebot.server.database import create_sqlalchemy_repository
from strategy_codebot.server.llm_clients import LLMClient
from strategy_codebot.server.llm_clients import ProviderConfigurationError
from strategy_codebot.server.llm_clients import ResponsesClient
from strategy_codebot.server.llm_tools import _required_pineforge_pine
from strategy_codebot.server.llm_tools import decide_backtest_preview_approval
from strategy_codebot.server.llm_orchestrator import LLMOrchestrator
from strategy_codebot.server.market_data import MarketDataGateway
from strategy_codebot.server.knowledge_learning import KnowledgeLearningService
from strategy_codebot.server.knowledge_learning import require_knowledge_admin
from strategy_codebot.server.model_routing import DEFAULT_MODEL_STAGE
from strategy_codebot.server.model_routing import MODEL_STAGE_BALANCED_REVIEW
from strategy_codebot.server.model_routing import MODEL_STAGE_PINE_CODE_GENERATION
from strategy_codebot.server.model_routing import MODEL_STAGE_REPAIR
from strategy_codebot.server.model_routing import gateway_env_report
from strategy_codebot.server.model_routing import load_model_registry
from strategy_codebot.server.model_routing import normalize_user_tier
from strategy_codebot.server.model_routing import resolve_routes
from strategy_codebot.server.model_audit import WORKFLOW_GATE_CONFIRMED
from strategy_codebot.server.model_audit import WORKFLOW_GATE_REJECTED
from strategy_codebot.server.model_audit import append_model_audit_event
from strategy_codebot.server.observability import append_stage_event
from strategy_codebot.server.observability import build_observability_summary
from strategy_codebot.server.observability import ensure_harness_evidence_artifact
from strategy_codebot.server.policy import EVIDENCE_GENERATED_ARTIFACT
from strategy_codebot.server.policy import EVIDENCE_MANUAL_RUNTIME_PROOF
from strategy_codebot.server.policy import EVIDENCE_STATIC_VALIDATION
from strategy_codebot.server.policy import EVIDENCE_STRATEGY_IDEA
from strategy_codebot.server.policy import PolicyFinding
from strategy_codebot.server.policy import PolicySubject
from strategy_codebot.server.policy import SAFE_BLOCKED_MESSAGE
from strategy_codebot.server.policy import evaluate_policy
from strategy_codebot.server.policy import policy_finding_payload
from strategy_codebot.server.provider_errors import log_provider_exception
from strategy_codebot.server.provider_errors import provider_error_payload
from strategy_codebot.server.readiness import build_readiness_payload
from strategy_codebot.server.redaction import redact_text
from strategy_codebot.server.redaction import redact_value
from strategy_codebot.server.bot_proposals import BotProposalArtifactUnreadableError
from strategy_codebot.server.bot_proposals import BotProposalDraftInput
from strategy_codebot.server.bot_proposals import BotProposalSourceNotFoundError
from strategy_codebot.server.bot_proposals import bot_required_missing
from strategy_codebot.server.bot_proposals import build_bot_proposal_create_input
from strategy_codebot.server.repository import ArtifactRecord
from strategy_codebot.server.repository import AssistantRunRecord
from strategy_codebot.server.repository import BotProposalRecord
from strategy_codebot.server.repository import ConversationSidebarRecord
from strategy_codebot.server.repository import ConversationRepository
from strategy_codebot.server.repository import CONVERSATION_ARTIFACT_PAGE_MAX_LIMIT
from strategy_codebot.server.repository import CONVERSATION_ARTIFACT_STATE_LIMIT
from strategy_codebot.server.repository import InMemoryConversationRepository
from strategy_codebot.server.repository import MessageRecord
from strategy_codebot.server.repository import NautilusRuntimeEventRecord
from strategy_codebot.server.repository import NautilusRuntimeRecord
from strategy_codebot.server.repository import RunEventRecord
from strategy_codebot.server.repository import TERMINAL_RUN_STATUSES
from strategy_codebot.server.repository import WorkflowTaskRecord
from strategy_codebot.server.run_modes import RUN_MODE_AGENT
from strategy_codebot.server.run_modes import RUN_MODE_BACKTEST_PREVIEW
from strategy_codebot.server.run_modes import BACKTEST_ENGINE_PINEFORGE
from strategy_codebot.server.run_modes import RUN_MODE_LIVE_GENERATION
from strategy_codebot.server.run_modes import RUN_MODES
from strategy_codebot.server.run_modes import backtest_job_limits_for_tier
from strategy_codebot.server.run_modes import backtest_runtime_boundary
from strategy_codebot.server.schemas import ArtifactContentResponse
from strategy_codebot.server.schemas import ArtifactListResponse
from strategy_codebot.server.schemas import ArtifactResponse
from strategy_codebot.server.schemas import ArtifactPreviewResponse
from strategy_codebot.server.schemas import BacktestApprovalDecisionRequest
from strategy_codebot.server.schemas import BacktestApprovalDecisionResponse
from strategy_codebot.server.schemas import AccountUsageResponse
from strategy_codebot.server.schemas import BotProposalConfirmStartRequest
from strategy_codebot.server.schemas import BotProposalConfirmStartResponse
from strategy_codebot.server.schemas import BotProposalCreateRequest
from strategy_codebot.server.schemas import BotProposalResponse
from strategy_codebot.server.schemas import ConversationCreate
from strategy_codebot.server.schemas import ConversationListResponse
from strategy_codebot.server.schemas import ConversationResponse
from strategy_codebot.server.schemas import ConversationSidebarItem
from strategy_codebot.server.schemas import ConversationSidebarResponse
from strategy_codebot.server.schemas import ConversationStateResponse
from strategy_codebot.server.schemas import ConversationUpdate
from strategy_codebot.server.schemas import FeedbackCreate
from strategy_codebot.server.schemas import FeedbackOption
from strategy_codebot.server.schemas import FeedbackOptionsResponse
from strategy_codebot.server.schemas import FeedbackResponse
from strategy_codebot.server.schemas import CapabilityModeStatus
from strategy_codebot.server.schemas import KnowledgeCandidateCreate
from strategy_codebot.server.schemas import KnowledgeCandidateListResponse
from strategy_codebot.server.schemas import KnowledgeCandidateResponse
from strategy_codebot.server.schemas import KnowledgeAutoReviewRequest
from strategy_codebot.server.schemas import KnowledgeLearningRequest
from strategy_codebot.server.schemas import KnowledgeLearningResponse
from strategy_codebot.server.schemas import MessageCreate
from strategy_codebot.server.schemas import MessageListResponse
from strategy_codebot.server.schemas import MessageResponse
from strategy_codebot.server.schemas import MeResponse
from strategy_codebot.server.schemas import NautilusRuntimeEventCreate
from strategy_codebot.server.schemas import NautilusRuntimeEventIngestResponse
from strategy_codebot.server.schemas import NautilusRuntimeEventResponse
from strategy_codebot.server.schemas import NautilusRuntimeHeartbeatRequest
from strategy_codebot.server.schemas import NautilusRuntimeKillSwitchRequest
from strategy_codebot.server.schemas import NautilusRuntimeListResponse
from strategy_codebot.server.schemas import NautilusRuntimeResponse
from strategy_codebot.server.schemas import NautilusRuntimeStartRequest
from strategy_codebot.server.schemas import ProviderStatusResponse
from strategy_codebot.server.schemas import RunCreate
from strategy_codebot.server.schemas import RunCreateResponse
from strategy_codebot.server.schemas import RunEventResponse
from strategy_codebot.server.schemas import RunResponse
from strategy_codebot.server.schemas import StrategyAssumptionsResponse
from strategy_codebot.server.schemas import StrategyBriefResponse
from strategy_codebot.server.schemas import StrategyCodeOutlineItemResponse
from strategy_codebot.server.schemas import StrategyMemoryResponse
from strategy_codebot.server.schemas import StrategyProfileResponse
from strategy_codebot.server.schemas import StrategySnapshotResponse
from strategy_codebot.server.schemas import WorkspaceCapabilityResponse
from strategy_codebot.server.schemas import WorkflowTaskListResponse
from strategy_codebot.server.schemas import WorkflowTaskContinuationRequest
from strategy_codebot.server.schemas import WorkflowTaskResponse
from strategy_codebot.server.schemas import WorkflowTaskResponseRequest
from strategy_codebot.server.runner_bridge import execute_dry_run
from strategy_codebot.server.runner_bridge import execute_live_generation
from strategy_codebot.server.runner_bridge import RunnerIntegrationResult
from strategy_codebot.server.security_controls import BudgetExceeded
from strategy_codebot.server.security_controls import IdempotencyRecord
from strategy_codebot.server.security_controls import RateLimitConfig
from strategy_codebot.server.security_controls import RedisSecurityControls
from strategy_codebot.server.security_controls import RunBudgetConfig
from strategy_codebot.server.security_controls import SecurityControlError
from strategy_codebot.server.security_controls import SecurityControls
from strategy_codebot.server.security_controls import budget_policy_finding
from strategy_codebot.server.security_controls import security_error_payload
from strategy_codebot.server.streaming import DETERMINISTIC_DELTA_CHUNKS
from strategy_codebot.server.streaming import compact_delta_text
from strategy_codebot.server.workflow_tasks import WorkflowTaskValidationError
from strategy_codebot.server.workflow_tasks import validate_workflow_task_response
from strategy_codebot.server.workflow_tasks import workflow_task_continuation_state
from strategy_codebot.server.workflow_tasks import workflow_task_response_status
from strategy_codebot.server.workflow_tasks import workflow_task_resume_context
from strategy_codebot.server.workflow_tasks import workflow_task_state
from strategy_codebot.server.workflow_task_status import WORKFLOW_TASK_RESOLVED_STATUSES
from strategy_codebot.server.streaming import sse_frame
from strategy_codebot.server.streaming import transient_delta_event
from strategy_codebot.server.ids import opaque_id
from strategy_codebot.server.worker import InlineRunWorker
from strategy_codebot.server.worker import RunWorker

logger = logging.getLogger(__name__)

ARTIFACT_PREVIEW_DEFAULT_BYTES = 16 * 1024
ARTIFACT_PREVIEW_MAX_BYTES = 64 * 1024
FEEDBACK_RATING_OPTIONS = (
    ("up", "Up"),
    ("down", "Down"),
    ("neutral", "Neutral"),
)
FEEDBACK_CATEGORY_OPTIONS = (
    ("incorrect_strategy", "Incorrect strategy"),
    ("unsafe_claim", "Unsafe claim"),
    ("bad_artifact", "Bad artifact"),
    ("missing_evidence", "Missing evidence"),
    ("other", "Other"),
)
DEFAULT_CORS_ALLOW_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)
DEFAULT_CORS_ALLOW_ORIGIN_REGEX = (
    r"^https?://("
    r"localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"):3000$"
)


@dataclass(frozen=True)
class ServerAppConfig:
    repository: ConversationRepository | None = None
    database_url: str | None = None
    artifact_root: Path | str | None = None
    llm_client: LLMClient | None = None
    llm_max_tool_calls: int = 8
    redis_url: str | None = None
    redis_client: object | None = None
    rate_limit_config: RateLimitConfig | None = None
    budget_config: RunBudgetConfig | None = None
    security_controls: SecurityControls | None = None
    run_worker: RunWorker | None = None
    cors_allow_origins: tuple[str, ...] | None = None
    market_data_gateway: MarketDataGateway | None = None


def _resolve_app_config(
    config: ServerAppConfig | None,
    *,
    repository: ConversationRepository | None,
    database_url: str | None,
    artifact_root: Path | str | None,
    llm_client: LLMClient | None,
    llm_max_tool_calls: int | None,
    redis_url: str | None,
    redis_client: object | None,
    rate_limit_config: RateLimitConfig | None,
    budget_config: RunBudgetConfig | None,
    security_controls: SecurityControls | None,
    run_worker: RunWorker | None,
    cors_allow_origins: tuple[str, ...] | None,
    market_data_gateway: MarketDataGateway | None,
) -> ServerAppConfig:
    base = config or ServerAppConfig()
    overrides = {
        "repository": repository,
        "database_url": database_url,
        "artifact_root": artifact_root,
        "llm_client": llm_client,
        "llm_max_tool_calls": llm_max_tool_calls,
        "redis_url": redis_url,
        "redis_client": redis_client,
        "rate_limit_config": rate_limit_config,
        "budget_config": budget_config,
        "security_controls": security_controls,
        "run_worker": run_worker,
        "cors_allow_origins": cors_allow_origins,
        "market_data_gateway": market_data_gateway,
    }
    return replace(base, **{key: value for key, value in overrides.items() if value is not None})


def _resolve_cors_allow_origins(configured: tuple[str, ...] | None) -> tuple[str, ...]:
    if configured is not None:
        return configured
    raw = os.getenv("STRATEGY_CODEBOT_API_CORS_ORIGINS")
    if raw:
        return tuple(origin.strip() for origin in raw.split(",") if origin.strip())
    return DEFAULT_CORS_ALLOW_ORIGINS


def _resolve_cors_allow_origin_regex() -> str:
    return os.getenv("STRATEGY_CODEBOT_API_CORS_ORIGIN_REGEX", DEFAULT_CORS_ALLOW_ORIGIN_REGEX)


def create_app(
    config: ServerAppConfig | None = None,
    repository: ConversationRepository | None = None,
    database_url: str | None = None,
    artifact_root: Path | str | None = None,
    llm_client: LLMClient | None = None,
    llm_max_tool_calls: int | None = None,
    redis_url: str | None = None,
    redis_client: object | None = None,
    rate_limit_config: RateLimitConfig | None = None,
    budget_config: RunBudgetConfig | None = None,
    security_controls: SecurityControls | None = None,
    run_worker: RunWorker | None = None,
    cors_allow_origins: tuple[str, ...] | None = None,
    market_data_gateway: MarketDataGateway | None = None,
) -> FastAPI:
    resolved = _resolve_app_config(
        config,
        repository=repository,
        database_url=database_url,
        artifact_root=artifact_root,
        llm_client=llm_client,
        llm_max_tool_calls=llm_max_tool_calls,
        redis_url=redis_url,
        redis_client=redis_client,
        rate_limit_config=rate_limit_config,
        budget_config=budget_config,
        security_controls=security_controls,
        run_worker=run_worker,
        cors_allow_origins=cors_allow_origins,
        market_data_gateway=market_data_gateway,
    )
    repository = resolved.repository
    database_url = resolved.database_url
    artifact_root = resolved.artifact_root
    llm_client = resolved.llm_client
    llm_max_tool_calls = resolved.llm_max_tool_calls
    redis_url = resolved.redis_url
    redis_client = resolved.redis_client
    rate_limit_config = resolved.rate_limit_config
    budget_config = resolved.budget_config
    security_controls = resolved.security_controls
    run_worker = resolved.run_worker or InlineRunWorker()
    market_data_gateway = resolved.market_data_gateway or MarketDataGateway.from_env()
    cors_origins = _resolve_cors_allow_origins(resolved.cors_allow_origins)
    cors_origin_regex = _resolve_cors_allow_origin_regex()
    if repository is not None and database_url is not None:
        raise ValueError("Pass either repository or database_url, not both.")
    if repository is not None:
        conversation_repository = repository
    elif database_url is not None:
        conversation_repository = create_sqlalchemy_repository(database_url)
    else:
        conversation_repository = InMemoryConversationRepository()
    artifact_store = LocalArtifactStore(artifact_root)
    run_budget_config = budget_config or RunBudgetConfig()
    controls = security_controls
    if controls is None:
        controls = (
            RedisSecurityControls(
                redis_url=redis_url,
                redis_client=redis_client,
                rate_limit_config=rate_limit_config,
                budget_config=run_budget_config,
            )
            if redis_url is not None or redis_client is not None
            else SecurityControls()
        )
    llm_orchestrator = LLMOrchestrator(
        repository=conversation_repository,
        artifact_store=artifact_store,
        client=llm_client if llm_client is not None else ResponsesClient(),
        max_tool_calls=min(llm_max_tool_calls, run_budget_config.max_tool_calls),
        security_controls=controls,
        budget_config=run_budget_config,
        market_data_gateway=market_data_gateway,
    )
    knowledge_learning_service = KnowledgeLearningService(
        conversation_repository,
        artifact_store,
        llm_client=llm_orchestrator.client,
    )
    api = FastAPI(title="Strategy Codebot API", version=__version__)
    if cors_origins:
        api.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_origins),
            allow_origin_regex=cors_origin_regex,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @api.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "strategy-codebot-api",
            "version": __version__,
        }

    @api.get("/ready")
    def ready() -> JSONResponse:
        payload = build_readiness_payload(
            repository=conversation_repository,
            artifact_store=artifact_store,
            controls=controls,
            llm_orchestrator=llm_orchestrator,
            run_worker=run_worker,
        )
        status_code = status.HTTP_200_OK if payload["status"] == "ok" else status.HTTP_503_SERVICE_UNAVAILABLE
        return JSONResponse(payload, status_code=status_code)

    @api.get("/v1/me", response_model=MeResponse)
    def get_me(
        auth: AuthContext = Depends(require_auth_context),
    ) -> MeResponse:
        capability = _workspace_capability(auth)
        return MeResponse(
            user={"id": auth.user_id},
            workspace={"id": auth.workspace_id, "role": auth.role},
            capability=capability,
        )

    @api.get("/v1/provider/status", response_model=ProviderStatusResponse)
    def get_provider_status(
        auth: AuthContext = Depends(require_auth_context),
    ) -> ProviderStatusResponse:
        capability = _workspace_capability(auth)
        configured = True
        reason = None
        try:
            llm_orchestrator.ensure_configured()
        except Exception as exc:
            configured = False
            reason = exc.__class__.__name__
        routing_status = _user_safe_model_routing_status(auth, configured)
        return ProviderStatusResponse(
            configured=configured,
            available=configured and "agent" in capability.allowed_message_modes,
            tier=capability.tier,
            tier_label=capability.tier_label,
            allowed_message_modes=capability.allowed_message_modes,
            allowed_run_modes=capability.allowed_run_modes,
            capability_matrix=capability.capability_matrix,
            fallback_mode="deterministic",
            model_routing_mode=routing_status["model_routing_mode"],
            model_tier=routing_status["model_tier"],
            selected_stage_defaults=routing_status["selected_stage_defaults"],
            available_gateways=routing_status["available_gateways"],
            route_ready=routing_status["route_ready"],
            fallback_enabled=routing_status["fallback_enabled"],
            user_message=routing_status["user_message"],
            status="ready" if configured else "not_configured",
            reason=reason,
        )

    @api.get("/v1/action-registry")
    def get_action_registry(
        auth: AuthContext = Depends(require_auth_context),
    ) -> dict[str, Any]:
        _ = auth
        return {
            "version": 1,
            "actions": [
                entry.payload(available=True)
                for entry in ACTION_REGISTRY
            ],
        }

    @api.get("/v1/conversations/{conversation_id}/workflow-tasks", response_model=WorkflowTaskListResponse)
    def list_workflow_tasks(
        conversation_id: str,
        auth: AuthContext = Depends(require_auth_context),
    ) -> WorkflowTaskListResponse:
        tasks = conversation_repository.list_workflow_tasks(auth, conversation_id)
        if tasks is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
        return WorkflowTaskListResponse(items=[_workflow_task_response(task) for task in tasks])

    @api.post("/v1/workflow-tasks/{task_id}/responses", response_model=WorkflowTaskResponse)
    def submit_workflow_task_response(
        task_id: str,
        payload: WorkflowTaskResponseRequest,
        request: Request,
        auth: AuthContext = Depends(require_auth_context),
    ) -> WorkflowTaskResponse | JSONResponse:
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="workflow_tasks.response")
        except SecurityControlError as exc:
            return _security_error_response(exc)
        task = conversation_repository.get_workflow_task(auth, task_id)
        if task is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow task not found")
        try:
            response_json = validate_workflow_task_response(
                task,
                values=payload.values,
                action_id=payload.action_id,
                partial=True,
            )
        except WorkflowTaskValidationError as exc:
            _append_workflow_task_audit_event(
                conversation_repository,
                auth,
                task,
                WORKFLOW_GATE_REJECTED,
                {
                    "actor": "backend",
                    "status": "rejected",
                    "reason_code": "task_response_invalid",
                    "action_id": payload.action_id,
                },
            )
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        updated = conversation_repository.submit_workflow_task_response(
            auth,
            task.id,
            response_json=response_json,
            status=workflow_task_response_status(task, response_json, requested_status=payload.status),
        )
        if updated is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow task not found")
        _append_workflow_task_event(
            conversation_repository,
            auth,
            updated,
            "workflow.task.response_submitted",
            {"task_id": updated.id, "task_template_id": updated.task_template_id, "status": updated.status},
        )
        if updated.status in WORKFLOW_TASK_RESOLVED_STATUSES:
            _append_workflow_task_event(
                conversation_repository,
                auth,
                updated,
                "workflow.task.resolved",
                {"task_id": updated.id, "task_template_id": updated.task_template_id, "status": updated.status},
            )
            continuation = (
                workflow_task_continuation_state(updated)
                if task.status not in WORKFLOW_TASK_RESOLVED_STATUSES
                else None
            )
            if continuation is not None:
                _append_workflow_task_event(
                    conversation_repository,
                    auth,
                    updated,
                    "workflow.continuation.required",
                    continuation,
                )
        _append_workflow_task_audit_event(
            conversation_repository,
            auth,
            updated,
            WORKFLOW_GATE_CONFIRMED,
            {
                "actor": "user",
                "status": "executed" if updated.status in WORKFLOW_TASK_RESOLVED_STATUSES else "allowed",
                "decision": updated.status,
                "action_id": payload.action_id,
            },
        )
        return _workflow_task_response(updated)

    @api.post("/v1/workflow-tasks/{task_id}/continuations")
    def continue_workflow_task(
        task_id: str,
        payload: WorkflowTaskContinuationRequest,
        request: Request,
        stream: bool = Query(default=True),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
        x_trace_id: str | None = Header(default=None, alias="X-Trace-Id"),
        auth: AuthContext = Depends(require_auth_context),
    ):
        request_id = x_request_id or opaque_id("req")
        trace_id = x_trace_id or opaque_id("trace")
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="workflow_tasks.continuation")
            idempotency = controls.begin_idempotency(
                auth,
                method=request.method,
                path=request.url.path,
                key=idempotency_key,
                body={"task_id": task_id, **payload.model_dump(mode="json"), "stream": stream},
            )
        except SecurityControlError as exc:
            return _security_error_response(exc)
        if idempotency is not None and idempotency.replay:
            return JSONResponse(idempotency.response, status_code=idempotency.status_code or status.HTTP_200_OK)
        if not stream:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workflow task continuation requires stream=true")
        task = conversation_repository.get_workflow_task(auth, task_id)
        if task is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow task not found")
        continuation = workflow_task_continuation_state(task)
        if continuation is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Workflow task is not resumable")
        continuation_status = _workflow_continuation_event_state(
            conversation_repository,
            auth,
            task.id,
        )
        if continuation_status in {"started", "completed"}:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Workflow continuation already started")
        try:
            llm_orchestrator.ensure_configured()
        except ProviderConfigurationError as exc:
            return _security_error_response(exc)
        agent_log(
            logger,
            "info",
            "workflow.continuation.opened",
            component="api",
            conversation_id=task.conversation_id,
            language=payload.language,
            request_id=request_id,
            task_id=task.id,
            trace_id=trace_id,
            user_tier=auth.user_tier,
            web_search=payload.web_search,
        )
        return StreamingResponse(
            _idempotent_sse_stream(
                llm_orchestrator.stream_chat(
                    auth=auth,
                    conversation_id=task.conversation_id,
                    language=payload.language,
                    message_content=workflow_task_resume_context(task, language=payload.language),
                    current_message_id=None,
                    request_id=request_id,
                    trace_id=trace_id,
                    web_search=payload.web_search,
                    workflow_task_resume=continuation,
                ),
                controls,
                idempotency,
            ),
            media_type="text/event-stream",
            status_code=status.HTTP_200_OK,
        )

    @api.post("/v1/workflow-tasks/{task_id}/actions/{action_id}", response_model=WorkflowTaskResponse)
    def submit_workflow_task_action(
        task_id: str,
        action_id: str,
        request: Request,
        payload: WorkflowTaskResponseRequest | None = None,
        auth: AuthContext = Depends(require_auth_context),
    ) -> WorkflowTaskResponse | JSONResponse:
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="workflow_tasks.action")
        except SecurityControlError as exc:
            return _security_error_response(exc)
        task = conversation_repository.get_workflow_task(auth, task_id)
        if task is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow task not found")
        values = payload.values if payload is not None else {}
        requested_status = payload.status if payload is not None else "approved"
        try:
            response_json = validate_workflow_task_response(task, values=values, action_id=action_id)
        except WorkflowTaskValidationError as exc:
            _append_workflow_task_audit_event(
                conversation_repository,
                auth,
                task,
                WORKFLOW_GATE_REJECTED,
                {
                    "actor": "backend",
                    "status": "rejected",
                    "reason_code": "task_action_invalid",
                    "action_id": action_id,
                },
            )
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        updated = conversation_repository.resolve_workflow_task(
            auth,
            task.id,
            status=requested_status,
            response_json=response_json,
        )
        if updated is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow task not found")
        _append_workflow_task_event(
            conversation_repository,
            auth,
            updated,
            "workflow.gate.updated",
            {
                "task_id": updated.id,
                "task_template_id": updated.task_template_id,
                "action_id": action_id,
                "status": updated.status,
            },
        )
        _append_workflow_task_audit_event(
            conversation_repository,
            auth,
            updated,
            WORKFLOW_GATE_CONFIRMED,
            {
                "actor": "user",
                "status": "executed",
                "decision": updated.status,
                "action_id": action_id,
            },
        )
        return _workflow_task_response(updated)

    @api.post(
        "/v1/bots/proposals",
        response_model=BotProposalResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_bot_proposal(
        payload: BotProposalCreateRequest,
        request: Request,
        auth: AuthContext = Depends(require_auth_context),
    ) -> BotProposalResponse | JSONResponse:
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="bots.proposal.create")
        except SecurityControlError as exc:
            return _security_error_response(exc)

        try:
            draft = build_bot_proposal_create_input(
                auth=auth,
                repository=conversation_repository,
                artifact_store=artifact_store,
                draft=BotProposalDraftInput(
                    strategy_artifact_id=payload.strategy_artifact_id,
                    run_id=payload.run_id,
                    strategy_id=payload.strategy_id,
                    strategy_name=payload.strategy_name,
                    manifest=payload.manifest,
                    data_subscriptions=payload.data_subscriptions,
                    broker_connection_id=payload.broker_connection_id,
                    account_id=payload.account_id,
                    risk_policy_id=payload.risk_policy_id,
                    readiness_checks=payload.readiness_checks,
                ),
            )
        except BotProposalSourceNotFoundError as exc:
            detail = "Strategy artifact not found" if exc.source == "strategy_artifact" else "Strategy run not found"
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail) from exc
        except BotProposalArtifactUnreadableError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Could not read strategy artifact") from exc
        proposal = conversation_repository.create_bot_proposal(
            auth,
            draft.create_input,
        )
        return _bot_proposal_response(proposal)

    @api.get("/v1/bots/proposals/{proposal_id}", response_model=BotProposalResponse)
    def get_bot_proposal(
        proposal_id: str,
        auth: AuthContext = Depends(require_auth_context),
    ) -> BotProposalResponse:
        proposal = conversation_repository.get_bot_proposal(auth, proposal_id)
        if proposal is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot proposal not found")
        return _bot_proposal_response(proposal)

    @api.post("/v1/bots/proposals/{proposal_id}/confirm-start", response_model=BotProposalConfirmStartResponse)
    def confirm_start_bot_proposal(
        proposal_id: str,
        payload: BotProposalConfirmStartRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        auth: AuthContext = Depends(require_auth_context),
    ) -> BotProposalConfirmStartResponse | JSONResponse:
        proposal = conversation_repository.get_bot_proposal(auth, proposal_id)
        if proposal is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot proposal not found")
        broker_connection_id = payload.broker_connection_id or proposal.broker_connection_id
        account_id = payload.account_id or proposal.account_id
        risk_policy_id = payload.risk_policy_id or proposal.risk_policy_id
        missing = bot_required_missing(
            broker_connection_id=broker_connection_id,
            account_id=account_id,
            risk_policy_id=risk_policy_id,
            strategy_id=proposal.strategy_id,
            data_subscriptions=proposal.data_subscriptions_json,
        )
        if missing:
            _append_bot_proposal_audit_event(
                conversation_repository,
                auth,
                proposal,
                WORKFLOW_GATE_REJECTED,
                {
                    "status": "rejected",
                    "reason_code": "missing_inputs",
                    "missing_fields": missing,
                    "risk_level": "paper_simulation_gate",
                },
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"message": "Bot proposal is missing required inputs", "missing_inputs": missing},
            )
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="bots.proposal.confirm_start")
            idempotency = controls.begin_idempotency(
                auth,
                method=request.method,
                path=request.url.path,
                key=idempotency_key,
                body={
                    "proposal_id": proposal_id,
                    "broker_connection_id": broker_connection_id,
                    "account_id": account_id,
                    "risk_policy_id": risk_policy_id,
                },
            )
        except SecurityControlError as exc:
            return _security_error_response(exc)
        if idempotency is not None and idempotency.replay:
            return JSONResponse(idempotency.response, status_code=idempotency.status_code or status.HTTP_200_OK)
        if proposal.status == "started" and proposal.runtime_id:
            existing_runtime = conversation_repository.get_nautilus_runtime(auth, proposal.runtime_id)
            if existing_runtime is not None:
                response = BotProposalConfirmStartResponse(
                    proposal=_bot_proposal_response(proposal),
                    runtime=_nautilus_runtime_response(existing_runtime),
                )
                return _complete_idempotent_response(controls, idempotency, response, status.HTTP_200_OK)
        runtime_key = RuntimeKey(
            user_id=auth.user_id,
            broker_connection_id=broker_connection_id or "",
            account_id=account_id or "",
            mode="paper",
            risk_policy_id=risk_policy_id or "",
        ).stable_id()
        runtime = conversation_repository.upsert_nautilus_runtime(
            auth,
            runtime_key=runtime_key,
            broker_connection_id=broker_connection_id or "",
            account_id=account_id or "",
            mode="paper",
            risk_policy_id=risk_policy_id or "",
            strategy_id=proposal.strategy_id,
            manifest_json={**proposal.manifest_json, "bot_proposal_id": proposal.id},
            data_subscriptions_json=proposal.data_subscriptions_json,
        )
        conversation_repository.append_nautilus_runtime_event(
            auth,
            runtime.id,
            "strategy_loaded",
            {"strategy_id": proposal.strategy_id, "bot_proposal_id": proposal.id, "state": runtime.state},
            idempotency_key=f"bot-proposal-start:{proposal.id}",
        )
        updated_proposal = conversation_repository.mark_bot_proposal_started(auth, proposal.id, runtime_id=runtime.id)
        _append_bot_proposal_audit_event(
            conversation_repository,
            auth,
            updated_proposal or proposal,
            WORKFLOW_GATE_CONFIRMED,
            {
                "status": "executed",
                "reason_code": "confirm_start",
                "risk_level": "paper_simulation",
                "runtime_id": runtime.id,
            },
        )
        response = BotProposalConfirmStartResponse(
            proposal=_bot_proposal_response(updated_proposal or proposal),
            runtime=_nautilus_runtime_response(runtime),
        )
        return _complete_idempotent_response(controls, idempotency, response, status.HTTP_200_OK)

    @api.post(
        "/v1/nautilus/runtimes",
        response_model=NautilusRuntimeResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def start_nautilus_runtime(
        payload: NautilusRuntimeStartRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        auth: AuthContext = Depends(require_auth_context),
    ) -> NautilusRuntimeResponse | JSONResponse:
        if payload.mode == "live":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Live Nautilus broker execution is disabled until an explicit future approval gate enables it.",
            )
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="nautilus.runtime.start")
            idempotency = controls.begin_idempotency(
                auth,
                method=request.method,
                path=request.url.path,
                key=idempotency_key,
                body=payload.model_dump(mode="json"),
            )
        except SecurityControlError as exc:
            return _security_error_response(exc)
        if idempotency is not None and idempotency.replay:
            return JSONResponse(idempotency.response, status_code=idempotency.status_code or status.HTTP_201_CREATED)
        runtime_key = RuntimeKey(
            user_id=auth.user_id,
            broker_connection_id=payload.broker_connection_id,
            account_id=payload.account_id,
            mode=payload.mode,
            risk_policy_id=payload.risk_policy_id,
        ).stable_id()
        runtime = conversation_repository.upsert_nautilus_runtime(
            auth,
            runtime_key=runtime_key,
            broker_connection_id=payload.broker_connection_id,
            account_id=payload.account_id,
            mode=payload.mode,
            risk_policy_id=payload.risk_policy_id,
            strategy_id=payload.strategy_id,
            manifest_json=payload.manifest,
            data_subscriptions_json=payload.data_subscriptions,
        )
        conversation_repository.append_nautilus_runtime_event(
            auth,
            runtime.id,
            "strategy_loaded",
            {"strategy_id": payload.strategy_id, "state": runtime.state},
            idempotency_key=f"start:{idempotency_key}" if idempotency_key else None,
        )
        response = _nautilus_runtime_response(runtime)
        return _complete_idempotent_response(controls, idempotency, response, status.HTTP_201_CREATED)

    @api.get("/v1/nautilus/runtimes", response_model=NautilusRuntimeListResponse)
    def list_nautilus_runtimes(
        mode: Literal["paper", "live"] | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        auth: AuthContext = Depends(require_auth_context),
    ) -> NautilusRuntimeListResponse:
        rows = conversation_repository.list_nautilus_runtimes(auth, mode=mode, limit=limit)
        return NautilusRuntimeListResponse(items=[_nautilus_runtime_response(runtime) for runtime in rows])

    @api.get("/v1/nautilus/runtimes/{runtime_id}", response_model=NautilusRuntimeResponse)
    def get_nautilus_runtime(
        runtime_id: str,
        auth: AuthContext = Depends(require_auth_context),
    ) -> NautilusRuntimeResponse:
        runtime = conversation_repository.get_nautilus_runtime(auth, runtime_id)
        if runtime is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runtime not found")
        return _nautilus_runtime_response(runtime)

    @api.post("/v1/nautilus/runtimes/{runtime_id}/heartbeat", response_model=NautilusRuntimeResponse)
    def heartbeat_nautilus_runtime(
        runtime_id: str,
        payload: NautilusRuntimeHeartbeatRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        auth: AuthContext = Depends(require_auth_context),
    ) -> NautilusRuntimeResponse | JSONResponse:
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="nautilus.runtime.heartbeat")
            idempotency = controls.begin_idempotency(
                auth,
                method=request.method,
                path=request.url.path,
                key=idempotency_key,
                body=payload.model_dump(mode="json"),
            )
        except SecurityControlError as exc:
            return _security_error_response(exc)
        if idempotency is not None and idempotency.replay:
            return JSONResponse(idempotency.response, status_code=idempotency.status_code or status.HTTP_200_OK)
        heartbeat = conversation_repository.record_nautilus_runtime_heartbeat(
            auth,
            runtime_id,
            payload={"status": payload.status, "metrics": payload.metrics},
            idempotency_key=idempotency_key,
        )
        if heartbeat is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runtime not found")
        response = _nautilus_runtime_response(heartbeat.runtime)
        return _complete_idempotent_response(controls, idempotency, response, status.HTTP_200_OK)

    @api.post("/v1/nautilus/runtimes/{runtime_id}/events", response_model=NautilusRuntimeEventIngestResponse)
    def append_nautilus_runtime_event(
        runtime_id: str,
        payload: NautilusRuntimeEventCreate,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        auth: AuthContext = Depends(require_auth_context),
    ) -> NautilusRuntimeEventIngestResponse | JSONResponse:
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="nautilus.runtime.event")
            idempotency = controls.begin_idempotency(
                auth,
                method=request.method,
                path=request.url.path,
                key=idempotency_key,
                body=payload.model_dump(mode="json"),
            )
        except SecurityControlError as exc:
            return _security_error_response(exc)
        if idempotency is not None and idempotency.replay:
            return JSONResponse(idempotency.response, status_code=idempotency.status_code or status.HTTP_200_OK)
        if payload.type == "heartbeat":
            heartbeat = conversation_repository.record_nautilus_runtime_heartbeat(
                auth,
                runtime_id,
                payload=payload.payload,
                idempotency_key=idempotency_key,
            )
            if heartbeat is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runtime not found")
            response = NautilusRuntimeEventIngestResponse(
                event_appended=heartbeat.event_appended,
                event=_nautilus_runtime_event_response(heartbeat.event) if heartbeat.event is not None else None,
                runtime=_nautilus_runtime_response(heartbeat.runtime),
            )
            return _complete_idempotent_response(controls, idempotency, response, status.HTTP_200_OK)
        event = conversation_repository.append_nautilus_runtime_event(
            auth,
            runtime_id,
            payload.type,
            payload.payload,
            idempotency_key=idempotency_key,
        )
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runtime not found")
        runtime = conversation_repository.get_nautilus_runtime(auth, runtime_id)
        if runtime is not None:
            next_state = runtime_state_after_event(
                runtime.state,
                payload.type,
                kill_switch_active=runtime.kill_switch_active,
            )
            if next_state != runtime.state:
                runtime = conversation_repository.set_nautilus_runtime_state(auth, runtime_id, state=next_state)
        response = NautilusRuntimeEventIngestResponse(
            event_appended=True,
            event=_nautilus_runtime_event_response(event),
            runtime=_nautilus_runtime_response(runtime) if runtime is not None else None,
        )
        return _complete_idempotent_response(controls, idempotency, response, status.HTTP_200_OK)

    @api.get("/v1/nautilus/runtimes/{runtime_id}/events", response_model=list[NautilusRuntimeEventResponse])
    def list_nautilus_runtime_events(
        runtime_id: str,
        limit: int = Query(default=100, ge=1, le=500),
        after_sequence: int | None = Query(default=None, ge=0),
        auth: AuthContext = Depends(require_auth_context),
    ) -> list[NautilusRuntimeEventResponse]:
        events = conversation_repository.list_nautilus_runtime_events(
            auth,
            runtime_id,
            limit=limit,
            after_sequence=after_sequence,
        )
        if events is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runtime not found")
        return [_nautilus_runtime_event_response(event) for event in events]

    @api.post("/v1/nautilus/runtimes/{runtime_id}/stop", response_model=NautilusRuntimeResponse)
    def stop_nautilus_runtime(
        runtime_id: str,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        auth: AuthContext = Depends(require_auth_context),
    ) -> NautilusRuntimeResponse | JSONResponse:
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="nautilus.runtime.stop")
            idempotency = controls.begin_idempotency(
                auth,
                method=request.method,
                path=request.url.path,
                key=idempotency_key,
                body={},
            )
        except SecurityControlError as exc:
            return _security_error_response(exc)
        if idempotency is not None and idempotency.replay:
            return JSONResponse(idempotency.response, status_code=idempotency.status_code or status.HTTP_200_OK)
        runtime = conversation_repository.set_nautilus_runtime_state(auth, runtime_id, state="stopping")
        if runtime is not None:
            runtime = conversation_repository.set_nautilus_runtime_desired_state(auth, runtime_id, desired_state="stopping")
        if runtime is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runtime not found")
        conversation_repository.append_nautilus_runtime_event(
            auth,
            runtime.id,
            "stop_requested",
            {"reason": "operator_requested"},
            idempotency_key=idempotency_key,
        )
        response = _nautilus_runtime_response(runtime)
        return _complete_idempotent_response(controls, idempotency, response, status.HTTP_200_OK)

    @api.post("/v1/nautilus/runtimes/{runtime_id}/kill-switch", response_model=NautilusRuntimeResponse)
    def kill_switch_nautilus_runtime(
        runtime_id: str,
        payload: NautilusRuntimeKillSwitchRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        auth: AuthContext = Depends(require_auth_context),
    ) -> NautilusRuntimeResponse | JSONResponse:
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="nautilus.runtime.kill_switch")
            idempotency = controls.begin_idempotency(
                auth,
                method=request.method,
                path=request.url.path,
                key=idempotency_key,
                body=payload.model_dump(mode="json"),
            )
        except SecurityControlError as exc:
            return _security_error_response(exc)
        if idempotency is not None and idempotency.replay:
            return JSONResponse(idempotency.response, status_code=idempotency.status_code or status.HTTP_200_OK)
        runtime = conversation_repository.activate_nautilus_runtime_kill_switch(auth, runtime_id)
        if runtime is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runtime not found")
        conversation_repository.append_nautilus_runtime_event(
            auth,
            runtime.id,
            "risk_block",
            {"reason": payload.reason, "action": "kill_switch"},
            idempotency_key=idempotency_key,
        )
        response = _nautilus_runtime_response(runtime)
        return _complete_idempotent_response(controls, idempotency, response, status.HTTP_200_OK)

    @api.get("/v1/knowledge/candidates", response_model=KnowledgeCandidateListResponse)
    def list_knowledge_candidates(
        auth: AuthContext = Depends(require_auth_context),
    ) -> KnowledgeCandidateListResponse:
        require_knowledge_admin(auth)
        return KnowledgeCandidateListResponse(**knowledge_learning_service.list_candidates())

    @api.post(
        "/v1/knowledge/candidates",
        response_model=KnowledgeCandidateResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_knowledge_candidate(
        payload: KnowledgeCandidateCreate,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        auth: AuthContext = Depends(require_auth_context),
    ):
        require_knowledge_admin(auth)
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="knowledge.candidate.create")
            idempotency = controls.begin_idempotency(
                auth,
                method=request.method,
                path=request.url.path,
                key=idempotency_key,
                body=payload.model_dump(mode="json"),
            )
        except SecurityControlError as exc:
            return _security_error_response(exc)
        if idempotency is not None and idempotency.replay:
            return JSONResponse(idempotency.response, status_code=idempotency.status_code or status.HTTP_201_CREATED)
        try:
            candidate = knowledge_learning_service.propose_candidate(
                lesson=payload.lesson,
                evidence_ref=payload.evidence_ref,
                candidate_type=payload.candidate_type,
                confidence=payload.confidence,
                trust_level=payload.trust_level,
                source_uri=payload.source_uri,
                metadata=payload.metadata,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        response = KnowledgeCandidateResponse(**candidate)
        return _complete_idempotent_response(controls, idempotency, response, status.HTTP_201_CREATED)

    @api.post("/v1/knowledge/candidates/{candidate_id}/approve", response_model=KnowledgeCandidateResponse)
    def approve_knowledge_candidate(
        candidate_id: str,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        auth: AuthContext = Depends(require_auth_context),
    ):
        require_knowledge_admin(auth)
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="knowledge.candidate.approve")
            idempotency = controls.begin_idempotency(
                auth,
                method=request.method,
                path=request.url.path,
                key=idempotency_key,
                body={"candidate_id": candidate_id},
            )
        except SecurityControlError as exc:
            return _security_error_response(exc)
        if idempotency is not None and idempotency.replay:
            return JSONResponse(idempotency.response, status_code=idempotency.status_code or status.HTTP_200_OK)
        try:
            response = KnowledgeCandidateResponse(**knowledge_learning_service.approve_candidate(auth, candidate_id))
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge candidate not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        return _complete_idempotent_response(controls, idempotency, response, status.HTTP_200_OK)

    @api.post("/v1/knowledge/candidates/{candidate_id}/reject", response_model=KnowledgeCandidateResponse)
    def reject_knowledge_candidate(
        candidate_id: str,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        auth: AuthContext = Depends(require_auth_context),
    ):
        require_knowledge_admin(auth)
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="knowledge.candidate.reject")
            idempotency = controls.begin_idempotency(
                auth,
                method=request.method,
                path=request.url.path,
                key=idempotency_key,
                body={"candidate_id": candidate_id},
            )
        except SecurityControlError as exc:
            return _security_error_response(exc)
        if idempotency is not None and idempotency.replay:
            return JSONResponse(idempotency.response, status_code=idempotency.status_code or status.HTTP_200_OK)
        try:
            response = KnowledgeCandidateResponse(**knowledge_learning_service.reject_candidate(auth, candidate_id))
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge candidate not found") from exc
        return _complete_idempotent_response(controls, idempotency, response, status.HTTP_200_OK)

    @api.post("/v1/knowledge/candidates/{candidate_id}/auto-review", response_model=KnowledgeCandidateResponse)
    def auto_review_knowledge_candidate(
        candidate_id: str,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        auth: AuthContext = Depends(require_auth_context),
    ):
        require_knowledge_admin(auth)
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="knowledge.candidate.auto_review")
            idempotency = controls.begin_idempotency(
                auth,
                method=request.method,
                path=request.url.path,
                key=idempotency_key,
                body={"candidate_id": candidate_id},
            )
        except SecurityControlError as exc:
            return _security_error_response(exc)
        if idempotency is not None and idempotency.replay:
            return JSONResponse(idempotency.response, status_code=idempotency.status_code or status.HTTP_200_OK)
        try:
            response = KnowledgeCandidateResponse(**knowledge_learning_service.auto_review_candidate(auth, candidate_id))
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge candidate not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        return _complete_idempotent_response(controls, idempotency, response, status.HTTP_200_OK)

    @api.post("/v1/knowledge/candidates/auto-review", response_model=KnowledgeCandidateListResponse)
    def auto_review_knowledge_candidates(
        payload: KnowledgeAutoReviewRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        auth: AuthContext = Depends(require_auth_context),
    ):
        require_knowledge_admin(auth)
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="knowledge.candidates.auto_review")
            idempotency = controls.begin_idempotency(
                auth,
                method=request.method,
                path=request.url.path,
                key=idempotency_key,
                body=payload.model_dump(mode="json"),
            )
        except SecurityControlError as exc:
            return _security_error_response(exc)
        if idempotency is not None and idempotency.replay:
            return JSONResponse(idempotency.response, status_code=idempotency.status_code or status.HTTP_200_OK)
        try:
            response = KnowledgeCandidateListResponse(**knowledge_learning_service.auto_review_candidates(auth, payload.candidate_ids))
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge candidate not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        return _complete_idempotent_response(controls, idempotency, response, status.HTTP_200_OK)

    @api.post("/v1/runs/{run_id}/knowledge-learning", response_model=KnowledgeLearningResponse)
    def extract_run_knowledge_candidates(
        run_id: str,
        payload: KnowledgeLearningRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        auth: AuthContext = Depends(require_auth_context),
    ):
        require_knowledge_admin(auth)
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="knowledge.run_learning")
            idempotency = controls.begin_idempotency(
                auth,
                method=request.method,
                path=request.url.path,
                key=idempotency_key,
                body={"run_id": run_id, **payload.model_dump(mode="json")},
            )
        except SecurityControlError as exc:
            return _security_error_response(exc)
        if idempotency is not None and idempotency.replay:
            return JSONResponse(idempotency.response, status_code=idempotency.status_code or status.HTTP_200_OK)
        run = conversation_repository.get_run(auth, run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
        try:
            report = knowledge_learning_service.extract_run_candidates(auth, run, approval_mode=payload.approval_mode)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        response = KnowledgeLearningResponse(**report)
        return _complete_idempotent_response(controls, idempotency, response, status.HTTP_200_OK)

    @api.get("/v1/account/usage", response_model=AccountUsageResponse)
    def get_account_usage(
        auth: AuthContext = Depends(require_auth_context),
    ) -> AccountUsageResponse:
        capability = _workspace_capability(auth)
        usage = conversation_repository.summarize_account_usage(auth)
        period_start, period_end = _current_usage_period()
        return AccountUsageResponse(
            tier=capability.tier,
            tier_label=capability.tier_label,
            period_start=period_start,
            period_end=period_end,
            messages=usage.messages,
            runs=usage.runs,
            artifacts=usage.artifacts,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            estimated_cost_usd=usage.estimated_cost_usd,
        )

    @api.post(
        "/v1/conversations",
        response_model=ConversationResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_conversation(
        payload: ConversationCreate,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        auth: AuthContext = Depends(require_auth_context),
    ):
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="conversation.create")
            idempotency = controls.begin_idempotency(
                auth,
                method=request.method,
                path=request.url.path,
                key=idempotency_key,
                body=payload.model_dump(mode="json"),
            )
        except SecurityControlError as exc:
            return _security_error_response(exc)
        if idempotency is not None and idempotency.replay:
            return JSONResponse(idempotency.response, status_code=idempotency.status_code or status.HTTP_201_CREATED)
        conversation = conversation_repository.create_conversation(auth=auth, title=payload.title)
        response = ConversationResponse.model_validate(conversation)
        return _complete_idempotent_response(controls, idempotency, response, status.HTTP_201_CREATED)

    @api.get("/v1/conversations", response_model=ConversationListResponse)
    def list_conversations(
        auth: AuthContext = Depends(require_auth_context),
    ) -> ConversationListResponse:
        return ConversationListResponse(items=conversation_repository.list_conversations(auth))

    @api.get("/v1/conversations/sidebar", response_model=ConversationSidebarResponse)
    def list_conversation_sidebar(
        auth: AuthContext = Depends(require_auth_context),
    ) -> ConversationSidebarResponse:
        items = [_conversation_sidebar_item(record) for record in conversation_repository.list_conversation_sidebar(auth)]
        return ConversationSidebarResponse(items=items)

    @api.get("/v1/conversations/{conversation_id}", response_model=ConversationResponse)
    def get_conversation(
        conversation_id: str,
        auth: AuthContext = Depends(require_auth_context),
    ):
        conversation = conversation_repository.get_conversation(auth, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
        return conversation

    @api.patch("/v1/conversations/{conversation_id}", response_model=ConversationResponse)
    def update_conversation(
        conversation_id: str,
        payload: ConversationUpdate,
        request: Request,
        auth: AuthContext = Depends(require_auth_context),
    ):
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="conversation.update")
        except SecurityControlError as exc:
            return _security_error_response(exc)
        conversation = conversation_repository.update_conversation_title(auth, conversation_id, payload.title)
        if conversation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
        return conversation

    @api.delete("/v1/conversations/{conversation_id}", response_model=ConversationResponse)
    def delete_conversation(
        conversation_id: str,
        request: Request,
        auth: AuthContext = Depends(require_auth_context),
    ):
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="conversation.delete")
        except SecurityControlError as exc:
            return _security_error_response(exc)
        conversation = conversation_repository.delete_conversation(auth, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
        return conversation

    @api.get("/v1/conversations/{conversation_id}/state", response_model=ConversationStateResponse)
    def get_conversation_state(
        conversation_id: str,
        auth: AuthContext = Depends(require_auth_context),
    ) -> ConversationStateResponse:
        snapshot = conversation_repository.get_conversation_state_snapshot(auth, conversation_id)
        if snapshot is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
        state_artifacts_by_id = {
            artifact.id: artifact
            for artifact in [*snapshot.latest_run_artifacts, *snapshot.conversation_artifacts]
        }
        state_preview_summaries = _artifact_preview_summary_batch(
            list(state_artifacts_by_id.values()),
            auth=auth,
            repository=conversation_repository,
        )
        return ConversationStateResponse(
            conversation=ConversationResponse.model_validate(snapshot.conversation),
            messages=[MessageResponse.model_validate(message) for message in snapshot.messages],
            message_count=snapshot.message_count,
            messages_truncated=snapshot.messages_truncated,
            message_limit=snapshot.message_limit,
            latest_run=RunResponse.model_validate(snapshot.latest_run) if snapshot.latest_run is not None else None,
            latest_run_artifacts=_artifact_responses(
                snapshot.latest_run_artifacts,
                auth=auth,
                repository=conversation_repository,
                preview_summaries=state_preview_summaries,
            ),
            conversation_artifacts=_artifact_responses(
                snapshot.conversation_artifacts,
                auth=auth,
                repository=conversation_repository,
                preview_summaries=state_preview_summaries,
            ),
            conversation_artifacts_next_cursor=snapshot.conversation_artifacts_next_cursor,
            latest_run_events=[_run_event_response(event) for event in snapshot.latest_run_events],
            conversation_run_events=[
                _run_event_response(event) for event in snapshot.conversation_run_events
            ],
            feedback_targets=_feedback_targets(
                snapshot.conversation.id,
                snapshot.messages,
                snapshot.latest_run,
                snapshot.latest_run_artifacts,
            ),
            strategy_profile=_strategy_profile(snapshot),
            pending_workflow_continuation=_pending_workflow_continuation(
                conversation_repository,
                auth,
                snapshot.conversation.id,
            ),
        )

    @api.get("/v1/conversations/{conversation_id}/artifacts", response_model=ArtifactListResponse)
    def list_conversation_artifacts(
        conversation_id: str,
        limit: int = Query(
            default=CONVERSATION_ARTIFACT_STATE_LIMIT,
            ge=1,
            le=CONVERSATION_ARTIFACT_PAGE_MAX_LIMIT,
        ),
        cursor: str | None = Query(default=None),
        visibility: Literal["user", "all"] = Query(default="user"),
        auth: AuthContext = Depends(require_auth_context),
    ) -> ArtifactListResponse:
        try:
            page = conversation_repository.list_conversation_artifacts_page(
                auth,
                conversation_id,
                limit=limit,
                cursor=cursor,
                visibility=visibility,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid artifact cursor") from exc
        if page is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
        return ArtifactListResponse(
            items=_artifact_responses(page.items, auth=auth, repository=conversation_repository),
            next_cursor=page.next_cursor,
        )

    @api.get("/v1/artifacts", response_model=ArtifactListResponse)
    def list_workspace_artifacts(
        limit: int = Query(
            default=CONVERSATION_ARTIFACT_STATE_LIMIT,
            ge=1,
            le=CONVERSATION_ARTIFACT_PAGE_MAX_LIMIT,
        ),
        cursor: str | None = Query(default=None),
        visibility: Literal["user", "all"] = Query(default="user"),
        auth: AuthContext = Depends(require_auth_context),
    ) -> ArtifactListResponse:
        try:
            page = conversation_repository.list_workspace_artifacts_page(
                auth,
                limit=limit,
                cursor=cursor,
                visibility=visibility,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid artifact cursor") from exc
        return ArtifactListResponse(
            items=_artifact_responses(page.items, auth=auth, repository=conversation_repository),
            next_cursor=page.next_cursor,
        )

    @api.post(
        "/v1/conversations/{conversation_id}/backtest-approvals/{approval_id}",
        response_model=BacktestApprovalDecisionResponse,
    )
    def decide_backtest_approval(
        conversation_id: str,
        approval_id: str,
        payload: BacktestApprovalDecisionRequest,
        request: Request,
        auth: AuthContext = Depends(require_auth_context),
    ) -> BacktestApprovalDecisionResponse:
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="backtest.approval")
        except SecurityControlError as exc:
            return _security_error_response(exc)
        try:
            result = decide_backtest_preview_approval(
                conversation_repository,
                artifact_store,
                auth,
                conversation_id=conversation_id,
                approval_id=approval_id,
                decision=payload.decision,
            )
        except ValueError as exc:
            message = str(exc)
            status_code = status.HTTP_404_NOT_FOUND if "not found" in message.lower() else status.HTTP_409_CONFLICT
            raise HTTPException(status_code=status_code, detail=message) from exc
        return BacktestApprovalDecisionResponse.model_validate(result)

    @api.get("/v1/conversations/{conversation_id}/messages", response_model=MessageListResponse)
    def list_conversation_messages(
        conversation_id: str,
        auth: AuthContext = Depends(require_auth_context),
    ) -> MessageListResponse:
        conversation = conversation_repository.get_conversation(auth, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
        return MessageListResponse(items=conversation_repository.list_messages(auth, conversation.id))

    @api.post("/v1/runs", response_model=RunCreateResponse, status_code=status.HTTP_201_CREATED)
    def create_runner_run(
        payload: RunCreate,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
        x_trace_id: str | None = Header(default=None, alias="X-Trace-Id"),
        auth: AuthContext = Depends(require_auth_context),
    ):
        request_id = x_request_id or opaque_id("req")
        trace_id = x_trace_id or opaque_id("trace")
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="run.create")
            idempotency = controls.begin_idempotency(
                auth,
                method=request.method,
                path=request.url.path,
                key=idempotency_key,
                body=_run_idempotency_body(payload),
            )
        except SecurityControlError as exc:
            return _security_error_response(exc)
        if idempotency is not None and idempotency.replay:
            return JSONResponse(idempotency.response, status_code=idempotency.status_code or status.HTTP_201_CREATED)
        conversation = conversation_repository.get_conversation(auth, payload.conversation_id)
        if conversation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
        try:
            validate_schema_payload(payload.strategy_spec, "strategy-spec.schema.json")
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        policy_decision = evaluate_policy(
            PolicySubject(
                surface="run.strategy_spec",
                payload=payload.strategy_spec,
                evidence_level=EVIDENCE_STRATEGY_IDEA,
            )
        )
        if not policy_decision.allowed and policy_decision.blocked_finding is not None:
            blocked = _create_blocked_run_response(
                conversation_repository,
                auth,
                conversation.id,
                payload.strategy_spec,
                policy_decision.blocked_finding,
                request_id=request_id,
                trace_id=trace_id,
            )
            if blocked is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
            return _complete_idempotent_response(controls, idempotency, blocked, status.HTTP_201_CREATED)

        _require_run_mode(auth, payload.mode)
        if payload.mode == RUN_MODE_BACKTEST_PREVIEW:
            try:
                controls.check_run_start()
            except SecurityControlError as exc:
                if not isinstance(exc, BudgetExceeded):
                    return _security_error_response(exc)
                blocked = _create_blocked_run_response(
                    conversation_repository,
                    auth,
                    conversation.id,
                    payload.strategy_spec,
                    budget_policy_finding(exc),
                    request_id=request_id,
                    trace_id=trace_id,
                )
                if blocked is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
                return _complete_idempotent_response(controls, idempotency, blocked, status.HTTP_201_CREATED)
            run = conversation_repository.create_run(
                auth,
                conversation.id,
                status="queued",
                mode=payload.mode,
                request_id=request_id,
                trace_id=trace_id,
            )
            if run is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
            conversation_repository.create_strategy_spec(auth, run.id, payload.strategy_spec, "backtest-preview.v1")
            backtest_config = payload.backtest_config.model_dump(mode="json") if payload.backtest_config else {}
            pine_code = _required_pineforge_pine(payload.pine_code, payload.strategy_spec)
            validation = validate_pineforge_pine(pine_code, payload.strategy_spec)
            if validation["status"] == "fail":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": "pineforge_validation_failed", "validation": validation},
                )
            job = conversation_repository.create_run_job(
                auth,
                run.id,
                job_type=RUN_MODE_BACKTEST_PREVIEW,
                payload_json={
                    "strategy_spec": payload.strategy_spec,
                    "pine_code": pine_code,
                    "backtest_config": backtest_config,
                    "runtime": backtest_runtime_boundary(BACKTEST_ENGINE_PINEFORGE),
                    "limits": backtest_job_limits_for_tier(auth.user_tier),
                },
            )
            if job is None:
                failed = conversation_repository.set_run_status(auth, run.id, "failed") or run
                conversation_repository.append_run_event(
                    auth,
                    run.id,
                    "backtest.failed",
                    {"error_code": "job_create_failed", "mode": payload.mode},
                )
                result = RunnerIntegrationResult(run=failed, artifacts=[])
            else:
                conversation_repository.append_run_event(
                    auth,
                    run.id,
                    "backtest.queued",
                    {
                        "job_id": job.id,
                        "job_type": job.job_type,
                        "mode": payload.mode,
                        "engine": backtest_config.get("engine"),
                        "symbol": backtest_config.get("symbol"),
                        "timeframe": backtest_config.get("timeframe"),
                        "data_source": backtest_config.get("data_source"),
                        "execution_semantics": "model_generated_pine_pineforge",
                    },
                )
                result = RunnerIntegrationResult(run=run, artifacts=[])
        elif payload.mode == RUN_MODE_AGENT:
            try:
                llm_orchestrator.ensure_configured()
                result = run_worker.run(
                    lambda: llm_orchestrator.execute_agent_run(
                        auth=auth,
                        conversation_id=conversation.id,
                        strategy_spec=payload.strategy_spec,
                        request_id=request_id,
                        trace_id=trace_id,
                    )
                )
            except (ProviderConfigurationError, SecurityControlError) as exc:
                return _security_error_response(exc)
        else:
            try:
                controls.check_run_start()
            except SecurityControlError as exc:
                if not isinstance(exc, BudgetExceeded):
                    return _security_error_response(exc)
                blocked = _create_blocked_run_response(
                    conversation_repository,
                    auth,
                    conversation.id,
                    payload.strategy_spec,
                    budget_policy_finding(exc),
                    request_id=request_id,
                    trace_id=trace_id,
                )
                if blocked is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
                return _complete_idempotent_response(controls, idempotency, blocked, status.HTTP_201_CREATED)
            run = conversation_repository.create_run(
                auth,
                conversation.id,
                status="running",
                mode=payload.mode,
                request_id=request_id,
                trace_id=trace_id,
            )
            if run is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
            execute_runner = execute_live_generation if payload.mode == RUN_MODE_LIVE_GENERATION else execute_dry_run
            result = run_worker.run(
                lambda: execute_runner(
                    repository=conversation_repository,
                    artifact_store=artifact_store,
                    auth=auth,
                    conversation_id=conversation.id,
                    strategy_spec=payload.strategy_spec,
                    existing_run=run,
                    **({"web_search": payload.web_search} if payload.mode == RUN_MODE_LIVE_GENERATION else {}),
                )
            )
        if result is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
        response = RunCreateResponse(
            id=result.run.id,
            conversation_id=result.run.conversation_id,
            owner_user_id=result.run.owner_user_id,
            workspace_id=result.run.workspace_id,
            status=result.run.status,
            mode=result.run.mode,
            created_at=result.run.created_at,
            updated_at=result.run.updated_at,
            retry_of_run_id=result.run.retry_of_run_id,
            request_id=result.run.request_id,
            trace_id=result.run.trace_id,
            artifacts=_artifact_responses(result.artifacts, auth=auth, repository=conversation_repository),
        )
        return _complete_idempotent_response(controls, idempotency, response, status.HTTP_201_CREATED)

    @api.post(
        "/v1/conversations/{conversation_id}/messages",
        response_model=MessageResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_message(
        conversation_id: str,
        payload: MessageCreate,
        request: Request,
        stream: bool = Query(default=False),
        mode: str = Query(default="deterministic", pattern="^(deterministic|agent)$"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
        x_trace_id: str | None = Header(default=None, alias="X-Trace-Id"),
        auth: AuthContext = Depends(require_auth_context),
    ):
        request_id = x_request_id or opaque_id("req")
        trace_id = x_trace_id or opaque_id("trace")
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="message.create")
            idempotency = controls.begin_idempotency(
                auth,
                method=request.method,
                path=request.url.path,
                key=idempotency_key,
                body={"conversation_id": conversation_id, **payload.model_dump(mode="json"), "stream": stream, "mode": mode},
            )
        except SecurityControlError as exc:
            return _security_error_response(exc)
        if idempotency is not None and idempotency.replay:
            return JSONResponse(idempotency.response, status_code=idempotency.status_code or status.HTTP_201_CREATED)
        _require_message_mode(auth, mode)
        conversation = conversation_repository.get_conversation(auth, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
        existing_messages = conversation_repository.list_messages(auth, conversation_id)
        if stream and mode == "agent":
            try:
                llm_orchestrator.ensure_configured()
            except ProviderConfigurationError as exc:
                return _security_error_response(exc)
        message = conversation_repository.create_message(
            auth=auth,
            conversation_id=conversation_id,
            content=payload.content,
        )
        if message is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
        agent_log(
            logger,
            "info",
            "message.persisted",
            component="api",
            conversation_id=conversation_id,
            message_id=message.id,
            request_id=request_id,
            role=message.role,
            text_len=len(message.content),
            trace_id=trace_id,
        )
        _maybe_generate_conversation_title(
            repository=conversation_repository,
            llm_orchestrator=llm_orchestrator,
            auth=auth,
            conversation_id=conversation_id,
            current_title=conversation.title,
            previous_messages=existing_messages,
            user_message=message.content,
        )
        if stream:
            if mode == "agent":
                agent_log(
                    logger,
                    "info",
                    "backend.stream.opened",
                    component="api",
                    conversation_id=conversation_id,
                    language=payload.language,
                    message_id=message.id,
                    request_id=request_id,
                    trace_id=trace_id,
                    user_tier=auth.user_tier,
                    web_search=payload.web_search,
                )
                return StreamingResponse(
                    _idempotent_sse_stream(
                        llm_orchestrator.stream_chat(
                            auth=auth,
                            conversation_id=conversation_id,
                            language=payload.language,
                            message_content=message.content,
                            current_message_id=message.id,
                            request_id=request_id,
                            trace_id=trace_id,
                            web_search=payload.web_search,
                        ),
                        controls,
                        idempotency,
                    ),
                    media_type="text/event-stream",
                    status_code=status.HTTP_200_OK,
                )
            run = conversation_repository.create_run(
                auth,
                conversation_id,
                status="running",
                request_id=request_id,
                trace_id=trace_id,
            )
            if run is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
            return StreamingResponse(
                _idempotent_sse_stream(
                    _deterministic_run_stream(conversation_repository, auth, run, language=payload.language),
                    controls,
                    idempotency,
                ),
                media_type="text/event-stream",
                status_code=status.HTTP_200_OK,
            )
        response = MessageResponse.model_validate(message)
        return _complete_idempotent_response(controls, idempotency, response, status.HTTP_201_CREATED)

    @api.get("/v1/runs/{run_id}/events")
    def stream_run_events(
        run_id: str,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        auth: AuthContext = Depends(require_auth_context),
    ) -> StreamingResponse:
        replay_events = conversation_repository.list_run_events_after(auth, run_id, last_event_id)
        if replay_events is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
        return StreamingResponse(
            (sse_frame(event) for event in replay_events),
            media_type="text/event-stream",
        )

    @api.get("/v1/runs/{run_id}/progress")
    def stream_run_progress(
        run_id: str,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        auth: AuthContext = Depends(require_auth_context),
    ) -> StreamingResponse:
        snapshot = conversation_repository.get_run_progress_snapshot(auth, run_id)
        if snapshot is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

        def progress_frames() -> Iterator[str]:
            yield sse_frame(
                _progress_snapshot_event(
                    snapshot.run,
                    snapshot.event_summary.event_count,
                    snapshot.event_summary.latest_event,
                    snapshot.artifacts,
                )
            )
            replay_events = conversation_repository.list_run_events_after(auth, run_id, last_event_id) or []
            for event in replay_events:
                yield sse_frame(_progress_event(event))

        return StreamingResponse(
            progress_frames(),
            media_type="text/event-stream",
        )

    @api.get("/v1/runs/{run_id}/observability")
    def get_run_observability(
        run_id: str,
        auth: AuthContext = Depends(require_auth_context),
    ) -> dict:
        summary = build_observability_summary(conversation_repository, auth, run_id)
        if summary is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
        artifact = ensure_harness_evidence_artifact(conversation_repository, artifact_store, auth, run_id, summary)
        if artifact is not None:
            summary["harness_evidence_artifact_id"] = artifact.id
        return summary

    @api.get("/v1/feedback/options", response_model=FeedbackOptionsResponse)
    def get_feedback_options(
        auth: AuthContext = Depends(require_auth_context),
    ) -> FeedbackOptionsResponse:
        return FeedbackOptionsResponse(
            ratings=[FeedbackOption(value=value, label=label) for value, label in FEEDBACK_RATING_OPTIONS],
            categories=[FeedbackOption(value=value, label=label) for value, label in FEEDBACK_CATEGORY_OPTIONS],
        )

    @api.post("/v1/feedback", response_model=FeedbackResponse, status_code=status.HTTP_201_CREATED)
    def create_feedback(
        payload: FeedbackCreate,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        auth: AuthContext = Depends(require_auth_context),
    ):
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="feedback.create")
            idempotency = controls.begin_idempotency(
                auth,
                method=request.method,
                path=request.url.path,
                key=idempotency_key,
                body=payload.model_dump(mode="json"),
            )
        except SecurityControlError as exc:
            return _security_error_response(exc)
        if idempotency is not None and idempotency.replay:
            return JSONResponse(idempotency.response, status_code=idempotency.status_code or status.HTTP_201_CREATED)
        feedback = conversation_repository.create_feedback(
            auth,
            conversation_id=payload.conversation_id,
            run_id=payload.run_id,
            message_id=payload.message_id,
            artifact_id=payload.artifact_id,
            rating=payload.rating,
            category=payload.category,
            correction=payload.correction,
        )
        if feedback is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feedback target not found")
        response = FeedbackResponse.model_validate(feedback)
        return _complete_idempotent_response(controls, idempotency, response, status.HTTP_201_CREATED)

    @api.post("/v1/runs/{run_id}/cancel", response_model=RunResponse)
    def cancel_run(
        run_id: str,
        auth: AuthContext = Depends(require_auth_context),
    ):
        run = conversation_repository.get_run(auth, run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
        if run.status in TERMINAL_RUN_STATUSES:
            return run
        updated = conversation_repository.set_run_status(auth, run_id, "cancelled")
        if updated is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
        conversation_repository.cancel_run_jobs(
            auth,
            run_id,
            result_json={"reason": "api_cancelled"},
            error_code="api_cancelled",
        )
        conversation_repository.append_run_event(
            auth,
            run_id,
            "run.cancelled",
            {"status": "cancelled", "reason": "api_cancelled"},
        )
        return updated

    @api.post("/v1/runs/{run_id}/retry", response_model=RunResponse, status_code=status.HTTP_201_CREATED)
    def retry_run(
        run_id: str,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        auth: AuthContext = Depends(require_auth_context),
    ):
        try:
            controls.check_write(auth, ip_address=_client_ip(request), surface="run.retry")
            idempotency = controls.begin_idempotency(
                auth,
                method=request.method,
                path=request.url.path,
                key=idempotency_key,
                body={"run_id": run_id},
            )
        except SecurityControlError as exc:
            return _security_error_response(exc)
        if idempotency is not None and idempotency.replay:
            return JSONResponse(idempotency.response, status_code=idempotency.status_code or status.HTTP_201_CREATED)
        run = conversation_repository.get_run(auth, run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
        if run.mode == RUN_MODE_BACKTEST_PREVIEW:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Retry is not yet supported for backtest-preview runs",
            )
        retry = conversation_repository.create_run(
            auth,
            run.conversation_id,
            status="queued",
            retry_of_run_id=run.id,
        )
        if retry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
        response = RunResponse.model_validate(retry)
        return _complete_idempotent_response(controls, idempotency, response, status.HTTP_201_CREATED)

    @api.get("/v1/artifacts/{artifact_id}/preview", response_model=ArtifactPreviewResponse)
    def get_artifact_preview(
        artifact_id: str,
        max_bytes: int = Query(default=ARTIFACT_PREVIEW_DEFAULT_BYTES, ge=1, le=ARTIFACT_PREVIEW_MAX_BYTES),
        auth: AuthContext = Depends(require_auth_context),
    ) -> ArtifactPreviewResponse:
        artifact, content, truncated_by_read = _authorized_artifact_preview(
            conversation_repository,
            artifact_store,
            auth,
            artifact_id,
            max_bytes,
        )
        preview, truncated, line_count, language = _artifact_preview(artifact, content, max_bytes)
        truncated = truncated or truncated_by_read
        base = _artifact_response(artifact, auth=auth, repository=conversation_repository).model_dump()
        return ArtifactPreviewResponse(
            **base,
            preview=preview,
            raw_available=True,
            truncated=truncated,
            line_count=line_count,
            language=language,
        )

    @api.get("/v1/artifacts/{artifact_id}", response_model=ArtifactContentResponse)
    def get_artifact(
        artifact_id: str,
        auth: AuthContext = Depends(require_auth_context),
    ) -> ArtifactContentResponse:
        artifact, content = _authorized_artifact_content(conversation_repository, artifact_store, auth, artifact_id)
        return ArtifactContentResponse(
            **_artifact_response(artifact, auth=auth, repository=conversation_repository).model_dump(),
            content=redact_value(content),
        )

    return api


def _create_blocked_run_response(
    repository: ConversationRepository,
    auth: AuthContext,
    conversation_id: str,
    strategy_spec: dict,
    finding: PolicyFinding,
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
) -> RunCreateResponse | None:
    run = repository.create_run(auth, conversation_id, status="blocked", request_id=request_id, trace_id=trace_id)
    if run is None:
        return None
    append_stage_event(repository, auth, run, "policy", 0, status="blocked")
    repository.create_strategy_spec(auth, run.id, strategy_spec, "strategy-spec.schema.json")
    _persist_policy_finding(repository, auth, run.id, finding)
    repository.append_run_event(run_id=run.id, auth=auth, event_type="run.completed", payload={"status": "blocked"})
    return RunCreateResponse(
        id=run.id,
        conversation_id=run.conversation_id,
        owner_user_id=run.owner_user_id,
        workspace_id=run.workspace_id,
        status="blocked",
        created_at=run.created_at,
        updated_at=run.updated_at,
        retry_of_run_id=run.retry_of_run_id,
        request_id=run.request_id,
        trace_id=run.trace_id,
        artifacts=[],
    )


def _persist_policy_finding(
    repository: ConversationRepository,
    auth: AuthContext,
    run_id: str | None,
    finding: PolicyFinding,
) -> None:
    if run_id is None:
        return
    message = str(redact_value(finding.message))
    existing_findings = repository.list_policy_findings(auth, run_id)
    if existing_findings is not None and any(
        record.severity == finding.severity and record.code == finding.code and record.message == message
        for record in existing_findings
    ):
        return
    repository.create_policy_finding(
        auth,
        run_id,
        severity=finding.severity,
        code=finding.code,
        message=message,
    )
    repository.append_run_events(
        auth,
        run_id,
        [
            ("policy.blocked", redact_value(policy_finding_payload(finding))),
            ("message.delta", redact_value({"text": SAFE_BLOCKED_MESSAGE, "compact": True})),
        ],
    )


def _security_error_response(exc: Exception):
    if isinstance(exc, SecurityControlError):
        return JSONResponse(security_error_payload(exc), status_code=exc.status_code)
    log_provider_exception(exc)
    return JSONResponse(provider_error_payload(), status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


def _complete_idempotent_response(
    controls: SecurityControls,
    idempotency: IdempotencyRecord | None,
    response_model,
    status_code: int,
):
    payload = response_model.model_dump(mode="json") if hasattr(response_model, "model_dump") else response_model
    payload = redact_value(payload)
    controls.complete_idempotency(idempotency, status_code=status_code, response=payload)
    if idempotency is not None:
        return JSONResponse(payload, status_code=status_code)
    return response_model


def _idempotent_sse_stream(chunks: Iterator[str], controls: SecurityControls, idempotency: IdempotencyRecord | None):
    if idempotency is None:
        for chunk in chunks:
            yield chunk
        return
    run_id: str | None = None
    terminal_status: str | None = None
    for chunk in chunks:
        if run_id is None:
            run_id = _run_id_from_sse_frame(chunk)
        terminal_status = _terminal_status_from_sse_frame(chunk) or terminal_status
        yield chunk
    controls.complete_idempotency(
        idempotency,
        status_code=status.HTTP_200_OK,
        response={
            "status": terminal_status or "completed",
            "run_id": run_id,
            "events_url": f"/v1/runs/{run_id}/events" if run_id else None,
        },
    )


def _run_id_from_sse_frames(frames: list[str]) -> str | None:
    for frame in frames:
        run_id = _run_id_from_sse_frame(frame)
        if run_id is not None:
            return run_id
    return None


def _run_id_from_sse_frame(frame: str) -> str | None:
    for line in frame.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            payload = json.loads(line.removeprefix("data: "))
        except json.JSONDecodeError:
            continue
        run_id = payload.get("run_id")
        if isinstance(run_id, str):
            return run_id
    return None


def _terminal_status_from_sse_frame(frame: str) -> str | None:
    event_type: str | None = None
    payload: dict[str, Any] | None = None
    for line in frame.splitlines():
        if line.startswith("event: "):
            event_type = line.removeprefix("event: ")
        elif line.startswith("data: "):
            try:
                data = json.loads(line.removeprefix("data: "))
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                raw_payload = data.get("payload")
                payload = raw_payload if isinstance(raw_payload, dict) else None
    if event_type == "run.failed":
        return "failed"
    if event_type == "run.cancelled":
        return "cancelled"
    if event_type == "run.completed":
        status_value = payload.get("status") if payload else None
        return status_value if isinstance(status_value, str) else "completed"
    return None


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _conversation_sidebar_item(
    record: ConversationSidebarRecord,
) -> ConversationSidebarItem:
    return ConversationSidebarItem(
        conversation=ConversationResponse.model_validate(record.conversation),
        last_message_preview=_preview_text(record.last_message_content) if record.last_message_content is not None else None,
        last_message_at=record.last_message_at,
        message_count=record.message_count,
        latest_run_id=record.latest_run_id,
        latest_run_status=record.latest_run_status,
        updated_at=record.updated_at,
    )


def _feedback_targets(
    conversation_id: str,
    messages: list,
    latest_run: AssistantRunRecord | None,
    artifacts: list[ArtifactRecord],
) -> dict[str, Any]:
    return {
        "conversation_id": conversation_id,
        "message_ids": [message.id for message in messages],
        "latest_run_id": latest_run.id if latest_run is not None else None,
        "artifact_ids": [artifact.id for artifact in artifacts],
        "ratings": [value for value, _label in FEEDBACK_RATING_OPTIONS],
        "categories": [value for value, _label in FEEDBACK_CATEGORY_OPTIONS],
    }


def _strategy_profile(snapshot: Any) -> StrategyProfileResponse | None:
    latest_user_message = next(
        (message for message in reversed(snapshot.messages) if message.role == "user" and message.content.strip()),
        None,
    )
    strategy_spec = _extract_strategy_spec(snapshot)
    if strategy_spec is None and latest_user_message is None and not snapshot.latest_run_artifacts:
        return None

    brief = _strategy_brief(strategy_spec, latest_user_message.content if latest_user_message else "")
    missing_fields = _strategy_missing_fields(brief)
    next_actions = _strategy_next_actions(missing_fields, snapshot.latest_run_artifacts)
    open_questions = [_missing_field_question(field) for field in missing_fields]
    code_artifact = _first_user_code_artifact(snapshot.latest_run_artifacts)
    summary = _strategy_memory_summary(brief, latest_user_message.content if latest_user_message else "")

    return StrategyProfileResponse(
        source="strategy_spec" if strategy_spec is not None else "conversation",
        updated_at=latest_user_message.created_at if latest_user_message is not None else snapshot.conversation.updated_at,
        brief=brief,
        snapshot=StrategySnapshotResponse(
            completeness=_strategy_completeness(missing_fields),
            missing_fields=missing_fields,
            next_actions=next_actions,
            boundary_flags=[
                "review_only",
                "no_broker_execution",
                "manual_validation_required",
            ],
        ),
        assumptions=StrategyAssumptionsResponse(
            confirmed=_string_list(strategy_spec.get("assumptions") if strategy_spec else None),
            open_questions=open_questions,
            constraints=_string_list(strategy_spec.get("constraints") if strategy_spec else None),
        ),
        memory=StrategyMemoryResponse(
            has_context=summary is not None,
            summary=summary,
            last_artifact_id=code_artifact.id if code_artifact is not None else None,
            open_questions=open_questions,
        ),
        code_outline=_code_outline(code_artifact),
    )


def _extract_strategy_spec(snapshot: Any) -> dict[str, Any] | None:
    latest_strategy_spec = getattr(snapshot, "latest_strategy_spec", None)
    if latest_strategy_spec is not None:
        return latest_strategy_spec.payload_json
    for artifact in reversed(snapshot.latest_run_artifacts):
        found = _strategy_spec_from_unknown(artifact.metadata_json)
        if found is not None:
            return found
    for event in reversed(snapshot.latest_run_events):
        found = _strategy_spec_from_unknown(event.payload)
        if found is not None:
            return found
    return None


def _strategy_spec_from_unknown(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    for key in ("strategy_spec", "spec"):
        nested = value.get(key)
        if isinstance(nested, dict) and _is_valid_strategy_spec(nested):
            return nested
    for key in ("strategy_spec", "spec", "input", "request", "metadata"):
        nested = _strategy_spec_from_unknown(value.get(key))
        if nested is not None:
            return nested
    return None


def _is_valid_strategy_spec(value: dict[str, Any]) -> bool:
    try:
        validate_schema_payload(value, "strategy-spec.schema.json")
    except Exception:
        return False
    return True


def _strategy_brief(strategy_spec: dict[str, Any] | None, fallback_text: str) -> StrategyBriefResponse:
    if strategy_spec is None:
        return StrategyBriefResponse(
            strategy_type=_classify_strategy_type(fallback_text),
        )
    rules_text = " ".join(
        _string_list(strategy_spec.get("entry_rules"))
        + _string_list(strategy_spec.get("exit_rules"))
        + _string_list(strategy_spec.get("risk_rules"))
    )
    return StrategyBriefResponse(
        market=_optional_string(strategy_spec.get("market")),
        symbol=_optional_string(strategy_spec.get("symbol")),
        timeframe=_optional_string(strategy_spec.get("timeframe")),
        platform=_optional_string(strategy_spec.get("target_platform") or strategy_spec.get("platform")),
        strategy_type=_classify_strategy_type(rules_text or fallback_text),
        entry_rules=_string_list(strategy_spec.get("entry_rules")),
        exit_rules=_string_list(strategy_spec.get("exit_rules")),
        risk_rules=_string_list(strategy_spec.get("risk_rules")),
    )


def _strategy_missing_fields(brief: StrategyBriefResponse) -> list[str]:
    missing = []
    checks = {
        "market": brief.market,
        "timeframe": brief.timeframe,
        "platform": brief.platform,
        "entry_rules": brief.entry_rules,
        "exit_rules": brief.exit_rules,
        "risk_rules": brief.risk_rules,
    }
    for field, value in checks.items():
        if not value:
            missing.append(field)
    return missing


def _strategy_next_actions(missing_fields: list[str], artifacts: list[ArtifactRecord]) -> list[str]:
    actions = []
    if missing_fields:
        actions.append("turn_into_strategy_spec")
    if "risk_rules" in missing_fields:
        actions.append("add_risk_rules")
    if not missing_fields and _first_user_code_artifact(artifacts) is None:
        actions.append("generate_pine_artifact")
    actions.append("review_assumptions")
    return actions


def _strategy_completeness(missing_fields: list[str]) -> str:
    if not missing_fields:
        return "ready_for_artifact"
    if set(missing_fields) <= {"risk_rules"}:
        return "needs_risk"
    return "draft"


def _strategy_memory_summary(brief: StrategyBriefResponse, fallback_text: str) -> str | None:
    parts = []
    if brief.strategy_type:
        parts.append(brief.strategy_type)
    if brief.market:
        parts.append(brief.market)
    if brief.symbol:
        parts.append(brief.symbol)
    if brief.timeframe:
        parts.append(brief.timeframe)
    if parts:
        return " / ".join(parts)
    text = fallback_text.strip()
    return text[:160] if text else None


def _code_outline(artifact: ArtifactRecord | None) -> list[StrategyCodeOutlineItemResponse]:
    if artifact is None:
        return []
    sections = [
        ("inputs", "Inputs", "parameters"),
        ("signals", "Signal logic", "logic"),
        ("risk", "Risk controls", "risk"),
        ("alerts", "Review checklist", "review"),
    ]
    return [
        StrategyCodeOutlineItemResponse(
            id=f"{artifact.id}:{section_id}",
            label=label,
            kind=kind,
            artifact_id=artifact.id,
            anchor=section_id,
        )
        for section_id, label, kind in sections
    ]


def _first_user_code_artifact(artifacts: list[ArtifactRecord]) -> ArtifactRecord | None:
    for artifact in artifacts:
        visibility, category = _artifact_visibility_and_category(artifact)
        if visibility == "user" and category == "code":
            return artifact
    return None


def _missing_field_question(field: str) -> str:
    labels = {
        "market": "Which market should this strategy target?",
        "timeframe": "Which timeframe should the strategy use?",
        "platform": "Which platform should the artifact target?",
        "entry_rules": "What exact entry condition should trigger a signal?",
        "exit_rules": "What condition should close or invalidate a signal?",
        "risk_rules": "What stop-loss, take-profit, and sizing rules should be applied?",
    }
    return labels.get(field, f"Please clarify {field}.")


def _classify_strategy_type(text: str) -> str | None:
    normalized = text.lower()
    if any(word in normalized for word in ("breakout", "support", "resistance")):
        return "Breakout"
    if any(word in normalized for word in ("mean reversion", "revert", "oversold", "overbought")):
        return "Mean reversion"
    if any(word in normalized for word in ("ema", "sma", "moving average", "crossover")):
        return "Moving average crossover"
    if "rsi" in normalized:
        return "Momentum filter"
    return None


def _optional_string(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _artifact_response(
    artifact: ArtifactRecord,
    *,
    auth: AuthContext | None = None,
    repository: ConversationRepository | None = None,
    preview_summary: dict[str, Any] | None = None,
) -> ArtifactResponse:
    visibility, category = _artifact_visibility_and_category(artifact)
    presentation = _artifact_presentation(artifact, visibility=visibility, category=category)
    return ArtifactResponse(
        id=artifact.id,
        run_id=artifact.run_id,
        conversation_id=artifact.conversation_id,
        owner_user_id=artifact.owner_user_id,
        workspace_id=artifact.workspace_id,
        kind=artifact.kind,
        mime_type=artifact.mime_type,
        display_name=artifact.display_name,
        metadata_json=redact_value(artifact.metadata_json),
        visibility=visibility,
        category=category,
        presentation=presentation,
        preview_summary=_artifact_preview_summary(
            artifact,
            auth=auth,
            repository=repository,
            preview_summary=preview_summary,
        ),
        created_at=artifact.created_at,
    )


def _artifact_responses(
    artifacts: list[ArtifactRecord],
    *,
    auth: AuthContext,
    repository: ConversationRepository,
    preview_summaries: dict[str, dict[str, Any]] | None = None,
) -> list[ArtifactResponse]:
    summaries = preview_summaries or _artifact_preview_summary_batch(artifacts, auth=auth, repository=repository)
    return [
        _artifact_response(
            artifact,
            auth=auth,
            repository=repository,
            preview_summary=summaries.get(artifact.id),
        )
        for artifact in artifacts
    ]


def _artifact_preview_summary(
    artifact: ArtifactRecord,
    *,
    auth: AuthContext | None = None,
    repository: ConversationRepository | None = None,
    preview_summary: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if preview_summary is not None:
        return preview_summary
    metadata_summary = _artifact_preview_summary_from_metadata(artifact)
    if metadata_summary is not None:
        return metadata_summary
    if auth is None or repository is None:
        return None
    return _backtest_dashboard_preview_summary_from_index(artifact, auth=auth, repository=repository)


def _artifact_preview_summary_from_metadata(artifact: ArtifactRecord) -> dict[str, Any] | None:
    metadata = artifact.metadata_json
    if isinstance(metadata, dict):
        preview_summary = metadata.get("preview_summary")
        if isinstance(preview_summary, dict):
            redacted = redact_value(preview_summary)
            return redacted if isinstance(redacted, dict) else None
    return None


def _artifact_preview_summary_batch(
    artifacts: list[ArtifactRecord],
    *,
    auth: AuthContext,
    repository: ConversationRepository,
) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    backtest_artifacts: list[ArtifactRecord] = []
    for artifact in artifacts:
        metadata_summary = _artifact_preview_summary_from_metadata(artifact)
        if metadata_summary is not None:
            summaries[artifact.id] = metadata_summary
        elif artifact.kind == BACKTEST_DASHBOARD_ARTIFACT_KIND and artifact.run_id:
            backtest_artifacts.append(artifact)
    run_ids = [artifact.run_id for artifact in backtest_artifacts if artifact.run_id]
    if not run_ids:
        return summaries
    backtest_summaries = repository.get_backtest_summaries(auth, run_ids)
    equity_summaries = repository.get_backtest_equity_summaries(auth, run_ids)
    for artifact in backtest_artifacts:
        if not artifact.run_id:
            continue
        summary = backtest_summaries.get(artifact.run_id)
        if not isinstance(summary, dict):
            continue
        preview_summary = _backtest_dashboard_preview_summary_from_parts(
            artifact,
            summary=summary,
            equity_summary=equity_summaries.get(artifact.run_id),
        )
        if preview_summary is not None:
            summaries[artifact.id] = preview_summary
    return summaries


def _backtest_dashboard_preview_summary_from_index(
    artifact: ArtifactRecord,
    *,
    auth: AuthContext,
    repository: ConversationRepository,
) -> dict[str, Any] | None:
    if artifact.kind != BACKTEST_DASHBOARD_ARTIFACT_KIND or not artifact.run_id:
        return None
    summary = repository.get_backtest_summary(auth, artifact.run_id)
    if not isinstance(summary, dict):
        return None
    equity_summary = repository.get_backtest_equity_summary(auth, artifact.run_id)
    return _backtest_dashboard_preview_summary_from_parts(
        artifact,
        summary=summary,
        equity_summary=equity_summary,
    )


def _backtest_dashboard_preview_summary_from_parts(
    artifact: ArtifactRecord,
    *,
    summary: dict[str, Any],
    equity_summary: dict[str, Any] | None,
) -> dict[str, Any] | None:
    metrics = summary.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}
    equity_points = (
        equity_summary.get("points")
        if isinstance(equity_summary, dict) and isinstance(equity_summary.get("points"), list)
        else []
    )
    return {
        "kind": "backtest_result",
        "run_id": artifact.run_id,
        "symbol": _optional_string(summary.get("symbol")),
        "timeframe": _optional_string(summary.get("signal_timeframe")),
        "metrics": {
            "net_pnl": _first_number(
                _nested_number(metrics, ("pnl", "absolute")),
                _number_from_keys(metrics, ("net_pnl", "net_profit", "pnl_cost")),
            ),
            "return_pct": _first_number(
                _nested_number(metrics, ("pnl", "percentage")),
                _number_from_keys(metrics, ("return_pct", "net_profit_pct", "total_return")),
            ),
            "max_drawdown": _number_from_keys(metrics, ("max_drawdown_cost", "max_drawdown_absolute")),
            "max_drawdown_pct": _number_from_keys(metrics, ("max_drawdown_pct", "drawdown_pct", "max_drawdown")),
            "win_rate": _number_from_keys(metrics, ("win_rate",)),
            "winning_trades": _number_from_keys(metrics, ("winning_trades", "winners")),
            "losing_trades": _number_from_keys(metrics, ("losing_trades", "losers")),
            "trade_count": _number_from_keys(metrics, ("trade_count", "trades", "closed_trades")),
            "profit_factor": _number_from_keys(metrics, ("profit_factor",)),
        },
        "equity_preview": _backtest_equity_preview(equity_points, limit=80),
        "generated_at": artifact.created_at.isoformat(),
    }


def _backtest_equity_preview(points: list[Any], *, limit: int) -> list[dict[str, Any]]:
    if not points:
        return []
    step = max(1, len(points) // limit)
    sampled = points[::step][:limit]
    preview: list[dict[str, Any]] = []
    for index, point in enumerate(sampled):
        if not isinstance(point, dict):
            continue
        pnl = _number_from_keys(point, ("pnl", "pnl_cost", "equity", "value"))
        if pnl is None:
            continue
        preview.append(
            {
                "index": _first_number(_number_from_keys(point, ("index", "bar_index")), index),
                "timestamp": _optional_string(point.get("timestamp")),
                "pnl": pnl,
            }
        )
    return preview


def _first_number(*values: float | int | None) -> float | int | None:
    for value in values:
        if value is not None:
            return value
    return None


def _number_from_keys(record: dict[str, Any], keys: tuple[str, ...]) -> float | int | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            return value
    return None


def _nested_number(record: dict[str, Any], path: tuple[str, ...]) -> float | int | None:
    current: Any = record
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if isinstance(current, bool):
        return None
    return current if isinstance(current, int | float) else None


def _run_event_response(event: RunEventRecord) -> RunEventResponse:
    return RunEventResponse(
        event_id=event.id,
        conversation_id=event.conversation_id,
        run_id=event.run_id,
        request_id=event.request_id,
        trace_id=event.trace_id,
        sequence=event.sequence,
        type=event.type,
        payload=redact_value(event.payload),
        created_at=event.created_at,
    )


def _nautilus_runtime_response(runtime: NautilusRuntimeRecord) -> NautilusRuntimeResponse:
    return NautilusRuntimeResponse(
        id=runtime.id,
        runtime_key=runtime.runtime_key,
        broker_connection_id=runtime.broker_connection_id,
        account_id=runtime.account_id,
        mode=runtime.mode,
        risk_policy_id=runtime.risk_policy_id,
        state=runtime.state,
        strategy_ids=runtime.strategy_ids,
        manifest=redact_value(runtime.manifest_json),
        data_subscriptions=redact_value(runtime.data_subscriptions_json),
        last_heartbeat_at=runtime.last_heartbeat_at,
        heartbeat_count=runtime.heartbeat_count,
        heartbeat_metrics=redact_value(runtime.heartbeat_metrics_json),
        last_heartbeat_event_at=runtime.last_heartbeat_event_at,
        kill_switch_active=runtime.kill_switch_active,
        desired_state=runtime.desired_state,
        worker_id=runtime.worker_id,
        lease_until=runtime.lease_until,
        generation=runtime.generation,
        started_at=runtime.started_at,
        stopped_at=runtime.stopped_at,
        last_error=redact_value(runtime.last_error_json),
        stream_cursor=redact_value(runtime.stream_cursor_json),
        created_at=runtime.created_at,
        updated_at=runtime.updated_at,
    )


def _bot_proposal_response(proposal: BotProposalRecord) -> BotProposalResponse:
    return BotProposalResponse(
        id=proposal.id,
        status=proposal.status,
        source_conversation_id=proposal.source_conversation_id,
        source_run_id=proposal.source_run_id,
        source_artifact_ids=list(proposal.source_artifact_ids),
        strategy_id=proposal.strategy_id,
        strategy_name=proposal.strategy_name,
        manifest=redact_value(proposal.manifest_json),
        data_subscriptions=redact_value(proposal.data_subscriptions_json),
        broker_connection_id=proposal.broker_connection_id,
        account_id=proposal.account_id,
        risk_policy_id=proposal.risk_policy_id,
        readiness_checks=list(proposal.readiness_checks_json),
        missing_inputs=list(proposal.missing_inputs_json),
        runtime_id=proposal.runtime_id,
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
    )


def _workflow_task_response(task: WorkflowTaskRecord) -> WorkflowTaskResponse:
    return WorkflowTaskResponse(**workflow_task_state(task))


def _workflow_continuation_event_state(
    repository: ConversationRepository,
    auth: AuthContext,
    task_id: str,
) -> str | None:
    events = repository.list_workflow_task_continuation_events(auth, task_id)
    if events is None:
        return None
    return _workflow_continuation_events_by_task(events).get(task_id, {}).get("status")


def _workflow_continuation_events_by_task(events: list[RunEventRecord]) -> dict[str, dict[str, Any]]:
    state_by_task: dict[str, dict[str, Any]] = {}
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            continue
        if event.type == "workflow.continuation.required":
            state_by_task[task_id] = {"payload": payload, "status": "required"}
        elif event.type == "workflow.continuation.started":
            state_by_task[task_id] = {**state_by_task.get(task_id, {}), "status": "started"}
        elif event.type == "workflow.continuation.completed":
            state_by_task[task_id] = {**state_by_task.get(task_id, {}), "status": "completed"}
        elif event.type == "workflow.continuation.failed":
            state_by_task[task_id] = {**state_by_task.get(task_id, {}), "status": "failed"}
    return state_by_task


def _pending_workflow_continuation(
    repository: ConversationRepository,
    auth: AuthContext,
    conversation_id: str,
) -> dict[str, Any] | None:
    tasks = repository.list_workflow_tasks(auth, conversation_id)
    if not tasks:
        return None
    for task in reversed(tasks):
        if workflow_task_continuation_state(task) is None:
            continue
        events = repository.list_workflow_task_continuation_events(auth, task.id)
        if events is None:
            continue
        item = _workflow_continuation_events_by_task(events).get(task.id, {})
        if item.get("status") != "required":
            continue
        payload = item.get("payload")
        if isinstance(payload, dict):
            return payload
    return None


def _append_workflow_task_event(
    repository: ConversationRepository,
    auth: AuthContext,
    task: WorkflowTaskRecord,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    if not task.run_id:
        return
    repository.append_run_event(auth, task.run_id, event_type, payload)


def _append_workflow_task_audit_event(
    repository: ConversationRepository,
    auth: AuthContext,
    task: WorkflowTaskRecord,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    if not task.run_id:
        return
    run = repository.get_run(auth, task.run_id)
    if run is None:
        return
    append_model_audit_event(
        repository,
        auth,
        run,
        event_type,
        {
            "actor": "user" if payload.get("actor") == "user" else "backend",
            "source": "workflow_task",
            "workflow_id": task.workflow_id,
            "task_id": task.id,
            "task_template_id": task.task_template_id,
            "step_id": task.step_id,
            **payload,
        },
    )


def _append_bot_proposal_audit_event(
    repository: ConversationRepository,
    auth: AuthContext,
    proposal: BotProposalRecord,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    if not proposal.source_run_id:
        return
    run = repository.get_run(auth, proposal.source_run_id)
    if run is None:
        return
    append_model_audit_event(
        repository,
        auth,
        run,
        event_type,
        {
            "actor": "backend",
            "source": "bot_proposal",
            "proposal_id": proposal.id,
            **payload,
        },
    )


def _nautilus_runtime_event_response(event: NautilusRuntimeEventRecord) -> NautilusRuntimeEventResponse:
    return NautilusRuntimeEventResponse(
        event_id=event.id,
        runtime_id=event.runtime_id,
        sequence=event.sequence,
        type=event.type,
        payload=redact_value(event.payload),
        created_at=event.created_at,
    )


def _artifact_visibility_and_category(artifact: ArtifactRecord) -> tuple[str, str]:
    visibility = "internal" if artifact.kind in INTERNAL_ARTIFACT_KINDS else "user"
    if artifact.kind in USER_ARTIFACT_KINDS:
        visibility = "user"
    if artifact.kind in {"pine_file", "mql5_file"} or "mql5" in artifact.kind:
        return visibility, "code"
    if artifact.kind in REPORT_ARTIFACT_KINDS:
        return visibility, "report"
    if artifact.kind in EVIDENCE_ARTIFACT_KINDS:
        return visibility, "evidence"
    if artifact.kind in TRACE_ARTIFACT_KINDS:
        return visibility, "trace"
    return visibility, "other"


ArtifactPresentationUserKind = Literal["code", "dashboard", "report", "risk", "validation", "evidence", "raw"]
ArtifactPresentationViewerKind = Literal["code", "backtest_dashboard", "backtest_plan", "backtest_report", "trades", "json"]
ArtifactPresentationLanguageHint = Literal["markdown", "json", "pine", "mql5"]
ArtifactPresentationVisibility = Literal["user", "internal"]


class ArtifactPresentation(TypedDict):
    dedupe_key: str
    is_primary: bool
    language_hint: ArtifactPresentationLanguageHint | None
    user_kind: ArtifactPresentationUserKind
    viewer_kind: ArtifactPresentationViewerKind
    visibility: ArtifactPresentationVisibility


def _artifact_presentation(artifact: ArtifactRecord, *, visibility: str, category: str) -> ArtifactPresentation:
    kind = artifact.kind.lower()
    name = artifact.display_name.lower()
    user_kind = _artifact_user_kind(kind=kind, category=category, mime_type=artifact.mime_type)
    viewer_kind = _artifact_viewer_kind(kind=kind, category=category, user_kind=user_kind)
    return {
        "dedupe_key": _artifact_dedupe_key(artifact, user_kind=user_kind),
        "is_primary": _artifact_is_primary(kind=kind, name=name),
        "language_hint": _artifact_language_hint(kind=kind, mime_type=artifact.mime_type),
        "user_kind": user_kind,
        "viewer_kind": viewer_kind,
        "visibility": _artifact_presentation_visibility(visibility),
    }


def _artifact_user_kind(*, kind: str, category: str, mime_type: str | None) -> ArtifactPresentationUserKind:
    if kind == BACKTEST_DASHBOARD_ARTIFACT_KIND:
        return "dashboard"
    if "risk" in kind:
        return "risk"
    if kind in {"pineforge_compile_report"} or "validation" in kind or "checklist" in kind:
        return "validation"
    if category == "evidence" or "evidence" in kind or kind == "pineforge_runner_manifest":
        return "evidence"
    if category == "code" or kind in {"pine_file", "pine_strategy_source", "mql5_file"} or "mql5" in kind or mime_type == "text/plain":
        return "code"
    if category == "report" or "report" in kind:
        return "report"
    return "raw"


def _artifact_viewer_kind(
    *, kind: str, category: str, user_kind: ArtifactPresentationUserKind
) -> ArtifactPresentationViewerKind:
    if kind == BACKTEST_DASHBOARD_ARTIFACT_KIND:
        return "backtest_dashboard"
    if kind == BACKTEST_PLAN_ARTIFACT_KIND:
        return "backtest_plan"
    if kind in {
        BACKTEST_REPORT_ARTIFACT_KIND,
        ROBUSTNESS_REPORT_ARTIFACT_KIND,
        BACKTEST_VARIANT_COMPARISON_ARTIFACT_KIND,
    }:
        return "backtest_report"
    if kind == BACKTEST_TRADES_ARTIFACT_KIND:
        return "trades"
    if user_kind == "code" or category == "code":
        return "code"
    return "json"


def _artifact_is_primary(*, kind: str, name: str) -> bool:
    return kind == BACKTEST_DASHBOARD_ARTIFACT_KIND or kind in {"pine_file", "pine_strategy_source", "mql5_file"} or name.endswith(".pine")


def _artifact_language_hint(*, kind: str, mime_type: str | None) -> ArtifactPresentationLanguageHint | None:
    if mime_type == "text/markdown":
        return "markdown"
    if mime_type == "application/json" or kind.startswith("backtest_") or kind.endswith("_report"):
        return "json"
    if kind in {"pine_file", "pine_strategy_source"} or "pine" in kind:
        return "pine"
    if kind == "mql5_file" or "mql5" in kind:
        return "mql5"
    return None


def _artifact_presentation_visibility(visibility: str) -> ArtifactPresentationVisibility:
    return "internal" if visibility == "internal" else "user"


def _artifact_dedupe_key(artifact: ArtifactRecord, *, user_kind: ArtifactPresentationUserKind) -> str:
    if user_kind == "code":
        return f"code:{artifact.display_name.lower()}"
    return f"{artifact.kind.lower()}:{artifact.display_name.lower()}"


def _authorized_artifact_content(
    repository: ConversationRepository,
    artifact_store: LocalArtifactStore,
    auth: AuthContext,
    artifact_id: str,
) -> tuple[ArtifactRecord, Any]:
    artifact = repository.get_artifact(auth, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    try:
        content = artifact_store.read_content(artifact)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Artifact content missing") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Artifact storage invalid") from exc
    finding = _artifact_policy_finding(artifact, content)
    if finding is not None:
        _persist_policy_finding(repository, auth, artifact.run_id, finding)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Artifact blocked by policy")
    return artifact, content


def _authorized_artifact_preview(
    repository: ConversationRepository,
    artifact_store: LocalArtifactStore,
    auth: AuthContext,
    artifact_id: str,
    max_bytes: int,
) -> tuple[ArtifactRecord, Any, bool]:
    artifact = repository.get_artifact(auth, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    try:
        content, truncated = artifact_store.read_text_preview(artifact, max_bytes)
        if artifact.mime_type == "application/json" and not truncated:
            content = json.loads(content)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Artifact content missing") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Artifact storage invalid") from exc
    finding = _artifact_policy_finding(artifact, content)
    if finding is not None:
        _persist_policy_finding(repository, auth, artifact.run_id, finding)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Artifact blocked by policy")
    return artifact, content, truncated


def _artifact_preview(
    artifact: ArtifactRecord,
    content: Any,
    max_bytes: int,
) -> tuple[Any, bool, int | None, str | None]:
    language = _artifact_language(artifact)
    if isinstance(content, dict | list):
        return _json_preview(content), False, None, language or "json"
    text = redact_text(str(content))
    line_count = text.count("\n") + 1 if text else 0
    preview, truncated = _truncate_text(text, max_bytes)
    return preview, truncated, line_count, language


def _json_preview(content: Any) -> Any:
    redacted = redact_value(content)
    if isinstance(redacted, list):
        return {"type": "array", "item_count": len(redacted), "sample": redacted[:5]}
    if not isinstance(redacted, dict):
        return redacted
    summary: dict[str, Any] = {}
    preferred_keys = ("status", "decision", "platform", "schema_version", "model", "provider", "trace_id", "request_id")
    for key in preferred_keys:
        if key in redacted:
            summary[key] = redacted[key]
    for key, value in redacted.items():
        if key in summary:
            continue
        if isinstance(value, str | int | float | bool) or value is None:
            summary[key] = value
        elif isinstance(value, list):
            summary[f"{key}_count"] = len(value)
        elif isinstance(value, dict):
            summary[f"{key}_keys"] = sorted(str(item_key) for item_key in value.keys())
    return summary


def _artifact_language(artifact: ArtifactRecord) -> str | None:
    name = artifact.display_name.lower()
    if name.endswith(".pine") or artifact.kind == "pine_file":
        return "pine"
    if name.endswith(".md") or artifact.mime_type == "text/markdown":
        return "markdown"
    if name.endswith(".json") or artifact.mime_type == "application/json":
        return "json"
    if artifact.mime_type and artifact.mime_type.startswith("text/"):
        return "text"
    return None


def _truncate_text(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def _preview_text(text: str, max_chars: int = 160) -> str:
    normalized = " ".join(redact_text(text).split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "…"


def _progress_snapshot_event(
    run: AssistantRunRecord,
    event_count: int,
    latest: RunEventRecord | None,
    artifacts: list[ArtifactRecord],
) -> RunEventRecord:
    return RunEventRecord(
        id=f"progress_snapshot_{run.id}",
        run_id=run.id,
        conversation_id=run.conversation_id,
        owner_user_id=run.owner_user_id,
        workspace_id=run.workspace_id,
        sequence=0,
        type="progress.snapshot",
        payload={
            "status": run.status,
            "event_count": event_count,
            "artifact_count": len(artifacts),
            "latest_event_id": latest.id if latest is not None else None,
            "latest_event_sequence": latest.sequence if latest is not None else None,
        },
        created_at=run.updated_at,
        request_id=run.request_id,
        trace_id=run.trace_id,
    )


def _progress_event(event: RunEventRecord) -> RunEventRecord:
    return RunEventRecord(
        id=event.id,
        run_id=event.run_id,
        conversation_id=event.conversation_id,
        owner_user_id=event.owner_user_id,
        workspace_id=event.workspace_id,
        sequence=event.sequence,
        type="progress.update",
        payload=_progress_payload(event),
        created_at=event.created_at,
        request_id=event.request_id,
        trace_id=event.trace_id,
    )


def _progress_payload(event: RunEventRecord) -> dict[str, Any]:
    payload = event.payload or {}
    if event.type in {"stage.started", "stage.completed"}:
        return {**payload, "kind": event.type, "source_event_type": event.type}
    if event.type == "observability.stage.completed":
        return {**payload, "kind": "stage.completed", "source_event_type": event.type}
    if event.type == "artifact.created":
        return {**payload, "kind": "artifact.created", "source_event_type": event.type}
    if event.type == "policy.blocked":
        return {**payload, "kind": "policy.blocked", "source_event_type": event.type}
    if event.type in {"run.completed", "run.failed", "run.cancelled"}:
        return {**payload, "kind": "run.terminal", "source_event_type": event.type}
    if event.type in {"tool.started", "tool.completed"}:
        return {**payload, "kind": "tool.status", "source_event_type": event.type}
    if event.type in {"validation.completed", "review.completed"}:
        return {**payload, "kind": event.type, "source_event_type": event.type}
    return {**payload, "kind": "event", "source_event_type": event.type}


def _artifact_policy_finding(artifact: ArtifactRecord, content: object) -> PolicyFinding | None:
    evidence_level = EVIDENCE_GENERATED_ARTIFACT
    if artifact.kind == "validation_report":
        evidence_level = EVIDENCE_STATIC_VALIDATION
    elif artifact.kind == "runtime_trace_summary":
        evidence_level = EVIDENCE_MANUAL_RUNTIME_PROOF
    decision = evaluate_policy(
        PolicySubject(
            surface=f"artifact.{artifact.kind}",
            payload={"metadata": artifact.metadata_json or {}, "content": content},
            evidence_level=evidence_level,
        )
    )
    return decision.blocked_finding


def _deterministic_run_stream(
    repository: ConversationRepository,
    auth: AuthContext,
    run: AssistantRunRecord,
    *,
    language: str = "en",
) -> Iterator[str]:
    chunks = _deterministic_delta_chunks(language)
    yield _append_and_frame(
        repository,
        auth,
        run,
        "stage.started",
        {"stage": "model", "status": "running"},
    )
    yield _append_and_frame(
        repository,
        auth,
        run,
        "tool.started",
        {
            "tool_id": "deterministic_simulator",
            "label": "Chuẩn bị response deterministic" if language == "vi" else "Prepare deterministic response",
            "phase": "streaming_mvp",
        },
    )
    for index, chunk in enumerate(chunks, start=1):
        yield sse_frame(transient_delta_event(run, delta=chunk, chunk_index=index))
    response_text = compact_delta_text(chunks)
    yield _append_and_frame(
        repository,
        auth,
        run,
        "message.delta",
        {"text": response_text, "compact": True},
    )
    repository.create_message(auth, run.conversation_id, response_text, role="assistant")
    yield _append_and_frame(
        repository,
        auth,
        run,
        "stage.completed",
        {"stage": "model", "duration_ms": 0, "status": "completed"},
    )
    yield _append_and_frame(
        repository,
        auth,
        run,
        "tool.completed",
        {"tool_id": "deterministic_simulator", "status": "completed"},
    )
    yield _append_and_frame(
        repository,
        auth,
        run,
        "validation.completed",
        {"status": "passed", "source": "deterministic_simulator"},
    )
    yield _append_and_frame(
        repository,
        auth,
        run,
        "review.completed",
        {"decision": "simulated_review_completed", "source": "deterministic_simulator"},
    )
    completed = repository.set_run_status(auth, run.id, "completed")
    completed_run = completed if completed is not None else run
    yield _append_and_frame(
        repository,
        auth,
        completed_run,
        "run.completed",
        {"status": "completed"},
    )


def _deterministic_delta_chunks(language: str) -> tuple[str, ...]:
    if language == "vi":
        return (
            "## Response review-only\n\n",
            "Mình đã nhận request trading và chuẩn bị response deterministic để review.\n\n",
            "- Strategy context đã được nhận để review.\n",
            "- Static validation và review placeholder đã hoàn tất.\n",
            "- Không thực hiện live trading, broker execution, hoặc chứng minh runtime trên platform.",
        )
    return DETERMINISTIC_DELTA_CHUNKS


def _append_and_frame(
    repository: ConversationRepository,
    auth: AuthContext,
    run: AssistantRunRecord,
    event_type: str,
    payload: dict,
) -> str:
    event = repository.append_run_event(auth, run.id, event_type, redact_value(payload))
    if event is None:
        event = RunEventRecord(
            id="evt_failed_append",
            run_id=run.id,
            conversation_id=run.conversation_id,
            owner_user_id=run.owner_user_id,
            workspace_id=run.workspace_id,
            sequence=0,
            type="run.failed",
            payload={"error": "run_event_append_failed"},
            created_at=run.created_at,
        )
    return sse_frame(event)


def _maybe_generate_conversation_title(
    *,
    repository: ConversationRepository,
    llm_orchestrator: LLMOrchestrator,
    auth: AuthContext,
    conversation_id: str,
    current_title: str | None,
    previous_messages: list[MessageRecord],
    user_message: str,
) -> None:
    if current_title is not None:
        return
    if any(message.role == "user" for message in previous_messages):
        return
    try:
        title = llm_orchestrator.generate_conversation_title(auth=auth, user_message=user_message)
        repository.update_conversation_title(auth, conversation_id, title)
    except Exception:
        return


TIER_LABELS = {
    "free": "Free",
    "paid_low": "Basic",
    "paid_medium": "Pro",
    "paid_high": "Advanced",
}


def _current_usage_period() -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if period_start.month == 12:
        period_end = period_start.replace(year=period_start.year + 1, month=1)
    else:
        period_end = period_start.replace(month=period_start.month + 1)
    return period_start, period_end


def _workspace_capability(auth: AuthContext) -> WorkspaceCapabilityResponse:
    allowed_message_modes = ["deterministic", "agent"]
    capability_matrix = _workspace_capability_matrix()
    allowed_run_modes = [
        mode
        for mode in RUN_MODES
        if capability_matrix.get(mode, CapabilityModeStatus(status="blocked", reason_codes=["unknown_mode"])).status
        in {"available", "degraded"}
    ]
    return WorkspaceCapabilityResponse(
        user_id=auth.user_id,
        workspace_id=auth.workspace_id,
        role=auth.role,
        tier=auth.user_tier,
        tier_label=TIER_LABELS.get(auth.user_tier, auth.user_tier),
        allowed_message_modes=allowed_message_modes,
        allowed_run_modes=allowed_run_modes,
        capability_matrix=capability_matrix,
    )


def _workspace_capability_matrix() -> dict[str, CapabilityModeStatus]:
    return {
        mode: CapabilityModeStatus(status="available")
        for mode in RUN_MODES
    }


USER_FACING_MODEL_STAGES = (
    DEFAULT_MODEL_STAGE,
    MODEL_STAGE_PINE_CODE_GENERATION,
    MODEL_STAGE_BALANCED_REVIEW,
    MODEL_STAGE_REPAIR,
)


def _user_safe_model_routing_status(auth: AuthContext, configured: bool) -> dict[str, Any]:
    routing_mode = os.getenv("STRATEGY_CODEBOT_LLM_ROUTING", "registry").strip().lower() or "registry"
    gateway_report = gateway_env_report()
    available_gateways = [str(gateway) for gateway in gateway_report.get("available_gateways", [])]
    model_tier = normalize_user_tier(auth.user_tier)
    selected_stage_defaults: dict[str, str] = {}
    fallback_enabled = False

    if routing_mode == "registry":
        try:
            registry = load_model_registry()
            for stage in USER_FACING_MODEL_STAGES:
                routes = resolve_routes(registry, tier=model_tier, stage=stage)
                selected_stage_defaults[stage] = _safe_model_route_label(routes[0]) if routes else "Unavailable"
                fallback_enabled = fallback_enabled or len(routes) > 1
        except Exception:
            selected_stage_defaults = {stage: "Unavailable" for stage in USER_FACING_MODEL_STAGES}
    else:
        selected_stage_defaults[DEFAULT_MODEL_STAGE] = "Configured provider" if configured else "Unavailable"

    route_ready = bool(configured and (routing_mode != "registry" or available_gateways or selected_stage_defaults))
    user_message = (
        "Model route is ready for this workspace."
        if route_ready
        else "Model route is not ready. Retry later or contact an admin."
    )
    return {
        "model_routing_mode": routing_mode,
        "model_tier": model_tier,
        "selected_stage_defaults": selected_stage_defaults,
        "available_gateways": available_gateways,
        "route_ready": route_ready,
        "fallback_enabled": fallback_enabled,
        "user_message": user_message,
    }


def _safe_model_route_label(route: str) -> str:
    provider, _, _model = route.partition("/")
    if provider == "litellm_proxy":
        return "Managed model route"
    if provider == "openrouter":
        return "OpenRouter route"
    if provider in {"vercel_ai_gateway", "vercel-ai-gateway"}:
        return "Vercel AI Gateway route"
    if provider == "openai":
        return "OpenAI route"
    return "Configured model route"


def _require_message_mode(auth: AuthContext, mode: str) -> None:
    if mode not in _workspace_capability(auth).allowed_message_modes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "tier_upgrade_required", "mode": mode},
        )


def _require_run_mode(auth: AuthContext, mode: str) -> None:
    if mode not in _workspace_capability(auth).allowed_run_modes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "tier_upgrade_required", "mode": mode},
        )


def _run_idempotency_body(payload: RunCreate) -> dict[str, Any]:
    body = payload.model_dump(mode="json")
    if body.get("web_search") == "auto":
        body.pop("web_search", None)
    for key in ("pine_code", "backtest_config"):
        if body.get(key) is None:
            body.pop(key, None)
    return body


app = create_app()
