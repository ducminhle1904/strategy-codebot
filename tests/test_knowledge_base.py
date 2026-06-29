import os
from pathlib import Path

import pytest

from strategy_codebot.knowledge_base import (
    EMBEDDING_DIMENSION_TEXT_3_SMALL,
    EMBEDDING_MODEL_PRODUCTION_OPENROUTER,
    EMBEDDING_PROFILE_PRODUCTION_OPENROUTER,
    RetrievalOptions,
    approve_candidate,
    approve_source_summary,
    build_knowledge_index,
    build_retrieved_knowledge_context,
    classify_prompt,
    evaluate_knowledge_suite,
    knowledge_health,
    learn_from_run,
    load_candidates,
    postgres_schema_sql,
    propose_candidate,
    propose_failure_candidate,
    reject_candidate,
    review_candidate_for_auto_promotion,
    review_candidates_for_auto_promotion,
    resolve_embedding_config,
    search_knowledge,
    snapshot_trusted_source,
    summarize_source_snapshot,
    _learning_safety_rejection,
    _query_embedding_cache_key,
    _chunk_text,
    _prefilter_chunks,
)
from strategy_codebot.schemas import load_json, validate_payload, write_json


def test_knowledge_index_builds_required_types_and_schema(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    report = build_knowledge_index(index_path=index_path)
    index = load_json(index_path)

    validate_payload(index, "knowledge-index.schema.json")
    assert report["status"] == "pass"
    assert index["store"]["type"] == "postgres_pgvector"
    assert index["store"]["adapter"] == "local_json"
    assert {"semantic", "procedural", "strategy_pattern", "source_ref"} <= set(index["stats"]["type_counts"])
    assert all("embedding_model" in chunk for chunk in index["chunks"])
    assert any(item["id"] == "internal-crypto-playbook" and item["market_tags"] == ["crypto"] for item in index["items"])
    assert any(item["id"] == "internal-forex-playbook" and item["market_tags"] == ["forex"] for item in index["items"])
    assert any(
        item["id"] == "internal-trading-skill-integration"
        and {"backtest", "quality", "learning_loop"} <= set(item["domain_tags"])
        for item in index["items"]
    )
    assert any(
        item["id"] == "internal-bots-chat-workflow"
        and item["status"] == "active"
        and item["trust_level"] == "high"
        and {"bots", "chat_workflow", "simulation"} <= set(item["domain_tags"])
        for item in index["items"]
    )
    workflow_sources = {
        "internal-backtest-preview-workflow": {"backtest", "preview", "review_only"},
        "internal-variant-robustness-workflow": {"variant_lab", "robustness", "review_only"},
        "internal-risk-gate-order-intent-workflow": {"risk_gate", "order_intent", "review_only"},
        "internal-model-workflow-boundaries": {"artifact_policy", "knowledge_proposal", "market_research"},
    }
    for source_id, expected_tags in workflow_sources.items():
        assert any(
            item["id"] == source_id
            and item["status"] == "active"
            and item["trust_level"] == "high"
            and expected_tags <= set(item["domain_tags"])
            for item in index["items"]
        )
    assert any(source["id"] == "babypips-school-of-pipsology" and source["type"] == "external_ref" for source in index["sources"])
    assert any(source["id"] == "tradermonty-backtest-expert-skill" and source["type"] == "external_ref" for source in index["sources"])
    assert index["retrieval_index"]["source_map"]
    assert index["retrieval_index"]["tag_map"]["crypto"]
    assert all("section_title" in chunk and "token_frequencies" in chunk for chunk in index["chunks"])
    assert all(chunk["chunk_id"].startswith(chunk["item_id"]) for chunk in index["chunks"])


def test_postgres_schema_matches_kb_vector_store() -> None:
    schema = postgres_schema_sql()

    assert "CREATE EXTENSION IF NOT EXISTS vector" in schema
    assert "CREATE TABLE IF NOT EXISTS knowledge_index_state" in schema
    assert "embedding vector(64)" in schema
    assert "CREATE TABLE IF NOT EXISTS knowledge_query_embeddings" in schema
    assert "search_vector tsvector" in schema
    assert "knowledge_chunks_embedding_hnsw" in schema
    assert "knowledge_chunks_search_vector_idx" in schema


def test_query_embedding_cache_key_is_normalized_and_versioned() -> None:
    first = _query_embedding_cache_key(
        " BOS   retest ",
        stage="strategy_reasoning",
        embedding_provider="openrouter",
        embedding_model="openai/text-embedding-3-small",
        knowledge_version=1,
    )
    second = _query_embedding_cache_key(
        "bos retest",
        stage="strategy_reasoning",
        embedding_provider="openrouter",
        embedding_model="openai/text-embedding-3-small",
        knowledge_version=1,
    )
    changed_version = _query_embedding_cache_key(
        "bos retest",
        stage="strategy_reasoning",
        embedding_provider="openrouter",
        embedding_model="openai/text-embedding-3-small",
        knowledge_version=2,
    )

    assert first == second
    assert changed_version != first


def test_chunk_text_keeps_section_metadata_on_heading_boundaries() -> None:
    chunks = _chunk_text(
        "# First Regime\n"
        + ("trend behavior " * 20)
        + "\n\n# Second Regime\n"
        + ("range behavior " * 20),
        max_chars=160,
        overlap_chars=0,
    )

    assert chunks[0]["section_title"] == "First Regime"
    assert chunks[1]["section_title"] == "Second Regime"
    assert chunks[0]["section_path"].endswith("first-regime-first-regime")


def test_prefilter_limit_prioritizes_curated_market_chunks() -> None:
    chunks = [
        {
            "chunk_id": "external",
            "chunk_index": 0,
            "market_tags": ["crypto"],
            "platform_tags": ["general"],
            "source_type": "external_ref",
            "chunk_kind": "metadata_only",
        },
        {
            "chunk_id": "forex",
            "chunk_index": 1,
            "market_tags": ["forex"],
            "platform_tags": ["general"],
            "source_type": "internal_curated",
            "chunk_kind": "section",
        },
        {
            "chunk_id": "curated",
            "chunk_index": 2,
            "market_tags": ["crypto"],
            "platform_tags": ["general"],
            "source_type": "internal_curated",
            "chunk_kind": "section",
        },
    ]

    filtered, stats = _prefilter_chunks(chunks, {"tags": ["crypto"]}, options=RetrievalOptions(prefilter_limit=1))

    assert stats == {"prefilter_input_count": 3, "prefilter_output_count": 1}
    assert filtered[0]["chunk_id"] == "curated"


def test_knowledge_health_skips_when_db_is_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STRATEGY_CODEBOT_KNOWLEDGE_DATABASE_URL", raising=False)

    report = knowledge_health()

    assert report["status"] == "skipped"
    assert report["configured"] is False
    assert "cache_status" in report


def test_production_openrouter_embedding_profile_uses_text_embedding_3_small() -> None:
    config = resolve_embedding_config(embedding_profile=EMBEDDING_PROFILE_PRODUCTION_OPENROUTER)

    assert config["embedding_provider"] == "openrouter"
    assert config["embedding_model"] == EMBEDDING_MODEL_PRODUCTION_OPENROUTER
    assert config["embedding_dimension"] == EMBEDDING_DIMENSION_TEXT_3_SMALL
    assert "embedding vector(1536)" in postgres_schema_sql(embedding_dimension=config["embedding_dimension"])


def test_production_embedding_profile_records_remote_dimensions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_remote_embedding(text: str, embedding_model: str, embedding_provider: str) -> list[float]:
        calls.append((embedding_model, embedding_provider))
        return [0.0] * EMBEDDING_DIMENSION_TEXT_3_SMALL

    monkeypatch.setattr("strategy_codebot.knowledge_base._remote_embedding", fake_remote_embedding)
    index_path = tmp_path / "kb" / "index.json"

    build_knowledge_index(index_path=index_path, embedding_profile=EMBEDDING_PROFILE_PRODUCTION_OPENROUTER)
    index = load_json(index_path)

    assert calls
    assert index["embedding_provider"] == "openrouter"
    assert index["embedding_model"] == EMBEDDING_MODEL_PRODUCTION_OPENROUTER
    assert index["embedding_dimension"] == EMBEDDING_DIMENSION_TEXT_3_SMALL
    assert {chunk["embedding_dimension"] for chunk in index["chunks"]} == {EMBEDDING_DIMENSION_TEXT_3_SMALL}


def test_production_embedding_profile_requires_provider_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        build_knowledge_index(index_path=tmp_path / "index.json", embedding_profile=EMBEDDING_PROFILE_PRODUCTION_OPENROUTER)


def test_knowledge_index_can_route_to_postgres_adapter_without_secret_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_write_db_index(index: dict[str, object], database_url: str) -> None:
        captured["database_url"] = database_url
        captured["item_count"] = len(index["items"])  # type: ignore[index]

    monkeypatch.setattr("strategy_codebot.knowledge_base._write_db_index", fake_write_db_index)
    index_path = tmp_path / "index.json"

    report = build_knowledge_index(index_path=index_path, database_url="postgresql://kb_user:secret-pass@localhost:5432/strategy")

    assert report["status"] == "pass"
    assert report["store"]["adapter"] == "postgres_pgvector"
    assert captured["database_url"] == "postgresql://kb_user:secret-pass@localhost:5432/strategy"
    assert captured["item_count"]
    assert not index_path.exists()
    assert "secret-pass" not in report["index_ref"]
    assert "kb_user:***" in report["index_ref"]


def test_hybrid_search_finds_exact_and_semantic_trading_terms(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)

    exact = search_knowledge("Repair Pine code with lookahead_on repaint risk", index_path=index_path)
    semantic = search_knowledge("Use price action: break of structure, pullback, and no indicators", index_path=index_path)

    assert any("lookahead_on" in chunk["text"] for chunk in exact["retrieved_chunks"])
    assert any(chunk["type"] == "strategy_pattern" and "break of structure" in chunk["text"].lower() for chunk in semantic["retrieved_chunks"])
    assert semantic["intent"]["no_indicators"] is True
    assert "BOS" in semantic["expanded_terms"]


def test_market_playbook_retrieval_prefers_crypto_and_forex_context(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)

    crypto = search_knowledge("Create a crypto BTC perpetual breakout strategy with funding and exchange liquidity risk", index_path=index_path)
    forex = search_knowledge("Create a forex EURUSD London session mean reversion strategy with spread and rollover controls", index_path=index_path)

    assert crypto["intent"]["tags"]
    assert any(chunk["source_id"] == "internal-crypto-playbook" and "crypto" in chunk["market_tags"] for chunk in crypto["retrieved_chunks"])
    assert any(chunk["source_id"] == "internal-forex-playbook" and "forex" in chunk["market_tags"] for chunk in forex["retrieved_chunks"])
    index = load_json(index_path)
    assert any(chunk["source_id"] == "binance-academy-risk-management-strategies" and chunk["source_type"] == "external_ref" for chunk in index["chunks"])
    assert any(chunk["source_id"] == "babypips-school-of-pipsology" and chunk["source_type"] == "external_ref" for chunk in index["chunks"])
    assert "internal-forex-playbook" not in {chunk["source_id"] for chunk in crypto["retrieved_chunks"]}
    assert "internal-crypto-playbook" not in {chunk["source_id"] for chunk in forex["retrieved_chunks"]}
    assert crypto["filters_applied"]["prefilter_output_count"] <= crypto["filters_applied"]["prefilter_input_count"]
    assert crypto["retrieval_confidence"]["score"] >= 0.4
    assert crypto["citations"]


def test_playbooks_keep_risk_boundary_retrievable(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)

    result = search_knowledge("Can this crypto strategy guarantee profit and be certified live-ready?", index_path=index_path)

    combined = " ".join(chunk["text"].lower() for chunk in result["retrieved_chunks"])
    assert "must not be used to claim" in combined or "must not claim" in combined
    assert any(chunk["source_id"] == "internal-risk-policy" for chunk in result["retrieved_chunks"])
    assert "internal-risk-policy" in result["required_source_hits"]
    assert result["low_confidence"] is False


def test_model_workflow_boundaries_are_required_for_action_prompts(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)

    prompts = [
        "which artifacts are internal raw JSON validation trades compile report",
        "create knowledge proposal with affected sources and recommendations",
        "repair validation blockers before preview",
    ]

    for prompt in prompts:
        result = search_knowledge(prompt, index_path=index_path)
        assert result["retrieved_chunks"][0]["source_id"] == "internal-model-workflow-boundaries"
        assert "internal-model-workflow-boundaries" in result["required_source_hits"]


def test_trading_skill_integration_retrieves_robustness_checklist(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)

    result = search_knowledge("Review a local preview strategy for sample size, slippage stress, invalidation, and overfit risk", index_path=index_path)

    assert any(chunk["source_id"] == "internal-trading-skill-integration" for chunk in result["retrieved_chunks"])
    combined = " ".join(chunk["text"].lower() for chunk in result["retrieved_chunks"])
    assert "sample size" in combined
    assert "slippage" in combined


def test_local_query_embedding_cache_hits_repeated_query(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)

    first = search_knowledge("crypto liquidity funding risk", index_path=index_path)
    second = search_knowledge("crypto liquidity funding risk", index_path=index_path)
    index = load_json(index_path)

    assert first["embedding_cache_status"] == "miss"
    assert second["embedding_cache_status"] == "hit"
    assert second["retrieval_cache_status"] == "hit"
    assert second["cache_layer"] == "retrieval_result"
    assert index["query_embedding_cache"]
    assert index["retrieval_result_cache"]


def test_retrieval_result_cache_invalidates_on_knowledge_version_change(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)

    first = search_knowledge("crypto liquidity funding risk", index_path=index_path)
    warm = search_knowledge("crypto liquidity funding risk", index_path=index_path)
    index = load_json(index_path)
    index["version"] = 2
    write_json(index_path, index)
    changed = search_knowledge("crypto liquidity funding risk", index_path=index_path)

    assert first["retrieval_cache_status"] == "miss"
    assert warm["retrieval_cache_status"] == "hit"
    assert changed["retrieval_cache_status"] == "miss"


def test_metadata_only_external_refs_are_not_top_ranked_unless_source_query(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)

    strategy_result = search_knowledge("crypto risk management strategy", index_path=index_path)
    source_result = search_knowledge("crypto risk management source reference link", index_path=index_path)

    assert strategy_result["retrieved_chunks"][0]["source_type"] != "external_ref"
    assert any(chunk["source_type"] == "external_ref" for chunk in source_result["retrieved_chunks"])


def test_rich_playbooks_retrieve_practical_experience_blocks(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)

    crypto = search_knowledge("Crypto BTC perpetual fakeout liquidity sweep funding traps", index_path=index_path)
    forex = search_knowledge("Forex London breakout spread rollover news trap", index_path=index_path)
    patterns = search_knowledge("No-indicator price action strategy with BOS retest and no future pivots", index_path=index_path)

    crypto_text = " ".join(chunk["text"].lower() for chunk in crypto["retrieved_chunks"])
    forex_text = " ".join(chunk["text"].lower() for chunk in forex["retrieved_chunks"])
    pattern_text = " ".join(chunk["text"].lower() for chunk in patterns["retrieved_chunks"])

    assert "when it works" in crypto_text
    assert "funding" in crypto_text
    assert "liquidity" in crypto_text
    assert "london breakout" in forex_text
    assert "spread" in forex_text
    assert "rollover" in forex_text
    assert "no-indicator price action" in pattern_text
    assert "future pivots" in pattern_text


def test_trusted_source_snapshot_summary_and_approval_promotes_curated_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)

    fake_article = """
    <html><body>
    Risk management in crypto requires position sizing, stop loss planning, and awareness of volatility.
    Traders should consider liquidity, leverage, and exchange risk before using a strategy.
    Funding and market conditions can change risk during a trade.
    This educational article does not guarantee profit.
    </body></html>
    """
    monkeypatch.setattr("strategy_codebot.knowledge_base._fetch_url_text", lambda url: fake_article)

    snapshot_path = tmp_path / "snapshot.json"
    proposal_path = tmp_path / "proposal.json"
    snapshot = snapshot_trusted_source("binance-academy-risk-management-strategies", out=snapshot_path)
    proposal = summarize_source_snapshot(snapshot_path, out=proposal_path)
    approved = approve_source_summary(proposal_path, index_path=index_path)
    result = search_knowledge("crypto risk management position sizing liquidity exchange", index_path=index_path)

    assert snapshot["source_state"] == "snapshotted"
    assert snapshot["content_hash"]
    assert snapshot["extractor_version"]
    assert proposal["status"] == "needs_review"
    assert "extracted_text" not in proposal
    assert "raw external text must not be promoted" in proposal["curated_summary"]
    assert approved["status"] == "pass"
    assert approved["item_id"].startswith("curated-binance-academy-risk-management-strategies")
    assert any(chunk["source_id"] == approved["item_id"] and chunk["source_type"] == "approved_source_summary" for chunk in result["retrieved_chunks"])


