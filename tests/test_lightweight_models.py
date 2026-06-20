from __future__ import annotations

from pathlib import Path
from typing import Any

from strategy_codebot.lightweight_models import build_model_candidate_matrix
from strategy_codebot.schemas import write_json


def test_model_candidate_matrix_skips_missing_vercel_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("VERCEL_AI_GATEWAY_API_KEY", raising=False)

    report = build_model_candidate_matrix(
        suite=Path("examples/evals/price-action-smoke.yaml"),
        out_root=tmp_path / "matrix",
        out=tmp_path / "matrix.json",
        stages=["none"],
        candidates=["pine_code_generation=vercel_ai_gateway/google/gemini-2.5-flash-lite"],
        fetch_catalog=False,
    )

    candidate = report["candidates"][0]
    assert candidate["status"] == "skipped"
    assert candidate["skip_reason"] == "missing_credentials"
    assert candidate["missing_envs"] == ["VERCEL_AI_GATEWAY_API_KEY"]
    assert candidate["promotion_eligible"] is False


def test_model_candidate_matrix_marks_stable_candidate_eligible(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")

    def fake_eval_runner(**kwargs: Any) -> dict[str, Any]:
        out_dir = kwargs["out_dir"]
        case_dir = out_dir / "cases" / "case-a"
        case_dir.mkdir(parents=True)
        write_json(
            case_dir / "live-workflow-trace.json",
            {
                "attempts": [
                    {
                        "stage": "pine_code_generation",
                        "status": "pass",
                        "stage_total_ms": 1200,
                        "provider_call_ratio": 0.98,
                    }
                ]
            },
        )
        report = {
            "status": "pass",
            "case_count": 1,
            "failed": 0,
            "cases": [
                {
                    "case_id": "case-a",
                    "status": "pass",
                    "validation_status": "pass",
                    "quality_status": "pass",
                    "context_contract": {"budget_warnings": []},
                }
            ],
        }
        write_json(out_dir / "eval-report.json", report)
        return report

    report = build_model_candidate_matrix(
        suite=Path("examples/evals/price-action-smoke.yaml"),
        out_root=tmp_path / "matrix",
        out=tmp_path / "matrix.json",
        runs=2,
        stages=["none"],
        candidates=["pine_code_generation=openrouter/qwen/qwen3-coder-next"],
        fetch_catalog=False,
        eval_runner=fake_eval_runner,
    )

    candidate = report["candidates"][0]
    assert candidate["status"] == "pass"
    assert candidate["promotion_eligible"] is True
    assert candidate["p95_latency_ms"] == 1200
    assert report["promotion_recommendations"]["pine_code_generation"]["recommended_route"] == "openrouter/qwen/qwen3-coder-next"
