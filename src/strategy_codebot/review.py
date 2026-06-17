from __future__ import annotations

import asyncio
import contextlib
import io
import inspect
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Awaitable, Callable

import yaml

from strategy_codebot.harness import NO_ERROR_TRACE_ARG, build_trace_command, harness_outcome, record_trace, record_trace_intake, should_record_harness
from strategy_codebot.live import (
    STAGE_BALANCED_REVIEW,
    STAGE_PINE_CODE_GENERATION,
    STAGE_STRATEGY_REASONING,
    LiveRunOptions,
    _models_for_stage,
)
from strategy_codebot.paths import repo_root, resolve_repo_path
from strategy_codebot.schemas import load_json, validate_payload, write_json
from strategy_codebot.tool_runtime import POLICY_MODES, POLICY_OBSERVE, ToolHarness, call_tool


REVIEW_ROLES = ("trading_analyst", "pine_specialist", "risk_reviewer", "critic")
REVIEW_MODE_NONE = "none"
REVIEW_MODE_PARALLEL = "parallel"
REVIEW_REPORT_PATH = "review-report.json"
REVIEW_RUNTIME_TRACE_PATH = "review-runtime-trace.jsonl"
REVIEW_RUNTIME_SUMMARY_PATH = "review-runtime-summary.json"
REVIEW_STATUSES = {"pass", "fail", "manual_required", "skipped", "error"}
FINDING_SEVERITIES = {"info", "warning", "blocker"}
ReviewerFn = Callable[["ReviewContext"], dict[str, Any] | Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ReviewContext:
    run_id: str
    spec: dict[str, Any]
    validation: dict[str, Any]
    pine_code: str | None
    mql5_runner_design: str | None
    mode: str
    model_registry: dict[str, Any]
    live_options: LiveRunOptions | None = None


def review_run_directory(
    *,
    run_dir: Path,
    mode: str,
    out_path: Path,
    record_harness: bool | None,
    runtime_trace: bool = True,
    policy: str = POLICY_OBSERVE,
) -> dict[str, Any]:
    if policy not in POLICY_MODES:
        raise ValueError("policy must be observe or enforce")
    spec = load_json(run_dir / "strategy-spec.json")
    validation = load_json(run_dir / "validation-report.json")
    return write_review_report(
        run_id=run_dir.name,
        spec=spec,
        validation=validation,
        pine_code=_read_optional_text(run_dir / "pine" / "strategy.pine"),
        mql5_runner_design=_read_optional_text(run_dir / "mql5" / "runner-design.md"),
        mode=mode,
        out_path=out_path,
        record_harness=record_harness,
        runtime_trace=runtime_trace,
        policy=policy,
    )


def write_review_report(
    *,
    run_id: str,
    spec: dict[str, Any],
    validation: dict[str, Any],
    pine_code: str | None,
    mql5_runner_design: str | None,
    mode: str,
    out_path: Path,
    record_harness: bool | None,
    runtime_trace: bool = True,
    policy: str = POLICY_OBSERVE,
    tool_harness: ToolHarness | None = None,
    model_registry: Path | None = None,
    live_options: LiveRunOptions | None = None,
    intake_id: int | None = None,
) -> dict[str, Any]:
    started_at = perf_counter()
    if policy not in POLICY_MODES:
        raise ValueError("policy must be observe or enforce")
    local_harness = tool_harness or (ToolHarness(run_id=run_id, policy_mode=policy) if runtime_trace else None)
    report = call_tool(
        local_harness,
        "run_parallel_review",
        _run_parallel_review_sync,
        run_id,
        spec,
        validation,
        pine_code,
        mql5_runner_design,
        mode,
        model_registry,
        live_options,
        input_refs=["strategy-spec.json", "validation-report.json"],
        output_refs=[REVIEW_REPORT_PATH],
        policy_text=json.dumps(spec, ensure_ascii=False),
    )
    validate_payload(report, "review-report.schema.json")
    write_json(out_path, report)

    repository_trace_enabled = should_record_harness(record_harness)
    if repository_trace_enabled and intake_id is None:
        intake_id = record_trace_intake(
            summary=f"Parallel review {report['run_id']}",
            input_type="maintenance request",
            docs=_review_trace_reads(local_harness, model_registry),
            notes="auto-created for strategy-codebot parallel review trace",
        )

    if repository_trace_enabled:
        command = build_trace_command(
            summary=f"Phase 2 parallel review {report['run_id']}",
            intake=intake_id,
            story=None,
            agent="critic",
            outcome=harness_outcome(_decision_as_validation_status(report["decision"])),
            changed=[str(out_path)],
            actions=_review_trace_actions(local_harness),
            read=_review_trace_reads(local_harness, model_registry),
            errors=_review_trace_errors(local_harness),
            friction=_review_trace_friction(local_harness),
            duration=max(0, int(perf_counter() - started_at)),
            tokens=0,
            decisions=_review_trace_decisions(
                mode=mode,
                spec=spec,
                validation=validation,
                report=report,
                policy=policy,
                runtime_trace=runtime_trace,
                live_options=live_options,
            ),
            notes=(
                "strategy-codebot parallel review; deterministic validation remains the proof source; "
                f"validation_status={validation['status']}; review_decision={report['decision']}"
            ),
        )
        if local_harness:
            call_tool(local_harness, "record_harness_trace", record_trace, command, input_refs=[REVIEW_REPORT_PATH], output_refs=["repository-harness trace"])
        else:
            record_trace(command)

    if local_harness and tool_harness is None:
        local_harness.write_trace(
            out_path.parent / REVIEW_RUNTIME_TRACE_PATH,
            out_path.parent / REVIEW_RUNTIME_SUMMARY_PATH,
            [REVIEW_REPORT_PATH, REVIEW_RUNTIME_TRACE_PATH, REVIEW_RUNTIME_SUMMARY_PATH],
        )

    return report


def _review_trace_actions(tool_harness: ToolHarness | None) -> list[str]:
    if tool_harness is None:
        return ["runtime_trace_disabled"]
    actions: list[str] = []
    for event in tool_harness.events:
        tool_id = event.get("tool_id")
        if not tool_id or tool_id == "record_harness_trace":
            continue
        if event.get("event_type") in {"tool.completed", "tool.failed", "tool.blocked"}:
            actions.append(f"{tool_id}:{event.get('status', 'unknown')}")
    return actions or ["parallel_review_recorded"]


def _review_trace_reads(tool_harness: ToolHarness | None, model_registry: Path | None) -> list[str]:
    reads = ["strategy-spec.json", "validation-report.json"]
    if model_registry:
        _append_unique(reads, model_registry)
    if tool_harness:
        for event in tool_harness.events:
            for ref in event.get("input_refs", []):
                _append_unique(reads, ref)
    return reads


def _review_trace_errors(tool_harness: ToolHarness | None) -> str:
    if tool_harness is None:
        return NO_ERROR_TRACE_ARG
    errors = []
    for event in _review_error_events(tool_harness):
        errors.append(
            {
                "tool_id": event.get("tool_id"),
                "event_type": event.get("event_type"),
                "status": event.get("status"),
                "failure_class": event.get("failure_class"),
                "error": event.get("error"),
            }
        )
    return json.dumps(errors, ensure_ascii=False)


def _review_trace_friction(tool_harness: ToolHarness | None) -> str:
    if tool_harness is None:
        return "runtime trace disabled"
    return "runtime tool failures or policy blocks recorded" if _review_error_events(tool_harness) else "none"


def _review_trace_decisions(
    *,
    mode: str,
    spec: dict[str, Any],
    validation: dict[str, Any],
    report: dict[str, Any],
    policy: str,
    runtime_trace: bool,
    live_options: LiveRunOptions | None,
) -> list[str]:
    decisions = [
        f"mode={mode}",
        f"target_platform={spec['target_platform']}",
        "review=parallel",
        f"policy={policy}",
        f"runtime_trace={str(runtime_trace).lower()}",
        f"validation_status={validation['status']}",
        f"review_decision={report['decision']}",
    ]
    if live_options:
        decisions.extend([f"workflow={live_options.workflow}", f"cost_profile={live_options.cost_profile}"])
    return decisions


def _review_error_events(tool_harness: ToolHarness) -> list[dict[str, Any]]:
    return [event for event in tool_harness.events if event.get("event_type") in {"tool.failed", "tool.blocked"}]


def _append_unique(items: list[str], value: Any) -> None:
    text = str(value)
    if text and text not in items:
        items.append(text)


def _run_parallel_review_sync(
    run_id: str,
    spec: dict[str, Any],
    validation: dict[str, Any],
    pine_code: str | None,
    mql5_runner_design: str | None,
    mode: str,
    model_registry: Path | None = None,
    live_options: LiveRunOptions | None = None,
) -> dict[str, Any]:
    return asyncio.run(
        run_parallel_review(
            run_id=run_id,
            spec=spec,
            validation=validation,
            pine_code=pine_code,
            mql5_runner_design=mql5_runner_design,
            mode=mode,
            model_registry=model_registry,
            live_options=live_options,
        )
    )


async def run_parallel_review(
    *,
    run_id: str,
    spec: dict[str, Any],
    validation: dict[str, Any],
    pine_code: str | None,
    mql5_runner_design: str | None,
    mode: str,
    model_registry: Path | None = None,
    live_options: LiveRunOptions | None = None,
    reviewer_functions: dict[str, ReviewerFn] | None = None,
) -> dict[str, Any]:
    if mode not in {"dry-run", "live"}:
        raise ValueError("mode must be dry-run or live")

    registry = _load_model_registry(model_registry) if mode == "live" else {}
    context = ReviewContext(
        run_id=run_id,
        spec=spec,
        validation=validation,
        pine_code=pine_code,
        mql5_runner_design=mql5_runner_design,
        mode=mode,
        model_registry=registry,
        live_options=live_options,
    )
    functions = _dry_run_reviewers() if mode == "dry-run" else _live_reviewers()
    if reviewer_functions:
        functions = {**functions, **reviewer_functions}
    tasks = [_run_reviewer(role, functions.get(role, _skipped_reviewer(role)), context) for role in REVIEW_ROLES]
    reviewers = list(await asyncio.gather(*tasks, return_exceptions=False))
    return _build_report(run_id, reviewers)


async def _run_reviewer(role: str, reviewer: ReviewerFn, context: ReviewContext) -> dict[str, Any]:
    try:
        result = reviewer(context)
        if inspect.isawaitable(result):
            result = await result
        normalized = _normalize_reviewer_result(role, result)
        _validate_reviewer_result(normalized)
        return normalized
    except Exception as exc:  # noqa: BLE001 - reviewer failures must be isolated.
        return {
            "role": role,
            "provider": context.mode,
            "model": "reviewer-error",
            "status": "error",
            "findings": [],
            "evidence_refs": [],
            "warnings": [f"{role} failed: {exc}"],
        }


def _normalize_reviewer_result(role: str, result: dict[str, Any]) -> dict[str, Any]:
    status = str(result.get("status") or "pass")
    status = {
        "approve": "pass",
        "approved": "pass",
        "accepted": "pass",
        "changes_requested": "fail",
        "needs_changes": "fail",
    }.get(status, status)
    normalized = {
        "role": role,
        "provider": result.get("provider") or "dry-run",
        "model": result.get("model") or "deterministic-reviewer",
        "status": status,
        "findings": _normalize_findings(result.get("findings", [])),
        "evidence_refs": result.get("evidence_refs", []),
        "warnings": result.get("warnings", []),
    }
    if result.get("provider_warnings"):
        normalized["provider_warnings"] = result["provider_warnings"]
    return normalized


def _normalize_findings(findings: Any) -> Any:
    if not isinstance(findings, list):
        return findings
    normalized: list[Any] = []
    for finding in findings:
        if isinstance(finding, dict):
            item = dict(finding)
            if item.get("recommendation") is None:
                item.pop("recommendation", None)
            normalized.append(item)
        else:
            normalized.append(finding)
    return normalized


def _validate_reviewer_result(result: dict[str, Any]) -> None:
    if result["status"] not in REVIEW_STATUSES:
        raise ValueError(f"invalid reviewer status: {result['status']}")
    if not isinstance(result["findings"], list):
        raise ValueError("reviewer findings must be a list")
    if not isinstance(result["evidence_refs"], list):
        raise ValueError("reviewer evidence_refs must be a list")
    if not isinstance(result["warnings"], list):
        raise ValueError("reviewer warnings must be a list")
    for finding in result["findings"]:
        if not isinstance(finding, dict):
            raise ValueError("reviewer finding must be an object")
        missing = {"reviewer", "severity", "category", "message", "evidence_refs"} - set(finding)
        if missing:
            raise ValueError(f"reviewer finding missing keys: {', '.join(sorted(missing))}")
        if finding["severity"] not in FINDING_SEVERITIES:
            raise ValueError(f"invalid finding severity: {finding['severity']}")
        if not isinstance(finding["evidence_refs"], list):
            raise ValueError("finding evidence_refs must be a list")


def _dry_run_reviewers() -> dict[str, ReviewerFn]:
    return {
        "trading_analyst": _review_trading_logic,
        "pine_specialist": _review_pine,
        "risk_reviewer": _review_risk,
        "critic": _review_critic,
    }


def _live_reviewers() -> dict[str, ReviewerFn]:
    return {role: _live_reviewer(role) for role in REVIEW_ROLES}


def _review_trading_logic(context: ReviewContext) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    if not context.spec.get("assumptions"):
        findings.append(
            _finding(
                "trading_analyst",
                "warning",
                "assumptions",
                "Strategy assumptions are not explicit enough for regime and backtest interpretation.",
                ["strategy-spec.json"],
                "Add market regime, session, commission, slippage, and data-quality assumptions before manual testing.",
            )
        )
    if len(context.spec.get("entry_rules", [])) > 4 or len(context.spec.get("exit_rules", [])) > 4:
        findings.append(
            _finding(
                "trading_analyst",
                "warning",
                "overfit_risk",
                "The rule set is relatively broad and should be checked against the anti-overfit checklist.",
                ["strategy-spec.json"],
                "Prefer fewer independent conditions and validate on out-of-sample periods.",
            )
        )
    return _reviewer_result("trading_analyst", "pass", findings, ["strategy-spec.json", "docs/trading/anti-overfit-checklist.md"])


def _review_pine(context: ReviewContext) -> dict[str, Any]:
    if context.pine_code is None:
        return _reviewer_result(
            "pine_specialist",
            "manual_required",
            [
                _finding(
                    "pine_specialist",
                    "warning",
                    "pine_artifact",
                    "No Pine artifact exists for this run, so Pine-specific review is limited.",
                    ["strategy-spec.json"],
                    "Generate Pine code before requesting Pine-specific approval.",
                )
            ],
            ["strategy-spec.json"],
        )

    findings = [
        _finding(
            "pine_specialist",
            "info",
            "runtime_boundary",
            "Static review cannot prove TradingView compile or Strategy Tester results.",
            ["pine/strategy.pine", "validation-report.json"],
            "Paste the script into TradingView and attach manual evidence before claiming runtime validation.",
        )
    ]
    for warning in context.validation.get("warnings", []):
        findings.append(
            _finding(
                "pine_specialist",
                "warning",
                "static_validation",
                warning,
                ["validation-report.json"],
                "Resolve or document this warning during manual TradingView validation.",
            )
        )
    status = context.validation.get("status", "skipped")
    if status not in {"pass", "fail", "manual_required", "skipped"}:
        status = "manual_required"
    return _reviewer_result("pine_specialist", status, findings, ["pine/strategy.pine", "validation-report.json"])


def _review_risk(context: ReviewContext) -> dict[str, Any]:
    text = " ".join(
        [
            json.dumps(context.spec, ensure_ascii=False).lower(),
            (context.pine_code or "").lower(),
            (context.mql5_runner_design or "").lower(),
        ]
    )
    blockers = _blocked_risk_claims(text)
    findings = []
    if blockers:
        findings.append(
            _finding(
                "risk_reviewer",
                "blocker",
                "risk_policy",
                f"Output or request includes blocked risk language: {', '.join(blockers)}.",
                ["strategy-spec.json", "docs/trading/risk-policy.md"],
                "Remove profitability guarantees and live-trading automation claims from the request/output.",
            )
        )
    else:
        findings.append(
            _finding(
                "risk_reviewer",
                "info",
                "risk_policy",
                "No profit guarantee or live-trading automation claim was found in generated artifacts.",
                ["strategy-spec.json", "docs/trading/risk-policy.md"],
            )
        )
    return _reviewer_result("risk_reviewer", "fail" if blockers else "pass", findings, ["docs/trading/risk-policy.md"])


def _review_critic(context: ReviewContext) -> dict[str, Any]:
    findings = []
    if context.validation.get("status") in {"manual_required", "skipped"}:
        findings.append(
            _finding(
                "critic",
                "warning",
                "proof_gap",
                "Validation still depends on external/manual platform evidence.",
                ["validation-report.json"],
                "Keep the run marked as requiring manual proof until TradingView or MT5 evidence is attached.",
            )
        )
    if context.spec["target_platform"] in {"mql5", "both"}:
        findings.append(
            _finding(
                "critic",
                "warning",
                "mql5_boundary",
                "MQL5 is represented by runner design only; no .mq5 compile/test proof exists.",
                ["mql5/runner-design.md", "validation-report.json"],
                "Do not claim MQL5 compile or Strategy Tester success in Phase 2.",
            )
        )
    status = "manual_required" if findings else "pass"
    evidence = ["strategy-spec.json", "validation-report.json"]
    if context.mql5_runner_design:
        evidence.append("mql5/runner-design.md")
    return _reviewer_result("critic", status, findings, evidence)


def _live_reviewer(role: str) -> ReviewerFn:
    async def reviewer(context: ReviewContext) -> dict[str, Any]:
        return await asyncio.to_thread(_run_live_reviewer, role, context)

    return reviewer


def _run_live_reviewer(role: str, context: ReviewContext) -> dict[str, Any]:
    try:
        import litellm
    except ImportError as exc:
        raise RuntimeError("Live review requires optional live dependencies. Run with `uv run --extra live strategy-codebot ...`.") from exc

    model = _review_model(role, context)
    prompt = {
        "role": role,
        "strategy_spec": context.spec,
        "validation_report": context.validation,
        "has_pine_code": context.pine_code is not None,
        "has_mql5_runner_design": context.mql5_runner_design is not None,
    }
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Return strict JSON for one reviewer result with keys "
                    "status, findings, evidence_refs, warnings. Do not claim runtime validation, "
                    "backtest success, profit, or live-trading readiness."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "temperature": context.model_registry.get("defaults", {}).get("temperature", 0.2),
        "response_format": _reviewer_response_format(),
    }
    if model.startswith("openrouter/") and os.getenv("OPENROUTER_API_BASE"):
        kwargs["base_url"] = os.getenv("OPENROUTER_API_BASE")
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
        response = litellm.completion(**kwargs)
    payload = json.loads(response.choices[0].message.content)
    provider_warnings = _captured_provider_warnings(stdout_buffer.getvalue(), stderr_buffer.getvalue())
    if provider_warnings:
        payload["provider_warnings"] = provider_warnings
    payload["provider"] = model.split("/", 1)[0] if "/" in model else "litellm"
    payload["model"] = model
    return payload


