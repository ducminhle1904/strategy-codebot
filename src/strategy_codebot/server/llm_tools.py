import hashlib
import json
import math
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from jsonschema import ValidationError, validate

from strategy_codebot.evaluator_optimizer import (
    evaluator_review_status,
    evaluator_stop_reason,
    validation_allows_artifact,
    validation_failures,
)
from strategy_codebot.harness_types import STATUS_FAIL, STATUS_PASS
from strategy_codebot.knowledge_context import build_knowledge_context
from strategy_codebot.mql5 import runner_design
from strategy_codebot.pine import generate_pine, validate_pine, validate_pineforge_pine
from strategy_codebot.review import REVIEW_REPORT_PATH, write_review_report
from strategy_codebot.schemas import schema
from strategy_codebot.schemas import write_json
from strategy_codebot.paths import repo_root
from strategy_codebot.tool_runtime import POLICY_OBSERVE
from strategy_codebot.tool_runtime import load_tool_registry
from strategy_codebot.server.action_registry import action_registry_backend_tool_ids
from strategy_codebot.server.artifact_kinds import ROBUSTNESS_REPORT_ARTIFACT_KIND
from strategy_codebot.server.artifact_store import LocalArtifactStore
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.bot_proposals import BotProposalArtifactUnreadableError
from strategy_codebot.server.bot_proposals import BotProposalDraftInput
from strategy_codebot.server.bot_proposals import BotProposalSourceNotFoundError
from strategy_codebot.server.bot_proposals import build_bot_proposal_create_input
from strategy_codebot.server.ids import opaque_id
from strategy_codebot.server.knowledge_learning import KnowledgeLearningService
from strategy_codebot.server.policy import evaluate_agent_loop_tool_policy
from strategy_codebot.server.redaction import redact_value
from strategy_codebot.server.repository import AssistantRunRecord
from strategy_codebot.server.repository import ConversationRepository
from strategy_codebot.server.run_modes import RUN_MODE_BACKTEST_PREVIEW
from strategy_codebot.server.run_modes import BACKTEST_MAX_VARIANTS
from strategy_codebot.server.run_modes import BACKTEST_ENGINE_PINEFORGE
from strategy_codebot.server.run_modes import BACKTEST_ENGINES
from strategy_codebot.server.run_modes import BACKTEST_EXECUTABLE_TIMEFRAMES
from strategy_codebot.server.run_modes import BACKTEST_MAX_COST_BPS
from strategy_codebot.server.run_modes import BACKTEST_OHLCV_DEFAULT_EXCHANGE
from strategy_codebot.server.run_modes import BACKTEST_OHLCV_EXCHANGES
from strategy_codebot.server.run_modes import backtest_default_engine
from strategy_codebot.server.run_modes import backtest_job_limits_for_tier
from strategy_codebot.server.run_modes import backtest_runtime_boundary
from strategy_codebot.server.run_modes import RUN_MODE_DRY_RUN
from strategy_codebot.server.tool_errors import ToolExecutionError

OBJECT_SCHEMA = {"type": "object"}
MAX_USER_FACING_SOURCES = 5
PINE_STRATEGY_PATH = "pine/strategy.pine"
MQL5_RUNNER_DESIGN_PATH = "mql5/runner-design.md"
VALIDATION_REPORT_PATH = "validation-report.json"
BACKTEST_PLAN_PATH = "backtest/backtest-plan.json"
BACKTEST_PINEFORGE_VALIDATION_PATH = "backtest/pineforge-validation.json"
BACKTEST_PREVIEW_BOUNDARY_COPY = (
    "Local sandbox preview only; not TradingView proof, broker proof, live trading evidence, "
    "or a profitability claim."
)
STRATEGY_SPEC_SCHEMA_VERSION = "strategy-spec.schema.json"
BACKTEST_DEFAULT_CONFIG = {
    "engine": BACKTEST_ENGINE_PINEFORGE,
    "exchange": BACKTEST_OHLCV_DEFAULT_EXCHANGE,
    "symbol": "BTC/USDT",
    "timeframe": "1h",
    "candle_timeframe": "1m",
    "start": "2024-01-01",
    "end": "2024-12-31",
    "initial_capital": 10000,
    "fee_bps": 10,
    "slippage_bps": 5,
    "data_source": "public-readonly-cache",
}


def _strategy_spec_schema() -> dict[str, Any]:
    strategy_schema = deepcopy(schema("strategy-spec.schema.json"))
    strategy_schema.pop("$schema", None)
    strategy_schema.pop("$id", None)
    strategy_schema.pop("title", None)
    return strategy_schema


STRATEGY_SPEC_SCHEMA = _strategy_spec_schema()
BACKTEST_CONFIG_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "engine",
        "symbol",
        "timeframe",
        "start",
        "end",
        "initial_capital",
        "fee_bps",
        "slippage_bps",
        "data_source",
    ],
    "properties": {
        "engine": {"type": "string", "enum": list(BACKTEST_ENGINES)},
        "exchange": {"type": "string", "enum": list(BACKTEST_OHLCV_EXCHANGES)},
        "symbol": {"type": "string", "minLength": 1, "maxLength": 64},
        "timeframe": {"type": "string", "enum": list(BACKTEST_EXECUTABLE_TIMEFRAMES)},
        "candle_timeframe": {"type": "string", "const": "1m"},
        "start": {"type": "string", "minLength": 1},
        "end": {"type": "string", "minLength": 1},
        "initial_capital": {"type": "number", "exclusiveMinimum": 0},
        "fee_bps": {"type": "number", "minimum": 0, "maximum": BACKTEST_MAX_COST_BPS},
        "slippage_bps": {"type": "number", "minimum": 0, "maximum": BACKTEST_MAX_COST_BPS},
        "data_source": {"type": "string", "const": "public-readonly-cache"},
    },
}
BACKTEST_CONFIG_OVERRIDES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": BACKTEST_CONFIG_SCHEMA["properties"],
}


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]

    def as_provider_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


@dataclass(frozen=True)
class ToolExecutionContext:
    repository: ConversationRepository
    artifact_store: LocalArtifactStore
    auth: AuthContext
    run: AssistantRunRecord


