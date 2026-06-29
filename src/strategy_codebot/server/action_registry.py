from dataclasses import dataclass
from typing import Any

from strategy_codebot.server.artifact_kinds import BACKTEST_REPORT_ARTIFACT_KIND
from strategy_codebot.server.artifact_kinds import BACKTEST_RUN_METADATA_ARTIFACT_KIND
from strategy_codebot.server.artifact_kinds import BACKTEST_VARIANT_COMPARISON_ARTIFACT_KIND
from strategy_codebot.server.artifact_kinds import PROPOSED_ORDER_INTENT_ARTIFACT_KIND
from strategy_codebot.server.artifact_kinds import RISK_GATE_REPORT_ARTIFACT_KIND
from strategy_codebot.server.artifact_kinds import ROBUSTNESS_REPORT_ARTIFACT_KIND


@dataclass(frozen=True)
class ActionRegistryEntry:
    action_id: str
    tool_id: str
    label: str
    prompt: str
    category: str
    risk_level: str
    next_state: str
    artifact_kind: str | None = None
    backend_tool: bool = True
    presentation: dict[str, str] | None = None

    def payload(
        self,
        *,
        available: bool,
        disabled_reason: str | None = None,
        required_inputs: list[str] | None = None,
        risk_level: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.action_id,
            "tool_id": self.tool_id,
            "label": self.label,
            "prompt": self.prompt,
            "category": self.category,
            "risk_level": risk_level or self.risk_level,
            "next_state": self.next_state,
            "available": available,
            "presentation": self.presentation or _default_action_presentation(
                self,
                risk_level=risk_level or self.risk_level,
            ),
        }
        if self.artifact_kind:
            payload["artifact_kind"] = self.artifact_kind
        if disabled_reason:
            payload["disabled_reason"] = disabled_reason
        if required_inputs:
            payload["required_inputs"] = required_inputs
        return payload


ACTION_REGISTRY: tuple[ActionRegistryEntry, ...] = (
    ActionRegistryEntry(
        action_id="repair-validation",
        tool_id="repair",
        label="Repair validation",
        prompt="Repair static validation blockers before preview or risk review.",
        category="review",
        risk_level="read_only",
        next_state="validation_repair",
        backend_tool=False,
    ),
    ActionRegistryEntry(
        action_id="create-proposed-intent",
        tool_id="create_proposed_intent",
        label="Create Proposed Intent",
        prompt="Create a review-only OrderIntent draft from the current setup; no paper/live/broker execution.",
        category="risk",
        risk_level="review_required",
        next_state="proposed_intent",
        artifact_kind=PROPOSED_ORDER_INTENT_ARTIFACT_KIND,
        backend_tool=False,
    ),
    ActionRegistryEntry(
        action_id="run-risk-gate",
        tool_id="run_risk_gate",
        label="Run Risk Gate",
        prompt="Check sizing, stop, target, exposure, leverage, stale signal, and venue assumptions.",
        category="risk",
        risk_level="review_required",
        next_state="risk_gate",
        artifact_kind=RISK_GATE_REPORT_ARTIFACT_KIND,
        backend_tool=False,
    ),
    ActionRegistryEntry(
        action_id="prepare-bot",
        tool_id="draft_bot",
        label="Prepare bot",
        prompt="Draft a Bot setup for user review. Do not start the simulation or broker execution.",
        category="strategy",
        risk_level="review_required",
        next_state="bot_proposal",
    ),
    ActionRegistryEntry(
        action_id="review-bot-setup",
        tool_id="review_bot_setup",
        label="Review setup",
        prompt="Review Bot account, risk policy, source strategy, and subscriptions before start.",
        category="risk",
        risk_level="review_required",
        next_state="bot_setup_review",
        backend_tool=False,
    ),
    ActionRegistryEntry(
        action_id="explain-bot-status",
        tool_id="get_bot_status",
        label="Explain Bot status",
        prompt="Fetch current Bot status, heartbeat, risk state, and recent issue context.",
        category="review",
        risk_level="read_only",
        next_state="bot_status",
    ),
    ActionRegistryEntry(
        action_id="stop-bot-requires-confirmation",
        tool_id="stop_bot_requires_confirmation",
        label="Stop bot",
        prompt="Open the Bot controls for explicit user confirmation before stop or kill switch.",
        category="risk",
        risk_level="review_required",
        next_state="bot_stop_review",
        backend_tool=False,
    ),
    ActionRegistryEntry(
        action_id="review-assumptions",
        tool_id="review_assumptions",
        label="Review assumptions",
        prompt="List missing or unclear assumptions in the current strategy context.",
        category="review",
        risk_level="read_only",
        next_state="assumption_review",
        backend_tool=False,
    ),
    ActionRegistryEntry(
        action_id="market-research",
        tool_id="market_research",
        label="Market Research",
        prompt="Research the current market context with sources and summarize what matters for strategy review.",
        category="market",
        risk_level="read_only",
        next_state="source_evidence",
        artifact_kind="market_research",
        backend_tool=False,
    ),
    ActionRegistryEntry(
        action_id="query-backtest-trades",
        tool_id="query_backtest_trades",
        label="Show Trades",
        prompt=(
            "Fetch bounded indexed trades from the latest completed backtest report. "
            "Use this for explicit requests like show/list/fetch/give the first N trades, top winners, or top losers."
        ),
        category="review",
        risk_level="read_only",
        next_state="trade_review",
    ),
    ActionRegistryEntry(
        action_id="get-backtest-summary",
        tool_id="get_backtest_summary",
        label="Backtest Summary",
        prompt="Fetch bounded indexed summary metrics from the latest completed backtest report.",
        category="review",
        risk_level="read_only",
        next_state="backtest_summary",
    ),
    ActionRegistryEntry(
        action_id="build-robustness-report",
        tool_id="build_robustness_report",
        label="Robustness Report",
        prompt=(
            "Build a review-only robustness report for the current preview evidence. Summarize sample size, fees, "
            "slippage, drawdown, OOS concerns, and suspicious metrics."
        ),
        category="review",
        risk_level="read_only",
        next_state="robustness_review",
        artifact_kind=ROBUSTNESS_REPORT_ARTIFACT_KIND,
    ),
    ActionRegistryEntry(
        action_id="run-backtest-preview",
        tool_id="run_backtest_preview",
        label="Backtest Preview",
        prompt="Prepare a review-only local preview evidence check for the current strategy.",
        category="review",
        risk_level="read_only",
        next_state="local_preview_evidence",
        artifact_kind=BACKTEST_REPORT_ARTIFACT_KIND,
    ),
    ActionRegistryEntry(
        action_id="run-variant-lab",
        tool_id="run_backtest_variant_lab",
        label="Variant Lab",
        prompt="Prepare a review-only Variant Lab comparison using the same preview assumptions.",
        category="review",
        risk_level="read_only",
        next_state="variant_comparison",
        artifact_kind=BACKTEST_VARIANT_COMPARISON_ARTIFACT_KIND,
    ),
    ActionRegistryEntry(
        action_id="review-risk",
        tool_id="review_risk",
        label="Review Risk",
        prompt="Review risk rules and suggest a safer version if needed.",
        category="risk",
        risk_level="read_only",
        next_state="risk_review",
        backend_tool=False,
    ),
)


