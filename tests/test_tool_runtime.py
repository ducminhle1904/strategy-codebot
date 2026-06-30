import json
from pathlib import Path

import pytest
import yaml
from jsonschema import ValidationError

from strategy_codebot.schemas import validate_payload
from strategy_codebot.tool_runtime import (
    POLICY_ENFORCE,
    ToolBlockedError,
    ToolHarness,
    check_tool_registry,
    find_blocked_claims,
    load_tool_registry,
)


def test_tool_registry_contracts_are_valid() -> None:
    registry = load_tool_registry(Path("configs/tool-registry.yaml"))

    assert {tool["id"] for tool in registry["tools"]} >= {
        "load_strategy_spec",
        "run_parallel_review",
        "knowledge_snapshot",
        "knowledge_diff",
        "knowledge_audit",
        "knowledge_check",
        "knowledge_proposal",
        "doctor_check",
        "record_harness_trace",
    }
    for tool in registry["tools"]:
        validate_payload(tool, "tool-contract.schema.json")


def test_tool_contract_schema_accepts_provider_metadata() -> None:
    validate_payload(
        {
            "id": "canonical_tool",
            "capability": "example",
            "risk_tier": "read",
            "input_schema_ref": "input",
            "output_schema_ref": "output",
            "evidence_required": ["evidence"],
            "phase_status": "implemented",
            "backend_handler": "provider_tool",
            "provider_exposed": True,
            "aliases": ["legacy_provider_tool"],
            "presentation": {"card_kind": "summary", "tool_name": "Provider tool"},
        },
        "tool-contract.schema.json",
    )


def test_tool_contract_schema_rejects_missing_risk_metadata() -> None:
    with pytest.raises(ValidationError):
        validate_payload(
            {
                "id": "bad_tool",
                "capability": "bad",
                "input_schema_ref": "in",
                "output_schema_ref": "out",
                "evidence_required": ["evidence"],
                "phase_status": "implemented",
            },
            "tool-contract.schema.json",
        )


def test_policy_allows_explicit_avoid_profitability_claim_language() -> None:
    findings = find_blocked_claims(
        "The goal is to demonstrate a risk-managed approach rather than claiming specific profitability."
    )

    assert findings == []


def test_tool_registry_check_reports_valid_registry() -> None:
    report = check_tool_registry(Path("configs/tool-registry.yaml"))

    assert report["status"] == "pass"
    assert any(check["name"] == "load_strategy_spec:schema" for check in report["checks"])


def test_tool_registry_check_rejects_duplicate_provider_names(tmp_path: Path) -> None:
    registry_path = tmp_path / "tool-registry.yaml"
    registry_path.write_text(
        yaml.safe_dump(
            {
                "tools": [
                    {
                        "id": "first",
                        "capability": "one",
                        "risk_tier": "read",
                        "input_schema_ref": "input",
                        "output_schema_ref": "output",
                        "evidence_required": ["evidence"],
                        "phase_status": "implemented",
                        "backend_handler": "same_provider_name",
                        "provider_exposed": True,
                    },
                    {
                        "id": "second",
                        "capability": "two",
                        "risk_tier": "read",
                        "input_schema_ref": "input",
                        "output_schema_ref": "output",
                        "evidence_required": ["evidence"],
                        "phase_status": "implemented",
                        "backend_handler": "same_provider_name",
                        "provider_exposed": True,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    report = check_tool_registry(registry_path)

    assert report["status"] == "fail"
    assert any(check["name"] == "second:provider_name:same_provider_name" for check in report["checks"])


def test_tool_harness_records_started_and_completed_events() -> None:
    harness = ToolHarness(run_id="tool-pass")

    result = harness.call("load_strategy_spec", lambda: {"ok": True}, output_refs=["strategy-spec.json"])

    assert result == {"ok": True}
    assert [event["event_type"] for event in harness.events] == ["tool.started", "tool.completed"]
    assert [event["sequence"] for event in harness.events] == [1, 2]


def test_tool_harness_records_agent_lifecycle_event() -> None:
    harness = ToolHarness(run_id="agent-pass")

    event = harness.record_event(
        "agent.started",
        workflow="multi-agent",
        stage="strategy_reasoning",
        agent_role="trading_analyst",
        model="openai/test-model",
        provider="openai",
        status="started",
    )

    validate_payload(event, "tool-event.schema.json")
    assert event["event_type"] == "agent.started"
    assert event["agent_role"] == "trading_analyst"


def test_tool_event_schema_rejects_unknown_status_and_failure_class() -> None:
    event = {
        "sequence": 1,
        "created_at": "2026-06-16T00:00:00+00:00",
        "run_id": "bad-event",
        "event_type": "llm.completed",
        "policy_mode": "observe",
        "status": "retrying",
        "failure_class": "rate_limit",
    }

    with pytest.raises(ValidationError):
        validate_payload(event, "tool-event.schema.json")


def test_tool_harness_records_failed_event() -> None:
    harness = ToolHarness(run_id="tool-fail")

    def fail() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        harness.call("validate_pine_static", fail)

    assert harness.events[-1]["event_type"] == "tool.failed"
    assert harness.events[-1]["error"]["type"] == "RuntimeError"


def test_tool_harness_records_ordered_blocked_and_completed_events() -> None:
    harness = ToolHarness(run_id="ordered-tool-events")

    harness.record_blocked_tool("unknown_tool", "Tool is not allowed in this loop.", risk_tier="unknown")
    harness.call("load_strategy_spec", lambda: {"ok": True})

    assert [event["sequence"] for event in harness.events] == [1, 2, 3, 4]
    assert [event["event_type"] for event in harness.events] == [
        "tool.started",
        "tool.blocked",
        "tool.started",
        "tool.completed",
    ]


def test_tool_harness_rejects_unknown_tool_id() -> None:
    harness = ToolHarness(run_id="unknown-tool")

    with pytest.raises(ValueError, match="Unknown tool id"):
        harness.call("record_harness_trce", lambda: None)


def test_tool_harness_enforce_blocks_prohibited_risk_tier(tmp_path: Path) -> None:
    registry_path = tmp_path / "tool-registry.yaml"
    registry_path.write_text(
        yaml.safe_dump(
            {
                "tools": [
                    {
                        "id": "dangerous_tool",
                        "capability": "danger",
                        "risk_tier": "destructive",
                        "input_schema_ref": "input",
                        "output_schema_ref": "output",
                        "evidence_required": ["approval"],
                        "phase_status": "planned",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    harness = ToolHarness(run_id="tool-block", policy_mode=POLICY_ENFORCE, registry=load_tool_registry(registry_path))

    with pytest.raises(ToolBlockedError):
        harness.call("dangerous_tool", lambda: None)

    assert [event["event_type"] for event in harness.events] == ["tool.started", "tool.blocked"]


def test_runtime_trace_jsonl_and_summary_validate(tmp_path: Path) -> None:
    harness = ToolHarness(run_id="trace-write")
    harness.call("load_strategy_spec", lambda: {"ok": True})
    trace_path = tmp_path / "runtime-trace.jsonl"
    summary_path = tmp_path / "runtime-summary.json"

    summary = harness.write_trace(trace_path, summary_path, ["strategy-spec.json"])

    for line in trace_path.read_text(encoding="utf-8").splitlines():
        validate_payload(json.loads(line), "tool-event.schema.json")
    validate_payload(summary, "runtime-trace.schema.json")
    assert summary["event_count"] == 2
    assert summary["completed_tools"] == ["load_strategy_spec"]
