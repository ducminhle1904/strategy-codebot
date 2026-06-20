from __future__ import annotations

from strategy_codebot.openrouter_free import FREE_ROUTER_MODEL, resolve_free_catalog, select_free_models_for_task


def test_free_catalog_seed_excludes_stale_deepseek_models(tmp_path):
    catalog = resolve_free_catalog(cache_path=tmp_path / "free.json", fetch=False)
    model_ids = [model["id"] for model in catalog.models]

    assert "deepseek/deepseek-v4-flash:free" not in model_ids
    assert "deepseek/deepseek-r1:free" not in model_ids
    assert "qwen/qwen3-coder:free" in model_ids


def test_select_free_models_prefers_coding_routes_and_last_resort_router(tmp_path):
    catalog = resolve_free_catalog(cache_path=tmp_path / "free.json", fetch=False)

    selected = select_free_models_for_task("single", catalog=catalog, limit=1)

    assert selected[0] == "openrouter/qwen/qwen3-coder:free"
    assert selected[-1] == FREE_ROUTER_MODEL


def test_select_free_models_demotes_unstable_routes(tmp_path):
    catalog = resolve_free_catalog(cache_path=tmp_path / "free.json", fetch=False)

    selected = select_free_models_for_task(
        "single",
        catalog=catalog,
        health_snapshot=[{"model": "openrouter/qwen/qwen3-coder:free", "status": "unstable"}],
        include_free_router=False,
        limit=2,
    )

    assert selected[0] != "openrouter/qwen/qwen3-coder:free"
