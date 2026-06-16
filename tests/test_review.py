import asyncio
from pathlib import Path

import pytest
from jsonschema import ValidationError

from strategy_codebot.review import ReviewContext, run_parallel_review
from strategy_codebot.runner import run_strategy
from strategy_codebot.schemas import load_json, load_strategy_spec, validate_payload


def test_review_report_schema_accepts_valid_report(tmp_path: Path) -> None:
    out_dir = tmp_path / "review-schema"
    run_strategy(
        spec_path=Path("examples/specs/ma-crossover-pine.json"),
        prompt=None,
        mode="dry-run",
        out_dir=out_dir,
        review="parallel",
        record_harness=False,
    )

    validate_payload(load_json(out_dir / "review-report.json"), "review-report.schema.json")


def test_review_report_schema_rejects_malformed_reviewer_result() -> None:
    with pytest.raises(ValidationError):
        validate_payload(
            {
                "run_id": "bad",
                "created_at": "2026-06-16T00:00:00+00:00",
                "run_status": "completed",
                "decision": "approve",
                "reviewers": [{"role": "critic"}],
                "findings": [],
                "conflicts": [],
                "warnings": [],
                "next_actions": [],
            },
            "review-report.schema.json",
        )


def test_dry_run_parallel_review_returns_four_reviewers() -> None:
    spec = load_strategy_spec(Path("examples/specs/ma-crossover-pine.json"))
    validation = {
        "platform": "pine_v6",
        "status": "pass",
        "checks": [],
        "evidence": ["static-pine-validator"],
        "warnings": [],
        "next_actions": [],
    }

    report = asyncio.run(
        run_parallel_review(
            run_id="dry-review",
            spec=spec,
            validation=validation,
            pine_code="//@version=6\nstrategy(\"x\")\n",
            mql5_runner_design=None,
            mode="dry-run",
        )
    )

    assert report["run_status"] == "completed"
    assert {reviewer["role"] for reviewer in report["reviewers"]} == {
        "trading_analyst",
        "pine_specialist",
        "risk_reviewer",
        "critic",
    }


def test_pine_reviewer_preserves_failed_validation_status() -> None:
    spec = load_strategy_spec(Path("examples/specs/ma-crossover-pine.json"))

    report = asyncio.run(
        run_parallel_review(
            run_id="failed-pine-review",
            spec=spec,
            validation={
                "platform": "pine_v6",
                "status": "fail",
                "checks": [],
                "evidence": ["static-pine-validator"],
                "warnings": [],
                "next_actions": [],
            },
            pine_code="//@version=6\nstrategy(\"x\")\n",
            mql5_runner_design=None,
            mode="dry-run",
        )
    )

    pine_result = next(reviewer for reviewer in report["reviewers"] if reviewer["role"] == "pine_specialist")
    assert pine_result["status"] == "fail"
    assert report["run_status"] == "failed"
    assert report["decision"] == "changes_requested"


def test_reviewer_exception_yields_partial_report() -> None:
    spec = load_strategy_spec(Path("examples/specs/ma-crossover-pine.json"))

    def broken_reviewer(_: ReviewContext) -> dict[str, object]:
        raise RuntimeError("reviewer unavailable")

    report = asyncio.run(
        run_parallel_review(
            run_id="partial-review",
            spec=spec,
            validation={
                "platform": "pine_v6",
                "status": "pass",
                "checks": [],
                "evidence": [],
                "warnings": [],
                "next_actions": [],
            },
            pine_code="//@version=6\nstrategy(\"x\")\n",
            mql5_runner_design=None,
            mode="dry-run",
            reviewer_functions={"critic": broken_reviewer},
        )
    )

    assert report["run_status"] == "partial"
    assert report["decision"] == "manual_required"
    assert any("reviewer unavailable" in warning for warning in report["warnings"])


def test_malformed_reviewer_result_yields_partial_report() -> None:
    spec = load_strategy_spec(Path("examples/specs/ma-crossover-pine.json"))

    def malformed_reviewer(_: ReviewContext) -> dict[str, object]:
        return {
            "provider": "mock",
            "model": "bad-reviewer",
            "status": "approved",
            "findings": [{"message": "missing required fields"}],
            "evidence_refs": [],
            "warnings": [],
        }

    report = asyncio.run(
        run_parallel_review(
            run_id="malformed-review",
            spec=spec,
            validation={
                "platform": "pine_v6",
                "status": "pass",
                "checks": [],
                "evidence": [],
                "warnings": [],
                "next_actions": [],
            },
            pine_code="//@version=6\nstrategy(\"x\")\n",
            mql5_runner_design=None,
            mode="dry-run",
            reviewer_functions={"critic": malformed_reviewer},
        )
    )

    critic_result = next(reviewer for reviewer in report["reviewers"] if reviewer["role"] == "critic")
    assert critic_result["status"] == "error"
    assert report["run_status"] == "partial"
    assert report["decision"] == "manual_required"


def test_risk_reviewer_blocks_profit_and_live_trading_claims() -> None:
    spec = load_strategy_spec(Path("examples/specs/ma-crossover-pine.json"))
    spec["user_notes"] = "This should guarantee profit for live trading."

    report = asyncio.run(
        run_parallel_review(
            run_id="risk-review",
            spec=spec,
            validation={
                "platform": "pine_v6",
                "status": "pass",
                "checks": [],
                "evidence": [],
                "warnings": [],
                "next_actions": [],
            },
            pine_code="//@version=6\nstrategy(\"x\")\n",
            mql5_runner_design=None,
            mode="dry-run",
        )
    )

    assert report["decision"] == "blocked"
    assert any(finding["category"] == "risk_policy" for finding in report["findings"])


def test_mql5_target_keeps_manual_required_boundary(tmp_path: Path) -> None:
    out_dir = tmp_path / "both-review"
    run_strategy(
        spec_path=Path("examples/specs/ma-crossover-both.json"),
        prompt=None,
        mode="dry-run",
        out_dir=out_dir,
        review="parallel",
        record_harness=False,
    )

    report = load_json(out_dir / "review-report.json")
    assert report["run_status"] == "partial"
    assert report["decision"] == "manual_required"
    assert any(finding["category"] == "mql5_boundary" for finding in report["findings"])


def test_live_review_path_can_be_mocked_without_provider() -> None:
    spec = load_strategy_spec(Path("examples/specs/ma-crossover-pine.json"))

    def mocked_reviewer(context: ReviewContext) -> dict[str, object]:
        return {
            "provider": "mock",
            "model": context.model_registry["agents"]["critic"]["primary"],
            "status": "pass",
            "findings": [],
            "evidence_refs": ["validation-report.json"],
            "warnings": [],
        }

    report = asyncio.run(
        run_parallel_review(
            run_id="live-mocked",
            spec=spec,
            validation={
                "platform": "pine_v6",
                "status": "pass",
                "checks": [],
                "evidence": [],
                "warnings": [],
                "next_actions": [],
            },
            pine_code="//@version=6\nstrategy(\"x\")\n",
            mql5_runner_design=None,
            mode="live",
            reviewer_functions={role: mocked_reviewer for role in ("trading_analyst", "pine_specialist", "risk_reviewer", "critic")},
        )
    )

    assert report["run_status"] == "completed"
    assert {reviewer["provider"] for reviewer in report["reviewers"]} == {"mock"}
