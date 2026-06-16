import json
from pathlib import Path

import pytest
from jsonschema import ValidationError

from strategy_codebot.knowledge import audit_run, create_proposal, create_snapshot, diff_snapshots
from strategy_codebot.schemas import validate_payload, write_json


def test_source_snapshot_schema_validates_offline_snapshot() -> None:
    snapshot = create_snapshot(Path("configs/source-registry.yaml"), offline=True, snapshot_id="test-snapshot")

    validate_payload(snapshot, "knowledge-snapshot.schema.json")
    assert snapshot["fetch_mode"] == "offline"
    assert any(source["id"] == "internal-risk-policy" for source in snapshot["sources"])


def test_snapshot_hashing_is_deterministic_for_internal_docs_and_offline_urls() -> None:
    first = create_snapshot(Path("configs/source-registry.yaml"), offline=True, snapshot_id="first")
    second = create_snapshot(Path("configs/source-registry.yaml"), offline=True, snapshot_id="second")

    first_hashes = {source["id"]: source["content_hash"] for source in first["sources"]}
    second_hashes = {source["id"]: source["content_hash"] for source in second["sources"]}

    assert first_hashes["internal-risk-policy"] == second_hashes["internal-risk-policy"]
    assert first_hashes["tradingview-pine-welcome"] == second_hashes["tradingview-pine-welcome"]


def test_diff_reports_changed_added_removed_and_unchanged(tmp_path: Path) -> None:
    baseline = _snapshot(
        [
            _source("changed-official", content_hash="old", source_type="official"),
            _source("removed-internal", content_hash="gone", source_type="internal"),
            _source("same-internal", content_hash="same", source_type="internal"),
        ]
    )
    current = _snapshot(
        [
            _source("changed-official", content_hash="new", source_type="official"),
            _source("same-internal", content_hash="same", source_type="internal"),
            _source("added-internal", content_hash="added", source_type="internal"),
        ]
    )
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    write_json(baseline_path, baseline)
    write_json(current_path, current)

    report = diff_snapshots(baseline_path, current_path)

    assert report["status"] == "manual_required"
    assert [source["id"] for source in report["changed_sources"]] == ["changed-official"]
    assert [source["id"] for source in report["removed_sources"]] == ["removed-internal"]
    assert [source["id"] for source in report["added_sources"]] == ["added-internal"]
    assert report["unchanged_sources"] == ["same-internal"]


