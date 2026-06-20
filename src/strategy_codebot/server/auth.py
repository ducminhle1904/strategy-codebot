from dataclasses import dataclass
import os
from typing import Annotated

from fastapi import Header, HTTPException, status

from strategy_codebot.live import DEFAULT_USER_TIER, USER_TIERS


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    workspace_id: str
    user_tier: str = DEFAULT_USER_TIER
    role: str = "owner"


def require_auth_context(
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
    x_workspace_id: Annotated[str | None, Header(alias="X-Workspace-Id")] = None,
    x_user_tier: Annotated[str | None, Header(alias="X-User-Tier")] = None,
    x_workspace_role: Annotated[str | None, Header(alias="X-Workspace-Role")] = None,
    x_internal_secret: Annotated[str | None, Header(alias="X-Strategy-Codebot-Internal-Secret")] = None,
) -> AuthContext:
    required_secret = os.getenv("STRATEGY_CODEBOT_INTERNAL_AUTH_SECRET")
    if required_secret and x_internal_secret != required_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing trusted backend auth")
    user_id = x_user_id.strip() if x_user_id else ""
    workspace_id = x_workspace_id.strip() if x_workspace_id else ""
    if not user_id or not workspace_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authentication headers")
    tier = x_user_tier.strip() if x_user_tier else DEFAULT_USER_TIER
    if tier not in USER_TIERS:
        tier = DEFAULT_USER_TIER
    role = x_workspace_role.strip() if x_workspace_role else "owner"
    return AuthContext(user_id=user_id, workspace_id=workspace_id, user_tier=tier, role=role)
