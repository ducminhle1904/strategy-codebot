from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from strategy_codebot.harness_types import (
    FAILURE_ARTIFACT_MISSING,
    FAILURE_FREE_CAPACITY_UNAVAILABLE,
    FAILURE_MALFORMED_RESPONSE,
    FAILURE_MISSING_CREDENTIAL,
    FAILURE_POLICY_VIOLATION,
    FAILURE_PROVIDER_ERROR,
    FAILURE_PROVIDER_NOT_FOUND,
    FAILURE_PROVIDER_RATE_LIMITED,
    FAILURE_PROVIDER_TIMEOUT,
    FAILURE_REVIEW_FAILED,
    FAILURE_REVIEW_VALIDATION_DISAGREEMENT,
    FAILURE_SCHEMA_INVALID,
    FAILURE_STATIC_VALIDATION_FAILED,
    FAILURE_TOOL_ERROR,
    FAILURE_UNKNOWN,
    STATUS_BLOCKED,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_SKIPPED,
)
from strategy_codebot.paths import ensure_parent
from strategy_codebot.schemas import load_json, write_json


HARNESS_REPORT_PATH = "agent-harness-report.json"
OTEL_TRACE_PATH = "otel-trace.jsonl"
OPTIONAL_ARTIFACTS = (
    "agent-run.json",
    "runtime-summary.json",
    "live-error.json",
    "live-metadata.json",
    "live-workflow-trace.json",
    "validation-report.json",
    "review-report.json",
)
LIFECYCLE_EVENT_TYPES = {
    "agent.started",
    "agent.handoff",
    "llm.started",
    "llm.completed",
    "guardrail.blocked",
    "repair.started",
    "repair.completed",
    "agent.completed",
}
FAILURE_CLASSES = {
    "missing_provider_credential": FAILURE_MISSING_CREDENTIAL,
    FAILURE_MISSING_CREDENTIAL: FAILURE_MISSING_CREDENTIAL,
    FAILURE_PROVIDER_RATE_LIMITED: FAILURE_PROVIDER_RATE_LIMITED,
    FAILURE_PROVIDER_TIMEOUT: FAILURE_PROVIDER_TIMEOUT,
    FAILURE_PROVIDER_NOT_FOUND: FAILURE_PROVIDER_NOT_FOUND,
    "malformed_provider_response": FAILURE_MALFORMED_RESPONSE,
    FAILURE_MALFORMED_RESPONSE: FAILURE_MALFORMED_RESPONSE,
    "schema_invalid_provider_response": FAILURE_SCHEMA_INVALID,
    FAILURE_SCHEMA_INVALID: FAILURE_SCHEMA_INVALID,
    "safety_policy_violation": FAILURE_POLICY_VIOLATION,
    FAILURE_POLICY_VIOLATION: FAILURE_POLICY_VIOLATION,
    FAILURE_STATIC_VALIDATION_FAILED: FAILURE_STATIC_VALIDATION_FAILED,
    FAILURE_REVIEW_FAILED: FAILURE_REVIEW_FAILED,
    FAILURE_REVIEW_VALIDATION_DISAGREEMENT: FAILURE_REVIEW_VALIDATION_DISAGREEMENT,
    FAILURE_ARTIFACT_MISSING: FAILURE_ARTIFACT_MISSING,
    FAILURE_FREE_CAPACITY_UNAVAILABLE: FAILURE_FREE_CAPACITY_UNAVAILABLE,
}


