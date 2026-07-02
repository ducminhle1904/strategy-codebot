from collections.abc import Callable
from collections.abc import Mapping
from typing import Any

from strategy_codebot.server.modules.catalog import tool_names_for_module

ToolHandler = Callable[[dict[str, Any], Any], dict[str, Any]]


def tool_handlers_for_module(
    module_name: str,
    handlers_by_tool_name: Mapping[str, ToolHandler],
) -> dict[str, ToolHandler]:
    return {tool_name: handlers_by_tool_name[tool_name] for tool_name in tool_names_for_module(module_name)}