def test_trusted_source_snapshot_rejects_untrusted_or_unregistered_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a trusted public source"):
        snapshot_trusted_source("tradingview-pine-strategies", out=tmp_path / "snapshot.json")
    with pytest.raises(KeyError):
        snapshot_trusted_source("unknown-source", out=tmp_path / "snapshot.json")


def test_approve_source_summary_is_deduped_by_source_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)
    monkeypatch.setattr(
        "strategy_codebot.knowledge_base._fetch_url_text",
        lambda url: "Forex risk management should consider spread, rollover, position sizing, and session behavior.",
    )

    snapshot_path = tmp_path / "snapshot.json"
    proposal_path = tmp_path / "proposal.json"
    snapshot_trusted_source("babypips-school-of-pipsology", out=snapshot_path)
    summarize_source_snapshot(snapshot_path, out=proposal_path)
    first = approve_source_summary(proposal_path, index_path=index_path)
    second = approve_source_summary(proposal_path, index_path=index_path)
    index = load_json(index_path)

    assert first["item_id"] == second["item_id"]
    assert len([item for item in index["items"] if item["id"] == first["item_id"]]) == 1


def test_build_retrieved_context_exposes_trace_fields(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)

    context = build_retrieved_knowledge_context("Create a liquidity sweep strategy with reclaim candle", index_path=index_path)

    assert context["store"] == "knowledge_base"
    assert context["retrieval_query"]
    assert context["retrieval_latency_ms"] >= 0
    assert context["hybrid_candidate_count"] >= 1
    assert context["retrieved_chunks"]
    assert context["context_refs"][0].startswith("chunk:")
    assert "strategy_reasoning" in context["stage_relevance"]


