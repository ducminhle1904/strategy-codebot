from pathlib import Path

from typer.testing import CliRunner

from strategy_codebot import __version__
from strategy_codebot import cli as cli_module
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
