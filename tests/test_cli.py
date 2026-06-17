import json
from pathlib import Path

from typer.testing import CliRunner

from strategy_codebot import __version__
from strategy_codebot import cli as cli_module
from strategy_codebot import harness as harness_module
from strategy_codebot.cli import app
from strategy_codebot.schemas import load_json


runner = CliRunner()


def run_cli_dry_run(out_dir: Path, *extra_args: str):
    result = runner.invoke(
        app,
        [
            "run",
            "--spec",
            "examples/specs/ma-crossover-pine.json",
            "--mode",
            "dry-run",
            "--out",
            str(out_dir),
            *extra_args,
            "--no-record-harness",
        ],
    )
    assert result.exit_code == 0, result.output
    return result


def test_cli_version_prints_package_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == __version__


def test_cli_doctor_writes_report(tmp_path: Path) -> None:
    out_path = tmp_path / "doctor.json"
    result = runner.invoke(app, ["doctor", "--out", str(out_path)])

    assert result.exit_code == 0, result.output
    report = load_json(out_path)
    assert report["status"] == "pass"
    assert report["environment"]["package_version"] == __version__
    assert any(check["name"] == "optional_harness_cli" for check in report["checks"])
    if report["environment"]["harness_cli"]["status"] == "missing_optional":
        assert report["warnings"]


def test_cli_doctor_exits_nonzero_on_failed_report(monkeypatch, tmp_path: Path) -> None:
    out_path = tmp_path / "doctor.json"

    def failed_report():
        return {
            "status": "fail",
            "checks": [{"name": "bad", "status": "fail", "details": "broken"}],
            "warnings": [],
            "next_actions": ["Fix failed doctor checks before release."],
            "environment": {"package_version": __version__},
        }

    monkeypatch.setattr(cli_module, "doctor_report", failed_report)

    result = runner.invoke(app, ["doctor", "--out", str(out_path)])

    assert result.exit_code == 1
    assert load_json(out_path)["status"] == "fail"


def test_cli_run_dry_run_creates_artifacts(tmp_path: Path) -> None:
    out_dir = tmp_path / "cli-run"
    result = run_cli_dry_run(out_dir)

    assert "status=pass" in result.output
    assert (out_dir / "pine" / "strategy.pine").exists()
    assert (out_dir / "runtime-trace.jsonl").exists()
    assert (out_dir / "runtime-summary.json").exists()
    assert not (out_dir / "review-report.json").exists()


def test_cli_harness_inspect_writes_report(tmp_path: Path) -> None:
    out_dir = tmp_path / "cli-run"
    report_path = tmp_path / "harness-report.json"
    run_cli_dry_run(out_dir)

    result = runner.invoke(app, ["harness", "inspect", "--run-dir", str(out_dir), "--out", str(report_path)])

    assert result.exit_code == 0, result.output
    report = load_json(report_path)
    assert report["status"] == "pass"
    assert report["run_id"] == "cli-run"


def test_cli_harness_confirm_reports_missing_repository_harness(monkeypatch, tmp_path: Path) -> None:
    missing_cli = tmp_path / "missing-harness-cli"
    monkeypatch.setattr(cli_module, "harness_cli_path", lambda: missing_cli)

    result = runner.invoke(app, ["harness", "confirm", "--out", str(tmp_path / "confirm")])

    assert result.exit_code == 1
    assert "reason=missing_harness_cli" in result.output


