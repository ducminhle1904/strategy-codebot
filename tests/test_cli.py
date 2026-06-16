import json
from pathlib import Path

from typer.testing import CliRunner

from strategy_codebot.cli import app


runner = CliRunner()


def test_cli_run_dry_run_creates_artifacts(tmp_path: Path) -> None:
    out_dir = tmp_path / "cli-run"
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
            "--no-record-harness",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "status=pass" in result.output
    assert (out_dir / "pine" / "strategy.pine").exists()


def test_cli_validate_pine_writes_report(tmp_path: Path) -> None:
    run_dir = tmp_path / "cli-run"
    runner.invoke(
        app,
        [
            "run",
            "--spec",
            "examples/specs/ma-crossover-pine.json",
            "--mode",
            "dry-run",
            "--out",
            str(run_dir),
            "--no-record-harness",
        ],
    )
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
    assert json.loads(report_path.read_text())["status"] == "pass"


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
    assert json.loads(out_path.read_text())["status"] == "pass"

