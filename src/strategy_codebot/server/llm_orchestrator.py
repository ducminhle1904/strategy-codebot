from collections.abc import Iterator
from dataclasses import dataclass, field
import json
import re
from typing import Any

from strategy_codebot.server.artifact_store import LocalArtifactStore
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.conversation_context import ConversationContextBuilder
from strategy_codebot.server.llm_clients import LLMClient, LLMClientEvent, ResponsesClient
from strategy_codebot.server.llm_clients import LLM_EVENT_MESSAGE_DELTA
from strategy_codebot.server.llm_clients import LLM_EVENT_SOURCES
from strategy_codebot.server.llm_clients import LLM_EVENT_TOOL_CALL
from strategy_codebot.server.llm_clients import LLM_EVENT_USAGE
from strategy_codebot.server.llm_tools import ToolExecutionContext
from strategy_codebot.server.llm_tools import TOOL_DEFINITIONS
from strategy_codebot.server.llm_tools import compact_tool_output
from strategy_codebot.server.llm_tools import execute_tool
from strategy_codebot.server.llm_tools import provider_tools
from strategy_codebot.server.llm_tools import validate_tool_arguments
from strategy_codebot.server.market_data import MarketDataGateway
from strategy_codebot.server.market_data import market_data_context
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
from strategy_codebot.server.provider_errors import log_provider_exception
from strategy_codebot.server.provider_errors import provider_run_failed_payload
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

SAFE_REASONING_EVENT = "model.reasoning.delta"
SUGGESTIONS_EVENT = "chat.suggestions.updated"
RESPONSE_INTENTS = {
    "artifact_generation",
    "capability_help",
    "docs_research",
    "general_chat",
    "market_research",
    "market_snapshot",
    "strategy_building",
}
SUGGESTION_SLOTS = {"entry", "exit", "market", "risk"}
RESPONSE_INTENT_FALLBACK_CONFIDENCE = 0.35
RESPONSE_INTENT_LLM_MIN_CONFIDENCE = 0.6
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


