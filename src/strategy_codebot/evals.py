from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
import multiprocessing as mp
import os
from pathlib import Path
import time
from typing import Any

import yaml

from strategy_codebot import __version__
from strategy_codebot.agent_harness import failure_from_attempt, inspect_run, write_combined_otel_export
from strategy_codebot.evaluator_optimizer import evaluator_review_status
from strategy_codebot.evaluator_optimizer import evaluator_stop_reason as _evaluator_optimizer_stop_reason
from strategy_codebot.evaluator_optimizer import nonnegative_int as _nonnegative_int
from strategy_codebot.evaluator_optimizer import repair_source_mix as _repair_source_mix_from_history
from strategy_codebot.evaluator_optimizer import validation_allows_artifact as _validation_allows_artifact
from strategy_codebot.evaluator_optimizer import validation_failures as _validation_failures
from strategy_codebot.harness_types import FAILURE_ARTIFACT_MISSING, FAILURE_POLICY_VIOLATION, FAILURE_PROVIDER_TIMEOUT, FAILURE_TOOL_ERROR, STATUS_FAIL, STATUS_PASS, STATUS_SKIPPED
from strategy_codebot.knowledge_base import KNOWLEDGE_DATABASE_URL_ENV, propose_failure_candidate
from strategy_codebot.knowledge_context import KNOWLEDGE_CONTEXT_AUTO, KNOWLEDGE_CONTEXT_PATH, build_knowledge_context, knowledge_metadata
from strategy_codebot.live import COST_PROFILE_QUALITY, DEFAULT_USER_TIER, LIVE_ERROR_PATH, LIVE_WORKFLOW_TRACE_PATH, PROXY_ATTRIBUTION_EVENTS_PATH, WORKFLOW_COMPACT_FREE, WORKFLOW_MULTI_AGENT, LiveError, LiveRunOptions, live_error_report, normalize_live_options
from strategy_codebot.paths import ensure_dir, repo_root, resolve_repo_path
from strategy_codebot.prompt_contracts import DEFAULT_PROMPT_PROFILE
from strategy_codebot.quality import QUALITY_REPORT_PATH, quality_metadata
from strategy_codebot.review import REVIEW_MODE_NONE
from strategy_codebot.route_health import record_timeout_mirror_events
from strategy_codebot.runner import run_strategy
from strategy_codebot.schemas import load_json, write_json
from strategy_codebot.tool_runtime import RUNTIME_SUMMARY_PATH, RUNTIME_TRACE_PATH, ToolBlockedError, find_blocked_claims, find_prompt_boundary_violations


EVAL_REPORT_PATH = "eval-report.json"
MAX_LIVE_EVAL_CONCURRENCY = 8


def run_live_eval(
    *,
    suite_path: Path,
    out_dir: Path,
    policy: str,
    live_options: LiveRunOptions | None = None,
    model_registry: Path | None = None,
    model_override: str | None = None,
    model_stage_overrides: dict[str, str] | None = None,
    workflow: str = WORKFLOW_MULTI_AGENT,
    cost_profile: str = COST_PROFILE_QUALITY,
    user_tier: str = DEFAULT_USER_TIER,
    save_raw_provider: bool = True,
    knowledge_context: str = "auto",
    prompt_profile: str = DEFAULT_PROMPT_PROFILE,
    otel_export: Path | None = None,
    concurrency: int = 2,
    case_timeout_seconds: int | None = None,
) -> dict[str, Any]:
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if concurrency > MAX_LIVE_EVAL_CONCURRENCY:
        raise ValueError(f"concurrency must be at most {MAX_LIVE_EVAL_CONCURRENCY}")
    if case_timeout_seconds is not None and case_timeout_seconds < 1:
        raise ValueError("case_timeout_seconds must be at least 1")
    options = live_options or normalize_live_options(
        model_override=model_override,
        model_stage_overrides=model_stage_overrides,
        workflow=workflow,
        cost_profile=cost_profile,
        user_tier=user_tier,
        save_raw_provider=save_raw_provider,
        knowledge_context=knowledge_context,
        prompt_profile=prompt_profile,
    )
    suite_path = resolve_repo_path(suite_path)
    registry_path = resolve_repo_path(model_registry or repo_root() / "configs" / "model-registry.example.yaml")
    suite = yaml.safe_load(suite_path.read_text(encoding="utf-8"))
    cases = suite.get("cases", []) if isinstance(suite, dict) else []
    if not cases:
        raise ValueError("Eval suite must contain at least one case.")

    ensure_dir(out_dir)
    case_inputs = [
        {
            "case": case,
            "out_dir": out_dir / "cases" / _case_id(case),
            "policy": policy,
            "model_registry": registry_path,
            "live_options": options,
        }
        for case in cases
    ]
    case_reports: list[dict[str, Any]] = []

    def checkpoint(reports: list[dict[str, Any]]) -> None:
        write_json(
            out_dir / EVAL_REPORT_PATH,
            _build_live_eval_report(
                suite=suite,
                suite_path=suite_path,
                policy=policy,
                registry_path=registry_path,
                options=options,
                case_timeout_seconds=case_timeout_seconds,
                case_reports=reports,
                expected_case_ids=[_case_id(case) for case in cases],
                is_complete=False,
            ),
        )

    try:
        if case_timeout_seconds is not None:
            case_reports = _run_cases_with_timeout(
                case_inputs,
                concurrency=concurrency,
                case_timeout_seconds=case_timeout_seconds,
                on_case_complete=checkpoint,
            )
        elif concurrency == 1:
            for case_input in case_inputs:
                case_reports.append(_run_case(**case_input))
                checkpoint(case_reports)
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                for case_report in executor.map(lambda case_input: _run_case(**case_input), case_inputs):
                    case_reports.append(case_report)
                    checkpoint(case_reports)
    except BaseException:
        if case_reports:
            write_json(
                out_dir / EVAL_REPORT_PATH,
                _build_live_eval_report(
                    suite=suite,
                    suite_path=suite_path,
                    policy=policy,
                    registry_path=registry_path,
                    options=options,
                    case_timeout_seconds=case_timeout_seconds,
                    case_reports=case_reports,
                    expected_case_ids=[_case_id(case) for case in cases],
                    is_complete=False,
                    status_override="incomplete",
                ),
            )
        raise

    report = _build_live_eval_report(
        suite=suite,
        suite_path=suite_path,
        policy=policy,
        registry_path=registry_path,
        options=options,
        case_timeout_seconds=case_timeout_seconds,
        case_reports=case_reports,
        expected_case_ids=[_case_id(case) for case in cases],
        is_complete=True,
    )
    if otel_export:
        write_combined_otel_export([Path(case["run_dir"]) for case in case_reports], otel_export)
        report["otel_export_ref"] = str(otel_export)
    write_json(out_dir / EVAL_REPORT_PATH, report)
    return report


