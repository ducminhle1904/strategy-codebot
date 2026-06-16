from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from collections.abc import Mapping
from json import JSONDecodeError
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen
from uuid import uuid4

import yaml

from strategy_codebot.paths import repo_root
from strategy_codebot.reporting import aggregate_status, validation_check
from strategy_codebot.review import REVIEW_RUNTIME_SUMMARY_PATH, REVIEW_RUNTIME_TRACE_PATH
from strategy_codebot.schemas import load_json, validate_payload
from strategy_codebot.tool_runtime import RUNTIME_SUMMARY_PATH, RUNTIME_TRACE_PATH


REQUIRED_KEYS = {"id", "platform", "type", "trust_level", "freshness_ttl_days"}
OFFICIAL_TYPES = {"official"}
FETCH_TIMEOUT_SECONDS = 20
HASH_CHUNK_SIZE = 1024 * 1024
SUMMARY_ARTIFACTS = (RUNTIME_SUMMARY_PATH, REVIEW_RUNTIME_SUMMARY_PATH)
TRACE_ARTIFACTS = (RUNTIME_TRACE_PATH, REVIEW_RUNTIME_TRACE_PATH)
DOC_RULES = (
    (("pine", "pine_v6"), "docs/trading/pine-v6-rules.md"),
    (("mql5", "mt5"), "docs/trading/mql5-rules.md"),
    (("risk", "profit", "live trading"), "docs/trading/risk-policy.md"),
    (("overfit", "curve"), "docs/trading/anti-overfit-checklist.md"),
)


def check_registry(registry_path: Path, offline: bool = True) -> dict[str, Any]:
    sources = _load_sources(registry_path)
    checks: list[dict[str, str]] = []
    warnings: list[str] = []

    if not sources:
        checks.append(validation_check("sources_present", False, "Registry must contain a non-empty sources list."))
    else:
        checks.append(validation_check("sources_present", True, f"Found {len(sources)} sources."))

    seen_ids: set[str] = set()
    for index, source in enumerate(sources):
        if not isinstance(source, Mapping):
            checks.append(
                {
                    "name": f"source_{index}:mapping",
                    "status": "fail",
                    "details": "Each source entry must be a mapping.",
                }
            )
            continue
        source_id = str(source.get("id", "<missing>"))
        missing = sorted(key for key in REQUIRED_KEYS if key not in source)
        checks.append(validation_check(f"{source_id}:required_metadata", not missing, f"Missing keys: {', '.join(missing)}" if missing else "Required metadata present."))

        duplicate = source_id in seen_ids
        checks.append(validation_check(f"{source_id}:unique_id", not duplicate, "Duplicate source id." if duplicate else "Source id is unique."))
        seen_ids.add(source_id)

        has_url = "url" in source
        has_path = "path" in source
        checks.append(validation_check(f"{source_id}:locator", has_url ^ has_path, "Exactly one of url or path is required."))

        if has_url:
            parsed = urlparse(str(source["url"]))
            checks.append(validation_check(f"{source_id}:url", parsed.scheme in {"http", "https"} and bool(parsed.netloc), "External URL must be absolute HTTP(S)."))
            if offline:
                warnings.append(f"{source_id}: external URL shape checked only; network fetch skipped.")

        if has_path:
            local = repo_root() / str(source["path"])
            checks.append(validation_check(f"{source_id}:path", local.exists(), f"Internal path must exist: {source['path']}"))

    status = "fail" if any(check["status"] == "fail" for check in checks) else "pass"
    return {
        "platform": "both",
        "status": status,
        "checks": checks,
        "evidence": [str(registry_path)],
        "warnings": warnings,
        "next_actions": [] if status == "pass" else ["Fix source registry metadata before ingestion."],
    }


def create_snapshot(registry_path: Path, *, offline: bool = True, snapshot_id: str | None = None) -> dict[str, Any]:
    sources = _load_sources(registry_path)
    fetch_mode = "offline" if offline else "fetch"
    now = _now()
    snapshot_sources: list[dict[str, Any]] = []

    for source in sources:
        if not isinstance(source, Mapping):
            continue
        snapshot_sources.append(
            {
                "id": str(source.get("id", "unknown-source")),
                "platform": str(source.get("platform", "unknown")),
                "type": str(source.get("type", "unknown")),
                "trust_level": str(source.get("trust_level", "unknown")),
                "locator": _source_locator(source),
                "freshness_ttl_days": _freshness_ttl_days(source),
                "content_hash": _source_hash(source, offline=offline),
                "checked_at": now,
                "fetch_mode": fetch_mode,
            }
        )

    snapshot = {
        "snapshot_id": snapshot_id or f"snapshot-{uuid4().hex[:8]}",
        "created_at": now,
        "fetch_mode": fetch_mode,
        "registry_ref": str(registry_path),
        "sources": snapshot_sources,
    }
    validate_payload(snapshot, "knowledge-snapshot.schema.json")
    return snapshot


