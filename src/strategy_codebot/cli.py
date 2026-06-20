from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from time import time
from typing import Any, Optional

import typer

from strategy_codebot import __version__
from strategy_codebot.agent_harness import inspect_run
from strategy_codebot.doctor import doctor_report
from strategy_codebot.evals import run_live_eval
from strategy_codebot.harness import (
    NO_ERROR_TRACE_ARG,
    assess_development,
    audit_traces,
    build_trace_command,
    gate_development,
    harness_cli_path,
    memory_candidates,
    preflight_context,
    record_trace,
    record_trace_intake,
    recommend_next,
    summarize_traces,
)
from strategy_codebot.harness_intelligence import (
    DEFAULT_INTELLIGENCE_IMPROVEMENTS_PATH,
    DEFAULT_INTELLIGENCE_PATCH_PATH,
    DEFAULT_INTELLIGENCE_PROPOSALS_PATH,
    DEFAULT_INTELLIGENCE_REPLAY_PATH,
    DEFAULT_INTELLIGENCE_REPORT_PATH,
    DEFAULT_CONTEXT_REPORT_PATH,
    DEFAULT_LATENCY_MATRIX_PATH,
    DEFAULT_PROXY_LOG_REPORT_PATH,
    DEFAULT_LATENCY_REPORT_PATH,
    DEFAULT_PROMPT_MATRIX_PATH,
    DEFAULT_ROUTE_HEALTH_REPORT_PATH,
    build_context_report,
    apply_approved_improvement,
    build_intelligence_report,
    build_latency_matrix,
    build_latency_report,
    build_prompt_matrix,
    build_proxy_log_report,
    propose_improvements,
    propose_intelligence_lessons,
    replay_recommendations,
)
from strategy_codebot.harness_types import STATUS_PASS
from strategy_codebot.live import COST_PROFILE_QUALITY, DEFAULT_USER_TIER, PROMPT_PROFILE_DEFAULT, WEB_SEARCH_DEFAULT, WORKFLOW_MULTI_AGENT, LiveRunOptions, _completion_kwargs, _provider_error_subclass, _provider_route, validate_model_stage_overrides
from strategy_codebot.lightweight_models import MODEL_CANDIDATE_MATRIX_REPORT_PATH, build_model_candidate_matrix
from strategy_codebot.knowledge import audit_run, check_registry, create_proposal, create_snapshot, diff_snapshots
from strategy_codebot.knowledge_base import (
    EMBEDDING_PROFILE_LOCAL,
    KNOWLEDGE_CANDIDATES_PATH,
    KNOWLEDGE_DATABASE_URL_ENV,
    KNOWLEDGE_INDEX_PATH,
    POSTGRES_SCHEMA_PATH,
    approve_source_summary,
    approve_candidate,
    build_knowledge_index,
    evaluate_knowledge_suite,
    ingest_knowledge_source,
    knowledge_health,
    learn_from_run,
    load_candidates,
    postgres_schema_sql,
    propose_candidate,
    reject_candidate,
    resolve_embedding_config,
    search_knowledge,
    snapshot_trusted_source,
    summarize_source_snapshot,
)
from strategy_codebot.paths import ensure_parent, repo_root, resolve_repo_path
from strategy_codebot.prompt_contracts import DEFAULT_PROMPT_MATRIX_PROFILES, normalize_prompt_profiles
from strategy_codebot.review import REVIEW_MODE_NONE, review_run_directory
from strategy_codebot.route_health import route_health_report
from strategy_codebot.runner import run_strategy, validate_pine_file
from strategy_codebot.schemas import validate_payload, write_json
from strategy_codebot.model_matrix import run_model_combo_matrix
from strategy_codebot.openrouter_free import free_catalog_report, resolve_free_catalog, select_free_models_for_task
from strategy_codebot.tool_runtime import POLICY_OBSERVE, check_tool_registry, tool_ids

app = typer.Typer(help="Strategy Codebot CLI.")
knowledge_app = typer.Typer(help="Knowledge source registry commands.")
knowledge_candidates_app = typer.Typer(help="Knowledge memory candidate commands.")
models_app = typer.Typer(help="Model routing diagnostics commands.")
model_gateways_app = typer.Typer(help="Gateway routing diagnostics commands.")
model_litellm_app = typer.Typer(help="LiteLLM proxy administration commands.")
model_litellm_keys_app = typer.Typer(help="LiteLLM virtual key provisioning commands.")
tools_app = typer.Typer(help="Runtime tool registry commands.")
eval_app = typer.Typer(help="Evaluation harness commands.")
harness_app = typer.Typer(help="Agent harness inspection commands.")
app.add_typer(knowledge_app, name="knowledge")
knowledge_app.add_typer(knowledge_candidates_app, name="candidates")
app.add_typer(models_app, name="models")
models_app.add_typer(model_gateways_app, name="gateways")
models_app.add_typer(model_litellm_app, name="litellm")
model_litellm_app.add_typer(model_litellm_keys_app, name="keys")
app.add_typer(tools_app, name="tools")
app.add_typer(eval_app, name="eval")
app.add_typer(harness_app, name="harness")

DEFAULT_HARNESS_SESSION_STATE = Path(".strategy-codebot/harness-session.json")
DEFAULT_HARNESS_STARTUP_STATE = Path(".strategy-codebot/harness-startup.json")
DEFAULT_HARNESS_PREFLIGHT_REPORT = Path(".strategy-codebot/harness-preflight.json")
DEFAULT_HARNESS_RECOMMENDATIONS_REPORT = Path(".strategy-codebot/harness-recommendations.json")
DEFAULT_HARNESS_MEMORY_CANDIDATES_REPORT = Path(".strategy-codebot/harness-memory-candidates.json")


def _resolve_input_path(path: Path) -> Path:
    return resolve_repo_path(path)


