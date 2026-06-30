from strategy_codebot.server.action_registry import action_registry_payload
from strategy_codebot.server.action_registry import ActionRegistryRequestCache
from strategy_codebot.server.action_registry import build_action_evidence_packet
from strategy_codebot.server.action_registry import evaluate_action_registry
from strategy_codebot.server.action_registry import evaluate_action_availability
from strategy_codebot.server.action_registry import registry_entry_for_tool
from strategy_codebot.server.artifact_kinds import ROBUSTNESS_REPORT_ARTIFACT_KIND


def test_action_availability_uses_registry_evidence_packet() -> None:
    entry = registry_entry_for_tool("draft_bot")
    assert entry is not None

    evidence = build_action_evidence_packet(
        artifact_kinds={"strategy_spec"},
        context_text="Please prepare a paper bot proposal.",
        web_search="auto",
        context_signals={"bot_boundary_request"},
    )
    result = evaluate_action_availability(entry, evidence=evidence)

    assert result.available is True


def test_action_text_keywords_do_not_unlock_bot_actions() -> None:
    entry = registry_entry_for_tool("draft_bot")
    assert entry is not None

    evidence = build_action_evidence_packet(
        artifact_kinds={"strategy_spec"},
        context_text="Please prepare a paper bot proposal.",
        web_search="auto",
    )
    result = evaluate_action_availability(entry, evidence=evidence)

    assert result.available is False
    assert result.disabled_reason_code == "strategy_artifact_and_bot_request_required"
    assert "bot_boundary_request" in evidence.lexical_hints
    assert "bot_boundary_request" not in evidence.context_signals


def test_risk_gate_missing_inputs_stay_blocked() -> None:
    entry = registry_entry_for_tool("run_risk_gate")
    assert entry is not None

    evidence = build_action_evidence_packet(
        artifact_kinds={"strategy_spec"},
        context_text="Create risk gate for this bot.",
        web_search="auto",
        context_signals={"bot_boundary_request"},
    )
    result = evaluate_action_availability(entry, evidence=evidence)

    assert result.available is False
    assert result.disabled_reason_code == "missing_required_inputs"
    assert result.risk_level == "blocked"
    assert result.required_inputs == ("stop_or_invalidation", "sizing", "stale_after")


def test_existing_robustness_report_blocks_duplicate_report_action() -> None:
    entry = registry_entry_for_tool("build_robustness_report")
    assert entry is not None

    evidence = build_action_evidence_packet(
        artifact_kinds={"backtest_report", ROBUSTNESS_REPORT_ARTIFACT_KIND},
        context_text="Review the completed backtest report.",
        web_search="auto",
    )
    result = evaluate_action_availability(entry, evidence=evidence)

    assert result.available is False
    assert result.disabled_reason_code == "robustness_report_exists"


def test_action_registry_payload_exposes_requirements_and_reason_codes() -> None:
    payload = action_registry_payload(
        artifact_kinds={"strategy_spec"},
        context_text="Create risk gate for this bot.",
        web_search="auto",
        context_signals={"bot_boundary_request"},
    )
    risk_gate = next(item for item in payload if item["tool_id"] == "run_risk_gate")

    assert risk_gate["available"] is False
    assert risk_gate["disabled_reason_code"] == "missing_required_inputs"
    assert risk_gate["required_inputs"] == ["stop_or_invalidation", "sizing", "stale_after"]
    assert risk_gate["requirements"]["requires_any_context_signal"] == [
        "bot_boundary_request",
        "has_proposed_intent",
        "has_strategy_artifact",
    ]


def test_risk_gate_structured_inputs_enable_action() -> None:
    entry = registry_entry_for_tool("run_risk_gate")
    assert entry is not None

    evidence = build_action_evidence_packet(
        artifact_kinds={"strategy_spec"},
        context_text="Create risk gate for this bot without parsing text fields.",
        web_search="auto",
        context_signals={"bot_boundary_request"},
        risk_gate_inputs={"stop_or_invalidation", "sizing", "stale_after"},
    )
    result = evaluate_action_availability(entry, evidence=evidence)

    assert result.available is True


def test_action_registry_request_cache_reuses_same_key() -> None:
    cache = ActionRegistryRequestCache()

    first = cache.get(
        artifact_kinds={"strategy_spec"},
        context_text="review this strategy",
        web_search="auto",
    )
    second = cache.get(
        artifact_kinds={"strategy_spec"},
        context_text="review this strategy",
        web_search="auto",
    )

    assert second is first
    assert first.available_tool_ids == {
        str(item["tool_id"])
        for item in first.payload
        if item.get("available") is True and isinstance(item.get("tool_id"), str)
    }


def test_action_registry_request_cache_separates_context_web_search_and_artifacts() -> None:
    cache = ActionRegistryRequestCache()
    base = cache.get(artifact_kinds={"strategy_spec"}, context_text="review this strategy", web_search="auto")

    different_context = cache.get(
        artifact_kinds={"strategy_spec"},
        context_text="review this strategy with web disabled",
        web_search="auto",
    )
    different_web_search = cache.get(
        artifact_kinds={"strategy_spec"},
        context_text="review this strategy",
        web_search="off",
    )
    different_artifacts = cache.get(
        artifact_kinds={"pine_file"},
        context_text="review this strategy",
        web_search="auto",
    )

    assert different_context is not base
    assert different_web_search is not base
    assert different_artifacts is not base


def test_evaluate_action_registry_matches_payload_availability() -> None:
    evaluation = evaluate_action_registry(
        artifact_kinds={"strategy_spec"},
        context_text="Please prepare a paper bot proposal.",
        web_search="auto",
        context_signals={"bot_boundary_request"},
    )

    assert evaluation.payload
    assert evaluation.available_tool_ids == {
        str(item["tool_id"])
        for item in evaluation.payload
        if item.get("available") is True and isinstance(item.get("tool_id"), str)
    }