def _build_live_eval_report(
    *,
    suite: dict[str, Any],
    suite_path: Path,
    policy: str,
    registry_path: Path,
    options: LiveRunOptions,
    case_timeout_seconds: int | None,
    case_reports: list[dict[str, Any]],
    expected_case_ids: list[str],
    is_complete: bool,
    status_override: str | None = None,
) -> dict[str, Any]:
    failed = [case for case in case_reports if case["status"] != STATUS_PASS]
    safety_cases = [case for case in case_reports if _is_expected_blocked(case)]
    artifact_cases = [case for case in case_reports if not _is_expected_blocked(case)]
    safety_failed = [case for case in safety_cases if _gate_status(case, "safety_gate") != STATUS_PASS]
    generation_failed = [case for case in artifact_cases if _gate_status(case, "generation_gate", fallback=case.get("status")) != STATUS_PASS]
    production_failed = [case for case in artifact_cases if _gate_status(case, "production_gate") != STATUS_PASS]
    knowledge_candidate_ids = [
        candidate_id
        for case in case_reports
        for candidate_id in case.get("knowledge_candidate_ids", [])
        if candidate_id
    ]
    completed_case_ids = [str(case.get("id")) for case in case_reports if case.get("id")]
    missing_case_ids = [case_id for case_id in expected_case_ids if case_id not in completed_case_ids]
    status = status_override or ("running" if not is_complete else STATUS_FAIL if failed else STATUS_PASS)
    evaluator_optimizer_summary = _build_eval_evaluator_optimizer_summary(case_reports)
    return {
        "suite": suite.get("name", suite_path.stem),
        "suite_path": str(suite_path),
        "created_at": datetime.now(UTC).isoformat(),
        "status": status,
        "is_complete": is_complete,
        "expected_case_count": len(expected_case_ids),
        "completed_case_count": len(case_reports),
        "pending_case_count": len(missing_case_ids),
        "missing_case_ids": missing_case_ids,
        "policy": policy,
        "model_registry": str(registry_path),
        "model_override": options.model_override,
        "model_stage_overrides": options.model_stage_overrides,
        "workflow": options.workflow,
        "cost_profile": options.cost_profile,
        "user_tier": options.user_tier,
        "case_timeout_seconds": case_timeout_seconds,
        "case_count": len(case_reports),
        "passed": len(case_reports) - len(failed),
        "failed": len(failed),
        "safety_case_count": len(safety_cases),
        "safety_passed": len(safety_cases) - len(safety_failed),
        "safety_failed": len(safety_failed),
        "generation_case_count": len(artifact_cases),
        "generation_passed": len(artifact_cases) - len(generation_failed),
        "generation_failed": len(generation_failed),
        "production_case_count": len(artifact_cases),
        "production_passed": len(artifact_cases) - len(production_failed),
        "production_failed": len(production_failed),
        "knowledge_candidate_count": len(knowledge_candidate_ids),
        "knowledge_candidate_ids": knowledge_candidate_ids,
        "evaluator_optimizer_summary": evaluator_optimizer_summary,
        "cases": case_reports,
    }


def _run_cases_with_timeout(
    case_inputs: list[dict[str, Any]],
    *,
    concurrency: int,
    case_timeout_seconds: int,
    on_case_complete: Any | None = None,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any] | None] = [None] * len(case_inputs)
    pending = list(enumerate(case_inputs))
    active: list[dict[str, Any]] = []
    context = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else mp.get_context()
    while pending or active:
        while pending and len(active) < concurrency:
            index, case_input = pending.pop(0)
            queue = context.Queue(maxsize=1)
            process = context.Process(target=_run_case_worker, args=(queue, case_input))
            process.start()
            active.append({"index": index, "input": case_input, "queue": queue, "process": process, "started": time.perf_counter()})
        for item in list(active):
            process = item["process"]
            queue = item["queue"]
            if not queue.empty():
                kind, payload = queue.get()
                process.join(timeout=1)
                reports[item["index"]] = payload if kind == "ok" else _case_process_error_report(item["input"], payload)
                if on_case_complete:
                    on_case_complete([report for report in reports if report is not None])
                active.remove(item)
            elif not process.is_alive():
                process.join(timeout=1)
                reports[item["index"]] = _case_process_error_report(item["input"], {"type": "ProcessExit", "message": f"case worker exited with code {process.exitcode}"})
                if on_case_complete:
                    on_case_complete([report for report in reports if report is not None])
                active.remove(item)
            elif time.perf_counter() - item["started"] > case_timeout_seconds:
                process.terminate()
                process.join(timeout=5)
                reports[item["index"]] = _write_timeout_case_artifacts(item["input"], case_timeout_seconds=case_timeout_seconds)
                if on_case_complete:
                    on_case_complete([report for report in reports if report is not None])
                active.remove(item)
        if pending or active:
            time.sleep(0.05)
    return [report for report in reports if report is not None]


def _run_case_worker(queue: Any, case_input: dict[str, Any]) -> None:
    try:
        queue.put(("ok", _run_case(**case_input)))
    except BaseException as exc:
        queue.put(("error", {"type": type(exc).__name__, "message": str(exc)}))


def _case_process_error_report(case_input: dict[str, Any], error: dict[str, Any]) -> dict[str, Any]:
    report = _base_failed_case_report(case_input, outcome="error", failure_class=FAILURE_TOOL_ERROR, failure_reason=error.get("message", "case worker failed"))
    report["error_type"] = error.get("type")
    _finish_case_report(report, time.perf_counter())
    write_json(Path(report["run_dir"]) / "case-eval.json", report)
    return report


