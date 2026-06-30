from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Literal

from strategy_codebot.harness_types import (
    FAILURE_POLICY_VIOLATION,
    FAILURE_PROVIDER_ERROR,
    FAILURE_SCHEMA_INVALID,
    FAILURE_UNKNOWN,
    STATUS_BLOCKED,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_STARTED,
)
from strategy_codebot.paths import repo_root
from strategy_codebot.server.llm_clients import LLMClient, LLMClientEvent
from strategy_codebot.server.llm_clients import LLM_EVENT_MESSAGE_DELTA
from strategy_codebot.server.llm_clients import LLM_EVENT_TOOL_CALL
from strategy_codebot.server.llm_clients import LLM_EVENT_USAGE
from strategy_codebot.server.llm_clients import stream_client
from strategy_codebot.server.llm_tools import ToolExecutionContext
from strategy_codebot.server.llm_tools import compact_tool_output
from strategy_codebot.server.llm_tools import execute_tool
from strategy_codebot.server.llm_tools import read_risk_provider_tools
from strategy_codebot.server.llm_tools import validate_tool_arguments
from strategy_codebot.server.policy import evaluate_agent_loop_tool_policy
from strategy_codebot.tool_runtime import POLICY_ENFORCE
from strategy_codebot.tool_runtime import ToolBlockedError
from strategy_codebot.tool_runtime import ToolHarness
from strategy_codebot.tool_runtime import load_tool_registry

AgentLoopStatus = Literal["completed", "partial", "blocked"]
ToolCallStatus = Literal["completed", "blocked", "failed"]
BOUNDED_AGENT_LOOP_WORKFLOW = "bounded_agent_loop"
BOUNDED_SCOUT_STAGE = "bounded_scout"
BOUNDED_SCOUT_ROLE = "bounded_scout_runner"


@dataclass(frozen=True)
class AgentLoopBudget:
    max_iterations: int = 4
    max_tool_calls: int = 4
    max_tokens: int = 8_000
    max_runtime_seconds: float = 30.0


@dataclass(frozen=True)
class AgentToolResult:
    tool_name: str
    status: ToolCallStatus
    output: dict[str, Any] | None = None
    error: str | None = None


@dataclass(frozen=True)
class AgentLoopResult:
    status: AgentLoopStatus
    response_text: str
    tool_results: tuple[AgentToolResult, ...]
    events: tuple[dict[str, Any], ...]
    iterations: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    blocked_reason: str | None = None
    budget_exhausted: str | None = None


