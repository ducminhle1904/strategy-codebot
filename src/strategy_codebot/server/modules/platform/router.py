from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, status
from starlette.responses import JSONResponse

from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.auth import require_auth_context
from strategy_codebot.server.readiness import build_readiness_payload
from strategy_codebot.server.schemas import ProviderStatusResponse


@dataclass(frozen=True)
class PlatformRouterDeps:
    version: str
    repository: Any
    artifact_store: Any
    controls: Any
    llm_orchestrator: Any
    run_worker: Any
    workspace_capability: Callable[[AuthContext], Any]
    user_safe_model_routing_status: Callable[[AuthContext, bool], dict[str, Any]]


def build_platform_router(deps: PlatformRouterDeps) -> APIRouter:
    router = APIRouter(tags=["platform"])

    @router.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "strategy-codebot-api",
            "version": deps.version,
        }

    @router.get("/ready")
    def ready() -> JSONResponse:
        payload = build_readiness_payload(
            repository=deps.repository,
            artifact_store=deps.artifact_store,
            controls=deps.controls,
            llm_orchestrator=deps.llm_orchestrator,
            run_worker=deps.run_worker,
        )
        status_code = status.HTTP_200_OK if payload["status"] == "ok" else status.HTTP_503_SERVICE_UNAVAILABLE
        return JSONResponse(payload, status_code=status_code)

    @router.get("/v1/provider/status", response_model=ProviderStatusResponse)
    def get_provider_status(
        auth: AuthContext = Depends(require_auth_context),
    ) -> ProviderStatusResponse:
        capability = deps.workspace_capability(auth)
        configured = True
        reason = None
        try:
            deps.llm_orchestrator.ensure_configured()
        except Exception as exc:
            configured = False
            reason = exc.__class__.__name__
        routing_status = deps.user_safe_model_routing_status(auth, configured)
        return ProviderStatusResponse(
            configured=configured,
            available=configured and "agent" in capability.allowed_message_modes,
            tier=capability.tier,
            tier_label=capability.tier_label,
            allowed_message_modes=capability.allowed_message_modes,
            allowed_run_modes=capability.allowed_run_modes,
            capability_matrix=capability.capability_matrix,
            fallback_mode="deterministic",
            model_routing_mode=routing_status["model_routing_mode"],
            model_tier=routing_status["model_tier"],
            selected_stage_defaults=routing_status["selected_stage_defaults"],
            available_gateways=routing_status["available_gateways"],
            route_ready=routing_status["route_ready"],
            fallback_enabled=routing_status["fallback_enabled"],
            user_message=routing_status["user_message"],
            status="ready" if configured else "not_configured",
            reason=reason,
        )

    return router