def diff_snapshots(baseline_path: Path, current_path: Path) -> dict[str, Any]:
    baseline = load_json(baseline_path)
    current = load_json(current_path)
    validate_payload(baseline, "knowledge-snapshot.schema.json")
    validate_payload(current, "knowledge-snapshot.schema.json")

    baseline_sources = {source["id"]: source for source in baseline["sources"]}
    current_sources = {source["id"]: source for source in current["sources"]}
    changed_sources: list[dict[str, Any]] = []
    added_sources: list[dict[str, Any]] = []
    removed_sources: list[dict[str, Any]] = []
    unchanged_sources: list[str] = []

    for source_id in sorted(baseline_sources):
        if source_id not in current_sources:
            removed_sources.append(_source_change(baseline_sources[source_id], "removed", baseline=baseline_sources[source_id]))
            continue
        baseline_source = baseline_sources[source_id]
        current_source = current_sources[source_id]
        if _comparable_source_state(baseline_source) == _comparable_source_state(current_source):
            unchanged_sources.append(source_id)
        else:
            changed_sources.append(_source_change(current_source, "changed", baseline=baseline_source, current=current_source))

    for source_id in sorted(set(current_sources) - set(baseline_sources)):
        added_sources.append(_source_change(current_sources[source_id], "added", current=current_sources[source_id]))

    changed_total = len(changed_sources) + len(added_sources) + len(removed_sources)
    needs_manual = any(change["manual_required"] for change in [*changed_sources, *added_sources, *removed_sources])
    report = {
        "baseline_ref": str(baseline_path),
        "current_ref": str(current_path),
        "created_at": _now(),
        "status": "manual_required" if needs_manual else "pass",
        "summary": f"{changed_total} changed source entries; {len(unchanged_sources)} unchanged.",
        "changed_sources": changed_sources,
        "added_sources": added_sources,
        "removed_sources": removed_sources,
        "unchanged_sources": unchanged_sources,
        "warnings": ["Official source changes require human review before canonical docs are updated."] if needs_manual else [],
        "next_actions": ["Review source changes, verify official docs manually, then decide whether to promote updates into docs/trading/."] if changed_total else [],
    }
    validate_payload(report, "knowledge-diff.schema.json")
    return report


def audit_run(run_dir: Path) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    warnings: list[str] = []
    evidence_refs: list[str] = []
    failed_tools: list[str] = []
    blocked_tools: list[str] = []
    finding_keys: set[tuple[str, str, str]] = set()

    validation_path = run_dir / "validation-report.json"
    if validation_path.exists():
        validation = load_json(validation_path)
        evidence_refs.append(str(validation_path))
        for warning in validation.get("warnings", []):
            warnings.append(str(warning))
            _add_finding(findings, finding_keys, "validation", "warning", str(warning), str(validation_path))
        if validation.get("status") not in {"pass", "skipped"}:
            _add_finding(findings, finding_keys, "validation", "failure", f"Validation status is {validation.get('status')}.", str(validation_path))

    review_path = run_dir / "review-report.json"
    if review_path.exists():
        review = load_json(review_path)
        evidence_refs.append(str(review_path))
        for warning in review.get("warnings", []):
            warnings.append(str(warning))
            _add_finding(findings, finding_keys, "review", "warning", str(warning), str(review_path))
        for finding in review.get("findings", []):
            message = str(finding.get("summary") or finding.get("message") or finding)
            severity = str(finding.get("severity", "warning")) if isinstance(finding, Mapping) else "warning"
            _add_finding(findings, finding_keys, "review", severity, message, str(review_path))
        if review.get("run_status") in {"partial", "failed"}:
            _add_finding(findings, finding_keys, "review", "failure", f"Review status is {review.get('run_status')}.", str(review_path))

    for summary_name in SUMMARY_ARTIFACTS:
        summary_path = run_dir / summary_name
        if summary_path.exists():
            summary = load_json(summary_path)
            evidence_refs.append(str(summary_path))
            for tool_id in summary.get("failed_tools", []):
                failed_tools.append(str(tool_id))
                _add_finding(findings, finding_keys, "runtime", "failure", f"Tool failed: {tool_id}", str(summary_path))
            for tool_id in summary.get("blocked_tools", []):
                blocked_tools.append(str(tool_id))
                _add_finding(findings, finding_keys, "runtime", "blocked", f"Tool blocked: {tool_id}", str(summary_path))

    for trace_name in TRACE_ARTIFACTS:
        trace_path = run_dir / trace_name
        if trace_path.exists():
            evidence_refs.append(str(trace_path))
            for event, error in _read_trace_events(trace_path):
                if error:
                    warnings.append(error)
                    _add_finding(findings, finding_keys, "runtime-trace", "warning", error, str(trace_path))
                    continue
                if event.get("event_type") == "tool.failed":
                    tool_id = str(event.get("tool_id", "unknown-tool"))
                    failed_tools.append(tool_id)
                    _add_finding(findings, finding_keys, "runtime", "failure", f"Tool failed: {tool_id}", str(trace_path))
                if event.get("event_type") == "tool.blocked":
                    tool_id = str(event.get("tool_id", "unknown-tool"))
                    blocked_tools.append(tool_id)
                    _add_finding(findings, finding_keys, "runtime", "blocked", f"Tool blocked: {tool_id}", str(trace_path))

    warning_counts: dict[str, int] = {}
    for warning in warnings:
        warning_counts[warning] = warning_counts.get(warning, 0) + 1
    repeated_warnings = [{"message": message, "count": count} for message, count in sorted(warning_counts.items()) if count > 1]
    statuses = {"pass"}
    if findings or warnings:
        statuses.add("manual_required")
    if failed_tools or blocked_tools:
        statuses.add("fail")
    status = aggregate_status(statuses)

    return {
        "run_id": run_dir.name,
        "created_at": _now(),
        "status": status,
        "findings": findings,
        "warnings": warnings,
        "repeated_warnings": repeated_warnings,
        "failed_tools": sorted(set(failed_tools)),
        "blocked_tools": sorted(set(blocked_tools)),
        "evidence_refs": sorted(set(evidence_refs)),
        "next_actions": _audit_next_actions(status, findings),
    }


