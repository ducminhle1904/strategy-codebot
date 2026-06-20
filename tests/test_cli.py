import json
from pathlib import Path
import sys
import types

from typer.testing import CliRunner

from strategy_codebot import __version__
from strategy_codebot import cli as cli_module
from strategy_codebot import harness as harness_module
from strategy_codebot.cli import app
from strategy_codebot.knowledge_base import EMBEDDING_DIMENSION_TEXT_3_SMALL, EMBEDDING_MODEL_PRODUCTION_OPENROUTER
from strategy_codebot.schemas import load_json, write_json


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


def test_cli_harness_route_health_writes_report(tmp_path: Path, monkeypatch) -> None:
    out_path = tmp_path / "route-health.json"
    monkeypatch.setattr(
        cli_module,
        "route_health_report",
        lambda **kwargs: {
            "status": "pass",
            "store": "postgres",
            "configured": True,
            "route_count": 1,
            "cooldown_count": 1,
            "routes": [{"stage": "repair", "route_status": "cooldown"}],
        },
    )

    result = runner.invoke(app, ["harness", "route-health", "--out", str(out_path), "--user-tier", "paid_low"])

    assert result.exit_code == 0, result.output
    assert "routes=1" in result.output
    assert load_json(out_path)["routes"][0]["route_status"] == "cooldown"


def test_cli_harness_model_candidate_matrix_writes_report(tmp_path: Path, monkeypatch) -> None:
    out_path = tmp_path / "candidate-matrix.json"

    def fake_matrix(**kwargs):
        payload = {
            "status": "pass",
            "candidate_count": 1,
            "catalog_status": "pass",
            "candidates": [{"promotion_eligible": True}],
            "promotion_recommendations": {"pine_code_generation": {"recommended_route": "openrouter/qwen/qwen3-coder-next"}},
        }
        write_json(kwargs["out"], payload)
        return payload

    monkeypatch.setattr(cli_module, "build_model_candidate_matrix", fake_matrix)

    result = runner.invoke(app, ["harness", "model-candidate-matrix", "--out", str(out_path), "--stage", "pine_code_generation"])

    assert result.exit_code == 0, result.output
    assert "candidates=1" in result.output
    assert "eligible=1" in result.output
    assert load_json(out_path)["promotion_recommendations"]["pine_code_generation"]["recommended_route"] == "openrouter/qwen/qwen3-coder-next"


def test_cli_models_gateways_smoke_route_reports_connection_error(tmp_path: Path, monkeypatch) -> None:
    out_path = tmp_path / "smoke-route.json"

    def completion(**_kwargs):
        raise RuntimeError("OpenAIException - Connection error.")

    monkeypatch.setitem(sys.modules, "litellm", types.SimpleNamespace(completion=completion))
    monkeypatch.setenv("LITELLM_PROXY_API_KEY", "test-proxy")
    monkeypatch.setenv("LITELLM_PROXY_API_BASE", "https://litellm-proxy.example/v1")

    result = runner.invoke(app, ["models", "gateways", "smoke-route", "--alias", "paid_low.repair", "--out", str(out_path)])

    assert result.exit_code == 1
    report = load_json(out_path)
    assert report["status"] == "fail"
    assert report["provider_error_subclass"] == "provider_connection_error"
    assert "messages" not in report
    assert "api_key" not in json.dumps(report).lower()


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
            "--prompt-profile",
            "optimized_v1",
            "--require-web-search",
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
    assert captured["live_options"].prompt_profile == "optimized_v1"
    assert captured["live_options"].web_search == "auto"
    assert captured["live_options"].require_web_search is True
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


def test_cli_litellm_keys_aliases_lists_paid_tier_routes(tmp_path: Path) -> None:
    out_path = tmp_path / "aliases.json"
    result = runner.invoke(app, ["models", "litellm", "keys", "aliases", "--tier", "paid_medium", "--out", str(out_path)])

    assert result.exit_code == 0, result.output
    assert "status=pass tier=paid_medium aliases=5" in result.output
    assert "paid_medium.strategy_reasoning" in result.output
    report = load_json(out_path)
    assert report["aliases"] == [
        "paid_medium.balanced_review",
        "paid_medium.pine_code_generation",
        "paid_medium.repair",
        "paid_medium.strategy_coding",
        "paid_medium.strategy_reasoning",
    ]