def inspect_run(run_dir: Path, *, out_path: Path | None = None) -> dict[str, Any]:
    artifacts = {name: _read_json_if_exists(run_dir / name) for name in OPTIONAL_ARTIFACTS}
    missing = [name for name, payload in artifacts.items() if payload is None]
    agent_run = artifacts["agent-run.json"] or {}
    runtime_summary = artifacts["runtime-summary.json"] or {}
    live_error = artifacts["live-error.json"] or {}
    live_metadata = artifacts["live-metadata.json"] or {}
    workflow_trace = artifacts["live-workflow-trace.json"] or _workflow_trace_from_live_error(live_error)
    validation = artifacts["validation-report.json"] or {}
    review = artifacts["review-report.json"] or {}
    runtime_events, trace_parse_errors = _read_jsonl_result(run_dir / str(runtime_summary.get("trace_ref", "runtime-trace.jsonl")))
    attempt_diagnostics = _attempt_diagnostics(live_metadata, workflow_trace)

    failure_attribution = _failure_attribution(
        missing_artifacts=missing,
        runtime_summary=runtime_summary,
        validation=validation,
        review=review,
        live_error=live_error,
        live_metadata=live_metadata,
        workflow_trace=workflow_trace,
        runtime_events=runtime_events,
        attempt_diagnostics=attempt_diagnostics,
        trace_parse_errors=trace_parse_errors,
    )
    report = {
        "run_dir": str(run_dir),
        "created_at": datetime.now(UTC).isoformat(),
        "status": STATUS_FAIL if failure_attribution else STATUS_PASS,
        "run_id": agent_run.get("run_id") or runtime_summary.get("run_id") or run_dir.name,
        "artifact_refs": {name: name for name, payload in artifacts.items() if payload is not None},
        "missing_artifacts": missing,
        "stage_timeline": _stage_timeline(workflow_trace, runtime_events),
        "model_provider_map": _model_provider_map(agent_run, live_metadata, workflow_trace),
        "total_latency_ms": live_metadata.get("total_latency_ms") or live_metadata.get("latency_ms") or _sum_stage_latency(workflow_trace),
        "usage": live_metadata.get("total_usage") or live_metadata.get("usage", {}),
        "repair_count": live_metadata.get("repair_count") or _final_decision(workflow_trace).get("repair_count", 0),
        "policy_findings": _policy_findings(runtime_summary, workflow_trace, runtime_events),
        "trace_parse_errors": trace_parse_errors,
        "attempt_diagnostics": attempt_diagnostics,
        "failure_attribution": failure_attribution,
    }
    if out_path:
        write_json(out_path, report)
    return report


def write_otel_export(run_dir: Path, out_path: Path, *, case_id: str | None = None) -> list[dict[str, Any]]:
    spans = otel_spans_for_run(run_dir, case_id=case_id)
    ensure_parent(out_path)
    with out_path.open("w", encoding="utf-8") as handle:
        for span in spans:
            handle.write(json.dumps(span, ensure_ascii=False) + "\n")
    return spans


def write_combined_otel_export(run_dirs: list[Path], out_path: Path) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    ensure_parent(out_path)
    with out_path.open("w", encoding="utf-8") as handle:
        for run_dir in run_dirs:
            for span in otel_spans_for_run(run_dir, case_id=run_dir.name):
                spans.append(span)
                handle.write(json.dumps(span, ensure_ascii=False) + "\n")
    return spans


def otel_spans_for_run(run_dir: Path, *, case_id: str | None = None) -> list[dict[str, Any]]:
    report = inspect_run(run_dir)
    runtime_summary = _read_json_if_exists(run_dir / "runtime-summary.json") or {}
    runtime_events = _read_jsonl(run_dir / str(runtime_summary.get("trace_ref", "runtime-trace.jsonl")))
    workflow_trace = _read_json_if_exists(run_dir / "live-workflow-trace.json") or {}
    if not workflow_trace:
        workflow_trace = _workflow_trace_from_live_error(_read_json_if_exists(run_dir / "live-error.json") or {})
    trace_id = _stable_id(f"trace:{report['run_id']}:{case_id or ''}")
    spans = [
        _span_from_event(trace_id, report["run_id"], event, case_id=case_id)
        for event in runtime_events
    ]
    if not any(event.get("event_type") in LIFECYCLE_EVENT_TYPES for event in runtime_events):
        for event in workflow_trace.get("lifecycle_events", []):
            spans.append(_span_from_event(trace_id, report["run_id"], event, case_id=case_id))
    return spans


def classify_failure(error_code: str | None, error: str | None = None) -> str:
    if error_code in FAILURE_CLASSES:
        return FAILURE_CLASSES[error_code]
    text = (error or "").lower()
    if "429" in text or "ratelimit" in text or "rate limit" in text or "rate-limited" in text:
        return FAILURE_PROVIDER_RATE_LIMITED
    if "404" in text or "notfound" in text or "no endpoints found" in text:
        return FAILURE_PROVIDER_NOT_FOUND
    if "timeout" in text or "readtimeout" in text or "timed out" in text:
        return FAILURE_PROVIDER_TIMEOUT
    if "schema" in text or "validationerror" in text:
        return FAILURE_SCHEMA_INVALID
    if "policy" in text or "safety" in text:
        return FAILURE_POLICY_VIOLATION
    return FAILURE_PROVIDER_ERROR if error_code == FAILURE_PROVIDER_ERROR else (error_code or FAILURE_UNKNOWN)


def _workflow_trace_from_live_error(live_error: dict[str, Any]) -> dict[str, Any]:
    diagnostics = live_error.get("diagnostics", {}) if isinstance(live_error.get("diagnostics"), dict) else {}
    workflow_trace = diagnostics.get("workflow_trace", {})
    return workflow_trace if isinstance(workflow_trace, dict) else {}


