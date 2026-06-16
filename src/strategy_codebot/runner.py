from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from strategy_codebot import __version__
from strategy_codebot.harness import build_trace_command, harness_outcome, record_trace, should_record_harness
from strategy_codebot.live import generate_live
from strategy_codebot.mql5 import runner_design, validation_report as mql5_validation_report
from strategy_codebot.paths import ensure_dir, repo_root
from strategy_codebot.pine import generate_pine, manual_checklist, validate_pine
from strategy_codebot.reporting import aggregate_status
from strategy_codebot.review import REVIEW_MODE_NONE, REVIEW_MODE_PARALLEL, REVIEW_REPORT_PATH, write_review_report
from strategy_codebot.schemas import load_strategy_spec, validate_payload, write_json
from strategy_codebot.tool_runtime import POLICY_MODES, POLICY_OBSERVE, RUNTIME_SUMMARY_PATH, RUNTIME_TRACE_PATH, ToolHarness, call_tool


def run_strategy(
    *,
    spec_path: Path | None,
    prompt: str | None,
    mode: str,
    out_dir: Path,
    review: str = REVIEW_MODE_NONE,
    record_harness: bool | None = None,
    runtime_trace: bool = True,
    policy: str = POLICY_OBSERVE,
) -> dict[str, Any]:
    if review not in {REVIEW_MODE_NONE, REVIEW_MODE_PARALLEL}:
        raise ValueError("review must be none or parallel")
    if policy not in POLICY_MODES:
        raise ValueError("policy must be observe or enforce")
    if mode not in {"dry-run", "live"}:
        raise ValueError("mode must be dry-run or live")
    if mode == "dry-run" and spec_path is None:
        raise ValueError("--spec is required when --mode dry-run")
    if mode == "live" and not prompt:
        raise ValueError("--prompt is required when --mode live")

    run_id = out_dir.name if out_dir.name else f"run-{uuid4().hex[:8]}"
    tool_harness = ToolHarness(run_id=run_id, policy_mode=policy) if runtime_trace else None
    runtime_artifacts = [RUNTIME_TRACE_PATH, RUNTIME_SUMMARY_PATH] if runtime_trace else []

    if mode == "dry-run":
        spec = call_tool(tool_harness, "load_strategy_spec", load_strategy_spec, spec_path, input_refs=[str(spec_path)], output_refs=["strategy-spec.json"])
        pine_code = (
            call_tool(tool_harness, "generate_pine", generate_pine, spec, input_refs=["strategy-spec.json"], output_refs=["pine/strategy.pine"], policy_text=str(spec))
            if spec["target_platform"] in {"pine_v6", "both"}
            else None
        )
    elif mode == "live":
        spec, pine_code = generate_live(prompt, repo_root() / "configs" / "model-registry.example.yaml")
        validate_payload(spec, "strategy-spec.schema.json")

    ensure_dir(out_dir)
    artifacts: list[str] = []

    def write_text_artifact(relative_path: str, content: str) -> None:
        target = out_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        artifacts.append(relative_path)

    def write_json_artifact(relative_path: str, payload: dict[str, Any]) -> None:
        write_json(out_dir / relative_path, payload)
        artifacts.append(relative_path)

    write_json_artifact("strategy-spec.json", spec)

    validation = None
    mql5_design = None
    if pine_code:
        write_text_artifact("pine/strategy.pine", pine_code)
        validation = call_tool(tool_harness, "validate_pine_static", validate_pine, pine_code, spec, input_refs=["pine/strategy.pine", "strategy-spec.json"], output_refs=["validation-report.json"])
        checklist = call_tool(tool_harness, "write_manual_checklist", manual_checklist, spec, input_refs=["strategy-spec.json"], output_refs=["manual-tradingview-checklist.md"])
        write_text_artifact("manual-tradingview-checklist.md", checklist)

    if spec["target_platform"] in {"mql5", "both"}:
        mql5_design = call_tool(tool_harness, "create_mql5_runner_design", runner_design, spec, input_refs=["strategy-spec.json"], output_refs=["mql5/runner-design.md"])
        write_text_artifact("mql5/runner-design.md", mql5_design)
        mql5_report = mql5_validation_report()
        validation = _combine_validation(validation, mql5_report) if validation else mql5_report

    if validation is None:
        validation = {
            "platform": spec["target_platform"],
            "status": "skipped",
            "checks": [],
            "evidence": [],
            "warnings": ["No Phase 1 validator is available for this target."],
            "next_actions": [],
        }

    validate_payload(validation, "validation-report.schema.json")
    write_json_artifact("validation-report.json", validation)

    if review == REVIEW_MODE_PARALLEL:
        review_report = write_review_report(
            run_id=run_id,
            spec=spec,
            validation=validation,
            pine_code=pine_code,
            mql5_runner_design=mql5_design,
            mode=mode,
            out_path=out_dir / REVIEW_REPORT_PATH,
            record_harness=record_harness,
            runtime_trace=False,
            policy=policy,
            tool_harness=tool_harness,
        )
        artifacts.append(REVIEW_REPORT_PATH)
    else:
        review_report = None

    agent_run = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "agent_role": "pine_specialist" if spec["target_platform"] == "pine_v6" else "validator",
        "provider": "dry-run" if mode == "dry-run" else "litellm",
        "model": "deterministic-template" if mode == "dry-run" else "model-registry",
        "prompt_version": __version__,
        "input_refs": [str(spec_path)] if spec_path else ["prompt"],
        "retrieved_sources": ["configs/source-registry.yaml"],
        "tool_calls": [*(["pine-static-validator"] if pine_code else []), *(["parallel-review"] if review_report else [])],
        "output_refs": [*artifacts, *runtime_artifacts, "agent-run.json"],
        "validation_refs": ["validation-report.json", *([REVIEW_REPORT_PATH] if review_report else []), *runtime_artifacts],
        "status": validation["status"],
        "warnings": [*validation["warnings"], *(review_report["warnings"] if review_report else [])],
    }
    validate_payload(agent_run, "agent-run.schema.json")
    write_json_artifact("agent-run.json", agent_run)

    if should_record_harness(record_harness):
        command = build_trace_command(
            summary=f"Phase 1 single-agent run {run_id}",
            story=None,
            agent=agent_run["agent_role"],
            outcome=harness_outcome(validation["status"]),
            changed=[str(out_dir / item) for item in artifacts],
            notes="strategy-codebot CLI run; story reference is kept in docs until harness story rows are seeded",
        )
        call_tool(tool_harness, "record_harness_trace", record_trace, command, input_refs=["agent-run.json"], output_refs=["repository-harness trace"])

    if tool_harness:
        tool_harness.write_trace(out_dir / RUNTIME_TRACE_PATH, out_dir / RUNTIME_SUMMARY_PATH, [*artifacts, *runtime_artifacts])

    return {"run_id": run_id, "out_dir": str(out_dir), "status": validation["status"]}


def validate_pine_file(file_path: Path, spec_path: Path, out_path: Path) -> dict[str, Any]:
    spec = load_strategy_spec(spec_path)
    report = validate_pine(file_path.read_text(encoding="utf-8"), spec)
    validate_payload(report, "validation-report.schema.json")
    write_json(out_path, report)
    return report


def _combine_validation(pine_report: dict[str, Any] | None, mql5_report: dict[str, Any]) -> dict[str, Any]:
    if pine_report is None:
        return mql5_report
    status = aggregate_status({pine_report["status"], mql5_report["status"]})
    return {
        "platform": "both",
        "status": status,
        "checks": pine_report["checks"] + mql5_report["checks"],
        "evidence": pine_report["evidence"] + mql5_report["evidence"],
        "warnings": pine_report["warnings"] + mql5_report["warnings"],
        "next_actions": pine_report["next_actions"] + mql5_report["next_actions"],
    }
