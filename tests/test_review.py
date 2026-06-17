import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import ValidationError

from strategy_codebot.review import ReviewContext, run_parallel_review, write_review_report
from strategy_codebot.runner import run_strategy
from strategy_codebot.schemas import load_json, load_strategy_spec, validate_payload
from strategy_codebot.live import LiveRunOptions


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


def test_standalone_review_harness_trace_creates_detailed_linked_intake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    spec = load_strategy_spec(Path("examples/specs/ma-crossover-pine.json"))
    validation = {
        "platform": "pine_v6",
        "status": "pass",
        "checks": [],
        "evidence": ["static-pine-validator"],
        "warnings": [],
        "next_actions": [],
    }
    trace_commands = []
    intake_calls = []

    def fake_record_trace_intake(**kwargs):
        intake_calls.append(kwargs)
        return 123

    def fake_record_trace(command):
        trace_commands.append(command)

    monkeypatch.setattr("strategy_codebot.review.record_trace_intake", fake_record_trace_intake)
    monkeypatch.setattr("strategy_codebot.review.record_trace", fake_record_trace)

    report = write_review_report(
        run_id="standalone-review",
        spec=spec,
        validation=validation,
        pine_code="//@version=6\nstrategy(\"x\")\n",
        mql5_runner_design=None,
        mode="dry-run",
        out_path=tmp_path / "review-report.json",
        record_harness=True,
    )

    command = trace_commands[0]
    assert report["run_status"] == "completed"
    assert intake_calls[0]["input_type"] == "maintenance request"
    assert command[command.index("--intake") + 1] == "123"
    assert "--actions" in command
    assert "--read" in command
    assert "--errors" in command
    assert command[command.index("--errors") + 1] == "[]"
    assert "--friction" in command
    assert command[command.index("--friction") + 1] == "none"
    assert "--duration" in command
    assert "--tokens" in command
    assert "--decisions" in command


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


def test_live_review_uses_live_options_stage_models(monkeypatch, capsys) -> None:
    spec = load_strategy_spec(Path("examples/specs/ma-crossover-pine.json"))
    calls: list[dict[str, object]] = []

    def fake_completion(**kwargs):
        print("Provider List: endpoint warning")
        calls.append(kwargs)
        content = json.dumps(
            {
                "status": "approved",
                "findings": [
                    {
                        "reviewer": "critic",
                        "severity": "info",
                        "category": "routing",
                        "message": "routed",
                        "evidence_refs": ["validation-report.json"],
                        "recommendation": None,
                    }
                ],
                "evidence_refs": ["validation-report.json"],
                "warnings": [],
            }
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(completion=fake_completion))
    monkeypatch.setenv("OPENROUTER_API_BASE", "https://openrouter.example/api/v1")

    report = asyncio.run(
        run_parallel_review(
            run_id="live-routed",
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
            live_options=LiveRunOptions(
                cost_profile="cheap",
                model_stage_overrides={
                    "strategy_reasoning": "openrouter/google/gemini-2.5-flash",
                    "pine_code_generation": "openrouter/google/gemini-2.5-flash",
                    "balanced_review": "openrouter/google/gemini-2.5-flash",
                },
            ),
        )
    )

    assert report["run_status"] == "completed"
    assert report["decision"] == "approve"
    assert {reviewer["provider"] for reviewer in report["reviewers"]} == {"openrouter"}
    assert all("recommendation" not in finding for finding in report["findings"])
    assert {call["model"] for call in calls} == {"openrouter/google/gemini-2.5-flash"}
    assert {call["base_url"] for call in calls} == {"https://openrouter.example/api/v1"}
    assert {call["response_format"]["json_schema"]["name"] for call in calls} == {"strategy_codebot_reviewer_result"}
    assert "Provider List" not in capsys.readouterr().out
    assert all(reviewer["provider_warnings"] for reviewer in report["reviewers"])
