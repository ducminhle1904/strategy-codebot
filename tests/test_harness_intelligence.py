from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from strategy_codebot.cli import app
from strategy_codebot.harness_intelligence import apply_approved_improvement, build_context_report, build_intelligence_report, build_latency_matrix, build_latency_report, build_proxy_log_report, propose_improvements, propose_intelligence_lessons, replay_recommendations
from strategy_codebot.schemas import load_json, write_json


def _sample_artifacts(root: Path) -> None:
    run_dir = root / "case-a"
    run_dir.mkdir(parents=True)
    write_json(
        run_dir / "case-eval.json",
        {
            "id": "case-a",
            "status": "fail",
            "case_completed_at": "2026-06-18T00:00:00Z",
            "workflow": "compact-free",
            "user_tier": "free",
            "run_dir": str(run_dir),
            "failure_attribution": [
                {"stage": "compact_free", "failure_class": "static_validation_failed", "details": "script_type"}
            ],
            "route_health_snapshot": [
                {
                    "stage": "compact_free",
                    "model": "openrouter/bad:free",
                    "provider": "openrouter",
                    "status": "cooldown",
                    "failure_count": 1,
                    "success_count": 0,
                    "last_failure_class": "static_validation_failed",
                }
            ],
        },
    )
    write_json(
        run_dir / "live-workflow-trace.json",
        {
            "run_id": "case-a",
            "workflow": "compact-free",
            "user_tier": "free",
            "attempts": [
                {"stage": "compact_free", "model": "openrouter/bad:free", "provider": "openrouter", "status": "fail", "failure_class": "static_validation_failed"},
                {"stage": "compact_free", "model": "openrouter/good:free", "provider": "openrouter", "status": "pass"},
            ],
        },
    )


def test_intelligence_report_aggregates_routes_and_failures(tmp_path: Path) -> None:
    _sample_artifacts(tmp_path)

    report = build_intelligence_report(artifacts_root=tmp_path)

    assert report["status"] == "pass"
    assert report["case_count"] == 1
    assert report["live_trace_count"] == 1
    assert report["evidence_completeness"]["confidence"] == "partial"
    assert report["evidence_completeness"]["eval_report_present"] is False
    assert report["failure_summary"]["by_failure_class"]["static_validation_failed"] >= 1
    assert report["failure_signatures"][0]["stage"] == "compact_free"
    assert any(item["model"] == "openrouter/bad:free" for item in report["scorecard"])
    assert report["route_recommendations"]
    assert report["anti_pollution"]["mutates_registry"] is False


def test_intelligence_report_aggregates_sophistication_weaknesses(tmp_path: Path) -> None:
    run_dir = tmp_path / "case-quality"
    run_dir.mkdir(parents=True)
    write_json(
        run_dir / "quality-report.json",
        {
            "status": "pass",
            "score": 100,
            "sophistication_score": 50,
            "sophistication_grade": "weak",
            "missing_trader_assumptions": ["market_premise", "invalidation"],
            "improvement_hints": ["State regime.", "Define invalidation."],
            "strategy_sophistication": {
                "score": 50,
                "grade": "weak",
                "warn_only": True,
                "missing_trader_assumptions": ["market_premise", "invalidation"],
                "improvement_hints": ["State regime.", "Define invalidation."],
            },
        },
    )

    report = build_intelligence_report(artifacts_root=tmp_path)

    assert report["status"] == "pass"
    assert report["sophistication_summary"]["by_weakness"]["market_premise"] == 1
    assert report["sophistication_summary"]["warn_only"] is True
    assert report["sophistication_signatures"][0]["warn_only"] is True
    assert report["proposal_seed_count"] >= 2


def test_sophistication_proposals_create_warn_first_candidates(tmp_path: Path) -> None:
    report_path = tmp_path / "intelligence.json"
    proposals_path = tmp_path / "proposals.json"
    improvements_path = tmp_path / "improvements.json"
    write_json(
        report_path,
        {
            "status": "pass",
            "artifacts_root": str(tmp_path),
            "evidence_completeness": {"confidence": "full"},
            "failure_signatures": [],
            "failure_summary": {"by_failure_class": {}},
            "sophistication_signatures": [
                {
                    "weakness": "invalidation",
                    "occurrence_count": 2,
                    "evidence_refs": ["case-a", "case-b"],
                    "sample_hints": ["Define invalidation."],
                    "warn_only": True,
                }
            ],
        },
    )

    proposals = propose_intelligence_lessons(report_path, out=proposals_path)
    improvements = propose_improvements(proposals_path, out=improvements_path)

    assert proposals["proposals"][0]["suggested_change_type"] == "prompt_contract_tuning"
    assert proposals["proposals"][0]["warn_only"] is True
    assert improvements["candidates"][0]["candidate_type"] == "prompt_contract_tuning"
    assert improvements["candidates"][0]["suggested_patch"]["warn_only"] is True


