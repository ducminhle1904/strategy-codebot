from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
from typing import Any

import yaml

from strategy_codebot.evals import EVAL_REPORT_PATH, run_live_eval
from strategy_codebot.harness_types import (
    FAILURE_ARTIFACT_MISSING,
    FAILURE_POLICY_VIOLATION,
    FAILURE_PROVIDER_NOT_FOUND,
    FAILURE_PROVIDER_TIMEOUT,
    FAILURE_SCHEMA_INVALID,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_SKIPPED,
)
from strategy_codebot.live import (
    COST_PROFILE_CHEAP,
    COST_PROFILE_QUALITY,
    STAGE_BALANCED_REVIEW,
    STAGE_PINE_CODE_GENERATION,
    STAGE_REPAIR,
    STAGE_STRATEGY_CODING,
    STAGE_STRATEGY_REASONING,
    WORKFLOW_MULTI_AGENT,
    LiveRunOptions,
)
from strategy_codebot.paths import ensure_dir, repo_root, resolve_repo_path
from strategy_codebot.schemas import write_json

MODEL_MATRIX_REPORT_PATH = "model-matrix-report.json"
MODEL_HEALTH_REPORT_PATH = "model-health.json"

GEMINI_FLASH_OPENROUTER = "openrouter/google/gemini-2.5-flash"
KIMI_K2_OPENROUTER = "openrouter/moonshotai/kimi-k2.5"
MINIMAX_M3_OPENROUTER = "openrouter/minimax/minimax-m3"
QWEN_CODER_OPENROUTER = "openrouter/qwen/qwen3-coder"

SMOKE_PASS_RATE = 1.0
FULL_PASS_RATE = 0.9
STATIC_VALIDATION_PASS_RATE = 1.0
MAX_AVG_REPAIR_COUNT = 1.0
BLOCKING_FAILURE_CLASSES = {
    FAILURE_PROVIDER_TIMEOUT,
    FAILURE_PROVIDER_NOT_FOUND,
    FAILURE_SCHEMA_INVALID,
    FAILURE_POLICY_VIOLATION,
}


@dataclass(frozen=True)
class ModelCombo:
    combo_id: str
    description: str
    cost_profile: str
    model_stage_overrides: dict[str, str]
    required_env_any: tuple[str, ...] = ()
    required_env_all: tuple[str, ...] = ()

    def live_options(self, *, save_raw_provider: bool, knowledge_context: str = "auto") -> LiveRunOptions:
        return LiveRunOptions(
            workflow=WORKFLOW_MULTI_AGENT,
            cost_profile=self.cost_profile,
            model_stage_overrides=dict(self.model_stage_overrides),
            save_raw_provider=save_raw_provider,
            knowledge_context=knowledge_context,
        )