def test_cli_harness_confirm_records_repository_harness_trace(monkeypatch, tmp_path: Path) -> None:
    harness_log = tmp_path / "harness-call.txt"
    harness_cli = tmp_path / "harness-cli"
    harness_cli.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "from pathlib import Path",
                "import sys",
                "if sys.argv[1] == 'intake':",
                "    print('Intake #7 recorded.')",
                "else:",
                f"    Path({str(harness_log)!r}).write_text(' '.join(sys.argv[1:]), encoding='utf-8')",
            ]
        ),
        encoding="utf-8",
    )
    harness_cli.chmod(0o755)
    monkeypatch.setattr(cli_module, "harness_cli_path", lambda: harness_cli)
    monkeypatch.setattr(harness_module, "harness_cli_path", lambda: harness_cli)

    out_dir = tmp_path / "confirm"
    result = runner.invoke(app, ["harness", "confirm", "--out", str(out_dir)])

    assert result.exit_code == 0, result.output
    assert "status=pass" in result.output
    harness_command = harness_log.read_text(encoding="utf-8")
    assert harness_command.startswith("trace --summary")
    assert "--intake 7" in harness_command
    assert "--actions" in harness_command
    assert "--read" in harness_command
    assert "--changed" in harness_command
    assert "--errors" in harness_command
    assert "--decisions" in harness_command
    assert "--friction" in harness_command
    assert "--duration" in harness_command
    assert "--tokens" in harness_command
    events = [json.loads(line) for line in (out_dir / "runtime-trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(event.get("tool_id") == "record_harness_trace" and event.get("status") == "pass" for event in events)


def test_cli_harness_audit_traces_writes_report_and_fails(monkeypatch, tmp_path: Path) -> None:
    report_path = tmp_path / "trace-audit.json"

    def fake_audit_traces(**kwargs):
        return {
            "status": "fail",
            "checked": 1,
            "failed": 1,
            "scope": {"type": "latest", "latest": kwargs["latest"]},
            "failures": [{"id": 1, "task_summary": "bad", "issues": ["intake_id: null"]}],
        }

    monkeypatch.setattr(cli_module, "audit_traces", fake_audit_traces)

    result = runner.invoke(app, ["harness", "audit-traces", "--latest", "1", "--out", str(report_path)])

    assert result.exit_code == 1
    assert "status=fail checked=1 failed=1" in result.output
    assert load_json(report_path)["failures"][0]["issues"] == ["intake_id: null"]


def test_cli_harness_audit_traces_passes(monkeypatch, tmp_path: Path) -> None:
    report_path = tmp_path / "trace-audit.json"

    def fake_audit_traces(**kwargs):
        return {"status": "pass", "checked": 2, "failed": 0, "scope": {"type": "latest"}, "failures": []}

    monkeypatch.setattr(cli_module, "audit_traces", fake_audit_traces)

    result = runner.invoke(app, ["harness", "audit-traces", "--latest", "2", "--out", str(report_path)])

    assert result.exit_code == 0, result.output
    assert "status=pass checked=2 failed=0" in result.output
    assert load_json(report_path)["checked"] == 2


def test_cli_harness_summarize_traces_writes_report(monkeypatch, tmp_path: Path) -> None:
    report_path = tmp_path / "trace-summary.json"

    def fake_summarize_traces(**kwargs):
        assert kwargs["artifacts_root"] == tmp_path
        assert kwargs["include_development_evidence"] is True
        return {
            "status": "pass",
            "trace_count": kwargs["latest"],
            "linked_count": 3,
            "unlinked_count": 1,
            "friction_trace_count": 0,
            "high_risk_count": 1,
            "duration_seconds": {"total": 12, "avg": 3.0, "max": 8},
            "by_lane": {"normal": 3, "unlinked": 1},
        }

    monkeypatch.setattr(cli_module, "summarize_traces", fake_summarize_traces)

    result = runner.invoke(
        app,
        [
            "harness",
            "summarize-traces",
            "--latest",
            "4",
            "--artifacts-root",
            str(tmp_path),
            "--include-development-evidence",
            "--out",
            str(report_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "status=pass traces=4 linked=3 unlinked=1 friction=0 high_risk=1 duration_total=12" in result.output
    assert load_json(report_path)["by_lane"] == {"normal": 3, "unlinked": 1}


def test_cli_harness_assess_development_writes_report(monkeypatch, tmp_path: Path) -> None:
    report_path = tmp_path / "development-assessment.json"

    def fake_assess_development(**kwargs):
        assert kwargs["latest"] == 10
        assert kwargs["artifacts_root"] == tmp_path
        return {
            "status": "warn",
            "process_quality": {"trace_count": 10, "high_risk_count": 2},
            "engineering_quality": {"verified_count": 7, "missing_verification_evidence": 3},
            "production_impact": {"fail": 0},
        }

    monkeypatch.setattr(cli_module, "assess_development", fake_assess_development)

    result = runner.invoke(
        app,
        [
            "harness",
            "assess-development",
            "--latest",
            "10",
            "--artifacts-root",
            str(tmp_path),
            "--out",
            str(report_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "status=warn traces=10 high_risk=2 verified=7 missing_evidence=3 production_fail=0" in result.output
    assert load_json(report_path)["status"] == "warn"


def test_cli_harness_preflight_writes_bounded_report(monkeypatch, tmp_path: Path) -> None:
    report_path = tmp_path / "preflight.json"

    def fake_preflight_context(**kwargs):
        assert kwargs["latest"] == 5
        assert kwargs["artifacts_root"] == tmp_path
        return {
            "status": "warn",
            "context_brief": ["Check trace 1 evidence."],
            "audit": {"checked": 5, "failed": 0},
            "assessment": {"missing_evidence": 1},
        }

    monkeypatch.setattr(cli_module, "preflight_context", fake_preflight_context)

    result = runner.invoke(
        app,
        ["harness", "preflight", "--latest", "5", "--artifacts-root", str(tmp_path), "--out", str(report_path)],
    )

    assert result.exit_code == 0, result.output
    assert "status=warn bullets=1 checked=5 missing_evidence=1" in result.output
    assert load_json(report_path)["context_brief"] == ["Check trace 1 evidence."]


def test_cli_harness_agent_start_writes_preflight_and_startup_state(monkeypatch, tmp_path: Path) -> None:
    preflight_path = tmp_path / "preflight.json"
    session_state = tmp_path / "session.json"
    startup_state = tmp_path / "startup.json"

    def fake_preflight_context(**kwargs):
        assert kwargs["latest"] == 3
        return {
            "status": "warn",
            "context_brief": ["Bounded warning."],
            "anti_pollution": {
                "raw_trace_rows_included": False,
                "raw_artifacts_included": False,
                "memory_written": False,
                "global_context_updated": False,
            },
        }

    monkeypatch.setattr(cli_module, "preflight_context", fake_preflight_context)

    result = runner.invoke(
        app,
        [
            "harness",
            "agent-start",
            "--summary",
            "Startup contract",
            "--latest",
            "3",
            "--preflight-out",
            str(preflight_path),
            "--session-state",
            str(session_state),
            "--startup-state",
            str(startup_state),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "status=warn" in result.output
    assert load_json(preflight_path)["context_brief"] == ["Bounded warning."]
    assert load_json(session_state)["summary"] == "Startup contract"
    startup = load_json(startup_state)
    assert startup["preflight_applied"] is True
    assert startup["preflight_ref"] == str(preflight_path)
    assert startup["preflight_brief_count"] == 1


def test_cli_harness_gate_development_enforce_fails(monkeypatch, tmp_path: Path) -> None:
    report_path = tmp_path / "gate.json"

    def fake_gate_development(**kwargs):
        assert kwargs["policy"] == "enforce"
        return {
            "status": "fail",
            "policy": "enforce",
            "issues": [{"code": "audit_failed", "trace_ids": [1]}],
        }

    monkeypatch.setattr(cli_module, "gate_development", fake_gate_development)

    result = runner.invoke(app, ["harness", "gate-development", "--policy", "enforce", "--out", str(report_path)])

    assert result.exit_code == 1
    assert "status=fail policy=enforce issues=1" in result.output
    assert load_json(report_path)["issues"][0]["code"] == "audit_failed"


def test_cli_harness_recommend_next_writes_report(monkeypatch, tmp_path: Path) -> None:
    report_path = tmp_path / "recommendations.json"

    def fake_recommend_next(**kwargs):
        assert kwargs["latest"] == 10
        return {
            "status": "warn",
            "recommendation_count": 1,
            "recommendations": [
                {
                    "id": "rec-missing-verification-evidence",
                    "source_trace_ids": [2],
                    "severity": "medium",
                    "reason": "missing evidence",
                    "suggested_action": "add evidence",
                    "evidence_refs": ["trace:2"],
                }
            ],
            "anti_pollution": {"memory_written": False, "stories_written": False},
        }

    monkeypatch.setattr(cli_module, "recommend_next", fake_recommend_next)

    result = runner.invoke(app, ["harness", "recommend-next", "--latest", "10", "--out", str(report_path)])

    assert result.exit_code == 0, result.output
    assert "status=warn recommendations=1" in result.output
    assert load_json(report_path)["recommendations"][0]["source_trace_ids"] == [2]


def test_cli_harness_memory_candidates_writes_report_without_memory_write(monkeypatch, tmp_path: Path) -> None:
    report_path = tmp_path / "memory-candidates.json"

    def fake_memory_candidates(**kwargs):
        assert kwargs["latest"] == 20
        return {
            "status": "warn",
            "candidate_count": 1,
            "candidates": [
                {
                    "id": "memory-candidate-1",
                    "source_trace_ids": [1, 2],
                    "recurrence_count": 2,
                    "confidence": "medium",
                    "proposed_memory_text": "Use verification evidence for high-risk work.",
                    "expiry_or_review_after": "review after 30 days",
                }
            ],
            "anti_pollution": {"memory_written": False, "memory_path_touched": False},
        }

    monkeypatch.setattr(cli_module, "memory_candidates", fake_memory_candidates)

    result = runner.invoke(app, ["harness", "memory-candidates", "--latest", "20", "--out", str(report_path)])

    assert result.exit_code == 0, result.output
    assert "status=warn candidates=1 memory_written=false" in result.output
    assert load_json(report_path)["anti_pollution"]["memory_written"] is False


def test_cli_harness_session_start_writes_state(tmp_path: Path) -> None:
    state_path = tmp_path / "harness-session.json"

    result = runner.invoke(
        app,
        ["harness", "session-start", "--summary", "Timed trace", "--session-state", str(state_path)],
    )

    assert result.exit_code == 0, result.output
    assert "status=pass" in result.output
    payload = load_json(state_path)
    assert payload["summary"] == "Timed trace"
    assert payload["started_at_epoch"] > 0


def test_cli_harness_dev_trace_uses_session_state_duration(monkeypatch, tmp_path: Path) -> None:
    harness_log = tmp_path / "harness-call.txt"
    harness_cli = tmp_path / "harness-cli"
    session_state = tmp_path / "harness-session.json"
    session_state.write_text(json.dumps({"summary": "Timed trace", "started_at_epoch": 100.0}), encoding="utf-8")
    harness_cli.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "from pathlib import Path",
                "import sys",
                "if sys.argv[1] == 'intake':",
                "    print('Intake #9 recorded.')",
                "else:",
                f"    Path({str(harness_log)!r}).write_text(' '.join(sys.argv[1:]), encoding='utf-8')",
            ]
        ),
        encoding="utf-8",
    )
    harness_cli.chmod(0o755)
    monkeypatch.setattr(cli_module, "harness_cli_path", lambda: harness_cli)
    monkeypatch.setattr(harness_module, "harness_cli_path", lambda: harness_cli)
    monkeypatch.setattr(cli_module, "time", lambda: 112.0)

    result = runner.invoke(
        app,
        [
            "harness",
            "dev-trace",
            "--summary",
            "Timed trace",
            "--session-state",
            str(session_state),
            "--action",
            "verify session timer",
            "--read",
            "docs/HARNESS.md",
            "--changed",
            "docs/HARNESS.md",
            "--decision",
            "duration comes from session state",
        ],
    )

    assert result.exit_code == 0, result.output
    assert not session_state.exists()
    harness_command = harness_log.read_text(encoding="utf-8")
    assert "--duration 12" in harness_command


def test_cli_harness_dev_trace_consumes_startup_preflight_state(monkeypatch, tmp_path: Path) -> None:
    harness_log = tmp_path / "harness-call.txt"
    harness_cli = tmp_path / "harness-cli"
    startup_state = tmp_path / "startup.json"
    startup_state.write_text(
        json.dumps(
            {
                "preflight_applied": True,
                "preflight_ref": str(tmp_path / "preflight.json"),
                "preflight_brief_count": 2,
            }
        ),
        encoding="utf-8",
    )
    harness_cli.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "from pathlib import Path",
                "import sys",
                "if sys.argv[1] == 'intake':",
                "    print('Intake #12 recorded.')",
                "else:",
                f"    Path({str(harness_log)!r}).write_text(' '.join(sys.argv[1:]), encoding='utf-8')",
            ]
        ),
        encoding="utf-8",
    )
    harness_cli.chmod(0o755)
    monkeypatch.setattr(cli_module, "harness_cli_path", lambda: harness_cli)
    monkeypatch.setattr(harness_module, "harness_cli_path", lambda: harness_cli)

    result = runner.invoke(
        app,
        [
            "harness",
            "dev-trace",
            "--summary",
            "Startup trace",
            "--startup-state",
            str(startup_state),
            "--no-session-state",
        ],
    )

    assert result.exit_code == 0, result.output
    assert not startup_state.exists()
    harness_command = harness_log.read_text(encoding="utf-8")
    assert "preflight_applied=true" in harness_command
    assert f"preflight_ref={tmp_path / 'preflight.json'}" in harness_command
    assert "preflight_brief_count=2" in harness_command


def test_cli_harness_dev_trace_started_at_sets_duration(monkeypatch, tmp_path: Path) -> None:
    harness_log = tmp_path / "harness-call.txt"
    harness_cli = tmp_path / "harness-cli"
    harness_cli.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "from pathlib import Path",
                "import sys",
                "if sys.argv[1] == 'intake':",
                "    print('Intake #10 recorded.')",
                "else:",
                f"    Path({str(harness_log)!r}).write_text(' '.join(sys.argv[1:]), encoding='utf-8')",
            ]
        ),
        encoding="utf-8",
    )
    harness_cli.chmod(0o755)
    monkeypatch.setattr(cli_module, "harness_cli_path", lambda: harness_cli)
    monkeypatch.setattr(harness_module, "harness_cli_path", lambda: harness_cli)
    monkeypatch.setattr(cli_module, "time", lambda: 205.0)

    result = runner.invoke(
        app,
        [
            "harness",
            "dev-trace",
            "--summary",
            "Started trace",
            "--started-at",
            "200",
            "--no-session-state",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "--duration 5" in harness_log.read_text(encoding="utf-8")


def test_cli_harness_dev_trace_lane_override_reaches_intake(monkeypatch, tmp_path: Path) -> None:
    harness_log = tmp_path / "harness-call.txt"
    harness_cli = tmp_path / "harness-cli"
    captured = {}
    harness_cli.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "from pathlib import Path",
                "import sys",
                f"Path({str(harness_log)!r}).write_text(' '.join(sys.argv[1:]), encoding='utf-8')",
            ]
        ),
        encoding="utf-8",
    )
    harness_cli.chmod(0o755)

    def fake_record_trace_intake(**kwargs):
        captured.update(kwargs)
        return 11

    monkeypatch.setattr(cli_module, "harness_cli_path", lambda: harness_cli)
    monkeypatch.setattr(harness_module, "harness_cli_path", lambda: harness_cli)
    monkeypatch.setattr(cli_module, "record_trace_intake", fake_record_trace_intake)

    result = runner.invoke(
        app,
        [
            "harness",
            "dev-trace",
            "--summary",
            "Override lane",
            "--lane",
            "high-risk",
            "--intake-type",
            "harness improvement",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["lane"] == "high-risk"
    assert captured["input_type"] == "harness improvement"
    assert "--intake 11" in harness_log.read_text(encoding="utf-8")


def test_cli_harness_dev_trace_records_detailed_session_trace(monkeypatch, tmp_path: Path) -> None:
    harness_log = tmp_path / "harness-call.txt"
    harness_cli = tmp_path / "harness-cli"
    harness_cli.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "from pathlib import Path",
                "import sys",
                "if sys.argv[1] == 'intake':",
                "    print('Intake #7 recorded.')",
                "else:",
                f"    Path({str(harness_log)!r}).write_text(' '.join(sys.argv[1:]), encoding='utf-8')",
            ]
        ),
        encoding="utf-8",
    )
    harness_cli.chmod(0o755)
    monkeypatch.setattr(cli_module, "harness_cli_path", lambda: harness_cli)
    monkeypatch.setattr(harness_module, "harness_cli_path", lambda: harness_cli)

    result = runner.invoke(
        app,
        [
            "harness",
            "dev-trace",
            "--summary",
            "Investigate detailed harness trace",
            "--no-session-state",
            "--action",
            "read harness docs",
            "--read",
            "docs/HARNESS.md",
            "--changed",
            "src/strategy_codebot/cli.py",
            "--decision",
            "record dev sessions with repository harness",
            "--test-outcome",
            "pass",
            "--review-outcome",
            "pass",
            "--review-evidence",
            "focused review completed",
            "--validation-outcome",
            "pass",
            "--production-impact",
            "skipped",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "status=pass" in result.output
    assert "intake=7" in result.output
    harness_command = harness_log.read_text(encoding="utf-8")
    assert harness_command.startswith("trace --summary")
    assert "--intake 7" in harness_command
    assert "--actions read harness docs" in harness_command
    assert "--read docs/HARNESS.md" in harness_command
    assert "test_outcome=pass" in harness_command
    assert "review_outcome=pass" in harness_command
    assert "review_evidence=focused review completed" in harness_command
    assert "validation_outcome=pass" in harness_command
    assert "production_impact=skipped" in harness_command
    assert "--changed src/strategy_codebot/cli.py" in harness_command
    assert "--decisions record dev sessions with repository harness" in harness_command
    assert "--errors []" in harness_command
    assert "--friction none" in harness_command
    assert "--duration 0" in harness_command
    assert "--tokens 0" in harness_command


def test_cli_harness_dev_trace_defaults_to_detailed_sentinels(monkeypatch, tmp_path: Path) -> None:
    harness_log = tmp_path / "harness-call.txt"
    harness_cli = tmp_path / "harness-cli"
    harness_cli.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "from pathlib import Path",
                "import sys",
                "if sys.argv[1] == 'intake':",
                "    print('Intake #8 recorded.')",
                "else:",
                f"    Path({str(harness_log)!r}).write_text(' '.join(sys.argv[1:]), encoding='utf-8')",
            ]
        ),
        encoding="utf-8",
    )
    harness_cli.chmod(0o755)
    monkeypatch.setattr(cli_module, "harness_cli_path", lambda: harness_cli)
    monkeypatch.setattr(harness_module, "harness_cli_path", lambda: harness_cli)

    result = runner.invoke(app, ["harness", "dev-trace", "--summary", "Record chat-only trace", "--no-session-state"])

    assert result.exit_code == 0, result.output
    assert "intake=8" in result.output
    harness_command = harness_log.read_text(encoding="utf-8")
    assert "--intake 8" in harness_command
    assert "--actions dev_trace_recorded" in harness_command
    assert "--read conversation_context" in harness_command
    assert "--changed no_repo_files_changed" in harness_command
    assert "--decisions no_durable_decisions" in harness_command
    assert "--errors []" in harness_command


