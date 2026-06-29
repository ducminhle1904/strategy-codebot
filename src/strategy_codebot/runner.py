from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from strategy_codebot import __version__
from strategy_codebot.agent_harness import write_otel_export
from strategy_codebot.harness import build_trace_command, harness_cli_availability, harness_outcome, record_trace, record_trace_intake, should_record_harness
from strategy_codebot.harness_types import STATUS_FAIL, STATUS_PASS, STATUS_SKIPPED
from strategy_codebot.knowledge_context import KNOWLEDGE_CONTEXT_PATH, knowledge_metadata
from strategy_codebot.live import LIVE_ERROR_PATH, LIVE_WORKFLOW_TRACE_PATH, MARKET_RESEARCH_PATH, PROXY_ATTRIBUTION_EVENTS_PATH, WORKFLOW_COMPACT_FREE, WORKFLOW_MULTI_AGENT, LiveError, LiveGenerationResult, LiveRunOptions, generate_live, live_error_report
from strategy_codebot.mql5 import runner_design, validation_report as mql5_validation_report
from strategy_codebot.nautilus import nautilus_artifact_bundle
from strategy_codebot.nautilus import validate_nautilus_spec
from strategy_codebot.paths import ensure_dir, repo_root, resolve_repo_path
from strategy_codebot.pine import generate_pine, manual_checklist, validate_pine
from strategy_codebot.quality import QUALITY_REPORT_PATH, assess_strategy_quality, production_gate_with_quality
from strategy_codebot.reporting import aggregate_status
from strategy_codebot.review import REVIEW_MODE_NONE, REVIEW_MODE_PARALLEL, REVIEW_REPORT_PATH, write_review_report
from strategy_codebot.schemas import load_strategy_spec, validate_payload, write_json
from strategy_codebot.strategy_spec import TARGET_NAUTILUS, wants_target
from strategy_codebot.tool_runtime import POLICY_MODES, POLICY_OBSERVE, RUNTIME_SUMMARY_PATH, RUNTIME_TRACE_PATH, ToolHarness, call_tool

