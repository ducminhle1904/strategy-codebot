"""Container-oriented ASGI entrypoint."""

import os

from strategy_codebot.server.app import create_app
from strategy_codebot.server.llm_clients import E2EFakeLLMClient
from strategy_codebot.server.security_controls import RateLimitConfig
from strategy_codebot.server.security_controls import RateLimitRule


def _container_llm_client():
    if os.getenv("STRATEGY_CODEBOT_LLM_MODE") == "fake":
        return E2EFakeLLMClient()
    return None


def _container_rate_limit_config():
    if os.getenv("STRATEGY_CODEBOT_E2E_RELAX_RATE_LIMITS") != "1":
        return None
    minute = RateLimitRule(10000, 60)
    day = RateLimitRule(100000, 86400)
    return RateLimitConfig(
        user_minute=minute,
        user_day=day,
        workspace_minute=minute,
        workspace_day=day,
        ip_minute=minute,
        model_user_minute=minute,
        model_workspace_minute=minute,
        tool_user_minute=minute,
        tool_workspace_minute=minute,
    )


def create_container_app():
    return create_app(
        database_url=os.getenv("STRATEGY_CODEBOT_API_DATABASE_URL"),
        redis_url=os.getenv("STRATEGY_CODEBOT_API_REDIS_URL"),
        artifact_root=os.getenv("STRATEGY_CODEBOT_API_ARTIFACT_ROOT"),
        llm_client=_container_llm_client(),
        rate_limit_config=_container_rate_limit_config(),
    )


app = create_container_app()
