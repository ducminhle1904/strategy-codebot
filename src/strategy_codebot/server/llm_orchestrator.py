from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field, replace
from datetime import datetime
import json
import logging
import os
import re
import time
from typing import Any

from strategy_codebot.server.action_registry import action_registry_payload
from strategy_codebot.server.action_registry import ActionRegistryEvaluation
from strategy_codebot.server.action_registry import ActionRegistryRequestCache
from strategy_codebot.server.action_registry import evaluate_action_registry
from strategy_codebot.server.action_registry import available_registry_tool_ids
from strategy_codebot.server.action_registry import registry_entry_for_tool
from strategy_codebot.server.agent_loop import AgentLoopBudget
from strategy_codebot.server.agent_loop import BoundedScoutRunner
from strategy_codebot.server.agent_logging import agent_log
from strategy_codebot.server.artifact_kinds import BACKTEST_PLAN_ARTIFACT_KIND
from strategy_codebot.server.artifact_kinds import BACKTEST_REPORT_ARTIFACT_KIND
from strategy_codebot.server.artifact_kinds import BACKTEST_RUN_METADATA_ARTIFACT_KIND
from strategy_codebot.server.artifact_kinds import RISK_GATE_REPORT_ARTIFACT_KIND
from strategy_codebot.server.artifact_kinds import ROBUSTNESS_REPORT_ARTIFACT_KIND
from strategy_codebot.server.artifact_store import LocalArtifactStore
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.backtest_auto_chain import BACKTEST_AUTO_CHAIN_EVENTS
from strategy_codebot.server.backtest_auto_chain import BacktestAutoChainPlanner
from strategy_codebot.server.backtest_summary_text import format_backtest_summary_text
from strategy_codebot.server.bot_proposals import bot_required_missing
from strategy_codebot.server.conversation_context import ConversationContextBuilder
from strategy_codebot.server.domain_intent_gate import CHAT_INTENT_ACTIONS
from strategy_codebot.server.domain_intent_gate import CHAT_INTENT_MIN_CONFIDENCE
from strategy_codebot.server.domain_intent_gate import CHAT_INTENT_MODEL_STAGES
from strategy_codebot.server.domain_intent_gate import DOMAIN_SCOPES
from strategy_codebot.server.domain_intent_gate import RESPONSE_INTENTS
from strategy_codebot.server.domain_intent_gate import WORKFLOW_INTENTS
from strategy_codebot.server.domain_intent_gate import DomainScopeDecision
from strategy_codebot.server.domain_intent_gate import chat_intent_registry_guidance
from strategy_codebot.server.domain_intent_gate import classify_domain_scope_compat
from strategy_codebot.server.domain_intent_gate import domain_scope_for_response_intent
from strategy_codebot.server.domain_intent_gate import evidence_signals_from_regex
from strategy_codebot.server.domain_intent_gate import model_stage_for_response_intent
from strategy_codebot.server.domain_intent_gate import normalize_evidence_signals
from strategy_codebot.server.domain_intent_gate import normalize_domain_scope
from strategy_codebot.server.domain_intent_gate import normalize_workflow_intent
from strategy_codebot.server.domain_intent_gate import precheck_domain_scope
from strategy_codebot.server.domain_intent_gate import response_intent_allows_workflow
from strategy_codebot.server.domain_intent_gate import should_block_domain_scope
from strategy_codebot.server.domain_intent_gate import classifier_fallback_policy
from strategy_codebot.server.domain_intent_gate import workflow_timeout_fallback_policy
from strategy_codebot.server.domain_intent_gate import workflow_id_for_intent
from strategy_codebot.server.domain_intent_gate import workflow_intent_policy
from strategy_codebot.current_context_policy import current_context_policy_decision
from strategy_codebot.server.llm_clients import LLMClient, LLMClientEvent, ResponsesClient
from strategy_codebot.server.llm_clients import LLM_EVENT_MESSAGE_DELTA
from strategy_codebot.server.llm_clients import LLM_EVENT_SOURCES
from strategy_codebot.server.llm_clients import LLM_EVENT_TOOL_CALL
from strategy_codebot.server.llm_clients import LLM_EVENT_USAGE
from strategy_codebot.server.llm_clients import stream_client as _stream_client
from strategy_codebot.server.llm_json import extract_json_object
from strategy_codebot.server.llm_tools import ToolExecutionContext
from strategy_codebot.server.llm_tools import TOOL_DEFINITIONS
from strategy_codebot.server.llm_tools import compact_tool_output
from strategy_codebot.server.llm_tools import execute_tool
from strategy_codebot.server.llm_tools import persist_generated_pine_artifact
from strategy_codebot.server.llm_tools import provider_tools
from strategy_codebot.server.llm_tools import validate_tool_arguments
from strategy_codebot.server.knowledge_learning import KnowledgeLearningService
from strategy_codebot.server.intent_evidence import collect_chat_regex_evidence
from strategy_codebot.server.intent_evidence import current_context_signal
from strategy_codebot.server.market_data import MarketDataGateway
from strategy_codebot.server.market_data import market_data_context
from strategy_codebot.server.model_routing import DEFAULT_MODEL_STAGE
from strategy_codebot.server.model_routing import MODEL_STAGE_BALANCED_REVIEW
from strategy_codebot.server.model_routing import MODEL_STAGE_CLASSIFIER
from strategy_codebot.server.model_routing import MODEL_STAGE_WORKFLOW_FAST
from strategy_codebot.server.model_routing import MODEL_STAGE_PINE_CODE_GENERATION
from strategy_codebot.server.model_routing import MODEL_STAGE_REPAIR
from strategy_codebot.server.model_routing import MODEL_STAGE_STRATEGY_CODING
from strategy_codebot.server.model_routing import PROVIDER_KEEPALIVE_EVENT
from strategy_codebot.server.model_routing import PROVIDER_ROUTE_EVENT
from strategy_codebot.server.model_audit import MODEL_ACTION_EXECUTED
from strategy_codebot.server.model_audit import MODEL_ACTION_PROPOSED
from strategy_codebot.server.model_audit import MODEL_ACTION_REJECTED
from strategy_codebot.server.model_audit import MODEL_ACTION_VALIDATED
from strategy_codebot.server.model_audit import WORKFLOW_GATE_REQUIRED
from strategy_codebot.server.model_audit import append_model_audit_event
from strategy_codebot.server.model_audit import model_audit_payload
from strategy_codebot.server.observability import StageTimer
from strategy_codebot.server.observability import append_stage_event
from strategy_codebot.server.observability import append_stage_started_event
from strategy_codebot.server.policy_semantic_gate import PolicySemanticGateClassifier
from strategy_codebot.server.policy_semantic_gate import collect_semantic_policy_candidates
from strategy_codebot.server.policy_semantic_gate import semantic_policy_block_finding
from strategy_codebot.server.policy_semantic_gate import semantic_policy_fallback_decision
from strategy_codebot.server.policy_semantic_gate import should_block_semantic_policy
from strategy_codebot.server.policy import EVIDENCE_GENERATED_ARTIFACT
from strategy_codebot.server.policy import EVIDENCE_STRATEGY_IDEA
from strategy_codebot.server.policy import SAFE_BLOCKED_MESSAGE
from strategy_codebot.server.policy import PolicyFinding
from strategy_codebot.server.policy import PolicySubject
from strategy_codebot.server.policy import evaluate_policy
from strategy_codebot.server.policy import policy_finding_payload
from strategy_codebot.server.provider_errors import log_run_exception
from strategy_codebot.server.provider_errors import run_failed_payload
from strategy_codebot.server.tool_errors import tool_failure_fields
from strategy_codebot.server.redaction import redact_text
from strategy_codebot.server.redaction import redact_value
from strategy_codebot.server.repository import AssistantRunRecord
from strategy_codebot.server.repository import ArtifactRecord
from strategy_codebot.server.repository import ConversationRepository
from strategy_codebot.server.runner_bridge import RunnerIntegrationResult
from strategy_codebot.server.runner_bridge import execute_dry_run
from strategy_codebot.server.security_controls import BudgetExceeded
from strategy_codebot.server.security_controls import RunBudgetConfig
from strategy_codebot.server.security_controls import SecurityControlError
from strategy_codebot.server.security_controls import SecurityControls
from strategy_codebot.server.security_controls import budget_policy_finding
from strategy_codebot.server.streaming import sse_frame
from strategy_codebot.server.streaming import transient_delta_event
from strategy_codebot.server.streaming import transient_reasoning_event
from strategy_codebot.server.token_estimation import estimate_tokens as _token_estimate
from strategy_codebot.server.workflow_registry import STRATEGY_BOT_OPTIONAL_STEPS
from strategy_codebot.server.workflow_registry import STRATEGY_BOT_REQUIRED_INPUT_FIELDS
from strategy_codebot.server.workflow_registry import STRATEGY_BOT_SETUP_FIELDS
from strategy_codebot.server.workflow_registry import STRATEGY_BOT_WORKFLOW_ID
from strategy_codebot.server.workflow_registry import STRATEGY_BOT_WORKFLOW_STEPS
from strategy_codebot.server.workflow_registry import validate_workflow_payload
from strategy_codebot.server.workflow_registry import workflow_catalog_guidance
from strategy_codebot.server.workflow_task_status import WORKFLOW_TASK_RESOLVED_STATUSES
from strategy_codebot.server.workflow_prompt_generator import generate_workflow_task_prompt_payload
from strategy_codebot.server.workflow_prompt_generator import workflow_prompt_generator_events
from strategy_codebot.server.workflow_tasks import build_workflow_task_payload
from strategy_codebot.server.workflow_tasks import normalize_workflow_tasks
from strategy_codebot.server.workflow_tasks import STRATEGY_SPEC_NEXT_ACTION_GENERATE_PINE
from strategy_codebot.server.workflow_tasks import STRATEGY_SPEC_NEXT_ACTION_SKIP_PINE
from strategy_codebot.server.workflow_tasks import STRATEGY_SPEC_NEXT_STEP_TASK_ID
from strategy_codebot.server.workflow_tasks import workflow_task_strategy_spec_next_action
from strategy_codebot.server.workflow_tasks import workflow_task_state
from strategy_codebot.prompt_contracts import STAGE_PINE_CODE_GENERATION
from strategy_codebot.prompt_contracts import STAGE_STRATEGY_CODING
from strategy_codebot.prompt_contracts import STAGE_STRATEGY_REASONING
from strategy_codebot.prompt_contracts import stage_messages as prompt_stage_messages

logger = logging.getLogger(__name__)

SAFE_REASONING_EVENT = "model.reasoning.delta"
SUGGESTIONS_EVENT = "chat.suggestions.updated"
STRATEGY_WORKFLOW_EVENT = "chat.workflow.updated"
CLASSIFIER_TIMEOUT_SECONDS_ENV = "STRATEGY_CODEBOT_CLASSIFIER_TIMEOUT_SECONDS"
DEFAULT_CLASSIFIER_TIMEOUT_SECONDS = 25.0
CLASSIFIER_DEFAULT_TIMEOUTS = {
    "policy_semantic_gate": 5.0,
    "chat_intent_decision": 12.0,
    "action_planner": 8.0,
    "response_intent": 8.0,
}
CLASSIFIER_ROUTE_TIMEOUT_SECONDS_ENV = "STRATEGY_CODEBOT_CLASSIFIER_ROUTE_TIMEOUT_SECONDS"
CLASSIFIER_ROUTE_TIMEOUT_MARGIN_SECONDS = 1.0
CLASSIFIER_ROUTE_ATTEMPT_TARGET = 2
CLASSIFIER_ROUTE_TIMEOUT_MAX_SECONDS = 5.0
CLASSIFIER_ROUTE_TIMEOUT_MIN_SECONDS = 0.5
CLASSIFIER_EVENT_STARTED = "classifier.started"
CLASSIFIER_EVENT_ROUTE = "classifier.route"
CLASSIFIER_EVENT_COMPLETED = "classifier.completed"
CLASSIFIER_EVENT_TIMEOUT = "classifier.timeout"
CLASSIFIER_EVENT_FAILED = "classifier.failed"
WORKFLOW_TIMEOUT_FALLBACK_SOURCE = "workflow_timeout_fallback"
WORKFLOW_CLASSIFIER_FALLBACK_SOURCE = "workflow_classifier_fallback"
WORKFLOW_KICKOFF_FALLBACK_SOURCES = frozenset(
    {
        WORKFLOW_TIMEOUT_FALLBACK_SOURCE,
        WORKFLOW_CLASSIFIER_FALLBACK_SOURCE,
    }
)
CHAT_RESPONSE_MODEL_STAGES = CHAT_INTENT_MODEL_STAGES - {
    MODEL_STAGE_CLASSIFIER,
    MODEL_STAGE_WORKFLOW_FAST,
}
SAFE_CLASSIFIER_PROMPT_SUMMARY_SCALAR_KEYS = frozenset(
    {
        "action_count",
        "artifact_kind_count",
        "candidate_count",
        "message_chars",
        "regex_evidence_count",
    }
)
ACTION_PLANNER_ENABLED_ENV = "STRATEGY_CODEBOT_ACTION_PLANNER_ENABLED"
SUGGESTION_SLOTS = {"entry", "exit", "market", "risk"}
RESPONSE_INTENT_FALLBACK_CONFIDENCE = 0.35
RESPONSE_INTENT_LLM_MIN_CONFIDENCE = 0.6
SEMANTIC_ACTION_MIN_CONFIDENCE = 0.65
CHAT_INTENT_DECISION_MIN_CONFIDENCE = CHAT_INTENT_MIN_CONFIDENCE
STRATEGY_PROMPT_CHAIN_INTENTS = {"artifact_generation", "pine_generation", "strategy_building"}
STRATEGY_PROMPT_CHAIN_STAGES = (
    STAGE_STRATEGY_REASONING,
    STAGE_STRATEGY_CODING,
    STAGE_PINE_CODE_GENERATION,
)
PROMPT_CHAIN_WORKFLOW = "strategy_prompt_chain"
PROMPT_CHAIN_STARTED_EVENT = "prompt_chain.started"
PROMPT_CHAIN_STAGE_COMPLETED_EVENT = "prompt_chain.stage_completed"
PROMPT_CHAIN_COMPLETED_EVENT = "prompt_chain.completed"
PROMPT_CHAIN_ROUTE_TIMEOUT_EVENT = "prompt_chain.route_timeout"
PROMPT_CHAIN_FALLBACK_EVENT = "prompt_chain.fallback"
PROMPT_CHAIN_FAILED_EVENT = "prompt_chain.failed"
PROMPT_CHAIN_ROUTE_TIMEOUT_SECONDS_ENV = "STRATEGY_CODEBOT_PROMPT_CHAIN_ROUTE_TIMEOUT_SECONDS"
PROMPT_CHAIN_ROUTE_KEEPALIVE_SECONDS_ENV = "STRATEGY_CODEBOT_PROMPT_CHAIN_ROUTE_KEEPALIVE_SECONDS"
DEFAULT_PROMPT_CHAIN_ROUTE_TIMEOUT_SECONDS = 45.0
DEFAULT_PROMPT_CHAIN_ROUTE_KEEPALIVE_SECONDS = 15.0
STRATEGY_PROMPT_CHAIN_SIZING_GUIDANCE = (
    "Use bounded position sizing such as fixed units or 1-2% account equity risk per trade; "
    "never use full-capital, all-in, or unbounded leverage assumptions."
)
SEMANTIC_ACTIONS = {
    "build_robustness_report",
    "create_proposed_intent",
    "draft_bot",
    "get_bot_status",
    "get_backtest_summary",
    "list_bot_events",
    "list_bots",
    "market_research",
    "query_backtest_trades",
    "repair",
    "review_assumptions",
    "review_risk",
    "run_backtest_preview",
    "run_backtest_variant_lab",
    "run_risk_gate",
}
SAFE_REASONING_LABELS = {
    "artifact": {
        "en": "Preparing the review artifact.",
        "vi": "Đang chuẩn bị artifact review.",
    },
    "context": {
        "en": "Reading conversation context.",
        "vi": "Đang đọc ngữ cảnh cuộc trò chuyện.",
    },
    "finalizing": {
        "en": "Finalizing the response.",
        "vi": "Đang hoàn thiện phản hồi.",
    },
    "model": {
        "en": "Preparing the response.",
        "vi": "Đang chuẩn bị phản hồi.",
    },
    "strategy_spec": {
        "en": "Drafting the strategy spec.",
        "vi": "Đang soạn strategy spec.",
    },
    "retrieval": {
        "en": "Checking relevant knowledge.",
        "vi": "Đang kiểm tra knowledge context liên quan.",
    },
    "tool": {
        "en": "Running the required support step.",
        "vi": "Đang chạy bước hỗ trợ cần thiết.",
    },
    "backtest": {
        "en": "Preparing Backtest Preview.",
        "vi": "Đang chuẩn bị Backtest Preview.",
    },
}


@dataclass
class RunBudget:
    max_tool_calls: int = 12
    max_total_tokens: int = 64000
    max_output_tokens: int = 16000
    executed_tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    blocked: bool = False
    completed_tool_ids: list[str] = field(default_factory=list)
    completed_tool_results: list[dict[str, Any]] = field(default_factory=list)
    auto_chain_started: bool = False
    auto_chain_steps_completed: int = 0
    auto_chain_failure_text: str | None = None
    auto_chain_allowed: bool = False
    auto_chain_source: str = "none"

    def allow_tool(self) -> bool:
        return self.executed_tool_calls < self.max_tool_calls

    def add_usage(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

    def check_usage(self) -> None:
        if self.input_tokens + self.output_tokens > self.max_total_tokens:
            raise BudgetExceeded("tokens")
        if self.output_tokens > self.max_output_tokens:
            raise BudgetExceeded("output_tokens")


@dataclass(frozen=True)
class IntentClassification:
    intent: str
    confidence: float
    source: str

    def payload(self) -> dict[str, Any]:
        return {
            "confidence": round(max(0.0, min(1.0, self.confidence)), 3),
            "intent": self.intent,
            "safe": True,
            "source": self.source,
        }


@dataclass(frozen=True)
class ClassifierRunOutcome:
    value: Any
    status: str
    duration_ms: int
    timeout_seconds: float
    fallback_source: str | None = None
    error_class: str | None = None


class ClassifierRouteCaptureClient:
    def __init__(
        self,
        client: LLMClient,
        *,
        stage: str,
        route_timeout_seconds: float | None,
        auth: AuthContext | None = None,
        user_tier: str | None = None,
    ) -> None:
        self.client = client
        self.model = getattr(client, "model", "classifier-route-capture")
        self.stage = stage
        self.route_timeout_seconds = route_timeout_seconds
        self.auth = auth
        self.user_tier = user_tier
        self.route_events: list[dict[str, Any]] = []

    def ensure_configured(self) -> None:
        self.client.ensure_configured()

    def stream(
        self,
        *,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]],
        routing_context: dict[str, Any] | None = None,
    ) -> Iterable[LLMClientEvent]:
        context = dict(routing_context or {})
        context["stage"] = self.stage
        if self.auth is not None:
            context["auth"] = self.auth
            context["user_tier"] = self.auth.user_tier
        elif self.user_tier:
            context["user_tier"] = self.user_tier
        if self.route_timeout_seconds is not None:
            context["route_timeout_seconds"] = self.route_timeout_seconds
            context["hard_route_timeout"] = True
        for event in _stream_client(self.client, messages=messages, tools=tools, routing_context=context):
            if event.type == PROVIDER_ROUTE_EVENT and isinstance(event.arguments, dict):
                self.route_events.append(dict(event.arguments))
            yield event


def _run_optional_classifier(
    classifier_name: str,
    classify: Callable[[], Any],
    fallback: Any,
    *,
    log_context: dict[str, Any] | None = None,
) -> Any:
    outcome = _run_optional_classifier_outcome(
        classifier_name,
        classify,
        fallback,
        log_context=log_context,
    )
    return outcome.value