TOOL_DEFINITIONS: dict[str, ToolDefinition] = {
    "generate_pine": ToolDefinition(
        name="generate_pine",
        description=(
            "Generate Pine Script v6 from a validated strategy spec. If the spec uses fixed cash/notional sizing "
            "(for example $1,000 per trade), do not encode it as strategy.fixed default_qty_value; compute an "
            "explicit qty such as cash_per_trade / close and pass that qty to strategy.entry."
        ),
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["strategy_spec"],
            "properties": {"strategy_spec": STRATEGY_SPEC_SCHEMA},
        },
    ),
    "create_mql5_design": ToolDefinition(
        name="create_mql5_design",
        description="Create an MQL5 runner design document from a strategy spec.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["strategy_spec"],
            "properties": {"strategy_spec": STRATEGY_SPEC_SCHEMA},
        },
    ),
    "static_validate": ToolDefinition(
        name="static_validate",
        description="Run static validation for generated Pine code.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["strategy_spec", "pine_code"],
            "properties": {"strategy_spec": STRATEGY_SPEC_SCHEMA, "pine_code": {"type": "string", "minLength": 1}},
        },
    ),
    "parallel_review": ToolDefinition(
        name="parallel_review",
        description="Run Strategy Codebot parallel review for spec, validation, and optional code artifacts.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["strategy_spec", "validation"],
            "properties": {
                "strategy_spec": STRATEGY_SPEC_SCHEMA,
                "validation": OBJECT_SCHEMA,
                "pine_code": {"type": ["string", "null"]},
                "mql5_runner_design": {"type": ["string", "null"]},
            },
        },
    ),
    "knowledge_check": ToolDefinition(
        name="knowledge_check",
        description="Return read-only internal knowledge context relevant to a trading strategy request.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["prompt"],
            "properties": {"prompt": {"type": "string", "minLength": 1}},
        },
    ),
    "knowledge_proposal": ToolDefinition(
        name="knowledge_proposal",
        description="Create a proposal artifact for human review without mutating source documents.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["summary"],
            "properties": {"summary": {"type": "string", "minLength": 1}},
        },
    ),
    "create_backtest_plan": ToolDefinition(
        name="create_backtest_plan",
        description="Create a local sandbox preview plan from model-generated PineScript v6 strategy source.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["prompt", "strategy_spec", "pine_code"],
            "properties": {
                "prompt": {"type": "string", "minLength": 1},
                "strategy_spec": STRATEGY_SPEC_SCHEMA,
                "pine_code": {"type": "string", "minLength": 1},
                "backtest_config": BACKTEST_CONFIG_OVERRIDES_SCHEMA,
            },
        },
    ),
    "run_backtest_preview": ToolDefinition(
        name="run_backtest_preview",
        description="Queue a sandboxed local Pine preview run from PineScript v6 strategy source.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["approval_id", "strategy_spec", "pine_code", "backtest_config"],
            "properties": {
                "approval_id": {"type": "string", "minLength": 1},
                "strategy_spec": STRATEGY_SPEC_SCHEMA,
                "pine_code": {"type": "string", "minLength": 1},
                "backtest_config": BACKTEST_CONFIG_SCHEMA,
                "prompt": {"type": "string"},
                "auto_chain": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"summary_on_complete": {"type": "boolean"}},
                },
            },
        },
    ),
    "run_backtest_variant_lab": ToolDefinition(
        name="run_backtest_variant_lab",
        description="Queue multiple comparable local preview variants with shared cache metadata.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["prompt", "strategy_spec", "base_backtest_config", "variants"],
            "properties": {
                "prompt": {"type": "string", "minLength": 1},
                "strategy_spec": STRATEGY_SPEC_SCHEMA,
                "pine_code": {"type": "string", "minLength": 1},
                "base_backtest_config": BACKTEST_CONFIG_SCHEMA,
                "variants": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": BACKTEST_MAX_VARIANTS,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name"],
                        "properties": {
                            "name": {"type": "string", "minLength": 1},
                            "strategy_spec": STRATEGY_SPEC_SCHEMA,
                            "pine_code": {"type": "string", "minLength": 1},
                            "backtest_config": BACKTEST_CONFIG_OVERRIDES_SCHEMA,
                        },
                    },
                },
            },
        },
    ),
    "get_backtest_summary": ToolDefinition(
        name="get_backtest_summary",
        description="Fetch bounded DB-indexed backtest report summary for model critique without loading raw artifacts.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["run_id"],
            "properties": {"run_id": {"type": "string", "minLength": 1}},
        },
    ),
    "query_backtest_trades": ToolDefinition(
        name="query_backtest_trades",
        description="Fetch bounded indexed trades for a completed backtest report.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["run_id"],
            "properties": {
                "run_id": {"type": "string", "minLength": 1},
                "bucket": {"type": "string", "enum": ["top_loser", "top_winner", "sample"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
        },
    ),
    "build_robustness_report": ToolDefinition(
        name="build_robustness_report",
        description="Build a review-only robustness report from a completed local backtest preview.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["run_id"],
            "properties": {"run_id": {"type": "string", "minLength": 1}},
        },
    ),
    "get_equity_curve_sample": ToolDefinition(
        name="get_equity_curve_sample",
        description="Fetch downsampled DB-indexed equity curve summary for a completed backtest.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["run_id"],
            "properties": {"run_id": {"type": "string", "minLength": 1}},
        },
    ),
    "draft_bot": ToolDefinition(
        name="draft_bot",
        description="Draft a Bot proposal for user review. This does not start a runtime or execute broker orders.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "strategy_spec": STRATEGY_SPEC_SCHEMA,
                "strategy_artifact_id": {"type": "string", "minLength": 1},
                "run_id": {"type": "string", "minLength": 1},
                "broker_connection_id": {"type": "string"},
                "account_id": {"type": "string"},
                "risk_policy_id": {"type": "string"},
                "strategy_id": {"type": "string"},
                "strategy_name": {"type": "string"},
                "manifest": OBJECT_SCHEMA,
                "data_subscriptions": {"type": "array", "items": OBJECT_SCHEMA},
                "readiness_checks": {"type": "array", "items": {"type": "string"}},
            },
        },
    ),
    "get_bot_status": ToolDefinition(
        name="get_bot_status",
        description="Fetch current Bot proposal/runtime status for chat explanation. Read-only.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "proposal_id": {"type": "string", "minLength": 1},
                "runtime_id": {"type": "string", "minLength": 1},
            },
        },
    ),
    "list_bots": ToolDefinition(
        name="list_bots",
        description="List current simulated Bots for chat explanation. Read-only.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
        },
    ),
    "list_bot_events": ToolDefinition(
        name="list_bot_events",
        description="List recent Bot lifecycle and risk events. Read-only.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["runtime_id"],
            "properties": {
                "runtime_id": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    ),
}


def provider_tools() -> list[dict[str, Any]]:
    return [definition.as_provider_tool() for definition in TOOL_DEFINITIONS.values()]


def read_risk_tool_names(registry: dict[str, Any]) -> list[str]:
    registry_read_tools = {
        str(entry["id"])
        for entry in registry.get("tools", [])
        if isinstance(entry, dict) and entry.get("id") and entry.get("risk_tier") == "read"
    }
    return [
        name
        for name in TOOL_DEFINITIONS
        if name in registry_read_tools
        and name in TOOL_HANDLERS
        and evaluate_agent_loop_tool_policy(name, "read").allowed
    ]


def read_risk_provider_tools(registry: dict[str, Any]) -> list[dict[str, Any]]:
    return [TOOL_DEFINITIONS[name].as_provider_tool() for name in read_risk_tool_names(registry)]


def validate_tool_arguments(tool_name: str, arguments: dict[str, Any]) -> str | None:
    definition = TOOL_DEFINITIONS.get(tool_name)
    if definition is None:
        return f"Unknown tool: {tool_name}"
    try:
        validate(instance=arguments, schema=definition.parameters)
    except ValidationError as exc:
        return f"Invalid tool input for {tool_name}: {exc.message}"
    return None


ToolHandler = Callable[[dict[str, Any], ToolExecutionContext], dict[str, Any]]


def _generate_pine_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    strategy_spec = arguments["strategy_spec"]
    pine_code = generate_pine(strategy_spec)
    context.repository.create_strategy_spec(
        context.auth,
        context.run.id,
        strategy_spec,
        STRATEGY_SPEC_SCHEMA_VERSION,
    )
    artifact = _persist_text_artifact(
        context,
        kind="pine_file",
        mime_type="text/plain",
        display_name="strategy.pine",
        relative_path=PINE_STRATEGY_PATH,
        content=pine_code,
        source="llm_orchestrator.generate_pine",
    )
    validation_summary = run_chat_artifact_validation_summary(
        context,
        strategy_spec=strategy_spec,
        pine_code=pine_code,
    )
    return {
        "pine_code": pine_code,
        "artifact_id": artifact.id if artifact else None,
        **validation_summary,
    }


def _create_mql5_design_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    strategy_spec = arguments["strategy_spec"]
    design = runner_design(strategy_spec)
    context.repository.create_strategy_spec(
        context.auth,
        context.run.id,
        strategy_spec,
        STRATEGY_SPEC_SCHEMA_VERSION,
    )
    artifact = _persist_text_artifact(
        context,
        kind="mql5_file",
        mime_type="text/markdown",
        display_name="runner-design.md",
        relative_path=MQL5_RUNNER_DESIGN_PATH,
        content=design,
        source="llm_orchestrator.create_mql5_design",
    )
    return {"mql5_runner_design": design, "artifact_id": artifact.id if artifact else None}


def _static_validate_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    strategy_spec = arguments["strategy_spec"]
    context.repository.create_strategy_spec(
        context.auth,
        context.run.id,
        strategy_spec,
        STRATEGY_SPEC_SCHEMA_VERSION,
    )
    validation, artifact_id = _persist_chat_validation_summary(
        context,
        strategy_spec=strategy_spec,
        pine_code=arguments["pine_code"],
        source="llm_orchestrator.static_validate",
    )
    return {"validation": validation, "artifact_id": artifact_id}


def run_chat_artifact_validation_summary(
    context: ToolExecutionContext,
    *,
    strategy_spec: dict[str, Any],
    pine_code: str,
) -> dict[str, Any]:
    validation, validation_artifact_id = _persist_chat_validation_summary(
        context,
        strategy_spec=strategy_spec,
        pine_code=pine_code,
        source="llm_orchestrator.generate_pine.static_validation",
    )
    review_report, review_artifact_id = _persist_chat_review_summary(
        context,
        validation=validation,
        source="llm_orchestrator.generate_pine.static_review",
    )
    evaluator_summary = _persist_chat_evaluator_optimizer_summary(
        context,
        validation=validation,
        review_report=review_report,
    )
    return {
        "validation": validation,
        "validation_artifact_id": validation_artifact_id,
        "review": review_report,
        "review_artifact_id": review_artifact_id,
        "evaluator_optimizer_summary": evaluator_summary,
    }


def _persist_chat_validation_summary(
    context: ToolExecutionContext,
    *,
    strategy_spec: dict[str, Any],
    pine_code: str,
    source: str,
) -> tuple[dict[str, Any], str | None]:
    validation = validate_pine(pine_code, strategy_spec)
    artifact = _persist_json_artifact(
        context,
        kind="validation_report",
        display_name="validation-report.json",
        relative_path=VALIDATION_REPORT_PATH,
        payload=validation,
        source=source,
    )
    context.repository.create_validation_report(
        context.auth,
        context.run.id,
        status=str(validation.get("status", "unknown")),
        payload=validation,
    )
    context.repository.append_run_event(
        context.auth,
        context.run.id,
        "validation.completed",
        {"status": str(validation.get("status", "unknown"))},
    )
    return validation, artifact.id if artifact else None


