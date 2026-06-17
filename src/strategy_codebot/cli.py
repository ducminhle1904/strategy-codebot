from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from time import time
from typing import Optional

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
from strategy_codebot.harness_types import STATUS_PASS
from strategy_codebot.live import COST_PROFILE_QUALITY, WORKFLOW_MULTI_AGENT, LiveRunOptions, validate_model_stage_overrides
from strategy_codebot.knowledge import audit_run, check_registry, create_proposal, create_snapshot, diff_snapshots
from strategy_codebot.paths import repo_root, resolve_repo_path
from strategy_codebot.review import REVIEW_MODE_NONE, review_run_directory
from strategy_codebot.runner import run_strategy, validate_pine_file
from strategy_codebot.schemas import validate_payload, write_json
from strategy_codebot.model_matrix import run_model_combo_matrix
from strategy_codebot.tool_runtime import POLICY_OBSERVE, check_tool_registry, tool_ids

app = typer.Typer(help="Strategy Codebot CLI.")
knowledge_app = typer.Typer(help="Knowledge source registry commands.")
tools_app = typer.Typer(help="Runtime tool registry commands.")
eval_app = typer.Typer(help="Evaluation harness commands.")
harness_app = typer.Typer(help="Agent harness inspection commands.")
app.add_typer(knowledge_app, name="knowledge")
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
    workflow: str = typer.Option(WORKFLOW_MULTI_AGENT, "--workflow", help="multi-agent or single."),
    cost_profile: str = typer.Option(COST_PROFILE_QUALITY, "--cost-profile", help="quality or cheap."),
    model_stage: Optional[list[str]] = typer.Option(None, "--model-stage", help="Live multi-agent stage override as stage=model. Repeatable."),
    save_raw_provider: bool = typer.Option(False, "--save-raw-provider/--no-save-raw-provider", help="Write raw live provider response artifact."),
    knowledge_context: str = typer.Option("auto", "--knowledge-context", help="auto or off."),
    otel_export: Optional[Path] = typer.Option(None, "--otel-export", help="Write local OpenTelemetry-compatible JSONL spans."),
    record_harness: Optional[bool] = typer.Option(None, "--record-harness/--no-record-harness", help="Record a repository-harness trace."),
) -> None:
    live_options = (
        LiveRunOptions(
            model_override=model,
            model_stage_overrides=_parse_model_stage_overrides(model_stage),
            workflow=workflow,
            cost_profile=cost_profile,
            save_raw_provider=save_raw_provider,
            knowledge_context=knowledge_context,
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
    offline: bool = typer.Option(True, "--offline/--fetch", help="Use deterministic offline metadata instead of fetching external URLs."),
    out: Path = typer.Option(Path("knowledge/snapshots/current.json"), "--out", help="Snapshot output path."),
) -> None:
    snapshot = create_snapshot(_resolve_input_path(registry), offline=offline)
    validate_payload(snapshot, "knowledge-snapshot.schema.json")
    write_json(out, snapshot)
    typer.echo(f"sources={len(snapshot['sources'])} fetch_mode={snapshot['fetch_mode']} out={out}")


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
    workflow: str = typer.Option(WORKFLOW_MULTI_AGENT, "--workflow", help="multi-agent or single."),
    cost_profile: str = typer.Option(COST_PROFILE_QUALITY, "--cost-profile", help="quality or cheap."),
    model_stage: Optional[list[str]] = typer.Option(None, "--model-stage", help="Live multi-agent stage override as stage=model. Repeatable."),
    save_raw_provider: bool = typer.Option(True, "--save-raw-provider/--no-save-raw-provider", help="Write raw live provider response artifacts."),
    knowledge_context: str = typer.Option("auto", "--knowledge-context", help="auto or off."),
    otel_export: Optional[Path] = typer.Option(None, "--otel-export", help="Write local OpenTelemetry-compatible JSONL spans."),
    concurrency: int = typer.Option(2, "--concurrency", help="Max live eval cases to run concurrently, capped at 8."),
    case_timeout_seconds: int = typer.Option(600, "--case-timeout-seconds", help="Hard timeout for each live eval case."),
) -> None:
    live_options = LiveRunOptions(
        model_override=model,
        model_stage_overrides=_parse_model_stage_overrides(model_stage),
        workflow=workflow,
        cost_profile=cost_profile,
        save_raw_provider=save_raw_provider,
        knowledge_context=knowledge_context,
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
    combo: Optional[list[str]] = typer.Option(None, "--combo", help="Model combo id to run. Repeatable; defaults to all combos."),
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
        run_full=run_full,
        save_raw_provider=save_raw_provider,
        concurrency=concurrency,
        case_timeout_seconds=case_timeout_seconds,
        knowledge_context=knowledge_context,
    )
    typer.echo(
        f"status={report['status']} combos={len(report['combos'])} "
        f"recommended={report['recommended_combo']} out={out}"
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