def test_intelligence_scorecard_normalizes_aggregate_route_counts(tmp_path: Path) -> None:
    run_dir = tmp_path / "case-aggregate"
    run_dir.mkdir(parents=True)
    write_json(
        run_dir / "case-eval.json",
        {
            "id": "case-aggregate",
            "status": "fail",
            "case_completed_at": "2026-06-18T00:00:00Z",
            "workflow": "compact-free",
            "user_tier": "free",
            "route_health_snapshot": [
                {
                    "stage": "compact_free",
                    "model": "openrouter/bad:free",
                    "provider": "openrouter",
                    "status": "cooldown",
                    "failure_count": 4,
                    "success_count": 1,
                    "last_failure_class": "provider_timeout",
                }
            ],
        },
    )

    report = build_intelligence_report(artifacts_root=tmp_path)

    bad_route = next(item for item in report["scorecard"] if item["model"] == "openrouter/bad:free")
    assert bad_route["attempt_count"] == 5
    assert bad_route["pass_count"] == 1
    assert bad_route["fail_count"] == 4
    assert bad_route["fail_rate"] == 0.8
    assert bad_route["pass_rate"] == 0.2


def test_intelligence_scorecard_prefers_live_trace_over_duplicate_snapshot(tmp_path: Path) -> None:
    _sample_artifacts(tmp_path)

    report = build_intelligence_report(artifacts_root=tmp_path)

    bad_route = next(item for item in report["scorecard"] if item["model"] == "openrouter/bad:free")
    assert bad_route["attempt_count"] == 1
    assert bad_route["fail_count"] == 1
    assert bad_route["fail_rate"] == 1.0


def test_propose_lessons_writes_review_only_proposals(tmp_path: Path) -> None:
    _sample_artifacts(tmp_path / "artifacts")
    report_path = tmp_path / "intelligence.json"
    out_path = tmp_path / "proposals.json"
    write_json(report_path, build_intelligence_report(artifacts_root=tmp_path / "artifacts"))

    payload = propose_intelligence_lessons(report_path, out=out_path, candidates_path=tmp_path / "candidates.json")

    assert payload["status"] == "pass"
    assert payload["proposal_count"] >= 1
    assert payload["proposals"][0]["status"] == "needs_review"
    assert payload["proposals"][0]["failure_signature"]["stage"] == "compact_free"
    assert payload["proposals"][0]["evidence_confidence"] == "partial"
    assert payload["anti_pollution"]["auto_approved"] is False
    assert out_path.exists()


def test_intelligence_report_marks_complete_eval_evidence_full(tmp_path: Path) -> None:
    _sample_artifacts(tmp_path)
    write_json(
        tmp_path / "eval-report.json",
        {
            "status": "fail",
            "is_complete": True,
            "expected_case_count": 1,
            "completed_case_count": 1,
            "pending_case_count": 0,
            "missing_case_ids": [],
            "cases": [{"id": "case-a", "status": "fail"}],
        },
    )

    report = build_intelligence_report(artifacts_root=tmp_path)

    assert report["evidence_completeness"] == {
        "eval_report_present": True,
        "eval_report_complete": True,
        "case_eval_count": 1,
        "live_trace_count": 1,
        "missing_case_ids": [],
        "confidence": "full",
    }


def test_intelligence_report_marks_running_eval_evidence_partial(tmp_path: Path) -> None:
    _sample_artifacts(tmp_path)
    write_json(
        tmp_path / "eval-report.json",
        {
            "status": "running",
            "is_complete": False,
            "expected_case_count": 2,
            "completed_case_count": 1,
            "pending_case_count": 1,
            "missing_case_ids": ["case-b"],
            "cases": [{"id": "case-a", "status": "fail"}],
        },
    )

    report = build_intelligence_report(artifacts_root=tmp_path)

    assert report["evidence_completeness"]["eval_report_present"] is True
    assert report["evidence_completeness"]["eval_report_complete"] is False
    assert report["evidence_completeness"]["missing_case_ids"] == ["case-b"]
    assert report["evidence_completeness"]["confidence"] == "partial"


