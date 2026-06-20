from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
import json
import re
from typing import Any

from strategy_codebot.evals import EVAL_REPORT_PATH, run_live_eval
from strategy_codebot.harness_types import STATUS_FAIL, STATUS_PASS, STATUS_SKIPPED
from strategy_codebot.knowledge_base import propose_failure_candidate
from strategy_codebot.paths import repo_root
from strategy_codebot.prompt_contracts import DEFAULT_PROMPT_PROFILE, normalize_prompt_profile, normalize_prompt_profiles
from strategy_codebot.route_health import route_health_report
from strategy_codebot.schemas import load_json, write_json


DEFAULT_INTELLIGENCE_REPORT_PATH = Path(".strategy-codebot/harness-intelligence.json")
DEFAULT_INTELLIGENCE_PROPOSALS_PATH = Path(".strategy-codebot/harness-intelligence-proposals.json")
DEFAULT_INTELLIGENCE_REPLAY_PATH = Path(".strategy-codebot/harness-intelligence-replay.json")
DEFAULT_INTELLIGENCE_IMPROVEMENTS_PATH = Path(".strategy-codebot/harness-intelligence-improvements.json")
DEFAULT_INTELLIGENCE_PATCH_PATH = Path(".strategy-codebot/harness-intelligence-approved-patch.json")
DEFAULT_LATENCY_REPORT_PATH = Path(".strategy-codebot/harness-latency.json")
DEFAULT_LATENCY_MATRIX_PATH = Path(".strategy-codebot/harness-latency-matrix.json")
DEFAULT_PROXY_LOG_REPORT_PATH = Path(".strategy-codebot/harness-proxy-log.json")
DEFAULT_CONTEXT_REPORT_PATH = Path(".strategy-codebot/harness-context.json")
DEFAULT_ROUTE_HEALTH_REPORT_PATH = Path(".strategy-codebot/harness-route-health.json")
DEFAULT_PROMPT_MATRIX_PATH = Path(".strategy-codebot/harness-prompt-matrix.json")

STAGE_CONTEXT_BUDGETS = {
    "strategy_reasoning": 20_000,
    "strategy_coding": 28_000,
    "pine_code_generation": 12_000,
    "balanced_review": 18_000,
    "repair": 50_000,
}


def build_intelligence_report(*, artifacts_root: Path | None = None) -> dict[str, Any]:
    root = _resolve_artifacts_root(artifacts_root)
    reports = _collect_json_reports(root)
    eval_reports = [report for path, report in reports if _looks_like_eval_report(path, report)]
    case_reports = [report for path, report in reports if _looks_like_case_report(path, report)]
    live_traces = [report for path, report in reports if _looks_like_live_trace(path, report)]
    quality_reports = [report for path, report in reports if _looks_like_quality_report(path, report)]
    route_rows = _route_rows(case_reports, live_traces)
    failure_rows = _failure_rows(case_reports, live_traces)
    sophistication_rows = _sophistication_rows(quality_reports, live_traces)
    scorecard = _scorecard(route_rows)
    recommendations = _route_recommendations(scorecard)
    latency_summary = _latency_summary(live_traces)
    sophistication_summary = _sophistication_summary(sophistication_rows)
    persisted_route_health = route_health_report()
    return {
        "status": STATUS_PASS,
        "created_at": _now(),
        "artifacts_root": str(root),
        "report_count": len(reports),
        "case_count": len(case_reports),
        "live_trace_count": len(live_traces),
        "evidence_completeness": _evidence_completeness(eval_reports, case_reports, live_traces),
        "scorecard": scorecard,
        "latency_summary": latency_summary,
        "persisted_route_health": persisted_route_health,
        "sophistication_summary": sophistication_summary,
        "failure_summary": _failure_summary(failure_rows),
        "failure_signatures": _failure_signatures(failure_rows),
        "sophistication_signatures": _sophistication_signatures(sophistication_rows),
        "route_recommendations": recommendations,
        "proposal_seed_count": len(_proposal_seeds(failure_rows)) + len(_sophistication_proposal_seeds(sophistication_rows)),
        "anti_pollution": {"writes_memory": False, "mutates_registry": False, "proposals_only": True},
    }


def build_latency_report(*, artifacts_root: Path | None = None) -> dict[str, Any]:
    root = _resolve_artifacts_root(artifacts_root)
    reports = _collect_json_reports(root)
    live_traces = [report for path, report in reports if _looks_like_live_trace(path, report)]
    return {
        "status": STATUS_PASS,
        "created_at": _now(),
        "artifacts_root": str(root),
        "live_trace_count": len(live_traces),
        "latency_summary": _latency_summary(live_traces),
        "persisted_route_health": route_health_report(),
        "anti_pollution": {"writes_memory": False, "mutates_registry": False, "analysis_only": True},
    }


def build_context_report(*, artifacts_root: Path | None = None, out: Path | None = None) -> dict[str, Any]:
    root = _resolve_artifacts_root(artifacts_root)
    reports = _collect_json_reports(root)
    live_traces = [report for path, report in reports if _looks_like_live_trace(path, report)]
    stage_reports = []
    missing_count = 0
    budget_warning_count = 0
    for trace in live_traces:
        trace_stage_reports = _context_stage_reports(trace)
        for report in trace_stage_reports:
            if report["missing_context"]:
                missing_count += 1
            if report["budget_status"] == "warn":
                budget_warning_count += 1
        stage_reports.extend(trace_stage_reports)
    payload = {
        "status": STATUS_FAIL if missing_count else STATUS_PASS,
        "created_at": _now(),
        "artifacts_root": str(root),
        "live_trace_count": len(live_traces),
        "stage_count": len(stage_reports),
        "missing_context_count": missing_count,
        "budget_warning_count": budget_warning_count,
        "budgets": STAGE_CONTEXT_BUDGETS,
        "stage_reports": stage_reports,
        "anti_pollution": {"writes_memory": False, "mutates_registry": False, "analysis_only": True},
    }
    if out:
        write_json(out, payload)
    return payload


def build_latency_matrix(
    *,
    suite: Path,
    out_root: Path,
    runs: int = 3,
    policy: str = "enforce",
    workflow: str = "multi-agent",
    cost_profile: str = "cheap",
    user_tier: str = "paid_low",
    concurrency: int = 1,
    case_timeout_seconds: int = 300,
    knowledge_context: str = "auto",
    prompt_profile: str = DEFAULT_PROMPT_PROFILE,
    out: Path | None = None,
) -> dict[str, Any]:
    prompt_profile = normalize_prompt_profile(prompt_profile)
    if runs < 1:
        raise ValueError("runs must be at least 1")
    run_reports = []
    for index in range(1, runs + 1):
        run_dir = out_root / f"run-{index:02d}"
        report = run_live_eval(
            suite_path=suite,
            out_dir=run_dir,
            policy=policy,
            workflow=workflow,
            cost_profile=cost_profile,
            user_tier=user_tier,
            save_raw_provider=True,
            knowledge_context=knowledge_context,
            prompt_profile=prompt_profile,
            concurrency=concurrency,
            case_timeout_seconds=case_timeout_seconds,
        )
        run_reports.append(
            {
                "run_index": index,
                "status": report.get("status"),
                "case_count": len(report.get("cases", [])),
                "failed_case_count": sum(1 for case in report.get("cases", []) if case.get("status") == STATUS_FAIL),
                "eval_report_ref": str(run_dir / EVAL_REPORT_PATH),
            }
        )
    live_traces = [
        report
        for path, report in _collect_json_reports(out_root)
        if _looks_like_live_trace(path, report)
    ]
    latency_summary = _latency_summary(live_traces)
    payload = {
        "status": STATUS_PASS if all(run.get("status") == STATUS_PASS for run in run_reports) else STATUS_FAIL,
        "created_at": _now(),
        "suite": str(suite),
        "out_root": str(out_root),
        "runs_requested": runs,
        "runs_completed": len(run_reports),
        "policy": policy,
        "workflow": workflow,
        "cost_profile": cost_profile,
        "user_tier": user_tier,
        "prompt_profile": prompt_profile,
        "run_reports": run_reports,
        "latency_summary": latency_summary,
        "route_policy_candidates": _latency_route_policy_candidates(latency_summary),
        "anti_pollution": {"auto_applied": False, "mutates_registry": False, "analysis_only": True},
    }
    if out:
        write_json(out, payload)
    return payload


