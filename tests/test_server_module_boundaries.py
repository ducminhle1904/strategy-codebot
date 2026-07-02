from pathlib import Path

import pytest

from strategy_codebot.server import create_app
from strategy_codebot.server import llm_tools
from strategy_codebot.server.artifact_store import LocalArtifactStore
from strategy_codebot.server.contracts import action_registry as action_contract
from strategy_codebot.server.contracts import workflow_registry as workflow_contract
from strategy_codebot.server.llm_orchestrator import LLMOrchestrator
from strategy_codebot.server.modules import SERVER_MODULES
from strategy_codebot.server.modules import iter_module_routes
from strategy_codebot.server.modules import missing_repository_methods
from strategy_codebot.server.modules import module_for_tool_name
from strategy_codebot.server.modules import route_tag_for_path
from strategy_codebot.server.modules import tool_names_for_module
from strategy_codebot.server.modules import tool_module_coverage_errors
from strategy_codebot.server.repository import ConversationRepository
from strategy_codebot.server.repository import InMemoryConversationRepository


APP_SOURCE_PATH = Path(__file__).resolve().parents[1] / "src/strategy_codebot/server/app.py"
EXTRACTED_ROUTER_PATHS = (
    "/health",
    "/ready",
    "/v1/provider/status",
    "/v1/me",
    "/v1/account/usage",
    "/v1/action-registry",
)


def _owned_api_paths(api=None) -> list[str]:
    api = api or create_app()
    return sorted(
        path
        for route in iter_module_routes(api)
        if (path := getattr(route, "path", None)) is not None
        if path == "/health" or path == "/ready" or path.startswith("/v1/")
    )


def test_server_modules_tag_every_public_api_route() -> None:
    api = create_app()
    owned_paths = set(_owned_api_paths(api))
    missing: list[str] = []
    missing_tags: list[str] = []

    for route in iter_module_routes(api):
        path = getattr(route, "path", None)
        if path not in owned_paths:
            continue
        tag = route_tag_for_path(path)
        if tag is None:
            missing.append(path)
            continue
        if tag not in getattr(route, "tags", []):
            missing_tags.append(path)

    assert missing == []
    assert missing_tags == []


def test_extracted_router_paths_are_still_registered_once() -> None:
    paths = _owned_api_paths(create_app())

    for path in EXTRACTED_ROUTER_PATHS:
        assert paths.count(path) == 1


def test_extracted_router_paths_are_not_declared_inline_in_app() -> None:
    app_source = APP_SOURCE_PATH.read_text()
    inline_decorators = [
        f'@api.get("{path}")'
        for path in EXTRACTED_ROUTER_PATHS
    ]

    assert [decorator for decorator in inline_decorators if decorator in app_source] == []


def test_server_modules_cover_every_tool_handler() -> None:
    assert tool_module_coverage_errors(llm_tools.TOOL_HANDLERS) == []
    assert module_for_tool_name("run_backtest_preview").name == "backtest"
    assert module_for_tool_name("draft_bot").name == "bots"
    assert module_for_tool_name("generate_pine").name == "generation"


def test_tool_handler_registry_is_composed_from_module_catalog() -> None:
    catalog_tool_names = {
        name
        for module in SERVER_MODULES
        for name in module.tool_names
    }

    assert catalog_tool_names == set(llm_tools.TOOL_HANDLERS)
    assert set(tool_names_for_module("backtest")) <= set(llm_tools.TOOL_HANDLERS)


def test_repository_adapter_satisfies_smaller_module_ports() -> None:
    repository = InMemoryConversationRepository()

    assert missing_repository_methods(repository) == {}


def test_conversation_repository_protocol_keeps_static_typing_semantics() -> None:
    repository = InMemoryConversationRepository()

    with pytest.raises(TypeError):
        isinstance(repository, ConversationRepository)


def test_contract_facades_match_legacy_registry_modules() -> None:
    module_names = {module.name for module in SERVER_MODULES}

    assert "workflow" in module_names
    assert workflow_contract.STRATEGY_BOT_WORKFLOW_ID == "strategy_bot_simulation"
    assert action_contract.action_registry_backend_tool_ids() <= set(llm_tools.TOOL_HANDLERS)


def test_orchestrator_exposes_service_ports(tmp_path) -> None:
    repository = InMemoryConversationRepository()
    artifact_store = LocalArtifactStore(tmp_path)
    orchestrator = LLMOrchestrator(repository=repository, artifact_store=artifact_store)

    assert orchestrator.services.repository is repository
    assert orchestrator.services.artifact_store is artifact_store
    assert orchestrator.services.client is orchestrator.client
