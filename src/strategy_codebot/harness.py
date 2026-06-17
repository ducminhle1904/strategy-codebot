from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from strategy_codebot.paths import repo_root

NO_ERROR_TRACE_ARG = "[]"

_INTAKE_RE = re.compile(r"Intake #(?P<id>\d+) recorded")
_NO_ERROR_CSV_JSON = '["[]"]'
_TRACE_ARRAY_FIELDS = ("actions_taken", "files_read", "files_changed", "decisions_made", "errors")
_REQUIRED_NON_EMPTY_TRACE_ARRAY_FIELDS = ("actions_taken", "files_read", "files_changed", "decisions_made")
_HIGH_RISK_TERMS = (
    "harness",
    "trace",
    "audit",
    "schema",
    "runtime",
    "policy",
    "live",
    "model",
    "workflow",
    "generated",
)
_HARNESS_IMPROVEMENT_TERMS = ("harness", "trace", "audit", "observability")
_DOC_SUFFIXES = (".md", ".mdx", ".txt", ".rst")
_ARTIFACT_NAMES = (
    "validation-report.json",
    "review-report.json",
    "runtime-trace.jsonl",
    "review-runtime-trace.jsonl",
    "live-workflow-trace.json",
    "eval-report.json",
)
_VERIFICATION_DECISION_PREFIXES = (
    "test_outcome=",
    "review_outcome=",
    "validation_outcome=",
    "production_impact=",
    "validation_status=",
    "review_decision=",
    "production_gate=",
)
_CONTEXT_BRIEF_MAX_BULLETS = 5
_CONTEXT_BRIEF_MAX_CHARS = 1500


def harness_cli_path() -> Path:
    return repo_root() / "scripts" / "bin" / "harness-cli"


def should_record_harness(requested: bool | None) -> bool:
    if requested is not None:
        return requested
    return harness_cli_path().exists()


def classify_trace_intake(
    *,
    summary: str,
    read: Sequence[str] | None = None,
    changed: Sequence[str] | None = None,
    input_type: str | None = None,
    lane: str | None = None,
) -> dict[str, Any]:
    read_values = _unique_values(read or [])
    changed_values = _unique_values(changed or [])
    text = " ".join([summary, *read_values, *changed_values]).lower()
    effective_input_type = input_type or (
        "harness improvement" if any(term in text for term in _HARNESS_IMPROVEMENT_TERMS) else "maintenance request"
    )
    effective_lane = lane or _classify_trace_lane(summary=summary, read=read_values, changed=changed_values)
    return {
        "input_type": effective_input_type,
        "lane": effective_lane,
        "read": read_values,
        "changed": changed_values,
    }


def audit_traces(
    *,
    latest: int = 1,
    since_id: int | None = None,
    include_all: bool = False,
    db_path: Path | None = None,
) -> dict[str, Any]:
    rows = _select_trace_rows(db_path or _harness_db_path(), latest=latest, since_id=since_id, include_all=include_all)
    failures = []
    warnings = []
    for row in rows:
        issues = _trace_quality_issues(row)
        if issues:
            failures.append({"id": row["id"], "task_summary": row["task_summary"], "issues": issues})
        warning_issues = _trace_quality_warnings(row)
        if warning_issues:
            warnings.append({"id": row["id"], "task_summary": row["task_summary"], "warnings": warning_issues})
    return {
        "status": "fail" if failures else "pass",
        "checked": len(rows),
        "failed": len(failures),
        "warned": len(warnings),
        "scope": _trace_scope(latest=latest, since_id=since_id, include_all=include_all),
        "failures": failures,
        "warnings": warnings,
    }


def summarize_traces(
    *, latest: int = 20, db_path: Path | None = None, artifacts_root: Path | None = None, include_development_evidence: bool = False
) -> dict[str, Any]:
    rows = _select_trace_rows(db_path or _harness_db_path(), latest=latest, since_id=None, include_all=False)
    read_counter: Counter[str] = Counter()
    changed_counter: Counter[str] = Counter()
    lane_counter: Counter[str] = Counter()
    type_counter: Counter[str] = Counter()
    decisions_timeline = []
    failed_or_null_quality_rows = []
    clean_errors = 0
    error_traces = 0
    no_friction = 0
    friction_traces = 0
    durations: list[tuple[int, str, str, int]] = []
    durations_by_lane: dict[str, list[int]] = {}

    for row in rows:
        lane = row["risk_lane"] or "unlinked"
        lane_counter[lane] += 1
        type_counter[row["input_type"] or "unlinked"] += 1
        issues = _trace_quality_issues(row)
        if issues or row["outcome"] in {"blocked", "failed"}:
            failed_or_null_quality_rows.append(
                {"id": row["id"], "task_summary": row["task_summary"], "outcome": row["outcome"], "issues": issues}
            )

        errors = _parse_json_array(row["errors"])
        if errors == []:
            clean_errors += 1
        elif errors is not None:
            error_traces += 1

        friction = row["harness_friction"]
        if friction == "none":
            no_friction += 1
        elif friction:
            friction_traces += 1

        duration = row["duration_seconds"]
        if duration is not None:
            duration_int = int(duration)
            durations.append((row["id"], row["created_at"], row["task_summary"], duration_int))
            durations_by_lane.setdefault(lane, []).append(duration_int)

        for ref in _parse_json_array(row["files_read"]) or []:
            read_counter[str(ref)] += 1
        for ref in _parse_json_array(row["files_changed"]) or []:
            changed_counter[str(ref)] += 1
        decisions = _parse_json_array(row["decisions_made"]) or []
        if decisions:
            decisions_timeline.append(
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "task_summary": row["task_summary"],
                    "decisions": decisions,
                }
            )

    report = {
        "status": "pass",
        "latest": latest,
        "trace_count": len(rows),
        "linked_count": sum(1 for row in rows if row["intake_id"] is not None),
        "unlinked_count": sum(1 for row in rows if row["intake_id"] is None),
        "clean_error_count": clean_errors,
        "error_trace_count": error_traces,
        "no_friction_count": no_friction,
        "friction_trace_count": friction_traces,
        "high_risk_count": lane_counter.get("high-risk", 0) + lane_counter.get("high_risk", 0),
        "duration_seconds": _duration_stats([item[3] for item in durations]),
        "duration_by_lane": {lane: _duration_stats(values) for lane, values in sorted(durations_by_lane.items())},
        "top_slow_traces": [
            {"id": trace_id, "created_at": created_at, "task_summary": summary, "duration_seconds": duration}
            for trace_id, created_at, summary, duration in sorted(durations, key=lambda item: item[3], reverse=True)[:10]
        ],
        "by_lane": dict(sorted(lane_counter.items())),
        "by_input_type": dict(sorted(type_counter.items())),
        "top_changed_files": _counter_top(changed_counter),
        "top_read_files": _counter_top(read_counter),
        "decisions_timeline": decisions_timeline,
        "failed_or_null_quality_rows": failed_or_null_quality_rows,
    }
    if include_development_evidence:
        evidence_report = _development_evidence_report(rows, artifacts_root=artifacts_root)
        report["development_evidence"] = evidence_report["engineering_quality"]
        report["business_correctness"] = evidence_report["business_correctness"]
        report["production_impact"] = evidence_report["production_impact"]
        report["human_feedback"] = evidence_report["human_feedback"]
        report["development_warnings"] = evidence_report["warnings"]
        if evidence_report["warnings"]:
            report["failed_or_null_quality_rows"].extend(evidence_report["warnings"])
    return report


