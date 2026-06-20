import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from jsonschema import ValidationError, validate

from strategy_codebot.knowledge_context import build_knowledge_context
from strategy_codebot.mql5 import runner_design
from strategy_codebot.pine import generate_pine, validate_pine
from strategy_codebot.review import REVIEW_REPORT_PATH, write_review_report
from strategy_codebot.schemas import schema
from strategy_codebot.schemas import write_json
from strategy_codebot.tool_runtime import POLICY_OBSERVE
from strategy_codebot.server.artifact_store import LocalArtifactStore
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.ids import opaque_id
from strategy_codebot.server.repository import AssistantRunRecord
from strategy_codebot.server.repository import ConversationRepository
from strategy_codebot.server.run_modes import RUN_MODE_BACKTEST_PREVIEW
from strategy_codebot.server.run_modes import BACKTEST_MAX_VARIANTS
from strategy_codebot.server.run_modes import backtest_job_limits_for_tier
from strategy_codebot.server.run_modes import backtest_runtime_boundary
from strategy_codebot.server.run_modes import RUN_MODE_DRY_RUN

OBJECT_SCHEMA = {"type": "object"}
MAX_USER_FACING_SOURCES = 5
PINE_STRATEGY_PATH = "pine/strategy.pine"
MQL5_RUNNER_DESIGN_PATH = "mql5/runner-design.md"
VALIDATION_REPORT_PATH = "validation-report.json"
BACKTEST_PLAN_PATH = "backtest/backtest-plan.json"
BACKTEST_PINETS_PREVIEW_PATH = "backtest/pinets-preview-plan.json"
BACKTEST_SIGNALS_CONTEXT_PATH = "backtest/signals-market-context-plan.json"
BACKTEST_GRAPH_PIPELINE_PATH = "backtest/graph-pipeline-plan.json"
BACKTEST_SIDEKICK_EXPORT_PATH = "backtest/sidekick-export-plan.json"
STRATEGY_SPEC_SCHEMA_VERSION = "strategy-spec.schema.json"
BACKTEST_DEFAULT_CONFIG = {
    "engine": "backtest-kit",
    "symbol": "BTC/USDT",
    "timeframe": "1h",
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
        "engine": {"type": "string", "const": "backtest-kit"},
        "symbol": {"type": "string", "minLength": 1},
        "timeframe": {"type": "string", "minLength": 1},
        "start": {"type": "string", "minLength": 1},
        "end": {"type": "string", "minLength": 1},
        "initial_capital": {"type": "number", "exclusiveMinimum": 0},
        "fee_bps": {"type": "number", "minimum": 0},
        "slippage_bps": {"type": "number", "minimum": 0},
        "data_source": {"type": "string", "const": "public-readonly-cache"},
    },
}
BACKTEST_CONFIG_OVERRIDES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": BACKTEST_CONFIG_SCHEMA["properties"],
}
BACKTEST_STRATEGY_LOGIC_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["logic_version", "position", "indicators", "entry", "exit", "risk"],
    "properties": {
        "logic_version": {"type": "string", "const": "backtest-strategy-logic.v1"},
        "position": {"type": "string", "const": "long"},
        "indicators": {
            "type": "object",
            "additionalProperties": False,
            "required": ["fast_ema", "slow_ema"],
            "properties": {
                "fast_ema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "period", "source"],
                    "properties": {
                        "kind": {"type": "string", "const": "ema"},
                        "period": {"type": "integer", "minimum": 2, "maximum": 500},
                        "source": {"type": "string", "const": "close"},
                    },
                },
                "slow_ema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "period", "source"],
                    "properties": {
                        "kind": {"type": "string", "const": "ema"},
                        "period": {"type": "integer", "minimum": 2, "maximum": 500},
                        "source": {"type": "string", "const": "close"},
                    },
                },
                "rsi": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "period", "source"],
                    "properties": {
                        "kind": {"type": "string", "const": "rsi"},
                        "period": {"type": "integer", "minimum": 2, "maximum": 500},
                        "source": {"type": "string", "const": "close"},
                    },
                },
            },
        },
        "entry": {
            "type": "object",
            "additionalProperties": False,
            "required": ["all"],
            "properties": {
                "all": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["type", "left", "right"],
                        "properties": {
                            "type": {"type": "string", "enum": ["crossover", "crossunder", "greater_than", "less_than"]},
                            "left": {"type": "string", "minLength": 1},
                            "right": {"anyOf": [{"type": "string", "minLength": 1}, {"type": "number"}]},
                        },
                    },
                }
            },
        },
        "exit": {
            "type": "object",
            "additionalProperties": False,
            "required": ["take_profit_pct", "stop_loss_pct", "max_holding_minutes"],
            "properties": {
                "take_profit_pct": {"type": "number", "exclusiveMinimum": 0, "maximum": 100},
                "stop_loss_pct": {"type": "number", "exclusiveMinimum": 0, "maximum": 100},
                "max_holding_minutes": {"type": "integer", "minimum": 1},
            },
        },
        "risk": {
            "type": "object",
            "additionalProperties": False,
            "required": ["cost"],
            "properties": {"cost": {"type": "number", "exclusiveMinimum": 0}},
        },
    },
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
        description="Generate Pine Script v6 from a validated strategy spec.",
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
        description="Create a Backtest Kit local preview plan, normalized backtest_config, and executable strategy_logic DSL from a user prompt.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["prompt", "strategy_spec"],
            "properties": {
                "prompt": {"type": "string", "minLength": 1},
                "strategy_spec": STRATEGY_SPEC_SCHEMA,
                "strategy_logic": BACKTEST_STRATEGY_LOGIC_SCHEMA,
                "backtest_config": BACKTEST_CONFIG_OVERRIDES_SCHEMA,
            },
        },
    ),
    "run_backtest_preview": ToolDefinition(
        name="run_backtest_preview",
        description="Queue a sandboxed Backtest Kit local preview run. Prefer executable strategy_logic DSL for model-generated EMA/RSI strategies.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["strategy_spec", "backtest_config"],
            "properties": {
                "strategy_spec": STRATEGY_SPEC_SCHEMA,
                "strategy_logic": BACKTEST_STRATEGY_LOGIC_SCHEMA,
                "backtest_config": BACKTEST_CONFIG_SCHEMA,
                "prompt": {"type": "string"},
            },
        },
    ),
    "run_backtest_variant_lab": ToolDefinition(
        name="run_backtest_variant_lab",
        description="Queue multiple comparable Backtest Kit local preview variants with shared cache metadata.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["prompt", "strategy_spec", "base_backtest_config", "variants"],
            "properties": {
                "prompt": {"type": "string", "minLength": 1},
                "strategy_spec": STRATEGY_SPEC_SCHEMA,
                "strategy_logic": BACKTEST_STRATEGY_LOGIC_SCHEMA,
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
                            "strategy_logic": BACKTEST_STRATEGY_LOGIC_SCHEMA,
                            "backtest_config": BACKTEST_CONFIG_OVERRIDES_SCHEMA,
                        },
                    },
                },
            },
        },
    ),
    "create_pinets_preview_plan": ToolDefinition(
        name="create_pinets_preview_plan",
        description="Create a PineTS local preview plan using @backtest-kit/pinets; label it explicitly as not TradingView validation.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["prompt", "strategy_spec"],
            "properties": {
                "prompt": {"type": "string", "minLength": 1},
                "strategy_spec": STRATEGY_SPEC_SCHEMA,
                "pine_code": {"type": "string"},
                "backtest_config": BACKTEST_CONFIG_OVERRIDES_SCHEMA,
            },
        },
    ),
    "create_signals_market_context_plan": ToolDefinition(
        name="create_signals_market_context_plan",
        description="Create an LLM-ready market-context plan using @backtest-kit/signals without routing model calls through Backtest Kit.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["prompt"],
            "properties": {
                "prompt": {"type": "string", "minLength": 1},
                "symbol": {"type": "string"},
                "backtest_config": BACKTEST_CONFIG_OVERRIDES_SCHEMA,
                "sections": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["order_book", "1m_history", "15m_history", "30m_history", "1h_history", "indicators"],
                },
            },
        },
    ),
    "create_graph_pipeline_plan": ToolDefinition(
        name="create_graph_pipeline_plan",
        description="Create a multi-timeframe strategy pipeline plan using @backtest-kit/graph for variant composition.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["prompt", "strategy_spec"],
            "properties": {
                "prompt": {"type": "string", "minLength": 1},
                "strategy_spec": STRATEGY_SPEC_SCHEMA,
                "base_backtest_config": BACKTEST_CONFIG_SCHEMA,
                "timeframes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["4h", "15m"],
                },
                "variants": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
            },
        },
    ),
    "create_sidekick_export_plan": ToolDefinition(
        name="create_sidekick_export_plan",
        description="Create a Sidekick export/scaffold plan; Sidekick is not used inside API runtime.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["prompt", "strategy_spec"],
            "properties": {
                "prompt": {"type": "string", "minLength": 1},
                "strategy_spec": STRATEGY_SPEC_SCHEMA,
                "project_name": {"type": "string"},
            },
        },
    ),
}


