from __future__ import annotations

from typing import Any

from strategy_codebot.harness_types import STATUS_FAIL, STATUS_PASS, STATUS_SKIPPED

REPAIR_SOURCE_LLM = "llm"
REPAIR_SOURCE_DETERMINISTIC = "deterministic"
REPAIR_SOURCE_UNKNOWN = "unknown"
REPAIR_SOURCES = (REPAIR_SOURCE_LLM, REPAIR_SOURCE_DETERMINISTIC, REPAIR_SOURCE_UNKNOWN)
BLOCKING_POLICY_SEVERITIES = frozenset({"block", "blocker"})
REVIEW_BLOCKING_STATUSES = frozenset(
    {STATUS_FAIL, "failed", "needs_fix", "changes_requested", "blocked"}
)


def nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def validation_failures(validation: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not validation:
        return []
    return [
        {"name": check.get("name"), "status": check.get("status"), "details": check.get("details")}
        for check in validation.get("checks", [])
        if check.get("status") not in {STATUS_PASS, STATUS_SKIPPED}
    ]


def validation_allows_artifact(validation: dict[str, Any]) -> bool:
    return validation.get("status") == STATUS_PASS or (
        validation.get("status") == "manual_required" and not validation_failures(validation)
    )


def repair_source_mix(
    repair_history: list[dict[str, Any]],
    *,
    repair_count: int,
    repair_loop_metrics: dict[str, Any] | None = None,
    llm_repair_count: Any = None,
    deterministic_repair_count: Any = None,
) -> dict[str, int]:
    mix = {source: 0 for source in REPAIR_SOURCES}
    for entry in repair_history:
        source = ""
        if isinstance(entry, dict):
            source = str(entry.get("repair_source") or "")
            if not source and entry.get("stage") == "compact_free_validation_repair":
                source = REPAIR_SOURCE_LLM
        if source not in {REPAIR_SOURCE_LLM, REPAIR_SOURCE_DETERMINISTIC}:
            source = REPAIR_SOURCE_UNKNOWN
        mix[source] += 1

    if repair_loop_metrics:
        llm_repair_count = repair_loop_metrics.get("llm_repair_count")
        deterministic_repair_count = repair_loop_metrics.get("deterministic_repair_count")
    mix[REPAIR_SOURCE_LLM] = max(mix[REPAIR_SOURCE_LLM], nonnegative_int(llm_repair_count))
    mix[REPAIR_SOURCE_DETERMINISTIC] = max(
        mix[REPAIR_SOURCE_DETERMINISTIC],
        nonnegative_int(deterministic_repair_count),
    )

    known_count = sum(mix.values())
    if repair_count > known_count:
        mix[REPAIR_SOURCE_UNKNOWN] += repair_count - known_count
    return mix


def evaluator_review_status(
    *,
    review_output: dict[str, Any] | None = None,
    review_report: dict[str, Any] | None = None,
    production_gate: dict[str, Any] | None = None,
    final_decision: dict[str, Any] | None = None,
) -> str | None:
    review_output = review_output or {}
    review_report = review_report or {}
    production_gate = production_gate or {}
    final_decision = final_decision or {}
    status = (
        review_output.get("verdict")
        or review_output.get("decision")
        or review_output.get("run_status")
        or review_report.get("decision")
        or review_report.get("run_status")
        or production_gate.get("review_verdict")
        or production_gate.get("review_decision")
        or final_decision.get("review_verdict")
    )
    return str(status) if status is not None else None


def evaluator_stop_reason(
    *,
    validation: dict[str, Any],
    final_review_status: str | None,
    production_gate: dict[str, Any],
    policy_findings: list[dict[str, Any]],
    budget_exhausted: bool,
) -> str:
    if any(_policy_finding_blocks(finding) for finding in policy_findings):
        return "policy_blocked"
    if budget_exhausted:
        return "repair_budget_exhausted"
    if validation and not validation_allows_artifact(validation):
        return "validation_blocked"
    review_status = str(final_review_status or "").lower()
    if production_gate.get("status") == STATUS_FAIL:
        if review_status in REVIEW_BLOCKING_STATUSES or production_gate.get("blocking_required_fixes"):
            return "review_blocked"
        return "production_gate_failed"
    if production_gate.get("status") == STATUS_PASS:
        return "production_gate_passed"
    return "completed"


def _policy_finding_blocks(finding: dict[str, Any]) -> bool:
    return str(finding.get("severity") or "").lower() in BLOCKING_POLICY_SEVERITIES