def assess_development(*, latest: int = 20, db_path: Path | None = None, artifacts_root: Path | None = None) -> dict[str, Any]:
    rows = _select_trace_rows(db_path or _harness_db_path(), latest=latest, since_id=None, include_all=False)
    evidence_report = _development_evidence_report(rows, artifacts_root=artifacts_root)
    process = {
        "trace_count": len(rows),
        "linked_count": sum(1 for row in rows if row["intake_id"] is not None),
        "detailed_count": sum(1 for row in rows if not _trace_quality_issues(row)),
        "high_risk_count": sum(1 for row in rows if row["risk_lane"] in {"high-risk", "high_risk"}),
        "friction_trace_count": sum(1 for row in rows if row["harness_friction"] and row["harness_friction"] != "none"),
        "duration_seconds": _duration_stats([int(row["duration_seconds"]) for row in rows if row["duration_seconds"] is not None]),
    }
    missing_evidence = evidence_report["engineering_quality"]["missing_verification_evidence"]
    production_fail = evidence_report["production_impact"]["fail"]
    status = "fail" if production_fail else ("warn" if missing_evidence or evidence_report["warnings"] else "pass")
    return {
        "status": status,
        "latest": latest,
        "process_quality": process,
        "engineering_quality": evidence_report["engineering_quality"],
        "business_correctness": evidence_report["business_correctness"],
        "human_feedback": evidence_report["human_feedback"],
        "production_impact": evidence_report["production_impact"],
        "trace_evidence": evidence_report["traces"],
        "warnings": evidence_report["warnings"],
        "recommendations": _development_recommendations(evidence_report),
    }


def preflight_context(*, latest: int = 10, db_path: Path | None = None, artifacts_root: Path | None = None) -> dict[str, Any]:
    assessment = assess_development(latest=latest, db_path=db_path, artifacts_root=artifacts_root)
    audit = audit_traces(latest=latest, db_path=db_path)
    bullets = _preflight_bullets(assessment=assessment, audit=audit)
    return {
        "status": "warn" if bullets or assessment["status"] != "pass" or audit["status"] != "pass" else "pass",
        "latest": latest,
        "context_brief": _bounded_context_brief(bullets),
        "brief_limits": {"max_bullets": _CONTEXT_BRIEF_MAX_BULLETS, "max_chars": _CONTEXT_BRIEF_MAX_CHARS},
        "audit": {"status": audit["status"], "checked": audit["checked"], "failed": audit["failed"], "warned": audit.get("warned", 0)},
        "assessment": {
            "status": assessment["status"],
            "traces": assessment["process_quality"]["trace_count"],
            "high_risk": assessment["process_quality"]["high_risk_count"],
            "verified": assessment["engineering_quality"]["verified_count"],
            "missing_evidence": assessment["engineering_quality"]["missing_verification_evidence"],
            "production_fail": assessment["production_impact"]["fail"],
        },
        "anti_pollution": {
            "raw_trace_rows_included": False,
            "raw_artifacts_included": False,
            "memory_written": False,
            "global_context_updated": False,
        },
    }


def gate_development(
    *, latest: int = 5, policy: str = "observe", db_path: Path | None = None, artifacts_root: Path | None = None
) -> dict[str, Any]:
    if policy not in {"observe", "enforce"}:
        raise ValueError("policy must be observe or enforce")
    rows = _select_trace_rows(db_path or _harness_db_path(), latest=latest, since_id=None, include_all=False)
    assessment = assess_development(latest=latest, db_path=db_path, artifacts_root=artifacts_root)
    audit = audit_traces(latest=latest, db_path=db_path)
    issues = _gate_issues(assessment=assessment, audit=audit, rows=rows)
    blocking = any(issue.get("blocking", True) for issue in issues)
    return {
        "status": "fail" if blocking and policy == "enforce" else ("warn" if issues else "pass"),
        "policy": policy,
        "latest": latest,
        "issues": issues,
        "audit_status": audit["status"],
        "assessment_status": assessment["status"],
        "anti_pollution": {"memory_written": False, "global_context_updated": False},
    }


