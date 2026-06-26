import os

from strategy_codebot.server.backtest_ohlcv_contract import BACKTEST_OHLCV_DEFAULT_EXCHANGE
from strategy_codebot.server.backtest_ohlcv_contract import BACKTEST_OHLCV_EXCHANGES
from strategy_codebot.server.backtest_ohlcv_contract import BACKTEST_EXECUTABLE_TIMEFRAMES
from strategy_codebot.server.backtest_ohlcv_contract import BACKTEST_MAX_COST_BPS
from strategy_codebot.server.backtest_ohlcv_contract import BACKTEST_RUN_EVENTS

RUN_MODE_DRY_RUN = "dry-run"
RUN_MODE_AGENT = "agent"
RUN_MODE_LIVE_GENERATION = "live-generation"
RUN_MODE_BACKTEST_PREVIEW = "backtest-preview"
CHAT_BACKTEST_SUMMARY_JOB_TYPE = "chat-backtest-summary"
PREVIEW_COMPATIBILITY_REPAIR_JOB_TYPE = "preview-compatibility-repair"

RUN_MODES = (
    RUN_MODE_DRY_RUN,
    RUN_MODE_AGENT,
    RUN_MODE_LIVE_GENERATION,
    RUN_MODE_BACKTEST_PREVIEW,
)
QUEUED_RUN_MODES = {RUN_MODE_BACKTEST_PREVIEW}
RUN_MODES_REQUIRING_BACKTEST_CONFIG = {RUN_MODE_BACKTEST_PREVIEW}

BACKTEST_ACTIVE_LIMITS_BY_TIER = {
    "free": 1,
    "paid_low": 2,
    "paid_medium": 4,
    "paid_high": 8,
}
BACKTEST_DEFAULT_ACTIVE_LIMIT = BACKTEST_ACTIVE_LIMITS_BY_TIER["paid_low"]
BACKTEST_MAX_VARIANTS = 6
BACKTEST_JOB_MAX_ATTEMPTS = 3
CHAT_BACKTEST_SUMMARY_JOB_MAX_ATTEMPTS = 2
PREVIEW_COMPATIBILITY_REPAIR_JOB_MAX_ATTEMPTS = 2
BACKTEST_ENGINE_PINEFORGE = "pineforge"
BACKTEST_ENGINES = (BACKTEST_ENGINE_PINEFORGE,)
BACKTEST_ENGINE_DEFAULT = BACKTEST_ENGINE_PINEFORGE
PINEFORGE_RUNTIME_BOUNDARY = {
    "engine": BACKTEST_ENGINE_PINEFORGE,
    "allowed_api": ["pineforge-runner", "pineforge-engine-native"],
    "blocked_api": [
        "alerts",
        "broker_credentials",
        "paper_trading",
        "live_trading",
        "telegram_alerts",
        "external_data_fetch",
    ],
}


def backtest_default_engine() -> str:
    configured = os.getenv("BACKTEST_ENGINE_DEFAULT", BACKTEST_ENGINE_DEFAULT).strip().lower()
    return configured if configured in BACKTEST_ENGINES else BACKTEST_ENGINE_DEFAULT


def backtest_active_limit_for_tier(tier: str | None) -> int:
    return BACKTEST_ACTIVE_LIMITS_BY_TIER.get(tier or "", BACKTEST_DEFAULT_ACTIVE_LIMIT)


def backtest_job_limits_for_tier(tier: str | None) -> dict[str, int]:
    return {
        "workspace_active_limit": backtest_active_limit_for_tier(tier),
        "max_variants": BACKTEST_MAX_VARIANTS,
    }


def backtest_runtime_boundary(engine: str = BACKTEST_ENGINE_PINEFORGE) -> dict[str, object]:
    if engine != BACKTEST_ENGINE_PINEFORGE:
        raise ValueError(f"Unsupported backtest engine: {engine}")
    boundary = PINEFORGE_RUNTIME_BOUNDARY
    return {
        "engine": boundary["engine"],
        "allowed_api": list(boundary["allowed_api"]),
        "blocked_api": list(boundary["blocked_api"]),
    }


def backtest_active_limit_from_payload(payload_json: dict) -> int:
    limits = payload_json.get("limits")
    if not isinstance(limits, dict):
        return BACKTEST_DEFAULT_ACTIVE_LIMIT
    value = limits.get("workspace_active_limit")
    if not isinstance(value, int):
        return BACKTEST_DEFAULT_ACTIVE_LIMIT
    return max(1, value)
