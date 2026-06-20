from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from strategy_codebot.paths import ensure_dir, repo_root
from strategy_codebot.schemas import write_json

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
FREE_CATALOG_TTL_SECONDS = 6 * 60 * 60
FREE_CATALOG_PATH = ".strategy-codebot/openrouter-free-models.json"
FREE_ROUTER_MODEL = "openrouter/openrouter/free"
FREE_CAPACITY_UNAVAILABLE = "free_capacity_unavailable"

FREE_REASONING_SEEDS = [
    "openrouter/meta-llama/llama-3.3-70b-instruct:free",
    "openrouter/nex-agi/nex-n2-pro:free",
    "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
]
FREE_CODING_SEEDS = [
    "openrouter/qwen/qwen3-coder:free",
    "openrouter/qwen/qwen3-next-80b-a3b-instruct:free",
    "openrouter/poolside/laguna-m.1:free",
]
STALE_FREE_MODELS = {
    "deepseek/deepseek-v4-flash:free",
    "deepseek/deepseek-r1:free",
}


@dataclass(frozen=True)
class FreeCatalogResult:
    models: list[dict[str, Any]]
    source: str
    path: Path
    refreshed: bool
    age_seconds: int | None
    error: str | None = None

    def metadata(self) -> dict[str, Any]:
        return {
            "free_catalog_ref": str(self.path),
            "free_catalog_source": self.source,
            "free_catalog_refreshed": self.refreshed,
            "catalog_age_seconds": self.age_seconds,
            "free_catalog_error": self.error,
            "free_catalog_model_count": len(self.models),
        }


def resolve_free_catalog(*, cache_path: Path | None = None, ttl_seconds: int = FREE_CATALOG_TTL_SECONDS, fetch: bool = True) -> FreeCatalogResult:
    path = cache_path or repo_root() / FREE_CATALOG_PATH
    cached = _load_cache(path)
    if cached and _cache_age_seconds(cached) <= ttl_seconds:
        return FreeCatalogResult(models=cached.get("models", []), source="cache", path=path, refreshed=False, age_seconds=_cache_age_seconds(cached))
    if fetch:
        try:
            models = _fetch_openrouter_free_models()
            payload = {"created_at_epoch": int(time.time()), "models": models}
            ensure_dir(path.parent)
            write_json(path, payload)
            return FreeCatalogResult(models=models, source="openrouter", path=path, refreshed=True, age_seconds=0)
        except Exception as exc:
            if cached:
                return FreeCatalogResult(models=cached.get("models", []), source="stale_cache", path=path, refreshed=False, age_seconds=_cache_age_seconds(cached), error=str(exc))
            seeds = _seed_catalog_models()
            return FreeCatalogResult(models=seeds, source="seed", path=path, refreshed=False, age_seconds=None, error=str(exc))
    return FreeCatalogResult(models=_seed_catalog_models(), source="seed", path=path, refreshed=False, age_seconds=None)


def select_free_models_for_task(
    task: str,
    *,
    catalog: FreeCatalogResult,
    health_snapshot: list[dict[str, Any]] | None = None,
    include_free_router: bool = True,
    limit: int = 4,
) -> list[str]:
    preferred = FREE_CODING_SEEDS if task in {"coding", "pine", "repair", "single"} else FREE_REASONING_SEEDS
    catalog_ids = {_to_litellm_openrouter_model(model.get("id", "")): model for model in catalog.models}
    candidates: list[str] = []
    for model in preferred:
        if model in catalog_ids:
            candidates.append(model)
    scored = sorted(
        (_score_catalog_model(model, task), _to_litellm_openrouter_model(model.get("id", "")))
        for model in catalog.models
        if _is_usable_free_model(model)
    )
    for _, model_id in reversed(scored):
        if model_id not in candidates:
            candidates.append(model_id)
    candidates = _apply_health_penalty(candidates, health_snapshot or [])
    selected = candidates[:limit]
    if include_free_router and FREE_ROUTER_MODEL not in selected:
        selected.append(FREE_ROUTER_MODEL)
    return selected


def free_catalog_report(catalog: FreeCatalogResult, selected_models: list[str] | None = None) -> dict[str, Any]:
    return {
        **catalog.metadata(),
        "selected_free_models": selected_models or [],
        "free_capacity_status": "available" if selected_models else FREE_CAPACITY_UNAVAILABLE,
    }


def _fetch_openrouter_free_models() -> list[dict[str, Any]]:
    with urllib.request.urlopen(OPENROUTER_MODELS_URL, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    models = payload.get("data", [])
    return [_catalog_entry(model) for model in models if _is_usable_free_model(model)]


def _catalog_entry(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": model.get("id"),
        "name": model.get("name"),
        "context_length": model.get("context_length"),
        "pricing": model.get("pricing", {}),
        "supported_parameters": model.get("supported_parameters", []),
    }


def _is_usable_free_model(model: dict[str, Any]) -> bool:
    model_id = str(model.get("id") or "")
    if not model_id.endswith(":free") or model_id in STALE_FREE_MODELS:
        return False
    context_length = model.get("context_length") or 0
    try:
        if int(context_length) < 32000:
            return False
    except (TypeError, ValueError):
        return False
    return True


def _score_catalog_model(model: dict[str, Any], task: str) -> int:
    model_id = str(model.get("id") or "").lower()
    context_score = min(int(model.get("context_length") or 0) // 1000, 300)
    coding_bonus = 120 if task in {"coding", "pine", "repair", "single"} and any(token in model_id for token in ("qwen", "coder", "poolside", "laguna")) else 0
    reasoning_bonus = 100 if task in {"reasoning", "review"} and any(token in model_id for token in ("nemotron", "nex", "llama")) else 0
    practical_size_bonus = 40 if any(token in model_id for token in ("70b", "80b", "120b")) else 0
    huge_model_penalty = 120 if any(token in model_id for token in ("405b", "480b", "550b")) else 0
    return context_score + coding_bonus + reasoning_bonus + practical_size_bonus - huge_model_penalty


def _apply_health_penalty(models: list[str], health_snapshot: list[dict[str, Any]]) -> list[str]:
    bad_models = {
        str(route.get("model"))
        for route in health_snapshot
        if route.get("status") in {"unstable", "cooldown"} or route.get("timeout_count") or route.get("not_found_count")
    }
    return [model for model in models if model not in bad_models] + [model for model in models if model in bad_models]


def _to_litellm_openrouter_model(model_id: str) -> str:
    return f"openrouter/{model_id}" if model_id and not model_id.startswith("openrouter/") else model_id


def _load_cache(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _cache_age_seconds(payload: dict[str, Any]) -> int:
    try:
        return max(0, int(time.time()) - int(payload.get("created_at_epoch", 0)))
    except (TypeError, ValueError):
        return 10**9


def _seed_catalog_models() -> list[dict[str, Any]]:
    return [
        {"id": model.removeprefix("openrouter/"), "context_length": 131072, "pricing": {"prompt": "0", "completion": "0"}}
        for model in [*FREE_REASONING_SEEDS, *FREE_CODING_SEEDS]
    ]
