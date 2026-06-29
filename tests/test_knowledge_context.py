from __future__ import annotations

from pathlib import Path

from strategy_codebot.knowledge_context import build_knowledge_context, compact_knowledge_context
from strategy_codebot.knowledge_context import knowledge_metadata
from strategy_codebot.knowledge_base import build_knowledge_index
from strategy_codebot.live import _messages


def _retrieved_source_ids(context: dict) -> list[str]:
    ids: list[str] = []
    for chunk in context.get("retrieved_chunks", []):
        source_id = chunk.get("source_id")
        if source_id and source_id not in ids:
            ids.append(source_id)
    return ids


def test_knowledge_context_selects_pine_risk_and_external_refs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_KNOWLEDGE_INDEX", str(tmp_path / "missing.json"))
    context = build_knowledge_context("Create a Pine v6 strategy with stop loss and take profit")

    doc_ids = [doc["id"] for doc in context["internal_docs"]]
    source_ids = [source["id"] for source in context["external_refs"]]

    assert "pine_v6_rules" in doc_ids
    assert "risk_policy" in doc_ids
    assert "tradingview-pine-strategies" in source_ids
    assert all("url" in source and "excerpt" not in source for source in context["external_refs"])
    assert context["stage_relevance"]["pine_code_generation"] == ["pine_v6_rules"]


def test_knowledge_context_auto_degrades_to_static_context_when_database_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("strategy_codebot.knowledge_context.ensure_database_url", lambda: "postgresql://localhost/unavailable")

    def unavailable(*args, **kwargs):
        raise ConnectionError("connection refused")

    monkeypatch.setattr("strategy_codebot.knowledge_context.build_retrieved_knowledge_context", unavailable)

    context = build_knowledge_context("Create a Pine v6 strategy with stop loss")
    metadata = knowledge_metadata(context)

    assert context["knowledge_context_status"] == "degraded"
    assert context["knowledge_health_status"] == "degraded"
    assert context["failure_class"] == "knowledge_unavailable"
    assert context["fallback"] == "static_curated_context"
    assert "pine_v6_rules" in [doc["id"] for doc in context["internal_docs"]]
    assert metadata["knowledge_context_status"] == "degraded"
    assert metadata["knowledge_failure_class"] == "knowledge_unavailable"


