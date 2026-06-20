from __future__ import annotations

from pathlib import Path

from strategy_codebot.harness_types import FAILURE_PROVIDER_TIMEOUT, FAILURE_REVIEW_FAILED, STATUS_FAIL, STATUS_PASS, STATUS_SKIPPED
from strategy_codebot.live import (
    STAGE_BALANCED_REVIEW,
    STAGE_PINE_CODE_GENERATION,
    STAGE_REPAIR,
    STAGE_STRATEGY_CODING,
    STAGE_STRATEGY_REASONING,
)
from strategy_codebot.model_matrix import (
    GEMINI_FLASH_OPENROUTER,
    KIMI_K2_OPENROUTER,
    MINIMAX_M3_OPENROUTER,
    default_model_combos,
    run_model_combo_matrix,
)
from strategy_codebot.schemas import write_json


def _suite(path: Path) -> Path:
    path.write_text("name: suite\ncases:\n  - id: case\n    prompt: Create a Pine strategy\n", encoding="utf-8")
    return path


def _eval_report(*, suite_path: Path, status: str = STATUS_PASS, failure_class: str | None = None) -> dict:
    case = {
        "id": "case",
        "status": status,
        "validation_status": STATUS_PASS if status == STATUS_PASS else None,
        "latest_validation_ref": "validation-report.json" if status == STATUS_PASS else None,
        "failure_class": failure_class,
        "latency_ms": 120,
        "case_started_at": "2026-06-17T00:00:00+00:00",
        "case_completed_at": "2026-06-17T00:00:01+00:00",
        "case_duration_ms": 250,
        "total_usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, "cost_usd": 0.001},
        "repair_count": 0,
        "generation_gate": {"status": status},
        "production_gate": {"status": status},
        "quality_status": status,
        "quality_score": 95 if status == STATUS_PASS else None,
        "quality_blockers": [],
        "quality_warnings": [],
        "knowledge_context_ref": "knowledge-context.json" if status == STATUS_PASS else None,
        "knowledge_doc_ids": ["pine_v6_rules", "risk_policy"] if status == STATUS_PASS else [],
        "external_source_ids": ["tradingview-pine-strategies"] if status == STATUS_PASS else [],
        "stages": [{"stage": "strategy_coding", "model": "openrouter/test", "latency_ms": 120}],
    }
    return {
        "suite": suite_path.stem,
        "suite_path": str(suite_path),
        "status": STATUS_PASS if status == STATUS_PASS else STATUS_FAIL,
        "cases": [case],
        "otel_export_ref": str(suite_path.with_suffix(".otel.jsonl")),
    }


def _write_eval_report(out_dir: Path, report: dict) -> dict:
    write_json(out_dir / "eval-report.json", report)
    return report


def test_model_matrix_skips_combo_when_required_credential_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    smoke_suite = _suite(tmp_path / "smoke.yaml")
    full_suite = _suite(tmp_path / "full.yaml")

    report = run_model_combo_matrix(
        smoke_suite_path=smoke_suite,
        full_suite_path=full_suite,
        out_dir=tmp_path / "matrix",
        policy="enforce",
        combo_ids=["baseline_gemini_all"],
    )

    assert report["status"] == STATUS_FAIL
    assert report["recommended_combo"] is None
    assert report["combos"][0]["status"] == STATUS_SKIPPED
    assert "OPENROUTER_API_KEY" in report["combos"][0]["skip_reason"]


