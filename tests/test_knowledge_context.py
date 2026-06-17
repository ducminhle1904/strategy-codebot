from __future__ import annotations

from pathlib import Path

from strategy_codebot.knowledge_context import build_knowledge_context, compact_knowledge_context
from strategy_codebot.live import _messages


def test_knowledge_context_selects_pine_risk_and_external_refs() -> None:
    context = build_knowledge_context("Create a Pine v6 strategy with stop loss and take profit")

    doc_ids = [doc["id"] for doc in context["internal_docs"]]
    source_ids = [source["id"] for source in context["external_refs"]]

    assert "pine_v6_rules" in doc_ids
    assert "risk_policy" in doc_ids
    assert "tradingview-pine-strategies" in source_ids
    assert all("url" in source and "excerpt" not in source for source in context["external_refs"])
    assert context["stage_relevance"]["pine_code_generation"] == ["pine_v6_rules"]


def test_knowledge_context_selects_mql5_for_both_platform_prompt() -> None:
    context = build_knowledge_context("Create both-platform Pine and MQL5 strategy artifacts")

    doc_ids = [doc["id"] for doc in context["internal_docs"]]
    source_ids = [source["id"] for source in context["external_refs"]]

    assert "pine_v6_rules" in doc_ids
    assert "mql5_rules" in doc_ids
    assert "mql5-reference" in source_ids


def test_knowledge_context_is_deterministically_truncated(tmp_path: Path) -> None:
    context = build_knowledge_context("Create a Pine v6 indicator")
    compact = compact_knowledge_context(context)

    assert compact["context_refs"] == context["context_refs"]
    assert all(len(doc["excerpt"]) <= 1800 for doc in context["internal_docs"])


def test_single_workflow_prompt_shape_preserved_when_knowledge_off() -> None:
    messages = _messages("Create a Pine strategy", {})

    assert messages[1]["content"] == "Create a Pine strategy"