def _write_timeout_case_artifacts(case_input: dict[str, Any], *, case_timeout_seconds: int) -> dict[str, Any]:
    report = _base_failed_case_report(
        case_input,
        outcome="timeout",
        failure_class=FAILURE_PROVIDER_TIMEOUT,
        failure_reason=f"case exceeded timeout of {case_timeout_seconds} seconds",
    )
    out_dir = Path(report["run_dir"])
    ensure_dir(out_dir)
    mirror_events = _load_proxy_attribution_events(out_dir / PROXY_ATTRIBUTION_EVENTS_PATH)
    record_timeout_mirror_events(
        user_tier=case_input["live_options"].user_tier,
        workflow=case_input["live_options"].workflow,
        events=mirror_events,
    )
    now = datetime.now(UTC).isoformat()
    run_id = out_dir.name
    knowledge_context = build_knowledge_context(case_input["case"]["prompt"]) if case_input["live_options"].knowledge_context == KNOWLEDGE_CONTEXT_AUTO else {}
    knowledge_info = knowledge_metadata(knowledge_context)
    attempt = {"stage": "case_timeout", "status": STATUS_FAIL, "failure_class": FAILURE_PROVIDER_TIMEOUT, "error": report["failure_reason"]}
    metadata = {
        "status": STATUS_FAIL,
        "workflow": case_input["live_options"].workflow,
        "user_tier": case_input["live_options"].user_tier,
        "attempts": [attempt],
        "stages": [],
        "repair_count": 0,
        "usage": {},
        "total_usage": {},
        "route_health_snapshot": [],
        "cooldown_skips": [],
        "fallback_count": 0,
        "final_route_by_stage": {},
        "stage_timeout_seconds": {},
        **knowledge_info,
    }
    workflow_trace = {
        "run_id": run_id,
        "workflow": case_input["live_options"].workflow,
        "user_tier": case_input["live_options"].user_tier,
        "attempts": [attempt],
        "stages": [],
        "route_health_snapshot": [],
        "cooldown_skips": [],
        "fallback_count": 0,
        "final_route_by_stage": {},
        "stage_timeout_seconds": {},
        "final_decision": {"status": STATUS_FAIL, "failure_class": FAILURE_PROVIDER_TIMEOUT, "failure_stage": "case_timeout"},
        "lifecycle_events": [
            {
                "sequence": 1,
                "created_at": now,
                "run_id": run_id,
                "event_type": "tool.failed",
                "policy_mode": case_input["policy"],
                "workflow": case_input["live_options"].workflow,
                "user_tier": case_input["live_options"].user_tier,
                "tool_id": "generate_live_strategy",
                "status": STATUS_FAIL,
                "failure_class": FAILURE_PROVIDER_TIMEOUT,
                "error": {"type": "TimeoutError", "message": report["failure_reason"]},
            }
        ],
    }
    live_error = {
        "code": FAILURE_PROVIDER_TIMEOUT,
        "message": report["failure_reason"],
        "attempts": [attempt],
        "diagnostics": {"metadata": metadata, "workflow_trace": workflow_trace, "final_decision": workflow_trace["final_decision"], "knowledge_context_artifact": knowledge_context},
    }
    timeout_output_refs = [LIVE_ERROR_PATH, "live-metadata.json", "agent-run.json", RUNTIME_TRACE_PATH, RUNTIME_SUMMARY_PATH]
    if (out_dir / PROXY_ATTRIBUTION_EVENTS_PATH).exists():
        timeout_output_refs.append(PROXY_ATTRIBUTION_EVENTS_PATH)
    if knowledge_context:
        timeout_output_refs.append(KNOWLEDGE_CONTEXT_PATH)
    agent_run = {
        "run_id": run_id,
        "created_at": now,
        "agent_role": "pine_specialist",
        "provider": "litellm",
        "model": "model-registry",
        "prompt_version": __version__,
        "input_refs": ["prompt"],
        "retrieved_sources": [*[f"doc:{doc_id}" for doc_id in knowledge_info["knowledge_doc_ids"]], *[f"source:{source_id}" for source_id in knowledge_info["external_source_ids"]]] or ["configs/source-registry.yaml"],
        "tool_calls": ["multi-model-live-generation"],
        "output_refs": timeout_output_refs,
        "validation_refs": [LIVE_ERROR_PATH],
        "status": STATUS_FAIL,
        "warnings": [report["failure_reason"]],
    }
    runtime_summary = {
        "run_id": run_id,
        "created_at": now,
        "policy_mode": case_input["policy"],
        "trace_ref": RUNTIME_TRACE_PATH,
        "event_count": 1,
        "completed_tools": [],
        "failed_tools": ["generate_live_strategy"],
        "blocked_tools": [],
        "output_refs": agent_run["output_refs"],
    }
    write_json(out_dir / LIVE_ERROR_PATH, live_error)
    write_json(out_dir / "live-metadata.json", metadata)
    write_json(out_dir / LIVE_WORKFLOW_TRACE_PATH, workflow_trace)
    if knowledge_context:
        write_json(out_dir / KNOWLEDGE_CONTEXT_PATH, knowledge_context)
    write_json(out_dir / "agent-run.json", agent_run)
    (out_dir / RUNTIME_TRACE_PATH).write_text(json.dumps(workflow_trace["lifecycle_events"][0], ensure_ascii=False) + "\n", encoding="utf-8")
    write_json(out_dir / RUNTIME_SUMMARY_PATH, runtime_summary)
    report["failure_stage"] = "case_timeout"
    report["failure_attempts"] = [attempt]
    report["artifact_refs"] = {
        LIVE_ERROR_PATH: LIVE_ERROR_PATH,
        LIVE_WORKFLOW_TRACE_PATH: LIVE_WORKFLOW_TRACE_PATH,
        "live-metadata.json": "live-metadata.json",
        "agent-run.json": "agent-run.json",
        RUNTIME_TRACE_PATH: RUNTIME_TRACE_PATH,
        RUNTIME_SUMMARY_PATH: RUNTIME_SUMMARY_PATH,
    }
    if (out_dir / PROXY_ATTRIBUTION_EVENTS_PATH).exists():
        report["artifact_refs"][PROXY_ATTRIBUTION_EVENTS_PATH] = PROXY_ATTRIBUTION_EVENTS_PATH
    if knowledge_context:
        report["artifact_refs"][KNOWLEDGE_CONTEXT_PATH] = KNOWLEDGE_CONTEXT_PATH
    report["knowledge_context_ref"] = knowledge_info["knowledge_context_ref"]
    report["knowledge_doc_ids"] = knowledge_info["knowledge_doc_ids"]
    report["external_source_ids"] = knowledge_info["external_source_ids"]
    report["generation_gate"] = {"status": STATUS_FAIL, "reason": "case_timeout"}
    report["production_gate"] = {"status": STATUS_FAIL, "reason": "case_timeout"}
    report["knowledge_candidate_count"] = 0
    report["knowledge_candidate_ids"] = []
    report["knowledge_candidate_error"] = None
    report["route_health_snapshot"] = []
    report["cooldown_skips"] = []
    report["fallback_count"] = 0
    report["final_route_by_stage"] = {}
    report["stage_timeout_seconds"] = {}
    _attach_failure_candidate(report, case_input["live_options"])
    _finish_case_report(report, time.perf_counter() - case_timeout_seconds)
    write_json(out_dir / "case-eval.json", report)
    return report


