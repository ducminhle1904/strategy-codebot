from __future__ import annotations

import contextlib
import io
import json
import os
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from jsonschema import ValidationError

from strategy_codebot.agent_harness import classify_failure
from strategy_codebot.harness_types import (
    FAILURE_MISSING_CREDENTIAL,
    FAILURE_POLICY_VIOLATION,
    FAILURE_PROVIDER_ERROR,
    FAILURE_REVIEW_FAILED,
    FAILURE_REVIEW_VALIDATION_DISAGREEMENT,
    FAILURE_STATIC_VALIDATION_FAILED,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_SKIPPED,
    STATUS_STARTED,
)
from strategy_codebot.knowledge_context import KNOWLEDGE_CONTEXT_AUTO, KNOWLEDGE_CONTEXT_MODES, build_knowledge_context, compact_knowledge_context, knowledge_metadata
from strategy_codebot.pine import validate_pine
from strategy_codebot.schemas import schema, validate_payload
from strategy_codebot.tool_runtime import POLICY_ENFORCE, POLICY_OBSERVE, find_policy_claims


LIVE_RESPONSE_SCHEMA_NAME = "strategy_codebot_live_generation"
WORKFLOW_MULTI_AGENT = "multi-agent"
WORKFLOW_SINGLE = "single"
WORKFLOWS = {WORKFLOW_MULTI_AGENT, WORKFLOW_SINGLE}
COST_PROFILE_QUALITY = "quality"
COST_PROFILE_CHEAP = "cheap"
COST_PROFILES = {COST_PROFILE_QUALITY, COST_PROFILE_CHEAP}
MAX_REPAIR_LOOPS = 2
LIVE_ERROR_PATH = "live-error.json"
LIVE_WORKFLOW_TRACE_PATH = "live-workflow-trace.json"

STAGE_STRATEGY_REASONING = "strategy_reasoning"
STAGE_STRATEGY_CODING = "strategy_coding"
STAGE_PINE_CODE_GENERATION = "pine_code_generation"
STAGE_BALANCED_REVIEW = "balanced_review"
STAGE_REPAIR = "repair"
WORKFLOW_STAGES = (STAGE_STRATEGY_REASONING, STAGE_STRATEGY_CODING, STAGE_PINE_CODE_GENERATION, STAGE_BALANCED_REVIEW)
MODEL_STAGE_KEYS = {*WORKFLOW_STAGES, STAGE_REPAIR}
CONSERVATIVE_POSITION_SIZING_GUIDANCE = (
    "Use 1-2% account equity risk per trade, fixed units, or another explicitly bounded small-risk position sizing model. "
    "Never invent 100% of available capital, all-capital, entire-account, or full-balance sizing."
)
FULL_CAPITAL_POSITION_SIZING_PHRASES = (
    "100% of available capital",
    "100 percent of available capital",
    "all available capital",
    "all capital",
    "entire account",
    "entire account balance",
    "full account",
    "full balance",
    "whole account",
    "100% account",
    "100% of account",
    "100% of equity",
    "100% equity",
)
QUALITY_STAGE_AGENTS = {
    STAGE_STRATEGY_REASONING: "trading_analyst",
    STAGE_STRATEGY_CODING: "orchestrator",
    STAGE_PINE_CODE_GENERATION: "pine_specialist",
    STAGE_BALANCED_REVIEW: "critic",
    STAGE_REPAIR: "pine_specialist",
}
AGENT_ROLE_REGISTRY = {
    STAGE_STRATEGY_REASONING: {
        "agent_role": "trading_analyst",
        "responsibility": "Analyze the prompt into a strategy brief with constraints, assumptions, and risk boundaries.",
        "required_context": ["original_prompt", "policy_boundaries", "schema_summary"],
        "allowed_outputs": ["summary", "constraints", "indicators", "entries", "exits", "risk_rules", "non_goals"],
        "handoff_contract": "Pass a policy-safe strategy brief to strategy_coding.",
        "failure_policy": "Retry/fallback on provider or malformed response; fail before later stages if unavailable.",
    },
    STAGE_STRATEGY_CODING: {
        "agent_role": "orchestrator",
        "responsibility": "Convert the strategy brief into a schema-valid strategy_spec.",
        "required_context": ["strategy_reasoning", "strategy-spec.schema.json"],
        "allowed_outputs": ["strategy_spec"],
        "handoff_contract": "Pass only schema-valid strategy_spec to pine_code_generation.",
        "failure_policy": "Fail on schema invalid output after retries/fallbacks.",
    },
    STAGE_PINE_CODE_GENERATION: {
        "agent_role": "pine_specialist",
        "responsibility": "Generate Pine Script v6 from strategy_spec without changing strategy logic.",
        "required_context": ["strategy_spec", "policy_boundaries"],
        "allowed_outputs": ["pine_code"],
        "handoff_contract": "Pass Pine code plus strategy_spec to static validation and balanced_review.",
        "failure_policy": "Fail or repair if Pine code is missing or static validation fails.",
    },
    STAGE_BALANCED_REVIEW: {
        "agent_role": "critic",
        "responsibility": "Review brief, spec, code, validation, and policy boundaries.",
        "required_context": ["strategy_spec", "pine_code", "validation", "policy_boundaries"],
        "allowed_outputs": ["verdict", "required_fixes", "rationale"],
        "handoff_contract": "Pass pass/fix/fail decision to final gate or repair loop.",
        "failure_policy": "Required fixes trigger repair up to the configured limit.",
    },
    STAGE_REPAIR: {
        "agent_role": "pine_specialist",
        "responsibility": "Repair only validation/review findings while preserving strategy intent.",
        "required_context": ["validation", "review", "strategy_spec", "pine_code"],
        "allowed_outputs": ["strategy_spec", "pine_code"],
        "handoff_contract": "Pass repaired artifacts back to validation and review.",
        "failure_policy": "Fail final gate if unresolved after max repair loops.",
    },
}
CHEAP_STAGE_MODELS = {
    STAGE_STRATEGY_REASONING: "strategy_reasoning",
    STAGE_STRATEGY_CODING: "strategy_coding",
    STAGE_PINE_CODE_GENERATION: "pine_code_generation",
    STAGE_BALANCED_REVIEW: "balanced_review",
    STAGE_REPAIR: "pine_code_generation",
}