def recommend_next(*, latest: int = 10, db_path: Path | None = None, artifacts_root: Path | None = None) -> dict[str, Any]:
    assessment = assess_development(latest=latest, db_path=db_path, artifacts_root=artifacts_root)
    recommendations = _structured_recommendations(assessment)
    return {
        "status": "warn" if recommendations else "pass",
        "latest": latest,
        "recommendation_count": len(recommendations),
        "recommendations": recommendations,
        "anti_pollution": {"memory_written": False, "stories_written": False, "global_context_updated": False},
    }


def memory_candidates(*, latest: int = 20, db_path: Path | None = None, artifacts_root: Path | None = None) -> dict[str, Any]:
    assessment = assess_development(latest=latest, db_path=db_path, artifacts_root=artifacts_root)
    grouped: dict[str, list[int]] = {}
    for warning in assessment["warnings"]:
        for item in warning.get("warnings", []):
            grouped.setdefault(str(item), []).append(int(warning["id"]))
    candidates = []
    for index, (warning, trace_ids) in enumerate(sorted(grouped.items()), start=1):
        unique_ids = sorted(set(trace_ids))
        if len(unique_ids) < 2:
            continue
        candidates.append(
            {
                "id": f"memory-candidate-{index}",
                "source_trace_ids": unique_ids,
                "recurrence_count": len(unique_ids),
                "confidence": "medium",
                "proposed_memory_text": _memory_candidate_text(warning),
                "expiry_or_review_after": "review after 30 days or after the warning stops recurring",
            }
        )
    return {
        "status": "warn" if candidates else "pass",
        "latest": latest,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "anti_pollution": {"memory_written": False, "memory_path_touched": False, "global_context_updated": False},
    }


def _preflight_bullets(*, assessment: dict[str, Any], audit: dict[str, Any]) -> list[str]:
    bullets = []
    if audit["status"] != "pass":
        failed_ids = [str(item["id"]) for item in audit.get("failures", [])[:3]]
        bullets.append(f"Audit failed for trace ids {', '.join(failed_ids) or 'unknown'}; fix trace quality before relying on history.")
    audit_warnings = audit.get("warnings", [])
    if audit_warnings:
        warning_ids = [str(item["id"]) for item in audit_warnings[:3]]
        bullets.append(f"Audit warnings on trace ids {', '.join(warning_ids)}; check duration and verification evidence.")
    missing = assessment["engineering_quality"]["missing_verification_evidence"]
    if missing:
        missing_ids = [str(item["id"]) for item in assessment["trace_evidence"] if not item["verification_evidence"]][:3]
        bullets.append(f"{missing} trace(s) lack verification evidence; priority trace ids: {', '.join(missing_ids)}.")
    if assessment["process_quality"]["high_risk_count"]:
        bullets.append(
            f"{assessment['process_quality']['high_risk_count']} high-risk trace(s) in window; require test/validation/review evidence for new high-risk work."
        )
    skipped_review_ids = [
        str(item["id"])
        for item in assessment["trace_evidence"]
        if item["risk_lane"] in {"high-risk", "high_risk"} and item["review"]["status"] == "skipped"
    ][:3]
    if skipped_review_ids:
        bullets.append(f"High-risk trace ids {', '.join(skipped_review_ids)} skipped review; add review evidence or justification.")
    if assessment["production_impact"]["fail"] or assessment["production_impact"].get("blocked", 0):
        bullets.append("Production gate failure or block exists; do not promote live workflow until required fixes are resolved.")
    recommendations = assessment.get("recommendations", [])
    if recommendations:
        bullets.append(f"Next verification focus: {recommendations[0]}")
    return bullets


def _bounded_context_brief(bullets: Sequence[str]) -> list[str]:
    brief = []
    used = 0
    for bullet in bullets[:_CONTEXT_BRIEF_MAX_BULLETS]:
        text = str(bullet).replace("\n", " ").strip()
        if not text:
            continue
        remaining = _CONTEXT_BRIEF_MAX_CHARS - used
        if remaining <= 0:
            break
        if len(text) > remaining:
            text = text[: max(0, remaining - 3)].rstrip() + "..."
        brief.append(text)
        used += len(text)
    return brief