def test_latency_report_diagnoses_slow_provider_and_timeout_overrun(tmp_path: Path) -> None:
    run_dir = tmp_path / "case-latency"
    run_dir.mkdir(parents=True)
    write_json(
        run_dir / "live-workflow-trace.json",
        {
            "run_id": "case-latency",
            "workflow": "multi-agent",
            "user_tier": "paid_low",
            "attempts": [
                {
                    "stage": "pine_code_generation",
                    "model": "litellm_proxy/paid_low.pine_code_generation",
                    "provider": "unknown",
                    "gateway": "litellm_proxy",
                    "route_model": "paid_low.pine_code_generation",
                    "status": "pass",
                    "latency_ms": 111000,
                    "stage_total_ms": 111000,
                    "duration_ms": 111000,
                    "provider_call_ms": 110000,
                    "stage_input_chars": 10_000,
                    "response_parse_ms": 10,
                    "payload_validation_ms": 5,
                    "policy_scan_ms": 5,
                    "request_timeout_seconds": 90,
                    "timeout_overrun": True,
                }
            ],
        },
    )

    payload = build_latency_report(artifacts_root=tmp_path)
    summary = payload["latency_summary"]

    assert summary["sample_count"] == 1
    assert summary["diagnosis"] == ["sample_too_small", "timeout_policy_not_enforced", "provider_or_proxy_slow"]
    route = summary["by_route"][0]
    assert route["stage"] == "pine_code_generation"
    assert route["max_ms"] == 111000
    assert route["timeout_overrun_count"] == 1
    assert route["proxy_or_provider_suspected"] is True
    assert route["slow_stage_reason"] == "provider_or_proxy_dominates_payload_unlikely"
    assert route["sample_confidence"] == "sample_too_small"
    assert summary["slowest"][0]["provider_call_ratio"] > 0.9
    assert summary["slowest"][0]["payload_unlikely_primary_cause"] is True


def test_intelligence_report_includes_latency_summary(tmp_path: Path) -> None:
    _sample_artifacts(tmp_path)

    report = build_intelligence_report(artifacts_root=tmp_path)

    assert report["latency_summary"]["run_count"] == 1
    assert "by_route" in report["latency_summary"]


def test_cli_latency_report_writes_report(tmp_path: Path) -> None:
    _sample_artifacts(tmp_path / "artifacts")
    out_path = tmp_path / "latency.json"

    result = CliRunner().invoke(app, ["harness", "latency-report", "--artifacts-root", str(tmp_path / "artifacts"), "--out", str(out_path)])

    assert result.exit_code == 0, result.output
    assert "status=pass" in result.output
    assert out_path.exists()
    assert load_json(out_path)["latency_summary"]["run_count"] == 1


def test_context_report_checks_contracts_and_soft_budgets(tmp_path: Path) -> None:
    run_dir = tmp_path / "case-context"
    run_dir.mkdir()
    write_json(
        run_dir / "live-workflow-trace.json",
            {
                "run_id": "case-context",
                "workflow": "multi-agent",
                "user_tier": "paid_low",
                "attempts": [],
                "final_decision": {"status": "pass", "validation_status": "pass", "validation": {"status": "pass"}},
                "stages": [
                {"stage": "strategy_reasoning", "model": "m1", "context_refs": ["prompt", "policy_boundaries"], "stage_input_chars": 1000, "knowledge_context_chars": 20, "output": {"summary": "ok"}},
                {"stage": "strategy_coding", "model": "m2", "context_refs": ["prompt", "policy_boundaries", "schemas/strategy-spec.schema.json"], "stage_input_chars": 1000, "knowledge_context_chars": 20, "output": {"strategy_spec": {"script_type": "strategy"}}},
                {"stage": "pine_code_generation", "model": "m3", "context_refs": ["policy_boundaries", "schemas/strategy-spec.schema.json"], "stage_input_chars": 1000, "knowledge_context_chars": 20, "output": {"pine_code": "//@version=6\nstrategy('x')"}},
                {"stage": "balanced_review", "model": "m4", "context_refs": ["policy_boundaries", "pine_code_generation"], "stage_input_chars": 1000, "knowledge_context_chars": 20, "output": {"verdict": "pass", "required_fixes": [], "rationale": "ok"}},
            ],
        },
    )

    report = build_context_report(artifacts_root=tmp_path)

    assert report["status"] == "pass"
    assert report["stage_count"] == 4
    assert report["missing_context_count"] == 0
    assert report["budget_warning_count"] == 0