def default_model_combos() -> list[ModelCombo]:
    return [
        ModelCombo(
            combo_id="baseline_gemini_all",
            description="OpenRouter Gemini 2.5 Flash on every live workflow stage.",
            cost_profile=COST_PROFILE_CHEAP,
            model_stage_overrides={
                STAGE_STRATEGY_REASONING: GEMINI_FLASH_OPENROUTER,
                STAGE_STRATEGY_CODING: GEMINI_FLASH_OPENROUTER,
                STAGE_PINE_CODE_GENERATION: GEMINI_FLASH_OPENROUTER,
                STAGE_BALANCED_REVIEW: GEMINI_FLASH_OPENROUTER,
                STAGE_REPAIR: GEMINI_FLASH_OPENROUTER,
            },
            required_env_all=("OPENROUTER_API_KEY",),
        ),
        ModelCombo(
            combo_id="cheap_default_current",
            description="Current OpenRouter cheap-quality registry mapping.",
            cost_profile=COST_PROFILE_CHEAP,
            model_stage_overrides={},
            required_env_all=("OPENROUTER_API_KEY",),
        ),
        ModelCombo(
            combo_id="hybrid_gemini_review",
            description="Gemini for reasoning/review with cheap registry coding and Pine stages.",
            cost_profile=COST_PROFILE_CHEAP,
            model_stage_overrides={
                STAGE_STRATEGY_REASONING: GEMINI_FLASH_OPENROUTER,
                STAGE_BALANCED_REVIEW: GEMINI_FLASH_OPENROUTER,
            },
            required_env_all=("OPENROUTER_API_KEY",),
        ),
        ModelCombo(
            combo_id="hybrid_gemini_reasoning_review",
            description="Gemini for reasoning/review, Kimi for spec coding, and Qwen Coder for Pine/repair.",
            cost_profile=COST_PROFILE_CHEAP,
            model_stage_overrides={
                STAGE_STRATEGY_REASONING: GEMINI_FLASH_OPENROUTER,
                STAGE_STRATEGY_CODING: KIMI_K2_OPENROUTER,
                STAGE_PINE_CODE_GENERATION: QWEN_CODER_OPENROUTER,
                STAGE_BALANCED_REVIEW: GEMINI_FLASH_OPENROUTER,
                STAGE_REPAIR: QWEN_CODER_OPENROUTER,
            },
            required_env_all=("OPENROUTER_API_KEY",),
        ),
        ModelCombo(
            combo_id="hybrid_gemini_kimi_gemini",
            description="Gemini for reasoning/review/Pine/repair with Kimi only for spec coding.",
            cost_profile=COST_PROFILE_CHEAP,
            model_stage_overrides={
                STAGE_STRATEGY_REASONING: GEMINI_FLASH_OPENROUTER,
                STAGE_STRATEGY_CODING: KIMI_K2_OPENROUTER,
                STAGE_PINE_CODE_GENERATION: GEMINI_FLASH_OPENROUTER,
                STAGE_BALANCED_REVIEW: GEMINI_FLASH_OPENROUTER,
                STAGE_REPAIR: GEMINI_FLASH_OPENROUTER,
            },
            required_env_all=("OPENROUTER_API_KEY",),
        ),
        ModelCombo(
            combo_id="hybrid_gemini_minimax_gemini",
            description="Gemini for reasoning/review/Pine/repair with MiniMax M3 only for spec coding.",
            cost_profile=COST_PROFILE_CHEAP,
            model_stage_overrides={
                STAGE_STRATEGY_REASONING: GEMINI_FLASH_OPENROUTER,
                STAGE_STRATEGY_CODING: MINIMAX_M3_OPENROUTER,
                STAGE_PINE_CODE_GENERATION: GEMINI_FLASH_OPENROUTER,
                STAGE_BALANCED_REVIEW: GEMINI_FLASH_OPENROUTER,
                STAGE_REPAIR: GEMINI_FLASH_OPENROUTER,
            },
            required_env_all=("OPENROUTER_API_KEY",),
        ),
        ModelCombo(
            combo_id="hybrid_gemini_kimi_only",
            description="Gemini for reasoning/review with Kimi for coding/Pine/repair.",
            cost_profile=COST_PROFILE_CHEAP,
            model_stage_overrides={
                STAGE_STRATEGY_REASONING: GEMINI_FLASH_OPENROUTER,
                STAGE_STRATEGY_CODING: KIMI_K2_OPENROUTER,
                STAGE_PINE_CODE_GENERATION: KIMI_K2_OPENROUTER,
                STAGE_BALANCED_REVIEW: GEMINI_FLASH_OPENROUTER,
                STAGE_REPAIR: KIMI_K2_OPENROUTER,
            },
            required_env_all=("OPENROUTER_API_KEY",),
        ),
        ModelCombo(
            combo_id="quality_profile",
            description="Quality-first registry profile using configured provider fallbacks.",
            cost_profile=COST_PROFILE_QUALITY,
            model_stage_overrides={},
            required_env_any=("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY"),
        ),
    ]