def run_strategy(
    *,
    spec_path: Path | None,
    prompt: str | None,
    mode: str,
    out_dir: Path,
    review: str = REVIEW_MODE_NONE,
    record_harness: bool | None = None,
    runtime_trace: bool = True,
    policy: str = POLICY_OBSERVE,
    live_options: LiveRunOptions | None = None,
    model_registry: Path | None = None,
    otel_export: Path | None = None,
) -> dict[str, Any]:
    started_at = perf_counter()
    if review not in {REVIEW_MODE_NONE, REVIEW_MODE_PARALLEL}:
        raise ValueError("review must be none or parallel")
    if policy not in POLICY_MODES:
        raise ValueError("policy must be observe or enforce")
    if mode not in {"dry-run", "live"}:
        raise ValueError("mode must be dry-run or live")
    if mode == "dry-run" and spec_path is None:
        raise ValueError("--spec is required when --mode dry-run")
    if mode == "live" and not prompt:
        raise ValueError("--prompt is required when --mode live")
    options: LiveRunOptions | None = None
    if mode == "live":
        options = live_options or LiveRunOptions()
        if options.workflow == WORKFLOW_MULTI_AGENT and options.model_override:
            raise ValueError("--model is only supported with --workflow single; use --model-stage for multi-agent runs")

    run_id = out_dir.name if out_dir.name else f"run-{uuid4().hex[:8]}"
    tool_harness = ToolHarness(run_id=run_id, policy_mode=policy) if runtime_trace else None
    runtime_artifacts = [RUNTIME_TRACE_PATH, RUNTIME_SUMMARY_PATH] if runtime_trace else []
    live_result: LiveGenerationResult | None = None

    if mode == "dry-run":
        spec = call_tool(tool_harness, "load_strategy_spec", load_strategy_spec, spec_path, input_refs=[str(spec_path)], output_refs=["strategy-spec.json"])
        pine_code = (
            call_tool(tool_harness, "generate_pine", generate_pine, spec, input_refs=["strategy-spec.json"], output_refs=["pine/strategy.pine"], policy_text=str(spec))
            if spec["target_platform"] in {"pine_v6", "both"}
            else None
        )
    elif mode == "live":
        assert options is not None
        if options.proxy_attribution_path is None:
            options.proxy_attribution_path = out_dir / PROXY_ATTRIBUTION_EVENTS_PATH
        registry_path = resolve_repo_path(model_registry or repo_root() / "configs" / "model-registry.example.yaml")
        live_output_refs = ["strategy-spec.json", "pine/strategy.pine", "live-metadata.json"]
        if options.knowledge_context == "auto":
            live_output_refs.append(KNOWLEDGE_CONTEXT_PATH)
        if options.workflow in {WORKFLOW_MULTI_AGENT, WORKFLOW_COMPACT_FREE}:
            live_output_refs.append(LIVE_WORKFLOW_TRACE_PATH)
        try:
            live_result = call_tool(
                tool_harness,
                "generate_live_strategy",
                generate_live,
                prompt,
                registry_path,
                run_id=run_id,
                live_options=options,
                policy=policy,
                input_refs=["prompt", str(registry_path)],
                output_refs=live_output_refs,
                policy_text=prompt,
            )
        except LiveError as exc:
            _write_live_failure_artifacts(
                out_dir=out_dir,
                run_id=run_id,
                prompt=prompt,
                options=options,
                exc=exc,
                tool_harness=tool_harness,
                runtime_trace=runtime_trace,
                otel_export=otel_export,
            )
            raise
        spec = live_result.strategy_spec
        pine_code = live_result.pine_code
        validate_payload(spec, "strategy-spec.schema.json")
        if tool_harness and live_result.workflow_trace:
            tool_harness.record_external_events(live_result.workflow_trace.get("lifecycle_events", []))

    ensure_dir(out_dir)
    artifacts: list[str] = []

    def write_text_artifact(relative_path: str, content: str) -> None:
        target = out_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        artifacts.append(relative_path)

    def write_json_artifact(relative_path: str, payload: dict[str, Any]) -> None:
        write_json(out_dir / relative_path, payload)
        artifacts.append(relative_path)

    write_json_artifact("strategy-spec.json", spec)
    if live_result:
        if live_result.market_research and live_result.market_research.get("web_search_enabled"):
            write_json_artifact(MARKET_RESEARCH_PATH, live_result.market_research)
        if live_result.knowledge_context:
            write_json_artifact(KNOWLEDGE_CONTEXT_PATH, live_result.knowledge_context)
        if live_result.workflow_trace:
            write_json_artifact(LIVE_WORKFLOW_TRACE_PATH, live_result.workflow_trace)
        proxy_events = _write_proxy_attribution_events(out_dir, run_id=run_id, options=options, attempts=live_result.attempts)
        if proxy_events:
            artifacts.append(PROXY_ATTRIBUTION_EVENTS_PATH)
        if options.save_raw_provider:
            write_json_artifact("live-provider-response.json", live_result.raw_response)

    validation = None
    mql5_design = None
    if pine_code:
        write_text_artifact("pine/strategy.pine", pine_code)
        validation = call_tool(tool_harness, "validate_pine_static", validate_pine, pine_code, spec, input_refs=["pine/strategy.pine", "strategy-spec.json"], output_refs=["validation-report.json"])
        checklist = call_tool(tool_harness, "write_manual_checklist", manual_checklist, spec, input_refs=["strategy-spec.json"], output_refs=["manual-tradingview-checklist.md"])
        write_text_artifact("manual-tradingview-checklist.md", checklist)

    if spec["target_platform"] in {"mql5", "both"}:
        mql5_design = call_tool(tool_harness, "create_mql5_runner_design", runner_design, spec, input_refs=["strategy-spec.json"], output_refs=["mql5/runner-design.md"])
        write_text_artifact("mql5/runner-design.md", mql5_design)
        mql5_report = mql5_validation_report()
        validation = _combine_validation(validation, mql5_report) if validation else mql5_report

    if wants_target(spec, TARGET_NAUTILUS):
        nautilus_report = call_tool(
            tool_harness,
            "validate_nautilus_contract",
            validate_nautilus_spec,
            spec,
            input_refs=["strategy-spec.json"],
            output_refs=["nautilus/strategy.py", "nautilus/runtime-manifest.json", "nautilus/parity-report.json"],
        )
        if nautilus_report["status"] == STATUS_PASS:
            for relative_path, content in nautilus_artifact_bundle(spec).items():
                write_text_artifact(relative_path, content)
        validation = _combine_validation(validation, nautilus_report) if validation else nautilus_report

    if validation is None:
        validation = {
            "platform": spec["target_platform"],
            "status": STATUS_SKIPPED,
            "checks": [],
            "evidence": [],
            "warnings": ["No Phase 1 validator is available for this target."],
            "next_actions": [],
        }

    validate_payload(validation, "validation-report.schema.json")
    write_json_artifact("validation-report.json", validation)
    quality_report = None
    if live_result:
        quality_report = assess_strategy_quality(spec, pine_code, validation=validation)
        live_result.quality_report = quality_report
        base_production_gate = live_result.production_gate or {"status": STATUS_PASS if validation["status"] == STATUS_PASS else STATUS_FAIL, "validation_status": validation["status"]}
        live_result.production_gate = production_gate_with_quality(base_production_gate, quality_report)
        if live_result.workflow_trace:
            live_result.workflow_trace["quality_report"] = quality_report
            live_result.workflow_trace["production_gate"] = live_result.production_gate
            final_decision = live_result.workflow_trace.get("final_decision")
            if isinstance(final_decision, dict):
                final_decision["quality_status"] = quality_report["status"]
                final_decision["quality_score"] = quality_report["score"]
                final_decision["production_gate"] = live_result.production_gate
            write_json(out_dir / LIVE_WORKFLOW_TRACE_PATH, live_result.workflow_trace)
        write_json_artifact(QUALITY_REPORT_PATH, quality_report)
        write_json_artifact("live-metadata.json", live_result.metadata())

    harness_availability = harness_cli_availability()
    repository_trace_enabled = should_record_harness(record_harness)
    harness_recording_status = "recorded" if repository_trace_enabled else ("skipped_requested" if record_harness is False else "skipped_unavailable")
    harness_recording_reason = "record_harness_enabled" if repository_trace_enabled else ("record_harness_disabled" if record_harness is False else str(harness_availability.get("reason") or "harness_cli_unavailable"))
    repository_intake_id = (
        record_trace_intake(
            summary=f"Strategy generation {run_id}",
            input_type="new spec",
            docs=[
                str(spec_path) if spec_path else "prompt",
                "configs/source-registry.yaml",
                str(out_dir / "validation-report.json"),
            ],
            notes="auto-created for strategy-codebot run --record-harness",
        )
        if repository_trace_enabled
        else None
    )

    if review == REVIEW_MODE_PARALLEL:
        review_report = write_review_report(
            run_id=run_id,
            spec=spec,
            validation=validation,
            pine_code=pine_code,
            mql5_runner_design=mql5_design,
            mode=mode,
            out_path=out_dir / REVIEW_REPORT_PATH,
            record_harness=record_harness,
            runtime_trace=False,
            policy=policy,
            tool_harness=tool_harness,
            model_registry=registry_path if mode == "live" else None,
            live_options=options if mode == "live" else None,
            intake_id=repository_intake_id,
        )
        artifacts.append(REVIEW_REPORT_PATH)
    else:
        review_report = None

    agent_run = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "agent_role": "pine_specialist" if spec["target_platform"] == "pine_v6" else "validator",
        "provider": "dry-run" if mode == "dry-run" else (live_result.provider if live_result else "litellm"),
        "model": "deterministic-template" if mode == "dry-run" else (live_result.model if live_result else "model-registry"),
        "prompt_version": __version__,
        "input_refs": [str(spec_path)] if spec_path else ["prompt"],
        "retrieved_sources": _live_retrieved_sources(live_result),
        "tool_calls": [
            *([_live_generation_tool_call(live_result)] if live_result else []),
            *(["pine-static-validator"] if pine_code else []),
            *(["nautilus-static-contract"] if wants_target(spec, TARGET_NAUTILUS) else []),
            *(["parallel-review"] if review_report else []),
        ],
        "output_refs": [*artifacts, *runtime_artifacts, "agent-run.json"],
        "validation_refs": ["validation-report.json", *([QUALITY_REPORT_PATH] if quality_report else []), *([REVIEW_REPORT_PATH] if review_report else []), *runtime_artifacts],
        "status": validation["status"],
        "warnings": [*validation["warnings"], *([finding["message"] for finding in quality_report.get("warnings", [])] if quality_report else []), *(review_report["warnings"] if review_report else [])],
        "harness_recording_status": harness_recording_status,
        "harness_recording_reason": harness_recording_reason,
    }
    validate_payload(agent_run, "agent-run.schema.json")
    write_json_artifact("agent-run.json", agent_run)

    if repository_trace_enabled:
        trace_decisions = [
            *_harness_trace_decisions(mode=mode, spec=spec, review=review, policy=policy, runtime_trace=runtime_trace, live_options=options),
            *_harness_verification_decisions(validation=validation, review_report=review_report, live_result=live_result),
        ]
        command = build_trace_command(
            summary=f"Phase 1 single-agent run {run_id}",
            intake=repository_intake_id,
            story=None,
            agent=agent_run["agent_role"],
            outcome=harness_outcome(validation["status"]),
            changed=[str(out_dir / item) for item in artifacts],
            actions=_harness_trace_actions(tool_harness),
            read=_harness_trace_reads(agent_run, tool_harness),
            errors=_harness_trace_errors(tool_harness),
            friction=_harness_trace_friction(tool_harness),
            duration=max(0, int(perf_counter() - started_at)),
            tokens=_harness_trace_token_estimate(live_result),
            decisions=trace_decisions,
            notes=f"strategy-codebot CLI run; {'; '.join(_harness_verification_decisions(validation=validation, review_report=review_report, live_result=live_result))}",
        )
        call_tool(tool_harness, "record_harness_trace", record_trace, command, input_refs=["agent-run.json"], output_refs=["repository-harness trace"])

    if tool_harness:
        tool_harness.write_trace(out_dir / RUNTIME_TRACE_PATH, out_dir / RUNTIME_SUMMARY_PATH, [*artifacts, *runtime_artifacts])
    if otel_export:
        write_otel_export(out_dir, otel_export)

    return {"run_id": run_id, "out_dir": str(out_dir), "status": validation["status"]}