def test_context_report_warns_on_budget_and_fails_missing_context(tmp_path: Path) -> None:
    run_dir = tmp_path / "case-context"
    run_dir.mkdir()
    write_json(
        run_dir / "live-workflow-trace.json",
            {
                "run_id": "case-context",
                "workflow": "multi-agent",
                "user_tier": "paid_low",
                "attempts": [],
                "stages": [
                {"stage": "pine_code_generation", "model": "m3", "context_refs": [], "stage_input_chars": 20_000, "knowledge_context_chars": 0, "output": {"pine_code": "x"}},
            ],
        },
    )

    report = build_context_report(artifacts_root=tmp_path)

    assert report["status"] == "fail"
    assert report["missing_context_count"] == 1
    assert report["budget_warning_count"] == 1
    assert report["stage_reports"][0]["unexpected_large_fields"]


def test_cli_context_report_writes_report(tmp_path: Path) -> None:
    _sample_artifacts(tmp_path / "artifacts")
    out_path = tmp_path / "context.json"

    result = CliRunner().invoke(app, ["harness", "context-report", "--artifacts-root", str(tmp_path / "artifacts"), "--out", str(out_path)])

    assert result.exit_code == 0, result.output
    assert "status=pass" in result.output
    assert out_path.exists()
    assert load_json(out_path)["live_trace_count"] == 1