def _persist_chat_review_summary(
    context: ToolExecutionContext,
    *,
    validation: dict[str, Any],
    source: str,
) -> tuple[dict[str, Any], str | None]:
    failures = validation_failures(validation)
    allowed = validation_allows_artifact(validation)
    required_fixes = [
        {
            "source": "static_validation",
            "check": str(failure.get("name") or "validation_check"),
            "details": str(failure.get("details") or failure.get("message") or "Validation check failed."),
        }
        for failure in failures
    ]
    decision = STATUS_PASS if allowed else STATUS_FAIL
    review_report: dict[str, Any] = {
        "decision": decision,
        "verdict": decision,
        "source": "deterministic_static_validation",
        "validation_status": str(validation.get("status", "unknown")),
        "required_fixes": required_fixes,
        "rationale": (
            "Static Pine validation did not find blocking failures."
            if allowed
            else "Static Pine validation found blocking failures."
        ),
    }
    artifact = _persist_json_artifact(
        context,
        kind="review_report",
        display_name="review-report.json",
        relative_path=REVIEW_REPORT_PATH,
        payload=review_report,
        source=source,
    )
    context.repository.create_review_report(
        context.auth,
        context.run.id,
        decision=decision,
        payload=review_report,
    )
    context.repository.append_run_event(
        context.auth,
        context.run.id,
        "review.completed",
        {"decision": decision, "source": "deterministic_static_validation"},
    )
    return review_report, artifact.id if artifact else None


def _persist_chat_evaluator_optimizer_summary(
    context: ToolExecutionContext,
    *,
    validation: dict[str, Any],
    review_report: dict[str, Any],
) -> dict[str, Any]:
    final_review_status = evaluator_review_status(review_report=review_report)
    production_gate = {
        "status": STATUS_PASS if validation_allows_artifact(validation) and final_review_status != STATUS_FAIL else STATUS_FAIL,
        "validation_status": str(validation.get("status", "unknown")),
        "review_decision": final_review_status,
        "blocking_required_fixes": review_report.get("required_fixes", []),
        "source": "chat_generate_pine_static_validation",
    }
    summary = {
        "stop_reason": evaluator_stop_reason(
            validation=validation,
            final_review_status=final_review_status,
            production_gate=production_gate,
            policy_findings=[],
            budget_exhausted=False,
        ),
        "repair_count": 0,
        "repair_source_mix": {"llm": 0, "deterministic": 0, "unknown": 0},
        "final_validation_status": str(validation.get("status", "unknown")),
        "final_review_status": final_review_status,
        "budget_exhausted": False,
    }
    context.repository.append_run_event(
        context.auth,
        context.run.id,
        "evaluator_optimizer.summary",
        summary,
    )
    return summary


def _parallel_review_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    out_dir = context.artifact_store.run_dir(context.run.id)
    report = write_review_report(
        run_id=context.run.id,
        spec=arguments["strategy_spec"],
        validation=arguments["validation"],
        pine_code=arguments.get("pine_code"),
        mql5_runner_design=arguments.get("mql5_runner_design"),
        mode=RUN_MODE_DRY_RUN,
        out_path=out_dir / REVIEW_REPORT_PATH,
        record_harness=False,
        runtime_trace=False,
        policy=POLICY_OBSERVE,
    )
    context.repository.create_strategy_spec(
        context.auth,
        context.run.id,
        arguments["strategy_spec"],
        STRATEGY_SPEC_SCHEMA_VERSION,
    )
    artifact = context.repository.create_artifact(
        context.auth,
        context.run.id,
        kind="review_report",
        mime_type="application/json",
        display_name=REVIEW_REPORT_PATH,
        storage_key=context.artifact_store.storage_key(context.run.id, REVIEW_REPORT_PATH),
        metadata_json={"source": "llm_orchestrator.parallel_review"},
    )
    if artifact is not None:
        context.repository.append_run_event(
            context.auth,
            context.run.id,
            "artifact.created",
            {"artifact_id": artifact.id, "kind": artifact.kind, "display_name": artifact.display_name},
        )
    decision = str(report.get("decision") or report.get("status") or "completed")
    context.repository.create_review_report(context.auth, context.run.id, decision=decision, payload=report)
    context.repository.append_run_event(
        context.auth,
        context.run.id,
        "review.completed",
        {"decision": decision},
    )
    return {"review": report, "artifact_id": artifact.id if artifact else None}


def _knowledge_check_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    return {"knowledge_context": build_knowledge_context(arguments["prompt"])}


def _knowledge_proposal_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    filename = "knowledge-proposal.json"
    proposal = {
        "status": "manual_review_required",
        "summary": arguments["summary"],
        "recommendations": ["Review this proposal before changing knowledge documents."],
    }
    out_dir = context.artifact_store.run_dir(context.run.id)
    path = out_dir / filename
    candidate = KnowledgeLearningService(context.repository, context.artifact_store).propose_candidate(
        lesson=arguments["summary"],
        evidence_ref=f"run:{context.run.id}:knowledge_proposal",
        candidate_type="episodic",
        source_uri=f"run:{context.run.id}:knowledge_proposal",
        metadata={"source": "knowledge_proposal_tool"},
    )
    proposal["candidate_id"] = candidate["candidate_id"]
    proposal["candidate_status"] = candidate["status"]
    write_json(path, proposal)
    artifact = context.repository.create_artifact(
        context.auth,
        context.run.id,
        kind="knowledge_proposal",
        mime_type="application/json",
        display_name=filename,
        storage_key=context.artifact_store.storage_key(context.run.id, filename),
        metadata_json={"source": "llm_orchestrator"},
    )
    if artifact is not None:
        context.repository.append_run_event(
            context.auth,
            context.run.id,
            "artifact.created",
            {"artifact_id": artifact.id, "kind": artifact.kind, "display_name": artifact.display_name},
        )
    if not candidate.get("deduped"):
        context.repository.append_run_event(
            context.auth,
            context.run.id,
            "knowledge.candidate.created",
            candidate,
        )
    return {"proposal": proposal, "artifact_id": artifact.id if artifact else None, "candidate_id": candidate["candidate_id"], "status": candidate["status"]}


def _create_backtest_plan_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    prompt = arguments["prompt"]
    strategy_spec = arguments["strategy_spec"]
    approval_id = opaque_id("approval")
    backtest_config = _build_backtest_config(
        prompt,
        strategy_spec=strategy_spec,
        overrides=arguments.get("backtest_config"),
    )
    pine_code = _required_pineforge_pine(arguments.get("pine_code"), strategy_spec)
    validation = validate_pineforge_pine(pine_code, strategy_spec)
    pine_artifact, validation_artifact = _persist_pineforge_validation_artifacts(
        context,
        pine_code=pine_code,
        validation=validation,
        source="llm_orchestrator.create_backtest_plan.pineforge",
    )
    if validation["status"] == "fail":
        _raise_pine_validation_error(
            message="Backtest plan failed because local Pine validation failed.",
            pine_artifact_id=pine_artifact.id if pine_artifact else None,
            validation_artifact_id=validation_artifact.id if validation_artifact else None,
            validation=validation,
        )
    plan = {
        "kind": "backtest_plan",
        "approval_id": approval_id,
        "approval_status": "pending",
        "requires_user_approval": True,
        "prompt": prompt,
        "strategy_spec": strategy_spec,
        "pine_code": pine_code,
        "pine_code_artifact_id": pine_artifact.id if pine_artifact else None,
        "validation_artifact_id": validation_artifact.id if validation_artifact else None,
        "backtest_config": _user_facing_backtest_config(backtest_config),
        "assumptions": [
            "Local sandbox preview output is review-only evidence.",
            "Public read-only market data and cache are used; no broker, paper, or live execution is allowed.",
            "The model-generated PineScript is statically guarded before local preview.",
        ],
        "warnings": [
            BACKTEST_PREVIEW_BOUNDARY_COPY,
        ],
    }
    artifact = _persist_json_artifact(
        context,
        kind="backtest_plan",
        display_name="Backtest plan",
        relative_path=BACKTEST_PLAN_PATH,
        payload=plan,
        source="llm_orchestrator.create_backtest_plan",
    )
    context.repository.append_run_event(
        context.auth,
        context.run.id,
        "backtest.preview.approval_required",
        {
            "approval_id": approval_id,
            "artifact_id": artifact.id if artifact else None,
            "requires_user_approval": True,
            "status": "pending",
            "symbol": backtest_config["symbol"],
            "timeframe": backtest_config["timeframe"],
            "boundary": BACKTEST_PREVIEW_BOUNDARY_COPY,
        },
    )
    return {
        "approval_id": approval_id,
        "approval_status": "pending",
        "requires_user_approval": True,
        "strategy_spec": strategy_spec,
        "backtest_config": _user_facing_backtest_config(backtest_config),
        "pine_code": pine_code,
        "validation": validation,
        "artifact_id": artifact.id if artifact else None,
        "pine_code_artifact_id": pine_artifact.id if pine_artifact else None,
        "warnings": plan["warnings"],
        "assumptions": plan["assumptions"],
    }