def _live_retrieved_sources(live_result: LiveGenerationResult | None) -> list[str]:
    if not live_result or not live_result.knowledge_context:
        return ["configs/source-registry.yaml"]
    return _knowledge_retrieved_sources(live_result.knowledge_context)


def _live_generation_tool_call(live_result: LiveGenerationResult) -> str:
    return _live_generation_tool_call_from_workflow(live_result.workflow)


def _live_generation_tool_call_from_workflow(workflow: str) -> str:
    if workflow == WORKFLOW_MULTI_AGENT:
        return "multi-model-live-generation"
    if workflow == WORKFLOW_COMPACT_FREE:
        return "compact-free-live-generation"
    return "live-generation"


def _knowledge_retrieved_sources(knowledge_context: dict[str, Any]) -> list[str]:
    if not knowledge_context:
        return ["configs/source-registry.yaml"]
    metadata = knowledge_metadata(knowledge_context)
    return [
        *[f"doc:{doc_id}" for doc_id in metadata["knowledge_doc_ids"]],
        *[f"source:{source_id}" for source_id in metadata["external_source_ids"]],
    ]


def _harness_trace_actions(tool_harness: ToolHarness | None) -> list[str]:
    if tool_harness is None:
        return ["runtime_trace_disabled"]
    actions: list[str] = []
    for event in tool_harness.events:
        tool_id = event.get("tool_id")
        if not tool_id or tool_id == "record_harness_trace":
            continue
        if event.get("event_type") in {"tool.completed", "tool.failed", "tool.blocked"}:
            actions.append(f"{tool_id}:{event.get('status', 'unknown')}")
    return actions


