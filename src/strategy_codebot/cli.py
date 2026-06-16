from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from strategy_codebot import __version__
from strategy_codebot.doctor import doctor_report
from strategy_codebot.knowledge import audit_run, check_registry, create_proposal, create_snapshot, diff_snapshots
from strategy_codebot.paths import repo_root
from strategy_codebot.review import REVIEW_MODE_NONE, review_run_directory
from strategy_codebot.runner import run_strategy, validate_pine_file
from strategy_codebot.schemas import validate_payload, write_json
from strategy_codebot.tool_runtime import POLICY_OBSERVE, check_tool_registry, tool_ids

app = typer.Typer(help="Strategy Codebot CLI.")
knowledge_app = typer.Typer(help="Knowledge source registry commands.")
tools_app = typer.Typer(help="Runtime tool registry commands.")
app.add_typer(knowledge_app, name="knowledge")
app.add_typer(tools_app, name="tools")


def _resolve_input_path(path: Path) -> Path:
    if path.exists() or path.is_absolute():
        return path
    packaged_path = repo_root() / path
    return packaged_path if packaged_path.exists() else path


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
    if report["status"] != "pass":
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
    record_harness: Optional[bool] = typer.Option(None, "--record-harness/--no-record-harness", help="Record a repository-harness trace."),
) -> None:
    result = run_strategy(spec_path=spec, prompt=prompt, mode=mode, out_dir=out, review=review, record_harness=record_harness, runtime_trace=runtime_trace, policy=policy)
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


def main() -> None:
    app()
