from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from strategy_codebot.paths import repo_root, resolve_repo_path

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
}


def build_knowledge_context(prompt: str, *, source_registry_path: Path | None = None) -> dict[str, Any]:
    registry_path = resolve_repo_path(source_registry_path or repo_root() / "configs" / "source-registry.yaml")
    prompt_text = prompt.lower()
    wants_mql5 = any(token in prompt_text for token in ("mql5", "mt5", "metatrader", "expert advisor", "both-platform", "both platform"))
    wants_pine = not wants_mql5 or any(token in prompt_text for token in ("pine", "tradingview", "indicator", "strategy"))
    selected_keys = ["risk_policy"]
    if wants_pine:
        selected_keys.insert(0, "pine_v6_rules")
    if any(token in prompt_text for token in ("review", "overfit", "backtest", "optimize", "curve")):
        selected_keys.append("anti_overfit_checklist")
    else:
        selected_keys.append("anti_overfit_checklist")
    if wants_mql5:
        selected_keys.append("mql5_rules")

    internal_docs = [_internal_doc_payload(key) for key in _dedupe(selected_keys)[:MAX_DOCS_PER_CONTEXT]]
    external_refs = _external_refs(registry_path, wants_pine=wants_pine, wants_mql5=wants_mql5)
    stage_relevance = _stage_relevance(internal_docs)
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "mode": KNOWLEDGE_CONTEXT_AUTO,
        "source_registry": str(registry_path),
        "selection_reasons": {
            "pine_context": wants_pine,
            "mql5_context": wants_mql5,
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
        "internal_docs": context.get("internal_docs", []),
        "external_refs": context.get("external_refs", []),
        "stage_relevance": context.get("stage_relevance", {}),
        "context_refs": context.get("context_refs", []),
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
        "knowledge_doc_ids": [doc["id"] for doc in context.get("internal_docs", [])],
        "external_source_ids": [source["id"] for source in context.get("external_refs", [])],
    }


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


def _external_refs(registry_path: Path, *, wants_pine: bool, wants_mql5: bool) -> list[dict[str, Any]]:
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
        refs.append(
            {
                "id": source.get("id"),
                "platform": platform,
                "type": source.get("type"),
                "trust_level": source.get("trust_level"),
                "url": source.get("url"),
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
