from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from strategy_codebot.harness_types import FAILURE_POLICY_VIOLATION, FAILURE_TOOL_ERROR, STATUS_BLOCKED, STATUS_FAIL, STATUS_PASS, STATUS_STARTED
from strategy_codebot.paths import ensure_parent, repo_root
from strategy_codebot.policy_engine import EVIDENCE_GENERATED_ARTIFACT
from strategy_codebot.policy_engine import EVIDENCE_STRATEGY_IDEA
from strategy_codebot.policy_engine import contains_blocking_policy as contains_engine_blocking_policy
from strategy_codebot.policy_engine import find_policy_findings as find_engine_policy_findings
from strategy_codebot.policy_engine import validate_policy_rules
from strategy_codebot.reporting import aggregate_status, validation_check
from strategy_codebot.schemas import validate_payload, write_json


RUNTIME_TRACE_PATH = "runtime-trace.jsonl"
RUNTIME_SUMMARY_PATH = "runtime-summary.json"
POLICY_OBSERVE = "observe"
POLICY_ENFORCE = "enforce"
POLICY_MODES = {POLICY_OBSERVE, POLICY_ENFORCE}
BLOCKED_RISK_TIERS = {"broker_write", "destructive"}
REQUIRED_TOOL_KEYS = {"id", "capability", "risk_tier", "input_schema_ref", "output_schema_ref", "evidence_required", "phase_status"}


class ToolBlockedError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolContract:
    id: str
    risk_tier: str