def create_proposal(diff_path: Path, *, audit_path: Path | None = None, runs_path: Path | None = None, proposal_id: str | None = None) -> dict[str, Any]:
    if audit_path and runs_path:
        raise ValueError("--audit and --runs are mutually exclusive")
    diff = load_json(diff_path)
    validate_payload(diff, "knowledge-diff.schema.json")
    audit = load_json(audit_path) if audit_path else audit_run(runs_path) if runs_path else None

    source_changes = [*diff["changed_sources"], *diff["added_sources"], *diff["removed_sources"]]
    affected_sources = sorted({change["id"] for change in source_changes})
    affected_docs = sorted({doc for change in source_changes for doc in _affected_docs(change)})
    evidence_refs = [str(diff_path)]
    if audit_path:
        evidence_refs.append(str(audit_path))
    if runs_path:
        evidence_refs.append(str(runs_path))
    if audit:
        evidence_refs.extend(str(ref) for ref in audit.get("evidence_refs", []))
        affected_docs.extend(_docs_from_audit(audit))

    status = "needs_review" if affected_sources or (audit and audit.get("findings")) else "draft"
    risk_level = _proposal_risk_level(diff, audit)
    proposal = {
        "proposal_id": proposal_id or f"proposal-{uuid4().hex[:8]}",
        "created_at": _now(),
        "status": status,
        "risk_level": risk_level,
        "affected_sources": affected_sources,
        "affected_docs": sorted(set(affected_docs)),
        "evidence_refs": sorted(set(evidence_refs)),
        "recommendations": _proposal_recommendations(diff, audit),
        "next_actions": _proposal_next_actions(status, risk_level),
    }
    validate_payload(proposal, "knowledge-proposal.schema.json")
    return proposal


def _load_sources(registry_path: Path) -> list[Any]:
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    return payload.get("sources", []) if isinstance(payload, dict) else []


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _source_locator(source: Mapping[str, Any]) -> str:
    if "url" in source:
        return str(source["url"])
    return str(source.get("path", ""))