def _parse_model_stage_overrides(values: Optional[list[str]]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for value in values or []:
        if "=" not in value:
            raise typer.BadParameter("--model-stage must use stage=model")
        stage, model = value.split("=", 1)
        stage = stage.strip()
        model = model.strip()
        if not stage or not model:
            raise typer.BadParameter("--model-stage must use non-empty stage=model")
        overrides[stage] = model
    try:
        validate_model_stage_overrides(overrides)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    return overrides


def _trace_has_completed_harness_record(trace_path: Path) -> bool:
    if not trace_path.exists():
        return False
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("tool_id") == "record_harness_trace" and event.get("event_type") == "tool.completed" and event.get("status") == STATUS_PASS:
            return True
    return False


@app.command()
def version() -> None:
    typer.echo(__version__)


@app.command()
def doctor(
    out: Optional[Path] = typer.Option(None, "--out", help="Optional doctor report JSON output path."),
) -> None:
    report = doctor_report()
    if out:
        write_json(out, report)
    typer.echo(f"status={report['status']} version={report['environment']['package_version']} checks={len(report['checks'])}")
    if report["status"] != STATUS_PASS:
        raise typer.Exit(1)


@app.command()
def run(
    spec: Optional[Path] = typer.Option(None, "--spec", help="Strategy spec JSON path."),
    prompt: Optional[str] = typer.Option(None, "--prompt", help="Prompt for live LLM mode."),
    mode: str = typer.Option("dry-run", "--mode", help="dry-run or live."),
    out: Path = typer.Option(Path("runs/latest"), "--out", help="Output run directory."),
    review: str = typer.Option(REVIEW_MODE_NONE, "--review", help="none or parallel."),
    runtime_trace: bool = typer.Option(True, "--runtime-trace/--no-runtime-trace", help="Write runtime trace artifacts."),
    policy: str = typer.Option(POLICY_OBSERVE, "--policy", help="observe or enforce."),
    model_registry: Optional[Path] = typer.Option(None, "--model-registry", help="Live mode model registry YAML path."),
    model: Optional[str] = typer.Option(None, "--model", help="Live mode model override."),
    workflow: str = typer.Option(WORKFLOW_MULTI_AGENT, "--workflow", help="multi-agent, single, or compact-free."),
    cost_profile: str = typer.Option(COST_PROFILE_QUALITY, "--cost-profile", help="quality or cheap."),
    user_tier: str = typer.Option(DEFAULT_USER_TIER, "--user-tier", help="free, paid_low, paid_medium, or paid_high."),
    model_stage: Optional[list[str]] = typer.Option(None, "--model-stage", help="Live multi-agent stage override as stage=model. Repeatable."),
    save_raw_provider: bool = typer.Option(False, "--save-raw-provider/--no-save-raw-provider", help="Write raw live provider response artifact."),
    knowledge_context: str = typer.Option("auto", "--knowledge-context", help="auto or off."),
    prompt_profile: str = typer.Option(PROMPT_PROFILE_DEFAULT, "--prompt-profile", help="current or optimized_v1."),
    web_search: str = typer.Option(WEB_SEARCH_DEFAULT, "--web-search", help="off, auto, or on. Defaults to auto smart-search."),
    require_web_search: bool = typer.Option(False, "--require-web-search/--no-require-web-search", help="Fail live mode when requested web search is unavailable."),
    otel_export: Optional[Path] = typer.Option(None, "--otel-export", help="Write local OpenTelemetry-compatible JSONL spans."),
    record_harness: Optional[bool] = typer.Option(None, "--record-harness/--no-record-harness", help="Record a repository-harness trace."),
) -> None:
    live_options = (
        LiveRunOptions(
            model_override=model,
            model_stage_overrides=_parse_model_stage_overrides(model_stage),
            workflow=workflow,
            cost_profile=cost_profile,
            user_tier=user_tier,
            save_raw_provider=save_raw_provider,
            knowledge_context=knowledge_context,
            prompt_profile=prompt_profile,
            web_search=web_search,
            require_web_search=require_web_search,
        )
        if mode == "live"
        else None
    )
    result = run_strategy(
        spec_path=spec,
        prompt=prompt,
        mode=mode,
        out_dir=out,
        review=review,
        record_harness=record_harness,
        runtime_trace=runtime_trace,
        policy=policy,
        model_registry=model_registry,
        live_options=live_options,
        otel_export=otel_export,
    )
    typer.echo(f"run_id={result['run_id']} status={result['status']} out={result['out_dir']}")


@app.command()
def review(
    run_dir: Path = typer.Option(..., "--run-dir", help="Existing run directory to review."),
    mode: str = typer.Option("dry-run", "--mode", help="dry-run or live."),
    out: Path = typer.Option(..., "--out", help="Review report output path."),
    runtime_trace: bool = typer.Option(True, "--runtime-trace/--no-runtime-trace", help="Write runtime trace artifacts."),
    policy: str = typer.Option(POLICY_OBSERVE, "--policy", help="observe or enforce."),
    record_harness: Optional[bool] = typer.Option(None, "--record-harness/--no-record-harness", help="Record a repository-harness trace."),
) -> None:
    report = review_run_directory(run_dir=run_dir, mode=mode, out_path=out, record_harness=record_harness, runtime_trace=runtime_trace, policy=policy)
    typer.echo(f"run_id={report['run_id']} run_status={report['run_status']} decision={report['decision']} out={out}")


@app.command("validate-pine")
def validate_pine_command(
    file: Path = typer.Option(..., "--file", help="Pine Script file to validate."),
    spec: Path = typer.Option(..., "--spec", help="Strategy spec JSON path."),
    out: Path = typer.Option(..., "--out", help="Validation report output path."),
) -> None:
    report = validate_pine_file(file, spec, out)
    typer.echo(f"status={report['status']} out={out}")


@models_app.command("refresh-free")
def models_refresh_free(
    out: Optional[Path] = typer.Option(None, "--out", help="Optional JSON report output path."),
    no_fetch: bool = typer.Option(False, "--no-fetch", help="Use seed/cache only; do not call OpenRouter catalog."),
) -> None:
    catalog = resolve_free_catalog(fetch=not no_fetch)
    selected = select_free_models_for_task("single", catalog=catalog)
    report = free_catalog_report(catalog, selected)
    if out:
        write_json(out, report)
    typer.echo(
        f"status={report['free_capacity_status']} source={report['free_catalog_source']} "
        f"models={report['free_catalog_model_count']} selected={len(selected)} catalog={report['free_catalog_ref']}"
    )


@models_app.command("health")
def models_health(
    tier: str = typer.Option(DEFAULT_USER_TIER, "--tier", help="free, paid_low, paid_medium, or paid_high."),
    out: Optional[Path] = typer.Option(None, "--out", help="Optional JSON report output path."),
) -> None:
    if tier != "free":
        report = {"tier": tier, "status": "pass", "message": "Persistent model health is currently implemented for free catalog diagnostics only."}
    else:
        catalog = resolve_free_catalog(fetch=False)
        selected = select_free_models_for_task("single", catalog=catalog)
        report = {"tier": tier, "status": "pass", **free_catalog_report(catalog, selected)}
    if out:
        write_json(out, report)
    typer.echo(f"status={report['status']} tier={tier} selected={len(report.get('selected_free_models', []))}")


@model_gateways_app.command("check")
def models_gateways_check(
    model_registry: Optional[Path] = typer.Option(None, "--model-registry", help="Model registry YAML path."),
    out: Optional[Path] = typer.Option(None, "--out", help="Optional JSON report output path."),
) -> None:
    import yaml

    registry_path = resolve_repo_path(model_registry or repo_root() / "configs" / "model-registry.example.yaml")
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    routes = _gateway_routes_from_registry(registry)
    report = {"status": "pass", "model_registry": str(registry_path), "route_count": len(routes), "routes": routes}
    if out:
        write_json(out, report)
    missing = sum(1 for route in routes if route["missing_credentials"])
    typer.echo(f"status=pass routes={len(routes)} missing_credentials={missing} registry={registry_path}")


@model_gateways_app.command("health")
def models_gateways_health(
    model_registry: Optional[Path] = typer.Option(None, "--model-registry", help="Model registry YAML path."),
    out: Optional[Path] = typer.Option(None, "--out", help="Optional JSON report output path."),
) -> None:
    import yaml

    registry_path = resolve_repo_path(model_registry or repo_root() / "configs" / "model-registry.example.yaml")
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    routes = _gateway_routes_from_registry(registry)
    by_gateway: dict[str, dict[str, Any]] = {}
    for route in routes:
        bucket = by_gateway.setdefault(route["gateway"], {"gateway": route["gateway"], "route_count": 0, "configured_count": 0, "missing_credentials": []})
        bucket["route_count"] += 1
        if route["configured"]:
            bucket["configured_count"] += 1
        else:
            bucket["missing_credentials"].extend(route["missing_credentials"])
    report = {"status": "pass", "model_registry": str(registry_path), "gateways": list(by_gateway.values())}
    if out:
        write_json(out, report)
    configured = sum(1 for gateway in by_gateway.values() if gateway["configured_count"])
    typer.echo(f"status=pass gateways={len(by_gateway)} configured_gateways={configured} registry={registry_path}")


@model_gateways_app.command("smoke-route")
def models_gateways_smoke_route(
    alias: str = typer.Option(..., "--alias", help="Route alias, for example paid_low.repair or litellm_proxy/paid_low.repair."),
    out: Optional[Path] = typer.Option(None, "--out", help="Optional JSON report output path."),
    timeout_seconds: float = typer.Option(30.0, "--timeout-seconds", min=1.0, help="Provider call timeout for the smoke request."),
) -> None:
    model = alias if "/" in alias else f"litellm_proxy/{alias}"
    route = _provider_route(model)
    missing = route.missing_envs()
    report: dict[str, Any] = {
        "status": "skipped" if missing else "unknown",
        "model": model,
        "gateway": route.gateway,
        "route_model": route.route_model,
        "provider": route.provider,
        "missing_credentials": missing,
    }
    if not missing:
        import litellm

        started = time()
        try:
            response = litellm.completion(
                **_completion_kwargs(
                    model=model,
                    route=route,
                    messages=[{"role": "user", "content": "Return JSON {\"ok\": true}."}],
                    temperature=0,
                    request_timeout=timeout_seconds,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "strategy_codebot_gateway_smoke",
                            "strict": True,
                            "schema": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {"ok": {"type": "boolean"}},
                                "required": ["ok"],
                            },
                        },
                    },
                    metadata={"strategy_codebot.diagnostic": "gateway_smoke_route", "strategy_codebot.route_model": route.route_model},
                )
            )
            report.update({"status": "pass", "latency_ms": int((time() - started) * 1000), "response_type": type(response).__name__})
        except Exception as exc:
            report.update(
                {
                    "status": "fail",
                    "latency_ms": int((time() - started) * 1000),
                    "failure_class": "provider_error",
                    "provider_error_subclass": _provider_error_subclass(exc),
                    "error": str(exc)[:500],
                }
            )
    if out:
        write_json(out, report)
    typer.echo(
        f"status={report['status']} alias={alias} gateway={report['gateway']} "
        f"provider={report['provider']} latency_ms={report.get('latency_ms', 0)}"
    )
    if report["status"] == "fail":
        raise typer.Exit(1)


def _gateway_routes_from_registry(registry: dict[str, Any]) -> list[dict[str, Any]]:
    models: list[str] = []
    for tier in (registry.get("model_tiers") or {}).values():
        for route_config in (tier.get("routes_by_stage") or {}).values() if isinstance(tier, dict) else []:
            if isinstance(route_config, str):
                models.append(route_config)
            elif isinstance(route_config, list):
                models.extend(str(model) for model in route_config if model)
    routes = []
    for model in sorted(set(models)):
        route = _provider_route(model)
        missing = route.missing_envs()
        routes.append(
            {
                "model": model,
                "gateway": route.gateway,
                "provider": route.provider,
                "route_model": route.route_model,
                "credential_env": route.credential_env,
                "base_url_env": route.base_url_env,
                "configured": not missing,
                "missing_credentials": missing,
            }
        )
    return routes


@model_litellm_keys_app.command("aliases")
def models_litellm_keys_aliases(
    tier: str = typer.Option("paid_medium", "--tier", help="paid_low, paid_medium, or paid_high."),
    model_registry: Optional[Path] = typer.Option(None, "--model-registry", help="Model registry YAML path."),
    out: Optional[Path] = typer.Option(None, "--out", help="Optional JSON report output path."),
) -> None:
    aliases = _litellm_aliases_for_tier(_load_model_registry(model_registry), tier)
    report = {"status": "pass", "tier": tier, "aliases": aliases, "alias_count": len(aliases)}
    if out:
        write_json(out, report)
    typer.echo(f"status=pass tier={tier} aliases={len(aliases)}")
    for alias in aliases:
        typer.echo(alias)


@model_litellm_keys_app.command("check")
def models_litellm_keys_check(
    production: bool = typer.Option(False, "--production", help="Require production-safe virtual key separation."),
    out: Optional[Path] = typer.Option(None, "--out", help="Optional JSON report output path."),
) -> None:
    report = _litellm_key_readiness(production=production)
    if out:
        write_json(out, report)
    typer.echo(f"status={report['status']} production={production} checks={len(report['checks'])}")
    if report["status"] != STATUS_PASS:
        raise typer.Exit(1)


@model_litellm_keys_app.command("provision")
def models_litellm_keys_provision(
    tier: str = typer.Option("paid_medium", "--tier", help="paid_low, paid_medium, or paid_high."),
    workspace_id: str = typer.Option(..., "--workspace-id", help="Workspace id for LiteLLM key metadata."),
    user_id: Optional[str] = typer.Option(None, "--user-id", help="Optional user id for LiteLLM key metadata."),
    budget_duration: str = typer.Option("30d", "--budget-duration", help="LiteLLM virtual key budget duration."),
    max_budget: Optional[float] = typer.Option(None, "--max-budget", help="Optional max budget in USD."),
    api_base: Optional[str] = typer.Option(None, "--api-base", help="LiteLLM admin API base URL."),
    model_registry: Optional[Path] = typer.Option(None, "--model-registry", help="Model registry YAML path."),
    out: Optional[Path] = typer.Option(None, "--out", help="Optional JSON output path containing the generated key."),
) -> None:
    registry = _load_model_registry(model_registry)
    aliases = _litellm_aliases_for_tier(registry, tier)
    budget = max_budget if max_budget is not None else _litellm_budget_from_env(tier)
    payload: dict[str, Any] = {
        "models": aliases,
        "metadata": {"tier": tier, "workspace_id": workspace_id, "source": "strategy-codebot"},
        "budget_duration": budget_duration,
    }
    if user_id:
        payload["metadata"]["user_id"] = user_id
    if budget is not None:
        payload["max_budget"] = budget
    response = _litellm_generate_key(api_base=api_base, payload=payload)
    report = {
        "status": "pass",
        "tier": tier,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "aliases": aliases,
        "request": payload,
        "response": response,
    }
    if out:
        write_json(out, report)
        typer.echo(f"status=pass tier={tier} aliases={len(aliases)} out={out} contains_secret=true")
    else:
        generated_key = response.get("key") or response.get("token") or response.get("api_key")
        typer.echo(f"status=pass tier={tier} aliases={len(aliases)} key={generated_key}")