class ToolHarness:
    def __init__(self, *, run_id: str, policy_mode: str = POLICY_OBSERVE, registry: dict[str, Any] | None = None) -> None:
        if policy_mode not in POLICY_MODES:
            raise ValueError("policy must be observe or enforce")
        self.run_id = run_id
        self.policy_mode = policy_mode
        self.registry = registry or load_tool_registry(repo_root() / "configs" / "tool-registry.yaml")
        self.contracts = _contracts_by_id(self.registry)
        self.events: list[dict[str, Any]] = []

    def record_event(self, event_type: str, **fields: Any) -> dict[str, Any]:
        event: dict[str, Any] = {
            "sequence": len(self.events) + 1,
            "created_at": datetime.now(UTC).isoformat(),
            "run_id": self.run_id,
            "event_type": event_type,
            "policy_mode": self.policy_mode,
            **{key: value for key, value in fields.items() if value is not None},
        }
        validate_payload(event, "tool-event.schema.json")
        self.events.append(event)
        return event

    def record_external_event(self, event: dict[str, Any]) -> dict[str, Any]:
        fields = {key: value for key, value in event.items() if key not in {"sequence", "created_at", "run_id", "event_type"}}
        return self.record_event(str(event["event_type"]), **fields)

    def record_external_events(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            self.record_external_event(event)

    def record_blocked_tool(
        self,
        tool_id: str,
        reason: str,
        *,
        risk_tier: str | None = None,
        input_refs: list[str] | None = None,
        output_refs: list[str] | None = None,
        failure_class: str = FAILURE_POLICY_VIOLATION,
    ) -> None:
        contract = self.contracts.get(tool_id, ToolContract(id=tool_id, risk_tier=risk_tier or "unknown"))
        input_refs = input_refs or []
        output_refs = output_refs or []
        self._record("tool.started", contract, input_refs=input_refs, output_refs=output_refs, status=STATUS_STARTED)
        self._record(
            "tool.blocked",
            contract,
            input_refs=input_refs,
            output_refs=output_refs,
            error={"type": "ToolBlockedError", "message": reason},
            status=STATUS_BLOCKED,
            failure_class=failure_class,
        )

    def call(
        self,
        tool_id: str,
        func: Callable[..., Any],
        *args: Any,
        input_refs: list[str] | None = None,
        output_refs: list[str] | None = None,
        policy_text: str | None = None,
        **kwargs: Any,
    ) -> Any:
        contract = self._contract(tool_id)
        input_refs = input_refs or []
        output_refs = output_refs or []
        self._record("tool.started", contract, input_refs=input_refs, output_refs=output_refs, status=STATUS_STARTED)

        block_reason = self._block_reason(contract, policy_text)
        if block_reason:
            error = {"type": "ToolBlockedError", "message": block_reason}
            self._record("tool.blocked", contract, input_refs=input_refs, output_refs=output_refs, error=error, status=STATUS_BLOCKED, failure_class=FAILURE_POLICY_VIOLATION)
            raise ToolBlockedError(block_reason)

        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            self._record("tool.failed", contract, input_refs=input_refs, output_refs=output_refs, error=error, status=STATUS_FAIL, failure_class=FAILURE_TOOL_ERROR)
            raise

        self._record("tool.completed", contract, input_refs=input_refs, output_refs=output_refs, status=STATUS_PASS)
        return result

    def write_trace(self, trace_path: Path, summary_path: Path, output_refs: list[str]) -> dict[str, Any]:
        ensure_parent(trace_path)
        with trace_path.open("w", encoding="utf-8") as handle:
            for event in self.events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        summary = self.summary(trace_path.name, output_refs)
        validate_payload(summary, "runtime-trace.schema.json")
        write_json(summary_path, summary)
        return summary

    def summary(self, trace_ref: str, output_refs: list[str]) -> dict[str, Any]:
        completed = [event["tool_id"] for event in self.events if event["event_type"] == "tool.completed" and "tool_id" in event]
        failed = [event["tool_id"] for event in self.events if event["event_type"] == "tool.failed" and "tool_id" in event]
        blocked = [event["tool_id"] for event in self.events if event["event_type"] == "tool.blocked" and "tool_id" in event]
        return {
            "run_id": self.run_id,
            "created_at": datetime.now(UTC).isoformat(),
            "policy_mode": self.policy_mode,
            "trace_ref": trace_ref,
            "event_count": len(self.events),
            "completed_tools": completed,
            "failed_tools": failed,
            "blocked_tools": blocked,
            "output_refs": output_refs,
        }

    def _contract(self, tool_id: str) -> ToolContract:
        try:
            return self.contracts[tool_id]
        except KeyError as exc:
            raise ValueError(f"Unknown tool id: {tool_id}") from exc

    def _block_reason(self, contract: ToolContract, policy_text: str | None) -> str | None:
        if self.policy_mode != POLICY_ENFORCE:
            return None
        if contract.risk_tier in BLOCKED_RISK_TIERS:
            return f"{contract.id} is blocked by risk tier {contract.risk_tier}."
        if policy_text and _contains_blocked_claim(policy_text):
            return f"{contract.id} is blocked by Phase 3 policy because live/profit claim language was detected."
        return None

    def _record(
        self,
        event_type: str,
        contract: ToolContract,
        *,
        input_refs: list[str],
        output_refs: list[str],
        error: dict[str, str] | None = None,
        status: str | None = None,
        failure_class: str | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "sequence": len(self.events) + 1,
            "created_at": datetime.now(UTC).isoformat(),
            "run_id": self.run_id,
            "event_type": event_type,
            "tool_id": contract.id,
            "policy_mode": self.policy_mode,
            "risk_tier": contract.risk_tier,
            "input_refs": input_refs,
            "output_refs": output_refs,
        }
        if status:
            event["status"] = status
        if failure_class:
            event["failure_class"] = failure_class
        if error:
            event["error"] = error
        validate_payload(event, "tool-event.schema.json")
        self.events.append(event)


def load_tool_registry(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {"tools": []}


def call_tool(tool_harness: ToolHarness | None, tool_id: str, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    input_refs = kwargs.pop("input_refs", None)
    output_refs = kwargs.pop("output_refs", None)
    policy_text = kwargs.pop("policy_text", None)
    if tool_harness is None:
        return func(*args, **kwargs)
    return tool_harness.call(tool_id, func, *args, input_refs=input_refs, output_refs=output_refs, policy_text=policy_text, **kwargs)


def tool_ids(registry_path: Path) -> list[str]:
    registry = load_tool_registry(registry_path)
    return [str(tool.get("id")) for tool in registry.get("tools", []) if isinstance(tool, dict) and tool.get("id")]


def check_tool_registry(registry_path: Path) -> dict[str, Any]:
    registry = load_tool_registry(registry_path)
    tools = registry.get("tools", [])
    checks: list[dict[str, str]] = []
    warnings: list[str] = []

    checks.append(validation_check("tools_present", bool(tools), f"Found {len(tools)} tools." if tools else "Registry must contain tools."))
    seen: set[str] = set()
    seen_provider_names: dict[str, str] = {}
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            checks.append({"name": f"tool_{index}:mapping", "status": STATUS_FAIL, "details": "Each tool entry must be a mapping."})
            continue
        tool_id = str(tool.get("id", f"tool_{index}"))
        missing = sorted(REQUIRED_TOOL_KEYS - set(tool))
        checks.append(validation_check(f"{tool_id}:required_metadata", not missing, f"Missing keys: {', '.join(missing)}" if missing else "Required metadata present."))
        checks.append(validation_check(f"{tool_id}:unique_id", tool_id not in seen, "Duplicate tool id." if tool_id in seen else "Tool id is unique."))
        seen.add(tool_id)
        try:
            validate_payload(tool, "tool-contract.schema.json")
        except Exception as exc:
            checks.append({"name": f"{tool_id}:schema", "status": STATUS_FAIL, "details": str(exc)})
        provider_names: list[str] = []
        if tool.get("provider_exposed") is True:
            backend_handler = str(tool.get("backend_handler") or "").strip()
            if backend_handler:
                provider_names.append(backend_handler)
            aliases = tool.get("aliases", [])
            if isinstance(aliases, list):
                provider_names.extend(str(alias).strip() for alias in aliases if str(alias).strip())
            provider_names = list(dict.fromkeys(provider_names))
            checks.append(
                validation_check(
                    f"{tool_id}:provider_metadata",
                    bool(provider_names),
                    "Provider-exposed tools must declare backend_handler or aliases." if not provider_names else "Provider metadata present.",
                )
            )
        for provider_name in provider_names:
            previous = seen_provider_names.get(provider_name)
            checks.append(
                validation_check(
                    f"{tool_id}:provider_name:{provider_name}",
                    previous is None,
                    f"Provider name already claimed by {previous}." if previous else "Provider name is unique.",
                )
            )
            seen_provider_names.setdefault(provider_name, tool_id)
        else:
            checks.append({"name": f"{tool_id}:schema", "status": STATUS_PASS, "details": "Tool contract schema is valid."})

    policy_report = validate_policy_rules()
    checks.append(
        validation_check(
            "policy_rules:schema",
            policy_report["status"] == STATUS_PASS,
            f"Policy rules loaded: {policy_report['rule_count']}" if policy_report["status"] == STATUS_PASS else "; ".join(policy_report["errors"]),
        )
    )
    status = aggregate_status({check["status"] for check in checks})
    return {
        "platform": "both",
        "status": status,
        "checks": checks,
        "evidence": [str(registry_path)],
        "warnings": warnings,
        "next_actions": [] if status == STATUS_PASS else ["Fix tool registry metadata before enabling runtime harness changes."],
    }


def contains_blocked_claim(text: str) -> bool:
    return contains_engine_blocking_policy(text, surface="policy_text", evidence_level=EVIDENCE_GENERATED_ARTIFACT)


def find_blocked_claims(text: str) -> list[dict[str, str]]:
    return [finding for finding in find_policy_claims(text) if finding.get("severity") == "block"]


def find_prompt_boundary_violations(text: str) -> list[dict[str, str]]:
    return [
        _legacy_policy_finding(finding, claim_field="rule_id")
        for finding in find_engine_policy_findings(text, surface="user_prompt", evidence_level=EVIDENCE_STRATEGY_IDEA)
        if finding.get("severity") == "blocker"
    ]


def find_policy_claims(text: str) -> list[dict[str, str]]:
    return [
        _legacy_policy_finding(finding)
        for finding in find_engine_policy_findings(text, surface="policy_text", evidence_level=EVIDENCE_GENERATED_ARTIFACT)
    ]


def _legacy_policy_finding(finding: dict[str, str], *, claim_field: str = "matched_text") -> dict[str, str]:
    payload = dict(finding)
    payload.update({
        "claim": finding.get(claim_field) or finding.get("claim") or finding.get("matched_text", ""),
        "severity": "block" if finding.get("severity") == "blocker" else "warn",
    })
    return payload


def _contains_blocked_claim(text: str) -> bool:
    return contains_blocked_claim(text)


def _contracts_by_id(registry: dict[str, Any]) -> dict[str, ToolContract]:
    contracts: dict[str, ToolContract] = {}
    for entry in registry.get("tools", []):
        if isinstance(entry, dict) and entry.get("id"):
            contracts[str(entry["id"])] = ToolContract(id=str(entry["id"]), risk_tier=str(entry.get("risk_tier", "unknown")))
    return contracts
