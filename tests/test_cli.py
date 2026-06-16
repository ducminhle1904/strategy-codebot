from pathlib import Path

from typer.testing import CliRunner

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