class ResponseIntentClassifier:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def classify(self, message_content: str, *, web_search: str = "auto") -> IntentClassification:
        deterministic = _deterministic_response_intent(message_content, web_search=web_search)
        if deterministic is not None:
            return deterministic
        if not _has_intent_classifier_signal(message_content):
            return IntentClassification("general_chat", 0.75, "deterministic")
        return self._classify_with_llm(message_content)

    def _classify_with_llm(self, message_content: str) -> IntentClassification:
        try:
            chunks: list[str] = []
            for event in self.client.stream(
                messages=[
                    {"role": "system", "content": _intent_classifier_system_prompt()},
                    {"role": "user", "content": message_content[:2000]},
                ],
                tools=[],
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
            for event in self.client.stream(
                messages=[
                    {"role": "system", "content": _title_system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                tools=[],
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
        web_search: str = "auto",
    ) -> Iterator[str]:
        language = _normalize_language(language)
        web_search = _normalize_web_search(web_search)
        run = self.repository.create_run(auth, conversation_id, status="running", request_id=request_id)
        if run is None:
            return
        budget = self._new_budget()
        accumulated_text: list[str] = []
        terminal_status: str | None = None
        context_builder = ConversationContextBuilder(self.repository)
        response_classification = ResponseIntentClassifier(self.client).classify(
            message_content,
            web_search=web_search,
        )
        response_intent = response_classification.intent
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
        system_prompt = _system_prompt(language, web_search=web_search)
        if market_context is not None:
            system_prompt = f"{system_prompt}\n\n<market_data>\n{market_context}\n</market_data>"
        conversation_context = context_builder.build(
            auth=auth,
            conversation_id=conversation_id,
            current_message_id=current_message_id,
            current_user_message=message_content,
            system_prompt=system_prompt,
        )
        artifact_available = response_intent in {
            "artifact_generation",
            "strategy_building",
        } and _conversation_has_user_artifact(self.repository, auth, conversation_id, current_run_id=run.id)
        suggestions_payload = _suggestions_payload(
            response_intent=response_intent,
            message_content=message_content,
            context_text=conversation_context.prior_context_text,
            language=language,
            artifact_available=artifact_available,
        )
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
            },
        )
        try:
            yield self._append_frame(
                auth,
                run,
                "chat.response_intent",
                response_classification.payload(),
            )
            if market_snapshot is not None:
                response_state["market_snapshot_emitted"] = True
                response_state["market_data_emitted"] = True
                response_state["market_snapshot_sources"] = [
                    source.to_payload()
                    for source in (
                        (market_snapshot.quote.source,) if market_snapshot.quote.source is not None else ()
                    )
                ]
                yield self._append_frame(auth, run, "market_data.snapshot", {"provider": market_snapshot.quote.provider})
                yield self._append_frame(
                    auth,
                    run,
                    "chat.market_snapshot",
                    market_snapshot.to_chat_payload(),
                )
            yield self._append_frame(auth, run, SUGGESTIONS_EVENT, suggestions_payload)
            yield self._safe_reasoning_frame(auth, run, "context", language)
            context_guard_message = _missing_current_context_message(
                message_content,
                conversation_context.prior_context_text,
                language,
            )
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

            model_timer = StageTimer()
            self.security_controls.check_model_call(auth, model=self.client.model)
            append_stage_started_event(self.repository, auth, run, "model")
            active_tools = (
                []
                if market_snapshot is not None
                else _provider_tools_for_web_search(
                    web_search,
                    message_content,
                    response_intent=response_intent,
                )
            )
            yield self._append_frame(
                auth,
                run,
                "provider.started",
                {
                    "mode": "agent",
                    "model": self.client.model,
                    "tier": auth.user_tier,
                    "web_search": web_search,
                    "web_search_enabled": _has_web_search_tool(active_tools),
                },
            )
            yield self._safe_reasoning_frame(auth, run, "model", language)
            for event in self.client.stream(messages=conversation_context.messages, tools=active_tools):
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
                    language=language,
                )
                if budget.blocked:
                    break
            append_stage_event(self.repository, auth, run, "model", model_timer.elapsed_ms())
            if accumulated_text and not budget.blocked:
                final_text = "".join(accumulated_text)
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
                tool_only_text = _tool_only_success_message(budget.completed_tool_ids, language)
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
            failure_payload = provider_run_failed_payload(exc)
            failure_text = _failure_assistant_message(failure_payload, language)
            self.repository.create_message(auth, conversation_id, failure_text, role="assistant")
            log_provider_exception(exc, run_id=run.id, trace_id=run.trace_id)
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
            for event in self.client.stream(messages=summary_messages, tools=[]):
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
    ) -> RunnerIntegrationResult | None:
        run = self.repository.create_run(auth, conversation_id, status="running", request_id=request_id)
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
            for event in self.client.stream(messages=messages, tools=provider_tools()):
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
            log_provider_exception(exc, run_id=run.id, trace_id=run.trace_id)
            self.repository.append_run_event(
                auth,
                run.id,
                "run.failed",
                provider_run_failed_payload(exc),
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
        language: str = "en",
    ) -> Iterator[str]:
        if event.type == LLM_EVENT_MESSAGE_DELTA and event.text:
            redacted_text = redact_text(event.text)
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
                _usage_payload(event.model or self.client.model, event.input_tokens, event.output_tokens),
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
                language=language,
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
        language: str = "en",
    ) -> Iterator[str]:
        block = self._gate_tool(auth, run, tool_name, arguments, budget)
        if block is not None:
            budget.blocked = True
            yield from self._policy_blocked(auth, run, tool_name, block, language=language)
            return

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
            append_stage_event(self.repository, auth, run, "tool", tool_timer.elapsed_ms())
        except Exception as exc:
            if tool_call is not None:
                self.repository.complete_tool_call(
                    auth,
                    tool_call.id,
                    status="failed",
                    output_json={"error": exc.__class__.__name__, "message": redact_text(str(exc))},
                )
            error_payload = {
                "tool_id": tool_name,
                "label": _tool_activity_label(tool_name, language),
                "status": "failed",
                "error": exc.__class__.__name__,
                "message": redact_text(str(exc)),
                "output_summary": f"Tool failed: {exc.__class__.__name__}",
            }
            yield self._append_frame(auth, run, "tool.completed", error_payload)
            append_stage_event(self.repository, auth, run, "tool", tool_timer.elapsed_ms(), status="failed")
            raise

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
        event = self.repository.append_run_event(auth, run.id, event_type, redact_value(payload))
        if event is None:
            raise RuntimeError(f"Unable to append run event {event_type}")
        return sse_frame(event)

    def _safe_reasoning_frame(self, auth: AuthContext, run: AssistantRunRecord, phase: str, language: str = "en") -> str:
        return sse_frame(transient_reasoning_event(run, payload=redact_value(_safe_reasoning_payload(phase, language))))

    def _new_budget(self) -> RunBudget:
        return RunBudget(
            max_tool_calls=min(self.max_tool_calls, self.budget_config.max_tool_calls),
            max_total_tokens=self.budget_config.max_total_tokens,
            max_output_tokens=self.budget_config.max_output_tokens,
        )

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


