from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from strategy_codebot.paths import ensure_parent, repo_root
from strategy_codebot.reporting import aggregate_status, validation_check
from strategy_codebot.schemas import validate_payload, write_json


RUNTIME_TRACE_PATH = "runtime-trace.jsonl"
RUNTIME_SUMMARY_PATH = "runtime-summary.json"
POLICY_OBSERVE = "observe"
POLICY_ENFORCE = "enforce"
POLICY_MODES = {POLICY_OBSERVE, POLICY_ENFORCE}
BLOCKED_RISK_TIERS = {"broker_write", "destructive"}
BLOCKED_CLAIMS = ("guaranteed profit", "guaranteed returns", "risk-free", "live trading", "broker integration", "autonomous live")
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
        self._record("tool.started", contract, input_refs=input_refs, output_refs=output_refs)

        block_reason = self._block_reason(contract, policy_text)
        if block_reason:
            error = {"type": "ToolBlockedError", "message": block_reason}
            self._record("tool.blocked", contract, input_refs=input_refs, output_refs=output_refs, error=error)
            raise ToolBlockedError(block_reason)

        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            self._record("tool.failed", contract, input_refs=input_refs, output_refs=output_refs, error=error)
            raise

        self._record("tool.completed", contract, input_refs=input_refs, output_refs=output_refs)
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
        completed = [event["tool_id"] for event in self.events if event["event_type"] == "tool.completed"]
        failed = [event["tool_id"] for event in self.events if event["event_type"] == "tool.failed"]
        blocked = [event["tool_id"] for event in self.events if event["event_type"] == "tool.blocked"]
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
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            checks.append({"name": f"tool_{index}:mapping", "status": "fail", "details": "Each tool entry must be a mapping."})
            continue
        tool_id = str(tool.get("id", f"tool_{index}"))
        missing = sorted(REQUIRED_TOOL_KEYS - set(tool))
        checks.append(validation_check(f"{tool_id}:required_metadata", not missing, f"Missing keys: {', '.join(missing)}" if missing else "Required metadata present."))
        checks.append(validation_check(f"{tool_id}:unique_id", tool_id not in seen, "Duplicate tool id." if tool_id in seen else "Tool id is unique."))
        seen.add(tool_id)
        try:
            validate_payload(tool, "tool-contract.schema.json")
        except Exception as exc:
            checks.append({"name": f"{tool_id}:schema", "status": "fail", "details": str(exc)})
        else:
            checks.append({"name": f"{tool_id}:schema", "status": "pass", "details": "Tool contract schema is valid."})

    status = aggregate_status({check["status"] for check in checks})
    return {
        "platform": "both",
        "status": status,
        "checks": checks,
        "evidence": [str(registry_path)],
        "warnings": warnings,
        "next_actions": [] if status == "pass" else ["Fix tool registry metadata before enabling runtime harness changes."],
    }


def _contains_blocked_claim(text: str) -> bool:
    lowered = text.lower()
    for claim in BLOCKED_CLAIMS:
        start = 0
        while True:
            index = lowered.find(claim, start)
            if index == -1:
                break
            prefix = lowered[max(0, index - 32) : index]
            if not any(marker in prefix for marker in ("no ", "without ", "before any ", "do not ", "not ")):
                return True
            start = index + len(claim)
    return False


def _contracts_by_id(registry: dict[str, Any]) -> dict[str, ToolContract]:
    contracts: dict[str, ToolContract] = {}
    for entry in registry.get("tools", []):
        if isinstance(entry, dict) and entry.get("id"):
            contracts[str(entry["id"])] = ToolContract(id=str(entry["id"]), risk_tier=str(entry.get("risk_tier", "unknown")))
    return contracts
