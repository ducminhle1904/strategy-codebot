from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field, replace
import json
import logging
import os
import re
from typing import Any

from strategy_codebot.server.action_registry import action_registry_payload
from strategy_codebot.server.action_registry import available_registry_tool_ids
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
from strategy_codebot.server.llm_clients import LLMClient, LLMClientEvent, ResponsesClient
from strategy_codebot.server.llm_clients import LLM_EVENT_MESSAGE_DELTA
from strategy_codebot.server.llm_clients import LLM_EVENT_SOURCES
from strategy_codebot.server.llm_clients import LLM_EVENT_TOOL_CALL
from strategy_codebot.server.llm_clients import LLM_EVENT_USAGE
from strategy_codebot.server.llm_json import extract_json_object
from strategy_codebot.server.llm_tools import ToolExecutionContext
from strategy_codebot.server.llm_tools import TOOL_DEFINITIONS
from strategy_codebot.server.llm_tools import compact_tool_output
from strategy_codebot.server.llm_tools import execute_tool
from strategy_codebot.server.llm_tools import provider_tools
from strategy_codebot.server.llm_tools import validate_tool_arguments
from strategy_codebot.server.knowledge_learning import KnowledgeLearningService
from strategy_codebot.server.market_data import MarketDataGateway
from strategy_codebot.server.market_data import market_data_context
from strategy_codebot.server.model_routing import DEFAULT_MODEL_STAGE
from strategy_codebot.server.model_routing import MODEL_STAGE_BALANCED_REVIEW
from strategy_codebot.server.model_routing import MODEL_STAGE_PINE_CODE_GENERATION
from strategy_codebot.server.model_routing import MODEL_STAGE_REPAIR
from strategy_codebot.server.model_routing import PROVIDER_ROUTE_EVENT
from strategy_codebot.server.observability import StageTimer
from strategy_codebot.server.observability import append_stage_event
from strategy_codebot.server.observability import append_stage_started_event
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
from strategy_codebot.server.workflow_registry import STRATEGY_BOT_REQUIRED_INPUT_FIELDS
from strategy_codebot.server.workflow_registry import STRATEGY_BOT_SETUP_FIELDS
from strategy_codebot.server.workflow_registry import STRATEGY_BOT_WORKFLOW_ID
from strategy_codebot.server.workflow_registry import STRATEGY_BOT_WORKFLOW_STEPS
from strategy_codebot.server.workflow_registry import validate_workflow_payload
from strategy_codebot.server.workflow_registry import workflow_catalog_guidance

logger = logging.getLogger(__name__)

