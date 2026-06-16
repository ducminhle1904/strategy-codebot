from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from strategy_codebot import __version__
from strategy_codebot.harness import build_trace_command, harness_outcome, record_trace, should_record_harness
from strategy_codebot.live import generate_live
from strategy_codebot.mql5 import runner_design, validation_report as mql5_validation_report
from strategy_codebot.paths import ensure_dir, ensure_parent, repo_root
from strategy_codebot.pine import generate_pine, manual_checklist, validate_pine
from strategy_codebot.schemas import load_strategy_spec, validate_payload, write_json


def run_strategy(
    *,
    spec_path: Path | None,
    prompt: str | None,
    mode: str,
    out_dir: Path,
    record_harness: bool | None,
) -> dict[str, Any]:
    ensure_dir(out_dir)
    run_id = out_dir.name if out_dir.name else f"run-{uuid4().hex[:8]}"

    if mode == "dry-run":
        if spec_path is None:
            raise ValueError("--spec is required when --mode dry-run")
        spec = load_strategy_spec(spec_path)
        pine_code = generate_pine(spec) if spec["target_platform"] in {"pine_v6", "both"} else None
    elif mode == "live":
        if not prompt:
            raise ValueError("--prompt is required when --mode live")
        spec, pine_code = generate_live(prompt, repo_root() / "configs" / "model-registry.example.yaml")
        validate_payload(spec, "strategy-spec.schema.json")
    else:
        raise ValueError("mode must be dry-run or live")

    write_json(out_dir / "strategy-spec.json", spec)

    validation = None
    changed = ["strategy-spec.json", "agent-run.json"]
    if pine_code:
        pine_dir = out_dir / "pine"
        ensure_dir(pine_dir)
        (pine_dir / "strategy.pine").write_text(pine_code, encoding="utf-8")
        validation = validate_pine(pine_code, spec)
        (out_dir / "manual-tradingview-checklist.md").write_text(manual_checklist(spec), encoding="utf-8")
        changed.extend(["pine/strategy.pine", "manual-tradingview-checklist.md"])

    if spec["target_platform"] in {"mql5", "both"}:
        mql5_dir = out_dir / "mql5"
        ensure_dir(mql5_dir)
        (mql5_dir / "runner-design.md").write_text(runner_design(spec), encoding="utf-8")
        mql5_report = mql5_validation_report()
        validation = _combine_validation(validation, mql5_report) if validation else mql5_report
        changed.append("mql5/runner-design.md")

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
    write_json(out_dir / "validation-report.json", validation)
    changed.append("validation-report.json")

    agent_run = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "agent_role": "pine_specialist" if spec["target_platform"] == "pine_v6" else "validator",
        "provider": "dry-run" if mode == "dry-run" else "litellm",
        "model": "deterministic-template" if mode == "dry-run" else "model-registry",
        "prompt_version": __version__,
        "input_refs": [str(spec_path)] if spec_path else ["prompt"],
        "retrieved_sources": ["configs/source-registry.yaml"],
        "tool_calls": ["pine-static-validator"] if pine_code else [],
        "output_refs": changed,
        "validation_refs": ["validation-report.json"],
        "status": validation["status"],
        "warnings": validation["warnings"],
    }
    validate_payload(agent_run, "agent-run.schema.json")
    write_json(out_dir / "agent-run.json", agent_run)

    if should_record_harness(record_harness):
        command = build_trace_command(
            summary=f"Phase 1 single-agent run {run_id}",
            story=None,
            agent=agent_run["agent_role"],
            outcome=harness_outcome(validation["status"]),
            changed=[str(out_dir / item) for item in changed],
            notes="strategy-codebot CLI run; story reference is kept in docs until harness story rows are seeded",
        )
        record_trace(command)

    return {"run_id": run_id, "out_dir": str(out_dir), "status": validation["status"]}


def validate_pine_file(file_path: Path, spec_path: Path, out_path: Path) -> dict[str, Any]:
    spec = load_strategy_spec(spec_path)
    report = validate_pine(file_path.read_text(encoding="utf-8"), spec)
    validate_payload(report, "validation-report.schema.json")
    ensure_parent(out_path)
    write_json(out_path, report)
    return report


def _combine_validation(pine_report: dict[str, Any] | None, mql5_report: dict[str, Any]) -> dict[str, Any]:
    if pine_report is None:
        return mql5_report
    statuses = {pine_report["status"], mql5_report["status"]}
    if "fail" in statuses:
        status = "fail"
    elif "manual_required" in statuses:
        status = "manual_required"
    else:
        status = "pass"
    return {
        "platform": "both",
        "status": status,
        "checks": pine_report["checks"] + mql5_report["checks"],
        "evidence": pine_report["evidence"] + mql5_report["evidence"],
        "warnings": pine_report["warnings"] + mql5_report["warnings"],
        "next_actions": pine_report["next_actions"] + mql5_report["next_actions"],
    }
