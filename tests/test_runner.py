from pathlib import Path

import pytest

from strategy_codebot.runner import run_strategy
from strategy_codebot.schemas import load_json


def test_dry_run_creates_pine_artifacts(tmp_path: Path) -> None:
    out_dir = tmp_path / "pine-run"

    result = run_strategy(
        spec_path=Path("examples/specs/ma-crossover-pine.json"),
        prompt=None,
        mode="dry-run",
        out_dir=out_dir,
        record_harness=False,
    )

    assert result["status"] == "pass"
    assert (out_dir / "strategy-spec.json").exists()
    assert (out_dir / "pine" / "strategy.pine").exists()
    assert (out_dir / "manual-tradingview-checklist.md").exists()
    assert load_json(out_dir / "validation-report.json")["platform"] == "pine_v6"
    assert load_json(out_dir / "agent-run.json")["status"] == "pass"


def test_combined_target_creates_mql5_runner_design(tmp_path: Path) -> None:
    out_dir = tmp_path / "both-run"

    result = run_strategy(
        spec_path=Path("examples/specs/ma-crossover-both.json"),
        prompt=None,
        mode="dry-run",
        out_dir=out_dir,
        record_harness=False,
    )

    report = load_json(out_dir / "validation-report.json")
    assert result["status"] == "manual_required"
    assert report["platform"] == "both"
    assert (out_dir / "mql5" / "runner-design.md").exists()
    assert "MetaTrader 5" in (out_dir / "mql5" / "runner-design.md").read_text()


def test_invalid_mode_does_not_create_output_directory(tmp_path: Path) -> None:
    out_dir = tmp_path / "bad-mode"

    with pytest.raises(ValueError):
        run_strategy(
            spec_path=Path("examples/specs/ma-crossover-pine.json"),
            prompt=None,
            mode="bad",
            out_dir=out_dir,
            record_harness=False,
        )

    assert not out_dir.exists()
