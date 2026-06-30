import os
from urllib.request import urlopen
from typing import Any

from strategy_codebot.knowledge_base import knowledge_health
from strategy_codebot.server.artifact_store import LocalArtifactStore
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.llm_orchestrator import LLMOrchestrator
from strategy_codebot.server.model_routing import DEFAULT_USER_TIER
from strategy_codebot.server.model_routing import gateway_env_report
from strategy_codebot.server.model_routing import model_registry_path_from_env
from strategy_codebot.server.model_routing import normalize_user_tier
from strategy_codebot.server.repository import ConversationRepository
from strategy_codebot.server.run_modes import RUN_MODE_BACKTEST_PREVIEW
from strategy_codebot.server.run_modes import backtest_default_engine
from strategy_codebot.server.security_controls import SecurityControls
from strategy_codebot.server.worker import RunWorker


def build_readiness_payload(
    *,
    repository: ConversationRepository,
    artifact_store: LocalArtifactStore,
    controls: SecurityControls,
    llm_orchestrator: LLMOrchestrator,
    run_worker: RunWorker,
) -> dict[str, Any]:
    checks = {
        "repository": _repository_check(repository),
        "artifact_store": _artifact_store_check(artifact_store),
        "security_controls": _security_controls_check(controls),
        "llm_provider": _llm_provider_check(llm_orchestrator),
        "run_worker": run_worker.readiness(),
        "worker_queue": _worker_queue_check(repository),
        "pineforge_runner": _pineforge_runner_check(),
        "knowledge_base": _knowledge_base_check(),
    }
    status = "ok" if all(check.get("status") == "ok" for check in checks.values()) else "unavailable"
    return {
        "status": status,
        "checks": checks,
    }


def _repository_check(repository: ConversationRepository) -> dict[str, str | bool]:
    try:
        repository.list_conversations(AuthContext(user_id="usr_readiness", workspace_id="wsp_readiness"))
    except Exception:
        return {"status": "unavailable"}
    return {"status": "ok"}


def _worker_queue_check(repository: ConversationRepository) -> dict[str, Any]:
    try:
        stats = repository.run_queue_stats(job_type=RUN_MODE_BACKTEST_PREVIEW)
    except Exception:
        return {
            "status": "unavailable",
            "backtest_worker": False,
        }
    return {
        "status": "ok",
        "backtest_worker": True,
        "queue_depth": stats.queued,
        "running": stats.running,
        "active_jobs": stats.active_running,
        "stale_running_jobs": stats.stale_running,
        "worker_failures": stats.failed,
        "job_wait_time_seconds": stats.oldest_queued_seconds,
        "oldest_queued_seconds": stats.oldest_queued_seconds,
        "oldest_running_seconds": stats.oldest_running_seconds,
    }


def _pineforge_runner_check() -> dict[str, Any]:
    url = os.getenv("BACKTEST_PINEFORGE_RUNNER_URL", "").rstrip("/")
    payload: dict[str, Any] = {
        "status": "ok",
        "backtest_default_engine": backtest_default_engine(),
        "pineforge_runner_ready": False,
        "pineforge_runner_version": None,
    }
    if not url:
        payload.update(
            {
                "status": "ok",
                "configured": False,
                "reason": "BACKTEST_PINEFORGE_RUNNER_URL is not configured",
            }
        )
        return payload
    try:
        with urlopen(f"{url}/ready", timeout=2) as response:
            import json

            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        payload.update({"status": "unavailable", "reason": str(exc)})
        return payload
    ready = body.get("status") == "ok"
    payload.update(
        {
            "status": "ok" if ready else "unavailable",
            "pineforge_runner_ready": ready,
            "pineforge_runner_version": body.get("engine_version") or body.get("version"),
            "native_engine": body.get("native_engine"),
            "license_state": body.get("license_state"),
        }
    )
    return payload


def _artifact_store_check(artifact_store: LocalArtifactStore) -> dict[str, str | bool]:
    root = artifact_store.root
    available = root.exists() or root.parent.exists()
    return {
        "status": "ok" if available else "unavailable",
        "kind": "local",
    }


def _security_controls_check(controls: SecurityControls) -> dict[str, str | bool]:
    if not getattr(controls, "enabled", False):
        return {
            "status": "ok",
            "mode": "in_process",
            "fail_closed": False,
        }
    try:
        redis = getattr(controls, "redis")
        redis.ping()
    except Exception:
        return {
            "status": "unavailable",
            "mode": "redis",
            "fail_closed": True,
        }
    return {
        "status": "ok",
        "mode": "redis",
        "fail_closed": True,
    }


def _llm_provider_check(llm_orchestrator: LLMOrchestrator) -> dict[str, Any]:
    model = getattr(llm_orchestrator.client, "model", "unknown")
    routing_mode = os.getenv("STRATEGY_CODEBOT_LLM_ROUTING", "registry").strip() or "registry"
    gateway_report = gateway_env_report()
    base_payload: dict[str, Any] = {
        "model": str(model),
        "model_routing_mode": routing_mode,
        "model_registry": str(model_registry_path_from_env()),
        "default_user_tier": normalize_user_tier(os.getenv("STRATEGY_CODEBOT_SERVER_USER_TIER") or DEFAULT_USER_TIER),
        **gateway_report,
    }
    try:
        llm_orchestrator.ensure_configured()
    except Exception:
        return {
            **base_payload,
            "status": "unavailable",
        }
    return {
        **base_payload,
        "status": "ok",
    }


def _knowledge_base_check() -> dict[str, Any]:
    if os.getenv("STRATEGY_CODEBOT_DISABLE_KNOWLEDGE_READINESS") == "1":
        return {"status": "ok", "configured": False, "disabled": True}
    report = knowledge_health()
    if report["status"] == "skipped":
        return {"status": "ok", "configured": False}
    return {
        "status": "ok" if report["status"] == "pass" else "unavailable",
        "configured": report.get("configured", False),
        "embedding_provider": report.get("embedding_provider"),
        "embedding_model": report.get("embedding_model"),
        "embedding_dimension": report.get("embedding_dimension"),
        "checks": report.get("checks", []),
    }
