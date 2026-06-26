from __future__ import annotations

import json
import os
import socket
from datetime import UTC
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from strategy_codebot.server.artifact_kinds import BACKTEST_REPORT_ARTIFACT_KIND
from strategy_codebot.server.artifact_store import LocalArtifactStore
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.database import create_sqlite_repository
from strategy_codebot.server.llm_clients import ChatCompletionsClient
from strategy_codebot.server.llm_clients import ResponsesClient
from strategy_codebot.server.llm_orchestrator import LLMOrchestrator
from strategy_codebot.server.model_routing import RegistryRoutedLLMClient
from strategy_codebot.server.model_routing import DEFAULT_MODEL_STAGE
from strategy_codebot.server.model_routing import load_model_registry
from strategy_codebot.server.model_routing import MODEL_STAGE_BALANCED_REVIEW
from strategy_codebot.server.model_routing import MODEL_STAGE_PINE_CODE_GENERATION
from strategy_codebot.server.model_routing import model_registry_path_from_env
from strategy_codebot.server.model_routing import resolve_routes


FORBIDDEN_PUBLIC_TERMS = ("pineforge", "runner", "engine", "compile", "transpile")
POSITIVE_PROMPT = (
    "Evaluate how this BTCUSDT 1h strategy would have behaved over January 2024 using local preview evidence "
    "after generating the Pine strategy. Use a conservative EMA/RSI long-only idea with explicit entry and exit risk rules."
)
NEGATIVE_PROMPT = "For a BTCUSDT EMA strategy idea, explain the risk tradeoffs without generating code or running preview."
CURRENT_PREVIEW_PROMPT = (
    "Review current preview evidence and summarize risk concerns. Do not fetch current market data or external docs."
)
MATRIX_POSITIVE_PROMPTS = (
    "Simulate this BTCUSDT 1h EMA/RSI setup over January 2024 after creating the Pine strategy.",
    "How would this BTCUSDT 1h EMA/RSI setup have behaved historically after you create the strategy?",
    "Chạy thử logic BTCUSDT 1h này sau khi tạo Pine.",
    "Chay thu logic BTCUSDT 1h nay sau khi tao Pine.",
)


def test_live_model_hybrid_intent_gate() -> None:
    _require_live_hybrid_intent_smoke()
    report_dir = _report_dir()
    provider = _provider_summary()
    evidence: dict[str, Any] = {"provider_env": provider, "scenarios": {}}

    try:
        positive = _run_chat_scenario(POSITIVE_PROMPT, report_dir=report_dir, title="positive")
        evidence["scenarios"]["positive"] = positive
        positive_intent = positive["intent_payload"]
        assert positive_intent["source"] == "llm"
        assert positive_intent["source"] != "fallback_regex"
        assert positive_intent["intent"] in {"backtest_preview", "artifact_generation", "pine_generation"}
        assert positive_intent["action"] == "start_auto_chain"
        assert positive_intent["auto_chain"] is True
        assert positive_intent["model_stage"] == "pine_code_generation"
        assert positive["auto_chain_started_payload"]["source"] == "llm"
        assert positive["completed_tool_ids"][:2] == ["generate_pine", "create_backtest_plan"]
        assert _auto_chain_reached_preview_boundary(positive)

        negative = _run_chat_scenario(NEGATIVE_PROMPT, report_dir=report_dir, title="negative")
        evidence["scenarios"]["negative"] = negative
        negative_intent = negative["intent_payload"]
        assert negative_intent["source"] == "llm"
        assert negative_intent["auto_chain"] is False
        assert negative["auto_chain_event_count"] == 0

        current_preview = _run_chat_scenario(
            CURRENT_PREVIEW_PROMPT,
            report_dir=report_dir,
            title="current-preview",
            seed_backtest_report=True,
        )
        evidence["scenarios"]["current_preview"] = current_preview
        current_intent = current_preview["intent_payload"]
        assert current_intent["source"] == "llm"
        assert current_intent["intent"] != "market_snapshot"
        assert current_intent["current_context_required"] is False
        assert current_preview["web_search_enabled"] is False

        if os.getenv("STRATEGY_CODEBOT_RUN_LIVE_HYBRID_INTENT_MATRIX") == "1":
            matrix_results: list[dict[str, Any]] = []
            for index, prompt in enumerate(MATRIX_POSITIVE_PROMPTS, start=1):
                result = _run_chat_scenario(prompt, report_dir=report_dir, title=f"matrix-{index}")
                matrix_results.append(result)
                intent = result["intent_payload"]
                assert intent["source"] == "llm"
                assert intent["action"] == "start_auto_chain"
                assert intent["auto_chain"] is True
                assert result["auto_chain_started_payload"]["source"] == "llm"
            evidence["scenarios"]["matrix"] = matrix_results

        public_text = json.dumps(evidence, sort_keys=True).lower()
        for term in FORBIDDEN_PUBLIC_TERMS:
            assert term not in public_text
    finally:
        _write_json(report_dir / "hybrid-intent-smoke.json", evidence)


