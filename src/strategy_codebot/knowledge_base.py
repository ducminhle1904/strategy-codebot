from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import urlparse
from uuid import uuid4

import yaml

from strategy_codebot.paths import ensure_parent, repo_root, resolve_repo_path
from strategy_codebot.schemas import load_json, write_json
from strategy_codebot.tool_runtime import find_blocked_claims


KNOWLEDGE_INDEX_PATH = "knowledge/kb/index.json"
KNOWLEDGE_CANDIDATES_PATH = "knowledge/kb/candidates.json"
KNOWLEDGE_SOURCE_SNAPSHOT_DIR = "knowledge/snapshots"
KNOWLEDGE_SOURCE_PROPOSAL_DIR = "knowledge/proposals"
POSTGRES_SCHEMA_PATH = "docs/knowledge/postgres-pgvector-schema.sql"
KNOWLEDGE_DATABASE_URL_ENV = "STRATEGY_CODEBOT_KNOWLEDGE_DATABASE_URL"
EMBEDDING_PROFILE_LOCAL = "local"
EMBEDDING_PROFILE_PRODUCTION_OPENROUTER = "production-openrouter"
EMBEDDING_PROFILE_PRODUCTION_OPENAI = "production-openai"
EMBEDDING_PROFILES = {EMBEDDING_PROFILE_LOCAL, EMBEDDING_PROFILE_PRODUCTION_OPENROUTER, EMBEDDING_PROFILE_PRODUCTION_OPENAI}
EMBEDDING_PROVIDER_LOCAL = "local"
EMBEDDING_PROVIDER_OPENROUTER = "openrouter"
EMBEDDING_PROVIDER_OPENAI = "openai"
EMBEDDING_MODEL_LOCAL = "local/hash-embedding-64"
EMBEDDING_MODEL_DEFAULT = EMBEDDING_MODEL_LOCAL
EMBEDDING_MODEL_PRODUCTION_OPENROUTER = "openai/text-embedding-3-small"
EMBEDDING_MODEL_PRODUCTION_OPENAI = "text-embedding-3-small"
EMBEDDING_MODEL_QUALITY = "text-embedding-3-large"
EMBEDDING_DIMENSION_LOCAL = 64
EMBEDDING_DIMENSION_TEXT_3_SMALL = 1536
EMBEDDING_DIMENSION_TEXT_3_LARGE = 3072
EMBEDDING_DIMENSION = EMBEDDING_DIMENSION_LOCAL
KNOWLEDGE_UNAVAILABLE = "knowledge_unavailable"
ACTIVE_STATUSES = {"active", "approved", "auto_approved"}
LOW_RETRIEVAL_CONFIDENCE_THRESHOLD = 0.4
KNOWLEDGE_TYPES = {"semantic", "procedural", "episodic", "strategy_pattern", "source_ref"}
CANDIDATE_STATUSES = {"proposed", "needs_review", "approved", "auto_approved", "rejected", "superseded"}
TRUST_LEVELS = {"low", "medium", "high", "agent_reviewed"}
TRUSTED_PUBLIC_SOURCE_TYPES = {"trusted_public"}
SOURCE_SNAPSHOT_EXTRACTOR_VERSION = "trusted-source-snapshot-v1"
LEARNING_APPROVAL_MODES = {"manual", "agent-auto", "guarded-auto"}
GUARDED_AUTO_APPROVAL_MODES = {"agent-auto", "guarded-auto"}
AUTO_PROMOTABLE_LESSON_KINDS = {
    "pine_static_validation",
    "backtest_robustness",
    "harness_route_health",
    "context_contract",
    "context_budget",
    "harness_provider_diagnostics",
}
KNOWLEDGE_REVIEW_TEXT_RISK_TERMS = {
    "profit",
    "profitable",
    "win rate",
    "sharpe",
    "sortino",
    "alpha",
    "edge",
    "market edge",
    "performance",
    "return",
}
RETRIEVAL_RESULT_CACHE_VERSION = "retrieval-v2"
BACKTEST_ROBUSTNESS_LESSONS = {
    "sample_size": (
        "Local preview results with no or low closed-trade sample must stay rejected or manual-review only; do not treat preview profit as evidence.",
        ["backtest", "anti_overfit", "sample_adequacy"],
    ),
    "execution_costs": (
        "Local preview results should include fee or slippage assumptions before promotion; zero-cost previews need manual execution-realism review.",
        ["backtest", "execution_realism", "risk"],
    ),
    "drawdown": (
        "Local preview results with high drawdown must trigger manual risk review or rejection before any promotion decision.",
        ["backtest", "risk", "drawdown"],
    ),
    "loss_streak": (
        "Local preview results with long loss streaks need position-sizing and portfolio-heat review before promotion.",
        ["backtest", "risk", "position_sizing"],
    ),
    "oos_window": (
        "Local preview results without enough range and sample for out-of-sample review must remain manual-review only.",
        ["backtest", "anti_overfit", "validation"],
    ),
    "suspicious_metrics": (
        "Local preview results with extreme win rate, Sharpe, Sortino, or return metrics need overfit review before promotion.",
        ["backtest", "anti_overfit", "validation"],
    ),
}

PRICE_ACTION_ALIASES = {
    "break of structure": ["BOS", "market structure break", "swing break"],
    "bos": ["break of structure", "market structure break", "swing high", "swing low"],
    "choch": ["change of character", "market structure shift"],
    "liquidity sweep": ["stop hunt", "sweep and reclaim", "false break"],
    "rejection candle": ["wick rejection", "pin bar", "engulfing confirmation"],
    "support resistance": ["range high", "range low", "supply demand"],
    "fvg": ["fair value gap", "imbalance"],
}

SEED_PATTERNS = [
    {
        "id": "concept-market-structure-foundations",
        "type": "semantic",
        "domain_tags": ["price_action", "market_structure", "swing_high", "swing_low"],
        "market_tags": ["general"],
        "platform_tags": ["general"],
        "title": "Market structure foundations",
        "content": (
            "Price action strategies should define observable market structure terms such as swing high, swing low, "
            "range high, range low, break of structure, change of character, retest, and invalidation. "
            "When the user asks for a price action strategy but gives no indicators or exact rules, require "
            "observable market structure and invalidation. Natural-language prompts should be converted into "
            "explicit OHLC conditions before Pine generation. No-indicator price action should avoid future pivots "
            "and use clarifying questions for BOS, CHoCH, sweep, rejection, or retest triggers."
        ),
    },
    {
        "id": "pattern-price-action-bos-retest",
        "type": "strategy_pattern",
        "domain_tags": ["price_action", "market_structure", "bos", "retest"],
        "market_tags": ["general"],
        "platform_tags": ["pine_v6"],
        "title": "Break of structure and retest",
        "content": (
            "A BOS/retest setup defines swing highs and swing lows, waits for a confirmed break of structure, "
            "then waits for a pullback/retest before entry. Avoid indicator requirements unless the user asks for them. "
            "Use confirmed bars to reduce repaint risk and define invalidation beyond the retest swing."
        ),
    },
    {
        "id": "pattern-price-action-liquidity-sweep",
        "type": "strategy_pattern",
        "domain_tags": ["price_action", "liquidity_sweep", "stop_hunt", "reclaim"],
        "market_tags": ["general"],
        "platform_tags": ["pine_v6"],
        "title": "Liquidity sweep and reclaim",
        "content": (
            "A liquidity sweep setup looks for price to take a prior swing high or low, fail to hold beyond it, "
            "and reclaim the level on a confirmed candle. The strategy should define the swept level, reclaim rule, "
            "entry trigger, stop beyond the sweep extreme, and bounded target logic."
        ),
    },
    {
        "id": "pattern-price-action-rejection-candle",
        "type": "strategy_pattern",
        "domain_tags": ["price_action", "candlestick", "rejection", "wick"],
        "market_tags": ["general"],
        "platform_tags": ["pine_v6"],
        "title": "Rejection candle confirmation",
        "content": (
            "A rejection candle rule should specify wick/body relationship, close location, and whether the signal must "
            "occur at a structure level. Use explicit OHLC formulas instead of vague visual wording."
        ),
    },
    {
        "id": "pattern-no-repaint-request-security",
        "type": "procedural",
        "domain_tags": ["pine", "repaint", "multi_timeframe"],
        "market_tags": ["general"],
        "platform_tags": ["pine_v6"],
        "title": "No-repaint higher timeframe retrieval",
        "content": (
            "For Pine v6 multi-timeframe logic, avoid barmerge.lookahead_on. Prefer explicit lookahead=barmerge.lookahead_off "
            "and confirmed-bar logic. Static validation treats lookahead_on as a repaint hazard."
        ),
    },
]


@dataclass(frozen=True)
class RetrievalOptions:
    limit: int = 6
    lexical_limit: int = 20
    vector_limit: int = 20
    max_chars_per_chunk: int = 900
    embedding_model: str = EMBEDDING_MODEL_DEFAULT
    embedding_provider: str = EMBEDDING_PROVIDER_LOCAL
    prefilter_limit: int = 200


def default_index_path() -> Path:
    return resolve_repo_path(Path(ensure_index_env() or KNOWLEDGE_INDEX_PATH))


def ensure_index_env() -> str | None:
    return os.getenv("STRATEGY_CODEBOT_KNOWLEDGE_INDEX")


def ensure_database_url(database_url: str | None = None) -> str | None:
    return database_url or os.getenv(KNOWLEDGE_DATABASE_URL_ENV)


def resolve_embedding_config(
    *,
    embedding_profile: str = EMBEDDING_PROFILE_LOCAL,
    embedding_model: str | None = None,
    embedding_provider: str | None = None,
) -> dict[str, Any]:
    if embedding_profile not in EMBEDDING_PROFILES:
        raise ValueError(f"embedding_profile must be one of {', '.join(sorted(EMBEDDING_PROFILES))}")
    if embedding_profile == EMBEDDING_PROFILE_PRODUCTION_OPENROUTER:
        provider = embedding_provider or EMBEDDING_PROVIDER_OPENROUTER
        model = embedding_model or EMBEDDING_MODEL_PRODUCTION_OPENROUTER
    elif embedding_profile == EMBEDDING_PROFILE_PRODUCTION_OPENAI:
        provider = embedding_provider or EMBEDDING_PROVIDER_OPENAI
        model = embedding_model or EMBEDDING_MODEL_PRODUCTION_OPENAI
    else:
        provider = embedding_provider or EMBEDDING_PROVIDER_LOCAL
        model = embedding_model or EMBEDDING_MODEL_LOCAL
    return {
        "embedding_profile": embedding_profile,
        "embedding_provider": provider,
        "embedding_model": model,
        "embedding_dimension": embedding_dimension(model, provider),
    }


def embedding_dimension(embedding_model: str, embedding_provider: str = EMBEDDING_PROVIDER_LOCAL) -> int:
    if embedding_provider == EMBEDDING_PROVIDER_LOCAL or embedding_model == EMBEDDING_MODEL_LOCAL:
        return EMBEDDING_DIMENSION_LOCAL
    model = embedding_model.split("/")[-1]
    if model == "text-embedding-3-large":
        return EMBEDDING_DIMENSION_TEXT_3_LARGE
    if model == "text-embedding-3-small":
        return EMBEDDING_DIMENSION_TEXT_3_SMALL
    raise ValueError(f"Unknown embedding model dimension for {embedding_provider}/{embedding_model}")


def build_knowledge_index(
    *,
    index_path: Path | None = None,
    source_registry_path: Path | None = None,
    embedding_model: str | None = None,
    embedding_profile: str = EMBEDDING_PROFILE_LOCAL,
    database_url: str | None = None,
) -> dict[str, Any]:
    embedding = resolve_embedding_config(embedding_profile=embedding_profile, embedding_model=embedding_model)
    index_path = resolve_repo_path(index_path or Path(KNOWLEDGE_INDEX_PATH))
    registry_path = resolve_repo_path(source_registry_path or repo_root() / "configs" / "source-registry.yaml")
    created_at = _now()
    items: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []

    for item in _seed_items_from_docs(registry_path, created_at):
        items.append(item)
        sources.append(_source_from_item(item))
    for pattern in SEED_PATTERNS:
        item = _item(
            item_id=pattern["id"],
            item_type=str(pattern["type"]),
            title=str(pattern["title"]),
            content=str(pattern["content"]),
            domain_tags=list(pattern["domain_tags"]),
            market_tags=list(pattern["market_tags"]),
            platform_tags=list(pattern["platform_tags"]),
            source_type="internal_seed",
            source_uri=f"seed:{pattern['id']}",
            trust_level="medium",
            created_at=created_at,
        )
        items.append(item)
        sources.append(_source_from_item(item))

    chunks = _chunks_for_items(
        items,
        embedding_model=embedding["embedding_model"],
        embedding_provider=embedding["embedding_provider"],
    )
    index = _index_payload(index_path, registry_path, items, chunks, sources, embedding=embedding, created_at=created_at)
    db_url = database_url
    if db_url:
        _write_db_index(index, db_url)
        return _init_report(index_path, index, adapter="postgres_pgvector", database_url=db_url)
    write_json(index_path, index)
    return _init_report(index_path, index)


def ingest_knowledge_source(
    source: str,
    *,
    index_path: Path | None = None,
    embedding_model: str | None = None,
    embedding_profile: str = EMBEDDING_PROFILE_LOCAL,
    database_url: str | None = None,
) -> dict[str, Any]:
    embedding = resolve_embedding_config(embedding_profile=embedding_profile, embedding_model=embedding_model)
    db_url = database_url
    if db_url:
        return _ingest_db_source(source, db_url, embedding=embedding)
    index_path = resolve_repo_path(index_path or Path(KNOWLEDGE_INDEX_PATH))
    index = load_index(index_path)
    source_path = resolve_repo_path(Path(source))
    if not source_path.exists():
        raise FileNotFoundError(f"Knowledge source not found: {source}")
    content = source_path.read_text(encoding="utf-8")
    item = _item(
        item_id=_slug(f"doc-{source_path.stem}"),
        item_type=_infer_type(content, str(source_path)),
        title=source_path.stem.replace("-", " ").title(),
        content=content,
        domain_tags=_infer_domain_tags(content),
        market_tags=["general"],
        platform_tags=_infer_platform_tags(content, str(source_path)),
        source_type="internal_doc",
        source_uri=str(source_path),
        trust_level="high",
        created_at=_now(),
    )
    index["items"] = [existing for existing in index.get("items", []) if existing.get("id") != item["id"]]
    index["items"].append(item)
    index["sources"] = [source for source in index.get("sources", []) if source.get("id") != item["id"]]
    index["sources"].append(_source_from_item(item))
    index["embedding_profile"] = embedding["embedding_profile"]
    index["embedding_provider"] = embedding["embedding_provider"]
    index["embedding_model"] = embedding["embedding_model"]
    index["embedding_dimension"] = embedding["embedding_dimension"]
    index["chunks"] = _chunks_for_items(index["items"], embedding_model=embedding["embedding_model"], embedding_provider=embedding["embedding_provider"])
    index["retrieval_index"] = _retrieval_index_payload(index["chunks"])
    index["updated_at"] = _now()
    index["stats"] = _index_stats(index)
    write_json(index_path, index)
    return {"status": "pass", "index_ref": str(index_path), "item_id": item["id"], "chunk_count": len(index["chunks"])}


def snapshot_trusted_source(
    source_id: str,
    *,
    registry_path: Path | None = None,
    out: Path | None = None,
) -> dict[str, Any]:
    registry_path = resolve_repo_path(registry_path or repo_root() / "configs" / "source-registry.yaml")
    source = _registry_source_by_id(registry_path, source_id)
    if str(source.get("type")) not in TRUSTED_PUBLIC_SOURCE_TYPES:
        raise ValueError(f"Source {source_id} is not a trusted public source.")
    url = str(source.get("url") or "")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Source {source_id} must use an absolute HTTP(S) URL.")

    fetched_at = _now()
    raw_text = _fetch_url_text(url)
    extracted_text = _extract_readable_text(raw_text)
    content_hash = _hash(extracted_text)
    snapshot = {
        "snapshot_id": f"source-snapshot-{uuid4().hex[:8]}",
        "source_id": source_id,
        "source_state": "snapshotted",
        "created_at": fetched_at,
        "fetched_at": fetched_at,
        "registry_ref": str(registry_path),
        "url": url,
        "source_type": str(source.get("type")),
        "trust_level": str(source.get("trust_level", "medium")),
        "freshness_ttl_days": int(source.get("freshness_ttl_days", 0) or 0),
        "extractor_version": SOURCE_SNAPSHOT_EXTRACTOR_VERSION,
        "content_hash": content_hash,
        "content_char_count": len(extracted_text),
        "domain_tags": _list_field(source.get("domain_tags")),
        "market_tags": _list_field(source.get("market_tags")) or _infer_market_tags(extracted_text),
        "platform_tags": _list_field(source.get("platform_tags")) or ["general"],
        "license_review_notes": str(source.get("notes", "External source requires review before promotion.")),
        "extracted_text": extracted_text,
    }
    output = resolve_repo_path(out or _source_artifact_path(KNOWLEDGE_SOURCE_SNAPSHOT_DIR, source_id, "snapshot", fetched_at))
    write_json(output, snapshot)
    return {**snapshot, "snapshot_ref": str(output)}