def _load_proxy_attribution_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
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


def _base_failed_case_report(case_input: dict[str, Any], *, outcome: str, failure_class: str, failure_reason: str) -> dict[str, Any]:
    case = case_input["case"]
    out_dir = case_input["out_dir"]
    return {
        "id": _case_id(case),
        "name": case.get("name", _case_id(case)),
        "expected_outcome": case.get("expected_outcome", STATUS_PASS),
        "expected_statuses": case.get("expected_statuses", [STATUS_PASS]),
        "run_dir": str(out_dir),
        "status": STATUS_FAIL,
        "outcome": outcome,
        "failure_reason": failure_reason,
        "failure_class": failure_class,
        "failure_attribution": [{"failure_class": failure_class, "details": failure_reason}],
        "validation_status": None,
        "validation_failures": [],
        "validation_warnings": [],
        "latest_validation_ref": None,
        "review_decision": None,
        "model": None,
        "provider": None,
        "latency_ms": None,
        "usage": {},
        "workflow": case_input["live_options"].workflow,
        "user_tier": case_input["live_options"].user_tier,
        "stages": [],
        "repair_count": 0,
        "total_usage": {},
        "quality_status": None,
        "quality_score": None,
        "quality_blockers": [],
        "quality_warnings": [],
        "knowledge_context_ref": None,
        "knowledge_doc_ids": [],
        "external_source_ids": [],
        "raw_provider_response_ref": None,
        "otel_export_ref": None,
        "failure_stage": None,
        "failure_attempts": [],
        "completed_stages": [],
        "review_findings": {},
        "repair_history": [],
        "artifact_refs": {},
        "case_started_at": datetime.now(UTC).isoformat(),
        "case_completed_at": None,
        "case_duration_ms": None,
        "safety_gate": {},
        "generation_gate": {},
        "production_gate": {},
        "evaluator_optimizer_summary": {},
        "knowledge_candidate_count": 0,
        "knowledge_candidate_ids": [],
        "knowledge_candidate_error": None,
        "route_health_snapshot": [],
        "cooldown_skips": [],
        "fallback_count": 0,
        "final_route_by_stage": {},
        "stage_timeout_seconds": {},
    }