def _harness_trace_reads(agent_run: dict[str, Any], tool_harness: ToolHarness | None) -> list[str]:
    reads: list[str] = []
    for ref in [*agent_run.get("input_refs", []), *agent_run.get("retrieved_sources", [])]:
        _append_unique(reads, ref)
    if tool_harness:
        for event in tool_harness.events:
            for ref in event.get("input_refs", []):
                _append_unique(reads, ref)
    return reads


def _harness_trace_errors(tool_harness: ToolHarness | None) -> str:
    if tool_harness is None:
        return "[]"
    errors = []
    for event in _harness_error_events(tool_harness):
        errors.append(
            {
                "tool_id": event.get("tool_id"),
                "event_type": event.get("event_type"),
                "status": event.get("status"),
                "failure_class": event.get("failure_class"),
                "error": event.get("error"),
            }
        )
    return json.dumps(errors, ensure_ascii=False)


def _harness_trace_friction(tool_harness: ToolHarness | None) -> str:
    if tool_harness is None:
        return "runtime trace disabled"
    return "runtime tool failures or policy blocks recorded" if _harness_error_events(tool_harness) else "none"


def _harness_trace_decisions(
    *,
    mode: str,
    spec: dict[str, Any],
    review: str,
    policy: str,
    runtime_trace: bool,
    live_options: LiveRunOptions | None,
) -> list[str]:
    decisions = [
        f"mode={mode}",
        f"target_platform={spec['target_platform']}",
        f"review={review}",
        f"policy={policy}",
        f"runtime_trace={str(runtime_trace).lower()}",
    ]
    if live_options:
        decisions.extend([f"workflow={live_options.workflow}", f"cost_profile={live_options.cost_profile}"])
    return decisions