def _run_chat_scenario(
    prompt: str,
    *,
    report_dir: Path,
    title: str,
    seed_backtest_report: bool = False,
) -> dict[str, Any]:
    auth = AuthContext(
        f"live-hybrid-{title}-user",
        f"live-hybrid-{title}-workspace",
        user_tier=_live_smoke_user_tier(),
    )
    repository = create_sqlite_repository()
    artifact_store = LocalArtifactStore(report_dir / f"{title}-artifacts")
    conversation = repository.create_conversation(auth, title=f"Live hybrid intent {title}")
    assert conversation is not None
    if seed_backtest_report:
        _seed_backtest_report(repository, artifact_store, auth, conversation.id)
    orchestrator = LLMOrchestrator(
        repository=repository,
        artifact_store=artifact_store,
        client=_live_model_client(),
    )
    frames = [
        frame
        for raw in orchestrator.stream_chat(
            auth=auth,
            conversation_id=conversation.id,
            message_content=prompt,
            language="en",
            web_search="auto",
        )
        for frame in _parse_sse(raw)
    ]
    _write_json(report_dir / f"{title}-frames.json", frames)
    intent = _first_payload(frames, "chat.response_intent")
    auto_chain_started = _first_payload(frames, "chat.auto_chain.started", required=False)
    auto_chain_failed = _first_payload(frames, "chat.auto_chain.failed", required=False)
    provider_started = _first_payload(frames, "provider.started", required=False) or {}
    provider_route_payloads = _event_payloads(frames, "provider.route")
    relevant_events = _relevant_public_events(frames)
    pine_code = _generated_pine(frames)
    evidence: dict[str, Any] = {
        "prompt": prompt,
        "conversation_id": conversation.id,
        "intent_payload": intent,
        "auto_chain_started_payload": auto_chain_started,
        "auto_chain_failed_payload": auto_chain_failed,
        "auto_chain_event_count": sum(1 for frame in frames if str(frame.get("event", "")).startswith("chat.auto_chain.")),
        "completed_tool_ids": _completed_tool_ids(frames),
        "event_types": [frame.get("event") for frame in frames],
        "failure_class": _classify_failure(frames, intent_payload=intent, auto_chain_started=auto_chain_started),
        "provider_started": provider_started,
        "provider_route_payloads": provider_route_payloads,
        "relevant_public_events": relevant_events,
        "web_search_enabled": bool(provider_started.get("web_search_enabled")),
    }
    if pine_code:
        evidence["generated_pine_hash"] = sha256(pine_code.encode("utf-8")).hexdigest()
        evidence["generated_pine_excerpt"] = pine_code[:800]
    return evidence