SAFE_REASONING_EVENT = "model.reasoning.delta"
SUGGESTIONS_EVENT = "chat.suggestions.updated"
STRATEGY_WORKFLOW_EVENT = "chat.workflow.updated"
CLASSIFIER_TIMEOUT_SECONDS_ENV = "STRATEGY_CODEBOT_CLASSIFIER_TIMEOUT_SECONDS"
DEFAULT_CLASSIFIER_TIMEOUT_SECONDS = 25.0
ACTION_PLANNER_ENABLED_ENV = "STRATEGY_CODEBOT_ACTION_PLANNER_ENABLED"
RESPONSE_INTENTS = {
    "artifact_generation",
    "backtest_preview",
    "capability_help",
    "docs_research",
    "general_chat",
    "market_research",
    "market_snapshot",
    "pine_generation",
    "strategy_building",
}
SUGGESTION_SLOTS = {"entry", "exit", "market", "risk"}
RESPONSE_INTENT_FALLBACK_CONFIDENCE = 0.35
RESPONSE_INTENT_LLM_MIN_CONFIDENCE = 0.6
SEMANTIC_ACTION_MIN_CONFIDENCE = 0.65
CHAT_INTENT_DECISION_MIN_CONFIDENCE = 0.65
CHAT_INTENT_ACTIONS = {"answer", "call_tool", "suggest_actions", "ask_clarification", "start_auto_chain"}
CHAT_INTENT_MODEL_STAGES = {
    DEFAULT_MODEL_STAGE,
    MODEL_STAGE_BALANCED_REVIEW,
    MODEL_STAGE_PINE_CODE_GENERATION,
    MODEL_STAGE_REPAIR,
}
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
    "retrieval": {
        "en": "Checking relevant knowledge.",
        "vi": "Đang kiểm tra knowledge context liên quan.",
    },
    "tool": {
        "en": "Running the required support step.",
        "vi": "Đang chạy bước hỗ trợ cần thiết.",
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
class DomainScopeDecision:
    allowed: bool
    scope: str
    reason: str
    confidence: float

    def payload(self) -> dict[str, Any]:
        return {
            "domain_scope": self.scope,
            "domain_scope_allowed": self.allowed,
            "domain_scope_confidence": round(max(0.0, min(1.0, self.confidence)), 3),
            "domain_scope_reason": self.reason,
        }


def _run_optional_classifier(
    classifier_name: str,
    classify: Callable[[], Any],
    fallback: Any,
    *,
    log_context: dict[str, Any] | None = None,
) -> Any:
    timeout_seconds = _classifier_timeout_seconds()
    if timeout_seconds <= 0:
        return classify()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"strategy-{classifier_name}")
    future = executor.submit(classify)
    try:
        return future.result(timeout=timeout_seconds)
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
            return replace(fallback, source="timeout_fallback")
        except TypeError:
            return fallback
    except Exception:
        agent_log(
            logger,
            "error",
            "agent.classifier.failed",
            component="llm_orchestrator",
            classifier=classifier_name,
            **(log_context or {}),
        )
        return fallback
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _classifier_timeout_seconds() -> float:
    raw = os.getenv(CLASSIFIER_TIMEOUT_SECONDS_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_CLASSIFIER_TIMEOUT_SECONDS
    try:
        timeout = float(raw)
    except ValueError:
        return DEFAULT_CLASSIFIER_TIMEOUT_SECONDS
    return max(0.0, timeout)


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
        if not _has_intent_classifier_signal(message_content):
            return IntentClassification("general_chat", 0.75, "deterministic")
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
                routing_context={"stage": DEFAULT_MODEL_STAGE},
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
            "intent": self.response_intent,
            "model_stage": self.model_stage,
            "safe": True,
            "source": self.source,
        }
        if self.tool_id:
            payload["tool_id"] = self.tool_id
        if self.missing_inputs:
            payload["missing_inputs"] = list(self.missing_inputs)
        if self.reasons:
            payload["reasons"] = list(self.reasons)
        if self.used_signals:
            payload["used_signals"] = list(self.used_signals)
        return payload


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
        log_context: dict[str, Any] | None = None,
    ) -> ChatIntentDecision:
        regex_evidence = _chat_regex_evidence(message_content)
        registry_payload = action_registry_payload(
            artifact_kinds=artifact_kinds,
            context_text=f"{context_text}\n{message_content}",
            web_search=web_search,
        )
        available_tools = {
            str(item.get("tool_id"))
            for item in registry_payload
            if item.get("available") is True and isinstance(item.get("tool_id"), str)
        }
        fallback = _fallback_chat_intent_decision(
            message_content,
            web_search=web_search,
            regex_evidence=regex_evidence,
        )
        return _run_optional_classifier(
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
                fallback=fallback,
            ),
            fallback,
            log_context=log_context,
        )

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
        fallback: ChatIntentDecision,
    ) -> ChatIntentDecision:
        prompt = {
            "user_message": message_content[:2000],
            "language": language,
            "artifact_kinds": sorted(artifact_kinds),
            "context_excerpt": context_text[-3000:],
            "web_search": web_search,
            "regex_evidence": regex_evidence,
            "actions": registry_payload,
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
                routing_context={"stage": DEFAULT_MODEL_STAGE},
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
    ) -> ActionPlanDecision:
        registry_payload = action_registry_payload(
            artifact_kinds=artifact_kinds,
            context_text=f"{context_text}\n{message_content}",
            web_search=web_search,
        )
        available_tools = {
            item.get("tool_id")
            for item in registry_payload
            if item.get("available") is True and isinstance(item.get("tool_id"), str)
        }
        if not available_tools:
            return ActionPlanDecision("answer", "none", 0.0, "none")
        return _run_optional_classifier(
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
                routing_context={"stage": DEFAULT_MODEL_STAGE},
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
        trace_id: str | None = None,
        web_search: str = "auto",
    ) -> Iterator[str]:
        language = _normalize_language(language)
        web_search = _normalize_web_search(web_search)
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
        accumulated_text: list[str] = []
        terminal_status: str | None = None
        context_builder = ConversationContextBuilder(self.repository)
        artifact_kinds = _conversation_user_artifact_kinds(self.repository, auth, conversation_id, current_run_id=run.id)
        domain_scope = _classify_domain_scope(message_content, artifact_kinds=artifact_kinds)
        if not domain_scope.allowed:
            yield from self._domain_scope_blocked(
                auth=auth,
                run=run,
                conversation_id=conversation_id,
                domain_scope=domain_scope,
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
        suggestion_context_text = conversation_context.prior_context_text
        context_guard_message = _missing_current_context_message(
            message_content,
            conversation_context.prior_context_text,
            language,
        )
        chat_decision = (
            ChatIntentDecision(
                response_intent="artifact_generation",
                action="ask_clarification",
                model_stage=MODEL_STAGE_PINE_CODE_GENERATION,
                confidence=1.0,
                source="deterministic_safety",
                missing_inputs=("current_strategy_context",),
                reasons=("The user referenced current strategy context but none is available.",),
            )
            if context_guard_message is not None
            else ChatIntentDecisionPlanner(self.client).decide(
                message_content,
                context_text=suggestion_context_text,
                artifact_kinds=artifact_kinds,
                web_search=web_search,
                language=language,
                log_context={
                    "conversation_id": conversation_id,
                    "request_id": run.request_id,
                    "run_id": run.id,
                    "trace_id": run.trace_id,
                },
            )
        )
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
        action_plan = (
            ActionPlanner(self.client).plan(
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
            )
            if _action_planner_enabled() and context_guard_message is None
            else ActionPlanDecision("answer", "none", 0.0, "none")
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
        )
        workflow_payload = _strategy_bot_workflow_payload(
            message_content=message_content,
            context_text=suggestion_context_text,
            artifact_kinds=artifact_kinds,
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
            yield self._append_frame(
                auth,
                run,
                "chat.response_intent",
                chat_decision.payload(),
            )
            if workflow_payload is not None:
                yield self._append_frame(auth, run, STRATEGY_WORKFLOW_EVENT, workflow_payload)
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
                yield self._append_frame(auth, completed or run, "run.completed", {"status": terminal_status})
                if terminal_status == "completed":
                    self._maybe_compact_conversation(auth, conversation_id, run.id, language=language)
                return

            active_tools = (
                []
                if market_snapshot is not None
                else _provider_tools_for_web_search(
                    web_search,
                    message_content,
                    response_intent=response_intent,
                    current_context_required=chat_decision.current_context_required,
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
                )
                if budget.blocked:
                    break
            append_stage_event(self.repository, auth, run, "model", model_timer.elapsed_ms())
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
            terminal_status = "blocked" if budget.blocked else "completed"
            completed = self.repository.set_run_status(auth, run.id, terminal_status)
            append_stage_event(self.repository, auth, completed or run, "response_finalization", 0, status=terminal_status)
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
    ) -> Iterator[str]:
        block = self._gate_tool(auth, run, tool_name, arguments, budget)
        if block is not None:
            budget.blocked = True
            yield from self._policy_blocked(auth, run, tool_name, block, language=language)
            return

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
        yield self._safe_reasoning_frame(auth, run, _reasoning_phase_for_tool(tool_name), language)
        tool_timer = StageTimer()
        append_stage_started_event(self.repository, auth, run, "tool")
        try:
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
                input_tokens=_token_estimate(arguments),
                output_tokens=_token_estimate(compact_output),
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
            workflow_candidate = _is_strategy_bot_workflow_request(
                user_message or "",
                context_text=context_text,
                artifact_kinds=set(),
                tool_name=tool_name,
            )
            post_tool_artifact_kinds: set[str] | None = None
            if workflow_candidate or tool_name == "generate_pine":
                post_tool_artifact_kinds = _conversation_user_artifact_kinds(
                    self.repository,
                    auth,
                    run.conversation_id,
                    current_run_id=run.id,
                )
                post_tool_artifact_kinds.update(_current_run_user_artifact_kinds(self.repository, auth, run.id))
            workflow_payload = (
                _strategy_bot_workflow_payload(
                    message_content=user_message or "",
                    context_text=context_text,
                    artifact_kinds=post_tool_artifact_kinds or set(),
                    tool_name=tool_name,
                    tool_result=output,
                )
                if workflow_candidate
                else None
            )
            if workflow_payload is not None:
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
            yield self._append_frame(auth, run, "tool.completed", error_payload)
            append_stage_event(self.repository, auth, run, "tool", tool_timer.elapsed_ms(), status="failed")
            raise

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

    def _safe_reasoning_frame(self, auth: AuthContext, run: AssistantRunRecord, phase: str, language: str = "en") -> str:
        return sse_frame(transient_reasoning_event(run, payload=redact_value(_safe_reasoning_payload(phase, language))))

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


def _stream_client(
    client: LLMClient,
    *,
    messages: list[dict[str, str]],
    tools: list[dict[str, Any]],
    routing_context: dict[str, Any],
) -> Iterator[LLMClientEvent]:
    try:
        yield from client.stream(messages=messages, tools=tools, routing_context=routing_context)
    except TypeError as exc:
        if "routing_context" not in str(exc):
            raise
        yield from client.stream(messages=messages, tools=tools)


def _model_stage_for_chat(
    message_content: str,
    *,
    response_intent: str | None,
    active_tools: list[dict[str, Any]],
    decision_model_stage: str | None = None,
) -> str:
    if decision_model_stage in CHAT_INTENT_MODEL_STAGES:
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


def _safe_reasoning_payload(phase: str, language: str = "en") -> dict[str, Any]:
    normalized_phase = phase if phase in SAFE_REASONING_LABELS else "model"
    return {
        "phase": normalized_phase,
        "safe": True,
        "text": _safe_reasoning_text(normalized_phase, language),
    }


def _safe_reasoning_text(phase: str, language: str = "en") -> str:
    return SAFE_REASONING_LABELS[phase][_normalize_language(language)]


def _reasoning_phase_for_tool(tool_name: str) -> str:
    if tool_name == "knowledge_check":
        return "retrieval"
    if tool_name in {
        "generate_pine",
        "static_validate",
        "parallel_review",
        "create_backtest_plan",
        "run_backtest_preview",
        "run_backtest_variant_lab",
    }:
        return "artifact"
    return "tool"


def _normalize_language(language: str | None) -> str:
    return "vi" if language == "vi" else "en"


def _normalize_web_search(web_search: str | None) -> str:
    return web_search if web_search in {"off", "auto", "on"} else "auto"


def _classify_domain_scope(
    message_content: str,
    *,
    artifact_kinds: set[str] | None = None,
) -> DomainScopeDecision:
    normalized = " ".join(message_content.lower().split())
    artifact_kinds = artifact_kinds or set()
    if not normalized:
        return DomainScopeDecision(True, "product_help", "empty_or_whitespace", 0.8)
    if _is_trading_or_product_domain_request(normalized):
        return DomainScopeDecision(True, "trading_or_product", "domain_signal", 0.95)
    if _is_artifact_context_followup_request(normalized, artifact_kinds):
        return DomainScopeDecision(True, "context_followup", "artifact_context_signal", 0.86)
    if _is_small_talk_or_context_followup(normalized):
        return DomainScopeDecision(True, "context_followup", "small_talk_or_context_followup", 0.72)
    if _is_explicit_off_topic_request(normalized):
        return DomainScopeDecision(False, "off_topic", "explicit_off_topic_request", 0.9)
    if _looks_like_general_task_request(normalized):
        return DomainScopeDecision(False, "off_topic", "general_task_without_trading_context", 0.78)
    return DomainScopeDecision(True, "context_followup", "ambiguous_short_followup", 0.62)


def _is_trading_or_product_domain_request(normalized: str) -> bool:
    domain_terms = (
        "action awareness",
        "artifact",
        "backtest",
        "backtest kit",
        "broker boundary",
        "crypto",
        "drawdown",
        "fees",
        "forex",
        "indicator",
        "knowledge",
        "market research",
        "mql5",
        "openrouter",
        "orderintent",
        "out-of-sample",
        "oos",
        "pine",
        "price action",
        "proposed intent",
        "risk gate",
        "robustness",
        "review-only",
        "sample size",
        "slippage",
        "strategy",
        "trade log",
        "trades",
        "trading",
        "tradingview",
        "variant lab",
        "web search",
        "win rate",
        "bot boundary",
        "chiến lược",
        "giao dịch",
        "quản trị rủi ro",
        "rủi ro",
        "thị trường",
        "vào lệnh",
    )
    market_terms = ("btc", "bitcoin", "eth", "ethereum", "xau", "gold", "usdt")
    capability_terms = ("what can you do", "strategy codebot", "bạn làm được gì", "khả năng", "hỗ trợ app")
    return any(term in normalized for term in domain_terms + market_terms + capability_terms)


def _is_artifact_context_followup_request(normalized: str, artifact_kinds: set[str]) -> bool:
    if not artifact_kinds:
        return False
    has_strategy_context = any(
        _artifact_kind_matches(
            kind,
            (
                BACKTEST_REPORT_ARTIFACT_KIND,
                BACKTEST_RUN_METADATA_ARTIFACT_KIND,
                ROBUSTNESS_REPORT_ARTIFACT_KIND,
                RISK_GATE_REPORT_ARTIFACT_KIND,
                "backtest_dashboard",
                "backtest_trades",
                "backtest_equity_curve",
                "pine",
                "strategy",
            ),
        )
        for kind in artifact_kinds
    )
    if not has_strategy_context:
        return False
    context_terms = (
        "current evidence",
        "current preview",
        "current report",
        "current result",
        "current results",
        "preview evidence",
        "review the result",
        "the evidence",
        "the preview",
        "the report",
        "the result",
        "the results",
        "this evidence",
        "this preview",
        "this report",
        "this result",
    )
    return any(term in normalized for term in context_terms)


def _is_small_talk_or_context_followup(normalized: str) -> bool:
    small_talk = {
        "hi",
        "hello",
        "hey",
        "thanks",
        "thank you",
        "ok",
        "okay",
        "chào",
        "cảm ơn",
        "oke",
    }
    if normalized in small_talk:
        return True
    followup_terms = (
        "what next",
        "what should i do next",
        "what did i mention",
        "cái này",
        "giờ sao",
        "làm gì tiếp",
        "nên làm gì",
    )
    return any(term in normalized for term in followup_terms)


def _is_explicit_off_topic_request(normalized: str) -> bool:
    if any(term in normalized for term in ("pine script", "trading script", "strategy code", "mql5")):
        return False
    off_topic_terms = (
        "cover letter",
        "essay",
        "homework",
        "javascript",
        "legal contract",
        "marketing copy",
        "math problem",
        "medical",
        "poem",
        "python script",
        "react component",
        "recipe",
        "resume",
        "song lyrics",
        "sql query",
        "travel itinerary",
        "viết email",
        "làm thơ",
        "nấu ăn",
        "du lịch",
        "bài tập",
        "hợp đồng",
    )
    return any(term in normalized for term in off_topic_terms)


def _looks_like_general_task_request(normalized: str) -> bool:
    request_terms = (
        "build",
        "create",
        "draft",
        "explain",
        "generate",
        "how do i",
        "how to",
        "summarize",
        "translate",
        "what is",
        "write",
        "dịch",
        "giải thích",
        "là gì",
        "tạo",
        "tóm tắt",
        "viết",
    )
    return len(normalized.split()) >= 3 and any(term in normalized for term in request_terms)


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
) -> dict[str, Any]:
    language = _normalize_language(language)
    combined_context = f"{context_text}\n{message_content}".lower()
    normalized_message = message_content.lower()
    artifact_kinds = artifact_kinds or set()
    missing_fields = _strategy_missing_fields(combined_context)
    readiness = "ready_for_artifact" if not missing_fields else "needs_detail"
    actions: list[dict[str, Any]] = []
    composer_blocks = (
        _composer_block_suggestions(language, missing_fields)
        if response_intent in {"strategy_building", "artifact_generation"}
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
        for entry in action_registry_payload(artifact_kinds=artifact_kinds, context_text=context_text, web_search=web_search)
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


def _is_bot_boundary_request(normalized: str) -> bool:
    terms = ("bot", "live", "paper", "order", "intent", "signal", "vào lệnh", "đặt lệnh", "chạy bot", "trade setup", "nếu trade")
    return any(term in normalized for term in terms)


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
    normalized = context.lower()
    symbol_pattern = re.compile(
        r"\b(?:BTC|ETH|SOL|BNB|XRP|EUR|GBP|JPY|XAU|AAPL|TSLA|NVDA)"
        r"(?:[/:-]?(?:USD|USDT|USDC|BTC|ETH|JPY))?\b",
        re.IGNORECASE,
    )
    timeframe_pattern = re.compile(r"\b(?:[1-9]\d?\s?(?:m|min|h|d|w)|[1-9]\d?[mhdw]|daily|hourly|weekly)\b")
    checks = {
        "market": ("market", "crypto", "forex", "stock", "equity", "futures", "thị trường", "chứng khoán"),
        "symbol": ("symbol", "ticker", "btcusdt", "ethusdt", "eurusd", "xauusd"),
        "timeframe": ("timeframe", "khung thời gian", "khung", "daily", "hourly", "intraday"),
        "style": ("trend", "mean reversion", "breakout", "scalping", "dca", "style", "phong cách"),
        "risk_preference": ("risk", "rủi ro", "conservative", "balanced", "aggressive", "an toàn", "mạo hiểm"),
    }
    missing = [field for field, terms in checks.items() if not any(term in normalized for term in terms)]
    if "symbol" in missing and symbol_pattern.search(context):
        missing.remove("symbol")
    if "timeframe" in missing and timeframe_pattern.search(normalized):
        missing.remove("timeframe")
    return missing


def _has_strategy_bot_signal(context: str) -> bool:
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


def _is_strategy_bot_workflow_request(
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
        return tool_name == "draft_bot" or _has_strategy_bot_signal(combined_for_tool)
    combined = f"{message_content}\n{context_text}".lower()
    bot_signal = _has_strategy_bot_signal(combined)
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


def _strategy_bot_workflow_payload(
    *,
    message_content: str,
    context_text: str,
    artifact_kinds: set[str],
    tool_name: str | None = None,
    tool_result: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not _is_strategy_bot_workflow_request(
        message_content,
        context_text=context_text,
        artifact_kinds=artifact_kinds,
        tool_name=tool_name,
    ):
        return None
    result = tool_result if isinstance(tool_result, dict) else {}
    combined_context = f"{message_content}\n{context_text}"
    strategy_missing = _strategy_bot_missing_fields(combined_context)

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
    start_allowed = proposal is not None and not setup_missing

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

    completed: list[str] = []
    if not strategy_missing:
        completed.append("collect_strategy_inputs")
    if has_pine:
        completed.extend(["draft_strategy_spec", "generate_pine"])
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
    elif not has_pine:
        current_step = "draft_strategy_spec"
    elif not has_validation:
        current_step = "static_validation"
    elif not has_backtest_preview:
        current_step = "backtest_preview"
    elif not has_evidence_review:
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

    return validate_workflow_payload({
        "workflow_id": STRATEGY_BOT_WORKFLOW_ID,
        "current_step": current_step,
        "completed_steps": completed_steps,
        "blocked_reason": blocked_reason,
        "required_fields": required_fields,
        "missing_fields": missing_fields,
        "artifact_refs": artifact_refs,
        "evidence_status": evidence_status,
        "bot_proposal_id": proposal_id,
        "start_allowed": start_allowed,
    })


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
    return [messages[0], {"role": "system", "content": content}, *messages[1:]]


def _copy(language: str, vi: str, en: str) -> str:
    return vi if _normalize_language(language) == "vi" else en


def _classify_response_intent(message_content: str, *, web_search: str = "auto") -> str:
    return ResponseIntentClassifier(_NoopIntentClient()).classify(
        message_content,
        web_search=web_search,
    ).intent


def _deterministic_response_intent(message_content: str, *, web_search: str = "auto") -> IntentClassification | None:
    normalized = message_content.lower()
    if _is_artifact_generation_request(normalized):
        return IntentClassification("artifact_generation", 0.96, "deterministic")
    if _is_market_snapshot_request(normalized):
        return IntentClassification("market_snapshot", 0.96, "deterministic")
    if _is_docs_research_request(normalized):
        return IntentClassification("docs_research", 0.94, "deterministic")
    if _is_market_research_request(normalized):
        return IntentClassification("market_research", 0.92, "deterministic")
    if _is_capability_help_request(normalized):
        return IntentClassification("capability_help", 0.9, "deterministic")
    if _is_strategy_building_request(normalized):
        return IntentClassification("strategy_building", 0.9, "deterministic")
    return None


class _NoopIntentClient:
    model = "local/noop-intent-classifier"

    def ensure_configured(self) -> None:
        return None

    def stream(self, *, messages: list[dict[str, str]], tools: list[dict[str, Any]]) -> Iterator[LLMClientEvent]:
        return iter(())


def _is_artifact_generation_request(normalized: str) -> bool:
    artifact_terms = (
        "artifact",
        "code",
        "pine",
        "mql5",
        "script",
        "ea",
        "expert advisor",
        "generate",
        "gen ",
        "create",
        "tạo",
        "viết code",
        "sinh code",
    )
    strategy_terms = ("strategy", "chiến lược", "indicator", "review", "spec")
    return any(term in normalized for term in artifact_terms) and any(term in normalized for term in strategy_terms)


def _is_market_snapshot_request(normalized: str) -> bool:
    asset_terms = (
        "btc",
        "bitcoin",
        "eth",
        "ethereum",
        "sol",
        "bnb",
        "xau",
        "gold",
        "forex",
        "usd",
        "usdt",
    )
    price_terms = ("price", "giá", "quote", "current", "today", "now", "hiện tại", "hôm nay", "bây giờ")
    return any(term in normalized for term in asset_terms) and any(term in normalized for term in price_terms)


def _is_docs_research_request(normalized: str) -> bool:
    doc_terms = (
        "docs",
        "documentation",
        "api",
        "sdk",
        "provider",
        "pricing",
        "version",
        "release",
        "tài liệu",
        "phiên bản",
    )
    return any(term in normalized for term in doc_terms)


def _is_market_research_request(normalized: str) -> bool:
    research_terms = ("research", "news", "latest", "sources", "citation", "tin tức", "nguồn", "nghiên cứu")
    market_terms = ("market", "crypto", "forex", "btc", "eth", "price", "giá")
    if any(term in normalized for term in research_terms) and any(term in normalized for term in market_terms):
        return True
    market_context_terms = ("market condition", "market conditions", "market setup", "market context", "market hiện tại")
    market_followup_terms = (
        "what should",
        "what do i do",
        "what to do",
        "should i",
        "suitable",
        "plan",
        "condition",
        "nên làm gì",
        "làm gì",
        "phù hợp",
    )
    return any(term in normalized for term in market_context_terms) or (
        "market" in normalized and any(term in normalized for term in market_followup_terms)
    )


def _is_capability_help_request(normalized: str) -> bool:
    return any(
        term in normalized
        for term in (
            "what can you do",
            "help me",
            "bạn làm được gì",
            "bạn hỗ trợ",
            "khả năng",
            "help",
        )
    )


def _is_strategy_building_request(normalized: str) -> bool:
    strategy_terms = (
        "strategy",
        "chiến lược",
        "entry",
        "exit",
        "stop loss",
        "take profit",
        "risk",
        "timeframe",
        "ema",
        "sma",
        "rsi",
        "breakout",
        "liquidity",
    )
    return any(term in normalized for term in strategy_terms)


def _has_intent_classifier_signal(message_content: str) -> bool:
    normalized = message_content.lower()
    signal_terms = (
        "api",
        "btc",
        "code",
        "docs",
        "eth",
        "forex",
        "indicator",
        "market",
        "mql5",
        "pine",
        "price",
        "pricing",
        "provider",
        "risk",
        "strategy",
        "trading",
        "xau",
        "chiến lược",
        "giá",
        "giao dịch",
        "luật",
        "mô hình",
        "nguồn",
        "phí",
        "tài liệu",
        "thị trường",
        "ý tưởng",
    )
    return any(term in normalized for term in signal_terms)


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
    stages = ", ".join(sorted(CHAT_INTENT_MODEL_STAGES))
    return (
        "You are Strategy Codebot's semantic chat intent gate. Return JSON only with keys: "
        "response_intent, action, model_stage, confidence, tool_id, auto_chain, "
        "current_context_required, missing_inputs, reasons, used_signals. "
        f"response_intent must be one of: {intents}. "
        f"action must be one of: {actions}. "
        f"model_stage must be one of: {stages}. "
        "Regex evidence is only a hint; decide from semantic intent, recent context, artifacts, and available actions. "
        "Use start_auto_chain or auto_chain=true when the user wants local preview/backtest evidence, including paraphrases such as simulate, paper test, preview performance, chạy thử, thử hiệu quả, or chay thu. "
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
    if model_stage not in CHAT_INTENT_MODEL_STAGES:
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
    used_signals = _safe_string_tuple(payload.get("used_signals"))
    if not used_signals and regex_evidence:
        used_signals = tuple(key for key, value in regex_evidence.items() if value)
    return ChatIntentDecision(
        response_intent=response_intent,
        action=action,
        model_stage=model_stage,
        confidence=bounded_confidence,
        source="llm",
        tool_id=tool_id,
        auto_chain=bool(payload.get("auto_chain")),
        current_context_required=bool(payload.get("current_context_required")),
        missing_inputs=_safe_string_tuple(payload.get("missing_inputs")),
        reasons=_safe_string_tuple(payload.get("reasons") or payload.get("reason")),
        used_signals=used_signals,
    )


def _fallback_chat_intent_decision(
    message_content: str,
    *,
    web_search: str,
    regex_evidence: dict[str, bool],
) -> ChatIntentDecision:
    deterministic = _deterministic_response_intent(message_content, web_search=web_search)
    response_intent = deterministic.intent if deterministic is not None else "general_chat"
    confidence = deterministic.confidence if deterministic is not None else RESPONSE_INTENT_FALLBACK_CONFIDENCE
    auto_chain = bool(regex_evidence.get("explicit_backtest") or regex_evidence.get("preview_intent"))
    source = "fallback_regex" if auto_chain else (deterministic.source if deterministic is not None else "fallback")
    action = "start_auto_chain" if auto_chain else "answer"
    if regex_evidence.get("pine_or_code") and response_intent == "general_chat":
        response_intent = "artifact_generation"
        confidence = max(confidence, 0.75)
    return ChatIntentDecision(
        response_intent=response_intent,
        action=action,
        model_stage=_model_stage_for_intent(response_intent),
        confidence=confidence,
        source=source,
        auto_chain=auto_chain,
        current_context_required=bool(regex_evidence.get("current_info")),
        used_signals=tuple(key for key, value in regex_evidence.items() if value),
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
    normalized = " ".join((message_content or "").lower().split())
    return {
        "explicit_backtest": _explicit_backtest_signal(normalized),
        "preview_intent": _preview_intent_signal(normalized),
        "pine_or_code": _pine_or_code_signal(normalized),
        "current_info": _should_enable_web_search_auto(normalized),
        "artifact_or_strategy": _is_artifact_generation_request(normalized) or _is_strategy_building_request(normalized),
        "risky_url_action": _risky_url_action_signal(normalized),
    }


def _explicit_backtest_signal(normalized: str) -> bool:
    return bool(
        re.search(
            r"\b(backtest|run\s+(?:a\s+)?preview|test(?:\s+(?:the\s+)?strategy)?|compare(?:\s+variants?)?)\b"
            r"|chạy\s+backtest|kiểm\s*thử|test\s+(?:chiến\s*lược|strategy)|so\s+sánh",
            normalized,
            flags=re.IGNORECASE,
        )
    )


def _preview_intent_signal(normalized: str) -> bool:
    terms = (
        "simulate",
        "paper test",
        "preview performance",
        "preview evidence",
        "run thử",
        "chạy thử",
        "thử hiệu quả",
        "xem chiến lược này ổn không",
        "chay thu",
        "thu hieu qua",
    )
    return any(term in normalized for term in terms)


def _pine_or_code_signal(normalized: str) -> bool:
    return bool(
        re.search(
            r"\b(pine|pinescript|strategy\.entry|strategy\.exit|script|code|indicator|mql5|expert advisor)\b"
            r"|viết\s+code|sinh\s+code|tạo\s+code",
            normalized,
            flags=re.IGNORECASE,
        )
    )


def _risky_url_action_signal(normalized: str) -> bool:
    return bool(
        re.search(
            r"\b(run|execute|call|use|fetch|open|read|send|submit|request|connect|download|upload|curl|wget|shell|filesystem|network\s+request)\b",
            normalized,
            flags=re.IGNORECASE,
        )
    )


def _model_stage_for_intent(response_intent: str | None) -> str:
    if response_intent in {"artifact_generation", "backtest_preview", "pine_generation", "strategy_building"}:
        return MODEL_STAGE_PINE_CODE_GENERATION
    if response_intent in {"market_research", "docs_research"}:
        return MODEL_STAGE_BALANCED_REVIEW
    return DEFAULT_MODEL_STAGE


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
    if action_plan.tool_id not in {"query_backtest_trades", "get_backtest_summary", "build_robustness_report", "get_bot_status", "list_bots", "list_bot_events"}:
        return None
    available_tools = available_registry_tool_ids(
        artifact_kinds=artifact_kinds,
        context_text=context_text,
        web_search=web_search,
    )
    if action_plan.tool_id not in available_tools:
        return None
    arguments = dict(action_plan.arguments or {})
    if action_plan.tool_id in {"query_backtest_trades", "get_backtest_summary", "build_robustness_report"}:
        arguments.setdefault("run_id", "latest_completed_backtest")
    if action_plan.tool_id == "query_backtest_trades":
        bucket = arguments.get("bucket")
        if bucket is not None and bucket not in {"sample", "top_loser", "top_winner"}:
            arguments.pop("bucket", None)
        arguments["limit"] = _requested_tool_output_limit(arguments.get("limit"), default=20, maximum=50)
    return action_plan.tool_id, arguments


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
    if _looks_like_strategy_context(prior_context_text):
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


def _looks_like_strategy_context(text: str) -> bool:
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
) -> list[dict[str, Any]]:
    mode = _normalize_web_search(web_search)
    intent_needs_web_search = current_context_required or response_intent in {
        "docs_research",
        "market_research",
        "market_snapshot",
    }
    if intent_needs_web_search and mode != "off":
        return [{"type": "web_search"}]

    tools = provider_tools()
    if mode == "on" or (mode == "auto" and _should_enable_web_search_auto(message_content)):
        tools.append({"type": "web_search"})
    return tools


def _has_web_search_tool(tools: list[dict[str, Any]]) -> bool:
    return any(tool.get("type") == "web_search" for tool in tools)


def _should_enable_web_search_auto(message_content: str) -> bool:
    normalized = message_content.lower()
    explicit_terms = (
        "latest",
        "recent",
        "research",
        "sources",
        "citation",
        "citations",
        "cite",
        "docs",
        "documentation",
        "provider",
        "pricing",
        "news",
        "release",
        "version",
        "web",
        "search",
        "mới nhất",
        "gần đây",
        "nghiên cứu",
        "tìm kiếm",
        "tài liệu",
        "nguồn",
        "tin tức",
    )
    if any(term in normalized for term in explicit_terms):
        return True

    qualified_patterns = (
        r"\b(current|today(?:'s)?|now|real[- ]?time|up[- ]?to[- ]?date)\s+(price|prices|market data|docs?|documentation|provider|providers|model|models|pricing|release|version)s?\b",
        r"\b(current|today(?:'s)?|now|real[- ]?time|up[- ]?to[- ]?date)\b.{0,32}\b(price|prices|market data|docs?|documentation|provider|providers|model|models|pricing|release|version)s?\b",
        r"\b(price|prices|market data|docs?|documentation|provider|providers|model|models|pricing|release|version)s?\s+(today|now|currently|current)\b",
        r"(giá|market data|provider|model|phiên bản|release).{0,32}(hiện tại|hôm nay|bây giờ)",
        r"(hiện tại|hôm nay|bây giờ).{0,32}(giá|market data|provider|model|phiên bản|release)",
    )
    return any(re.search(pattern, normalized) for pattern in qualified_patterns)