def run_model_combo_matrix(
    *,
    smoke_suite_path: Path,
    full_suite_path: Path,
    out_dir: Path,
    policy: str,
    model_registry: Path | None = None,
    combo_ids: list[str] | None = None,
    run_full: bool = False,
    save_raw_provider: bool = True,
    concurrency: int = 1,
    case_timeout_seconds: int | None = 300,
    knowledge_context: str = "auto",
) -> dict[str, Any]:
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    smoke_suite = resolve_repo_path(smoke_suite_path)
    full_suite = resolve_repo_path(full_suite_path)
    registry_path = resolve_repo_path(model_registry or repo_root() / "configs" / "model-registry.example.yaml")
    ensure_dir(out_dir)

    combos = _select_combos(combo_ids)
    combo_reports = []
    for combo in combos:
        combo_reports.append(
            _run_combo(
                combo=combo,
                smoke_suite=smoke_suite,
                full_suite=full_suite,
                out_dir=out_dir / combo.combo_id,
                policy=policy,
                model_registry=registry_path,
                run_full=run_full,
                save_raw_provider=save_raw_provider,
                concurrency=concurrency,
                case_timeout_seconds=case_timeout_seconds,
                knowledge_context=knowledge_context,
            )
        )

    accepted = [combo for combo in combo_reports if combo.get("accepted")]
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "status": STATUS_PASS if accepted else STATUS_FAIL,
        "policy": policy,
        "smoke_suite": str(smoke_suite),
        "full_suite": str(full_suite),
        "model_registry": str(registry_path),
        "run_full": run_full,
        "concurrency": concurrency,
        "case_timeout_seconds": case_timeout_seconds,
        "knowledge_context": knowledge_context,
        "acceptance": {
            "smoke_min_pass_rate": SMOKE_PASS_RATE,
            "full_min_pass_rate": FULL_PASS_RATE,
            "static_validation_pass_rate": STATIC_VALIDATION_PASS_RATE,
            "max_avg_repair_count": MAX_AVG_REPAIR_COUNT,
            "blocking_failure_classes": sorted(BLOCKING_FAILURE_CLASSES),
        },
        "recommended_combo": _recommended_combo_id(combo_reports),
        "model_health_ref": MODEL_HEALTH_REPORT_PATH,
        "combos": combo_reports,
    }
    write_json(out_dir / MODEL_HEALTH_REPORT_PATH, _model_health_scorecard(out_dir, combo_reports))
    write_json(out_dir / MODEL_MATRIX_REPORT_PATH, report)
    return report


def _select_combos(combo_ids: list[str] | None) -> list[ModelCombo]:
    combos = default_model_combos()
    if not combo_ids:
        return combos
    by_id = {combo.combo_id: combo for combo in combos}
    unknown = [combo_id for combo_id in combo_ids if combo_id not in by_id]
    if unknown:
        valid = ", ".join(sorted(by_id))
        raise ValueError(f"Unknown model combo(s): {', '.join(unknown)}. Valid combos: {valid}")
    return [by_id[combo_id] for combo_id in combo_ids]


def _run_combo(
    *,
    combo: ModelCombo,
    smoke_suite: Path,
    full_suite: Path,
    out_dir: Path,
    policy: str,
    model_registry: Path,
    run_full: bool,
    save_raw_provider: bool,
    concurrency: int,
    case_timeout_seconds: int | None,
    knowledge_context: str,
) -> dict[str, Any]:
    ensure_dir(out_dir)
    missing_env = _missing_required_env(combo)
    report: dict[str, Any] = {
        "id": combo.combo_id,
        "description": combo.description,
        "status": STATUS_SKIPPED if missing_env else STATUS_FAIL,
        "accepted": False,
        "skip_reason": None,
        "cost_profile": combo.cost_profile,
        "model_stage_overrides": combo.model_stage_overrides,
        "required_env_any": list(combo.required_env_any),
        "required_env_all": list(combo.required_env_all),
        "out_dir": str(out_dir),
        "smoke": None,
        "full": None,
    }
    if missing_env:
        report["skip_reason"] = f"missing credential env: {', '.join(missing_env)}"
        return report

    options = combo.live_options(save_raw_provider=save_raw_provider, knowledge_context=knowledge_context)
    smoke_report = _run_live_eval_for_matrix(
        suite_path=smoke_suite,
        out_dir=out_dir / "smoke",
        policy=policy,
        model_registry=model_registry,
        live_options=options,
        otel_export=out_dir / "smoke" / "otel.jsonl",
        concurrency=concurrency,
        case_timeout_seconds=case_timeout_seconds,
    )
    smoke_summary = _summarize_eval_report(smoke_report, tier="smoke")
    report["smoke"] = smoke_summary
    report["status"] = STATUS_PASS if smoke_summary["accepted"] else STATUS_FAIL
    report["accepted"] = smoke_summary["accepted"]

    if not run_full:
        return report
    if not smoke_summary["accepted"]:
        report["full"] = {"status": STATUS_SKIPPED, "accepted": False, "skip_reason": "smoke gate failed"}
        report["accepted"] = False
        return report

    full_report = _run_live_eval_for_matrix(
        suite_path=full_suite,
        out_dir=out_dir / "full",
        policy=policy,
        model_registry=model_registry,
        live_options=options,
        otel_export=out_dir / "full" / "otel.jsonl",
        concurrency=concurrency,
        case_timeout_seconds=case_timeout_seconds,
    )
    full_summary = _summarize_eval_report(full_report, tier="full")
    report["full"] = full_summary
    report["status"] = STATUS_PASS if full_summary["accepted"] else STATUS_FAIL
    report["accepted"] = full_summary["accepted"]
    return report


