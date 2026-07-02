from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends

from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.auth import require_auth_context
from strategy_codebot.server.schemas import AccountUsageResponse
from strategy_codebot.server.schemas import MeResponse


@dataclass(frozen=True)
class AccountRouterDeps:
    repository: Any
    workspace_capability: Callable[[AuthContext], Any]
    current_usage_period: Callable[[], tuple[datetime, datetime]]


def build_account_router(deps: AccountRouterDeps) -> APIRouter:
    router = APIRouter(tags=["account"])

    @router.get("/v1/me", response_model=MeResponse)
    def get_me(
        auth: AuthContext = Depends(require_auth_context),
    ) -> MeResponse:
        capability = deps.workspace_capability(auth)
        return MeResponse(
            user={"id": auth.user_id},
            workspace={"id": auth.workspace_id, "role": auth.role},
            capability=capability,
        )

    @router.get("/v1/account/usage", response_model=AccountUsageResponse)
    def get_account_usage(
        auth: AuthContext = Depends(require_auth_context),
    ) -> AccountUsageResponse:
        capability = deps.workspace_capability(auth)
        usage = deps.repository.summarize_account_usage(auth)
        period_start, period_end = deps.current_usage_period()
        return AccountUsageResponse(
            tier=capability.tier,
            tier_label=capability.tier_label,
            period_start=period_start,
            period_end=period_end,
            messages=usage.messages,
            runs=usage.runs,
            artifacts=usage.artifacts,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            estimated_cost_usd=usage.estimated_cost_usd,
        )

    return router