def test_cli_run_live_passes_provider_options(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_run_strategy(**kwargs):
        captured.update(kwargs)
        return {"run_id": "live-cli", "status": "pass", "out_dir": str(kwargs["out_dir"])}

    monkeypatch.setattr(cli_module, "run_strategy", fake_run_strategy)
    registry_path = tmp_path / "models.yaml"
    result = runner.invoke(
        app,
        [
            "run",
            "--prompt",
            "Create a Pine strategy",
            "--mode",
            "live",
            "--out",
            str(tmp_path / "live"),
            "--model-registry",
            str(registry_path),
            "--workflow",
            "single",
            "--model",
            "openai/test-model",
            "--cost-profile",
            "quality",
            "--model-stage",
            "pine_code_generation=openrouter/qwen/qwen3-coder:free",
            "--save-raw-provider",
            "--knowledge-context",
            "off",
            "--otel-export",
            str(tmp_path / "live" / "otel.jsonl"),
            "--policy",
            "enforce",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["model_registry"] == registry_path
    assert captured["live_options"].model_override == "openai/test-model"
    assert captured["live_options"].workflow == "single"
    assert captured["live_options"].cost_profile == "quality"
    assert captured["live_options"].model_stage_overrides == {"pine_code_generation": "openrouter/qwen/qwen3-coder:free"}
    assert captured["live_options"].save_raw_provider is True
    assert captured["live_options"].knowledge_context == "off"
    assert captured["otel_export"] == tmp_path / "live" / "otel.jsonl"
    assert captured["policy"] == "enforce"


def test_cli_rejects_unknown_model_stage() -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--prompt",
            "Create a Pine strategy",
            "--mode",
            "live",
            "--model-stage",
            "balnced_review=openai/test",
        ],
    )

    assert result.exit_code != 0
    assert "Unknown model stage override" in result.output


def test_cli_run_with_parallel_review_creates_review_report(tmp_path: Path) -> None:
    out_dir = tmp_path / "cli-review-run"
    result = run_cli_dry_run(out_dir, "--review", "parallel")

    assert load_json(out_dir / "review-report.json")["run_status"] == "completed"


def test_cli_validate_pine_writes_report(tmp_path: Path) -> None:
    run_dir = tmp_path / "cli-run"
    run_cli_dry_run(run_dir)
    report_path = tmp_path / "pine-report.json"
    result = runner.invoke(
        app,
        [
            "validate-pine",
            "--file",
            str(run_dir / "pine" / "strategy.pine"),
            "--spec",
            "examples/specs/ma-crossover-pine.json",
            "--out",
            str(report_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert load_json(report_path)["status"] == "pass"


def test_cli_review_existing_run_writes_report(tmp_path: Path) -> None:
    run_dir = tmp_path / "cli-run-for-review"
    run_cli_dry_run(run_dir)
    report_path = run_dir / "review-report.json"
    result = runner.invoke(
        app,
        [
            "review",
            "--run-dir",
            str(run_dir),
            "--mode",
            "dry-run",
            "--out",
            str(report_path),
            "--no-record-harness",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "decision=approve" in result.output
    assert load_json(report_path)["run_status"] == "completed"
    assert (run_dir / "runtime-trace.jsonl").exists()
    assert (run_dir / "review-runtime-trace.jsonl").exists()


def test_cli_review_does_not_overwrite_run_runtime_trace(tmp_path: Path) -> None:
    run_dir = tmp_path / "cli-review-trace"
    run_cli_dry_run(run_dir)
    before = (run_dir / "runtime-trace.jsonl").read_text(encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "review",
            "--run-dir",
            str(run_dir),
            "--mode",
            "dry-run",
            "--out",
            str(run_dir / "review-report.json"),
            "--no-record-harness",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (run_dir / "runtime-trace.jsonl").read_text(encoding="utf-8") == before
    assert (run_dir / "review-runtime-summary.json").exists()


def test_cli_review_rejects_invalid_policy_without_runtime_trace(tmp_path: Path) -> None:
    run_dir = tmp_path / "cli-review-policy"
    run_cli_dry_run(run_dir)
    result = runner.invoke(
        app,
        [
            "review",
            "--run-dir",
            str(run_dir),
            "--mode",
            "dry-run",
            "--out",
            str(run_dir / "review-report.json"),
            "--no-runtime-trace",
            "--policy",
            "bogus",
            "--no-record-harness",
        ],
    )

    assert result.exit_code != 0


def test_cli_no_runtime_trace_preserves_phase_2_artifacts(tmp_path: Path) -> None:
    out_dir = tmp_path / "cli-no-runtime"
    run_cli_dry_run(out_dir, "--review", "parallel", "--no-runtime-trace")

    assert (out_dir / "review-report.json").exists()
    assert not (out_dir / "runtime-trace.jsonl").exists()
    assert not (out_dir / "runtime-summary.json").exists()


def test_cli_tools_list_and_check(tmp_path: Path) -> None:
    list_result = runner.invoke(app, ["tools", "list"])
    out_path = tmp_path / "tool-check.json"
    check_result = runner.invoke(app, ["tools", "check", "--out", str(out_path)])

    assert list_result.exit_code == 0, list_result.output
    assert "load_strategy_spec" in list_result.output
    assert check_result.exit_code == 0, check_result.output
    assert load_json(out_path)["status"] == "pass"


def test_cli_eval_live_reports_failure(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_run_live_eval(**kwargs):
        captured.update(kwargs)
        return {"status": "fail", "case_count": 2, "failed": 1}

    monkeypatch.setattr(cli_module, "run_live_eval", fake_run_live_eval)

    result = runner.invoke(
        app,
        [
            "eval",
            "live",
            "--suite",
            str(tmp_path / "suite.yaml"),
            "--out",
            str(tmp_path / "eval"),
            "--cost-profile",
            "cheap",
            "--model-stage",
            "balanced_review=openrouter/qwen/qwen3.6-plus-preview",
            "--concurrency",
            "3",
            "--knowledge-context",
            "off",
            "--otel-export",
            str(tmp_path / "eval" / "otel.jsonl"),
        ],
    )

    assert result.exit_code == 1
    assert "status=fail cases=2 failed=1" in result.output
    assert captured["live_options"].cost_profile == "cheap"
    assert captured["live_options"].model_stage_overrides == {"balanced_review": "openrouter/qwen/qwen3.6-plus-preview"}
    assert captured["live_options"].knowledge_context == "off"
    assert captured["otel_export"] == tmp_path / "eval" / "otel.jsonl"
    assert captured["concurrency"] == 3


def test_cli_eval_matrix_passes_options(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_run_model_combo_matrix(**kwargs):
        captured.update(kwargs)
        return {"status": "pass", "combos": [{"id": "baseline_gemini_all"}], "recommended_combo": "baseline_gemini_all"}

    monkeypatch.setattr(cli_module, "run_model_combo_matrix", fake_run_model_combo_matrix)

    result = runner.invoke(
        app,
        [
            "eval",
            "matrix",
            "--smoke-suite",
            str(tmp_path / "smoke.yaml"),
            "--full-suite",
            str(tmp_path / "full.yaml"),
            "--out",
            str(tmp_path / "matrix"),
            "--combo",
            "baseline_gemini_all",
            "--run-full",
            "--concurrency",
            "1",
            "--knowledge-context",
            "off",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "status=pass combos=1 recommended=baseline_gemini_all" in result.output
    assert captured["smoke_suite_path"] == tmp_path / "smoke.yaml"
    assert captured["full_suite_path"] == tmp_path / "full.yaml"
    assert captured["out_dir"] == tmp_path / "matrix"
    assert captured["combo_ids"] == ["baseline_gemini_all"]
    assert captured["run_full"] is True
    assert captured["concurrency"] == 1
    assert captured["knowledge_context"] == "off"


def test_cli_registry_defaults_work_outside_repo_cwd(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    tool_report = tmp_path / "tool-check.json"
    source_report = tmp_path / "source-check.json"

    tool_result = runner.invoke(app, ["tools", "check", "--out", str(tool_report)])
    source_result = runner.invoke(app, ["knowledge", "check", "--offline", "--out", str(source_report)])

    assert tool_result.exit_code == 0, tool_result.output
    assert source_result.exit_code == 0, source_result.output
    assert load_json(tool_report)["status"] == "pass"
    assert load_json(source_report)["status"] == "pass"


def test_cli_knowledge_check_writes_report(tmp_path: Path) -> None:
    out_path = tmp_path / "source-check.json"
    result = runner.invoke(
        app,
        [
            "knowledge",
            "check",
            "--offline",
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert load_json(out_path)["status"] == "pass"


def test_cli_knowledge_snapshot_diff_audit_and_propose(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_result = runner.invoke(
        app,
        [
            "knowledge",
            "snapshot",
            "--offline",
            "--out",
            str(snapshot_path),
        ],
    )
    assert snapshot_result.exit_code == 0, snapshot_result.output
    assert load_json(snapshot_path)["fetch_mode"] == "offline"

    diff_path = tmp_path / "knowledge-diff.json"
    diff_result = runner.invoke(
        app,
        [
            "knowledge",
            "diff",
            "--baseline",
            "examples/knowledge/baseline-snapshot.json",
            "--current",
            str(snapshot_path),
            "--out",
            str(diff_path),
        ],
    )
    assert diff_result.exit_code == 0, diff_result.output
    assert load_json(diff_path)["status"] == "manual_required"

    run_dir = tmp_path / "knowledge-run"
    run_cli_dry_run(run_dir, "--review", "parallel")
    audit_path = tmp_path / "knowledge-audit.json"
    audit_result = runner.invoke(
        app,
        [
            "knowledge",
            "audit",
            "--runs",
            str(run_dir),
            "--out",
            str(audit_path),
        ],
    )
    assert audit_result.exit_code == 0, audit_result.output
    assert load_json(audit_path)["run_id"] == run_dir.name

    proposal_path = tmp_path / "proposal.json"
    proposal_result = runner.invoke(
        app,
        [
            "knowledge",
            "propose",
            "--diff",
            str(diff_path),
            "--audit",
            str(audit_path),
            "--out",
            str(proposal_path),
        ],
    )
    assert proposal_result.exit_code == 0, proposal_result.output
    assert load_json(proposal_path)["status"] == "needs_review"