class AgentLoopRunner:
    def __init__(
        self,
        *,
        llm_client: LLMClient,
        tool_context: ToolExecutionContext,
        run_id: str,
        budget: AgentLoopBudget | None = None,
        registry: dict[str, Any] | None = None,
        harness: ToolHarness | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.tool_context = tool_context
        self.run_id = run_id
        self.budget = budget or AgentLoopBudget()
        self.registry = registry or load_tool_registry(repo_root() / "configs" / "tool-registry.yaml")
        self.harness = harness or ToolHarness(run_id=run_id, policy_mode=POLICY_ENFORCE, registry=self.registry)

    def provider_tools(self) -> list[dict[str, Any]]:
        return read_risk_provider_tools(self.registry)

    def run(
        self,
        messages: list[dict[str, str]],
        *,
        routing_context: dict[str, Any] | None = None,
    ) -> AgentLoopResult:
        started_at = time.monotonic()
        event_start_index = len(self.harness.events)
        conversation = [dict(message) for message in messages]
        response_chunks: list[str] = []
        tool_results: list[AgentToolResult] = []
        input_tokens = 0
        output_tokens = 0
        tool_calls = 0
        iterations = 0

        self._record_event_pair(
            "agent_loop.started",
            "agent.started",
            primary_fields={"iteration": 0, "tool_call_count": 0},
            status=STATUS_STARTED,
        )

        def finish(
            status: AgentLoopStatus,
            *,
            blocked_reason: str | None = None,
            budget_exhausted: str | None = None,
        ) -> AgentLoopResult:
            terminal_status = STATUS_PASS if status == "completed" else STATUS_BLOCKED
            self._record_event_pair(
                "agent_loop.completed",
                "agent.completed",
                primary_fields={
                    "iteration": iterations,
                    "tool_call_count": tool_calls,
                    "budget_exhausted": budget_exhausted,
                },
                status=terminal_status,
                failure_class=FAILURE_UNKNOWN if status != "completed" else None,
                output_summary=blocked_reason or budget_exhausted,
            )
            return AgentLoopResult(
                status=status,
                response_text="".join(response_chunks),
                tool_results=tuple(tool_results),
                events=tuple(dict(event) for event in self.harness.events[event_start_index:]),
                iterations=iterations,
                tool_calls=tool_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                blocked_reason=blocked_reason,
                budget_exhausted=budget_exhausted,
            )

        initial_budget_reason = self._budget_exhausted(started_at, input_tokens, output_tokens)
        if initial_budget_reason:
            self._record_budget_block(initial_budget_reason)
            return finish("partial", budget_exhausted=initial_budget_reason)

        provider_tools = self.provider_tools()
        for attempt in range(1, max(self.budget.max_iterations, 0) + 1):
            iterations = attempt
            self.harness.record_event(
                "llm.started",
                workflow="bounded_agent_loop",
                stage="bounded_scout",
                model=self.llm_client.model,
                attempt=attempt,
                status=STATUS_STARTED,
            )
            saw_tool_call = False
            try:
                stream = stream_client(
                    self.llm_client,
                    messages=conversation,
                    tools=provider_tools,
                    routing_context=routing_context,
                )
                for event in stream:
                    if event.type == LLM_EVENT_USAGE:
                        input_tokens += event.input_tokens
                        output_tokens += event.output_tokens
                        budget_reason = self._budget_exhausted(started_at, input_tokens, output_tokens)
                        if budget_reason:
                            self._record_budget_block(budget_reason)
                            self._record_llm_completed(attempt, STATUS_BLOCKED, input_tokens, output_tokens)
                            return finish("partial", budget_exhausted=budget_reason)
                        continue

                    if event.type == LLM_EVENT_MESSAGE_DELTA and event.text:
                        response_chunks.append(event.text)
                        continue

                    if event.type != LLM_EVENT_TOOL_CALL:
                        continue

                    saw_tool_call = True
                    if tool_calls >= self.budget.max_tool_calls:
                        budget_reason = f"max_tool_calls budget exhausted ({self.budget.max_tool_calls})"
                        self._record_budget_block(budget_reason)
                        self._record_llm_completed(attempt, STATUS_BLOCKED, input_tokens, output_tokens)
                        return finish("blocked", blocked_reason=budget_reason, budget_exhausted=budget_reason)

                    tool_calls += 1
                    tool_result = self._execute_tool_event(
                        event,
                        iteration=attempt,
                        tool_call_count=tool_calls,
                    )
                    tool_results.append(tool_result)
                    if tool_result.status == "blocked":
                        self._record_llm_completed(attempt, STATUS_BLOCKED, input_tokens, output_tokens)
                        return finish("blocked", blocked_reason=tool_result.error)
                    if tool_result.status == "failed":
                        self._record_llm_completed(attempt, STATUS_FAIL, input_tokens, output_tokens)
                        return finish("partial", blocked_reason=tool_result.error)
                    if tool_result.output is not None:
                        conversation.append(
                            {
                                "role": "assistant",
                                "content": f"Tool {tool_result.tool_name} returned: "
                                f"{json.dumps(tool_result.output, ensure_ascii=False)}",
                            }
                        )
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                self._record_llm_completed(
                    attempt,
                    STATUS_FAIL,
                    input_tokens,
                    output_tokens,
                    failure_class=FAILURE_PROVIDER_ERROR,
                    error={"type": type(exc).__name__, "message": message},
                )
                return finish("partial", blocked_reason=message)

            self._record_llm_completed(attempt, STATUS_PASS, input_tokens, output_tokens)
            if not saw_tool_call:
                return finish("completed")

            budget_reason = self._budget_exhausted(started_at, input_tokens, output_tokens)
            if budget_reason:
                self._record_budget_block(budget_reason)
                return finish("partial", budget_exhausted=budget_reason)

        budget_reason = f"max_iterations budget exhausted ({self.budget.max_iterations})"
        self._record_budget_block(budget_reason)
        return finish("partial", budget_exhausted=budget_reason)

    def _execute_tool_event(
        self,
        event: LLMClientEvent,
        *,
        iteration: int,
        tool_call_count: int,
    ) -> AgentToolResult:
        tool_name = event.tool_name or "unknown"
        arguments = event.arguments or {}
        contract = self.harness.contracts.get(tool_name)
        risk_tier = contract.risk_tier if contract is not None else "unknown"
        policy_decision = evaluate_agent_loop_tool_policy(tool_name, risk_tier)
        self._record_tool_checked(
            tool_name,
            risk_tier=risk_tier,
            gate="policy",
            decision="allowed" if policy_decision.allowed else "blocked",
            reason_code=policy_decision.blocked_finding.code if policy_decision.blocked_finding else None,
            iteration=iteration,
            tool_call_count=tool_call_count,
            budget_exhausted=False,
        )
        if not policy_decision.allowed:
            reason = policy_decision.blocked_finding.message if policy_decision.blocked_finding else "Tool is blocked."
            self.harness.record_blocked_tool(tool_name, reason, risk_tier=risk_tier)
            return AgentToolResult(tool_name=tool_name, status="blocked", error=reason)

        validation_error = validate_tool_arguments(tool_name, arguments)
        if validation_error:
            self._record_tool_checked(
                tool_name,
                risk_tier=risk_tier,
                gate="schema",
                decision="blocked",
                reason_code="schema_invalid",
                iteration=iteration,
                tool_call_count=tool_call_count,
                budget_exhausted=False,
            )
            self.harness.record_blocked_tool(
                tool_name,
                validation_error,
                risk_tier=risk_tier,
                failure_class=FAILURE_SCHEMA_INVALID,
            )
            return AgentToolResult(tool_name=tool_name, status="blocked", error=validation_error)

        try:
            output = self.harness.call(tool_name, lambda: execute_tool(tool_name, arguments, self.tool_context))
        except ToolBlockedError as exc:
            return AgentToolResult(tool_name=tool_name, status="blocked", error=str(exc))
        except Exception as exc:
            message = str(exc) or type(exc).__name__
            return AgentToolResult(tool_name=tool_name, status="failed", error=message)

        return AgentToolResult(tool_name=tool_name, status="completed", output=compact_tool_output(output))

    def _budget_exhausted(self, started_at: float, input_tokens: int, output_tokens: int) -> str | None:
        if self.budget.max_runtime_seconds >= 0 and time.monotonic() - started_at >= self.budget.max_runtime_seconds:
            return f"max_runtime_seconds budget exhausted ({self.budget.max_runtime_seconds})"
        if self.budget.max_tokens >= 0 and input_tokens + output_tokens >= self.budget.max_tokens:
            return f"max_tokens budget exhausted ({self.budget.max_tokens})"
        return None

    def _bounded_scout_payload(self, **fields: Any) -> dict[str, Any]:
        payload = {
            "workflow": BOUNDED_AGENT_LOOP_WORKFLOW,
            "stage": BOUNDED_SCOUT_STAGE,
            "agent_role": BOUNDED_SCOUT_ROLE,
            "model": self.llm_client.model,
        }
        payload.update(fields)
        return payload

    def _record_bounded_scout_event(self, event_type: str, **fields: Any) -> None:
        self.harness.record_event(event_type, **self._bounded_scout_payload(**fields))

    def _record_event_pair(
        self,
        primary_event_type: str,
        secondary_event_type: str,
        *,
        primary_fields: dict[str, Any] | None = None,
        secondary_fields: dict[str, Any] | None = None,
        **shared_fields: Any,
    ) -> None:
        self._record_bounded_scout_event(
            primary_event_type,
            **shared_fields,
            **(primary_fields or {}),
        )
        self._record_bounded_scout_event(
            secondary_event_type,
            **shared_fields,
            **(secondary_fields or {}),
        )

    def _record_budget_block(self, reason: str) -> None:
        self._record_bounded_scout_event(
            "guardrail.blocked",
            status=STATUS_BLOCKED,
            failure_class=FAILURE_UNKNOWN,
            label="agent_loop_budget",
            input_summary=reason,
        )

    def _record_llm_completed(
        self,
        attempt: int,
        status: str,
        input_tokens: int,
        output_tokens: int,
        *,
        failure_class: str | None = None,
        error: dict[str, str] | None = None,
    ) -> None:
        if failure_class is None and status == STATUS_BLOCKED:
            failure_class = FAILURE_POLICY_VIOLATION
        self._record_event_pair(
            "agent_loop.llm_completed",
            "llm.completed",
            primary_fields={"iteration": attempt},
            secondary_fields={"attempt": attempt},
            status=status,
            failure_class=failure_class,
            error=error,
            usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
        )

    def _record_tool_checked(
        self,
        tool_id: str,
        *,
        risk_tier: str,
        gate: str,
        decision: str,
        iteration: int,
        tool_call_count: int,
        reason_code: str | None = None,
        budget_exhausted: bool | str | None = None,
    ) -> None:
        self._record_bounded_scout_event(
            "agent_loop.tool_checked",
            tool_id=tool_id,
            risk_tier=risk_tier,
            gate=gate,
            decision=decision,
            reason_code=reason_code,
            iteration=iteration,
            tool_call_count=tool_call_count,
            budget_exhausted=budget_exhausted,
            status=STATUS_BLOCKED if decision == "blocked" else STATUS_PASS,
        )


class BoundedScoutRunner(AgentLoopRunner):
    pass