def _seed_backtest_report(repository, artifact_store: LocalArtifactStore, auth: AuthContext, conversation_id: str) -> None:
    run = repository.create_run(auth, conversation_id, status="completed", mode="backtest-preview")
    assert run is not None
    relative_path = "backtest-report.json"
    path = artifact_store.run_dir(run.id) / relative_path
    path.write_text(
        json.dumps(
            {
                "summary": {
                    "symbol": "BTC/USDT",
                    "timeframe": "1h",
                    "trade_count": 12,
                    "max_drawdown_pct": 8.2,
                }
            }
        ),
        encoding="utf-8",
    )
    artifact = repository.create_artifact(
        auth,
        run.id,
        kind=BACKTEST_REPORT_ARTIFACT_KIND,
        mime_type="application/json",
        display_name="backtest-report.json",
        storage_key=artifact_store.storage_key(run.id, relative_path),
        metadata_json={"preview_summary": {"symbol": "BTC/USDT", "timeframe": "1h"}},
    )
    assert artifact is not None


def _live_model_client():
    routing = (os.getenv("STRATEGY_CODEBOT_LLM_ROUTING") or "").strip().lower()
    if routing == "registry":
        return RegistryRoutedLLMClient(registry_path=model_registry_path_from_env())
    provider = (os.getenv("STRATEGY_CODEBOT_LLM_PROVIDER") or "").strip().lower()
    model = _configured_live_model()
    if not model:
        raise AssertionError("Configure STRATEGY_CODEBOT_LLM_MODEL or agents.orchestrator.primary in the model registry.")
    if provider == "openrouter":
        return ChatCompletionsClient(
            model=model,
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url=os.getenv("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1"),
        )
    if provider in {"vercel-ai-gateway", "vercel_ai_gateway"}:
        return ChatCompletionsClient(
            model=model,
            api_key=os.getenv("VERCEL_AI_GATEWAY_API_KEY"),
            base_url=os.getenv("VERCEL_AI_GATEWAY_API_BASE", "https://ai-gateway.vercel.sh/v1"),
        )
    if provider == "openai":
        return ResponsesClient(model=model.removeprefix("openai/"), api_key=os.getenv("OPENAI_API_KEY"))
    raise AssertionError("Set STRATEGY_CODEBOT_LLM_ROUTING=registry or a supported STRATEGY_CODEBOT_LLM_PROVIDER.")