class LiveError(RuntimeError):
    code = "live_error"

    def __init__(self, message: str, *, attempts: list[dict[str, Any]] | None = None, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.attempts = attempts or []
        self.diagnostics = diagnostics or {}


class LiveDependencyError(LiveError):
    code = "missing_live_dependency"


class LiveCredentialError(LiveError):
    code = "missing_provider_credential"


class LiveProviderError(LiveError):
    code = FAILURE_PROVIDER_ERROR


class LiveResponseError(LiveError):
    code = "malformed_provider_response"


class LiveResponseSchemaError(LiveResponseError):
    code = "schema_invalid_provider_response"


class LiveSafetyError(LiveError):
    code = "safety_policy_violation"


@dataclass
class LiveRunOptions:
    model_override: str | None = None
    model_stage_overrides: dict[str, str] = field(default_factory=dict)
    workflow: str = WORKFLOW_MULTI_AGENT
    cost_profile: str = COST_PROFILE_QUALITY
    save_raw_provider: bool = False
    knowledge_context: str = KNOWLEDGE_CONTEXT_AUTO

    def __post_init__(self) -> None:
        if self.workflow not in WORKFLOWS:
            raise ValueError("workflow must be multi-agent or single")
        if self.cost_profile not in COST_PROFILES:
            raise ValueError("cost_profile must be quality or cheap")
        if self.knowledge_context not in KNOWLEDGE_CONTEXT_MODES:
            raise ValueError("knowledge_context must be auto or off")
        validate_model_stage_overrides(self.model_stage_overrides)
        if self.workflow == WORKFLOW_MULTI_AGENT and self.model_override:
            raise ValueError("--model is only supported with --workflow single; use --model-stage for multi-agent runs")


@dataclass(frozen=True)
class ProviderRoute:
    provider: str
    credential_env: str | None = None

    def completion_kwargs(self) -> dict[str, Any]:
        if self.provider == "openrouter" and os.getenv("OPENROUTER_API_BASE"):
            return {"base_url": os.getenv("OPENROUTER_API_BASE")}
        return {}


@dataclass
class ProviderCallResult:
    payload: dict[str, Any]
    raw_response: dict[str, Any]
    usage: dict[str, Any]
    model: str
    provider: str
    latency_ms: int
    policy_findings: list[dict[str, str]] = field(default_factory=list)
    provider_warnings: list[str] = field(default_factory=list)


@dataclass
class LiveGenerationResult:
    strategy_spec: dict[str, Any]
    pine_code: str
    model: str
    provider: str
    latency_ms: int
    attempts: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    raw_response: dict[str, Any] = field(default_factory=dict)
    workflow: str = WORKFLOW_SINGLE
    stages: list[dict[str, Any]] = field(default_factory=list)
    workflow_trace: dict[str, Any] = field(default_factory=dict)
    repair_count: int = 0
    policy_findings: list[dict[str, str]] = field(default_factory=list)
    generation_gate: dict[str, Any] = field(default_factory=dict)
    production_gate: dict[str, Any] = field(default_factory=dict)
    quality_report: dict[str, Any] = field(default_factory=dict)
    knowledge_context: dict[str, Any] = field(default_factory=dict)

    def metadata(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow,
            "provider": self.provider,
            "model": self.model,
            "final_model": self.model,
            "latency_ms": self.latency_ms,
            "total_latency_ms": self.latency_ms,
            "usage": self.usage,
            "total_usage": self.usage,
            "attempts": self.attempts,
            "stages": self.stages,
            "repair_count": self.repair_count,
            "policy_findings": self.policy_findings,
            "generation_gate": self.generation_gate,
            "production_gate": self.production_gate,
            "quality_report": self.quality_report,
            "quality_status": self.quality_report.get("status") if self.quality_report else None,
            "quality_score": self.quality_report.get("score") if self.quality_report else None,
            **knowledge_metadata(self.knowledge_context),
        }


@dataclass
class StageRunContext:
    litellm: Any
    registry: dict[str, Any]
    attempts: list[dict[str, Any]]
    stage_records: list[dict[str, Any]]
    raw_responses: dict[str, Any]
    options: LiveRunOptions
    policy: str
    run_id: str | None = None
    validation: dict[str, Any] | None = None
    review_output: dict[str, Any] | None = None
    repair_history: list[dict[str, Any]] = field(default_factory=list)
    repair_count: int = 0
    strategy_spec: dict[str, Any] | None = None
    pine_code: str | None = None
    normalizations: list[dict[str, Any]] = field(default_factory=list)
    policy_findings: list[dict[str, str]] = field(default_factory=list)
    knowledge_context: dict[str, Any] = field(default_factory=dict)


def generate_live(
    prompt: str,
    model_registry: Path,
    *,
    run_id: str | None = None,
    live_options: LiveRunOptions | None = None,
    model_override: str | None = None,
    model_stage_overrides: dict[str, str] | None = None,
    workflow: str = WORKFLOW_MULTI_AGENT,
    cost_profile: str = COST_PROFILE_QUALITY,
    policy: str = POLICY_OBSERVE,
    save_raw_provider: bool = False,
    knowledge_context: str = KNOWLEDGE_CONTEXT_AUTO,
) -> LiveGenerationResult:
    try:
        import litellm
    except ImportError as exc:
        raise LiveDependencyError("Live mode requires the optional live dependencies. Run with `uv run --extra live strategy-codebot ...`.") from exc

    registry = yaml.safe_load(model_registry.read_text(encoding="utf-8"))
    options = normalize_live_options(
        live_options,
        model_override=model_override,
        model_stage_overrides=model_stage_overrides,
        workflow=workflow,
        cost_profile=cost_profile,
        save_raw_provider=save_raw_provider,
        knowledge_context=knowledge_context,
    )
    run_knowledge_context = build_knowledge_context(prompt) if options.knowledge_context == KNOWLEDGE_CONTEXT_AUTO else {}
    if options.workflow == WORKFLOW_SINGLE:
        return _generate_single_live(litellm, prompt, registry, options=options, policy=policy, knowledge_context=run_knowledge_context)
    return _generate_multi_agent_live(
        litellm,
        prompt,
        registry,
        options=options,
        policy=policy,
        run_id=run_id,
        knowledge_context=run_knowledge_context,
    )


def live_error_report(exc: LiveError) -> dict[str, Any]:
    report = {"code": exc.code, "message": str(exc), "attempts": exc.attempts}
    if exc.diagnostics:
        report["diagnostics"] = exc.diagnostics
    return report


def normalize_live_options(
    live_options: LiveRunOptions | None = None,
    *,
    model_override: str | None = None,
    model_stage_overrides: dict[str, str] | None = None,
    workflow: str = WORKFLOW_MULTI_AGENT,
    cost_profile: str = COST_PROFILE_QUALITY,
    save_raw_provider: bool = False,
    knowledge_context: str = KNOWLEDGE_CONTEXT_AUTO,
) -> LiveRunOptions:
    if live_options is not None:
        if model_override or model_stage_overrides or workflow != WORKFLOW_MULTI_AGENT or cost_profile != COST_PROFILE_QUALITY or save_raw_provider or knowledge_context != KNOWLEDGE_CONTEXT_AUTO:
            raise ValueError("live_options cannot be combined with legacy live option kwargs")
        return live_options
    return LiveRunOptions(
        model_override=model_override,
        model_stage_overrides=model_stage_overrides or {},
        workflow=workflow,
        cost_profile=cost_profile,
        save_raw_provider=save_raw_provider,
        knowledge_context=knowledge_context,
    )


def _generate_single_live(
    litellm: Any,
    prompt: str,
    registry: dict[str, Any],
    *,
    options: LiveRunOptions,
    policy: str,
    knowledge_context: dict[str, Any],
) -> LiveGenerationResult:
    models = _models_for_agent(registry, "pine_specialist", model_override=options.model_override)
    attempts: list[dict[str, Any]] = []
    call = _call_model_with_fallbacks(
        litellm,
        registry,
        models,
        messages=_messages(prompt, knowledge_context),
        response_format=_response_format(),
        attempts=attempts,
        policy=policy,
        payload_validator=_validate_single_payload,
    )
    return LiveGenerationResult(
        strategy_spec=call.payload["strategy_spec"],
        pine_code=call.payload["pine_code"],
        model=call.model,
        provider=call.provider,
        latency_ms=call.latency_ms,
        attempts=attempts,
        usage=call.usage,
        raw_response=call.raw_response,
        workflow=WORKFLOW_SINGLE,
        policy_findings=call.policy_findings,
        knowledge_context=knowledge_context,
    )


def _generate_multi_agent_live(
    litellm: Any,
    prompt: str,
    registry: dict[str, Any],
    *,
    options: LiveRunOptions,
    policy: str,
    run_id: str | None,
    knowledge_context: dict[str, Any],
) -> LiveGenerationResult:
    attempts: list[dict[str, Any]] = []
    stage_records: list[dict[str, Any]] = []
    raw_responses: dict[str, Any] = {}
    stage_context = StageRunContext(
        litellm=litellm,
        registry=registry,
        attempts=attempts,
        stage_records=stage_records,
        raw_responses=raw_responses,
        options=options,
        policy=policy,
        run_id=run_id,
        knowledge_context=knowledge_context,
    )
    context_packet = _initial_context_packet(prompt, policy, knowledge_context)

    reasoning = _run_and_record_stage(stage_context, STAGE_STRATEGY_REASONING, context_packet)
    context_packet = _advance_context(context_packet, STAGE_STRATEGY_REASONING, reasoning["payload"])

    coding = _run_and_record_stage(stage_context, STAGE_STRATEGY_CODING, context_packet)
    strategy_spec = coding["payload"]["output"]["strategy_spec"]
    validate_payload(strategy_spec, "strategy-spec.schema.json")
    strategy_spec = _normalize_target_platform_for_prompt(prompt, strategy_spec, stage_context, STAGE_STRATEGY_CODING)
    coding["payload"]["output"]["strategy_spec"] = strategy_spec
    stage_records[-1]["output"]["strategy_spec"] = strategy_spec
    stage_context.strategy_spec = strategy_spec
    context_packet = _advance_context(context_packet, STAGE_STRATEGY_CODING, coding["payload"], artifacts={"strategy_spec": strategy_spec})

    pine = _run_and_record_stage(stage_context, STAGE_PINE_CODE_GENERATION, context_packet)
    pine_code = pine["payload"]["output"]["pine_code"]
    if not isinstance(pine_code, str) or not pine_code.strip():
        raise LiveResponseSchemaError("pine_code_generation must produce non-empty pine_code.", attempts=attempts)
    pine_code = _normalize_live_pine_code(pine_code, stage_context, STAGE_PINE_CODE_GENERATION)
    pine["payload"]["output"]["pine_code"] = pine_code
    stage_records[-1]["output"]["pine_code"] = pine_code
    stage_context.pine_code = pine_code
    validation = validate_pine(pine_code, strategy_spec)
    stage_context.validation = validation
    context_packet = _advance_context(context_packet, STAGE_PINE_CODE_GENERATION, pine["payload"], artifacts={"strategy_spec": strategy_spec, "pine_code": pine_code, "validation": validation, "validation_failures": _validation_failures(validation)})

    review = _run_and_record_stage(stage_context, STAGE_BALANCED_REVIEW, context_packet)
    review_output = review["payload"]["output"]
    stage_context.review_output = review_output
    context_packet = _advance_context(context_packet, STAGE_BALANCED_REVIEW, review["payload"], artifacts={"validation": validation, "review": review_output})

    repair_history: list[dict[str, Any]] = []
    repair_count = 0
    while _requires_repair(validation, review_output) and repair_count < MAX_REPAIR_LOOPS:
        repair_count += 1
        repair = _run_and_record_stage(stage_context, STAGE_REPAIR, context_packet, repair_iteration=repair_count)
        repair_output = repair["payload"]["output"]
        strategy_spec = repair_output["strategy_spec"]
        pine_code = repair_output["pine_code"]
        validate_payload(strategy_spec, "strategy-spec.schema.json")
        strategy_spec = _normalize_target_platform_for_prompt(prompt, strategy_spec, stage_context, STAGE_REPAIR, repair_iteration=repair_count)
        repair["payload"]["output"]["strategy_spec"] = strategy_spec
        stage_records[-1]["output"]["strategy_spec"] = strategy_spec
        if not isinstance(pine_code, str) or not pine_code.strip():
            raise LiveResponseSchemaError("repair must produce non-empty pine_code.", attempts=attempts)
        pine_code = _normalize_live_pine_code(pine_code, stage_context, STAGE_REPAIR, repair_iteration=repair_count)
        repair["payload"]["output"]["pine_code"] = pine_code
        stage_records[-1]["output"]["pine_code"] = pine_code
        stage_context.strategy_spec = strategy_spec
        stage_context.pine_code = pine_code
        validation = validate_pine(pine_code, strategy_spec)
        stage_context.validation = validation
        repair_history.append({"iteration": repair_count, "validation_status": validation["status"], "validation": validation, "validation_failures": _validation_failures(validation), "validation_warnings": validation.get("warnings", [])})
        stage_context.repair_count = repair_count
        stage_context.repair_history = repair_history
        context_packet = _advance_context(
            context_packet,
            STAGE_REPAIR,
            repair["payload"],
            artifacts={"strategy_spec": strategy_spec, "pine_code": pine_code, "validation": validation, "validation_failures": _validation_failures(validation), "repair_iteration": repair_count},
        )
        review = _run_and_record_stage(stage_context, STAGE_BALANCED_REVIEW, context_packet)
        review_output = review["payload"]["output"]
        stage_context.review_output = review_output
        context_packet = _advance_context(context_packet, STAGE_BALANCED_REVIEW, review["payload"], artifacts={"validation": validation, "review": review_output})
        repair_history[-1]["review_verdict"] = review_output["verdict"]
        repair_history[-1]["required_fixes"] = review_output.get("required_fixes", [])
        repair_history[-1]["rationale"] = review_output.get("rationale")

    final_policy_findings = find_policy_claims(json.dumps({"strategy_spec": strategy_spec, "pine_code": pine_code}, ensure_ascii=False))
    stage_context.policy_findings.extend(final_policy_findings)
    blocking_policy_findings = [finding for finding in final_policy_findings if finding.get("severity") == "block"]
    validation_allows_artifact = _validation_allows_artifact(validation)
    review_validation_disagreement = not validation_allows_artifact and review_output.get("verdict") == STATUS_PASS
    policy_blocked = policy == POLICY_ENFORCE and bool(blocking_policy_findings)
    hard_gate_failed = not validation_allows_artifact or review_output.get("verdict") == STATUS_FAIL or policy_blocked
    if hard_gate_failed:
        review_validation_disagreement = not validation_allows_artifact and review_output.get("verdict") == STATUS_PASS
        attempts.append(
            {
                "stage": "final_gate",
                "status": STATUS_FAIL,
                "error_code": "safety_policy_violation" if blocking_policy_findings else "workflow_gate_failed",
                "failure_class": FAILURE_POLICY_VIOLATION if blocking_policy_findings else _final_gate_failure_class(validation, review_output),
                "validation_status": validation["status"],
                "validation": validation,
                "validation_failures": _validation_failures(validation),
                "review_validation_disagreement": review_validation_disagreement,
                "review": review_output,
                "policy_findings": blocking_policy_findings,
            }
        )
        if blocking_policy_findings:
            exc = LiveSafetyError("Final live workflow artifacts violate hard safety policy.", attempts=attempts)
        else:
            exc = LiveProviderError("Live multi-agent workflow failed final validation/review gate after repair attempts.", attempts=attempts)
        _attach_live_diagnostics(exc, stage_context)
        raise exc

    total_latency_ms = sum(int(stage.get("latency_ms", 0)) for stage in stage_records)
    total_usage = _sum_usage(stage_records)
    final_stage = stage_records[-1]
    generation_gate = _generation_gate(validation)
    production_gate = _production_gate(validation, review_output, stage_context.policy_findings, repair_count)
    workflow_trace = {
        "run_id": run_id,
        "workflow": WORKFLOW_MULTI_AGENT,
        "cost_profile": options.cost_profile,
        "agent_roles": AGENT_ROLE_REGISTRY,
        "lifecycle_events": _lifecycle_events(run_id or "live-workflow", options.cost_profile, policy, stage_records, attempts),
        "attempts": attempts,
        "stages": stage_records,
        "repair_history": repair_history,
        "normalizations": stage_context.normalizations,
        "policy_findings": stage_context.policy_findings,
        "knowledge_context": _knowledge_context_summary(knowledge_context),
        "generation_gate": generation_gate,
        "production_gate": production_gate,
        "final_decision": {
            "status": STATUS_PASS,
            "validation_status": validation["status"],
            "validation": validation,
            "review_verdict": review_output["verdict"],
            "required_fixes": review_output.get("required_fixes", []),
            "repair_count": repair_count,
            "generation_gate": generation_gate,
            "production_gate": production_gate,
        },
    }
    return LiveGenerationResult(
        strategy_spec=strategy_spec,
        pine_code=pine_code,
        model=final_stage["model"],
        provider=final_stage["provider"],
        latency_ms=total_latency_ms,
        attempts=attempts,
        usage=total_usage,
        raw_response={"stages": raw_responses} if options.save_raw_provider else {},
        workflow=WORKFLOW_MULTI_AGENT,
        stages=[_stage_metadata(stage) for stage in stage_records],
        workflow_trace=workflow_trace,
        repair_count=repair_count,
        policy_findings=stage_context.policy_findings,
        generation_gate=generation_gate,
        production_gate=production_gate,
        knowledge_context=knowledge_context,
    )


def _normalize_live_pine_code(code: str, context: StageRunContext, stage: str, *, repair_iteration: int | None = None) -> str:
    normalized, action = _normalize_pine_version_header(code)
    if action["changed"]:
        action.update({"stage": stage, "repair_iteration": repair_iteration})
        context.normalizations.append(action)
        if context.stage_records:
            context.stage_records[-1].setdefault("normalization", []).append(action)
    return normalized


def _normalize_target_platform_for_prompt(
    prompt: str,
    strategy_spec: dict[str, Any],
    context: StageRunContext,
    stage: str,
    *,
    repair_iteration: int | None = None,
) -> dict[str, Any]:
    if not _prompt_requests_both_platforms(prompt) or strategy_spec.get("target_platform") == "both":
        return strategy_spec
    normalized = deepcopy(strategy_spec)
    previous = normalized.get("target_platform")
    normalized["target_platform"] = "both"
    normalized.setdefault("constraints", [])
    if isinstance(normalized["constraints"], list):
        normalized["constraints"].append("MQL5 output is design-only and requires manual MetaTrader validation.")
    action = {
        "kind": "target_platform",
        "changed": True,
        "from": previous,
        "to": "both",
        "stage": stage,
        "repair_iteration": repair_iteration,
        "reason": "prompt_requests_both_platforms",
    }
    context.normalizations.append(action)
    if context.stage_records:
        context.stage_records[-1].setdefault("normalization", []).append(action)
    return normalized


def _prompt_requests_both_platforms(prompt: str) -> bool:
    lowered = prompt.lower()
    return (
        "both-platform" in lowered
        or "both platform" in lowered
        or ("pine" in lowered and "mql5" in lowered)
        or ("tradingview" in lowered and "metatrader" in lowered)
    )


def _normalize_pine_version_header(code: str) -> tuple[str, dict[str, Any]]:
    lines = code.splitlines()
    version_indexes = [index for index, line in enumerate(lines) if line.strip().startswith("//@version=") or line.strip().startswith("@version=")]
    action: dict[str, Any] = {"kind": "pine_version_header", "changed": False}
    if len(version_indexes) != 1:
        action["reason"] = "missing_or_multiple_version_directives"
        return code, action
    version_index = version_indexes[0]
    stripped_version = lines[version_index].strip()
    if stripped_version == "@version=6":
        lines[version_index] = "//@version=6"
        action["fixed_missing_comment_prefix"] = True
    elif stripped_version != "//@version=6":
        action["reason"] = "version_directive_not_v6"
        return code, action
    first_non_empty = next((index for index, line in enumerate(lines) if line.strip()), 0)
    if version_index == first_non_empty:
        action["reason"] = "already_first_non_empty"
        if action.get("fixed_missing_comment_prefix"):
            action["changed"] = True
            trailing_newline = "\n" if code.endswith("\n") else ""
            return "\n".join(lines) + trailing_newline, action
        return code, action
    version_line = lines.pop(version_index)
    insert_at = 0
    lines.insert(insert_at, version_line.strip())
    action.update({"changed": True, "from_line": version_index + 1, "to_line": insert_at + 1})
    trailing_newline = "\n" if code.endswith("\n") else ""
    return "\n".join(lines) + trailing_newline, action


def _validation_failures(validation: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not validation:
        return []
    return [
        {"name": check.get("name"), "status": check.get("status"), "details": check.get("details")}
        for check in validation.get("checks", [])
        if check.get("status") not in {STATUS_PASS, STATUS_SKIPPED}
    ]


def _final_gate_failure_class(validation: dict[str, Any], review_output: dict[str, Any]) -> str:
    if validation["status"] != STATUS_PASS and review_output.get("verdict") == STATUS_PASS:
        return FAILURE_REVIEW_VALIDATION_DISAGREEMENT
    if validation["status"] != STATUS_PASS:
        return FAILURE_STATIC_VALIDATION_FAILED
    return FAILURE_REVIEW_FAILED


def _attach_live_diagnostics(exc: LiveError, context: StageRunContext) -> None:
    if not exc.diagnostics:
        exc.diagnostics = _live_failure_diagnostics(context, exc)


def _live_failure_diagnostics(context: StageRunContext, exc: LiveError) -> dict[str, Any]:
    final_attempt = _last_failed_attempt(context.attempts)
    failure_class = final_attempt.get("failure_class") or classify_failure(final_attempt.get("error_code"), final_attempt.get("error"))
    final_decision = {
        "status": STATUS_FAIL,
        "failure_class": failure_class,
        "failure_stage": final_attempt.get("stage"),
        "error_code": final_attempt.get("error_code"),
        "validation_status": (context.validation or {}).get("status"),
        "validation": context.validation or {},
        "validation_failures": _validation_failures(context.validation),
        "validation_warnings": (context.validation or {}).get("warnings", []),
        "review_verdict": (context.review_output or {}).get("verdict"),
        "review_validation_disagreement": final_attempt.get("review_validation_disagreement", False),
        "repair_count": context.repair_count,
    }
    generation_gate = _generation_gate(context.validation or {})
    production_gate = _production_gate(context.validation or {}, context.review_output or {}, context.policy_findings, context.repair_count)
    final_decision["generation_gate"] = generation_gate
    final_decision["production_gate"] = production_gate
    workflow_trace = {
        "run_id": context.run_id,
        "workflow": context.options.workflow,
        "cost_profile": context.options.cost_profile,
        "agent_roles": AGENT_ROLE_REGISTRY,
        "lifecycle_events": _lifecycle_events(context.run_id or "live-workflow", context.options.cost_profile, context.policy, context.stage_records, context.attempts),
        "attempts": context.attempts,
        "stages": context.stage_records,
        "repair_history": context.repair_history,
        "normalizations": context.normalizations,
        "policy_findings": context.policy_findings,
        "knowledge_context": _knowledge_context_summary(context.knowledge_context),
        "generation_gate": generation_gate,
        "production_gate": production_gate,
        "final_decision": final_decision,
    }
    metadata = {
        "status": STATUS_FAIL,
        "workflow": context.options.workflow,
        "provider": final_attempt.get("provider"),
        "model": final_attempt.get("model"),
        "final_model": final_attempt.get("model"),
        "latency_ms": sum(int(stage.get("latency_ms", 0)) for stage in context.stage_records),
        "total_latency_ms": sum(int(stage.get("latency_ms", 0)) for stage in context.stage_records),
        "usage": _sum_usage(context.stage_records),
        "total_usage": _sum_usage(context.stage_records),
        "attempts": context.attempts,
        "stages": [_stage_metadata(stage) for stage in context.stage_records],
        "repair_count": context.repair_count,
        "validation": context.validation or {},
        "validation_status": (context.validation or {}).get("status"),
        "validation_failures": _validation_failures(context.validation),
        "validation_warnings": (context.validation or {}).get("warnings", []),
        "normalizations": context.normalizations,
        "policy_findings": context.policy_findings,
        **knowledge_metadata(context.knowledge_context),
        "generation_gate": generation_gate,
        "production_gate": production_gate,
    }
    return {
        "code": exc.code,
        "message": str(exc),
        "workflow": context.options.workflow,
        "attempts": context.attempts,
        "stage_records": context.stage_records,
        "raw_responses": {"stages": context.raw_responses} if context.options.save_raw_provider else {},
        "workflow_trace": workflow_trace,
        "metadata": metadata,
        "final_decision": final_decision,
        "validation": context.validation or {},
        "validation_failures": _validation_failures(context.validation),
        "validation_warnings": (context.validation or {}).get("warnings", []),
        "review_findings": context.review_output or {},
        "repair_history": context.repair_history,
        "normalizations": context.normalizations,
        "policy_findings": context.policy_findings or _attempt_policy_findings(context.attempts),
        "knowledge_context": _knowledge_context_summary(context.knowledge_context),
        "knowledge_context_artifact": context.knowledge_context,
        "generation_gate": generation_gate,
        "production_gate": production_gate,
    }


def _last_failed_attempt(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    for attempt in reversed(attempts):
        if attempt.get("status") in {STATUS_FAIL, STATUS_SKIPPED}:
            return attempt
    return attempts[-1] if attempts else {"stage": "live", "failure_class": FAILURE_PROVIDER_ERROR}


def _attempt_policy_findings(attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for attempt in attempts:
        for finding in attempt.get("policy_findings", []) or []:
            findings.append({"stage": attempt.get("stage"), **finding})
    return findings


def _run_and_record_stage(
    context: StageRunContext,
    stage: str,
    context_packet: dict[str, Any],
    *,
    repair_iteration: int | None = None,
) -> dict[str, Any]:
    try:
        stage_result = _run_stage(
            context,
            stage,
            context_packet,
            repair_iteration=repair_iteration,
        )
    except LiveError as exc:
        _attach_live_diagnostics(exc, context)
        raise
    _record_stage(stage_result, context.stage_records, context.raw_responses)
    return stage_result


def _run_stage(
    context: StageRunContext,
    stage: str,
    context_packet: dict[str, Any],
    *,
    repair_iteration: int | None = None,
) -> dict[str, Any]:
    models = _models_for_stage(context.registry, stage, model_stage_overrides=context.options.model_stage_overrides, cost_profile=context.options.cost_profile)
    call = _call_model_with_fallbacks(
        context.litellm,
        context.registry,
        models,
        messages=_stage_messages(stage, context_packet, repair_iteration=repair_iteration),
        response_format=_stage_response_format(stage),
        attempts=context.attempts,
        policy=context.policy,
        payload_validator=lambda payload: _validate_stage_payload(stage, payload),
        stage=stage,
    )
    context.policy_findings.extend(call.policy_findings)
    return {
        "stage": stage,
        "model": call.model,
        "provider": call.provider,
        "latency_ms": call.latency_ms,
        "usage": call.usage,
        "policy_findings": call.policy_findings,
        "provider_warnings": call.provider_warnings,
        "payload": call.payload,
        "raw_response": call.raw_response if context.options.save_raw_provider else {},
        "repair_iteration": repair_iteration,
        "context_refs": list(context_packet.get("context_refs", [])),
    }


def _call_model_with_fallbacks(
    litellm: Any,
    registry: dict[str, Any],
    models: list[str],
    *,
    messages: list[dict[str, str]],
    response_format: dict[str, Any],
    attempts: list[dict[str, Any]],
    policy: str,
    payload_validator: Any,
    stage: str | None = None,
) -> ProviderCallResult:
    first_attempt = len(attempts)
    max_retries = int(registry.get("defaults", {}).get("max_retries", 0))
    temperature = float(registry.get("defaults", {}).get("temperature", 0.2))
    request_timeout = float(registry.get("defaults", {}).get("request_timeout_seconds", 60))

    for model in models:
        route = _provider_route(model)
        attempt_base = _attempt_base(model, route, stage)
        if route.credential_env and not os.getenv(route.credential_env):
            attempts.append({**attempt_base, "status": STATUS_SKIPPED, "error_code": "missing_provider_credential", "failure_class": FAILURE_MISSING_CREDENTIAL, "credential": route.credential_env})
            continue

        for attempt_index in range(max_retries + 1):
            started = time.perf_counter()
            attempt_number = attempt_index + 1
            try:
                response, provider_warnings = _litellm_completion(
                    litellm,
                    **_completion_kwargs(
                        model=model,
                        route=route,
                        messages=messages,
                        temperature=temperature,
                        request_timeout=request_timeout,
                        response_format=response_format,
                    )
                )
                latency_ms = int((time.perf_counter() - started) * 1000)
                raw_response = _response_to_dict(response)
                payload = _payload_from_response(response)
                payload_validator(payload)
                policy_findings = find_policy_claims(json.dumps(payload, ensure_ascii=False))
                blocking_policy_findings = [finding for finding in policy_findings if finding.get("severity") == "block"]
                if policy == POLICY_ENFORCE and blocking_policy_findings:
                    subject = f"{stage} response" if stage else "Provider response"
                    attempts.append(
                        {
                            **attempt_base,
                            "attempt": attempt_number,
                            "status": STATUS_FAIL,
                            "error_code": "safety_policy_violation",
                            "failure_class": FAILURE_POLICY_VIOLATION,
                            "latency_ms": latency_ms,
                            "request_timeout_seconds": request_timeout,
                            "policy_findings": blocking_policy_findings,
                        }
                    )
                    raise LiveSafetyError(f"{subject} violates live/profitability safety policy.", attempts=attempts)
                attempt_record = {**attempt_base, "attempt": attempt_number, "status": STATUS_PASS, "latency_ms": latency_ms, "request_timeout_seconds": request_timeout}
                if provider_warnings:
                    attempt_record["provider_warnings"] = provider_warnings
                attempts.append(attempt_record)
                return ProviderCallResult(
                    payload=payload,
                    raw_response=raw_response,
                    usage=_usage_from_response(raw_response),
                    model=model,
                    provider=route.provider,
                    latency_ms=latency_ms,
                    policy_findings=policy_findings,
                    provider_warnings=provider_warnings,
                )
            except LiveSafetyError:
                raise
            except (json.JSONDecodeError, KeyError, TypeError, ValidationError, LiveResponseError) as exc:
                error_code = "schema_invalid_provider_response" if isinstance(exc, (ValidationError, LiveResponseSchemaError)) else "malformed_provider_response"
                attempts.append(
                    {
                        **attempt_base,
                        "attempt": attempt_number,
                        "status": STATUS_FAIL,
                        "error_code": error_code,
                        "failure_class": classify_failure(error_code, str(exc)),
                        "error": str(exc),
                        "request_timeout_seconds": request_timeout,
                    }
                )
            except Exception as exc:
                attempts.append(
                    {
                        **attempt_base,
                        "attempt": attempt_number,
                        "status": STATUS_FAIL,
                        "error_code": FAILURE_PROVIDER_ERROR,
                        "failure_class": classify_failure(FAILURE_PROVIDER_ERROR, str(exc)),
                        "error": str(exc),
                        "request_timeout_seconds": request_timeout,
                    }
                )

    _raise_live_failure(attempts, attempts[first_attempt:])


def _attempt_base(model: str, route: ProviderRoute, stage: str | None) -> dict[str, Any]:
    base: dict[str, Any] = {"model": model, "provider": route.provider}
    if stage:
        base["stage"] = stage
    return base


def _models_for_agent(registry: dict[str, Any], agent: str, *, model_override: str | None) -> list[str]:
    if model_override:
        return [model_override]
    config = registry["agents"][agent]
    models = [config["primary"], *config.get("fallbacks", [])]
    return [model for model in models if isinstance(model, str) and model]


def _models_for_stage(registry: dict[str, Any], stage: str, *, model_stage_overrides: dict[str, str], cost_profile: str) -> list[str]:
    if stage in model_stage_overrides:
        return [model_stage_overrides[stage]]
    if cost_profile == COST_PROFILE_CHEAP:
        model_key = CHEAP_STAGE_MODELS[stage]
        model_config = registry["provider_model_mappings"]["openrouter"]["cheap_quality"][model_key]
        if isinstance(model_config, list):
            return [model for model in model_config if isinstance(model, str) and model]
        return [model_config]
    return _models_for_agent(registry, QUALITY_STAGE_AGENTS[stage], model_override=None)


def _provider(model: str) -> str:
    return model.split("/", 1)[0] if "/" in model else "unknown"


def _provider_route(model: str) -> ProviderRoute:
    provider = _provider(model)
    credential_env = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "google": "GEMINI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "litellm": "LITELLM_API_KEY",
    }.get(provider)
    return ProviderRoute(provider=provider, credential_env=credential_env)


def _completion_kwargs(
    *,
    model: str,
    route: ProviderRoute,
    messages: list[dict[str, str]],
    temperature: float,
    request_timeout: float,
    response_format: dict[str, Any],
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "timeout": request_timeout,
        "response_format": response_format,
    }
    kwargs.update(route.completion_kwargs())
    return kwargs


def _litellm_completion(litellm: Any, **kwargs: Any) -> tuple[Any, list[str]]:
    if os.getenv("STRATEGY_CODEBOT_LITELLM_DEBUG"):
        return litellm.completion(**kwargs), []
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
        response = litellm.completion(**kwargs)
    return response, _captured_provider_warnings(stdout_buffer.getvalue(), stderr_buffer.getvalue())


def _captured_provider_warnings(stdout_text: str, stderr_text: str) -> list[str]:
    warnings = []
    for label, text in (("stdout", stdout_text), ("stderr", stderr_text)):
        normalized = text.strip()
        if normalized:
            warnings.append(f"provider {label}: {normalized}")
    return warnings


def _messages(prompt: str, knowledge_context: dict[str, Any]) -> list[dict[str, str]]:
    user_content = prompt
    if knowledge_context:
        user_content = json.dumps(
            {
                "original_prompt": prompt,
                "knowledge_context": compact_knowledge_context(knowledge_context),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    return [
        {
            "role": "system",
            "content": (
                "Return only JSON matching the provided schema. "
                "Generate reviewable Pine Script v6 artifacts only. "
                "Do not claim runtime validation, live execution readiness, broker deployment, guaranteed returns, or risk-free behavior. "
                "Take-profit and profit-target rules are allowed as strategy mechanics, not performance claims."
            ),
        },
        {"role": "user", "content": user_content},
    ]


def _stage_messages(stage: str, context_packet: dict[str, Any], *, repair_iteration: int | None) -> list[dict[str, str]]:
    role_prompts = {
        STAGE_STRATEGY_REASONING: (
            "Analyze the trading prompt and create a strategy brief. Do not write Pine code. "
            "For strategy outputs, include concrete position sizing, stop-loss, and take-profit assumptions "
            "unless the prompt explicitly requests indicator-only output or excludes fixed risk exits. "
            f"{CONSERVATIVE_POSITION_SIZING_GUIDANCE}"
        ),
        STAGE_STRATEGY_CODING: (
            "Convert the strategy brief into a valid strategy_spec JSON object. Do not write Pine code. "
            "Populate stop_loss and take_profit for strategy outputs when risk exits are expected. "
            f"{CONSERVATIVE_POSITION_SIZING_GUIDANCE}"
        ),
        STAGE_PINE_CODE_GENERATION: (
            "Generate Pine Script v6 from the supplied strategy_spec without changing strategy logic. "
            "For strategy entries with stop-loss or take-profit risk rules, implement exits with "
            "strategy.exit using stop and/or limit parameters; do not rely only on strategy.close."
        ),
        STAGE_BALANCED_REVIEW: (
            "Review the full context for schema, Pine, trading-logic, and safety issues. If static "
            "validation has failing checks, verdict must be needs_fix or fail with required fixes. "
            "Manual-required warnings may pass only when they are explained and non-blocking."
        ),
        STAGE_REPAIR: (
            "Repair all static validation failures first, especially "
            "version_header and missing strategy.exit for stop-loss/take-profit behavior, then review "
            "findings. Preserve the accepted strategy intent. "
            f"{CONSERVATIVE_POSITION_SIZING_GUIDANCE}"
        ),
    }
    repair_note = f" Repair iteration {repair_iteration}." if repair_iteration else ""
    return [
        {
            "role": "system",
            "content": (
                f"You are the {stage} stage in a multi-model strategy generation workflow. "
                f"{role_prompts[stage]}{repair_note} Return only JSON matching the provided schema. "
                "Never claim live execution readiness, broker deployment, runtime validation, guaranteed returns, or risk-free behavior. "
                "Take-profit and profit-target rules are allowed as strategy mechanics, not performance claims."
            ),
        },
        {"role": "user", "content": json.dumps(context_packet, ensure_ascii=False, sort_keys=True)},
    ]


def _response_format() -> dict[str, Any]:
    strategy_schema = schema("strategy-spec.schema.json")
    strategy_schema.pop("$schema", None)
    return _json_schema_response_format(
        LIVE_RESPONSE_SCHEMA_NAME,
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["strategy_spec", "pine_code"],
            "properties": {
                "strategy_spec": strategy_schema,
                "pine_code": {"type": "string", "minLength": 1},
            },
        },
    )


def _stage_response_format(stage: str) -> dict[str, Any]:
    output_schemas = {
        STAGE_STRATEGY_REASONING: {
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "constraints", "indicators", "entries", "exits", "risk_rules", "non_goals"],
            "properties": {
                "summary": {"type": "string", "minLength": 1},
                "constraints": _string_array_schema(),
                "indicators": _string_array_schema(),
                "entries": _string_array_schema(),
                "exits": _string_array_schema(),
                "risk_rules": _string_array_schema(),
                "non_goals": _string_array_schema(),
            },
        },
        STAGE_STRATEGY_CODING: {
            "type": "object",
            "additionalProperties": False,
            "required": ["strategy_spec"],
            "properties": {"strategy_spec": _strategy_schema()},
        },
        STAGE_PINE_CODE_GENERATION: {
            "type": "object",
            "additionalProperties": False,
            "required": ["pine_code"],
            "properties": {"pine_code": {"type": "string", "minLength": 1}},
        },
        STAGE_BALANCED_REVIEW: {
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict", "required_fixes", "rationale"],
            "properties": {
                "verdict": {"type": "string", "enum": ["pass", "needs_fix", "fail"]},
                "required_fixes": _string_array_schema(),
                "rationale": {"type": "string"},
            },
        },
        STAGE_REPAIR: {
            "type": "object",
            "additionalProperties": False,
            "required": ["strategy_spec", "pine_code"],
            "properties": {
                "strategy_spec": _strategy_schema(),
                "pine_code": {"type": "string", "minLength": 1},
            },
        },
    }
    return _json_schema_response_format(
        f"strategy_codebot_{stage}",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["stage", "output", "assumptions", "handoff_notes", "policy_observations"],
            "properties": {
                "stage": {"type": "string", "enum": [stage]},
                "output": output_schemas[stage],
                "assumptions": _string_array_schema(),
                "handoff_notes": {"type": "string"},
                "policy_observations": _string_array_schema(),
            },
        },
    )


def _strategy_schema() -> dict[str, Any]:
    strategy_schema = schema("strategy-spec.schema.json")
    strategy_schema.pop("$schema", None)
    return _strict_response_schema(strategy_schema)


def _strict_response_schema(schema_payload: dict[str, Any]) -> dict[str, Any]:
    return _strict_schema_node(schema_payload, optional=False)


def _strict_schema_node(schema_payload: dict[str, Any], *, optional: bool) -> dict[str, Any]:
    node = deepcopy(schema_payload)
    node.pop("default", None)
    node_type = node.get("type")
    if node_type == "object" and isinstance(node.get("properties"), dict):
        properties = node["properties"]
        original_required = set(node.get("required", []))
        node["properties"] = {
            key: _strict_schema_node(value, optional=key not in original_required)
            for key, value in properties.items()
        }
        node["required"] = list(properties)
        node["additionalProperties"] = False
    elif node_type == "array" and isinstance(node.get("items"), dict):
        node["items"] = _strict_schema_node(node["items"], optional=False)
    if optional:
        node = _nullable_schema(node)
    return node


def _nullable_schema(schema_payload: dict[str, Any]) -> dict[str, Any]:
    node = deepcopy(schema_payload)
    node_type = node.get("type")
    if isinstance(node_type, str):
        node["type"] = [node_type, "null"]
    elif isinstance(node_type, list) and "null" not in node_type:
        node["type"] = [*node_type, "null"]
    if "enum" in node and None not in node["enum"]:
        node["enum"] = [*node["enum"], None]
    return node


def _string_array_schema() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}}


