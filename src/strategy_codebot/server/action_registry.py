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
    requires_artifacts: tuple[str, ...] = ()
    requires_context_signals: tuple[str, ...] = ()
    requires_workflow_state: tuple[str, ...] = ()
    required_inputs: tuple[str, ...] = ()
    disabled_reason_code: str | None = None
    direct_execution_allowed: bool | None = None

    def payload(
        self,
        *,
        available: bool,
        disabled_reason: str | None = None,
        disabled_reason_code: str | None = None,
        required_inputs: list[str] | None = None,
        risk_level: str | None = None,
        requirements: dict[str, Any] | None = None,
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
        if self.direct_execution_allowed is not None:
            payload["direct_execution_allowed"] = self.direct_execution_allowed
        if self.artifact_kind:
            payload["artifact_kind"] = self.artifact_kind
        if disabled_reason:
            payload["disabled_reason"] = disabled_reason
        if disabled_reason_code:
            payload["disabled_reason_code"] = disabled_reason_code
        if required_inputs:
            payload["required_inputs"] = required_inputs
        if requirements:
            payload["requirements"] = requirements
        return payload


@dataclass(frozen=True)
class ActionEvidencePacket:
    artifact_kinds: frozenset[str]
    context_signals: frozenset[str]
    web_search: str
    required_inputs_by_tool: dict[str, tuple[str, ...]]
    lexical_hints: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ActionAvailabilityResult:
    available: bool
    disabled_reason: str | None = None
    disabled_reason_code: str | None = None
    required_inputs: tuple[str, ...] = ()
    risk_level: str | None = None


@dataclass
class ActionRegistryEvaluation:
    payload: list[dict[str, Any]]
    available_tool_ids: set[str]


class ActionRegistryRequestCache:
    def __init__(self) -> None:
        self._cache: dict[tuple[frozenset[str], str, str], ActionRegistryEvaluation] = {}

    def get(
        self,
        *,
        artifact_kinds: set[str],
        context_text: str,
        web_search: str,
    ) -> ActionRegistryEvaluation:
        key = (frozenset(artifact_kinds), context_text, web_search)
        evaluation = self._cache.get(key)
        if evaluation is None:
            evaluation = evaluate_action_registry(
                artifact_kinds=artifact_kinds,
                context_text=context_text,
                web_search=web_search,
            )
            self._cache[key] = evaluation
        return evaluation


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


_ACTION_REQUIREMENTS: dict[str, dict[str, tuple[str, ...] | str]] = {
    "market_research": {
        "requires_context_signals": ("web_search_enabled",),
        "disabled_reason_code": "web_search_disabled",
    },
    "repair": {
        "requires_context_signals": ("validation_problem",),
        "disabled_reason_code": "validation_problem_required",
    },
    "create_proposed_intent": {
        "requires_context_signals": ("bot_boundary_request",),
        "blocked_by_context_signals": ("has_proposed_intent",),
        "disabled_reason_code": "bot_boundary_required",
    },
    "run_risk_gate": {
        "requires_any_context_signal": ("bot_boundary_request", "has_proposed_intent", "has_strategy_artifact"),
        "blocked_by_context_signals": ("has_risk_gate",),
        "disabled_reason_code": "risk_gate_context_required",
    },
    "draft_bot": {
        "requires_context_signals": ("bot_boundary_request", "has_strategy_artifact"),
        "disabled_reason_code": "strategy_artifact_and_bot_request_required",
    },
    "review_bot_setup": {
        "requires_context_signals": ("bot_context",),
        "disabled_reason_code": "bot_context_required",
    },
    "get_bot_status": {
        "requires_context_signals": ("bot_context",),
        "disabled_reason_code": "bot_context_required",
    },
    "stop_bot_requires_confirmation": {
        "requires_context_signals": ("bot_context",),
        "disabled_reason_code": "bot_context_required",
    },
    "query_backtest_trades": {
        "requires_context_signals": ("has_backtest_report",),
        "disabled_reason_code": "backtest_report_required",
    },
    "get_backtest_summary": {
        "requires_context_signals": ("has_backtest_report",),
        "disabled_reason_code": "backtest_report_required",
    },
    "build_robustness_report": {
        "requires_context_signals": ("has_backtest_report",),
        "blocked_by_context_signals": ("has_robustness_report",),
        "disabled_reason_code": "backtest_report_required",
    },
    "run_backtest_preview": {
        "requires_any_context_signal": ("has_strategy_artifact", "has_backtest_config"),
        "blocked_by_context_signals": ("has_backtest_report",),
        "disabled_reason_code": "strategy_artifact_or_backtest_config_required",
    },
    "run_backtest_variant_lab": {
        "requires_any_context_signal": ("has_strategy_artifact", "has_backtest_report", "has_backtest_config"),
        "disabled_reason_code": "strategy_artifact_or_backtest_context_required",
    },
    "review_risk": {
        "requires_any_context_signal": ("has_strategy_artifact", "has_backtest_report"),
        "disabled_reason_code": "strategy_or_backtest_required",
    },
}


def action_registry_payload(
    *,
    artifact_kinds: set[str],
    context_text: str,
    web_search: str,
    context_signals: set[str] | None = None,
    risk_gate_inputs: set[str] | None = None,
) -> list[dict[str, Any]]:
    evidence = build_action_evidence_packet(
        artifact_kinds=artifact_kinds,
        context_text=context_text,
        web_search=web_search,
        context_signals=context_signals,
        risk_gate_inputs=risk_gate_inputs,
    )
    payloads: list[dict[str, Any]] = []
    for entry in ACTION_REGISTRY:
        result = evaluate_action_availability(entry, evidence=evidence)
        payloads.append(
            entry.payload(
                available=result.available,
                disabled_reason=result.disabled_reason,
                disabled_reason_code=result.disabled_reason_code,
                required_inputs=list(result.required_inputs),
                risk_level=result.risk_level,
                requirements=_action_requirements_payload(entry),
            )
        )
    return payloads


def evaluate_action_registry(
    *,
    artifact_kinds: set[str],
    context_text: str,
    web_search: str,
    context_signals: set[str] | None = None,
    risk_gate_inputs: set[str] | None = None,
) -> ActionRegistryEvaluation:
    payload = action_registry_payload(
        artifact_kinds=artifact_kinds,
        context_text=context_text,
        web_search=web_search,
        context_signals=context_signals,
        risk_gate_inputs=risk_gate_inputs,
    )
    available_tool_ids = {
        str(item.get("tool_id"))
        for item in payload
        if item.get("available") is True and isinstance(item.get("tool_id"), str)
    }
    return ActionRegistryEvaluation(payload=payload, available_tool_ids=available_tool_ids)


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


def available_registry_tool_ids(
    *,
    artifact_kinds: set[str],
    context_text: str,
    web_search: str,
    context_signals: set[str] | None = None,
    risk_gate_inputs: set[str] | None = None,
) -> set[str]:
    return evaluate_action_registry(
        artifact_kinds=artifact_kinds,
        context_text=context_text,
        web_search=web_search,
        context_signals=context_signals,
        risk_gate_inputs=risk_gate_inputs,
    ).available_tool_ids


def build_action_evidence_packet(
    *,
    artifact_kinds: set[str],
    context_text: str,
    web_search: str,
    context_signals: set[str] | None = None,
    risk_gate_inputs: set[str] | None = None,
) -> ActionEvidencePacket:
    signals: set[str] = set(context_signals or set())
    if web_search != "off":
        signals.add("web_search_enabled")
    if _has_backtest_report(artifact_kinds):
        signals.add("has_backtest_report")
    if ROBUSTNESS_REPORT_ARTIFACT_KIND in artifact_kinds:
        signals.add("has_robustness_report")
    if any(kind in artifact_kinds for kind in {"pine_file", "strategy_spec", "backtest_plan"}):
        signals.add("has_strategy_artifact")
    if PROPOSED_ORDER_INTENT_ARTIFACT_KIND in artifact_kinds:
        signals.add("has_proposed_intent")
    if RISK_GATE_REPORT_ARTIFACT_KIND in artifact_kinds:
        signals.add("has_risk_gate")
    lexical_hints = _action_lexical_hints(context_text)
    return ActionEvidencePacket(
        artifact_kinds=frozenset(artifact_kinds),
        context_signals=frozenset(signals),
        web_search=web_search,
        required_inputs_by_tool={"run_risk_gate": tuple(_risk_gate_required_inputs(risk_gate_inputs))},
        lexical_hints=frozenset(lexical_hints),
    )


def evaluate_action_availability(entry: ActionRegistryEntry, *, evidence: ActionEvidencePacket) -> ActionAvailabilityResult:
    requirements = _ACTION_REQUIREMENTS.get(entry.tool_id, {})
    required_signals = _tuple_requirement(requirements, "requires_context_signals")
    missing_signals = [signal for signal in required_signals if signal not in evidence.context_signals]
    any_signals = _tuple_requirement(requirements, "requires_any_context_signal")
    missing_any_signal = bool(any_signals) and not any(signal in evidence.context_signals for signal in any_signals)
    blocked_signals = [
        signal for signal in _tuple_requirement(requirements, "blocked_by_context_signals") if signal in evidence.context_signals
    ]
    required_inputs = evidence.required_inputs_by_tool.get(entry.tool_id, ())
    reason_code = str(requirements.get("disabled_reason_code") or "required_context_missing")
    if required_inputs:
        return ActionAvailabilityResult(
            available=False,
            disabled_reason=_disabled_reason_for_code("missing_required_inputs"),
            disabled_reason_code="missing_required_inputs",
            required_inputs=required_inputs,
            risk_level="blocked",
        )
    if missing_signals or missing_any_signal or blocked_signals:
        if blocked_signals:
            reason_code = _blocked_signal_reason_code(blocked_signals[0])
        return ActionAvailabilityResult(
            available=False,
            disabled_reason=_disabled_reason_for_code(reason_code),
            disabled_reason_code=reason_code,
        )
    return ActionAvailabilityResult(available=True)


def _action_requirements_payload(entry: ActionRegistryEntry) -> dict[str, Any] | None:
    requirements = _ACTION_REQUIREMENTS.get(entry.tool_id)
    if not requirements:
        return None
    payload: dict[str, Any] = {}
    for key in ("requires_context_signals", "requires_any_context_signal", "blocked_by_context_signals"):
        values = _tuple_requirement(requirements, key)
        if values:
            payload[key] = list(values)
    if entry.requires_artifacts:
        payload["requires_artifacts"] = list(entry.requires_artifacts)
    if entry.requires_workflow_state:
        payload["requires_workflow_state"] = list(entry.requires_workflow_state)
    return payload or None


def _tuple_requirement(requirements: dict[str, tuple[str, ...] | str], key: str) -> tuple[str, ...]:
    value = requirements.get(key, ())
    return value if isinstance(value, tuple) else ()


def _blocked_signal_reason_code(signal: str) -> str:
    if signal == "has_proposed_intent":
        return "proposed_intent_exists"
    if signal == "has_risk_gate":
        return "risk_gate_exists"
    if signal == "has_robustness_report":
        return "robustness_report_exists"
    if signal == "has_backtest_report":
        return "backtest_report_exists"
    return "blocked_by_existing_context"


def _disabled_reason_for_code(reason_code: str) -> str:
    return {
        "web_search_disabled": "Web search is disabled.",
        "validation_problem_required": "A static validation problem is required.",
        "bot_boundary_required": "A bot or order-intent review prompt is required.",
        "proposed_intent_exists": "A proposed intent already exists.",
        "risk_gate_context_required": "A proposed intent or strategy artifact is required.",
        "risk_gate_exists": "A Risk Gate report already exists.",
        "strategy_artifact_and_bot_request_required": "A strategy artifact and Bot request are required.",
        "bot_context_required": "A Bot context is required.",
        "backtest_report_required": "A completed backtest report is required.",
        "robustness_report_exists": "A robustness report already exists.",
        "strategy_artifact_or_backtest_config_required": "A strategy artifact or backtest config is required.",
        "backtest_report_exists": "A completed backtest report already exists.",
        "strategy_artifact_or_backtest_context_required": "A strategy artifact or backtest context is required.",
        "strategy_or_backtest_required": "A strategy artifact or completed backtest report is required.",
        "missing_required_inputs": "Required Risk Gate inputs are missing.",
    }.get(reason_code, "Required context is not available.")


def _action_available(
    entry: ActionRegistryEntry,
    *,
    artifact_kinds: set[str],
    context_text: str,
    web_search: str,
) -> bool:
    evidence = build_action_evidence_packet(artifact_kinds=artifact_kinds, context_text=context_text, web_search=web_search)
    return evaluate_action_availability(entry, evidence=evidence).available


def _disabled_reason(
    entry: ActionRegistryEntry,
    *,
    artifact_kinds: set[str],
    context_text: str,
    web_search: str,
) -> str | None:
    evidence = build_action_evidence_packet(artifact_kinds=artifact_kinds, context_text=context_text, web_search=web_search)
    return evaluate_action_availability(entry, evidence=evidence).disabled_reason


def _has_backtest_report(artifact_kinds: set[str]) -> bool:
    return bool(artifact_kinds.intersection({BACKTEST_REPORT_ARTIFACT_KIND, BACKTEST_RUN_METADATA_ARTIFACT_KIND, "backtest_dashboard"}))


def _required_inputs(entry: ActionRegistryEntry, *, context_text: str) -> list[str] | None:
    evidence = build_action_evidence_packet(artifact_kinds=set(), context_text=context_text, web_search="auto")
    required_inputs = evaluate_action_availability(entry, evidence=evidence).required_inputs
    return list(required_inputs) if required_inputs else None


def _risk_level(entry: ActionRegistryEntry, *, context_text: str) -> str | None:
    evidence = build_action_evidence_packet(artifact_kinds=set(), context_text=context_text, web_search="auto")
    return evaluate_action_availability(entry, evidence=evidence).risk_level


def _validation_problem_lexical_hint(context_text: str) -> bool:
    normalized = context_text.lower()
    return any(term in normalized for term in ("validation failed", "static validation failed", "compile error", "validation blocker"))


def _bot_boundary_lexical_hint(context_text: str) -> bool:
    normalized = context_text.lower()
    return any(term in normalized for term in ("bot", "order", "signal", "trade live", "live trade", "paper trade", "intent"))


def _action_lexical_hints(context_text: str) -> set[str]:
    normalized = context_text.lower()
    hints: set[str] = set()
    if "backtest_config" in normalized or "backtest config" in normalized:
        hints.add("has_backtest_config")
    if "proposed intent" in normalized:
        hints.add("has_proposed_intent")
    if "risk gate" in normalized:
        hints.add("has_risk_gate")
    if _bot_boundary_lexical_hint(context_text):
        hints.add("bot_boundary_request")
    if "bot" in normalized or "runtime" in normalized:
        hints.add("bot_context")
    if _validation_problem_lexical_hint(context_text):
        hints.add("validation_problem")
    return hints


def _risk_gate_required_inputs(risk_gate_inputs: set[str] | None) -> list[str]:
    provided = set(risk_gate_inputs or set())
    required: list[str] = []
    for field in ("stop_or_invalidation", "sizing", "stale_after"):
        if field not in provided:
            required.append(field)
    return required
