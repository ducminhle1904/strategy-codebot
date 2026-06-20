from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_uses_production_runtime_practices() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.13-slim-bookworm AS builder" in dockerfile
    assert "FROM python:3.13-slim-bookworm AS runtime" in dockerfile
    assert "uv sync --frozen --no-dev --extra live" in dockerfile
    assert "USER strategy-codebot" in dockerfile
    assert "strategy_codebot.server.asgi:app" in dockerfile
    assert "strategy-codebot-migrate" in dockerfile
    assert "HEALTHCHECK" in dockerfile


def test_compose_defines_backend_stack_without_public_db_or_redis_ports() -> None:
    compose = yaml.safe_load((ROOT / "compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert {"api", "migration", "knowledge-init", "postgres", "redis", "litellm-proxy"} <= set(services)
    assert services["postgres"]["image"] == "pgvector/pgvector:pg17"
    assert "ports" not in services["postgres"]
    assert "ports" not in services["redis"]
    assert services["api"]["ports"] == ["${STRATEGY_CODEBOT_API_PORT:-8000}:8000"]
    assert "healthcheck" in services["api"]
    assert "healthcheck" in services["postgres"]
    assert "healthcheck" in services["redis"]
    assert "healthcheck" in services["litellm-proxy"]
    assert services["api"]["depends_on"]["migration"]["condition"] == "service_completed_successfully"
    assert "knowledge-init" not in services["api"]["depends_on"]
    assert services["api"]["depends_on"]["redis"]["condition"] == "service_healthy"
    assert services["api"]["depends_on"]["litellm-proxy"]["condition"] == "service_healthy"
    assert services["litellm-proxy"]["image"] == "docker.litellm.ai/berriai/litellm:main-latest"
    assert services["litellm-proxy"]["command"] == ["--config", "/app/config.yaml", "--port", "4000"]
    assert "./docker/litellm/config.yaml:/app/config.yaml:ro" in services["litellm-proxy"]["volumes"]
    assert services["migration"]["command"] == ["strategy-codebot-migrate"]
    assert services["knowledge-init"]["command"] == [
        "sh",
        "-c",
        'strategy-codebot knowledge init --embedding-profile production-openrouter --db-url "$$STRATEGY_CODEBOT_KNOWLEDGE_DATABASE_URL"',
    ]
    assert services["knowledge-init"]["depends_on"]["migration"]["condition"] == "service_completed_successfully"
    assert services["knowledge-init"]["profiles"] == ["knowledge-init"]
    assert compose["x-api-environment"]["POSTGRES_PASSWORD"] == "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
    assert compose["x-api-environment"]["REDIS_PASSWORD"] == "${REDIS_PASSWORD:?REDIS_PASSWORD is required}"
    assert compose["x-api-environment"]["LITELLM_PROXY_API_BASE"] == "${LITELLM_PROXY_API_BASE:-http://litellm-proxy:4000/v1}"
    assert compose["x-api-environment"]["LITELLM_PROXY_API_KEY"] == "${LITELLM_PROXY_API_KEY:-${LITELLM_MASTER_KEY:-}}"
    assert "OPENROUTER_API_KEY" in compose["x-api-environment"]
    assert "LITELLM_PROXY_API_KEY" in compose["x-api-environment"]
    assert "VERCEL_AI_GATEWAY_API_KEY" in compose["x-api-environment"]
    assert services["litellm-proxy"]["environment"]["VERCEL_AI_GATEWAY_API_KEY"] == "${VERCEL_AI_GATEWAY_API_KEY:-}"
    assert services["litellm-proxy"]["environment"]["VERCEL_AI_GATEWAY_API_BASE"] == "${VERCEL_AI_GATEWAY_API_BASE:-https://ai-gateway.vercel.sh/v1}"
    assert services["litellm-proxy"]["environment"]["OPENROUTER_API_BASE"] == "${OPENROUTER_API_BASE:-}"
    assert services["litellm-proxy"]["environment"]["PORTKEY_API_KEY"] == "${PORTKEY_API_KEY:-}"
    assert services["litellm-proxy"]["environment"]["PORTKEY_API_BASE"] == "${PORTKEY_API_BASE:-}"
    assert services["litellm-proxy"]["environment"]["PORTKEY_VIRTUAL_KEY"] == "${PORTKEY_VIRTUAL_KEY:-}"
    assert services["litellm-proxy"]["environment"]["PORTKEY_CONFIG_ID"] == "${PORTKEY_CONFIG_ID:-}"
    assert "PORTKEY_API_KEY" in compose["x-api-environment"]
    assert "GROQ_API_KEY" in compose["x-api-environment"]
    assert "TOGETHER_API_KEY" in compose["x-api-environment"]
    assert "FIREWORKS_API_KEY" in compose["x-api-environment"]
    assert "DEEPINFRA_API_KEY" in compose["x-api-environment"]
    assert "secrets" not in compose
    assert "secrets" not in services["migration"]
    assert "secrets" not in services["knowledge-init"]
    assert "secrets" not in services["api"]


def test_dev_compose_only_exposes_local_debug_ports() -> None:
    compose = yaml.safe_load((ROOT / "compose.dev.yml").read_text(encoding="utf-8"))

    assert compose["services"]["postgres"]["ports"] == ["5432:5432"]
    assert compose["services"]["redis"]["ports"] == ["6379:6379"]
    assert compose["services"]["litellm-proxy"]["ports"] == ["4000:4000"]
    assert "ports" not in compose["services"].get("api", {})


def test_e2e_compose_enables_deterministic_real_service_stack() -> None:
    compose = yaml.safe_load((ROOT / "compose.e2e.yml").read_text(encoding="utf-8"))

    assert compose["name"] == "strategy-codebot-e2e"
    assert compose["services"]["postgres"]["ports"] == ["${STRATEGY_CODEBOT_E2E_POSTGRES_PORT:-55432}:5432"]
    assert compose["services"]["redis"]["ports"] == ["${STRATEGY_CODEBOT_E2E_REDIS_PORT:-56379}:6379"]
    assert compose["services"]["api"]["environment"]["STRATEGY_CODEBOT_LLM_MODE"] == "fake"
    assert compose["services"]["litellm-proxy"]["image"] == "python:3.13-slim-bookworm"
    worker_env = compose["services"]["backtest-worker"]["environment"]
    assert worker_env["BACKTEST_WORKER_MARKET_DATA_MODE"] == "${BACKTEST_WORKER_MARKET_DATA_MODE:-fixture}"
    assert worker_env["BACKTEST_WORKER_DEFAULT_WORKSPACE_ACTIVE_LIMIT"] == "${BACKTEST_WORKER_DEFAULT_WORKSPACE_ACTIVE_LIMIT:-2}"


def test_e2e_runner_collects_evidence_and_runs_real_service_groups() -> None:
    script = (ROOT / "scripts" / "e2e-docker.sh").read_text(encoding="utf-8")

    assert "docker compose -p" in script
    assert "--scale \"backtest-worker=$WORKERS\"" in script
    assert "tests/e2e/docker/test_api_backtest_worker.py" in script
    assert "tests/e2e/docker/test_chat_tools.py" in script
    assert "tests/e2e/docker/test_load.py" in script
    assert "reports/e2e" in script
    assert "analysis.md" in script
    assert "npm audit --omit=dev" in script


def test_dockerignore_excludes_local_state_and_secrets() -> None:
    ignored = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())

    assert ".git" in ignored
    assert ".env" in ignored
    assert ".strategy-codebot" in ignored
    assert "harness.db" in ignored
    assert "docker/secrets" in ignored


def test_entrypoint_expands_openrouter_secret_and_knowledge_database_url() -> None:
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")

    assert "file_env OPENROUTER_API_KEY" in entrypoint
    assert "file_env LITELLM_PROXY_API_KEY" in entrypoint
    assert "file_env VERCEL_AI_GATEWAY_API_KEY" in entrypoint
    assert "file_env PORTKEY_API_KEY" in entrypoint
    assert "file_env GROQ_API_KEY" in entrypoint
    assert "file_env TOGETHER_API_KEY" in entrypoint
    assert "file_env FIREWORKS_API_KEY" in entrypoint
    assert "file_env DEEPINFRA_API_KEY" in entrypoint
    assert "STRATEGY_CODEBOT_KNOWLEDGE_DATABASE_URL" in entrypoint
    assert "postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" in entrypoint


def test_litellm_proxy_config_covers_registry_routes() -> None:
    config = yaml.safe_load((ROOT / "docker" / "litellm" / "config.yaml").read_text(encoding="utf-8"))
    registry = yaml.safe_load((ROOT / "configs" / "model-registry.example.yaml").read_text(encoding="utf-8"))
    entries_by_alias: dict[str, list[dict]] = {}
    for entry in config["model_list"]:
        entries_by_alias.setdefault(entry["model_name"], []).append(entry)
    diagnostic_aliases = {"diagnostics.vercel_gemini_flash_lite"}

    expected_aliases = {
        route.removeprefix("litellm_proxy/")
        for tier_name, tier in registry["model_tiers"].items()
        if tier_name != "free"
        for routes in tier["routes_by_stage"].values()
        for route in routes
    }
    assert set(entries_by_alias) == expected_aliases | diagnostic_aliases
    assert all(len(entries_by_alias[alias]) >= 1 for alias in expected_aliases)
    assert len(entries_by_alias["diagnostics.vercel_gemini_flash_lite"]) == 1
    diagnostic_params = entries_by_alias["diagnostics.vercel_gemini_flash_lite"][0]["litellm_params"]
    assert diagnostic_params["model"] == "vercel_ai_gateway/google/gemini-2.5-flash-lite"
    assert diagnostic_params["api_key"] == "os.environ/VERCEL_AI_GATEWAY_API_KEY"
    assert diagnostic_params["api_base"] == "os.environ/VERCEL_AI_GATEWAY_API_BASE"
    paid_models = [
        entry["litellm_params"]["model"]
        for alias, entries in entries_by_alias.items()
        if alias not in diagnostic_aliases
        for entry in entries
    ]
    assert "openrouter/qwen/qwen3.7-plus" not in paid_models
    assert all("weight" in entry["litellm_params"] for entry in config["model_list"])
    vercel_paid_entries = [
        (alias, entry)
        for alias, entries in entries_by_alias.items()
        if alias not in diagnostic_aliases
        for entry in entries
        if entry["litellm_params"]["model"].startswith("vercel_ai_gateway/")
    ]
    assert len(vercel_paid_entries) == 7
    assert {alias.split(".", 1)[0] for alias, _entry in vercel_paid_entries} == {"paid_low", "paid_medium"}
    assert {alias.rsplit(".", 1)[1] for alias, _entry in vercel_paid_entries} == {
        "balanced_review",
        "balanced_review_vercel",
        "pine_code_generation",
        "pine_code_generation_vercel",
        "repair_vercel",
        "strategy_reasoning",
        "strategy_reasoning_vercel",
    }
    assert all(entry["litellm_params"]["api_key"] == "os.environ/VERCEL_AI_GATEWAY_API_KEY" for _alias, entry in vercel_paid_entries)
    assert all(entry["litellm_params"]["api_base"] == "os.environ/VERCEL_AI_GATEWAY_API_BASE" for _alias, entry in vercel_paid_entries)
    assert all(entry["litellm_params"]["weight"] == 1 for _alias, entry in vercel_paid_entries)
    for entry in config["model_list"]:
        alias = entry["model_name"]
        if alias.startswith(("paid_low.", "paid_medium.")):
            params = entry["litellm_params"]
            assert params["timeout"] == (85 if alias.endswith(("pine_code_generation", "pine_code_generation_qwen", "pine_code_generation_vercel", "repair", "repair_qwen", "repair_vercel")) else 55)
    for entry in config["model_list"]:
        model = entry["litellm_params"]["model"]
        if "moonshotai/kimi" in model:
            assert entry["litellm_params"].get("include_reasoning") is False
    assert config["general_settings"]["master_key"] == "os.environ/LITELLM_MASTER_KEY"
    assert config["litellm_settings"]["request_timeout"] == 60
    assert config["litellm_settings"]["num_retries"] == 0
    assert config["router_settings"]["routing_strategy"] == "simple-shuffle"
    assert config["router_settings"]["allowed_fails"] == 1
    assert config["router_settings"]["cooldown_time"] == 600


def test_paid_registry_routes_use_litellm_proxy_aliases_and_free_stays_direct() -> None:
    registry = yaml.safe_load((ROOT / "configs" / "model-registry.example.yaml").read_text(encoding="utf-8"))
    tiers = registry["model_tiers"]

    assert all(not route.startswith("litellm_proxy/") for routes in tiers["free"]["routes_by_stage"].values() for route in routes)
    for tier in ("paid_low", "paid_medium", "paid_high"):
        for stage, routes in tiers[tier]["routes_by_stage"].items():
            if tier == "paid_low" and stage == "strategy_reasoning":
                assert routes == [
                    "litellm_proxy/paid_low.strategy_reasoning",
                    "litellm_proxy/paid_low.strategy_reasoning_vercel",
                    "litellm_proxy/paid_low.strategy_reasoning_gemini_lite",
                    "litellm_proxy/paid_medium.strategy_reasoning",
                ]
            elif tier == "paid_low" and stage == "strategy_coding":
                assert routes == [
                    "litellm_proxy/paid_low.strategy_coding",
                    "litellm_proxy/paid_low.strategy_coding_deepseek",
                    "litellm_proxy/paid_low.strategy_coding_qwen",
                    "litellm_proxy/paid_medium.strategy_coding",
                ]
            elif tier == "paid_low" and stage == "pine_code_generation":
                assert routes == [
                    "litellm_proxy/paid_low.pine_code_generation",
                    "litellm_proxy/paid_low.pine_code_generation_qwen",
                    "litellm_proxy/paid_low.pine_code_generation_vercel",
                    "litellm_proxy/paid_medium.pine_code_generation",
                ]
            elif tier == "paid_low" and stage == "balanced_review":
                assert routes == [
                    "litellm_proxy/paid_low.balanced_review",
                    "litellm_proxy/paid_low.balanced_review_vercel",
                    "litellm_proxy/paid_low.balanced_review_minimax",
                    "litellm_proxy/paid_medium.balanced_review",
                ]
            elif tier == "paid_low" and stage == "repair":
                assert routes == [
                    "litellm_proxy/paid_low.repair",
                    "litellm_proxy/paid_low.repair_qwen",
                    "litellm_proxy/paid_low.repair_vercel",
                    "litellm_proxy/paid_medium.repair",
                ]
            else:
                assert routes == [f"litellm_proxy/{tier}.{stage}"]


def test_env_example_contains_litellm_proxy_runtime_keys() -> None:
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "LITELLM_MASTER_KEY=sk-litellm-local-dev-change-me" in env_example
    assert "LITELLM_SALT_KEY=change-me-long-random-value" in env_example
    assert "LITELLM_ADMIN_API_BASE=http://127.0.0.1:4000" in env_example
    assert "LITELLM_PROXY_API_KEY=sk-litellm-local-dev-change-me" in env_example
    assert "LITELLM_PROXY_API_BASE=http://litellm-proxy:4000/v1" in env_example
    assert "LITELLM_BUDGET_PAID_LOW_DAILY_USD=2" in env_example
    assert "LITELLM_BUDGET_PAID_MEDIUM_DAILY_USD=10" in env_example
    assert "LITELLM_BUDGET_PAID_HIGH_DAILY_USD=50" in env_example