def test_cli_litellm_keys_check_allows_local_master_key(monkeypatch) -> None:
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-litellm-local-dev")
    monkeypatch.setenv("LITELLM_PROXY_API_KEY", "sk-litellm-local-dev")
    monkeypatch.setenv("LITELLM_ADMIN_API_BASE", "http://127.0.0.1:4000")

    result = runner.invoke(app, ["models", "litellm", "keys", "check"])

    assert result.exit_code == 0, result.output
    assert "status=pass production=False" in result.output


def test_cli_litellm_keys_check_production_rejects_master_key_as_runtime_key(monkeypatch, tmp_path: Path) -> None:
    out_path = tmp_path / "litellm-key-check.json"
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-litellm-master")
    monkeypatch.setenv("LITELLM_PROXY_API_KEY", "sk-litellm-master")
    monkeypatch.setenv("LITELLM_ADMIN_API_BASE", "http://127.0.0.1:4000")

    result = runner.invoke(app, ["models", "litellm", "keys", "check", "--production", "--out", str(out_path)])

    assert result.exit_code == 1
    report = load_json(out_path)
    assert report["status"] == "fail"
    assert {"name": "proxy_key_is_virtual", "status": "fail"} in report["checks"]


def test_cli_litellm_keys_provision_posts_admin_payload(monkeypatch, tmp_path: Path) -> None:
    requests = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"key": "sk-generated-virtual-key", "key_alias": "workspace-a-paid-medium"}).encode("utf-8")

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    out_path = tmp_path / "generated-key.json"
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-litellm-master")
    monkeypatch.setenv("LITELLM_BUDGET_PAID_MEDIUM_MONTHLY_USD", "42.5")
    monkeypatch.setattr(cli_module.urllib.request, "urlopen", fake_urlopen)

    result = runner.invoke(
        app,
        [
            "models",
            "litellm",
            "keys",
            "provision",
            "--tier",
            "paid_medium",
            "--workspace-id",
            "workspace-a",
            "--user-id",
            "user-a",
            "--api-base",
            "http://litellm.local",
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "contains_secret=true" in result.output
    request, timeout = requests[0]
    assert timeout == 30
    assert request.full_url == "http://litellm.local/key/generate"
    assert request.headers["Authorization"] == "Bearer sk-litellm-master"
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["models"] == [
        "paid_medium.balanced_review",
        "paid_medium.pine_code_generation",
        "paid_medium.repair",
        "paid_medium.strategy_coding",
        "paid_medium.strategy_reasoning",
    ]
    assert payload["metadata"] == {
        "tier": "paid_medium",
        "workspace_id": "workspace-a",
        "source": "strategy-codebot",
        "user_id": "user-a",
    }
    assert payload["budget_duration"] == "30d"
    assert payload["max_budget"] == 42.5
    report = load_json(out_path)
    assert report["response"]["key"] == "sk-generated-virtual-key"


def test_cli_litellm_keys_provision_requires_master_key(monkeypatch) -> None:
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)

    result = runner.invoke(app, ["models", "litellm", "keys", "provision", "--workspace-id", "workspace-a"])

    assert result.exit_code != 0
    assert "LITELLM_MASTER_KEY is required" in result.output


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
            "--prompt-profile",
            "optimized_v1",
            "--web-search",
            "on",
            "--otel-export",
            str(tmp_path / "eval" / "otel.jsonl"),
        ],
    )

    assert result.exit_code == 1
    assert "status=fail cases=2 failed=1" in result.output
    assert captured["live_options"].cost_profile == "cheap"
    assert captured["live_options"].model_stage_overrides == {"balanced_review": "openrouter/qwen/qwen3.6-plus-preview"}
    assert captured["live_options"].knowledge_context == "off"
    assert captured["live_options"].prompt_profile == "optimized_v1"
    assert captured["live_options"].web_search == "on"
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
    assert "status=pass mode=combo combos=1 tiers=0 recommended=baseline_gemini_all" in result.output
    assert captured["smoke_suite_path"] == tmp_path / "smoke.yaml"
    assert captured["full_suite_path"] == tmp_path / "full.yaml"
    assert captured["out_dir"] == tmp_path / "matrix"
    assert captured["combo_ids"] == ["baseline_gemini_all"]
    assert captured["matrix_mode"] == "combo"
    assert captured["tier_ids"] is None
    assert captured["run_full"] is True
    assert captured["concurrency"] == 1
    assert captured["knowledge_context"] == "off"