def _captured_provider_warnings(stdout_text: str, stderr_text: str) -> list[str]:
    warnings = []
    for label, text in (("stdout", stdout_text), ("stderr", stderr_text)):
        normalized = text.strip()
        if normalized:
            warnings.append(f"provider {label}: {normalized}")
    return warnings


def _review_model(role: str, context: ReviewContext) -> str:
    options = context.live_options
    if options:
        stage = _review_stage(role)
        return _models_for_stage(
            context.model_registry,
            stage,
            model_stage_overrides=options.model_stage_overrides,
            cost_profile=options.cost_profile,
        )[0]
    agent_config = context.model_registry["agents"][role]
    return agent_config["primary"]


def _review_stage(role: str) -> str:
    if role == "trading_analyst":
        return STAGE_STRATEGY_REASONING
    if role == "pine_specialist":
        return STAGE_PINE_CODE_GENERATION
    return STAGE_BALANCED_REVIEW


def _reviewer_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "strategy_codebot_reviewer_result",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "status": {"type": "string", "enum": ["pass", "fail", "manual_required", "skipped", "error", "approve", "approved", "changes_requested"]},
                    "findings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "reviewer": {"type": "string"},
                                "severity": {"type": "string", "enum": ["info", "warning", "blocker"]},
                                "category": {"type": "string"},
                                "message": {"type": "string"},
                                "evidence_refs": {"type": "array", "items": {"type": "string"}},
                                "recommendation": {"type": ["string", "null"]},
                            },
                            "required": ["reviewer", "severity", "category", "message", "evidence_refs", "recommendation"],
                        },
                    },
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "warnings": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["status", "findings", "evidence_refs", "warnings"],
            },
        },
    }