def _run_backtest_preview_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    approval_id = str(arguments.get("approval_id") or "").strip()
    if not approval_id:
        raise ValueError("run_backtest_preview requires an approved backtest approval_id")
    _ensure_backtest_preview_approved(context, approval_id)
    config = _normalize_backtest_config(arguments["backtest_config"])
    pine_code = _required_pineforge_pine(arguments.get("pine_code"), arguments["strategy_spec"])
    validation = validate_pineforge_pine(pine_code, arguments["strategy_spec"])
    pine_artifact, validation_artifact = _persist_pineforge_validation_artifacts(
        context,
        pine_code=pine_code,
        validation=validation,
        source="llm_orchestrator.run_backtest_preview.pineforge",
    )
    if validation["status"] == "fail":
        _raise_pine_validation_error(
            message="Backtest preview failed because local Pine validation failed.",
            pine_artifact_id=pine_artifact.id if pine_artifact else None,
            validation_artifact_id=validation_artifact.id if validation_artifact else None,
            validation=validation,
        )
    queued = _queue_backtest_preview(
        context,
        strategy_spec=arguments["strategy_spec"],
        backtest_config=config,
        metadata={
            "source_tool": "run_backtest_preview",
            "prompt": arguments.get("prompt"),
            "approval_id": approval_id,
        },
        pine_code=pine_code,
        auto_chain=arguments.get("auto_chain") if isinstance(arguments.get("auto_chain"), dict) else None,
    )
    return {
        "run_id": queued["run_id"],
        "job_id": queued["job_id"],
        "status": "queued",
        "mode": RUN_MODE_BACKTEST_PREVIEW,
        "backtest_config": _user_facing_backtest_config(queued["backtest_config"]),
        "evidence_label": "Local sandbox preview evidence only",
        "validation": validation,
    }


def decide_backtest_preview_approval(
    repository: ConversationRepository,
    artifact_store: LocalArtifactStore,
    auth: AuthContext,
    *,
    conversation_id: str,
    approval_id: str,
    decision: str,
) -> dict[str, Any]:
    approval_id = approval_id.strip()
    decision = decision.strip().lower()
    if decision not in {"approved", "rejected"}:
        raise ValueError("decision must be approved or rejected")
    plan_record = _find_backtest_plan_for_approval(repository, artifact_store, auth, conversation_id, approval_id)
    plan = plan_record["plan"]
    source_run = plan_record["run"]
    status = _backtest_preview_approval_status(plan_record["events"], approval_id)
    if status == "queued":
        return {
            "approval_id": approval_id,
            "conversation_id": conversation_id,
            "decision": "approved",
            "status": "queued",
            "run_id": plan_record["queued_run_id"],
            "job_id": plan_record["queued_job_id"],
            "backtest_config": plan.get("backtest_config"),
        }
    if status == "rejected" and decision == "approved":
        raise ValueError("Backtest approval was already rejected")
    if status == "approved" and decision == "rejected":
        raise ValueError("Backtest approval was already approved")
    if decision == "rejected":
        repository.append_run_event(
            auth,
            source_run.id,
            "backtest.preview.rejected",
            {
                "approval_id": approval_id,
                "artifact_id": plan_record["artifact_id"],
                "status": "rejected",
                "boundary": BACKTEST_PREVIEW_BOUNDARY_COPY,
            },
        )
        return {
            "approval_id": approval_id,
            "conversation_id": conversation_id,
            "decision": decision,
            "status": "rejected",
            "backtest_config": plan.get("backtest_config"),
        }

    repository.append_run_event(
        auth,
        source_run.id,
        "backtest.preview.approved",
        {
            "approval_id": approval_id,
            "artifact_id": plan_record["artifact_id"],
            "status": "approved",
            "boundary": BACKTEST_PREVIEW_BOUNDARY_COPY,
        },
    )
    context = ToolExecutionContext(
        repository=repository,
        artifact_store=artifact_store,
        auth=auth,
        run=source_run,
    )
    try:
        queued = _queue_backtest_preview(
            context,
            strategy_spec=plan["strategy_spec"],
            backtest_config=_normalize_backtest_config(plan["backtest_config"]),
            metadata={
                "source_tool": "confirm_backtest_preview",
                "approval_id": approval_id,
                "backtest_plan_artifact_id": plan_record["artifact_id"],
            },
            pine_code=plan["pine_code"],
            auto_chain={"summary_on_complete": True},
        )
    except Exception as exc:
        repository.append_run_event(
            auth,
            source_run.id,
            "backtest.preview.failed",
            {
                "approval_id": approval_id,
                "artifact_id": plan_record["artifact_id"],
                "error": exc.__class__.__name__,
                "message": str(exc),
                "boundary": BACKTEST_PREVIEW_BOUNDARY_COPY,
            },
        )
        raise
    queued_payload = {
        "approval_id": approval_id,
        "artifact_id": plan_record["artifact_id"],
        "child_run_id": queued["run_id"],
        "job_id": queued["job_id"],
        "status": "queued",
        "boundary": BACKTEST_PREVIEW_BOUNDARY_COPY,
    }
    repository.append_run_event(auth, source_run.id, "backtest.preview.queued", queued_payload)
    repository.append_run_event(
        auth,
        source_run.id,
        "chat.auto_chain.waiting_for_backtest",
        {"child_run_id": queued["run_id"], "status": "queued", "approval_id": approval_id},
    )
    return {
        "approval_id": approval_id,
        "conversation_id": conversation_id,
        "decision": decision,
        "status": "queued",
        "run_id": queued["run_id"],
        "job_id": queued["job_id"],
        "backtest_config": _user_facing_backtest_config(queued["backtest_config"]),
    }


def _ensure_backtest_preview_approved(context: ToolExecutionContext, approval_id: str) -> None:
    snapshot = context.repository.get_conversation_state_snapshot(
        context.auth,
        context.run.conversation_id,
        event_limit=500,
    )
    if snapshot is None:
        raise ValueError("Conversation not found for backtest approval")
    status = _backtest_preview_approval_status(snapshot.conversation_run_events, approval_id)
    if status not in {"approved", "queued"}:
        raise ValueError("run_backtest_preview is blocked until the user approves the backtest plan")


def _find_backtest_plan_for_approval(
    repository: ConversationRepository,
    artifact_store: LocalArtifactStore,
    auth: AuthContext,
    conversation_id: str,
    approval_id: str,
) -> dict[str, Any]:
    snapshot = repository.get_conversation_state_snapshot(auth, conversation_id, event_limit=500)
    if snapshot is None:
        raise ValueError("Conversation not found")
    artifacts = list(snapshot.conversation_artifacts)
    page = repository.list_conversation_artifacts_page(
        auth,
        conversation_id,
        limit=500,
        visibility="all",
    )
    if page is not None:
        seen_artifact_ids = {artifact.id for artifact in artifacts}
        for artifact in page.items:
            if artifact.id not in seen_artifact_ids:
                artifacts.append(artifact)
                seen_artifact_ids.add(artifact.id)
    for artifact in artifacts:
        if artifact.kind != "backtest_plan":
            continue
        content = artifact_store.read_content(artifact)
        if not isinstance(content, dict) or content.get("approval_id") != approval_id:
            continue
        run = repository.get_run(auth, artifact.run_id) if artifact.run_id else None
        if run is None:
            raise ValueError("Backtest plan source run not found")
        if not isinstance(content.get("strategy_spec"), dict) or not isinstance(content.get("backtest_config"), dict):
            raise ValueError("Backtest plan artifact is incomplete")
        if not isinstance(content.get("pine_code"), str) or not content["pine_code"].strip():
            raise ValueError("Backtest plan artifact is missing Pine source")
        queued_run_id, queued_job_id = _queued_backtest_preview_for_approval(
            snapshot.conversation_run_events,
            approval_id,
        )
        return {
            "artifact_id": artifact.id,
            "plan": content,
            "run": run,
            "events": snapshot.conversation_run_events,
            "queued_run_id": queued_run_id,
            "queued_job_id": queued_job_id,
        }
    raise ValueError("Backtest approval not found")


def _backtest_preview_approval_status(events: list[Any], approval_id: str) -> str:
    status = "pending"
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if payload.get("approval_id") != approval_id:
            continue
        if event.type == "backtest.preview.rejected":
            status = "rejected"
        elif event.type == "backtest.preview.queued":
            status = "queued"
        elif event.type == "backtest.preview.approved" and status != "queued":
            status = "approved"
        elif event.type == "backtest.preview.approval_required" and status == "pending":
            status = "pending"
    return status


def _queued_backtest_preview_for_approval(events: list[Any], approval_id: str) -> tuple[str | None, str | None]:
    for event in reversed(events):
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "backtest.preview.queued" and payload.get("approval_id") == approval_id:
            child_run_id = payload.get("child_run_id")
            job_id = payload.get("job_id")
            return (
                child_run_id if isinstance(child_run_id, str) else None,
                job_id if isinstance(job_id, str) else None,
            )
    return None, None