def _gate_issues(*, assessment: dict[str, Any], audit: dict[str, Any], rows: Sequence[sqlite3.Row]) -> list[dict[str, Any]]:
    issues = []
    if audit["status"] != "pass":
        issues.append({"code": "audit_failed", "severity": "high", "trace_ids": [item["id"] for item in audit.get("failures", [])]})
    warning_map: dict[str, list[int]] = {}
    for warning in audit.get("warnings", []):
        for item in warning.get("warnings", []):
            warning_map.setdefault(str(item), []).append(int(warning["id"]))
    for warning, trace_ids in sorted(warning_map.items()):
        issues.append({"code": _issue_code(warning), "severity": "medium", "trace_ids": sorted(set(trace_ids)), "reason": warning})
    high_risk_missing_duration = [
        int(row["id"])
        for row in rows
        if row["risk_lane"] in {"high-risk", "high_risk"}
        and int(row["duration_seconds"] or 0) == 0
        and "duration unavailable" in str(row["notes"] or "").lower()
    ]
    if high_risk_missing_duration:
        issues.append(
            {
                "code": "high_risk_missing_session_start",
                "severity": "medium",
                "trace_ids": high_risk_missing_duration,
                "reason": "high-risk trace has zero duration; use harness session-start for meaningful elapsed time",
            }
        )
    high_risk_missing_preflight = [
        int(row["id"])
        for row in rows
        if row["risk_lane"] in {"high-risk", "high_risk"} and not _trace_has_preflight_marker(row)
    ]
    if high_risk_missing_preflight:
        issues.append(
            {
                "code": "high_risk_missing_preflight",
                "severity": "high",
                "trace_ids": high_risk_missing_preflight,
                "reason": "high-risk trace does not record bounded preflight context application",
                "blocking": True,
            }
        )
    production_failed = [
        item["id"]
        for item in assessment["trace_evidence"]
        if item["production_gate"]["status"] in {"fail", "blocked"}
    ]
    if production_failed:
        issues.append(
            {
                "code": "production_gate_failed",
                "severity": "high",
                "trace_ids": production_failed,
                "reason": "production gate failed or blocked",
            }
        )
    for item in assessment["trace_evidence"]:
        review_issue = _high_risk_review_issue(item)
        if review_issue:
            issues.append(review_issue)
    return issues


def _structured_recommendations(assessment: dict[str, Any]) -> list[dict[str, Any]]:
    recommendations = []
    missing_ids = [item["id"] for item in assessment["trace_evidence"] if not item["verification_evidence"]]
    if missing_ids:
        recommendations.append(
            {
                "id": "rec-missing-verification-evidence",
                "source_trace_ids": missing_ids,
                "severity": "medium",
                "reason": "one or more traces do not have test, validation, review, runtime, or production-gate evidence",
                "suggested_action": "Record explicit outcome flags or attach validation/review/runtime artifacts in the next trace.",
                "evidence_refs": [f"trace:{trace_id}" for trace_id in missing_ids],
            }
        )
    production_ids = [item["id"] for item in assessment["trace_evidence"] if item["production_gate"]["status"] in {"fail", "blocked"}]
    if production_ids:
        recommendations.append(
            {
                "id": "rec-production-gate-failure",
                "source_trace_ids": production_ids,
                "severity": "high",
                "reason": "production gate failed or blocked",
                "suggested_action": "Resolve required fixes before live workflow promotion.",
                "evidence_refs": [f"trace:{trace_id}:production_gate" for trace_id in production_ids],
            }
        )
    runtime_ids = [
        item["id"]
        for item in assessment["trace_evidence"]
        if item["runtime_tools"]["failed"] or item["runtime_tools"]["blocked"]
    ]
    if runtime_ids:
        recommendations.append(
            {
                "id": "rec-runtime-tool-failures",
                "source_trace_ids": runtime_ids,
                "severity": "high",
                "reason": "runtime tool failures or policy blocks were recorded",
                "suggested_action": "Review failed tool events and add follow-up fixes before treating the session as clean.",
                "evidence_refs": [f"trace:{trace_id}:runtime_tools" for trace_id in runtime_ids],
            }
        )
    review_issues = [_high_risk_review_issue(item) for item in assessment["trace_evidence"]]
    review_issues = [item for item in review_issues if item]
    if review_issues:
        trace_ids = sorted({trace_id for issue in review_issues for trace_id in issue["trace_ids"]})
        recommendations.append(
            {
                "id": "rec-high-risk-review-evidence",
                "source_trace_ids": trace_ids,
                "severity": "high" if any(issue.get("blocking", True) for issue in review_issues) else "medium",
                "reason": "high-risk trace needs review evidence, review justification, or follow-up for failed review outcome",
                "suggested_action": "Attach review-report.json or record review_outcome with bounded review_evidence or review_justification.",
                "evidence_refs": [f"trace:{trace_id}:review" for trace_id in trace_ids],
            }
        )
    if assessment["human_feedback"]["status"] == "unknown":
        recommendations.append(
            {
                "id": "rec-human-correction-capture",
                "source_trace_ids": [item["id"] for item in assessment["trace_evidence"]],
                "severity": "low",
                "reason": "no human correction or intervention references were recorded in the window",
                "suggested_action": "When human correction affects implementation, record it explicitly in decisions or intervention records.",
                "evidence_refs": [],
            }
        )
    return recommendations