def _run_optional_classifier_outcome(
    classifier_name: str,
    classify: Callable[[], Any],
    fallback: Any,
    *,
    log_context: dict[str, Any] | None = None,
) -> ClassifierRunOutcome:
    timeout_seconds = _classifier_timeout_seconds(classifier_name)
    started = time.perf_counter()
    if timeout_seconds <= 0:
        result = classify()
        return ClassifierRunOutcome(
            value=result,
            status="completed",
            duration_ms=_duration_ms(started),
            timeout_seconds=timeout_seconds,
            fallback_source=getattr(result, "source", None),
        )
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"strategy-{classifier_name}")
    future = executor.submit(classify)
    try:
        result = future.result(timeout=timeout_seconds)
        return ClassifierRunOutcome(
            value=result,
            status="completed",
            duration_ms=_duration_ms(started),
            timeout_seconds=timeout_seconds,
            fallback_source=getattr(result, "source", None),
        )
    except FutureTimeoutError:
        agent_log(
            logger,
            "warn",
            "agent.classifier.timeout",
            component="llm_orchestrator",
            classifier=classifier_name,
            timeout_seconds=round(timeout_seconds, 3),
            **(log_context or {}),
        )
        try:
            value = replace(fallback, source="timeout_fallback")
        except TypeError:
            value = fallback
        return ClassifierRunOutcome(
            value=value,
            status="timeout",
            duration_ms=_duration_ms(started),
            timeout_seconds=timeout_seconds,
            fallback_source=getattr(value, "source", "timeout_fallback"),
        )
    except Exception as exc:
        agent_log(
            logger,
            "error",
            "agent.classifier.failed",
            component="llm_orchestrator",
            classifier=classifier_name,
            **(log_context or {}),
        )
        return ClassifierRunOutcome(
            value=fallback,
            status="failed",
            duration_ms=_duration_ms(started),
            timeout_seconds=timeout_seconds,
            fallback_source=getattr(fallback, "source", "fallback"),
            error_class=exc.__class__.__name__,
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _duration_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _classifier_timeout_seconds(classifier_name: str | None = None) -> float:
    raw = os.getenv(_classifier_timeout_env_name(classifier_name)) if classifier_name else None
    if raw is None:
        raw = os.getenv(CLASSIFIER_TIMEOUT_SECONDS_ENV)
    if raw is None or not raw.strip():
        return CLASSIFIER_DEFAULT_TIMEOUTS.get(classifier_name or "", DEFAULT_CLASSIFIER_TIMEOUT_SECONDS)
    try:
        timeout = float(raw)
    except ValueError:
        return CLASSIFIER_DEFAULT_TIMEOUTS.get(classifier_name or "", DEFAULT_CLASSIFIER_TIMEOUT_SECONDS)
    return max(0.0, timeout)


def _classifier_timeout_env_name(classifier_name: str | None) -> str:
    if not classifier_name:
        return CLASSIFIER_TIMEOUT_SECONDS_ENV
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", classifier_name).strip("_").upper()
    return f"{CLASSIFIER_TIMEOUT_SECONDS_ENV}_{normalized}"


def _classifier_route_timeout_seconds(classifier_name: str) -> float | None:
    timeout = _classifier_timeout_seconds(classifier_name)
    if timeout <= 0:
        return None
    override = _classifier_route_timeout_override(classifier_name)
    max_allowed = max(0.1, timeout - CLASSIFIER_ROUTE_TIMEOUT_MARGIN_SECONDS)
    if override is not None:
        return min(max_allowed, override)
    per_route = max_allowed / CLASSIFIER_ROUTE_ATTEMPT_TARGET
    return max(
        CLASSIFIER_ROUTE_TIMEOUT_MIN_SECONDS,
        min(CLASSIFIER_ROUTE_TIMEOUT_MAX_SECONDS, per_route),
    )


def _classifier_route_timeout_override(classifier_name: str) -> float | None:
    raw = os.getenv(_classifier_route_timeout_env_name(classifier_name)) or os.getenv(
        CLASSIFIER_ROUTE_TIMEOUT_SECONDS_ENV
    )
    if raw is None or not raw.strip():
        return None
    try:
        timeout = float(raw)
    except ValueError:
        return None
    return timeout if timeout > 0 else None


def _classifier_route_timeout_env_name(classifier_name: str | None) -> str:
    if not classifier_name:
        return CLASSIFIER_ROUTE_TIMEOUT_SECONDS_ENV
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", classifier_name).strip("_").upper()
    return f"{CLASSIFIER_ROUTE_TIMEOUT_SECONDS_ENV}_{normalized}"


def _prompt_chain_route_timeout_seconds() -> float:
    raw = os.getenv(PROMPT_CHAIN_ROUTE_TIMEOUT_SECONDS_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_PROMPT_CHAIN_ROUTE_TIMEOUT_SECONDS
    try:
        timeout = float(raw)
    except ValueError:
        return DEFAULT_PROMPT_CHAIN_ROUTE_TIMEOUT_SECONDS
    return timeout if timeout > 0 else DEFAULT_PROMPT_CHAIN_ROUTE_TIMEOUT_SECONDS


def _prompt_chain_route_keepalive_seconds() -> float:
    raw = os.getenv(PROMPT_CHAIN_ROUTE_KEEPALIVE_SECONDS_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_PROMPT_CHAIN_ROUTE_KEEPALIVE_SECONDS
    try:
        timeout = float(raw)
    except ValueError:
        return DEFAULT_PROMPT_CHAIN_ROUTE_KEEPALIVE_SECONDS
    return timeout if timeout > 0 else DEFAULT_PROMPT_CHAIN_ROUTE_KEEPALIVE_SECONDS


def _classifier_event_payload(
    run: AssistantRunRecord,
    *,
    classifier_name: str,
    stage: str,
    status: str,
    timeout_seconds: float,
    prompt_summary: dict[str, Any],
    duration_ms: int | None = None,
    fallback_source: str | None = None,
    error_class: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "actor": "backend",
        "source": "classifier",
        "classifier_name": classifier_name,
        "stage": stage,
        "status": status,
        "timeout_seconds": round(max(0.0, timeout_seconds), 3),
        "conversation_id": run.conversation_id,
        "run_id": run.id,
        "request_id": run.request_id,
        "trace_id": run.trace_id,
        "safe_prompt_summary": _safe_classifier_prompt_summary(prompt_summary),
    }
    if duration_ms is not None:
        payload["duration_ms"] = max(0, duration_ms)
    if fallback_source:
        payload["fallback_source"] = fallback_source
    if error_class:
        payload["error_class"] = error_class
    return payload


def _classifier_route_event_payload(
    run: AssistantRunRecord,
    *,
    classifier_name: str,
    stage: str,
    route_event: dict[str, Any],
    prompt_summary: dict[str, Any],
) -> dict[str, Any]:
    payload = _classifier_event_payload(
        run,
        classifier_name=classifier_name,
        stage=stage,
        status="route",
        timeout_seconds=_classifier_timeout_seconds(classifier_name),
        prompt_summary=prompt_summary,
    )
    for key, value in route_event.items():
        if isinstance(value, str | bool | int | float):
            payload[key] = value
    fallback_attempts = route_event.get("fallback_attempts")
    if isinstance(fallback_attempts, list):
        payload["fallback_attempt_count"] = len(fallback_attempts)
    return payload


def _safe_classifier_prompt_summary(value: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, raw in value.items():
        if key in SAFE_CLASSIFIER_PROMPT_SUMMARY_SCALAR_KEYS and (
            isinstance(raw, bool | int | float) or raw is None
        ):
            summary[key] = raw
        elif isinstance(raw, str):
            summary[f"{key}_chars"] = len(raw)
        elif isinstance(raw, set | list | tuple):
            summary[f"{key}_count"] = len(raw)
        elif isinstance(raw, dict):
            summary[f"{key}_count"] = len(raw)
    return summary


def _action_planner_enabled() -> bool:
    raw = os.getenv(ACTION_PLANNER_ENABLED_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "off", "no"}


def _should_run_action_planner(message_content: str, *, context_text: str, artifact_kinds: set[str]) -> bool:
    return True


class ResponseIntentClassifier:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def classify(self, message_content: str, *, web_search: str = "auto") -> IntentClassification:
        deterministic = _deterministic_response_intent(message_content, web_search=web_search)
        if deterministic is not None:
            return deterministic
        return _run_optional_classifier(
            "response_intent",
            lambda: self._classify_with_llm(message_content),
            IntentClassification("general_chat", RESPONSE_INTENT_FALLBACK_CONFIDENCE, "fallback"),
        )

    def _classify_with_llm(self, message_content: str) -> IntentClassification:
        try:
            chunks: list[str] = []
            for event in _stream_client(
                self.client,
                messages=[
                    {"role": "system", "content": _intent_classifier_system_prompt()},
                    {"role": "user", "content": message_content[:2000]},
                ],
                tools=[],
                routing_context={"stage": MODEL_STAGE_WORKFLOW_FAST},
            ):
                if event.type == LLM_EVENT_MESSAGE_DELTA and event.text:
                    chunks.append(event.text)
            decoded = _parse_intent_classifier_json("".join(chunks))
        except Exception:
            return IntentClassification("general_chat", RESPONSE_INTENT_FALLBACK_CONFIDENCE, "fallback")
        if decoded is None:
            return IntentClassification("general_chat", RESPONSE_INTENT_FALLBACK_CONFIDENCE, "fallback")
        intent = decoded["intent"]
        confidence = decoded["confidence"]
        if confidence < RESPONSE_INTENT_LLM_MIN_CONFIDENCE:
            return IntentClassification("general_chat", confidence, "fallback")
        return IntentClassification(intent, confidence, "llm")


@dataclass(frozen=True)
class SemanticActionClassification:
    intent: str
    confidence: float
    source: str
    suggested_actions: tuple[str, ...] = ()
    reason: str | None = None
    missing_inputs: tuple[str, ...] = ()

    def is_active(self) -> bool:
        return self.source != "none" and self.confidence >= SEMANTIC_ACTION_MIN_CONFIDENCE and bool(self.suggested_actions)

    def context_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "semantic_action_confidence": round(max(0.0, min(1.0, self.confidence)), 3),
            "semantic_action_intent": self.intent,
            "semantic_action_source": self.source,
        }
        if self.suggested_actions:
            payload["semantic_suggested_actions"] = list(self.suggested_actions)
        return payload


@dataclass(frozen=True)
class ActionPlanDecision:
    decision: str
    intent_id: str
    confidence: float
    source: str
    tool_id: str | None = None
    arguments: dict[str, Any] | None = None
    suggested_actions: tuple[str, ...] = ()
    reason: str | None = None

    def is_active(self) -> bool:
        return self.source != "none" and self.confidence >= SEMANTIC_ACTION_MIN_CONFIDENCE

    def semantic_classification(self) -> SemanticActionClassification:
        return SemanticActionClassification(
            self.intent_id,
            self.confidence,
            "planner",
            suggested_actions=self.suggested_actions,
            reason=self.reason,
        )

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action_plan_confidence": round(max(0.0, min(1.0, self.confidence)), 3),
            "action_plan_decision": self.decision,
            "action_plan_intent": self.intent_id,
            "action_plan_source": self.source,
        }
        if self.tool_id:
            payload["action_plan_tool_id"] = self.tool_id
        if self.suggested_actions:
            payload["action_plan_suggested_actions"] = list(self.suggested_actions)
        if self.reason:
            payload["reason"] = self.reason
        return payload


@dataclass(frozen=True)
class ChatIntentDecision:
    response_intent: str
    action: str
    model_stage: str
    confidence: float
    source: str
    tool_id: str | None = None
    auto_chain: bool = False
    current_context_required: bool = False
    domain_scope: str = "ambiguous"
    workflow_intent: str | None = None
    missing_inputs: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    used_signals: tuple[str, ...] = ()

    def should_start_auto_chain(self) -> bool:
        return (
            self.source != "none"
            and self.confidence >= CHAT_INTENT_DECISION_MIN_CONFIDENCE
            and (self.auto_chain or self.action == "start_auto_chain")
        )

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": self.action,
            "auto_chain": self.auto_chain,
            "confidence": round(max(0.0, min(1.0, self.confidence)), 3),
            "current_context_required": self.current_context_required,
            "domain_scope": self.domain_scope,
            "intent": self.response_intent,
            "model_stage": self.model_stage,
            "safe": True,
            "source": self.source,
        }
        if self.workflow_intent:
            payload["workflow_intent"] = self.workflow_intent
        if self.tool_id:
            payload["tool_id"] = self.tool_id
        if self.missing_inputs:
            payload["missing_inputs"] = list(self.missing_inputs)
        if self.reasons:
            payload["reasons"] = list(self.reasons)
        if self.used_signals:
            payload["used_signals"] = list(self.used_signals)
        return payload


@dataclass(frozen=True)
class _StrategyPromptChainResult:
    final_text: str
    strategy_spec: dict[str, Any]
    pine_code: str


class ChatIntentDecisionPlanner:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def decide(
        self,
        message_content: str,
        *,
        context_text: str,
        artifact_kinds: set[str],
        web_search: str,
        language: str,
        domain_scope_hint: str | None = None,
        log_context: dict[str, Any] | None = None,
        return_outcome: bool = False,
        action_evaluation: ActionRegistryEvaluation | None = None,
    ) -> ChatIntentDecision | ClassifierRunOutcome:
        regex_evidence = _chat_regex_evidence(message_content)
        precheck = precheck_domain_scope(message_content, artifact_kinds=artifact_kinds)
        deterministic = _deterministic_response_intent(message_content, web_search=web_search)
        if precheck is not None and precheck.reason in {
            "empty_or_whitespace",
            "small_talk_or_context_followup",
            "artifact_context_signal",
            "explicit_off_topic_request",
        }:
            response_intent = deterministic.intent if deterministic is not None else "general_chat"
            decision = ChatIntentDecision(
                response_intent=response_intent,
                action="answer",
                model_stage=_model_stage_for_intent(response_intent),
                confidence=max(precheck.confidence, deterministic.confidence if deterministic is not None else 0.0),
                source="deterministic_precheck",
                current_context_required=bool(regex_evidence.get("current_info")),
                domain_scope=precheck.scope,
                used_signals=evidence_signals_from_regex(regex_evidence),
            )
            if return_outcome:
                return ClassifierRunOutcome(
                    value=decision,
                    status="completed",
                    duration_ms=0,
                    timeout_seconds=_classifier_timeout_seconds("chat_intent_decision"),
                    fallback_source=decision.source,
                )
            return decision
        if deterministic is not None:
            decision = ChatIntentDecision(
                response_intent=deterministic.intent,
                action="answer",
                model_stage=_model_stage_for_intent(deterministic.intent),
                confidence=deterministic.confidence,
                source=deterministic.source,
                current_context_required=bool(regex_evidence.get("current_info")),
                domain_scope=domain_scope_for_response_intent(deterministic.intent),
                used_signals=evidence_signals_from_regex(regex_evidence),
            )
            if return_outcome:
                return ClassifierRunOutcome(
                    value=decision,
                    status="completed",
                    duration_ms=0,
                    timeout_seconds=_classifier_timeout_seconds("chat_intent_decision"),
                    fallback_source=decision.source,
                )
            return decision
        effective_domain_scope_hint = domain_scope_hint or (precheck.scope if precheck is not None else None)
        action_evaluation = action_evaluation or evaluate_action_registry(
            artifact_kinds=artifact_kinds,
            context_text=f"{context_text}\n{message_content}",
            web_search=web_search,
        )
        registry_payload = action_evaluation.payload
        available_tools = {str(tool_id) for tool_id in action_evaluation.available_tool_ids}
        fallback = _fallback_chat_intent_decision(
            message_content,
            web_search=web_search,
            regex_evidence=regex_evidence,
            domain_scope_hint=effective_domain_scope_hint,
        )
        outcome = _run_optional_classifier_outcome(
            "chat_intent_decision",
            lambda: self._decide_with_llm(
                message_content,
                context_text=context_text,
                artifact_kinds=artifact_kinds,
                web_search=web_search,
                language=language,
                regex_evidence=regex_evidence,
                registry_payload=registry_payload,
                available_tools=available_tools,
                domain_scope_hint=effective_domain_scope_hint,
                fallback=fallback,
            ),
            fallback,
            log_context=log_context,
        )
        return outcome if return_outcome else outcome.value

    def _decide_with_llm(
        self,
        message_content: str,
        *,
        context_text: str,
        artifact_kinds: set[str],
        web_search: str,
        language: str,
        regex_evidence: dict[str, bool],
        registry_payload: list[dict[str, Any]],
        available_tools: set[str],
        domain_scope_hint: str | None,
        fallback: ChatIntentDecision,
    ) -> ChatIntentDecision:
        prompt = {
            "user_message": message_content[:2000],
            "language": language,
            "artifact_kinds": sorted(artifact_kinds),
            "context_excerpt": context_text[-3000:],
            "domain_scope_hint": domain_scope_hint,
            "allowed_domain_scopes": sorted(DOMAIN_SCOPES),
            "allowed_workflow_intents": sorted(WORKFLOW_INTENTS),
            "web_search": web_search,
            "regex_evidence": regex_evidence,
            "actions": registry_payload,
            "intent_registry_guidance": chat_intent_registry_guidance(),
            "boundaries": [
                "Regex evidence is a hint only; decide from the user's semantic intent.",
                "Choose start_auto_chain only when the user asks to generate or preview local backtest evidence.",
                "Use current_context_required for current market/docs/provider/model information, not for current preview evidence.",
                "Never plan paper/live trading, broker execution, profitability claims, or approval bypasses.",
            ],
        }
        try:
            chunks: list[str] = []
            for event in _stream_client(
                self.client,
                messages=[
                    {"role": "system", "content": _chat_intent_decision_system_prompt()},
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
                tools=[],
                routing_context={"stage": MODEL_STAGE_WORKFLOW_FAST},
            ):
                if event.type == LLM_EVENT_MESSAGE_DELTA and event.text:
                    chunks.append(event.text)
            decoded = _parse_chat_intent_decision_json(
                "".join(chunks),
                available_tools=available_tools,
                regex_evidence=regex_evidence,
            )
        except Exception:
            return fallback
        return decoded or fallback


class ActionPlanner:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def plan(
        self,
        message_content: str,
        *,
        response_intent: str,
        context_text: str,
        artifact_kinds: set[str],
        web_search: str,
        regex_evidence: dict[str, bool] | None = None,
        log_context: dict[str, Any] | None = None,
        return_outcome: bool = False,
        action_evaluation: ActionRegistryEvaluation | None = None,
    ) -> ActionPlanDecision | ClassifierRunOutcome:
        action_evaluation = action_evaluation or evaluate_action_registry(
            artifact_kinds=artifact_kinds,
            context_text=f"{context_text}\n{message_content}",
            web_search=web_search,
        )
        registry_payload = action_evaluation.payload
        available_tools = set(action_evaluation.available_tool_ids)
        if not available_tools:
            decision = ActionPlanDecision("answer", "none", 0.0, "none")
            if return_outcome:
                return ClassifierRunOutcome(
                    value=decision,
                    status="completed",
                    duration_ms=0,
                    timeout_seconds=_classifier_timeout_seconds("action_planner"),
                    fallback_source=decision.source,
                )
            return decision
        outcome = _run_optional_classifier_outcome(
            "action_planner",
            lambda: self._plan_with_llm(
                message_content,
                response_intent=response_intent,
                context_text=context_text,
                artifact_kinds=artifact_kinds,
                regex_evidence=regex_evidence or _chat_regex_evidence(message_content),
                registry_payload=registry_payload,
                available_tools={str(tool_id) for tool_id in available_tools},
            ),
            ActionPlanDecision("answer", "none", 0.0, "fallback"),
            log_context=log_context,
        )
        return outcome if return_outcome else outcome.value

    def _plan_with_llm(
        self,
        message_content: str,
        *,
        response_intent: str,
        context_text: str,
        artifact_kinds: set[str],
        regex_evidence: dict[str, bool],
        registry_payload: list[dict[str, Any]],
        available_tools: set[str],
    ) -> ActionPlanDecision:
        prompt = {
            "user_message": message_content[:1600],
            "response_intent_hint": response_intent,
            "artifact_kinds": sorted(artifact_kinds),
            "context_excerpt": context_text[-2400:],
            "regex_evidence": regex_evidence,
            "actions": registry_payload,
            "boundaries": [
                "Local backtest preview artifacts are review-only evidence.",
                "Do not claim TradingView proof, broker proof, live trading evidence, or profitability.",
                "Only choose call_tool for an available action whose tool_id exactly matches the user's request.",
            ],
        }
        try:
            chunks: list[str] = []
            for event in _stream_client(
                self.client,
                messages=[
                    {"role": "system", "content": _action_planner_system_prompt()},
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
                tools=[],
                routing_context={"stage": MODEL_STAGE_WORKFLOW_FAST},
            ):
                if event.type == LLM_EVENT_MESSAGE_DELTA and event.text:
                    chunks.append(event.text)
            decoded = _parse_action_plan_json("".join(chunks), available_tools=available_tools)
        except Exception:
            return ActionPlanDecision("answer", "none", 0.0, "fallback")
        return decoded or ActionPlanDecision("answer", "none", 0.0, "fallback")


@dataclass
class LLMOrchestrator:
    repository: ConversationRepository
    artifact_store: LocalArtifactStore
    client: LLMClient = field(default_factory=ResponsesClient)
    max_tool_calls: int = 8
    security_controls: SecurityControls = field(default_factory=SecurityControls)
    budget_config: RunBudgetConfig = field(default_factory=RunBudgetConfig)
    market_data_gateway: MarketDataGateway | None = None

    def ensure_configured(self) -> None:
        self.client.ensure_configured()

    def generate_conversation_title(self, *, auth: AuthContext, user_message: str) -> str:
        prompt = _title_prompt(user_message)
        try:
            self.security_controls.check_model_call(auth, model=self.client.model)
            chunks: list[str] = []
            for event in _stream_client(
                self.client,
                messages=[
                    {"role": "system", "content": _title_system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                tools=[],
                routing_context={"auth": auth, "user_tier": auth.user_tier, "stage": DEFAULT_MODEL_STAGE},
            ):
                if event.type == LLM_EVENT_MESSAGE_DELTA and event.text:
                    chunks.append(event.text)
            title = _normalize_title("".join(chunks))
        except Exception:
            title = None
        return title or deterministic_conversation_title(user_message)

    def stream_chat(
        self,
        *,
        auth: AuthContext,
        conversation_id: str,
        message_content: str,
        current_message_id: str | None = None,
        language: str = "en",
        request_id: str | None = None,
        selected_action: dict[str, Any] | None = None,
        trace_id: str | None = None,
        web_search: str = "auto",
        workflow_task_resume: dict[str, Any] | None = None,
    ) -> Iterator[str]:
        language = _normalize_language(language)
        web_search = _normalize_web_search(web_search)
        selected_action_payload = selected_action if isinstance(selected_action, dict) else None
        workflow_task_resume_payload = workflow_task_resume if isinstance(workflow_task_resume, dict) else None
        run = self.repository.create_run(
            auth,
            conversation_id,
            status="running",
            request_id=request_id,
            trace_id=trace_id,
        )
        if run is None:
            return
        agent_log(
            logger,
            "info",
            "agent.run.started",
            component="llm_orchestrator",
            conversation_id=conversation_id,
            language=language,
            request_id=run.request_id,
            run_id=run.id,
            trace_id=run.trace_id,
            user_tier=auth.user_tier,
            web_search=web_search,
        )
        budget = self._new_budget()
        action_registry_cache = ActionRegistryRequestCache()
        accumulated_text: list[str] = []
        pre_response_frames: list[str] = []
        terminal_status: str | None = None
        if workflow_task_resume_payload is not None:
            pre_response_frames.append(
                self._append_frame(
                    auth,
                    run,
                    "workflow.continuation.started",
                    {
                        **workflow_task_resume_payload,
                        "source": "workflow_task_resume",
                        "status": "started",
                    },
                )
            )
        safety_finding = None if workflow_task_resume_payload is not None else chat_safety_preflight(message_content)
        if safety_finding is not None:
            yield from self._chat_safety_blocked(
                auth,
                run,
                safety_finding,
                language=language,
                audit_source="chat_safety_preflight",
            )
            return
        policy_candidates = [] if workflow_task_resume_payload is not None else collect_semantic_policy_candidates(message_content)
        if policy_candidates:
            classifier_name = "policy_semantic_gate"
            policy_prompt_summary = {
                "message_chars": len(message_content or ""),
                "candidate_count": len(policy_candidates),
                "artifact_kind_count": 0,
                "action_count": 0,
            }
            policy_capture_client = ClassifierRouteCaptureClient(
                self.client,
                stage=MODEL_STAGE_WORKFLOW_FAST,
                route_timeout_seconds=_classifier_route_timeout_seconds(classifier_name),
                auth=auth,
            )
            pre_response_frames.append(
                self._classifier_started_frame(
                    auth,
                    run,
                    classifier_name=classifier_name,
                    stage=MODEL_STAGE_WORKFLOW_FAST,
                    timeout_seconds=_classifier_timeout_seconds(classifier_name),
                    prompt_summary=policy_prompt_summary,
                )
            )
            policy_outcome = _run_optional_classifier_outcome(
                classifier_name,
                lambda: PolicySemanticGateClassifier(policy_capture_client).classify(
                    message_content,
                    candidates=policy_candidates,
                    surface="agent.chat.input",
                    evidence_level=EVIDENCE_STRATEGY_IDEA,
                ),
                semantic_policy_fallback_decision(policy_candidates),
                log_context={
                    "conversation_id": conversation_id,
                    "run_id": run.id,
                    "request_id": run.request_id,
                    "trace_id": run.trace_id,
                },
            )
            pre_response_frames.extend(
                self._classifier_result_frames(
                    auth,
                    run,
                    classifier_name=classifier_name,
                    stage=MODEL_STAGE_WORKFLOW_FAST,
                    capture_client=policy_capture_client,
                    outcome=policy_outcome,
                    prompt_summary=policy_prompt_summary,
                )
            )
            semantic_decision = policy_outcome.value
            if semantic_decision.source == "policy_semantic_gate":
                pre_response_frames.append(
                    self._append_audit_frame(
                        auth,
                        run,
                        MODEL_ACTION_PROPOSED,
                        {
                            "actor": "model",
                            "source": "policy_semantic_gate",
                            "status": "proposed",
                            "policy_intent": semantic_decision.policy_intent,
                            "target": semantic_decision.target,
                            "polarity": semantic_decision.polarity,
                            "confidence": semantic_decision.confidence,
                            "reason_code": semantic_decision.reason_code,
                            "risk_level": "candidate",
                            "safe_args_summary": {
                                "candidate_count": len(policy_candidates),
                                "candidate_rule_ids": list(semantic_decision.candidate_rule_ids),
                            },
                        },
                    )
                )
            semantic_blocked = should_block_semantic_policy(semantic_decision)
            pre_response_frames.append(
                self._append_audit_frame(
                    auth,
                    run,
                    MODEL_ACTION_VALIDATED,
                    {
                        "actor": "backend",
                        "source": "policy_semantic_gate",
                        "status": "rejected" if semantic_blocked else "allowed",
                        "policy_intent": semantic_decision.policy_intent,
                        "target": semantic_decision.target,
                        "polarity": semantic_decision.polarity,
                        "confidence": semantic_decision.confidence,
                        "reason_code": semantic_decision.reason_code,
                        "risk_level": "blocker" if semantic_blocked else "candidate",
                        "safe_args_summary": {
                            "candidate_count": len(policy_candidates),
                            "candidate_rule_ids": list(semantic_decision.candidate_rule_ids),
                            "decision_source": semantic_decision.source,
                        },
                    },
                )
            )
            if semantic_blocked:
                for frame in pre_response_frames:
                    yield frame
                pre_response_frames.clear()
                yield from self._chat_safety_blocked(
                    auth,
                    run,
                    semantic_policy_block_finding(semantic_decision, policy_candidates),
                    language=language,
                    audit_source="policy_semantic_gate",
                )
                return
        context_builder = ConversationContextBuilder(self.repository)
        artifact_kinds = _conversation_user_artifact_kinds(self.repository, auth, conversation_id, current_run_id=run.id)
        domain_precheck = (
            None
            if workflow_task_resume_payload is not None
            else precheck_domain_scope(message_content, artifact_kinds=artifact_kinds)
        )
        if domain_precheck is not None and not domain_precheck.allowed:
            yield from self._domain_scope_blocked(
                auth=auth,
                run=run,
                conversation_id=conversation_id,
                domain_scope=domain_precheck,
                language=language,
            )
            return
        system_prompt = _system_prompt(language, web_search=web_search)
        conversation_context = context_builder.build(
            auth=auth,
            conversation_id=conversation_id,
            current_message_id=current_message_id,
            current_user_message=message_content,
            system_prompt=system_prompt,
        )
        backtest_live_context = _latest_backtest_live_context(self.repository, auth, conversation_id)
        if backtest_live_context is not None:
            conversation_context = replace(
                conversation_context,
                messages=_insert_system_context(conversation_context.messages, backtest_live_context),
                estimated_input_tokens=conversation_context.estimated_input_tokens
                + _token_estimate(backtest_live_context),
                prior_context_text=f"{conversation_context.prior_context_text}\n{backtest_live_context}",
            )
        durable_artifact_context = _latest_strategy_artifact_context_text(
            self.repository,
            auth,
            conversation_id,
        )
        if durable_artifact_context:
            conversation_context = replace(
                conversation_context,
                messages=_insert_system_context(
                    conversation_context.messages,
                    f"<current_strategy_artifact>\n{durable_artifact_context}\n</current_strategy_artifact>",
                ),
                estimated_input_tokens=conversation_context.estimated_input_tokens
                + _token_estimate(durable_artifact_context),
                prior_context_text=f"{conversation_context.prior_context_text}\n{durable_artifact_context}",
            )
        suggestion_context_text = conversation_context.prior_context_text
        context_guard_message = _missing_current_context_message(
            message_content,
            conversation_context.prior_context_text,
            language,
        )
        workflow_task_values: dict[str, Any] = {}
        if workflow_task_resume_payload is not None:
            workflow_task_values = _workflow_task_values_for_conversation(
                self.repository,
                auth,
                conversation_id,
                STRATEGY_BOT_WORKFLOW_ID,
            )
            payload_values = workflow_task_resume_payload.get("values")
            if isinstance(payload_values, dict):
                workflow_task_values.update(payload_values)
            workflow_task_context_text = _workflow_task_values_context_text(workflow_task_values)
            if workflow_task_context_text:
                workflow_task_context_block = (
                    "<workflow_task_values>\n"
                    f"{workflow_task_context_text}\n"
                    "</workflow_task_values>"
                )
                conversation_context = replace(
                    conversation_context,
                    messages=_insert_system_context(conversation_context.messages, workflow_task_context_block),
                    estimated_input_tokens=conversation_context.estimated_input_tokens
                    + _token_estimate(workflow_task_context_block),
                    prior_context_text=f"{conversation_context.prior_context_text}\n{workflow_task_context_text}",
                )
                suggestion_context_text = conversation_context.prior_context_text
        chat_action_evaluation: ActionRegistryEvaluation | None = None
        if workflow_task_resume_payload is not None:
            context_guard_message = None
            chat_decision = _workflow_task_resume_chat_decision(workflow_task_resume_payload)
        elif context_guard_message is not None:
            chat_decision = ChatIntentDecision(
                response_intent="artifact_generation",
                action="ask_clarification",
                model_stage=MODEL_STAGE_PINE_CODE_GENERATION,
                confidence=1.0,
                source="deterministic_safety",
                domain_scope=(domain_precheck.scope if domain_precheck is not None else "context_followup"),
                missing_inputs=("current_strategy_context",),
                reasons=("The user referenced current strategy context but none is available.",),
            )
        else:
            classifier_name = "chat_intent_decision"
            chat_regex_evidence = _chat_regex_evidence(message_content)
            chat_action_evaluation = action_registry_cache.get(
                artifact_kinds=artifact_kinds,
                context_text=f"{suggestion_context_text}\n{message_content}",
                web_search=web_search,
            )
            chat_prompt_summary = {
                "message_chars": len(message_content or ""),
                "artifact_kind_count": len(artifact_kinds),
                "action_count": len(chat_action_evaluation.payload),
                "regex_evidence_count": sum(1 for value in chat_regex_evidence.values() if value),
            }
            chat_capture_client = ClassifierRouteCaptureClient(
                self.client,
                stage=MODEL_STAGE_WORKFLOW_FAST,
                route_timeout_seconds=_classifier_route_timeout_seconds(classifier_name),
                auth=auth,
            )
            pre_response_frames.append(
                self._classifier_started_frame(
                    auth,
                    run,
                    classifier_name=classifier_name,
                    stage=MODEL_STAGE_WORKFLOW_FAST,
                    timeout_seconds=_classifier_timeout_seconds(classifier_name),
                    prompt_summary=chat_prompt_summary,
                )
            )
            chat_outcome = ChatIntentDecisionPlanner(chat_capture_client).decide(
                message_content,
                context_text=suggestion_context_text,
                artifact_kinds=artifact_kinds,
                web_search=web_search,
                language=language,
                domain_scope_hint=domain_precheck.scope if domain_precheck is not None else None,
                log_context={
                    "conversation_id": conversation_id,
                    "request_id": run.request_id,
                    "run_id": run.id,
                    "trace_id": run.trace_id,
                },
                return_outcome=True,
                action_evaluation=chat_action_evaluation,
            )
            pre_response_frames.extend(
                self._classifier_result_frames(
                    auth,
                    run,
                    classifier_name=classifier_name,
                    stage=MODEL_STAGE_WORKFLOW_FAST,
                    capture_client=chat_capture_client,
                    outcome=chat_outcome,
                    prompt_summary=chat_prompt_summary,
                )
            )
            chat_decision = chat_outcome.value
            if chat_outcome.status == "timeout":
                workflow_fallback_decision = _workflow_kickoff_fallback_decision(
                    message_content,
                    source=WORKFLOW_TIMEOUT_FALLBACK_SOURCE,
                )
                if workflow_fallback_decision is not None:
                    chat_decision = workflow_fallback_decision
            elif chat_outcome.status == "failed" or (
                chat_decision.source == "fallback" and not chat_decision.workflow_intent
            ):
                workflow_fallback_decision = _workflow_kickoff_fallback_decision(
                    message_content,
                    source=WORKFLOW_CLASSIFIER_FALLBACK_SOURCE,
                )
                if workflow_fallback_decision is not None:
                    chat_decision = workflow_fallback_decision
        if chat_decision.source == "llm":
            yield self._append_audit_frame(
                auth,
                run,
                MODEL_ACTION_PROPOSED,
                {
                    "actor": "model",
                    "source": "classifier",
                    "status": "proposed",
                    "intent_id": chat_decision.response_intent,
                    "decision": chat_decision.action,
                    "tool_id": chat_decision.tool_id,
                    "confidence": chat_decision.confidence,
                    "reason_code": chat_decision.domain_scope,
                    "safe_args_summary": {
                        "domain_scope": chat_decision.domain_scope,
                        "workflow_intent": chat_decision.workflow_intent,
                        "model_stage": chat_decision.model_stage,
                        "used_signals": list(chat_decision.used_signals),
                    },
                },
            )
        selected_action_plan = _selected_action_plan(
            selected_action_payload,
            repository=self.repository,
            artifact_store=self.artifact_store,
            auth=auth,
            conversation_id=conversation_id,
            message_content=message_content,
            artifact_kinds=artifact_kinds,
            context_text=f"{suggestion_context_text}\n{message_content}",
            web_search=web_search,
            action_evaluation=chat_action_evaluation,
        )
        chat_decision = _chat_decision_for_selected_action(chat_decision, selected_action_plan)
        if should_block_domain_scope(chat_decision.domain_scope, chat_decision.confidence):
            yield from self._domain_scope_blocked(
                auth=auth,
                run=run,
                conversation_id=conversation_id,
                domain_scope=DomainScopeDecision(
                    False,
                    chat_decision.domain_scope,
                    f"{chat_decision.source}_semantic_off_topic",
                    chat_decision.confidence,
                ),
                language=language,
            )
            return
        response_intent = chat_decision.response_intent
        budget.auto_chain_allowed = chat_decision.should_start_auto_chain()
        budget.auto_chain_source = chat_decision.source if budget.auto_chain_allowed else "none"
        response_state: dict[str, Any] = {
            "market_snapshot_emitted": False,
            "market_data_emitted": False,
            "sources": [],
        }
        market_snapshot = (
            self.market_data_gateway.snapshot(
                _market_symbol_from_text(message_content),
                include_series=_market_snapshot_needs_series(message_content),
                tier=auth.user_tier,
            )
            if response_intent == "market_snapshot" and self.market_data_gateway is not None
            else None
        )
        market_context = market_data_context(market_snapshot)
        if market_context is not None:
            conversation_context = replace(
                conversation_context,
                messages=_insert_system_context(conversation_context.messages, f"<market_data>\n{market_context}\n</market_data>"),
                estimated_input_tokens=conversation_context.estimated_input_tokens + _token_estimate(market_context),
                prior_context_text=f"{conversation_context.prior_context_text}\n{market_context}",
            )
            suggestion_context_text = conversation_context.prior_context_text
        artifact_available = response_intent in {
            "artifact_generation",
            "backtest_preview",
            "pine_generation",
            "strategy_building",
        } and bool(artifact_kinds)
        should_run_action_planner = (
            _action_planner_enabled()
            and context_guard_message is None
            and workflow_task_resume_payload is None
            and selected_action_plan is None
            and not _is_classifier_fallback_source(chat_decision.source)
        )
        if selected_action_plan is not None:
            action_plan = selected_action_plan
        elif should_run_action_planner:
            classifier_name = "action_planner"
            action_planner_context_text = f"{suggestion_context_text}\n{message_content}"
            action_planner_evaluation = action_registry_cache.get(
                artifact_kinds=artifact_kinds,
                context_text=action_planner_context_text,
                web_search=web_search,
            )
            action_prompt_summary = {
                "message_chars": len(message_content or ""),
                "artifact_kind_count": len(artifact_kinds),
                "action_count": len(action_planner_evaluation.payload),
                "regex_evidence_count": sum(1 for value in _chat_regex_evidence(message_content).values() if value),
            }
            action_capture_client = ClassifierRouteCaptureClient(
                self.client,
                stage=MODEL_STAGE_WORKFLOW_FAST,
                route_timeout_seconds=_classifier_route_timeout_seconds(classifier_name),
                auth=auth,
            )
            pre_response_frames.append(
                self._classifier_started_frame(
                    auth,
                    run,
                    classifier_name=classifier_name,
                    stage=MODEL_STAGE_WORKFLOW_FAST,
                    timeout_seconds=_classifier_timeout_seconds(classifier_name),
                    prompt_summary=action_prompt_summary,
                )
            )
            action_outcome = ActionPlanner(action_capture_client).plan(
                message_content,
                response_intent=response_intent,
                context_text=suggestion_context_text,
                artifact_kinds=artifact_kinds,
                web_search=web_search,
                regex_evidence={key: value for key, value in _chat_regex_evidence(message_content).items()},
                log_context={
                    "conversation_id": conversation_id,
                    "request_id": run.request_id,
                    "run_id": run.id,
                    "trace_id": run.trace_id,
                },
                return_outcome=True,
                action_evaluation=action_planner_evaluation,
            )
            pre_response_frames.extend(
                self._classifier_result_frames(
                    auth,
                    run,
                    classifier_name=classifier_name,
                    stage=MODEL_STAGE_WORKFLOW_FAST,
                    capture_client=action_capture_client,
                    outcome=action_outcome,
                    prompt_summary=action_prompt_summary,
                )
            )
            action_plan = action_outcome.value
        else:
            action_plan = _workflow_task_resume_action_plan(
                workflow_task_resume_payload,
                workflow_task_values,
            ) or ActionPlanDecision("answer", "none", 0.0, "none")
        workflow_payload = _maybe_strategy_bot_workflow_payload(
            repository=self.repository,
            auth=auth,
            conversation_id=conversation_id,
            chat_decision=chat_decision,
            message_content=message_content,
            context_text=suggestion_context_text,
            artifact_kinds=artifact_kinds,
            structured_strategy_values=workflow_task_values,
            completed_strategy_spec=(
                workflow_task_resume_payload is not None
                and workflow_task_resume_payload.get("task_template_id") == STRATEGY_SPEC_NEXT_STEP_TASK_ID
            ),
        )
        suggestions_action_evaluation = action_registry_cache.get(
            artifact_kinds=artifact_kinds,
            context_text=suggestion_context_text,
            web_search=web_search,
        )
        suggestions_payload = _suggestions_payload(
            response_intent=response_intent,
            message_content=message_content,
            context_text=suggestion_context_text,
            language=language,
            artifact_available=artifact_available,
            artifact_kinds=artifact_kinds,
            web_search=web_search,
            action_plan=action_plan,
            workflow_enabled=workflow_payload is not None,
            action_evaluation=suggestions_action_evaluation,
        )
        if action_plan.source != "none":
            agent_log(
                logger,
                "info",
                "agent.action_plan",
                component="llm_orchestrator",
                confidence=round(max(0.0, min(1.0, action_plan.confidence)), 3),
                conversation_id=conversation_id,
                decision=action_plan.decision,
                intent_id=action_plan.intent_id,
                request_id=run.request_id,
                run_id=run.id,
                source=action_plan.source,
                tool_id=action_plan.tool_id,
                trace_id=run.trace_id,
            )
            self.repository.append_run_event(auth, run.id, "chat.action_plan", action_plan.payload())
        self.repository.append_run_event(
            auth,
            run.id,
            "context.built",
            {
                "history_message_count": conversation_context.history_message_count,
                "summary_used": conversation_context.summary_used,
                "estimated_input_tokens": conversation_context.estimated_input_tokens,
                "truncated": conversation_context.truncated,
                "web_search": web_search,
                "backtest_live_status_included": backtest_live_context is not None,
            },
        )
        try:
            for frame in pre_response_frames:
                yield frame
            pre_response_frames.clear()
            yield self._append_frame(
                auth,
                run,
                "chat.response_intent",
                chat_decision.payload(),
            )
            yield self._append_audit_frame(
                auth,
                run,
                MODEL_ACTION_VALIDATED,
                {
                    "actor": "backend",
                    "source": "classifier",
                    "status": "allowed",
                    "intent_id": chat_decision.response_intent,
                    "decision": chat_decision.action,
                    "tool_id": chat_decision.tool_id,
                    "confidence": chat_decision.confidence,
                    "reason_code": chat_decision.source,
                    "missing_fields": list(chat_decision.missing_inputs),
                    "safe_args_summary": {
                        "domain_scope": chat_decision.domain_scope,
                        "workflow_intent": chat_decision.workflow_intent,
                        "model_stage": chat_decision.model_stage,
                        "auto_chain": chat_decision.auto_chain,
                        "current_context_required": chat_decision.current_context_required,
                    },
                },
            )
            if action_plan.source != "none":
                yield self._append_audit_frame(
                    auth,
                    run,
                    MODEL_ACTION_PROPOSED,
                    {
                        "actor": "user" if action_plan.source == "selected_action" else "model",
                        "source": action_plan.source if action_plan.source == "selected_action" else "planner",
                        "status": "proposed",
                        "intent_id": action_plan.intent_id,
                        "decision": action_plan.decision,
                        "tool_id": action_plan.tool_id,
                        "confidence": action_plan.confidence,
                        "reason_code": action_plan.source,
                        "suggested_actions": list(action_plan.suggested_actions),
                        "safe_args_summary": action_plan.arguments or {},
                    },
                )
            if workflow_payload is not None:
                workflow_payload = self._sync_workflow_tasks(
                    auth,
                    run,
                    workflow_payload,
                    language=language,
                    user_prompt=message_content,
                )
                yield self._append_frame(auth, run, STRATEGY_WORKFLOW_EVENT, workflow_payload)
                if _workflow_payload_has_blocking_user_task(workflow_payload):
                    yield self._append_frame(auth, run, SUGGESTIONS_EVENT, suggestions_payload)
                    terminal_status = "completed"
                    completed = self.repository.set_run_status(auth, run.id, terminal_status)
                    append_stage_event(
                        self.repository,
                        auth,
                        completed or run,
                        "response_finalization",
                        0,
                        status=terminal_status,
                    )
                    yield self._append_frame(
                        auth,
                        completed or run,
                        "run.completed",
                        {"status": terminal_status, "source": "workflow_task_prompt"},
                    )
                    return
            if market_snapshot is not None:
                response_state["market_snapshot_emitted"] = True
                response_state["market_data_emitted"] = True
                response_state["market_snapshot_sources"] = [
                    source.to_payload()
                    for source in (
                        (market_snapshot.quote.source,) if market_snapshot.quote.source is not None else ()
                    )
                ]
                agent_log(
                    logger,
                    "info",
                    "market.snapshot.emitted",
                    component="llm_orchestrator",
                    conversation_id=conversation_id,
                    point_count=len(market_snapshot.points),
                    provider=market_snapshot.quote.provider,
                    request_id=run.request_id,
                    run_id=run.id,
                    symbol=market_snapshot.quote.symbol,
                    trace_id=run.trace_id,
                )
                yield self._append_frame(auth, run, "market_data.snapshot", {"provider": market_snapshot.quote.provider})
                yield self._append_frame(
                    auth,
                    run,
                    "chat.market_snapshot",
                    market_snapshot.to_chat_payload(),
            )
            yield self._append_frame(auth, run, SUGGESTIONS_EVENT, suggestions_payload)
            yield self._safe_reasoning_frame(auth, run, "context", language)
            if context_guard_message is not None:
                yield self._safe_reasoning_frame(auth, run, "finalizing", language)
                self.repository.create_message(auth, conversation_id, context_guard_message, role="assistant")
                yield self._append_frame(
                    auth,
                    run,
                    LLM_EVENT_MESSAGE_DELTA,
                    {"text": context_guard_message, "compact": True, "source": "missing_current_strategy_context"},
                )
                terminal_status = "completed"
                completed = self.repository.set_run_status(auth, run.id, terminal_status)
                append_stage_event(self.repository, auth, completed or run, "response_finalization", 0, status=terminal_status)
                yield self._append_frame(auth, completed or run, "run.completed", {"status": terminal_status})
                return

            artifact_evidence = _latest_artifact_evidence_followup(
                self.repository,
                auth,
                conversation_id,
                message_content,
                language=language,
            )
            if artifact_evidence is not None:
                for event_type, payload in artifact_evidence["events"]:
                    yield self._append_frame(auth, run, event_type, payload)
                evidence_text = _sanitize_user_facing_model_text(redact_text(artifact_evidence["text"]))
                self.repository.create_message(auth, conversation_id, evidence_text, role="assistant")
                yield self._safe_reasoning_frame(auth, run, "finalizing", language)
                yield self._append_frame(
                    auth,
                    run,
                    LLM_EVENT_MESSAGE_DELTA,
                    {"text": evidence_text, "compact": True, "source": "artifact_evidence_followup"},
                )
                terminal_status = "completed"
                completed = self.repository.set_run_status(auth, run.id, terminal_status)
                append_stage_event(self.repository, auth, completed or run, "response_finalization", 0, status=terminal_status)
                yield self._append_frame(auth, completed or run, "run.completed", {"status": terminal_status})
                self._maybe_compact_conversation(auth, conversation_id, run.id, language=language)
                return

            if should_use_bounded_scout(message_content, response_intent=response_intent):
                yield from self._run_bounded_scout(
                    auth=auth,
                    run=run,
                    conversation_id=conversation_id,
                    conversation_context=conversation_context,
                    language=language,
                )
                return

            planned_tool_call = _direct_action_plan_tool_args(
                action_plan,
                artifact_kinds=artifact_kinds,
                context_text=suggestion_context_text,
                web_search=web_search,
            )
            direct_tool_name: str | None = None
            direct_tool_args: dict[str, Any] | None = None
            if planned_tool_call is not None:
                direct_tool_name, direct_tool_args = planned_tool_call
            if direct_tool_name is not None and direct_tool_args is not None:
                yield from self._execute_tool_call(
                    auth,
                    run,
                    direct_tool_name,
                    direct_tool_args,
                    budget,
                    response_intent=response_intent,
                    user_message=message_content,
                    context_text=suggestion_context_text,
                    web_search=web_search,
                    language=language,
                    workflow_intent=chat_decision.workflow_intent,
                )
                if not budget.blocked:
                    tool_only_text = _tool_only_success_message(
                        budget.completed_tool_ids,
                        language,
                        tool_results=budget.completed_tool_results,
                    )
                    self.repository.create_message(auth, conversation_id, tool_only_text, role="assistant")
                    yield self._safe_reasoning_frame(auth, run, "finalizing", language)
                    yield self._append_frame(
                        auth,
                        run,
                        LLM_EVENT_MESSAGE_DELTA,
                        {"text": tool_only_text, "compact": True, "source": "direct_backtest_trade_query"},
                    )
                terminal_status = "blocked" if budget.blocked else "completed"
                completed = self.repository.set_run_status(auth, run.id, terminal_status)
                append_stage_event(self.repository, auth, completed or run, "response_finalization", 0, status=terminal_status)
                if workflow_task_resume_payload is not None:
                    continuation_event = (
                        "workflow.continuation.completed"
                        if terminal_status == "completed"
                        else "workflow.continuation.failed"
                    )
                    yield self._append_frame(
                        auth,
                        completed or run,
                        continuation_event,
                        {
                            **workflow_task_resume_payload,
                            "source": "workflow_task_resume",
                            "status": terminal_status,
                        },
                    )
                yield self._append_frame(auth, completed or run, "run.completed", {"status": terminal_status})
                if terminal_status == "completed":
                    self._maybe_compact_conversation(auth, conversation_id, run.id, language=language)
                return

            active_tools = (
                []
                if market_snapshot is not None or workflow_task_resume_payload is not None
                else _provider_tools_for_web_search(
                    web_search,
                    message_content,
                    response_intent=response_intent,
                    current_context_required=chat_decision.current_context_required,
                    decision_source=chat_decision.source,
                )
            )
            model_stage = _model_stage_for_chat(
                message_content,
                response_intent=response_intent,
                active_tools=active_tools,
                decision_model_stage=chat_decision.model_stage,
            )
            model_timer = StageTimer()
            self.security_controls.check_model_call(auth, model=self.client.model)
            append_stage_started_event(self.repository, auth, run, "model")
            yield self._append_frame(
                auth,
                run,
                "provider.started",
                {
                    "mode": "agent",
                    "model": self.client.model,
                    "tier": auth.user_tier,
                    "model_tier": auth.user_tier,
                    "model_stage": model_stage,
                    "web_search": web_search,
                    "web_search_enabled": _has_web_search_tool(active_tools),
                },
            )
            yield self._safe_reasoning_frame(auth, run, "model", language)
            chain_result = None
            if _should_run_strategy_prompt_chain(
                message_content,
                response_intent=response_intent,
                active_tools=active_tools,
            ):
                chain_result = yield from self._run_strategy_prompt_chain(
                    auth=auth,
                    run=run,
                    budget=budget,
                    message_content=message_content,
                    context_text=suggestion_context_text,
                    response_intent=response_intent,
                    language=language,
                )
            if chain_result is not None and not budget.blocked:
                chain_text = _sanitize_user_facing_model_text(redact_text(chain_result.final_text))
                finding = _first_policy_finding(
                    surface="agent.chat.output",
                    payload=chain_text,
                    evidence_level=EVIDENCE_STRATEGY_IDEA,
                    response_intent=response_intent,
                )
                if finding is not None:
                    budget.blocked = True
                    yield from self._policy_blocked(auth, run, None, finding, language=language)
                else:
                    if self.artifact_store is None:
                        raise RuntimeError("artifact_store_required_for_prompt_chain_artifact")
                    pre_artifact_events = self.repository.list_run_events(auth, run.id) or []
                    pre_artifact_sequence = pre_artifact_events[-1].sequence if pre_artifact_events else 0
                    artifact_result = persist_generated_pine_artifact(
                        ToolExecutionContext(
                            repository=self.repository,
                            artifact_store=self.artifact_store,
                            auth=auth,
                            run=run,
                        ),
                        strategy_spec=chain_result.strategy_spec,
                        pine_code=chain_result.pine_code,
                        source="llm_orchestrator.prompt_chain",
                        validation_source="llm_orchestrator.prompt_chain.static_validation",
                        review_source="llm_orchestrator.prompt_chain.static_review",
                    )
                    yield from self._stream_existing_run_events_after(auth, run, pre_artifact_sequence)
                    artifact_id = _safe_string(artifact_result.get("artifact_id"))
                    if artifact_id:
                        budget.completed_tool_results.append(
                            {
                                "tool_id": "prompt_chain_pine_generation",
                                "arguments": {"strategy_spec": chain_result.strategy_spec},
                                "output": compact_tool_output(artifact_result),
                            }
                        )
                    artifact_kinds.update(_current_run_user_artifact_kinds(self.repository, auth, run.id))
                    accumulated_text.append(chain_text)
            if chain_result is None and not budget.blocked:
                for event in _stream_client(
                    self.client,
                    messages=conversation_context.messages,
                    tools=active_tools,
                    routing_context={"auth": auth, "user_tier": auth.user_tier, "stage": model_stage},
                ):
                    yield from self._handle_client_event(
                        auth,
                        run,
                        event,
                        budget,
                        accumulated_text,
                        output_surface="agent.chat.output",
                        response_intent=response_intent,
                        response_state=response_state,
                        stream_transient_delta=True,
                        user_message=message_content,
                        context_text=suggestion_context_text,
                        web_search=web_search,
                        language=language,
                        workflow_intent=chat_decision.workflow_intent,
                    )
                    if budget.blocked:
                        break
            append_stage_event(self.repository, auth, run, "model", model_timer.elapsed_ms())
            completed_strategy_spec_response = False
            if accumulated_text and not budget.blocked:
                final_text = "".join(accumulated_text)
                final_text = _maybe_backtest_summary_response(
                    final_text,
                    budget.completed_tool_ids,
                    budget.completed_tool_results,
                    language,
                )
                final_text = _maybe_backtest_trades_response(
                    final_text,
                    budget.completed_tool_ids,
                    budget.completed_tool_results,
                    language,
                )
                final_text = _maybe_auto_chain_final_response(
                    final_text,
                    budget.completed_tool_ids,
                    budget.completed_tool_results,
                    language,
                    auto_chain_started=budget.auto_chain_started,
                )
                if response_intent == "market_snapshot" and not response_state.get("market_snapshot_emitted"):
                    final_text = _market_snapshot_source_required_message(language)
                if (
                    response_intent == "market_snapshot"
                    and response_state.get("market_snapshot_emitted")
                    and not response_state.get("market_data_emitted")
                ):
                    price = _market_price_from_text(final_text)
                    sources = response_state.get("market_snapshot_sources")
                    if price and isinstance(sources, list):
                        yield self._append_frame(
                            auth,
                            run,
                            "chat.market_snapshot",
                            _market_snapshot_payload(
                                message_content,
                                sources,
                                language=language,
                                price=price,
                            ),
                        )
                self.repository.create_message(
                    auth,
                    conversation_id,
                    final_text,
                    role="assistant",
                )
                yield self._safe_reasoning_frame(auth, run, "finalizing", language)
                yield self._append_frame(
                    auth,
                    run,
                    LLM_EVENT_MESSAGE_DELTA,
                    {"text": final_text, "compact": True},
                )
                completed_strategy_spec_response = (
                    workflow_task_resume_payload is not None
                    and workflow_task_resume_payload.get("task_template_id") == "collect_strategy_inputs"
                    and response_intent == "strategy_building"
                )
            elif budget.executed_tool_calls > 0 and not budget.blocked:
                tool_only_text = budget.auto_chain_failure_text or _tool_only_success_message(
                    budget.completed_tool_ids,
                    language,
                    tool_results=budget.completed_tool_results,
                )
                self.repository.create_message(auth, conversation_id, tool_only_text, role="assistant")
                yield self._safe_reasoning_frame(auth, run, "finalizing", language)
                yield self._append_frame(
                    auth,
                    run,
                    LLM_EVENT_MESSAGE_DELTA,
                    {"text": tool_only_text, "compact": True, "source": "tool_only_success_fallback"},
                )
            if completed_strategy_spec_response:
                advanced_workflow_payload = _maybe_strategy_bot_workflow_payload(
                    repository=self.repository,
                    auth=auth,
                    conversation_id=conversation_id,
                    chat_decision=chat_decision,
                    message_content=message_content,
                    context_text=suggestion_context_text,
                    artifact_kinds=artifact_kinds,
                    structured_strategy_values=workflow_task_values,
                    completed_strategy_spec=True,
                )
                if advanced_workflow_payload is not None:
                    advanced_workflow_payload = self._sync_workflow_tasks(
                        auth,
                        run,
                        advanced_workflow_payload,
                        language=language,
                        user_prompt=message_content,
                    )
                    yield self._append_frame(
                        auth,
                        run,
                        STRATEGY_WORKFLOW_EVENT,
                        advanced_workflow_payload,
                    )
            terminal_status = "blocked" if budget.blocked else "completed"
            completed = self.repository.set_run_status(auth, run.id, terminal_status)
            append_stage_event(self.repository, auth, completed or run, "response_finalization", 0, status=terminal_status)
            if workflow_task_resume_payload is not None:
                continuation_event = (
                    "workflow.continuation.completed"
                    if terminal_status == "completed"
                    else "workflow.continuation.failed"
                )
                yield self._append_frame(
                    auth,
                    completed or run,
                    continuation_event,
                    {
                        **workflow_task_resume_payload,
                        "source": "workflow_task_resume",
                        "status": terminal_status,
                    },
                )
            yield self._append_frame(auth, completed or run, "run.completed", {"status": terminal_status})
            if terminal_status == "completed":
                self._maybe_compact_conversation(auth, conversation_id, run.id, language=language)
        except GeneratorExit:
            if terminal_status is None:
                cancelled = self.repository.set_run_status(auth, run.id, "cancelled")
                self.repository.append_run_event(
                    auth,
                    run.id,
                    "run.cancelled",
                    {"status": "cancelled", "reason": "client_disconnected"},
                )
                if workflow_task_resume_payload is not None:
                    self.repository.append_run_event(
                        auth,
                        run.id,
                        "workflow.continuation.failed",
                        _redact_event_payload(
                            "workflow.continuation.failed",
                            {
                                **workflow_task_resume_payload,
                                "source": "workflow_task_resume",
                                "status": "cancelled",
                                "reason": "client_disconnected",
                            },
                        ),
                    )
                append_stage_event(self.repository, auth, cancelled or run, "model", 0, status="cancelled")
            raise
        except Exception as exc:
            terminal_status = "failed"
            failed = self.repository.set_run_status(auth, run.id, "failed")
            failure_payload = run_failed_payload(exc)
            failure_text = _failure_assistant_message(failure_payload, language)
            self.repository.create_message(auth, conversation_id, failure_text, role="assistant")
            log_run_exception(exc, run_id=run.id, trace_id=run.trace_id)
            append_stage_event(self.repository, auth, failed or run, "model", 0, status="failed")
            yield self._append_frame(
                auth,
                failed or run,
                LLM_EVENT_MESSAGE_DELTA,
                {"text": failure_text, "compact": True},
            )
            if workflow_task_resume_payload is not None:
                yield self._append_frame(
                    auth,
                    failed or run,
                    "workflow.continuation.failed",
                    {
                        **workflow_task_resume_payload,
                        "source": "workflow_task_resume",
                        "status": "failed",
                    },
                )
            yield self._append_frame(
                auth,
                failed or run,
                "run.failed",
                {**failure_payload, "assistant_message_persisted": True},
            )

    def _maybe_compact_conversation(self, auth: AuthContext, conversation_id: str, run_id: str, *, language: str) -> None:
        context_builder = ConversationContextBuilder(self.repository)
        if not context_builder.should_compact(auth=auth, conversation_id=conversation_id):
            return
        summary_messages, covered_message_id, estimated_tokens = context_builder.build_summary_messages(
            auth=auth,
            conversation_id=conversation_id,
            language=language,
        )
        if not summary_messages or covered_message_id is None:
            return
        chunks: list[str] = []
        try:
            for event in _stream_client(
                self.client,
                messages=summary_messages,
                tools=[],
                routing_context={"auth": auth, "user_tier": auth.user_tier, "stage": MODEL_STAGE_BALANCED_REVIEW},
            ):
                if event.type == LLM_EVENT_MESSAGE_DELTA and event.text:
                    chunks.append(event.text)
            summary = " ".join("".join(chunks).split()).strip()
            if not summary:
                return
            memory = self.repository.upsert_conversation_memory(
                auth,
                conversation_id,
                summary=summary,
                covered_message_id=covered_message_id,
                estimated_tokens=estimated_tokens,
            )
            self.repository.append_run_event(
                auth,
                run_id,
                "context.compacted",
                {
                    "summary_version": memory.summary_version if memory is not None else None,
                    "covered_message_id": covered_message_id,
                    "estimated_tokens": estimated_tokens,
                },
            )
        except Exception as exc:
            self.repository.append_run_event(
                auth,
                run_id,
                "context.compaction_skipped",
                {"error": exc.__class__.__name__, "message": redact_text(str(exc))},
            )

    def _run_strategy_prompt_chain(
        self,
        *,
        auth: AuthContext,
        run: AssistantRunRecord,
        budget: RunBudget,
        message_content: str,
        context_text: str,
        response_intent: str,
        language: str,
    ):
        chain_timer = StageTimer()
        context_packet = _strategy_prompt_chain_initial_packet(
            message_content,
            context_text=context_text,
            response_intent=response_intent,
            language=language,
        )

        def prompt_chain_frame(event_type: str, **payload_fields: Any):
            return self._append_frame(
                auth,
                run,
                event_type,
                _prompt_chain_event_payload(run, **payload_fields),
            )

        yield prompt_chain_frame(
            PROMPT_CHAIN_STARTED_EVENT,
            status="started",
            response_intent=response_intent,
            stages=list(STRATEGY_PROMPT_CHAIN_STAGES),
        )
        stage_outputs: dict[str, dict[str, Any]] = {}
        for stage in STRATEGY_PROMPT_CHAIN_STAGES:
            chunks: list[str] = []
            provider_route: str | None = None
            stage_timer = StageTimer()
            stage_usage = {"input_tokens": 0, "output_tokens": 0}

            def stage_prompt_chain_frame(event_type: str, **payload_fields: Any):
                return prompt_chain_frame(
                    event_type,
                    stage=stage,
                    model_stage=stage,
                    provider_route=provider_route,
                    latency_ms=stage_timer.elapsed_ms(),
                    usage=_prompt_chain_usage(stage_usage),
                    **payload_fields,
                )

            stage_context = _strategy_prompt_chain_stage_context(stage, context_packet)
            messages = prompt_stage_messages(
                stage,
                stage_context,
                conservative_sizing_guidance=STRATEGY_PROMPT_CHAIN_SIZING_GUIDANCE,
                repair_iteration=None,
            )
            heartbeat_phase = "artifact" if stage == STAGE_PINE_CODE_GENERATION else "strategy_spec"
            heartbeat_step = "generate_pine" if stage == STAGE_PINE_CODE_GENERATION else "draft_strategy_spec"
            try:
                self.security_controls.check_model_call(auth, model=self.client.model)
                for event in _stream_client(
                    self.client,
                    messages=messages,
                    tools=[],
                    routing_context={
                        "auth": auth,
                        "user_tier": auth.user_tier,
                        "stage": stage,
                        "route_timeout_seconds": _prompt_chain_route_timeout_seconds(),
                        "route_keepalive_seconds": _prompt_chain_route_keepalive_seconds(),
                        "hard_route_timeout": True,
                    },
                ):
                    if event.type == LLM_EVENT_MESSAGE_DELTA and event.text:
                        chunks.append(event.text)
                        continue
                    if event.type == PROVIDER_KEEPALIVE_EVENT:
                        yield self._safe_reasoning_frame(
                            auth,
                            run,
                            heartbeat_phase,
                            language,
                            workflow_step=heartbeat_step,
                        )
                        continue
                    if event.type == PROVIDER_ROUTE_EVENT:
                        route_payload = event.arguments or {}
                        provider_route = _safe_string(route_payload.get("provider_route"))
                        for failure in _prompt_chain_timeout_failures(route_payload):
                            yield stage_prompt_chain_frame(
                                PROMPT_CHAIN_ROUTE_TIMEOUT_EVENT,
                                status="fallback",
                                handoff_status="not_evaluated",
                                fallback_reason="route_timeout",
                                failed_provider_route=failure.get("provider_route"),
                                error_class=failure.get("error"),
                            )
                        if route_payload.get("fallback_used") is True:
                            yield self._safe_reasoning_frame(
                                auth,
                                run,
                                heartbeat_phase,
                                language,
                                workflow_step=heartbeat_step,
                            )
                        yield self._append_frame(auth, run, PROVIDER_ROUTE_EVENT, event.arguments or {})
                        continue
                    if event.type == LLM_EVENT_USAGE:
                        budget.add_usage(event.input_tokens, event.output_tokens)
                        stage_usage["input_tokens"] += event.input_tokens
                        stage_usage["output_tokens"] += event.output_tokens
                        model_metadata = _model_metadata_from_event(event)
                        self.repository.create_usage_ledger(
                            auth,
                            run_id=run.id,
                            model=event.model or self.client.model,
                            tool_id=None,
                            input_tokens=event.input_tokens,
                            output_tokens=event.output_tokens,
                        )
                        yield self._append_frame(
                            auth,
                            run,
                            "model.usage",
                            _usage_payload(
                                event.model or self.client.model,
                                event.input_tokens,
                                event.output_tokens,
                                metadata=model_metadata,
                            ),
                        )
                        try:
                            budget.check_usage()
                            self.security_controls.check_usage_budget(
                                total_tokens=budget.input_tokens + budget.output_tokens,
                                output_tokens=budget.output_tokens,
                            )
                        except BudgetExceeded as exc:
                            budget.blocked = True
                            yield stage_prompt_chain_frame(
                                PROMPT_CHAIN_FAILED_EVENT,
                                status="blocked",
                                handoff_status="not_evaluated",
                                error_class=exc.__class__.__name__,
                            )
                            yield from self._policy_blocked(auth, run, None, budget_policy_finding(exc), language=language)
                            return None
                        continue
                    if event.type == LLM_EVENT_SOURCES:
                        continue
                    agent_log(
                        logger,
                        "warn",
                        "strategy.prompt_chain.unsupported_event",
                        component="llm_orchestrator",
                        event_type=event.type,
                        request_id=run.request_id,
                        run_id=run.id,
                        stage=stage,
                        trace_id=run.trace_id,
                    )
                    yield stage_prompt_chain_frame(
                        PROMPT_CHAIN_FALLBACK_EVENT,
                        status="fallback",
                        handoff_status="not_evaluated",
                        fallback_reason="unsupported_event",
                        error_class=event.type,
                    )
                    return None
            except SecurityControlError as exc:
                yield stage_prompt_chain_frame(
                    PROMPT_CHAIN_FAILED_EVENT,
                    status="failed",
                    handoff_status="not_evaluated",
                    error_class=exc.__class__.__name__,
                )
                raise
            except Exception as exc:
                agent_log(
                    logger,
                    "warn",
                    "strategy.prompt_chain.failed",
                    component="llm_orchestrator",
                    error=exc.__class__.__name__,
                    request_id=run.request_id,
                    run_id=run.id,
                    stage=stage,
                    trace_id=run.trace_id,
                )
                yield stage_prompt_chain_frame(
                    PROMPT_CHAIN_FAILED_EVENT,
                    status="failed",
                    handoff_status="not_evaluated",
                    error_class=exc.__class__.__name__,
                )
                return None
            payload = _parse_strategy_prompt_chain_stage_payload("".join(chunks), stage)
            if payload is None:
                retry_chunks: list[str] = []
                retry_messages = prompt_stage_messages(
                    stage,
                    stage_context,
                    conservative_sizing_guidance=STRATEGY_PROMPT_CHAIN_SIZING_GUIDANCE,
                    repair_iteration=1,
                )
                try:
                    self.security_controls.check_model_call(auth, model=self.client.model)
                    for event in _stream_client(
                        self.client,
                        messages=retry_messages,
                        tools=[],
                        routing_context={
                            "auth": auth,
                            "user_tier": auth.user_tier,
                            "stage": stage,
                            "route_timeout_seconds": _prompt_chain_route_timeout_seconds(),
                            "route_keepalive_seconds": _prompt_chain_route_keepalive_seconds(),
                            "hard_route_timeout": True,
                        },
                    ):
                        if event.type == LLM_EVENT_MESSAGE_DELTA and event.text:
                            retry_chunks.append(event.text)
                            continue
                        if event.type == PROVIDER_KEEPALIVE_EVENT:
                            yield self._safe_reasoning_frame(
                                auth,
                                run,
                                heartbeat_phase,
                                language,
                                workflow_step=heartbeat_step,
                            )
                            continue
                        if event.type == PROVIDER_ROUTE_EVENT:
                            route_payload = event.arguments or {}
                            provider_route = _safe_string(route_payload.get("provider_route"))
                            for failure in _prompt_chain_timeout_failures(route_payload):
                                yield stage_prompt_chain_frame(
                                    PROMPT_CHAIN_ROUTE_TIMEOUT_EVENT,
                                    status="fallback",
                                    handoff_status="not_evaluated",
                                    fallback_reason="route_timeout",
                                    failed_provider_route=failure.get("provider_route"),
                                    error_class=failure.get("error"),
                                )
                            if route_payload.get("fallback_used") is True:
                                yield self._safe_reasoning_frame(
                                    auth,
                                    run,
                                    heartbeat_phase,
                                    language,
                                    workflow_step=heartbeat_step,
                                )
                            yield self._append_frame(auth, run, PROVIDER_ROUTE_EVENT, event.arguments or {})
                            continue
                        if event.type == LLM_EVENT_USAGE:
                            budget.add_usage(event.input_tokens, event.output_tokens)
                            stage_usage["input_tokens"] += event.input_tokens
                            stage_usage["output_tokens"] += event.output_tokens
                            model_metadata = _model_metadata_from_event(event)
                            self.repository.create_usage_ledger(
                                auth,
                                run_id=run.id,
                                model=event.model or self.client.model,
                                tool_id=None,
                                input_tokens=event.input_tokens,
                                output_tokens=event.output_tokens,
                            )
                            yield self._append_frame(
                                auth,
                                run,
                                "model.usage",
                                _usage_payload(
                                    event.model or self.client.model,
                                    event.input_tokens,
                                    event.output_tokens,
                                    metadata=model_metadata,
                                ),
                            )
                            try:
                                budget.check_usage()
                                self.security_controls.check_usage_budget(
                                    total_tokens=budget.input_tokens + budget.output_tokens,
                                    output_tokens=budget.output_tokens,
                                )
                            except BudgetExceeded as exc:
                                budget.blocked = True
                                yield stage_prompt_chain_frame(
                                    PROMPT_CHAIN_FAILED_EVENT,
                                    status="blocked",
                                    handoff_status="not_evaluated",
                                    error_class=exc.__class__.__name__,
                                )
                                yield from self._policy_blocked(
                                    auth,
                                    run,
                                    None,
                                    budget_policy_finding(exc),
                                    language=language,
                                )
                                return None
                            continue
                        if event.type == LLM_EVENT_SOURCES:
                            continue
                        agent_log(
                            logger,
                            "warn",
                            "strategy.prompt_chain.unsupported_event",
                            component="llm_orchestrator",
                            event_type=event.type,
                            request_id=run.request_id,
                            run_id=run.id,
                            stage=stage,
                            trace_id=run.trace_id,
                        )
                        yield stage_prompt_chain_frame(
                            PROMPT_CHAIN_FALLBACK_EVENT,
                            status="fallback",
                            handoff_status="not_evaluated",
                            fallback_reason="unsupported_event",
                            error_class=event.type,
                        )
                        return None
                except SecurityControlError as exc:
                    yield stage_prompt_chain_frame(
                        PROMPT_CHAIN_FAILED_EVENT,
                        status="failed",
                        handoff_status="not_evaluated",
                        error_class=exc.__class__.__name__,
                    )
                    raise
                except Exception as exc:
                    agent_log(
                        logger,
                        "warn",
                        "strategy.prompt_chain.failed",
                        component="llm_orchestrator",
                        error=exc.__class__.__name__,
                        request_id=run.request_id,
                        run_id=run.id,
                        stage=stage,
                        trace_id=run.trace_id,
                    )
                    yield stage_prompt_chain_frame(
                        PROMPT_CHAIN_FAILED_EVENT,
                        status="failed",
                        handoff_status="not_evaluated",
                        error_class=exc.__class__.__name__,
                    )
                    return None
                payload = _parse_strategy_prompt_chain_stage_payload("".join(retry_chunks), stage)
            if payload is None:
                agent_log(
                    logger,
                    "warn",
                    "strategy.prompt_chain.invalid_handoff",
                    component="llm_orchestrator",
                    request_id=run.request_id,
                    run_id=run.id,
                    stage=stage,
                    trace_id=run.trace_id,
                )
                yield stage_prompt_chain_frame(
                    PROMPT_CHAIN_FALLBACK_EVENT,
                    status="fallback",
                    handoff_status="failed",
                    fallback_reason="invalid_handoff",
                )
                return None
            stage_outputs[stage] = payload
            yield stage_prompt_chain_frame(
                PROMPT_CHAIN_STAGE_COMPLETED_EVENT,
                status="completed",
                handoff_status="passed",
            )
            context_packet = _strategy_prompt_chain_advance_context(context_packet, stage, payload)

        final_text = _strategy_prompt_chain_final_text(stage_outputs, language=language)
        if not final_text:
            yield prompt_chain_frame(
                PROMPT_CHAIN_FALLBACK_EVENT,
                status="fallback",
                handoff_status="passed",
                fallback_reason="empty_final_text",
                latency_ms=chain_timer.elapsed_ms(),
            )
            return None
        coding_output = (stage_outputs.get(STAGE_STRATEGY_CODING) or {}).get("output")
        pine_output = (stage_outputs.get(STAGE_PINE_CODE_GENERATION) or {}).get("output")
        strategy_spec = coding_output.get("strategy_spec") if isinstance(coding_output, dict) else None
        pine_code = pine_output.get("pine_code") if isinstance(pine_output, dict) else None
        if not isinstance(strategy_spec, dict) or not isinstance(pine_code, str) or not pine_code.strip():
            yield prompt_chain_frame(
                PROMPT_CHAIN_FALLBACK_EVENT,
                status="fallback",
                handoff_status="passed",
                fallback_reason="missing_artifact_payload",
                latency_ms=chain_timer.elapsed_ms(),
            )
            return None
        yield prompt_chain_frame(
            PROMPT_CHAIN_COMPLETED_EVENT,
            status="completed",
            handoff_status="passed",
            latency_ms=chain_timer.elapsed_ms(),
            stage_count=len(stage_outputs),
        )
        return _StrategyPromptChainResult(
            final_text=final_text,
            strategy_spec=strategy_spec,
            pine_code=pine_code,
        )

    def execute_agent_run(
        self,
        *,
        auth: AuthContext,
        conversation_id: str,
        strategy_spec: dict[str, Any],
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> RunnerIntegrationResult | None:
        run = self.repository.create_run(
            auth,
            conversation_id,
            status="running",
            request_id=request_id,
            trace_id=trace_id,
        )
        if run is None:
            return None
        budget = self._new_budget()
        messages = [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": "Review this strategy spec before dry-run execution."},
        ]
        try:
            model_timer = StageTimer()
            self.security_controls.check_model_call(auth, model=self.client.model)
            append_stage_started_event(self.repository, auth, run, "model")
            self.repository.append_run_event(
                auth,
                run.id,
                "provider.started",
                {"mode": "agent", "model": self.client.model, "tier": auth.user_tier},
            )
        except BudgetExceeded as exc:
            for _frame in self._policy_blocked(auth, run, None, budget_policy_finding(exc)):
                pass
            return self._finish_blocked_run(auth, run, stage="model", duration_ms=0)
        try:
            accumulated_text: list[str] = []
            for event in _stream_client(
                self.client,
                messages=messages,
                tools=provider_tools(),
                routing_context={"auth": auth, "user_tier": auth.user_tier, "stage": DEFAULT_MODEL_STAGE},
            ):
                for _frame in self._handle_client_event(
                    auth,
                    run,
                    event,
                    budget,
                    accumulated_text,
                    output_surface="agent.run.output",
                    stream_transient_delta=False,
                ):
                    pass
                if budget.blocked:
                    return self._finish_blocked_run(auth, run, stage="model", duration_ms=model_timer.elapsed_ms())
            append_stage_event(self.repository, auth, run, "model", model_timer.elapsed_ms())
        except SecurityControlError:
            raise
        except Exception as exc:
            failed = self.repository.set_run_status(auth, run.id, "failed")
            log_run_exception(exc, run_id=run.id, trace_id=run.trace_id)
            self.repository.append_run_event(
                auth,
                run.id,
                "run.failed",
                run_failed_payload(exc),
            )
            append_stage_event(self.repository, auth, failed or run, "model", 0, status="failed")
            return RunnerIntegrationResult(run=failed or run, artifacts=[])
        result = execute_dry_run(
            repository=self.repository,
            artifact_store=self.artifact_store,
            auth=auth,
            conversation_id=conversation_id,
            strategy_spec=strategy_spec,
            existing_run=run,
        )
        return result

    def _handle_client_event(
        self,
        auth: AuthContext,
        run: AssistantRunRecord,
        event: LLMClientEvent,
        budget: RunBudget,
        accumulated_text: list[str],
        *,
        output_surface: str,
        response_intent: str | None = None,
        response_state: dict[str, Any] | None = None,
        stream_transient_delta: bool,
        user_message: str | None = None,
        context_text: str = "",
        web_search: str = "auto",
        language: str = "en",
        workflow_intent: str | None = None,
    ) -> Iterator[str]:
        if event.type == LLM_EVENT_MESSAGE_DELTA and event.text:
            redacted_text = _sanitize_user_facing_model_text(redact_text(event.text))
            finding = _first_policy_finding(
                surface=output_surface,
                payload=redacted_text,
                evidence_level=EVIDENCE_STRATEGY_IDEA,
                response_intent=response_intent,
            )
            if finding is not None:
                budget.blocked = True
                yield from self._policy_blocked(auth, run, None, finding, language=language)
                return
            accumulated_text.append(redacted_text)
            if (
                response_intent == "market_snapshot"
                and response_state is not None
                and not response_state.get("market_snapshot_emitted")
            ):
                return
            if stream_transient_delta:
                yield sse_frame(transient_delta_event(run, delta=redacted_text, chunk_index=len(accumulated_text)))
            else:
                yield self._append_frame(auth, run, LLM_EVENT_MESSAGE_DELTA, {"text": redacted_text, "compact": True})
            return
        if event.type == LLM_EVENT_USAGE:
            budget.add_usage(event.input_tokens, event.output_tokens)
            model_metadata = _model_metadata_from_event(event)
            self.repository.create_usage_ledger(
                auth,
                run_id=run.id,
                model=event.model or self.client.model,
                tool_id=None,
                input_tokens=event.input_tokens,
                output_tokens=event.output_tokens,
            )
            yield self._append_frame(
                auth,
                run,
                "model.usage",
                _usage_payload(
                    event.model or self.client.model,
                    event.input_tokens,
                    event.output_tokens,
                    metadata=model_metadata,
                ),
            )
            try:
                budget.check_usage()
                self.security_controls.check_usage_budget(
                    total_tokens=budget.input_tokens + budget.output_tokens,
                    output_tokens=budget.output_tokens,
                )
            except BudgetExceeded as exc:
                budget.blocked = True
                yield from self._policy_blocked(auth, run, None, budget_policy_finding(exc), language=language)
            return
        if event.type == PROVIDER_ROUTE_EVENT:
            yield self._append_frame(auth, run, PROVIDER_ROUTE_EVENT, event.arguments or {})
            return
        if event.type == LLM_EVENT_SOURCES:
            sources = _normalize_web_sources((event.arguments or {}).get("sources"))
            if not sources:
                return
            yield self._append_frame(auth, run, LLM_EVENT_SOURCES, {"sources": sources})
            if (
                response_intent == "market_snapshot"
                and response_state is not None
                and not response_state.get("market_snapshot_emitted")
            ):
                response_state["market_snapshot_emitted"] = True
                response_state["market_snapshot_sources"] = sources
                yield self._append_frame(
                    auth,
                    run,
                    "chat.market_snapshot",
                    _market_snapshot_payload(user_message or "", sources, language=language),
                )
                if accumulated_text and stream_transient_delta:
                    yield sse_frame(
                        transient_delta_event(
                            run,
                            delta="".join(accumulated_text),
                            chunk_index=len(accumulated_text),
                        )
                    )
            return
        if event.type == LLM_EVENT_TOOL_CALL:
            accumulated_text.clear()
            yield from self._execute_tool_call(
                auth,
                run,
                event.tool_name or "",
                event.arguments or {},
                budget,
                response_intent=response_intent,
                user_message=user_message,
                context_text=context_text,
                web_search=web_search,
                language=language,
                auto_chain_allowed=budget.auto_chain_allowed,
                workflow_intent=workflow_intent,
            )
            return
        raise RuntimeError(f"Unsupported LLM event type: {event.type}")

    def _execute_tool_call(
        self,
        auth: AuthContext,
        run: AssistantRunRecord,
        tool_name: str,
        arguments: dict[str, Any],
        budget: RunBudget,
        *,
        response_intent: str | None = None,
        user_message: str | None = None,
        context_text: str = "",
        web_search: str = "auto",
        language: str = "en",
        auto_chain_allowed: bool = False,
        workflow_intent: str | None = None,
    ) -> Iterator[str]:
        yield self._append_audit_frame(
            auth,
            run,
            MODEL_ACTION_PROPOSED,
            {
                "actor": "model",
                "source": "llm_tool_call",
                "status": "proposed",
                "intent_id": response_intent,
                "tool_id": tool_name,
                "safe_args_summary": arguments,
            },
        )
        block = self._gate_tool(auth, run, tool_name, arguments, budget)
        if block is not None:
            budget.blocked = True
            yield self._append_audit_frame(
                auth,
                run,
                MODEL_ACTION_REJECTED,
                {
                    "actor": "backend",
                    "source": "llm_tool_call",
                    "status": "rejected",
                    "intent_id": response_intent,
                    "tool_id": tool_name,
                    "reason_code": block.code,
                    "risk_level": block.severity,
                    "safe_args_summary": arguments,
                },
            )
            yield from self._policy_blocked(auth, run, tool_name, block, language=language)
            return

        yield self._append_audit_frame(
            auth,
            run,
            MODEL_ACTION_VALIDATED,
            {
                "actor": "backend",
                "source": "llm_tool_call",
                "status": "allowed",
                "intent_id": response_intent,
                "tool_id": tool_name,
                "safe_args_summary": arguments,
            },
        )
        agent_log(
            logger,
            "info",
            "tool.started",
            component="llm_orchestrator",
            conversation_id=run.conversation_id,
            input_keys=sorted(arguments.keys()),
            request_id=run.request_id,
            run_id=run.id,
            tool_id=tool_name,
            trace_id=run.trace_id,
        )
        tool_call = self.repository.create_tool_call(
            auth,
            run.id,
            tool_id=tool_name,
            status="running",
            input_json=arguments,
        )
        yield self._append_frame(
            auth,
            run,
            "tool.started",
            {"tool_id": tool_name, "label": _tool_activity_label(tool_name, language), "input_summary": _summary(arguments)},
        )
        yield self._safe_reasoning_frame(
            auth,
            run,
            _reasoning_phase_for_tool(tool_name),
            language,
            tool_id=tool_name,
            workflow_step=_workflow_step_for_tool(tool_name),
        )
        tool_timer = StageTimer()
        append_stage_started_event(self.repository, auth, run, "tool")
        try:
            pre_tool_events = self.repository.list_run_events(auth, run.id) or []
            pre_tool_sequence = pre_tool_events[-1].sequence if pre_tool_events else 0
            output = execute_tool(
                tool_name,
                arguments,
                ToolExecutionContext(
                    repository=self.repository,
                    artifact_store=self.artifact_store,
                    auth=auth,
                    run=run,
                ),
            )
            compact_output = compact_tool_output(output)
            user_summary = _tool_user_summary(tool_name, compact_output, language)
            if tool_call is not None:
                self.repository.complete_tool_call(auth, tool_call.id, status="completed", output_json=compact_output)
            budget.executed_tool_calls += 1
            budget.completed_tool_ids.append(tool_name)
            budget.completed_tool_results.append(_tool_success_result(tool_name, arguments, output))
            output_status, row_count, artifact_kind = _tool_log_summary(compact_output)
            input_tokens = _token_estimate(arguments)
            output_tokens = _token_estimate(compact_output)
            yield from self._stream_existing_run_events_after(auth, run, pre_tool_sequence)
            agent_log(
                logger,
                "info",
                "tool.completed",
                component="llm_orchestrator",
                artifact_kind=artifact_kind,
                conversation_id=run.conversation_id,
                duration_ms=tool_timer.elapsed_ms(),
                output_status=output_status,
                request_id=run.request_id,
                row_count=row_count,
                run_id=run.id,
                status="completed",
                tool_id=tool_name,
                trace_id=run.trace_id,
            )
            self.repository.create_usage_ledger(
                auth,
                run_id=run.id,
                model=self.client.model,
                tool_id=tool_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            yield self._append_audit_frame(
                auth,
                run,
                MODEL_ACTION_EXECUTED,
                {
                    "actor": "backend",
                    "source": "llm_tool_call",
                    "status": "executed",
                    "intent_id": response_intent,
                    "tool_id": tool_name,
                    "duration_ms": tool_timer.elapsed_ms(),
                    "output_status": output_status,
                    "row_count": row_count,
                    "artifact_kind": artifact_kind,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            )
            yield self._append_frame(
                auth,
                run,
                "tool.completed",
                {
                    "tool_id": tool_name,
                    "label": _tool_activity_label(tool_name, language),
                    "output": compact_output,
                    "output_summary": _summary(compact_output),
                    "tool_user_summary": user_summary,
                },
            )
            if tool_name == "create_backtest_plan" and output.get("requires_user_approval") is True:
                backtest_config = output.get("backtest_config") if isinstance(output.get("backtest_config"), dict) else {}
                yield self._append_frame(
                    auth,
                    run,
                    "backtest.preview.approval_required",
                    {
                        "approval_id": output.get("approval_id"),
                        "artifact_id": output.get("artifact_id"),
                        "requires_user_approval": True,
                        "status": output.get("approval_status", "pending"),
                        "symbol": backtest_config.get("symbol"),
                        "timeframe": backtest_config.get("timeframe"),
                        "boundary": (
                            "Local sandbox preview only; not TradingView proof, broker proof, "
                            "live trading evidence, or a profitability claim."
                        ),
                    },
                )
            workflow_relevant_tool = tool_name in {
                "generate_pine",
                "create_backtest_plan",
                "run_backtest_preview",
                "draft_bot",
            }
            post_tool_artifact_kinds: set[str] | None = None
            active_strategy_bot_workflow = False
            if workflow_relevant_tool:
                active_strategy_bot_workflow = _has_active_workflow(
                    self.repository,
                    auth,
                    run.conversation_id,
                    STRATEGY_BOT_WORKFLOW_ID,
                )
                post_tool_artifact_kinds = _conversation_user_artifact_kinds(
                    self.repository,
                    auth,
                    run.conversation_id,
                    current_run_id=run.id,
                )
                post_tool_artifact_kinds.update(_current_run_user_artifact_kinds(self.repository, auth, run.id))
            normalized_tool_workflow_intent = normalize_workflow_intent(workflow_intent)
            tool_workflow_intent = (
                "strategy_to_paper_bot_simulation"
                if workflow_relevant_tool
                and normalized_tool_workflow_intent == "strategy_to_paper_bot_simulation"
                and response_intent_allows_workflow(response_intent, "strategy_to_paper_bot_simulation")
                else None
            )
            tool_chat_decision = ChatIntentDecision(
                response_intent=response_intent or "general_chat",
                action="answer",
                model_stage=_model_stage_for_intent(response_intent),
                confidence=1.0,
                source="tool_result",
                domain_scope=domain_scope_for_response_intent(response_intent),
                workflow_intent=tool_workflow_intent,
            )
            workflow_payload = (
                _maybe_strategy_bot_workflow_payload(
                    repository=self.repository,
                    auth=auth,
                    conversation_id=run.conversation_id,
                    chat_decision=tool_chat_decision,
                    message_content=user_message or "",
                    context_text=context_text,
                    artifact_kinds=post_tool_artifact_kinds or set(),
                    tool_name=tool_name,
                    tool_result=_workflow_tool_result_payload(tool_name, arguments, output),
                )
                if workflow_relevant_tool and (tool_workflow_intent is not None or active_strategy_bot_workflow)
                else None
            )
            if workflow_payload is not None:
                workflow_payload = self._sync_workflow_tasks(
                    auth,
                    run,
                    workflow_payload,
                    language=language,
                    user_prompt=user_message or "",
                )
                yield self._append_frame(auth, run, STRATEGY_WORKFLOW_EVENT, workflow_payload)
            refreshed_suggestions = _post_tool_suggestions_payload(
                repository=self.repository,
                auth=auth,
                run=run,
                tool_name=tool_name,
                tool_result=output if tool_name == "draft_bot" else budget.completed_tool_results[-1],
                response_intent=response_intent,
                message_content=user_message or "",
                context_text=context_text,
                language=language,
                web_search=web_search,
                artifact_kinds=post_tool_artifact_kinds,
            )
            if refreshed_suggestions is not None:
                yield self._append_frame(auth, run, SUGGESTIONS_EVENT, refreshed_suggestions)
            if auto_chain_allowed:
                yield from self._run_backtest_auto_chain(
                    auth,
                    run,
                    budget,
                    response_intent=response_intent,
                    user_message=user_message or "",
                    context_text=context_text,
                    web_search=web_search,
                    language=language,
                    workflow_intent=workflow_intent,
                )
            append_stage_event(self.repository, auth, run, "tool", tool_timer.elapsed_ms())
        except Exception as exc:
            failure_fields = tool_failure_fields(exc)
            failed_output = {
                "error": exc.__class__.__name__,
                "message": redact_text(str(exc)),
                **failure_fields,
            }
            if tool_call is not None:
                self.repository.complete_tool_call(
                    auth,
                    tool_call.id,
                    status="failed",
                    output_json=failed_output,
                )
            error_payload = {
                "tool_id": tool_name,
                "label": _tool_activity_label(tool_name, language),
                "status": "failed",
                "error": exc.__class__.__name__,
                "message": redact_text(str(exc)),
                "output_summary": f"Tool failed: {exc.__class__.__name__}",
                **failure_fields,
            }
            agent_log(
                logger,
                "error",
                "tool.completed",
                component="llm_orchestrator",
                failure_code=failure_fields.get("code") if isinstance(failure_fields.get("code"), str) else None,
                conversation_id=run.conversation_id,
                duration_ms=tool_timer.elapsed_ms(),
                error_class=exc.__class__.__name__,
                output_status="failed",
                request_id=run.request_id,
                run_id=run.id,
                status="failed",
                tool_id=tool_name,
                trace_id=run.trace_id,
            )
            yield self._append_audit_frame(
                auth,
                run,
                MODEL_ACTION_EXECUTED,
                {
                    "actor": "backend",
                    "source": "llm_tool_call",
                    "status": "failed",
                    "intent_id": response_intent,
                    "tool_id": tool_name,
                    "duration_ms": tool_timer.elapsed_ms(),
                    "output_status": "failed",
                    "reason_code": failure_fields.get("code") if isinstance(failure_fields.get("code"), str) else None,
                    "error_class": exc.__class__.__name__,
                },
            )
            yield self._append_frame(auth, run, "tool.completed", error_payload)
            append_stage_event(self.repository, auth, run, "tool", tool_timer.elapsed_ms(), status="failed")
            raise

    def _stream_existing_run_events_after(
        self,
        auth: AuthContext,
        run: AssistantRunRecord,
        sequence: int,
    ) -> Iterator[str]:
        events = self.repository.list_run_events(auth, run.id) or []
        for event in events:
            if event.sequence > sequence:
                yield sse_frame(event)

    def _run_backtest_auto_chain(
        self,
        auth: AuthContext,
        run: AssistantRunRecord,
        budget: RunBudget,
        *,
        response_intent: str | None,
        user_message: str,
        context_text: str,
        web_search: str,
        language: str,
        workflow_intent: str | None = None,
    ) -> Iterator[str]:
        planner = BacktestAutoChainPlanner(
            enabled=os.getenv("STRATEGY_CODEBOT_BACKTEST_AUTO_CHAIN_ENABLED", "1") == "1",
            max_steps=_int_env("STRATEGY_CODEBOT_BACKTEST_AUTO_CHAIN_MAX_STEPS", 4),
        )
        while budget.allow_tool():
            step = planner.next_step(
                message_content=user_message,
                completed_tool_ids=budget.completed_tool_ids,
                completed_tool_results=budget.completed_tool_results,
                auto_steps_completed=budget.auto_chain_steps_completed,
                start_allowed=budget.auto_chain_allowed,
            )
            if step is None:
                return
            if not budget.auto_chain_started:
                budget.auto_chain_started = True
                yield self._append_frame(
                    auth,
                    run,
                    BACKTEST_AUTO_CHAIN_EVENTS["started"],
                    {"source": budget.auto_chain_source, "trigger": "chat_intent_decision"},
                )
            try:
                yield from self._execute_tool_call(
                    auth,
                    run,
                    step.tool_id,
                    step.arguments,
                    budget,
                    response_intent=response_intent,
                    user_message=user_message,
                    context_text=context_text,
                    web_search=web_search,
                    language=language,
                    auto_chain_allowed=False,
                    workflow_intent=workflow_intent,
                )
            except Exception as exc:
                message = redact_text(str(exc))
                budget.auto_chain_failure_text = _auto_chain_failure_message(step.tool_id, message, language)
                yield self._append_frame(
                    auth,
                    run,
                    BACKTEST_AUTO_CHAIN_EVENTS["failed"],
                    {"tool_id": step.tool_id, "reason": step.reason, "error": exc.__class__.__name__, "message": message},
                )
                return
            budget.auto_chain_steps_completed += 1
            latest_result = budget.completed_tool_results[-1] if budget.completed_tool_results else {}
            payload: dict[str, Any] = {"tool_id": step.tool_id, "reason": step.reason}
            if isinstance(latest_result.get("run_id"), str):
                payload["child_run_id"] = latest_result["run_id"]
            yield self._append_frame(auth, run, BACKTEST_AUTO_CHAIN_EVENTS["step_completed"], payload)
            if step.tool_id == "run_backtest_preview":
                yield self._append_frame(
                    auth,
                    run,
                    BACKTEST_AUTO_CHAIN_EVENTS["waiting"],
                    {
                        "child_run_id": latest_result.get("run_id"),
                        "status": latest_result.get("status", "queued"),
                    },
                )
                return

    def _gate_tool(
        self,
        auth: AuthContext,
        run: AssistantRunRecord,
        tool_name: str,
        arguments: dict[str, Any],
        budget: RunBudget,
    ) -> PolicyFinding | None:
        if self.repository.get_run(auth, run.id) is None:
            return PolicyFinding(
                severity="blocker",
                code="auth_denied",
                message="Run is not authorized for this user/workspace.",
                surface="tool.auth",
                evidence_level=EVIDENCE_STRATEGY_IDEA,
            )
        if tool_name not in TOOL_DEFINITIONS:
            return PolicyFinding(
                severity="blocker",
                code="tool_not_allowed",
                message=f"Tool is not in the server allowlist: {tool_name}",
                surface="tool.allowlist",
                evidence_level=EVIDENCE_STRATEGY_IDEA,
            )
        schema_error = validate_tool_arguments(tool_name, arguments)
        if schema_error:
            return PolicyFinding(
                severity="blocker",
                code="schema_invalid",
                message=schema_error,
                surface="tool.schema",
                evidence_level=EVIDENCE_STRATEGY_IDEA,
            )
        if not budget.allow_tool():
            self.repository.create_usage_ledger(
                auth,
                run_id=run.id,
                model=self.client.model,
                tool_id=tool_name,
                input_tokens=_token_estimate(arguments),
                output_tokens=0,
            )
            return PolicyFinding(
                severity="blocker",
                code="budget_exceeded",
                message="Tool call budget exceeded for this run.",
                surface="tool.budget",
                evidence_level=EVIDENCE_STRATEGY_IDEA,
            )
        try:
            self.security_controls.check_tool_call(auth, tool_id=tool_name)
        except SecurityControlError as exc:
            self.repository.create_usage_ledger(
                auth,
                run_id=run.id,
                model=self.client.model,
                tool_id=tool_name,
                input_tokens=_token_estimate(arguments),
                output_tokens=0,
            )
            if isinstance(exc, BudgetExceeded):
                return budget_policy_finding(exc)
            return PolicyFinding(
                severity="blocker",
                code=exc.code,
                message=f"Security control blocked tool call: {exc.dimension}",
                surface=f"tool.{tool_name}",
                evidence_level=EVIDENCE_GENERATED_ARTIFACT,
            )
        return _first_policy_finding(
            surface=f"tool.{tool_name}",
            payload=arguments,
            evidence_level=EVIDENCE_GENERATED_ARTIFACT,
        )

    def _chat_safety_blocked(
        self,
        auth: AuthContext,
        run: AssistantRunRecord,
        safety_finding: PolicyFinding,
        *,
        language: str,
        audit_source: str,
    ) -> Iterator[str]:
        yield self._append_audit_frame(
            auth,
            run,
            MODEL_ACTION_REJECTED,
            {
                "actor": "backend",
                "source": audit_source,
                "status": "rejected",
                "reason_code": safety_finding.code,
                "risk_level": safety_finding.severity,
                "safe_args_summary": {
                    "surface": safety_finding.surface,
                    "rule_id": safety_finding.rule_id,
                    "category": safety_finding.category,
                },
            },
        )
        blocked_message = _safe_blocked_message(language)
        self.repository.create_message(auth, run.conversation_id, blocked_message, role="assistant")
        yield from self._policy_blocked(auth, run, None, safety_finding, language=language)
        completed = self.repository.set_run_status(auth, run.id, "blocked")
        append_stage_event(
            self.repository,
            auth,
            completed or run,
            "chat_safety_preflight",
            0,
            status="blocked",
        )
        yield self._append_frame(auth, completed or run, "run.completed", {"status": "blocked"})

    def _policy_blocked(
        self,
        auth: AuthContext,
        run: AssistantRunRecord,
        tool_name: str | None,
        finding: PolicyFinding,
        *,
        language: str = "en",
    ) -> Iterator[str]:
        self.repository.create_policy_finding(
            auth,
            run.id,
            severity=finding.severity,
            code=finding.code,
            message=redact_text(finding.message),
        )
        yield self._append_frame(
            auth,
            run,
            "policy.blocked",
            {"tool_id": tool_name, **policy_finding_payload(finding)},
        )
        yield self._append_frame(
            auth,
            run,
            LLM_EVENT_MESSAGE_DELTA,
            {"text": _safe_blocked_message(language), "compact": True},
        )

    def _run_bounded_scout(
        self,
        *,
        auth: AuthContext,
        run: AssistantRunRecord,
        conversation_id: str,
        conversation_context: Any,
        language: str,
    ) -> Iterator[str]:
        tool_context = ToolExecutionContext(
            repository=self.repository,
            artifact_store=self.artifact_store,
            auth=auth,
            run=run,
        )
        runner = BoundedScoutRunner(
            llm_client=self.client,
            tool_context=tool_context,
            run_id=run.id,
            budget=AgentLoopBudget(max_iterations=2, max_tool_calls=2, max_tokens=4_000, max_runtime_seconds=20.0),
        )
        result = runner.run(
            conversation_context.messages,
            routing_context={"auth": auth, "user_tier": auth.user_tier, "stage": "bounded_scout"},
        )
        for event in result.events:
            event_type = str(event.get("event_type") or "agent_loop.event")
            payload = {
                key: value
                for key, value in event.items()
                if key not in {"event_type", "sequence", "created_at", "run_id"}
            }
            yield self._append_frame(auth, run, event_type, payload)

        final_text = result.response_text.strip() or _bounded_scout_fallback_message(result.status, language)
        if result.status == "blocked":
            final_text = _safe_blocked_message(language)
        self.repository.create_message(auth, conversation_id, final_text, role="assistant")
        yield self._safe_reasoning_frame(auth, run, "finalizing", language)
        yield self._append_frame(
            auth,
            run,
            LLM_EVENT_MESSAGE_DELTA,
            {"text": final_text, "compact": True, "source": "bounded_scout"},
        )
        terminal_status = "blocked" if result.status == "blocked" else "completed"
        completed = self.repository.set_run_status(auth, run.id, terminal_status)
        append_stage_event(self.repository, auth, completed or run, "bounded_scout", 0, status=terminal_status)
        yield self._append_frame(auth, completed or run, "run.completed", {"status": terminal_status})
        if terminal_status == "completed":
            self._maybe_compact_conversation(auth, conversation_id, run.id, language=language)

    def _domain_scope_blocked(
        self,
        *,
        auth: AuthContext,
        run: AssistantRunRecord,
        conversation_id: str,
        domain_scope: DomainScopeDecision,
        language: str,
    ) -> Iterator[str]:
        response_payload = {
            "confidence": domain_scope.confidence,
            "intent": "general_chat",
            "safe": True,
            "source": "domain_scope_guard",
            **domain_scope.payload(),
        }
        suggestions_payload = _domain_scope_suggestions_payload(language, domain_scope)
        message = _domain_scope_blocked_message(language)
        yield self._append_audit_frame(
            auth,
            run,
            MODEL_ACTION_REJECTED,
            {
                "actor": "backend",
                "source": "domain_intent_gate",
                "status": "rejected",
                "reason_code": domain_scope.reason,
                "risk_level": "low",
                "safe_args_summary": {
                    "domain_scope": domain_scope.scope,
                    "confidence": domain_scope.confidence,
                },
            },
        )
        yield self._append_frame(auth, run, "chat.response_intent", response_payload)
        yield self._append_frame(auth, run, SUGGESTIONS_EVENT, suggestions_payload)
        self.repository.create_message(auth, conversation_id, message, role="assistant")
        yield self._append_frame(
            auth,
            run,
            LLM_EVENT_MESSAGE_DELTA,
            {"text": message, "compact": True, "source": "domain_scope_guard"},
        )
        terminal_status = "completed"
        completed = self.repository.set_run_status(auth, run.id, terminal_status)
        append_stage_event(self.repository, auth, completed or run, "domain_scope_guard", 0, status=terminal_status)
        yield self._append_frame(auth, completed or run, "run.completed", {"status": terminal_status})

    def _finish_blocked_run(
        self,
        auth: AuthContext,
        run: AssistantRunRecord,
        *,
        stage: str,
        duration_ms: int,
    ) -> RunnerIntegrationResult:
        blocked = self.repository.set_run_status(auth, run.id, "blocked")
        final_run = blocked or run
        append_stage_event(self.repository, auth, final_run, stage, duration_ms, status="blocked")
        self.repository.append_run_event(auth, run.id, "run.completed", {"status": "blocked"})
        return RunnerIntegrationResult(run=final_run, artifacts=[])

    def _append_frame(self, auth: AuthContext, run: AssistantRunRecord, event_type: str, payload: dict) -> str:
        event = self.repository.append_run_event(auth, run.id, event_type, _redact_event_payload(event_type, payload))
        if event is None:
            raise RuntimeError(f"Unable to append run event {event_type}")
        if event_type in {"artifact.created", "run.completed", "run.failed"}:
            log_event = {
                "artifact.created": "artifact.created",
                "run.completed": "agent.run.finished",
                "run.failed": "agent.run.failed",
            }[event_type]
            agent_log(
                logger,
                "error" if event_type == "run.failed" else "info",
                log_event,
                component="llm_orchestrator",
                artifact_kind=payload.get("artifact_kind") if isinstance(payload.get("artifact_kind"), str) else None,
                conversation_id=run.conversation_id,
                error_class=(
                    payload.get("failure_class")
                    if isinstance(payload.get("failure_class"), str)
                    else payload.get("error")
                    if isinstance(payload.get("error"), str)
                    else None
                ),
                failure_code=payload.get("code") if isinstance(payload.get("code"), str) else None,
                output_status=payload.get("status") if isinstance(payload.get("status"), str) else None,
                request_id=run.request_id,
                run_id=run.id,
                trace_id=run.trace_id,
            )
        if event_type == "run.completed" and payload.get("status") == "completed" and self.artifact_store is not None:
            KnowledgeLearningService(self.repository, self.artifact_store).maybe_extract_run_candidates(auth, run)
        return sse_frame(event)

    def _append_audit_frame(
        self,
        auth: AuthContext,
        run: AssistantRunRecord,
        event_type: str,
        payload: dict[str, Any],
    ) -> str:
        return self._append_frame(auth, run, event_type, model_audit_payload(run, payload))

    def _classifier_started_frame(
        self,
        auth: AuthContext,
        run: AssistantRunRecord,
        *,
        classifier_name: str,
        stage: str,
        timeout_seconds: float,
        prompt_summary: dict[str, Any],
    ) -> str:
        return self._append_frame(
            auth,
            run,
            CLASSIFIER_EVENT_STARTED,
            _classifier_event_payload(
                run,
                classifier_name=classifier_name,
                stage=stage,
                status="started",
                timeout_seconds=timeout_seconds,
                prompt_summary=prompt_summary,
            ),
        )

    def _classifier_result_frames(
        self,
        auth: AuthContext,
        run: AssistantRunRecord,
        *,
        classifier_name: str,
        stage: str,
        capture_client: ClassifierRouteCaptureClient,
        outcome: ClassifierRunOutcome,
        prompt_summary: dict[str, Any],
    ) -> Iterator[str]:
        for route_event in capture_client.route_events:
            yield self._append_frame(
                auth,
                run,
                CLASSIFIER_EVENT_ROUTE,
                _classifier_route_event_payload(
                    run,
                    classifier_name=classifier_name,
                    stage=stage,
                    route_event=route_event,
                    prompt_summary=prompt_summary,
                ),
            )
        event_type = {
            "completed": CLASSIFIER_EVENT_COMPLETED,
            "timeout": CLASSIFIER_EVENT_TIMEOUT,
            "failed": CLASSIFIER_EVENT_FAILED,
        }.get(outcome.status, CLASSIFIER_EVENT_COMPLETED)
        yield self._append_frame(
            auth,
            run,
            event_type,
            _classifier_event_payload(
                run,
                classifier_name=classifier_name,
                stage=stage,
                status=outcome.status,
                duration_ms=outcome.duration_ms,
                timeout_seconds=outcome.timeout_seconds,
                fallback_source=outcome.fallback_source,
                error_class=outcome.error_class,
                prompt_summary=prompt_summary,
            ),
        )

    def _sync_workflow_tasks(
        self,
        auth: AuthContext,
        run: AssistantRunRecord,
        workflow_payload: dict[str, Any],
        *,
        language: str = "en",
        user_prompt: str = "",
    ) -> dict[str, Any]:
        workflow_id = workflow_payload.get("workflow_id")
        if not isinstance(workflow_id, str):
            return workflow_payload
        workflow_payload = self._enrich_workflow_task_prompts(
            auth,
            run,
            workflow_payload,
            workflow_id=workflow_id,
            language=language,
            user_prompt=user_prompt,
        )
        bot_proposal_id = workflow_payload.get("bot_proposal_id")
        task_payloads = normalize_workflow_tasks(
            workflow_id,
            workflow_payload.get("tasks"),
            start_allowed=workflow_payload.get("start_allowed") is True,
            bot_proposal_id=bot_proposal_id if isinstance(bot_proposal_id, str) else None,
        )
        completed_steps = {
            step for step in workflow_payload.get("completed_steps", []) if isinstance(step, str)
        }
        sync_result = self.repository.sync_workflow_tasks(
            auth,
            conversation_id=run.conversation_id,
            run_id=run.id,
            workflow_id=workflow_id,
            task_payloads=task_payloads,
            completed_steps=completed_steps,
        )
        if sync_result is None:
            return workflow_payload
        raw_tasks = workflow_payload.get("tasks")
        raw_task_count = len(raw_tasks) if isinstance(raw_tasks, list) else 0
        missing_fields = [
            field
            for field in workflow_payload.get("missing_fields", [])
            if isinstance(field, str)
        ]
        append_model_audit_event(
            self.repository,
            auth,
            run,
            MODEL_ACTION_VALIDATED,
            {
                "actor": "backend",
                "source": "workflow_registry",
                "status": "allowed",
                "workflow_id": workflow_id,
                "proposal_id": bot_proposal_id if isinstance(bot_proposal_id, str) else None,
                "missing_fields": missing_fields,
                "workflow_summary": {
                    "current_step": workflow_payload.get("current_step"),
                    "status": workflow_payload.get("status"),
                    "task_count": len(sync_result.records),
                    "input_request_count": sum(
                        len(task.payload_json.get("input_requests", []))
                        for task in sync_result.records
                        if isinstance(task.payload_json.get("input_requests"), list)
                    ),
                    "start_allowed": workflow_payload.get("start_allowed") is True,
                },
                "dropped_counts": {
                    "tasks": max(0, raw_task_count - len(task_payloads)),
                },
            },
        )
        has_open_gate = any(task.status in {"pending_user", "blocked"} for task in sync_result.records)
        if missing_fields or has_open_gate or (
            isinstance(bot_proposal_id, str) and workflow_payload.get("start_allowed") is not True
        ):
            append_model_audit_event(
                self.repository,
                auth,
                run,
                WORKFLOW_GATE_REQUIRED,
                {
                    "actor": "backend",
                    "source": "workflow_task",
                    "status": "gated",
                    "workflow_id": workflow_id,
                    "proposal_id": bot_proposal_id if isinstance(bot_proposal_id, str) else None,
                    "reason_code": "missing_fields" if missing_fields else "pending_workflow_task",
                    "risk_level": "review_gate",
                    "missing_fields": missing_fields,
                    "workflow_summary": {
                        "open_task_count": sum(
                            1 for task in sync_result.records if task.status in {"pending_user", "blocked"}
                        ),
                        "start_allowed": workflow_payload.get("start_allowed") is True,
                    },
                },
            )
        task_events = [
            ("workflow.task.created", _workflow_task_event_payload(record))
            for record in sync_result.created
        ]
        task_events.extend(
            ("workflow.task.updated", _workflow_task_event_payload(record))
            for record in sync_result.updated
        )
        task_events.extend(
            ("workflow.task.resolved", _workflow_task_event_payload(record))
            for record in sync_result.resolved
        )
        if task_events:
            self.repository.append_run_events(auth, run.id, task_events)

        hydrated_tasks = [workflow_task_state(task) for task in sync_result.records]
        if not hydrated_tasks:
            return workflow_payload
        hydrated = dict(workflow_payload)
        hydrated["tasks"] = hydrated_tasks
        hydrated["input_requests"] = [
            request
            for task in hydrated_tasks
            for request in task.get("input_requests", [])
            if isinstance(request, dict)
        ]
        hydrated["task_values"] = _workflow_task_values_from_state(hydrated_tasks)
        return validate_workflow_payload(hydrated) or hydrated

    def _enrich_workflow_task_prompts(
        self,
        auth: AuthContext,
        run: AssistantRunRecord,
        workflow_payload: dict[str, Any],
        *,
        workflow_id: str,
        language: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        raw_tasks = workflow_payload.get("tasks")
        if workflow_id != STRATEGY_BOT_WORKFLOW_ID or not isinstance(raw_tasks, list):
            return workflow_payload
        enriched_tasks: list[Any] = []
        changed = False
        for task in raw_tasks:
            if not isinstance(task, dict) or task.get("task_template_id") != "collect_strategy_inputs":
                enriched_tasks.append(task)
                continue
            if task.get("status") != "pending_user":
                enriched_tasks.append(task)
                continue
            result = generate_workflow_task_prompt_payload(
                self.client,
                workflow_id=workflow_id,
                task_payload=task,
                language=language,
                user_prompt=user_prompt,
                task_values=task.get("values") if isinstance(task.get("values"), dict) else None,
                auth=auth,
                context={
                    "current_step": workflow_payload.get("current_step"),
                    "status": workflow_payload.get("status"),
                    "missing_fields": [
                        field for field in workflow_payload.get("missing_fields", []) if isinstance(field, str)
                    ],
                },
            )
            if result.payload != task:
                changed = True
            enriched_tasks.append(result.payload)
            self.repository.append_run_events(
                auth,
                run.id,
                workflow_prompt_generator_events(
                    result,
                    workflow_id=workflow_id,
                    task_template_id="collect_strategy_inputs",
                ),
            )
            append_model_audit_event(
                self.repository,
                auth,
                run,
                MODEL_ACTION_VALIDATED,
                {
                    "actor": "backend",
                    "source": "workflow_prompt_generator",
                    "status": "allowed" if result.status == "generated" else "failed",
                    "workflow_id": workflow_id,
                    "task_template_id": "collect_strategy_inputs",
                    "reason_code": result.fallback_reason or result.status,
                    "duration_ms": result.duration_ms,
                    "workflow_summary": {
                        "input_id": result.input_id,
                        "target_input_ids": list(result.target_input_ids),
                        "generated_input_ids": list(result.generated_input_ids),
                        "fallback_input_ids": list(result.fallback_input_ids),
                        "option_count": result.option_count,
                        "generation_status": result.status,
                    },
                },
            )
        if not changed:
            return workflow_payload
        enriched = dict(workflow_payload)
        enriched["tasks"] = enriched_tasks
        return enriched

    def _safe_reasoning_frame(
        self,
        auth: AuthContext,
        run: AssistantRunRecord,
        phase: str,
        language: str = "en",
        *,
        tool_id: str | None = None,
        workflow_step: str | None = None,
    ) -> str:
        return sse_frame(
            transient_reasoning_event(
                run,
                payload=redact_value(
                    _safe_reasoning_payload(
                        phase,
                        language,
                        tool_id=tool_id,
                        workflow_step=workflow_step,
                    )
                ),
            )
        )

    def _new_budget(self) -> RunBudget:
        return RunBudget(
            max_tool_calls=min(self.max_tool_calls, self.budget_config.max_tool_calls),
            max_total_tokens=self.budget_config.max_total_tokens,
            max_output_tokens=self.budget_config.max_output_tokens,
        )


def _redact_event_payload(event_type: str, payload: dict) -> dict:
    redacted = redact_value(payload)
    if event_type != SUGGESTIONS_EVENT or not isinstance(redacted, dict):
        return redacted

    # Suggestion prompts are user-facing button actions, not hidden model prompts.
    # Keep redaction for every other event so internal prompts/context stay protected.
    for section in ("actions", "composer_blocks"):
        source_items = payload.get(section)
        redacted_items = redacted.get(section)
        if not isinstance(source_items, list) or not isinstance(redacted_items, list):
            continue
        for source_item, redacted_item in zip(source_items, redacted_items, strict=False):
            _restore_user_facing_prompt(source_item, redacted_item)
    return redacted


def _restore_user_facing_prompt(source: Any, target: Any) -> None:
    if not isinstance(source, dict) or not isinstance(target, dict):
        return
    prompt = source.get("prompt")
    if isinstance(prompt, str):
        target["prompt"] = redact_text(prompt)
    source_variants = source.get("variants")
    target_variants = target.get("variants")
    if not isinstance(source_variants, list) or not isinstance(target_variants, list):
        return
    for source_variant, target_variant in zip(source_variants, target_variants, strict=False):
        _restore_user_facing_prompt(source_variant, target_variant)


def _should_run_strategy_prompt_chain(
    message_content: str,
    *,
    response_intent: str | None,
    active_tools: list[dict[str, Any]],
) -> bool:
    if response_intent not in STRATEGY_PROMPT_CHAIN_INTENTS:
        return False
    if _has_web_search_tool(active_tools):
        return False
    if response_intent == "strategy_building":
        return _strategy_prompt_chain_generation_signal(message_content)
    return True


def _strategy_prompt_chain_generation_signal(message_content: str) -> bool:
    normalized = " ".join((message_content or "").lower().split())
    return bool(
        re.search(
            r"\b(build|create|generate|draft|design|write|make|construct|code|script|pine|pinescript)\b"
            r"|xây\s*dựng|xay\s*dung|tạo|tao|viết|viet|sinh|dựng|dung",
            normalized,
            flags=re.IGNORECASE,
        )
    )


def _strategy_prompt_chain_initial_packet(
    message_content: str,
    *,
    context_text: str,
    response_intent: str,
    language: str,
) -> dict[str, Any]:
    return {
        "original_prompt": message_content,
        "language": language,
        "response_intent": response_intent,
        "conversation_context_excerpt": context_text[-3000:],
        "policy": "reviewable_strategy_artifact_generation_only",
        "policy_boundaries": [
            "Generate reviewable strategy artifacts only.",
            "Do not claim profitability, live-trading readiness, broker execution, or deployment readiness.",
            "Pine output must be Pine Script v6 and must preserve the accepted strategy intent.",
            STRATEGY_PROMPT_CHAIN_SIZING_GUIDANCE,
        ],
        "schema_summary": {
            "stages": list(STRATEGY_PROMPT_CHAIN_STAGES),
            "strategy_reasoning": ["summary", "constraints", "indicators", "entries", "exits", "risk_rules", "non_goals"],
            "strategy_coding": ["strategy_spec"],
            "pine_code_generation": ["pine_code"],
        },
        "current_artifacts": {},
        "stage_outputs": {},
        "previous_stage_output": {},
        "context_refs": ["prompt", "policy_boundaries", "schemas/strategy-spec.schema.json"],
    }


def _prompt_chain_event_payload(
    run: AssistantRunRecord,
    *,
    status: str,
    response_intent: str | None = None,
    stage: str | None = None,
    model_stage: str | None = None,
    provider_route: str | None = None,
    handoff_status: str | None = None,
    fallback_reason: str | None = None,
    latency_ms: int | None = None,
    usage: dict[str, int] | None = None,
    error_class: str | None = None,
    failed_provider_route: str | None = None,
    stages: list[str] | None = None,
    stage_count: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": run.id,
        "trace_id": run.trace_id,
        "workflow": PROMPT_CHAIN_WORKFLOW,
        "status": status,
    }
    optional = {
        "response_intent": response_intent,
        "stage": stage,
        "model_stage": model_stage,
        "provider_route": provider_route,
        "handoff_status": handoff_status,
        "fallback_reason": fallback_reason,
        "latency_ms": latency_ms,
        "usage": usage,
        "error_class": error_class,
        "failed_provider_route": failed_provider_route,
        "stages": stages,
        "stage_count": stage_count,
    }
    payload.update({key: value for key, value in optional.items() if value is not None})
    return payload


def _prompt_chain_timeout_failures(route_payload: dict[str, Any]) -> list[dict[str, str]]:
    attempts = route_payload.get("fallback_attempts")
    if not isinstance(attempts, list):
        return []
    failures: list[dict[str, str]] = []
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        error = _safe_string(attempt.get("error"))
        if error != "ProviderTimeoutError":
            continue
        provider_route = _safe_string(attempt.get("provider_route"))
        failures.append(
            {
                "error": error,
                "provider_route": provider_route or "unknown",
            }
        )
    return failures


def _prompt_chain_usage(usage: dict[str, int]) -> dict[str, int]:
    input_tokens = int(usage.get("input_tokens", 0))
    output_tokens = int(usage.get("output_tokens", 0))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def _strategy_prompt_chain_stage_context(stage: str, context_packet: dict[str, Any]) -> dict[str, Any]:
    stage_outputs = context_packet.get("stage_outputs") if isinstance(context_packet.get("stage_outputs"), dict) else {}
    current_artifacts = (
        context_packet.get("current_artifacts") if isinstance(context_packet.get("current_artifacts"), dict) else {}
    )
    common = {
        "original_prompt": context_packet.get("original_prompt"),
        "language": context_packet.get("language"),
        "response_intent": context_packet.get("response_intent"),
        "conversation_context_excerpt": context_packet.get("conversation_context_excerpt"),
        "policy": context_packet.get("policy"),
        "policy_boundaries": context_packet.get("policy_boundaries", []),
    }
    if stage == STAGE_STRATEGY_CODING:
        return {
            **common,
            "schema_summary": {"stage": STAGE_STRATEGY_CODING, "expected_output": ["strategy_spec"]},
            "previous_stage_output": stage_outputs.get(STAGE_STRATEGY_REASONING, {}),
            "current_artifacts": {},
            "context_refs": [
                "prompt",
                "policy_boundaries",
                "schemas/strategy-spec.schema.json",
                STAGE_STRATEGY_REASONING,
            ],
        }
    if stage == STAGE_PINE_CODE_GENERATION:
        strategy_spec = current_artifacts.get("strategy_spec")
        return {
            **common,
            "schema_summary": {
                "stage": STAGE_PINE_CODE_GENERATION,
                "expected_output": ["pine_code"],
                "pine_version": "v6",
            },
            "previous_stage_output": stage_outputs.get(STAGE_STRATEGY_CODING, {}),
            "current_artifacts": {"strategy_spec": strategy_spec},
            "context_refs": [
                "prompt",
                "policy_boundaries",
                "schemas/strategy-spec.schema.json",
                STAGE_STRATEGY_CODING,
            ],
        }
    return context_packet


def _strategy_prompt_chain_advance_context(
    context_packet: dict[str, Any],
    stage: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    stage_outputs = dict(context_packet.get("stage_outputs") or {})
    stage_outputs[stage] = payload
    current_artifacts = dict(context_packet.get("current_artifacts") or {})
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    if stage == STAGE_STRATEGY_CODING and isinstance(output.get("strategy_spec"), dict):
        current_artifacts["strategy_spec"] = output["strategy_spec"]
    if stage == STAGE_PINE_CODE_GENERATION and isinstance(output.get("pine_code"), str):
        current_artifacts["pine_code"] = output["pine_code"]
    context_refs = [
        *[str(ref) for ref in context_packet.get("context_refs", [])],
        stage,
    ]
    return {
        **context_packet,
        "previous_stage_output": payload,
        "stage_outputs": stage_outputs,
        "current_artifacts": current_artifacts,
        "context_refs": context_refs,
    }


def _parse_strategy_prompt_chain_stage_payload(text: str, stage: str) -> dict[str, Any] | None:
    payload = _extract_json_object(text)
    if not isinstance(payload, dict) or payload.get("stage") != stage:
        return None
    output = payload.get("output")
    if not isinstance(output, dict):
        return None
    if not isinstance(payload.get("assumptions"), list):
        return None
    if not isinstance(payload.get("handoff_notes"), str):
        return None
    if not isinstance(payload.get("policy_observations"), list):
        return None
    if stage == STAGE_STRATEGY_REASONING:
        required = ("summary", "constraints", "indicators", "entries", "exits", "risk_rules", "non_goals")
        if not isinstance(output.get("summary"), str) or not output["summary"].strip():
            return None
        if any(not isinstance(output.get(key), list) for key in required[1:]):
            return None
        return payload
    if stage == STAGE_STRATEGY_CODING:
        strategy_spec = output.get("strategy_spec")
        if not isinstance(strategy_spec, dict) or not strategy_spec:
            return None
        required_spec_keys = (
            "target_platform",
            "script_type",
            "market",
            "timeframe",
            "entry_rules",
            "exit_rules",
            "risk_rules",
        )
        if any(key not in strategy_spec for key in required_spec_keys):
            return None
        if any(
            not isinstance(strategy_spec.get(key), str) or not str(strategy_spec.get(key)).strip()
            for key in ("target_platform", "script_type", "market", "timeframe")
        ):
            return None
        if any(
            not isinstance(strategy_spec.get(key), list) or not strategy_spec.get(key)
            for key in ("entry_rules", "exit_rules", "risk_rules")
        ):
            return None
        return payload
    if stage == STAGE_PINE_CODE_GENERATION:
        pine_code = output.get("pine_code")
        if not isinstance(pine_code, str) or not pine_code.strip():
            return None
        if not pine_code.lstrip().startswith("//@version=6"):
            return None
        return payload
    return None


def _strategy_prompt_chain_final_text(stage_outputs: dict[str, dict[str, Any]], *, language: str) -> str:
    reasoning_output = (stage_outputs.get(STAGE_STRATEGY_REASONING) or {}).get("output")
    coding_output = (stage_outputs.get(STAGE_STRATEGY_CODING) or {}).get("output")
    pine_output = (stage_outputs.get(STAGE_PINE_CODE_GENERATION) or {}).get("output")
    if not isinstance(reasoning_output, dict) or not isinstance(coding_output, dict) or not isinstance(pine_output, dict):
        return ""
    strategy_spec = coding_output.get("strategy_spec")
    pine_code = pine_output.get("pine_code")
    if not isinstance(strategy_spec, dict) or not isinstance(pine_code, str) or not pine_code.strip():
        return ""
    summary = _safe_string(reasoning_output.get("summary")) or "Reviewable strategy draft."
    name = _safe_string(strategy_spec.get("name")) or "Strategy"
    risk = _safe_string(strategy_spec.get("position_sizing"))
    entry_rules = _safe_string_list(reasoning_output.get("entries"), limit=2)
    exit_rules = _safe_string_list(reasoning_output.get("exits"), limit=2)
    risk_rules = _safe_string_list(reasoning_output.get("risk_rules"), limit=2)
    if _normalize_language(language) == "vi":
        lines = [
            f"Mình đã dựng artifact Pine Script v6 cho `{name}`.",
            "",
            f"Tóm tắt: {summary}",
        ]
        if risk:
            lines.append(f"Quản trị vốn: {risk}")
        detail_heading = "Điểm chính"
    else:
        lines = [
            f"Generated a reviewable Pine Script v6 artifact for `{name}`.",
            "",
            f"Summary: {summary}",
        ]
        if risk:
            lines.append(f"Position sizing: {risk}")
        detail_heading = "Key points"
    details = [*entry_rules, *exit_rules, *risk_rules]
    if details:
        lines.extend(["", f"{detail_heading}:"])
        lines.extend(f"- {item}" for item in details[:6])
    lines.extend(["", "```pine", pine_code.strip(), "```"])
    return "\n".join(lines)


def _safe_string_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = _safe_string(item)
        if text:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def _model_stage_for_chat(
    message_content: str,
    *,
    response_intent: str | None,
    active_tools: list[dict[str, Any]],
    decision_model_stage: str | None = None,
) -> str:
    if decision_model_stage in CHAT_RESPONSE_MODEL_STAGES:
        return decision_model_stage
    lowered = message_content.lower()
    tool_names = {
        str(tool.get("function", {}).get("name") or tool.get("name") or "")
        for tool in active_tools
        if isinstance(tool, dict)
    }
    if "repair" in lowered or response_intent == "artifact_generation":
        return MODEL_STAGE_REPAIR
    if response_intent in {"market_research", "docs_research"}:
        return MODEL_STAGE_BALANCED_REVIEW
    if tool_names & {"create_backtest_plan", "run_backtest_preview", "run_backtest_variant_lab"}:
        return MODEL_STAGE_PINE_CODE_GENERATION
    if response_intent in {"backtest_preview", "pine_generation"}:
        return MODEL_STAGE_PINE_CODE_GENERATION
    if re.search(r"\b(backtest|pine|pinescript|pineforge|strategy\.entry|strategy\.exit|generate strategy|btc/usdt)\b", lowered):
        return MODEL_STAGE_PINE_CODE_GENERATION
    return DEFAULT_MODEL_STAGE


def _model_metadata_from_event(event: LLMClientEvent) -> dict[str, Any]:
    arguments = event.arguments or {}
    keys = ("model_tier", "model_stage", "provider_route", "fallback_used", "attempt_count")
    return {key: arguments[key] for key in keys if key in arguments}


def _first_policy_finding(
    *,
    surface: str,
    payload: Any,
    evidence_level: str,
    response_intent: str | None = None,
) -> PolicyFinding | None:
    decision = evaluate_policy(PolicySubject(surface=surface, payload=payload, evidence_level=evidence_level))
    for finding in decision.findings:
        if finding.severity != "blocker":
            continue
        if _allow_chat_output_reference_url(finding, surface=surface, response_intent=response_intent):
            continue
        return finding
    return None


def _allow_chat_output_reference_url(
    finding: PolicyFinding,
    *,
    surface: str,
    response_intent: str | None,
) -> bool:
    if surface != "agent.chat.output":
        return False
    if response_intent not in {"docs_research", "market_research", "market_snapshot"}:
        return False
    if finding.rule_id != "arbitrary_io_request" or finding.matched_text.lower() not in {"http://", "https://"}:
        return False
    return (
        re.search(
            r"\b(run|execute|call|use|fetch|open|read|send|submit|request|connect|download|upload|curl|wget|shell|filesystem|network\s+request)\b",
            finding.sentence,
            flags=re.IGNORECASE,
        )
        is None
    )


def _usage_payload(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
    if metadata:
        payload.update(metadata)
    return payload


def _safe_reasoning_payload(
    phase: str,
    language: str = "en",
    *,
    tool_id: str | None = None,
    workflow_step: str | None = None,
) -> dict[str, Any]:
    normalized_phase = phase if phase in SAFE_REASONING_LABELS else "model"
    payload = {
        "phase": normalized_phase,
        "safe": True,
        "text": _safe_reasoning_text(normalized_phase, language),
    }
    if tool_id in TOOL_DEFINITIONS:
        payload["tool_id"] = tool_id
    if workflow_step in STRATEGY_BOT_WORKFLOW_STEPS:
        payload["workflow_step"] = workflow_step
    return payload


def _safe_reasoning_text(phase: str, language: str = "en") -> str:
    return SAFE_REASONING_LABELS[phase][_normalize_language(language)]


def _reasoning_phase_for_tool(tool_name: str) -> str:
    if tool_name == "knowledge_check":
        return "retrieval"
    if tool_name in {"create_backtest_plan", "run_backtest_preview", "run_backtest_variant_lab"}:
        return "backtest"
    if tool_name in {
        "generate_pine",
        "static_validate",
        "parallel_review",
    }:
        return "artifact"
    return "tool"


def _workflow_step_for_tool(tool_name: str) -> str | None:
    if tool_name == "generate_pine":
        return "generate_pine"
    if tool_name == "static_validate":
        return "static_validation"
    if tool_name in {"create_backtest_plan", "run_backtest_preview", "run_backtest_variant_lab"}:
        return "backtest_preview"
    if tool_name == "draft_bot":
        return "draft_bot_proposal"
    return None


def _normalize_language(language: str | None) -> str:
    return "vi" if language == "vi" else "en"


def _normalize_web_search(web_search: str | None) -> str:
    return web_search if web_search in {"off", "auto", "on"} else "auto"


def _classify_domain_scope(
    message_content: str,
    *,
    artifact_kinds: set[str] | None = None,
) -> DomainScopeDecision:
    return classify_domain_scope_compat(message_content, artifact_kinds=artifact_kinds)


def _domain_scope_blocked_message(language: str = "en") -> str:
    if _normalize_language(language) == "vi":
        return (
            "Mình chỉ hỗ trợ các việc trong Strategy Codebot: trading strategy/spec, Pine/MQL5 artifact, "
            "backtest preview, risk/robustness review, market research có citation, và các boundary review-only. "
            "Bạn có thể chuyển yêu cầu này thành một câu hỏi liên quan đến trading strategy hoặc workflow trong app."
        )
    return (
        "I can only help inside Strategy Codebot: trading strategy/spec work, Pine/MQL5 artifacts, backtest previews, "
        "risk/robustness review, citation-backed market research, and review-only workflow boundaries. "
        "Please reframe the request as a trading-strategy or app-workflow question."
    )


def _domain_scope_suggestions_payload(language: str, domain_scope: DomainScopeDecision) -> dict[str, Any]:
    return {
        "actions": [
            _suggestion_action(
                "show-strategy-format",
                _copy(language, "Cho mình mẫu spec", "Show a spec example"),
                _copy(
                    language,
                    "Cho mình một mẫu strategy spec ngắn để bắt đầu trong Strategy Codebot.",
                    "Show me a short strategy spec example to start inside Strategy Codebot.",
                ),
                "strategy",
                priority=1,
                reason=_copy(language, "Yêu cầu hiện tại nằm ngoài domain của app.", "The current request is outside the app domain."),
                risk_level="read_only",
            )
        ],
        "composer_blocks": [],
        "context": {
            "artifact_available": False,
            "artifact_kinds": [],
            "domain_scope": domain_scope.scope,
            "domain_scope_allowed": domain_scope.allowed,
            "domain_scope_reason": domain_scope.reason,
            "intent": "general_chat",
            "missing_fields": [],
            "readiness": "domain_scope_blocked",
        },
        "safe": True,
        "version": 1,
    }


def _suggestions_payload(
    *,
    response_intent: str,
    message_content: str,
    context_text: str,
    language: str,
    artifact_available: bool = False,
    artifact_kinds: set[str] | None = None,
    web_search: str = "auto",
    semantic_action: SemanticActionClassification | None = None,
    action_plan: ActionPlanDecision | None = None,
    workflow_enabled: bool = False,
    action_evaluation: ActionRegistryEvaluation | None = None,
) -> dict[str, Any]:
    language = _normalize_language(language)
    combined_context = f"{context_text}\n{message_content}".lower()
    normalized_message = message_content.lower()
    artifact_kinds = artifact_kinds or set()
    missing_fields = _strategy_missing_fields(combined_context) if workflow_enabled else []
    readiness = "ready_for_artifact" if not missing_fields else "needs_detail"
    actions: list[dict[str, Any]] = []
    composer_blocks = (
        _composer_block_suggestions(language, missing_fields)
        if workflow_enabled and response_intent in {"strategy_building", "artifact_generation"}
        else []
    )

    actions.extend(
        _registry_action_suggestions(
            language=language,
            context_text=combined_context,
            artifact_kinds=artifact_kinds,
            web_search=web_search,
            action_plan=action_plan,
            start_priority=-10,
            action_evaluation=action_evaluation,
        )
    )
    context_payload: dict[str, Any] = {
        "artifact_available": artifact_available,
        "artifact_kinds": sorted(artifact_kinds),
        "intent": response_intent,
        "missing_fields": missing_fields,
        "readiness": readiness,
    }
    if semantic_action is not None and semantic_action.source != "none":
        context_payload.update(semantic_action.context_payload())
    if action_plan is not None and action_plan.source != "none":
        context_payload.update(action_plan.payload())

    return {
        "actions": _dedupe_suggestions(sorted(actions, key=lambda item: item["priority"]))[:3],
        "composer_blocks": composer_blocks,
        "context": context_payload,
        "safe": True,
        "version": 1,
    }


def _registry_action_suggestions(
    *,
    language: str,
    context_text: str,
    artifact_kinds: set[str],
    web_search: str,
    action_plan: ActionPlanDecision | None,
    start_priority: int,
    action_evaluation: ActionRegistryEvaluation | None = None,
) -> list[dict[str, Any]]:
    if action_plan is None or not action_plan.is_active():
        return []
    planned_tool_ids: list[str] = []
    if action_plan.tool_id:
        planned_tool_ids.append(action_plan.tool_id)
    for tool_id in action_plan.suggested_actions:
        if tool_id not in planned_tool_ids:
            planned_tool_ids.append(tool_id)
    if not planned_tool_ids:
        return []

    registry_entries = {
        str(entry.get("tool_id")): entry
        for entry in (
            action_evaluation.payload
            if action_evaluation is not None
            else action_registry_payload(artifact_kinds=artifact_kinds, context_text=context_text, web_search=web_search)
        )
        if isinstance(entry.get("tool_id"), str)
    }
    actions: list[dict[str, Any]] = []
    priority = start_priority
    for tool_id in planned_tool_ids:
        entry = registry_entries.get(tool_id)
        if entry is None:
            continue
        actions.append(
            _suggestion_action(
                str(entry["id"]),
                str(entry["label"]),
                str(entry["prompt"]),
                str(entry["category"]),
                priority=priority,
                enabled=entry.get("available") is True,
                disabled_reason=_safe_string(entry.get("disabled_reason")),
                reason=action_plan.reason,
                risk_level=_safe_string(entry.get("risk_level")),
                tool_id=tool_id,
                required_inputs=[str(item) for item in entry.get("required_inputs", []) if isinstance(item, str)],
                artifact_kind=_safe_string(entry.get("artifact_kind")),
                next_state=_safe_string(entry.get("next_state")),
                presentation=entry.get("presentation") if isinstance(entry.get("presentation"), dict) else None,
            )
        )
        priority += 1
    return actions


def _dedupe_suggestions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for action in actions:
        action_id = str(action.get("id") or "")
        if action_id in seen:
            continue
        seen.add(action_id)
        deduped.append(action)
    return deduped


def _artifact_kind_matches(kind: str, terms: tuple[str, ...]) -> bool:
    normalized = kind.lower()
    return any(term in normalized for term in terms)


def _has_strategy_artifact_kind(artifact_kinds: set[str]) -> bool:
    return any(_artifact_kind_matches(kind, ("pine", "strategy_spec", "strategy", "code", "source_bundle")) for kind in artifact_kinds)


def _composer_block_suggestions(language: str, missing_fields: list[str]) -> list[dict[str, Any]]:
    missing = set(missing_fields)
    return [
        _composer_block("market", _copy(language, "Thị trường", "Market"), language, "market" in missing, 1),
        _composer_block("entry", _copy(language, "Vào lệnh", "Entry"), language, "entry" in missing, 2),
        _composer_block("exit", _copy(language, "Thoát lệnh", "Exit"), language, "exit" in missing, 3),
        _composer_block("risk", _copy(language, "Risk", "Risk"), language, "risk" in missing, 4),
    ]


def _composer_block(slot: str, label: str, language: str, emphasized: bool, priority: int) -> dict[str, Any]:
    return {
        "action": "insert_or_update_block",
        "category": slot,
        "emphasized": emphasized,
        "enabled": True,
        "id": f"block-{slot}",
        "kind": "composer_block",
        "label": label,
        "priority": priority,
        "slot": slot,
        "variants": _composer_variants(slot, language),
    }


def _composer_variants(slot: str, language: str) -> list[dict[str, str]]:
    if slot == "market":
        return [
            {
                "id": "crypto-eth",
                "label": "ETH / 1h",
                "insert_template": _copy(language, "Thị trường: ETHUSDT\nTimeframe: 1h", "Market: ETHUSDT\nTimeframe: 1h"),
            },
            {
                "id": "crypto-btc",
                "label": "BTC / 4h",
                "insert_template": _copy(language, "Thị trường: BTCUSDT\nTimeframe: 4h", "Market: BTCUSDT\nTimeframe: 4h"),
            },
        ]
    if slot == "entry":
        return [
            {
                "id": "ema-crossover",
                "label": "EMA crossover",
                "insert_template": _copy(
                    language,
                    "Entry rules:\n- Long khi EMA 20 cắt lên EMA 50\n- Xác nhận RSI trên 50",
                    "Entry rules:\n- Long when EMA 20 crosses above EMA 50\n- Confirm RSI is above 50",
                ),
            },
            {
                "id": "breakout",
                "label": "Breakout",
                "insert_template": _copy(
                    language,
                    "Entry rules:\n- Long khi giá phá vùng kháng cự gần nhất\n- Xác nhận bằng volume tăng",
                    "Entry rules:\n- Long when price breaks the nearest resistance\n- Confirm with rising volume",
                ),
            },
        ]
    if slot == "exit":
        return [
            {
                "id": "atr-stop",
                "label": "ATR stop",
                "insert_template": _copy(
                    language,
                    "Exit rules:\n- Stop-loss: 2 ATR\n- Take-profit: 2R\n- Thoát khi tín hiệu đảo chiều",
                    "Exit rules:\n- Stop-loss: 2 ATR\n- Take-profit: 2R\n- Exit on opposite signal",
                ),
            },
            {
                "id": "trailing",
                "label": "Trailing stop",
                "insert_template": _copy(
                    language,
                    "Exit rules:\n- Dùng trailing stop theo swing low/high\n- Chốt một phần ở 1R",
                    "Exit rules:\n- Use a trailing stop by swing low/high\n- Take partial profit at 1R",
                ),
            },
        ]
    return [
        {
            "id": "balanced",
            "label": _copy(language, "Balanced", "Balanced"),
            "insert_template": _copy(
                language,
                "Risk rules:\n- Risk 1% equity mỗi lệnh\n- Max 1 vị thế mở\n- Không vào lệnh khi biến động bất thường",
                "Risk rules:\n- Risk 1% equity per trade\n- Max 1 open position\n- Avoid entries during abnormal volatility",
            ),
        },
        {
            "id": "conservative",
            "label": _copy(language, "Conservative", "Conservative"),
            "insert_template": _copy(
                language,
                "Risk rules:\n- Risk 0.5% equity mỗi lệnh\n- Stop-loss bắt buộc\n- Bỏ qua setup nếu R:R dưới 1.5",
                "Risk rules:\n- Risk 0.5% equity per trade\n- Stop-loss is required\n- Skip setups below 1.5R",
            ),
        },
    ]


def _suggestion_action(
    suggestion_id: str,
    label: str,
    prompt: str,
    category: str,
    *,
    priority: int,
    enabled: bool = True,
    disabled_reason: str | None = None,
    reason: str | None = None,
    risk_level: str | None = None,
    tool_id: str | None = None,
    required_inputs: list[str] | None = None,
    artifact_kind: str | None = None,
    next_state: str | None = None,
    presentation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "action": "send_prompt",
        "category": category,
        "enabled": enabled,
        "id": suggestion_id,
        "kind": "chat_action",
        "label": label,
        "priority": priority,
        "prompt": prompt,
    }
    if disabled_reason:
        payload["disabled_reason"] = disabled_reason
    if reason:
        payload["reason"] = reason
    if risk_level:
        payload["risk_level"] = risk_level
    if tool_id:
        payload["tool_id"] = tool_id
    if required_inputs:
        payload["required_inputs"] = required_inputs
    if artifact_kind:
        payload["artifact_kind"] = artifact_kind
    if next_state:
        payload["next_state"] = next_state
    if presentation:
        payload["presentation"] = presentation
    return payload


def _missing_field_action(field: str, language: str, *, priority: int) -> dict[str, Any]:
    labels = {
        "entry": _copy(language, "Thêm entry rules", "Add entry rules"),
        "exit": _copy(language, "Thêm exit rules", "Add exit rules"),
        "market": _copy(language, "Thêm market", "Add market"),
        "risk": _copy(language, "Thêm risk rules", "Add risk rules"),
    }
    prompts = {
        "entry": _copy(language, "Thêm entry rules rõ ràng cho strategy context hiện tại.", "Add clear entry rules to the current strategy context."),
        "exit": _copy(language, "Thêm exit rules rõ ràng cho strategy context hiện tại.", "Add clear exit rules to the current strategy context."),
        "market": _copy(language, "Thêm market, symbol và timeframe cho strategy context hiện tại.", "Add market, symbol, and timeframe to the current strategy context."),
        "risk": _copy(language, "Thêm risk rules gồm stop-loss, take-profit và position sizing.", "Add risk rules with stop-loss, take-profit, and position sizing."),
    }
    return _suggestion_action(f"add-{field}", labels[field], prompts[field], field, priority=priority)


def _strategy_missing_fields(context: str) -> list[str]:
    checks = {
        "market": ("market", "symbol", "thị trường", "timeframe", "khung thời gian", "btcusdt", "ethusdt"),
        "entry": ("entry", "enter", "long when", "short when", "vào lệnh", "mua khi", "bán khi", "crossover", "breakout"),
        "exit": ("exit", "stop-loss", "take-profit", "thoát lệnh", "chốt lời", "cắt lỗ", "trailing"),
        "risk": ("risk", "position sizing", "1%", "0.5%", "rủi ro", "quản trị", "max position"),
    }
    return [field for field, terms in checks.items() if not any(term in context for term in terms)]


def _strategy_bot_missing_fields(context: str) -> list[str]:
    evidence_lines: list[str] = []
    for line in context.splitlines() or [context]:
        lowered = line.lower()
        if any(marker in lowered for marker in ("ví dụ", "vi du", "for example", "example:", "e.g.", "vd:")):
            continue
        if "?" in line and any(
            label in lowered
            for label in (
                "market",
                "symbol",
                "timeframe",
                "style",
                "risk",
                "thị trường",
                "mã giao dịch",
                "khung thời gian",
                "phong cách",
                "rủi ro",
            )
        ):
            continue
        evidence_lines.append(line)
    evidence_context = "\n".join(evidence_lines)
    normalized = evidence_context.lower()
    symbol_pattern = re.compile(
        r"\b(?:BTC|ETH|SOL|BNB|XRP|EUR|GBP|JPY|XAU|AAPL|TSLA|NVDA)"
        r"(?:[/:-]?(?:USD|USDT|USDC|BTC|ETH|JPY))?\b",
        re.IGNORECASE,
    )
    timeframe_pattern = re.compile(r"\b(?:[1-9]\d?\s?(?:m|min|h|d|w)|[1-9]\d?[mhdw]|daily|hourly|weekly)\b")
    market_pattern = re.compile(
        r"\b(?:crypto|forex|stock|equity|futures|chứng khoán|co phieu|cổ phiếu)\b",
        re.IGNORECASE,
    )
    style_pattern = re.compile(r"\b(?:trend(?: following)?|mean reversion|breakout|scalping|dca)\b", re.IGNORECASE)
    risk_pattern = re.compile(
        r"\b(?:conservative|balanced|moderate|aggressive|low risk|medium risk|high risk|an toàn|can bang|cân bằng|mạo hiểm)\b"
        r"|(?:\brisk\b|\brủi ro\b).{0,24}\b\d+(?:\.\d+)?\s?%",
        re.IGNORECASE,
    )
    style_matches = {match.group(0).lower() for match in style_pattern.finditer(evidence_context)}
    has_style_catalog = len(style_matches) > 1 and any(
        label in normalized for label in ("style", "phong cách", "loại chiến lược")
    )
    checks = {
        "market": bool(market_pattern.search(evidence_context)),
        "symbol": bool(symbol_pattern.search(evidence_context)),
        "timeframe": bool(timeframe_pattern.search(normalized)),
        "style": bool(style_matches) and not has_style_catalog,
        "risk_preference": bool(risk_pattern.search(evidence_context)),
    }
    missing = [field for field, is_present in checks.items() if not is_present]
    return missing


def _strategy_bot_missing_after_structured_evidence(
    missing_fields: list[str],
    *,
    structured_strategy_values: dict[str, Any] | None,
    tool_result: dict[str, Any],
) -> list[str]:
    if _strategy_spec_satisfies_strategy_inputs(tool_result.get("strategy_spec")):
        return []
    present = _strategy_bot_present_fields_from_values(structured_strategy_values or {})
    if not present:
        return missing_fields
    return [field for field in missing_fields if field not in present]


def _strategy_bot_present_fields_from_values(values: dict[str, Any]) -> set[str]:
    present: set[str] = set()
    for field in STRATEGY_BOT_REQUIRED_INPUT_FIELDS:
        value = values.get(field)
        if not _is_empty_workflow_value(value):
            present.add(field)
    return present


def _strategy_spec_satisfies_strategy_inputs(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = (
        value.get("market"),
        value.get("timeframe"),
        value.get("entry_rules"),
        value.get("exit_rules"),
        value.get("risk_rules"),
    )
    if any(_is_empty_workflow_value(item) for item in required):
        return False
    return True


def _strategy_bot_lexical_hint(context: str) -> bool:
    return any(
        term in context
        for term in (
            "bot",
            "paper bot",
            "paper simulation",
            "paper runtime",
            "bot simulation",
            "chạy bot",
            "tạo bot",
        )
    )


def _strategy_bot_workflow_lexical_hint(
    message_content: str,
    *,
    context_text: str,
    artifact_kinds: set[str],
    tool_name: str | None = None,
) -> bool:
    if tool_name in {
        "generate_pine",
        "static_validate",
        "create_backtest_plan",
        "run_backtest_preview",
        "get_backtest_summary",
        "build_robustness_report",
        "draft_bot",
    }:
        combined_for_tool = f"{message_content}\n{context_text}".lower()
        return tool_name == "draft_bot" or _strategy_bot_lexical_hint(combined_for_tool)
    combined = f"{message_content}\n{context_text}".lower()
    bot_signal = _strategy_bot_lexical_hint(combined)
    strategy_signal = any(
        term in combined
        for term in (
            "strategy",
            "chiến lược",
            "pine",
            "backtest",
            "entry",
            "exit",
        )
    ) or _has_strategy_artifact_kind(artifact_kinds)
    return bot_signal and strategy_signal


def _proposal_missing_setup_fields(
    proposal: dict[str, Any] | None,
    explicit_missing: list[str],
) -> list[str]:
    if explicit_missing:
        return explicit_missing
    if proposal is None:
        return list(STRATEGY_BOT_SETUP_FIELDS)
    data_subscriptions = proposal.get("data_subscriptions")
    return bot_required_missing(
        broker_connection_id=_safe_string(proposal.get("broker_connection_id")),
        account_id=_safe_string(proposal.get("account_id")),
        risk_policy_id=_safe_string(proposal.get("risk_policy_id")),
        strategy_id=_safe_string(proposal.get("strategy_id")),
        data_subscriptions=data_subscriptions if isinstance(data_subscriptions, list) else [],
    )


def _workflow_ref(refs: dict[str, str], key: str, value: Any) -> None:
    text = _safe_string(value)
    if text:
        refs[key] = text


STRATEGY_BOT_SKIP_PINE_STEPS = ("generate_pine", "static_validation")
STRATEGY_BOT_SKIP_BACKTEST_STEPS = ("backtest_preview", "evidence_review")
STRATEGY_BOT_SKIP_STEP_REASONS = {
    "generate_pine": "User asked to skip Pine generation.",
    "static_validation": "No generated Pine artifact to validate.",
    "backtest_preview": "User asked to skip backtest preview.",
    "evidence_review": "Draft-only review without backtest evidence.",
}
STRATEGY_BOT_SKIP_PINE_TERMS = (
    "không cần pine",
    "khong can pine",
    "không muốn pine",
    "khong muon pine",
    "bỏ pine",
    "bo pine",
    "skip pine",
    "without pine",
    "existing strategy spec",
    "existing spec",
    "spec có sẵn",
    "spec co san",
    "strategy spec có sẵn",
    "strategy spec co san",
)
STRATEGY_BOT_SKIP_PINE_NEGATED_TERMS = (
    "do not skip pine",
    "don't skip pine",
    "dont skip pine",
    "not skip pine",
    "không bỏ pine",
    "khong bo pine",
    "đừng bỏ pine",
    "dung bo pine",
    "đừng skip pine",
    "dung skip pine",
    "no existing spec",
    "no existing strategy spec",
    "không có spec có sẵn",
    "khong co spec co san",
    "không có existing spec",
    "khong co existing spec",
)
STRATEGY_BOT_SKIP_BACKTEST_TERMS = (
    "bỏ backtest",
    "bo backtest",
    "không cần backtest",
    "khong can backtest",
    "skip backtest",
    "without backtest",
    "bỏ backtest preview",
    "bo backtest preview",
    "skip backtest preview",
    "draft proposal only",
    "chỉ draft proposal",
    "chi draft proposal",
    "chỉ draft bot proposal",
    "chi draft bot proposal",
)
STRATEGY_BOT_SKIP_BACKTEST_NEGATED_TERMS = (
    "do not skip backtest",
    "don't skip backtest",
    "dont skip backtest",
    "not skip backtest",
    "do not skip backtest preview",
    "don't skip backtest preview",
    "dont skip backtest preview",
    "not skip backtest preview",
    "không bỏ backtest",
    "khong bo backtest",
    "đừng bỏ backtest",
    "dung bo backtest",
    "đừng skip backtest",
    "dung skip backtest",
)


def _has_explicit_skip_intent(
    normalized: str,
    *,
    positive_terms: tuple[str, ...],
    negated_terms: tuple[str, ...],
) -> bool:
    return any(term in normalized for term in positive_terms) and not any(
        term in normalized for term in negated_terms
    )


def _append_optional_skip_steps(
    skipped: list[str],
    reasons: dict[str, str],
    steps: tuple[str, ...],
) -> None:
    for step in steps:
        if step not in STRATEGY_BOT_OPTIONAL_STEPS or step in skipped:
            continue
        skipped.append(step)
        reasons[step] = STRATEGY_BOT_SKIP_STEP_REASONS[step]


def _strategy_bot_skip_preferences(context: str, *, has_pine: bool) -> tuple[list[str], dict[str, str]]:
    normalized = context.lower()
    skipped: list[str] = []
    reasons: dict[str, str] = {}

    skip_pine = not has_pine and _has_explicit_skip_intent(
        normalized,
        positive_terms=STRATEGY_BOT_SKIP_PINE_TERMS,
        negated_terms=STRATEGY_BOT_SKIP_PINE_NEGATED_TERMS,
    )
    if skip_pine:
        _append_optional_skip_steps(skipped, reasons, STRATEGY_BOT_SKIP_PINE_STEPS)

    skip_backtest = _has_explicit_skip_intent(
        normalized,
        positive_terms=STRATEGY_BOT_SKIP_BACKTEST_TERMS,
        negated_terms=STRATEGY_BOT_SKIP_BACKTEST_NEGATED_TERMS,
    )
    if skip_backtest:
        _append_optional_skip_steps(skipped, reasons, STRATEGY_BOT_SKIP_BACKTEST_STEPS)

    return skipped, reasons


_WORKFLOW_BLOCKING_RESPONSE_INTENTS = frozenset({"capability_help", "docs_research", "market_research", "market_snapshot"})


def _has_active_workflow(
    repository: ConversationRepository,
    auth: AuthContext,
    conversation_id: str,
    workflow_id: str,
) -> bool:
    tasks = repository.list_workflow_tasks(auth, conversation_id) or []
    return any(
        task.workflow_id == workflow_id and task.status not in WORKFLOW_TASK_RESOLVED_STATUSES
        for task in tasks
    )


def _is_empty_workflow_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _workflow_task_values_for_conversation(
    repository: ConversationRepository,
    auth: AuthContext,
    conversation_id: str,
    workflow_id: str,
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for task in repository.list_workflow_tasks(auth, conversation_id) or []:
        if task.workflow_id != workflow_id:
            continue
        state = workflow_task_state(task)
        task_values = state.get("values")
        if not isinstance(task_values, dict):
            continue
        for key, value in task_values.items():
            if isinstance(key, str) and not _is_empty_workflow_value(value):
                values[key] = value
    return values


def _workflow_task_values_context_text(values: dict[str, Any]) -> str:
    lines = []
    for key in sorted(values):
        value = values.get(key)
        if not isinstance(key, str) or _is_empty_workflow_value(value):
            continue
        lines.append(f"- {key}: {value}")
    if not lines:
        return ""
    return "Durable workflow task values:\n" + "\n".join(lines)


def _workflow_task_resume_action_plan(
    payload: dict[str, Any] | None,
    workflow_task_values: dict[str, Any],
) -> ActionPlanDecision | None:
    if not isinstance(payload, dict) or payload.get("task_template_id") != STRATEGY_SPEC_NEXT_STEP_TASK_ID:
        return None
    if workflow_task_strategy_spec_next_action(payload) != STRATEGY_SPEC_NEXT_ACTION_GENERATE_PINE:
        return None
    strategy_spec = _strategy_spec_from_workflow_task_values(workflow_task_values)
    if strategy_spec is None:
        return None
    return ActionPlanDecision(
        decision="call_tool",
        intent_id="generate_pine",
        confidence=1.0,
        source="workflow_task_resume",
        tool_id="generate_pine",
        arguments={"strategy_spec": strategy_spec},
        suggested_actions=("generate_pine",),
        reason="User approved generating Pine after reviewing the strategy spec.",
    )


def _selected_action_plan(
    selected_action: dict[str, Any] | None,
    *,
    repository: ConversationRepository,
    artifact_store: LocalArtifactStore | None,
    auth: AuthContext,
    conversation_id: str,
    message_content: str,
    artifact_kinds: set[str],
    context_text: str,
    web_search: str,
    action_evaluation: ActionRegistryEvaluation | None = None,
) -> ActionPlanDecision | None:
    if not isinstance(selected_action, dict):
        return None
    action_id = _safe_string(selected_action.get("action_id"))
    tool_id = _safe_string(selected_action.get("tool_id"))
    if not action_id or not tool_id:
        return None
    entry = registry_entry_for_tool(tool_id)
    if entry is None or entry.action_id != action_id:
        return None
    evaluation = action_evaluation or evaluate_action_registry(
        artifact_kinds=artifact_kinds,
        context_text=context_text,
        web_search=web_search,
    )
    if tool_id not in evaluation.available_tool_ids:
        return None
    if tool_id not in {"run_backtest_preview", "create_backtest_plan"}:
        return None
    arguments = _backtest_plan_arguments_from_conversation(
        repository,
        artifact_store,
        auth,
        conversation_id,
        prompt=message_content,
        source_message_id=_safe_string(selected_action.get("source_message_id")),
    )
    if arguments is None:
        return None
    return ActionPlanDecision(
        decision="call_tool",
        intent_id="backtest_preview",
        confidence=1.0,
        source="selected_action",
        tool_id="create_backtest_plan",
        arguments=arguments,
        reason="User selected the Backtest Preview workflow action.",
    )


def _chat_decision_for_selected_action(
    chat_decision: ChatIntentDecision,
    action_plan: ActionPlanDecision | None,
) -> ChatIntentDecision:
    if action_plan is None or action_plan.source != "selected_action" or action_plan.tool_id != "create_backtest_plan":
        return chat_decision
    return ChatIntentDecision(
        response_intent="backtest_preview",
        action="call_tool",
        model_stage=MODEL_STAGE_PINE_CODE_GENERATION,
        confidence=1.0,
        source="selected_action",
        tool_id="create_backtest_plan",
        auto_chain=False,
        current_context_required=False,
        domain_scope="trading_workflow",
        workflow_intent=None,
        used_signals=("selected_action", "has_strategy_artifact"),
    )


def _backtest_plan_arguments_from_conversation(
    repository: ConversationRepository,
    artifact_store: LocalArtifactStore | None,
    auth: AuthContext,
    conversation_id: str,
    *,
    prompt: str,
    source_message_id: str | None = None,
) -> dict[str, Any] | None:
    anchor_before = _selected_action_anchor_before(repository, auth, conversation_id, source_message_id)
    if source_message_id and anchor_before is None:
        return None
    strategy_spec_record = repository.get_latest_strategy_spec_for_conversation(
        auth,
        conversation_id,
        before=anchor_before,
    )
    strategy_spec = strategy_spec_record.payload_json if strategy_spec_record is not None else None
    if not isinstance(strategy_spec, dict) and anchor_before is None:
        strategy_spec = _strategy_spec_from_workflow_task_values(
            _workflow_task_values_for_conversation(repository, auth, conversation_id, STRATEGY_BOT_WORKFLOW_ID)
        )
    pine_code = _latest_pine_artifact_content(
        repository,
        artifact_store,
        auth,
        conversation_id,
        before=anchor_before,
    )
    if not isinstance(strategy_spec, dict) or not pine_code:
        return None
    return {
        "prompt": prompt,
        "strategy_spec": strategy_spec,
        "pine_code": pine_code,
    }


def _selected_action_anchor_before(
    repository: ConversationRepository,
    auth: AuthContext,
    conversation_id: str,
    source_message_id: str | None,
) -> datetime | None:
    if not source_message_id:
        return None
    return next(
        (
            message.created_at
            for message in repository.list_messages(auth, conversation_id)
            if message.id == source_message_id and message.conversation_id == conversation_id
        ),
        None,
    )


def _latest_pine_artifact_content(
    repository: ConversationRepository,
    artifact_store: LocalArtifactStore | None,
    auth: AuthContext,
    conversation_id: str,
    *,
    before: datetime | None = None,
) -> str | None:
    if artifact_store is None:
        return None
    artifact = _latest_conversation_artifact(
        repository,
        auth,
        conversation_id,
        kinds={"pine_file", "pine_strategy_source"},
        before=before,
    )
    if artifact is None:
        return None
    try:
        content = artifact_store.read_content(artifact)
    except Exception:
        return None
    return content if isinstance(content, str) and content.strip() else None


def _latest_conversation_artifact(
    repository: ConversationRepository,
    auth: AuthContext,
    conversation_id: str,
    *,
    kinds: set[str],
    before: datetime | None = None,
) -> ArtifactRecord | None:
    return repository.get_latest_conversation_artifact(
        auth,
        conversation_id,
        kinds=kinds,
        before=before,
    )


def _strategy_spec_from_workflow_task_values(values: dict[str, Any]) -> dict[str, Any] | None:
    market = _safe_string(values.get("market"))
    symbol = _safe_string(values.get("symbol"))
    timeframe = _safe_string(values.get("timeframe"))
    style = _safe_string(values.get("style"))
    risk_preference = _safe_string(values.get("risk_preference"))
    if not (market and symbol and timeframe and style and risk_preference):
        return None

    entry_exit_idea = _safe_string(values.get("entry_exit_idea"))
    style_text = style.replace("_", " ")
    entry_rules = _strategy_entry_rules_from_task_values(style_text, entry_exit_idea)
    exit_rules = _strategy_exit_rules_from_task_values(entry_exit_idea)
    risk_rules, position_sizing, stop_loss, take_profit = _strategy_risk_rules_from_task_values(risk_preference)
    assumptions = [
        "Generated from approved Strategy -> Paper Bot workflow task values.",
        "Review-only Pine artifact; not TradingView proof, backtest evidence, or live trading approval.",
        "Paper simulation boundary only; no broker execution.",
    ]
    if entry_exit_idea:
        assumptions.append(f"User entry/exit idea: {entry_exit_idea}")
    return {
        "target_platform": "pine_v6",
        "script_type": "strategy",
        "market": market,
        "symbol": symbol,
        "timeframe": timeframe,
        "runtime_targets": ["pine_v6"],
        "entry_rules": entry_rules,
        "exit_rules": exit_rules,
        "risk_rules": risk_rules,
        "position_sizing": position_sizing,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "assumptions": assumptions,
        "constraints": [
            f"Strategy style: {style_text}.",
            "Generated Pine should remain review-only until validation/backtest evidence exists.",
        ],
    }


def _strategy_entry_rules_from_task_values(style: str, entry_exit_idea: str | None) -> list[str]:
    idea = (entry_exit_idea or "").lower()
    rules: list[str] = []
    if "moving average" in idea or "ma " in f"{idea} " or "ema" in idea or "sma" in idea:
        rules.append("Enter long when the fast moving average crosses above the slow moving average on a confirmed bar.")
    elif "breakout" in style or "breakout" in idea:
        rules.append("Enter long when price confirms a bullish breakout on a confirmed bar.")
    elif "mean" in style or "reversion" in style:
        rules.append("Enter long when price shows a mean-reversion recovery signal on a confirmed bar.")
    elif "dca" in style:
        rules.append("Enter staged long entries only when review rules confirm the setup.")
    else:
        rules.append("Enter long when the trend-following signal turns bullish on a confirmed bar.")
    if "breakout" in style and "breakout" not in rules[0].lower():
        rules.append("Use the breakout style as confirmation before accepting the entry.")
    return rules


def _strategy_exit_rules_from_task_values(entry_exit_idea: str | None) -> list[str]:
    idea = (entry_exit_idea or "").lower()
    if "moving average" in idea or "ma " in f"{idea} " or "ema" in idea or "sma" in idea:
        return [
            "Exit when the fast moving average crosses below the slow moving average on a confirmed bar.",
            "Use protective stop-loss and take-profit levels for review-only risk control.",
        ]
    return [
        "Exit when the opposite signal appears on a confirmed bar.",
        "Use protective stop-loss and take-profit levels for review-only risk control.",
    ]


def _strategy_risk_rules_from_task_values(risk_preference: str) -> tuple[list[str], str, str, str]:
    normalized = risk_preference.lower()
    if "aggressive" in normalized:
        risk_pct = "2%"
        stop_loss = "2% below average entry price"
        take_profit = "4% above average entry price"
    elif "conservative" in normalized:
        risk_pct = "0.5%"
        stop_loss = "1% below average entry price"
        take_profit = "2% above average entry price"
    else:
        risk_pct = "1%"
        stop_loss = "1.5% below average entry price"
        take_profit = "3% above average entry price"
    position_sizing = f"Risk {risk_pct} account equity per trade"
    return (
        [
            f"{position_sizing}; review before any simulation.",
            "No broker execution or live order placement is allowed from this workflow.",
        ],
        position_sizing,
        stop_loss,
        take_profit,
    )


def _should_create_workflow_from_decision(chat_decision: ChatIntentDecision, workflow_id: str) -> bool:
    if _is_classifier_fallback_source(chat_decision.source) and chat_decision.source not in WORKFLOW_KICKOFF_FALLBACK_SOURCES:
        return False
    workflow_intent = normalize_workflow_intent(chat_decision.workflow_intent)
    if workflow_id_for_intent(workflow_intent) != workflow_id:
        return False
    if not response_intent_allows_workflow(chat_decision.response_intent, workflow_intent):
        return False
    policy = workflow_intent_policy(workflow_intent)
    creation_intents = policy.get("creation_response_intents", [])
    if chat_decision.response_intent not in creation_intents:
        return False
    return not bool(policy.get("requires_explicit_intent")) or workflow_intent is not None


def _workflow_kickoff_fallback_decision(
    message_content: str,
    *,
    source: str,
) -> ChatIntentDecision | None:
    if source not in WORKFLOW_KICKOFF_FALLBACK_SOURCES:
        return None
    fallback_policy = classifier_fallback_policy()
    if not fallback_policy.get("safe_workflow_kickoff_allowed"):
        return None
    regex_evidence = _chat_regex_evidence(message_content)
    evidence_signals = set(evidence_signals_from_regex(regex_evidence))
    for workflow_intent in WORKFLOW_INTENTS:
        policy = workflow_timeout_fallback_policy(workflow_intent)
        if not policy.get("enabled"):
            continue
        required = set(normalize_evidence_signals(policy.get("required_evidence_signals", [])))
        denied = set(normalize_evidence_signals(policy.get("denied_evidence_signals", [])))
        if not required.issubset(evidence_signals):
            continue
        if denied & evidence_signals:
            continue
        response_intent = policy.get("response_intent")
        domain_scope = policy.get("domain_scope")
        if not isinstance(response_intent, str) or response_intent not in RESPONSE_INTENTS:
            continue
        if not isinstance(domain_scope, str) or domain_scope not in DOMAIN_SCOPES:
            continue
        if source == WORKFLOW_TIMEOUT_FALLBACK_SOURCE:
            reason = "Classifier timed out; registry evidence allowed input-only workflow kickoff."
        else:
            reason = "Classifier fell back; registry evidence allowed input-only workflow kickoff."
        return ChatIntentDecision(
            response_intent=response_intent,
            action="answer",
            model_stage=_model_stage_for_intent(response_intent),
            confidence=RESPONSE_INTENT_FALLBACK_CONFIDENCE,
            source=source,
            auto_chain=False,
            current_context_required=False,
            domain_scope=domain_scope,
            workflow_intent=workflow_intent,
            used_signals=tuple(sorted(evidence_signals)),
            reasons=(reason,),
        )
    return None


def _workflow_timeout_fallback_decision(message_content: str) -> ChatIntentDecision | None:
    return _workflow_kickoff_fallback_decision(
        message_content,
        source=WORKFLOW_TIMEOUT_FALLBACK_SOURCE,
    )


def _should_resume_active_workflow(chat_decision: ChatIntentDecision, workflow_id: str, *, active: bool) -> bool:
    if not active or _is_classifier_fallback_source(chat_decision.source):
        return False
    if chat_decision.response_intent in _WORKFLOW_BLOCKING_RESPONSE_INTENTS:
        return False
    workflow_intent = normalize_workflow_intent(chat_decision.workflow_intent)
    if workflow_intent is not None and workflow_id_for_intent(workflow_intent) == workflow_id:
        return bool(workflow_intent_policy(workflow_intent).get("resume_existing"))
    for candidate_intent in WORKFLOW_INTENTS:
        policy = workflow_intent_policy(candidate_intent)
        if policy.get("workflow_id") != workflow_id or not policy.get("resume_existing"):
            continue
        creation_intents = policy.get("creation_response_intents", [])
        if chat_decision.response_intent in creation_intents:
            return True
    return chat_decision.response_intent == "general_chat" and chat_decision.domain_scope != "product_help"


def _workflow_task_resume_chat_decision(payload: dict[str, Any]) -> ChatIntentDecision:
    task_template_id = _safe_string(payload.get("task_template_id"))
    resume_intent = _safe_string(payload.get("resume_intent"))
    response_intent = resume_intent if resume_intent in RESPONSE_INTENTS else "strategy_building"
    reasons = ["Resuming from a completed workflow task."]
    used_signals = ["workflow_task_resume"]
    if task_template_id == STRATEGY_SPEC_NEXT_STEP_TASK_ID:
        next_action = workflow_task_strategy_spec_next_action(payload)
        if next_action:
            used_signals.append("strategy_spec_next_action")
        if next_action == STRATEGY_SPEC_NEXT_ACTION_GENERATE_PINE:
            response_intent = "pine_generation"
            reasons.append("User approved generating Pine after reviewing the strategy spec.")
        else:
            response_intent = "strategy_building"
            if next_action == STRATEGY_SPEC_NEXT_ACTION_SKIP_PINE:
                reasons.append("User chose to skip Pine generation.")
            else:
                reasons.append("User requested a revision or custom follow-up before Pine generation.")
    return ChatIntentDecision(
        response_intent=response_intent,
        action="answer",
        model_stage=_model_stage_for_intent(response_intent),
        confidence=1.0,
        source="workflow_task_resume",
        auto_chain=False,
        current_context_required=False,
        domain_scope="trading_workflow",
        workflow_intent="strategy_to_paper_bot_simulation",
        reasons=tuple(reasons),
        used_signals=tuple(used_signals),
    )


def _maybe_strategy_bot_workflow_payload(
    *,
    repository: ConversationRepository,
    auth: AuthContext,
    conversation_id: str,
    chat_decision: ChatIntentDecision,
    message_content: str,
    context_text: str,
    artifact_kinds: set[str],
    tool_name: str | None = None,
    tool_result: dict[str, Any] | None = None,
    structured_strategy_values: dict[str, Any] | None = None,
    completed_strategy_spec: bool = False,
) -> dict[str, Any] | None:
    active = _has_active_workflow(repository, auth, conversation_id, STRATEGY_BOT_WORKFLOW_ID)
    if not (
        _should_create_workflow_from_decision(chat_decision, STRATEGY_BOT_WORKFLOW_ID)
        or _should_resume_active_workflow(chat_decision, STRATEGY_BOT_WORKFLOW_ID, active=active)
    ):
        return None
    return _strategy_bot_workflow_payload(
        message_content=message_content,
        context_text=context_text,
        artifact_kinds=artifact_kinds,
        tool_name=tool_name,
        tool_result=tool_result,
        structured_strategy_values=structured_strategy_values,
        completed_strategy_spec=completed_strategy_spec,
        force=True,
    )


def _strategy_bot_workflow_payload(
    *,
    message_content: str,
    context_text: str,
    artifact_kinds: set[str],
    tool_name: str | None = None,
    tool_result: dict[str, Any] | None = None,
    structured_strategy_values: dict[str, Any] | None = None,
    completed_strategy_spec: bool = False,
    force: bool = False,
) -> dict[str, Any] | None:
    if not force and not _strategy_bot_workflow_lexical_hint(
        message_content,
        context_text=context_text,
        artifact_kinds=artifact_kinds,
        tool_name=tool_name,
    ):
        return None
    result = tool_result if isinstance(tool_result, dict) else {}
    combined_context = f"{message_content}\n{context_text}"
    strategy_missing = _strategy_bot_missing_fields(combined_context)
    strategy_missing = _strategy_bot_missing_after_structured_evidence(
        strategy_missing,
        structured_strategy_values=structured_strategy_values,
        tool_result=result,
    )

    proposal = result.get("bot_proposal") if isinstance(result.get("bot_proposal"), dict) else None
    explicit_missing = [
        field
        for field in result.get("missing_inputs", [])
        if isinstance(field, str) and field.strip()
    ] if isinstance(result.get("missing_inputs"), list) else []
    proposal_id = _safe_string(result.get("proposal_id"))
    if proposal is not None:
        proposal_id = proposal_id or _safe_string(proposal.get("proposal_id")) or _safe_string(proposal.get("id"))
    setup_missing = _proposal_missing_setup_fields(proposal, explicit_missing) if proposal is not None else []

    has_pine = bool({"pine_file", "pine_strategy_source"} & artifact_kinds) or tool_name in {
        "generate_pine",
        "create_backtest_plan",
        "run_backtest_preview",
    }
    evidence_artifact_kinds = {
        BACKTEST_PLAN_ARTIFACT_KIND,
        BACKTEST_RUN_METADATA_ARTIFACT_KIND,
        BACKTEST_REPORT_ARTIFACT_KIND,
        ROBUSTNESS_REPORT_ARTIFACT_KIND,
        RISK_GATE_REPORT_ARTIFACT_KIND,
    }
    has_validation = "validation_report" in artifact_kinds or bool(evidence_artifact_kinds & artifact_kinds) or tool_name in {
        "static_validate",
        "create_backtest_plan",
        "run_backtest_preview",
    } or _safe_string(result.get("validation_artifact_id")) is not None
    has_backtest_preview = bool(evidence_artifact_kinds & artifact_kinds) or tool_name in {
        "create_backtest_plan",
        "run_backtest_preview",
        "get_backtest_summary",
        "build_robustness_report",
    }
    has_evidence_review = bool(
        {BACKTEST_REPORT_ARTIFACT_KIND, ROBUSTNESS_REPORT_ARTIFACT_KIND, RISK_GATE_REPORT_ARTIFACT_KIND}
        & artifact_kinds
    ) or tool_name in {"get_backtest_summary", "build_robustness_report"}
    skipped_steps, step_reasons = _strategy_bot_skip_preferences(combined_context, has_pine=has_pine)
    skipped = set(skipped_steps)
    pine_satisfied = has_pine or "generate_pine" in skipped
    validation_satisfied = has_validation or "static_validation" in skipped
    backtest_satisfied = has_backtest_preview or "backtest_preview" in skipped
    evidence_satisfied = has_evidence_review or "evidence_review" in skipped
    skipped_evidence_gate = (
        ("backtest_preview" in skipped and not has_backtest_preview)
        or ("evidence_review" in skipped and not has_evidence_review)
    )
    start_allowed = proposal is not None and not setup_missing and not skipped_evidence_gate

    has_downstream_strategy_work = (
        has_pine or has_validation or has_backtest_preview or has_evidence_review or proposal is not None
    )
    if has_downstream_strategy_work and strategy_missing:
        strategy_missing = []

    completed: list[str] = []
    if not strategy_missing:
        completed.append("collect_strategy_inputs")
    has_existing_spec_skip = not strategy_missing and "generate_pine" in skipped
    draft_strategy_spec_satisfied = (
        completed_strategy_spec or has_downstream_strategy_work or has_existing_spec_skip
    )
    if draft_strategy_spec_satisfied:
        completed.append("draft_strategy_spec")
    if has_pine:
        completed.append("generate_pine")
    if has_validation:
        completed.append("static_validation")
    if has_backtest_preview:
        completed.append("backtest_preview")
    if has_evidence_review:
        completed.append("evidence_review")
    if proposal is not None:
        completed.append("draft_bot_proposal")
    completed_steps = [step for step in STRATEGY_BOT_WORKFLOW_STEPS if step in set(completed)]

    if strategy_missing:
        current_step = "collect_strategy_inputs"
    elif not draft_strategy_spec_satisfied:
        current_step = "draft_strategy_spec"
    elif not pine_satisfied:
        current_step = "generate_pine"
    elif not validation_satisfied:
        current_step = "static_validation"
    elif not backtest_satisfied:
        current_step = "backtest_preview"
    elif not evidence_satisfied:
        current_step = "evidence_review"
    elif proposal is None:
        current_step = "draft_bot_proposal"
    else:
        current_step = "complete_setup_confirm_start"

    if has_evidence_review:
        evidence_status = "reviewable_with_caveats"
    elif has_validation or has_backtest_preview:
        evidence_status = "needs_validation_or_robustness_check"
    else:
        evidence_status = "insufficient_evidence"

    artifact_refs: dict[str, str] = {}
    if tool_name == "generate_pine":
        _workflow_ref(artifact_refs, "pine_artifact_id", result.get("artifact_id"))
    elif tool_name == "static_validate":
        _workflow_ref(artifact_refs, "validation_artifact_id", result.get("artifact_id"))
    elif tool_name == "create_backtest_plan":
        _workflow_ref(artifact_refs, "backtest_plan_artifact_id", result.get("artifact_id"))
        _workflow_ref(artifact_refs, "pine_artifact_id", result.get("pine_code_artifact_id"))
        _workflow_ref(artifact_refs, "validation_artifact_id", result.get("validation_artifact_id"))
    elif tool_name in {"run_backtest_preview", "get_backtest_summary"}:
        _workflow_ref(artifact_refs, "backtest_run_id", result.get("run_id"))
    if proposal_id:
        artifact_refs["bot_proposal_id"] = proposal_id

    required_fields = list(STRATEGY_BOT_SETUP_FIELDS if proposal is not None else STRATEGY_BOT_REQUIRED_INPUT_FIELDS)
    missing_fields = setup_missing if proposal is not None else strategy_missing
    blocked_reason = None
    if strategy_missing:
        blocked_reason = "missing_strategy_inputs"
    elif proposal is not None and setup_missing:
        blocked_reason = "missing_bot_setup_fields"

    tasks: list[dict[str, Any]] = []
    task_values: dict[str, Any] = {}
    if strategy_missing:
        collect_inputs = [
            field
            for field in [*strategy_missing, "entry_exit_idea"]
            if field in {"market", "symbol", "timeframe", "style", "entry_exit_idea", "risk_preference"}
        ]
        collect_task = build_workflow_task_payload(
            STRATEGY_BOT_WORKFLOW_ID,
            "collect_strategy_inputs",
            input_request_ids=collect_inputs,
            status="pending_user",
            reason="Missing strategy design fields.",
        )
        if collect_task is not None:
            tasks.append(collect_task)
    if current_step == "generate_pine" and not has_pine:
        next_step_task = build_workflow_task_payload(
            STRATEGY_BOT_WORKFLOW_ID,
            STRATEGY_SPEC_NEXT_STEP_TASK_ID,
            status="pending_user",
            reason="Review the drafted strategy spec before generating Pine.",
        )
        if next_step_task is not None:
            tasks.append(next_step_task)
    if current_step == "backtest_preview" and not has_backtest_preview:
        draft_only = "backtest_preview" in skipped
        choice_task = build_workflow_task_payload(
            STRATEGY_BOT_WORKFLOW_ID,
            "draft_only_backtest_choice",
            status="completed" if draft_only else "pending_user",
            values={"draft_only_choice": "draft_only"} if draft_only else {},
            reason=step_reasons.get("backtest_preview") if draft_only else "Choose whether to run preview or keep draft-only review.",
        )
        if choice_task is not None:
            tasks.append(choice_task)
            if draft_only:
                task_values["draft_only_choice"] = "draft_only"
    if proposal is not None and setup_missing:
        setup_task = build_workflow_task_payload(
            STRATEGY_BOT_WORKFLOW_ID,
            "complete_paper_setup",
            input_request_ids=setup_missing,
            status="pending_user",
            reason="Paper simulation setup fields are incomplete.",
        )
        if setup_task is not None:
            tasks.append(setup_task)
    if proposal is not None:
        confirm_task = build_workflow_task_payload(
            STRATEGY_BOT_WORKFLOW_ID,
            "confirm_paper_start",
            status="pending_user" if start_allowed else "blocked",
            reason=None if start_allowed else "Paper simulation start remains locked until setup and review gates are complete.",
        )
        if confirm_task is not None:
            tasks.append(confirm_task)

    return validate_workflow_payload({
        "workflow_id": STRATEGY_BOT_WORKFLOW_ID,
        "current_step": current_step,
        "completed_steps": completed_steps,
        "skipped_steps": skipped_steps,
        "step_reasons": step_reasons,
        "blocked_reason": blocked_reason,
        "required_fields": required_fields,
        "missing_fields": missing_fields,
        "artifact_refs": artifact_refs,
        "evidence_status": evidence_status,
        "bot_proposal_id": proposal_id,
        "start_allowed": start_allowed,
        "tasks": tasks,
        "task_values": task_values,
    })


def _workflow_task_event_payload(task: Any) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "workflow_id": task.workflow_id,
        "task_template_id": task.task_template_id,
        "status": task.status,
    }


def _workflow_task_values_from_state(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for task in tasks:
        values = task.get("values")
        if isinstance(values, dict):
            merged.update(values)
        response = task.get("response")
        if isinstance(response, dict) and isinstance(response.get("values"), dict):
            merged.update(response["values"])
    return merged


def _workflow_payload_has_blocking_user_task(workflow_payload: dict[str, Any]) -> bool:
    tasks = workflow_payload.get("tasks")
    if not isinstance(tasks, list):
        return False
    return any(
        isinstance(task, dict)
        and task.get("blocking") is True
        and task.get("status") == "pending_user"
        for task in tasks
    )


def _conversation_user_artifact_kinds(
    repository: ConversationRepository,
    auth: AuthContext,
    conversation_id: str,
    *,
    current_run_id: str,
) -> set[str]:
    kinds: set[str] = set()
    runs = repository.list_runs(auth, conversation_id) or []
    for run in runs[:5]:
        if run.id == current_run_id:
            continue
        artifacts = repository.list_artifacts(auth, run.id) or []
        for artifact in artifacts:
            if _artifact_is_user_visible(artifact):
                kinds.add(str(getattr(artifact, "kind", "")).lower())
    return kinds


def _latest_strategy_artifact_context_text(
    repository: ConversationRepository,
    auth: AuthContext,
    conversation_id: str,
) -> str:
    strategy_spec_record = repository.get_latest_strategy_spec_for_conversation(auth, conversation_id)
    pine_artifact = _latest_user_visible_artifact(
        repository,
        auth,
        conversation_id,
        kind="pine_file",
    )
    if strategy_spec_record is None and pine_artifact is None:
        return ""

    lines = ["Durable current strategy artifact context:"]
    if strategy_spec_record is not None:
        strategy_spec = strategy_spec_record.payload_json
        lines.append(f"- strategy_spec_id: {strategy_spec_record.id}")
        lines.append(f"- strategy_spec_run_id: {strategy_spec_record.run_id}")
        for key in (
            "name",
            "target_platform",
            "script_type",
            "market",
            "symbol",
            "timeframe",
            "position_sizing",
            "stop_loss",
            "take_profit",
        ):
            value = _safe_string(strategy_spec.get(key))
            if value:
                lines.append(f"- {key}: {value}")
        for key in ("entry_rules", "exit_rules", "risk_rules"):
            values = _safe_string_list(strategy_spec.get(key), limit=3)
            if values:
                lines.append(f"- {key}: {'; '.join(values)}")
    if pine_artifact is not None:
        lines.append(f"- pine_artifact_id: {pine_artifact.id}")
        lines.append(f"- pine_artifact_run_id: {pine_artifact.run_id}")
        lines.append(f"- pine_artifact_kind: {pine_artifact.kind}")
        lines.append(f"- pine_artifact_display_name: {pine_artifact.display_name}")
    lines.append(
        "- instruction: Treat this as current durable strategy context for read-only scout and evidence follow-ups."
    )
    return "\n".join(lines)


def _latest_user_visible_artifact(
    repository: ConversationRepository,
    auth: AuthContext,
    conversation_id: str,
    *,
    kind: str,
):
    artifacts: list[Any] = []
    for run in repository.list_runs(auth, conversation_id) or []:
        for artifact in repository.list_artifacts(auth, run.id) or []:
            if _artifact_is_user_visible(artifact) and str(getattr(artifact, "kind", "")).lower() == kind:
                artifacts.append(artifact)
    if not artifacts:
        return None
    return sorted(
        artifacts,
        key=lambda artifact: (getattr(artifact, "created_at", datetime.min), getattr(artifact, "id", "")),
        reverse=True,
    )[0]


def _current_run_user_artifact_kinds(
    repository: ConversationRepository,
    auth: AuthContext,
    run_id: str,
) -> set[str]:
    kinds: set[str] = set()
    artifacts = repository.list_artifacts(auth, run_id) or []
    for artifact in artifacts:
        if _artifact_is_user_visible(artifact):
            kinds.add(str(getattr(artifact, "kind", "")).lower())
    return kinds


def _artifact_is_user_visible(artifact: Any) -> bool:
    visibility = getattr(artifact, "visibility", None)
    if visibility is not None:
        return visibility != "internal"
    kind = str(getattr(artifact, "kind", "")).lower()
    return not any(term in kind for term in ("trace", "observability", "internal"))


def _post_tool_suggestions_payload(
    *,
    repository: ConversationRepository,
    auth: AuthContext,
    run: AssistantRunRecord,
    tool_name: str,
    tool_result: dict[str, Any],
    response_intent: str | None,
    message_content: str,
    context_text: str,
    language: str,
    web_search: str,
    artifact_kinds: set[str] | None = None,
) -> dict[str, Any] | None:
    if tool_name == "draft_bot":
        proposal = tool_result.get("bot_proposal") if isinstance(tool_result.get("bot_proposal"), dict) else None
        if proposal is None:
            return None
        action = _suggestion_action(
            "review-bot-setup",
            _copy(language, "Review Bot setup", "Review Bot setup"),
            _copy(language, "Review the Bot setup before starting simulation. No broker execution.", "Review the Bot setup before starting simulation. No broker execution."),
            "risk",
            priority=-10,
            enabled=True,
            reason=tool_result.get("next_action") if isinstance(tool_result.get("next_action"), str) else None,
            risk_level="review_required",
            tool_id="review_bot_setup",
            next_state="bot_setup_review",
            presentation={"badge_key": "review_required", "icon_key": "bot", "visibility_key": "default"},
        )
        action["bot_proposal"] = proposal
        return {
            "actions": [action],
            "composer_blocks": [],
            "context": {
                "artifact_available": True,
                "intent": response_intent or "strategy_building",
                "readiness": tool_result.get("status") or "missing_inputs",
            },
            "safe": True,
            "version": 1,
        }
    if tool_name not in {"generate_pine"}:
        return None
    if artifact_kinds is None:
        artifact_kinds = _conversation_user_artifact_kinds(
            repository,
            auth,
            run.conversation_id,
            current_run_id=run.id,
        )
        artifact_kinds.update(_current_run_user_artifact_kinds(repository, auth, run.id))
    if not artifact_kinds:
        return None
    post_tool_context_text = f"{context_text}\n{_tool_result_context_text(tool_result)}"
    action_plan = ActionPlanDecision(
        "suggest_actions",
        "local_preview_evidence",
        1.0,
        "post_tool_registry",
        suggested_actions=("run_backtest_preview",),
        reason="A Pine artifact was created and can be reviewed with local preview evidence.",
    )
    return _suggestions_payload(
        response_intent=response_intent or "artifact_generation",
        message_content=message_content,
        context_text=post_tool_context_text,
        language=language,
        artifact_available=True,
        artifact_kinds=artifact_kinds,
        web_search=web_search,
        action_plan=action_plan,
    )


def _tool_result_context_text(tool_result: dict[str, Any]) -> str:
    spec = tool_result.get("strategy_spec") if isinstance(tool_result.get("strategy_spec"), dict) else None
    if not spec:
        return ""
    parts: list[str] = []
    for key in ("market", "symbol", "timeframe", "position_sizing", "risk"):
        value = spec.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(f"{key}: {value}")
    for key in ("entry_rules", "exit_rules", "risk_rules"):
        value = spec.get(key)
        if isinstance(value, list):
            joined = "; ".join(str(item) for item in value[:3] if str(item).strip())
            if joined:
                parts.append(f"{key}: {joined}")
    return "\n".join(parts)


def _latest_artifact_evidence_followup(
    repository: ConversationRepository,
    auth: AuthContext,
    conversation_id: str,
    message_content: str,
    *,
    language: str,
) -> dict[str, Any] | None:
    if not _is_artifact_evidence_followup_request(message_content):
        return None
    snapshot = repository.get_conversation_state_snapshot(auth, conversation_id, event_limit=100)
    if snapshot is None:
        return None
    relevant_types = ("validation.completed", "review.completed", "evaluator_optimizer.summary")
    latest: dict[str, Any] = {}
    for event in reversed(snapshot.conversation_run_events):
        if event.type not in relevant_types or event.type in latest:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        latest[event.type] = {
            "run_id": event.run_id,
            "payload": dict(payload),
        }
        if len(latest) == len(relevant_types):
            break
    if not latest:
        return None
    return {
        "events": [
            (
                event_type,
                {
                    **record["payload"],
                    "evidence_source": "persisted_conversation_evidence",
                    "reused_from_run_id": record["run_id"],
                },
            )
            for event_type, record in latest.items()
        ],
        "text": _artifact_evidence_followup_text(latest, language=language),
    }


def _artifact_evidence_followup_text(evidence: dict[str, Any], *, language: str) -> str:
    validation = (evidence.get("validation.completed") or {}).get("payload") or {}
    review = (evidence.get("review.completed") or {}).get("payload") or {}
    evaluator = (evidence.get("evaluator_optimizer.summary") or {}).get("payload") or {}
    validation_status = _safe_string(validation.get("status")) or _safe_string(evaluator.get("final_validation_status")) or "unknown"
    review_status = (
        _safe_string(review.get("decision"))
        or _safe_string(review.get("status"))
        or _safe_string(evaluator.get("final_review_status"))
        or "unknown"
    )
    stop_reason = _safe_string(evaluator.get("stop_reason")) or "unknown"
    if _normalize_language(language) == "vi":
        return (
            "Theo evidence đã persist cho artifact hiện tại: "
            f"static validation = {validation_status}, review = {review_status}, "
            f"evaluator stop reason = {stop_reason}. "
            "Mình không start paper bot hay live trading."
        )
    return (
        "Persisted evidence for the current artifact: "
        f"static validation = {validation_status}, review = {review_status}, "
        f"evaluator stop reason = {stop_reason}. "
        "I did not start paper or live trading."
    )


def _latest_backtest_live_context(
    repository: ConversationRepository,
    auth: AuthContext,
    conversation_id: str,
) -> str | None:
    snapshot = repository.get_conversation_state_snapshot(auth, conversation_id, event_limit=100)
    if snapshot is None:
        return None
    relevant_types = {
        "backtest.preview.heartbeat",
        "backtest.preview.approval_required",
        "backtest.preview.queued",
        "backtest.preview.failed",
        "backtest.preview.rejected",
        "chat.auto_chain.waiting_for_backtest",
    }
    latest = next(
        (event for event in reversed(snapshot.conversation_run_events) if event.type in relevant_types),
        None,
    )
    if latest is None:
        return None
    payload = latest.payload if isinstance(latest.payload, dict) else {}
    if latest.type == "backtest.preview.heartbeat":
        stage = _safe_string(payload.get("stage")) or "unknown"
        status = _safe_string(payload.get("status")) or "running"
        progress = payload.get("progress_pct")
        eta = payload.get("eta_ms")
        message = _safe_string(payload.get("message")) or "Backtest preview status updated."
        details = [
            "Latest local backtest preview status for this conversation.",
            f"run_id: {latest.run_id}",
            f"status: {status}",
            f"stage: {stage}",
            f"message: {message}",
        ]
        if isinstance(progress, int | float):
            details.append(f"progress_pct: {round(float(progress), 1)}")
        if isinstance(eta, int | float) and eta > 0:
            details.append(f"eta_ms: {round(float(eta))}")
        details.append(
            "Boundary: local sandbox preview only, not TradingView proof, broker proof, live trading evidence, or a profitability claim."
        )
        return "\n".join(details)
    if latest.type == "backtest.preview.rejected":
        return (
            "Latest local backtest preview status for this conversation.\n"
            f"run_id: {latest.run_id}\n"
            "status: rejected\n"
            "message: The user skipped the backtest preview. Do not imply a preview is running."
        )
    if latest.type == "backtest.preview.failed":
        message = _safe_string(payload.get("message")) or "Backtest preview failed."
        return (
            "Latest local backtest preview status for this conversation.\n"
            f"run_id: {latest.run_id}\n"
            "status: failed\n"
            f"message: {message}\n"
            "Boundary: local sandbox preview only, not TradingView proof, broker proof, live trading evidence, or a profitability claim."
        )
    if latest.type == "backtest.preview.approval_required":
        return (
            "Latest local backtest preview status for this conversation.\n"
            f"run_id: {latest.run_id}\n"
            "status: approval_required\n"
            "message: A backtest plan is waiting for explicit user approval before queueing."
        )
    child_run_id = _safe_string(payload.get("child_run_id")) or latest.run_id
    return (
        "Latest local backtest preview status for this conversation.\n"
        f"run_id: {child_run_id}\n"
        "status: queued\n"
        "message: The approved local preview job is queued or starting."
    )


def _insert_system_context(messages: list[dict[str, str]], content: str) -> list[dict[str, str]]:
    if not messages:
        return [{"role": "system", "content": content}]
    first = messages[0]
    if first.get("role") == "system":
        return [{**first, "content": f"{first.get('content', '')}\n\n{content}"}, *messages[1:]]
    return [messages[0], {"role": "system", "content": content}, *messages[1:]]


def _copy(language: str, vi: str, en: str) -> str:
    return vi if _normalize_language(language) == "vi" else en


def _classify_response_intent(message_content: str, *, web_search: str = "auto") -> str:
    return ResponseIntentClassifier(_NoopIntentClient()).classify(
        message_content,
        web_search=web_search,
    ).intent


def _deterministic_response_intent(message_content: str, *, web_search: str = "auto") -> IntentClassification | None:
    _ = web_search
    normalized = _normalize_chat_request_text(message_content)
    if not normalized:
        return None
    if _is_artifact_evidence_followup_request(normalized):
        return IntentClassification("general_chat", 0.91, "deterministic_artifact_evidence_followup")
    if _is_pine_generation_or_revision_request(normalized):
        return IntentClassification("pine_generation", 0.93, "deterministic_pine_generation")
    return None


def _normalize_chat_request_text(message_content: str | None) -> str:
    return " ".join((message_content or "").lower().split())


def _is_pine_generation_or_revision_request(normalized: str) -> bool:
    if _is_artifact_evidence_followup_request(normalized):
        return False
    if re.search(r"\b(?:backtest|preview|simulate|simulation|paper\s+bot|paper\s+trade)\b", normalized):
        return False
    has_pine = bool(re.search(r"\b(?:pine|pinescript|pine\s+script|pine\s+v6)\b", normalized))
    has_generation_action = bool(
        re.search(
            r"\b(?:build|create|generate|regenerate|revise|rewrite|update|fix|repair|implement|write|convert)\b",
            normalized,
        )
    )
    has_strategy_or_code_context = bool(
        re.search(
            r"\b(?:strategy|script|code|artifact|ema|rsi|entry|exit|stop\s*loss|take\s*profit|validation|review)\b",
            normalized,
        )
    )
    return has_pine and has_generation_action and has_strategy_or_code_context


def _is_artifact_evidence_followup_request(message_content: str | None) -> bool:
    normalized = _normalize_chat_request_text(message_content)
    if not normalized:
        return False
    if re.search(r"\b(?:generate|regenerate|build|create|write|implement|rewrite)\b", normalized):
        return False
    has_artifact_context = bool(re.search(r"\b(?:pine|strategy|artifact|script|code|generated)\b", normalized))
    has_evidence_request = bool(
        re.search(
            r"\b(?:validation|validate|validated|static|review|reviewed|passed|pass|fail|failed|"
            r"blocker|warning|warnings|evaluator|production\s+gate|kiểm\s*tra|danh\s*gia|đánh\s*giá)\b",
            normalized,
        )
    )
    return has_artifact_context and has_evidence_request


class _NoopIntentClient:
    model = "local/noop-intent-classifier"

    def ensure_configured(self) -> None:
        return None

    def stream(self, *, messages: list[dict[str, str]], tools: list[dict[str, Any]]) -> Iterator[LLMClientEvent]:
        return iter(())


def _intent_classifier_system_prompt() -> str:
    intents = ", ".join(sorted(RESPONSE_INTENTS))
    return (
        "Classify the user's latest message for Strategy Codebot UI routing. "
        f"Return JSON only with keys intent and confidence. intent must be one of: {intents}. "
        "Use market_snapshot for current/approximate price, quote, or 'how much is asset now' questions. "
        "Use market_research for market news/source research. "
        "Use docs_research for current docs, provider, model, API, release, or pricing questions. "
        "Use strategy_building for designing trading rules/specs. "
        "Use artifact_generation for code/artifact generation or validation/review artifact requests. "
        "Use capability_help for questions about what the assistant can do. "
        "Use general_chat when none apply. "
        "Do not include explanations, markdown, or extra keys."
    )


def _chat_intent_decision_system_prompt() -> str:
    intents = ", ".join(sorted(RESPONSE_INTENTS))
    actions = ", ".join(sorted(CHAT_INTENT_ACTIONS))
    stages = ", ".join(sorted(CHAT_RESPONSE_MODEL_STAGES))
    scopes = ", ".join(sorted(DOMAIN_SCOPES))
    workflows = ", ".join(sorted(WORKFLOW_INTENTS))
    return (
        "You are Strategy Codebot's semantic chat intent gate. Return JSON only with keys: "
        "response_intent, action, model_stage, confidence, tool_id, auto_chain, "
        "current_context_required, domain_scope, workflow_intent, missing_inputs, reasons, used_signals. "
        f"response_intent must be one of: {intents}. "
        f"action must be one of: {actions}. "
        f"model_stage must be one of: {stages}. "
        f"domain_scope must be one of: {scopes}. "
        f"workflow_intent may be one of: {workflows}, or null. "
        "Regex evidence is only a hint; decide from semantic intent, recent context, artifacts, and available actions. "
        "Use start_auto_chain or auto_chain=true when the user wants local preview/backtest evidence, including paraphrases such as simulate, paper test, preview performance, chạy thử, thử hiệu quả, or chay thu. "
        "Use ambiguous instead of off_topic when context is unclear. "
        "Use pine_generation or artifact_generation with the Pine code generation stage for Pine/code/script requests. "
        "Use current_context_required only for current external facts such as market data, docs, providers, models, pricing, releases, or versions; current preview evidence is internal context. "
        "Never select broker, live trading, paper trading execution, profitability proof, or approval bypass behavior. "
        "Do not include markdown or extra keys."
    )


def _action_planner_system_prompt() -> str:
    return (
        "You are Strategy Codebot's bounded action planner. Return JSON only with keys: "
        "decision, intent_id, confidence, tool_id, arguments, suggested_actions, reason. "
        "decision must be one of: answer, call_tool, suggest_actions, ask_clarification. "
        "Choose call_tool only when the user's request clearly maps to one available action. "
        "For explicit trade row requests such as show/list/fetch/give first N trades, choose call_tool "
        "with tool_id query_backtest_trades and include limit when the user gives a count. "
        "Omit bucket for first/latest/all trade requests; use bucket only when the user explicitly asks for sample, top winners, or top losers. "
        "Trade-list tool results are rendered by the UI as a structured table, so do not enumerate trade rows in prose. "
        "Do not answer that you will fetch data when an available read-only tool can fetch it now. "
        "Use suggested_actions for helpful next steps when execution is not explicitly requested. "
        "Do not use keyword matching blindly: interpret terms like current in context, e.g. current preview evidence "
        "means the active backtest preview, not market news. "
        "Never plan paper/live trading, broker execution, profitability claims, or bypass approval gates."
    )


def _parse_intent_classifier_json(text: str) -> dict[str, Any] | None:
    payload = _extract_json_object(text)
    if payload is None:
        return None
    intent = payload.get("intent")
    confidence = payload.get("confidence")
    if intent not in RESPONSE_INTENTS:
        return None
    if not isinstance(confidence, int | float):
        return None
    bounded_confidence = float(max(0.0, min(1.0, confidence)))
    return {"confidence": bounded_confidence, "intent": intent}


def _parse_action_plan_json(text: str, *, available_tools: set[str]) -> ActionPlanDecision | None:
    payload = _extract_json_object(text)
    if payload is None:
        return None
    decision = _safe_string(payload.get("decision")) or "answer"
    if decision not in {"answer", "call_tool", "suggest_actions", "ask_clarification"}:
        return None
    confidence = payload.get("confidence")
    if not isinstance(confidence, int | float):
        return None
    tool_id = _safe_string(payload.get("tool_id"))
    if decision == "call_tool":
        if tool_id not in available_tools:
            intent_id = (_safe_string(payload.get("intent_id")) or _safe_string(payload.get("intent")) or "unavailable_action")[:80]
            reason = _safe_string(payload.get("reason"))
            return ActionPlanDecision(
                "suggest_actions",
                intent_id or "unavailable_action",
                float(max(0.0, min(1.0, confidence))),
                "llm",
                tool_id=tool_id,
                reason=(reason or f"Planner selected unavailable action: {tool_id}")[:240],
            )
        arguments = payload.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
    else:
        arguments = None
        if tool_id not in available_tools:
            tool_id = None
    raw_suggestions = payload.get("suggested_actions")
    suggested_actions: list[str] = []
    if isinstance(raw_suggestions, list):
        for item in raw_suggestions:
            normalized = _normalize_semantic_action(item)
            if normalized and normalized in SEMANTIC_ACTIONS and normalized not in suggested_actions:
                suggested_actions.append(normalized)
    if tool_id and not suggested_actions:
        normalized_tool_action = _normalize_semantic_action(tool_id)
        if normalized_tool_action and normalized_tool_action in SEMANTIC_ACTIONS:
            suggested_actions.append(normalized_tool_action)
    intent_id = (_safe_string(payload.get("intent_id")) or _safe_string(payload.get("intent")) or decision)[:80]
    reason = _safe_string(payload.get("reason"))
    return ActionPlanDecision(
        decision,
        intent_id,
        float(max(0.0, min(1.0, confidence))),
        "llm",
        tool_id=tool_id,
        arguments=dict(arguments) if isinstance(arguments, dict) else None,
        suggested_actions=tuple(suggested_actions[:3]),
        reason=reason[:240] if reason else None,
    )


def _parse_chat_intent_decision_json(
    text: str,
    *,
    available_tools: set[str],
    regex_evidence: dict[str, bool] | None = None,
) -> ChatIntentDecision | None:
    payload = _extract_json_object(text)
    if payload is None:
        return None
    response_intent = _safe_string(payload.get("response_intent") or payload.get("intent")) or "general_chat"
    if response_intent not in RESPONSE_INTENTS:
        return None
    action = _safe_string(payload.get("action")) or "answer"
    if action not in CHAT_INTENT_ACTIONS:
        return None
    model_stage = _safe_string(payload.get("model_stage")) or _model_stage_for_intent(response_intent)
    if model_stage not in CHAT_RESPONSE_MODEL_STAGES:
        model_stage = _model_stage_for_intent(response_intent)
    confidence = payload.get("confidence")
    if not isinstance(confidence, int | float):
        return None
    bounded_confidence = float(max(0.0, min(1.0, confidence)))
    tool_id = _safe_string(payload.get("tool_id"))
    if action == "call_tool" and tool_id not in available_tools:
        action = "suggest_actions"
    if tool_id not in available_tools:
        tool_id = None
    if bounded_confidence < CHAT_INTENT_DECISION_MIN_CONFIDENCE:
        return None
    domain_scope = normalize_domain_scope(
        payload.get("domain_scope"),
        fallback=domain_scope_for_response_intent(response_intent),
    )
    workflow_intent = normalize_workflow_intent(payload.get("workflow_intent"))
    used_signals = normalize_evidence_signals(payload.get("used_signals"))
    if not used_signals and regex_evidence:
        used_signals = evidence_signals_from_regex(regex_evidence)
    return ChatIntentDecision(
        response_intent=response_intent,
        action=action,
        model_stage=model_stage,
        confidence=bounded_confidence,
        source="llm",
        tool_id=tool_id,
        auto_chain=bool(payload.get("auto_chain")),
        current_context_required=bool(payload.get("current_context_required")),
        domain_scope=domain_scope,
        workflow_intent=workflow_intent,
        missing_inputs=_safe_string_tuple(payload.get("missing_inputs")),
        reasons=_safe_string_tuple(payload.get("reasons") or payload.get("reason")),
        used_signals=used_signals,
    )


def _fallback_chat_intent_decision(
    message_content: str,
    *,
    web_search: str,
    regex_evidence: dict[str, bool],
    domain_scope_hint: str | None = None,
) -> ChatIntentDecision:
    deterministic = _deterministic_response_intent(message_content, web_search=web_search)
    if deterministic is None and str(web_search or "").strip().lower() == "on":
        if regex_evidence.get("market_research") or regex_evidence.get("market_snapshot"):
            deterministic = IntentClassification("market_snapshot", 0.9, "deterministic_market_snapshot")
    response_intent = deterministic.intent if deterministic is not None else "general_chat"
    return ChatIntentDecision(
        response_intent=response_intent,
        action="answer",
        model_stage=_model_stage_for_intent(response_intent),
        confidence=deterministic.confidence if deterministic is not None else RESPONSE_INTENT_FALLBACK_CONFIDENCE,
        source=deterministic.source if deterministic is not None else "fallback",
        auto_chain=False,
        current_context_required=False,
        domain_scope=domain_scope_for_response_intent(response_intent) if deterministic is not None else "ambiguous",
        used_signals=evidence_signals_from_regex(regex_evidence),
    )


def _safe_string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        stripped = value.strip()
        return (stripped[:160],) if stripped else ()
    if not isinstance(value, list):
        return ()
    items: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            items.append(item.strip()[:160])
    return tuple(items[:6])


def _chat_regex_evidence(message_content: str) -> dict[str, bool]:
    return collect_chat_regex_evidence(message_content)


def _model_stage_for_intent(response_intent: str | None) -> str:
    return model_stage_for_response_intent(response_intent, fallback=DEFAULT_MODEL_STAGE)


def _normalize_semantic_action(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("-", "_")
    aliases = {
        "build_robustness": "build_robustness_report",
        "bot_status": "get_bot_status",
        "prepare_bot": "draft_bot",
        "proposed_intent": "create_proposed_intent",
        "repair_validation": "repair",
        "risk_gate": "run_risk_gate",
        "variant_lab": "run_backtest_variant_lab",
    }
    return aliases.get(normalized, normalized)


def _has_blocked_semantic_action_claim(text: str) -> bool:
    normalized = text.lower()
    blocked_terms = (
        "guaranteed profit",
        "guaranteed returns",
        "live ready",
        "live-ready",
        "no loss",
        "profit guaranteed",
        "risk free",
        "risk-free",
        "safe to trade live",
        "broker execution",
        "paper trade now",
        "trade now",
        "chắc chắn lời",
        "không lỗ",
        "sẵn sàng live",
    )
    return any(term in normalized for term in blocked_terms)


def _direct_action_plan_tool_args(
    action_plan: ActionPlanDecision,
    *,
    artifact_kinds: set[str],
    context_text: str,
    web_search: str,
) -> tuple[str, dict[str, Any]] | None:
    if not action_plan.is_active() or action_plan.decision != "call_tool" or not action_plan.tool_id:
        return None
    arguments = dict(action_plan.arguments or {})
    if action_plan.tool_id == "generate_pine":
        if isinstance(arguments.get("strategy_spec"), dict):
            return action_plan.tool_id, arguments
        return None
    if action_plan.tool_id == "create_backtest_plan":
        if (
            isinstance(arguments.get("strategy_spec"), dict)
            and isinstance(arguments.get("pine_code"), str)
            and arguments.get("pine_code").strip()
            and isinstance(arguments.get("prompt"), str)
            and arguments.get("prompt").strip()
        ):
            return action_plan.tool_id, arguments
        return None
    if action_plan.tool_id not in {"query_backtest_trades", "get_backtest_summary", "build_robustness_report", "get_bot_status", "list_bots", "list_bot_events"}:
        return None
    available_tools = available_registry_tool_ids(
        artifact_kinds=artifact_kinds,
        context_text=context_text,
        web_search=web_search,
        context_signals=_action_plan_context_signals(action_plan.tool_id, arguments),
    )
    if action_plan.tool_id not in available_tools:
        return None
    if action_plan.tool_id in {"query_backtest_trades", "get_backtest_summary", "build_robustness_report"}:
        arguments.setdefault("run_id", "latest_completed_backtest")
    if action_plan.tool_id == "query_backtest_trades":
        bucket = arguments.get("bucket")
        if bucket is not None and bucket not in {"sample", "top_loser", "top_winner"}:
            arguments.pop("bucket", None)
        arguments["limit"] = _requested_tool_output_limit(arguments.get("limit"), default=20, maximum=50)
    return action_plan.tool_id, arguments


def _action_plan_context_signals(tool_id: str, arguments: dict[str, Any]) -> set[str]:
    signals: set[str] = set()
    if tool_id in {"get_bot_status", "list_bot_events"} and (
        _safe_string(arguments.get("runtime_id")) or _safe_string(arguments.get("bot_id"))
    ):
        signals.add("bot_context")
    return signals


def _extract_json_object(text: str) -> dict[str, Any] | None:
    return extract_json_object(text)


def _normalize_web_sources(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        title = _safe_string(item.get("title"))
        source_id = _safe_string(item.get("id"))
        source_type = item.get("type")
        url = _safe_string(item.get("url"))
        if source_type not in {"external", "internal"} or not title:
            continue
        if source_type == "external" and not url:
            continue
        key = url or source_id
        if not key or key in seen:
            continue
        seen.add(key)
        source = {
            "id": source_id or key[:96],
            "title": title[:160],
            "type": str(source_type),
        }
        if url:
            source["url"] = url
        normalized.append(source)
    return normalized[:5]


def _market_snapshot_payload(
    message_content: str,
    sources: list[dict[str, str]],
    *,
    language: str = "en",
    price: str | None = None,
) -> dict[str, Any]:
    symbol = _market_symbol_from_text(message_content)
    return {
        "approximate": True,
        "freshness": "source_backed",
        "generated_at": None,
        "label": "Market snapshot" if _normalize_language(language) == "en" else "Market snapshot",
        "price": price,
        "price_points": [],
        "source_count": len(sources),
        "sources": sources[:5],
        "symbol": symbol,
    }


def _market_symbol_from_text(message_content: str) -> str:
    normalized = message_content.upper()
    for token in ("BTC", "ETH", "SOL", "BNB", "XAU", "EURUSD", "GBPUSD", "USDJPY"):
        if token in normalized:
            return token
    return "Market"


def _market_snapshot_needs_series(message_content: str) -> bool:
    normalized = message_content.lower()
    return any(
        keyword in normalized
        for keyword in (
            "1h",
            "4h",
            "analyze",
            "chart",
            "context",
            "market",
            "range",
            "trend",
            "phân tích",
            "thị trường",
            "xu hướng",
        )
    )


def _market_price_from_text(text: str) -> str | None:
    match = re.search(r"\$[0-9][0-9,]*(?:\.[0-9]+)?", text)
    return match.group(0) if match else None


def _safe_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _safe_blocked_message(language: str = "en") -> str:
    if _normalize_language(language) == "vi":
        return (
            "Yêu cầu này chạm boundary review-only nên mình không thể thực hiện như đã viết. "
            "Mình có thể giúp chuyển nó thành strategy spec, Pine/MQL5 artifact để review, hoặc hướng dẫn validation thủ công."
        )
    return SAFE_BLOCKED_MESSAGE


def should_use_bounded_scout(message_content: str, *, response_intent: str) -> bool:
    text = message_content.lower()
    if response_intent not in {"general_chat", "capability_help", "strategy_building", "artifact_generation"}:
        return False
    if chat_safety_preflight(message_content) is not None:
        return False
    has_tool_context = _contains_any(text, "tool", "tools", "registry", "tooling", "công cụ", "cong cu")
    asks_for_read_only_scout = _contains_any(
        text,
        "read-only",
        "read safe",
        "read-safe",
        "risk_tier=read",
        "risk tier read",
        "scout",
        "inspect",
        "investigate",
        "kiểm tra",
        "kiem tra",
        "đọc",
        "doc",
    )
    generation_request = _contains_any(
        text,
        "generate pine",
        "sinh pine",
        "tạo pine",
        "tao pine",
        "viết pine",
        "viet pine",
        "backtest",
    )
    return has_tool_context and asks_for_read_only_scout and not generation_request


def chat_safety_preflight(message_content: str) -> PolicyFinding | None:
    text = message_content.lower()
    if _requests_shell_or_repo_write(text):
        return PolicyFinding(
            severity="blocker",
            code="unsafe_chat_tool_request",
            message="Chat requested shell, filesystem, edit, or repository-write execution outside approved tool boundaries.",
            surface="agent.chat.input",
            evidence_level=EVIDENCE_STRATEGY_IDEA,
            rule_id="chat_safety_preflight.unsafe_tool_request",
            category="tool_safety",
        )
    if _requests_live_trading_execution(text):
        return PolicyFinding(
            severity="blocker",
            code="trading_execution_boundary",
            message="Chat requested live or broker trading execution, which remains outside review-only boundaries.",
            surface="agent.chat.input",
            evidence_level=EVIDENCE_STRATEGY_IDEA,
            rule_id="chat_safety_preflight.trading_execution_boundary",
            category="trading_boundary",
        )
    if _requests_paper_bot_bypass(text):
        return PolicyFinding(
            severity="blocker",
            code="paper_bot_confirmation_required",
            message="Chat requested paper bot startup while bypassing backend eligibility or explicit user confirmation.",
            surface="agent.chat.input",
            evidence_level=EVIDENCE_STRATEGY_IDEA,
            rule_id="chat_safety_preflight.paper_bot_confirmation_required",
            category="trading_boundary",
        )
    return None


def _requests_shell_or_repo_write(text: str) -> bool:
    shell_terms = (
        "run shell",
        "execute shell",
        "chạy shell",
        "chay shell",
        "terminal command",
        "chạy lệnh",
        "chay lenh",
        "bash ",
        "sh ",
        "rm -rf",
        "python -c",
        "node -e",
    )
    repo_write_terms = (
        "repo-write",
        "write file",
        "edit file",
        "modify file",
        "filesystem write",
        "sửa file",
        "sua file",
        "ghi file",
        "git commit",
        "commit changes",
        "git push",
        "push branch",
    )
    action_terms = ("run", "execute", "chạy", "chay", "use", "dùng", "dung", "edit", "write", "sửa", "sua", "ghi")
    return _contains_any(text, *shell_terms) or (
        _contains_any(text, *repo_write_terms) and _contains_any(text, *action_terms)
    )


def _requests_live_trading_execution(text: str) -> bool:
    explicit_patterns = (
        r"\b(connect|integrate|deploy)\b.{0,80}\b(broker|exchange)\b",
        r"\bexecute\s+(?:broker|exchange)\b",
        r"\b(place|send|submit|execute)\b.{0,80}\blive\s+(?:orders?|trades?)\b",
        r"\bplace\s+orders?\b.{0,80}\b(?:broker|exchange|live)\b",
        r"\bđặt\s+lệnh\s+thật\b",
        r"\bdat\s+lenh\s+that\b",
        r"\bgiao\s+dịch\s+thật\b",
        r"\bgiao\s+dich\s+that\b",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in explicit_patterns)


def _requests_paper_bot_bypass(text: str) -> bool:
    bot_terms = ("paper bot", "paper-bot", "bot giấy", "bot giay", "simulation bot")
    start_terms = ("start", "run", "launch", "chạy", "chay", "bật", "bat", "kích hoạt", "kich hoat")
    bypass_terms = (
        "bypass",
        "skip confirmation",
        "without confirmation",
        "no confirmation",
        "skip eligibility",
        "không cần hỏi",
        "khong can hoi",
        "không cần xác nhận",
        "khong can xac nhan",
        "không cần kiểm tra",
        "khong can kiem tra",
    )
    return _contains_any(text, *bot_terms) and _contains_any(text, *start_terms) and _contains_any(text, *bypass_terms)


def _contains_any(text: str, *needles: str) -> bool:
    return any(needle in text for needle in needles)


def _bounded_scout_fallback_message(status: str, language: str) -> str:
    if status == "blocked":
        return _safe_blocked_message(language)
    if _normalize_language(language) == "vi":
        return "Mình đã hoàn tất bước scout read-only và ghi lại telemetry an toàn cho workflow."
    return "Read-only scout completed with safe workflow telemetry recorded."


def _failure_assistant_message(payload: dict[str, Any], language: str = "en") -> str:
    is_vi = _normalize_language(language) == "vi"
    if payload.get("dimension") == "workflow" or payload.get("code") == "pine_validation_failed":
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    if payload.get("code") == "provider_timeout" or payload.get("error") == "ProviderTimeoutError":
        return (
            "AI provider phản hồi quá lâu. Bạn có thể thử lại hoặc chuyển sang deterministic mode trong lúc provider ổn định lại."
            if is_vi
            else "The AI provider took too long to respond. You can try again or switch to deterministic mode while the provider catches up."
        )
    if payload.get("error") == "AuthenticationError":
        return (
            "AI provider từ chối API key hiện tại. Mình đã chuyển chat sang deterministic mode để bạn vẫn dùng workspace trong lúc sửa provider key."
            if is_vi
            else "The AI provider rejected the configured API key. I switched the chat to deterministic mode so you can keep using the workspace while the provider key is fixed."
        )
    if payload.get("error") == "RateLimitError":
        return (
            "AI provider đang bị rate limit. Hãy dùng deterministic mode hoặc thử lại sau khi limit reset."
            if is_vi
            else "The AI provider is rate-limited right now. Try deterministic mode or retry after the provider limit resets."
        )
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return f"AI run thất bại: {message}" if is_vi else f"The AI run failed: {message}"
    return (
        "AI run thất bại trước khi tạo response. Hãy thử deterministic mode hoặc kiểm tra provider configuration rồi retry."
        if is_vi
        else "The AI run failed before it could produce a response. Try deterministic mode or retry after checking provider configuration."
    )


def _workflow_tool_result_payload(tool_name: str, arguments: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    payload = dict(output)
    if tool_name == "generate_pine" and isinstance(arguments.get("strategy_spec"), dict):
        payload["strategy_spec"] = arguments["strategy_spec"]
    return payload


def _tool_success_result(tool_name: str, arguments: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"tool_id": tool_name}
    if tool_name == "get_backtest_summary":
        result["output"] = compact_tool_output(output)
    if tool_name == "query_backtest_trades":
        trades = output.get("trades")
        limit = _requested_tool_output_limit(arguments.get("limit"), default=20, maximum=50)
        result["output"] = {
            "status": output.get("status"),
            "run_id": output.get("run_id"),
            "requested_run_id": output.get("requested_run_id"),
            "fallback_used": output.get("fallback_used"),
            "trades": trades[:limit] if isinstance(trades, list) else [],
        }
    if tool_name == "build_robustness_report":
        result["output"] = compact_tool_output(output)
    if tool_name == "generate_pine":
        strategy_spec = arguments.get("strategy_spec")
        if isinstance(strategy_spec, dict):
            result["strategy_spec"] = strategy_spec
        pine_code = output.get("pine_code")
        if isinstance(pine_code, str) and pine_code.strip():
            result["pine_code"] = pine_code
        artifact_id = output.get("artifact_id")
        if isinstance(artifact_id, str) and artifact_id:
            result["artifact_id"] = artifact_id
        result["artifact_name"] = "strategy.pine"
    if tool_name == "create_backtest_plan":
        strategy_spec = arguments.get("strategy_spec")
        if isinstance(strategy_spec, dict):
            result["strategy_spec"] = strategy_spec
        for key in ("pine_code",):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                result[key] = value
        backtest_config = output.get("backtest_config")
        if isinstance(backtest_config, dict):
            full_backtest_config = arguments.get("backtest_config")
            internal_backtest_config = dict(full_backtest_config) if isinstance(full_backtest_config, dict) else dict(backtest_config)
            internal_backtest_config.setdefault("engine", "pineforge")
            internal_backtest_config.setdefault("data_source", "public-readonly-cache")
            result["backtest_config"] = internal_backtest_config
        validation = output.get("validation") or output.get("pineforge_validation")
        if isinstance(validation, dict):
            result["validation"] = validation
    if tool_name == "run_backtest_preview":
        for key in ("run_id", "job_id", "status", "mode", "evidence_label"):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                result[key] = value
        backtest_config = output.get("backtest_config")
        if isinstance(backtest_config, dict):
            result["backtest_config"] = backtest_config
    return result


def _tool_log_summary(output: dict[str, Any]) -> tuple[str | None, int | None, str | None]:
    output_status = output.get("status") if isinstance(output.get("status"), str) else None
    artifact_kind = output.get("artifact_kind") if isinstance(output.get("artifact_kind"), str) else None
    rows = output.get("rows") if isinstance(output.get("rows"), list) else None
    trades = output.get("trades") if isinstance(output.get("trades"), list) else None
    artifacts = output.get("artifacts") if isinstance(output.get("artifacts"), list) else None
    row_count = None
    if rows is not None:
        row_count = len(rows)
    elif trades is not None:
        row_count = len(trades)
    elif artifacts is not None:
        row_count = len(artifacts)
    return output_status, row_count, artifact_kind


def _tool_only_success_message(
    tool_ids: list[str],
    language: str = "en",
    *,
    tool_results: list[dict[str, Any]] | None = None,
) -> str:
    is_vi = _normalize_language(language) == "vi"
    if tool_ids and all(tool_id == "knowledge_check" for tool_id in tool_ids):
        if is_vi:
            return (
                "Mình đã kiểm tra knowledge context liên quan, nhưng hiện chưa có strategy spec hoặc Pine artifact để generate tiếp. "
                "Hãy chia sẻ strategy rules hoặc dùng Chuyển thành strategy spec trước, rồi mình có thể tạo artifact Pine v6 review-only."
            )
        return (
            "I checked the relevant knowledge context, but I do not have a strategy spec or Pine artifact to generate from yet. "
            "Share the strategy rules or turn the idea into a strategy spec first, then I can generate a review-only Pine v6 artifact."
        )
    if "run_backtest_preview" in tool_ids:
        if is_vi:
            return "Đã queue local sandbox preview run. Report sẽ là evidence artifact review-only khi worker hoàn tất."
        return "Queued a local sandbox preview run. The worker will persist review-only evidence artifacts when it completes."
    if "generate_pine" in tool_ids:
        pine_result = next(
            (result for result in reversed(tool_results or []) if result.get("tool_id") == "generate_pine"),
            None,
        )
        if pine_result is not None:
            return _generate_pine_fallback_summary(pine_result, language)
        if is_vi:
            return (
                "Đã tạo code Pine v6 review-only từ strategy spec đã cung cấp. "
                "Hãy chạy static validation hoặc tạo review artifact trước khi dùng bên ngoài workspace này."
            )
        return (
            "Generated review-only Pine v6 code from the provided strategy spec. "
            "Run static validation or create a review artifact before using it outside this workspace."
        )
    if "parallel_review" in tool_ids:
        if is_vi:
            return "Đã chuẩn bị review notes cho artifact đã tạo. Hãy review report trước khi dùng bên ngoài workspace này."
        return "Prepared review notes for the generated artifact. Review the report before using it outside this workspace."
    if "static_validate" in tool_ids:
        if is_vi:
            return "Đã hoàn tất static validation cho Pine artifact. Hãy review validation result trước khi tiếp tục."
        return "Completed static validation for the provided Pine artifact. Review the validation result before continuing."
    if "run_backtest_variant_lab" in tool_ids:
        if is_vi:
            return "Đã queue variant lab cho local preview. Hãy theo dõi từng child run và so sánh report khi tất cả hoàn tất."
        return "Queued a local preview variant lab. Track each child run and compare reports after all complete."
    if "get_backtest_summary" in tool_ids:
        summary_result = next(
            (result for result in reversed(tool_results or []) if result.get("tool_id") == "get_backtest_summary"),
            None,
        )
        if summary_result is not None:
            if not _has_available_backtest_summary(summary_result):
                return _backtest_summary_unavailable_message(language)
            return _backtest_summary_fallback(summary_result, language)
        if is_vi:
            return "Đã tải summary backtest từ DB index. Hãy review metrics và caveat local preview trước khi tiếp tục."
        return "Loaded the DB-indexed backtest summary. Review the metrics and local preview caveat before continuing."
    if "query_backtest_trades" in tool_ids:
        trades_result = next(
            (result for result in reversed(tool_results or []) if result.get("tool_id") == "query_backtest_trades"),
            None,
        )
        if trades_result is not None:
            return _backtest_trades_fallback(trades_result, language)
        if is_vi:
            return "Đã tải indexed trades từ backtest report. Hãy review trade rows và local preview caveat trước khi tiếp tục."
        return "Loaded indexed trades from the backtest report. Review the trade rows and local preview caveat before continuing."
    if "build_robustness_report" in tool_ids:
        robustness_result = next(
            (result for result in reversed(tool_results or []) if result.get("tool_id") == "build_robustness_report"),
            None,
        )
        if robustness_result is not None:
            return _robustness_report_fallback(robustness_result, language)
        if is_vi:
            return "Đã tạo robustness report review-only cho backtest preview hiện tại."
        return "Built a review-only robustness report for the current backtest preview."
    if "create_backtest_plan" in tool_ids:
        if is_vi:
            return "Đã tạo backtest plan review-only cho local preview. Hãy review config trước khi queue run."
        return "Created a review-only local preview plan. Review the config before queueing a run."
    return "Tool run đã hoàn tất. Hãy review kết quả phía trên trước khi tiếp tục." if is_vi else "The tool run completed successfully. Review the result above before continuing."


def _maybe_backtest_summary_response(
    final_text: str,
    tool_ids: list[str],
    tool_results: list[dict[str, Any]] | None,
    language: str = "en",
) -> str:
    if "get_backtest_summary" not in tool_ids:
        return final_text
    summary_result = next(
        (result for result in reversed(tool_results or []) if result.get("tool_id") == "get_backtest_summary"),
        None,
    )
    if summary_result is None:
        return final_text
    if not _has_available_backtest_summary(summary_result):
        if final_text.strip():
            return final_text
        return _backtest_summary_unavailable_message(language)
    lower_text = final_text.lower()
    looks_missing = any(
        marker in lower_text
        for marker in (
            "didn't return specific",
            "did not return specific",
            "không trả về",
            "không thấy kết quả",
            "n/a",
        )
    )
    has_summary_metrics = any(marker in lower_text for marker in ("pnl", "drawdown", "trade", "win rate", "trades"))
    if looks_missing or not has_summary_metrics:
        return _backtest_summary_fallback(summary_result, language)
    return final_text


def _maybe_backtest_trades_response(
    final_text: str,
    tool_ids: list[str],
    tool_results: list[dict[str, Any]] | None,
    language: str = "en",
) -> str:
    if "query_backtest_trades" not in tool_ids:
        return final_text
    trades_result = next(
        (result for result in reversed(tool_results or []) if result.get("tool_id") == "query_backtest_trades"),
        None,
    )
    if trades_result is None:
        return final_text
    output = trades_result.get("output") if isinstance(trades_result.get("output"), dict) else {}
    trades = output.get("trades") if isinstance(output.get("trades"), list) else []
    if not trades:
        return final_text or _backtest_trades_fallback(trades_result, language)
    lower_text = final_text.lower()
    looks_deferred = any(
        marker in lower_text
        for marker in (
            "let me",
            "i'll fetch",
            "i will fetch",
            "need to actually retrieve",
            "right away",
            "để mình",
            "mình sẽ tải",
        )
    )
    has_trade_rows = "#1" in final_text or "trade_rank" in lower_text or "loaded " in lower_text and "trades" in lower_text
    if looks_deferred or not has_trade_rows:
        return _backtest_trades_fallback(trades_result, language)
    return final_text


def _maybe_auto_chain_final_response(
    final_text: str,
    tool_ids: list[str],
    tool_results: list[dict[str, Any]] | None,
    language: str = "en",
    *,
    auto_chain_started: bool,
) -> str:
    if not auto_chain_started:
        return final_text
    if "create_backtest_plan" in tool_ids and "run_backtest_preview" not in tool_ids:
        lower_text = final_text.lower()
        if "approval" in lower_text or "approve" in lower_text or "phê duyệt" in lower_text:
            return final_text
        if language == "vi":
            approval_text = (
                "Mình đã tạo backtest plan và đang chờ bạn phê duyệt trước khi queue local preview. "
                "Preview này chỉ là kiểm thử local sandbox, không phải TradingView proof, broker proof, "
                "live trading evidence, hoặc profitability claim."
            )
        else:
            approval_text = (
                "I created the backtest plan and am waiting for your approval before queueing the local preview. "
                "This preview is local sandbox evidence only, not TradingView proof, broker proof, live trading evidence, "
                "or a profitability claim."
            )
        return f"{final_text.rstrip()}\n\n{approval_text}"
    if "run_backtest_preview" not in tool_ids:
        return final_text
    lower_text = final_text.lower()
    if "queued" in lower_text or "đã queue" in lower_text or "backtest queued" in lower_text:
        return final_text
    queued_text = _tool_only_success_message(tool_ids, language, tool_results=tool_results)
    return f"{final_text.rstrip()}\n\n{queued_text}"


def _backtest_summary_fallback(tool_result: dict[str, Any], language: str = "en") -> str:
    output = tool_result.get("output") if isinstance(tool_result.get("output"), dict) else tool_result
    summary = output.get("summary") if isinstance(output.get("summary"), dict) else {}
    return format_backtest_summary_text(summary, language=language)


def _has_available_backtest_summary(tool_result: dict[str, Any]) -> bool:
    output = tool_result.get("output") if isinstance(tool_result.get("output"), dict) else tool_result
    return output.get("status") in (None, "ok") and isinstance(output.get("summary"), dict)


def _backtest_summary_unavailable_message(language: str = "en") -> str:
    if _normalize_language(language) == "vi":
        return "Summary backtest chưa sẵn sàng cho run này. Hãy thử lại sau khi backtest hoàn tất."
    return "The backtest summary is not available for this run yet. Try again after the backtest completes."


def _backtest_trades_fallback(tool_result: dict[str, Any], language: str = "en") -> str:
    output = tool_result.get("output") if isinstance(tool_result.get("output"), dict) else tool_result
    trades = output.get("trades") if isinstance(output.get("trades"), list) else []
    is_vi = _normalize_language(language) == "vi"
    if output.get("status") not in (None, "ok") or not trades:
        return (
            "Chưa có indexed trades cho backtest run này. Hãy thử lại sau khi report hoàn tất."
            if is_vi
            else "Indexed trades are not available for this backtest run yet. Try again after the report completes."
        )
    run_id = output.get("run_id")
    header = (
        f"Đã tải {len(trades)} indexed trades từ backtest run `{run_id}`." if is_vi and isinstance(run_id, str)
        else f"Loaded {len(trades)} indexed trades from backtest run `{run_id}`." if isinstance(run_id, str)
        else f"Đã tải {len(trades)} indexed trades từ backtest report." if is_vi
        else f"Loaded {len(trades)} indexed trades from the backtest report."
    )
    if output.get("fallback_used") is True:
        header += (
            " Mình dùng latest completed backtest report trong conversation vì requested run không khớp."
            if is_vi
            else " I used the latest completed backtest report in this conversation because the requested run did not match."
        )
    return f"{header} {'Xem bảng bên dưới.' if is_vi else 'See the table below.'}"


def _robustness_report_fallback(tool_result: dict[str, Any], language: str = "en") -> str:
    output = tool_result.get("output") if isinstance(tool_result.get("output"), dict) else tool_result
    report = output.get("robustness_report") if isinstance(output.get("robustness_report"), dict) else {}
    is_vi = _normalize_language(language) == "vi"
    if output.get("status") not in (None, "ok") or not report:
        return (
            "Chưa thể tạo robustness report vì backtest report chưa sẵn sàng."
            if is_vi
            else "The robustness report is not available because the backtest report is not ready."
        )
    recommendation = report.get("recommendation")
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    fail_count = sum(1 for check in checks if isinstance(check, dict) and check.get("status") == "fail")
    warn_count = sum(1 for check in checks if isinstance(check, dict) and check.get("status") == "warn")
    artifact_id = output.get("artifact_id")
    run_id = output.get("run_id")
    if is_vi:
        lines = [
            f"Đã tạo robustness report review-only cho backtest run `{run_id}`.",
            f"Recommendation: `{recommendation}` với {fail_count} fail và {warn_count} warning.",
        ]
    else:
        lines = [
            f"Built a review-only robustness report for backtest run `{run_id}`.",
            f"Recommendation: `{recommendation}` with {fail_count} failed checks and {warn_count} warnings.",
        ]
    if isinstance(artifact_id, str) and artifact_id:
        lines.append(f"Artifact: `{artifact_id}`.")
    lines.append("Local sandbox preview evidence only; not TradingView proof, broker proof, live trading evidence, or a profitability claim.")
    return "\n".join(lines)


def _format_backtest_trade_row(row: dict[str, Any]) -> str:
    trade = row.get("trade") if isinstance(row.get("trade"), dict) else row
    rank = row.get("trade_rank") or trade.get("trade_rank") or trade.get("number")
    bucket = row.get("bucket")
    side = trade.get("side") or trade.get("direction")
    opened_at = row.get("opened_at") or trade.get("opened_at") or trade.get("entry_time") or trade.get("entry_timestamp")
    closed_at = row.get("closed_at") or trade.get("closed_at") or trade.get("exit_time") or trade.get("exit_timestamp")
    pnl_cost = row.get("pnl_cost") if row.get("pnl_cost") is not None else trade.get("pnl_cost")
    pnl_pct = row.get("pnl_percentage") if row.get("pnl_percentage") is not None else trade.get("pnl_percentage")
    parts: list[str] = []
    if rank is not None:
        parts.append(f"#{rank}")
    if isinstance(bucket, str) and bucket:
        parts.append(bucket.replace("_", " "))
    if isinstance(side, str) and side:
        parts.append(side)
    pnl_parts = []
    if isinstance(pnl_cost, int | float):
        pnl_parts.append(f"{float(pnl_cost):+.2f}")
    if isinstance(pnl_pct, int | float):
        pnl_parts.append(f"{float(pnl_pct):+.2f}%")
    if pnl_parts:
        parts.append(f"P&L {' / '.join(pnl_parts)}")
    if isinstance(opened_at, str) and opened_at:
        if isinstance(closed_at, str) and closed_at:
            parts.append(f"{opened_at} -> {closed_at}")
        else:
            parts.append(opened_at)
    return ", ".join(parts) if parts else "indexed trade row"


def _requested_tool_output_limit(value: Any, *, default: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, maximum))


def _auto_chain_failure_message(tool_id: str, reason: str, language: str = "en") -> str:
    is_vi = _normalize_language(language) == "vi"
    safe_reason = _user_facing_preview_failure_text(reason)
    if is_vi:
        return (
            f"Mình đã dừng auto-chain backtest ở bước `{tool_id}`: {safe_reason}. "
            "Pine artifact/plan hiện có vẫn là review-only; hãy bổ sung symbol, timeframe, date range hoặc sửa validation rồi chạy lại preview."
        )
    return (
        f"I stopped the backtest auto-chain at `{tool_id}`: {safe_reason}. "
        "The current Pine artifact/plan remains review-only; add the missing symbol, timeframe, date range, or fix validation before running the preview again."
    )


def _user_facing_preview_failure_text(value: str) -> str:
    text = re.sub(r"pineforge[-_\s]*(?:runner|engine)?", "local preview", value, flags=re.IGNORECASE)
    text = re.sub(r"\bpineforge\b", "local preview", text, flags=re.IGNORECASE)
    text = re.sub(r"\brunner\b", "preview runtime", text, flags=re.IGNORECASE)
    text = re.sub(r"\bengine\b", "preview runtime", text, flags=re.IGNORECASE)
    text = re.sub(r"\bcompile(?:d|r)?\b", "compatibility", text, flags=re.IGNORECASE)
    text = re.sub(r"\btranspile(?:d|r)?\b", "compatibility", text, flags=re.IGNORECASE)
    return text


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _generate_pine_fallback_summary(tool_result: dict[str, Any], language: str = "en") -> str:
    is_vi = _normalize_language(language) == "vi"
    spec = tool_result.get("strategy_spec") if isinstance(tool_result.get("strategy_spec"), dict) else {}
    artifact_name = _safe_string(tool_result.get("artifact_name")) or "strategy.pine"
    market = _safe_string(spec.get("market"))
    symbol = _safe_string(spec.get("symbol"))
    timeframe = _safe_string(spec.get("timeframe"))
    premise = _strategy_premise_from_spec(spec)
    context_parts = [part for part in (market, symbol, timeframe) if part]
    context_text = " / ".join(context_parts) if context_parts else None
    if is_vi:
        lines = [f"Đã tạo artifact Pine v6 review-only `{artifact_name}`."]
        if context_text:
            lines.append(f"Giả định chính: {context_text}.")
        if premise:
            lines.append(f"Logic chính: {premise}.")
        lines.append("Đây chưa phải TradingView/runtime proof, backtest proof, hay broker/live deployment approval.")
        lines.append("Bước tiếp theo nên là review risk, static validation, rồi Backtest Preview nếu bạn muốn local preview evidence.")
        return "\n".join(lines)
    lines = [f"Generated the review-only Pine v6 artifact `{artifact_name}`."]
    if context_text:
        lines.append(f"Main assumptions: {context_text}.")
    if premise:
        lines.append(f"Core logic: {premise}.")
    lines.append("This is not TradingView/runtime proof, backtest proof, or broker/live deployment approval.")
    lines.append("Next step: review risk, run static validation, then use Backtest Preview for local preview evidence.")
    return "\n".join(lines)


def _strategy_premise_from_spec(spec: dict[str, Any]) -> str | None:
    for key in ("strategy_name", "name", "premise", "description", "setup"):
        value = _safe_string(spec.get(key))
        if value:
            return value[:180]
    entry_rules = spec.get("entry_rules")
    if isinstance(entry_rules, list):
        for item in entry_rules:
            value = _safe_string(item)
            if value:
                return value[:180]
    return None


def _market_snapshot_source_required_message(language: str = "en") -> str:
    if _normalize_language(language) == "vi":
        return "Mình chưa xác minh được nguồn cho giá hiện tại, nên không hiển thị market snapshot. Hãy thử lại với web search hoặc nguồn cụ thể."
    return "I could not verify a source for the current price, so I did not show a market snapshot. Try again with web search or a specific source."


def _tool_label(tool_name: str) -> str:
    definition = TOOL_DEFINITIONS.get(tool_name)
    return definition.description if definition is not None else tool_name.replace("_", " ")


def _tool_activity_label(tool_name: str, language: str = "en") -> str:
    labels = (
        {
            "generate_pine": "Tạo Pine v6",
            "create_mql5_design": "Tạo thiết kế MQL5",
            "static_validate": "Validate Pine",
            "parallel_review": "Review artifact",
            "knowledge_check": "Kiểm tra knowledge context",
            "knowledge_proposal": "Đề xuất cập nhật knowledge",
            "create_backtest_plan": "Tạo backtest plan",
            "run_backtest_preview": "Queue backtest preview",
            "run_backtest_variant_lab": "Queue variant lab",
        }
        if _normalize_language(language) == "vi"
        else {
            "generate_pine": "Generate Pine v6",
            "create_mql5_design": "Create MQL5 design",
            "static_validate": "Validate Pine",
            "parallel_review": "Review artifact",
            "knowledge_check": "Check knowledge context",
            "knowledge_proposal": "Propose knowledge update",
            "create_backtest_plan": "Create backtest plan",
            "run_backtest_preview": "Queue backtest preview",
            "run_backtest_variant_lab": "Queue variant lab",
        }
    )
    return labels.get(tool_name, _tool_label(tool_name))


def _tool_user_summary(tool_name: str, output: dict[str, Any], language: str = "en") -> str:
    is_vi = _normalize_language(language) == "vi"
    if tool_name == "knowledge_check":
        summary = output.get("knowledge_context_summary")
        if isinstance(summary, dict):
            doc_count = len(summary.get("internal_doc_ids") or [])
            source_count = len(summary.get("external_source_ids") or [])
            chunk_count = summary.get("retrieved_chunk_count") or 0
            missing = summary.get("missing_context") or []
            suffix = " Có ghi nhận context còn thiếu." if missing and is_vi else " Missing context was noted." if missing else ""
            if is_vi:
                return f"Đã kiểm tra knowledge context: {doc_count} internal docs, {chunk_count} retrieved chunks, {source_count} external refs.{suffix}"
            return f"Checked knowledge context: {doc_count} internal docs, {chunk_count} retrieved chunks, {source_count} external refs.{suffix}"
        return "Đã kiểm tra knowledge context cho request." if is_vi else "Checked knowledge context for the request."
    if tool_name == "generate_pine" and isinstance(output.get("pine_code"), str):
        return "Đã tạo code Pine v6 review-only từ strategy spec đã cung cấp." if is_vi else "Generated review-only Pine v6 code from the provided strategy spec."
    if tool_name == "static_validate":
        return "Đã hoàn tất static validation cho Pine artifact." if is_vi else "Completed static validation for the Pine artifact."
    if tool_name == "parallel_review":
        return "Đã chuẩn bị review notes cho artifact." if is_vi else "Prepared review notes for the artifact."
    if tool_name == "create_mql5_design":
        return "Đã chuẩn bị MQL5 design note từ strategy spec đã cung cấp." if is_vi else "Prepared an MQL5 design note from the provided strategy spec."
    if tool_name == "create_backtest_plan":
        return "Đã tạo backtest plan review-only cho local preview." if is_vi else "Created a review-only local preview plan."
    if tool_name == "run_backtest_preview":
        run_id = output.get("run_id")
        if is_vi:
            return f"Đã queue backtest-preview run {run_id}." if run_id else "Đã queue backtest-preview run."
        return f"Queued backtest-preview run {run_id}." if run_id else "Queued a backtest-preview run."
    if tool_name == "run_backtest_variant_lab":
        variants = output.get("variants")
        count = len(variants) if isinstance(variants, list) else 0
        if is_vi:
            return f"Đã queue {count} backtest variants để so sánh." if count else "Đã queue backtest variant lab."
        return f"Queued {count} comparable backtest variants." if count else "Queued a backtest variant lab."
    return "Tool output đã sẵn sàng." if is_vi else "Tool output is ready."


def _missing_current_context_message(message_content: str, prior_context_text: str, language: str = "en") -> str | None:
    if not _needs_existing_strategy_context(message_content):
        return None
    if _strategy_context_lexical_hint(prior_context_text):
        return None
    if _normalize_language(language) == "vi":
        return (
            "Mình chưa có strategy spec hiện tại hoặc chưa đủ strategy rules trong conversation này. "
            "Hãy chia sẻ entry, exit, risk, market và timeframe, hoặc dùng Chuyển thành strategy spec trước; sau đó mình có thể tạo artifact Pine v6 review-only."
        )
    return (
        "I do not have a current strategy spec or enough strategy rules in this conversation yet. "
        "Share the entry, exit, risk, market, and timeframe details, or use Turn into strategy spec first, then I can generate a review-only Pine v6 artifact."
    )


def _needs_existing_strategy_context(message_content: str) -> bool:
    normalized = message_content.lower()
    return "current strategy context" in normalized or "current strategy spec" in normalized or "existing strategy context" in normalized


def _strategy_context_lexical_hint(text: str) -> bool:
    normalized = text.lower()
    required_terms = ["entry", "exit", "risk", "strategy", "indicator", "ema", "rsi", "atr", "stop", "take profit", "timeframe"]
    return sum(1 for term in required_terms if term in normalized) >= 2


def _summary(value: Any, *, max_chars: int = 240) -> str:
    text = redact_text(str(redact_value(value)))
    return text if len(text) <= max_chars else f"{text[:max_chars].rstrip()}..."


def deterministic_conversation_title(message: str, *, max_chars: int = 60) -> str:
    normalized = " ".join(redact_text(message).split())
    if not normalized:
        return "New chat"
    title = normalized[:max_chars].rstrip()
    if len(normalized) > max_chars and " " in title:
        title = title.rsplit(" ", 1)[0].rstrip()
    return title.rstrip(".,;:!?") or "New chat"


def _normalize_title(value: str) -> str | None:
    title = " ".join(redact_text(value).replace("\n", " ").split())
    title = title.strip(" `\"'“”‘’")
    if not title:
        return None
    for prefix in ("Title:", "Chat title:", "Conversation title:"):
        if title.lower().startswith(prefix.lower()):
            title = title[len(prefix):].strip()
    title = title.strip(" `\"'“”‘’")
    if not title:
        return None
    return title[:160].rstrip()


def _title_system_prompt() -> str:
    return (
        "Create a short chat title for Strategy Codebot. "
        "Return only the title, 3-6 words, no markdown, no quotes, no emoji. "
        "Use the user's language when clear. "
        "Do not mention profitability, live trading, backtests, providers, tools, or internal systems."
    )


def _title_prompt(user_message: str) -> str:
    return f"User message:\n{redact_text(user_message)}"


def _sanitize_user_facing_model_text(value: str) -> str:
    return (
        value.replace("PineForge local Pine preview evidence only", "Local sandbox preview evidence only")
        .replace("PineForge local Pine preview evidence", "Local sandbox preview evidence")
        .replace("PineForge (Local Preview)", "local preview")
        .replace("PineForge Preview", "Backtest Preview")
        .replace("PineForge local Pine preview", "local sandbox preview")
        .replace("PineForge output", "Local sandbox preview output")
        .replace("PineForge compile/backtest", "local preview")
        .replace("PineForge backtest", "backtest preview")
        .replace("PineForge", "local sandbox preview")
        .replace("pineforge-engine", "local preview")
        .replace("pineforge-runner", "local preview")
    )


def _system_prompt(language: str = "en", *, web_search: str = "auto") -> str:
    language_instruction = (
        "Respond in Vietnamese for user-facing chat text. Keep code, Pine syntax, JSON schema keys, tool ids, artifact filenames, and policy/event codes unchanged."
        if _normalize_language(language) == "vi"
        else "Respond in English for user-facing chat text unless the user explicitly asks otherwise. Keep code, Pine syntax, JSON schema keys, tool ids, artifact filenames, and policy/event codes unchanged."
    )
    web_search_instruction = _web_search_instruction(web_search)
    workflow_guidance = workflow_catalog_guidance()
    return f"""You are Strategy Codebot, a trading-strategy assistant that helps users create reviewable strategy specs, code artifacts, and review notes.

<language>
{language_instruction}
</language>

<knowledge_access>
{web_search_instruction}
</knowledge_access>

<safety_boundaries>
- You may only request the provided trading strategy tools.
- You may use the built-in web_search tool only when Search mode enables it.
- Do not request shell, arbitrary network, arbitrary file, broker, exchange, or live trading actions.
- For Bots, you may draft setup proposals and read status/events only; starting, stopping, or kill-switch actions require explicit user confirmation through the UI.
- Do not claim profitability, compile success, runtime success, or backtest success without evidence.
- Keep every generated artifact and recommendation review-only.
- Do not reveal internal implementation names, package names, service names, provider routes, runner names, or engine/vendor names.
- If users ask what backtest engine, runner, provider, or package is used, answer only with the product term "local sandbox preview" or "Backtest Preview"; do not name internal dependencies.
</safety_boundaries>

<response_style>
- Use polished Markdown by default.
- For multi-part answers, use short `##` headings and scannable bullet lists.
- Use numbered lists for workflows or ordered steps.
- Use Markdown tables when comparing modes, platforms, or tradeoffs.
- Use fenced code blocks with a language tag for code or structured examples.
- Keep simple greetings and very short answers natural in one or two sentences; do not over-format them.
- Prefer concise, useful structure over long prose.
</response_style>

<domain_shape>
- For capability questions, include what you can help with and the review-only boundary.
- For strategy requests, summarize the idea, clarify assumptions if needed, then propose next steps.
- For generated code or reports, keep the chat answer brief and point users to the reviewable artifact when available.
- After generate_pine succeeds, always produce a short final summary covering the strategy premise, symbol/timeframe assumptions, artifact name, key risk/review caveat, and the next validation or preview action. Do not stop after the tool call unless the runtime stops you.
- For Backtest Preview requests, require PineScript v6 strategy source, use create_backtest_plan before queueing when config assumptions are not explicit, and treat run_backtest_preview and run_backtest_variant_lab outputs as queued local preview jobs only; never claim backtest success until report artifacts exist.
- For Strategy to Paper Bot Simulation workflows, ask only the missing strategy design fields first; draft a strategy spec before any Bot proposal; keep Bot setup as paper simulation only; never start or imply broker execution from chat text.
</domain_shape>

<workflow_ui>
{workflow_guidance}
</workflow_ui>"""


def _web_search_instruction(web_search: str) -> str:
    mode = _normalize_web_search(web_search)
    if mode == "off":
        return (
            "Web search mode: off. Base answers on the conversation, internal knowledge context, and provided artifacts only. "
            "If current external information is needed, say that web search is off and ask the user to enable Search."
        )
    if mode == "on":
        return (
            "Web search mode: on. Use available web-search/source-evidence capability for current external facts, recent docs, provider availability, or source-backed claims. "
            "If no web-search tool is available in this chat route, be explicit that you are using internal context only."
        )
    return (
        "Web search mode: auto. Prefer internal context for normal strategy generation. Use available web-search/source-evidence capability only when the user asks for latest/current information, external docs, provider/model availability, or source-backed claims. "
        "If no web-search tool is available, continue with internal context and avoid pretending that live web research was performed."
    )


def _provider_tools_for_web_search(
    web_search: str,
    message_content: str,
    *,
    response_intent: str | None = None,
    current_context_required: bool = False,
    decision_source: str | None = None,
) -> list[dict[str, Any]]:
    _ = message_content
    mode = _normalize_web_search(web_search)
    if mode == "off":
        return provider_tools()
    tools = provider_tools()
    if mode == "on":
        tools.append({"type": "web_search"})
        return tools
    if _decision_allows_readonly_web_search(
        response_intent=response_intent,
        current_context_required=current_context_required,
        decision_source=decision_source,
        web_search=mode,
    ):
        return [{"type": "web_search"}]

    return tools


def _has_web_search_tool(tools: list[dict[str, Any]]) -> bool:
    return any(tool.get("type") == "web_search" for tool in tools)


def _is_classifier_fallback_source(source: str | None) -> bool:
    return source in {"fallback", "timeout_fallback"} or source in WORKFLOW_KICKOFF_FALLBACK_SOURCES


def _decision_allows_readonly_web_search(
    *,
    response_intent: str | None,
    current_context_required: bool,
    decision_source: str | None,
    web_search: str,
) -> bool:
    return current_context_policy_decision(
        web_search=web_search,
        response_intent=response_intent,
        current_context_required=current_context_required,
        decision_source=decision_source,
    ).enabled


def _should_enable_web_search_auto(message_content: str) -> bool:
    return current_context_signal(message_content)
