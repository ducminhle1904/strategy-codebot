from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from statistics import quantiles
from typing import Any, Callable
import urllib.error
import urllib.request

from strategy_codebot.evals import EVAL_REPORT_PATH, run_live_eval
from strategy_codebot.harness_types import FAILURE_PROVIDER_ERROR, FAILURE_PROVIDER_TIMEOUT, FAILURE_SCHEMA_INVALID, STATUS_PASS, STATUS_SKIPPED
from strategy_codebot.live import (
    COST_PROFILE_CHEAP,
    STAGE_BALANCED_REVIEW,
    STAGE_PINE_CODE_GENERATION,
    STAGE_REPAIR,
    STAGE_STRATEGY_CODING,
    STAGE_STRATEGY_REASONING,
    WORKFLOW_MULTI_AGENT,
    LiveRunOptions,
    _provider_route,
)
from strategy_codebot.paths import ensure_dir
from strategy_codebot.schemas import write_json


OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
MODEL_CANDIDATE_MATRIX_REPORT_PATH = "model-candidate-matrix.json"

LIGHTWEIGHT_CANDIDATES: dict[str, list[str]] = {
    STAGE_STRATEGY_REASONING: [
        "openrouter/deepseek/deepseek-v4-flash",
        "vercel_ai_gateway/google/gemini-2.5-flash-lite",
        "openrouter/google/gemini-2.5-flash-lite",
        "openrouter/google/gemini-2.5-flash",
        "openrouter/minimax/minimax-m3",
        "openrouter/qwen/qwen3.7-plus",
    ],
    STAGE_STRATEGY_CODING: [
        "openrouter/qwen/qwen3-coder-next",
        "openrouter/deepseek/deepseek-v4-flash",
        "openrouter/qwen/qwen3-coder",
        "openrouter/moonshotai/kimi-k2.7-code",
        "vercel_ai_gateway/google/gemini-2.5-flash-lite",
    ],
    STAGE_PINE_CODE_GENERATION: [
        "openrouter/qwen/qwen3-coder-next",
        "openrouter/qwen/qwen3-coder",
        "vercel_ai_gateway/google/gemini-2.5-flash-lite",
        "openrouter/deepseek/deepseek-v4-flash",
        "openrouter/moonshotai/kimi-k2.7-code",
    ],
    STAGE_REPAIR: [
        "openrouter/qwen/qwen3-coder-next",
        "openrouter/qwen/qwen3-coder",
        "vercel_ai_gateway/google/gemini-2.5-flash-lite",
        "openrouter/deepseek/deepseek-v4-flash",
        "openrouter/moonshotai/kimi-k2.7-code",
    ],
    STAGE_BALANCED_REVIEW: [
        "openrouter/deepseek/deepseek-v4-flash",
        "vercel_ai_gateway/google/gemini-2.5-flash-lite",
        "openrouter/google/gemini-2.5-flash-lite",
        "openrouter/minimax/minimax-m3",
        "openrouter/google/gemini-2.5-flash",
    ],
}


@dataclass(frozen=True)
class Candidate:
    stage: str
    route: str

    @property
    def candidate_id(self) -> str:
        normalized = self.route.replace("/", "_").replace(".", "_").replace(":", "_")
        return f"{self.stage}__{normalized}"


def build_model_candidate_matrix(
    *,
    suite: Path,
    out_root: Path,
    out: Path,
    runs: int = 1,
    policy: str = "enforce",
    workflow: str = WORKFLOW_MULTI_AGENT,
    cost_profile: str = COST_PROFILE_CHEAP,
    user_tier: str = "paid_low",
    knowledge_context: str = "auto",
    concurrency: int = 1,
    case_timeout_seconds: int = 300,
    stages: list[str] | None = None,
    candidates: list[str] | None = None,
    fetch_catalog: bool = True,
    eval_runner: Callable[..., dict[str, Any]] = run_live_eval,
) -> dict[str, Any]:
    if runs < 1:
        raise ValueError("runs must be at least 1")
    ensure_dir(out_root)
    selected = select_candidates(stages=stages, candidates=candidates)
    catalog = fetch_openrouter_model_catalog() if fetch_catalog else {"status": "skipped", "models": {}, "error": None}
    catalog_path = out_root / f"openrouter-model-catalog-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    write_json(catalog_path, catalog)

    candidate_reports: list[dict[str, Any]] = []
    for candidate in selected:
        candidate_reports.append(
            _run_candidate(
                candidate,
                suite=suite,
                out_root=out_root,
                runs=runs,
                policy=policy,
                workflow=workflow,
                cost_profile=cost_profile,
                user_tier=user_tier,
                knowledge_context=knowledge_context,
                concurrency=concurrency,
                case_timeout_seconds=case_timeout_seconds,
                catalog=catalog,
                eval_runner=eval_runner,
            )
        )

    promotions = recommend_promotions(candidate_reports)
    report = {
        "status": STATUS_PASS if candidate_reports else "skipped",
        "created_at": datetime.now(UTC).isoformat(),
        "suite": str(suite),
        "out_root": str(out_root),
        "runs_requested": runs,
        "workflow": workflow,
        "cost_profile": cost_profile,
        "user_tier": user_tier,
        "catalog_ref": str(catalog_path),
        "catalog_status": catalog["status"],
        "candidate_count": len(candidate_reports),
        "candidates": candidate_reports,
        "promotion_recommendations": promotions,
    }
    write_json(out, report)
    return report