def _issue_code(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "quality_warning"


def _high_risk_review_issue(item: dict[str, Any]) -> dict[str, Any] | None:
    if item["risk_lane"] not in {"high-risk", "high_risk"}:
        return None
    review = item["review"]
    status = review["status"]
    evidence = review.get("evidence") or []
    justification = review.get("justification")
    if status == "pass":
        if review.get("source") == "trace_metadata" and not evidence:
            return {
                "code": "high_risk_review_missing_evidence",
                "severity": "high",
                "trace_ids": [item["id"]],
                "reason": "high-risk trace review_outcome=pass requires bounded review_evidence or review-report.json",
                "blocking": True,
            }
        return None
    if status == "skipped":
        return {
            "code": "high_risk_review_skipped",
            "severity": "medium",
            "trace_ids": [item["id"]],
            "reason": "high-risk trace skipped review; add review_justification or review evidence",
            "blocking": not bool(justification),
        }
    if status in {"fail", "blocked"}:
        return {
            "code": "high_risk_review_failed",
            "severity": "high",
            "trace_ids": [item["id"]],
            "reason": "high-risk trace review failed or blocked",
            "blocking": True,
        }
    if status == "manual_required":
        return {
            "code": "high_risk_review_manual_required",
            "severity": "medium",
            "trace_ids": [item["id"]],
            "reason": "high-risk trace review requires manual follow-up",
            "blocking": not bool(justification),
        }
    if status == "unknown":
        return {
            "code": "high_risk_review_missing_evidence",
            "severity": "high",
            "trace_ids": [item["id"]],
            "reason": "high-risk trace lacks review evidence",
            "blocking": True,
        }
    return None


def _memory_candidate_text(warning: str) -> str:
    if "review" in warning:
        return "For high-risk work, include review evidence or a clear skipped-review justification in the trace."
    if "verification" in warning:
        return "For high-risk harness work, include test, validation, review, or production-gate evidence in the trace."
    if "duration" in warning:
        return "For non-trivial harness work, start a session timer before implementation so trace duration is meaningful."
    return f"Recurring harness warning to review before similar future work: {warning}"


def _development_evidence_report(rows: Sequence[sqlite3.Row], *, artifacts_root: Path | None) -> dict[str, Any]:
    trace_reports = []
    warnings = []
    engineering = {
        "tests": _status_counter(),
        "validation": _status_counter(),
        "review": _status_counter(),
        "runtime_tool_failures": 0,
        "verified_count": 0,
        "missing_verification_evidence": 0,
    }
    business = _status_counter()
    production = _status_counter()
    production["required_fixes"] = []
    human_feedback = {"status": "unknown", "references": []}

    for row in rows:
        evidence = _trace_development_evidence(row, artifacts_root=artifacts_root)
        trace_reports.append(evidence)
        _increment_status(engineering["tests"], evidence["tests"]["status"])
        _increment_status(engineering["validation"], evidence["validation"]["status"])
        _increment_status(engineering["review"], evidence["review"]["status"])
        engineering["runtime_tool_failures"] += evidence["runtime_tools"]["failed"] + evidence["runtime_tools"]["blocked"]
        _increment_status(business, evidence["business_correctness"]["status"])
        _increment_status(production, evidence["production_gate"]["status"])
        production["required_fixes"].extend(evidence["production_gate"]["required_fixes"])
        if evidence["verification_evidence"]:
            engineering["verified_count"] += 1
        else:
            engineering["missing_verification_evidence"] += 1
        if evidence["human_corrections"]["references"]:
            human_feedback["references"].extend(evidence["human_corrections"]["references"])
            human_feedback["status"] = "recorded"
        if row["risk_lane"] in {"high-risk", "high_risk"} and not evidence["verification_evidence"]:
            warnings.append(
                {
                    "id": row["id"],
                    "task_summary": row["task_summary"],
                    "warnings": ["high-risk trace missing verification evidence"],
                }
            )
        review_issue = _high_risk_review_issue(evidence)
        if review_issue:
            warnings.append(
                {
                    "id": row["id"],
                    "task_summary": row["task_summary"],
                    "warnings": [review_issue["reason"]],
                }
            )

    production["required_fixes"] = _unique_values(production["required_fixes"])
    human_feedback["references"] = _unique_values(human_feedback["references"])
    return {
        "traces": trace_reports,
        "engineering_quality": engineering,
        "business_correctness": business,
        "production_impact": production,
        "human_feedback": human_feedback,
        "warnings": warnings,
    }


def _trace_development_evidence(row: sqlite3.Row, *, artifacts_root: Path | None) -> dict[str, Any]:
    decisions = _parse_json_array(row["decisions_made"]) or []
    notes = row["notes"] or ""
    explicit = _trace_explicit_outcomes(decisions, notes)
    roots = _artifact_roots_for_trace(row, artifacts_root=artifacts_root)
    validation = _validation_evidence(roots)
    review = _review_evidence(roots)
    runtime_tools = _runtime_tool_evidence(roots)
    eval_status = _eval_status(roots)
    live_gate = _production_gate_evidence(roots)

    if explicit.get("validation_outcome"):
        validation = {"status": explicit["validation_outcome"], "source": "trace_metadata", "platform": None}
    if explicit.get("review_outcome") and review["status"] == "unknown":
        review = {
            "status": explicit["review_outcome"],
            "source": "trace_metadata",
            "decision": None,
            "evidence": explicit.get("review_evidence", []),
            "justification": explicit.get("review_justification"),
        }
    tests = {"status": explicit.get("test_outcome") or eval_status["status"], "source": explicit.get("test_source") or eval_status["source"]}
    if explicit.get("production_impact"):
        live_gate = {"status": explicit["production_impact"], "source": "trace_metadata", "required_fixes": []}

    business_status = _business_correctness_status(validation["status"], live_gate["status"])
    human_refs = _human_correction_refs(decisions, notes)
    verification_evidence = any(
        status != "unknown" for status in (tests["status"], validation["status"], review["status"], live_gate["status"])
    ) or runtime_tools["status"] != "unknown"
    missing = []
    for key, signal in (("tests", tests), ("validation", validation), ("review", review), ("production_gate", live_gate)):
        if signal["status"] == "unknown":
            missing.append(key)

    return {
        "id": row["id"],
        "task_summary": row["task_summary"],
        "risk_lane": row["risk_lane"] or "unlinked",
        "artifact_roots": [str(root) for root in roots],
        "tests": tests,
        "validation": validation,
        "review": review,
        "runtime_tools": runtime_tools,
        "business_correctness": {"status": business_status, "source": "validation/live evidence"},
        "human_corrections": {"status": "recorded" if human_refs else "unknown", "references": human_refs},
        "production_gate": live_gate,
        "verification_evidence": verification_evidence,
        "missing_evidence": missing,
    }


def _status_counter() -> dict[str, int]:
    return {"pass": 0, "fail": 0, "manual_required": 0, "blocked": 0, "skipped": 0, "unknown": 0}


def _increment_status(counter: dict[str, int], status: str) -> None:
    counter[status if status in counter else "unknown"] += 1


def _trace_explicit_outcomes(decisions: Sequence[Any], notes: str) -> dict[str, Any]:
    outcomes: dict[str, Any] = {"review_evidence": []}
    status_keys = {"test_outcome", "review_outcome", "validation_outcome", "production_impact"}
    text_keys = {"review_evidence", "review_justification"}
    for text in _trace_metadata_chunks(decisions, notes):
        if "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key in status_keys and value:
            outcomes[key] = _normalize_status(value)
            outcomes[f"{key.rsplit('_', 1)[0]}_source"] = "trace_metadata"
        elif key == "review_evidence" and value:
            outcomes.setdefault("review_evidence", []).append(value)
        elif key in text_keys and value:
            outcomes[key] = value
    outcomes["review_evidence"] = _unique_values(outcomes.get("review_evidence", []))
    return outcomes


def _trace_metadata_chunks(decisions: Sequence[Any], notes: str) -> list[str]:
    chunks = [str(value).strip() for value in decisions if str(value).strip()]
    chunks.extend(part.strip() for part in str(notes or "").split(";") if part.strip())
    return chunks


def _artifact_roots_for_trace(row: sqlite3.Row, *, artifacts_root: Path | None) -> list[Path]:
    roots: list[Path] = []
    refs = [*(_parse_json_array(row["files_changed"]) or []), *(_parse_json_array(row["files_read"]) or [])]
    for ref in refs:
        for path in _candidate_paths(str(ref), artifacts_root):
            for root in _artifact_roots_from_path(path):
                if root not in roots:
                    roots.append(root)
    return roots


def _candidate_paths(ref: str, artifacts_root: Path | None) -> list[Path]:
    path = Path(ref)
    candidates = [path]
    if artifacts_root and not path.is_absolute():
        candidates.append(artifacts_root / path)
    return candidates


def _artifact_roots_from_path(path: Path) -> list[Path]:
    candidates = []
    if path.exists():
        candidates.append(path.parent if path.is_file() else path)
        if path.is_file():
            candidates.append(path.parent.parent)
    else:
        candidates.append(path.parent)
    roots = []
    for candidate in candidates:
        if candidate and candidate.exists() and any((candidate / name).exists() for name in _ARTIFACT_NAMES):
            roots.append(candidate)
    return roots


def _validation_evidence(roots: Sequence[Path]) -> dict[str, Any]:
    payload, source = _first_json_artifact(roots, "validation-report.json")
    if not payload:
        return {"status": "unknown", "source": None, "platform": None}
    return {"status": _normalize_status(payload.get("status")), "source": str(source), "platform": payload.get("platform")}


def _review_evidence(roots: Sequence[Path]) -> dict[str, Any]:
    payload, source = _first_json_artifact(roots, "review-report.json")
    if not payload:
        return {"status": "unknown", "source": None, "decision": None, "evidence": [], "justification": None}
    decision = str(payload.get("decision") or "unknown")
    status = {"approve": "pass", "changes_requested": "fail", "manual_required": "manual_required", "blocked": "blocked"}.get(
        decision, _normalize_status(payload.get("run_status"))
    )
    evidence_refs: list[str] = []
    for reviewer in payload.get("reviewers", []):
        if isinstance(reviewer, dict):
            evidence_refs.extend(str(ref) for ref in reviewer.get("evidence_refs", []))
    for finding in payload.get("findings", []):
        if isinstance(finding, dict):
            evidence_refs.extend(str(ref) for ref in finding.get("evidence_refs", []))
    return {"status": status, "source": str(source), "decision": decision, "evidence": _unique_values(evidence_refs), "justification": None}


def _runtime_tool_evidence(roots: Sequence[Path]) -> dict[str, Any]:
    failed = 0
    blocked = 0
    sources = []
    for root in roots:
        for name in ("runtime-trace.jsonl", "review-runtime-trace.jsonl"):
            path = root / name
            if not path.exists():
                continue
            sources.append(str(path))
            for event in _read_jsonl(path):
                if event.get("event_type") == "tool.failed" or event.get("status") == "fail":
                    failed += 1
                if event.get("event_type") == "tool.blocked" or event.get("status") == "blocked":
                    blocked += 1
    if not sources:
        return {"status": "unknown", "failed": 0, "blocked": 0, "sources": []}
    return {"status": "fail" if failed or blocked else "pass", "failed": failed, "blocked": blocked, "sources": sources}


def _eval_status(roots: Sequence[Path]) -> dict[str, Any]:
    payload, source = _first_json_artifact(roots, "eval-report.json")
    if not payload:
        return {"status": "unknown", "source": None}
    return {"status": _normalize_status(payload.get("status")), "source": str(source)}


def _production_gate_evidence(roots: Sequence[Path]) -> dict[str, Any]:
    payload, source = _first_json_artifact(roots, "live-workflow-trace.json")
    if not payload:
        return {"status": "unknown", "source": None, "required_fixes": []}
    final_decision = payload.get("final_decision") if isinstance(payload.get("final_decision"), dict) else {}
    gate = final_decision.get("production_gate") if isinstance(final_decision.get("production_gate"), dict) else {}
    status = _normalize_status(gate.get("status") or final_decision.get("status"))
    return {"status": status, "source": str(source), "required_fixes": list(gate.get("required_fixes") or [])}


def _first_json_artifact(roots: Sequence[Path], name: str) -> tuple[dict[str, Any] | None, Path | None]:
    for root in roots:
        path = root / name
        if not path.exists():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8")), path
        except (OSError, json.JSONDecodeError):
            return {"status": "fail"}, path
    return None, None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    events = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            events.append({"status": "fail", "event_type": "tool.failed"})
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _business_correctness_status(validation_status: str, production_status: str) -> str:
    if "fail" in {validation_status, production_status} or "blocked" in {validation_status, production_status}:
        return "fail"
    if "manual_required" in {validation_status, production_status}:
        return "manual_required"
    if validation_status == "pass" or production_status == "pass":
        return "pass"
    if validation_status == "skipped" and production_status == "skipped":
        return "skipped"
    return "unknown"


def _human_correction_refs(decisions: Sequence[Any], notes: str) -> list[str]:
    refs = []
    for value in [*decisions, notes]:
        text = str(value)
        if any(term in text.lower() for term in ("intervention", "correction", "human correction", "user correction")):
            refs.append(text)
    return _unique_values(refs)


def _normalize_status(value: Any) -> str:
    text = str(value or "unknown").strip().lower().replace("-", "_")
    return {
        "approved": "pass",
        "completed": "pass",
        "changes_requested": "fail",
        "error": "fail",
        "blocked": "blocked",
        "manual": "manual_required",
        "manual_required": "manual_required",
        "skipped": "skipped",
        "pass": "pass",
        "fail": "fail",
    }.get(text, "unknown")


def _development_recommendations(evidence_report: dict[str, Any]) -> list[str]:
    recommendations = []
    engineering = evidence_report["engineering_quality"]
    production = evidence_report["production_impact"]
    if engineering["missing_verification_evidence"]:
        recommendations.append("Add test, validation, review, or production-gate evidence for traces marked unknown.")
    if engineering["runtime_tool_failures"]:
        recommendations.append("Review failed or blocked runtime tool events before treating the development session as clean.")
    if production["fail"] or production["blocked"]:
        recommendations.append("Resolve production gate failures or required fixes before live workflow promotion.")
    if evidence_report["human_feedback"]["status"] == "unknown":
        recommendations.append("Record human corrections or interventions when they influence implementation decisions.")
    return recommendations


def build_trace_command(
    summary: str,
    intake: int | None,
    story: str | None,
    agent: str,
    outcome: str,
    changed: Sequence[str],
    actions: Sequence[str] | None = None,
    read: Sequence[str] | None = None,
    errors: str | None = None,
    friction: str | None = None,
    duration: int | None = None,
    tokens: int | None = None,
    decisions: Sequence[str] | None = None,
    notes: str | None = None,
) -> list[str]:
    command = [
        str(harness_cli_path()),
        "trace",
        "--summary",
        summary,
        "--agent",
        agent,
        "--outcome",
        outcome,
        "--changed",
        ",".join(changed),
    ]
    if intake is not None:
        command.extend(["--intake", str(intake)])
    if story:
        command.extend(["--story", story])
    _append_trace_option(command, "--actions", actions)
    _append_trace_option(command, "--read", read)
    _append_trace_option(command, "--errors", errors)
    _append_trace_option(command, "--friction", friction)
    _append_trace_option(command, "--duration", duration)
    _append_trace_option(command, "--tokens", tokens)
    _append_trace_option(command, "--decisions", decisions)
    if notes:
        command.extend(["--notes", notes])
    return command


def _append_trace_option(command: list[str], flag: str, value: Sequence[str] | str | int | None) -> None:
    if value is None:
        return
    if isinstance(value, str):
        text = value
    elif isinstance(value, int):
        text = str(value)
    else:
        text = ",".join(str(item) for item in value)
    command.extend([flag, text])


def harness_outcome(validation_status: str) -> str:
    return {
        "pass": "completed",
        "fail": "failed",
        "manual_required": "partial",
        "skipped": "partial",
    }.get(validation_status, "partial")


def record_trace(command: list[str]) -> None:
    harness_cli = command[0]
    _initialize_harness_db(harness_cli)
    subprocess.run(command, cwd=repo_root(), check=True)
    _normalize_no_error_trace(command)


def record_intake(
    summary: str,
    input_type: str,
    lane: str,
    docs: Sequence[str] | None = None,
    story: str | None = None,
    notes: str | None = None,
) -> int:
    harness_cli = str(harness_cli_path())
    _initialize_harness_db(harness_cli)
    command = [
        harness_cli,
        "intake",
        "--type",
        input_type,
        "--summary",
        summary,
        "--lane",
        lane,
    ]
    _append_trace_option(command, "--docs", docs)
    if story:
        command.extend(["--story", story])
    if notes:
        command.extend(["--notes", notes])
    result = subprocess.run(command, cwd=repo_root(), check=True, capture_output=True, text=True)
    match = _INTAKE_RE.search(result.stdout or "")
    if not match:
        raise RuntimeError(f"Unable to parse harness intake id from output: {result.stdout!r}")
    return int(match.group("id"))


def record_trace_intake(
    summary: str,
    docs: Sequence[str],
    story: str | None = None,
    input_type: str | None = None,
    lane: str | None = None,
    changed: Sequence[str] | None = None,
    notes: str = "auto-created for strategy-codebot repository trace",
) -> int:
    classification = classify_trace_intake(summary=summary, read=docs, changed=changed, input_type=input_type, lane=lane)
    return record_intake(
        summary=summary,
        input_type=classification["input_type"],
        lane=classification["lane"],
        docs=_unique_values([*classification["read"], *classification["changed"]]),
        story=story,
        notes=notes,
    )


def _initialize_harness_db(harness_cli: str) -> None:
    for setup_command in ([harness_cli, "init"], [harness_cli, "migrate"]):
        subprocess.run(setup_command, cwd=repo_root(), check=True)


def _normalize_no_error_trace(command: Sequence[str]) -> None:
    try:
        errors_index = command.index("--errors") + 1
    except ValueError:
        return
    if errors_index >= len(command) or command[errors_index] != NO_ERROR_TRACE_ARG:
        return
    db_path = repo_root() / "harness.db"
    if not db_path.exists():
        return
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "update trace set errors = ? where id = (select max(id) from trace) and errors = ?",
            (NO_ERROR_TRACE_ARG, _NO_ERROR_CSV_JSON),
        )