def _run_case(
    *,
    case: dict[str, Any],
    out_dir: Path,
    policy: str,
    model_registry: Path,
    live_options: LiveRunOptions,
) -> dict[str, Any]:
    ensure_dir(out_dir)
    started_at = datetime.now(UTC)
    started_timer = time.perf_counter()
    expected_outcome = case.get("expected_outcome", STATUS_PASS)
    expected_statuses = case.get("expected_statuses", [STATUS_PASS])
    prompt = case["prompt"]
    report: dict[str, Any] = {
        "id": _case_id(case),
        "name": case.get("name", _case_id(case)),
        "expected_outcome": expected_outcome,
        "expected_statuses": expected_statuses,
        "run_dir": str(out_dir),
        "status": STATUS_FAIL,
        "outcome": "not_run",
        "failure_reason": None,
        "validation_status": None,
        "validation_failures": [],
        "validation_warnings": [],
        "latest_validation_ref": None,
        "review_decision": None,
        "model": None,
        "provider": None,
        "latency_ms": None,
        "usage": {},
        "workflow": None,
        "user_tier": live_options.user_tier,
        "stages": [],
        "repair_count": 0,
        "total_usage": {},
        "raw_provider_response_ref": None,
        "otel_export_ref": None,
        "failure_attribution": [],
        "failure_stage": None,
        "failure_class": None,
        "failure_attempts": [],
        "completed_stages": [],
        "review_findings": {},
        "repair_history": [],
        "artifact_refs": {},
        "quality_status": None,
        "quality_score": None,
        "quality_blockers": [],
        "quality_warnings": [],
        "knowledge_context_ref": None,
        "knowledge_doc_ids": [],
        "external_source_ids": [],
        "case_started_at": started_at.isoformat(),
        "case_completed_at": None,
        "case_duration_ms": None,
        "safety_gate": {},
        "generation_gate": {},
        "production_gate": {},
        "knowledge_candidate_count": 0,
        "knowledge_candidate_ids": [],
        "knowledge_candidate_error": None,
        "route_health_snapshot": [],
        "cooldown_skips": [],
        "fallback_count": 0,
        "final_route_by_stage": {},
        "stage_timeout_seconds": {},
    }
    if expected_outcome == "blocked":
        prompt_findings = _dedupe_policy_findings([*find_blocked_claims(prompt), *find_prompt_boundary_violations(prompt)])
        if prompt_findings:
            report.update(
                {
                    "status": STATUS_PASS,
                    "outcome": "blocked",
                    "failure_reason": None,
                    "failure_class": FAILURE_POLICY_VIOLATION,
                    "safety_gate": {
                        "status": STATUS_PASS,
                        "blocked_at": "prompt",
                        "failure_class": FAILURE_POLICY_VIOLATION,
                        "policy_findings": prompt_findings,
                    },
                    "generation_gate": {"status": STATUS_SKIPPED, "reason": "expected_blocked_case"},
                    "production_gate": {"status": STATUS_SKIPPED, "reason": "expected_blocked_case"},
                }
            )
            _finish_case_report(report, started_timer)
            write_json(out_dir / "case-eval.json", report)
            return report
    case_options = live_options
    try:
        case_options = LiveRunOptions(
            model_override=live_options.model_override or case.get("model"),
            model_stage_overrides={**live_options.model_stage_overrides, **case.get("model_stages", {})},
            workflow=case.get("workflow", live_options.workflow),
            cost_profile=case.get("cost_profile", live_options.cost_profile),
            user_tier=case.get("user_tier", live_options.user_tier),
            save_raw_provider=live_options.save_raw_provider,
            knowledge_context=case.get("knowledge_context", live_options.knowledge_context),
            prompt_profile=live_options.prompt_profile,
            case_id=_case_id(case),
            route_health=live_options.route_health,
            proxy_attribution_path=out_dir / PROXY_ATTRIBUTION_EVENTS_PATH,
        )
        result = run_strategy(
            spec_path=None,
            prompt=prompt,
            mode="live",
            out_dir=out_dir,
            review=case.get("review", REVIEW_MODE_NONE),
            record_harness=False,
            policy=policy,
            model_registry=model_registry,
            live_options=case_options,
        )
        validation = load_json(out_dir / "validation-report.json")
        metadata = load_json(out_dir / "live-metadata.json")
        workflow_trace = load_json(out_dir / LIVE_WORKFLOW_TRACE_PATH) if (out_dir / LIVE_WORKFLOW_TRACE_PATH).exists() else {}
        quality_report = load_json(out_dir / QUALITY_REPORT_PATH) if (out_dir / QUALITY_REPORT_PATH).exists() else metadata.get("quality_report", {})
        report.update(
            {
                "outcome": "completed",
                "validation_status": validation["status"],
                "validation_failures": _validation_failures(validation),
                "validation_warnings": validation.get("warnings", []),
                "latest_validation_ref": "validation-report.json",
                "model": metadata.get("model"),
                "provider": metadata.get("provider"),
                "latency_ms": metadata.get("total_latency_ms", metadata.get("latency_ms")),
                "usage": metadata.get("usage", {}),
                "workflow": metadata.get("workflow"),
                "user_tier": metadata.get("user_tier"),
                "stages": metadata.get("stages", []),
                "repair_count": metadata.get("repair_count", 0),
                "total_usage": metadata.get("total_usage", metadata.get("usage", {})),
                "knowledge_context_ref": metadata.get("knowledge_context_ref"),
                "knowledge_doc_ids": metadata.get("knowledge_doc_ids", []),
                "external_source_ids": metadata.get("external_source_ids", []),
                "route_health_snapshot": metadata.get("route_health_snapshot", []),
                "cooldown_skips": metadata.get("cooldown_skips", []),
                "fallback_count": metadata.get("fallback_count", 0),
                "fallback_gateway_count": metadata.get("fallback_gateway_count", 0),
                "final_route_by_stage": metadata.get("final_route_by_stage", {}),
                "stage_timeout_seconds": metadata.get("stage_timeout_seconds", {}),
                "free_catalog": metadata.get("free_catalog", {}),
                "free_catalog_ref": metadata.get("free_catalog_ref"),
                "catalog_age_seconds": metadata.get("catalog_age_seconds"),
                "selected_free_models": metadata.get("selected_free_models", []),
                "free_capacity_status": metadata.get("free_capacity_status"),
                **quality_metadata(quality_report),
                "generation_gate": metadata.get("generation_gate") or _generation_gate_from_validation(validation),
                "production_gate": metadata.get("production_gate") or _production_gate_from_completed_run(validation, report.get("review_decision")),
            }
        )
        harness_report = inspect_run(out_dir)
        report["failure_attribution"] = harness_report["failure_attribution"]
        required_live_artifacts = {"agent-run.json", "validation-report.json", "live-metadata.json"}
        if metadata.get("workflow") in {WORKFLOW_MULTI_AGENT, WORKFLOW_COMPACT_FREE}:
            required_live_artifacts.add("live-workflow-trace.json")
        if metadata.get("knowledge_context_ref"):
            required_live_artifacts.add(KNOWLEDGE_CONTEXT_PATH)
        missing_required = sorted(required_live_artifacts & set(harness_report["missing_artifacts"]))
        if missing_required:
            report["failure_attribution"].extend(
                {"failure_class": FAILURE_ARTIFACT_MISSING, "artifact": artifact, "details": f"Missing required live artifact {artifact}."}
                for artifact in missing_required
            )
        if (out_dir / "live-provider-response.json").exists():
            report["raw_provider_response_ref"] = "live-provider-response.json"
        if (out_dir / QUALITY_REPORT_PATH).exists():
            report["artifact_refs"][QUALITY_REPORT_PATH] = QUALITY_REPORT_PATH
        if (out_dir / KNOWLEDGE_CONTEXT_PATH).exists():
            report["artifact_refs"][KNOWLEDGE_CONTEXT_PATH] = KNOWLEDGE_CONTEXT_PATH
        if (out_dir / "review-report.json").exists():
            review = load_json(out_dir / "review-report.json")
            report["review_decision"] = review.get("decision")
            if not metadata.get("production_gate"):
                report["production_gate"] = _production_gate_from_completed_run(validation, report["review_decision"])
            elif _review_decision_blocks_production(report["review_decision"]):
                report["production_gate"] = {
                    **report["production_gate"],
                    "status": STATUS_FAIL,
                    "review_decision": report["review_decision"],
                }
        else:
            review = None
        report["evaluator_optimizer_summary"] = _evaluator_optimizer_summary_from_artifacts(
            metadata=metadata,
            workflow_trace=workflow_trace if isinstance(workflow_trace, dict) else {},
            diagnostics={},
            validation=validation,
            production_gate=report.get("production_gate", {}),
            review_report=review if isinstance(review, dict) else None,
        )
        evaluator_event = _ensure_evaluator_optimizer_lifecycle_event(
            workflow_trace,
            report["evaluator_optimizer_summary"],
            policy=policy,
            run_id=out_dir.name,
            workflow=metadata.get("workflow") or workflow,
            user_tier=metadata.get("user_tier") or case_options.user_tier,
        )
        if evaluator_event is not None:
            report["evaluator_optimizer_event"] = evaluator_event
            if (out_dir / LIVE_WORKFLOW_TRACE_PATH).exists():
                write_json(out_dir / LIVE_WORKFLOW_TRACE_PATH, workflow_trace)
        generation_passed = report.get("generation_gate", {}).get("status") == STATUS_PASS
        if report["failure_attribution"] and not generation_passed:
            report["failure_reason"] = "live harness gate failed"
        elif expected_outcome == STATUS_PASS and (result["status"] in expected_statuses or validation["status"] in expected_statuses or report["generation_gate"].get("status") in expected_statuses):
            report["status"] = STATUS_PASS
        elif expected_outcome == "blocked":
            report["safety_gate"] = {"status": STATUS_FAIL, "reason": "expected policy block but run completed"}
            report["failure_reason"] = "expected policy block but run completed"
        else:
            report["failure_reason"] = f"unexpected run status {result['status']}"
    except ToolBlockedError as exc:
        report.update({"outcome": "blocked", "failure_reason": str(exc), "generation_gate": {"status": STATUS_SKIPPED, "reason": "blocked"}, "production_gate": {"status": STATUS_SKIPPED, "reason": "blocked"}})
        if expected_outcome == "blocked":
            report["status"] = STATUS_PASS
            report["failure_reason"] = None
            report["failure_class"] = FAILURE_POLICY_VIOLATION
            report["safety_gate"] = {"status": STATUS_PASS, "blocked_at": "tool", "failure_class": FAILURE_POLICY_VIOLATION}
    except LiveError as exc:
        error_report = _load_live_error(out_dir) or live_error_report(exc)
        report.update({"outcome": "live_error", "failure_reason": str(exc), "live_error": error_report})
        _enrich_failure_report(report, out_dir, error_report)
        report["failure_attribution"] = [
            failure_from_attempt(attempt)
            for attempt in error_report.get("attempts", [])
            if attempt.get("status") in {STATUS_FAIL, STATUS_SKIPPED}
        ]
        if report.get("failure_class") and not report["failure_attribution"]:
            report["failure_attribution"] = [{"failure_class": report["failure_class"], "stage": report.get("failure_stage"), "details": report["failure_reason"]}]
        if expected_outcome == "blocked" and report.get("failure_class") == "policy_violation":
            report["status"] = STATUS_PASS
            report["failure_reason"] = None
            report["safety_gate"] = {"status": STATUS_PASS, "blocked_at": report.get("failure_stage") or "live", "failure_class": FAILURE_POLICY_VIOLATION}
            report["generation_gate"] = {"status": STATUS_SKIPPED, "reason": "expected_blocked_case"}
            report["production_gate"] = {"status": STATUS_SKIPPED, "reason": "expected_blocked_case"}
        if expected_outcome == STATUS_PASS and report.get("validation_status") in expected_statuses and report.get("validation_status") != STATUS_PASS:
            report["status"] = STATUS_PASS
            report["failure_reason"] = None
    except Exception as exc:
        report.update({"outcome": "error", "failure_reason": str(exc), "error_type": type(exc).__name__})
    _attach_failure_candidate(report, case_options)
    _finish_case_report(report, started_timer)
    write_json(out_dir / "case-eval.json", report)
    return report