def _usage_payload(model: str, input_tokens: int, output_tokens: int) -> dict[str, Any]:
    return {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


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
        "create_pinets_preview_plan",
        "create_signals_market_context_plan",
        "create_graph_pipeline_plan",
        "create_sidekick_export_plan",
    }:
        return "artifact"
    return "tool"


def _normalize_language(language: str | None) -> str:
    return "vi" if language == "vi" else "en"


def _normalize_web_search(web_search: str | None) -> str:
    return web_search if web_search in {"off", "auto", "on"} else "auto"


def _suggestions_payload(
    *,
    response_intent: str,
    message_content: str,
    context_text: str,
    language: str,
    artifact_available: bool = False,
) -> dict[str, Any]:
    language = _normalize_language(language)
    combined_context = f"{context_text}\n{message_content}".lower()
    missing_fields = _strategy_missing_fields(combined_context)
    readiness = "ready_for_artifact" if not missing_fields else "needs_detail"
    actions: list[dict[str, Any]] = []
    composer_blocks = (
        _composer_block_suggestions(language, missing_fields)
        if response_intent in {"strategy_building", "artifact_generation"}
        else []
    )

    if response_intent == "market_snapshot":
        symbol = _market_symbol_from_text(message_content) or "ETH"
        actions.extend(
            [
                _suggestion_action(
                    "compare-market",
                    _copy(language, "So sánh với BTC", "Compare with BTC"),
                    _copy(
                        language,
                        f"So sánh bối cảnh hiện tại của {symbol} với BTC.",
                        f"Compare the current {symbol} context with BTC.",
                    ),
                    "market",
                    priority=1,
                ),
                _suggestion_action(
                    "use-market-for-strategy",
                    _copy(language, "Dùng cho strategy", "Use for strategy"),
                    _copy(
                        language,
                        f"Dùng {symbol} làm market context và giúp mình xây strategy review-only.",
                        f"Use {symbol} as the market context and help me build a review-only strategy.",
                    ),
                    "strategy",
                    priority=2,
                ),
            ]
        )
    elif response_intent == "docs_research":
        actions.append(
            _suggestion_action(
                "summarize-docs",
                _copy(language, "Tóm tắt điểm cần dùng", "Summarize what matters"),
                _copy(
                    language,
                    "Tóm tắt các điểm quan trọng nhất và cách áp dụng vào workflow strategy hiện tại.",
                    "Summarize the most important points and how they apply to the current strategy workflow.",
                ),
                "review",
                priority=1,
            )
        )
    elif artifact_available:
        actions.extend(
            [
                _artifact_action(
                    "view-artifact",
                    _copy(language, "Mở artifact", "View artifact"),
                    priority=1,
                ),
                _suggestion_action(
                    "review-code",
                    _copy(language, "Review code", "Review code"),
                    _copy(
                        language,
                        "Review artifact hiện tại và chỉ ra giả định, rủi ro, và điểm cần tự validation.",
                        "Review the current artifact and list assumptions, risks, and manual validation points.",
                    ),
                    "review",
                    priority=2,
                ),
            ]
        )
    elif response_intent in {"strategy_building", "artifact_generation"}:
        if missing_fields:
            for field in missing_fields[:2]:
                actions.append(_missing_field_action(field, language, priority=len(actions) + 1))
            actions.append(
                _suggestion_action(
                    "review-assumptions",
                    _copy(language, "Review giả định", "Review assumptions"),
                    _copy(
                        language,
                        "Liệt kê các giả định còn thiếu hoặc chưa rõ trong strategy context hiện tại.",
                        "List missing or unclear assumptions in the current strategy context.",
                    ),
                    "review",
                    priority=3,
                )
            )
        else:
            actions.extend(
                [
                    _suggestion_action(
                        "generate-pine-v6",
                        _copy(language, "Tạo Pine v6", "Generate Pine v6"),
                        _copy(
                            language,
                            "Tạo artifact Pine v6 review-only từ strategy context hiện tại.",
                            "Generate a review-only Pine v6 artifact from the current strategy context.",
                        ),
                        "code",
                        priority=1,
                    ),
                    _suggestion_action(
                        "review-risk",
                        _copy(language, "Review risk", "Review risk"),
                        _copy(
                            language,
                            "Review risk rules và đề xuất phiên bản an toàn hơn nếu cần.",
                            "Review the risk rules and suggest a safer version if needed.",
                        ),
                        "risk",
                        priority=2,
                    ),
                    _suggestion_action(
                        "conservative-version",
                        _copy(language, "Tạo bản conservative", "Create safer version"),
                        _copy(
                            language,
                            "Tạo phiên bản conservative hơn với filter và risk controls chặt hơn.",
                            "Create a more conservative version with stricter filters and risk controls.",
                        ),
                        "strategy",
                        priority=3,
                    ),
                ]
            )
    elif response_intent == "capability_help":
        actions.append(
            _suggestion_action(
                "show-strategy-format",
                _copy(language, "Cho mình mẫu spec", "Show a spec example"),
                _copy(
                    language,
                    "Cho mình một mẫu strategy spec ngắn để bắt đầu.",
                    "Show me a short strategy spec example to get started.",
                ),
                "strategy",
                priority=1,
            )
        )

    return {
        "actions": sorted(actions, key=lambda item: item["priority"])[:3],
        "composer_blocks": composer_blocks,
        "context": {
            "artifact_available": artifact_available,
            "intent": response_intent,
            "missing_fields": missing_fields,
            "readiness": readiness,
        },
        "safe": True,
        "version": 1,
    }


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
    return payload