def action_registry_payload(
    *,
    artifact_kinds: set[str],
    context_text: str,
    web_search: str,
) -> list[dict[str, Any]]:
    return [
        entry.payload(
            available=_action_available(entry, artifact_kinds=artifact_kinds, context_text=context_text, web_search=web_search),
            disabled_reason=_disabled_reason(entry, artifact_kinds=artifact_kinds, context_text=context_text, web_search=web_search),
            required_inputs=_required_inputs(entry, context_text=context_text),
            risk_level=_risk_level(entry, context_text=context_text),
        )
        for entry in ACTION_REGISTRY
    ]


def action_registry_tool_ids() -> set[str]:
    return {entry.tool_id for entry in ACTION_REGISTRY}


def action_registry_backend_tool_ids() -> set[str]:
    return {entry.tool_id for entry in ACTION_REGISTRY if entry.backend_tool}


def registry_entry_for_tool(tool_id: str) -> ActionRegistryEntry | None:
    return next((entry for entry in ACTION_REGISTRY if entry.tool_id == tool_id), None)


def _default_action_presentation(entry: ActionRegistryEntry, *, risk_level: str) -> dict[str, str]:
    icon_key = "list"
    if entry.tool_id == "market_research":
        icon_key = "search"
    elif entry.tool_id in {"run_backtest_preview", "run_backtest_variant_lab"}:
        icon_key = "play"
    elif entry.tool_id in {"create_proposed_intent", "review_risk", "run_risk_gate", "review_bot_setup", "stop_bot_requires_confirmation"}:
        icon_key = "gauge"
    elif entry.tool_id in {"draft_bot", "get_bot_status"}:
        icon_key = "bot"
    elif entry.tool_id == "query_backtest_trades":
        icon_key = "list"
    elif entry.tool_id == "build_robustness_report":
        icon_key = "checklist"
    elif entry.category == "code":
        icon_key = "file_code"
    elif entry.category == "market":
        icon_key = "globe"
    elif entry.category == "risk":
        icon_key = "gauge"
    return {
        "badge_key": risk_level,
        "icon_key": icon_key,
        "visibility_key": "default",
    }


def available_registry_tool_ids(*, artifact_kinds: set[str], context_text: str, web_search: str) -> set[str]:
    return {
        entry.tool_id
        for entry in ACTION_REGISTRY
        if _action_available(entry, artifact_kinds=artifact_kinds, context_text=context_text, web_search=web_search)
    }