def test_model_matrix_runs_full_only_after_smoke_pass(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")
    smoke_suite = _suite(tmp_path / "smoke.yaml")
    full_suite = _suite(tmp_path / "full.yaml")
    calls: list[dict] = []

    def fake_run_live_eval(**kwargs):
        calls.append(kwargs)
        return _eval_report(suite_path=kwargs["suite_path"])

    monkeypatch.setattr("strategy_codebot.model_matrix.run_live_eval", fake_run_live_eval)

    report = run_model_combo_matrix(
        smoke_suite_path=smoke_suite,
        full_suite_path=full_suite,
        out_dir=tmp_path / "matrix",
        policy="enforce",
        combo_ids=["baseline_gemini_all"],
        run_full=True,
    )

    assert report["status"] == STATUS_PASS
    assert report["recommended_combo"] == "baseline_gemini_all"
    assert [call["suite_path"] for call in calls] == [smoke_suite, full_suite]
    assert calls[0]["concurrency"] == 1
    assert calls[0]["live_options"].model_stage_overrides["strategy_reasoning"] == GEMINI_FLASH_OPENROUTER
    assert calls[0]["live_options"].model_stage_overrides["repair"] == GEMINI_FLASH_OPENROUTER
    assert report["combos"][0]["smoke"]["accepted"] is True
    assert report["combos"][0]["full"]["accepted"] is True
    assert report["combos"][0]["smoke"]["latency_ms"] == {"avg": 120.0, "p95": 120.0, "max": 120.0}
    assert report["combos"][0]["smoke"]["max_case_duration_ms"] == 250.0
    assert report["combos"][0]["smoke"]["case_duration_ms"] == {"avg": 250.0, "p95": 250.0, "max": 250.0}
    assert report["combos"][0]["smoke"]["stalled_case_count"] == 0
    assert report["combos"][0]["smoke"]["total_usage"] == {
        "completion_tokens": 20,
        "cost_usd": 0.001,
        "prompt_tokens": 10,
        "total_tokens": 30,
    }
    assert report["combos"][0]["smoke"]["total_cost_usd"] == 0.001
    assert report["combos"][0]["smoke"]["avg_cost_usd"] == 0.001
    assert report["combos"][0]["smoke"]["quality_pass_rate"] == 1.0
    assert report["combos"][0]["smoke"]["avg_quality_score"] == 95.0
    assert report["combos"][0]["smoke"]["knowledge_context_case_count"] == 1
    assert report["combos"][0]["smoke"]["avg_internal_doc_count"] == 2.0
    assert report["combos"][0]["smoke"]["external_ref_count"] == 1
    assert (tmp_path / "matrix" / "model-health.json").exists()


def test_model_matrix_skips_full_when_smoke_gate_fails(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")
    smoke_suite = _suite(tmp_path / "smoke.yaml")
    full_suite = _suite(tmp_path / "full.yaml")
    calls: list[dict] = []

    def fake_run_live_eval(**kwargs):
        calls.append(kwargs)
        return _eval_report(suite_path=kwargs["suite_path"], status=STATUS_FAIL, failure_class=FAILURE_PROVIDER_TIMEOUT)

    monkeypatch.setattr("strategy_codebot.model_matrix.run_live_eval", fake_run_live_eval)

    report = run_model_combo_matrix(
        smoke_suite_path=smoke_suite,
        full_suite_path=full_suite,
        out_dir=tmp_path / "matrix",
        policy="enforce",
        combo_ids=["baseline_gemini_all"],
        run_full=True,
    )

    assert report["status"] == STATUS_FAIL
    assert len(calls) == 1
    assert report["combos"][0]["smoke"]["blocking_failure_classes"] == {FAILURE_PROVIDER_TIMEOUT: 1}
    assert report["combos"][0]["full"]["status"] == STATUS_SKIPPED
    assert report["combos"][0]["full"]["skip_reason"] == "smoke gate failed"


def test_model_matrix_static_gate_counts_only_final_artifact_cases(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")
    smoke_suite = _suite(tmp_path / "smoke.yaml")
    full_suite = _suite(tmp_path / "full.yaml")

    def fake_run_live_eval(**kwargs):
        return {
            "suite": "smoke",
            "suite_path": str(kwargs["suite_path"]),
            "status": STATUS_PASS,
            "cases": [
                    {
                        "id": "generated",
                        "status": STATUS_PASS,
                        "validation_status": STATUS_PASS,
                        "latest_validation_ref": "validation-report.json",
                        "repair_count": 0,
                        "generation_gate": {"status": STATUS_PASS},
                        "production_gate": {"status": STATUS_PASS},
                    },
                {
                    "id": "blocked",
                    "status": STATUS_PASS,
                    "outcome": "blocked",
                    "expected_outcome": "blocked",
                    "safety_gate": {"status": STATUS_PASS},
                    "generation_gate": {"status": STATUS_SKIPPED},
                    "production_gate": {"status": STATUS_SKIPPED},
                    "validation_status": None,
                    "latest_validation_ref": None,
                    "repair_count": 0,
                },
                    {
                        "id": "manual",
                        "status": STATUS_PASS,
                        "validation_status": "manual_required",
                        "latest_validation_ref": "validation-report.json",
                        "expected_statuses": ["manual_required"],
                        "repair_count": 0,
                        "generation_gate": {"status": STATUS_PASS},
                        "production_gate": {"status": STATUS_PASS},
                    },
            ],
        }

    monkeypatch.setattr("strategy_codebot.model_matrix.run_live_eval", fake_run_live_eval)

    report = run_model_combo_matrix(
        smoke_suite_path=smoke_suite,
        full_suite_path=full_suite,
        out_dir=tmp_path / "matrix",
        policy="enforce",
        combo_ids=["baseline_gemini_all"],
    )

    smoke = report["combos"][0]["smoke"]
    assert report["status"] == STATUS_PASS
    assert smoke["static_validation_pass_rate"] == 1.0
    assert smoke["final_artifact_case_count"] == 1
    assert smoke["safety_accepted"] is True
    assert smoke["safety_case_count"] == 1


def test_model_matrix_excludes_expected_blocked_from_artifact_gate_denominators(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")
    smoke_suite = _suite(tmp_path / "smoke.yaml")
    full_suite = _suite(tmp_path / "full.yaml")

    def fake_run_live_eval(**kwargs):
        return {
            "suite": "smoke",
            "suite_path": str(kwargs["suite_path"]),
            "status": STATUS_PASS,
            "cases": [
                {
                    "id": "generated",
                    "status": STATUS_PASS,
                    "validation_status": STATUS_PASS,
                    "latest_validation_ref": "validation-report.json",
                    "repair_count": 0,
                    "generation_gate": {"status": STATUS_PASS},
                    "production_gate": {"status": STATUS_PASS},
                },
                {
                    "id": "blocked",
                    "expected_outcome": "blocked",
                    "status": STATUS_PASS,
                    "safety_gate": {"status": STATUS_PASS},
                    "generation_gate": {"status": STATUS_SKIPPED},
                    "production_gate": {"status": STATUS_SKIPPED},
                },
            ],
        }

    monkeypatch.setattr("strategy_codebot.model_matrix.run_live_eval", fake_run_live_eval)

    report = run_model_combo_matrix(
        smoke_suite_path=smoke_suite,
        full_suite_path=full_suite,
        out_dir=tmp_path / "matrix",
        policy="enforce",
        combo_ids=["baseline_gemini_all"],
    )

    smoke = report["combos"][0]["smoke"]
    assert smoke["generation_case_count"] == 1
    assert smoke["generation_pass_rate"] == 1.0
    assert smoke["production_case_count"] == 1
    assert smoke["production_pass_rate"] == 1.0
    assert smoke["safety_case_count"] == 1
    assert smoke["safety_pass_rate"] == 1.0


def test_model_matrix_treats_manual_required_without_failures_as_static_clean(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")
    smoke_suite = _suite(tmp_path / "smoke.yaml")
    full_suite = _suite(tmp_path / "full.yaml")

    def fake_run_live_eval(**kwargs):
        return {
            "suite": "smoke",
            "suite_path": str(kwargs["suite_path"]),
            "status": STATUS_PASS,
            "cases": [
                {
                    "id": "manual-warning",
                    "status": STATUS_PASS,
                    "validation_status": "manual_required",
                    "validation_failures": [],
                    "latest_validation_ref": "validation-report.json",
                    "expected_statuses": [STATUS_PASS],
                    "repair_count": 0,
                    "generation_gate": {"status": STATUS_PASS},
                    "production_gate": {"status": STATUS_PASS},
                }
            ],
        }

    monkeypatch.setattr("strategy_codebot.model_matrix.run_live_eval", fake_run_live_eval)

    report = run_model_combo_matrix(
        smoke_suite_path=smoke_suite,
        full_suite_path=full_suite,
        out_dir=tmp_path / "matrix",
        policy="enforce",
        combo_ids=["baseline_gemini_all"],
    )

    smoke = report["combos"][0]["smoke"]
    assert smoke["static_validation_pass_rate"] == 1.0
    assert smoke["generation_accepted"] is True
    assert smoke["production_accepted"] is True


def test_model_matrix_counts_failed_case_failure_attribution(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")
    smoke_suite = _suite(tmp_path / "smoke.yaml")
    full_suite = _suite(tmp_path / "full.yaml")

    def fake_run_live_eval(**kwargs):
        return {
            "suite": "smoke",
            "suite_path": str(kwargs["suite_path"]),
            "status": STATUS_FAIL,
            "cases": [
                {
                    "id": "review",
                    "status": STATUS_FAIL,
                    "failure_class": None,
                    "failure_attribution": [{"failure_class": FAILURE_REVIEW_FAILED}],
                    "validation_status": STATUS_PASS,
                    "latest_validation_ref": "validation-report.json",
                    "repair_count": 0,
                }
            ],
        }

    monkeypatch.setattr("strategy_codebot.model_matrix.run_live_eval", fake_run_live_eval)

    report = run_model_combo_matrix(
        smoke_suite_path=smoke_suite,
        full_suite_path=full_suite,
        out_dir=tmp_path / "matrix",
        policy="enforce",
        combo_ids=["baseline_gemini_all"],
    )

    assert report["status"] == STATUS_FAIL
    assert report["combos"][0]["smoke"]["failure_classes"] == {FAILURE_REVIEW_FAILED: 1}


def test_model_matrix_separates_generation_and_production_gates(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")
    smoke_suite = _suite(tmp_path / "smoke.yaml")
    full_suite = _suite(tmp_path / "full.yaml")

    def fake_run_live_eval(**kwargs):
        return {
            "suite": "smoke",
            "suite_path": str(kwargs["suite_path"]),
            "status": STATUS_PASS,
            "cases": [
                {
                    "id": "review-warning",
                    "status": STATUS_PASS,
                    "validation_status": STATUS_PASS,
                    "latest_validation_ref": "validation-report.json",
                    "repair_count": 0,
                    "generation_gate": {"status": STATUS_PASS},
                    "production_gate": {"status": STATUS_FAIL, "required_fixes": ["inspect sizing"]},
                }
            ],
        }

    monkeypatch.setattr("strategy_codebot.model_matrix.run_live_eval", fake_run_live_eval)

    report = run_model_combo_matrix(
        smoke_suite_path=smoke_suite,
        full_suite_path=full_suite,
        out_dir=tmp_path / "matrix",
        policy="enforce",
        combo_ids=["baseline_gemini_all"],
    )

    smoke = report["combos"][0]["smoke"]
    assert report["status"] == STATUS_FAIL
    assert smoke["accepted"] is False
    assert smoke["generation_accepted"] is True
    assert smoke["production_accepted"] is False
    assert smoke["generation_pass_rate"] == 1.0
    assert smoke["production_pass_rate"] == 0.0


def test_model_matrix_resolves_replacement_combos() -> None:
    combos = {combo.combo_id: combo for combo in default_model_combos()}

    kimi_gemini = combos["hybrid_gemini_kimi_gemini"].model_stage_overrides
    assert kimi_gemini == {
        STAGE_STRATEGY_REASONING: GEMINI_FLASH_OPENROUTER,
        STAGE_STRATEGY_CODING: KIMI_K2_OPENROUTER,
        STAGE_PINE_CODE_GENERATION: GEMINI_FLASH_OPENROUTER,
        STAGE_BALANCED_REVIEW: GEMINI_FLASH_OPENROUTER,
        STAGE_REPAIR: GEMINI_FLASH_OPENROUTER,
    }

    minimax_gemini = combos["hybrid_gemini_minimax_gemini"].model_stage_overrides
    assert minimax_gemini[STAGE_STRATEGY_CODING] == MINIMAX_M3_OPENROUTER
    assert minimax_gemini[STAGE_PINE_CODE_GENERATION] == GEMINI_FLASH_OPENROUTER
    assert minimax_gemini[STAGE_REPAIR] == GEMINI_FLASH_OPENROUTER

    kimi_only = combos["hybrid_gemini_kimi_only"].model_stage_overrides
    assert kimi_only[STAGE_STRATEGY_CODING] == KIMI_K2_OPENROUTER
    assert kimi_only[STAGE_PINE_CODE_GENERATION] == KIMI_K2_OPENROUTER
    assert kimi_only[STAGE_REPAIR] == KIMI_K2_OPENROUTER


def test_model_matrix_marks_missing_case_eval_as_stalled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")
    smoke_suite = tmp_path / "smoke.yaml"
    smoke_suite.write_text(
        "name: smoke\ncases:\n  - id: completed\n    prompt: Create a Pine strategy\n  - id: missing\n    prompt: Create another Pine strategy\n",
        encoding="utf-8",
    )
    full_suite = _suite(tmp_path / "full.yaml")

    def fake_run_live_eval(**kwargs):
        completed_dir = kwargs["out_dir"] / "cases" / "completed"
        completed_dir.mkdir(parents=True, exist_ok=True)
        write_json(
            completed_dir / "case-eval.json",
            {
                "id": "completed",
                "status": STATUS_PASS,
                "validation_status": STATUS_PASS,
                "latest_validation_ref": "validation-report.json",
                "repair_count": 0,
                "generation_gate": {"status": STATUS_PASS},
                "production_gate": {"status": STATUS_PASS},
                "case_started_at": "2026-06-17T00:00:00+00:00",
                "case_completed_at": "2026-06-17T00:00:01+00:00",
                "case_duration_ms": 1000,
            },
        )
        raise RuntimeError("matrix eval interrupted")

    monkeypatch.setattr("strategy_codebot.model_matrix.run_live_eval", fake_run_live_eval)

    report = run_model_combo_matrix(
        smoke_suite_path=smoke_suite,
        full_suite_path=full_suite,
        out_dir=tmp_path / "matrix",
        policy="enforce",
        combo_ids=["baseline_gemini_all"],
    )

    smoke = report["combos"][0]["smoke"]
    assert report["status"] == STATUS_FAIL
    assert smoke["accepted"] is False
    assert smoke["generation_accepted"] is False
    assert smoke["stalled_case_count"] == 1
    assert smoke["max_case_duration_ms"] == 1000.0
    assert smoke["artifact_missing_count"] == 1
    assert (tmp_path / "matrix" / "baseline_gemini_all" / "smoke" / "eval-report.json").exists()


def test_model_matrix_quality_blocker_prevents_acceptance(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")
    smoke_suite = _suite(tmp_path / "smoke.yaml")
    full_suite = _suite(tmp_path / "full.yaml")

    def fake_run_live_eval(**kwargs):
        report = _eval_report(suite_path=kwargs["suite_path"])
        report["cases"][0]["quality_status"] = STATUS_FAIL
        report["cases"][0]["quality_score"] = 60
        report["cases"][0]["quality_blockers"] = [{"category": "exit_logic", "severity": "blocker"}]
        return _write_eval_report(kwargs["out_dir"], report)

    monkeypatch.setattr("strategy_codebot.model_matrix.run_live_eval", fake_run_live_eval)

    report = run_model_combo_matrix(
        smoke_suite_path=smoke_suite,
        full_suite_path=full_suite,
        out_dir=tmp_path / "matrix",
        policy="enforce",
        combo_ids=["baseline_gemini_all"],
    )

    smoke = report["combos"][0]["smoke"]
    assert report["status"] == STATUS_FAIL
    assert smoke["generation_accepted"] is False
    assert smoke["quality_blocker_count"] == 1
    assert smoke["quality_pass_rate"] == 0.0


def test_model_health_marks_timeout_route_unstable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")
    smoke_suite = _suite(tmp_path / "smoke.yaml")
    full_suite = _suite(tmp_path / "full.yaml")

    def fake_run_live_eval(**kwargs):
        return _write_eval_report(kwargs["out_dir"], {
            "suite": "smoke",
            "suite_path": str(kwargs["suite_path"]),
            "status": STATUS_FAIL,
            "cases": [
                {
                    "id": "case",
                    "status": STATUS_FAIL,
                    "failure_class": FAILURE_PROVIDER_TIMEOUT,
                    "validation_status": None,
                    "repair_count": 0,
                    "quality_status": None,
                    "quality_blockers": [],
                    "cooldown_skips": [
                        {
                            "stage": "pine_code_generation",
                            "model": "openrouter/qwen/qwen3-coder",
                            "provider": "openrouter",
                            "status": STATUS_SKIPPED,
                            "failure_class": FAILURE_PROVIDER_TIMEOUT,
                            "skip_reason": "route_cooldown",
                            "consecutive_failure_count": 1,
                        }
                    ],
                    "route_health_snapshot": [
                        {
                            "stage": "pine_code_generation",
                            "model": "openrouter/qwen/qwen3-coder",
                            "provider": "openrouter",
                            "status": "cooldown",
                            "cooldown_count": 1,
                            "consecutive_failure_max": 1,
                            "timeout_count": 1,
                        }
                    ],
                    "stages": [{"stage": "pine_code_generation", "model": "openrouter/qwen/qwen3-coder", "latency_ms": 90_000}],
                }
            ],
        })

    monkeypatch.setattr("strategy_codebot.model_matrix.run_live_eval", fake_run_live_eval)

    run_model_combo_matrix(
        smoke_suite_path=smoke_suite,
        full_suite_path=full_suite,
        out_dir=tmp_path / "matrix",
        policy="enforce",
        combo_ids=["baseline_gemini_all"],
    )

    health = (tmp_path / "matrix" / "model-health.json").read_text(encoding="utf-8")
    assert '"status": "unstable"' in health
    assert '"route_status": "unstable"' in health
    assert '"cooldown_count": 2' in health
    assert '"timeout_rate":' in health
    assert '"consecutive_failure_max": 1' in health
    assert "openrouter/qwen/qwen3-coder" in health


def test_model_matrix_tier_mode_runs_user_tier_routes(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")
    smoke_suite = _suite(tmp_path / "smoke.yaml")
    full_suite = _suite(tmp_path / "full.yaml")
    captured_tiers: list[str] = []

    def fake_run_live_eval(**kwargs):
        captured_tiers.append(kwargs["live_options"].user_tier)
        return _write_eval_report(kwargs["out_dir"], _eval_report(suite_path=kwargs["suite_path"]))

    monkeypatch.setattr("strategy_codebot.model_matrix.run_live_eval", fake_run_live_eval)

    report = run_model_combo_matrix(
        smoke_suite_path=smoke_suite,
        full_suite_path=full_suite,
        out_dir=tmp_path / "matrix",
        policy="enforce",
        matrix_mode="tier",
        tier_ids=["free", "paid_low"],
    )

    assert report["mode"] == "tier"
    assert report["recommended_tier"] in {"free", "paid_low"}
    assert [tier["user_tier"] for tier in report["tiers"]] == ["free", "paid_low"]
    assert captured_tiers == ["free", "paid_low"]