def select_candidates(*, stages: list[str] | None = None, candidates: list[str] | None = None) -> list[Candidate]:
    allowed_stages = set(stages or LIGHTWEIGHT_CANDIDATES)
    selected: list[Candidate] = []
    for stage, routes in LIGHTWEIGHT_CANDIDATES.items():
        if stage not in allowed_stages:
            continue
        for route in routes:
            selected.append(Candidate(stage=stage, route=route))
    for value in candidates or []:
        if "=" not in value:
            raise ValueError("candidate must use stage=model")
        stage, route = value.split("=", 1)
        stage = stage.strip()
        route = route.strip()
        if not stage or not route:
            raise ValueError("candidate must use non-empty stage=model")
        selected.append(Candidate(stage=stage, route=route))
    deduped: dict[tuple[str, str], Candidate] = {}
    for candidate in selected:
        deduped[(candidate.stage, candidate.route)] = candidate
    return list(deduped.values())


def fetch_openrouter_model_catalog(*, url: str = OPENROUTER_MODELS_URL, timeout_seconds: int = 20) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"status": "warn", "models": {}, "error": f"{type(exc).__name__}: {exc}"}
    models: dict[str, Any] = {}
    for item in payload.get("data", []):
        model_id = str(item.get("id") or "")
        if not model_id:
            continue
        parameters = set(item.get("supported_parameters") or [])
        pricing = item.get("pricing") or {}
        benchmarks = (item.get("benchmarks") or {}).get("artificial_analysis") or {}
        models[model_id] = {
            "id": model_id,
            "context_length": item.get("context_length"),
            "supports_response_format": "response_format" in parameters,
            "supports_structured_outputs": "structured_outputs" in parameters,
            "prompt_per_1m": _price_per_million(pricing.get("prompt")),
            "completion_per_1m": _price_per_million(pricing.get("completion")),
            "coding_index": benchmarks.get("coding_index"),
            "agentic_index": benchmarks.get("agentic_index"),
            "intelligence_index": benchmarks.get("intelligence_index"),
        }
    return {"status": STATUS_PASS, "models": models, "error": None}


def recommend_promotions(candidate_reports: list[dict[str, Any]]) -> dict[str, Any]:
    by_stage: dict[str, list[dict[str, Any]]] = {}
    for report in candidate_reports:
        by_stage.setdefault(str(report["stage"]), []).append(report)
    recommendations: dict[str, Any] = {}
    for stage, reports in by_stage.items():
        eligible = [report for report in reports if report.get("promotion_eligible")]
        eligible.sort(key=lambda item: (int(item.get("p95_latency_ms") or 10**12), _estimated_cost(item)))
        recommendations[stage] = {
            "status": STATUS_PASS if eligible else "insufficient_evidence",
            "recommended_route": eligible[0]["route"] if eligible else None,
            "fallback_routes": [report["route"] for report in eligible[1:3]],
            "rejected_count": len(reports) - len(eligible),
        }
    return recommendations