def _action_available(
    entry: ActionRegistryEntry,
    *,
    artifact_kinds: set[str],
    context_text: str,
    web_search: str,
) -> bool:
    has_backtest_report = _has_backtest_report(artifact_kinds, context_text)
    has_robustness_report = ROBUSTNESS_REPORT_ARTIFACT_KIND in artifact_kinds
    has_strategy_artifact = any(kind in artifact_kinds for kind in {"pine_file", "strategy_spec", "backtest_plan"})
    has_backtest_config = "backtest_config" in context_text.lower() or "backtest config" in context_text.lower()
    has_proposed_intent = PROPOSED_ORDER_INTENT_ARTIFACT_KIND in artifact_kinds or "proposed intent" in context_text.lower()
    has_risk_gate = RISK_GATE_REPORT_ARTIFACT_KIND in artifact_kinds or "risk gate" in context_text.lower()
    bot_boundary_prompt = _is_bot_boundary_request(context_text)
    if entry.tool_id == "market_research":
        return web_search != "off"
    if entry.tool_id == "repair":
        return _has_validation_problem(context_text)
    if entry.tool_id == "create_proposed_intent":
        return bot_boundary_prompt and not has_proposed_intent
    if entry.tool_id == "run_risk_gate":
        return (bot_boundary_prompt or has_proposed_intent or has_strategy_artifact) and not has_risk_gate and not _risk_gate_required_inputs(context_text)
    if entry.tool_id == "draft_bot":
        return bot_boundary_prompt and has_strategy_artifact
    if entry.tool_id in {"review_bot_setup", "get_bot_status", "stop_bot_requires_confirmation"}:
        return "bot" in context_text.lower() or "runtime" in context_text.lower()
    if entry.tool_id == "review_assumptions":
        return True
    if entry.tool_id in {"query_backtest_trades", "get_backtest_summary"}:
        return has_backtest_report
    if entry.tool_id == "build_robustness_report":
        return has_backtest_report and not has_robustness_report
    if entry.tool_id == "run_backtest_preview":
        return (has_strategy_artifact or has_backtest_config) and not has_backtest_report
    if entry.tool_id == "run_backtest_variant_lab":
        return has_strategy_artifact or has_backtest_report or has_backtest_config
    if entry.tool_id == "review_risk":
        return has_strategy_artifact or has_backtest_report
    return True


def _disabled_reason(
    entry: ActionRegistryEntry,
    *,
    artifact_kinds: set[str],
    context_text: str,
    web_search: str,
) -> str | None:
    if _action_available(entry, artifact_kinds=artifact_kinds, context_text=context_text, web_search=web_search):
        return None
    if entry.tool_id == "market_research":
        return "Web search is disabled."
    if entry.tool_id == "repair":
        return "A static validation problem is required."
    if entry.tool_id == "create_proposed_intent":
        return "A bot or order-intent review prompt is required."
    if entry.tool_id == "run_risk_gate":
        required = _risk_gate_required_inputs(context_text)
        if required:
            return "Required Risk Gate inputs are missing."
        return "A proposed intent or strategy artifact is required."
    if entry.tool_id == "draft_bot":
        return "A strategy artifact and Bot request are required."
    if entry.tool_id in {"review_bot_setup", "get_bot_status", "stop_bot_requires_confirmation"}:
        return "A Bot context is required."
    if entry.tool_id in {"query_backtest_trades", "get_backtest_summary", "build_robustness_report"}:
        return "A completed backtest report is required."
    if entry.tool_id == "run_backtest_preview":
        return "A strategy artifact or backtest config is required."
    return "Required context is not available."


def _has_backtest_report(artifact_kinds: set[str], context_text: str) -> bool:
    return bool(
        artifact_kinds.intersection({BACKTEST_REPORT_ARTIFACT_KIND, BACKTEST_RUN_METADATA_ARTIFACT_KIND, "backtest_dashboard"})
        or "backtest report" in context_text.lower()
    )


def _required_inputs(entry: ActionRegistryEntry, *, context_text: str) -> list[str] | None:
    if entry.tool_id == "run_risk_gate":
        return _risk_gate_required_inputs(context_text)
    return None


def _risk_level(entry: ActionRegistryEntry, *, context_text: str) -> str | None:
    if entry.tool_id == "run_risk_gate" and _risk_gate_required_inputs(context_text):
        return "blocked"
    return None


def _has_validation_problem(context_text: str) -> bool:
    normalized = context_text.lower()
    return any(term in normalized for term in ("validation failed", "static validation failed", "compile error", "validation blocker"))


def _is_bot_boundary_request(context_text: str) -> bool:
    normalized = context_text.lower()
    return any(term in normalized for term in ("bot", "order", "signal", "trade live", "live trade", "paper trade", "intent"))


def _risk_gate_required_inputs(context_text: str) -> list[str]:
    normalized = context_text.lower()
    required: list[str] = []
    if not any(term in normalized for term in ("stop", "invalidation", "stop-loss", "stop loss")):
        required.append("stop_or_invalidation")
    if not any(term in normalized for term in ("size", "sizing", "risk 1", "risk per trade", "position")):
        required.append("sizing")
    if not any(term in normalized for term in ("stale", "expire", "valid for", "timeout")):
        required.append("stale_after")
    return required