def build_prompt_matrix(
    *,
    suite: Path,
    out_root: Path,
    profiles: list[str],
    runs: int = 1,
    policy: str = "enforce",
    workflow: str = "multi-agent",
    cost_profile: str = "cheap",
    user_tier: str = "paid_low",
    concurrency: int = 1,
    case_timeout_seconds: int = 300,
    knowledge_context: str = "auto",
    out: Path | None = None,
) -> dict[str, Any]:
    normalized_profiles = normalize_prompt_profiles(profiles)
    matrices = []
    for profile in normalized_profiles:
        profile_out = out_root / profile / "latency-matrix.json"
        matrices.append(
            build_latency_matrix(
                suite=suite,
                out_root=out_root / profile,
                runs=runs,
                policy=policy,
                workflow=workflow,
                cost_profile=cost_profile,
                user_tier=user_tier,
                concurrency=concurrency,
                case_timeout_seconds=case_timeout_seconds,
                knowledge_context=knowledge_context,
                prompt_profile=profile,
                out=profile_out,
            )
        )
    payload = {
        "status": STATUS_PASS if all(matrix.get("status") == STATUS_PASS for matrix in matrices) else STATUS_FAIL,
        "created_at": _now(),
        "suite": str(suite),
        "out_root": str(out_root),
        "prompt_profiles": normalized_profiles,
        "profile_count": len(normalized_profiles),
        "runs": runs,
        "policy": policy,
        "workflow": workflow,
        "cost_profile": cost_profile,
        "user_tier": user_tier,
        "matrices": matrices,
        "comparison": _prompt_matrix_comparison(matrices),
        "anti_pollution": {"auto_applied": False, "mutates_registry": False, "analysis_only": True},
    }
    if out:
        write_json(out, payload)
    return payload


