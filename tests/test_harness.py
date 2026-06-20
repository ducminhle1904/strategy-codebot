import sqlite3
import subprocess
import json
from pathlib import Path

from strategy_codebot.harness import (
    assess_development,
    audit_traces,
    build_trace_command,
    classify_trace_intake,
    gate_development,
    harness_cli_availability,
    harness_outcome,
    memory_candidates,
    preflight_context,
    record_intake,
    record_trace,
    recommend_next,
    should_record_harness,
    summarize_traces,
)


def _create_trace_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "harness.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("create table intake (id integer primary key, input_type text, risk_lane text)")
        connection.execute(
            """
            create table trace (
                id integer primary key,
                created_at text,
                task_summary text not null,
                intake_id integer,
                story_id text,
                agent text,
                actions_taken text,
                files_read text,
                files_changed text,
                decisions_made text,
                errors text,
                outcome text,
                duration_seconds integer,
                token_estimate integer,
                harness_friction text,
                notes text
            )
            """
        )
    return db_path


def _insert_intake(db_path: Path, intake_id: int = 1, input_type: str = "maintenance", risk_lane: str = "normal") -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "insert into intake (id, input_type, risk_lane) values (?, ?, ?)",
            (intake_id, input_type, risk_lane),
        )


def _insert_trace(db_path: Path, **overrides) -> None:
    row = {
        "id": 1,
        "created_at": "2026-06-17 00:00:00",
        "task_summary": "Trace test",
        "intake_id": 1,
        "story_id": None,
        "agent": "codex",
        "actions_taken": json.dumps(["implemented"]),
        "files_read": json.dumps(["src/app.py"]),
        "files_changed": json.dumps(["src/app.py"]),
        "decisions_made": json.dumps(["ship gate"]),
        "errors": json.dumps([]),
        "outcome": "completed",
        "duration_seconds": 0,
        "token_estimate": 0,
        "harness_friction": "none",
        "notes": "unit test",
    }
    row.update(overrides)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            insert into trace (
                id, created_at, task_summary, intake_id, story_id, agent, actions_taken,
                files_read, files_changed, decisions_made, errors, outcome,
                duration_seconds, token_estimate, harness_friction, notes
            ) values (
                :id, :created_at, :task_summary, :intake_id, :story_id, :agent,
                :actions_taken, :files_read, :files_changed, :decisions_made,
                :errors, :outcome, :duration_seconds, :token_estimate,
                :harness_friction, :notes
            )
            """,
            row,
        )


def test_harness_trace_command_shape() -> None:
    command = build_trace_command(
        summary="Test run",
        intake=42,
        story="US-001",
        agent="pine_specialist",
        outcome="pass",
        changed=["runs/test/validation-report.json"],
        actions=["load_strategy_spec:pass"],
        read=["examples/specs/ma-crossover-pine.json"],
        errors="[]",
        friction="none",
        duration=0,
        tokens=0,
        decisions=["mode=dry-run"],
        notes="unit test",
    )

    assert command[1:4] == ["trace", "--summary", "Test run"]
    assert "--intake" in command
    assert command[command.index("--intake") + 1] == "42"
    assert "--story" in command
    assert "US-001" in command
    assert "--changed" in command
    assert command[command.index("--actions") + 1] == "load_strategy_spec:pass"
    assert command[command.index("--read") + 1] == "examples/specs/ma-crossover-pine.json"
    assert command[command.index("--errors") + 1] == "[]"
    assert command[command.index("--friction") + 1] == "none"
    assert command[command.index("--duration") + 1] == "0"
    assert command[command.index("--tokens") + 1] == "0"
    assert command[command.index("--decisions") + 1] == "mode=dry-run"


def test_explicit_no_record_harness_wins() -> None:
    assert should_record_harness(False) is False


def test_implicit_record_harness_requires_executable_cli(monkeypatch, tmp_path: Path) -> None:
    fake_cli = tmp_path / "harness-cli"
    fake_cli.write_bytes(b"\xcf\xfa\xed\xfe")
    fake_cli.chmod(0o755)
    monkeypatch.setattr("strategy_codebot.harness.harness_cli_path", lambda: fake_cli)

    availability = harness_cli_availability()

    assert availability["available"] is False
    assert availability["status"] in {"not_executable", "unusable"}
    assert should_record_harness(None) is False


def test_harness_outcome_maps_validation_status() -> None:
    assert harness_outcome("pass") == "completed"
    assert harness_outcome("fail") == "failed"
    assert harness_outcome("manual_required") == "partial"


def test_trace_intake_classifier_marks_harness_work_high_risk() -> None:
    classification = classify_trace_intake(
        summary="Update trace audit gate",
        read=["src/strategy_codebot/harness.py"],
        changed=["src/strategy_codebot/harness.py", "schemas/tool-event.schema.json"],
    )

    assert classification["input_type"] == "harness improvement"
    assert classification["lane"] == "high-risk"


def test_trace_intake_classifier_marks_small_docs_only_work_tiny() -> None:
    classification = classify_trace_intake(
        summary="Document trace policy",
        read=["docs/HARNESS.md"],
        changed=["docs/HARNESS.md"],
    )

    assert classification["input_type"] == "harness improvement"
    assert classification["lane"] == "tiny"


def test_audit_traces_passes_for_linked_detailed_trace(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    _insert_intake(db_path)
    _insert_trace(db_path)

    report = audit_traces(db_path=db_path)

    assert report["status"] == "pass"
    assert report["checked"] == 1
    assert report["failed"] == 0


def test_audit_traces_fails_for_missing_intake(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    _insert_trace(db_path, intake_id=None)

    report = audit_traces(db_path=db_path)

    assert report["status"] == "fail"
    assert report["failures"][0]["issues"] == ["intake_id: null"]


def test_audit_traces_fails_for_null_errors(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    _insert_intake(db_path)
    _insert_trace(db_path, errors=None)

    report = audit_traces(db_path=db_path)

    assert report["status"] == "fail"
    assert "errors: null" in report["failures"][0]["issues"]


def test_audit_traces_fails_for_invalid_json_array(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    _insert_intake(db_path)
    _insert_trace(db_path, files_read="not-json")

    report = audit_traces(db_path=db_path)

    assert report["status"] == "fail"
    assert "files_read: invalid_json_array" in report["failures"][0]["issues"]


def test_audit_traces_fails_for_empty_required_arrays(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    _insert_intake(db_path)
    _insert_trace(db_path, actions_taken=json.dumps([]), files_read=json.dumps([]), decisions_made=json.dumps([]))

    report = audit_traces(db_path=db_path)

    assert report["status"] == "fail"
    assert "actions_taken: empty" in report["failures"][0]["issues"]
    assert "files_read: empty" in report["failures"][0]["issues"]
    assert "decisions_made: empty" in report["failures"][0]["issues"]


def test_audit_traces_warns_for_zero_duration_without_note(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    _insert_intake(db_path)
    _insert_trace(db_path, duration_seconds=0, notes="unit test")

    report = audit_traces(db_path=db_path)

    assert report["status"] == "pass"
    assert report["warned"] == 1
    assert report["warnings"][0]["warnings"] == ["duration_seconds: zero_without_unavailable_note"]


def test_audit_traces_warns_for_high_risk_missing_verification_evidence(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    _insert_intake(db_path, risk_lane="high_risk")
    _insert_trace(db_path, duration_seconds=1, notes="unit test")

    report = audit_traces(db_path=db_path)

    assert report["status"] == "pass"
    assert report["warned"] == 1
    assert "high-risk trace missing verification evidence" in report["warnings"][0]["warnings"]


def test_assess_development_reads_artifact_evidence(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    (out_dir / "validation-report.json").write_text(
        json.dumps({"platform": "pine_v6", "status": "pass", "checks": [], "evidence": [], "warnings": [], "next_actions": []}),
        encoding="utf-8",
    )
    (out_dir / "review-report.json").write_text(
        json.dumps(
            {
                "run_id": "run",
                "created_at": "2026-06-17T00:00:00+00:00",
                "run_status": "completed",
                "decision": "approve",
                "reviewers": [],
                "findings": [],
                "conflicts": [],
                "warnings": [],
                "next_actions": [],
            }
        ),
        encoding="utf-8",
    )
    (out_dir / "runtime-trace.jsonl").write_text(json.dumps({"event_type": "tool.completed", "status": "pass"}) + "\n", encoding="utf-8")
    (out_dir / "live-workflow-trace.json").write_text(
        json.dumps({"final_decision": {"status": "pass", "production_gate": {"status": "pass", "required_fixes": []}}}),
        encoding="utf-8",
    )
    _insert_intake(db_path)
    _insert_trace(db_path, files_changed=json.dumps([str(out_dir / "validation-report.json")]), duration_seconds=1)

    report = assess_development(db_path=db_path)

    assert report["status"] == "pass"
    assert report["engineering_quality"]["validation"]["pass"] == 1
    assert report["engineering_quality"]["review"]["pass"] == 1
    assert report["business_correctness"]["pass"] == 1
    assert report["production_impact"]["pass"] == 1
    assert report["human_feedback"]["status"] == "unknown"


def test_assess_development_reports_unknown_when_artifacts_missing(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    _insert_intake(db_path)
    _insert_trace(db_path, duration_seconds=1)

    report = assess_development(db_path=db_path)

    assert report["status"] == "warn"
    assert report["engineering_quality"]["validation"]["unknown"] == 1
    assert report["engineering_quality"]["missing_verification_evidence"] == 1
    assert report["human_feedback"]["status"] == "unknown"


def test_assess_development_reports_production_fail_recommendation(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    (out_dir / "live-workflow-trace.json").write_text(
        json.dumps({"final_decision": {"status": "fail", "production_gate": {"status": "fail", "required_fixes": ["add exit"]}}}),
        encoding="utf-8",
    )
    _insert_intake(db_path)
    _insert_trace(db_path, files_changed=json.dumps([str(out_dir / "live-workflow-trace.json")]), duration_seconds=1)

    report = assess_development(db_path=db_path)

    assert report["status"] == "fail"
    assert report["production_impact"]["fail"] == 1
    assert report["production_impact"]["required_fixes"] == ["add exit"]
    assert any("production gate" in item.lower() for item in report["recommendations"])


def test_preflight_context_is_bounded_and_omits_raw_trace_payloads(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    _insert_intake(db_path, risk_lane="high_risk")
    _insert_trace(db_path, duration_seconds=1, notes="unit test")

    report = preflight_context(latest=1, db_path=db_path)

    assert report["status"] == "warn"
    assert 1 <= len(report["context_brief"]) <= 5
    assert sum(len(item) for item in report["context_brief"]) <= 1500
    assert report["anti_pollution"]["raw_trace_rows_included"] is False
    assert report["anti_pollution"]["memory_written"] is False
    assert "actions_taken" not in json.dumps(report["context_brief"])


def test_gate_development_observe_warns_without_failing_status(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    _insert_intake(db_path, risk_lane="high_risk")
    _insert_trace(db_path, duration_seconds=0, notes="duration unavailable")

    report = gate_development(latest=1, policy="observe", db_path=db_path)

    assert report["status"] == "warn"
    assert report["policy"] == "observe"
    assert any(issue["code"] == "high_risk_missing_session_start" for issue in report["issues"])


def test_gate_development_enforce_fails_for_high_risk_missing_evidence(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    _insert_intake(db_path, risk_lane="high_risk")
    _insert_trace(db_path, duration_seconds=1, notes="unit test")

    report = gate_development(latest=1, policy="enforce", db_path=db_path)

    assert report["status"] == "fail"
    assert any(issue["code"] == "high_risk_trace_missing_verification_evidence" for issue in report["issues"])


def test_gate_development_enforce_fails_for_high_risk_skipped_review_without_justification(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    _insert_intake(db_path, risk_lane="high_risk")
    _insert_trace(
        db_path,
        duration_seconds=1,
        decisions_made=json.dumps(["preflight_applied=true", "test_outcome=pass", "validation_outcome=pass", "review_outcome=skipped"]),
        notes="preflight_applied=true; test_outcome=pass; validation_outcome=pass; review_outcome=skipped",
    )

    report = gate_development(latest=1, policy="enforce", db_path=db_path)

    assert report["status"] == "fail"
    assert any(issue["code"] == "high_risk_review_skipped" and issue["blocking"] is True for issue in report["issues"])


def test_gate_development_allows_skipped_review_with_justification_as_nonblocking(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    _insert_intake(db_path, risk_lane="high_risk")
    _insert_trace(
        db_path,
        duration_seconds=1,
        decisions_made=json.dumps(
            [
                "preflight_applied=true",
                "test_outcome=pass",
                "validation_outcome=pass",
                "review_outcome=skipped",
                "review_justification=docs-only follow-up with focused tests",
            ]
        ),
        notes="preflight_applied=true; test_outcome=pass; validation_outcome=pass; review_outcome=skipped; review_justification=docs-only follow-up with focused tests",
    )

    report = gate_development(latest=1, policy="enforce", db_path=db_path)

    assert report["status"] == "warn"
    assert any(issue["code"] == "high_risk_review_skipped" and issue["blocking"] is False for issue in report["issues"])


def test_gate_development_passes_high_risk_review_metadata_with_evidence(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    _insert_intake(db_path, risk_lane="high_risk")
    _insert_trace(
        db_path,
        duration_seconds=1,
        decisions_made=json.dumps(
            [
                "preflight_applied=true",
                "test_outcome=pass",
                "validation_outcome=pass",
                "review_outcome=pass",
                "review_evidence=focused review completed",
            ]
        ),
        notes="preflight_applied=true; test_outcome=pass; validation_outcome=pass; review_outcome=pass; review_evidence=focused review completed",
    )

    report = gate_development(latest=1, policy="enforce", db_path=db_path)

    assert report["status"] == "pass"
    assert report["issues"] == []


def test_gate_development_enforce_fails_for_high_risk_missing_preflight_marker(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    _insert_intake(db_path, risk_lane="high_risk")
    _insert_trace(
        db_path,
        duration_seconds=1,
        decisions_made=json.dumps(
            [
                "test_outcome=pass",
                "validation_outcome=pass",
                "review_outcome=pass",
                "review_evidence=focused review completed",
            ]
        ),
        notes="test_outcome=pass; validation_outcome=pass; review_outcome=pass; review_evidence=focused review completed",
    )

    report = gate_development(latest=1, policy="enforce", db_path=db_path)

    assert report["status"] == "fail"
    assert any(issue["code"] == "high_risk_missing_preflight" for issue in report["issues"])


def test_review_report_artifact_takes_priority_over_trace_metadata(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    (out_dir / "review-report.json").write_text(
        json.dumps(
            {
                "run_id": "run",
                "created_at": "2026-06-17T00:00:00+00:00",
                "run_status": "completed",
                "decision": "approve",
                "reviewers": [{"role": "critic", "provider": "mock", "model": "mock", "status": "pass", "findings": [], "evidence_refs": ["review-report.json"], "warnings": []}],
                "findings": [],
                "conflicts": [],
                "warnings": [],
                "next_actions": [],
            }
        ),
        encoding="utf-8",
    )
    _insert_intake(db_path, risk_lane="high_risk")
    _insert_trace(
        db_path,
        files_changed=json.dumps([str(out_dir / "review-report.json")]),
        decisions_made=json.dumps(["preflight_applied=true", "review_outcome=skipped"]),
        duration_seconds=1,
    )

    assessment = assess_development(latest=1, db_path=db_path)
    report = gate_development(latest=1, policy="enforce", db_path=db_path)

    assert assessment["trace_evidence"][0]["review"]["source"].endswith("review-report.json")
    assert assessment["trace_evidence"][0]["review"]["status"] == "pass"
    assert report["status"] == "pass"


def test_recommend_next_reports_missing_evidence_and_production_fail(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    (out_dir / "live-workflow-trace.json").write_text(
        json.dumps({"final_decision": {"status": "fail", "production_gate": {"status": "fail"}}}),
        encoding="utf-8",
    )
    _insert_intake(db_path, intake_id=1)
    _insert_intake(db_path, intake_id=2)
    _insert_trace(db_path, id=1, intake_id=1, files_changed=json.dumps([str(out_dir / "live-workflow-trace.json")]), duration_seconds=1)
    _insert_trace(db_path, id=2, intake_id=2, duration_seconds=1)

    report = recommend_next(latest=2, db_path=db_path)

    assert report["status"] == "warn"
    assert {item["id"] for item in report["recommendations"]} >= {
        "rec-missing-verification-evidence",
        "rec-production-gate-failure",
    }
    assert report["anti_pollution"]["memory_written"] is False


def test_recommend_next_reports_high_risk_review_evidence_gap(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    _insert_intake(db_path, risk_lane="high_risk")
    _insert_trace(
        db_path,
        duration_seconds=1,
        decisions_made=json.dumps(["preflight_applied=true", "test_outcome=pass", "validation_outcome=pass", "review_outcome=skipped"]),
        notes="preflight_applied=true; test_outcome=pass; validation_outcome=pass; review_outcome=skipped",
    )

    report = recommend_next(latest=1, db_path=db_path)

    assert any(item["id"] == "rec-high-risk-review-evidence" and item["source_trace_ids"] == [1] for item in report["recommendations"])


def test_memory_candidates_requires_repeated_warnings_without_writing_memory(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    _insert_intake(db_path, intake_id=1, risk_lane="high_risk")
    _insert_intake(db_path, intake_id=2, risk_lane="high_risk")
    _insert_trace(db_path, id=1, intake_id=1, duration_seconds=1, notes="unit test")
    _insert_trace(db_path, id=2, intake_id=2, duration_seconds=1, notes="unit test")

    report = memory_candidates(latest=2, db_path=db_path)

    assert report["status"] == "warn"
    assert report["candidate_count"] >= 1
    assert any(candidate["recurrence_count"] == 2 for candidate in report["candidates"])
    assert report["anti_pollution"]["memory_written"] is False
    assert report["anti_pollution"]["memory_path_touched"] is False


def test_summarize_traces_reports_process_aggregates(tmp_path: Path) -> None:
    db_path = _create_trace_db(tmp_path)
    _insert_intake(db_path)
    _insert_intake(db_path, intake_id=2, input_type="harness_improvement", risk_lane="high_risk")
    _insert_trace(db_path, id=1, duration_seconds=5)
    _insert_trace(
        db_path,
        id=2,
        intake_id=2,
        task_summary="Legacy trace",
        errors=json.dumps(["failed"]),
        harness_friction="tool failure",
        outcome="failed",
        duration_seconds=15,
    )

    report = summarize_traces(latest=10, db_path=db_path)

    assert report["trace_count"] == 2
    assert report["linked_count"] == 2
    assert report["unlinked_count"] == 0
    assert report["clean_error_count"] == 1
    assert report["error_trace_count"] == 1
    assert report["friction_trace_count"] == 1
    assert report["duration_seconds"] == {"total": 20, "avg": 10.0, "max": 15}
    assert report["duration_by_lane"]["normal"] == {"total": 5, "avg": 5.0, "max": 5}
    assert report["top_slow_traces"][0]["id"] == 2
    assert report["high_risk_count"] == 1
    assert report["by_lane"] == {"high_risk": 1, "normal": 1}
    assert report["top_changed_files"][0] == {"value": "src/app.py", "count": 2}
    assert report["failed_or_null_quality_rows"][0]["id"] == 2


def test_harness_trace_command_shape_for_review() -> None:
    command = build_trace_command(
        summary="Phase 2 parallel review test",
        intake=None,
        story=None,
        agent="critic",
        outcome="completed",
        changed=["runs/test/review-report.json"],
        notes="parallel-review",
    )

    assert "--agent" in command
    assert "critic" in command
    assert "runs/test/review-report.json" in ",".join(command)


def test_record_trace_initializes_and_migrates_before_trace(monkeypatch) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))

    monkeypatch.setattr("strategy_codebot.harness.subprocess.run", fake_run)

    record_trace(["/tmp/harness-cli", "trace", "--summary", "test"])

    assert [call[0] for call in calls] == [
        ["/tmp/harness-cli", "init"],
        ["/tmp/harness-cli", "migrate"],
        ["/tmp/harness-cli", "trace", "--summary", "test"],
    ]
    assert all(call[1]["check"] is True for call in calls)


def test_record_trace_normalizes_no_error_sentinel(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "harness.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("create table trace (id integer primary key, errors text)")
        connection.execute("insert into trace (id, errors) values (1, ?)", ('["[]"]',))

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("strategy_codebot.harness.repo_root", lambda: tmp_path)
    monkeypatch.setattr("strategy_codebot.harness.subprocess.run", fake_run)

    record_trace(["/tmp/harness-cli", "trace", "--summary", "test", "--errors", "[]"])

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("select errors from trace where id = 1").fetchone()[0] == "[]"


def test_record_intake_initializes_and_returns_id(monkeypatch) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        stdout = "Intake #42 recorded.\n" if command[1] == "intake" else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout)

    monkeypatch.setattr("strategy_codebot.harness.subprocess.run", fake_run)

    intake_id = record_intake(
        summary="Trace linked work",
        input_type="maintenance request",
        lane="normal",
        docs=["AGENTS.md", "docs/HARNESS.md"],
        notes="unit test",
    )

    assert intake_id == 42
    assert [call[0][1] for call in calls] == ["init", "migrate", "intake"]
    assert "--docs" in calls[-1][0]
    assert calls[-1][0][calls[-1][0].index("--docs") + 1] == "AGENTS.md,docs/HARNESS.md"
    assert calls[-1][1]["capture_output"] is True
