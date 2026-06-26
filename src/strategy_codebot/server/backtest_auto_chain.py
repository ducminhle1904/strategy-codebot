from dataclasses import dataclass
from typing import Any


BACKTEST_AUTO_CHAIN_EVENTS = {
    "started": "chat.auto_chain.started",
    "step_completed": "chat.auto_chain.step.completed",
    "waiting": "chat.auto_chain.waiting_for_backtest",
    "failed": "chat.auto_chain.failed",
    "summary_completed": "chat.auto_chain.summary.completed",
}

BACKTEST_AUTO_CHAIN_ALLOWLIST = {
    "generate_pine",
    "create_backtest_plan",
    "run_backtest_preview",
    "get_backtest_summary",
}

@dataclass(frozen=True)
class BacktestAutoChainStep:
    tool_id: str
    arguments: dict[str, Any]
    reason: str


class BacktestAutoChainPlanner:
    def __init__(self, *, enabled: bool = True, max_steps: int = 4) -> None:
        self.enabled = enabled
        self.max_steps = max(1, max_steps)

    def should_start(self, completed_tool_ids: list[str], *, start_allowed: bool) -> bool:
        if not self.enabled:
            return False
        if "run_backtest_preview" in completed_tool_ids:
            return False
        return start_allowed

    def next_step(
        self,
        *,
        message_content: str,
        completed_tool_ids: list[str],
        completed_tool_results: list[dict[str, Any]],
        auto_steps_completed: int,
        start_allowed: bool = False,
    ) -> BacktestAutoChainStep | None:
        if auto_steps_completed >= self.max_steps or not self.should_start(completed_tool_ids, start_allowed=start_allowed):
            return None
        if completed_tool_ids and completed_tool_ids[-1] not in BACKTEST_AUTO_CHAIN_ALLOWLIST:
            return None
        if "generate_pine" in completed_tool_ids and "create_backtest_plan" not in completed_tool_ids:
            pine = _latest_tool_result(completed_tool_results, "generate_pine")
            strategy_spec = _dict_value(pine, "strategy_spec")
            pine_code = _string_value(pine, "pine_code")
            if strategy_spec and pine_code:
                return BacktestAutoChainStep(
                    "create_backtest_plan",
                    {
                        "prompt": message_content,
                        "strategy_spec": strategy_spec,
                        "pine_code": pine_code,
                    },
                    "generated_pine_ready",
                )
            return None
        return None


def _latest_tool_result(tool_results: list[dict[str, Any]], tool_id: str) -> dict[str, Any]:
    return next((result for result in reversed(tool_results) if result.get("tool_id") == tool_id), {})


def _dict_value(payload: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = payload.get(key)
    return value if isinstance(value, dict) else None


def _string_value(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return None