def _harness_verification_decisions(
    *,
    validation: dict[str, Any],
    review_report: dict[str, Any] | None,
    live_result: LiveGenerationResult | None,
) -> list[str]:
    decisions = [f"validation_status={validation['status']}"]
    if review_report:
        decisions.append(f"review_decision={review_report['decision']}")
    else:
        decisions.append("review_decision=skipped")
    if live_result and live_result.workflow_trace:
        final_decision = live_result.workflow_trace.get("final_decision", {})
        if isinstance(final_decision, dict):
            production_gate = final_decision.get("production_gate", {})
            gate_status = production_gate.get("status") if isinstance(production_gate, dict) else None
            decisions.append(f"production_gate={gate_status or final_decision.get('status', 'unknown')}")
        else:
            decisions.append("production_gate=unknown")
    else:
        decisions.append("production_gate=skipped")
    return decisions


def _harness_trace_token_estimate(live_result: LiveGenerationResult | None) -> int:
    if live_result is None:
        return 0
    try:
        return int(live_result.usage.get("total_tokens") or 0)
    except (TypeError, ValueError):
        return 0


def _harness_error_events(tool_harness: ToolHarness) -> list[dict[str, Any]]:
    return [event for event in tool_harness.events if event.get("event_type") in {"tool.failed", "tool.blocked"}]


def _append_unique(items: list[str], value: Any) -> None:
    text = str(value)
    if text and text not in items:
        items.append(text)


def _write_live_failure_artifacts(
    *,
    out_dir: Path,
    run_id: str,
    prompt: str | None,
    options: LiveRunOptions,
    exc: LiveError,
    tool_harness: ToolHarness | None,
    runtime_trace: bool,
    otel_export: Path | None,
) -> None:
    ensure_dir(out_dir)
    error_report = live_error_report(exc)
    diagnostics = error_report.get("diagnostics", {})
    metadata = diagnostics.get("metadata") or {
        "status": STATUS_FAIL,
        "workflow": options.workflow,
        "attempts": exc.attempts,
        "stages": [],
        "repair_count": 0,
        "usage": {},
        "total_usage": {},
    }
    workflow_trace = diagnostics.get("workflow_trace")
    raw_response = diagnostics.get("raw_responses", {})
    knowledge_context = diagnostics.get("knowledge_context_artifact") or {}
    output_refs = [LIVE_ERROR_PATH, "live-metadata.json", "agent-run.json"]
    attempts = metadata.get("attempts") if isinstance(metadata.get("attempts"), list) else exc.attempts

    write_json(out_dir / LIVE_ERROR_PATH, error_report)
    write_json(out_dir / "live-metadata.json", metadata)
    if _write_proxy_attribution_events(out_dir, run_id=run_id, options=options, attempts=attempts):
        output_refs.append(PROXY_ATTRIBUTION_EVENTS_PATH)
    if workflow_trace:
        write_json(out_dir / LIVE_WORKFLOW_TRACE_PATH, workflow_trace)
        output_refs.append(LIVE_WORKFLOW_TRACE_PATH)
        if tool_harness:
            tool_harness.record_external_events(workflow_trace.get("lifecycle_events", []))
    if options.save_raw_provider:
        write_json(out_dir / "live-provider-response.json", raw_response)
        output_refs.append("live-provider-response.json")
    if knowledge_context:
        write_json(out_dir / KNOWLEDGE_CONTEXT_PATH, knowledge_context)
        output_refs.append(KNOWLEDGE_CONTEXT_PATH)

    agent_run = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "agent_role": "pine_specialist",
        "provider": metadata.get("provider") or "litellm",
        "model": metadata.get("model") or "model-registry",
        "prompt_version": __version__,
        "input_refs": ["prompt"] if prompt else [],
        "retrieved_sources": _knowledge_retrieved_sources(knowledge_context),
        "tool_calls": [_live_generation_tool_call_from_workflow(options.workflow)],
        "output_refs": output_refs,
        "validation_refs": [LIVE_ERROR_PATH],
        "status": STATUS_FAIL,
        "warnings": [str(exc)],
    }
    validate_payload(agent_run, "agent-run.schema.json")
    write_json(out_dir / "agent-run.json", agent_run)

    if tool_harness and runtime_trace:
        tool_harness.write_trace(out_dir / RUNTIME_TRACE_PATH, out_dir / RUNTIME_SUMMARY_PATH, [*output_refs, RUNTIME_TRACE_PATH, RUNTIME_SUMMARY_PATH])
    if otel_export:
        write_otel_export(out_dir, otel_export)