def _artifact_action(suggestion_id: str, label: str, *, priority: int) -> dict[str, Any]:
    return {
        "action": "open_artifact",
        "category": "code",
        "enabled": True,
        "id": suggestion_id,
        "kind": "artifact_action",
        "label": label,
        "priority": priority,
    }


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


def _conversation_has_user_artifact(
    repository: ConversationRepository,
    auth: AuthContext,
    conversation_id: str,
    *,
    current_run_id: str,
) -> bool:
    runs = repository.list_runs(auth, conversation_id) or []
    for run in runs[:5]:
        if run.id == current_run_id:
            continue
        artifacts = repository.list_artifacts(auth, run.id) or []
        if any(_artifact_is_user_visible(artifact) for artifact in artifacts):
            return True
    return False


def _artifact_is_user_visible(artifact: Any) -> bool:
    visibility = getattr(artifact, "visibility", None)
    if visibility is not None:
        return visibility != "internal"
    kind = str(getattr(artifact, "kind", "")).lower()
    return not any(term in kind for term in ("trace", "observability", "internal"))


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
    return any(term in normalized for term in research_terms) and any(term in normalized for term in market_terms)


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


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        decoded = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


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


def _tool_only_success_message(tool_ids: list[str], language: str = "en") -> str:
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
    if "generate_pine" in tool_ids:
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
            return "Đã queue variant lab cho Backtest Kit local preview. Hãy theo dõi từng child run và so sánh report khi tất cả hoàn tất."
        return "Queued a Backtest Kit local preview variant lab. Track each child run and compare reports after all complete."
    if "run_backtest_preview" in tool_ids:
        if is_vi:
            return "Đã queue Backtest Kit local preview run. Report sẽ là evidence artifact review-only khi worker hoàn tất."
        return "Queued a Backtest Kit local preview run. The worker will persist review-only evidence artifacts when it completes."
    if "create_pinets_preview_plan" in tool_ids:
        if is_vi:
            return "Đã tạo PineTS local preview plan. Đây không phải TradingView validation."
        return "Created a PineTS local preview plan. This is not TradingView validation."
    if "create_signals_market_context_plan" in tool_ids:
        if is_vi:
            return "Đã tạo Backtest Kit signals market-context plan cho LLM context."
        return "Created a Backtest Kit signals market-context plan for LLM context."
    if "create_graph_pipeline_plan" in tool_ids:
        if is_vi:
            return "Đã tạo Backtest Kit graph pipeline plan cho multi-timeframe/variant composition."
        return "Created a Backtest Kit graph pipeline plan for multi-timeframe and variant composition."
    if "create_sidekick_export_plan" in tool_ids:
        if is_vi:
            return "Đã tạo Sidekick export/scaffold plan. Sidekick không chạy trong API runtime."
        return "Created a Sidekick export/scaffold plan. Sidekick does not run in the API runtime."
    if "create_backtest_plan" in tool_ids:
        if is_vi:
            return "Đã tạo backtest plan review-only cho Backtest Kit local preview. Hãy review config trước khi queue run."
        return "Created a review-only Backtest Kit local preview plan. Review the config before queueing a run."
    return "Tool run đã hoàn tất. Hãy review kết quả phía trên trước khi tiếp tục." if is_vi else "The tool run completed successfully. Review the result above before continuing."


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
            "create_pinets_preview_plan": "Tạo PineTS preview plan",
            "create_signals_market_context_plan": "Tạo market context plan",
            "create_graph_pipeline_plan": "Tạo graph pipeline plan",
            "create_sidekick_export_plan": "Tạo Sidekick export plan",
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
            "create_pinets_preview_plan": "Create PineTS preview plan",
            "create_signals_market_context_plan": "Create market context plan",
            "create_graph_pipeline_plan": "Create graph pipeline plan",
            "create_sidekick_export_plan": "Create Sidekick export plan",
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
        return "Đã tạo backtest plan review-only cho Backtest Kit local preview." if is_vi else "Created a review-only Backtest Kit local preview plan."
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
    if tool_name == "create_pinets_preview_plan":
        return "Đã tạo PineTS local preview plan; đây không phải TradingView validation." if is_vi else "Created a PineTS local preview plan; this is not TradingView validation."
    if tool_name == "create_signals_market_context_plan":
        return "Đã tạo market-context plan dùng @backtest-kit/signals, model routing vẫn thuộc strategy-codebot." if is_vi else "Created a market-context plan using @backtest-kit/signals; model routing remains in strategy-codebot."
    if tool_name == "create_graph_pipeline_plan":
        return "Đã tạo graph pipeline plan cho multi-timeframe/variant composition." if is_vi else "Created a graph pipeline plan for multi-timeframe and variant composition."
    if tool_name == "create_sidekick_export_plan":
        return "Đã tạo Sidekick export plan; Sidekick không chạy trong API runtime." if is_vi else "Created a Sidekick export plan; Sidekick does not run in the API runtime."
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


