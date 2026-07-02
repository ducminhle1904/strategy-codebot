from typing import Any

from fastapi import APIRouter, Depends

from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.auth import require_auth_context
from strategy_codebot.server.contracts.action_registry import ACTION_REGISTRY

router = APIRouter(prefix="/v1/action-registry", tags=["actions"])


@router.get("")
def get_action_registry(
    auth: AuthContext = Depends(require_auth_context),
) -> dict[str, Any]:
    _ = auth
    return {
        "version": 1,
        "actions": [entry.payload(available=True) for entry in ACTION_REGISTRY],
    }