def _skipped_reviewer(role: str) -> ReviewerFn:
    def reviewer(_: ReviewContext) -> dict[str, Any]:
        return _reviewer_result(role, "skipped", [], [])

    return reviewer


def _reviewer_result(role: str, status: str, findings: list[dict[str, Any]], evidence_refs: list[str]) -> dict[str, Any]:
    return {
        "role": role,
        "provider": "dry-run",
        "model": "deterministic-reviewer",
        "status": status,
        "findings": findings,
        "evidence_refs": evidence_refs,
        "warnings": [finding["message"] for finding in findings if finding["severity"] in {"warning", "blocker"}],
    }


def _finding(
    reviewer: str,
    severity: str,
    category: str,
    message: str,
    evidence_refs: list[str],
    recommendation: str | None = None,
) -> dict[str, Any]:
    finding = {
        "reviewer": reviewer,
        "severity": severity,
        "category": category,
        "message": message,
        "evidence_refs": evidence_refs,
    }
    if recommendation:
        finding["recommendation"] = recommendation
    return finding


def _build_report(run_id: str, reviewers: list[dict[str, Any]]) -> dict[str, Any]:
    findings = [finding for reviewer in reviewers for finding in reviewer["findings"]]
    warnings = [warning for reviewer in reviewers for warning in reviewer["warnings"]]
    statuses = {reviewer["status"] for reviewer in reviewers}
    decision = _decision(findings, statuses)
    return {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "run_status": _run_status(statuses),
        "decision": decision,
        "reviewers": reviewers,
        "findings": findings,
        "conflicts": _conflicts(reviewers),
        "warnings": warnings,
        "next_actions": _next_actions(decision),
    }


