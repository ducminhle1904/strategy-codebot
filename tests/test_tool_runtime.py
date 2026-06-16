import json
from pathlib import Path

import pytest
import yaml
from jsonschema import ValidationError

from strategy_codebot.schemas import validate_payload
from strategy_codebot.tool_runtime import POLICY_ENFORCE, ToolBlockedError, ToolHarness, check_tool_registry, load_tool_registry


def test_tool_registry_contracts_are_valid() -> None:
    registry = load_tool_registry(Path("configs/tool-registry.yaml"))

    assert {tool["id"] for tool in registry["tools"]} >= {
        "load_strategy_spec",
        "run_parallel_review",
        "knowledge_snapshot",
        "knowledge_diff",
        "knowledge_audit",
        "knowledge_proposal",
        "record_harness_trace",
    }
    for tool in registry["tools"]:
        validate_payload(tool, "tool-contract.schema.json")


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


def test_tool_registry_check_reports_valid_registry() -> None:
    report = check_tool_registry(Path("configs/tool-registry.yaml"))

    assert report["status"] == "pass"
    assert any(check["name"] == "load_strategy_spec:schema" for check in report["checks"])


def test_tool_harness_records_started_and_completed_events() -> None:
    harness = ToolHarness(run_id="tool-pass")

    result = harness.call("load_strategy_spec", lambda: {"ok": True}, output_refs=["strategy-spec.json"])

    assert result == {"ok": True}
    assert [event["event_type"] for event in harness.events] == ["tool.started", "tool.completed"]
    assert [event["sequence"] for event in harness.events] == [1, 2]


def test_tool_harness_records_failed_event() -> None:
    harness = ToolHarness(run_id="tool-fail")

    def fail() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        harness.call("validate_pine_static", fail)

    assert harness.events[-1]["event_type"] == "tool.failed"
    assert harness.events[-1]["error"]["type"] == "RuntimeError"


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
