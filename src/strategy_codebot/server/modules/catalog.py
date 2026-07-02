from collections.abc import Iterable
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ServerModule:
    name: str
    tag: str
    description: str
    path_prefixes: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()

    def owns_path(self, path: str) -> bool:
        return any(path == prefix or path.startswith(f"{prefix}/") for prefix in self.path_prefixes)


SERVER_MODULES: tuple[ServerModule, ...] = (
    ServerModule(
        name="platform",
        tag="platform",
        description="Health, readiness, provider status, and cross-cutting API metadata.",
        path_prefixes=("/health", "/ready", "/v1/provider"),
    ),
    ServerModule(
        name="account",
        tag="account",
        description="Authenticated user and workspace/account usage APIs.",
        path_prefixes=("/v1/me", "/v1/account"),
    ),
    ServerModule(
        name="actions",
        tag="actions",
        description="Registry-backed action catalog exposed to clients and LLM tools.",
        path_prefixes=("/v1/action-registry",),
    ),
    ServerModule(
        name="workflow",
        tag="workflow",
        description="Workflow task inbox, continuations, and structured human gates.",
        path_prefixes=(
            "/v1/conversations/{conversation_id}/workflow-tasks",
            "/v1/workflow-tasks",
        ),
    ),
    ServerModule(
        name="bots",
        tag="bots",
        description="Paper-bot proposals and read-only bot runtime status.",
        path_prefixes=("/v1/bots",),
        tool_names=("draft_bot", "get_bot_status", "list_bots", "list_bot_events"),
    ),
    ServerModule(
        name="nautilus",
        tag="nautilus",
        description="Nautilus paper runtime lifecycle, events, and worker coordination.",
        path_prefixes=("/v1/nautilus",),
    ),
    ServerModule(
        name="knowledge",
        tag="knowledge",
        description="Knowledge candidates, review, learning extraction, and read-only context.",
        path_prefixes=("/v1/knowledge", "/v1/runs/{run_id}/knowledge-learning"),
        tool_names=("knowledge_check", "knowledge_proposal"),
    ),
    ServerModule(
        name="artifacts",
        tag="artifacts",
        description="Workspace and conversation artifacts, previews, and content access.",
        path_prefixes=(
            "/v1/artifacts",
            "/v1/conversations/{conversation_id}/artifacts",
        ),
    ),
    ServerModule(
        name="backtest",
        tag="backtest",
        description="Backtest preview approval, report access, trades, equity, and robustness tools.",
        path_prefixes=("/v1/conversations/{conversation_id}/backtest-approvals",),
        tool_names=(
            "create_backtest_plan",
            "run_backtest_preview",
            "run_backtest_variant_lab",
            "get_backtest_summary",
            "query_backtest_trades",
            "build_robustness_report",
            "get_equity_curve_sample",
        ),
    ),
    ServerModule(
        name="runs",
        tag="runs",
        description="Assistant run creation, cancellation, retry, progress, events, and observability.",
        path_prefixes=("/v1/runs",),
    ),
    ServerModule(
        name="conversations",
        tag="conversations",
        description="Conversation CRUD, messages, sidebar, and state snapshots.",
        path_prefixes=("/v1/conversations",),
    ),
    ServerModule(
        name="feedback",
        tag="feedback",
        description="User feedback options and submissions.",
        path_prefixes=("/v1/feedback",),
    ),
    ServerModule(
        name="generation",
        tag="generation",
        description="Strategy generation, validation, and parallel-review tool handlers.",
        tool_names=("generate_pine", "create_mql5_design", "static_validate", "parallel_review"),
    ),
)


def module_openapi_tags() -> list[dict[str, str]]:
    return [{"name": module.tag, "description": module.description} for module in SERVER_MODULES]


def module_for_path(path: str) -> ServerModule | None:
    matches = [module for module in SERVER_MODULES if module.owns_path(path)]
    if not matches:
        return None
    return max(matches, key=lambda module: max(len(prefix) for prefix in module.path_prefixes if path == prefix or path.startswith(f"{prefix}/")))


def route_tag_for_path(path: str) -> str | None:
    module = module_for_path(path)
    return module.tag if module else None


def iter_module_routes(api: Any) -> Iterator[Any]:
    for route in getattr(api, "routes", ()):
        yield route
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            yield from getattr(original_router, "routes", ())


def apply_module_route_tags(api: Any) -> None:
    for route in iter_module_routes(api):
        path = getattr(route, "path", "")
        tag = route_tag_for_path(path)
        if tag is None or not hasattr(route, "tags"):
            continue
        tags = list(getattr(route, "tags", None) or [])
        if tag not in tags:
            route.tags = [tag, *tags]


def module_for_tool_name(tool_name: str) -> ServerModule | None:
    for module in SERVER_MODULES:
        if tool_name in module.tool_names:
            return module
    return None


def tool_names_for_module(module_name: str) -> tuple[str, ...]:
    for module in SERVER_MODULES:
        if module.name == module_name:
            return module.tool_names
    raise KeyError(module_name)


def tool_module_coverage_errors(tool_names: Iterable[str]) -> list[str]:
    missing = sorted(tool_name for tool_name in tool_names if module_for_tool_name(tool_name) is None)
    if not missing:
        return []
    return [f"Tool handlers missing module ownership: {', '.join(missing)}"]