def _source_hash(source: Mapping[str, Any], *, offline: bool) -> str:
    if "path" in source:
        return _hash_local_file(source)
    elif offline:
        content = json.dumps(
            {
                "id": source.get("id"),
                "url": source.get("url"),
                "platform": source.get("platform"),
                "type": source.get("type"),
                "trust_level": source.get("trust_level"),
                "freshness_ttl_days": source.get("freshness_ttl_days"),
            },
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(content).hexdigest()
    else:
        digest = hashlib.sha256()
        with urlopen(str(source["url"]), timeout=FETCH_TIMEOUT_SECONDS) as response:
            for chunk in iter(lambda: response.read(HASH_CHUNK_SIZE), b""):
                digest.update(chunk)
        return digest.hexdigest()


def _freshness_ttl_days(source: Mapping[str, Any]) -> int:
    source_id = str(source.get("id", "unknown-source"))
    try:
        return int(source.get("freshness_ttl_days", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source_id}: freshness_ttl_days must be an integer") from exc


def _hash_local_file(source: Mapping[str, Any]) -> str:
    source_id = str(source.get("id", "unknown-source"))
    root = repo_root().resolve()
    local = (root / str(source["path"])).resolve()
    if not local.is_relative_to(root):
        raise ValueError(f"{source_id}: source path must stay inside repository root")
    if not local.is_file():
        raise ValueError(f"{source_id}: source path must be a regular file")
    digest = hashlib.sha256()
    with local.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _comparable_source_state(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "platform": source.get("platform"),
        "type": source.get("type"),
        "trust_level": source.get("trust_level"),
        "locator": source.get("locator"),
        "freshness_ttl_days": source.get("freshness_ttl_days"),
        "content_hash": source.get("content_hash"),
    }


def _source_change(
    source: Mapping[str, Any],
    change_type: str,
    *,
    baseline: Mapping[str, Any] | None = None,
    current: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    change = {
        "id": str(source["id"]),
        "platform": str(source.get("platform", "")),
        "type": str(source.get("type", "")),
        "trust_level": str(source.get("trust_level", "")),
        "change_type": change_type,
        "manual_required": str(source.get("type")) in OFFICIAL_TYPES,
    }
    if baseline:
        change["baseline_hash"] = str(baseline.get("content_hash", ""))
    if current:
        change["current_hash"] = str(current.get("content_hash", ""))
    return change


def _finding(source: str, severity: str, message: str, evidence_ref: str) -> dict[str, str]:
    return {"source": source, "severity": severity, "message": message, "evidence_ref": evidence_ref}


def _add_finding(
    findings: list[dict[str, Any]],
    finding_keys: set[tuple[str, str, str]],
    source: str,
    severity: str,
    message: str,
    evidence_ref: str,
) -> None:
    key = (source, severity, message)
    if key not in finding_keys:
        findings.append(_finding(source, severity, message, evidence_ref))
        finding_keys.add(key)


def _read_trace_events(trace_path: Path) -> Iterator[tuple[dict[str, Any], str | None]]:
    with trace_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line), None
            except JSONDecodeError:
                yield {}, f"{trace_path}: malformed JSONL at line {line_number}"


def _audit_next_actions(status: str, findings: list[dict[str, Any]]) -> list[str]:
    if status == "pass":
        return []
    if any(finding["severity"] in {"failure", "blocked"} for finding in findings):
        return ["Review failed or blocked runtime evidence before promoting knowledge updates."]
    return ["Review warnings for repeated knowledge gaps and decide whether a proposal should update docs."]


def _affected_docs(change: Mapping[str, Any]) -> list[str]:
    platform = str(change.get("platform", ""))
    source_id = str(change.get("id", ""))
    return _docs_for_text(f"{platform} {source_id}")


def _docs_from_audit(audit: Mapping[str, Any]) -> list[str]:
    docs: set[str] = set()
    for finding in audit.get("findings", []):
        message = str(finding.get("message", "")).lower() if isinstance(finding, Mapping) else str(finding).lower()
        docs.update(_docs_for_text(message))
    return sorted(docs)


def _docs_for_text(text: str) -> list[str]:
    lowered = text.lower()
    return [doc for keywords, doc in DOC_RULES if any(keyword in lowered for keyword in keywords)]


def _proposal_recommendations(diff: Mapping[str, Any], audit: Mapping[str, Any] | None) -> list[str]:
    recommendations = ["Do not mutate canonical docs automatically; review this proposal first."]
    if diff.get("status") == "manual_required":
        recommendations.append("Manually inspect changed official sources before updating docs/trading/.")
    if audit and audit.get("findings"):
        recommendations.append("Use validation, review, and runtime evidence to decide whether rules or checklists need updates.")
    if len(recommendations) == 1:
        recommendations.append("No source or run evidence requires a docs update yet.")
    return recommendations


def _proposal_risk_level(diff: Mapping[str, Any], audit: Mapping[str, Any] | None) -> str:
    if audit and (audit.get("failed_tools") or audit.get("blocked_tools")):
        return "high"
    if audit:
        text = json.dumps(audit).lower()
        if any(marker in text for marker in ("profit", "live trading", "broker", "risk")):
            return "high"
    if diff.get("status") == "manual_required":
        return "medium"
    if diff.get("changed_sources") or diff.get("added_sources") or diff.get("removed_sources"):
        return "medium"
    return "low"


def _proposal_next_actions(status: str, risk_level: str) -> list[str]:
    if status == "draft":
        return ["Keep proposal as evidence until a human reviewer accepts a concrete docs change."]
    actions = ["Human reviewer must approve any canonical docs update."]
    if risk_level == "high":
        actions.append("Resolve high-risk findings before using the proposal as product guidance.")
    return actions