def _load_model_registry(model_registry: Path | None) -> dict[str, Any]:
    import yaml

    registry_path = resolve_repo_path(model_registry or repo_root() / "configs" / "model-registry.example.yaml")
    return yaml.safe_load(registry_path.read_text(encoding="utf-8"))


def _litellm_aliases_for_tier(registry: dict[str, Any], tier: str) -> list[str]:
    if tier == "free":
        raise typer.BadParameter("free tier does not use LiteLLM virtual-key aliases")
    tier_config = (registry.get("model_tiers") or {}).get(tier)
    if not isinstance(tier_config, dict):
        raise typer.BadParameter(f"Unknown paid tier: {tier}")
    aliases = []
    for routes in (tier_config.get("routes_by_stage") or {}).values():
        for route in routes if isinstance(routes, list) else [routes]:
            route = str(route)
            if route.startswith("litellm_proxy/"):
                aliases.append(route.split("/", 1)[1])
    if not aliases:
        raise typer.BadParameter(f"No LiteLLM aliases configured for tier: {tier}")
    return sorted(dict.fromkeys(aliases))


def _litellm_budget_from_env(tier: str) -> float | None:
    env_name = f"LITELLM_BUDGET_{tier.upper()}_MONTHLY_USD"
    value = os.getenv(env_name)
    if not value:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise typer.BadParameter(f"{env_name} must be a number") from exc


def _litellm_key_readiness(*, production: bool) -> dict[str, Any]:
    master_key = os.getenv("LITELLM_MASTER_KEY", "")
    proxy_key = os.getenv("LITELLM_PROXY_API_KEY", "")
    checks = [
        {"name": "litellm_master_key", "status": STATUS_PASS if master_key else "fail"},
        {"name": "litellm_proxy_api_key", "status": STATUS_PASS if proxy_key else "fail"},
        {"name": "litellm_admin_api_base", "status": STATUS_PASS if _litellm_admin_api_base(None) else "fail"},
    ]
    if production:
        checks.append({"name": "proxy_key_is_virtual", "status": STATUS_PASS if proxy_key and proxy_key != master_key else "fail"})
    status = STATUS_PASS if all(check["status"] == STATUS_PASS for check in checks) else "fail"
    return {"status": status, "production": production, "checks": checks}


def _litellm_admin_api_base(api_base: str | None) -> str:
    return (api_base or os.getenv("LITELLM_ADMIN_API_BASE") or "http://127.0.0.1:4000").rstrip("/")