def test_cli_eval_matrix_tier_mode_passes_tiers(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_run_model_combo_matrix(**kwargs):
        captured.update(kwargs)
        return {"status": "pass", "mode": "tier", "tiers": [{"id": "free"}], "combos": [], "recommended_tier": "free"}

    monkeypatch.setattr(cli_module, "run_model_combo_matrix", fake_run_model_combo_matrix)

    result = runner.invoke(
        app,
        [
            "eval",
            "matrix",
            "--mode",
            "tier",
            "--tier",
            "free",
            "--out",
            str(tmp_path / "matrix"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "status=pass mode=tier combos=0 tiers=1 recommended=free" in result.output
    assert captured["matrix_mode"] == "tier"
    assert captured["tier_ids"] == ["free"]


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


def test_cli_knowledge_base_init_search_eval_and_candidates(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    schema_path = tmp_path / "kb" / "postgres-schema.sql"
    init_result = runner.invoke(app, ["knowledge", "init", "--index", str(index_path), "--postgres-schema", str(schema_path)])

    assert init_result.exit_code == 0, init_result.output
    assert load_json(index_path)["store"]["type"] == "postgres_pgvector"
    assert "CREATE EXTENSION IF NOT EXISTS vector" in schema_path.read_text(encoding="utf-8")

    search_path = tmp_path / "search.json"
    search_result = runner.invoke(app, ["knowledge", "search", "break of structure retest", "--index", str(index_path), "--out", str(search_path)])
    assert search_result.exit_code == 0, search_result.output
    search_report = load_json(search_path)
    assert search_report["retrieved_chunks"]
    assert search_report["citations"]
    assert "retrieval_confidence" in search_report
    assert "filters_applied" in search_report
    assert "low_confidence" in search_report
    assert "required_source_hits" in search_report

    eval_path = tmp_path / "knowledge-eval.json"
    eval_result = runner.invoke(app, ["knowledge", "eval", "--index", str(index_path), "--out", str(eval_path)])
    assert eval_result.exit_code == 0, eval_result.output
    assert load_json(eval_path)["status"] == "pass"

    candidates_path = tmp_path / "kb" / "candidates.json"
    propose_result = runner.invoke(
        app,
        [
            "knowledge",
            "candidates",
            "propose",
            "--lesson",
            "Use confirmed bars for BOS retest entries.",
            "--evidence-ref",
            "review-report.json",
            "--candidates",
            str(candidates_path),
        ],
    )
    assert propose_result.exit_code == 0, propose_result.output
    candidate_id = load_json(candidates_path)["candidates"][0]["candidate_id"]

    approve_result = runner.invoke(app, ["knowledge", "candidates", "approve", candidate_id, "--index", str(index_path), "--candidates", str(candidates_path)])
    assert approve_result.exit_code == 0, approve_result.output
    assert load_json(candidates_path)["candidates"][0]["status"] == "approved"

    reject_result = runner.invoke(app, ["knowledge", "candidates", "reject", candidate_id, "--candidates", str(candidates_path)])
    assert reject_result.exit_code == 0, reject_result.output
    assert load_json(candidates_path)["candidates"][0]["status"] == "rejected"


def test_cli_knowledge_learn_from_run_auto_promotes_candidate(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    candidates_path = tmp_path / "kb" / "candidates.json"
    artifacts_root = tmp_path / "artifacts"
    report_path = tmp_path / "learning-report.json"
    init_result = runner.invoke(app, ["knowledge", "init", "--index", str(index_path)])
    assert init_result.exit_code == 0, init_result.output
    for run in ("run-01", "run-02"):
        write_json(
            artifacts_root / run / "eval-report.json",
            {
                "status": "fail",
                "cases": [
                    {
                        "id": "case-a",
                        "status": "fail",
                        "failure_class": "static_validation_failed",
                        "failure_stage": "final_gate",
                        "validation_failures": [{"name": "version_header", "status": "fail"}],
                    }
                ],
            },
        )

    result = runner.invoke(
        app,
        [
            "knowledge",
            "learn-from-run",
            "--artifacts-root",
            str(artifacts_root),
            "--index",
            str(index_path),
            "--candidates",
            str(candidates_path),
            "--out",
            str(report_path),
        ],
    )

    assert result.exit_code == 0, result.output
    report = load_json(report_path)
    assert report["promoted_count"] == 1
    assert load_json(candidates_path)["candidates"][0]["status"] == "approved"


def test_cli_knowledge_trusted_source_snapshot_summary_and_approval(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "strategy_codebot.knowledge_base._fetch_url_text",
        lambda url: (
            "Risk management for crypto trading requires position sizing, stop loss planning, liquidity review, "
            "and volatility awareness. Exchange risk and leverage can change strategy risk."
        ),
    )
    index_path = tmp_path / "kb" / "index.json"
    snapshot_path = tmp_path / "snapshot.json"
    proposal_path = tmp_path / "proposal.json"

    init_result = runner.invoke(app, ["knowledge", "init", "--index", str(index_path)])
    assert init_result.exit_code == 0, init_result.output

    snapshot_result = runner.invoke(
        app,
        [
            "knowledge",
            "snapshot",
            "--source-id",
            "binance-academy-risk-management-strategies",
            "--fetch",
            "--out",
            str(snapshot_path),
        ],
    )
    assert snapshot_result.exit_code == 0, snapshot_result.output
    assert load_json(snapshot_path)["source_state"] == "snapshotted"

    summarize_result = runner.invoke(app, ["knowledge", "summarize-snapshot", "--snapshot", str(snapshot_path), "--out", str(proposal_path)])
    assert summarize_result.exit_code == 0, summarize_result.output
    proposal = load_json(proposal_path)
    assert proposal["status"] == "needs_review"
    assert "extracted_text" not in proposal

    approve_result = runner.invoke(app, ["knowledge", "approve-source-summary", "--proposal", str(proposal_path), "--index", str(index_path)])
    assert approve_result.exit_code == 0, approve_result.output

    search_path = tmp_path / "source-search.json"
    search_result = runner.invoke(app, ["knowledge", "search", "crypto liquidity exchange risk", "--index", str(index_path), "--out", str(search_path)])
    assert search_result.exit_code == 0, search_result.output
    assert any(chunk["source_type"] == "approved_source_summary" for chunk in load_json(search_path)["retrieved_chunks"])


def test_cli_knowledge_health_skips_when_unconfigured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("STRATEGY_CODEBOT_KNOWLEDGE_DATABASE_URL", raising=False)
    out = tmp_path / "knowledge-health.json"

    result = runner.invoke(app, ["knowledge", "health", "--out", str(out)])

    assert result.exit_code == 0, result.output
    report = load_json(out)
    assert report["status"] == "skipped"
    assert report["configured"] is False


def test_cli_knowledge_init_production_openrouter_profile_writes_1536_schema(tmp_path: Path, monkeypatch) -> None:
    def fake_remote_embedding(text: str, embedding_model: str, embedding_provider: str) -> list[float]:
        assert embedding_model == EMBEDDING_MODEL_PRODUCTION_OPENROUTER
        assert embedding_provider == "openrouter"
        return [0.0] * EMBEDDING_DIMENSION_TEXT_3_SMALL

    monkeypatch.setattr("strategy_codebot.knowledge_base._remote_embedding", fake_remote_embedding)
    index_path = tmp_path / "kb" / "index.json"
    schema_path = tmp_path / "kb" / "postgres-schema.sql"

    result = runner.invoke(
        app,
        [
            "knowledge",
            "init",
            "--index",
            str(index_path),
            "--postgres-schema",
            str(schema_path),
            "--embedding-profile",
            "production-openrouter",
        ],
    )

    assert result.exit_code == 0, result.output
    index = load_json(index_path)
    assert index["embedding_provider"] == "openrouter"
    assert index["embedding_model"] == EMBEDDING_MODEL_PRODUCTION_OPENROUTER
    assert index["embedding_dimension"] == EMBEDDING_DIMENSION_TEXT_3_SMALL
    assert "embedding vector(1536)" in schema_path.read_text(encoding="utf-8")