def _system_prompt(language: str = "en", *, web_search: str = "auto") -> str:
    language_instruction = (
        "Respond in Vietnamese for user-facing chat text. Keep code, Pine syntax, JSON schema keys, tool ids, artifact filenames, and policy/event codes unchanged."
        if _normalize_language(language) == "vi"
        else "Respond in English for user-facing chat text unless the user explicitly asks otherwise. Keep code, Pine syntax, JSON schema keys, tool ids, artifact filenames, and policy/event codes unchanged."
    )
    web_search_instruction = _web_search_instruction(web_search)
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
- Do not claim profitability, compile success, runtime success, or backtest success without evidence.
- Keep every generated artifact and recommendation review-only.
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
- For Backtest Kit requests, use create_backtest_plan before queueing when config assumptions are not explicit. Use create_pinets_preview_plan for Pine local preview, create_signals_market_context_plan for LLM-ready market context, create_graph_pipeline_plan for multi-timeframe/variant composition, and create_sidekick_export_plan only for export/scaffold guidance. Treat run_backtest_preview and run_backtest_variant_lab outputs as queued local preview jobs only; never claim backtest success until report artifacts exist.
</domain_shape>"""


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
) -> list[dict[str, Any]]:
    mode = _normalize_web_search(web_search)
    intent_needs_web_search = response_intent in {"docs_research", "market_research", "market_snapshot"}
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