def _run_backtest_variant_lab_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    if len(arguments["variants"]) > BACKTEST_MAX_VARIANTS:
        raise ValueError(f"Backtest variant limit exceeded: {BACKTEST_MAX_VARIANTS}")
    group_id = opaque_id("variant")
    base_config = _normalize_backtest_config(arguments["base_backtest_config"])
    prepared_variants = [
        (
            index,
            variant,
            variant.get("strategy_spec") or arguments["strategy_spec"],
            _normalize_backtest_config({**base_config, **variant.get("backtest_config", {})}),
        )
        for index, variant in enumerate(arguments["variants"])
    ]
    cache_keys = [_backtest_cache_key(config) for _index, _variant, _variant_spec, config in prepared_variants]
    shared_cache_key = cache_keys[0] if len(set(cache_keys)) == 1 else None
    queued_variants: list[dict[str, Any]] = []
    for (index, variant, variant_spec, config), variant_cache_key in zip(prepared_variants, cache_keys):
        variant_pine_code = _required_pineforge_pine(variant.get("pine_code") or arguments.get("pine_code"), variant_spec)
        queued = _queue_backtest_preview(
            context,
            strategy_spec=variant_spec,
            backtest_config=config,
            metadata={
                "source_tool": "run_backtest_variant_lab",
                "prompt": arguments["prompt"],
                "variant_group_id": group_id,
                "variant_index": index,
                "variant_name": variant["name"],
                "shared_cache_key": shared_cache_key,
                "variant_cache_key": variant_cache_key,
            },
            pine_code=variant_pine_code,
        )
        queued_variants.append(
            {
                "name": variant["name"],
                "run_id": queued["run_id"],
                "job_id": queued["job_id"],
                "status": "queued",
                "backtest_config": _user_facing_backtest_config(config),
                "cache_key": variant_cache_key,
            }
        )
    comparison = {
        "kind": "backtest_variant_comparison",
        "variant_group_id": group_id,
        "shared_cache_key": shared_cache_key,
        "shared_cache": shared_cache_key is not None,
        "prompt": arguments["prompt"],
        "base_backtest_config": _user_facing_backtest_config(base_config),
        "variants": queued_variants,
        "warnings": [
            "Variant results are comparable only after every queued child run completes.",
            "Local sandbox preview output is review-only evidence, not TradingView official validation, broker proof, or live-trading proof.",
        ],
    }
    artifact = _persist_json_artifact(
        context,
        kind="backtest_variant_comparison",
        display_name="Backtest variant comparison",
        relative_path=f"backtest/variant-lab-{group_id}.json",
        payload=comparison,
        source="llm_orchestrator.run_backtest_variant_lab",
    )
    return {
        "variant_group_id": group_id,
        "shared_cache_key": shared_cache_key,
        "shared_cache": shared_cache_key is not None,
        "artifact_id": artifact.id if artifact else None,
        "variants": queued_variants,
    }


def _get_backtest_summary_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    requested_run_id = arguments["run_id"]
    run_id = context.repository.resolve_backtest_report_run_id(context.auth, context.run.conversation_id, requested_run_id)
    if run_id is None:
        return {"status": "not_found", "run_id": requested_run_id}
    summary = context.repository.get_backtest_summary(context.auth, run_id)
    if summary is None:
        return {"status": "not_found", "run_id": requested_run_id}
    return {
        "status": "ok",
        "run_id": run_id,
        "requested_run_id": requested_run_id,
        "fallback_used": run_id != requested_run_id,
        "summary": _user_facing_backtest_summary(summary),
    }


def _query_backtest_trades_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    requested_run_id = arguments["run_id"]
    run_id = context.repository.resolve_backtest_report_run_id(context.auth, context.run.conversation_id, requested_run_id)
    if run_id is None:
        return {"status": "not_found", "run_id": requested_run_id, "trades": []}
    trades = context.repository.query_backtest_trades(
        context.auth,
        run_id,
        bucket=arguments.get("bucket"),
        limit=int(arguments.get("limit") or 20),
    )
    if trades is None:
        return {"status": "not_found", "run_id": requested_run_id, "trades": []}
    return {
        "status": "ok",
        "run_id": run_id,
        "requested_run_id": requested_run_id,
        "fallback_used": run_id != requested_run_id,
        "trades": trades,
    }


def _build_robustness_report_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    requested_run_id = arguments["run_id"]
    run_id = context.repository.resolve_backtest_report_run_id(context.auth, context.run.conversation_id, requested_run_id)
    if run_id is None:
        return {"status": "not_found", "run_id": requested_run_id}
    summary = context.repository.get_backtest_summary(context.auth, run_id)
    if summary is None:
        return {"status": "not_found", "run_id": requested_run_id}
    sample_trades = context.repository.query_backtest_trades(context.auth, run_id, bucket="sample", limit=20) or []
    top_losers = context.repository.query_backtest_trades(context.auth, run_id, bucket="top_loser", limit=5) or []
    top_winners = context.repository.query_backtest_trades(context.auth, run_id, bucket="top_winner", limit=5) or []
    equity_summary = context.repository.get_backtest_equity_summary(context.auth, run_id)
    report = _build_robustness_payload(
        run_id=run_id,
        requested_run_id=requested_run_id,
        summary=_user_facing_backtest_summary(summary),
        sample_trades=sample_trades,
        top_losers=top_losers,
        top_winners=top_winners,
        equity_summary=_user_facing_backtest_summary(equity_summary) if equity_summary is not None else None,
    )
    artifact = _persist_json_artifact(
        context,
        kind=ROBUSTNESS_REPORT_ARTIFACT_KIND,
        display_name="Robustness Report",
        relative_path=f"backtest/robustness-report-{opaque_id('robust')}.json",
        payload=report,
        source="llm_tools.build_robustness_report",
    )
    return {
        "status": "ok",
        "run_id": run_id,
        "requested_run_id": requested_run_id,
        "fallback_used": run_id != requested_run_id,
        "artifact_id": artifact.id if artifact else None,
        "robustness_report": report,
    }


def _get_equity_curve_sample_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    requested_run_id = arguments["run_id"]
    run_id = context.repository.resolve_backtest_report_run_id(context.auth, context.run.conversation_id, requested_run_id)
    if run_id is None:
        return {"status": "not_found", "run_id": requested_run_id}
    summary = context.repository.get_backtest_equity_summary(context.auth, run_id)
    if summary is None:
        return {"status": "not_found", "run_id": requested_run_id}
    return {
        "status": "ok",
        "run_id": run_id,
        "requested_run_id": requested_run_id,
        "fallback_used": run_id != requested_run_id,
        "equity_summary": summary,
    }


def _draft_bot_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    strategy_spec = arguments.get("strategy_spec") if isinstance(arguments.get("strategy_spec"), dict) else None
    broker_connection_id = _string_or_none(arguments.get("broker_connection_id"))
    account_id = _string_or_none(arguments.get("account_id"))
    risk_policy_id = _string_or_none(arguments.get("risk_policy_id"))
    try:
        draft = build_bot_proposal_create_input(
            auth=context.auth,
            repository=context.repository,
            artifact_store=context.artifact_store,
            draft=BotProposalDraftInput(
                strategy_artifact_id=_string_or_none(arguments.get("strategy_artifact_id")),
                run_id=_string_or_none(arguments.get("run_id")),
                fallback_run_id=context.run.id,
                fallback_conversation_id=context.run.conversation_id,
                strategy_spec=strategy_spec,
                strategy_id=_string_or_none(arguments.get("strategy_id")),
                strategy_name=_string_or_none(arguments.get("strategy_name")),
                manifest=arguments.get("manifest") if isinstance(arguments.get("manifest"), dict) else {},
                data_subscriptions=arguments.get("data_subscriptions") if isinstance(arguments.get("data_subscriptions"), list) else [],
                broker_connection_id=broker_connection_id,
                account_id=account_id,
                risk_policy_id=risk_policy_id,
                readiness_checks=[item for item in arguments.get("readiness_checks", []) if isinstance(item, str)],
            ),
        )
    except BotProposalSourceNotFoundError as exc:
        return {"status": "not_found", "source": exc.source}
    except BotProposalArtifactUnreadableError as exc:
        return {"status": "error", "error": exc.__class__.__name__, "message": "Could not read strategy artifact"}
    proposal = context.repository.create_bot_proposal(
        context.auth,
        draft.create_input,
    )
    return {
        "status": proposal.status,
        "proposal_id": proposal.id,
        "bot_proposal": _bot_proposal_tool_payload(proposal),
        "missing_inputs": draft.missing_inputs,
        "next_action": "review_setup" if not draft.missing_inputs else "collect_missing_inputs",
    }


def _get_bot_status_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    proposal = None
    proposal_id = _string_or_none(arguments.get("proposal_id"))
    if proposal_id:
        proposal = context.repository.get_bot_proposal(context.auth, proposal_id)
    runtime_id = _string_or_none(arguments.get("runtime_id")) or (proposal.runtime_id if proposal else None)
    runtime = context.repository.get_nautilus_runtime(context.auth, runtime_id) if runtime_id else None
    if proposal is None and runtime is None:
        return {"status": "not_found"}
    return {
        "status": "ok",
        "proposal": _bot_proposal_tool_payload(proposal) if proposal else None,
        "runtime": _bot_runtime_tool_payload(runtime) if runtime else None,
    }


def _list_bots_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    limit = min(50, max(1, int(arguments.get("limit") or 20)))
    runtimes = context.repository.list_nautilus_runtimes(context.auth, mode="paper", limit=limit)
    return {"status": "ok", "bots": [_bot_runtime_tool_payload(runtime) for runtime in runtimes]}


def _list_bot_events_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    runtime_id = str(arguments["runtime_id"])
    events = context.repository.list_nautilus_runtime_events(
        context.auth,
        runtime_id,
        limit=min(100, max(1, int(arguments.get("limit") or 20))),
    )
    if events is None:
        return {"status": "not_found", "runtime_id": runtime_id, "events": []}
    return {
        "status": "ok",
        "runtime_id": runtime_id,
        "events": [
            {"sequence": event.sequence, "type": event.type, "payload": event.payload, "created_at": event.created_at.isoformat()}
            for event in events
        ],
    }


