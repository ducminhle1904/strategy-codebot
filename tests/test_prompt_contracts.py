from __future__ import annotations

from strategy_codebot.prompt_contracts import STAGE_PINE_CODE_GENERATION
from strategy_codebot.prompt_contracts import STAGE_STRATEGY_CODING
from strategy_codebot.prompt_contracts import build_stage_json_contract
from strategy_codebot.prompt_contracts import stage_messages


def test_strategy_coding_contract_requires_strategy_spec_skeleton() -> None:
    contract = build_stage_json_contract(STAGE_STRATEGY_CODING)

    assert contract["required_top_level_keys"] == [
        "stage",
        "output",
        "assumptions",
        "handoff_notes",
        "policy_observations",
    ]
    assert contract["required_output_keys"] == ["strategy_spec"]
    strategy_spec = contract["output_schema"]["strategy_spec"]
    assert strategy_spec["required_keys"] == [
        "target_platform",
        "script_type",
        "market",
        "timeframe",
        "entry_rules",
        "exit_rules",
        "risk_rules",
    ]
    assert "position_sizing" in strategy_spec["optional_keys"]
    assert strategy_spec["field_contract"]["entry_rules"] == "non-empty array of strings"


def test_pine_generation_contract_requires_plain_pine_v6_string() -> None:
    contract = build_stage_json_contract(STAGE_PINE_CODE_GENERATION)

    assert contract["required_output_keys"] == ["pine_code"]
    pine_contract = contract["output_schema"]["pine_code"]
    assert "plain Pine Script string" in pine_contract
    assert "not markdown" in pine_contract
    assert "no code fence" in pine_contract
    assert "//@version=6" in pine_contract


def test_stage_messages_embed_strict_json_contract() -> None:
    messages = stage_messages(
        STAGE_PINE_CODE_GENERATION,
        {"user_prompt": "generate Pine v6"},
        conservative_sizing_guidance="Use bounded risk.",
        repair_iteration=None,
    )

    assert messages[0]["role"] == "system"
    assert "Return exactly one strict JSON object" in messages[0]["content"]
    assert "no markdown, prose, code fences, or extra text" in messages[0]["content"]
    assert "response_contract" in messages[1]["content"]
    assert "pine_code" in messages[1]["content"]