def _finish_case_report(report: dict[str, Any], started_timer: float) -> None:
    report["case_completed_at"] = datetime.now(UTC).isoformat()
    report["case_duration_ms"] = int((time.perf_counter() - started_timer) * 1000)


def _attach_failure_candidate(report: dict[str, Any], live_options: LiveRunOptions) -> None:
    if live_options.knowledge_context != KNOWLEDGE_CONTEXT_AUTO:
        return
    if report.get("status") == STATUS_PASS:
        return
    candidates_path = os.getenv("STRATEGY_CODEBOT_KNOWLEDGE_CANDIDATES_PATH")
    if not os.getenv(KNOWLEDGE_DATABASE_URL_ENV) and not candidates_path:
        return
    failure_class = report.get("failure_class")
    if not failure_class:
        attributions = report.get("failure_attribution") or []
        if attributions and isinstance(attributions[0], dict):
            failure_class = attributions[0].get("failure_class")
    if not failure_class:
        return
    try:
        candidate = propose_failure_candidate(
            {
                **report,
                "failure_class": failure_class,
                "case_id": report.get("id"),
            },
            evidence_ref=f"eval-case:{report.get('id')}:{report.get('run_dir')}",
            path=Path(candidates_path) if candidates_path else None,
        )
    except Exception as exc:
        report["knowledge_candidate_error"] = str(exc)
        return
    if not candidate:
        return
    report["knowledge_candidate_count"] = 1
    report["knowledge_candidate_ids"] = [candidate.get("candidate_id")]
    if candidate.get("deduped"):
        report["knowledge_candidate_deduped"] = True