def test_audit_extracts_warnings_and_failed_tools(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_json(
        run_dir / "validation-report.json",
        {
            "platform": "pine_v6",
            "status": "manual_required",
            "checks": [],
            "evidence": [],
            "warnings": ["risk rule needs manual review"],
            "next_actions": [],
        },
    )
    write_json(
        run_dir / "runtime-summary.json",
        {
            "run_id": "run",
            "created_at": "2026-01-01T00:00:00+00:00",
            "policy_mode": "observe",
            "trace_ref": "runtime-trace.jsonl",
            "event_count": 1,
            "completed_tools": [],
            "failed_tools": ["validate_pine_static"],
            "blocked_tools": ["broker_write"],
            "output_refs": [],
        },
    )
    (run_dir / "runtime-trace.jsonl").write_text(
        json.dumps(
            {
                "sequence": 1,
                "created_at": "2026-01-01T00:00:00+00:00",
                "run_id": "run",
                "event_type": "tool.failed",
                "tool_id": "validate_pine_static",
                "policy_mode": "observe",
                "risk_tier": "validation",
                "input_refs": [],
                "output_refs": [],
                "error": {"type": "RuntimeError", "message": "boom"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = audit_run(run_dir)

    assert report["status"] == "fail"
    assert "validate_pine_static" in report["failed_tools"]
    assert "broker_write" in report["blocked_tools"]
    assert any("risk rule" in warning for warning in report["warnings"])
    assert [finding["message"] for finding in report["findings"]].count("Tool failed: validate_pine_static") == 1


def test_audit_reports_malformed_trace_without_crashing(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "runtime-trace.jsonl").write_text('{"event_type": "tool.started"}\n{broken\n', encoding="utf-8")

    report = audit_run(run_dir)

    assert report["status"] == "manual_required"
    assert any("malformed JSONL" in warning for warning in report["warnings"])


def test_proposal_combines_diff_and_audit_without_editing_docs(tmp_path: Path) -> None:
    canonical_doc = Path("docs/trading/risk-policy.md")
    before = canonical_doc.read_text(encoding="utf-8")
    diff_report = {
        "baseline_ref": "baseline.json",
        "current_ref": "current.json",
        "created_at": "2026-01-01T00:00:00+00:00",
        "status": "manual_required",
        "summary": "1 changed source entries; 0 unchanged.",
        "changed_sources": [
            {
                "id": "tradingview-pine-welcome",
                "platform": "pine_v6",
                "type": "official",
                "trust_level": "high",
                "change_type": "changed",
                "manual_required": True,
                "baseline_hash": "old",
                "current_hash": "new",
            }
        ],
        "added_sources": [],
        "removed_sources": [],
        "unchanged_sources": [],
        "warnings": ["Official source changes require human review before canonical docs are updated."],
        "next_actions": ["Review source changes."],
    }
    audit_report = {
        "run_id": "run",
        "created_at": "2026-01-01T00:00:00+00:00",
        "status": "manual_required",
        "findings": [{"source": "validation", "severity": "warning", "message": "risk warning", "evidence_ref": "run/validation-report.json"}],
        "warnings": ["risk warning"],
        "repeated_warnings": [],
        "failed_tools": [],
        "blocked_tools": [],
        "evidence_refs": ["run/validation-report.json"],
        "next_actions": [],
    }
    diff_path = tmp_path / "diff.json"
    audit_path = tmp_path / "audit.json"
    write_json(diff_path, diff_report)
    write_json(audit_path, audit_report)

    proposal = create_proposal(diff_path, audit_path=audit_path, proposal_id="proposal-test")

    validate_payload(proposal, "knowledge-proposal.schema.json")
    assert proposal["status"] == "needs_review"
    assert proposal["risk_level"] == "high"
    assert "docs/trading/pine-v6-rules.md" in proposal["affected_docs"]
    assert "docs/trading/risk-policy.md" in proposal["affected_docs"]
    assert canonical_doc.read_text(encoding="utf-8") == before


def test_proposal_rejects_ambiguous_audit_inputs(tmp_path: Path) -> None:
    diff_path = tmp_path / "diff.json"
    audit_path = tmp_path / "audit.json"
    runs_path = tmp_path / "runs"
    runs_path.mkdir()
    write_json(diff_path, _empty_diff())
    write_json(audit_path, {"findings": [], "evidence_refs": []})

    with pytest.raises(ValueError, match="mutually exclusive"):
        create_proposal(diff_path, audit_path=audit_path, runs_path=runs_path)


def test_malformed_snapshot_rejects_schema() -> None:
    with pytest.raises(ValidationError):
        validate_payload({"snapshot_id": "missing-required-fields"}, "knowledge-snapshot.schema.json")


def _snapshot(sources: list[dict[str, object]]) -> dict[str, object]:
    return {
        "snapshot_id": "snapshot",
        "created_at": "2026-01-01T00:00:00+00:00",
        "fetch_mode": "offline",
        "registry_ref": "registry.yaml",
        "sources": sources,
    }


def _empty_diff() -> dict[str, object]:
    return {
        "baseline_ref": "baseline.json",
        "current_ref": "current.json",
        "created_at": "2026-01-01T00:00:00+00:00",
        "status": "pass",
        "summary": "0 changed source entries; 0 unchanged.",
        "changed_sources": [],
        "added_sources": [],
        "removed_sources": [],
        "unchanged_sources": [],
        "warnings": [],
        "next_actions": [],
    }


def _source(source_id: str, *, content_hash: str, source_type: str) -> dict[str, object]:
    return {
        "id": source_id,
        "platform": "pine_v6",
        "type": source_type,
        "trust_level": "high",
        "locator": f"docs/{source_id}.md",
        "freshness_ttl_days": 30,
        "content_hash": content_hash,
        "checked_at": "2026-01-01T00:00:00+00:00",
        "fetch_mode": "offline",
    }