def _prompt_matrix_comparison(matrices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for matrix in matrices:
        latency = matrix.get("latency_summary", {}) if isinstance(matrix.get("latency_summary"), dict) else {}
        correlation = latency.get("context_cache_correlation", {}) if isinstance(latency.get("context_cache_correlation"), dict) else {}
        rows.append(
            {
                "prompt_profile": matrix.get("prompt_profile"),
                "status": matrix.get("status"),
                "sample_count": latency.get("sample_count", 0),
                "avg_stage_input_chars": correlation.get("avg_stage_input_chars", 0),
                "avg_system_prompt_chars": correlation.get("avg_system_prompt_chars", 0),
                "avg_user_context_chars": correlation.get("avg_user_context_chars", 0),
                "slowest_max_ms": max((_safe_int(row.get("stage_total_ms") or row.get("max_ms")) for row in latency.get("slowest", [])), default=0),
            }
        )
    return rows


def build_proxy_log_report(
    *,
    artifacts_root: Path | None = None,
    log_text: str = "",
    out: Path | None = None,
) -> dict[str, Any]:
    root = _resolve_artifacts_root(artifacts_root)
    reports = _collect_json_reports(root)
    live_traces = [report for path, report in reports if _looks_like_live_trace(path, report)]
    app_mirror_events = _collect_proxy_attribution_events(root)
    windows = _proxy_log_windows(live_traces)
    redacted_log = _redact_log_text(log_text)
    snippets = _matching_proxy_log_snippets(redacted_log, windows)
    classifications = _proxy_log_classifications(snippets)
    app_classifications = _proxy_event_classifications(app_mirror_events)
    docker_log_line_count = len([line for line in redacted_log.splitlines() if line.strip()])
    docker_log_match_status = "matched" if snippets else ("logs_present_no_matching_identifiers" if docker_log_line_count else "no_logs_provided")
    correlation_confidence = "app_plus_proxy_logs" if app_mirror_events and snippets else ("app_trace_only" if app_mirror_events else "insufficient")
    payload = {
        "status": STATUS_PASS,
        "created_at": _now(),
        "artifacts_root": str(root),
        "live_trace_count": len(live_traces),
        "app_mirror_event_count": len(app_mirror_events),
        "app_mirror_events": app_mirror_events,
        "app_mirror_attribution": _app_mirror_attribution_summary(app_mirror_events),
        "app_mirror_classifications": app_classifications,
        "app_mirror_classification_summary": dict(Counter(item["classification"] for item in app_classifications)),
        "window_count": len(windows),
        "windows": windows,
        "snippet_count": len(snippets),
        "snippets": snippets,
        "log_line_count": docker_log_line_count,
        "docker_log_line_count": docker_log_line_count,
        "log_match_status": docker_log_match_status,
        "docker_log_match_status": docker_log_match_status,
        "correlation_confidence": correlation_confidence,
        "correlation_notes": _proxy_correlation_notes(app_mirror_events, snippets, docker_log_match_status),
        "classifications": classifications,
        "classification_summary": dict(Counter(item["classification"] for item in classifications)),
        "anti_pollution": {"secrets_redacted": True, "stores_raw_prompts": False, "analysis_only": True},
    }
    if out:
        write_json(out, payload)
    return payload


def _collect_proxy_attribution_events(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    paths = [root] if root.is_file() and root.name == "proxy-attribution-events.jsonl" else list(root.rglob("proxy-attribution-events.jsonl"))
    events: list[dict[str, Any]] = []
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                events.append({"event_type": "proxy.attribution.parse_error", "path": str(path), "line": line_number})
                continue
            if isinstance(event, dict):
                events.append(_redact_proxy_attribution_event(event))
    return events


def _redact_proxy_attribution_event(event: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "event_type",
        "run_id",
        "case_id",
        "stage",
        "route_model",
        "model",
        "gateway",
        "started_at",
        "completed_at",
        "provider_call_ms",
        "stage_total_ms",
        "provider_call_ratio",
        "local_processing_ms",
        "stage_input_chars",
        "output_chars",
        "status",
        "failure_class",
        "provider_error_subclass",
        "timeout_enforced_by",
        "timeout_overrun",
        "fallback_used",
        "fallback_from",
        "resolved_provider",
        "upstream_provider_ms",
        "litellm_overhead_ms",
        "callback_duration_ms",
        "attempted_retries",
        "attempted_fallbacks",
    }
    return {key: event.get(key) for key in sorted(allowed) if key in event}


def _app_mirror_attribution_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    provider_dominant = [
        event
        for event in events
        if _safe_float(event.get("provider_call_ratio")) >= 0.95 and _safe_int(event.get("local_processing_ms")) < 50
    ]
    return {
        "event_count": len(events),
        "provider_or_proxy_dominant_count": len(provider_dominant),
        "timeout_overrun_count": sum(1 for event in events if event.get("timeout_overrun")),
        "fallback_used_count": sum(1 for event in events if event.get("fallback_used")),
        "upstream_provider_slow_count": sum(1 for event in events if _proxy_event_classification(event) == "upstream_provider_slow"),
        "proxy_overhead_slow_count": sum(1 for event in events if _proxy_event_classification(event) == "proxy_overhead_slow"),
        "timeout_incomplete_count": sum(1 for event in events if _proxy_event_classification(event) == "provider_or_proxy_timeout_incomplete"),
        "provider_connection_error_fast_count": sum(1 for event in events if _proxy_event_classification(event) == "provider_connection_error_fast"),
        "provider_timeout_enforced_count": sum(1 for event in events if _proxy_event_classification(event) == "provider_timeout_enforced"),
        "provider_timeout_late_count": sum(1 for event in events if _proxy_event_classification(event) == "provider_timeout_late"),
        "by_stage": dict(Counter(str(event.get("stage") or "unknown") for event in events)),
        "by_route_model": dict(Counter(str(event.get("route_model") or "unknown") for event in events)),
    }


def _proxy_correlation_notes(app_events: list[dict[str, Any]], snippets: list[dict[str, Any]], docker_log_match_status: str) -> list[str]:
    notes = []
    split_available = any(_proxy_event_classification(event) in {"upstream_provider_slow", "proxy_overhead_slow"} for event in app_events)
    if app_events:
        if split_available:
            notes.append("app_trace_timing_split_available")
        else:
            notes.append("app_trace_only_provider_proxy_dominant" if not snippets else "app_plus_proxy_logs_available")
    if app_events and docker_log_match_status != "matched" and not split_available:
        notes.append("cannot_split_proxy_vs_upstream_without_structured_proxy_logs")
    if not app_events and not snippets:
        notes.append("insufficient_proxy_correlation_evidence")
    return notes


def _proxy_event_classifications(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "run_id": event.get("run_id"),
            "stage": event.get("stage"),
            "route_model": event.get("route_model"),
            "status": event.get("status"),
            "classification": _proxy_event_classification(event),
        }
        for event in events
    ]


def _proxy_event_classification(event: dict[str, Any]) -> str:
    if event.get("status") == "started" and not event.get("completed_at"):
        return "provider_or_proxy_timeout_incomplete"
    if event.get("provider_error_subclass") == "provider_connection_error":
        stage_total_ms = _safe_float(event.get("stage_total_ms"))
        if stage_total_ms and stage_total_ms <= 5000:
            return "provider_connection_error_fast"
        return "provider_connection_error"
    if event.get("failure_class") == "provider_timeout" and event.get("timeout_enforced_by") in {"app_future_deadline", "signal_deadline"}:
        return "provider_timeout_enforced"
    if event.get("failure_class") == "provider_timeout" and event.get("timeout_overrun"):
        return "provider_timeout_late"
    upstream_ms = _safe_float(event.get("upstream_provider_ms"))
    overhead_ms = _safe_float(event.get("litellm_overhead_ms"))
    stage_total_ms = _safe_float(event.get("stage_total_ms"))
    if upstream_ms > 0 or overhead_ms > 0:
        if overhead_ms >= max(1000.0, upstream_ms * 0.25):
            return "proxy_overhead_slow"
        if upstream_ms >= 10000 or (stage_total_ms > 0 and upstream_ms / stage_total_ms >= 0.8):
            return "upstream_provider_slow"
        return "app_trace_split_latency_within_expected_bounds"
    if _safe_float(event.get("provider_call_ratio")) >= 0.95 and _safe_int(event.get("local_processing_ms")) < 50:
        return "app_trace_only_unknown_split"
    return "app_trace_unclassified"


def propose_intelligence_lessons(
    report_path: Path,
    *,
    out: Path | None = None,
    candidates_path: Path | None = None,
) -> dict[str, Any]:
    report = load_json(report_path)
    seeds = _proposal_seeds_from_report(report)
    evidence_confidence = (report.get("evidence_completeness") or {}).get("confidence", "unknown")
    proposals: list[dict[str, Any]] = []
    for seed in seeds:
        candidate = None
        if seed["suggested_change_type"] in {"prompt_contract", "validator_rule", "playbook_gap"}:
            candidate = propose_failure_candidate(
                {
                    "id": seed["id"],
                    "failure_class": seed["failure_class"],
                    "failure_stage": seed["stage"],
                    "failure_reason": seed["lesson"],
                },
                evidence_ref=seed["evidence_refs"][0],
                path=candidates_path,
            )
        proposals.append({**seed, "evidence_confidence": evidence_confidence, "knowledge_candidate_id": candidate.get("candidate_id") if candidate else None})
    payload = {
        "status": STATUS_PASS,
        "created_at": _now(),
        "source_report": str(report_path),
        "proposal_count": len(proposals),
        "proposals": proposals,
        "anti_pollution": {"auto_approved": False, "writes_memory": False, "mutates_registry": False},
    }
    if out:
        write_json(out, payload)
    return payload


def replay_recommendations(
    proposals_path: Path,
    *,
    suite: Path,
    out: Path | None = None,
    knowledge_context: str = "auto",
) -> dict[str, Any]:
    proposals = load_json(proposals_path)
    replay_out = out or DEFAULT_INTELLIGENCE_REPLAY_PATH
    eval_out = replay_out.parent / f"{replay_out.stem}-eval"
    eval_report = run_live_eval(suite_path=suite, out_dir=eval_out, policy="enforce", knowledge_context=knowledge_context)
    eval_report_ref = eval_out / EVAL_REPORT_PATH
    proposal_results = []
    for proposal in proposals.get("proposals", []):
        ready = eval_report.get("status") == STATUS_PASS
        proposal_results.append(
            {
                "proposal_id": proposal.get("id"),
                "suggested_change_type": proposal.get("suggested_change_type"),
                "replay_status": eval_report.get("status"),
                "ready": ready,
                "baseline": "baseline_unavailable",
                "eval_report_ref": str(eval_report_ref),
            }
        )
    payload = {
        "status": STATUS_PASS if all(item["ready"] for item in proposal_results) else STATUS_FAIL,
        "created_at": _now(),
        "proposals_ref": str(proposals_path),
        "suite": str(suite),
        "eval_report_ref": str(eval_report_ref),
        "proposal_results": proposal_results,
    }
    if out:
        write_json(out, payload)
    return payload


def propose_improvements(
    proposals_path: Path,
    *,
    replay_path: Path | None = None,
    out: Path | None = None,
) -> dict[str, Any]:
    proposals_payload, replay_payload = _load_proposals_and_replay(proposals_path, replay_path)
    replay_by_id = {
        str(result.get("proposal_id")): result
        for result in (replay_payload or {}).get("proposal_results", [])
        if result.get("proposal_id")
    }
    candidates = []
    for proposal in proposals_payload.get("proposals", []):
        candidate = _improvement_candidate(proposal, replay_by_id.get(str(proposal.get("id"))))
        if candidate:
            candidates.append(candidate)
    candidates.extend(_latency_route_policy_candidates(proposals_payload.get("latency_summary") or {}))
    payload = {
        "status": STATUS_PASS,
        "created_at": _now(),
        "source_proposals": str(proposals_payload.get("source_report") or proposals_path),
        "source_replay": str(replay_path or proposals_path) if replay_payload else None,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "anti_pollution": {"auto_applied": False, "writes_memory": False, "mutates_registry": False, "patch_artifacts_only": True},
    }
    if out:
        write_json(out, payload)
    return payload


def apply_approved_improvement(candidate_id: str, *, candidates_path: Path, out: Path | None = None) -> dict[str, Any]:
    candidates_payload = load_json(candidates_path)
    candidate = next((item for item in candidates_payload.get("candidates", []) if item.get("candidate_id") == candidate_id), None)
    if not candidate:
        payload = {"status": STATUS_FAIL, "reason": "candidate_not_found", "candidate_id": candidate_id}
    elif candidate.get("status") != "approved":
        payload = {
            "status": STATUS_FAIL,
            "reason": "candidate_not_approved",
            "candidate_id": candidate_id,
            "candidate_status": candidate.get("status"),
            "ready_for_review": bool(candidate.get("ready_for_review")),
            "anti_pollution": {"repo_files_mutated": False, "patch_artifact_written": False},
        }
    else:
        payload = {
            "status": STATUS_PASS,
            "created_at": _now(),
            "candidate_id": candidate_id,
            "candidate_type": candidate.get("candidate_type"),
            "source_candidates": str(candidates_path),
            "patch": candidate.get("suggested_patch", {}),
            "anti_pollution": {"repo_files_mutated": False, "patch_artifact_written": True},
        }
    if out:
        write_json(out, payload)
    return payload


def _resolve_artifacts_root(path: Path | None) -> Path:
    return path if path else repo_root()


def _collect_json_reports(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    reports: list[tuple[Path, dict[str, Any]]] = []
    if root.is_file() and root.suffix == ".json":
        try:
            return [(root, load_json(root))]
        except Exception:
            return []
    if not root.exists():
        return []
    for path in root.rglob("*.json"):
        if ".venv" in path.parts or "node_modules" in path.parts:
            continue
        try:
            payload = load_json(path)
        except Exception:
            continue
        if isinstance(payload, dict):
            reports.append((path, payload))
    return reports


def _looks_like_case_report(path: Path, report: dict[str, Any]) -> bool:
    return path.name == "case-eval.json" and "case_completed_at" in report


def _looks_like_eval_report(path: Path, report: dict[str, Any]) -> bool:
    return path.name == EVAL_REPORT_PATH and isinstance(report.get("cases"), list)


def _looks_like_live_trace(path: Path, report: dict[str, Any]) -> bool:
    return (
        path.name == "live-workflow-trace.json"
        and report.get("workflow") in {"compact-free", "multi-agent", "single"}
        and isinstance(report.get("attempts"), list)
    )


def _looks_like_quality_report(path: Path, report: dict[str, Any]) -> bool:
    return path.name == "quality-report.json" and (
        "strategy_sophistication" in report or "sophistication_grade" in report or "quality_score" in report or "score" in report
    )


def _route_rows(case_reports: list[dict[str, Any]], live_traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    traced_case_ids = {str(trace.get("run_id")) for trace in live_traces if trace.get("run_id")}
    for report in case_reports:
        if str(report.get("id")) in traced_case_ids:
            continue
        for route in report.get("route_health_snapshot") or []:
            rows.append({**route, "case_id": report.get("id"), "workflow": report.get("workflow"), "user_tier": report.get("user_tier")})
    for trace in live_traces:
        for attempt in trace.get("attempts", []):
            if attempt.get("model"):
                rows.append(
                    {
                        "stage": attempt.get("stage"),
                        "model": attempt.get("model"),
                        "provider": attempt.get("provider"),
                        "gateway": attempt.get("gateway"),
                        "status": attempt.get("status"),
                        "failure_class": attempt.get("failure_class"),
                        "skip_reason": attempt.get("skip_reason"),
                        "workflow": trace.get("workflow"),
                        "user_tier": trace.get("user_tier"),
                    }
                )
    return rows


def _failure_rows(case_reports: list[dict[str, Any]], live_traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in case_reports:
        for failure in report.get("failure_attribution") or []:
            rows.append(
                {
                    "case_id": report.get("id"),
                    "stage": failure.get("stage") or report.get("failure_stage"),
                    "failure_class": failure.get("failure_class") or report.get("failure_class"),
                    "details": failure.get("details") or report.get("failure_reason"),
                    "evidence_ref": report.get("run_dir") or report.get("id"),
                    "workflow": report.get("workflow"),
                    "user_tier": report.get("user_tier"),
                }
            )
    for trace in live_traces:
        for attempt in trace.get("attempts", []):
            if attempt.get("status") in {STATUS_FAIL, STATUS_SKIPPED}:
                rows.append(
                    {
                        "case_id": trace.get("run_id"),
                        "stage": attempt.get("stage"),
                        "model": attempt.get("model"),
                        "failure_class": attempt.get("failure_class"),
                        "details": attempt.get("error") or attempt.get("error_code") or attempt.get("skip_reason"),
                        "evidence_ref": trace.get("run_id") or "live-workflow-trace",
                        "workflow": trace.get("workflow"),
                        "user_tier": trace.get("user_tier"),
                    }
                )
    return rows


def _sophistication_rows(quality_reports: list[dict[str, Any]], live_traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, report in enumerate(quality_reports):
        ref = str(report.get("run_id") or f"quality-report-{index}")
        rows.extend(_sophistication_rows_from_quality(report, evidence_ref=ref))
    if quality_reports:
        return rows
    for trace in live_traces:
        quality = trace.get("quality_report")
        if not isinstance(quality, dict):
            continue
        ref = str(trace.get("run_id") or "live-workflow-trace")
        rows.extend(_sophistication_rows_from_quality(quality, evidence_ref=ref))
    return rows


def _sophistication_rows_from_quality(report: dict[str, Any], *, evidence_ref: str) -> list[dict[str, Any]]:
    sophistication = report.get("strategy_sophistication") if isinstance(report.get("strategy_sophistication"), dict) else {}
    grade = str(report.get("sophistication_grade") or sophistication.get("grade") or "unknown")
    score = _safe_int(report.get("sophistication_score") or sophistication.get("score"))
    missing = report.get("missing_trader_assumptions") or sophistication.get("missing_trader_assumptions") or []
    hints = report.get("improvement_hints") or sophistication.get("improvement_hints") or []
    rows = []
    for index, assumption in enumerate(missing if isinstance(missing, list) else []):
        rows.append(
            {
                "weakness": str(assumption),
                "grade": grade,
                "score": score,
                "hint": str(hints[index]) if isinstance(hints, list) and index < len(hints) else "",
                "evidence_ref": evidence_ref,
                "warn_only": True,
            }
        )
    return rows


def _evidence_completeness(
    eval_reports: list[dict[str, Any]],
    case_reports: list[dict[str, Any]],
    live_traces: list[dict[str, Any]],
) -> dict[str, Any]:
    eval_report = eval_reports[0] if eval_reports else None
    eval_report_present = eval_report is not None
    eval_report_complete = bool(eval_report and eval_report.get("is_complete") is True)
    missing_case_ids = list(eval_report.get("missing_case_ids") or []) if eval_report else []
    if eval_report_complete and not missing_case_ids:
        confidence = "full"
    elif case_reports or live_traces or eval_report_present:
        confidence = "partial"
    else:
        confidence = "weak"
    return {
        "eval_report_present": eval_report_present,
        "eval_report_complete": eval_report_complete,
        "case_eval_count": len(case_reports),
        "live_trace_count": len(live_traces),
        "missing_case_ids": missing_case_ids,
        "confidence": confidence,
    }


def _scorecard(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("stage") or "unknown"), str(row.get("model") or "unknown"), str(row.get("user_tier") or "unknown"))
        bucket = buckets.setdefault(
            key,
            {
                "stage": key[0],
                "model": key[1],
                "user_tier": key[2],
                "attempt_count": 0,
                "pass_count": 0,
                "fail_count": 0,
                "skip_count": 0,
                "failure_classes": Counter(),
            },
        )
        row_counts = _route_row_counts(row)
        bucket["attempt_count"] += row_counts["attempt"]
        status = row.get("status")
        bucket["pass_count"] += row_counts["pass"]
        bucket["fail_count"] += row_counts["fail"]
        bucket["skip_count"] += row_counts["skip"]
        if row.get("failure_class") or row.get("last_failure_class"):
            bucket["failure_classes"][str(row.get("failure_class") or row.get("last_failure_class"))] += max(1, row_counts["fail"] or row_counts["skip"])
    scorecard = []
    for bucket in buckets.values():
        attempts = max(1, int(bucket["attempt_count"]))
        failure_classes = dict(bucket.pop("failure_classes"))
        scorecard.append(
            {
                **bucket,
                "pass_rate": round(bucket["pass_count"] / attempts, 4),
                "fail_rate": round(bucket["fail_count"] / attempts, 4),
                "failure_classes": failure_classes,
            }
        )
    return sorted(scorecard, key=lambda item: (item["stage"], item["model"], item["user_tier"]))


def _latency_summary(live_traces: list[dict[str, Any]]) -> dict[str, Any]:
    rows = _latency_rows(live_traces)
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["stage"], row["model"], row["user_tier"], row.get("prompt_profile") or "current")].append(row)
    by_route = []
    for (stage, model, user_tier, prompt_profile), group_rows in groups.items():
        durations = sorted(row["stage_total_ms"] for row in group_rows)
        timeout_overrun_count = sum(1 for row in group_rows if row.get("timeout_overrun"))
        slow_count = sum(1 for row in group_rows if _is_slow_latency_row(row))
        provider_ratios = [float(row.get("provider_call_ratio") or 0) for row in group_rows]
        by_route.append(
            {
                "stage": stage,
                "model": model,
                "user_tier": user_tier,
                "prompt_profile": prompt_profile,
                "sample_count": len(group_rows),
                "p50_ms": _percentile(durations, 50),
                "p95_ms": _percentile(durations, 95),
                "max_ms": max(durations) if durations else 0,
                "timeout_overrun_count": timeout_overrun_count,
                "slow_count": slow_count,
                "avg_provider_call_ratio": round(sum(provider_ratios) / len(provider_ratios), 4) if provider_ratios else 0.0,
                "avg_local_processing_ms": _avg_int(row.get("local_processing_ms") for row in group_rows),
                "proxy_or_provider_suspected": any(row.get("proxy_or_provider_suspected") for row in group_rows),
                "slow_stage_reason": _slow_stage_reason(group_rows),
                "sample_confidence": "sufficient" if len(group_rows) >= 3 else "sample_too_small",
                "diagnosis": _diagnose_latency_group(group_rows),
            }
        )
    slowest = sorted(rows, key=lambda row: row["stage_total_ms"], reverse=True)[:5]
    return {
        "sample_count": len(rows),
        "run_count": len(live_traces),
        "by_route": sorted(by_route, key=lambda item: (-item["max_ms"], item["stage"], item["model"], item["user_tier"])),
        "slowest": slowest,
        "context_cache_correlation": _context_cache_correlation(rows),
        "diagnosis": _diagnose_latency(rows, live_traces),
    }


def _context_cache_correlation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    with_context = [row for row in rows if _safe_int(row.get("stage_input_chars")) > 0]
    cache_hits = [row for row in rows if row.get("cache_hit") is True or row.get("retrieval_cache_status") == "hit"]
    cache_misses = [row for row in rows if row.get("cache_hit") is False or row.get("retrieval_cache_status") == "miss"]
    return {
        "sample_count": len(with_context),
        "avg_stage_input_chars": _avg_int(row.get("stage_input_chars") for row in with_context),
        "avg_system_prompt_chars": _avg_int(row.get("system_prompt_chars") for row in with_context),
        "avg_user_context_chars": _avg_int(row.get("user_context_chars") for row in with_context),
        "max_stage_input_chars": max((_safe_int(row.get("stage_input_chars")) for row in with_context), default=0),
        "avg_latency_ms": _avg_int(row.get("stage_total_ms") for row in with_context),
        "cache_hit_sample_count": len(cache_hits),
        "cache_miss_sample_count": len(cache_misses),
        "avg_latency_cache_hit_ms": _avg_int(row.get("stage_total_ms") for row in cache_hits),
        "avg_latency_cache_miss_ms": _avg_int(row.get("stage_total_ms") for row in cache_misses),
    }


def _avg_int(values: Any) -> int:
    normalized = [_safe_int(value) for value in values]
    normalized = [value for value in normalized if value >= 0]
    return int(sum(normalized) / len(normalized)) if normalized else 0


def _latency_rows(live_traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for trace in live_traces:
        run_id = str(trace.get("run_id") or "unknown")
        workflow = str(trace.get("workflow") or "unknown")
        user_tier = str(trace.get("user_tier") or "unknown")
        for attempt in trace.get("attempts", []):
            model = str(attempt.get("model") or "unknown")
            stage = str(attempt.get("stage") or "single")
            stage_total_ms = _safe_int(attempt.get("stage_total_ms") or attempt.get("duration_ms") or attempt.get("latency_ms"))
            request_timeout_seconds = _safe_float(attempt.get("request_timeout_seconds"))
            timeout_overrun = bool(attempt.get("timeout_overrun")) or (
                request_timeout_seconds > 0 and stage_total_ms > int(request_timeout_seconds * 1000)
            )
            row = {
                "run_id": run_id,
                "workflow": workflow,
                "user_tier": user_tier,
                "prompt_profile": str(attempt.get("prompt_profile") or trace.get("prompt_profile") or "current"),
                "stage": stage,
                "model": model,
                "gateway": attempt.get("gateway"),
                "provider": attempt.get("provider"),
                "route_model": attempt.get("route_model"),
                "status": attempt.get("status"),
                "failure_class": attempt.get("failure_class"),
                "provider_error_subclass": attempt.get("provider_error_subclass"),
                "started_at": attempt.get("started_at"),
                "completed_at": attempt.get("completed_at"),
                "stage_total_ms": stage_total_ms,
                "latency_ms": _safe_int(attempt.get("latency_ms") or stage_total_ms),
                "provider_call_ms": _safe_int(attempt.get("provider_call_ms")),
                "provider_call_ratio": float(attempt.get("provider_call_ratio") or 0),
                "local_processing_ms": _safe_int(attempt.get("local_processing_ms")),
                "response_parse_ms": _safe_int(attempt.get("response_parse_ms")),
                "payload_validation_ms": _safe_int(attempt.get("payload_validation_ms")),
                "policy_scan_ms": _safe_int(attempt.get("policy_scan_ms")),
                "response_chars": _safe_int(attempt.get("response_chars")),
                "output_chars": _safe_int(attempt.get("output_chars")),
                "prompt_to_output_ratio": float(attempt.get("prompt_to_output_ratio") or 0),
                "prompt_chars": _safe_int(attempt.get("prompt_chars")),
                "system_prompt_chars": _safe_int(attempt.get("system_prompt_chars")),
                "user_context_chars": _safe_int(attempt.get("user_context_chars")),
                "knowledge_context_chars": _safe_int(attempt.get("knowledge_context_chars")),
                "stage_input_chars": _safe_int(attempt.get("stage_input_chars")),
                "cache_hit": attempt.get("cache_hit"),
                "retrieval_cache_status": attempt.get("retrieval_cache_status"),
                "cache_layer": attempt.get("cache_layer"),
                "request_timeout_seconds": request_timeout_seconds,
                "timeout_overrun": timeout_overrun,
                "timeout_enforced_by": attempt.get("timeout_enforced_by"),
            }
            if not row["provider_call_ratio"] and stage_total_ms > 0:
                row["provider_call_ratio"] = round(row["provider_call_ms"] / stage_total_ms, 4)
            if not row["local_processing_ms"]:
                row["local_processing_ms"] = row["response_parse_ms"] + row["payload_validation_ms"] + row["policy_scan_ms"]
            row.update(_latency_row_attribution(row))
            rows.append(row)
    return rows


def _context_stage_reports(trace: dict[str, Any]) -> list[dict[str, Any]]:
    stages = trace.get("stages", []) if isinstance(trace.get("stages"), list) else []
    final_decision = trace.get("final_decision", {}) if isinstance(trace.get("final_decision"), dict) else {}
    reports = []
    completed = {str(stage.get("stage")) for stage in stages if stage.get("stage")}
    has_strategy_spec = any(isinstance((stage.get("output") or {}).get("strategy_spec"), dict) for stage in stages)
    has_pine_code = any(isinstance((stage.get("output") or {}).get("pine_code"), str) for stage in stages)
    has_validation = bool(final_decision.get("validation") or final_decision.get("validation_status"))
    for stage_record in stages:
        stage = str(stage_record.get("stage") or "unknown")
        context_refs = [str(ref) for ref in stage_record.get("context_refs", [])]
        required = _context_required_presence(
            stage,
            context_refs=context_refs,
            completed=completed,
            has_strategy_spec=has_strategy_spec,
            has_pine_code=has_pine_code,
            has_validation=has_validation,
        )
        missing = [name for name, present in required.items() if not present]
        budget = STAGE_CONTEXT_BUDGETS.get(stage)
        stage_input_chars = _safe_int(stage_record.get("stage_input_chars"))
        unexpected_large_fields = []
        if budget and stage_input_chars > budget:
            unexpected_large_fields.append({"field": "stage_input_chars", "value": stage_input_chars, "budget": budget})
        reports.append(
            {
                "run_id": trace.get("run_id"),
                "stage": stage,
                "model": stage_record.get("model"),
                "prompt_profile": stage_record.get("prompt_profile") or trace.get("prompt_profile") or "current",
                "required_present": required,
                "missing_context": missing,
                "unexpected_large_fields": unexpected_large_fields,
                "budget_status": "warn" if unexpected_large_fields else "pass",
                "stage_input_chars": stage_input_chars,
                "knowledge_context_chars": _safe_int(stage_record.get("knowledge_context_chars")),
                "prompt_chars": _safe_int(stage_record.get("prompt_chars")),
                "system_prompt_chars": _safe_int(stage_record.get("system_prompt_chars")),
                "user_context_chars": _safe_int(stage_record.get("user_context_chars")),
                "context_refs": context_refs,
            }
        )
    return reports


def _context_required_presence(
    stage: str,
    *,
    context_refs: list[str],
    completed: set[str],
    has_strategy_spec: bool,
    has_pine_code: bool,
    has_validation: bool,
) -> dict[str, bool]:
    refs = set(context_refs)
    if stage == "strategy_reasoning":
        return {
            "prompt": "prompt" in refs,
            "policy_boundaries": "policy_boundaries" in refs,
        }
    if stage == "strategy_coding":
        return {
            "strategy_reasoning": "strategy_reasoning" in completed or "strategy_reasoning" in refs,
            "schema_summary": "schemas/strategy-spec.schema.json" in refs,
            "policy_boundaries": "policy_boundaries" in refs,
        }
    if stage == "pine_code_generation":
        return {
            "strategy_spec": has_strategy_spec,
            "policy_boundaries": "policy_boundaries" in refs,
            "pine_constraints": "schemas/strategy-spec.schema.json" in refs or any("pine" in ref for ref in refs),
        }
    if stage == "balanced_review":
        return {
            "strategy_spec": has_strategy_spec,
            "pine_code": has_pine_code,
            "validation": has_validation,
            "policy_boundaries": "policy_boundaries" in refs,
        }
    if stage == "repair":
        return {
            "strategy_spec": has_strategy_spec,
            "pine_code": has_pine_code,
            "validation": has_validation,
            "policy_boundaries": "policy_boundaries" in refs,
        }
    return {}


def _latency_route_policy_candidates(latency_summary: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    for route in latency_summary.get("by_route", []):
        sample_count = _safe_int(route.get("sample_count"))
        timeout_count = _safe_int(route.get("timeout_overrun_count"))
        slow_count = _safe_int(route.get("slow_count"))
        if sample_count < 2 or (timeout_count < 2 and slow_count < 2):
            continue
        repeated_timeout = timeout_count >= 2
        action = "demote_or_add_fallback" if repeated_timeout else "slow_route_add_fallback"
        stage = route.get("stage")
        model = str(route.get("model") or "")
        user_tier = route.get("user_tier")
        recommended_fallback = _recommended_paid_low_fallback(stage, model)
        candidate_type = "route_policy_patch" if repeated_timeout else "slow_route_fallback_candidate"
        candidates.append(
            {
                "candidate_id": f"latency-route-{_slug(str(route.get('stage')))}-{_slug(str(route.get('model')))}",
                "candidate_type": candidate_type,
                "status": "needs_review",
                "ready_for_review": True,
                "stage": stage,
                "model": route.get("model"),
                "user_tier": user_tier,
                "sample_count": sample_count,
                "timeout_overrun_count": timeout_count,
                "slow_count": slow_count,
                "suggested_patch": {
                    "patch_type": "model_route_policy",
                    "action": action,
                    "stage": stage,
                    "model": route.get("model"),
                    "user_tier": user_tier,
                    "recommended_fallback": recommended_fallback,
                    "cooldown_seconds": 600,
                    "quarantine_on": {"timeout_overrun_count": 2, "window": "latency_matrix"} if repeated_timeout else {"slow_count": 2, "window": "latency_matrix"},
                    "slow_stage_reason": route.get("slow_stage_reason"),
                    "proxy_or_provider_suspected": route.get("proxy_or_provider_suspected"),
                    "reason": f"repeated latency evidence: timeout_overrun_count={timeout_count} slow_count={slow_count} sample_count={sample_count}",
                },
            }
        )
    return candidates


def _recommended_paid_low_fallback(stage: Any, model: str) -> str | None:
    if stage == "strategy_reasoning" and "paid_low.strategy_reasoning" in model:
        return "litellm_proxy/paid_medium.strategy_reasoning"
    if stage == "strategy_coding" and "paid_low.strategy_coding" in model:
        return "litellm_proxy/paid_medium.strategy_coding"
    if stage == "pine_code_generation" and "paid_low.pine_code_generation" in model:
        return "litellm_proxy/paid_medium.pine_code_generation"
    if stage == "balanced_review" and "paid_low.balanced_review" in model:
        return "litellm_proxy/paid_medium.balanced_review"
    if stage == "repair" and "paid_low.repair" in model:
        return "litellm_proxy/paid_medium.repair"
    return None


def _proxy_log_windows(live_traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    windows = []
    for row in _latency_rows(live_traces):
        windows.append(
            {
                "run_id": row.get("run_id"),
                "stage": row.get("stage"),
                "model": row.get("model"),
                "route_model": row.get("route_model"),
                "started_at": row.get("started_at"),
                "completed_at": row.get("completed_at"),
                "stage_total_ms": row.get("stage_total_ms"),
                "timeout_overrun": row.get("timeout_overrun"),
            }
        )
    return windows


def _matching_proxy_log_snippets(log_text: str, windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not log_text:
        return []
    lines = [line for line in log_text.splitlines() if line.strip()]
    snippets = []
    for window in windows:
        terms = [str(window.get("run_id") or ""), str(window.get("route_model") or ""), str(window.get("model") or "")]
        matched = [line for line in lines if any(term and term in line for term in terms)]
        if matched:
            snippets.append(
                {
                    "run_id": window.get("run_id"),
                    "stage": window.get("stage"),
                    "route_model": window.get("route_model"),
                    "line_count": min(10, len(matched)),
                    "lines": matched[:10],
                }
            )
    return snippets


def _proxy_log_classifications(snippets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    classifications = []
    for snippet in snippets:
        text = "\n".join(str(line).lower() for line in snippet.get("lines", []))
        if any(term in text for term in ("queue", "queued", "waiting", "rate limit", "ratelimit")):
            classification = "proxy_queue_or_rate_limit"
        elif any(term in text for term in ("upstream", "provider", "completion", "llm api")) and any(term in text for term in ("slow", "timeout", "latency", "duration")):
            classification = "upstream_provider_slow"
        elif any(term in text for term in ("retry", "malformed", "json", "schema")):
            classification = "retry_or_malformed_hidden"
        elif any(term in text for term in ("timeout", "overrun")):
            classification = "timeout_related"
        else:
            classification = "matched_unclassified"
        classifications.append(
            {
                "run_id": snippet.get("run_id"),
                "stage": snippet.get("stage"),
                "route_model": snippet.get("route_model"),
                "classification": classification,
            }
        )
    return classifications


def _redact_log_text(text: str) -> str:
    redacted = text
    patterns = [
        r"sk-[A-Za-z0-9_\-]{8,}",
        r"Bearer\s+[A-Za-z0-9_\-\.]{8,}",
        r"(?i)(api[_-]?key|authorization|x-api-key)([=:]\s*)[^\s,;]+",
    ]
    for pattern in patterns:
        redacted = re.sub(pattern, r"\1\2[REDACTED]" if "(" in pattern else "[REDACTED]", redacted)
    return redacted


def _diagnose_latency(rows: list[dict[str, Any]], live_traces: list[dict[str, Any]]) -> list[str]:
    diagnoses: list[str] = []
    if len(live_traces) < 3:
        diagnoses.append("sample_too_small")
    if any(row.get("timeout_overrun") and row.get("status") == STATUS_PASS for row in rows):
        diagnoses.append("timeout_policy_not_enforced")
    elif any(row.get("timeout_overrun") for row in rows):
        diagnoses.append("timeout_enforced_provider_timeout")
    if any(_provider_dominates(row) and _is_slow_latency_row(row) for row in rows):
        diagnoses.append("provider_or_proxy_slow")
    if any(_local_processing_slow(row) for row in rows):
        diagnoses.append("local_parse_or_validation_slow")
    return diagnoses or ["latency_within_expected_bounds"]


def _diagnose_latency_group(rows: list[dict[str, Any]]) -> list[str]:
    diagnoses: list[str] = []
    if len(rows) < 3:
        diagnoses.append("sample_too_small")
    if any(row.get("timeout_overrun") and row.get("status") == STATUS_PASS for row in rows):
        diagnoses.append("timeout_policy_not_enforced")
    elif any(row.get("timeout_overrun") for row in rows):
        diagnoses.append("timeout_enforced_provider_timeout")
    if any(_provider_dominates(row) and _is_slow_latency_row(row) for row in rows):
        diagnoses.append("provider_or_proxy_slow")
    if any(_local_processing_slow(row) for row in rows):
        diagnoses.append("local_parse_or_validation_slow")
    return diagnoses or ["latency_within_expected_bounds"]


def _latency_row_attribution(row: dict[str, Any]) -> dict[str, Any]:
    provider_dominates = _provider_dominates(row)
    local_slow = _local_processing_slow(row)
    payload_small = _safe_int(row.get("stage_input_chars")) <= 18_000
    slow = _is_slow_latency_row(row)
    if slow and provider_dominates and payload_small:
        reason = "provider_or_proxy_dominates_payload_unlikely"
    elif slow and provider_dominates:
        reason = "provider_or_proxy_dominates"
    elif slow and local_slow:
        reason = "local_processing_slow"
    elif slow:
        reason = "mixed_or_unknown_slow"
    else:
        reason = "latency_within_expected_bounds"
    return {
        "proxy_or_provider_suspected": bool(slow and provider_dominates),
        "payload_unlikely_primary_cause": bool(slow and provider_dominates and payload_small),
        "slow_stage_reason": reason,
        "sample_confidence": "single_sample",
    }


def _slow_stage_reason(rows: list[dict[str, Any]]) -> str:
    reasons = Counter(str(row.get("slow_stage_reason") or "unknown") for row in rows if _is_slow_latency_row(row))
    if not reasons:
        return "latency_within_expected_bounds"
    return reasons.most_common(1)[0][0]


def _is_slow_latency_row(row: dict[str, Any]) -> bool:
    stage_total_ms = _safe_int(row.get("stage_total_ms"))
    request_timeout_seconds = _safe_float(row.get("request_timeout_seconds"))
    threshold_ms = 60000
    if request_timeout_seconds > 0:
        threshold_ms = min(threshold_ms, int(request_timeout_seconds * 1000 * 0.8))
    return stage_total_ms >= threshold_ms


def _provider_dominates(row: dict[str, Any]) -> bool:
    stage_total_ms = max(1, _safe_int(row.get("stage_total_ms")))
    provider_call_ms = _safe_int(row.get("provider_call_ms"))
    return provider_call_ms / stage_total_ms >= 0.8


def _local_processing_slow(row: dict[str, Any]) -> bool:
    stage_total_ms = max(1, _safe_int(row.get("stage_total_ms")))
    local_ms = _safe_int(row.get("response_parse_ms")) + _safe_int(row.get("payload_validation_ms")) + _safe_int(row.get("policy_scan_ms"))
    return local_ms >= 1000 and local_ms / stage_total_ms >= 0.2


def _percentile(values: list[int], percentile: int) -> int:
    if not values:
        return 0
    if len(values) == 1:
        return values[0]
    rank = round((percentile / 100) * (len(values) - 1))
    return values[min(len(values) - 1, max(0, rank))]


def _route_row_counts(row: dict[str, Any]) -> dict[str, int]:
    success_count = _safe_int(row.get("success_count"))
    failure_count = _safe_int(row.get("failure_count"))
    skip_count = _safe_int(row.get("skip_count"))
    has_aggregate_counts = any(key in row for key in ("success_count", "failure_count", "skip_count"))
    if has_aggregate_counts:
        total = success_count + failure_count + skip_count
        if total > 0:
            return {"attempt": total, "pass": success_count, "fail": failure_count, "skip": skip_count}
    status = row.get("status")
    return {
        "attempt": 1,
        "pass": 1 if status == STATUS_PASS else 0,
        "fail": 1 if status == STATUS_FAIL else 0,
        "skip": 1 if status == STATUS_SKIPPED else 0,
    }


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def _failure_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_class = Counter(str(row.get("failure_class") or "unknown") for row in rows)
    by_stage = Counter(str(row.get("stage") or "unknown") for row in rows)
    return {"total": len(rows), "by_failure_class": dict(by_class), "by_stage": dict(by_stage)}


def _failure_signatures(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    signatures: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        failure_class = str(row.get("failure_class") or "unknown")
        stage = str(row.get("stage") or "unknown")
        if failure_class == "unknown":
            continue
        key = (failure_class, stage)
        signature = signatures.setdefault(
            key,
            {
                "failure_class": failure_class,
                "stage": stage,
                "occurrence_count": 0,
                "case_ids": set(),
                "evidence_refs": set(),
                "sample_details": [],
            },
        )
        signature["occurrence_count"] += 1
        if row.get("case_id"):
            signature["case_ids"].add(str(row["case_id"]))
        if row.get("evidence_ref") or row.get("case_id"):
            signature["evidence_refs"].add(str(row.get("evidence_ref") or row.get("case_id")))
        if row.get("details") and len(signature["sample_details"]) < 3:
            signature["sample_details"].append(str(row["details"]))
    normalized = []
    for signature in signatures.values():
        evidence_refs = sorted(signature["evidence_refs"])
        case_ids = sorted(signature.pop("case_ids"))
        normalized.append({**signature, "case_ids": case_ids, "evidence_refs": evidence_refs, "occurrence_count": len(case_ids) or len(evidence_refs) or signature["occurrence_count"]})
    return sorted(normalized, key=lambda item: (-item["occurrence_count"], item["stage"], item["failure_class"]))


def _sophistication_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_weakness = Counter(str(row.get("weakness") or "unknown") for row in rows)
    by_grade = Counter(str(row.get("grade") or "unknown") for row in rows)
    return {
        "total_weakness_count": len(rows),
        "by_weakness": dict(by_weakness),
        "by_grade": dict(by_grade),
        "warn_only": True,
    }


def _sophistication_signatures(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    signatures: dict[str, dict[str, Any]] = {}
    for row in rows:
        weakness = str(row.get("weakness") or "unknown")
        if weakness == "unknown":
            continue
        signature = signatures.setdefault(
            weakness,
            {
                "weakness": weakness,
                "occurrence_count": 0,
                "grades": Counter(),
                "scores": [],
                "evidence_refs": set(),
                "sample_hints": [],
                "warn_only": True,
            },
        )
        signature["occurrence_count"] += 1
        signature["grades"][str(row.get("grade") or "unknown")] += 1
        signature["scores"].append(_safe_int(row.get("score")))
        if row.get("evidence_ref"):
            signature["evidence_refs"].add(str(row["evidence_ref"]))
        if row.get("hint") and len(signature["sample_hints"]) < 3:
            signature["sample_hints"].append(str(row["hint"]))
    normalized = []
    for signature in signatures.values():
        scores = [score for score in signature.pop("scores") if score > 0]
        normalized.append(
            {
                **signature,
                "grades": dict(signature["grades"]),
                "avg_score": _avg_int(scores),
                "evidence_refs": sorted(signature["evidence_refs"]),
            }
        )
    return sorted(normalized, key=lambda item: (-item["occurrence_count"], item["weakness"]))


def _route_recommendations(scorecard: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recommendations = []
    for item in scorecard:
        if item["fail_count"] or item["skip_count"]:
            recommendations.append(
                {
                    "id": f"route-{_slug(item['stage'])}-{_slug(item['model'])}",
                    "suggested_change_type": "route_policy",
                    "stage": item["stage"],
                    "model": item["model"],
                    "reason": f"fail_rate={item['fail_rate']} skip_count={item['skip_count']}",
                    "action": "demote_or_cooldown" if item["fail_rate"] >= 0.5 else "monitor",
                }
            )
    return recommendations


def _load_proposals_and_replay(proposals_path: Path, replay_path: Path | None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    payload = load_json(proposals_path)
    if "proposal_results" in payload and payload.get("proposals_ref"):
        return load_json(Path(payload["proposals_ref"])), payload
    if replay_path:
        return payload, load_json(replay_path)
    return payload, None


def _improvement_candidate(proposal: dict[str, Any], replay_result: dict[str, Any] | None) -> dict[str, Any] | None:
    change_type = proposal.get("suggested_change_type")
    failure_class = str(proposal.get("failure_class") or "")
    if change_type == "route_policy" or failure_class in {"provider_rate_limited", "provider_timeout"}:
        candidate_type = "route_policy_patch"
        suggested_patch = _route_policy_patch(proposal)
    elif change_type in {"prompt_contract_tuning", "strategy_playbook_gap", "quality_rubric_gap", "eval_case_gap"}:
        candidate_type = change_type
        suggested_patch = {
            "patch_type": change_type,
            "action": _sophistication_candidate_action(change_type),
            "weakness": proposal.get("weakness"),
            "stage": proposal.get("stage"),
            "lesson": proposal.get("lesson"),
            "warn_only": True,
        }
    elif proposal.get("knowledge_candidate_id"):
        candidate_type = "knowledge_candidate"
        suggested_patch = {
            "patch_type": "knowledge_candidate_review",
            "knowledge_candidate_id": proposal.get("knowledge_candidate_id"),
            "action": "review_existing_candidate",
        }
    elif proposal.get("evidence_confidence") != "full":
        candidate_type = "eval_gap"
        suggested_patch = {
            "patch_type": "eval_evidence_gap",
            "action": "add_or_rerun_eval_case",
            "failure_signature": proposal.get("failure_signature", {}),
        }
    else:
        return None
    replay_status = (replay_result or {}).get("replay_status")
    replay_passed = replay_status == STATUS_PASS
    evidence_confidence = proposal.get("evidence_confidence", "unknown")
    diagnostic_route_ready = candidate_type == "route_policy_patch" and evidence_confidence == "full"
    ready_for_review = evidence_confidence == "full" and (replay_passed or diagnostic_route_ready)
    return {
        "candidate_id": f"improvement-{_slug(str(proposal.get('id') or candidate_type))}",
        "candidate_type": candidate_type,
        "status": "needs_review",
        "ready_for_review": ready_for_review,
        "evidence_confidence": evidence_confidence,
        "source_proposal_id": proposal.get("id"),
        "source_failure_signature": proposal.get("failure_signature", {}),
        "suggested_patch": suggested_patch,
        "replay_required": not ready_for_review,
        "replay_status": replay_status,
        "approval_command_hint": "Set this candidate status to approved, then run harness apply-approved-improvement.",
    }


def _route_policy_patch(proposal: dict[str, Any]) -> dict[str, Any]:
    failure_class = proposal.get("failure_class")
    stage = proposal.get("stage")
    if stage == "repair" and ("provider" in str(failure_class) or "timeout" in str(failure_class)):
        action = "repair_route_unstable"
    elif failure_class == "provider_rate_limited":
        action = "demote_or_cooldown"
    elif failure_class == "provider_timeout":
        action = "review_timeout_budget_or_provider_route"
    else:
        action = "review_route_policy"
    return {
        "patch_type": "model_route_policy",
        "action": action,
        "stage": stage,
        "model": proposal.get("model"),
        "failure_class": failure_class,
        "reason": proposal.get("lesson"),
    }


def _sophistication_candidate_action(change_type: Any) -> str:
    return {
        "prompt_contract_tuning": "tighten_stage_prompt_or_context_contract",
        "strategy_playbook_gap": "add_or_update_curated_strategy_playbook_block",
        "quality_rubric_gap": "review_quality_rubric_threshold_or_detection",
        "eval_case_gap": "add_eval_case_covering_sophistication_weakness",
    }.get(str(change_type), "review_sophistication_improvement")


def _proposal_seeds_from_report(report: dict[str, Any]) -> list[dict[str, Any]]:
    seeds = []
    sophistication = report.get("sophistication_signatures")
    if isinstance(sophistication, list) and sophistication:
        seeds.extend(
            _sophistication_proposal_seeds_from_signatures(
                sophistication,
                source_ref=str(report.get("artifacts_root") or report.get("source_report") or "intelligence-report"),
            )
        )
    signatures = report.get("failure_signatures")
    if isinstance(signatures, list) and signatures:
        rows = []
        for signature in signatures:
            if not isinstance(signature, dict):
                continue
            evidence_refs = signature.get("evidence_refs") if isinstance(signature.get("evidence_refs"), list) else []
            rows.append(
                {
                    "case_id": signature.get("failure_class"),
                    "stage": signature.get("stage") or "unknown",
                    "failure_class": signature.get("failure_class"),
                    "details": "; ".join(str(item) for item in signature.get("sample_details", [])[:3]) if isinstance(signature.get("sample_details"), list) else None,
                    "evidence_ref": evidence_refs[0] if evidence_refs else str(report.get("artifacts_root") or report.get("source_report") or "intelligence-report"),
                    "occurrence_count": signature.get("occurrence_count") or 1,
                }
            )
        seeds.extend(_proposal_seeds(rows))
        return seeds
    rows = []
    for failure_class, count in (report.get("failure_summary", {}).get("by_failure_class") or {}).items():
        if count:
            rows.append(
                {
                    "case_id": failure_class,
                    "stage": "unknown",
                    "failure_class": failure_class,
                    "details": f"{count} observed failures",
                    "evidence_ref": str(report.get("artifacts_root") or report.get("source_report") or "intelligence-report"),
                }
            )
    seeds.extend(_proposal_seeds(rows))
    return seeds


def _sophistication_proposal_seeds(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _sophistication_proposal_seeds_from_signatures(_sophistication_signatures(rows), source_ref="quality-report")


def _sophistication_proposal_seeds_from_signatures(signatures: list[dict[str, Any]], *, source_ref: str) -> list[dict[str, Any]]:
    proposals = []
    for signature in signatures:
        weakness = str(signature.get("weakness") or "unknown")
        if weakness == "unknown":
            continue
        change_type = _sophistication_change_type(weakness)
        evidence_refs = signature.get("evidence_refs") if isinstance(signature.get("evidence_refs"), list) else []
        proposals.append(
            {
                "id": f"proposal-sophistication-{_slug(weakness)}",
                "status": "needs_review",
                "failure_signature": {"failure_class": "strategy_sophistication_weakness", "stage": "quality_report", "weakness": weakness},
                "failure_class": "strategy_sophistication_weakness",
                "stage": "quality_report",
                "weakness": weakness,
                "suggested_change_type": change_type,
                "lesson": _sophistication_lesson(weakness, change_type),
                "evidence_refs": evidence_refs or [source_ref],
                "occurrence_count": int(signature.get("occurrence_count") or 1),
                "warn_only": True,
            }
        )
    return proposals


def _sophistication_change_type(weakness: str) -> str:
    if weakness in {"market_premise", "entry_trigger", "invalidation", "false_break_handling", "session_liquidity_timeframe"}:
        return "prompt_contract_tuning"
    if weakness in {"structure_target", "overfit_awareness"}:
        return "strategy_playbook_gap"
    if weakness == "price_action_purity":
        return "quality_rubric_gap"
    return "eval_case_gap"


def _sophistication_lesson(weakness: str, change_type: str) -> str:
    return f"Repeated trader-grade weakness `{weakness}` suggests a reviewed {change_type} improvement while keeping production gating warn-only."


def _proposal_seeds(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row.get("failure_class") or "unknown"), str(row.get("stage") or "unknown"))].append(row)
    proposals = []
    for (failure_class, stage), items in sorted(grouped.items()):
        if failure_class == "unknown":
            continue
        change_type = _suggested_change_type(failure_class, stage)
        proposals.append(
            {
                "id": f"proposal-{_slug(stage)}-{_slug(failure_class)}",
                "status": "needs_review",
                "failure_signature": {"failure_class": failure_class, "stage": stage},
                "failure_class": failure_class,
                "stage": stage,
                "suggested_change_type": change_type,
                "lesson": _lesson_for_failure(failure_class, stage, change_type),
                "evidence_refs": sorted({str(item.get("evidence_ref") or item.get("case_id") or "unknown") for item in items}),
                "occurrence_count": sum(int(item.get("occurrence_count") or 1) for item in items),
            }
        )
    return proposals


def _suggested_change_type(failure_class: str, stage: str) -> str:
    if "static_validation" in failure_class:
        return "validator_rule"
    if "policy" in failure_class:
        return "prompt_contract"
    if "knowledge" in stage:
        return "playbook_gap"
    if "provider" in failure_class or "rate" in failure_class or "timeout" in failure_class:
        return "route_policy"
    return "test_gap"


def _lesson_for_failure(failure_class: str, stage: str, change_type: str) -> str:
    if stage == "repair" and ("provider" in failure_class or "timeout" in failure_class):
        return "Observed repair-stage provider instability suggests route diagnostics before promoting repair aliases."
    return f"Observed {failure_class} at {stage} suggests a reviewed {change_type} improvement before future runs."


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-") or "unknown"


def _now() -> str:
    return datetime.now(UTC).isoformat()