def test_latency_matrix_runs_repeated_evals_and_candidates(monkeypatch, tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text("name: smoke\ncases:\n  - id: case-a\n    prompt: test\n", encoding="utf-8")

    def fake_run_live_eval(**kwargs):
        out_dir = kwargs["out_dir"]
        case_dir = out_dir / "cases" / "case-a"
        case_dir.mkdir(parents=True)
        write_json(out_dir / "eval-report.json", {"status": "pass", "cases": [{"id": "case-a", "status": "pass"}]})
        write_json(
            case_dir / "live-workflow-trace.json",
            {
                "run_id": f"run-{out_dir.name}",
                "workflow": kwargs["workflow"],
                "user_tier": kwargs["user_tier"],
                "attempts": [
                    {
                        "stage": "strategy_coding",
                        "model": "litellm_proxy/paid_low.strategy_coding",
                        "status": "fail",
                        "stage_total_ms": 61000,
                        "provider_call_ms": 61000,
                        "request_timeout_seconds": 60,
                        "timeout_overrun": True,
                    }
                ],
            },
        )
        return {"status": "pass", "cases": [{"id": "case-a", "status": "pass"}]}

    monkeypatch.setattr("strategy_codebot.harness_intelligence.run_live_eval", fake_run_live_eval)

    payload = build_latency_matrix(suite=suite_path, out_root=tmp_path / "matrix", runs=2, out=tmp_path / "matrix.json")

    assert payload["status"] == "pass"
    assert payload["runs_completed"] == 2
    assert payload["latency_summary"]["run_count"] == 2
    assert payload["route_policy_candidates"][0]["suggested_patch"]["action"] == "demote_or_add_fallback"


def test_propose_improvements_accepts_latency_summary_candidates(tmp_path: Path) -> None:
    report_path = tmp_path / "latency.json"
    write_json(
        report_path,
        {
            "latency_summary": {
                "by_route": [
                    {
                        "stage": "pine_code_generation",
                        "model": "litellm_proxy/paid_low.pine_code_generation",
                        "user_tier": "paid_low",
                        "sample_count": 2,
                        "timeout_overrun_count": 2,
                        "slow_count": 2,
                    },
                    {
                        "stage": "repair",
                        "model": "litellm_proxy/paid_low.repair",
                        "user_tier": "paid_low",
                        "sample_count": 2,
                        "timeout_overrun_count": 2,
                        "slow_count": 2,
                    }
                ]
            }
        },
    )

    payload = propose_improvements(report_path)

    assert payload["candidate_count"] == 2
    assert payload["candidates"][0]["candidate_type"] == "route_policy_patch"
    assert payload["candidates"][0]["suggested_patch"]["action"] == "demote_or_add_fallback"
    assert payload["candidates"][0]["suggested_patch"]["recommended_fallback"] == "litellm_proxy/paid_medium.pine_code_generation"
    assert payload["candidates"][0]["suggested_patch"]["quarantine_on"] == {"timeout_overrun_count": 2, "window": "latency_matrix"}
    assert payload["candidates"][1]["suggested_patch"]["action"] == "demote_or_add_fallback"
    assert payload["candidates"][1]["suggested_patch"]["recommended_fallback"] == "litellm_proxy/paid_medium.repair"


def test_propose_improvements_accepts_repeated_slow_pass_candidate(tmp_path: Path) -> None:
    report_path = tmp_path / "latency.json"
    write_json(
        report_path,
        {
            "latency_summary": {
                "by_route": [
                    {
                        "stage": "balanced_review",
                        "model": "litellm_proxy/paid_low.balanced_review",
                        "user_tier": "paid_low",
                        "sample_count": 3,
                        "timeout_overrun_count": 0,
                        "slow_count": 3,
                        "slow_stage_reason": "provider_or_proxy_dominates_payload_unlikely",
                        "proxy_or_provider_suspected": True,
                    }
                ]
            }
        },
    )

    payload = propose_improvements(report_path)

    assert payload["candidate_count"] == 1
    candidate = payload["candidates"][0]
    assert candidate["candidate_type"] == "slow_route_fallback_candidate"
    assert candidate["suggested_patch"]["action"] == "slow_route_add_fallback"
    assert candidate["suggested_patch"]["recommended_fallback"] == "litellm_proxy/paid_medium.balanced_review"
    assert candidate["suggested_patch"]["quarantine_on"] == {"slow_count": 2, "window": "latency_matrix"}


def test_proxy_log_report_redacts_and_matches_run_id(tmp_path: Path) -> None:
    run_dir = tmp_path / "case-a"
    run_dir.mkdir()
    write_json(
        run_dir / "live-workflow-trace.json",
        {
            "run_id": "case-a",
            "workflow": "multi-agent",
            "user_tier": "paid_low",
            "attempts": [
                {
                    "stage": "strategy_coding",
                    "model": "litellm_proxy/paid_low.strategy_coding",
                    "route_model": "paid_low.strategy_coding",
                    "status": "fail",
                    "started_at": "2026-06-18T00:00:00Z",
                    "completed_at": "2026-06-18T00:01:01Z",
                    "stage_total_ms": 61000,
                    "timeout_overrun": True,
                }
            ],
        },
    )

    payload = build_proxy_log_report(
        artifacts_root=tmp_path,
        log_text="case-a paid_low.strategy_coding Authorization=sk-secret123456789 queued\n",
    )

    assert payload["window_count"] == 1
    assert payload["snippet_count"] == 1
    assert "sk-secret" not in payload["snippets"][0]["lines"][0]
    assert "[REDACTED]" in payload["snippets"][0]["lines"][0]
    assert payload["classification_summary"]["proxy_queue_or_rate_limit"] == 1


def test_proxy_log_report_marks_logs_without_matching_identifiers(tmp_path: Path) -> None:
    _sample_artifacts(tmp_path)

    payload = build_proxy_log_report(artifacts_root=tmp_path, log_text="proxy started without request metadata\n")

    assert payload["snippet_count"] == 0
    assert payload["log_match_status"] == "logs_present_no_matching_identifiers"
    assert payload["correlation_confidence"] == "insufficient"


def test_proxy_log_report_uses_app_mirror_when_docker_logs_do_not_match(tmp_path: Path) -> None:
    _sample_artifacts(tmp_path)
    event_path = tmp_path / "case-a" / "proxy-attribution-events.jsonl"
    event_path.write_text(
        json.dumps(
            {
                "event_type": "proxy.attribution",
                "run_id": "case-a",
                "case_id": "case-a",
                "stage": "pine_code_generation",
                "route_model": "paid_low.pine_code_generation",
                "model": "litellm_proxy/paid_low.pine_code_generation",
                "gateway": "litellm_proxy",
                "provider_call_ms": 1490,
                "stage_total_ms": 1500,
                "provider_call_ratio": 0.99,
                "local_processing_ms": 10,
                "stage_input_chars": 8000,
                "output_chars": 2000,
                "status": "pass",
                "prompt": "must be dropped",
                "api_key": "sk-secret123456789",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_proxy_log_report(artifacts_root=tmp_path, log_text="proxy started without request metadata\n")

    assert payload["app_mirror_event_count"] == 1
    assert payload["docker_log_match_status"] == "logs_present_no_matching_identifiers"
    assert payload["correlation_confidence"] == "app_trace_only"
    assert "cannot_split_proxy_vs_upstream_without_structured_proxy_logs" in payload["correlation_notes"]
    assert payload["app_mirror_attribution"]["provider_or_proxy_dominant_count"] == 1
    assert "prompt" not in payload["app_mirror_events"][0]
    assert "api_key" not in payload["app_mirror_events"][0]


def test_proxy_log_report_upgrades_confidence_when_app_mirror_and_logs_match(tmp_path: Path) -> None:
    _sample_artifacts(tmp_path)
    event_path = tmp_path / "case-a" / "proxy-attribution-events.jsonl"
    event_path.write_text(
        json.dumps(
            {
                "event_type": "proxy.attribution",
                "run_id": "case-a",
                "stage": "compact_free",
                "route_model": "bad:free",
                "model": "openrouter/bad:free",
                "gateway": "litellm_proxy",
                "provider_call_ms": 61000,
                "stage_total_ms": 61005,
                "provider_call_ratio": 0.999,
                "local_processing_ms": 5,
                "status": "fail",
                "failure_class": "provider_timeout",
                "timeout_overrun": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_proxy_log_report(artifacts_root=tmp_path, log_text="case-a openrouter/bad:free timeout after retry\n")

    assert payload["app_mirror_event_count"] == 1
    assert payload["snippet_count"] >= 1
    assert payload["correlation_confidence"] == "app_plus_proxy_logs"
    assert "app_plus_proxy_logs_available" in payload["correlation_notes"]


def test_proxy_log_report_splits_upstream_provider_slow_from_app_mirror(tmp_path: Path) -> None:
    _sample_artifacts(tmp_path)
    event_path = tmp_path / "case-a" / "proxy-attribution-events.jsonl"
    event_path.write_text(
        json.dumps(
            {
                "event_type": "proxy.attribution",
                "run_id": "case-a",
                "stage": "pine_code_generation",
                "route_model": "paid_low.pine_code_generation",
                "model": "litellm_proxy/paid_low.pine_code_generation",
                "gateway": "litellm_proxy",
                "provider_call_ms": 21000,
                "stage_total_ms": 21100,
                "provider_call_ratio": 0.995,
                "local_processing_ms": 5,
                "upstream_provider_ms": 20500,
                "litellm_overhead_ms": 80,
                "callback_duration_ms": 0,
                "attempted_retries": 0,
                "attempted_fallbacks": 0,
                "status": "pass",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_proxy_log_report(artifacts_root=tmp_path, log_text="")

    assert payload["app_mirror_classification_summary"]["upstream_provider_slow"] == 1


def test_proxy_log_report_classifies_connection_error_and_enforced_timeout(tmp_path: Path) -> None:
    case_dir = tmp_path / "run-01" / "cases" / "case-a"
    case_dir.mkdir(parents=True)
    events = [
        {
            "event_type": "proxy.attribution",
            "run_id": "case-a",
            "stage": "repair",
            "route_model": "paid_low.repair_qwen",
            "model": "litellm_proxy/paid_low.repair_qwen",
            "gateway": "litellm_proxy",
            "status": "fail",
            "failure_class": "provider_error",
            "provider_error_subclass": "provider_connection_error",
            "stage_total_ms": 1200,
        },
        {
            "event_type": "proxy.attribution",
            "run_id": "case-a",
            "stage": "repair",
            "route_model": "paid_low.repair",
            "model": "litellm_proxy/paid_low.repair",
            "gateway": "litellm_proxy",
            "status": "fail",
            "failure_class": "provider_timeout",
            "timeout_enforced_by": "app_future_deadline",
            "stage_total_ms": 91000,
            "timeout_overrun": True,
        },
    ]
    with (case_dir / "proxy-attribution-events.jsonl").open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")

    payload = build_proxy_log_report(artifacts_root=tmp_path, log_text="")

    assert payload["app_mirror_classification_summary"]["provider_connection_error_fast"] == 1
    assert payload["app_mirror_classification_summary"]["provider_timeout_enforced"] == 1


def test_cli_latency_matrix_writes_report(monkeypatch, tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text("name: smoke\ncases:\n  - id: case-a\n    prompt: test\n", encoding="utf-8")
    out_path = tmp_path / "matrix.json"
    captured = {}

    def fake_build_latency_matrix(**kwargs):
        captured.update(kwargs)
        payload = {
            "status": "pass",
            "runs_completed": kwargs["runs"],
            "runs_requested": kwargs["runs"],
            "latency_summary": {"sample_count": 2},
            "route_policy_candidates": [],
        }
        write_json(kwargs["out"], payload)
        return payload

    monkeypatch.setattr("strategy_codebot.cli.build_latency_matrix", fake_build_latency_matrix)

    result = CliRunner().invoke(app, ["harness", "latency-matrix", "--suite", str(suite_path), "--runs", "2", "--prompt-profile", "optimized_v1", "--out", str(out_path)])

    assert result.exit_code == 0, result.output
    assert "runs=2/2" in result.output
    assert load_json(out_path)["status"] == "pass"
    assert captured["prompt_profile"] == "optimized_v1"


def test_cli_prompt_matrix_writes_report(monkeypatch, tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text("name: smoke\ncases:\n  - id: case-a\n    prompt: test\n", encoding="utf-8")
    out_path = tmp_path / "prompt-matrix.json"
    captured = {}

    def fake_build_prompt_matrix(**kwargs):
        captured.update(kwargs)
        payload = {
            "status": "pass",
            "profile_count": len(kwargs["profiles"]),
            "runs": kwargs["runs"],
            "comparison": [],
        }
        write_json(kwargs["out"], payload)
        return payload

    monkeypatch.setattr("strategy_codebot.cli.build_prompt_matrix", fake_build_prompt_matrix)

    result = CliRunner().invoke(
        app,
        [
            "harness",
            "prompt-matrix",
            "--suite",
            str(suite_path),
            "--profiles",
            "current,current,optimized_v1",
            "--runs",
            "2",
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "profiles=2" in result.output
    assert load_json(out_path)["status"] == "pass"
    assert captured["profiles"] == ["current", "optimized_v1"]
    assert captured["runs"] == 2


def test_cli_proxy_log_report_accepts_logs_file(tmp_path: Path) -> None:
    _sample_artifacts(tmp_path / "artifacts")
    logs_path = tmp_path / "proxy.log"
    logs_path.write_text("case-a openrouter/bad:free api_key=sk-secret123456789\n", encoding="utf-8")
    out_path = tmp_path / "proxy-report.json"

    result = CliRunner().invoke(
        app,
        [
            "harness",
            "proxy-log-report",
            "--artifacts-root",
            str(tmp_path / "artifacts"),
            "--logs-file",
            str(logs_path),
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert load_json(out_path)["status"] == "pass"


def test_replay_recommendations_links_eval_report(monkeypatch, tmp_path: Path) -> None:
    proposals_path = tmp_path / "proposals.json"
    suite_path = tmp_path / "suite.yaml"
    out_path = tmp_path / "replay.json"
    suite_path.write_text("name: smoke\ncases: []\n", encoding="utf-8")
    write_json(
        proposals_path,
        {"proposals": [{"id": "proposal-a", "suggested_change_type": "route_policy"}]},
    )

    def fake_run_live_eval(**kwargs):
        write_json(kwargs["out_dir"] / "eval-report.json", {"status": "pass", "cases": []})
        return {"status": "pass", "cases": []}

    monkeypatch.setattr("strategy_codebot.harness_intelligence.run_live_eval", fake_run_live_eval)

    payload = replay_recommendations(proposals_path, suite=suite_path, out=out_path)

    assert payload["status"] == "pass"
    assert payload["proposal_results"][0]["ready"] is True
    assert Path(payload["proposal_results"][0]["eval_report_ref"]).exists()


def test_propose_improvements_creates_ready_route_candidate_for_full_evidence(tmp_path: Path) -> None:
    proposals_path = tmp_path / "proposals.json"
    write_json(
        proposals_path,
        {
            "proposals": [
                {
                    "id": "proposal-case-timeout-provider-timeout",
                    "suggested_change_type": "route_policy",
                    "failure_signature": {"failure_class": "provider_timeout", "stage": "case_timeout"},
                    "failure_class": "provider_timeout",
                    "stage": "case_timeout",
                    "lesson": "Observed provider_timeout at case_timeout suggests route review.",
                    "evidence_confidence": "full",
                }
            ]
        },
    )

    payload = propose_improvements(proposals_path, out=tmp_path / "improvements.json")

    assert payload["status"] == "pass"
    assert payload["candidate_count"] == 1
    candidate = payload["candidates"][0]
    assert candidate["candidate_type"] == "route_policy_patch"
    assert candidate["status"] == "needs_review"
    assert candidate["ready_for_review"] is True
    assert candidate["suggested_patch"]["action"] == "review_timeout_budget_or_provider_route"
    assert payload["anti_pollution"]["mutates_registry"] is False


def test_propose_improvements_keeps_partial_evidence_not_ready(tmp_path: Path) -> None:
    proposals_path = tmp_path / "proposals.json"
    write_json(
        proposals_path,
        {
            "proposals": [
                {
                    "id": "proposal-compact-free-provider-rate-limited",
                    "suggested_change_type": "route_policy",
                    "failure_signature": {"failure_class": "provider_rate_limited", "stage": "compact_free"},
                    "failure_class": "provider_rate_limited",
                    "stage": "compact_free",
                    "lesson": "Observed provider_rate_limited at compact_free suggests route review.",
                    "evidence_confidence": "partial",
                }
            ]
        },
    )

    payload = propose_improvements(proposals_path)

    assert payload["candidates"][0]["ready_for_review"] is False
    assert payload["candidates"][0]["replay_required"] is True


def test_propose_improvements_accepts_replay_report_as_input(tmp_path: Path) -> None:
    proposals_path = tmp_path / "proposals.json"
    replay_path = tmp_path / "replay.json"
    write_json(
        proposals_path,
        {
            "proposals": [
                {
                    "id": "proposal-a",
                    "suggested_change_type": "route_policy",
                    "failure_class": "provider_rate_limited",
                    "stage": "compact_free",
                    "lesson": "Observed provider_rate_limited at compact_free suggests route review.",
                    "evidence_confidence": "full",
                }
            ]
        },
    )
    write_json(
        replay_path,
        {
            "status": "pass",
            "proposals_ref": str(proposals_path),
            "proposal_results": [{"proposal_id": "proposal-a", "replay_status": "pass", "ready": True}],
        },
    )

    payload = propose_improvements(replay_path)

    assert payload["candidate_count"] == 1
    assert payload["candidates"][0]["ready_for_review"] is True
    assert payload["source_replay"] == str(replay_path)


def test_apply_approved_improvement_writes_patch_artifact_only(tmp_path: Path) -> None:
    candidates_path = tmp_path / "improvements.json"
    out_path = tmp_path / "patch.json"
    write_json(
        candidates_path,
        {
            "candidates": [
                {
                    "candidate_id": "improvement-a",
                    "candidate_type": "route_policy_patch",
                    "status": "approved",
                    "suggested_patch": {"patch_type": "model_route_policy", "action": "demote_or_cooldown"},
                }
            ]
        },
    )

    payload = apply_approved_improvement("improvement-a", candidates_path=candidates_path, out=out_path)

    assert payload["status"] == "pass"
    assert payload["patch"]["action"] == "demote_or_cooldown"
    assert payload["anti_pollution"]["repo_files_mutated"] is False
    assert out_path.exists()


def test_apply_approved_improvement_rejects_unapproved_candidate(tmp_path: Path) -> None:
    candidates_path = tmp_path / "improvements.json"
    write_json(
        candidates_path,
        {
            "candidates": [
                {
                    "candidate_id": "improvement-a",
                    "candidate_type": "route_policy_patch",
                    "status": "needs_review",
                    "suggested_patch": {"patch_type": "model_route_policy", "action": "demote_or_cooldown"},
                }
            ]
        },
    )

    payload = apply_approved_improvement("improvement-a", candidates_path=candidates_path)

    assert payload["status"] == "fail"
    assert payload["reason"] == "candidate_not_approved"


def test_harness_intelligence_cli_commands(tmp_path: Path, monkeypatch) -> None:
    _sample_artifacts(tmp_path / "artifacts")
    runner = CliRunner()
    report_path = tmp_path / "report.json"
    proposals_path = tmp_path / "proposals.json"
    replay_path = tmp_path / "replay.json"
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text("name: smoke\ncases: []\n", encoding="utf-8")

    result = runner.invoke(app, ["harness", "intelligence-report", "--artifacts-root", str(tmp_path / "artifacts"), "--out", str(report_path)])
    assert result.exit_code == 0, result.output
    assert load_json(report_path)["route_recommendations"]

    result = runner.invoke(app, ["harness", "propose-lessons", "--report", str(report_path), "--out", str(proposals_path), "--candidates-path", str(tmp_path / "candidates.json")])
    assert result.exit_code == 0, result.output

    def fake_run_live_eval(**kwargs):
        write_json(kwargs["out_dir"] / "eval-report.json", {"status": "pass", "cases": []})
        return {"status": "pass", "cases": []}

    monkeypatch.setattr("strategy_codebot.harness_intelligence.run_live_eval", fake_run_live_eval)
    result = runner.invoke(app, ["harness", "replay-recommendations", "--proposals", str(proposals_path), "--suite", str(suite_path), "--out", str(replay_path)])
    assert result.exit_code == 0, result.output
    assert load_json(replay_path)["status"] == "pass"

    improvements_path = tmp_path / "improvements.json"
    result = runner.invoke(app, ["harness", "propose-improvements", "--proposals", str(proposals_path), "--replay", str(replay_path), "--out", str(improvements_path)])
    assert result.exit_code == 0, result.output
    improvement_payload = load_json(improvements_path)
    assert improvement_payload["candidate_count"] >= 1

    candidate_id = improvement_payload["candidates"][0]["candidate_id"]
    improvement_payload["candidates"][0]["status"] = "approved"
    write_json(improvements_path, improvement_payload)
    patch_path = tmp_path / "patch.json"
    result = runner.invoke(app, ["harness", "apply-approved-improvement", candidate_id, "--candidates", str(improvements_path), "--out", str(patch_path)])
    assert result.exit_code == 0, result.output
    assert load_json(patch_path)["status"] == "pass"