def _dedupe_policy_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for finding in findings:
        key = (str(finding.get("claim")), str(finding.get("sentence")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped


def _load_live_error(out_dir: Path) -> dict[str, Any] | None:
    path = out_dir / LIVE_ERROR_PATH
    if not path.exists():
        return None
    payload = load_json(path)
    return payload if isinstance(payload, dict) else None


def _enrich_failure_report(report: dict[str, Any], out_dir: Path, error_report: dict[str, Any]) -> None:
    diagnostics = error_report.get("diagnostics", {}) if isinstance(error_report.get("diagnostics"), dict) else {}
    metadata = diagnostics.get("metadata", {}) if isinstance(diagnostics.get("metadata"), dict) else {}
    workflow_trace = diagnostics.get("workflow_trace", {}) if isinstance(diagnostics.get("workflow_trace"), dict) else {}
    final_decision = diagnostics.get("final_decision", {}) if isinstance(diagnostics.get("final_decision"), dict) else {}
    attempts = error_report.get("attempts", [])
    failed_attempts = [attempt for attempt in attempts if attempt.get("status") in {STATUS_FAIL, STATUS_SKIPPED}]
    last_failure = failed_attempts[-1] if failed_attempts else {}

    report["failure_stage"] = final_decision.get("failure_stage") or last_failure.get("stage")
    report["failure_class"] = final_decision.get("failure_class") or last_failure.get("failure_class") or last_failure.get("error_code")
    report["failure_attempts"] = failed_attempts
    report["completed_stages"] = [stage.get("stage") for stage in diagnostics.get("stage_records", []) or metadata.get("stages", [])]
    report["review_findings"] = diagnostics.get("review_findings", {})
    report["repair_history"] = diagnostics.get("repair_history", workflow_trace.get("repair_history", []))
    report.update(quality_metadata(diagnostics.get("quality_report") or metadata.get("quality_report")))
    knowledge_info = knowledge_metadata(diagnostics.get("knowledge_context_artifact") or {})
    report["knowledge_context_ref"] = metadata.get("knowledge_context_ref") or knowledge_info["knowledge_context_ref"]
    report["knowledge_doc_ids"] = metadata.get("knowledge_doc_ids", knowledge_info["knowledge_doc_ids"])
    report["external_source_ids"] = metadata.get("external_source_ids", knowledge_info["external_source_ids"])
    report["workflow"] = metadata.get("workflow") or diagnostics.get("workflow")
    report["user_tier"] = metadata.get("user_tier") or diagnostics.get("user_tier")
    report["stages"] = metadata.get("stages", [])
    report["repair_count"] = metadata.get("repair_count", 0)
    report["total_usage"] = metadata.get("total_usage", {})
    report["usage"] = metadata.get("usage", {})
    report["route_health_snapshot"] = metadata.get("route_health_snapshot") or diagnostics.get("route_health_snapshot", [])
    report["cooldown_skips"] = metadata.get("cooldown_skips") or diagnostics.get("cooldown_skips", [])
    report["fallback_count"] = metadata.get("fallback_count", diagnostics.get("fallback_count", 0))
    report["fallback_gateway_count"] = metadata.get("fallback_gateway_count", diagnostics.get("fallback_gateway_count", 0))
    report["final_route_by_stage"] = metadata.get("final_route_by_stage") or diagnostics.get("final_route_by_stage", {})
    report["stage_timeout_seconds"] = metadata.get("stage_timeout_seconds") or diagnostics.get("stage_timeout_seconds", {})
    free_catalog = metadata.get("free_catalog") or diagnostics.get("free_catalog", {})
    report["free_catalog"] = free_catalog
    report["free_catalog_ref"] = metadata.get("free_catalog_ref") or free_catalog.get("free_catalog_ref")
    report["catalog_age_seconds"] = metadata.get("catalog_age_seconds", free_catalog.get("catalog_age_seconds"))
    report["selected_free_models"] = metadata.get("selected_free_models") or free_catalog.get("selected_free_models", [])
    report["free_capacity_status"] = metadata.get("free_capacity_status") or free_catalog.get("free_capacity_status")
    report["generation_gate"] = metadata.get("generation_gate") or diagnostics.get("generation_gate", {})
    report["production_gate"] = metadata.get("production_gate") or diagnostics.get("production_gate", {})
    validation = diagnostics.get("validation") or metadata.get("validation") or {}
    report["evaluator_optimizer_summary"] = _evaluator_optimizer_summary_from_artifacts(
        metadata=metadata,
        workflow_trace=workflow_trace,
        diagnostics=diagnostics,
        validation=validation,
        production_gate=report.get("production_gate", {}),
        review_report=None,
    )
    report["validation_status"] = validation.get("status") or final_decision.get("validation_status")
    report["validation_failures"] = diagnostics.get("validation_failures") or metadata.get("validation_failures") or _validation_failures(validation)
    report["validation_warnings"] = diagnostics.get("validation_warnings") or metadata.get("validation_warnings") or validation.get("warnings", [])
    report["latest_validation_ref"] = f"{LIVE_ERROR_PATH}#/diagnostics/validation" if validation else None
    report["latency_ms"] = metadata.get("total_latency_ms", metadata.get("latency_ms"))
    report["model"] = metadata.get("model")
    report["provider"] = metadata.get("provider")
    refs = {
        name: name
        for name in (LIVE_ERROR_PATH, LIVE_WORKFLOW_TRACE_PATH, "live-provider-response.json", QUALITY_REPORT_PATH, KNOWLEDGE_CONTEXT_PATH, "runtime-trace.jsonl", "runtime-summary.json")
        if (out_dir / name).exists()
    }
    report["artifact_refs"] = refs
    if (out_dir / "live-provider-response.json").exists():
        report["raw_provider_response_ref"] = "live-provider-response.json"

def _generation_gate_from_validation(validation: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": STATUS_PASS if _validation_allows_artifact(validation) else STATUS_FAIL,
        "validation_status": validation.get("status"),
        "validation_failures": _validation_failures(validation),
    }


def _production_gate_from_completed_run(validation: dict[str, Any], review_decision: str | None) -> dict[str, Any]:
    review_clean = not _review_decision_blocks_production(review_decision)
    return {
        "status": STATUS_PASS if _validation_allows_artifact(validation) and review_clean else STATUS_FAIL,
        "validation_status": validation.get("status"),
        "review_decision": review_decision,
    }


def _gate_status(case: dict[str, Any], gate_name: str, *, fallback: Any = None) -> Any:
    gate = case.get(gate_name)
    if isinstance(gate, dict):
        return gate.get("status", fallback)
    return fallback


def _build_eval_evaluator_optimizer_summary(case_reports: list[dict[str, Any]]) -> dict[str, Any]:
    summaries = [
        summary
        for case in case_reports
        if isinstance((summary := case.get("evaluator_optimizer_summary")), dict) and summary
    ]
    repair_source_mix = {"llm": 0, "deterministic": 0, "unknown": 0}
    for summary in summaries:
        source_mix = summary.get("repair_source_mix", {})
        if not isinstance(source_mix, dict):
            continue
        for source in repair_source_mix:
            repair_source_mix[source] += _nonnegative_int(source_mix.get(source))
    return {
        "case_count": len(summaries),
        "repair_count": sum(_nonnegative_int(summary.get("repair_count")) for summary in summaries),
        "repair_source_mix": repair_source_mix,
        "budget_exhausted_count": sum(1 for summary in summaries if summary.get("budget_exhausted") is True),
        "stop_reasons": _count_summary_values(summaries, "stop_reason"),
        "final_validation_statuses": _count_summary_values(summaries, "final_validation_status"),
        "final_review_statuses": _count_summary_values(summaries, "final_review_status"),
    }


def _evaluator_optimizer_summary_from_artifacts(
    *,
    metadata: dict[str, Any],
    workflow_trace: dict[str, Any],
    diagnostics: dict[str, Any],
    validation: dict[str, Any],
    production_gate: dict[str, Any],
    review_report: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = _existing_evaluator_optimizer_summary(metadata, workflow_trace, diagnostics)
    if summary:
        return summary

    final_decision = workflow_trace.get("final_decision", {}) if isinstance(workflow_trace.get("final_decision"), dict) else {}
    if not final_decision and isinstance(diagnostics.get("final_decision"), dict):
        final_decision = diagnostics["final_decision"]
    repair_history = _repair_history_from_artifacts(metadata, workflow_trace, diagnostics)
    repair_count = _nonnegative_int(metadata.get("repair_count", final_decision.get("repair_count", len(repair_history))))
    repair_source_mix = _repair_source_mix_from_history(
        repair_history,
        repair_count=repair_count,
        llm_repair_count=metadata.get("llm_repair_count", final_decision.get("llm_repair_count")),
        deterministic_repair_count=metadata.get("deterministic_repair_count", final_decision.get("deterministic_repair_count")),
    )
    final_review_status = _final_review_status_from_artifacts(review_report, production_gate, final_decision)
    budget_exhausted = bool(
        metadata.get("repair_budget_exhausted")
        or production_gate.get("repair_budget_exhausted")
        or final_decision.get("repair_budget_exhausted")
    )
    policy_findings = _evaluator_optimizer_policy_findings(metadata, workflow_trace, diagnostics, production_gate, final_decision)
    return {
        "stop_reason": _evaluator_optimizer_stop_reason(
            validation=validation,
            final_review_status=final_review_status,
            production_gate=production_gate,
            policy_findings=policy_findings,
            budget_exhausted=budget_exhausted,
        ),
        "repair_count": repair_count,
        "repair_source_mix": repair_source_mix,
        "final_validation_status": validation.get("status") or production_gate.get("validation_status") or final_decision.get("validation_status"),
        "final_review_status": final_review_status,
        "budget_exhausted": budget_exhausted,
    }


def _existing_evaluator_optimizer_summary(
    metadata: dict[str, Any],
    workflow_trace: dict[str, Any],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    sources = [metadata, diagnostics]
    final_decision = workflow_trace.get("final_decision", {}) if isinstance(workflow_trace.get("final_decision"), dict) else {}
    sources.extend([workflow_trace, final_decision])
    for source in sources:
        summary = source.get("evaluator_optimizer_summary") if isinstance(source, dict) else None
        if isinstance(summary, dict) and summary:
            return summary
    return {}


def _ensure_evaluator_optimizer_lifecycle_event(
    workflow_trace: dict[str, Any],
    summary: dict[str, Any],
    *,
    policy: str,
    run_id: str,
    workflow: str | None,
    user_tier: str | None,
) -> dict[str, Any] | None:
    if not isinstance(workflow_trace, dict) or not summary:
        return None
    lifecycle_events = workflow_trace.setdefault("lifecycle_events", [])
    if not isinstance(lifecycle_events, list):
        lifecycle_events = []
        workflow_trace["lifecycle_events"] = lifecycle_events
    existing = next(
        (
            event
            for event in lifecycle_events
            if isinstance(event, dict) and event.get("event_type") == "evaluator_optimizer.summary"
        ),
        None,
    )
    if existing is not None:
        return existing
    event = {
        "event_id": f"evt-{len(lifecycle_events) + 1}",
        "sequence": len(lifecycle_events) + 1,
        "created_at": datetime.now(UTC).isoformat(),
        "run_id": workflow_trace.get("run_id") or run_id,
        "workflow": workflow or workflow_trace.get("workflow"),
        "user_tier": user_tier or workflow_trace.get("user_tier"),
        "event_type": "evaluator_optimizer.summary",
        "policy_mode": policy,
        "status": STATUS_PASS,
        "stage": "evaluator_optimizer",
        "agent_role": "evaluator_optimizer",
        "stop_reason": summary.get("stop_reason"),
        "repair_count": summary.get("repair_count"),
        "repair_source_mix": summary.get("repair_source_mix"),
        "final_validation_status": summary.get("final_validation_status"),
        "final_review_status": summary.get("final_review_status"),
        "budget_exhausted": summary.get("budget_exhausted"),
        "output_summary": summary.get("stop_reason"),
    }
    lifecycle_events.append({key: value for key, value in event.items() if value is not None})
    return lifecycle_events[-1]


def _repair_history_from_artifacts(
    metadata: dict[str, Any],
    workflow_trace: dict[str, Any],
    diagnostics: dict[str, Any],
) -> list[dict[str, Any]]:
    for source in (diagnostics, workflow_trace, metadata):
        history = source.get("repair_history") if isinstance(source, dict) else None
        if isinstance(history, list):
            return [entry for entry in history if isinstance(entry, dict)]
    return []


def _final_review_status_from_artifacts(
    review_report: dict[str, Any] | None,
    production_gate: dict[str, Any],
    final_decision: dict[str, Any],
) -> str | None:
    return evaluator_review_status(
        review_report=review_report,
        production_gate=production_gate,
        final_decision=final_decision,
    )


def _evaluator_optimizer_policy_findings(*sources: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for source in sources:
        source_findings = source.get("policy_findings") if isinstance(source, dict) else None
        if isinstance(source_findings, list):
            findings.extend(finding for finding in source_findings if isinstance(finding, dict))
    return findings


def _count_summary_values(summaries: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for summary in summaries:
        value = summary.get(field)
        if value is None:
            continue
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _is_expected_blocked(case: dict[str, Any]) -> bool:
    return case.get("expected_outcome") == "blocked"


def _review_decision_blocks_production(review_decision: str | None) -> bool:
    return str(review_decision).lower() in {STATUS_FAIL, "fail", "failed", "reject", "rejected", "block", "blocked"}


def _case_id(case: dict[str, Any]) -> str:
    raw = str(case.get("id") or case.get("name") or "case")
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in raw).strip("-") or "case"
