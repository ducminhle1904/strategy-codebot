from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from strategy_codebot.paths import repo_root, resolve_repo_path
from strategy_codebot.knowledge_base import build_retrieved_knowledge_context, default_index_path, ensure_database_url

KNOWLEDGE_CONTEXT_PATH = "knowledge-context.json"
KNOWLEDGE_CONTEXT_AUTO = "auto"
KNOWLEDGE_CONTEXT_OFF = "off"
KNOWLEDGE_CONTEXT_MODES = {KNOWLEDGE_CONTEXT_AUTO, KNOWLEDGE_CONTEXT_OFF}

MAX_DOCS_PER_CONTEXT = 5
MAX_CHARS_PER_DOC = 1800

INTERNAL_DOCS = {
    "pine_v6_rules": {
        "path": "docs/trading/pine-v6-rules.md",
        "platform": "pine_v6",
        "stages": ["strategy_reasoning", "strategy_coding", "pine_code_generation", "balanced_review", "repair"],
    },
    "risk_policy": {
        "path": "docs/trading/risk-policy.md",
        "platform": "general",
        "stages": ["strategy_reasoning", "strategy_coding", "balanced_review", "repair"],
    },
    "anti_overfit_checklist": {
        "path": "docs/trading/anti-overfit-checklist.md",
        "platform": "general",
        "stages": ["balanced_review", "repair"],
    },
    "mql5_rules": {
        "path": "docs/trading/mql5-rules.md",
        "platform": "mql5",
        "stages": ["strategy_reasoning", "strategy_coding", "balanced_review", "repair"],
    },
    "crypto_playbook": {
        "path": "docs/trading/strategy-playbooks/crypto-playbook.md",
        "platform": "general",
        "stages": ["strategy_reasoning", "balanced_review", "repair"],
    },
    "forex_playbook": {
        "path": "docs/trading/strategy-playbooks/forex-playbook.md",
        "platform": "general",
        "stages": ["strategy_reasoning", "balanced_review", "repair"],
    },
    "strategy_patterns": {
        "path": "docs/trading/strategy-playbooks/strategy-patterns.md",
        "platform": "general",
        "stages": ["strategy_reasoning", "strategy_coding", "balanced_review", "repair"],
    },
    "trading_skill_integration": {
        "path": "docs/trading/strategy-playbooks/trading-skill-integration.md",
        "platform": "general",
        "stages": ["strategy_reasoning", "strategy_coding", "balanced_review", "repair"],
    },
}


def build_knowledge_context(prompt: str, *, source_registry_path: Path | None = None) -> dict[str, Any]:
    index_path = default_index_path()
    if ensure_database_url() or index_path.exists():
        try:
            return build_retrieved_knowledge_context(prompt, index_path=index_path)
        except Exception as exc:
            fallback = _build_static_knowledge_context(prompt, source_registry_path=source_registry_path)
            fallback.update(
                {
                    "knowledge_health_status": "degraded",
                    "knowledge_context_status": "degraded",
                    "failure_class": "knowledge_unavailable",
                    "fallback": "static_curated_context",
                    "degraded_reason": f"{type(exc).__name__}: {str(exc)[:300]}",
                }
            )
            return fallback
    return _build_static_knowledge_context(prompt, source_registry_path=source_registry_path)


