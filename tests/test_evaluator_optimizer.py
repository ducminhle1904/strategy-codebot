from __future__ import annotations

import json

from strategy_codebot import evals as evals_module
from strategy_codebot import live as live_module
from strategy_codebot.evaluator_optimizer import evaluator_stop_reason
from strategy_codebot.evaluator_optimizer import repair_source_mix
from strategy_codebot.evaluator_optimizer import validation_allows_artifact
from strategy_codebot.harness_types import STATUS_FAIL, STATUS_PASS
from strategy_codebot.paths import repo_root


def test_evaluator_stop_reason_treats_blocker_as_policy_block() -> None:
    reason = evaluator_stop_reason(
        validation={"status": STATUS_FAIL, "checks": [{"name": "static", "status": STATUS_FAIL}]},
        final_review_status="approve",
        production_gate={"status": STATUS_PASS},
        policy_findings=[{"severity": "blocker"}],
        budget_exhausted=True,
    )

    assert reason == "policy_blocked"


def test_evaluator_stop_reason_precedence_after_policy() -> None:
    assert (
        evaluator_stop_reason(
            validation={"status": STATUS_FAIL, "checks": [{"name": "static", "status": STATUS_FAIL}]},
            final_review_status="approve",
            production_gate={"status": STATUS_PASS},
            policy_findings=[],
            budget_exhausted=True,
        )
        == "repair_budget_exhausted"
    )
    assert (
        evaluator_stop_reason(
            validation={"status": STATUS_FAIL, "checks": [{"name": "static", "status": STATUS_FAIL}]},
            final_review_status="approve",
            production_gate={"status": STATUS_PASS},
            policy_findings=[],
            budget_exhausted=False,
        )
        == "validation_blocked"
    )


def test_repair_source_mix_combines_history_metrics_and_unknown_remainder() -> None:
    mix = repair_source_mix(
        [
            {"repair_source": "llm"},
            {"stage": "compact_free_validation_repair"},
            {"repair_source": "manual"},
        ],
        repair_count=6,
        deterministic_repair_count=2,
    )

    assert mix == {"llm": 2, "deterministic": 2, "unknown": 2}


def test_validation_allows_manual_required_without_failed_checks() -> None:
    assert validation_allows_artifact({"status": "manual_required", "checks": []}) is True
    assert (
        validation_allows_artifact(
            {"status": "manual_required", "checks": [{"name": "static", "status": STATUS_FAIL}]}
        )
        is False
    )


def test_live_and_eval_evaluator_summaries_share_canonical_rules() -> None:
    validation = {"status": STATUS_PASS, "checks": []}
    production_gate = {
        "status": STATUS_FAIL,
        "blocking_required_fixes": ["Add strategy.exit risk controls."],
    }
    repair_history = [{"repair_source": "llm"}]
    live_summary = live_module._evaluator_optimizer_summary(
        validation=validation,
        review_output={},
        production_gate=production_gate,
        policy_findings=[],
        repair_count=1,
        repair_history=repair_history,
        repair_loop_metrics={"llm_repair_count": 1},
    )
    eval_summary = evals_module._evaluator_optimizer_summary_from_artifacts(
        metadata={"repair_count": 1, "llm_repair_count": 1},
        workflow_trace={"repair_history": repair_history},
        diagnostics={},
        validation=validation,
        production_gate=production_gate,
        review_report=None,
    )

    assert live_summary == eval_summary


def test_tool_event_schema_contains_agent_workflow_observability_events() -> None:
    schema_path = repo_root() / "schemas" / "tool-event.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert {
        "evaluator_optimizer.summary",
        "agent_loop.started",
        "agent_loop.llm_completed",
        "agent_loop.tool_checked",
        "agent_loop.completed",
    }.issubset(set(schema["properties"]["event_type"]["enum"]))