def _run_live_eval_for_matrix(
    *,
    suite_path: Path,
    out_dir: Path,
    policy: str,
    model_registry: Path,
    live_options: LiveRunOptions,
    otel_export: Path,
    concurrency: int,
    case_timeout_seconds: int | None,
) -> dict[str, Any]:
    try:
        return run_live_eval(
            suite_path=suite_path,
            out_dir=out_dir,
            policy=policy,
            model_registry=model_registry,
            live_options=live_options,
            otel_export=otel_export,
            concurrency=concurrency,
            case_timeout_seconds=case_timeout_seconds,
        )
    except Exception as exc:
        report = _synthetic_stalled_eval_report(suite_path=suite_path, out_dir=out_dir, exc=exc)
        write_json(out_dir / EVAL_REPORT_PATH, report)
        return report


def _synthetic_stalled_eval_report(*, suite_path: Path, out_dir: Path, exc: Exception) -> dict[str, Any]:
    ensure_dir(out_dir)
    suite = yaml.safe_load(suite_path.read_text(encoding="utf-8"))
    suite_cases = suite.get("cases", []) if isinstance(suite, dict) else []
    cases = []
    for case in suite_cases:
        case_id = _case_id(case)
        case_dir = out_dir / "cases" / case_id
        case_eval_path = case_dir / "case-eval.json"
        if case_eval_path.exists():
            loaded = yaml.safe_load(case_eval_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cases.append(loaded)
                continue
        cases.append(_stalled_case_report(case=case, case_id=case_id, case_dir=case_dir, exc=exc))
    return {
        "suite": suite.get("name", suite_path.stem) if isinstance(suite, dict) else suite_path.stem,
        "suite_path": str(suite_path),
        "created_at": datetime.now(UTC).isoformat(),
        "status": STATUS_FAIL,
        "failure_reason": str(exc),
        "cases": cases,
    }


def _stalled_case_report(*, case: dict[str, Any], case_id: str, case_dir: Path, exc: Exception) -> dict[str, Any]:
    return {
        "id": case_id,
        "name": case.get("name", case_id),
        "expected_outcome": case.get("expected_outcome", STATUS_PASS),
        "expected_statuses": case.get("expected_statuses", [STATUS_PASS]),
        "run_dir": str(case_dir),
        "status": STATUS_FAIL,
        "outcome": "stalled",
        "failure_reason": str(exc),
        "failure_class": FAILURE_ARTIFACT_MISSING,
        "failure_attribution": [
            {
                "failure_class": FAILURE_ARTIFACT_MISSING,
                "details": "Missing case-eval.json after matrix eval failure.",
            }
        ],
        "validation_status": None,
        "validation_failures": [],
        "validation_warnings": [],
        "latest_validation_ref": None,
        "repair_count": 0,
        "generation_gate": {"status": STATUS_FAIL, "reason": "stalled"},
        "production_gate": {"status": STATUS_FAIL, "reason": "stalled"},
        "case_started_at": None,
        "case_completed_at": None,
        "case_duration_ms": None,
    }


def _missing_required_env(combo: ModelCombo) -> list[str]:
    missing_all = [name for name in combo.required_env_all if not os.getenv(name)]
    if missing_all:
        return missing_all
    if combo.required_env_any and not any(os.getenv(name) for name in combo.required_env_any):
        return list(combo.required_env_any)
    return []


def _summarize_eval_report(report: dict[str, Any], *, tier: str) -> dict[str, Any]:
    cases = report.get("cases", [])
    case_count = len(cases)
    passed = sum(1 for case in cases if case.get("status") == STATUS_PASS)
    pass_rate = passed / case_count if case_count else 0.0
    safety_cases = [case for case in cases if _is_expected_blocked(case)]
    artifact_cases = [case for case in cases if not _is_expected_blocked(case)]
    safety_passed = sum(1 for case in safety_cases if _case_gate_status(case, "safety_gate", fallback=case.get("status")) == STATUS_PASS)
    safety_pass_rate = safety_passed / len(safety_cases) if safety_cases else 1.0
    generation_passed = sum(1 for case in artifact_cases if _case_gate_status(case, "generation_gate", fallback=case.get("status")) == STATUS_PASS)
    generation_pass_rate = generation_passed / len(artifact_cases) if artifact_cases else 0.0
    production_passed = sum(1 for case in artifact_cases if _case_gate_status(case, "production_gate") == STATUS_PASS)
    production_pass_rate = production_passed / len(artifact_cases) if artifact_cases else 0.0
    final_artifact_cases = [
        case
        for case in cases
        if case.get("latest_validation_ref") == "validation-report.json"
        and STATUS_PASS in case.get("expected_statuses", [STATUS_PASS])
    ]
    validation_passed = sum(1 for case in final_artifact_cases if _case_validation_allows_artifact(case))
    static_validation_pass_rate = validation_passed / len(final_artifact_cases) if final_artifact_cases else 1.0
    failure_classes: Counter[str] = Counter()
    for case in cases:
        if case.get("status") != STATUS_PASS:
            failure_classes.update(_case_failure_classes(case))
    blocking_failures = {
        failure_class: count
        for failure_class, count in sorted(failure_classes.items())
        if failure_class in BLOCKING_FAILURE_CLASSES
    }
    artifact_missing_count = failure_classes.get(FAILURE_ARTIFACT_MISSING, 0)
    repair_counts = [int(case.get("repair_count") or 0) for case in artifact_cases]
    avg_repair_count = sum(repair_counts) / len(artifact_cases) if artifact_cases else 0.0
    latency_values = _case_latency_values(cases)
    duration_values = _case_duration_values(cases)
    stalled_case_count = sum(1 for case in cases if _case_is_stalled(case))
    total_usage = _sum_case_usage(cases)
    total_cost_usd = _case_total_cost_usd(cases)
    quality_cases = [case for case in artifact_cases if case.get("quality_status") is not None]
    quality_blocker_count = sum(len(case.get("quality_blockers") or []) for case in artifact_cases)
    quality_passed = sum(1 for case in quality_cases if case.get("quality_status") == STATUS_PASS and not case.get("quality_blockers"))
    quality_pass_rate = quality_passed / len(quality_cases) if quality_cases else 1.0
    quality_scores = [float(case["quality_score"]) for case in quality_cases if isinstance(case.get("quality_score"), int | float)]
    knowledge_cases = [case for case in artifact_cases if case.get("knowledge_context_ref")]
    internal_doc_counts = [len(case.get("knowledge_doc_ids") or []) for case in knowledge_cases]
    external_source_ids = sorted({source_id for case in knowledge_cases for source_id in (case.get("external_source_ids") or [])})
    min_pass_rate = SMOKE_PASS_RATE if tier == "smoke" else FULL_PASS_RATE
    generation_accepted = (
        len(artifact_cases) > 0
        and generation_pass_rate >= min_pass_rate
        and static_validation_pass_rate >= STATIC_VALIDATION_PASS_RATE
        and artifact_missing_count == 0
        and stalled_case_count == 0
        and quality_blocker_count == 0
    )
    safety_accepted = safety_pass_rate >= 1.0
    production_accepted = (
        generation_accepted
        and safety_accepted
        and production_pass_rate >= min_pass_rate
        and not blocking_failures
        and avg_repair_count <= MAX_AVG_REPAIR_COUNT
        and stalled_case_count == 0
    )
    return {
        "status": report.get("status", STATUS_FAIL),
        "accepted": production_accepted,
        "generation_accepted": generation_accepted,
        "production_accepted": production_accepted,
        "safety_accepted": safety_accepted,
        "suite": report.get("suite"),
        "eval_report_ref": EVAL_REPORT_PATH,
        "otel_export_ref": report.get("otel_export_ref"),
        "case_count": case_count,
        "passed": passed,
        "failed": case_count - passed,
        "pass_rate": pass_rate,
        "generation_passed": generation_passed,
        "generation_pass_rate": generation_pass_rate,
        "generation_case_count": len(artifact_cases),
        "production_passed": production_passed,
        "production_pass_rate": production_pass_rate,
        "production_case_count": len(artifact_cases),
        "safety_passed": safety_passed,
        "safety_pass_rate": safety_pass_rate,
        "safety_case_count": len(safety_cases),
        "min_pass_rate": min_pass_rate,
        "static_validation_pass_rate": static_validation_pass_rate,
        "final_artifact_case_count": len(final_artifact_cases),
        "avg_repair_count": avg_repair_count,
        "quality_pass_rate": quality_pass_rate,
        "avg_quality_score": sum(quality_scores) / len(quality_scores) if quality_scores else None,
        "quality_blocker_count": quality_blocker_count,
        "knowledge_context_case_count": len(knowledge_cases),
        "avg_internal_doc_count": sum(internal_doc_counts) / len(internal_doc_counts) if internal_doc_counts else 0.0,
        "external_ref_count": len(external_source_ids),
        "latency_ms": {
            "avg": sum(latency_values) / len(latency_values) if latency_values else None,
            "p95": _percentile(latency_values, 0.95) if latency_values else None,
            "max": max(latency_values) if latency_values else None,
        },
        "case_duration_ms": {
            "avg": sum(duration_values) / len(duration_values) if duration_values else None,
            "p95": _percentile(duration_values, 0.95) if duration_values else None,
            "max": max(duration_values) if duration_values else None,
        },
        "max_case_duration_ms": max(duration_values) if duration_values else None,
        "stalled_case_count": stalled_case_count,
        "total_usage": total_usage,
        "total_cost_usd": total_cost_usd,
        "avg_cost_usd": total_cost_usd / case_count if total_cost_usd is not None and case_count else None,
        "failure_classes": dict(sorted(failure_classes.items())),
        "blocking_failure_classes": blocking_failures,
        "artifact_missing_count": artifact_missing_count,
    }


def _case_latency_values(cases: list[dict[str, Any]]) -> list[float]:
    values = []
    for case in cases:
        value = case.get("latency_ms")
        if isinstance(value, int | float):
            values.append(float(value))
    return values


def _model_health_scorecard(out_dir: Path, combo_reports: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for combo in combo_reports:
        combo_dir = Path(combo.get("out_dir", out_dir / str(combo.get("id", "combo"))))
        for tier in ("smoke", "full"):
            eval_path = combo_dir / tier / EVAL_REPORT_PATH
            if not eval_path.exists():
                continue
            payload = yaml.safe_load(eval_path.read_text(encoding="utf-8"))
            for case in payload.get("cases", []) if isinstance(payload, dict) else []:
                for stage in case.get("stages", []) or []:
                    stage_name = stage.get("stage")
                    model = stage.get("model")
                    if not stage_name or not model:
                        continue
                    buckets.setdefault((str(stage_name), str(model)), []).append({"case": case, "stage": stage, "tier": tier, "combo_id": combo.get("id")})
    routes = []
    for (stage, model), records in sorted(buckets.items()):
        routes.append(_model_health_route(stage, model, records))
    return {"created_at": datetime.now(UTC).isoformat(), "routes": routes}


def _model_health_route(stage: str, model: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    case_count = len(records)
    success_count = sum(1 for record in records if record["case"].get("status") == STATUS_PASS)
    quality_blocker_count = sum(len(record["case"].get("quality_blockers") or []) for record in records)
    static_validation_fail_count = sum(1 for record in records if record["case"].get("validation_status") not in {None, STATUS_PASS, "manual_required"})
    schema_fail_count = sum(1 for record in records if record["case"].get("failure_class") == FAILURE_SCHEMA_INVALID)
    timeout_stall_count = sum(1 for record in records if record["case"].get("failure_class") == FAILURE_PROVIDER_TIMEOUT or _case_is_stalled(record["case"]))
    latency_values = [float(record["stage"].get("latency_ms")) for record in records if isinstance(record["stage"].get("latency_ms"), int | float)]
    repair_counts = [int(record["case"].get("repair_count") or 0) for record in records]
    total_usage = _sum_case_usage([record["case"] for record in records])
    success_rate = success_count / case_count if case_count else 0.0
    quality_blocker_rate = quality_blocker_count / case_count if case_count else 0.0
    status = _model_health_status(success_rate, quality_blocker_rate, timeout_stall_count, latency_values)
    return {
        "stage": stage,
        "model": model,
        "status": status,
        "case_count": case_count,
        "success_rate": success_rate,
        "quality_blocker_rate": quality_blocker_rate,
        "quality_blocker_count": quality_blocker_count,
        "static_validation_fail_rate": static_validation_fail_count / case_count if case_count else 0.0,
        "schema_fail_rate": schema_fail_count / case_count if case_count else 0.0,
        "timeout_stall_count": timeout_stall_count,
        "latency_ms": {
            "p50": _percentile(latency_values, 0.50) if latency_values else None,
            "p95": _percentile(latency_values, 0.95) if latency_values else None,
            "max": max(latency_values) if latency_values else None,
        },
        "avg_repair_count": sum(repair_counts) / len(repair_counts) if repair_counts else 0.0,
        "total_usage": total_usage,
        "total_cost_usd": _case_total_cost_usd([record["case"] for record in records]),
    }


def _model_health_status(success_rate: float, quality_blocker_rate: float, timeout_stall_count: int, latency_values: list[float]) -> str:
    p95_latency = _percentile(latency_values, 0.95) if latency_values else None
    if timeout_stall_count or success_rate < 0.8 or quality_blocker_rate > 0.2:
        return "unstable"
    if success_rate < 1.0 or quality_blocker_rate > 0 or (p95_latency is not None and p95_latency > 60_000):
        return "degraded"
    return "healthy"


def _case_duration_values(cases: list[dict[str, Any]]) -> list[float]:
    values = []
    for case in cases:
        value = case.get("case_duration_ms")
        if isinstance(value, int | float):
            values.append(float(value))
    return values


def _case_is_stalled(case: dict[str, Any]) -> bool:
    return case.get("outcome") == "stalled" or (
        case.get("failure_class") == FAILURE_ARTIFACT_MISSING and not case.get("case_completed_at")
    )


def _case_gate_status(case: dict[str, Any], gate_name: str, *, fallback: Any = None) -> Any:
    gate = case.get(gate_name)
    if isinstance(gate, dict):
        return gate.get("status", fallback)
    return fallback


def _is_expected_blocked(case: dict[str, Any]) -> bool:
    return case.get("expected_outcome") == "blocked"


def _case_validation_allows_artifact(case: dict[str, Any]) -> bool:
    return case.get("validation_status") == STATUS_PASS or (
        case.get("validation_status") == "manual_required" and not case.get("validation_failures")
    )


def _sum_case_usage(cases: list[dict[str, Any]]) -> dict[str, float | int]:
    totals: dict[str, float] = {}
    for case in cases:
        usage = case.get("total_usage") or case.get("usage") or {}
        if not isinstance(usage, dict):
            continue
        for key, value in usage.items():
            if isinstance(value, bool) or not isinstance(value, int | float):
                continue
            totals[key] = totals.get(key, 0.0) + float(value)
    return {key: int(value) if value.is_integer() else value for key, value in sorted(totals.items())}


def _case_total_cost_usd(cases: list[dict[str, Any]]) -> float | None:
    cost_keys = ("cost_usd", "total_cost_usd", "cost", "total_cost")
    total = 0.0
    found_any = False
    for case in cases:
        found_for_case = False
        for source in (case.get("total_usage"), case.get("usage"), case):
            if not isinstance(source, dict):
                continue
            for key in cost_keys:
                value = source.get(key)
                if isinstance(value, bool) or not isinstance(value, int | float):
                    continue
                total += float(value)
                found_any = True
                found_for_case = True
                break
            if found_for_case:
                break
    return total if found_any else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def _recommended_combo_id(combo_reports: list[dict[str, Any]]) -> str | None:
    accepted = [combo for combo in combo_reports if combo.get("accepted")]
    if not accepted:
        return None

    def sort_key(combo: dict[str, Any]) -> tuple[float, float, float, str]:
        summary = combo.get("full") or combo.get("smoke") or {}
        return (
            -float(summary.get("pass_rate", 0.0)),
            float(summary.get("avg_repair_count", 99.0)),
            -float(summary.get("static_validation_pass_rate", 0.0)),
            combo["id"],
        )

    return sorted(accepted, key=sort_key)[0]["id"]


def _case_failure_classes(case: dict[str, Any]) -> set[str]:
    classes = {case["failure_class"]} if case.get("failure_class") else set()
    for attribution in case.get("failure_attribution", []):
        failure_class = attribution.get("failure_class")
        if failure_class:
            classes.add(failure_class)
    return classes


def _case_id(case: dict[str, Any]) -> str:
    raw = str(case.get("id") or case.get("name") or "case")
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in raw).strip("-") or "case"