def test_candidate_approval_promotes_retrievable_lesson(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    candidates_path = tmp_path / "kb" / "candidates.json"
    build_knowledge_index(index_path=index_path)
    candidate = propose_candidate(
        "When review sees repeated malformed JSON from a model, keep static validation authoritative and record fallback metadata.",
        evidence_ref="runs/evals/case/live-error.json",
        path=candidates_path,
    )

    approve = approve_candidate(candidate["candidate_id"], index_path=index_path, candidates_path=candidates_path)
    result = search_knowledge("malformed JSON fallback static validation", index_path=index_path)

    assert approve["status"] == "pass"
    assert any(approve["item_id"] == chunk["item_id"] for chunk in result["retrieved_chunks"])


def test_candidate_approval_invalidates_retrieval_result_cache(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    candidates_path = tmp_path / "kb" / "candidates.json"
    query = "Trader-grade strategy specs should encode setup invalidation explicitly before Pine generation"
    build_knowledge_index(index_path=index_path)
    initial = search_knowledge(query, index_path=index_path)
    assert not any(chunk.get("source_type") == "approved_candidate" for chunk in initial["retrieved_chunks"])
    candidate = propose_candidate(
        "Trader-grade strategy specs should encode setup invalidation explicitly before Pine generation, especially for price-action entries.",
        evidence_ref="runs/evals/case/context-report.json",
        path=candidates_path,
        dedupe_key="test-invalidation-cache",
        lesson_kind="strategy_quality",
        confidence="high",
        domain_tags=["strategy_quality", "price_action", "invalidation"],
        platform_tags=["pine_v6"],
        stages=["strategy_reasoning", "strategy_coding"],
        trust_level="agent_reviewed",
    )

    approve = approve_candidate(candidate["candidate_id"], index_path=index_path, candidates_path=candidates_path)
    result = search_knowledge(query, index_path=index_path)

    assert approve["status"] == "pass"
    assert any(chunk["item_id"] == approve["item_id"] for chunk in result["retrieved_chunks"])
    assert result["retrieval_cache_status"] == "miss"


def test_rejected_candidate_does_not_enter_retrieval(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    candidates_path = tmp_path / "kb" / "candidates.json"
    build_knowledge_index(index_path=index_path)
    candidate = propose_candidate("This strategy guarantees profit and cannot lose money.", evidence_ref="bad", path=candidates_path)

    assert candidate["status"] == "rejected"
    with pytest.raises(ValueError):
        approve_candidate(candidate["candidate_id"], index_path=index_path, candidates_path=candidates_path)
    result = search_knowledge("guarantees profit cannot lose money", index_path=index_path)
    assert not any(chunk["item_id"] == f"lesson-{candidate['candidate_id']}" for chunk in result["retrieved_chunks"])


def test_candidate_reject_updates_status(tmp_path: Path) -> None:
    candidates_path = tmp_path / "kb" / "candidates.json"
    candidate = propose_candidate("Use confirmed bars for price action swing breaks.", evidence_ref="review-report.json", path=candidates_path)

    report = reject_candidate(candidate["candidate_id"], candidates_path=candidates_path)
    store = load_candidates(candidates_path)

    assert report["status"] == "pass"
    assert store["candidates"][0]["status"] == "rejected"


def test_guarded_auto_review_promotes_safe_validator_lesson(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    candidates_path = tmp_path / "kb" / "candidates.json"
    evidence_path = tmp_path / "runs" / "run-01" / "eval-report.json"
    build_knowledge_index(index_path=index_path)
    write_json(evidence_path, {"status": "fail", "cases": [{"id": "case-a"}]})
    candidate = propose_candidate(
        "Pine generation must keep the exact version header on the first line before static validation repair.",
        evidence_ref=str(evidence_path),
        path=candidates_path,
        candidate_type="procedural",
        lesson_kind="pine_static_validation",
        confidence="high",
        trust_level="agent_reviewed",
        metadata={"learning": {"evidence_count": 1, "extractor": "test"}},
    )

    review = review_candidate_for_auto_promotion(candidate["candidate_id"], index_path=index_path, candidates_path=candidates_path)
    result = search_knowledge("exact version header static validation repair", index_path=index_path)
    store = load_candidates(candidates_path)

    assert review["promotion_decision"] == "auto_approved"
    assert review["status"] == "auto_approved"
    assert store["candidates"][0]["status"] == "auto_approved"
    assert store["candidates"][0]["metadata"]["promotion"]["quality_score"] == 1.0
    assert any(chunk["item_id"] == review["approval"]["item_id"] for chunk in result["retrieved_chunks"])


def test_guarded_auto_review_keeps_performance_claim_for_review(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    candidates_path = tmp_path / "kb" / "candidates.json"
    evidence_path = tmp_path / "runs" / "run-01" / "eval-report.json"
    build_knowledge_index(index_path=index_path)
    write_json(evidence_path, {"status": "pass"})
    candidate = propose_candidate(
        "This strategy shows a strong market edge and better performance in January conditions.",
        evidence_ref=str(evidence_path),
        path=candidates_path,
        candidate_type="procedural",
        lesson_kind="pine_static_validation",
        confidence="high",
        metadata={"learning": {"evidence_count": 1}},
    )

    review = review_candidate_for_auto_promotion(candidate["candidate_id"], index_path=index_path, candidates_path=candidates_path)
    store = load_candidates(candidates_path)

    assert review["promotion_decision"] == "needs_review"
    assert review["review_required_reason"] == "trading_performance_claim_requires_review"
    assert store["candidates"][0]["status"] == "needs_review"


def test_guarded_auto_review_rejects_live_trading_claim(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    candidates_path = tmp_path / "kb" / "candidates.json"
    evidence_path = tmp_path / "runs" / "run-01" / "eval-report.json"
    build_knowledge_index(index_path=index_path)
    write_json(evidence_path, {"status": "pass"})
    candidate = propose_candidate(
        "This lesson certifies broker execution is live-ready with no-loss protection.",
        evidence_ref=str(evidence_path),
        path=candidates_path,
        candidate_type="procedural",
        lesson_kind="pine_static_validation",
        confidence="high",
        metadata={"learning": {"evidence_count": 1}},
    )

    review = review_candidate_for_auto_promotion(candidate["candidate_id"], index_path=index_path, candidates_path=candidates_path)

    assert review["promotion_decision"] == "auto_rejected"
    assert review["status"] == "rejected"
    assert load_candidates(candidates_path)["candidates"][0]["status"] == "rejected"


def test_guarded_auto_review_requires_existing_artifact_evidence(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    candidates_path = tmp_path / "kb" / "candidates.json"
    build_knowledge_index(index_path=index_path)
    candidate = propose_candidate(
        "Pine repair should summarize static validator failures before changing generated code.",
        evidence_ref=str(tmp_path / "runs" / "missing" / "eval-report.json"),
        path=candidates_path,
        candidate_type="procedural",
        lesson_kind="pine_static_validation",
        confidence="high",
        metadata={"learning": {"evidence_count": 1}},
    )

    review = review_candidate_for_auto_promotion(candidate["candidate_id"], index_path=index_path, candidates_path=candidates_path)

    assert review["promotion_decision"] == "needs_review"
    assert review["review_required_reason"] == "missing_artifact_evidence"


def test_guarded_auto_review_accepts_repeated_file_evidence(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    candidates_path = tmp_path / "kb" / "candidates.json"
    first_evidence = tmp_path / "runs" / "run-01" / "eval-report.json"
    second_evidence = tmp_path / "runs" / "run-02" / "eval-report.json"
    build_knowledge_index(index_path=index_path)
    write_json(first_evidence, {"status": "fail"})
    write_json(second_evidence, {"status": "fail"})
    lesson = "Static validation repair should preserve strategy declaration arguments while fixing Pine syntax."
    candidate = propose_candidate(
        lesson,
        evidence_ref=str(first_evidence),
        path=candidates_path,
        dedupe_key="validator-repeat",
        candidate_type="procedural",
        lesson_kind="pine_static_validation",
        confidence="high",
    )
    propose_candidate(
        lesson,
        evidence_ref=str(second_evidence),
        path=candidates_path,
        dedupe_key="validator-repeat",
        candidate_type="procedural",
        lesson_kind="pine_static_validation",
        confidence="high",
    )

    review = review_candidate_for_auto_promotion(candidate["candidate_id"], index_path=index_path, candidates_path=candidates_path)

    assert review["promotion_decision"] == "auto_approved"
    assert review["status"] == "auto_approved"


def test_guarded_auto_review_batch_rebuilds_local_index_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    candidates_path = tmp_path / "kb" / "candidates.json"
    evidence_path = tmp_path / "runs" / "run-01" / "eval-report.json"
    build_knowledge_index(index_path=index_path)
    write_json(evidence_path, {"status": "fail"})
    first = propose_candidate(
        "Static validation repair should preserve the Pine version header.",
        evidence_ref=str(evidence_path),
        path=candidates_path,
        candidate_type="procedural",
        lesson_kind="pine_static_validation",
        confidence="high",
        metadata={"learning": {"evidence_count": 1}},
    )
    second = propose_candidate(
        "Static validation repair should preserve risk exit rules.",
        evidence_ref=str(evidence_path),
        path=candidates_path,
        candidate_type="procedural",
        lesson_kind="pine_static_validation",
        confidence="high",
        metadata={"learning": {"evidence_count": 1}},
    )
    from strategy_codebot import knowledge_base as kb

    original_chunks_for_items = kb._chunks_for_items
    calls = 0

    def counted_chunks_for_items(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_chunks_for_items(*args, **kwargs)

    monkeypatch.setattr(kb, "_chunks_for_items", counted_chunks_for_items)

    reviews = review_candidates_for_auto_promotion(
        [first["candidate_id"], second["candidate_id"]],
        index_path=index_path,
        candidates_path=candidates_path,
    )

    assert calls == 2
    assert [review["promotion_decision"] for review in reviews] == ["auto_approved", "auto_approved"]
    assert {candidate["status"] for candidate in load_candidates(candidates_path)["candidates"]} == {"auto_approved"}


def test_guarded_auto_review_batch_retrieval_failure_does_not_block_other(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    index_path = tmp_path / "kb" / "index.json"
    candidates_path = tmp_path / "kb" / "candidates.json"
    evidence_path = tmp_path / "runs" / "run-01" / "eval-report.json"
    build_knowledge_index(index_path=index_path)
    write_json(evidence_path, {"status": "fail"})
    first = propose_candidate(
        "Static validation repair should preserve the Pine version header.",
        evidence_ref=str(evidence_path),
        path=candidates_path,
        candidate_type="procedural",
        lesson_kind="pine_static_validation",
        confidence="high",
        metadata={"learning": {"evidence_count": 1}},
    )
    second = propose_candidate(
        "Static validation repair should preserve risk exit rules.",
        evidence_ref=str(evidence_path),
        path=candidates_path,
        candidate_type="procedural",
        lesson_kind="pine_static_validation",
        confidence="high",
        metadata={"learning": {"evidence_count": 1}},
    )

    monkeypatch.setattr(
        "strategy_codebot.knowledge_base._verify_learning_retrieval_dry_run_batch",
        lambda candidates, **_kwargs: {
            str(candidates[0]["candidate_id"]): {"status": "fail", "reason": "not_retrieved"},
            str(candidates[1]["candidate_id"]): {"status": "pass", "retrieved": True},
        },
    )

    reviews = review_candidates_for_auto_promotion(
        [first["candidate_id"], second["candidate_id"]],
        index_path=index_path,
        candidates_path=candidates_path,
    )
    statuses = {candidate["candidate_id"]: candidate["status"] for candidate in load_candidates(candidates_path)["candidates"]}

    assert reviews[0]["promotion_decision"] == "needs_review"
    assert reviews[0]["review_required_reason"] == "retrieval_verification_failed"
    assert reviews[1]["promotion_decision"] == "auto_approved"
    assert statuses[first["candidate_id"]] == "needs_review"
    assert statuses[second["candidate_id"]] == "auto_approved"


def test_guarded_auto_review_blocks_retrieval_verification_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    candidates_path = tmp_path / "kb" / "candidates.json"
    evidence_path = tmp_path / "runs" / "run-01" / "eval-report.json"
    build_knowledge_index(index_path=index_path)
    write_json(evidence_path, {"status": "fail"})
    candidate = propose_candidate(
        "Static validation repair should keep risk exits present after syntax cleanup.",
        evidence_ref=str(evidence_path),
        path=candidates_path,
        candidate_type="procedural",
        lesson_kind="pine_static_validation",
        confidence="high",
        metadata={"learning": {"evidence_count": 1}},
    )
    monkeypatch.setattr(
        "strategy_codebot.knowledge_base._verify_learning_retrieval_dry_run",
        lambda *args, **kwargs: {"status": "fail", "reason": "not_retrieved"},
    )

    review = review_candidate_for_auto_promotion(candidate["candidate_id"], index_path=index_path, candidates_path=candidates_path)

    assert review["promotion_decision"] == "needs_review"
    assert review["review_required_reason"] == "retrieval_verification_failed"


def test_guarded_auto_review_llm_pass_cannot_override_deterministic_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    candidates_path = tmp_path / "kb" / "candidates.json"
    build_knowledge_index(index_path=index_path)
    candidate = propose_candidate(
        "Static validation repair should keep Pine version header before executable code.",
        evidence_ref=str(tmp_path / "runs" / "missing" / "eval-report.json"),
        path=candidates_path,
        candidate_type="procedural",
        lesson_kind="pine_static_validation",
        confidence="high",
        metadata={"learning": {"evidence_count": 1}},
    )

    review = review_candidate_for_auto_promotion(
        candidate["candidate_id"],
        index_path=index_path,
        candidates_path=candidates_path,
        llm_judge=lambda _candidate: {
            "generalizable": True,
            "unsafe_claims": [],
            "requires_human_review": False,
            "reason": "safe",
            "confidence": "high",
        },
    )

    assert review["promotion_decision"] == "needs_review"
    assert review["review_required_reason"] == "missing_artifact_evidence"


def test_guarded_auto_review_llm_review_blocks_otherwise_safe_candidate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    candidates_path = tmp_path / "kb" / "candidates.json"
    evidence_path = tmp_path / "runs" / "run-01" / "eval-report.json"
    build_knowledge_index(index_path=index_path)
    write_json(evidence_path, {"status": "fail"})
    candidate = propose_candidate(
        "Static validation repair should keep risk exits present after syntax cleanup.",
        evidence_ref=str(evidence_path),
        path=candidates_path,
        candidate_type="procedural",
        lesson_kind="pine_static_validation",
        confidence="high",
        metadata={"learning": {"evidence_count": 1}},
    )

    review = review_candidate_for_auto_promotion(
        candidate["candidate_id"],
        index_path=index_path,
        candidates_path=candidates_path,
        llm_judge=lambda _candidate: {
            "generalizable": False,
            "unsafe_claims": [],
            "requires_human_review": True,
            "reason": "too_specific",
            "confidence": "medium",
        },
    )

    assert review["promotion_decision"] == "needs_review"
    assert review["review_required_reason"] == "llm_requires_review"


def test_failure_candidate_is_pending_and_deduped(tmp_path: Path) -> None:
    candidates_path = tmp_path / "kb" / "candidates.json"
    failure = {
        "id": "pine_ma_crossover",
        "failure_class": "malformed_response",
        "failure_stage": "pine_code_generation",
        "failure_reason": "malformed provider response",
    }

    first = propose_failure_candidate(failure, evidence_ref="eval-case:pine_ma_crossover:/tmp/run", path=candidates_path)
    second = propose_failure_candidate(failure, evidence_ref="eval-case:pine_ma_crossover:/tmp/run", path=candidates_path)
    store = load_candidates(candidates_path)

    assert first is not None
    assert first["status"] == "needs_review"
    assert second is not None
    assert second["candidate_id"] == first["candidate_id"]
    assert second["deduped"] is True
    assert len(store["candidates"]) == 1


def test_learn_from_run_auto_approves_repeated_safe_validator_lesson(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    candidates_path = tmp_path / "kb" / "candidates.json"
    artifacts_root = tmp_path / "artifacts"
    build_knowledge_index(index_path=index_path)
    for run in ("run-01", "run-02"):
        write_json(
            artifacts_root / run / "eval-report.json",
            {
                "status": "fail",
                "cases": [
                    {
                        "id": "case-a",
                        "status": "fail",
                        "failure_class": "static_validation_failed",
                        "failure_stage": "final_gate",
                        "validation_failures": [{"name": "version_header", "status": "fail"}],
                    }
                ],
            },
        )

    report = learn_from_run(artifacts_root, index_path=index_path, candidates_path=candidates_path)
    second_report = learn_from_run(artifacts_root, index_path=index_path, candidates_path=candidates_path)
    store = load_candidates(candidates_path)
    result = search_knowledge("exact version header first line Pine repair", index_path=index_path)
    approved_item_id = report["promoted"][0]["approval"]["item_id"]
    index = load_json(index_path)
    live_style_result = search_knowledge(
        "Create a Pine v6 price-action-only strategy. Follow this learned requirement: Pine repair must preserve exact version header before code generation.",
        index_path=index_path,
    )

    assert report["status"] == "pass"
    assert report["promoted_count"] == 1
    assert second_report["promoted_count"] == 1
    assert store["candidates"][0]["status"] == "auto_approved"
    assert store["candidates"][0]["confidence"] == "high"
    assert store["candidates"][0]["trust_level"] == "agent_reviewed"
    assert sum(1 for item in index["items"] if item["id"] == approved_item_id) == 1
    assert any(chunk["item_id"] == approved_item_id for chunk in result["retrieved_chunks"])
    assert any(chunk["item_id"] == approved_item_id for chunk in live_style_result["retrieved_chunks"])


def test_learn_from_run_extracts_backtest_robustness_lessons(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    candidates_path = tmp_path / "kb" / "candidates.json"
    artifacts_root = tmp_path / "artifacts"
    build_knowledge_index(index_path=index_path)
    for run in ("run-01", "run-02"):
        write_json(
            artifacts_root / run / "backtest-report.json",
            {
                "status": "completed",
                "robustness_report": {
                    "status": "warn",
                    "checks": {
                        "sample_size": {
                            "status": "warn",
                            "message": "Low closed-trade sample; keep the result in manual review.",
                        }
                    },
                },
                "promotion_decision": {"decision": "manual_review"},
            },
        )

    report = learn_from_run(artifacts_root, index_path=index_path, candidates_path=candidates_path)
    result = search_knowledge("local preview low closed-trade sample manual review preview profit evidence", index_path=index_path)

    assert report["promoted_count"] == 0
    assert report["skipped_count"] == 1
    assert report["skipped"][0]["lesson_kind"] == "backtest_robustness"
    assert report["skipped"][0]["validator_check"] == "sample_size"
    assert report["skipped"][0]["review_required_reason"] == "trading_performance_claim_requires_review"
    assert "PineForge" not in report["skipped"][0]["lesson"]
    assert not any(chunk["source_type"] == "approved_candidate" for chunk in result["retrieved_chunks"])


def test_learn_from_run_manual_mode_dedupes_without_approval(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    candidates_path = tmp_path / "kb" / "candidates.json"
    artifacts_root = tmp_path / "artifacts"
    build_knowledge_index(index_path=index_path)
    write_json(
        artifacts_root / "run-01" / "eval-report.json",
        {
            "status": "fail",
            "cases": [
                {
                    "id": "case-a",
                    "status": "fail",
                    "failure_class": "static_validation_failed",
                    "failure_stage": "final_gate",
                    "validation_failures": [{"name": "version_header", "status": "fail"}],
                }
            ],
        },
    )

    first = learn_from_run(artifacts_root, approval_mode="manual", index_path=index_path, candidates_path=candidates_path)
    second = learn_from_run(artifacts_root, approval_mode="manual", index_path=index_path, candidates_path=candidates_path)
    store = load_candidates(candidates_path)

    assert first["promoted_count"] == 0
    assert first["skipped_count"] == 1
    assert second["candidate_count"] == 1
    assert len(store["candidates"]) == 1
    assert store["candidates"][0]["status"] == "needs_review"


def test_learning_safety_rejects_unsafe_claims() -> None:
    reason = _learning_safety_rejection({"lesson": "This strategy is live-ready and guaranteed profit with no loss risk."})

    assert reason in {"blocked_policy_claim", "unsafe_trading_claim"}


def test_prompt_classifier_price_action_without_forcing_indicators() -> None:
    intent = classify_prompt("Use BOS, liquidity sweep, and rejection candles. Do not use indicators.")

    assert "price_action" in intent["tags"]
    assert "indicator" in intent["tags"]
    assert intent["no_indicators"] is True


def test_knowledge_eval_suite_passes_seeded_index(tmp_path: Path) -> None:
    index_path = tmp_path / "kb" / "index.json"
    out_path = tmp_path / "eval-report.json"
    build_knowledge_index(index_path=index_path)

    report = evaluate_knowledge_suite(Path("examples/evals/knowledge-core.yaml"), index_path=index_path, out_path=out_path)

    validate_payload(report, "knowledge-eval-report.schema.json")
    assert report["status"] == "pass"
    assert out_path.exists()


def test_postgres_knowledge_store_roundtrip_when_database_available(tmp_path: Path) -> None:
    database_url = os.environ.get("STRATEGY_CODEBOT_KNOWLEDGE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set STRATEGY_CODEBOT_KNOWLEDGE_TEST_DATABASE_URL to run Postgres/pgvector KB integration.")

    report = build_knowledge_index(index_path=tmp_path / "unused.json", database_url=database_url)
    result = search_knowledge("BOS retest price action without indicators", database_url=database_url)
    candidate = propose_candidate(
        "When BOS retest prompts are vague, prefer explicit swing structure, retest trigger, invalidation, and bounded risk.",
        evidence_ref="test:postgres-roundtrip",
        database_url=database_url,
    )
    approved = approve_candidate(candidate["candidate_id"], database_url=database_url)

    assert report["store"]["adapter"] == "postgres_pgvector"
    assert result["retrieved_chunks"]
    assert result["cache_hit"] is False
    assert result["embedding_cache_status"] == "miss"
    warm = search_knowledge("BOS retest price action without indicators", database_url=database_url)
    assert warm["cache_hit"] is True
    assert warm["embedding_cache_status"] == "hit"
    assert "embedding_latency_ms" in warm
    health = knowledge_health(database_url=database_url)
    assert health["status"] == "pass"
    assert approved["status"] == "pass"


def test_postgres_production_profile_roundtrip_when_database_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = os.environ.get("STRATEGY_CODEBOT_KNOWLEDGE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set STRATEGY_CODEBOT_KNOWLEDGE_TEST_DATABASE_URL to run Postgres/pgvector KB integration.")

    def fake_remote_embedding(text: str, embedding_model: str, embedding_provider: str) -> list[float]:
        assert embedding_model == EMBEDDING_MODEL_PRODUCTION_OPENROUTER
        assert embedding_provider == "openrouter"
        vector = [0.0] * EMBEDDING_DIMENSION_TEXT_3_SMALL
        vector[0] = 1.0
        return vector

    monkeypatch.setattr("strategy_codebot.knowledge_base._remote_embedding", fake_remote_embedding)

    report = build_knowledge_index(
        index_path=tmp_path / "unused.json",
        database_url=database_url,
        embedding_profile=EMBEDDING_PROFILE_PRODUCTION_OPENROUTER,
    )
    result = search_knowledge("BOS retest price action without indicators", database_url=database_url)

    assert report["store"]["adapter"] == "postgres_pgvector"
    assert report["store"]["type"] == "postgres_pgvector"
    assert result["embedding_provider"] == "openrouter"
    assert result["embedding_model"] == EMBEDDING_MODEL_PRODUCTION_OPENROUTER
    assert result["retrieved_chunks"]