def _litellm_generate_key(*, api_base: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    master_key = os.getenv("LITELLM_MASTER_KEY")
    if not master_key:
        raise typer.BadParameter("LITELLM_MASTER_KEY is required to provision LiteLLM virtual keys")
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{_litellm_admin_api_base(api_base)}/key/generate",
        data=body,
        headers={"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise typer.BadParameter(f"LiteLLM key provisioning failed: HTTP {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise typer.BadParameter(f"LiteLLM key provisioning failed: {exc.reason}") from exc


@knowledge_app.command("init")
def knowledge_init(
    index: Path = typer.Option(Path(KNOWLEDGE_INDEX_PATH), "--index", help="Knowledge index JSON path."),
    registry: Path = typer.Option(Path("configs/source-registry.yaml"), "--registry", help="Source registry YAML path."),
    embedding_profile: str = typer.Option(EMBEDDING_PROFILE_LOCAL, "--embedding-profile", help="Embedding profile: local, production-openrouter, or production-openai."),
    embedding_model: Optional[str] = typer.Option(None, "--embedding-model", help="Optional embedding model override for the selected profile."),
    postgres_schema: Path = typer.Option(Path(POSTGRES_SCHEMA_PATH), "--postgres-schema", help="Write pgvector/Postgres schema reference SQL."),
    db_url: Optional[str] = typer.Option(None, "--db-url", envvar=KNOWLEDGE_DATABASE_URL_ENV, help="Postgres/pgvector database URL for production KB store."),
) -> None:
    embedding = resolve_embedding_config(embedding_profile=embedding_profile, embedding_model=embedding_model)
    report = build_knowledge_index(
        index_path=index,
        source_registry_path=registry,
        embedding_model=embedding_model,
        embedding_profile=embedding_profile,
        database_url=db_url,
    )
    ensure_parent(postgres_schema)
    postgres_schema.write_text(postgres_schema_sql(embedding_dimension=embedding["embedding_dimension"]), encoding="utf-8")
    typer.echo(
        f"status={report['status']} items={report['item_count']} chunks={report['chunk_count']} "
        f"store={report['store']['adapter']} embedding_profile={embedding_profile} "
        f"embedding_model={embedding['embedding_model']} dimension={embedding['embedding_dimension']} index={index}"
    )


@knowledge_app.command("ingest")
def knowledge_ingest(
    source: str = typer.Option(..., "--source", help="Internal file path to ingest into the knowledge index."),
    index: Path = typer.Option(Path(KNOWLEDGE_INDEX_PATH), "--index", help="Knowledge index JSON path."),
    embedding_profile: str = typer.Option(EMBEDDING_PROFILE_LOCAL, "--embedding-profile", help="Embedding profile: local, production-openrouter, or production-openai."),
    embedding_model: Optional[str] = typer.Option(None, "--embedding-model", help="Optional embedding model override for the selected profile."),
    db_url: Optional[str] = typer.Option(None, "--db-url", envvar=KNOWLEDGE_DATABASE_URL_ENV, help="Postgres/pgvector database URL for production KB store."),
) -> None:
    report = ingest_knowledge_source(source, index_path=index, embedding_model=embedding_model, embedding_profile=embedding_profile, database_url=db_url)
    typer.echo(f"status={report['status']} item={report['item_id']} chunks={report['chunk_count']} store={report.get('store', 'local_json')} index={index}")


@knowledge_app.command("search")
def knowledge_search(
    query: str = typer.Argument(..., help="Knowledge search query."),
    index: Path = typer.Option(Path(KNOWLEDGE_INDEX_PATH), "--index", help="Knowledge index JSON path."),
    stage: Optional[str] = typer.Option(None, "--stage", help="Optional workflow stage filter."),
    limit: int = typer.Option(6, "--limit", help="Maximum retrieved chunks."),
    out: Optional[Path] = typer.Option(None, "--out", help="Optional JSON report output path."),
    db_url: Optional[str] = typer.Option(None, "--db-url", envvar=KNOWLEDGE_DATABASE_URL_ENV, help="Postgres/pgvector database URL for production KB store."),
) -> None:
    from strategy_codebot.knowledge_base import RetrievalOptions

    report = search_knowledge(query, stage=stage, index_path=index, database_url=db_url, options=RetrievalOptions(limit=limit))
    if out:
        write_json(out, report)
    typer.echo(
        f"status={report['status']} chunks={len(report['retrieved_chunks'])} "
        f"latency_ms={report['retrieval_latency_ms']} embedding_ms={report.get('embedding_latency_ms')} "
        f"db_ms={report.get('db_search_latency_ms')} cache={report.get('embedding_cache_status')} "
        f"retrieval_cache={report.get('retrieval_cache_status')} cache_layer={report.get('cache_layer')} index={index}"
    )


@knowledge_app.command("health")
def knowledge_health_command(
    out: Optional[Path] = typer.Option(None, "--out", help="Optional JSON health report output path."),
    db_url: Optional[str] = typer.Option(None, "--db-url", envvar=KNOWLEDGE_DATABASE_URL_ENV, help="Postgres/pgvector database URL for production KB store."),
) -> None:
    report = knowledge_health(database_url=db_url)
    if out:
        write_json(out, report)
    typer.echo(
        f"status={report['status']} configured={str(report.get('configured')).lower()} "
        f"provider={report.get('embedding_provider')} model={report.get('embedding_model')}"
    )
    if report["status"] == "fail":
        raise typer.Exit(1)


@knowledge_app.command("eval")
def knowledge_eval(
    suite: Path = typer.Option(Path("examples/evals/knowledge-core.yaml"), "--suite", help="Knowledge retrieval eval suite YAML path."),
    index: Path = typer.Option(Path(KNOWLEDGE_INDEX_PATH), "--index", help="Knowledge index JSON path."),
    out: Path = typer.Option(Path("runs/evals/knowledge/eval-report.json"), "--out", help="Knowledge eval report output path."),
    db_url: Optional[str] = typer.Option(None, "--db-url", envvar=KNOWLEDGE_DATABASE_URL_ENV, help="Postgres/pgvector database URL for production KB store."),
) -> None:
    if not db_url and not resolve_repo_path(index).exists():
        build_knowledge_index(index_path=index, database_url=db_url)
    report = evaluate_knowledge_suite(suite, index_path=index, database_url=db_url, out_path=out)
    typer.echo(f"status={report['status']} cases={report['case_count']} failed={report['failed']} out={out}")
    if report["status"] != STATUS_PASS:
        raise typer.Exit(1)


@knowledge_candidates_app.command("list")
def knowledge_candidates_list(
    candidates: Path = typer.Option(Path(KNOWLEDGE_CANDIDATES_PATH), "--candidates", help="Knowledge candidates JSON path."),
    out: Optional[Path] = typer.Option(None, "--out", help="Optional JSON report output path."),
    db_url: Optional[str] = typer.Option(None, "--db-url", envvar=KNOWLEDGE_DATABASE_URL_ENV, help="Postgres/pgvector database URL for production KB store."),
) -> None:
    report = load_candidates(candidates, database_url=db_url)
    if out:
        write_json(out, report)
    typer.echo(f"candidates={len(report['candidates'])} store={'postgres_pgvector' if db_url else 'local_json'} path={candidates}")


@knowledge_candidates_app.command("propose")
def knowledge_candidates_propose(
    lesson: str = typer.Option(..., "--lesson", help="Lesson text to propose for approval."),
    evidence_ref: str = typer.Option(..., "--evidence-ref", help="Evidence artifact reference."),
    candidate_type: str = typer.Option("episodic", "--type", help="Knowledge type for the candidate."),
    candidates: Path = typer.Option(Path(KNOWLEDGE_CANDIDATES_PATH), "--candidates", help="Knowledge candidates JSON path."),
    db_url: Optional[str] = typer.Option(None, "--db-url", envvar=KNOWLEDGE_DATABASE_URL_ENV, help="Postgres/pgvector database URL for production KB store."),
) -> None:
    candidate = propose_candidate(lesson, evidence_ref=evidence_ref, candidate_type=candidate_type, path=candidates, database_url=db_url)
    typer.echo(f"status={candidate['status']} candidate={candidate['candidate_id']}")


@knowledge_candidates_app.command("approve")
def knowledge_candidates_approve(
    candidate_id: str = typer.Argument(..., help="Candidate id to approve."),
    index: Path = typer.Option(Path(KNOWLEDGE_INDEX_PATH), "--index", help="Knowledge index JSON path."),
    candidates: Path = typer.Option(Path(KNOWLEDGE_CANDIDATES_PATH), "--candidates", help="Knowledge candidates JSON path."),
    db_url: Optional[str] = typer.Option(None, "--db-url", envvar=KNOWLEDGE_DATABASE_URL_ENV, help="Postgres/pgvector database URL for production KB store."),
) -> None:
    report = approve_candidate(candidate_id, index_path=index, candidates_path=candidates, database_url=db_url)
    typer.echo(f"status={report['status']} candidate={candidate_id} item={report['item_id']}")


@knowledge_candidates_app.command("reject")
def knowledge_candidates_reject(
    candidate_id: str = typer.Argument(..., help="Candidate id to reject."),
    candidates: Path = typer.Option(Path(KNOWLEDGE_CANDIDATES_PATH), "--candidates", help="Knowledge candidates JSON path."),
    db_url: Optional[str] = typer.Option(None, "--db-url", envvar=KNOWLEDGE_DATABASE_URL_ENV, help="Postgres/pgvector database URL for production KB store."),
) -> None:
    report = reject_candidate(candidate_id, candidates_path=candidates, database_url=db_url)
    typer.echo(f"status={report['status']} candidate={candidate_id}")


@knowledge_app.command("learn-from-run")
def knowledge_learn_from_run(
    artifacts_root: Path = typer.Option(..., "--artifacts-root", help="Live/eval artifact root containing harness reports and run traces."),
    approval_mode: str = typer.Option("agent-auto", "--approval-mode", help="Approval mode: agent-auto or manual."),
    index: Path = typer.Option(Path(KNOWLEDGE_INDEX_PATH), "--index", help="Knowledge index JSON path."),
    candidates: Path = typer.Option(Path(KNOWLEDGE_CANDIDATES_PATH), "--candidates", help="Knowledge candidates JSON path."),
    out: Path = typer.Option(Path(".strategy-codebot/knowledge-learning-report.json"), "--out", help="Learning report output path."),
    db_url: Optional[str] = typer.Option(None, "--db-url", envvar=KNOWLEDGE_DATABASE_URL_ENV, help="Postgres/pgvector database URL for production KB store."),
) -> None:
    report = learn_from_run(
        artifacts_root,
        approval_mode=approval_mode,
        index_path=index,
        candidates_path=candidates,
        database_url=db_url,
        out=out,
    )
    typer.echo(
        f"status={report['status']} extracted={report['extracted_count']} "
        f"candidates={report['candidate_count']} promoted={report['promoted_count']} "
        f"skipped={report['skipped_count']} rejected={report['rejected_count']} out={out}"
    )


@knowledge_app.command("check")
def knowledge_check(
    registry: Path = typer.Option(Path("configs/source-registry.yaml"), "--registry", help="Source registry YAML path."),
    offline: bool = typer.Option(True, "--offline/--fetch", help="Only validate metadata and URL shape."),
    out: Path = typer.Option(Path("reports/source-check.json"), "--out", help="Report output path."),
) -> None:
    report = check_registry(_resolve_input_path(registry), offline=offline)
    validate_payload(report, "validation-report.schema.json")
    write_json(out, report)
    typer.echo(f"status={report['status']} out={out}")


@knowledge_app.command("snapshot")
def knowledge_snapshot(
    registry: Path = typer.Option(Path("configs/source-registry.yaml"), "--registry", help="Source registry YAML path."),
    source_id: Optional[str] = typer.Option(None, "--source-id", help="Trusted public source id to fetch into a source snapshot."),
    offline: bool = typer.Option(True, "--offline/--fetch", help="Use deterministic offline metadata instead of fetching external URLs."),
    out: Path = typer.Option(Path("knowledge/snapshots/current.json"), "--out", help="Snapshot output path."),
) -> None:
    if source_id:
        if offline:
            raise typer.BadParameter("--source-id snapshots require --fetch so generation never fetches implicitly.")
        source_out = None if out == Path("knowledge/snapshots/current.json") else out
        source_snapshot = snapshot_trusted_source(source_id, registry_path=_resolve_input_path(registry), out=source_out)
        typer.echo(
            f"status=pass source={source_snapshot['source_id']} state={source_snapshot['source_state']} "
            f"hash={source_snapshot['content_hash']} out={source_snapshot['snapshot_ref']}"
        )
        return
    snapshot = create_snapshot(_resolve_input_path(registry), offline=offline)
    validate_payload(snapshot, "knowledge-snapshot.schema.json")
    write_json(out, snapshot)
    typer.echo(f"sources={len(snapshot['sources'])} fetch_mode={snapshot['fetch_mode']} out={out}")


@knowledge_app.command("summarize-snapshot")
def knowledge_summarize_snapshot(
    snapshot: Path = typer.Option(..., "--snapshot", help="Trusted-source snapshot JSON path."),
    out: Path = typer.Option(Path("knowledge/proposals/source-summary.json"), "--out", help="Source summary proposal output path."),
) -> None:
    proposal_out = None if out == Path("knowledge/proposals/source-summary.json") else out
    proposal = summarize_source_snapshot(snapshot, out=proposal_out)
    typer.echo(f"status={proposal['status']} source={proposal['source_id']} proposal={proposal['proposal_id']} out={proposal['proposal_ref']}")


@knowledge_app.command("approve-source-summary")
def knowledge_approve_source_summary(
    proposal: Path = typer.Option(..., "--proposal", help="Source summary proposal JSON path."),
    index: Path = typer.Option(Path(KNOWLEDGE_INDEX_PATH), "--index", help="Knowledge index JSON path."),
    db_url: Optional[str] = typer.Option(None, "--db-url", envvar=KNOWLEDGE_DATABASE_URL_ENV, help="Postgres/pgvector database URL for production KB store."),
) -> None:
    report = approve_source_summary(proposal, index_path=index, database_url=db_url)
    typer.echo(f"status={report['status']} source={report['source_id']} item={report['item_id']} store={report['store']}")


@knowledge_app.command("diff")
def knowledge_diff(
    baseline: Path = typer.Option(..., "--baseline", help="Baseline knowledge snapshot JSON path."),
    current: Path = typer.Option(..., "--current", help="Current knowledge snapshot JSON path."),
    out: Path = typer.Option(Path("reports/knowledge-diff.json"), "--out", help="Diff report output path."),
) -> None:
    report = diff_snapshots(baseline, current)
    validate_payload(report, "knowledge-diff.schema.json")
    write_json(out, report)
    typer.echo(f"status={report['status']} out={out}")


@knowledge_app.command("audit")
def knowledge_audit(
    runs: Path = typer.Option(..., "--runs", help="Run directory containing validation/review/runtime artifacts."),
    out: Path = typer.Option(Path("reports/knowledge-audit.json"), "--out", help="Audit report output path."),
) -> None:
    report = audit_run(runs)
    write_json(out, report)
    typer.echo(f"status={report['status']} out={out}")


@knowledge_app.command("propose")
def knowledge_propose(
    diff: Path = typer.Option(..., "--diff", help="Knowledge diff report path."),
    audit: Optional[Path] = typer.Option(None, "--audit", help="Knowledge audit report path."),
    runs: Optional[Path] = typer.Option(None, "--runs", help="Run directory to audit inline when --audit is omitted."),
    out: Path = typer.Option(Path("knowledge/proposals/proposal.json"), "--out", help="Knowledge proposal output path."),
) -> None:
    proposal = create_proposal(diff, audit_path=audit, runs_path=runs)
    validate_payload(proposal, "knowledge-proposal.schema.json")
    write_json(out, proposal)
    typer.echo(f"status={proposal['status']} risk={proposal['risk_level']} out={out}")


@tools_app.command("list")
def tools_list(
    registry: Path = typer.Option(Path("configs/tool-registry.yaml"), "--registry", help="Tool registry YAML path."),
) -> None:
    for tool_id in tool_ids(_resolve_input_path(registry)):
        typer.echo(tool_id)


@tools_app.command("check")
def tools_check(
    registry: Path = typer.Option(Path("configs/tool-registry.yaml"), "--registry", help="Tool registry YAML path."),
    out: Path = typer.Option(Path("reports/tool-check.json"), "--out", help="Tool registry check output path."),
) -> None:
    report = check_tool_registry(_resolve_input_path(registry))
    validate_payload(report, "validation-report.schema.json")
    write_json(out, report)
    typer.echo(f"status={report['status']} out={out}")


@eval_app.command("live")
def eval_live(
    suite: Path = typer.Option(Path("examples/evals/live-core.yaml"), "--suite", help="Live eval suite YAML path."),
    out: Path = typer.Option(Path("runs/evals/live"), "--out", help="Eval output directory."),
    policy: str = typer.Option("enforce", "--policy", help="observe or enforce."),
    model_registry: Optional[Path] = typer.Option(None, "--model-registry", help="Live mode model registry YAML path."),
    model: Optional[str] = typer.Option(None, "--model", help="Live mode model override."),
    workflow: str = typer.Option(WORKFLOW_MULTI_AGENT, "--workflow", help="multi-agent, single, or compact-free."),
    cost_profile: str = typer.Option(COST_PROFILE_QUALITY, "--cost-profile", help="quality or cheap."),
    user_tier: str = typer.Option(DEFAULT_USER_TIER, "--user-tier", help="free, paid_low, paid_medium, or paid_high."),
    model_stage: Optional[list[str]] = typer.Option(None, "--model-stage", help="Live multi-agent stage override as stage=model. Repeatable."),
    save_raw_provider: bool = typer.Option(True, "--save-raw-provider/--no-save-raw-provider", help="Write raw live provider response artifacts."),
    knowledge_context: str = typer.Option("auto", "--knowledge-context", help="auto or off."),
    prompt_profile: str = typer.Option(PROMPT_PROFILE_DEFAULT, "--prompt-profile", help="current or optimized_v1."),
    web_search: str = typer.Option(WEB_SEARCH_DEFAULT, "--web-search", help="off, auto, or on. Defaults to auto smart-search."),
    require_web_search: bool = typer.Option(False, "--require-web-search/--no-require-web-search", help="Fail live eval cases when requested web search is unavailable."),
    otel_export: Optional[Path] = typer.Option(None, "--otel-export", help="Write local OpenTelemetry-compatible JSONL spans."),
    concurrency: int = typer.Option(2, "--concurrency", help="Max live eval cases to run concurrently, capped at 8."),
    case_timeout_seconds: int = typer.Option(600, "--case-timeout-seconds", help="Hard timeout for each live eval case."),
) -> None:
    live_options = LiveRunOptions(
        model_override=model,
        model_stage_overrides=_parse_model_stage_overrides(model_stage),
        workflow=workflow,
        cost_profile=cost_profile,
        user_tier=user_tier,
        save_raw_provider=save_raw_provider,
        knowledge_context=knowledge_context,
        prompt_profile=prompt_profile,
        web_search=web_search,
        require_web_search=require_web_search,
    )
    report = run_live_eval(
        suite_path=suite,
        out_dir=out,
        policy=policy,
        model_registry=model_registry,
        live_options=live_options,
        otel_export=otel_export,
        concurrency=concurrency,
        case_timeout_seconds=case_timeout_seconds,
    )
    typer.echo(f"status={report['status']} cases={report['case_count']} failed={report['failed']} out={out}")
    if report["status"] != STATUS_PASS:
        raise typer.Exit(1)


@eval_app.command("matrix")
def eval_matrix(
    smoke_suite: Path = typer.Option(Path("examples/evals/live-smoke.yaml"), "--smoke-suite", help="Smoke eval suite YAML path."),
    full_suite: Path = typer.Option(Path("examples/evals/live-core.yaml"), "--full-suite", help="Full eval suite YAML path."),
    out: Path = typer.Option(Path("runs/evals/model-matrix"), "--out", help="Model combo matrix output directory."),
    policy: str = typer.Option("enforce", "--policy", help="observe or enforce."),
    model_registry: Optional[Path] = typer.Option(None, "--model-registry", help="Live mode model registry YAML path."),
    mode: str = typer.Option("combo", "--mode", help="combo or tier."),
    combo: Optional[list[str]] = typer.Option(None, "--combo", help="Model combo id to run. Repeatable; defaults to all combos."),
    tier: Optional[list[str]] = typer.Option(None, "--tier", help="User tier to run in tier mode. Repeatable; defaults to all tiers."),
    run_full: bool = typer.Option(False, "--run-full/--smoke-only", help="Run full suite for combos that pass smoke."),
    save_raw_provider: bool = typer.Option(True, "--save-raw-provider/--no-save-raw-provider", help="Write raw live provider response artifacts."),
    knowledge_context: str = typer.Option("auto", "--knowledge-context", help="auto or off."),
    concurrency: int = typer.Option(1, "--concurrency", help="Max live eval cases to run concurrently, capped at 8."),
    case_timeout_seconds: int = typer.Option(300, "--case-timeout-seconds", help="Hard timeout for each matrix eval case."),
) -> None:
    report = run_model_combo_matrix(
        smoke_suite_path=smoke_suite,
        full_suite_path=full_suite,
        out_dir=out,
        policy=policy,
        model_registry=model_registry,
        combo_ids=combo,
        matrix_mode=mode,
        tier_ids=tier,
        run_full=run_full,
        save_raw_provider=save_raw_provider,
        concurrency=concurrency,
        case_timeout_seconds=case_timeout_seconds,
        knowledge_context=knowledge_context,
    )
    typer.echo(
        f"status={report['status']} mode={report.get('mode', 'combo')} combos={len(report.get('combos', []))} "
        f"tiers={len(report.get('tiers', []))} recommended={report.get('recommended_tier') or report.get('recommended_combo')} out={out}"
    )
    if report["status"] != STATUS_PASS:
        raise typer.Exit(1)


@harness_app.command("inspect")
def harness_inspect(
    run_dir: Path = typer.Option(..., "--run-dir", help="Run directory containing agent/runtime artifacts."),
    out: Path = typer.Option(Path("agent-harness-report.json"), "--out", help="Harness inspection report output path."),
) -> None:
    report = inspect_run(run_dir, out_path=out)
    typer.echo(f"status={report['status']} run_id={report['run_id']} out={out}")
    if report["status"] != STATUS_PASS:
        raise typer.Exit(1)


@harness_app.command("confirm")
def harness_confirm(
    spec: Path = typer.Option(Path("examples/specs/ma-crossover-pine.json"), "--spec", help="Strategy spec used for the harness confirmation run."),
    out: Path = typer.Option(Path("runs/harness-confirm"), "--out", help="Output directory for the confirmation run."),
) -> None:
    cli_path = harness_cli_path()
    if not cli_path.exists():
        typer.echo(f"status=fail reason=missing_harness_cli path={cli_path}")
        raise typer.Exit(1)
    if not os.access(cli_path, os.X_OK):
        typer.echo(f"status=fail reason=harness_cli_not_executable path={cli_path}")
        raise typer.Exit(1)

    result = run_strategy(
        spec_path=spec,
        prompt=None,
        mode="dry-run",
        out_dir=out,
        review=REVIEW_MODE_NONE,
        record_harness=True,
        runtime_trace=True,
        policy=POLICY_OBSERVE,
    )
    trace_path = out / "runtime-trace.jsonl"
    if not _trace_has_completed_harness_record(trace_path):
        typer.echo(f"status=fail reason=missing_record_harness_trace run_id={result['run_id']} trace={trace_path}")
        raise typer.Exit(1)

    report = inspect_run(out)
    typer.echo(f"status=pass run_id={result['run_id']} out={out} harness_cli={cli_path}")
    if report["status"] != STATUS_PASS:
        raise typer.Exit(1)


def _trace_values(values: Optional[list[str]], fallback: str) -> list[str]:
    cleaned = [value.strip() for value in values or [] if value.strip()]
    return cleaned or [fallback]


def _trace_outcome_decisions(
    *,
    test_outcome: str | None,
    review_outcome: str | None,
    review_evidence: list[str] | None = None,
    review_justification: str | None = None,
    validation_outcome: str | None = None,
    production_impact: str | None = None,
) -> list[str]:
    decisions = []
    for key, value in (
        ("test_outcome", test_outcome),
        ("review_outcome", review_outcome),
        ("validation_outcome", validation_outcome),
        ("production_impact", production_impact),
    ):
        if value and value.strip():
            decisions.append(f"{key}={value.strip()}")
    for value in review_evidence or []:
        if value and value.strip():
            decisions.append(f"review_evidence={_bounded_trace_metadata(value)}")
    if review_justification and review_justification.strip():
        decisions.append(f"review_justification={_bounded_trace_metadata(review_justification)}")
    return decisions


def _bounded_trace_metadata(value: str, limit: int = 240) -> str:
    text = " ".join(value.strip().split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _session_state_path(path: Path) -> Path:
    return path if path.is_absolute() else repo_root() / path


def _startup_state_path(path: Path) -> Path:
    return path if path.is_absolute() else repo_root() / path


def _local_report_path(path: Path) -> Path:
    return path if path.is_absolute() else repo_root() / path


def _parse_started_at(value: str) -> float:
    text = value.strip()
    try:
        return float(text)
    except ValueError:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()


def _duration_from_started_at(value: str, now: float | None = None) -> int:
    return max(0, int((now if now is not None else time()) - _parse_started_at(value)))


def _read_session_started_at(path: Path) -> str | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    value = payload.get("started_at_epoch") or payload.get("started_at")
    return str(value) if value is not None else None


def _read_startup_preflight(path: Path) -> dict | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not payload.get("preflight_applied"):
        return None
    return payload


def _trace_preflight_decisions(*, preflight_applied: bool, preflight_ref: str | None, preflight_brief_count: int | None) -> list[str]:
    if not preflight_applied:
        return []
    decisions = ["preflight_applied=true"]
    if preflight_ref:
        decisions.append(f"preflight_ref={preflight_ref}")
    if preflight_brief_count is not None:
        decisions.append(f"preflight_brief_count={preflight_brief_count}")
    return decisions


def _append_duration_unavailable_note(notes: str | None) -> str:
    suffix = "duration unavailable"
    if not notes:
        return suffix
    if suffix in notes.lower():
        return notes
    return f"{notes}; {suffix}"


def _write_recommendation_story(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Harness Recommendation Follow-up", "", "Generated from explicit `harness recommend-next --write-story`.", ""]
    for item in report.get("recommendations", []):
        lines.extend(
            [
                f"## {item['id']}",
                "",
                f"- Severity: {item['severity']}",
                f"- Source traces: {', '.join(str(trace_id) for trace_id in item['source_trace_ids'])}",
                f"- Reason: {item['reason']}",
                f"- Suggested action: {item['suggested_action']}",
                "",
            ]
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


@harness_app.command("session-start")
def harness_session_start(
    summary: str = typer.Option(..., "--summary", help="Short description of the dev session."),
    session_state: Path = typer.Option(DEFAULT_HARNESS_SESSION_STATE, "--session-state", help="Session state JSON path."),
) -> None:
    state_path = _session_state_path(session_state)
    started_at = datetime.now(UTC)
    payload = {
        "summary": summary,
        "started_at": started_at.isoformat(),
        "started_at_epoch": started_at.timestamp(),
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(state_path, payload)
    typer.echo(f"status=pass summary={summary} started_at={payload['started_at']} state={state_path}")


@harness_app.command("agent-start")
def harness_agent_start(
    summary: str = typer.Option(..., "--summary", help="Short description of the non-trivial agent session."),
    latest: int = typer.Option(10, "--latest", min=1, help="Number of latest traces to inspect for preflight."),
    artifacts_root: Optional[Path] = typer.Option(None, "--artifacts-root", help="Artifact root used for development evidence lookup."),
    preflight_out: Path = typer.Option(DEFAULT_HARNESS_PREFLIGHT_REPORT, "--preflight-out", help="Preflight JSON report path."),
    session_state: Path = typer.Option(DEFAULT_HARNESS_SESSION_STATE, "--session-state", help="Session state JSON path."),
    startup_state: Path = typer.Option(DEFAULT_HARNESS_STARTUP_STATE, "--startup-state", help="Startup state JSON path consumed by dev-trace."),
) -> None:
    started_at = datetime.now(UTC)
    session_path = _session_state_path(session_state)
    session_payload = {
        "summary": summary,
        "started_at": started_at.isoformat(),
        "started_at_epoch": started_at.timestamp(),
    }
    write_json(session_path, session_payload)
    try:
        preflight = preflight_context(latest=latest, artifacts_root=artifacts_root)
    except FileNotFoundError as exc:
        typer.echo(f"status=fail reason=missing_harness_db error={exc}")
        raise typer.Exit(1) from exc
    preflight_path = _local_report_path(preflight_out)
    write_json(preflight_path, preflight)
    startup_path = _startup_state_path(startup_state)
    startup_payload = {
        "summary": summary,
        "started_at": started_at.isoformat(),
        "started_at_epoch": started_at.timestamp(),
        "preflight_applied": True,
        "preflight_ref": str(preflight_path),
        "preflight_status": preflight["status"],
        "preflight_brief_count": len(preflight["context_brief"]),
        "anti_pollution": preflight["anti_pollution"],
    }
    write_json(startup_path, startup_payload)
    typer.echo(
        f"status={preflight['status']} summary={summary} bullets={len(preflight['context_brief'])} "
        f"session_state={session_path} startup_state={startup_path} preflight={preflight_path}"
    )


@harness_app.command("audit-traces")
def harness_audit_traces(
    latest: int = typer.Option(1, "--latest", min=1, help="Number of latest traces to audit."),
    since_id: Optional[int] = typer.Option(None, "--since-id", min=1, help="Audit traces with id >= this value."),
    all_traces: bool = typer.Option(False, "--all", help="Audit all traces, including legacy rows."),
    out: Optional[Path] = typer.Option(None, "--out", help="Optional JSON report output path."),
) -> None:
    try:
        report = audit_traces(latest=latest, since_id=since_id, include_all=all_traces)
    except FileNotFoundError as exc:
        typer.echo(f"status=fail reason=missing_harness_db error={exc}")
        raise typer.Exit(1) from exc
    if out:
        write_json(out, report)
    typer.echo(f"status={report['status']} checked={report['checked']} failed={report['failed']} out={out or 'none'}")
    if report["status"] != STATUS_PASS:
        raise typer.Exit(1)


@harness_app.command("summarize-traces")
def harness_summarize_traces(
    latest: int = typer.Option(20, "--latest", min=1, help="Number of latest traces to summarize."),
    artifacts_root: Optional[Path] = typer.Option(None, "--artifacts-root", help="Artifact root used for development evidence lookup."),
    include_development_evidence: bool = typer.Option(False, "--include-development-evidence", help="Include verification and production evidence aggregates."),
    out: Optional[Path] = typer.Option(None, "--out", help="Optional JSON report output path."),
) -> None:
    try:
        report = summarize_traces(
            latest=latest,
            artifacts_root=artifacts_root,
            include_development_evidence=include_development_evidence,
        )
    except FileNotFoundError as exc:
        typer.echo(f"status=fail reason=missing_harness_db error={exc}")
        raise typer.Exit(1) from exc
    if out:
        write_json(out, report)
    typer.echo(
        f"status={report['status']} traces={report['trace_count']} linked={report['linked_count']} "
        f"unlinked={report['unlinked_count']} friction={report['friction_trace_count']} "
        f"high_risk={report['high_risk_count']} duration_total={report['duration_seconds']['total']} "
        f"out={out or 'none'}"
    )


@harness_app.command("assess-development")
def harness_assess_development(
    latest: int = typer.Option(20, "--latest", min=1, help="Number of latest traces to assess."),
    artifacts_root: Optional[Path] = typer.Option(None, "--artifacts-root", help="Artifact root used for development evidence lookup."),
    out: Optional[Path] = typer.Option(None, "--out", help="Optional JSON report output path."),
) -> None:
    try:
        report = assess_development(latest=latest, artifacts_root=artifacts_root)
    except FileNotFoundError as exc:
        typer.echo(f"status=fail reason=missing_harness_db error={exc}")
        raise typer.Exit(1) from exc
    if out:
        write_json(out, report)
    typer.echo(
        f"status={report['status']} traces={report['process_quality']['trace_count']} "
        f"high_risk={report['process_quality']['high_risk_count']} "
        f"verified={report['engineering_quality']['verified_count']} "
        f"missing_evidence={report['engineering_quality']['missing_verification_evidence']} "
        f"production_fail={report['production_impact']['fail']} out={out or 'none'}"
    )
    if report["status"] == "fail":
        raise typer.Exit(1)


@harness_app.command("preflight")
def harness_preflight(
    latest: int = typer.Option(10, "--latest", min=1, help="Number of latest traces to inspect."),
    artifacts_root: Optional[Path] = typer.Option(None, "--artifacts-root", help="Artifact root used for development evidence lookup."),
    out: Path = typer.Option(DEFAULT_HARNESS_PREFLIGHT_REPORT, "--out", help="JSON report output path."),
) -> None:
    try:
        report = preflight_context(latest=latest, artifacts_root=artifacts_root)
    except FileNotFoundError as exc:
        typer.echo(f"status=fail reason=missing_harness_db error={exc}")
        raise typer.Exit(1) from exc
    out_path = _local_report_path(out)
    write_json(out_path, report)
    typer.echo(
        f"status={report['status']} bullets={len(report['context_brief'])} "
        f"checked={report['audit']['checked']} missing_evidence={report['assessment']['missing_evidence']} out={out_path}"
    )


@harness_app.command("gate-development")
def harness_gate_development(
    latest: int = typer.Option(5, "--latest", min=1, help="Number of latest traces to gate."),
    policy: str = typer.Option("observe", "--policy", help="Gate policy: observe or enforce."),
    artifacts_root: Optional[Path] = typer.Option(None, "--artifacts-root", help="Artifact root used for development evidence lookup."),
    out: Optional[Path] = typer.Option(None, "--out", help="Optional JSON report output path."),
) -> None:
    try:
        report = gate_development(latest=latest, policy=policy, artifacts_root=artifacts_root)
    except FileNotFoundError as exc:
        typer.echo(f"status=fail reason=missing_harness_db error={exc}")
        raise typer.Exit(1) from exc
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if out:
        write_json(_local_report_path(out), report)
    typer.echo(f"status={report['status']} policy={report['policy']} issues={len(report['issues'])} out={out or 'none'}")
    if report["status"] == "fail":
        raise typer.Exit(1)


@harness_app.command("recommend-next")
def harness_recommend_next(
    latest: int = typer.Option(10, "--latest", min=1, help="Number of latest traces to analyze."),
    artifacts_root: Optional[Path] = typer.Option(None, "--artifacts-root", help="Artifact root used for development evidence lookup."),
    out: Path = typer.Option(DEFAULT_HARNESS_RECOMMENDATIONS_REPORT, "--out", help="JSON recommendations output path."),
    write_story: Optional[Path] = typer.Option(None, "--write-story", help="Explicit durable story path to write from recommendations."),
) -> None:
    try:
        report = recommend_next(latest=latest, artifacts_root=artifacts_root)
    except FileNotFoundError as exc:
        typer.echo(f"status=fail reason=missing_harness_db error={exc}")
        raise typer.Exit(1) from exc
    out_path = _local_report_path(out)
    write_json(out_path, report)
    if write_story:
        _write_recommendation_story(_local_report_path(write_story), report)
        report["anti_pollution"]["stories_written"] = True
        write_json(out_path, report)
    typer.echo(f"status={report['status']} recommendations={report['recommendation_count']} out={out_path}")


@harness_app.command("memory-candidates")
def harness_memory_candidates(
    latest: int = typer.Option(20, "--latest", min=1, help="Number of latest traces to analyze."),
    artifacts_root: Optional[Path] = typer.Option(None, "--artifacts-root", help="Artifact root used for development evidence lookup."),
    out: Path = typer.Option(DEFAULT_HARNESS_MEMORY_CANDIDATES_REPORT, "--out", help="JSON memory-candidate output path."),
) -> None:
    try:
        report = memory_candidates(latest=latest, artifacts_root=artifacts_root)
    except FileNotFoundError as exc:
        typer.echo(f"status=fail reason=missing_harness_db error={exc}")
        raise typer.Exit(1) from exc
    out_path = _local_report_path(out)
    write_json(out_path, report)
    typer.echo(f"status={report['status']} candidates={report['candidate_count']} memory_written=false out={out_path}")


@harness_app.command("intelligence-report")
def harness_intelligence_report(
    artifacts_root: Optional[Path] = typer.Option(None, "--artifacts-root", help="Artifact root or JSON report to analyze."),
    out: Path = typer.Option(DEFAULT_INTELLIGENCE_REPORT_PATH, "--out", help="JSON intelligence report output path."),
) -> None:
    report = build_intelligence_report(artifacts_root=artifacts_root)
    out_path = _local_report_path(out)
    write_json(out_path, report)
    typer.echo(
        f"status={report['status']} cases={report['case_count']} live_traces={report['live_trace_count']} "
        f"routes={len(report['scorecard'])} recommendations={len(report['route_recommendations'])} out={out_path}"
    )


@harness_app.command("latency-report")
def harness_latency_report(
    artifacts_root: Optional[Path] = typer.Option(None, "--artifacts-root", help="Artifact root or JSON report to analyze."),
    out: Path = typer.Option(DEFAULT_LATENCY_REPORT_PATH, "--out", help="JSON latency report output path."),
) -> None:
    report = build_latency_report(artifacts_root=artifacts_root)
    out_path = _local_report_path(out)
    write_json(out_path, report)
    summary = report["latency_summary"]
    typer.echo(
        f"status={report['status']} runs={report['live_trace_count']} samples={summary['sample_count']} "
        f"diagnosis={','.join(summary['diagnosis'])} out={out_path}"
    )


@harness_app.command("context-report")
def harness_context_report(
    artifacts_root: Optional[Path] = typer.Option(None, "--artifacts-root", help="Artifact root or JSON report to analyze."),
    out: Path = typer.Option(DEFAULT_CONTEXT_REPORT_PATH, "--out", help="JSON context contract report output path."),
) -> None:
    out_path = _local_report_path(out)
    report = build_context_report(artifacts_root=artifacts_root, out=out_path)
    typer.echo(
        f"status={report['status']} traces={report['live_trace_count']} stages={report['stage_count']} "
        f"missing={report['missing_context_count']} budget_warnings={report['budget_warning_count']} out={out_path}"
    )


@harness_app.command("latency-matrix")
def harness_latency_matrix(
    suite: Path = typer.Option(..., "--suite", help="Live eval suite YAML path."),
    out_root: Path = typer.Option(Path(".strategy-codebot/latency-matrix"), "--out-root", help="Per-run artifact root."),
    out: Path = typer.Option(DEFAULT_LATENCY_MATRIX_PATH, "--out", help="JSON latency matrix output path."),
    runs: int = typer.Option(3, "--runs", min=1, help="Number of repeated eval runs."),
    policy: str = typer.Option("enforce", "--policy", help="Safety policy: enforce or observe."),
    workflow: str = typer.Option(WORKFLOW_MULTI_AGENT, "--workflow", help="multi-agent, single, or compact-free."),
    cost_profile: str = typer.Option("cheap", "--cost-profile", help="quality or cheap."),
    user_tier: str = typer.Option("paid_low", "--user-tier", help="free, paid_low, paid_medium, or paid_high."),
    concurrency: int = typer.Option(1, "--concurrency", min=1, help="Live eval concurrency."),
    case_timeout_seconds: int = typer.Option(300, "--case-timeout-seconds", min=1, help="Per-case timeout."),
    knowledge_context: str = typer.Option("auto", "--knowledge-context", help="auto or off."),
    prompt_profile: str = typer.Option(PROMPT_PROFILE_DEFAULT, "--prompt-profile", help="current or optimized_v1."),
) -> None:
    out_path = _local_report_path(out)
    payload = build_latency_matrix(
        suite=suite,
        out_root=out_root,
        runs=runs,
        policy=policy,
        workflow=workflow,
        cost_profile=cost_profile,
        user_tier=user_tier,
        concurrency=concurrency,
        case_timeout_seconds=case_timeout_seconds,
        knowledge_context=knowledge_context,
        prompt_profile=prompt_profile,
        out=out_path,
    )
    candidates = len(payload.get("route_policy_candidates", []))
    typer.echo(
        f"status={payload['status']} runs={payload['runs_completed']}/{payload['runs_requested']} "
        f"samples={payload['latency_summary']['sample_count']} candidates={candidates} out={out_path}"
    )


@harness_app.command("prompt-matrix")
def harness_prompt_matrix(
    suite: Path = typer.Option(..., "--suite", help="Live eval suite YAML path."),
    out_root: Path = typer.Option(Path(".strategy-codebot/prompt-matrix"), "--out-root", help="Per-profile artifact root."),
    out: Path = typer.Option(DEFAULT_PROMPT_MATRIX_PATH, "--out", help="JSON prompt matrix output path."),
    profiles: str = typer.Option(",".join(DEFAULT_PROMPT_MATRIX_PROFILES), "--profiles", help="Comma-separated prompt profiles to compare."),
    runs: int = typer.Option(1, "--runs", min=1, help="Number of repeated eval runs per profile."),
    policy: str = typer.Option("enforce", "--policy", help="Safety policy: enforce or observe."),
    workflow: str = typer.Option(WORKFLOW_MULTI_AGENT, "--workflow", help="multi-agent, single, or compact-free."),
    cost_profile: str = typer.Option("cheap", "--cost-profile", help="quality or cheap."),
    user_tier: str = typer.Option("paid_low", "--user-tier", help="free, paid_low, paid_medium, or paid_high."),
    concurrency: int = typer.Option(1, "--concurrency", min=1, help="Live eval concurrency."),
    case_timeout_seconds: int = typer.Option(300, "--case-timeout-seconds", min=1, help="Per-case timeout."),
    knowledge_context: str = typer.Option("auto", "--knowledge-context", help="auto or off."),
) -> None:
    out_path = _local_report_path(out)
    prompt_profiles = normalize_prompt_profiles(profiles.split(","))
    payload = build_prompt_matrix(
        suite=suite,
        out_root=out_root,
        out=out_path,
        profiles=prompt_profiles,
        runs=runs,
        policy=policy,
        workflow=workflow,
        cost_profile=cost_profile,
        user_tier=user_tier,
        concurrency=concurrency,
        case_timeout_seconds=case_timeout_seconds,
        knowledge_context=knowledge_context,
    )
    typer.echo(
        f"status={payload['status']} profiles={payload['profile_count']} "
        f"runs={payload['runs']} out={out_path}"
    )
    if payload["status"] != STATUS_PASS:
        raise typer.Exit(1)


@harness_app.command("model-candidate-matrix")
def harness_model_candidate_matrix(
    suite: Path = typer.Option(Path("examples/evals/price-action-smoke.yaml"), "--suite", help="Live eval suite YAML path."),
    out_root: Path = typer.Option(Path(".strategy-codebot/model-candidate-matrix"), "--out-root", help="Per-candidate artifact root."),
    out: Path = typer.Option(Path(".strategy-codebot") / MODEL_CANDIDATE_MATRIX_REPORT_PATH, "--out", help="JSON candidate matrix output path."),
    runs: int = typer.Option(1, "--runs", min=1, help="Number of repeated eval runs per candidate."),
    policy: str = typer.Option("enforce", "--policy", help="Safety policy: enforce or observe."),
    workflow: str = typer.Option(WORKFLOW_MULTI_AGENT, "--workflow", help="multi-agent, single, or compact-free."),
    cost_profile: str = typer.Option("cheap", "--cost-profile", help="quality or cheap."),
    user_tier: str = typer.Option("paid_low", "--user-tier", help="free, paid_low, paid_medium, or paid_high."),
    stage: Optional[list[str]] = typer.Option(None, "--stage", help="Restrict benchmark to stage. Repeatable."),
    candidate: Optional[list[str]] = typer.Option(None, "--candidate", help="Additional candidate as stage=model. Repeatable."),
    concurrency: int = typer.Option(1, "--concurrency", min=1, help="Live eval concurrency."),
    case_timeout_seconds: int = typer.Option(300, "--case-timeout-seconds", min=1, help="Per-case timeout."),
    knowledge_context: str = typer.Option("auto", "--knowledge-context", help="auto or off."),
    fetch_catalog: bool = typer.Option(True, "--fetch-catalog/--no-fetch-catalog", help="Fetch OpenRouter model metadata before running."),
) -> None:
    out_path = _local_report_path(out)
    payload = build_model_candidate_matrix(
        suite=suite,
        out_root=out_root,
        out=out_path,
        runs=runs,
        policy=policy,
        workflow=workflow,
        cost_profile=cost_profile,
        user_tier=user_tier,
        stages=stage,
        candidates=candidate,
        concurrency=concurrency,
        case_timeout_seconds=case_timeout_seconds,
        knowledge_context=knowledge_context,
        fetch_catalog=fetch_catalog,
    )
    eligible = sum(1 for item in payload.get("candidates", []) if item.get("promotion_eligible"))
    typer.echo(
        f"status={payload['status']} candidates={payload['candidate_count']} eligible={eligible} "
        f"catalog={payload['catalog_status']} out={out_path}"
    )


@harness_app.command("proxy-log-report")
def harness_proxy_log_report(
    artifacts_root: Optional[Path] = typer.Option(None, "--artifacts-root", help="Artifact root to inspect."),
    out: Path = typer.Option(DEFAULT_PROXY_LOG_REPORT_PATH, "--out", help="JSON proxy log report output path."),
    logs_file: Optional[Path] = typer.Option(None, "--logs-file", help="Optional pre-captured LiteLLM proxy log file."),
    since: str = typer.Option("30m", "--since", help="docker compose logs --since window."),
    collect_docker_logs: bool = typer.Option(True, "--collect-docker-logs/--no-collect-docker-logs", help="Collect docker compose logs when --logs-file is omitted."),
) -> None:
    log_text = logs_file.read_text(encoding="utf-8", errors="replace") if logs_file else ""
    if not log_text and collect_docker_logs:
        try:
            result = subprocess.run(
                ["docker", "compose", "logs", "--no-color", "--since", since, "litellm-proxy"],
                cwd=Path.cwd(),
                check=False,
                capture_output=True,
                text=True,
            )
            log_text = result.stdout if result.returncode == 0 else f"proxy_log_collection_failed: {result.stderr[:1000]}"
        except FileNotFoundError as exc:
            log_text = f"proxy_log_collection_failed: {exc}"
    out_path = _local_report_path(out)
    payload = build_proxy_log_report(artifacts_root=artifacts_root, log_text=log_text, out=out_path)
    typer.echo(f"status={payload['status']} windows={payload['window_count']} snippets={payload['snippet_count']} out={out_path}")


@harness_app.command("route-health")
def harness_route_health(
    out: Path = typer.Option(DEFAULT_ROUTE_HEALTH_REPORT_PATH, "--out", help="JSON route health report output path."),
    user_tier: Optional[str] = typer.Option(None, "--user-tier", help="Optional user tier filter."),
    workflow: Optional[str] = typer.Option(None, "--workflow", help="Optional workflow filter."),
) -> None:
    out_path = _local_report_path(out)
    payload = route_health_report(user_tier=user_tier, workflow=workflow)
    write_json(out_path, payload)
    typer.echo(
        f"status={payload['status']} store={payload['store']} routes={payload['route_count']} "
        f"cooldown={payload.get('cooldown_count', 0)} out={out_path}"
    )


@harness_app.command("propose-lessons")
def harness_propose_lessons(
    report: Path = typer.Option(DEFAULT_INTELLIGENCE_REPORT_PATH, "--report", help="Harness intelligence report JSON path."),
    out: Path = typer.Option(DEFAULT_INTELLIGENCE_PROPOSALS_PATH, "--out", help="JSON proposal output path."),
    candidates_path: Optional[Path] = typer.Option(None, "--candidates-path", help="Optional knowledge candidates JSON path."),
) -> None:
    out_path = _local_report_path(out)
    payload = propose_intelligence_lessons(report, out=out_path, candidates_path=candidates_path)
    typer.echo(f"status={payload['status']} proposals={payload['proposal_count']} out={out_path}")


@harness_app.command("replay-recommendations")
def harness_replay_recommendations(
    proposals: Path = typer.Option(DEFAULT_INTELLIGENCE_PROPOSALS_PATH, "--proposals", help="Harness intelligence proposal JSON path."),
    suite: Path = typer.Option(..., "--suite", help="Live eval suite YAML path."),
    out: Path = typer.Option(DEFAULT_INTELLIGENCE_REPLAY_PATH, "--out", help="JSON replay report output path."),
    knowledge_context: str = typer.Option("auto", "--knowledge-context", help="auto or off."),
) -> None:
    out_path = _local_report_path(out)
    payload = replay_recommendations(proposals, suite=suite, out=out_path, knowledge_context=knowledge_context)
    typer.echo(f"status={payload['status']} proposals={len(payload['proposal_results'])} out={out_path}")
    if payload["status"] != STATUS_PASS:
        raise typer.Exit(1)


@harness_app.command("propose-improvements")
def harness_propose_improvements(
    proposals: Path = typer.Option(DEFAULT_INTELLIGENCE_PROPOSALS_PATH, "--proposals", help="Harness intelligence proposal or replay JSON path."),
    replay: Optional[Path] = typer.Option(None, "--replay", help="Optional replay report JSON path."),
    out: Path = typer.Option(DEFAULT_INTELLIGENCE_IMPROVEMENTS_PATH, "--out", help="JSON improvement candidates output path."),
) -> None:
    out_path = _local_report_path(out)
    replay_path = _local_report_path(replay) if replay else None
    payload = propose_improvements(proposals, replay_path=replay_path, out=out_path)
    ready = sum(1 for candidate in payload["candidates"] if candidate.get("ready_for_review"))
    typer.echo(f"status={payload['status']} candidates={payload['candidate_count']} ready={ready} out={out_path}")


@harness_app.command("apply-approved-improvement")
def harness_apply_approved_improvement(
    candidate_id: str = typer.Argument(..., help="Approved improvement candidate id."),
    candidates: Path = typer.Option(DEFAULT_INTELLIGENCE_IMPROVEMENTS_PATH, "--candidates", help="Improvement candidates JSON path."),
    out: Path = typer.Option(DEFAULT_INTELLIGENCE_PATCH_PATH, "--out", help="Patch artifact JSON output path."),
) -> None:
    out_path = _local_report_path(out)
    payload = apply_approved_improvement(candidate_id, candidates_path=candidates, out=out_path)
    typer.echo(f"status={payload['status']} candidate={candidate_id} out={out_path}")
    if payload["status"] != STATUS_PASS:
        raise typer.Exit(1)


@harness_app.command("dev-trace")
def harness_dev_trace(
    summary: str = typer.Option(..., "--summary", help="Short description of the dev session or task."),
    agent: str = typer.Option("codex", "--agent", help="Agent name to record in repository harness."),
    outcome: str = typer.Option("completed", "--outcome", help="completed, partial, or failed."),
    intake: Optional[int] = typer.Option(None, "--intake", help="Existing repository-harness intake id to link."),
    link_intake: bool = typer.Option(True, "--link-intake/--no-link-intake", help="Create and link an intake when --intake is omitted."),
    intake_type: Optional[str] = typer.Option(None, "--intake-type", help="Intake type override for auto-created linked intake."),
    lane: Optional[str] = typer.Option(None, "--lane", help="Risk lane override for auto-created linked intake."),
    story: Optional[str] = typer.Option(None, "--story", help="Optional repository-harness story id."),
    action: Optional[list[str]] = typer.Option(None, "--action", "--actions", help="Action taken during the session. Repeatable."),
    read: Optional[list[str]] = typer.Option(None, "--read", help="File, doc, command, or source consulted. Repeatable."),
    changed: Optional[list[str]] = typer.Option(None, "--changed", help="File or artifact changed. Repeatable."),
    decision: Optional[list[str]] = typer.Option(None, "--decision", "--decisions", help="Decision made during the session. Repeatable."),
    error: Optional[list[str]] = typer.Option(None, "--error", "--errors", help="Error, blocker, or failed tool event. Repeatable."),
    friction: str = typer.Option("none", "--friction", help="Harness or runtime friction observed; use 'none' when clean."),
    duration: int = typer.Option(0, "--duration", min=0, help="Session duration in seconds."),
    started_at: Optional[str] = typer.Option(None, "--started-at", help="Session start time as epoch seconds or ISO timestamp."),
    session_state: Path = typer.Option(DEFAULT_HARNESS_SESSION_STATE, "--session-state", help="Session state JSON path."),
    use_session_state: bool = typer.Option(True, "--use-session-state/--no-session-state", help="Use and clear session state when available."),
    startup_state: Path = typer.Option(DEFAULT_HARNESS_STARTUP_STATE, "--startup-state", help="Startup state JSON path from harness agent-start."),
    use_startup_state: bool = typer.Option(True, "--use-startup-state/--no-startup-state", help="Use and clear startup preflight state when available."),
    preflight_applied: bool = typer.Option(False, "--preflight-applied", help="Record that bounded preflight context was applied."),
    preflight_ref: Optional[str] = typer.Option(None, "--preflight-ref", help="Preflight report reference to record when preflight was applied."),
    tokens: int = typer.Option(0, "--tokens", min=0, help="Token estimate for the session."),
    test_outcome: Optional[str] = typer.Option(None, "--test-outcome", help="Verification test outcome: pass, fail, skipped, or unknown."),
    review_outcome: Optional[str] = typer.Option(None, "--review-outcome", help="Review outcome: pass, fail, blocked, manual_required, or unknown."),
    review_evidence: Optional[list[str]] = typer.Option(None, "--review-evidence", help="Bounded review evidence note. Repeatable."),
    review_justification: Optional[str] = typer.Option(None, "--review-justification", help="Reason review was skipped or manual_required."),
    validation_outcome: Optional[str] = typer.Option(None, "--validation-outcome", help="Validation outcome: pass, fail, manual_required, skipped, or unknown."),
    production_impact: Optional[str] = typer.Option(None, "--production-impact", help="Production/live impact outcome: pass, fail, blocked, skipped, or unknown."),
    notes: Optional[str] = typer.Option("strategy-codebot dev session trace", "--notes", help="Optional trace notes."),
) -> None:
    cli_path = harness_cli_path()
    if not cli_path.exists():
        typer.echo(f"status=fail reason=missing_harness_cli path={cli_path}")
        raise typer.Exit(1)
    if not os.access(cli_path, os.X_OK):
        typer.echo(f"status=fail reason=harness_cli_not_executable path={cli_path}")
        raise typer.Exit(1)

    trace_reads = _trace_values(read, "conversation_context")
    trace_changes = _trace_values(changed, "no_repo_files_changed")
    state_path = _session_state_path(session_state)
    startup_path = _startup_state_path(startup_state)
    state_used = False
    startup_used = False
    preflight_brief_count: int | None = None
    if use_startup_state:
        startup_payload = _read_startup_preflight(startup_path)
        if startup_payload:
            startup_used = True
            preflight_applied = True
            preflight_ref = preflight_ref or startup_payload.get("preflight_ref")
            preflight_brief_count = startup_payload.get("preflight_brief_count")
    if duration == 0 and started_at:
        duration = _duration_from_started_at(started_at)
    elif duration == 0 and use_session_state:
        state_started_at = _read_session_started_at(state_path)
        if state_started_at:
            duration = _duration_from_started_at(state_started_at)
            state_used = True
    if duration == 0:
        notes = _append_duration_unavailable_note(notes)

    if intake is None and link_intake:
        intake = record_trace_intake(
            summary=summary,
            docs=[*trace_reads, *trace_changes],
            input_type=intake_type,
            lane=lane,
            changed=trace_changes,
            story=story,
            notes="auto-created for strategy-codebot dev-trace",
        )

    outcome_decisions = _trace_outcome_decisions(
        test_outcome=test_outcome,
        review_outcome=review_outcome,
        review_evidence=review_evidence,
        review_justification=review_justification,
        validation_outcome=validation_outcome,
        production_impact=production_impact,
    )
    preflight_decisions = _trace_preflight_decisions(
        preflight_applied=preflight_applied,
        preflight_ref=preflight_ref,
        preflight_brief_count=preflight_brief_count,
    )
    trace_decisions = [*_trace_values(decision, "no_durable_decisions"), *preflight_decisions, *outcome_decisions]
    metadata_decisions = [*preflight_decisions, *outcome_decisions]
    if metadata_decisions:
        notes = f"{notes}; {'; '.join(metadata_decisions)}" if notes else "; ".join(metadata_decisions)

    errors = _trace_values(error, NO_ERROR_TRACE_ARG)
    command = build_trace_command(
        summary=summary,
        intake=intake,
        story=story,
        agent=agent,
        outcome=outcome,
        changed=trace_changes,
        actions=_trace_values(action, "dev_trace_recorded"),
        read=trace_reads,
        errors=",".join(errors),
        friction=friction.strip() or "none",
        duration=duration,
        tokens=tokens,
        decisions=trace_decisions,
        notes=notes,
    )
    record_trace(command)
    if state_used and state_path.exists():
        state_path.unlink()
    if startup_used and startup_path.exists():
        startup_path.unlink()
    typer.echo(f"status=pass summary={summary} intake={intake} harness_cli={cli_path}")


def main() -> None:
    app()
