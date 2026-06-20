RUN_MODE_DRY_RUN = "dry-run"
RUN_MODE_AGENT = "agent"
RUN_MODE_LIVE_GENERATION = "live-generation"
RUN_MODE_BACKTEST_PREVIEW = "backtest-preview"

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
BACKTEST_RUNTIME_BOUNDARY = {
    "engine": "backtest-kit",
    "allowed_api": ["Backtest.run"],
    "blocked_api": [
        "Live.background",
        "broker_credentials",
        "paper_trading",
        "live_trading",
        "telegram_alerts",
    ],
}


def backtest_active_limit_for_tier(tier: str | None) -> int:
    return BACKTEST_ACTIVE_LIMITS_BY_TIER.get(tier or "", BACKTEST_DEFAULT_ACTIVE_LIMIT)


def backtest_job_limits_for_tier(tier: str | None) -> dict[str, int]:
    return {
        "workspace_active_limit": backtest_active_limit_for_tier(tier),
        "max_variants": BACKTEST_MAX_VARIANTS,
    }


def backtest_runtime_boundary() -> dict[str, object]:
    return {
        "engine": BACKTEST_RUNTIME_BOUNDARY["engine"],
        "allowed_api": list(BACKTEST_RUNTIME_BOUNDARY["allowed_api"]),
        "blocked_api": list(BACKTEST_RUNTIME_BOUNDARY["blocked_api"]),
    }


def backtest_active_limit_from_payload(payload_json: dict) -> int:
    limits = payload_json.get("limits")
    if not isinstance(limits, dict):
        return BACKTEST_DEFAULT_ACTIVE_LIMIT
    value = limits.get("workspace_active_limit")
    if not isinstance(value, int):
        return BACKTEST_DEFAULT_ACTIVE_LIMIT
    return max(1, value)