def _require_live_hybrid_intent_smoke() -> None:
    if os.getenv("STRATEGY_CODEBOT_RUN_LIVE_HYBRID_INTENT_SMOKE") != "1":
        pytest.skip("Set STRATEGY_CODEBOT_RUN_LIVE_HYBRID_INTENT_SMOKE=1 to run live hybrid intent smoke.")
    if (os.getenv("STRATEGY_CODEBOT_LLM_MODE") or "").strip().lower() == "fake":
        raise AssertionError("Live hybrid intent smoke must not run with STRATEGY_CODEBOT_LLM_MODE=fake")
    routing = (os.getenv("STRATEGY_CODEBOT_LLM_ROUTING") or "").strip().lower()
    provider = (os.getenv("STRATEGY_CODEBOT_LLM_PROVIDER") or "").strip().lower()
    if routing == "registry":
        if not (
            os.getenv("LITELLM_PROXY_API_KEY")
            or os.getenv("OPENROUTER_API_KEY")
            or os.getenv("VERCEL_AI_GATEWAY_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        ):
            raise AssertionError("Registry live hybrid intent smoke requires at least one configured provider credential.")
        _skip_if_registry_routes_are_not_locally_available()
    else:
        required_env_by_provider = {
            "openrouter": "OPENROUTER_API_KEY",
            "vercel-ai-gateway": "VERCEL_AI_GATEWAY_API_KEY",
            "vercel_ai_gateway": "VERCEL_AI_GATEWAY_API_KEY",
            "openai": "OPENAI_API_KEY",
        }
        required = required_env_by_provider.get(provider)
        if required is None:
            raise AssertionError("Set STRATEGY_CODEBOT_LLM_ROUTING=registry or a supported STRATEGY_CODEBOT_LLM_PROVIDER.")
        if not os.getenv(required):
            raise AssertionError(f"{required} is required for live hybrid intent provider {provider}.")
        if not _configured_live_model():
            raise AssertionError("Configure STRATEGY_CODEBOT_LLM_MODEL or agents.orchestrator.primary in the model registry.")
    if os.getenv("STRATEGY_CODEBOT_CLASSIFIER_TIMEOUT_SECONDS") is None:
        os.environ["STRATEGY_CODEBOT_CLASSIFIER_TIMEOUT_SECONDS"] = os.getenv(
            "STRATEGY_CODEBOT_LIVE_CLASSIFIER_TIMEOUT_SECONDS",
            "90",
        )


def _first_payload(frames: list[dict[str, Any]], event_type: str, *, required: bool = True) -> dict[str, Any] | None:
    for frame in frames:
        if frame.get("event") != event_type:
            continue
        payload = frame.get("data", {}).get("payload")
        if isinstance(payload, dict):
            return payload
    if required:
        raise AssertionError(f"Missing event {event_type}; events={[frame.get('event') for frame in frames]}")
    return None


def _configured_live_model() -> str | None:
    configured = (os.getenv("STRATEGY_CODEBOT_LLM_MODEL") or "").strip()
    if configured:
        return configured
    registry = load_model_registry(model_registry_path_from_env())
    agents = registry.get("agents")
    if not isinstance(agents, dict):
        return None
    orchestrator = agents.get("orchestrator")
    if not isinstance(orchestrator, dict):
        return None
    primary = orchestrator.get("primary")
    return str(primary).strip() if primary else None


def _live_smoke_user_tier() -> str:
    configured = (os.getenv("STRATEGY_CODEBOT_LIVE_HYBRID_INTENT_USER_TIER") or "").strip()
    if configured:
        return configured
    return AuthContext("live-hybrid-tier-probe", "live-hybrid-tier-probe").user_tier


def _skip_if_registry_routes_are_not_locally_available() -> None:
    registry = load_model_registry(model_registry_path_from_env())
    stages = (DEFAULT_MODEL_STAGE, MODEL_STAGE_PINE_CODE_GENERATION, MODEL_STAGE_BALANCED_REVIEW)
    user_tier = _live_smoke_user_tier()
    routes_by_stage = {stage: resolve_routes(registry, tier=user_tier, stage=stage) for stage in stages}
    missing_stages = [stage for stage, routes in routes_by_stage.items() if not routes]
    if missing_stages:
        raise AssertionError(f"Registry live hybrid intent smoke has no routes for stages: {missing_stages}")
    proxy_stages = [
        stage
        for stage, routes in routes_by_stage.items()
        if any(route.startswith("litellm_proxy/") for route in routes)
    ]
    if not proxy_stages:
        return
    if os.getenv("STRATEGY_CODEBOT_RUN_LIVE_HYBRID_INTENT_REGISTRY_FORCE") != "1":
        pytest.skip(
            "Registry live hybrid intent routes are LiteLLM-proxy backed for stages "
            f"{proxy_stages} at tier {user_tier}; set STRATEGY_CODEBOT_RUN_LIVE_HYBRID_INTENT_REGISTRY_FORCE=1 "
            "with a reachable proxy to run this smoke against those routes."
        )
    if not _litellm_proxy_reachable():
        pytest.skip("Registry live hybrid intent routes require a reachable LiteLLM proxy for local smoke execution.")


def _litellm_proxy_reachable() -> bool:
    configured = os.getenv("LITELLM_PROXY_API_BASE", "http://litellm-proxy:4000/v1")
    parsed = urlparse(configured)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _event_payloads(frames: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for frame in frames:
        if frame.get("event") != event_type:
            continue
        payload = frame.get("data", {}).get("payload")
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _relevant_public_events(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    event_types = {
        "chat.response_intent",
        "provider.route",
        "provider.started",
        "chat.auto_chain.started",
        "chat.auto_chain.step.completed",
        "chat.auto_chain.waiting_for_backtest",
        "chat.auto_chain.failed",
        "tool.completed",
        "run.completed",
        "run.failed",
    }
    events: list[dict[str, Any]] = []
    for frame in frames:
        event = frame.get("event")
        if event not in event_types:
            continue
        payload = frame.get("data", {}).get("payload")
        events.append({"event": event, "payload": payload if isinstance(payload, dict) else {}})
    return events


def _classify_failure(
    frames: list[dict[str, Any]],
    *,
    intent_payload: dict[str, Any],
    auto_chain_started: dict[str, Any] | None,
) -> str | None:
    event_types = [str(frame.get("event")) for frame in frames]
    if intent_payload.get("source") == "timeout_fallback":
        return "classifier_timeout"
    if intent_payload.get("source") != "llm":
        return "intent_gate_failed"
    route_payloads = _event_payloads(frames, "provider.route")
    if any(payload.get("error") for payload in route_payloads):
        return "provider_route_failed"
    completed = _completed_tool_ids(frames)
    if auto_chain_started is not None and completed[:2] != ["generate_pine", "create_backtest_plan"]:
        return "model_tool_generation_failed"
    if auto_chain_started is not None and not any(
        event in event_types
        for event in ("backtest.preview.approval_required", "chat.auto_chain.waiting_for_backtest", "chat.auto_chain.failed")
    ):
        return "preview_boundary_failed"
    return None


def _completed_tool_ids(frames: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for frame in frames:
        if frame.get("event") != "tool.completed":
            continue
        tool_id = frame.get("data", {}).get("payload", {}).get("tool_id")
        if isinstance(tool_id, str):
            ids.append(tool_id)
    return ids


def _generated_pine(frames: list[dict[str, Any]]) -> str | None:
    for frame in frames:
        if frame.get("event") != "tool.completed":
            continue
        payload = frame.get("data", {}).get("payload", {})
        if payload.get("tool_id") != "generate_pine":
            continue
        output = payload.get("output")
        if isinstance(output, dict) and isinstance(output.get("pine_code"), str):
            return output["pine_code"]
    return None


def _auto_chain_reached_preview_boundary(evidence: dict[str, Any]) -> bool:
    completed_tools = evidence["completed_tool_ids"]
    event_types = evidence["event_types"]
    if completed_tools[:3] == ["generate_pine", "create_backtest_plan", "run_backtest_preview"]:
        return True
    if completed_tools[:2] != ["generate_pine", "create_backtest_plan"]:
        return False
    if "backtest.preview.approval_required" in event_types and "run_backtest_preview" not in completed_tools:
        return True
    failed_payload = evidence.get("auto_chain_failed_payload")
    return isinstance(failed_payload, dict) and failed_payload.get("tool_id") == "create_backtest_plan"


def _parse_sse(body: str) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for raw in body.strip().split("\n\n"):
        if not raw.strip():
            continue
        event = "message"
        data_lines: list[str] = []
        for line in raw.splitlines():
            if line.startswith("event:"):
                event = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())
        if not data_lines:
            continue
        frames.append({"event": event, "data": json.loads("\n".join(data_lines))})
    return frames


def _provider_summary() -> dict[str, str | None]:
    keys = (
        "STRATEGY_CODEBOT_LLM_MODE",
        "STRATEGY_CODEBOT_LLM_ROUTING",
        "STRATEGY_CODEBOT_LLM_PROVIDER",
        "STRATEGY_CODEBOT_LLM_MODEL",
        "STRATEGY_CODEBOT_MODEL_REGISTRY",
        "STRATEGY_CODEBOT_CLASSIFIER_TIMEOUT_SECONDS",
        "STRATEGY_CODEBOT_LIVE_CLASSIFIER_TIMEOUT_SECONDS",
        "OPENROUTER_API_BASE",
        "VERCEL_AI_GATEWAY_API_BASE",
    )
    return {
        **{key: os.getenv(key) for key in keys},
        "RESOLVED_LIVE_MODEL": _configured_live_model(),
        "LIVE_SMOKE_USER_TIER": _live_smoke_user_tier(),
    }


def _report_dir() -> Path:
    configured = os.getenv("STRATEGY_CODEBOT_LIVE_HYBRID_INTENT_REPORT_DIR")
    path = Path(configured) if configured else Path("reports/live-hybrid-intent") / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