def _unique_values(values: Sequence[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in unique:
            unique.append(text)
    return unique


def _harness_db_path() -> Path:
    return repo_root() / "harness.db"


def _select_trace_rows(
    db_path: Path,
    *,
    latest: int,
    since_id: int | None,
    include_all: bool,
) -> list[sqlite3.Row]:
    if not db_path.exists():
        raise FileNotFoundError(f"harness database not found: {db_path}")
    latest = max(1, latest)
    query = (
        "select trace.*, intake.input_type, intake.risk_lane "
        "from trace left join intake on intake.id = trace.intake_id"
    )
    parameters: tuple[Any, ...] = ()
    if include_all:
        query = f"{query} order by trace.id"
    elif since_id is not None:
        query = f"{query} where trace.id >= ? order by trace.id"
        parameters = (since_id,)
    else:
        query = (
            "select * from ("
            f"{query} order by trace.id desc limit ?"
            ") order by id"
        )
        parameters = (latest,)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        return list(connection.execute(query, parameters))


def _trace_quality_issues(row: sqlite3.Row) -> list[str]:
    issues: list[str] = []
    if row["intake_id"] is None:
        issues.append("intake_id: null")
    for field in _TRACE_ARRAY_FIELDS:
        value = _trace_json_array(row[field], field, issues)
        if field in _REQUIRED_NON_EMPTY_TRACE_ARRAY_FIELDS and value == []:
            issues.append(f"{field}: empty")
    if row["harness_friction"] is None or not str(row["harness_friction"]).strip():
        issues.append("harness_friction: null")
    if row["duration_seconds"] is None:
        issues.append("duration_seconds: null")
    if row["token_estimate"] is None:
        issues.append("token_estimate: null")
    return issues


def _trace_quality_warnings(row: sqlite3.Row) -> list[str]:
    warnings = []
    notes = str(row["notes"] or "").lower()
    if row["duration_seconds"] == 0 and "duration unavailable" not in notes:
        warnings.append("duration_seconds: zero_without_unavailable_note")
    if row["risk_lane"] in {"high-risk", "high_risk"} and not _trace_has_verification_metadata(row):
        warnings.append("high-risk trace missing verification evidence")
    return warnings


def _trace_has_verification_metadata(row: sqlite3.Row) -> bool:
    refs = [*(_parse_json_array(row["files_changed"]) or []), *(_parse_json_array(row["files_read"]) or [])]
    if any(Path(str(ref)).name in _ARTIFACT_NAMES for ref in refs):
        return True
    decisions = _parse_json_array(row["decisions_made"]) or []
    text = " ".join(str(value) for value in [*decisions, row["notes"] or ""]).lower()
    return any(prefix in text for prefix in _VERIFICATION_DECISION_PREFIXES)


def _trace_has_preflight_marker(row: sqlite3.Row) -> bool:
    decisions = _parse_json_array(row["decisions_made"]) or []
    text = " ".join(str(value) for value in [*decisions, row["notes"] or ""]).lower()
    return "preflight_applied=true" in text


def _trace_json_array(value: str | None, field: str, issues: list[str]) -> list[Any]:
    if value is None:
        issues.append(f"{field}: null")
        return []
    parsed = _parse_json_array(value)
    if parsed is None:
        issues.append(f"{field}: invalid_json_array")
        return []
    return parsed


def _parse_json_array(value: str | None) -> list[Any] | None:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None


def _counter_top(counter: Counter[str], limit: int = 10) -> list[dict[str, Any]]:
    return [{"value": value, "count": count} for value, count in counter.most_common(limit)]


def _duration_stats(values: Sequence[int]) -> dict[str, float | int]:
    if not values:
        return {"total": 0, "avg": 0.0, "max": 0}
    total = sum(values)
    return {"total": total, "avg": total / len(values), "max": max(values)}


def _trace_scope(*, latest: int, since_id: int | None, include_all: bool) -> dict[str, Any]:
    if include_all:
        return {"type": "all"}
    if since_id is not None:
        return {"type": "since_id", "since_id": since_id}
    return {"type": "latest", "latest": max(1, latest)}


def _classify_trace_lane(*, summary: str, read: Sequence[str], changed: Sequence[str]) -> str:
    changed_values = _unique_values(changed)
    if changed_values and len(changed_values) > 5:
        return "high-risk"
    if changed_values and all(_is_doc_ref(value) for value in changed_values):
        return "tiny"
    text = " ".join([summary, *read, *changed_values]).lower()
    if any(term in text for term in _HIGH_RISK_TERMS):
        return "high-risk"
    return "normal"


def _is_doc_ref(value: str) -> bool:
    path = value.lower()
    return path.startswith("docs/") or path == "agents.md" or path.endswith(_DOC_SUFFIXES)
