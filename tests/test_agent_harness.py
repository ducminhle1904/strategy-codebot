import json
from pathlib import Path

from strategy_codebot.agent_harness import classify_failure, inspect_run, otel_spans_for_run, write_otel_export
from strategy_codebot.schemas import write_json


def test_inspect_run_reports_timeline_and_failure_attribution(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_json(
        run_dir / "agent-run.json",
        {
            "run_id": "run",
            "created_at": "2026-06-16T00:00:00+00:00",
            "agent_role": "pine_specialist",
            "provider": "openai",
            "model": "openai/test",
            "input_refs": ["prompt"],
            "output_refs": ["strategy-spec.json"],
            "status": "pass",
        },
    )
    write_json(
        run_dir / "validation-report.json",
        {
            "platform": "pine_v6",
            "status": "pass",
            "checks": [],
            "evidence": [],
            "warnings": [],
            "next_actions": [],
        },
    )
    write_json(
        run_dir / "runtime-summary.json",
        {
            "run_id": "run",
            "created_at": "2026-06-16T00:00:00+00:00",
            "policy_mode": "observe",
            "trace_ref": "runtime-trace.jsonl",
            "event_count": 1,
            "completed_tools": ["load_strategy_spec"],
            "failed_tools": [],
            "blocked_tools": [],
            "output_refs": ["agent-run.json"],
        },
    )
    (run_dir / "runtime-trace.jsonl").write_text(
        json.dumps(
            {
                "sequence": 1,
                "created_at": "2026-06-16T00:00:00+00:00",
                "run_id": "run",
                "event_type": "agent.started",
                "policy_mode": "observe",
                "workflow": "multi-agent",
                "stage": "strategy_reasoning",
                "agent_role": "trading_analyst",
                "status": "started",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = inspect_run(run_dir)

    assert report["status"] == "pass"
    assert report["stage_timeline"][0]["event_type"] == "agent.started"
    assert report["failure_attribution"] == []


def test_inspect_run_treats_approved_review_as_pass(tmp_path: Path) -> None:
    run_dir = tmp_path / "approved-review"
    run_dir.mkdir()
    write_json(
        run_dir / "agent-run.json",
        {
            "run_id": "approved-review",
            "created_at": "2026-06-16T00:00:00+00:00",
            "agent_role": "pine_specialist",
            "provider": "dry-run",
            "model": "deterministic-template",
            "input_refs": ["strategy-spec.json"],
            "output_refs": ["strategy-spec.json", "review-report.json"],
            "status": "pass",
        },
    )
    write_json(
        run_dir / "validation-report.json",
        {
            "platform": "pine_v6",
            "status": "pass",
            "checks": [],
            "evidence": [],
            "warnings": [],
            "next_actions": [],
        },
    )
    write_json(
        run_dir / "review-report.json",
        {
            "run_id": "approved-review",
            "run_status": "pass",
            "decision": "approve",
            "reviewers": [],
            "findings": [],
            "conflicts": [],
            "warnings": [],
            "next_actions": [],
        },
    )
    write_json(
        run_dir / "runtime-summary.json",
        {
            "run_id": "approved-review",
            "created_at": "2026-06-16T00:00:00+00:00",
            "policy_mode": "observe",
            "trace_ref": "runtime-trace.jsonl",
            "event_count": 0,
            "completed_tools": [],
            "failed_tools": [],
            "blocked_tools": [],
            "output_refs": ["agent-run.json"],
        },
    )
    (run_dir / "runtime-trace.jsonl").write_text("", encoding="utf-8")

    report = inspect_run(run_dir)

    assert report["status"] == "pass"
    assert report["failure_attribution"] == []


def test_inspect_run_uses_live_error_for_failed_live_run(tmp_path: Path) -> None:
    run_dir = tmp_path / "failed-live"
    run_dir.mkdir()
    write_json(
        run_dir / "agent-run.json",
        {
            "run_id": "failed-live",
            "created_at": "2026-06-16T00:00:00+00:00",
            "agent_role": "pine_specialist",
            "provider": "openai",
            "model": "openai/test",
            "input_refs": ["prompt"],
            "output_refs": ["live-error.json"],
            "status": "fail",
        },
    )
    attempts = [{"stage": "final_gate", "status": "fail", "error_code": "workflow_gate_failed", "failure_class": "review_failed"}]
    lifecycle = [
        {
            "event_id": "evt-1",
            "sequence": 1,
            "created_at": "2026-06-16T00:00:00+00:00",
            "run_id": "failed-live",
            "event_type": "agent.started",
            "policy_mode": "observe",
            "workflow": "multi-agent",
            "stage": "strategy_reasoning",
            "status": "started",
        }
    ]
    write_json(
        run_dir / "live-error.json",
        {
            "code": "provider_error",
            "message": "review failed",
            "attempts": attempts,
            "diagnostics": {
                "workflow_trace": {"workflow": "multi-agent", "lifecycle_events": lifecycle, "attempts": attempts, "stages": [], "final_decision": {"status": "fail", "failure_class": "review_failed"}},
                "final_decision": {"status": "fail", "failure_class": "review_failed", "failure_stage": "final_gate"},
            },
        },
    )

    report = inspect_run(run_dir)
    spans = otel_spans_for_run(run_dir)

    assert report["status"] == "fail"
    assert report["failure_attribution"][0]["failure_class"] == "review_failed"
    assert spans[0]["name"] == "agent.started"


def test_write_otel_export_has_vendor_neutral_span_fields(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_json(
        run_dir / "agent-run.json",
        {
            "run_id": "run",
            "created_at": "2026-06-16T00:00:00+00:00",
            "agent_role": "pine_specialist",
            "provider": "openai",
            "model": "openai/test",
            "input_refs": ["prompt"],
            "output_refs": ["strategy-spec.json"],
            "status": "pass",
        },
    )
    write_json(
        run_dir / "validation-report.json",
        {
            "platform": "pine_v6",
            "status": "pass",
            "checks": [],
            "evidence": [],
            "warnings": [],
            "next_actions": [],
        },
    )
    (run_dir / "runtime-trace.jsonl").write_text(
        json.dumps(
            {
                "sequence": 1,
                "created_at": "2026-06-16T00:00:00+00:00",
                "run_id": "run",
                "event_type": "llm.completed",
                "policy_mode": "observe",
                "stage": "balanced_review",
                "agent_role": "critic",
                "model": "openai/test",
                "provider": "openai",
                "usage": {"total_tokens": 12},
                "status": "pass",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(
        run_dir / "runtime-summary.json",
        {
            "run_id": "run",
            "created_at": "2026-06-16T00:00:00+00:00",
            "policy_mode": "observe",
            "trace_ref": "runtime-trace.jsonl",
            "event_count": 1,
            "completed_tools": [],
            "failed_tools": [],
            "blocked_tools": [],
            "output_refs": ["agent-run.json"],
        },
    )

    spans = write_otel_export(run_dir, tmp_path / "otel.jsonl")

    assert spans[0]["trace_id"]
    assert spans[0]["attributes"]["gen_ai.request.model"] == "openai/test"
    assert "sk-" not in (tmp_path / "otel.jsonl").read_text(encoding="utf-8")


def test_inspect_run_keeps_successful_fallback_failures_as_diagnostics(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_json(
        run_dir / "agent-run.json",
        {
            "run_id": "run",
            "created_at": "2026-06-16T00:00:00+00:00",
            "agent_role": "pine_specialist",
            "provider": "openai",
            "model": "openai/test",
            "input_refs": ["prompt"],
            "output_refs": ["strategy-spec.json"],
            "status": "pass",
        },
    )
    write_json(
        run_dir / "validation-report.json",
        {
            "platform": "pine_v6",
            "status": "pass",
            "checks": [],
            "evidence": [],
            "warnings": [],
            "next_actions": [],
        },
    )
    write_json(
        run_dir / "live-metadata.json",
        {
            "workflow": "multi-agent",
            "attempts": [
                {"stage": "strategy_reasoning", "model": "openai/bad", "provider": "openai", "attempt": 1, "status": "fail", "error_code": "provider_error", "error": "429"},
                {"stage": "strategy_reasoning", "model": "openai/good", "provider": "openai", "attempt": 1, "status": "pass"},
            ],
        },
    )
    write_json(run_dir / "live-workflow-trace.json", {"final_decision": {"status": "pass"}, "stages": []})

    report = inspect_run(run_dir)

    assert report["status"] == "pass"
    assert report["failure_attribution"] == []
    assert report["attempt_diagnostics"][0]["failure_class"] == "provider_rate_limited"


def test_inspect_run_reports_malformed_runtime_trace(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_json(
        run_dir / "agent-run.json",
        {
            "run_id": "run",
            "created_at": "2026-06-16T00:00:00+00:00",
            "agent_role": "pine_specialist",
            "provider": "openai",
            "model": "openai/test",
            "input_refs": ["prompt"],
            "output_refs": ["strategy-spec.json"],
            "status": "pass",
        },
    )
    write_json(
        run_dir / "validation-report.json",
        {
            "platform": "pine_v6",
            "status": "pass",
            "checks": [],
            "evidence": [],
            "warnings": [],
            "next_actions": [],
        },
    )
    write_json(
        run_dir / "runtime-summary.json",
        {
            "run_id": "run",
            "created_at": "2026-06-16T00:00:00+00:00",
            "policy_mode": "observe",
            "trace_ref": "runtime-trace.jsonl",
            "event_count": 2,
            "completed_tools": [],
            "failed_tools": [],
            "blocked_tools": [],
            "output_refs": ["agent-run.json"],
        },
    )
    (run_dir / "runtime-trace.jsonl").write_text('{"event_type": "agent.started"}\n{bad\n', encoding="utf-8")

    report = inspect_run(run_dir)

    assert report["status"] == "fail"
    assert report["trace_parse_errors"][0]["line"] == 2
    assert report["failure_attribution"][0]["failure_class"] == "malformed_response"


def test_otel_export_deduplicates_workflow_lifecycle_events_already_in_runtime_trace(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_json(
        run_dir / "agent-run.json",
        {
            "run_id": "run",
            "created_at": "2026-06-16T00:00:00+00:00",
            "agent_role": "pine_specialist",
            "provider": "openai",
            "model": "openai/test",
            "input_refs": ["prompt"],
            "output_refs": ["strategy-spec.json"],
            "status": "pass",
        },
    )
    write_json(
        run_dir / "validation-report.json",
        {
            "platform": "pine_v6",
            "status": "pass",
            "checks": [],
            "evidence": [],
            "warnings": [],
            "next_actions": [],
        },
    )
    lifecycle_event = {
        "sequence": 1,
        "created_at": "2026-06-16T00:00:00+00:00",
        "run_id": "run",
        "event_type": "agent.started",
        "policy_mode": "observe",
        "workflow": "multi-agent",
        "stage": "workflow",
        "agent_role": "multi_agent_orchestrator",
        "status": "started",
    }
    (run_dir / "runtime-trace.jsonl").write_text(json.dumps(lifecycle_event) + "\n", encoding="utf-8")
    write_json(
        run_dir / "runtime-summary.json",
        {
            "run_id": "run",
            "created_at": "2026-06-16T00:00:00+00:00",
            "policy_mode": "observe",
            "trace_ref": "runtime-trace.jsonl",
            "event_count": 1,
            "completed_tools": [],
            "failed_tools": [],
            "blocked_tools": [],
            "output_refs": ["agent-run.json"],
        },
    )
    write_json(run_dir / "live-workflow-trace.json", {"lifecycle_events": [lifecycle_event]})

    spans = otel_spans_for_run(run_dir)

    assert len(spans) == 1


def test_otel_export_maps_parent_event_id_to_parent_span_id(tmp_path: Path) -> None:
    run_dir = tmp_path / "otel-parent"
    run_dir.mkdir()
    write_json(
        run_dir / "agent-run.json",
        {
            "run_id": "run",
            "created_at": "2026-06-16T00:00:00+00:00",
            "agent_role": "pine_specialist",
            "provider": "openai",
            "model": "openai/test",
            "input_refs": ["prompt"],
            "output_refs": ["strategy-spec.json"],
            "status": "pass",
        },
    )
    write_json(run_dir / "validation-report.json", {"platform": "pine_v6", "status": "pass", "checks": [], "evidence": [], "warnings": [], "next_actions": []})
    events = [
        {
            "event_id": "evt-parent",
            "sequence": 1,
            "created_at": "2026-06-16T00:00:00+00:00",
            "run_id": "run",
            "event_type": "agent.started",
            "policy_mode": "observe",
            "status": "started",
        },
        {
            "event_id": "evt-child",
            "sequence": 2,
            "created_at": "2026-06-16T00:00:01+00:00",
            "run_id": "run",
            "event_type": "llm.started",
            "policy_mode": "observe",
            "status": "started",
            "parent_event_id": "evt-parent",
        },
    ]
    (run_dir / "runtime-trace.jsonl").write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    write_json(
        run_dir / "runtime-summary.json",
        {
            "run_id": "run",
            "created_at": "2026-06-16T00:00:00+00:00",
            "policy_mode": "observe",
            "trace_ref": "runtime-trace.jsonl",
            "event_count": 2,
            "completed_tools": [],
            "failed_tools": [],
            "blocked_tools": [],
            "output_refs": ["agent-run.json"],
        },
    )

    parent, child = otel_spans_for_run(run_dir)

    assert child["parent_span_id"] == parent["span_id"]
    assert child["parent_span_id"] != "evt-parent"


def test_failure_classifier_maps_provider_errors() -> None:
    assert classify_failure("provider_error", "OpenrouterException code 429") == "provider_rate_limited"
    assert classify_failure("provider_error", "No endpoints found 404") == "provider_not_found"
    assert classify_failure("provider_error", "ReadTimeout timed out") == "provider_timeout"


def test_inspect_run_ignores_retried_llm_failure_when_final_gate_passes(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_json(
        run_dir / "agent-run.json",
        {
            "run_id": "run",
            "created_at": "2026-06-16T00:00:00+00:00",
            "agent_role": "pine_specialist",
            "provider": "openai",
            "model": "openai/test",
            "input_refs": ["prompt"],
            "output_refs": ["strategy-spec.json"],
            "status": "pass",
        },
    )
    write_json(
        run_dir / "validation-report.json",
        {
            "platform": "pine_v6",
            "status": "pass",
            "checks": [],
            "evidence": [],
            "warnings": [],
            "next_actions": [],
        },
    )
    (run_dir / "runtime-trace.jsonl").write_text(
        json.dumps(
            {
                "sequence": 1,
                "created_at": "2026-06-16T00:00:00+00:00",
                "run_id": "run",
                "event_type": "llm.completed",
                "policy_mode": "observe",
                "stage": "strategy_reasoning",
                "agent_role": "trading_analyst",
                "model": "openai/test",
                "provider": "openai",
                "status": "fail",
                "failure_class": "malformed_response",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(
        run_dir / "runtime-summary.json",
        {
            "run_id": "run",
            "created_at": "2026-06-16T00:00:00+00:00",
            "policy_mode": "observe",
            "trace_ref": "runtime-trace.jsonl",
            "event_count": 1,
            "completed_tools": [],
            "failed_tools": [],
            "blocked_tools": [],
            "output_refs": ["agent-run.json"],
        },
    )

    report = inspect_run(run_dir)

    assert report["status"] == "pass"
    assert report["failure_attribution"] == []