def provider_tools() -> list[dict[str, Any]]:
    return [definition.as_provider_tool() for definition in TOOL_DEFINITIONS.values()]


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
    return {"pine_code": pine_code, "artifact_id": artifact.id if artifact else None}


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
    validation = validate_pine(arguments["pine_code"], strategy_spec)
    context.repository.create_strategy_spec(
        context.auth,
        context.run.id,
        strategy_spec,
        STRATEGY_SPEC_SCHEMA_VERSION,
    )
    artifact = _persist_json_artifact(
        context,
        kind="validation_report",
        display_name="validation-report.json",
        relative_path=VALIDATION_REPORT_PATH,
        payload=validation,
        source="llm_orchestrator.static_validate",
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
    return {"validation": validation, "artifact_id": artifact.id if artifact else None}


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
    proposal = {
        "status": "manual_review_required",
        "summary": arguments["summary"],
        "recommendations": ["Review this proposal before changing knowledge documents."],
    }
    out_dir = context.artifact_store.run_dir(context.run.id)
    path = out_dir / "knowledge-proposal.json"
    write_json(path, proposal)
    artifact = context.repository.create_artifact(
        context.auth,
        context.run.id,
        kind="knowledge_proposal",
        mime_type="application/json",
        display_name="knowledge-proposal.json",
        storage_key=context.artifact_store.storage_key(context.run.id, "knowledge-proposal.json"),
        metadata_json={"source": "llm_orchestrator"},
    )
    if artifact is not None:
        context.repository.append_run_event(
            context.auth,
            context.run.id,
            "artifact.created",
            {"artifact_id": artifact.id, "kind": artifact.kind, "display_name": artifact.display_name},
        )
    return {"proposal": proposal, "artifact_id": artifact.id if artifact else None}


def _create_backtest_plan_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    prompt = arguments["prompt"]
    strategy_spec = arguments["strategy_spec"]
    backtest_config = _build_backtest_config(
        prompt,
        strategy_spec=strategy_spec,
        overrides=arguments.get("backtest_config"),
    )
    strategy_logic = _build_backtest_strategy_logic(strategy_spec, backtest_config, arguments.get("strategy_logic"))
    plan = {
        "kind": "backtest_plan",
        "engine": "backtest-kit",
        "prompt": prompt,
        "strategy_spec": strategy_spec,
        "strategy_logic": strategy_logic,
        "execution_semantics": "semantic_strategy_logic",
        "backtest_config": backtest_config,
        "assumptions": [
            "Backtest Kit output is local preview evidence only.",
            "Public read-only market data and cache are used; no broker, paper, or live execution is allowed.",
            "Fee and slippage are explicit inputs and may not match any venue's final execution semantics.",
            "strategy_logic is a constrained deterministic DSL; arbitrary model-generated code is not executed.",
        ],
        "warnings": [
            "This is not TradingView proof, MQL5 proof, or live-trading evidence.",
            "Review the generated report artifacts before changing strategy logic.",
        ],
        "runtime_boundary": backtest_runtime_boundary(),
    }
    artifact = _persist_json_artifact(
        context,
        kind="backtest_plan",
        display_name="Backtest plan",
        relative_path=BACKTEST_PLAN_PATH,
        payload=plan,
        source="llm_orchestrator.create_backtest_plan",
    )
    return {
        "backtest_config": backtest_config,
        "strategy_logic": strategy_logic,
        "execution_semantics": plan["execution_semantics"],
        "artifact_id": artifact.id if artifact else None,
        "warnings": plan["warnings"],
        "assumptions": plan["assumptions"],
    }


def _run_backtest_preview_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    queued = _queue_backtest_preview(
        context,
        strategy_spec=arguments["strategy_spec"],
        strategy_logic=_build_backtest_strategy_logic(
            arguments["strategy_spec"],
            arguments["backtest_config"],
            arguments.get("strategy_logic"),
        ),
        backtest_config=arguments["backtest_config"],
        metadata={"source_tool": "run_backtest_preview", "prompt": arguments.get("prompt")},
    )
    return {
        "run_id": queued["run_id"],
        "job_id": queued["job_id"],
        "status": "queued",
        "mode": RUN_MODE_BACKTEST_PREVIEW,
        "backtest_config": queued["backtest_config"],
        "strategy_logic": queued["strategy_logic"],
        "execution_semantics": "semantic_strategy_logic",
        "evidence_label": "Backtest Kit local preview evidence only",
    }


def _run_backtest_variant_lab_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    if len(arguments["variants"]) > BACKTEST_MAX_VARIANTS:
        raise ValueError(f"Backtest variant limit exceeded: {BACKTEST_MAX_VARIANTS}")
    group_id = opaque_id("variant")
    base_config = _normalize_backtest_config(arguments["base_backtest_config"])
    shared_cache_key = _backtest_cache_key(base_config)
    queued_variants: list[dict[str, Any]] = []
    for index, variant in enumerate(arguments["variants"]):
        config = _normalize_backtest_config({**base_config, **variant.get("backtest_config", {})})
        queued = _queue_backtest_preview(
            context,
            strategy_spec=variant.get("strategy_spec") or arguments["strategy_spec"],
            strategy_logic=_build_backtest_strategy_logic(
                variant.get("strategy_spec") or arguments["strategy_spec"],
                config,
                variant.get("strategy_logic") or arguments.get("strategy_logic"),
            ),
            backtest_config=config,
            metadata={
                "source_tool": "run_backtest_variant_lab",
                "prompt": arguments["prompt"],
                "variant_group_id": group_id,
                "variant_index": index,
                "variant_name": variant["name"],
                "shared_cache_key": shared_cache_key,
            },
        )
        queued_variants.append(
            {
                "name": variant["name"],
                "run_id": queued["run_id"],
                "job_id": queued["job_id"],
                "status": "queued",
                "backtest_config": config,
                "strategy_logic": queued["strategy_logic"],
            }
        )
    comparison = {
        "kind": "backtest_variant_comparison",
        "variant_group_id": group_id,
        "shared_cache_key": shared_cache_key,
        "prompt": arguments["prompt"],
        "base_backtest_config": base_config,
        "variants": queued_variants,
        "warnings": [
            "Variant results are comparable only after every queued child run completes.",
            "Backtest Kit output is local preview evidence only, not TradingView, MQL5, or live-trading proof.",
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
        "artifact_id": artifact.id if artifact else None,
        "variants": queued_variants,
    }


def _create_pinets_preview_plan_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    strategy_spec = arguments["strategy_spec"]
    config = _build_backtest_config(
        arguments["prompt"],
        strategy_spec=strategy_spec,
        overrides=arguments.get("backtest_config"),
    )
    pine_code = arguments.get("pine_code") or generate_pine(strategy_spec)
    plan = {
        "kind": "backtest_pinets_preview",
        "package": "@backtest-kit/pinets",
        "package_version": "14.0.0",
        "evidence_label": "PineTS local preview only",
        "not_evidence": ["TradingView validation", "MQL5 proof", "live-trading evidence", "profitability claim"],
        "prompt": arguments["prompt"],
        "backtest_config": config,
        "strategy_spec": strategy_spec,
        "pine_source": pine_code,
        "expected_runtime": {
            "imports": ["Code", "getSignal", "run", "extract", "extractRows", "toMarkdown"],
            "source": "Code.fromString(pine_source)",
            "safe_default": "getSignal(source, { symbol, timeframe, limit })",
            "required_signal_plots": ["Signal", "Close", "StopLoss", "TakeProfit", "EstimatedTime"],
        },
        "warnings": [
            "PineTS preview runs Pine syntax locally and is not TradingView validation.",
            "Use TradingView/manual proof separately before claiming TradingView parity.",
            "No broker, paper, live execution, Telegram alerts, or Docker live mode is allowed.",
        ],
    }
    artifact = _persist_json_artifact(
        context,
        kind="backtest_pinets_preview",
        display_name="PineTS local preview plan",
        relative_path=BACKTEST_PINETS_PREVIEW_PATH,
        payload=plan,
        source="llm_orchestrator.create_pinets_preview_plan",
    )
    return {
        "artifact_id": artifact.id if artifact else None,
        "evidence_label": plan["evidence_label"],
        "backtest_config": config,
        "warnings": plan["warnings"],
    }


def _create_signals_market_context_plan_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    config = _normalize_backtest_config(arguments.get("backtest_config") or {})
    symbol = str(arguments.get("symbol") or config["symbol"]).strip().upper()
    sections = arguments.get("sections") or ["order_book", "1m_history", "15m_history", "30m_history", "1h_history", "indicators"]
    plan = {
        "kind": "backtest_signals_context",
        "package": "@backtest-kit/signals",
        "package_version": "14.0.0",
        "prompt": arguments["prompt"],
        "symbol": symbol,
        "llm_context_shape": {
            "primary_call": "commitHistorySetup(symbol, messages)",
            "granular_calls": [
                "commitBookDataReport",
                "commitOneMinuteHistory",
                "commitFifteenMinuteHistory",
                "commitThirtyMinuteHistory",
                "commitHourHistory",
                "commitMicroTermMath",
                "commitShortTermMath",
                "commitSwingTermMath",
                "commitLongTermMath",
            ],
            "requested_sections": sections,
        },
        "routing_policy": {
            "model_routing_owner": "strategy-codebot",
            "backtest_kit_ollama": "excluded_from_initial_runtime",
            "market_context_only": True,
        },
        "warnings": [
            "Signals output is LLM-ready market context, not trading advice or execution evidence.",
            "Model routing remains inside strategy-codebot; @backtest-kit/ollama is intentionally not used.",
        ],
    }
    artifact = _persist_json_artifact(
        context,
        kind="backtest_signals_context",
        display_name="Backtest Kit signals market context plan",
        relative_path=BACKTEST_SIGNALS_CONTEXT_PATH,
        payload=plan,
        source="llm_orchestrator.create_signals_market_context_plan",
    )
    return {
        "artifact_id": artifact.id if artifact else None,
        "package": plan["package"],
        "symbol": symbol,
        "warnings": plan["warnings"],
    }


def _create_graph_pipeline_plan_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    timeframes = arguments.get("timeframes") or ["4h", "15m"]
    variants = arguments.get("variants") or []
    base_config = (
        _normalize_backtest_config(arguments["base_backtest_config"])
        if arguments.get("base_backtest_config")
        else _build_backtest_config(arguments["prompt"], strategy_spec=arguments["strategy_spec"])
    )
    nodes = [
        {
            "id": f"pine_{timeframe.replace('/', '_')}",
            "type": "sourceNode",
            "package": "@backtest-kit/pinets",
            "timeframe": timeframe,
            "purpose": "Extract Pine plots for this timeframe using run() + extract().",
        }
        for timeframe in timeframes
    ]
    plan = {
        "kind": "backtest_graph_pipeline",
        "package": "@backtest-kit/graph",
        "package_version": "14.0.0",
        "prompt": arguments["prompt"],
        "strategy_spec": arguments["strategy_spec"],
        "base_backtest_config": base_config,
        "nodes": [
            *nodes,
            {
                "id": "composed_signal",
                "type": "outputNode",
                "package": "@backtest-kit/graph",
                "purpose": "Combine multi-timeframe outputs and variant gates before queueing backtest-preview runs.",
                "depends_on": [node["id"] for node in nodes],
            },
        ],
        "variant_composition": {
            "variants": variants,
            "queue_target": "run_backtest_variant_lab",
            "shared_cache_key": _backtest_cache_key(base_config),
        },
        "warnings": [
            "Graph output is a local composition plan until child backtest reports exist.",
            "Do not claim strategy success from graph topology alone.",
        ],
    }
    artifact = _persist_json_artifact(
        context,
        kind="backtest_graph_pipeline",
        display_name="Backtest Kit graph pipeline plan",
        relative_path=BACKTEST_GRAPH_PIPELINE_PATH,
        payload=plan,
        source="llm_orchestrator.create_graph_pipeline_plan",
    )
    return {
        "artifact_id": artifact.id if artifact else None,
        "package": plan["package"],
        "node_count": len(plan["nodes"]),
        "shared_cache_key": plan["variant_composition"]["shared_cache_key"],
    }


def _create_sidekick_export_plan_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    project_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(arguments.get("project_name") or "backtest-kit-sidekick-export")).strip("-")
    if not project_name:
        project_name = "backtest-kit-sidekick-export"
    plan = {
        "kind": "backtest_sidekick_export",
        "package": "@backtest-kit/sidekick",
        "package_version": "14.0.0",
        "prompt": arguments["prompt"],
        "strategy_spec": arguments["strategy_spec"],
        "project_name": project_name,
        "export_command": f"npx -y @backtest-kit/sidekick@14.0.0 {project_name}",
        "runtime_policy": {
            "usage": "export/scaffold only",
            "api_runtime": "blocked",
            "worker_runtime": "blocked",
            "live_trading": "blocked",
            "broker_credentials": "blocked",
        },
        "warnings": [
            "Sidekick does not run inside the API or worker runtime.",
            "Sidekick is an export/scaffold feature only and must not run inside the API or worker runtime.",
            "Review generated source manually before copying strategy-codebot artifacts into the scaffold.",
        ],
    }
    artifact = _persist_json_artifact(
        context,
        kind="backtest_sidekick_export",
        display_name="Backtest Kit Sidekick export plan",
        relative_path=BACKTEST_SIDEKICK_EXPORT_PATH,
        payload=plan,
        source="llm_orchestrator.create_sidekick_export_plan",
    )
    return {
        "artifact_id": artifact.id if artifact else None,
        "package": plan["package"],
        "project_name": project_name,
        "export_command": plan["export_command"],
        "warnings": plan["warnings"],
    }


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
    normalized["engine"] = "backtest-kit"
    normalized["symbol"] = str(normalized["symbol"]).strip().upper()
    normalized["timeframe"] = str(normalized["timeframe"]).strip()
    normalized["start"] = str(normalized["start"]).strip()
    normalized["end"] = str(normalized["end"]).strip()
    normalized["initial_capital"] = float(normalized["initial_capital"])
    normalized["fee_bps"] = float(normalized["fee_bps"])
    normalized["slippage_bps"] = float(normalized["slippage_bps"])
    normalized["data_source"] = "public-readonly-cache"
    validate(instance=normalized, schema=BACKTEST_CONFIG_SCHEMA)
    return normalized


def _build_backtest_strategy_logic(
    strategy_spec: dict[str, Any],
    backtest_config: dict[str, Any],
    provided: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if provided:
        validate(instance=provided, schema=BACKTEST_STRATEGY_LOGIC_SCHEMA)
        return provided
    initial_capital = float(backtest_config.get("initial_capital") or BACKTEST_DEFAULT_CONFIG["initial_capital"])
    take_profit = _number_from_strategy_spec(strategy_spec, ["take_profit_pct", "takeProfitPct", "tp_pct", "take_profit"], 2.0)
    stop_loss = _number_from_strategy_spec(strategy_spec, ["stop_loss_pct", "stopLossPct", "sl_pct", "stop_loss"], 1.0)
    holding_minutes = int(_number_from_strategy_spec(strategy_spec, ["max_holding_minutes", "minute_estimated_time", "holding_minutes"], 1440))
    cost = _number_from_strategy_spec(strategy_spec, ["cost", "trade_cost", "position_size"], initial_capital / 10)
    logic = {
        "logic_version": "backtest-strategy-logic.v1",
        "position": "long",
        "indicators": {
            "fast_ema": {"kind": "ema", "period": 3, "source": "close"},
            "slow_ema": {"kind": "ema", "period": 5, "source": "close"},
            "rsi": {"kind": "rsi", "period": 14, "source": "close"},
        },
        "entry": {
            "all": [
                {"type": "crossover", "left": "fast_ema", "right": "slow_ema"},
                {"type": "greater_than", "left": "rsi", "right": 45},
            ]
        },
        "exit": {
            "take_profit_pct": min(max(take_profit, 0.01), 100),
            "stop_loss_pct": min(max(stop_loss, 0.01), 100),
            "max_holding_minutes": max(1, holding_minutes),
        },
        "risk": {"cost": min(max(cost, 1), initial_capital)},
    }
    validate(instance=logic, schema=BACKTEST_STRATEGY_LOGIC_SCHEMA)
    return logic


def _number_from_strategy_spec(strategy_spec: dict[str, Any], keys: list[str], fallback: float) -> float:
    for key in keys:
        value = strategy_spec.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            match = re.search(r"-?\d+(?:\.\d+)?", value)
            if match:
                return float(match.group(0))
    return fallback


def _queue_backtest_preview(
    context: ToolExecutionContext,
    *,
    strategy_spec: dict[str, Any],
    strategy_logic: dict[str, Any] | None = None,
    backtest_config: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    config = _normalize_backtest_config(backtest_config)
    executable_logic = _build_backtest_strategy_logic(strategy_spec, config, strategy_logic)
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
            "strategy_logic": executable_logic,
            "backtest_config": config,
            "runtime": backtest_runtime_boundary(),
            "limits": backtest_job_limits_for_tier(context.auth.user_tier),
            "chat_tool": {key: value for key, value in metadata.items() if value is not None},
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
        "engine": config["engine"],
        "symbol": config["symbol"],
        "timeframe": config["timeframe"],
        "data_source": config["data_source"],
        "execution_semantics": "semantic_strategy_logic",
        **{key: value for key, value in metadata.items() if value is not None},
    }
    context.repository.append_run_event(context.auth, run.id, "backtest.queued", event_payload)
    context.repository.append_run_event(
        context.auth,
        context.run.id,
        "backtest.queued",
        {"child_run_id": run.id, **event_payload},
    )
    return {"run_id": run.id, "job_id": job.id, "backtest_config": config, "strategy_logic": executable_logic}


def _backtest_cache_key(config: dict[str, Any]) -> str:
    key_payload = {
        "data_source": config["data_source"],
        "symbol": config["symbol"],
        "timeframe": config["timeframe"],
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
    "create_pinets_preview_plan": _create_pinets_preview_plan_tool,
    "create_signals_market_context_plan": _create_signals_market_context_plan_tool,
    "create_graph_pipeline_plan": _create_graph_pipeline_plan_tool,
    "create_sidekick_export_plan": _create_sidekick_export_plan_tool,
}


def tool_catalog_consistency_errors() -> list[str]:
    definition_names = set(TOOL_DEFINITIONS)
    handler_names = set(TOOL_HANDLERS)
    errors: list[str] = []
    missing_handlers = sorted(definition_names - handler_names)
    if missing_handlers:
        errors.append(f"Tool definitions without handlers: {', '.join(missing_handlers)}")
    missing_definitions = sorted(handler_names - definition_names)
    if missing_definitions:
        errors.append(f"Tool handlers without definitions: {', '.join(missing_definitions)}")
    return errors


def execute_tool(tool_name: str, arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    handler = TOOL_HANDLERS.get(tool_name)
    if handler is not None:
        return handler(arguments, context)
    raise ValueError(f"Unsupported tool: {tool_name}")


def compact_tool_output(output: dict[str, Any]) -> dict[str, Any]:
    if "knowledge_context" in output and isinstance(output["knowledge_context"], dict):
        return {"knowledge_context_summary": _knowledge_context_summary(output["knowledge_context"])}
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
