from pathlib import Path

from strategy_codebot.policy_engine import EVIDENCE_GENERATED_ARTIFACT
from strategy_codebot.policy_engine import EVIDENCE_MANUAL_RUNTIME_PROOF
from strategy_codebot.policy_engine import PolicySubject as EnginePolicySubject
from strategy_codebot.policy_engine import evaluate_policy as evaluate_engine_policy
from strategy_codebot.policy_engine import load_policy_rules
from strategy_codebot.policy_engine import validate_policy_rules
from strategy_codebot.server.policy import PolicySubject as ServerPolicySubject
from strategy_codebot.server.policy import evaluate_policy as evaluate_server_policy
from strategy_codebot.tool_runtime import find_blocked_claims
from strategy_codebot.tool_runtime import find_policy_claims


def test_policy_engine_allows_negated_boundaries_and_blocks_hard_requests() -> None:
    allowed = evaluate_engine_policy(
        EnginePolicySubject(
            surface="tool.generate_pine",
            payload={"constraints": ["No broker execution, no live trading, and no profitability claims."]},
            evidence_level=EVIDENCE_GENERATED_ARTIFACT,
        )
    )
    blocked = evaluate_engine_policy(
        EnginePolicySubject(
            surface="tool.generate_pine",
            payload={"user_notes": "Connect broker execution and place live orders automatically."},
            evidence_level=EVIDENCE_GENERATED_ARTIFACT,
        )
    )

    assert allowed.allowed
    assert not blocked.allowed
    assert {finding.rule_id for finding in blocked.findings} >= {"broker_execution", "live_order_execution"}


def test_policy_engine_allow_pattern_does_not_hide_later_contrast_claim() -> None:
    decision = evaluate_engine_policy(
        EnginePolicySubject(
            surface="tool.generate_pine",
            payload="No live trading, but connect broker execution for me.",
            evidence_level=EVIDENCE_GENERATED_ARTIFACT,
        )
    )

    assert not decision.allowed
    assert decision.blocked_finding is not None
    assert decision.blocked_finding.rule_id == "broker_execution"


def test_server_policy_and_tool_runtime_share_blocking_decision() -> None:
    text = "This strategy has guaranteed profit in live trading."
    server_decision = evaluate_server_policy(
        ServerPolicySubject(surface="agent.chat.output", payload=text, evidence_level=EVIDENCE_GENERATED_ARTIFACT)
    )
    tool_findings = find_blocked_claims(text)

    assert not server_decision.allowed
    assert server_decision.blocked_finding is not None
    assert server_decision.blocked_finding.rule_id == tool_findings[0]["rule_id"]


def test_runtime_success_rule_allows_manual_runtime_proof() -> None:
    blocked = evaluate_engine_policy(
        EnginePolicySubject(
            surface="artifact.validation_report",
            payload="Compile success and backtest success.",
            evidence_level=EVIDENCE_GENERATED_ARTIFACT,
        )
    )
    allowed = evaluate_engine_policy(
        EnginePolicySubject(
            surface="artifact.runtime_trace_summary",
            payload="Compile success and backtest success.",
            evidence_level=EVIDENCE_MANUAL_RUNTIME_PROOF,
        )
    )

    assert not blocked.allowed
    assert allowed.allowed


def test_runtime_success_rule_does_not_block_legacy_policy_text_surface() -> None:
    assert find_policy_claims("The validation report says compile success.") == []


def test_profitability_warning_allows_legacy_negation_prefixes() -> None:
    assert find_policy_claims("Use evidence rather than claiming profitability.") == []
    assert find_policy_claims("Strategy cannot claim profitability.") == []


def test_policy_rule_validation_fails_invalid_config(tmp_path: Path) -> None:
    config = tmp_path / "policy-rules.yaml"
    config.write_text(
        """
rules:
  - id: bad
    category: test
    severity: blocker
    code: policy_violation
    message: bad
    block_patterns:
      - "["
  - id: bad
    category: test
    severity: blocker
    code: policy_violation
    message: duplicate
    block_patterns:
      - "ok"
  - id: wrong_severity
    category: test
    severity: blokcer
    code: policy_violation
    message: wrong severity
    block_patterns:
      - "ok"
  - id: wrong_evidence
    category: test
    severity: blocker
    code: policy_violation
    message: wrong evidence
    block_patterns:
      - "ok"
    evidence_levels:
      - made_up
""",
        encoding="utf-8",
    )

    report = validate_policy_rules(config)

    assert report["status"] == "fail"
    assert any("invalid regex" in error for error in report["errors"])
    assert any("duplicate rule id: bad" in error for error in report["errors"])
    assert any("invalid severity" in error for error in report["errors"])
    assert any("invalid evidence_level" in error for error in report["errors"])


def test_policy_rule_loader_rejects_invalid_config(tmp_path: Path) -> None:
    config = tmp_path / "policy-rules.yaml"
    config.write_text(
        """
rules:
  - id: bad
    category: test
    severity: blocker
    code: policy_violation
    message: bad
    block_patterns:
      - "["
""",
        encoding="utf-8",
    )

    try:
        load_policy_rules(config)
    except ValueError as exc:
        assert "Invalid policy rules" in str(exc)
    else:
        raise AssertionError("load_policy_rules should reject invalid regex config")