def test_knowledge_context_selects_mql5_for_both_platform_prompt(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_KNOWLEDGE_INDEX", str(tmp_path / "missing.json"))
    context = build_knowledge_context("Create both-platform Pine and MQL5 strategy artifacts")

    doc_ids = [doc["id"] for doc in context["internal_docs"]]
    source_ids = [source["id"] for source in context["external_refs"]]

    assert "pine_v6_rules" in doc_ids
    assert "mql5_rules" in doc_ids
    assert "mql5-reference" in source_ids


def test_knowledge_context_selects_crypto_playbook_and_trusted_refs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_KNOWLEDGE_INDEX", str(tmp_path / "missing.json"))
    context = build_knowledge_context("Create a crypto BTC perpetual breakout strategy with funding risk")

    doc_ids = [doc["id"] for doc in context["internal_docs"]]
    source_ids = [source["id"] for source in context["external_refs"]]

    assert "crypto_playbook" in doc_ids
    assert "strategy_patterns" in doc_ids
    assert "binance-academy-risk-management-strategies" in source_ids
    assert "babypips-school-of-pipsology" not in source_ids
    assert context["selection_reasons"]["crypto_context"] is True
    assert all("excerpt" not in source for source in context["external_refs"])


def test_knowledge_context_selects_forex_playbook_and_trusted_refs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_KNOWLEDGE_INDEX", str(tmp_path / "missing.json"))
    context = build_knowledge_context("Create a forex EURUSD London session mean reversion strategy with spread controls")

    doc_ids = [doc["id"] for doc in context["internal_docs"]]
    source_ids = [source["id"] for source in context["external_refs"]]

    assert "forex_playbook" in doc_ids
    assert "strategy_patterns" in doc_ids
    assert "babypips-school-of-pipsology" in source_ids
    assert "binance-academy-risk-management-strategies" not in source_ids
    assert context["selection_reasons"]["forex_context"] is True
    assert all("excerpt" not in source for source in context["external_refs"])


def test_generation_context_does_not_fetch_trusted_public_refs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_KNOWLEDGE_INDEX", str(tmp_path / "missing.json"))

    def fail_fetch(url: str) -> str:
        raise AssertionError(f"unexpected fetch during generation context: {url}")

    monkeypatch.setattr("strategy_codebot.knowledge_base._fetch_url_text", fail_fetch)

    context = build_knowledge_context("Create a crypto BTC strategy with Binance risk context")

    assert "binance-academy-risk-management-strategies" in [source["id"] for source in context["external_refs"]]
    assert all("excerpt" not in source for source in context["external_refs"])


def test_knowledge_context_is_deterministically_truncated(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_KNOWLEDGE_INDEX", str(tmp_path / "missing.json"))
    context = build_knowledge_context("Create a Pine v6 indicator")
    compact = compact_knowledge_context(context)

    assert compact["context_refs"] == context["context_refs"]
    assert all(len(doc["excerpt"]) <= 1800 for doc in context["internal_docs"])


def test_knowledge_context_uses_kb_index_when_available(monkeypatch, tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)
    monkeypatch.setenv("STRATEGY_CODEBOT_KNOWLEDGE_INDEX", str(index_path))

    context = build_knowledge_context("Create a price action strategy with break of structure and liquidity sweep")
    compact = compact_knowledge_context(context)

    assert context["store"] == "knowledge_base"
    assert context["retrieved_chunks"]
    assert any("break of structure" in chunk["text"].lower() for chunk in context["retrieved_chunks"])
    assert compact["retrieved_chunks"][0]["chunk_id"]
    assert context["citations"]
    assert compact["citations"][0]["chunk_id"]
    assert context["retrieval_confidence"]["score"] >= 0
    assert "low_confidence" in compact


def test_knowledge_context_retrieves_bots_workflow_for_prepare_prompt(monkeypatch, tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)
    monkeypatch.setenv("STRATEGY_CODEBOT_KNOWLEDGE_INDEX", str(index_path))

    context = build_knowledge_context("tạo bot cho strategy này sau khi có backtest và risk review")
    compact = compact_knowledge_context(context)

    assert any(chunk["source_id"] == "internal-bots-chat-workflow" for chunk in context["retrieved_chunks"])
    assert any("proposal" in chunk["text"].lower() for chunk in context["retrieved_chunks"])
    assert compact["retrieved_chunks"][0]["chunk_id"]
    assert context["citations"]


def test_knowledge_context_retrieves_bots_status_guidance(monkeypatch, tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)
    monkeypatch.setenv("STRATEGY_CODEBOT_KNOWLEDGE_INDEX", str(index_path))

    context = build_knowledge_context("explain bot status risk block heartbeat kill switch last error")

    assert any(chunk["source_id"] == "internal-bots-chat-workflow" for chunk in context["retrieved_chunks"])
    assert any("heartbeat" in chunk["text"].lower() for chunk in context["retrieved_chunks"])
    assert any("risk block" in chunk["text"].lower() for chunk in context["retrieved_chunks"])


def test_knowledge_context_retrieves_bots_safety_boundary_for_live_prompt(monkeypatch, tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)
    monkeypatch.setenv("STRATEGY_CODEBOT_KNOWLEDGE_INDEX", str(index_path))

    context = build_knowledge_context("chạy live bot vào lệnh thật qua broker execution")

    assert any(chunk["source_id"] == "internal-bots-chat-workflow" for chunk in context["retrieved_chunks"])
    assert any("no broker execution" in chunk["text"].lower() for chunk in context["retrieved_chunks"])
    assert any("live trading" in chunk["text"].lower() for chunk in context["retrieved_chunks"])


def test_knowledge_context_retrieves_backtest_preview_workflow(monkeypatch, tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)
    monkeypatch.setenv("STRATEGY_CODEBOT_KNOWLEDGE_INDEX", str(index_path))

    context = build_knowledge_context("run backtest preview and summarize trades equity curve")
    source_ids = _retrieved_source_ids(context)

    assert source_ids[0] == "internal-backtest-preview-workflow"
    assert "internal-bots-chat-workflow" not in source_ids[:2]
    assert any("local sandbox evidence" in chunk["text"].lower() for chunk in context["retrieved_chunks"])


def test_knowledge_context_retrieves_variant_and_robustness_workflow(monkeypatch, tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)
    monkeypatch.setenv("STRATEGY_CODEBOT_KNOWLEDGE_INDEX", str(index_path))

    context = build_knowledge_context("queue variant lab and build robustness report with sample size and slippage")
    source_ids = _retrieved_source_ids(context)

    assert "internal-variant-robustness-workflow" in source_ids[:2]
    assert any("run_backtest_variant_lab" in chunk["text"] for chunk in context["retrieved_chunks"])
    assert any("build_robustness_report" in chunk["text"] for chunk in context["retrieved_chunks"])


def test_knowledge_context_retrieves_risk_gate_and_order_intent_workflow(monkeypatch, tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)
    monkeypatch.setenv("STRATEGY_CODEBOT_KNOWLEDGE_INDEX", str(index_path))

    context = build_knowledge_context("create proposed order intent and run risk gate sizing leverage stale signal")
    source_ids = _retrieved_source_ids(context)

    assert source_ids[0] == "internal-risk-gate-order-intent-workflow"
    assert any("deterministic risk gates" in chunk["text"].lower() for chunk in context["retrieved_chunks"])
    assert any("not broker orders" in chunk["text"].lower() for chunk in context["retrieved_chunks"])


def test_knowledge_context_retrieves_artifact_exposure_policy(monkeypatch, tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)
    monkeypatch.setenv("STRATEGY_CODEBOT_KNOWLEDGE_INDEX", str(index_path))

    context = build_knowledge_context("which artifacts are internal raw JSON validation trades compile report")
    source_ids = _retrieved_source_ids(context)

    assert "internal-model-workflow-boundaries" in source_ids[:2]
    assert any("user-facing artifacts" in chunk["text"].lower() for chunk in context["retrieved_chunks"])
    assert any("internal artifacts" in chunk["text"].lower() for chunk in context["retrieved_chunks"])


def test_knowledge_context_retrieves_model_workflow_boundaries(monkeypatch, tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)
    monkeypatch.setenv("STRATEGY_CODEBOT_KNOWLEDGE_INDEX", str(index_path))

    prompts = [
        "repair validation blockers before preview",
        "create knowledge proposal with affected sources and recommendations",
        "market research current BTC context with sources and citations",
    ]

    for prompt in prompts:
        context = build_knowledge_context(prompt)
        assert "internal-model-workflow-boundaries" in _retrieved_source_ids(context)


def test_knowledge_context_uses_database_url_when_index_file_is_absent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_KNOWLEDGE_DATABASE_URL", "postgresql://kb_user:secret@localhost/strategy")
    monkeypatch.setenv("STRATEGY_CODEBOT_KNOWLEDGE_INDEX", str(tmp_path / "missing.json"))

    def fake_search_db(query, database_url, *, stage, options, started):
        return {
            "status": "pass",
            "index_ref": "postgres:postgresql://kb_user:***@localhost/strategy",
            "index_id": "kb-postgres",
            "knowledge_version": 1,
            "retrieval_query": query,
            "intent": {"tags": ["price_action"], "target": "pine"},
            "expanded_terms": ["BOS"],
            "embedding_model": "text-embedding-3-small",
            "retrieval_latency_ms": 3,
            "hybrid_candidate_count": 1,
            "rerank_latency_ms": 0,
            "cache_hit": False,
            "retrieved_chunks": [
                {
                    "chunk_id": "pattern-price-action-bos-retest#1",
                    "item_id": "pattern-price-action-bos-retest",
                    "source_id": "pattern-price-action-bos-retest",
                    "type": "strategy_pattern",
                    "title": "BOS retest",
                    "text": "Break of structure retest context.",
                    "stages": ["strategy_reasoning"],
                }
            ],
            "source_ids": ["pattern-price-action-bos-retest"],
            "metrics": {"chunk_hit_rate": 1.0, "source_coverage": 1},
        }

    monkeypatch.setattr("strategy_codebot.knowledge_base._search_db_knowledge", fake_search_db)

    context = build_knowledge_context("Use break of structure and retest")

    assert context["store"] == "knowledge_base"
    assert context["index_ref"].startswith("postgres:")
    assert context["retrieved_chunks"][0]["chunk_id"] == "pattern-price-action-bos-retest#1"


def test_single_workflow_prompt_shape_preserved_when_knowledge_off() -> None:
    messages = _messages("Create a Pine strategy", {})

    assert messages[1]["content"] == "Create a Pine strategy"