def _build_static_knowledge_context(prompt: str, *, source_registry_path: Path | None = None) -> dict[str, Any]:
    registry_path = resolve_repo_path(source_registry_path or repo_root() / "configs" / "source-registry.yaml")
    prompt_text = prompt.lower()
    wants_mql5 = any(token in prompt_text for token in ("mql5", "mt5", "metatrader", "expert advisor", "both-platform", "both platform"))
    wants_pine = not wants_mql5 or any(token in prompt_text for token in ("pine", "tradingview", "indicator", "strategy"))
    wants_crypto = any(token in prompt_text for token in ("crypto", "bitcoin", "btc", "ethereum", "eth", "altcoin", "perpetual", "funding", "exchange"))
    wants_forex = any(token in prompt_text for token in ("forex", "fx", "eurusd", "gbpusd", "usdjpy", "session", "london", "new york", "rollover", "spread"))
    wants_review_skill = any(
        token in prompt_text
        for token in (
            "review",
            "overfit",
            "backtest",
            "optimize",
            "curve",
            "robust",
            "sample size",
            "position sizing",
            "risk gate",
            "invalidation",
            "price action",
            "lesson",
            "postmortem",
        )
    )
    selected_keys = ["risk_policy"]
    if wants_pine:
        selected_keys.insert(0, "pine_v6_rules")
    if wants_review_skill:
        selected_keys.append("trading_skill_integration")
    if wants_crypto:
        selected_keys.append("crypto_playbook")
    if wants_forex:
        selected_keys.append("forex_playbook")
    if wants_crypto or wants_forex or any(token in prompt_text for token in ("trend", "mean reversion", "breakout", "volatility", "position sizing")):
        selected_keys.append("strategy_patterns")
    selected_keys.append("anti_overfit_checklist")
    if wants_mql5:
        selected_keys.append("mql5_rules")

    internal_docs = [_internal_doc_payload(key) for key in _dedupe(selected_keys)[:MAX_DOCS_PER_CONTEXT]]
    external_refs = _external_refs(registry_path, wants_pine=wants_pine, wants_mql5=wants_mql5, wants_crypto=wants_crypto, wants_forex=wants_forex)
    stage_relevance = _stage_relevance(internal_docs)
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "mode": KNOWLEDGE_CONTEXT_AUTO,
        "source_registry": str(registry_path),
        "selection_reasons": {
            "pine_context": wants_pine,
            "mql5_context": wants_mql5,
            "crypto_context": wants_crypto,
            "forex_context": wants_forex,
            "risk_context": True,
            "external_refs_only": True,
        },
        "internal_docs": internal_docs,
        "external_refs": external_refs,
        "stage_relevance": stage_relevance,
        "context_refs": [doc["path"] for doc in internal_docs] + [f"source:{source['id']}" for source in external_refs],
        "truncation": {
            "max_docs": MAX_DOCS_PER_CONTEXT,
            "max_chars_per_doc": MAX_CHARS_PER_DOC,
            "truncated_doc_ids": [doc["id"] for doc in internal_docs if doc["truncated"]],
        },
    }


def compact_knowledge_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": context.get("mode"),
        "store": context.get("store"),
        "retrieval_query": context.get("retrieval_query"),
        "intent": context.get("intent"),
        "expanded_terms": context.get("expanded_terms", []),
        "embedding_provider": context.get("embedding_provider"),
        "embedding_model": context.get("embedding_model"),
        "embedding_latency_ms": context.get("embedding_latency_ms"),
        "db_search_latency_ms": context.get("db_search_latency_ms"),
        "cache_hit": context.get("cache_hit"),
        "cache_layer": context.get("cache_layer"),
        "cache_key_hash": context.get("cache_key_hash"),
        "cache_saved_ms": context.get("cache_saved_ms"),
        "cache_ttl_seconds": context.get("cache_ttl_seconds"),
        "cache_bypass_reason": context.get("cache_bypass_reason"),
        "retrieval_cache_status": context.get("retrieval_cache_status"),
        "embedding_cache_status": context.get("embedding_cache_status"),
        "knowledge_health_status": context.get("knowledge_health_status"),
        "retrieved_chunks": context.get("retrieved_chunks", []),
        "citations": context.get("citations", []),
        "retrieval_confidence": context.get("retrieval_confidence"),
        "low_confidence": context.get("low_confidence"),
        "missing_context": context.get("missing_context", []),
        "filters_applied": context.get("filters_applied", {}),
        "required_source_hits": context.get("required_source_hits", []),
        "internal_docs": context.get("internal_docs", []),
        "external_refs": context.get("external_refs", []),
        "stage_relevance": context.get("stage_relevance", {}),
        "context_refs": context.get("context_refs", []),
        "metrics": context.get("metrics", {}),
    }