def _run_status(statuses: set[str]) -> str:
    if not statuses or statuses == {"skipped"}:
        return "skipped"
    if "fail" in statuses:
        return "failed"
    if "error" in statuses or "manual_required" in statuses or "skipped" in statuses:
        return "partial"
    return "completed"


def _decision(findings: list[dict[str, Any]], statuses: set[str]) -> str:
    if any(finding["severity"] == "blocker" for finding in findings):
        return "blocked"
    if "fail" in statuses:
        return "changes_requested"
    if "manual_required" in statuses or "error" in statuses:
        return "manual_required"
    return "approve"


def _conflicts(reviewers: list[dict[str, Any]]) -> list[str]:
    statuses = {reviewer["role"]: reviewer["status"] for reviewer in reviewers}
    if statuses.get("risk_reviewer") == "fail" and statuses.get("critic") == "pass":
        return ["Risk reviewer blocked the run while critic did not flag the same issue."]
    return []


def _next_actions(decision: str) -> list[str]:
    return {
        "approve": ["Attach TradingView or MT5 runtime evidence before claiming compile, backtest, or platform execution success."],
        "changes_requested": ["Fix reviewer findings and rerun static validation plus parallel review."],
        "manual_required": ["Collect manual platform evidence or inspect reviewer warnings before approving the run."],
        "blocked": ["Remove blocked risk claims or live-trading automation scope, then rerun review."],
    }[decision]


def _decision_as_validation_status(decision: str) -> str:
    return {
        "approve": "pass",
        "changes_requested": "fail",
        "manual_required": "manual_required",
        "blocked": "fail",
    }[decision]


def _blocked_risk_claims(text: str) -> list[str]:
    blocked_terms = ("guarantee profit", "guarantee returns", "guarantees profit", "guarantees returns", "guaranteed profit", "guaranteed returns", "risk-free", "cannot lose money", "live ready", "live-ready", "live immediately", "broker deployment", "broker integration", "autonomous live")
    blockers: list[str] = []
    for term in blocked_terms:
        for match in re.finditer(re.escape(term), text):
            prefix = text[max(0, match.start() - 32) : match.start()]
            if any(marker in prefix for marker in ("no ", "without ", "before any ", "do not ", "not ")):
                continue
            blockers.append(term)
            break
    return blockers


def _read_optional_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _load_model_registry(path: Path | None = None) -> dict[str, Any]:
    registry_path = resolve_repo_path(path or repo_root() / "configs" / "model-registry.example.yaml")
    return yaml.safe_load(registry_path.read_text(encoding="utf-8"))