def _json_schema_response_format(name: str, schema_payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema_payload}}


def _payload_from_response(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        content = response["choices"][0]["message"]["content"]
    else:
        choice = response.choices[0]
        message = getattr(choice, "message", None)
        content = message["content"] if isinstance(message, dict) else message.content
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise LiveResponseError("Provider response must be a JSON object.")
    return payload


def _validate_single_payload(payload: dict[str, Any]) -> None:
    if "strategy_spec" not in payload or "pine_code" not in payload:
        raise LiveResponseError("Provider response must include strategy_spec and pine_code.")
    payload["strategy_spec"] = _prune_nulls(payload["strategy_spec"])
    validate_payload(payload["strategy_spec"], "strategy-spec.schema.json")
    _validate_position_sizing_quality(payload["strategy_spec"])
    pine_code = payload.get("pine_code")
    if not isinstance(pine_code, str) or not pine_code.strip():
        raise LiveResponseSchemaError("Provider response must include non-empty pine_code.")


def _validate_stage_payload(stage: str, payload: dict[str, Any]) -> None:
    if payload.get("stage") != stage:
        raise LiveResponseError(f"Provider response must identify stage {stage}.")
    for key in ("output", "assumptions", "handoff_notes", "policy_observations"):
        if key not in payload:
            raise LiveResponseError(f"Provider response for {stage} must include {key}.")
    if stage in {STAGE_STRATEGY_CODING, STAGE_REPAIR}:
        payload["output"]["strategy_spec"] = _prune_nulls(payload["output"]["strategy_spec"])
        validate_payload(payload["output"]["strategy_spec"], "strategy-spec.schema.json")
        _validate_position_sizing_quality(payload["output"]["strategy_spec"])
    if stage in {STAGE_PINE_CODE_GENERATION, STAGE_REPAIR} and not payload["output"].get("pine_code"):
        raise LiveResponseError(f"Provider response for {stage} must include pine_code.")


def _validate_position_sizing_quality(strategy_spec: dict[str, Any]) -> None:
    sizing_text = " ".join(
        str(part)
        for part in (
            strategy_spec.get("position_sizing"),
            *(strategy_spec.get("risk_rules") or []),
        )
        if part
    ).lower()
    if not sizing_text:
        return
    for phrase in FULL_CAPITAL_POSITION_SIZING_PHRASES:
        if phrase in sizing_text:
            raise LiveResponseSchemaError(
                "strategy_spec uses unsafe full-capital position sizing; use 1-2% account equity risk per trade, "
                "fixed units, or another explicitly bounded small-risk model."
            )


def _response_to_dict(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return _json_safe(response)
    if hasattr(response, "model_dump"):
        return _json_safe(response.model_dump())
    if hasattr(response, "dict"):
        return _json_safe(response.dict())
    return {"repr": repr(response)}


def _prune_nulls(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _prune_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_prune_nulls(item) for item in value]
    return value


def _usage_from_response(raw_response: dict[str, Any]) -> dict[str, Any]:
    usage = raw_response.get("usage", {})
    return usage if isinstance(usage, dict) else {}


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(key): _json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_json_safe(item) for item in value]
        return repr(value)


def _initial_context_packet(prompt: str, policy: str, knowledge_context: dict[str, Any]) -> dict[str, Any]:
    compact_context = compact_knowledge_context(knowledge_context) if knowledge_context else {}
    knowledge_refs = list(compact_context.get("context_refs", []))
    return {
        "original_prompt": prompt,
        "knowledge_context": compact_context,
        "policy": policy,
        "policy_boundaries": [
            "No live trading automation.",
            "No broker deployment or integration-readiness claims.",
            "No guaranteed return or risk-free claims.",
            "Profit targets and take-profit rules are allowed as strategy mechanics, not performance claims.",
            CONSERVATIVE_POSITION_SIZING_GUIDANCE,
            "No TradingView or MetaTrader runtime validation claims.",
            "Generate reviewable Pine Script v6 artifacts only.",
        ],
        "schema_summary": {
            "final_required": ["strategy_spec", "pine_code"],
            "strategy_spec_schema": "schemas/strategy-spec.schema.json",
            "validation_schema": "schemas/validation-report.schema.json",
        },
        "stage_outputs": {},
        "current_artifacts": {},
        "previous_stage_output": {},
        "context_refs": ["prompt", "schemas/strategy-spec.schema.json", "policy_boundaries", *knowledge_refs],
    }


def _knowledge_context_summary(knowledge_context: dict[str, Any]) -> dict[str, Any]:
    if not knowledge_context:
        return {}
    metadata = knowledge_metadata(knowledge_context)
    return {
        **metadata,
        "stage_relevance": knowledge_context.get("stage_relevance", {}),
        "context_refs": knowledge_context.get("context_refs", []),
        "truncation": knowledge_context.get("truncation", {}),
    }


def _advance_context(context_packet: dict[str, Any], stage: str, payload: dict[str, Any], *, artifacts: dict[str, Any] | None = None) -> dict[str, Any]:
    next_packet = deepcopy(context_packet)
    next_packet["previous_stage_output"] = payload
    next_packet["stage_outputs"][stage] = payload
    if artifacts:
        next_packet["current_artifacts"].update(artifacts)
    next_packet["context_refs"] = [*next_packet.get("context_refs", []), stage]
    return next_packet


def _record_stage(stage_result: dict[str, Any], stage_records: list[dict[str, Any]], raw_responses: dict[str, Any]) -> None:
    stage_record = {
        "stage": stage_result["stage"],
        "agent_role": AGENT_ROLE_REGISTRY[stage_result["stage"]]["agent_role"],
        "model": stage_result["model"],
        "provider": stage_result["provider"],
        "latency_ms": stage_result["latency_ms"],
        "usage": stage_result["usage"],
        "status": STATUS_PASS,
        "context_refs": stage_result["context_refs"],
        "output": stage_result["payload"]["output"],
        "assumptions": stage_result["payload"].get("assumptions", []),
        "handoff_notes": stage_result["payload"].get("handoff_notes", ""),
        "policy_observations": stage_result["payload"].get("policy_observations", []),
    }
    if stage_result.get("policy_findings"):
        stage_record["policy_findings"] = stage_result["policy_findings"]
    if stage_result.get("provider_warnings"):
        stage_record["provider_warnings"] = stage_result["provider_warnings"]
    if stage_result.get("repair_iteration"):
        stage_record["repair_iteration"] = stage_result["repair_iteration"]
    if stage_result.get("raw_response"):
        stage_record["raw_response"] = stage_result["raw_response"]
    stage_records.append(stage_record)
    if stage_result.get("raw_response"):
        if stage_result.get("repair_iteration"):
            raw_key = f"{stage_result['stage']}_{stage_result['repair_iteration']}"
        else:
            raw_key = _raw_response_key(stage_result["stage"], stage_records)
        raw_responses[raw_key] = stage_result["raw_response"]


def _raw_response_key(stage: str, stage_records: list[dict[str, Any]]) -> str:
    occurrence = sum(1 for record in stage_records if record.get("stage") == stage)
    return stage if occurrence <= 1 else f"{stage}_{occurrence}"


def _stage_metadata(stage_record: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": stage_record["stage"],
        "agent_role": stage_record.get("agent_role"),
        "model": stage_record["model"],
        "provider": stage_record["provider"],
        "latency_ms": stage_record["latency_ms"],
        "usage": stage_record["usage"],
        "status": stage_record.get("status", STATUS_PASS),
        **({"provider_warnings": stage_record["provider_warnings"]} if "provider_warnings" in stage_record else {}),
        **({"repair_iteration": stage_record["repair_iteration"]} if "repair_iteration" in stage_record else {}),
    }


def _sum_usage(stage_records: list[dict[str, Any]]) -> dict[str, Any]:
    total: dict[str, Any] = {}
    for stage in stage_records:
        for key, value in stage.get("usage", {}).items():
            if isinstance(value, (int, float)):
                total[key] = total.get(key, 0) + value
    return total


def _lifecycle_events(run_id: str, cost_profile: str, policy: str, stage_records: list[dict[str, Any]], attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    attempt_lookup: dict[tuple[str | None, str | None, int | None], dict[str, Any]] = {}
    for attempt in attempts:
        key = (attempt.get("stage"), attempt.get("model"), attempt.get("attempt"))
        attempt_lookup[key] = attempt

    def append(event_type: str, **fields: Any) -> str:
        event_id = f"evt-{len(events) + 1}"
        events.append(
            {
                "event_id": event_id,
                "sequence": len(events) + 1,
                "created_at": datetime.now(UTC).isoformat(),
                "run_id": run_id,
                "workflow": WORKFLOW_MULTI_AGENT,
                "event_type": event_type,
                "policy_mode": policy,
                "status": fields.pop("status", STATUS_PASS),
                **{key: value for key, value in fields.items() if value is not None},
            }
        )
        validate_payload(events[-1], "tool-event.schema.json")
        return event_id

    root_event_id = append("agent.started", agent_role="multi_agent_orchestrator", stage="workflow", status=STATUS_STARTED)
    previous_event_id = root_event_id
    for stage in stage_records:
        stage_name = stage.get("stage")
        agent_role = stage.get("agent_role") or AGENT_ROLE_REGISTRY.get(stage_name, {}).get("agent_role")
        if previous_event_id != root_event_id:
            append("agent.handoff", stage=stage_name, agent_role=agent_role, parent_event_id=previous_event_id, status=STATUS_PASS)
        stage_event_type = "repair.started" if stage_name == STAGE_REPAIR else "agent.started"
        stage_event_id = append(stage_event_type, stage=stage_name, agent_role=agent_role, parent_event_id=previous_event_id, status=STATUS_STARTED)
        llm_start_id = append(
            "llm.started",
            stage=stage_name,
            agent_role=agent_role,
            model=stage.get("model"),
            provider=stage.get("provider"),
            attempt=1,
            parent_event_id=stage_event_id,
            status=STATUS_STARTED,
        )
        attempt = attempt_lookup.get((stage_name, stage.get("model"), 1), {})
        append(
            "llm.completed",
            stage=stage_name,
            agent_role=agent_role,
            model=stage.get("model"),
            provider=stage.get("provider"),
            attempt=1,
            parent_event_id=llm_start_id,
            latency_ms=stage.get("latency_ms"),
            usage=stage.get("usage", {}),
            status=attempt.get("status", stage.get("status", STATUS_PASS)),
            failure_class=attempt.get("failure_class"),
        )
        completed_type = "repair.completed" if stage_name == STAGE_REPAIR else "agent.completed"
        previous_event_id = append(
            completed_type,
            stage=stage_name,
            agent_role=agent_role,
            model=stage.get("model"),
            provider=stage.get("provider"),
            parent_event_id=stage_event_id,
            latency_ms=stage.get("latency_ms"),
            usage=stage.get("usage", {}),
            status=stage.get("status", STATUS_PASS),
        )
    append("agent.completed", agent_role="multi_agent_orchestrator", stage="workflow", parent_event_id=previous_event_id, status=STATUS_PASS, cost_profile=cost_profile)
    return events


def _requires_repair(validation: dict[str, Any], review_output: dict[str, Any]) -> bool:
    return (
        not _validation_allows_artifact(validation)
        or review_output.get("verdict") == STATUS_FAIL
        or any(_required_fix_blocks_production(fix) for fix in review_output.get("required_fixes", []))
    )


def _generation_gate(validation: dict[str, Any]) -> dict[str, Any]:
    validation_status = validation.get("status")
    return {
        "status": STATUS_PASS if _validation_allows_artifact(validation) else STATUS_FAIL,
        "validation_status": validation_status,
        "validation_failures": _validation_failures(validation),
    }


def _production_gate(
    validation: dict[str, Any],
    review_output: dict[str, Any],
    policy_findings: list[dict[str, str]],
    repair_count: int,
) -> dict[str, Any]:
    required_fixes = review_output.get("required_fixes", [])
    blocking_required_fixes = [fix for fix in required_fixes if _required_fix_blocks_production(fix)]
    warning_required_fixes = [fix for fix in required_fixes if fix not in blocking_required_fixes]
    policy_warnings = [finding for finding in policy_findings if finding.get("severity") == "warn"]
    policy_blocks = [finding for finding in policy_findings if finding.get("severity") == "block"]
    status = (
        STATUS_PASS
        if _validation_allows_artifact(validation)
        and review_output.get("verdict") != STATUS_FAIL
        and not blocking_required_fixes
        and not policy_blocks
        else STATUS_FAIL
    )
    return {
        "status": status,
        "validation_status": validation.get("status"),
        "review_verdict": review_output.get("verdict"),
        "required_fixes": required_fixes,
        "blocking_required_fixes": blocking_required_fixes,
        "warning_required_fixes": warning_required_fixes,
        "policy_warning_count": len(policy_warnings),
        "policy_block_count": len(policy_blocks),
        "repair_count": repair_count,
    }


def _validation_allows_artifact(validation: dict[str, Any]) -> bool:
    return validation.get("status") == STATUS_PASS or (
        validation.get("status") == "manual_required" and not _validation_failures(validation)
    )


def _required_fix_blocks_production(required_fix: str) -> bool:
    lowered = required_fix.lower()
    blocker_terms = (
        "strategy.exit",
        "stop loss",
        "stop-loss",
        "take profit",
        "take-profit",
        "schema",
        "static validation",
        "version_header",
        "pine version",
        "policy",
        "guaranteed",
        "risk-free",
        "no-loss",
        "broker",
        "live ready",
        "live-ready",
        "live execution",
    )
    return any(term in lowered for term in blocker_terms)


def validate_model_stage_overrides(model_stage_overrides: dict[str, str]) -> None:
    unknown = sorted(set(model_stage_overrides) - MODEL_STAGE_KEYS)
    if unknown:
        raise ValueError(f"Unknown model stage override(s): {', '.join(unknown)}")


def _validate_model_stage_overrides(model_stage_overrides: dict[str, str]) -> None:
    validate_model_stage_overrides(model_stage_overrides)


def _raise_live_failure(attempts: list[dict[str, Any]], failure_attempts: list[dict[str, Any]] | None = None) -> None:
    checked_attempts = failure_attempts if failure_attempts is not None else attempts
    if checked_attempts and all(attempt["status"] == STATUS_SKIPPED for attempt in checked_attempts):
        raise LiveCredentialError("No configured live model has credentials available.", attempts=attempts)
    raise LiveProviderError("Live generation failed for all configured models.", attempts=attempts)