def _run_candidate(
    candidate: Candidate,
    *,
    suite: Path,
    out_root: Path,
    runs: int,
    policy: str,
    workflow: str,
    cost_profile: str,
    user_tier: str,
    knowledge_context: str,
    concurrency: int,
    case_timeout_seconds: int,
    catalog: dict[str, Any],
    eval_runner: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    metadata = _candidate_metadata(candidate.route, catalog)
    missing_envs = _missing_route_envs(candidate.route)
    unsupported_reason = _unsupported_reason(metadata)
    base_report = {
        "candidate_id": candidate.candidate_id,
        "stage": candidate.stage,
        "route": candidate.route,
        "gateway": _provider_route(candidate.route).gateway,
        "route_provider": _provider_route(candidate.route).provider,
        "model_id": _catalog_model_id(candidate.route),
        "metadata": metadata,
        "missing_envs": missing_envs,
        "unsupported_reason": unsupported_reason,
        "run_reports": [],
    }
    if missing_envs:
        return _finalize_candidate_report({**base_report, "status": STATUS_SKIPPED, "skip_reason": "missing_credentials"})
    if unsupported_reason:
        return _finalize_candidate_report({**base_report, "status": STATUS_SKIPPED, "skip_reason": unsupported_reason})

    run_reports: list[dict[str, Any]] = []
    for run_index in range(1, runs + 1):
        run_out = out_root / candidate.candidate_id / f"run-{run_index:02d}"
        options = LiveRunOptions(
            model_stage_overrides={candidate.stage: candidate.route},
            workflow=workflow,
            cost_profile=cost_profile,
            user_tier=user_tier,
            save_raw_provider=True,
            knowledge_context=knowledge_context,
        )
        report = eval_runner(
            suite_path=suite,
            out_dir=run_out,
            policy=policy,
            live_options=options,
            concurrency=concurrency,
            case_timeout_seconds=case_timeout_seconds,
        )
        run_reports.append(_summarize_eval_run(report, run_out))
    return _finalize_candidate_report({**base_report, "status": _candidate_status(run_reports), "run_reports": run_reports})


def _summarize_eval_run(report: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    cases = report.get("cases") or []
    attempts = _load_attempts(out_dir)
    return {
        "status": report.get("status"),
        "case_count": report.get("case_count", len(cases)),
        "failed": report.get("failed"),
        "validation_pass_count": sum(1 for case in cases if case.get("validation_status") == STATUS_PASS),
        "quality_pass_count": sum(1 for case in cases if case.get("quality_status") == STATUS_PASS),
        "schema_invalid_count": sum(1 for attempt in attempts if attempt.get("failure_class") == FAILURE_SCHEMA_INVALID),
        "provider_error_count": sum(1 for attempt in attempts if attempt.get("failure_class") == FAILURE_PROVIDER_ERROR),
        "provider_timeout_count": sum(1 for attempt in attempts if attempt.get("failure_class") == FAILURE_PROVIDER_TIMEOUT),
        "context_budget_warning_count": sum(len((case.get("context_contract") or {}).get("budget_warnings") or []) for case in cases),
        "latencies_ms": [int(attempt.get("stage_total_ms") or attempt.get("latency_ms") or 0) for attempt in attempts if attempt.get("status") == STATUS_PASS],
        "provider_call_ratios": [float(attempt.get("provider_call_ratio")) for attempt in attempts if attempt.get("provider_call_ratio") is not None],
        "eval_report_ref": str(out_dir / EVAL_REPORT_PATH),
    }


def _finalize_candidate_report(report: dict[str, Any]) -> dict[str, Any]:
    run_reports = report.get("run_reports") or []
    latencies = [latency for run in run_reports for latency in run.get("latencies_ms", []) if latency]
    provider_ratios = [ratio for run in run_reports for ratio in run.get("provider_call_ratios", [])]
    schema_invalid_count = sum(int(run.get("schema_invalid_count") or 0) for run in run_reports)
    provider_error_count = sum(int(run.get("provider_error_count") or 0) for run in run_reports)
    provider_timeout_count = sum(int(run.get("provider_timeout_count") or 0) for run in run_reports)
    context_budget_warning_count = sum(int(run.get("context_budget_warning_count") or 0) for run in run_reports)
    promotion_eligible = (
        report.get("status") == STATUS_PASS
        and schema_invalid_count == 0
        and provider_error_count == 0
        and provider_timeout_count == 0
        and context_budget_warning_count == 0
    )
    return {
        **report,
        "p50_latency_ms": _percentile(latencies, 0.5),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "max_latency_ms": max(latencies) if latencies else 0,
        "avg_provider_call_ratio": round(sum(provider_ratios) / len(provider_ratios), 4) if provider_ratios else 0,
        "schema_invalid_count": schema_invalid_count,
        "provider_error_count": provider_error_count,
        "provider_timeout_count": provider_timeout_count,
        "context_budget_warning_count": context_budget_warning_count,
        "promotion_eligible": promotion_eligible,
    }


def _candidate_status(run_reports: list[dict[str, Any]]) -> str:
    return STATUS_PASS if run_reports and all(report.get("status") == STATUS_PASS for report in run_reports) else "fail"


def _load_attempts(out_dir: Path) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for path in out_dir.glob("cases/*/live-workflow-trace.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        attempts.extend(payload.get("attempts") or [])
    for path in out_dir.glob("cases/*/live-error.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        attempts.extend(payload.get("attempts") or [])
    return attempts


def _candidate_metadata(route: str, catalog: dict[str, Any]) -> dict[str, Any]:
    return dict((catalog.get("models") or {}).get(_catalog_model_id(route)) or {})


def _catalog_model_id(route: str) -> str:
    if route.startswith("openrouter/"):
        return route.split("/", 1)[1]
    if route.startswith("vercel_ai_gateway/"):
        return route.split("/", 1)[1]
    return route.split("/", 1)[1] if "/" in route else route


def _missing_route_envs(route: str) -> list[str]:
    return [env for env in _provider_route(route).missing_envs() if not os.environ.get(env)]


def _unsupported_reason(metadata: dict[str, Any]) -> str | None:
    if not metadata:
        return None
    if not metadata.get("supports_response_format") and not metadata.get("supports_structured_outputs"):
        return "structured_output_not_supported"
    return None


def _price_per_million(value: Any) -> float | None:
    try:
        return round(float(value) * 1_000_000, 6)
    except (TypeError, ValueError):
        return None


def _estimated_cost(report: dict[str, Any]) -> float:
    metadata = report.get("metadata") or {}
    return float(metadata.get("prompt_per_1m") or 0) + float(metadata.get("completion_per_1m") or 0)


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    if len(values) == 1:
        return values[0]
    if fraction == 0.5:
        return int(quantiles(sorted(values), n=2, method="inclusive")[0])
    return int(quantiles(sorted(values), n=20, method="inclusive")[18])