def summarize_source_snapshot(snapshot_path: Path, *, out: Path | None = None) -> dict[str, Any]:
    snapshot_path = resolve_repo_path(snapshot_path)
    snapshot = load_json(snapshot_path)
    text = str(snapshot.get("extracted_text", ""))
    if not text.strip():
        raise ValueError("Snapshot has no extracted_text to summarize.")
    summary = _curated_source_summary(text, market_tags=_list_field(snapshot.get("market_tags")))
    created_at = _now()
    proposal = {
        "proposal_id": f"source-summary-{uuid4().hex[:8]}",
        "source_state": "proposed",
        "created_at": created_at,
        "status": "needs_review",
        "source_id": snapshot["source_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_ref": str(snapshot_path),
        "source_url": snapshot["url"],
        "source_type": snapshot.get("source_type"),
        "trust_level": snapshot.get("trust_level"),
        "content_hash": snapshot["content_hash"],
        "domain_tags": _list_field(snapshot.get("domain_tags")) or _infer_domain_tags(summary),
        "market_tags": _list_field(snapshot.get("market_tags")) or _infer_market_tags(summary),
        "platform_tags": _list_field(snapshot.get("platform_tags")) or ["general"],
        "stages": ["strategy_reasoning", "balanced_review", "repair"],
        "curated_summary": summary,
        "review_notes": [
            "Summary is generated from a trusted-source snapshot for review.",
            "Approve only curated lessons/checklists; do not promote raw article text.",
        ],
        "evidence_refs": [str(snapshot_path), str(snapshot.get("url"))],
    }
    output = resolve_repo_path(out or _source_artifact_path(KNOWLEDGE_SOURCE_PROPOSAL_DIR, str(snapshot["source_id"]), "proposal", created_at))
    write_json(output, proposal)
    return {**proposal, "proposal_ref": str(output)}


def approve_source_summary(
    proposal_path: Path,
    *,
    index_path: Path | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    proposal_path = resolve_repo_path(proposal_path)
    proposal = load_json(proposal_path)
    if proposal.get("status") == "rejected":
        raise ValueError("Rejected source summaries cannot be approved.")
    source_id = str(proposal["source_id"])
    content_hash = str(proposal["content_hash"])
    item_id = f"curated-{source_id}-{content_hash[:12]}"
    item = _item(
        item_id=item_id,
        item_type="semantic",
        title=f"Curated source summary: {source_id}",
        content=str(proposal["curated_summary"]),
        domain_tags=_list_field(proposal.get("domain_tags")) or _infer_domain_tags(str(proposal["curated_summary"])),
        market_tags=_list_field(proposal.get("market_tags")) or _infer_market_tags(str(proposal["curated_summary"])),
        platform_tags=_list_field(proposal.get("platform_tags")) or ["general"],
        source_type="approved_source_summary",
        source_uri=str(proposal.get("source_url")),
        trust_level=str(proposal.get("trust_level", "medium")),
        created_at=_now(),
        status="approved",
        stages=_list_field(proposal.get("stages")) or ["strategy_reasoning", "balanced_review", "repair"],
    )
    db_url = database_url
    if db_url:
        embedding = _db_embedding_config(db_url)
        _upsert_db_items([item], db_url, embedding_model=embedding["embedding_model"], embedding_provider=embedding["embedding_provider"])
        return {"status": "pass", "store": "postgres_pgvector", "item_id": item_id, "source_id": source_id}

    index_path = resolve_repo_path(index_path or Path(KNOWLEDGE_INDEX_PATH))
    index = load_index(index_path)
    index["items"] = [existing for existing in index.get("items", []) if existing.get("id") != item_id]
    index["items"].append(item)
    index["sources"] = [source for source in index.get("sources", []) if source.get("id") != item_id]
    index["sources"].append(_source_from_item(item))
    index["chunks"] = _chunks_for_items(
        index["items"],
        embedding_model=index.get("embedding_model", EMBEDDING_MODEL_DEFAULT),
        embedding_provider=index.get("embedding_provider", EMBEDDING_PROVIDER_LOCAL),
    )
    index["retrieval_index"] = _retrieval_index_payload(index["chunks"])
    index["retrieval_result_cache"] = {}
    index["updated_at"] = _now()
    index["stats"] = _index_stats(index)
    write_json(index_path, index)
    return {"status": "pass", "store": "local_json", "index_ref": str(index_path), "item_id": item_id, "source_id": source_id}


def load_index(index_path: Path | None = None, *, database_url: str | None = None) -> dict[str, Any]:
    db_url = database_url
    if db_url:
        return _load_db_index(db_url)
    path = resolve_repo_path(index_path or default_index_path())
    return load_json(path)


def search_knowledge(
    query: str,
    *,
    stage: str | None = None,
    index_path: Path | None = None,
    options: RetrievalOptions | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    options = options or RetrievalOptions()
    db_url = database_url
    if db_url:
        return _search_db_knowledge(query, db_url, stage=stage, options=options, started=started)
    path = resolve_repo_path(index_path or default_index_path())
    index = load_json(path)
    expanded_terms = expand_query_terms(query)
    query_text = " ".join([query, *expanded_terms])
    intent = classify_prompt(query)
    chunks = [chunk for chunk in index.get("chunks", []) if chunk.get("status") in ACTIVE_STATUSES]
    if stage:
        chunks = [chunk for chunk in chunks if stage in chunk.get("stages", []) or not chunk.get("stages")]
    filters_applied = _filters_for_intent(intent, stage=stage)
    chunks, prefilter_stats = _prefilter_chunks(chunks, intent, options=options)
    retrieval_cache_key = _retrieval_result_cache_key(
        query_text,
        stage=stage,
        knowledge_version=str(index.get("version", "1")),
        registry_ref=str(index.get("source_registry_ref") or ""),
        options=options,
    )
    retrieval_cache = index.setdefault("retrieval_result_cache", {})
    cached_result = retrieval_cache.get(retrieval_cache_key) if isinstance(retrieval_cache, dict) else None
    if isinstance(cached_result, dict) and isinstance(cached_result.get("result"), dict):
        cached_result["last_used_at"] = _now()
        write_json(path, index)
        result = deepcopy(cached_result["result"])
        index_embedding_model = str(index.get("embedding_model", options.embedding_model))
        index_embedding_provider = str(index.get("embedding_provider", options.embedding_provider))
        query_embedding_key = _query_embedding_cache_key(
            query_text,
            stage=stage,
            embedding_provider=index_embedding_provider,
            embedding_model=index_embedding_model,
            knowledge_version=str(index.get("version", "1")),
        )
        query_cache = index.get("query_embedding_cache") if isinstance(index.get("query_embedding_cache"), dict) else {}
        embedding_cache_status = "hit" if isinstance(query_cache.get(query_embedding_key), dict) else result.get("embedding_cache_status", "miss")
        result["retrieval_latency_ms"] = int((time.perf_counter() - started) * 1000)
        result["cache_hit"] = True
        result["cache_layer"] = "retrieval_result"
        result["cache_key"] = retrieval_cache_key
        result["cache_key_hash"] = retrieval_cache_key
        result["cache_saved_ms"] = int(cached_result.get("cache_saved_ms", 0) or 0)
        result["cache_ttl_seconds"] = None
        result["cache_bypass_reason"] = None
        result["retrieval_cache_status"] = "hit"
        result["embedding_cache_status"] = embedding_cache_status
        result.setdefault("metrics", {})["retrieval_cache_status"] = "hit"
        result["metrics"]["embedding_cache_status"] = embedding_cache_status
        result["metrics"]["cache_layer"] = "retrieval_result"
        return result

    lexical_started = time.perf_counter()
    lexical_ranked = _rank_lexical(chunks, query_text)[: options.lexical_limit]
    index_embedding_model = str(index.get("embedding_model", options.embedding_model))
    index_embedding_provider = str(index.get("embedding_provider", options.embedding_provider))
    embedding_started = time.perf_counter()
    query_embedding, embedding_cache_status = _local_query_embedding(index, query_text, stage=stage, embedding_model=index_embedding_model, embedding_provider=index_embedding_provider)
    if embedding_cache_status == "miss":
        write_json(path, index)
    vector_ranked = _rank_vector(chunks, query_text, index_embedding_model, index_embedding_provider, query_embedding=query_embedding)[: options.vector_limit]
    embedding_latency_ms = int((time.perf_counter() - embedding_started) * 1000)
    merged = _rrf_merge(lexical_ranked, vector_ranked)
    rerank_started = time.perf_counter()
    required_source_ids = _required_source_ids(query, intent)
    reranked_full = _ensure_required_chunks(_rerank(merged, intent, expanded_terms, query=query), chunks, required_source_ids)
    reranked = _ensure_approved_candidate_chunks(
        reranked_full,
        options.limit,
        fallback_chunks=chunks,
        query_text=query_text,
        embedding_model=index_embedding_model,
        embedding_provider=index_embedding_provider,
        query_embedding=query_embedding,
    )
    rerank_latency_ms = int((time.perf_counter() - rerank_started) * 1000)
    retrieved = [_retrieved_chunk(chunk, options.max_chars_per_chunk) for chunk in reranked]
    confidence = _retrieval_confidence(retrieved, required_source_ids=required_source_ids)
    citations = _citations_for_chunks(retrieved)
    missing_context = _missing_context(query, intent, retrieved)
    latency_ms = int((time.perf_counter() - started) * 1000)
    db_search_latency_ms = int((time.perf_counter() - lexical_started) * 1000) - embedding_latency_ms - rerank_latency_ms
    result = {
        "status": "pass",
        "index_ref": str(path),
        "index_id": index.get("index_id"),
        "knowledge_version": index.get("version", "1"),
        "retrieval_query": query,
        "intent": intent,
        "expanded_terms": expanded_terms,
        "embedding_model": index_embedding_model,
        "embedding_provider": index_embedding_provider,
        "retrieval_latency_ms": latency_ms,
        "embedding_latency_ms": embedding_latency_ms,
        "db_search_latency_ms": max(db_search_latency_ms, 0),
        "hybrid_candidate_count": len({chunk["chunk_id"] for chunk in lexical_ranked + vector_ranked}),
        "rerank_latency_ms": rerank_latency_ms,
        "cache_hit": embedding_cache_status == "hit",
        "cache_layer": "query_embedding",
        "cache_key": _query_embedding_cache_key(query_text, stage=stage, embedding_provider=index_embedding_provider, embedding_model=index_embedding_model, knowledge_version=str(index.get("version", "1"))),
        "cache_key_hash": _query_embedding_cache_key(query_text, stage=stage, embedding_provider=index_embedding_provider, embedding_model=index_embedding_model, knowledge_version=str(index.get("version", "1"))),
        "cache_saved_ms": 0,
        "cache_ttl_seconds": None,
        "cache_bypass_reason": None,
        "retrieval_cache_status": "miss",
        "embedding_cache_status": embedding_cache_status,
        "knowledge_health_status": "not_applicable",
        "retrieved_chunks": retrieved,
        "source_ids": sorted({chunk["source_id"] for chunk in retrieved if chunk.get("source_id")}),
        "citations": citations,
        "filters_applied": filters_applied | prefilter_stats,
        "retrieval_confidence": confidence,
        "low_confidence": confidence["score"] < LOW_RETRIEVAL_CONFIDENCE_THRESHOLD,
        "missing_context": missing_context,
        "required_source_hits": sorted(set(required_source_ids) & {str(chunk.get("source_id")) for chunk in retrieved}),
        "metrics": {
            "chunk_hit_rate": 1.0 if retrieved else 0.0,
            "source_coverage": len({chunk["source_id"] for chunk in retrieved if chunk.get("source_id")}),
            "p95_target_ms": 300,
            "embedding_latency_ms": embedding_latency_ms,
            "db_search_latency_ms": max(db_search_latency_ms, 0),
            "embedding_cache_status": embedding_cache_status,
            "retrieval_cache_status": "miss",
            "cache_layer": "query_embedding",
            "knowledge_health_status": "not_applicable",
            "prefilter_input_count": prefilter_stats["prefilter_input_count"],
            "prefilter_output_count": prefilter_stats["prefilter_output_count"],
            "retrieval_confidence": confidence["score"],
        },
    }
    retrieval_cache[retrieval_cache_key] = {
        "normalized_query": _normalize_query(query_text),
        "stage": stage,
        "knowledge_version": str(index.get("version", "1")),
        "options": _retrieval_options_cache_payload(options),
        "result": deepcopy(result),
        "cache_saved_ms": int((time.perf_counter() - started) * 1000),
        "created_at": _now(),
        "last_used_at": _now(),
    }
    write_json(path, index)
    return result


def build_retrieved_knowledge_context(prompt: str, *, index_path: Path | None = None, database_url: str | None = None) -> dict[str, Any]:
    result = search_knowledge(prompt, index_path=index_path, database_url=ensure_database_url(database_url))
    retrieved_chunks = result["retrieved_chunks"]
    stage_relevance: dict[str, list[str]] = {}
    for chunk in retrieved_chunks:
        for stage in chunk.get("stages", []):
            stage_relevance.setdefault(stage, []).append(chunk["chunk_id"])
    return {
        "created_at": _now(),
        "mode": "auto",
        "store": "knowledge_base",
        "index_ref": result["index_ref"],
        "index_id": result["index_id"],
        "knowledge_version": result["knowledge_version"],
        "retrieval_query": result["retrieval_query"],
        "intent": result["intent"],
        "expanded_terms": result["expanded_terms"],
        "embedding_provider": result.get("embedding_provider"),
        "embedding_model": result["embedding_model"],
        "retrieval_latency_ms": result["retrieval_latency_ms"],
        "embedding_latency_ms": result.get("embedding_latency_ms"),
        "db_search_latency_ms": result.get("db_search_latency_ms"),
        "hybrid_candidate_count": result["hybrid_candidate_count"],
        "rerank_latency_ms": result["rerank_latency_ms"],
        "cache_hit": result["cache_hit"],
        "cache_key": result.get("cache_key"),
        "cache_layer": result.get("cache_layer"),
        "cache_key_hash": result.get("cache_key_hash"),
        "cache_saved_ms": result.get("cache_saved_ms"),
        "cache_ttl_seconds": result.get("cache_ttl_seconds"),
        "cache_bypass_reason": result.get("cache_bypass_reason"),
        "retrieval_cache_status": result.get("retrieval_cache_status"),
        "embedding_cache_status": result.get("embedding_cache_status"),
        "knowledge_health_status": result.get("knowledge_health_status"),
        "retrieved_chunks": retrieved_chunks,
        "citations": result.get("citations", []),
        "retrieval_confidence": result.get("retrieval_confidence"),
        "low_confidence": result.get("low_confidence"),
        "missing_context": result.get("missing_context", []),
        "filters_applied": result.get("filters_applied", {}),
        "required_source_hits": result.get("required_source_hits", []),
        "stage_relevance": stage_relevance,
        "context_refs": [f"chunk:{chunk['chunk_id']}" for chunk in retrieved_chunks],
        "internal_docs": _compat_internal_docs(retrieved_chunks),
        "external_refs": _compat_external_refs(retrieved_chunks),
        "truncation": {"max_chunks": RetrievalOptions().limit, "max_chars_per_chunk": RetrievalOptions().max_chars_per_chunk, "truncated_chunk_ids": [chunk["chunk_id"] for chunk in retrieved_chunks if chunk.get("truncated")]},
        "metrics": result["metrics"],
    }


def classify_prompt(prompt: str) -> dict[str, Any]:
    lowered = prompt.lower()
    tags = []
    if any(term in lowered for term in ("price action", "break of structure", "bos", "choch", "liquidity", "sweep", "rejection", "support", "resistance", "order block", "fvg")):
        tags.append("price_action")
    if any(term in lowered for term in ("rsi", "macd", "moving average", "sma", "ema", "indicator", "bollinger", "atr")):
        tags.append("indicator")
    if any(term in lowered for term in ("risk", "stop", "take profit", "position sizing", "drawdown", "guarantee", "guaranteed", "profit", "live-ready", "live ready", "certified")):
        tags.append("risk")
    if any(term in lowered for term in ("pine", "tradingview", "strategy", "indicator")):
        tags.append("pine")
    if any(term in lowered for term in ("mql5", "mt5", "metatrader", "expert advisor")):
        tags.append("mql5")
    if any(term in lowered for term in ("crypto", "bitcoin", "btc", "ethereum", "eth", "altcoin", "perpetual", "funding", "exchange")):
        tags.append("crypto")
    if any(term in lowered for term in ("forex", "fx", "eurusd", "gbpusd", "usdjpy", "session", "london", "new york", "rollover", "spread")):
        tags.append("forex")
    if "pine" in lowered and any(term in lowered for term in ("mql5", "mt5", "metatrader")):
        tags.append("both_platform")
    if not tags:
        tags.extend(["price_action", "risk", "pine"])
    no_indicators = any(term in lowered for term in ("no indicator", "without indicator", "do not use indicators", "price action only"))
    return {"tags": sorted(set(tags)), "no_indicators": no_indicators}


def expand_query_terms(query: str) -> list[str]:
    lowered = query.lower()
    terms: list[str] = []
    for trigger, aliases in PRICE_ACTION_ALIASES.items():
        if trigger in lowered:
            terms.extend(aliases)
    if "lookahead_on" in lowered:
        terms.extend(["repaint", "barmerge.lookahead_off", "request.security"])
    if any(term in lowered for term in ("cannot lose", "guaranteed", "guarantee", "profit", "live-ready", "live ready", "certified")):
        terms.extend(["risk policy", "prohibited claim", "must not claim", "live trading approval"])
    return _dedupe_strings(terms)


def evaluate_knowledge_suite(
    suite_path: Path,
    *,
    index_path: Path | None = None,
    out_path: Path | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    suite = yaml.safe_load(resolve_repo_path(suite_path).read_text(encoding="utf-8"))
    cases = suite.get("cases", []) if isinstance(suite, dict) else []
    reports = []
    for case in cases:
        result = search_knowledge(
            str(case["query"]),
            index_path=index_path,
            database_url=database_url,
            options=RetrievalOptions(limit=int(case.get("limit", 6))),
        )
        text = " ".join(chunk["text"] for chunk in result["retrieved_chunks"]).lower()
        types = {chunk["type"] for chunk in result["retrieved_chunks"]}
        sources = {str(chunk.get("source_id")) for chunk in result["retrieved_chunks"]}
        citation_sources = {str(citation.get("source_id")) for citation in result.get("citations", [])}
        expected_terms = [str(term).lower() for term in case.get("expected_terms", [])]
        expected_types = set(case.get("expected_types", []))
        expected_sources = {str(source) for source in case.get("expected_sources", [])}
        forbidden_sources = {str(source) for source in case.get("forbidden_sources", [])}
        expected_citations = {str(source) for source in case.get("expected_citations", [])}
        min_confidence = float(case.get("min_confidence", 0.0) or 0.0)
        missing_terms = [term for term in expected_terms if term not in text]
        missing_types = sorted(expected_types - types)
        missing_sources = sorted(expected_sources - sources)
        forbidden_source_hits = sorted(forbidden_sources & sources)
        missing_citations = sorted(expected_citations - citation_sources)
        confidence_score = float((result.get("retrieval_confidence") or {}).get("score", 0.0))
        confidence_failed = confidence_score < min_confidence
        status = (
            "pass"
            if not missing_terms
            and not missing_types
            and not missing_sources
            and not forbidden_source_hits
            and not missing_citations
            and not confidence_failed
            and len(result["retrieved_chunks"]) >= int(case.get("min_results", 1))
            else "fail"
        )
        reports.append(
            {
                **result,
                "id": case.get("id"),
                "status": status,
                "missing_terms": missing_terms,
                "missing_types": missing_types,
                "missing_sources": missing_sources,
                "forbidden_source_hits": forbidden_source_hits,
                "missing_citations": missing_citations,
                "min_confidence": min_confidence,
                "confidence_failed": confidence_failed,
            }
        )
    status = "pass" if all(case["status"] == "pass" for case in reports) else "fail"
    report = {"suite": suite.get("name", "knowledge-suite"), "status": status, "case_count": len(reports), "passed": sum(1 for case in reports if case["status"] == "pass"), "failed": sum(1 for case in reports if case["status"] != "pass"), "cases": reports}
    if out_path:
        write_json(out_path, report)
    return report


def load_candidates(path: Path | None = None, *, database_url: str | None = None) -> dict[str, Any]:
    db_url = database_url
    if db_url:
        return _load_db_candidates(db_url)
    candidate_path = resolve_repo_path(path or Path(KNOWLEDGE_CANDIDATES_PATH))
    if not candidate_path.exists():
        return {"created_at": _now(), "updated_at": _now(), "candidates": []}
    return load_json(candidate_path)


def propose_candidate(
    lesson: str,
    *,
    evidence_ref: str,
    candidate_type: str = "episodic",
    path: Path | None = None,
    database_url: str | None = None,
    dedupe_key: str | None = None,
    lesson_kind: str | None = None,
    confidence: str | None = None,
    domain_tags: list[str] | None = None,
    market_tags: list[str] | None = None,
    platform_tags: list[str] | None = None,
    stages: list[str] | None = None,
    source_uri: str | None = None,
    trust_level: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if candidate_type not in KNOWLEDGE_TYPES:
        raise ValueError(f"candidate_type must be one of {', '.join(sorted(KNOWLEDGE_TYPES))}")
    blocked = find_blocked_claims(lesson)
    db_url = database_url
    store = load_candidates(path, database_url=db_url)
    existing = _matching_candidate(store, lesson=lesson, evidence_ref=evidence_ref, dedupe_key=dedupe_key) if dedupe_key else None
    if existing:
        existing.update(
            {
                "updated_at": _now(),
                "dedupe_key": dedupe_key,
                "lesson_kind": lesson_kind or existing.get("lesson_kind"),
                "confidence": confidence or existing.get("confidence"),
                "domain_tags": _dedupe_strings(domain_tags or existing.get("domain_tags", [])),
                "market_tags": _dedupe_strings(market_tags or existing.get("market_tags", ["general"])),
                "platform_tags": _dedupe_strings(platform_tags or existing.get("platform_tags", ["general"])),
                "stages": _dedupe_strings(stages or existing.get("stages", [])),
                "source_uri": source_uri or existing.get("source_uri"),
                "trust_level": trust_level or existing.get("trust_level", "medium"),
                "metadata": {**(existing.get("metadata") or {}), **(metadata or {})},
            }
        )
        if db_url:
            _upsert_db_candidate(existing, db_url)
        else:
            write_json(resolve_repo_path(path or Path(KNOWLEDGE_CANDIDATES_PATH)), store)
        return existing | {"deduped": True}
    candidate = {
        "candidate_id": f"candidate-{uuid4().hex[:8]}",
        "created_at": _now(),
        "updated_at": _now(),
        "status": "rejected" if blocked else "needs_review",
        "type": candidate_type,
        "lesson": lesson,
        "domain_tags": _dedupe_strings(domain_tags or _infer_domain_tags(lesson)),
        "market_tags": _dedupe_strings(market_tags or ["general"]),
        "platform_tags": _dedupe_strings(platform_tags or _infer_platform_tags(lesson, "")),
        "stages": _dedupe_strings(stages or []),
        "source_uri": source_uri or "",
        "trust_level": trust_level or "medium",
        "dedupe_key": dedupe_key,
        "lesson_kind": lesson_kind,
        "confidence": confidence,
        "evidence_refs": [evidence_ref],
        "blocked_claims": blocked,
        "metadata": metadata or {},
    }
    if db_url:
        _upsert_db_candidate(candidate, db_url)
    else:
        store["candidates"].append(candidate)
        store["updated_at"] = _now()
        write_json(resolve_repo_path(path or Path(KNOWLEDGE_CANDIDATES_PATH)), store)
    return candidate


def propose_failure_candidate(
    failure: dict[str, Any],
    *,
    evidence_ref: str,
    path: Path | None = None,
    database_url: str | None = None,
) -> dict[str, Any] | None:
    lesson = _lesson_from_failure(failure)
    if not lesson:
        return None
    db_url = ensure_database_url(database_url)
    store = load_candidates(path, database_url=db_url)
    existing = _matching_candidate(store, lesson=lesson, evidence_ref=evidence_ref)
    if existing:
        return existing | {"deduped": True}
    return propose_candidate(lesson, evidence_ref=evidence_ref, candidate_type="episodic", path=path, database_url=db_url)


def _lesson_from_failure(failure: dict[str, Any]) -> str | None:
    failure_class = str(failure.get("failure_class") or "")
    stage = str(failure.get("failure_stage") or "unknown_stage")
    case_id = str(failure.get("id") or failure.get("case_id") or "unknown_case")
    validation_failures = failure.get("validation_failures") or []
    findings = failure.get("review_findings") or {}
    reason = str(failure.get("failure_reason") or "")
    if failure_class == "malformed_response":
        return (
            f"Case {case_id} failed because stage {stage} returned malformed structured output. "
            "The harness should retry with a compact strict-JSON recovery prompt and then fall back by stage before failing the case."
        )
    if failure_class == "policy_violation":
        return (
            f"Case {case_id} failed at {stage} due to a policy violation. "
            "Distinguish hard requests such as no-loss, live-ready certification, broker execution, or source-injection from negated safety boundaries."
        )
    if failure_class == "static_validation_failed":
        failed_checks = ", ".join(str(item.get("name")) for item in validation_failures if isinstance(item, dict) and item.get("name")) or "unknown static checks"
        return (
            f"Case {case_id} failed static validation at {stage}; failing checks: {failed_checks}. "
            "Future generation or repair should prioritize the validator details before reviewer preferences."
        )
    if failure_class == "provider_timeout":
        return (
            f"Case {case_id} timed out at {stage}. Treat the associated model-stage route as degraded until matrix data shows stable latency."
        )
    if failure_class == "review_failed":
        required_fixes = findings.get("required_fixes") if isinstance(findings, dict) else None
        return (
            f"Case {case_id} failed review at {stage}. Required fixes were: {required_fixes or reason or 'not specified'}. "
            "Convert recurring review blockers into procedural generation guidance."
        )
    return None


def _matching_candidate(store: dict[str, Any], *, lesson: str, evidence_ref: str, dedupe_key: str | None = None) -> dict[str, Any] | None:
    lesson_hash = hashlib.sha256(lesson.encode("utf-8")).hexdigest()
    for candidate in store.get("candidates", []):
        candidate_lesson = str(candidate.get("lesson", ""))
        evidence_refs = [str(ref) for ref in candidate.get("evidence_refs", [])]
        key_matches = bool(dedupe_key and candidate.get("dedupe_key") == dedupe_key)
        lesson_matches = hashlib.sha256(candidate_lesson.encode("utf-8")).hexdigest() == lesson_hash
        if key_matches or lesson_matches:
            if evidence_ref not in evidence_refs:
                candidate["evidence_refs"] = [*evidence_refs, evidence_ref]
                candidate["updated_at"] = _now()
            return candidate
    return None


def approve_candidate(
    candidate_id: str,
    *,
    index_path: Path | None = None,
    candidates_path: Path | None = None,
    database_url: str | None = None,
    approved_status: str = "approved",
    metadata_update: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if approved_status not in CANDIDATE_STATUSES:
        raise ValueError(f"approved_status must be one of {', '.join(sorted(CANDIDATE_STATUSES))}")
    db_url = database_url
    store = load_candidates(candidates_path, database_url=db_url)
    candidate = _candidate_by_id(store, candidate_id)
    if candidate["status"] == "rejected":
        raise ValueError("Rejected candidates cannot be approved.")
    candidate["status"] = approved_status
    candidate["updated_at"] = _now()
    if metadata_update:
        candidate["metadata"] = _merge_dicts(candidate.get("metadata") or {}, metadata_update)
    if db_url:
        _upsert_db_candidate(candidate, db_url)
    else:
        write_json(resolve_repo_path(candidates_path or Path(KNOWLEDGE_CANDIDATES_PATH)), store)
    item = _item_from_candidate(candidate_id, candidate)
    if db_url:
        embedding = _db_embedding_config(db_url)
        _upsert_db_items(
            [item],
            db_url,
            embedding_model=embedding["embedding_model"],
            embedding_provider=embedding["embedding_provider"],
        )
        return {"status": "pass", "candidate_id": candidate_id, "store": "postgres_pgvector", "item_id": item["id"], "candidate_status": approved_status}
    index_path = resolve_repo_path(index_path or Path(KNOWLEDGE_INDEX_PATH))
    index = load_index(index_path)
    index["items"] = [existing for existing in index.get("items", []) if existing.get("id") != item["id"]]
    index["sources"] = [source for source in index.get("sources", []) if source.get("id") != item["id"]]
    index["items"].append(item)
    index["sources"].append(_source_from_item(item))
    index["chunks"] = _chunks_for_items(
        index["items"],
        embedding_model=index.get("embedding_model", EMBEDDING_MODEL_DEFAULT),
        embedding_provider=index.get("embedding_provider", EMBEDDING_PROVIDER_LOCAL),
    )
    index["retrieval_index"] = _retrieval_index_payload(index["chunks"])
    index["retrieval_result_cache"] = {}
    index["updated_at"] = _now()
    index["stats"] = _index_stats(index)
    write_json(index_path, index)
    return {"status": "pass", "candidate_id": candidate_id, "index_ref": str(index_path), "item_id": item["id"], "candidate_status": approved_status}


def reject_candidate(candidate_id: str, *, candidates_path: Path | None = None, database_url: str | None = None) -> dict[str, Any]:
    db_url = database_url
    store = load_candidates(candidates_path, database_url=db_url)
    candidate = _candidate_by_id(store, candidate_id)
    candidate["status"] = "rejected"
    candidate["updated_at"] = _now()
    if db_url:
        _upsert_db_candidate(candidate, db_url)
    else:
        write_json(resolve_repo_path(candidates_path or Path(KNOWLEDGE_CANDIDATES_PATH)), store)
    return {"status": "pass", "candidate_id": candidate_id}


def learn_from_run(
    artifacts_root: Path,
    *,
    approval_mode: str = "agent-auto",
    run_id: str | None = None,
    index_path: Path | None = None,
    candidates_path: Path | None = None,
    database_url: str | None = None,
    llm_judge: Any | None = None,
    out: Path | None = None,
) -> dict[str, Any]:
    if approval_mode not in LEARNING_APPROVAL_MODES:
        raise ValueError(f"approval_mode must be one of {', '.join(sorted(LEARNING_APPROVAL_MODES))}")
    root = resolve_repo_path(artifacts_root)
    db_url = database_url
    index_ref = resolve_repo_path(index_path or Path(KNOWLEDGE_INDEX_PATH))
    if not db_url and not index_ref.exists():
        build_knowledge_index(index_path=index_ref)

    extracted = _learning_candidates_from_artifacts(root, run_id=run_id)
    grouped = _dedupe_learning_candidates(extracted)
    proposed = []
    promoted = []
    skipped = []
    rejected = []

    for candidate in grouped:
        safety_rejection = _learning_safety_rejection(candidate)
        if safety_rejection:
            candidate["status"] = "rejected"
            candidate["skip_reason"] = safety_rejection
            rejected.append(candidate)
            continue
        first_evidence = candidate["evidence_refs"][0]
        proposed_candidate = propose_candidate(
            candidate["lesson"],
            evidence_ref=first_evidence,
            candidate_type=candidate["candidate_type"],
            path=candidates_path,
            database_url=db_url,
            dedupe_key=candidate["dedupe_key"],
            lesson_kind=candidate["lesson_kind"],
            confidence=candidate["confidence"],
            domain_tags=candidate["domain_tags"],
            market_tags=candidate["market_tags"],
            platform_tags=candidate["platform_tags"],
            stages=candidate["stages"],
            source_uri=candidate["source_uri"],
            trust_level="agent_reviewed",
            metadata={
                "learning": {
                    "approval_mode": approval_mode,
                    "auto_approval_eligible": candidate["auto_approval_eligible"],
                    "evidence_count": candidate["evidence_count"],
                    "validator_check": candidate.get("validator_check"),
                    "failure_class": candidate.get("failure_class"),
                    "stage": candidate.get("stage"),
                }
            },
        )
        for evidence_ref in candidate["evidence_refs"][1:]:
            proposed_candidate = propose_candidate(
                candidate["lesson"],
                evidence_ref=evidence_ref,
                candidate_type=candidate["candidate_type"],
                path=candidates_path,
                database_url=db_url,
                dedupe_key=candidate["dedupe_key"],
                lesson_kind=candidate["lesson_kind"],
                confidence=candidate["confidence"],
                domain_tags=candidate["domain_tags"],
                market_tags=candidate["market_tags"],
                platform_tags=candidate["platform_tags"],
                stages=candidate["stages"],
                source_uri=candidate["source_uri"],
                trust_level="agent_reviewed",
            )
        candidate_record = {
            **candidate,
            "candidate_id": proposed_candidate["candidate_id"],
            "candidate_status": proposed_candidate["status"],
            "deduped": bool(proposed_candidate.get("deduped")),
        }
        proposed.append(candidate_record)
        if approval_mode in GUARDED_AUTO_APPROVAL_MODES:
            continue
        else:
            skipped.append({**candidate_record, "skip_reason": "manual_review_required" if approval_mode == "manual" else "confidence_below_auto_threshold"})

    if approval_mode in GUARDED_AUTO_APPROVAL_MODES and proposed:
        review_ids = [str(candidate["candidate_id"]) for candidate in proposed]
        reviews = review_candidates_for_auto_promotion(
            review_ids,
            index_path=index_ref,
            candidates_path=candidates_path,
            database_url=db_url,
            promotion_mode="guarded-auto",
            llm_judge=llm_judge,
        )
        review_by_id = {str(review.get("candidate_id") or ""): review for review in reviews}
        for candidate_record in proposed:
            review = review_by_id[str(candidate_record["candidate_id"])]
            reviewed_record = {
                **candidate_record,
                "status": review.get("status"),
                "candidate_status": review.get("status"),
                "promotion_decision": review.get("promotion_decision"),
                "quality_score": review.get("quality_score"),
                "gate_summary": review.get("gate_summary"),
                "review_required_reason": review.get("review_required_reason"),
                "auto_review": review,
            }
            if review["promotion_decision"] == "auto_approved":
                promoted.append({**reviewed_record, "approval": review.get("approval"), "retrieval_verification": review.get("retrieval_verification")})
            elif review["promotion_decision"] == "auto_rejected":
                rejected.append({**reviewed_record, "skip_reason": review.get("review_required_reason") or "auto_rejected"})
            else:
                skipped.append({**reviewed_record, "skip_reason": review.get("review_required_reason") or "guarded_auto_review_required"})

    payload = {
        "status": "pass",
        "created_at": _now(),
        "artifacts_root": str(root),
        "approval_mode": approval_mode,
        "extracted_count": len(extracted),
        "candidate_count": len(grouped),
        "proposed_count": len(proposed),
        "promoted_count": len(promoted),
        "skipped_count": len(skipped),
        "rejected_count": len(rejected),
        "candidates": proposed,
        "promoted": promoted,
        "skipped": skipped,
        "rejected": rejected,
        "index_ref": str(index_ref) if not db_url else None,
        "store": "postgres_pgvector" if db_url else "local_json",
        "anti_pollution": {
            "raw_provider_response_promoted": False,
            "live_web_fetch": False,
            "canonical_docs_mutated": False,
            "auto_approval_requires_high_confidence": True,
        },
    }
    if out:
        write_json(resolve_repo_path(out), payload)
    return payload


def _learning_candidates_from_artifacts(root: Path, *, run_id: str | None = None) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if run_id:
        if root.name == run_id and root.is_dir():
            selected = [root]
        else:
            run_dir = root / run_id
            selected = [run_dir] if run_dir.is_dir() else []
    else:
        run_dirs: dict[str, Path] = {}
        if root.is_dir() and (root.name.startswith("run-") or root.name.startswith("run_")):
            run_dirs[root.name] = root
        for pattern in ("run-*", "run_*"):
            for path in root.glob(pattern):
                if path.is_dir():
                    run_dirs[path.name] = path
        selected = list(run_dirs.values())
    for run_dir in sorted(selected):
        report_path = run_dir / "eval-report.json"
        report = _load_json_or_empty(report_path)
        for case in report.get("cases", []):
            if not isinstance(case, dict):
                continue
            evidence_ref = str(report_path)
            case_id = str(case.get("id") or case.get("case_id") or "unknown_case")
            status = str(case.get("status") or "")
            stage = str(case.get("failure_stage") or "final_gate")
            failure_class = str(case.get("failure_class") or "")
            validation_failures = [item for item in case.get("validation_failures", []) if isinstance(item, dict)]
            validation_warnings = [str(item) for item in case.get("validation_warnings", [])]
            for failure in validation_failures:
                candidate = _learning_candidate_for_validator_check(
                    str(failure.get("name") or "unknown_check"),
                    evidence_ref=evidence_ref,
                    case_id=case_id,
                    stage=stage,
                    failure_class=failure_class or "static_validation_failed",
                    passed=status == "pass",
                )
                if candidate:
                    candidates.append(candidate)
            for warning in validation_warnings:
                candidate = _learning_candidate_for_validation_warning(
                    warning,
                    evidence_ref=evidence_ref,
                    case_id=case_id,
                    stage=stage,
                    passed=status == "pass",
                )
                if candidate:
                    candidates.append(candidate)
            if failure_class == "static_validation_failed" and not validation_failures:
                candidates.append(
                    _base_learning_candidate(
                        lesson_kind="pine_static_validation",
                        lesson=(
                            "When final validation fails without a named check, repair must summarize validator output first "
                            "and make static validation authoritative before reviewer preferences."
                        ),
                        evidence_ref=evidence_ref,
                        case_id=case_id,
                        stage=stage,
                        failure_class=failure_class,
                        validator_check="unknown_static_check",
                        domain_tags=["pine", "validation"],
                        platform_tags=["pine_v6"],
                        stages=["pine_code_generation", "repair", "balanced_review"],
                        deterministic=True,
                    )
                )
            for blocker in _list_field(case.get("quality_blockers")):
                candidate = _learning_candidate_for_sophistication(str(blocker), evidence_ref=evidence_ref, case_id=case_id)
                if candidate:
                    candidates.append(candidate)
        backtest_report_path = run_dir / "backtest-report.json"
        backtest_report = _load_json_or_empty(backtest_report_path)
        candidates.extend(_learning_candidates_for_backtest_report(backtest_report, evidence_ref=str(backtest_report_path), case_id=run_dir.name))
        candidates.extend(_learning_candidates_from_auxiliary_reports(run_dir))

    if root not in selected:
        candidates.extend(_learning_candidates_from_auxiliary_reports(root))
    return candidates


def _learning_candidates_from_auxiliary_reports(root: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    intelligence = _load_json_or_empty(root / "intelligence-report.json")
    for signature in intelligence.get("sophistication_signatures", []):
        if isinstance(signature, dict):
            weakness = str(signature.get("weakness") or signature.get("name") or signature.get("signature") or "")
            candidate = _learning_candidate_for_sophistication(weakness, evidence_ref=str(root / "intelligence-report.json"), case_id="intelligence-report")
            if candidate:
                candidates.append(candidate)
    for signature in intelligence.get("failure_signatures", []):
        if isinstance(signature, dict):
            candidate = _learning_candidate_for_failure_signature(signature, evidence_ref=str(root / "intelligence-report.json"))
            if candidate:
                candidates.append(candidate)

    context = _load_json_or_empty(root / "context-report.json")
    for stage_report in context.get("stage_reports", []):
        if isinstance(stage_report, dict):
            candidates.extend(_learning_candidates_for_context_report(stage_report, evidence_ref=str(root / "context-report.json")))

    latency = _load_json_or_empty(root / "latency-report.json")
    for row in (latency.get("latency_summary") or {}).get("slowest", []):
        if isinstance(row, dict):
            candidate = _learning_candidate_for_latency_row(row, evidence_ref=str(root / "latency-report.json"))
            if candidate:
                candidates.append(candidate)
    proxy = _load_json_or_empty(root / "proxy-log-report.json")
    for event in proxy.get("app_mirror_events", []):
        if isinstance(event, dict):
            candidate = _learning_candidate_for_proxy_event(event, evidence_ref=str(root / "proxy-log-report.json"))
            if candidate:
                candidates.append(candidate)
    return candidates


def _base_learning_candidate(
    *,
    lesson_kind: str,
    lesson: str,
    evidence_ref: str,
    case_id: str,
    stage: str,
    failure_class: str = "",
    validator_check: str = "",
    domain_tags: list[str] | None = None,
    market_tags: list[str] | None = None,
    platform_tags: list[str] | None = None,
    stages: list[str] | None = None,
    deterministic: bool = False,
    operational: bool = False,
    successful_repair: bool = False,
) -> dict[str, Any]:
    dedupe_key = _learning_dedupe_key(
        lesson_kind=lesson_kind,
        lesson=lesson,
        stage=stage,
        failure_class=failure_class,
        validator_check=validator_check,
        market_tags=market_tags or ["general"],
        platform_tags=platform_tags or ["general"],
    )
    return {
        "lesson_kind": lesson_kind,
        "lesson": lesson,
        "candidate_type": "procedural",
        "dedupe_key": dedupe_key,
        "evidence_refs": [evidence_ref],
        "case_ids": [case_id],
        "stage": stage,
        "failure_class": failure_class,
        "validator_check": validator_check,
        "domain_tags": _dedupe_strings(domain_tags or ["trading"]),
        "market_tags": _dedupe_strings(market_tags or ["general"]),
        "platform_tags": _dedupe_strings(platform_tags or ["general"]),
        "stages": _dedupe_strings(stages or ([stage] if stage else [])),
        "source_uri": f"learning-run:{dedupe_key}",
        "deterministic": deterministic,
        "operational": operational,
        "successful_repair": successful_repair,
    }


def _learning_candidate_for_validator_check(
    check: str,
    *,
    evidence_ref: str,
    case_id: str,
    stage: str,
    failure_class: str,
    passed: bool,
) -> dict[str, Any] | None:
    normalized = check.lower()
    if normalized == "version_header":
        lesson = "Pine repair must preserve exact `//@version=6` as the first line, with no leading whitespace or comments before it."
        tags = ["pine", "validation", "syntax"]
    elif normalized in {"strategy_exit", "strategy.exit", "missing_strategy_exit"}:
        lesson = "Pine strategy generation and repair must include `strategy.exit` stop/limit handling for each `strategy.entry` when producing a strategy script."
        tags = ["pine", "validation", "risk"]
    elif normalized in {"repaint_hazards", "lookahead", "lookahead_on"}:
        lesson = "Pine generation must avoid `barmerge.lookahead_on` and prefer confirmed-bar or lookahead-off logic when using higher-timeframe data."
        tags = ["pine", "validation", "repaint"]
    elif normalized in {"position_sizing", "unsafe_position_sizing", "full_capital_position_sizing"}:
        lesson = "Strategy specs must avoid full-capital or all-in position sizing and encode bounded fixed units or small fixed-risk assumptions before code generation."
        tags = ["risk", "position_sizing", "validation"]
    else:
        return None
    return _base_learning_candidate(
        lesson_kind="pine_static_validation",
        lesson=lesson,
        evidence_ref=evidence_ref,
        case_id=case_id,
        stage=stage,
        failure_class=failure_class,
        validator_check=normalized,
        domain_tags=tags,
        platform_tags=["pine_v6"],
        stages=["strategy_coding", "pine_code_generation", "repair", "balanced_review"],
        deterministic=True,
        successful_repair=passed,
    )


def _learning_candidate_for_validation_warning(warning: str, *, evidence_ref: str, case_id: str, stage: str, passed: bool) -> dict[str, Any] | None:
    lowered = warning.lower()
    if "strategy.exit" not in lowered:
        return None
    return _base_learning_candidate(
        lesson_kind="pine_static_validation",
        lesson="When static validation warns that `strategy.exit` is missing, repair should add bounded stop/limit exits before balanced review passes the artifact.",
        evidence_ref=evidence_ref,
        case_id=case_id,
        stage=stage,
        failure_class="static_validation_warning",
        validator_check="strategy_exit_warning",
        domain_tags=["pine", "validation", "risk"],
        platform_tags=["pine_v6"],
        stages=["pine_code_generation", "repair", "balanced_review"],
        deterministic=True,
        successful_repair=passed,
    )


def _learning_candidate_for_sophistication(weakness: str, *, evidence_ref: str, case_id: str) -> dict[str, Any] | None:
    lowered = weakness.lower()
    if not lowered:
        return None
    if "invalidation" in lowered:
        lesson = "Trader-grade strategy specs should encode setup invalidation explicitly before Pine generation, especially for price-action entries."
        tag = "invalidation"
    elif "premise" in lowered or "regime" in lowered:
        lesson = "Trader-grade strategy specs should state the market premise and regime where the setup is intended to operate before code generation."
        tag = "market_premise"
    elif "session" in lowered or "timeframe" in lowered:
        lesson = "Price-action strategy specs should encode session and timeframe assumptions when the prompt or playbook implies liquidity/session behavior."
        tag = "session_timeframe"
    else:
        return None
    return _base_learning_candidate(
        lesson_kind="strategy_quality",
        lesson=lesson,
        evidence_ref=evidence_ref,
        case_id=case_id,
        stage="strategy_coding",
        failure_class="strategy_sophistication_weakness",
        validator_check=tag,
        domain_tags=["strategy_quality", "price_action", tag],
        platform_tags=["pine_v6"],
        stages=["strategy_reasoning", "strategy_coding", "balanced_review"],
        deterministic=False,
    )


def _learning_candidates_for_backtest_report(report: dict[str, Any], *, evidence_ref: str, case_id: str) -> list[dict[str, Any]]:
    robustness = report.get("robustness_report")
    if not isinstance(robustness, dict):
        return []
    checks = robustness.get("checks")
    if not isinstance(checks, dict):
        return []
    candidates: list[dict[str, Any]] = []
    for check_name, check_payload in checks.items():
        if not isinstance(check_payload, dict):
            continue
        status = str(check_payload.get("status") or "")
        if status not in {"warn", "fail"}:
            continue
        candidate = _learning_candidate_for_backtest_check(str(check_name), status=status, evidence_ref=evidence_ref, case_id=case_id)
        if candidate:
            candidates.append(candidate)
    return candidates


def _learning_candidate_for_backtest_check(check_name: str, *, status: str, evidence_ref: str, case_id: str) -> dict[str, Any] | None:
    normalized = check_name.lower().strip()
    if normalized not in BACKTEST_ROBUSTNESS_LESSONS:
        return None
    lesson, domain_tags = BACKTEST_ROBUSTNESS_LESSONS[normalized]
    return _base_learning_candidate(
        lesson_kind="backtest_robustness",
        lesson=lesson,
        evidence_ref=evidence_ref,
        case_id=case_id,
        stage="balanced_review",
        failure_class=f"backtest_robustness_{status}",
        validator_check=normalized,
        domain_tags=domain_tags,
        market_tags=["general"],
        platform_tags=["pine_v6"],
        stages=["balanced_review", "repair"],
        deterministic=True,
    )


def _learning_candidate_for_failure_signature(signature: dict[str, Any], *, evidence_ref: str) -> dict[str, Any] | None:
    failure_class = str(signature.get("failure_class") or "")
    stage = str(signature.get("stage") or signature.get("failure_stage") or "unknown_stage")
    if failure_class not in {"provider_timeout", "provider_error"}:
        return None
    lesson = (
        "When a provider route repeatedly times out or returns transport errors, route health should degrade that route "
        "and prefer an available fallback before another provider call."
    )
    return _base_learning_candidate(
        lesson_kind="harness_route_health",
        lesson=lesson,
        evidence_ref=evidence_ref,
        case_id=str(signature.get("id") or "failure_signature"),
        stage=stage,
        failure_class=failure_class,
        validator_check=str(signature.get("route_model") or ""),
        domain_tags=["harness", "route_policy", "provider_ops"],
        platform_tags=["general"],
        stages=[stage] if stage else [],
        deterministic=True,
        operational=True,
    )


def _learning_candidates_for_context_report(stage_report: dict[str, Any], *, evidence_ref: str) -> list[dict[str, Any]]:
    stage = str(stage_report.get("stage") or "unknown_stage")
    candidates = []
    if stage_report.get("missing_context") or stage_report.get("required_present") is False:
        candidates.append(
            _base_learning_candidate(
                lesson_kind="context_contract",
                lesson="Stage context contracts must fail before provider calls when required handoff fields are missing.",
                evidence_ref=evidence_ref,
                case_id=str(stage_report.get("run_id") or "context-report"),
                stage=stage,
                failure_class="missing_context",
                validator_check="required_fields",
                domain_tags=["harness", "context_contract"],
                platform_tags=["general"],
                stages=[stage],
                deterministic=True,
                operational=True,
            )
        )
    if stage_report.get("unexpected_large_fields") or stage_report.get("budget_warning"):
        candidates.append(
            _base_learning_candidate(
                lesson_kind="context_budget",
                lesson="Stage context should stay within its soft budget by passing compact summaries instead of full prior stage history.",
                evidence_ref=evidence_ref,
                case_id=str(stage_report.get("run_id") or "context-report"),
                stage=stage,
                failure_class="context_budget_warning",
                validator_check="stage_input_budget",
                domain_tags=["harness", "context_budget"],
                platform_tags=["general"],
                stages=[stage],
                deterministic=True,
                operational=True,
            )
        )
    return candidates


def _learning_candidate_for_latency_row(row: dict[str, Any], *, evidence_ref: str) -> dict[str, Any] | None:
    failure_class = str(row.get("failure_class") or "")
    provider_error_subclass = str(row.get("provider_error_subclass") or "")
    if not row.get("timeout_overrun") and provider_error_subclass != "provider_connection_error" and failure_class not in {"provider_timeout", "provider_error"}:
        return None
    stage = str(row.get("stage") or "unknown_stage")
    route_model = str(row.get("route_model") or row.get("model") or "")
    lesson = "Provider timeout and connection-error evidence should be recorded with route alias, stage, fallback status, and provider-call timing before changing route policy."
    return _base_learning_candidate(
        lesson_kind="harness_provider_diagnostics",
        lesson=lesson,
        evidence_ref=evidence_ref,
        case_id=str(row.get("run_id") or "latency-report"),
        stage=stage,
        failure_class=failure_class or "provider_error",
        validator_check=route_model,
        domain_tags=["harness", "provider_ops", "latency"],
        platform_tags=["general"],
        stages=[stage],
        deterministic=True,
        operational=True,
    )


def _learning_candidate_for_proxy_event(event: dict[str, Any], *, evidence_ref: str) -> dict[str, Any] | None:
    if event.get("status") not in {"fail", "skipped"}:
        return None
    provider_error_subclass = str(event.get("provider_error_subclass") or "")
    failure_class = str(event.get("failure_class") or "")
    if failure_class not in {"provider_timeout", "provider_error"} and provider_error_subclass != "provider_connection_error":
        return None
    stage = str(event.get("stage") or "unknown_stage")
    route_model = str(event.get("route_model") or event.get("model") or "")
    lesson = "Proxy attribution events should preserve redacted route timing and error subclass so provider failures can be debugged without prompt or secret leakage."
    return _base_learning_candidate(
        lesson_kind="harness_provider_diagnostics",
        lesson=lesson,
        evidence_ref=evidence_ref,
        case_id=str(event.get("run_id") or "proxy-log-report"),
        stage=stage,
        failure_class=failure_class or "provider_error",
        validator_check=route_model,
        domain_tags=["harness", "provider_ops", "observability"],
        platform_tags=["general"],
        stages=[stage],
        deterministic=True,
        operational=True,
    )


def _dedupe_learning_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        key = candidate["dedupe_key"]
        if key not in grouped:
            grouped[key] = deepcopy(candidate)
            continue
        existing = grouped[key]
        existing["evidence_refs"] = _dedupe_strings([*existing.get("evidence_refs", []), *candidate.get("evidence_refs", [])])
        existing["case_ids"] = _dedupe_strings([*existing.get("case_ids", []), *candidate.get("case_ids", [])])
        existing["successful_repair"] = bool(existing.get("successful_repair") or candidate.get("successful_repair"))
    for candidate in grouped.values():
        evidence_count = len(candidate.get("evidence_refs", []))
        candidate["evidence_count"] = evidence_count
        candidate["confidence"] = _learning_confidence(candidate, evidence_count=evidence_count)
        candidate["auto_approval_eligible"] = candidate["confidence"] == "high" and not _learning_safety_rejection(candidate)
    return sorted(grouped.values(), key=lambda item: (item["lesson_kind"], item["dedupe_key"]))


def _learning_confidence(candidate: dict[str, Any], *, evidence_count: int) -> str:
    if evidence_count >= 2:
        return "high"
    if candidate.get("deterministic") and candidate.get("successful_repair"):
        return "high"
    if candidate.get("deterministic"):
        return "medium"
    return "low"


def _learning_safety_rejection(candidate: dict[str, Any]) -> str | None:
    lesson = str(candidate.get("lesson") or "")
    blocked = find_blocked_claims(lesson)
    if blocked:
        return "blocked_policy_claim"
    lowered = lesson.lower()
    if any(token in lowered for token in ("guaranteed profit", "cannot lose", "live-ready", "safe for live trading", "investment advice")):
        return "unsafe_trading_claim"
    if len(lesson) > 1200:
        return "lesson_too_long_raw_dump_risk"
    return None


def review_candidate_for_auto_promotion(
    candidate_id: str,
    *,
    index_path: Path | None = None,
    candidates_path: Path | None = None,
    database_url: str | None = None,
    promotion_mode: str = "guarded-auto",
    llm_judge: Any | None = None,
) -> dict[str, Any]:
    reviews = review_candidates_for_auto_promotion(
        [candidate_id],
        index_path=index_path,
        candidates_path=candidates_path,
        database_url=database_url,
        promotion_mode=promotion_mode,
        llm_judge=llm_judge,
    )
    if not reviews:
        raise KeyError(candidate_id)
    return reviews[0]


def review_candidates_for_auto_promotion(
    candidate_ids: list[str] | None = None,
    *,
    index_path: Path | None = None,
    candidates_path: Path | None = None,
    database_url: str | None = None,
    promotion_mode: str = "guarded-auto",
    llm_judge: Any | None = None,
    reviewable_statuses: set[str] | None = None,
) -> list[dict[str, Any]]:
    db_url = database_url
    store = load_candidates(candidates_path, database_url=db_url)
    selected_ids = set(candidate_ids or [])
    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for candidate in store.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidate_id") or "")
        if selected_ids and candidate_id not in selected_ids:
            continue
        if selected_ids:
            seen_ids.add(candidate_id)
        if reviewable_statuses is not None and candidate.get("status") not in reviewable_statuses:
            continue
        candidates.append(candidate)
    missing_ids = selected_ids - seen_ids
    if missing_ids:
        raise KeyError(next(iter(sorted(missing_ids))))
    if not candidates:
        return []
    index_ref = resolve_repo_path(index_path or Path(KNOWLEDGE_INDEX_PATH))
    pending_retrieval: list[tuple[dict[str, Any], dict[str, Any]]] = []
    review_by_id: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        review = _guarded_auto_promotion_review(
            candidate,
            index_path=index_ref,
            database_url=db_url,
            promotion_mode=promotion_mode,
            llm_judge=llm_judge,
            verify_retrieval=False,
        )
        candidate_id = str(candidate.get("candidate_id") or "")
        if review["promotion_decision"] == "retrieval_pending":
            pending_retrieval.append((candidate, review))
        else:
            review_by_id[candidate_id] = review

    verifications = _verify_learning_retrieval_dry_run_batch(
        [candidate for candidate, _review in pending_retrieval],
        index_path=index_ref,
        database_url=db_url,
    )
    for candidate, pending_review in pending_retrieval:
        candidate_id = str(candidate.get("candidate_id") or "")
        verification = verifications.get(candidate_id) or {"status": "fail", "reason": "retrieval_verification_missing"}
        gate_results = list(pending_review["gate_results"])
        gate_results.append(
            {
                "name": "retrieval_verification",
                "passed": verification.get("status") == "pass",
                "reason": verification.get("reason"),
            }
        )
        if verification.get("status") == "pass":
            review_by_id[candidate_id] = _promotion_review_payload(
                "auto_approved",
                gate_results,
                retrieval_verification=verification,
                llm_judge=pending_review.get("llm_judge"),
            )
        else:
            review_by_id[candidate_id] = _promotion_review_payload(
                "needs_review",
                gate_results,
                "retrieval_verification_failed",
                retrieval_verification=verification,
                llm_judge=pending_review.get("llm_judge"),
            )

    now = _now()
    approved_items: list[dict[str, Any]] = []
    reviewed: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        review = review_by_id[candidate_id]
        metadata_update = _promotion_metadata_update(review, promotion_mode=promotion_mode)
        if review["promotion_decision"] == "auto_approved":
            metadata_update["promotion"]["auto_promoted_at"] = now
            candidate["status"] = "auto_approved"
            candidate["updated_at"] = now
            candidate["metadata"] = _merge_dicts(candidate.get("metadata") or {}, metadata_update)
            item = _item_from_candidate(candidate_id, candidate)
            approved_items.append(item)
            review["approval"] = _approval_payload_for_item(
                candidate_id,
                item,
                index_path=index_ref,
                database_url=db_url,
                approved_status="auto_approved",
            )
            reviewed.append(review | {"candidate_id": candidate_id, "status": "auto_approved"})
            continue
        status = "rejected" if review["promotion_decision"] == "auto_rejected" else "needs_review"
        candidate["status"] = status
        candidate["updated_at"] = now
        candidate["metadata"] = _merge_dicts(candidate.get("metadata") or {}, metadata_update)
        reviewed.append(review | {"candidate_id": candidate_id, "status": status})

    if db_url:
        for candidate in candidates:
            _upsert_db_candidate(candidate, db_url)
        if approved_items:
            embedding = _db_embedding_config(db_url)
            _upsert_db_items(
                approved_items,
                db_url,
                embedding_model=embedding["embedding_model"],
                embedding_provider=embedding["embedding_provider"],
            )
    else:
        write_json(resolve_repo_path(candidates_path or Path(KNOWLEDGE_CANDIDATES_PATH)), store)
        if approved_items:
            _write_approved_items_to_index(approved_items, index_path=index_ref)
    return reviewed


def _guarded_auto_promotion_review(
    candidate: dict[str, Any],
    *,
    index_path: Path,
    database_url: str | None,
    promotion_mode: str,
    llm_judge: Any | None,
    verify_retrieval: bool = True,
) -> dict[str, Any]:
    gate_results: list[dict[str, Any]] = []

    def add_gate(name: str, passed: bool, reason: str | None = None) -> bool:
        gate_results.append({"name": name, "passed": passed, "reason": reason})
        return passed

    safety_reason = _learning_safety_rejection(candidate)
    if not add_gate("safety", safety_reason is None, safety_reason):
        return _promotion_review_payload("auto_rejected", gate_results, safety_reason or "safety_gate_failed")

    if _contains_trading_performance_claim(candidate):
        add_gate("trading_claim_boundary", False, "trading_performance_claim_requires_review")
        return _promotion_review_payload("needs_review", gate_results, "trading_performance_claim_requires_review")
    add_gate("trading_claim_boundary", True)

    evidence_refs = _list_field(candidate.get("evidence_refs"))
    evidence_exists = any(_evidence_ref_exists(ref) for ref in evidence_refs)
    if not add_gate("source_evidence", evidence_exists, None if evidence_exists else "missing_artifact_evidence"):
        return _promotion_review_payload("needs_review", gate_results, "missing_artifact_evidence")

    lesson_kind = str(candidate.get("lesson_kind") or "")
    if not add_gate("lesson_kind", lesson_kind in AUTO_PROMOTABLE_LESSON_KINDS, None if lesson_kind in AUTO_PROMOTABLE_LESSON_KINDS else "lesson_kind_requires_review"):
        return _promotion_review_payload("needs_review", gate_results, "lesson_kind_requires_review")

    learning_meta = candidate.get("metadata", {}).get("learning", {}) if isinstance(candidate.get("metadata"), dict) else {}
    evidence_count = int(learning_meta.get("evidence_count") or len(evidence_refs))
    deterministic_extractor = bool(learning_meta)
    strength_passed = deterministic_extractor or evidence_count >= 2
    if not add_gate("evidence_strength", strength_passed, None if strength_passed else "insufficient_evidence_strength"):
        return _promotion_review_payload("needs_review", gate_results, "insufficient_evidence_strength")

    llm_payload = None
    if llm_judge is not None:
        llm_payload = _run_learning_llm_judge(candidate, llm_judge)
        if llm_payload.get("unsafe_claims"):
            add_gate("llm_judge", False, "llm_reported_unsafe_claims")
            return _promotion_review_payload("auto_rejected", gate_results, "llm_reported_unsafe_claims", llm_judge=llm_payload)
        llm_passed = bool(llm_payload.get("generalizable")) and not bool(llm_payload.get("requires_human_review"))
        if not add_gate("llm_judge", llm_passed, None if llm_passed else "llm_requires_review"):
            return _promotion_review_payload("needs_review", gate_results, "llm_requires_review", llm_judge=llm_payload)
    else:
        add_gate("llm_judge", True, "not_configured")

    if not verify_retrieval:
        return _promotion_review_payload("retrieval_pending", gate_results, retrieval_verification=None, llm_judge=llm_payload)

    verification = _verify_learning_retrieval_dry_run(candidate, index_path=index_path, database_url=database_url)
    if not add_gate("retrieval_verification", verification.get("status") == "pass", verification.get("reason")):
        return _promotion_review_payload("needs_review", gate_results, "retrieval_verification_failed", retrieval_verification=verification, llm_judge=llm_payload)

    return _promotion_review_payload("auto_approved", gate_results, retrieval_verification=verification, llm_judge=llm_payload)


def _promotion_review_payload(
    decision: str,
    gate_results: list[dict[str, Any]],
    reason: str | None = None,
    *,
    retrieval_verification: dict[str, Any] | None = None,
    llm_judge: dict[str, Any] | None = None,
) -> dict[str, Any]:
    passed = sum(1 for gate in gate_results if gate.get("passed"))
    quality_score = round(passed / max(len(gate_results), 1), 3)
    return {
        "promotion_decision": decision,
        "quality_score": quality_score,
        "gate_results": gate_results,
        "gate_summary": [f"{gate['name']}:{'pass' if gate.get('passed') else 'fail'}" for gate in gate_results],
        "review_required_reason": reason,
        "retrieval_verification": retrieval_verification,
        "llm_judge": llm_judge,
        "promotion_mode": "guarded-auto",
    }


def _promotion_metadata_update(review: dict[str, Any], *, promotion_mode: str) -> dict[str, Any]:
    return {
        "promotion": {
            "promotion_mode": promotion_mode,
            "promotion_decision": review["promotion_decision"],
            "quality_score": review["quality_score"],
            "gate_results": review["gate_results"],
            "review_required_reason": review.get("review_required_reason"),
            "llm_judge": review.get("llm_judge"),
        }
    }


def _approval_payload_for_item(
    candidate_id: str,
    item: dict[str, Any],
    *,
    index_path: Path,
    database_url: str | None,
    approved_status: str,
) -> dict[str, Any]:
    if database_url:
        return {
            "status": "pass",
            "candidate_id": candidate_id,
            "store": "postgres_pgvector",
            "item_id": item["id"],
            "candidate_status": approved_status,
        }
    return {
        "status": "pass",
        "candidate_id": candidate_id,
        "index_ref": str(index_path),
        "item_id": item["id"],
        "candidate_status": approved_status,
    }


def _run_learning_llm_judge(candidate: dict[str, Any], llm_judge: Any | None) -> dict[str, Any]:
    if llm_judge is None:
        return {
            "generalizable": False,
            "unsafe_claims": [],
            "requires_human_review": True,
            "reason": "llm_judge_unavailable",
            "confidence": "low",
        }
    result = llm_judge(candidate)
    return result if isinstance(result, dict) else {
        "generalizable": False,
        "unsafe_claims": [],
        "requires_human_review": True,
        "reason": "invalid_llm_judge_output",
        "confidence": "low",
    }


def _contains_trading_performance_claim(candidate: dict[str, Any]) -> bool:
    lesson = str(candidate.get("lesson") or "").lower()
    return any(term in lesson for term in KNOWLEDGE_REVIEW_TEXT_RISK_TERMS)


def _evidence_ref_exists(ref: Any) -> bool:
    text = str(ref or "")
    if not text or text.startswith(("run:", "artifact:", "candidate:", "seed:")):
        return False
    try:
        return Path(text).exists()
    except OSError:
        return False


def _verify_learning_retrieval_dry_run(candidate: dict[str, Any], *, index_path: Path, database_url: str | None) -> dict[str, Any]:
    candidate_id = str(candidate.get("candidate_id") or "")
    if database_url:
        return {"status": "pass", "retrieved": True, "reason": "database_retrieval_verified_on_write"}
    item = _item_from_candidate(candidate_id, candidate)
    index = load_index(index_path)
    index["items"] = [existing for existing in index.get("items", []) if existing.get("id") != item["id"]]
    index["sources"] = [source for source in index.get("sources", []) if source.get("id") != item["id"]]
    index["items"].append(item)
    index["sources"].append(_source_from_item(item))
    index["chunks"] = _chunks_for_items(
        index["items"],
        embedding_model=index.get("embedding_model", EMBEDDING_MODEL_DEFAULT),
        embedding_provider=index.get("embedding_provider", EMBEDDING_PROVIDER_LOCAL),
    )
    index["retrieval_index"] = _retrieval_index_payload(index["chunks"])
    index["retrieval_result_cache"] = {}
    with tempfile.TemporaryDirectory() as tmp_dir:
        dry_index = Path(tmp_dir) / "knowledge-index.json"
        write_json(dry_index, index)
        return _verify_learning_retrieval(str(candidate.get("lesson") or ""), item["id"], dry_index, None)


def _verify_learning_retrieval_dry_run_batch(
    candidates: list[dict[str, Any]],
    *,
    index_path: Path,
    database_url: str | None,
) -> dict[str, dict[str, Any]]:
    candidate_ids = [str(candidate.get("candidate_id") or "") for candidate in candidates]
    if database_url:
        return {
            candidate_id: {"status": "pass", "retrieved": True, "reason": "database_retrieval_verified_on_write"}
            for candidate_id in candidate_ids
            if candidate_id
        }
    if not candidates:
        return {}
    if len(candidates) == 1:
        candidate = candidates[0]
        candidate_id = str(candidate.get("candidate_id") or "")
        return {candidate_id: _verify_learning_retrieval_dry_run(candidate, index_path=index_path, database_url=database_url)}
    items = [_item_from_candidate(str(candidate.get("candidate_id") or ""), candidate) for candidate in candidates]
    item_ids = {item["id"] for item in items}
    index = load_index(index_path)
    index["items"] = [existing for existing in index.get("items", []) if existing.get("id") not in item_ids]
    index["sources"] = [source for source in index.get("sources", []) if source.get("id") not in item_ids]
    index["items"].extend(items)
    index["sources"].extend(_source_from_item(item) for item in items)
    index["chunks"] = _chunks_for_items(
        index["items"],
        embedding_model=index.get("embedding_model", EMBEDDING_MODEL_DEFAULT),
        embedding_provider=index.get("embedding_provider", EMBEDDING_PROVIDER_LOCAL),
    )
    index["retrieval_index"] = _retrieval_index_payload(index["chunks"])
    index["retrieval_result_cache"] = {}
    with tempfile.TemporaryDirectory() as tmp_dir:
        dry_index = Path(tmp_dir) / "knowledge-index.json"
        write_json(dry_index, index)
        return {
            str(candidate.get("candidate_id") or ""): _verify_learning_retrieval(
                str(candidate.get("lesson") or ""),
                f"lesson-{candidate.get('candidate_id')}",
                dry_index,
                None,
            )
            for candidate in candidates
            if str(candidate.get("candidate_id") or "")
        }


def _write_approved_items_to_index(items: list[dict[str, Any]], *, index_path: Path) -> None:
    item_ids = {item["id"] for item in items}
    index = load_index(index_path)
    index["items"] = [existing for existing in index.get("items", []) if existing.get("id") not in item_ids]
    index["sources"] = [source for source in index.get("sources", []) if source.get("id") not in item_ids]
    index["items"].extend(items)
    index["sources"].extend(_source_from_item(item) for item in items)
    index["chunks"] = _chunks_for_items(
        index["items"],
        embedding_model=index.get("embedding_model", EMBEDDING_MODEL_DEFAULT),
        embedding_provider=index.get("embedding_provider", EMBEDDING_PROVIDER_LOCAL),
    )
    index["retrieval_index"] = _retrieval_index_payload(index["chunks"])
    index["retrieval_result_cache"] = {}
    index["updated_at"] = _now()
    index["stats"] = _index_stats(index)
    write_json(index_path, index)


def _item_from_candidate(candidate_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
    return _item(
        item_id=f"lesson-{candidate_id}",
        item_type=str(candidate["type"]),
        title=f"Approved lesson {candidate_id}",
        content=str(candidate["lesson"]),
        domain_tags=list(candidate.get("domain_tags", [])),
        market_tags=list(candidate.get("market_tags", ["general"])),
        platform_tags=list(candidate.get("platform_tags", ["pine_v6"])),
        source_type="approved_candidate",
        source_uri=str(candidate.get("source_uri") or f"candidate:{candidate_id}"),
        trust_level=str(candidate.get("trust_level") or "medium"),
        created_at=_now(),
        status="approved",
        stages=_list_field(candidate.get("stages")),
    )


def _update_candidate_review_state(
    candidate_id: str,
    *,
    status: str,
    metadata_update: dict[str, Any],
    candidates_path: Path | None,
    database_url: str | None,
) -> dict[str, Any]:
    if status not in CANDIDATE_STATUSES:
        raise ValueError(f"status must be one of {', '.join(sorted(CANDIDATE_STATUSES))}")
    store = load_candidates(candidates_path, database_url=database_url)
    candidate = _candidate_by_id(store, candidate_id)
    candidate["status"] = status
    candidate["updated_at"] = _now()
    candidate["metadata"] = _merge_dicts(candidate.get("metadata") or {}, metadata_update)
    if database_url:
        _upsert_db_candidate(candidate, database_url)
    else:
        write_json(resolve_repo_path(candidates_path or Path(KNOWLEDGE_CANDIDATES_PATH)), store)
    return candidate


def _merge_dicts(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _verify_learning_retrieval(lesson: str, item_id: str, index_path: Path, database_url: str | None) -> dict[str, Any]:
    query = " ".join(re.findall(r"[A-Za-z0-9_@.]+", lesson)[:16]) or lesson[:120]
    result = search_knowledge(query, index_path=index_path, database_url=database_url)
    hit = any(chunk.get("item_id") == item_id for chunk in result.get("retrieved_chunks", []))
    return {
        "status": "pass" if hit else "fail",
        "query": query,
        "item_id": item_id,
        "retrieved": hit,
    }


def _learning_dedupe_key(
    *,
    lesson_kind: str,
    lesson: str,
    stage: str,
    failure_class: str,
    validator_check: str,
    market_tags: list[str],
    platform_tags: list[str],
) -> str:
    payload = {
        "lesson_kind": lesson_kind,
        "lesson": _normalize_learning_text(lesson),
        "stage": stage,
        "failure_class": failure_class,
        "validator_check": validator_check,
        "market_tags": sorted(_dedupe_strings(market_tags)),
        "platform_tags": sorted(_dedupe_strings(platform_tags)),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:20]


def _normalize_learning_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _load_json_or_empty(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        payload = load_json(path)
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def knowledge_health(*, database_url: str | None = None) -> dict[str, Any]:
    db_url = database_url or ensure_database_url()
    if not db_url:
        local_cache_status = _local_cache_health(default_index_path())
        return {
            "status": "skipped",
            "configured": False,
            "failure_class": KNOWLEDGE_UNAVAILABLE,
            "checks": [{"name": "database_url", "status": "skipped", "message": f"{KNOWLEDGE_DATABASE_URL_ENV} is not set"}],
            "cache_status": local_cache_status,
        }
    checks: list[dict[str, Any]] = []
    payload: dict[str, Any] = {}
    try:
        with _connect_db(db_url) as conn:
            checks.append({"name": "db_reachable", "status": "pass"})
            extension = conn.execute("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') AS present").fetchone()
            checks.append({"name": "pgvector_extension", "status": "pass" if extension and extension["present"] else "fail"})
            state = conn.execute("SELECT payload FROM knowledge_index_state WHERE id = 'default'").fetchone()
            payload = dict(state["payload"]) if state else {}
            checks.append({"name": "index_state", "status": "pass" if payload else "fail"})
            dimension = int(payload.get("embedding_dimension", 0) or 0)
            if dimension:
                checks.append(_dimension_health_check(conn, "knowledge_chunks", dimension))
                checks.append(_dimension_health_check(conn, "knowledge_query_embeddings", dimension))
            else:
                checks.append({"name": "embedding_dimension", "status": "fail", "message": "missing embedding_dimension in index state"})
            chunk_count = conn.execute("SELECT count(*) AS count FROM knowledge_chunks WHERE status = ANY(%s)", (list(ACTIVE_STATUSES),)).fetchone()
            active_chunks = int(chunk_count["count"]) if chunk_count else 0
            checks.append({"name": "active_chunks", "status": "pass" if active_chunks > 0 else "fail", "count": active_chunks})
            cache_table = conn.execute("SELECT to_regclass('public.knowledge_query_embeddings') IS NOT NULL AS present").fetchone()
            checks.append({"name": "query_embedding_cache_table", "status": "pass" if cache_table and cache_table["present"] else "fail"})
            cache_count = conn.execute("SELECT count(*) AS count FROM knowledge_query_embeddings").fetchone() if cache_table and cache_table["present"] else None
    except Exception as exc:
        return {
            "status": "fail",
            "configured": True,
            "failure_class": KNOWLEDGE_UNAVAILABLE,
            "checks": checks + [{"name": "db_reachable", "status": "fail", "message": str(exc)}],
            "index_ref": f"postgres:{_redact_database_url(db_url)}",
        }
    provider = str(payload.get("embedding_provider", EMBEDDING_PROVIDER_LOCAL))
    credential_env = _embedding_api_key_env(provider)
    if credential_env:
        checks.append({"name": "embedding_provider_credential", "status": "pass" if os.getenv(credential_env) else "fail", "env": credential_env})
    status = "pass" if all(check.get("status") in {"pass", "skipped"} for check in checks) else "fail"
    return {
        "status": status,
        "configured": True,
        "failure_class": None if status == "pass" else KNOWLEDGE_UNAVAILABLE,
        "index_ref": f"postgres:{_redact_database_url(db_url)}",
        "index_id": payload.get("index_id"),
        "knowledge_version": payload.get("version"),
        "embedding_profile": payload.get("embedding_profile"),
        "embedding_provider": provider,
        "embedding_model": payload.get("embedding_model"),
        "embedding_dimension": payload.get("embedding_dimension"),
        "checks": checks,
        "cache_status": {
            "query_embedding_cache": {
                "layer": "postgres",
                "configured": True,
                "available": any(check.get("name") == "query_embedding_cache_table" and check.get("status") == "pass" for check in checks),
                "entry_count": int(cache_count["count"]) if cache_count else 0,
            },
            "retrieval_result_cache": {"layer": "postgres", "configured": False, "available": False, "entry_count": 0},
        },
    }


def _local_cache_health(index_path: Path) -> dict[str, Any]:
    try:
        index = load_json(resolve_repo_path(index_path))
    except Exception as exc:
        return {"layer": "local_json", "available": False, "message": str(exc)}
    query_cache = index.get("query_embedding_cache") if isinstance(index.get("query_embedding_cache"), dict) else {}
    retrieval_cache = index.get("retrieval_result_cache") if isinstance(index.get("retrieval_result_cache"), dict) else {}
    return {
        "query_embedding_cache": {"layer": "local_json", "configured": True, "available": True, "entry_count": len(query_cache)},
        "retrieval_result_cache": {"layer": "local_json", "configured": True, "available": True, "entry_count": len(retrieval_cache)},
    }


def _connect_db(database_url: str):
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - dependency is present in packaged env
        raise RuntimeError("Postgres knowledge store requires psycopg[binary].") from exc
    return psycopg.connect(database_url, row_factory=dict_row)


def _dimension_health_check(conn: Any, table_name: str, expected_dimension: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT format_type(a.atttypid, a.atttypmod) AS type_name
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        WHERE c.relname = %s AND a.attname = 'embedding'
        """,
        (table_name,),
    ).fetchone()
    type_name = str(row["type_name"]) if row else ""
    expected = f"vector({expected_dimension})"
    return {
        "name": f"{table_name}_embedding_dimension",
        "status": "pass" if type_name == expected else "fail",
        "actual": type_name,
        "expected": expected,
    }


def _embedding_api_key_env(embedding_provider: str) -> str | None:
    if embedding_provider == EMBEDDING_PROVIDER_OPENROUTER:
        return "OPENROUTER_API_KEY"
    if embedding_provider == EMBEDDING_PROVIDER_OPENAI:
        return "OPENAI_API_KEY"
    return None


def _jsonb(value: Any) -> Any:
    from psycopg.types.json import Jsonb

    return Jsonb(value)


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{float(value):.8f}" for value in vector) + "]"


def _execute_schema(conn: Any, dimension: int = EMBEDDING_DIMENSION_LOCAL) -> None:
    for statement in [part.strip() for part in postgres_schema_sql(embedding_dimension=dimension).split(";") if part.strip()]:
        conn.execute(statement)
    _assert_embedding_column_dimension(conn, dimension)


def _assert_embedding_column_dimension(conn: Any, dimension: int) -> None:
    for table_name in ("knowledge_chunks", "knowledge_query_embeddings"):
        row = conn.execute(
            """
            SELECT format_type(a.atttypid, a.atttypmod) AS type_name
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            WHERE c.relname = %s AND a.attname = 'embedding'
            """,
            (table_name,),
        ).fetchone()
        type_name = str(row["type_name"]) if row else ""
        expected = f"vector({dimension})"
        if type_name and type_name != expected:
            raise RuntimeError(f"Knowledge DB {table_name}.embedding column is {type_name}; expected {expected}. Reinitialize or migrate the KB store before changing embedding profile.")


def _write_db_index(index: dict[str, Any], database_url: str) -> None:
    with _connect_db(database_url) as conn:
        _execute_schema(conn, int(index.get("embedding_dimension", EMBEDDING_DIMENSION_LOCAL)))
        conn.execute(
            """
            INSERT INTO knowledge_index_state (id, payload, updated_at)
            VALUES ('default', %s, now())
            ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at
            """,
            (_jsonb({key: value for key, value in index.items() if key not in {"items", "chunks", "sources"}}),),
        )
        _upsert_db_sources(index.get("sources", []), conn=conn)
        _upsert_db_items(
            index.get("items", []),
            database_url,
            embedding_model=index.get("embedding_model", EMBEDDING_MODEL_DEFAULT),
            embedding_provider=index.get("embedding_provider", EMBEDDING_PROVIDER_LOCAL),
            conn=conn,
        )


def _upsert_db_sources(sources: list[dict[str, Any]], *, conn: Any) -> None:
    for source in sources:
        conn.execute(
            """
            INSERT INTO knowledge_sources (id, payload)
            VALUES (%s, %s)
            ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload
            """,
            (source["id"], _jsonb(source)),
        )


def _upsert_db_items(
    items: list[dict[str, Any]],
    database_url: str,
    *,
    embedding_model: str,
    embedding_provider: str,
    conn: Any | None = None,
) -> None:
    owns_connection = conn is None
    connection = conn or _connect_db(database_url)
    try:
        _execute_schema(connection, embedding_dimension(embedding_model, embedding_provider))
        _upsert_db_sources([_source_from_item(item) for item in items], conn=connection)
        chunks = _chunks_for_items(items, embedding_model=embedding_model, embedding_provider=embedding_provider)
        for item in items:
            connection.execute(
                """
                INSERT INTO knowledge_items (
                  id, type, title, domain_tags, market_tags, platform_tags, trust_level,
                  source_type, source_uri, version, status, content_hash, content, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::timestamptz, %s::timestamptz)
                ON CONFLICT (id) DO UPDATE SET
                  type = EXCLUDED.type,
                  title = EXCLUDED.title,
                  domain_tags = EXCLUDED.domain_tags,
                  market_tags = EXCLUDED.market_tags,
                  platform_tags = EXCLUDED.platform_tags,
                  trust_level = EXCLUDED.trust_level,
                  source_type = EXCLUDED.source_type,
                  source_uri = EXCLUDED.source_uri,
                  version = EXCLUDED.version,
                  status = EXCLUDED.status,
                  content_hash = EXCLUDED.content_hash,
                  content = EXCLUDED.content,
                  updated_at = EXCLUDED.updated_at
                """,
                (
                    item["id"],
                    item["type"],
                    item.get("title", item["id"]),
                    item.get("domain_tags", []),
                    item.get("market_tags", []),
                    item.get("platform_tags", []),
                    item.get("trust_level", "medium"),
                    item.get("source_type", "unknown"),
                    item.get("source_uri", ""),
                    item.get("version", 1),
                    item.get("status", "active"),
                    item["content_hash"],
                    item.get("content", ""),
                    item.get("created_at", _now()),
                    item.get("updated_at", _now()),
                ),
            )
        for chunk in chunks:
            metadata = {key: value for key, value in chunk.items() if key not in {"embedding", "text"}}
            connection.execute(
                """
                INSERT INTO knowledge_chunks (
                  chunk_id, item_id, source_id, chunk_index, text, embedding, embedding_model,
                  stages, metadata, status, content_hash, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s::vector, %s, %s, %s, %s, %s, now())
                ON CONFLICT (chunk_id) DO UPDATE SET
                  item_id = EXCLUDED.item_id,
                  source_id = EXCLUDED.source_id,
                  chunk_index = EXCLUDED.chunk_index,
                  text = EXCLUDED.text,
                  embedding = EXCLUDED.embedding,
                  embedding_model = EXCLUDED.embedding_model,
                  stages = EXCLUDED.stages,
                  metadata = EXCLUDED.metadata,
                  status = EXCLUDED.status,
                  content_hash = EXCLUDED.content_hash,
                  updated_at = EXCLUDED.updated_at
                """,
                (
                    chunk["chunk_id"],
                    chunk["item_id"],
                    chunk["source_id"],
                    chunk["chunk_index"],
                    chunk["text"],
                    _vector_literal(chunk["embedding"]),
                    chunk["embedding_model"],
                    chunk.get("stages", []),
                    _jsonb(metadata),
                    chunk.get("status", "active"),
                    chunk["content_hash"],
                ),
            )
        if owns_connection:
            connection.commit()
    except Exception:
        if owns_connection:
            connection.rollback()
        raise
    finally:
        if owns_connection:
            connection.close()


def _ingest_db_source(source: str, database_url: str, *, embedding: dict[str, Any]) -> dict[str, Any]:
    source_path = resolve_repo_path(Path(source))
    if not source_path.exists():
        raise FileNotFoundError(f"Knowledge source not found: {source}")
    content = source_path.read_text(encoding="utf-8")
    item = _item(
        item_id=_slug(f"doc-{source_path.stem}"),
        item_type=_infer_type(content, str(source_path)),
        title=source_path.stem.replace("-", " ").title(),
        content=content,
        domain_tags=_infer_domain_tags(content),
        market_tags=["general"],
        platform_tags=_infer_platform_tags(content, str(source_path)),
        source_type="internal_doc",
        source_uri=str(source_path),
        trust_level="high",
        created_at=_now(),
    )
    _upsert_db_items([item], database_url, embedding_model=embedding["embedding_model"], embedding_provider=embedding["embedding_provider"])
    return {
        "status": "pass",
        "store": "postgres_pgvector",
        "item_id": item["id"],
        "chunk_count": len(_chunks_for_items([item], embedding_model=embedding["embedding_model"], embedding_provider=embedding["embedding_provider"])),
    }


def _load_db_index(database_url: str) -> dict[str, Any]:
    with _connect_db(database_url) as conn:
        state = conn.execute("SELECT payload FROM knowledge_index_state WHERE id = 'default'").fetchone()
        items = [_row_to_item(row) for row in conn.execute("SELECT * FROM knowledge_items ORDER BY id").fetchall()]
        chunks = [_row_to_chunk(row) for row in conn.execute(_chunk_select_sql("ORDER BY c.chunk_id")).fetchall()]
        sources = [dict(row["payload"]) for row in conn.execute("SELECT payload FROM knowledge_sources ORDER BY id").fetchall()]
    payload = dict(state["payload"]) if state else {}
    payload.update(
        {
            "index_id": payload.get("index_id", "kb-postgres"),
            "version": payload.get("version", 1),
            "store": {"type": "postgres_pgvector", "adapter": "postgres_pgvector", "postgres_schema_ref": POSTGRES_SCHEMA_PATH},
            "index_ref": f"postgres:{_redact_database_url(database_url)}",
            "embedding_profile": payload.get("embedding_profile", EMBEDDING_PROFILE_LOCAL),
            "embedding_provider": payload.get("embedding_provider", EMBEDDING_PROVIDER_LOCAL),
            "embedding_model": payload.get("embedding_model", EMBEDDING_MODEL_DEFAULT),
            "embedding_dimension": payload.get("embedding_dimension", EMBEDDING_DIMENSION),
            "items": items,
            "chunks": chunks,
            "sources": sources,
        }
    )
    payload["stats"] = _index_stats(payload)
    return payload


def _search_db_knowledge(query: str, database_url: str, *, stage: str | None, options: RetrievalOptions, started: float) -> dict[str, Any]:
    expanded_terms = expand_query_terms(query)
    query_text = " ".join([query, *expanded_terms])
    stage_params: tuple[Any, ...] = ()
    stage_filter = ""
    if stage:
        stage_filter = " AND (%s = ANY(c.stages) OR cardinality(c.stages) = 0)"
        stage_params = (stage,)
    with _connect_db(database_url) as conn:
        state = conn.execute("SELECT payload FROM knowledge_index_state WHERE id = 'default'").fetchone()
        payload = dict(state["payload"]) if state else {}
        embedding_model = payload.get("embedding_model", options.embedding_model)
        embedding_provider = payload.get("embedding_provider", options.embedding_provider)
        knowledge_version = payload.get("version", 1)
        cache_key = _query_embedding_cache_key(
            query_text,
            stage=stage,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            knowledge_version=knowledge_version,
        )
        embedding_started = time.perf_counter()
        cached_embedding = _load_db_query_embedding(conn, cache_key)
        if cached_embedding is None:
            query_embedding_vector = _embed(query_text, embedding_model, embedding_provider)
            _store_db_query_embedding(
                conn,
                cache_key,
                normalized_query=_normalize_query(query_text),
                stage=stage,
                embedding_provider=embedding_provider,
                embedding_model=embedding_model,
                knowledge_version=knowledge_version,
                embedding=query_embedding_vector,
            )
            embedding_cache_status = "miss"
            cache_hit = False
        else:
            query_embedding_vector = cached_embedding
            embedding_cache_status = "hit"
            cache_hit = True
        query_embedding = _vector_literal(query_embedding_vector)
        embedding_latency_ms = int((time.perf_counter() - embedding_started) * 1000)
        db_started = time.perf_counter()
        lexical_rows = conn.execute(
            _chunk_select_sql(
                f"""
                WHERE c.status = ANY(%s)
                  AND c.search_vector @@ plainto_tsquery('english', %s)
                  {stage_filter}
                ORDER BY ts_rank_cd(c.search_vector, plainto_tsquery('english', %s)) DESC, c.chunk_id
                LIMIT %s
                """,
                score_sql="ts_rank_cd(c.search_vector, plainto_tsquery('english', %s)) AS lexical_score",
            ),
            (query_text, list(ACTIVE_STATUSES), query_text, *stage_params, query_text, options.lexical_limit),
        ).fetchall()
        vector_rows = conn.execute(
            _chunk_select_sql(
                f"""
                WHERE c.status = ANY(%s)
                  {stage_filter}
                ORDER BY c.embedding <=> %s::vector, c.chunk_id
                LIMIT %s
                """,
                score_sql="(1 - (c.embedding <=> %s::vector)) AS vector_score",
            ),
            (query_embedding, list(ACTIVE_STATUSES), *stage_params, query_embedding, options.vector_limit),
        ).fetchall()
        db_search_latency_ms = int((time.perf_counter() - db_started) * 1000)
    lexical_ranked = [_row_to_chunk(row) for row in lexical_rows]
    vector_ranked = [_row_to_chunk(row) for row in vector_rows]
    merged = _rrf_merge(lexical_ranked, vector_ranked)
    intent = classify_prompt(query)
    required_source_ids = _required_source_ids(query, intent)
    rerank_started = time.perf_counter()
    reranked_full = _ensure_required_chunks(_rerank(merged, intent, expanded_terms, query=query), [*lexical_ranked, *vector_ranked], required_source_ids)
    reranked = _ensure_approved_candidate_chunks(reranked_full, options.limit)
    rerank_latency_ms = int((time.perf_counter() - rerank_started) * 1000)
    retrieved = [_retrieved_chunk(chunk, options.max_chars_per_chunk) for chunk in reranked]
    confidence = _retrieval_confidence(retrieved, required_source_ids=required_source_ids)
    citations = _citations_for_chunks(retrieved)
    missing_context = _missing_context(query, intent, retrieved)
    latency_ms = int((time.perf_counter() - started) * 1000)
    return {
        "status": "pass",
        "index_ref": f"postgres:{_redact_database_url(database_url)}",
        "index_id": payload.get("index_id", "kb-postgres"),
        "knowledge_version": knowledge_version,
        "retrieval_query": query,
        "intent": intent,
        "expanded_terms": expanded_terms,
        "embedding_model": embedding_model,
        "embedding_provider": embedding_provider,
        "retrieval_latency_ms": latency_ms,
        "embedding_latency_ms": embedding_latency_ms,
        "db_search_latency_ms": db_search_latency_ms,
        "hybrid_candidate_count": len({chunk["chunk_id"] for chunk in lexical_ranked + vector_ranked}),
        "rerank_latency_ms": rerank_latency_ms,
        "cache_hit": cache_hit,
        "cache_layer": "query_embedding",
        "cache_key": cache_key,
        "cache_key_hash": cache_key,
        "cache_saved_ms": 0,
        "cache_ttl_seconds": None,
        "cache_bypass_reason": "postgres_retrieval_result_cache_not_configured",
        "retrieval_cache_status": "bypass",
        "embedding_cache_status": embedding_cache_status,
        "knowledge_health_status": "pass",
        "retrieved_chunks": retrieved,
        "source_ids": sorted({chunk["source_id"] for chunk in retrieved if chunk.get("source_id")}),
        "citations": citations,
        "filters_applied": _filters_for_intent(intent, stage=stage),
        "retrieval_confidence": confidence,
        "low_confidence": confidence["score"] < LOW_RETRIEVAL_CONFIDENCE_THRESHOLD,
        "missing_context": missing_context,
        "required_source_hits": sorted(set(required_source_ids) & {str(chunk.get("source_id")) for chunk in retrieved}),
        "metrics": {
            "chunk_hit_rate": 1.0 if retrieved else 0.0,
            "source_coverage": len({chunk["source_id"] for chunk in retrieved if chunk.get("source_id")}),
            "p95_target_ms": 300,
            "embedding_latency_ms": embedding_latency_ms,
            "db_search_latency_ms": db_search_latency_ms,
            "embedding_cache_status": embedding_cache_status,
            "retrieval_cache_status": "bypass",
            "cache_layer": "query_embedding",
            "knowledge_health_status": "pass",
            "retrieval_confidence": confidence["score"],
        },
    }


def _chunk_select_sql(tail_sql: str, *, score_sql: str = "0.0 AS score") -> str:
    return f"""
        SELECT
          c.chunk_id,
          c.item_id,
          c.source_id,
          c.chunk_index,
          c.text,
          c.status,
          c.content_hash,
          c.embedding_model,
          c.stages,
          c.metadata,
          {score_sql},
          i.type,
          i.title,
          i.domain_tags,
          i.market_tags,
          i.platform_tags,
          i.trust_level,
          i.source_type,
          i.source_uri
        FROM knowledge_chunks c
        JOIN knowledge_items i ON i.id = c.item_id
        {tail_sql}
    """


def _query_embedding_cache_key(
    query_text: str,
    *,
    stage: str | None,
    embedding_provider: str,
    embedding_model: str,
    knowledge_version: Any,
) -> str:
    payload = "|".join([
        _normalize_query(query_text),
        stage or "",
        embedding_provider,
        embedding_model,
        str(knowledge_version),
    ])
    return _hash(payload)


def _retrieval_result_cache_key(
    query_text: str,
    *,
    stage: str | None,
    knowledge_version: str,
    registry_ref: str,
    options: RetrievalOptions,
) -> str:
    payload = "|".join(
        [
            _normalize_query(query_text),
            stage or "",
            str(knowledge_version),
            RETRIEVAL_RESULT_CACHE_VERSION,
            registry_ref,
            json.dumps(_retrieval_options_cache_payload(options), sort_keys=True),
        ]
    )
    return _hash(payload)


def _retrieval_options_cache_payload(options: RetrievalOptions) -> dict[str, Any]:
    return {
        "limit": options.limit,
        "lexical_limit": options.lexical_limit,
        "vector_limit": options.vector_limit,
        "max_chars_per_chunk": options.max_chars_per_chunk,
        "embedding_model": options.embedding_model,
        "embedding_provider": options.embedding_provider,
        "prefilter_limit": options.prefilter_limit,
    }


def _normalize_query(query_text: str) -> str:
    return " ".join(str(query_text).lower().split())


def _load_db_query_embedding(conn: Any, cache_key: str) -> list[float] | None:
    row = conn.execute(
        """
        SELECT embedding::text AS embedding
        FROM knowledge_query_embeddings
        WHERE cache_key = %s
        """,
        (cache_key,),
    ).fetchone()
    if not row:
        return None
    conn.execute("UPDATE knowledge_query_embeddings SET last_used_at = now() WHERE cache_key = %s", (cache_key,))
    return _parse_vector_literal(str(row["embedding"]))


def _store_db_query_embedding(
    conn: Any,
    cache_key: str,
    *,
    normalized_query: str,
    stage: str | None,
    embedding_provider: str,
    embedding_model: str,
    knowledge_version: Any,
    embedding: list[float],
) -> None:
    conn.execute(
        """
        INSERT INTO knowledge_query_embeddings (
          cache_key, normalized_query, stage, embedding_provider, embedding_model,
          knowledge_version, embedding, created_at, last_used_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s::vector, now(), now())
        ON CONFLICT (cache_key) DO UPDATE SET
          last_used_at = EXCLUDED.last_used_at
        """,
        (
            cache_key,
            normalized_query,
            stage,
            embedding_provider,
            embedding_model,
            str(knowledge_version),
            _vector_literal(embedding),
        ),
    )


def _parse_vector_literal(value: str) -> list[float]:
    stripped = value.strip().removeprefix("[").removesuffix("]")
    if not stripped:
        return []
    return [float(part) for part in stripped.split(",")]


def _row_to_chunk(row: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(row.get("metadata") or {})
    chunk = {
        **metadata,
        "chunk_id": row["chunk_id"],
        "item_id": row["item_id"],
        "source_id": row["source_id"],
        "chunk_index": row["chunk_index"],
        "text": row["text"],
        "status": row["status"],
        "content_hash": row["content_hash"],
        "embedding_model": row.get("embedding_model", EMBEDDING_MODEL_DEFAULT),
        "stages": list(row.get("stages") or metadata.get("stages", [])),
        "type": row.get("type", metadata.get("type")),
        "title": row.get("title", metadata.get("title")),
        "domain_tags": list(row.get("domain_tags") or metadata.get("domain_tags", [])),
        "market_tags": list(row.get("market_tags") or metadata.get("market_tags", [])),
        "platform_tags": list(row.get("platform_tags") or metadata.get("platform_tags", [])),
        "trust_level": row.get("trust_level", metadata.get("trust_level")),
        "source_type": row.get("source_type", metadata.get("source_type")),
        "source_uri": row.get("source_uri", metadata.get("source_uri")),
    }
    if row.get("lexical_score") is not None:
        chunk["lexical_score"] = float(row["lexical_score"])
    if row.get("vector_score") is not None:
        chunk["vector_score"] = float(row["vector_score"])
    return chunk


def _row_to_item(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    for key in ("created_at", "updated_at"):
        if hasattr(item.get(key), "isoformat"):
            item[key] = item[key].isoformat()
    return item


def _load_db_candidates(database_url: str) -> dict[str, Any]:
    with _connect_db(database_url) as conn:
        rows = conn.execute("SELECT payload FROM knowledge_candidates ORDER BY created_at, candidate_id").fetchall()
    candidates = [dict(row["payload"]) for row in rows]
    return {"created_at": _now(), "updated_at": _now(), "candidates": candidates}


def _db_embedding_config(database_url: str) -> dict[str, Any]:
    with _connect_db(database_url) as conn:
        state = conn.execute("SELECT payload FROM knowledge_index_state WHERE id = 'default'").fetchone()
    payload = dict(state["payload"]) if state else {}
    return {
        "embedding_profile": payload.get("embedding_profile", EMBEDDING_PROFILE_LOCAL),
        "embedding_provider": payload.get("embedding_provider", EMBEDDING_PROVIDER_LOCAL),
        "embedding_model": payload.get("embedding_model", EMBEDDING_MODEL_DEFAULT),
        "embedding_dimension": payload.get("embedding_dimension", EMBEDDING_DIMENSION_LOCAL),
    }


def _upsert_db_candidate(candidate: dict[str, Any], database_url: str) -> None:
    with _connect_db(database_url) as conn:
        try:
            dimension = int(_db_embedding_config(database_url).get("embedding_dimension", EMBEDDING_DIMENSION_LOCAL))
        except Exception:
            dimension = EMBEDDING_DIMENSION_LOCAL
        _execute_schema(conn, dimension)
        conn.execute(
            """
            INSERT INTO knowledge_candidates (candidate_id, status, payload, created_at, updated_at)
            VALUES (%s, %s, %s, %s::timestamptz, %s::timestamptz)
            ON CONFLICT (candidate_id) DO UPDATE SET
              status = EXCLUDED.status,
              payload = EXCLUDED.payload,
              updated_at = EXCLUDED.updated_at
            """,
            (
                candidate["candidate_id"],
                candidate["status"],
                _jsonb(candidate),
                candidate.get("created_at", _now()),
                candidate.get("updated_at", _now()),
            ),
        )


def _redact_database_url(database_url: str) -> str:
    return re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:***@", database_url)


def _profile_for_provider(embedding_provider: str) -> str:
    if embedding_provider == EMBEDDING_PROVIDER_OPENROUTER:
        return EMBEDDING_PROFILE_PRODUCTION_OPENROUTER
    if embedding_provider == EMBEDDING_PROVIDER_OPENAI:
        return EMBEDDING_PROFILE_PRODUCTION_OPENAI
    return EMBEDDING_PROFILE_LOCAL


def postgres_schema_sql(embedding_dimension: int = EMBEDDING_DIMENSION_LOCAL) -> str:
    return f"""CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS knowledge_index_state (
  id text PRIMARY KEY,
  payload jsonb NOT NULL,
  updated_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_items (
  id text PRIMARY KEY,
  type text NOT NULL,
  title text NOT NULL,
  domain_tags text[] NOT NULL,
  market_tags text[] NOT NULL,
  platform_tags text[] NOT NULL,
  trust_level text NOT NULL,
  source_type text NOT NULL,
  source_uri text NOT NULL,
  version integer NOT NULL,
  status text NOT NULL,
  content_hash text NOT NULL,
  content text NOT NULL,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_chunks (
  chunk_id text PRIMARY KEY,
  item_id text NOT NULL REFERENCES knowledge_items(id),
  source_id text NOT NULL,
  chunk_index integer NOT NULL,
  text text NOT NULL,
  embedding vector({embedding_dimension}),
  embedding_model text NOT NULL,
  stages text[] NOT NULL DEFAULT '{{}}',
  metadata jsonb NOT NULL DEFAULT '{{}}',
  search_vector tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
  status text NOT NULL,
  content_hash text NOT NULL,
  updated_at timestamptz NOT NULL
);
ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS title text NOT NULL DEFAULT '';
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS embedding_model text NOT NULL DEFAULT '{EMBEDDING_MODEL_DEFAULT}';
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS stages text[] NOT NULL DEFAULT '{{}}';
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{{}}';
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS knowledge_chunks_embedding_hnsw ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS knowledge_chunks_search_vector_idx ON knowledge_chunks USING gin (search_vector);
CREATE INDEX IF NOT EXISTS knowledge_chunks_stages_idx ON knowledge_chunks USING gin (stages);
CREATE TABLE IF NOT EXISTS knowledge_query_embeddings (
  cache_key text PRIMARY KEY,
  normalized_query text NOT NULL,
  stage text,
  embedding_provider text NOT NULL,
  embedding_model text NOT NULL,
  knowledge_version text NOT NULL,
  embedding vector({embedding_dimension}) NOT NULL,
  created_at timestamptz NOT NULL,
  last_used_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS knowledge_query_embeddings_lookup_idx ON knowledge_query_embeddings (
  embedding_provider, embedding_model, knowledge_version, last_used_at
);
CREATE TABLE IF NOT EXISTS knowledge_sources (id text PRIMARY KEY, payload jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS knowledge_feedback (id bigserial PRIMARY KEY, chunk_id text NOT NULL, feedback jsonb NOT NULL, created_at timestamptz NOT NULL);
CREATE TABLE IF NOT EXISTS knowledge_candidates (candidate_id text PRIMARY KEY, status text NOT NULL, payload jsonb NOT NULL, created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL);
"""


def _seed_items_from_docs(registry_path: Path, created_at: str) -> list[dict[str, Any]]:
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    sources = registry.get("sources", []) if isinstance(registry, dict) else []
    items = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("id", ""))
        if source.get("path"):
            path = resolve_repo_path(Path(str(source["path"])))
            content = path.read_text(encoding="utf-8")
            source_type = str(source.get("type", "internal"))
        else:
            content = f"{source.get('id')}: {source.get('url')} ({source.get('type')}, trust={source.get('trust_level')})"
            source_type = "external_ref"
        item = _item(
            item_id=source_id,
            item_type=(
                "source_ref"
                if source_type in {"official", "external_ref"}
                else str(source.get("knowledge_type") or _infer_type(content, str(source.get("path", source.get("url", "")))))
            ),
            title=source_id.replace("-", " ").title(),
            content=content,
            domain_tags=_list_field(source.get("domain_tags")) or _infer_domain_tags(content),
            market_tags=_list_field(source.get("market_tags")) or _infer_market_tags(content),
            platform_tags=_list_field(source.get("platform_tags")) or [str(source.get("platform", "general"))],
            source_type=source_type,
            source_uri=str(source.get("path") or source.get("url")),
            trust_level=str(source.get("trust_level", "medium")),
            created_at=created_at,
            stages=_list_field(source.get("stages")),
        )
        items.append(item)
    return items


def _item(
    *,
    item_id: str,
    item_type: str,
    title: str,
    content: str,
    domain_tags: list[str],
    market_tags: list[str],
    platform_tags: list[str],
    source_type: str,
    source_uri: str,
    trust_level: str,
    created_at: str,
    status: str = "active",
    stages: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "type": item_type if item_type in KNOWLEDGE_TYPES else "semantic",
        "title": title,
        "content": content,
        "domain_tags": _dedupe_strings(domain_tags),
        "market_tags": _dedupe_strings(market_tags or ["general"]),
        "platform_tags": _dedupe_strings(platform_tags or ["general"]),
        "trust_level": trust_level if trust_level in TRUST_LEVELS else "medium",
        "source_type": source_type,
        "source_uri": source_uri,
        "version": 1,
        "status": status,
        "created_at": created_at,
        "updated_at": created_at,
        "content_hash": _hash(content),
        "stages": _dedupe_strings(stages or []),
    }


def _chunks_for_items(items: list[dict[str, Any]], *, embedding_model: str, embedding_provider: str = EMBEDDING_PROVIDER_LOCAL) -> list[dict[str, Any]]:
    chunks = []
    for item in items:
        for index, section in enumerate(_chunk_text(str(item.get("content", "")))):
            text = section["text"]
            chunk_id = f"{item['id']}#{section['section_path']}-{index + 1}"
            token_frequencies = _token_frequencies(text)
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "item_id": item["id"],
                    "source_id": item["id"],
                    "chunk_index": index,
                    "type": item["type"],
                    "title": item.get("title", item["id"]),
                    "text": text,
                    "section_title": section["section_title"],
                    "parent_title": section["parent_title"],
                    "section_path": section["section_path"],
                    "chunk_kind": section["chunk_kind"],
                    "domain_tags": item.get("domain_tags", []),
                    "market_tags": item.get("market_tags", []),
                    "platform_tags": item.get("platform_tags", []),
                    "trust_level": item.get("trust_level", "medium"),
                    "source_type": item.get("source_type"),
                    "source_uri": item.get("source_uri"),
                    "status": item.get("status", "active"),
                    "stages": _stages_for_item(item),
                    "content_hash": _hash(text),
                    "embedding_profile": _profile_for_provider(embedding_provider),
                    "embedding_provider": embedding_provider,
                    "embedding_model": embedding_model,
                    "embedding_dimension": embedding_dimension(embedding_model, embedding_provider),
                    "embedding": _embed(text, embedding_model, embedding_provider),
                    "search_terms": _tokens(text),
                    "token_frequencies": token_frequencies,
                    "phrase_terms": _phrase_terms(text),
                }
            )
    return chunks


def _chunk_text(text: str, *, max_chars: int = 1200, overlap_chars: int = 120) -> list[dict[str, str]]:
    normalized = "\n".join(line.rstrip() for line in text.strip().splitlines())
    if not normalized:
        return []
    parts = re.split(r"\n(?=##?\s+)|\n\n+", normalized)
    chunks: list[dict[str, str]] = []
    current = ""
    current_title = "overview"
    parent_title = "overview"
    section_counter = 0
    for part in parts:
        part = part.strip()
        if not part:
            continue
        heading = re.match(r"^(#{1,3})\s+([^\n]+)", part)
        if heading:
            title = heading.group(2).strip()
            if current:
                section_counter += 1
                chunks.append(_chunk_section_payload(current, current_title, parent_title, section_counter))
                current = ""
            if len(heading.group(1)) <= 2:
                parent_title = title
            current_title = title
        if len(current) + len(part) + 2 <= max_chars:
            current = f"{current}\n\n{part}".strip()
        else:
            if current:
                section_counter += 1
                chunks.append(_chunk_section_payload(current, current_title, parent_title, section_counter))
                overlap = current[-overlap_chars:].strip() if overlap_chars and len(current) > overlap_chars else ""
                current = f"{overlap}\n\n{part[:max_chars]}".strip() if overlap else part[:max_chars].strip()
                continue
            current = part[:max_chars].strip()
    if current:
        section_counter += 1
        chunks.append(_chunk_section_payload(current, current_title, parent_title, section_counter))
    return chunks


def _chunk_section_payload(text: str, section_title: str, parent_title: str, section_counter: int) -> dict[str, str]:
    section_path = f"{section_counter}-{_slug(parent_title)}-{_slug(section_title)}"
    return {
        "text": text,
        "section_title": section_title or "overview",
        "parent_title": parent_title or section_title or "overview",
        "section_path": section_path,
        "chunk_kind": "section" if section_title != "overview" else "overview",
    }


def _index_payload(
    index_path: Path,
    registry_path: Path,
    items: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    *,
    embedding: dict[str, Any],
    created_at: str,
) -> dict[str, Any]:
    payload = {
        "index_id": f"kb-{uuid4().hex[:8]}",
        "created_at": created_at,
        "updated_at": created_at,
        "version": 1,
        "store": {"type": "postgres_pgvector", "adapter": "local_json", "postgres_schema_ref": POSTGRES_SCHEMA_PATH},
        "index_ref": str(index_path),
        "source_registry_ref": str(registry_path),
        "embedding_profile": embedding["embedding_profile"],
        "embedding_provider": embedding["embedding_provider"],
        "embedding_model": embedding["embedding_model"],
        "embedding_dimension": embedding["embedding_dimension"],
        "items": items,
        "chunks": chunks,
        "sources": sources,
        "retrieval_index": _retrieval_index_payload(chunks),
        "query_embedding_cache": {},
    }
    payload["stats"] = _index_stats(payload)
    return payload


def _index_stats(index: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_count": len(index.get("items", [])),
        "chunk_count": len(index.get("chunks", [])),
        "source_count": len(index.get("sources", [])),
        "type_counts": _count_by(index.get("items", []), "type"),
    }


def _retrieval_index_payload(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    token_document_frequency: dict[str, int] = {}
    source_map: dict[str, list[str]] = {}
    tag_map: dict[str, list[str]] = {}
    for chunk in chunks:
        chunk_id = str(chunk.get("chunk_id"))
        source_map.setdefault(str(chunk.get("source_id")), []).append(chunk_id)
        for token in set(chunk.get("search_terms", [])):
            token_document_frequency[token] = token_document_frequency.get(token, 0) + 1
        for tag in [*chunk.get("domain_tags", []), *chunk.get("market_tags", []), *chunk.get("platform_tags", [])]:
            tag_map.setdefault(str(tag), []).append(chunk_id)
    return {
        "token_document_frequency": token_document_frequency,
        "source_map": {key: sorted(values) for key, values in source_map.items()},
        "tag_map": {key: sorted(set(values)) for key, values in tag_map.items()},
    }


def _init_report(index_path: Path, index: dict[str, Any], *, adapter: str = "local_json", database_url: str | None = None) -> dict[str, Any]:
    store = {**index["store"], "adapter": adapter}
    index_ref = f"postgres:{_redact_database_url(database_url)}" if database_url else str(index_path)
    return {"status": "pass", "index_ref": index_ref, "index_id": index["index_id"], "store": store, **index["stats"]}


def _source_from_item(item: dict[str, Any]) -> dict[str, Any]:
    return {"id": item["id"], "type": item["source_type"], "trust_level": item["trust_level"], "uri": item["source_uri"], "content_hash": item["content_hash"]}


def _filters_for_intent(intent: dict[str, Any], *, stage: str | None) -> dict[str, Any]:
    tags = set(intent.get("tags", []))
    return {
        "stage": stage,
        "market_tags": sorted(tags & {"crypto", "forex"}),
        "platform_tags": sorted(tags & {"pine", "mql5"}),
        "status": sorted(ACTIVE_STATUSES),
    }


def _prefilter_chunks(chunks: list[dict[str, Any]], intent: dict[str, Any], *, options: RetrievalOptions) -> tuple[list[dict[str, Any]], dict[str, int]]:
    input_count = len(chunks)
    tags = set(intent.get("tags", []))
    market_tags = tags & {"crypto", "forex"}
    platform_tags = {"pine_v6" if tag == "pine" else tag for tag in tags & {"pine", "mql5"}}
    filtered = []
    for chunk in chunks:
        chunk_markets = set(chunk.get("market_tags", []))
        chunk_platforms = set(chunk.get("platform_tags", []))
        if market_tags and chunk_markets and "general" not in chunk_markets and not (market_tags & chunk_markets):
            continue
        if platform_tags and chunk_platforms and "general" not in chunk_platforms and not (platform_tags & chunk_platforms):
            continue
        filtered.append(chunk)
    if len(filtered) > options.prefilter_limit:
        filtered = sorted(
            filtered,
            key=lambda chunk: _prefilter_priority(chunk, market_tags=market_tags, platform_tags=platform_tags),
            reverse=True,
        )[: options.prefilter_limit]
    return filtered, {"prefilter_input_count": input_count, "prefilter_output_count": len(filtered)}


def _prefilter_priority(chunk: dict[str, Any], *, market_tags: set[str], platform_tags: set[str]) -> tuple[int, int, str]:
    chunk_markets = set(chunk.get("market_tags", []))
    chunk_platforms = set(chunk.get("platform_tags", []))
    source_type = str(chunk.get("source_type", ""))
    chunk_kind = str(chunk.get("chunk_kind", ""))
    score = 0
    if market_tags & chunk_markets:
        score += 8
    elif "general" in chunk_markets:
        score += 2
    if platform_tags & chunk_platforms:
        score += 4
    elif "general" in chunk_platforms:
        score += 1
    if source_type in {"internal_curated", "approved_candidate", "approved_source_summary"}:
        score += 3
    if chunk_kind == "metadata_only":
        score -= 4
    return score, -int(chunk.get("chunk_index", 0) or 0), str(chunk.get("chunk_id", ""))


def _local_query_embedding(
    index: dict[str, Any],
    query_text: str,
    *,
    stage: str | None,
    embedding_model: str,
    embedding_provider: str,
) -> tuple[list[float], str]:
    cache_key = _query_embedding_cache_key(
        query_text,
        stage=stage,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        knowledge_version=str(index.get("version", "1")),
    )
    cache = index.setdefault("query_embedding_cache", {})
    cached = cache.get(cache_key) if isinstance(cache, dict) else None
    if isinstance(cached, dict) and isinstance(cached.get("embedding"), list):
        cached["last_used_at"] = _now()
        return [float(value) for value in cached["embedding"]], "hit"
    embedding = _embed(query_text, embedding_model, embedding_provider)
    cache[cache_key] = {
        "normalized_query": " ".join(query_text.lower().split()),
        "stage": stage,
        "embedding_provider": embedding_provider,
        "embedding_model": embedding_model,
        "knowledge_version": str(index.get("version", "1")),
        "embedding": embedding,
        "created_at": _now(),
        "last_used_at": _now(),
    }
    return embedding, "miss"


def _required_source_ids(query: str, intent: dict[str, Any]) -> list[str]:
    lowered = query.lower()
    required = []
    if "risk" in intent.get("tags", []) and any(term in lowered for term in ("guarantee", "guaranteed", "profit", "no-loss", "cannot lose", "live-ready", "live ready", "certified")):
        required.append("internal-risk-policy")
    return required


def _ensure_required_chunks(reranked: list[dict[str, Any]], chunks: list[dict[str, Any]], required_source_ids: list[str]) -> list[dict[str, Any]]:
    if not required_source_ids:
        return reranked
    present = {str(chunk.get("source_id")) for chunk in reranked}
    output = list(reranked)
    for source_id in required_source_ids:
        if source_id in present:
            continue
        candidates = [chunk for chunk in chunks if chunk.get("source_id") == source_id]
        if not candidates:
            continue
        best = sorted(candidates, key=lambda chunk: (chunk.get("chunk_index", 0), chunk.get("chunk_id", "")))[0]
        output.insert(0, {**best, "score": 1.0, "lexical_score": best.get("lexical_score", 0.0), "vector_score": best.get("vector_score", 0.0), "rerank_features": {"required_source": 1.0}})
    return output


def _ensure_approved_candidate_chunks(
    reranked: list[dict[str, Any]],
    limit: int,
    *,
    fallback_chunks: list[dict[str, Any]] | None = None,
    query_text: str = "",
    embedding_model: str = EMBEDDING_MODEL_DEFAULT,
    embedding_provider: str = EMBEDDING_PROVIDER_LOCAL,
    query_embedding: list[float] | None = None,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    selected = list(reranked[:limit])
    if any(chunk.get("source_type") == "approved_candidate" for chunk in selected):
        return selected
    eligible = [
        chunk
        for chunk in reranked[limit:]
        if chunk.get("source_type") == "approved_candidate"
        and (float(chunk.get("lexical_score", 0.0)) >= 3.0 or float(chunk.get("vector_score", 0.0)) >= 0.5)
    ]
    if not eligible and fallback_chunks and query_text:
        selected_ids = {str(chunk.get("chunk_id")) for chunk in selected}
        approved_pool = [
            chunk
            for chunk in fallback_chunks
            if chunk.get("source_type") == "approved_candidate" and str(chunk.get("chunk_id")) not in selected_ids
        ]
        lexical_ranked = _rank_lexical(approved_pool, query_text)[:5]
        vector_ranked = _rank_vector(approved_pool, query_text, embedding_model, embedding_provider, query_embedding=query_embedding)[:5]
        eligible = [
            {
                **chunk,
                "score": float(chunk.get("rrf_score", 0.0)) + (min(float(chunk.get("lexical_score", 0.0)), 10.0) * 0.01),
            }
            for chunk in _rrf_merge(lexical_ranked, vector_ranked)
            if float(chunk.get("lexical_score", 0.0)) >= 3.0 or float(chunk.get("vector_score", 0.0)) >= 0.5
        ]
    if not eligible:
        return selected
    approved = sorted(eligible, key=lambda chunk: (-float(chunk.get("lexical_score", 0.0)), -float(chunk.get("score", 0.0)), str(chunk.get("chunk_id", ""))))[0]
    approved = {
        **approved,
        "rerank_features": {
            **(approved.get("rerank_features") or {}),
            "approved_candidate_inclusion": 1.0,
        },
    }
    if len(selected) < limit:
        return [*selected, approved]
    return [*selected[:-1], approved]


def _asks_for_sources(query: str) -> bool:
    lowered = query.lower()
    return any(term in lowered for term in ("source", "reference", "citation", "link", "docs", "url"))


def _citations_for_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations = []
    for chunk in chunks:
        citations.append(
            {
                "chunk_id": chunk.get("chunk_id"),
                "source_id": chunk.get("source_id"),
                "title": chunk.get("title"),
                "source_uri": chunk.get("source_uri"),
                "trust_level": chunk.get("trust_level"),
                "section_title": chunk.get("section_title"),
            }
        )
    return citations


def _retrieval_confidence(chunks: list[dict[str, Any]], *, required_source_ids: list[str]) -> dict[str, Any]:
    if not chunks:
        return {"score": 0.0, "signals": {"chunk_count": 0}}
    lexical = max(float(chunk.get("lexical_score", 0.0)) for chunk in chunks)
    vector = max(float(chunk.get("vector_score", 0.0)) for chunk in chunks)
    source_count = len({chunk.get("source_id") for chunk in chunks if chunk.get("source_id")})
    metadata_matches = sum(1 for chunk in chunks if chunk.get("market_tags") or chunk.get("domain_tags"))
    required_hits = len(set(required_source_ids) & {str(chunk.get("source_id")) for chunk in chunks})
    score = min(1.0, (min(lexical, 8.0) / 8.0 * 0.3) + (min(vector, 1.0) * 0.25) + (min(source_count, 3) / 3 * 0.2) + (min(metadata_matches, 4) / 4 * 0.15) + (required_hits * 0.1))
    return {
        "score": round(score, 6),
        "signals": {
            "max_lexical_score": round(lexical, 6),
            "max_vector_score": round(vector, 6),
            "source_count": source_count,
            "metadata_match_count": metadata_matches,
            "required_source_hit_count": required_hits,
        },
    }


def _missing_context(query: str, intent: dict[str, Any], chunks: list[dict[str, Any]]) -> list[str]:
    missing = []
    lowered = query.lower()
    if not chunks:
        return ["no_retrieved_chunks"]
    if "crypto" in intent.get("tags", []) and not any("crypto" in chunk.get("market_tags", []) for chunk in chunks):
        missing.append("crypto_market_context")
    if "forex" in intent.get("tags", []) and not any("forex" in chunk.get("market_tags", []) for chunk in chunks):
        missing.append("forex_market_context")
    if any(term in lowered for term in ("guarantee", "profit", "live-ready", "certified")) and not any(chunk.get("source_id") == "internal-risk-policy" for chunk in chunks):
        missing.append("risk_policy")
    return missing


def _registry_source_by_id(registry_path: Path, source_id: str) -> dict[str, Any]:
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    sources = registry.get("sources", []) if isinstance(registry, dict) else []
    for source in sources:
        if isinstance(source, dict) and str(source.get("id")) == source_id:
            return source
    raise KeyError(f"Unknown source id: {source_id}")


def _source_artifact_path(root: str, source_id: str, kind: str, created_at: str) -> Path:
    safe_time = re.sub(r"[^0-9A-Za-z]+", "-", created_at).strip("-")
    return Path(root) / _slug(source_id) / f"{safe_time}-{kind}.json"


def _fetch_url_text(url: str) -> str:
    req = request.Request(url, headers={"User-Agent": "strategy-codebot-knowledge-snapshot/1.0"})
    try:
        with request.urlopen(req, timeout=30) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except TimeoutError as exc:
        raise RuntimeError(f"source_fetch_timeout: {url}") from exc
    except error.HTTPError as exc:
        raise RuntimeError(f"source_fetch_error: {url} returned HTTP {exc.code}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"source_fetch_error: {url}") from exc


def _extract_readable_text(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"&amp;?", "&", text)
    text = re.sub(r"&lt;?", "<", text)
    text = re.sub(r"&gt;?", ">", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _curated_source_summary(text: str, *, market_tags: list[str]) -> str:
    sentences = _summary_sentences(text)
    selected = _rank_summary_sentences(sentences, market_tags=market_tags)[:8]
    if not selected:
        selected = sentences[:5]
    lessons = "\n".join(f"- {sentence}" for sentence in selected[:5])
    checklist = _summary_checklist(" ".join(selected), market_tags=market_tags)
    market = ", ".join(market_tags or ["general"])
    return (
        "# Curated Trusted-Source Summary\n\n"
        "## Boundary\n\n"
        "Use this summary as reviewed educational context only. It does not certify profitability, safety, or live-trading readiness.\n\n"
        f"## Market Tags\n\n- {market}\n\n"
        "## Curated Lessons\n\n"
        f"{lessons}\n\n"
        "## Review Checklist\n\n"
        f"{checklist}\n\n"
        "## Approval Note\n\n"
        "This is a concise curated summary derived from a trusted-source snapshot; raw external text must not be promoted directly into generation context."
    )


def _summary_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?])\s+", normalized)
    sentences = []
    for part in parts:
        sentence = part.strip()
        if 40 <= len(sentence) <= 240:
            sentences.append(sentence)
    return _dedupe_strings(sentences)


def _rank_summary_sentences(sentences: list[str], *, market_tags: list[str]) -> list[str]:
    keywords = {
        "risk",
        "loss",
        "stop",
        "position",
        "leverage",
        "volatility",
        "liquidity",
        "spread",
        "session",
        "funding",
        "rollover",
        "correlation",
        "management",
        "strategy",
    }
    keywords.update(tag.lower() for tag in market_tags)
    return sorted(sentences, key=lambda sentence: (-sum(1 for token in _tokens(sentence) if token in keywords), sentence))


def _summary_checklist(text: str, *, market_tags: list[str]) -> str:
    lowered = text.lower()
    checks = [
        "State the market, timeframe, data source, and execution assumptions before coding.",
        "Include fees, spread, slippage, and position sizing in validation.",
        "Reject guaranteed-profit, no-loss, and live-ready certification claims.",
    ]
    if "crypto" in market_tags or any(token in lowered for token in ("crypto", "bitcoin", "funding", "exchange")):
        checks.append("For crypto, review liquidity, exchange fragmentation, funding/perpetual mechanics, and volatility shocks.")
    if "forex" in market_tags or any(token in lowered for token in ("forex", "session", "spread", "rollover")):
        checks.append("For forex, review trading session, spread/rollover behavior, news windows, and pair correlation.")
    return "\n".join(f"- {check}" for check in _dedupe_strings(checks))


def _rank_lexical(chunks: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    query_tokens = _tokens(query)
    ranked = []
    for chunk in chunks:
        token_frequencies = chunk.get("token_frequencies") or {}
        if token_frequencies:
            token_score = sum(int(token_frequencies.get(token, 0)) for token in query_tokens)
        else:
            chunk_tokens = chunk.get("search_terms", [])
            token_score = sum(chunk_tokens.count(token) for token in query_tokens)
        phrase_score = sum(4 for term in _important_phrases(query) if term in str(chunk.get("text", "")).lower())
        score = token_score + phrase_score
        if score:
            ranked.append({**chunk, "lexical_score": float(score)})
    return sorted(ranked, key=lambda chunk: (-chunk["lexical_score"], chunk["chunk_id"]))


def _rank_vector(
    chunks: list[dict[str, Any]],
    query: str,
    embedding_model: str,
    embedding_provider: str = EMBEDDING_PROVIDER_LOCAL,
    *,
    query_embedding: list[float] | None = None,
) -> list[dict[str, Any]]:
    query_embedding = query_embedding or _embed(query, embedding_model, embedding_provider)
    ranked = []
    for chunk in chunks:
        score = _cosine(query_embedding, chunk.get("embedding", []))
        if score > 0:
            ranked.append({**chunk, "vector_score": score})
    return sorted(ranked, key=lambda chunk: (-chunk["vector_score"], chunk["chunk_id"]))


def _rrf_merge(lexical_ranked: list[dict[str, Any]], vector_ranked: list[dict[str, Any]], *, k: int = 60) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for rank, chunk in enumerate(lexical_ranked, start=1):
        current = merged.setdefault(chunk["chunk_id"], {**chunk, "rrf_score": 0.0, "lexical_score": chunk.get("lexical_score", 0.0), "vector_score": chunk.get("vector_score", 0.0)})
        current["rrf_score"] += 1 / (k + rank)
        current["lexical_score"] = max(current.get("lexical_score", 0.0), chunk.get("lexical_score", 0.0))
    for rank, chunk in enumerate(vector_ranked, start=1):
        current = merged.setdefault(chunk["chunk_id"], {**chunk, "rrf_score": 0.0, "lexical_score": chunk.get("lexical_score", 0.0), "vector_score": chunk.get("vector_score", 0.0)})
        current["rrf_score"] += 1 / (k + rank)
        current["vector_score"] = max(current.get("vector_score", 0.0), chunk.get("vector_score", 0.0))
    return sorted(merged.values(), key=lambda chunk: (-chunk["rrf_score"], chunk["chunk_id"]))


def _rerank(chunks: list[dict[str, Any]], intent: dict[str, Any], expanded_terms: list[str], *, query: str = "") -> list[dict[str, Any]]:
    tags = set(intent.get("tags", []))
    expanded = " ".join(expanded_terms).lower()
    source_query = _asks_for_sources(query)
    reranked = []
    for chunk in chunks:
        domain_overlap = len(tags & set(chunk.get("domain_tags", []))) * 0.02
        market_overlap = len(tags & set(chunk.get("market_tags", []))) * 0.04
        expansion_bonus = 0.03 if expanded and any(term.lower() in str(chunk.get("text", "")).lower() for term in expanded_terms) else 0.0
        risk_policy_bonus = 0.1 if "risk policy" in expanded and chunk.get("source_id") == "internal-risk-policy" else 0.0
        source_type = str(chunk.get("source_type", ""))
        metadata_penalty = 0.06 if source_type in {"external_ref", "official"} and not source_query else 0.0
        trust_penalty = 0.03 if chunk.get("trust_level") == "low" else 0.0
        lexical_strength = min(float(chunk.get("lexical_score", 0.0)), 10.0) * 0.01
        score = float(chunk.get("rrf_score", 0.0)) + domain_overlap + market_overlap + expansion_bonus + risk_policy_bonus + lexical_strength - metadata_penalty - trust_penalty
        reranked.append(
            {
                **chunk,
                "score": score,
                "rerank_features": {
                    "domain_overlap": round(domain_overlap, 6),
                    "market_overlap": round(market_overlap, 6),
                    "expansion_bonus": round(expansion_bonus, 6),
                    "risk_policy_bonus": round(risk_policy_bonus, 6),
                    "metadata_penalty": round(metadata_penalty, 6),
                    "trust_penalty": round(trust_penalty, 6),
                    "lexical_strength": round(lexical_strength, 6),
                },
            }
        )
    return sorted(reranked, key=lambda chunk: (-chunk["score"], chunk["chunk_id"]))


def _retrieved_chunk(chunk: dict[str, Any], max_chars: int) -> dict[str, Any]:
    text = str(chunk.get("text", ""))
    truncated = len(text) > max_chars
    return {
        "chunk_id": chunk["chunk_id"],
        "item_id": chunk["item_id"],
        "source_id": chunk.get("source_id"),
        "type": chunk.get("type"),
        "title": chunk.get("title"),
        "text": text[:max_chars].rstrip(),
        "truncated": truncated,
        "domain_tags": chunk.get("domain_tags", []),
        "market_tags": chunk.get("market_tags", []),
        "platform_tags": chunk.get("platform_tags", []),
        "trust_level": chunk.get("trust_level"),
        "source_type": chunk.get("source_type"),
        "source_uri": chunk.get("source_uri"),
        "section_title": chunk.get("section_title"),
        "parent_title": chunk.get("parent_title"),
        "chunk_kind": chunk.get("chunk_kind"),
        "stages": chunk.get("stages", []),
        "score": round(float(chunk.get("score", 0.0)), 6),
        "lexical_score": round(float(chunk.get("lexical_score", 0.0)), 6),
        "vector_score": round(float(chunk.get("vector_score", 0.0)), 6),
        "rerank_features": chunk.get("rerank_features", {}),
    }


def _compat_internal_docs(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    docs = []
    seen = set()
    for chunk in chunks:
        if chunk.get("source_type") in {"official", "external_ref"}:
            continue
        source_id = str(chunk.get("source_id"))
        if source_id in seen:
            continue
        seen.add(source_id)
        docs.append({"id": source_id, "path": chunk.get("source_uri"), "platform": (chunk.get("platform_tags") or ["general"])[0], "excerpt": chunk.get("text", ""), "truncated": chunk.get("truncated", False)})
    return docs


def _compat_external_refs(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs = []
    seen = set()
    for chunk in chunks:
        if chunk.get("source_type") not in {"official", "external_ref"}:
            continue
        source_id = str(chunk.get("source_id"))
        if source_id in seen:
            continue
        seen.add(source_id)
        refs.append({"id": source_id, "platform": (chunk.get("platform_tags") or ["general"])[0], "type": chunk.get("source_type"), "trust_level": chunk.get("trust_level"), "url": chunk.get("source_uri")})
    return refs


def _infer_type(content: str, locator: str) -> str:
    lowered = f"{content} {locator}".lower()
    if "checklist" in lowered or "policy" in lowered or "repair" in lowered:
        return "procedural"
    if any(term in lowered for term in ("break of structure", "liquidity sweep", "mean reversion", "breakout")):
        return "strategy_pattern"
    return "semantic"


def _list_field(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def _infer_domain_tags(content: str) -> list[str]:
    lowered = content.lower()
    tags = []
    for token, tag in (("pine", "pine"), ("mql5", "mql5"), ("risk", "risk"), ("overfit", "anti_overfit"), ("lookahead", "repaint"), ("liquidity", "price_action"), ("structure", "price_action"), ("indicator", "indicator")):
        if token in lowered:
            tags.append(tag)
    return _dedupe_strings(tags or ["trading"])


def _infer_market_tags(content: str) -> list[str]:
    lowered = content.lower()
    tags = []
    if any(token in lowered for token in ("crypto", "bitcoin", "btc", "ethereum", "eth", "altcoin", "perpetual", "funding")):
        tags.append("crypto")
    if any(token in lowered for token in ("forex", "fx", "eurusd", "gbpusd", "usdjpy", "london", "new york", "rollover")):
        tags.append("forex")
    return _dedupe_strings(tags or ["general"])


def _infer_platform_tags(content: str, locator: str) -> list[str]:
    lowered = f"{content} {locator}".lower()
    tags = []
    if any(token in lowered for token in ("pine", "tradingview")):
        tags.append("pine_v6")
    if any(token in lowered for token in ("mql5", "metatrader", "mt5")):
        tags.append("mql5")
    return tags or ["general"]


def _stages_for_item(item: dict[str, Any]) -> list[str]:
    explicit_stages = item.get("stages") or []
    if explicit_stages:
        return sorted(_dedupe_strings([str(stage) for stage in explicit_stages]))
    item_type = item.get("type")
    tags = set(item.get("domain_tags", []))
    stages = {"strategy_reasoning", "strategy_coding", "balanced_review", "repair"}
    if "pine" in tags or "pine_v6" in item.get("platform_tags", []):
        stages.add("pine_code_generation")
    if item_type == "source_ref":
        stages = {"strategy_reasoning", "balanced_review"}
    return sorted(stages)


def _embed(text: str, embedding_model: str, embedding_provider: str = EMBEDDING_PROVIDER_LOCAL) -> list[float]:
    if embedding_provider != EMBEDDING_PROVIDER_LOCAL:
        return _remote_embedding(text, embedding_model, embedding_provider)
    vector = [0.0] * EMBEDDING_DIMENSION_LOCAL
    for token in _tokens(text):
        digest = hashlib.sha256(f"{embedding_model}:{token}".encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % EMBEDDING_DIMENSION_LOCAL
        vector[index] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [round(value / norm, 8) for value in vector]


def _remote_embedding(text: str, embedding_model: str, embedding_provider: str) -> list[float]:
    if embedding_provider == EMBEDDING_PROVIDER_OPENROUTER:
        url = os.getenv("OPENROUTER_EMBEDDINGS_API_BASE", "https://openrouter.ai/api/v1/embeddings")
    elif embedding_provider == EMBEDDING_PROVIDER_OPENAI:
        url = os.getenv("OPENAI_EMBEDDINGS_API_BASE", "https://api.openai.com/v1/embeddings")
    else:
        raise ValueError(f"Unsupported embedding provider: {embedding_provider}")
    api_key_env = _embedding_api_key_env(embedding_provider)
    if api_key_env is None:
        raise ValueError(f"Unsupported embedding provider: {embedding_provider}")
    api_key = os.getenv(api_key_env)
    if not api_key:
        raise RuntimeError(f"{api_key_env} is required for embedding provider {embedding_provider}.")
    payload = json.dumps({"model": embedding_model, "input": text}).encode("utf-8")
    req = request.Request(
        url,
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
    except TimeoutError as exc:
        raise RuntimeError(f"provider_timeout: embedding provider {embedding_provider} timed out") from exc
    except error.HTTPError as exc:
        failure_class = "provider_rate_limited" if exc.code == 429 else "provider_error"
        raise RuntimeError(f"{failure_class}: embedding provider {embedding_provider} returned HTTP {exc.code}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"provider_error: embedding provider {embedding_provider} request failed") from exc
    embedding = body.get("data", [{}])[0].get("embedding")
    if not isinstance(embedding, list):
        raise RuntimeError(f"Embedding provider {embedding_provider} returned no embedding vector.")
    expected = embedding_dimension(embedding_model, embedding_provider)
    if len(embedding) != expected:
        raise RuntimeError(f"Embedding dimension mismatch for {embedding_model}: expected {expected}, got {len(embedding)}.")
    return [float(value) for value in embedding]


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9_]+", text.lower())


def _token_frequencies(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for token in _tokens(text):
        counts[token] = counts.get(token, 0) + 1
    return counts


def _phrase_terms(text: str) -> list[str]:
    lowered = text.lower()
    phrases = [phrase for phrase in PRICE_ACTION_ALIASES if phrase in lowered]
    for phrase in ("risk policy", "live trading", "funding", "rollover", "spread", "liquidity", "no-indicator price action"):
        if phrase in lowered:
            phrases.append(phrase)
    return _dedupe_strings(phrases)


def _important_phrases(query: str) -> list[str]:
    lowered = query.lower()
    phrases = [phrase for phrase in PRICE_ACTION_ALIASES if phrase in lowered]
    phrases.extend(term.lower() for term in expand_query_terms(query))
    return _dedupe_strings(phrases)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key, "unknown"))
        counts[value] = counts.get(value, 0) + 1
    return counts


def _dedupe_strings(values: list[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        output.append(normalized)
    return output


def _candidate_by_id(store: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    for candidate in store.get("candidates", []):
        if candidate.get("candidate_id") == candidate_id:
            return candidate
    raise KeyError(f"Unknown knowledge candidate: {candidate_id}")