def _live_error_failures(live_error: dict[str, Any]) -> list[dict[str, Any]]:
    diagnostics = live_error.get("diagnostics", {}) if isinstance(live_error.get("diagnostics"), dict) else {}
    final_decision = diagnostics.get("final_decision", {}) if isinstance(diagnostics.get("final_decision"), dict) else {}
    attempts = live_error.get("attempts", [])
    failed_attempts = [failure_from_attempt(attempt) for attempt in attempts if attempt.get("status") in {STATUS_FAIL, STATUS_SKIPPED}]
    if failed_attempts:
        return failed_attempts
    failure_class = final_decision.get("failure_class") or classify_failure(live_error.get("code"), live_error.get("message"))
    return [{"failure_class": failure_class, "stage": final_decision.get("failure_stage"), "details": live_error.get("message")}]


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    try:
        payload = load_json(path)
    except FileNotFoundError:
        return None
    return payload if isinstance(payload, dict) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return _read_jsonl_result(path)[0]


def _read_jsonl_result(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        handle = path.open(encoding="utf-8")
    except FileNotFoundError:
        return [], []
    events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    with handle:
        for line_number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    errors.append({"line": line_number, "message": str(exc)})
                    continue
                if isinstance(payload, dict):
                    events.append(payload)
    return events, errors


def _failure_attribution(
    *,
    missing_artifacts: list[str],
    runtime_summary: dict[str, Any],
    validation: dict[str, Any],
    review: dict[str, Any],
    live_error: dict[str, Any],
    live_metadata: dict[str, Any],
    workflow_trace: dict[str, Any],
    runtime_events: list[dict[str, Any]],
    attempt_diagnostics: list[dict[str, Any]],
    trace_parse_errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    if live_error:
        failures.extend(_live_error_failures(live_error))
    critical_artifacts = {"agent-run.json"} if live_error else {"agent-run.json", "validation-report.json"}
    critical_missing = [name for name in missing_artifacts if name in critical_artifacts]
    for name in critical_missing:
        failures.append({"failure_class": FAILURE_ARTIFACT_MISSING, "artifact": name, "details": f"Missing required artifact {name}."})
    for tool_id in runtime_summary.get("failed_tools", []):
        failures.append({"failure_class": FAILURE_TOOL_ERROR, "tool_id": tool_id, "details": "Runtime tool failed."})
    for tool_id in runtime_summary.get("blocked_tools", []):
        failures.append({"failure_class": FAILURE_POLICY_VIOLATION, "tool_id": tool_id, "details": "Runtime tool blocked by policy."})
    if validation and validation.get("status") == STATUS_FAIL:
        failures.append({"failure_class": FAILURE_STATIC_VALIDATION_FAILED, "details": "Validation report status is fail."})
    if review and review.get("decision") not in {None, STATUS_PASS, "accept", "approve"}:
        failures.append({"failure_class": FAILURE_REVIEW_FAILED, "details": f"Review decision is {review.get('decision')}."})
    for error in trace_parse_errors:
        failures.append({"failure_class": FAILURE_MALFORMED_RESPONSE, "artifact": "runtime-trace.jsonl", "details": f"Malformed JSONL at line {error['line']}: {error['message']}"})
    final_gate_passed = _final_gate_pass(validation, review, workflow_trace)
    if not final_gate_passed:
        failures.extend(attempt_diagnostics)
    for event in runtime_events:
        if event.get("failure_class"):
            if final_gate_passed and event.get("event_type") == "llm.completed":
                continue
            failures.append({"failure_class": event["failure_class"], "event_type": event.get("event_type"), "details": event.get("status", "runtime event failure")})
    return failures


def failure_from_attempt(attempt: dict[str, Any]) -> dict[str, Any]:
    return {
        "failure_class": classify_failure(attempt.get("failure_class") or attempt.get("error_code"), attempt.get("error")),
        "stage": attempt.get("stage"),
        "model": attempt.get("model"),
        "provider": attempt.get("provider"),
        "attempt": attempt.get("attempt"),
        "details": attempt.get("error") or attempt.get("error_code"),
    }


def _attempt_diagnostics(live_metadata: dict[str, Any], workflow_trace: dict[str, Any]) -> list[dict[str, Any]]:
    attempts = [*live_metadata.get("attempts", []), *workflow_trace.get("attempts", [])]
    return [failure_from_attempt(attempt) for attempt in attempts if attempt.get("status") in {STATUS_FAIL, STATUS_SKIPPED}]


def _final_gate_pass(validation: dict[str, Any], review: dict[str, Any], workflow_trace: dict[str, Any]) -> bool:
    final_decision = _final_decision(workflow_trace)
    if final_decision and final_decision.get("status") != STATUS_PASS:
        return False
    if validation and validation.get("status") == STATUS_FAIL:
        return False
    if review and review.get("decision") not in {None, STATUS_PASS, "accept", "approve"}:
        return False
    return True


def _stage_timeline(workflow_trace: dict[str, Any], runtime_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timeline = []
    for stage in workflow_trace.get("stages", []):
        timeline.append(
            {
                "event_type": "agent.completed",
                "stage": stage.get("stage"),
                "agent_role": stage.get("agent_role") or stage.get("stage"),
                "model": stage.get("model"),
                "provider": stage.get("provider"),
                "latency_ms": stage.get("latency_ms"),
            "status": stage.get("status", STATUS_PASS),
            }
        )
    if timeline:
        return timeline
    return [
        {
            "event_type": event.get("event_type"),
            "tool_id": event.get("tool_id"),
            "stage": event.get("stage"),
            "agent_role": event.get("agent_role"),
            "status": event.get("status"),
        }
        for event in runtime_events
    ]


def _model_provider_map(agent_run: dict[str, Any], live_metadata: dict[str, Any], workflow_trace: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    if agent_run:
        rows.append({"agent_role": agent_run.get("agent_role"), "model": agent_run.get("model"), "provider": agent_run.get("provider")})
    for stage in live_metadata.get("stages", []) or workflow_trace.get("stages", []):
        rows.append({"agent_role": stage.get("agent_role") or stage.get("stage"), "stage": stage.get("stage"), "model": stage.get("model"), "provider": stage.get("provider")})
    return rows


def _sum_stage_latency(workflow_trace: dict[str, Any]) -> int:
    return sum(int(stage.get("latency_ms", 0)) for stage in workflow_trace.get("stages", []))


def _final_decision(workflow_trace: dict[str, Any]) -> dict[str, Any]:
    decision = workflow_trace.get("final_decision", {})
    return decision if isinstance(decision, dict) else {}


def _policy_findings(runtime_summary: dict[str, Any], workflow_trace: dict[str, Any], runtime_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = [{"source": "runtime-summary", "blocked_tool": tool_id} for tool_id in runtime_summary.get("blocked_tools", [])]
    for event in runtime_events:
        if event.get("event_type") in {"tool.blocked", "guardrail.blocked"}:
            findings.append({"source": "runtime-trace", "event_type": event.get("event_type"), "failure_class": event.get("failure_class")})
    for stage in workflow_trace.get("stages", []):
        observations = stage.get("policy_observations", [])
        if observations:
            findings.append({"source": "live-workflow-trace", "stage": stage.get("stage"), "observations": observations})
    return findings


def _span_from_event(trace_id: str, run_id: str, event: dict[str, Any], *, case_id: str | None) -> dict[str, Any]:
    event_id = str(event.get("event_id") or event.get("sequence") or _stable_id(json.dumps(event, sort_keys=True, default=str)))
    parent_event_id = event.get("parent_event_id")
    attributes = {
        "strategy_codebot.run_id": run_id,
        "strategy_codebot.case_id": case_id,
        "strategy_codebot.event_type": event.get("event_type"),
        "strategy_codebot.workflow": event.get("workflow"),
        "gen_ai.operation.name": event.get("stage") or event.get("tool_id") or event.get("event_type"),
        "gen_ai.system": event.get("provider"),
        "gen_ai.request.model": event.get("model"),
        "gen_ai.agent.name": event.get("agent_role"),
        "gen_ai.tool.name": event.get("tool_id"),
        "gen_ai.usage.input_tokens": _usage_value(event, "prompt_tokens", "input_tokens"),
        "gen_ai.usage.output_tokens": _usage_value(event, "completion_tokens", "output_tokens"),
        "gen_ai.usage.total_tokens": _usage_value(event, "total_tokens"),
    }
    return {
        "trace_id": trace_id,
        "span_id": _stable_id(f"{trace_id}:{event_id}")[:16],
        "parent_span_id": _stable_id(f"{trace_id}:{parent_event_id}")[:16] if parent_event_id else None,
        "name": str(event.get("event_type", "strategy_codebot.event")),
        "kind": "internal",
        "start_time": event.get("created_at"),
        "end_time": event.get("created_at"),
        "status": {"code": "ERROR" if event.get("status") in {STATUS_FAIL, STATUS_BLOCKED} or event.get("failure_class") else "OK"},
        "attributes": {key: value for key, value in attributes.items() if value is not None},
    }


def _usage_value(event: dict[str, Any], *keys: str) -> Any:
    usage = event.get("usage", {})
    if not isinstance(usage, dict):
        return None
    for key in keys:
        if key in usage:
            return usage[key]
    return None


def _stable_id(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()