def _write_proxy_attribution_events(
    out_dir: Path,
    *,
    run_id: str,
    options: LiveRunOptions | None,
    attempts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    path = out_dir / PROXY_ATTRIBUTION_EVENTS_PATH
    if path.exists():
        return _read_proxy_attribution_events(path)
    events = [_proxy_attribution_event(run_id=run_id, options=options, attempt=attempt) for attempt in attempts]
    events = [event for event in events if event]
    if not events:
        return []
    path.write_text("\n".join(json.dumps(event, ensure_ascii=False, sort_keys=True) for event in events) + "\n", encoding="utf-8")
    return events


def _read_proxy_attribution_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _proxy_attribution_event(
    *,
    run_id: str,
    options: LiveRunOptions | None,
    attempt: dict[str, Any],
) -> dict[str, Any] | None:
    model = str(attempt.get("model") or "")
    gateway = attempt.get("gateway")
    if gateway != "litellm_proxy" and not model.startswith("litellm_proxy/"):
        return None
    route_model = attempt.get("route_model") or (model.split("/", 1)[1] if model.startswith("litellm_proxy/") else None)
    return {
        "event_type": "proxy.attribution",
        "run_id": run_id,
        "case_id": options.case_id if options else None,
        "stage": attempt.get("stage"),
        "route_model": route_model,
        "model": model,
        "gateway": gateway or "litellm_proxy",
        "started_at": attempt.get("started_at"),
        "completed_at": attempt.get("completed_at"),
        "provider_call_ms": attempt.get("provider_call_ms"),
        "stage_total_ms": attempt.get("stage_total_ms") or attempt.get("duration_ms") or attempt.get("latency_ms"),
        "provider_call_ratio": attempt.get("provider_call_ratio"),
        "local_processing_ms": attempt.get("local_processing_ms"),
        "stage_input_chars": attempt.get("stage_input_chars"),
        "output_chars": attempt.get("output_chars"),
        "status": attempt.get("status"),
        "failure_class": attempt.get("failure_class"),
        "timeout_overrun": attempt.get("timeout_overrun"),
        "fallback_used": attempt.get("fallback_used"),
        "fallback_from": attempt.get("fallback_from"),
    }


def validate_pine_file(file_path: Path, spec_path: Path, out_path: Path) -> dict[str, Any]:
    spec = load_strategy_spec(spec_path)
    report = validate_pine(file_path.read_text(encoding="utf-8"), spec)
    validate_payload(report, "validation-report.schema.json")
    write_json(out_path, report)
    return report


def _combine_validation(pine_report: dict[str, Any] | None, mql5_report: dict[str, Any]) -> dict[str, Any]:
    if pine_report is None:
        return mql5_report
    status = aggregate_status({pine_report["status"], mql5_report["status"]})
    return {
        "platform": "both",
        "status": status,
        "checks": pine_report["checks"] + mql5_report["checks"],
        "evidence": pine_report["evidence"] + mql5_report["evidence"],
        "warnings": pine_report["warnings"] + mql5_report["warnings"],
        "next_actions": pine_report["next_actions"] + mql5_report["next_actions"],
    }