def _bot_proposal_tool_payload(proposal: Any) -> dict[str, Any]:
    return {
        "id": proposal.id,
        "status": proposal.status,
        "strategy_id": proposal.strategy_id,
        "strategy_name": proposal.strategy_name,
        "broker_connection_id": proposal.broker_connection_id,
        "account_id": proposal.account_id,
        "risk_policy_id": proposal.risk_policy_id,
        "source_run_id": proposal.source_run_id,
        "source_artifact_ids": proposal.source_artifact_ids,
        "data_subscriptions": redact_value(proposal.data_subscriptions_json),
        "readiness": redact_value(proposal.readiness_checks_json),
        "missing_inputs": redact_value(proposal.missing_inputs_json),
        "runtime_id": proposal.runtime_id,
        "no_broker_execution": True,
    }


def _bot_runtime_tool_payload(runtime: Any) -> dict[str, Any]:
    return {
        "id": runtime.id,
        "name": runtime.manifest_json.get("name") if isinstance(runtime.manifest_json, dict) else runtime.id,
        "state": runtime.state,
        "desired_state": runtime.desired_state,
        "kill_switch_active": runtime.kill_switch_active,
        "last_heartbeat_at": runtime.last_heartbeat_at.isoformat() if runtime.last_heartbeat_at else None,
        "last_error": redact_value(runtime.last_error_json),
        "data_subscriptions": redact_value(runtime.data_subscriptions_json),
        "no_broker_execution": runtime.mode == "paper",
    }


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _build_robustness_payload(
    *,
    run_id: str,
    requested_run_id: str,
    summary: dict[str, Any],
    sample_trades: list[dict[str, Any]],
    top_losers: list[dict[str, Any]],
    top_winners: list[dict[str, Any]],
    equity_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    assumptions = summary.get("assumptions") if isinstance(summary.get("assumptions"), list) else []
    warnings = summary.get("warnings") if isinstance(summary.get("warnings"), list) else []
    trade_count = _first_number(metrics, "trade_count", "total_trades", "closed_trades")
    win_rate = _first_number(metrics, "win_rate", "win_rate_pct", "win_rate_percent")
    max_drawdown = _first_number(metrics, "max_drawdown_pct", "drawdown_pct", "max_drawdown_percent")
    profit_factor = _first_number(metrics, "profit_factor")
    net_profit_pct = _first_number(metrics, "net_profit_pct", "net_profit_percent", "return_pct")
    fee_bps = _first_number(metrics, "fee_bps", "fees_bps")
    slippage_bps = _first_number(metrics, "slippage_bps")
    checks = _robustness_checks(
        trade_count=trade_count,
        win_rate=win_rate,
        max_drawdown=max_drawdown,
        profit_factor=profit_factor,
        net_profit_pct=net_profit_pct,
        assumptions=assumptions,
        warnings=warnings,
        equity_summary=equity_summary,
    )
    recommendation = _robustness_recommendation(checks)
    return {
        "kind": ROBUSTNESS_REPORT_ARTIFACT_KIND,
        "schema_version": 1,
        "run_id": run_id,
        "requested_run_id": requested_run_id,
        "boundary": BACKTEST_PREVIEW_BOUNDARY_COPY,
        "summary": {
            "symbol": summary.get("symbol"),
            "signal_timeframe": summary.get("signal_timeframe"),
            "candle_timeframe": summary.get("candle_timeframe"),
            "evidence_label": summary.get("evidence_label"),
            "reproducibility_hash": summary.get("reproducibility_hash"),
        },
        "metrics": {
            "trade_count": trade_count,
            "win_rate": win_rate,
            "max_drawdown_pct": max_drawdown,
            "profit_factor": profit_factor,
            "net_profit_pct": net_profit_pct,
        },
        "assumption_review": {
            "fee_bps": fee_bps,
            "slippage_bps": slippage_bps,
            "assumptions": assumptions,
            "warnings": warnings,
        },
        "checks": checks,
        "trade_samples": {
            "sample": sample_trades[:20],
            "top_losers": top_losers[:5],
            "top_winners": top_winners[:5],
        },
        "equity_review": {
            "available": equity_summary is not None,
            "sample_resolution": equity_summary.get("sample_resolution") if isinstance(equity_summary, dict) else None,
            "drawdown_windows": equity_summary.get("drawdown_windows", [])[:5] if isinstance(equity_summary, dict) else [],
            "monthly_returns": equity_summary.get("monthly_returns", [])[:12] if isinstance(equity_summary, dict) else [],
        },
        "recommendation": recommendation,
    }


def _robustness_checks(
    *,
    trade_count: float | None,
    win_rate: float | None,
    max_drawdown: float | None,
    profit_factor: float | None,
    net_profit_pct: float | None,
    assumptions: list[Any],
    warnings: list[Any],
    equity_summary: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    checks = [
        _robustness_check(
            "sample_size",
            "warn" if trade_count is None or trade_count < 100 else "pass",
            "Trade sample is thin; require more data or OOS validation." if trade_count is None or trade_count < 100 else "Trade sample is large enough for initial review.",
            observed=trade_count,
        ),
        _robustness_check(
            "drawdown",
            "fail" if max_drawdown is not None and max_drawdown >= 50 else "warn" if max_drawdown is None or max_drawdown >= 25 else "pass",
            "Drawdown is extreme for a review candidate." if max_drawdown is not None and max_drawdown >= 50 else "Drawdown needs review." if max_drawdown is None or max_drawdown >= 25 else "Drawdown is below the warning threshold.",
            observed=max_drawdown,
        ),
        _robustness_check(
            "win_rate",
            "warn" if win_rate is None or win_rate <= 0 or win_rate >= 100 else "pass",
            "Win rate is missing or suspiciously extreme." if win_rate is None or win_rate <= 0 or win_rate >= 100 else "Win rate is not obviously degenerate.",
            observed=win_rate,
        ),
        _robustness_check(
            "profit_factor",
            "fail" if profit_factor is not None and profit_factor < 1 else "warn" if profit_factor is None or profit_factor > 5 else "pass",
            "Profit factor is below 1." if profit_factor is not None and profit_factor < 1 else "Profit factor is missing or unusually high." if profit_factor is None or profit_factor > 5 else "Profit factor is in a plausible range.",
            observed=profit_factor,
        ),
        _robustness_check(
            "net_profit",
            "fail" if net_profit_pct is not None and net_profit_pct < 0 else "warn" if net_profit_pct is None else "pass",
            "Net profit is negative." if net_profit_pct is not None and net_profit_pct < 0 else "Net profit is missing." if net_profit_pct is None else "Net profit is positive.",
            observed=net_profit_pct,
        ),
        _robustness_check(
            "fees_slippage",
            "warn" if not assumptions else "pass",
            "Fee/slippage assumptions are not explicit in the indexed summary." if not assumptions else "Assumptions are present; verify they match intended execution costs.",
            observed=len(assumptions),
        ),
        _robustness_check(
            "equity_trace",
            "warn" if equity_summary is None else "pass",
            "Equity summary is unavailable for drawdown/monthly-return review." if equity_summary is None else "Equity summary is available for review.",
            observed=equity_summary is not None,
        ),
    ]
    if warnings:
        checks.append(_robustness_check("source_warnings", "warn", "Backtest report contains warnings that require review.", observed=len(warnings)))
    return checks


def _robustness_check(check_id: str, status: str, message: str, *, observed: Any) -> dict[str, Any]:
    return {"id": check_id, "status": status, "message": message, "observed": observed}


def _robustness_recommendation(checks: list[dict[str, Any]]) -> str:
    statuses = {check.get("status") for check in checks}
    if "fail" in statuses:
        return "reject_preview"
    if "warn" in statuses:
        return "needs_more_evidence"
    return "candidate_for_review"


def _first_number(values: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = values.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip().rstrip("%"))
            except ValueError:
                continue
    return None


def _build_backtest_config(
    prompt: str,
    *,
    strategy_spec: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = dict(BACKTEST_DEFAULT_CONFIG)
    spec = strategy_spec or {}
    if isinstance(spec.get("symbol"), str) and spec["symbol"].strip():
        config["symbol"] = spec["symbol"].strip().upper()
    if isinstance(spec.get("timeframe"), str) and spec["timeframe"].strip():
        config["timeframe"] = spec["timeframe"].strip()
    config.update(_extract_backtest_prompt_config(prompt))
    if overrides:
        config.update(overrides)
    return _normalize_backtest_config(config)


def _extract_backtest_prompt_config(prompt: str) -> dict[str, Any]:
    lowered = prompt.lower()
    extracted: dict[str, Any] = {}
    symbol_match = re.search(r"\b([A-Z]{2,12})(?:[/:-]?)(USDT|USD|BTC|ETH)\b", prompt.upper())
    if symbol_match:
        extracted["symbol"] = f"{symbol_match.group(1)}/{symbol_match.group(2)}"
    timeframe_match = re.search(r"\b(1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d|1w)\b", lowered)
    if timeframe_match:
        extracted["timeframe"] = timeframe_match.group(1)
    for exchange in BACKTEST_OHLCV_EXCHANGES:
        if re.search(rf"\b{re.escape(exchange)}\b", lowered):
            extracted["exchange"] = exchange
            break
    dates = re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", prompt)
    if dates:
        extracted["start"] = dates[0]
    if len(dates) > 1:
        extracted["end"] = dates[1]
    capital_match = re.search(r"(?:capital|initial|vốn|von)\D{0,12}(\d+(?:[_,]\d{3})*(?:\.\d+)?)", lowered)
    if capital_match:
        extracted["initial_capital"] = float(capital_match.group(1).replace(",", "").replace("_", ""))
    return extracted


def _normalize_backtest_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = {**BACKTEST_DEFAULT_CONFIG, **config}
    normalized["engine"] = str(normalized.get("engine") or backtest_default_engine()).strip().lower()
    if normalized["engine"] == BACKTEST_ENGINE_PINEFORGE and os.getenv("BACKTEST_PINEFORGE_ENABLED") != "1":
        raise ValidationError("backtest preview is disabled")
    normalized["symbol"] = str(normalized["symbol"]).strip().upper()
    normalized["exchange"] = str(normalized.get("exchange") or BACKTEST_OHLCV_DEFAULT_EXCHANGE).strip().lower()
    normalized["timeframe"] = str(normalized["timeframe"]).strip().lower()
    normalized["candle_timeframe"] = str(normalized.get("candle_timeframe") or "1m").strip().lower()
    normalized["start"] = str(normalized["start"]).strip()
    normalized["end"] = str(normalized["end"]).strip()
    normalized["initial_capital"] = float(normalized["initial_capital"])
    normalized["fee_bps"] = float(normalized["fee_bps"])
    normalized["slippage_bps"] = float(normalized["slippage_bps"])
    normalized["data_source"] = "public-readonly-cache"
    validate(instance=normalized, schema=BACKTEST_CONFIG_SCHEMA)
    if any(ord(character) < 32 for character in normalized["symbol"]):
        raise ValidationError("symbol must be a printable value")
    for key in ("initial_capital", "fee_bps", "slippage_bps"):
        if not math.isfinite(normalized[key]):
            raise ValidationError(f"{key} must be finite")
    start = _parse_backtest_datetime(normalized["start"], "start")
    end = _parse_backtest_datetime(normalized["end"], "end")
    if end <= start:
        raise ValidationError("end must be after start")
    return normalized


def _user_facing_backtest_config(config: dict[str, Any]) -> dict[str, Any]:
    hidden_keys = {"data_source", "engine"}
    return {key: value for key, value in config.items() if key not in hidden_keys}


def _user_facing_backtest_summary(value: Any) -> Any:
    hidden_keys = {
        "engine",
        "execution_semantics",
        "pineforge_runtime",
        "runner",
        "runtime_boundary",
    }
    if isinstance(value, dict):
        return {
            key: _user_facing_backtest_summary(
                "Local sandbox preview evidence" if key == "evidence_label" else item
            )
            for key, item in value.items()
            if key not in hidden_keys
        }
    if isinstance(value, list):
        return [_user_facing_backtest_summary(item) for item in value]
    if isinstance(value, str):
        return _user_facing_backtest_text(value)
    return value


def _user_facing_backtest_text(value: str) -> str:
    return (
        value.replace("PineForge local Pine preview evidence only", "Local sandbox preview evidence only")
        .replace("PineForge local Pine preview evidence", "Local sandbox preview evidence")
        .replace("PineForge Preview", "Backtest Preview")
        .replace("PineForge local Pine preview", "local sandbox preview")
        .replace("PineForge output", "Local sandbox preview output")
        .replace("PineForge compile/backtest", "local preview")
        .replace("pineforge-engine", "local preview")
        .replace("pineforge-runner", "local preview")
        .replace("PineForge", "local preview")
    )


def _required_pineforge_pine(pine_code: Any, strategy_spec: dict[str, Any]) -> str:
    if not isinstance(pine_code, str) or not pine_code.strip():
        raise ValueError("Backtest preview requires PineScript v6 strategy source")
    if strategy_spec.get("script_type") != "strategy":
        raise ValueError("Backtest preview requires a Pine strategy, not an indicator")
    return pine_code.strip()


def _parse_backtest_datetime(value: str, field_name: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError as exc:
        raise ValidationError(f"{field_name} must be an ISO date or datetime") from exc


def _queue_backtest_preview(
    context: ToolExecutionContext,
    *,
    strategy_spec: dict[str, Any],
    backtest_config: dict[str, Any],
    metadata: dict[str, Any],
    pine_code: str | None = None,
    auto_chain: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = _normalize_backtest_config(backtest_config)
    pine_code = _required_pineforge_pine(pine_code, strategy_spec)
    run = context.repository.create_run(
        context.auth,
        context.run.conversation_id,
        status="queued",
        mode=RUN_MODE_BACKTEST_PREVIEW,
        request_id=opaque_id("req"),
    )
    if run is None:
        raise ValueError("Conversation not found for backtest-preview run")
    context.repository.create_strategy_spec(context.auth, run.id, strategy_spec, "backtest-preview.v1")
    job = context.repository.create_run_job(
        context.auth,
        run.id,
        job_type=RUN_MODE_BACKTEST_PREVIEW,
        payload_json={
            "strategy_spec": strategy_spec,
            "pine_code": pine_code,
            "backtest_config": config,
            "runtime": backtest_runtime_boundary(config["engine"]),
            "limits": backtest_job_limits_for_tier(context.auth.user_tier),
            "chat_tool": {key: value for key, value in metadata.items() if value is not None},
            "auto_chain": _auto_chain_payload(context, auto_chain),
        },
    )
    if job is None:
        context.repository.set_run_status(context.auth, run.id, "failed")
        context.repository.append_run_event(
            context.auth,
            run.id,
            "backtest.failed",
            {"error_code": "job_create_failed", "mode": RUN_MODE_BACKTEST_PREVIEW},
        )
        raise ValueError("Could not queue backtest-preview job")
    event_payload = {
        "job_id": job.id,
        "job_type": job.job_type,
        "mode": RUN_MODE_BACKTEST_PREVIEW,
        "exchange": config["exchange"],
        "symbol": config["symbol"],
        "timeframe": config["timeframe"],
        "signal_timeframe": config["timeframe"],
        "candle_timeframe": config["candle_timeframe"],
        **{key: value for key, value in metadata.items() if value is not None},
    }
    context.repository.append_run_event(context.auth, run.id, "backtest.queued", event_payload)
    context.repository.append_run_event(
        context.auth,
        context.run.id,
        "backtest.queued",
        {"child_run_id": run.id, **event_payload},
    )
    return {"run_id": run.id, "job_id": job.id, "backtest_config": config, "pine_code": pine_code}


def _auto_chain_payload(context: ToolExecutionContext, payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload or payload.get("summary_on_complete") is not True:
        return None
    return {
        "summary_on_complete": True,
        "source_run_id": context.run.id,
        "conversation_id": context.run.conversation_id,
    }


def _backtest_cache_key(config: dict[str, Any]) -> str:
    key_payload = {
        "data_source": config["data_source"],
        "exchange": config["exchange"],
        "symbol": config["symbol"],
        "timeframe": config["timeframe"],
        "candle_timeframe": config["candle_timeframe"],
        "start": config["start"],
        "end": config["end"],
    }
    serialized = json.dumps(key_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _persist_text_artifact(
    context: ToolExecutionContext,
    *,
    kind: str,
    mime_type: str,
    display_name: str,
    relative_path: str,
    content: str,
    source: str,
):
    path = context.artifact_store.run_dir(context.run.id) / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    artifact = context.repository.create_artifact(
        context.auth,
        context.run.id,
        kind=kind,
        mime_type=mime_type,
        display_name=display_name,
        storage_key=context.artifact_store.storage_key(context.run.id, relative_path),
        metadata_json={"source": source},
    )
    if artifact is not None:
        context.repository.append_run_event(
            context.auth,
            context.run.id,
            "artifact.created",
            {"artifact_id": artifact.id, "kind": artifact.kind, "display_name": artifact.display_name},
        )
    return artifact


def _persist_pineforge_validation_artifacts(
    context: ToolExecutionContext,
    *,
    pine_code: str,
    validation: dict[str, Any],
    source: str,
):
    pine_artifact = _persist_text_artifact(
        context,
        kind="pine_file",
        mime_type="text/plain",
        display_name="strategy.pine",
        relative_path=PINE_STRATEGY_PATH,
        content=pine_code,
        source=source,
    )
    validation_artifact = _persist_json_artifact(
        context,
        kind="validation_report",
        display_name="validation.json",
        relative_path=BACKTEST_PINEFORGE_VALIDATION_PATH,
        payload=validation,
        source=source,
    )
    return pine_artifact, validation_artifact


def _raise_pine_validation_error(
    *,
    message: str,
    pine_artifact_id: str | None,
    validation_artifact_id: str | None,
    validation: dict[str, Any],
) -> None:
    summary = _validation_failure_summary(validation)
    raise ToolExecutionError(
        code="pine_validation_failed",
        message=message,
        dimension="workflow",
        retryable=False,
        details={
            "pine_code_artifact_id": pine_artifact_id,
            "validation_artifact_id": validation_artifact_id,
            **summary,
        },
    )


def _validation_failure_summary(validation: dict[str, Any]) -> dict[str, Any]:
    errors = validation.get("errors")
    diagnostics = validation.get("diagnostics")
    issue_count = 0
    if isinstance(errors, list):
        issue_count += len(errors)
    if isinstance(diagnostics, list):
        issue_count += len(diagnostics)
    summary: dict[str, Any] = {
        "validation_status": validation.get("status"),
        "validation_issue_count": issue_count,
    }
    first_issue = _first_validation_issue(errors) or _first_validation_issue(diagnostics)
    if first_issue:
        summary["validation_first_issue"] = first_issue
    return summary


def _first_validation_issue(value: Any) -> str | None:
    if not isinstance(value, list) or not value:
        return None
    first = value[0]
    if isinstance(first, str):
        return first[:240]
    if isinstance(first, dict):
        for key in ("message", "error", "reason"):
            item = first.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()[:240]
    return None


def _persist_json_artifact(
    context: ToolExecutionContext,
    *,
    kind: str,
    display_name: str,
    relative_path: str,
    payload: dict[str, Any],
    source: str,
):
    path = context.artifact_store.run_dir(context.run.id) / relative_path
    write_json(path, payload)
    artifact = context.repository.create_artifact(
        context.auth,
        context.run.id,
        kind=kind,
        mime_type="application/json",
        display_name=display_name,
        storage_key=context.artifact_store.storage_key(context.run.id, relative_path),
        metadata_json={"source": source},
    )
    if artifact is not None:
        context.repository.append_run_event(
            context.auth,
            context.run.id,
            "artifact.created",
            {"artifact_id": artifact.id, "kind": artifact.kind, "display_name": artifact.display_name},
        )
    return artifact


TOOL_HANDLERS: dict[str, ToolHandler] = {
    "generate_pine": _generate_pine_tool,
    "create_mql5_design": _create_mql5_design_tool,
    "static_validate": _static_validate_tool,
    "parallel_review": _parallel_review_tool,
    "knowledge_check": _knowledge_check_tool,
    "knowledge_proposal": _knowledge_proposal_tool,
    "create_backtest_plan": _create_backtest_plan_tool,
    "run_backtest_preview": _run_backtest_preview_tool,
    "run_backtest_variant_lab": _run_backtest_variant_lab_tool,
    "get_backtest_summary": _get_backtest_summary_tool,
    "query_backtest_trades": _query_backtest_trades_tool,
    "build_robustness_report": _build_robustness_report_tool,
    "get_equity_curve_sample": _get_equity_curve_sample_tool,
    "draft_bot": _draft_bot_tool,
    "get_bot_status": _get_bot_status_tool,
    "list_bots": _list_bots_tool,
    "list_bot_events": _list_bot_events_tool,
}


def tool_catalog_consistency_errors() -> list[str]:
    definition_names = set(TOOL_DEFINITIONS)
    handler_names = set(TOOL_HANDLERS)
    action_tool_names = action_registry_backend_tool_ids()
    registry_provider_names, registry_errors = _provider_tool_names_from_registry()
    errors: list[str] = []
    missing_handlers = sorted(definition_names - handler_names)
    if missing_handlers:
        errors.append(f"Tool definitions without handlers: {', '.join(missing_handlers)}")
    missing_definitions = sorted(handler_names - definition_names)
    if missing_definitions:
        errors.append(f"Tool handlers without definitions: {', '.join(missing_definitions)}")
    missing_registry_definitions = sorted(action_tool_names - definition_names)
    if missing_registry_definitions:
        errors.append(f"Action registry backend tools without definitions: {', '.join(missing_registry_definitions)}")
    missing_registry_handlers = sorted(action_tool_names - handler_names)
    if missing_registry_handlers:
        errors.append(f"Action registry backend tools without handlers: {', '.join(missing_registry_handlers)}")
    errors.extend(registry_errors)
    missing_provider_registry = sorted(definition_names - registry_provider_names)
    if missing_provider_registry:
        errors.append(f"Provider tool definitions missing from tool registry: {', '.join(missing_provider_registry)}")
    registry_without_definitions = sorted(registry_provider_names - definition_names)
    if registry_without_definitions:
        errors.append(f"Provider-exposed registry tools without definitions: {', '.join(registry_without_definitions)}")
    missing_handler_registry = sorted(handler_names - registry_provider_names)
    if missing_handler_registry:
        errors.append(f"Tool handlers missing from tool registry: {', '.join(missing_handler_registry)}")
    return errors


def _provider_tool_names_from_registry() -> tuple[set[str], list[str]]:
    registry = load_tool_registry(repo_root() / "configs" / "tool-registry.yaml")
    provider_names: set[str] = set()
    errors: list[str] = []
    for entry in registry.get("tools", []):
        if not isinstance(entry, dict) or entry.get("provider_exposed") is not True:
            continue
        tool_id = str(entry.get("id") or "").strip()
        names: list[str] = []
        backend_handler = str(entry.get("backend_handler") or "").strip()
        if backend_handler:
            names.append(backend_handler)
        aliases = entry.get("aliases", [])
        if isinstance(aliases, list):
            names.extend(str(alias).strip() for alias in aliases if str(alias).strip())
        names = list(dict.fromkeys(names))
        if not names:
            errors.append(f"Provider-exposed registry tool {tool_id or '<unknown>'} has no backend_handler or aliases")
            continue
        provider_names.update(names)
    return provider_names, errors


def execute_tool(tool_name: str, arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    handler = TOOL_HANDLERS.get(tool_name)
    if handler is not None:
        return handler(arguments, context)
    raise ValueError(f"Unsupported tool: {tool_name}")


def compact_tool_output(output: dict[str, Any]) -> dict[str, Any]:
    if "knowledge_context" in output and isinstance(output["knowledge_context"], dict):
        return {"knowledge_context_summary": _knowledge_context_summary(output["knowledge_context"])}
    if "trades" in output and isinstance(output["trades"], list):
        return {
            "status": output.get("status"),
            "run_id": output.get("run_id"),
            "requested_run_id": output.get("requested_run_id"),
            "fallback_used": output.get("fallback_used"),
            "trades": output["trades"][:50],
            "truncated": len(output["trades"]) > 50,
        }
    encoded = json.dumps(output, ensure_ascii=False)
    if len(encoded) <= 4000:
        return output
    return {"truncated": True, "preview": encoded[:4000]}


def _knowledge_context_summary(context: dict[str, Any]) -> dict[str, Any]:
    internal_docs = context.get("internal_docs") if isinstance(context.get("internal_docs"), list) else []
    external_refs = context.get("external_refs") if isinstance(context.get("external_refs"), list) else []
    retrieved_chunks = context.get("retrieved_chunks") if isinstance(context.get("retrieved_chunks"), list) else []
    missing_context = context.get("missing_context") if isinstance(context.get("missing_context"), list) else []
    return {
        "mode": context.get("mode"),
        "store": context.get("store"),
        "status": context.get("knowledge_health_status") or context.get("retrieval_cache_status") or "ready",
        "internal_doc_ids": [doc.get("id") for doc in internal_docs if isinstance(doc, dict) and doc.get("id")],
        "external_source_ids": [source.get("id") for source in external_refs if isinstance(source, dict) and source.get("id")],
        "retrieved_chunk_count": len(retrieved_chunks),
        "sources": _knowledge_user_facing_sources(internal_docs, external_refs, retrieved_chunks),
        "missing_context": missing_context,
    }


def _knowledge_user_facing_sources(
    internal_docs: list[Any],
    external_refs: list[Any],
    retrieved_chunks: list[Any],
) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_source(source: dict[str, str]) -> None:
        source_id = source.get("id")
        if not source_id or source_id in seen or len(sources) >= MAX_USER_FACING_SOURCES:
            return
        seen.add(source_id)
        sources.append(source)

    for source in external_refs:
        if not isinstance(source, dict):
            continue
        source_id = _safe_source_text(source.get("id"))
        url = _safe_source_text(source.get("url"))
        if not source_id or not url:
            continue
        add_source(
            {
                "id": source_id,
                "label": "External source",
                "title": _source_title(source, source_id),
                "type": "external",
                "url": url,
            }
        )

    for doc in internal_docs:
        if not isinstance(doc, dict):
            continue
        source_id = _safe_source_text(doc.get("id"))
        if not source_id:
            continue
        add_source(
            {
                "id": source_id,
                "label": "Internal reference",
                "title": _source_title(doc, source_id),
                "type": "internal",
            }
        )

    for chunk in retrieved_chunks:
        if not isinstance(chunk, dict):
            continue
        source_id = _safe_source_text(chunk.get("source_id") or chunk.get("item_id"))
        if not source_id:
            continue
        add_source(
            {
                "id": source_id,
                "label": "Internal reference",
                "title": _source_title(chunk, source_id),
                "type": "internal",
            }
        )

    return sources


def _source_title(source: dict[str, Any], fallback_id: str) -> str:
    for key in ("title", "parent_title", "section_title", "path"):
        value = _safe_source_text(source.get(key))
        if value:
            return value.rsplit("/", maxsplit=1)[-1].replace("-", " ").replace("_", " ").removesuffix(".md").title()
    return fallback_id.replace("-", " ").replace("_", " ").title()


def _safe_source_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None