def knowledge_metadata(context: dict[str, Any] | None) -> dict[str, Any]:
    if not context:
        return {
            "knowledge_context_ref": None,
            "knowledge_doc_ids": [],
            "external_source_ids": [],
        }
    return {
        "knowledge_context_ref": KNOWLEDGE_CONTEXT_PATH,
        "knowledge_doc_ids": _knowledge_doc_ids(context),
        "external_source_ids": [source["id"] for source in context.get("external_refs", [])],
        "knowledge_chunk_ids": [chunk["chunk_id"] for chunk in context.get("retrieved_chunks", [])],
        "knowledge_version": context.get("knowledge_version"),
        "embedding_provider": context.get("embedding_provider"),
        "embedding_model": context.get("embedding_model"),
        "retrieval_latency_ms": context.get("retrieval_latency_ms"),
        "embedding_latency_ms": context.get("embedding_latency_ms"),
        "db_search_latency_ms": context.get("db_search_latency_ms"),
        "cache_hit": context.get("cache_hit"),
        "cache_layer": context.get("cache_layer"),
        "cache_key_hash": context.get("cache_key_hash"),
        "cache_saved_ms": context.get("cache_saved_ms"),
        "cache_ttl_seconds": context.get("cache_ttl_seconds"),
        "cache_bypass_reason": context.get("cache_bypass_reason"),
        "retrieval_cache_status": context.get("retrieval_cache_status"),
        "embedding_cache_status": context.get("embedding_cache_status"),
        "knowledge_health_status": context.get("knowledge_health_status"),
        "knowledge_context_status": context.get("knowledge_context_status") or context.get("knowledge_health_status") or "pass",
        "knowledge_failure_class": context.get("failure_class"),
    }


def _knowledge_doc_ids(context: dict[str, Any]) -> list[str]:
    doc_ids = [doc["id"] for doc in context.get("internal_docs", []) if doc.get("id")]
    if doc_ids:
        return doc_ids
    return [chunk["source_id"] for chunk in context.get("retrieved_chunks", []) if chunk.get("source_type") not in {"official", "external_ref"}]


def _internal_doc_payload(doc_id: str) -> dict[str, Any]:
    config = INTERNAL_DOCS[doc_id]
    path = resolve_repo_path(Path(config["path"]))
    raw = path.read_text(encoding="utf-8")
    excerpt, truncated = _truncate_doc(raw)
    return {
        "id": doc_id,
        "path": config["path"],
        "platform": config["platform"],
        "stages": config["stages"],
        "excerpt": excerpt,
        "truncated": truncated,
        "char_count": len(raw),
    }


def _truncate_doc(text: str) -> tuple[str, bool]:
    normalized = "\n".join(line.rstrip() for line in text.strip().splitlines())
    if len(normalized) <= MAX_CHARS_PER_DOC:
        return normalized, False
    cutoff = normalized[:MAX_CHARS_PER_DOC]
    last_break = max(cutoff.rfind("\n## "), cutoff.rfind("\n- "))
    if last_break > 400:
        cutoff = cutoff[:last_break].rstrip()
    return cutoff.rstrip(), True


def _external_refs(registry_path: Path, *, wants_pine: bool, wants_mql5: bool, wants_crypto: bool, wants_forex: bool) -> list[dict[str, Any]]:
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    sources = registry.get("sources", []) if isinstance(registry, dict) else []
    refs = []
    for source in sources:
        if not isinstance(source, dict) or not source.get("url"):
            continue
        platform = source.get("platform")
        if platform == "pine_v6" and not wants_pine:
            continue
        if platform == "mql5" and not wants_mql5:
            continue
        market_tags = [str(tag) for tag in source.get("market_tags", [])]
        if "crypto" in market_tags and not wants_crypto:
            continue
        if "forex" in market_tags and not wants_forex:
            continue
        refs.append(
            {
                "id": source.get("id"),
                "platform": platform,
                "type": source.get("type"),
                "trust_level": source.get("trust_level"),
                "url": source.get("url"),
                "market_tags": market_tags,
            }
        )
    return refs


def _stage_relevance(internal_docs: list[dict[str, Any]]) -> dict[str, list[str]]:
    relevance: dict[str, list[str]] = {}
    for doc in internal_docs:
        for stage in doc.get("stages", []):
            relevance.setdefault(stage, []).append(doc["id"])
    return relevance


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output
