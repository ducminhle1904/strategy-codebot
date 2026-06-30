from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CurrentContextPolicyDecision:
    enabled: bool
    reason: str


def current_context_policy_decision(
    *,
    web_search: str,
    response_intent: str | None = None,
    current_context_required: bool = False,
    decision_source: str | None = None,
    require_web_search: bool = False,
) -> CurrentContextPolicyDecision:
    if web_search == "off":
        return CurrentContextPolicyDecision(enabled=False, reason="mode_off")
    if web_search == "on":
        return CurrentContextPolicyDecision(enabled=True, reason="mode_on")
    if require_web_search:
        return CurrentContextPolicyDecision(enabled=True, reason="required")
    if decision_source in {"fallback", "timeout_fallback"}:
        allowed = bool(_classifier_fallback_policy().get("readonly_web_search_allowed"))
        return CurrentContextPolicyDecision(
            enabled=allowed,
            reason="fallback_policy" if allowed else "fallback_policy_blocked",
        )

    policy = _response_intent_policy(response_intent)
    if not policy.get("readonly_web_search_allowed"):
        return CurrentContextPolicyDecision(enabled=False, reason="auto_policy_blocked")
    if not current_context_required:
        return CurrentContextPolicyDecision(enabled=False, reason="auto_no_semantic_current_context")
    if not policy.get("current_context_allowed"):
        return CurrentContextPolicyDecision(enabled=False, reason="auto_current_context_not_allowed")
    return CurrentContextPolicyDecision(enabled=True, reason="semantic_current_context")


def _response_intent_policy(response_intent: str | None) -> dict[str, Any]:
    from strategy_codebot.server.domain_intent_gate import response_intent_policy

    return response_intent_policy(response_intent)


def _classifier_fallback_policy() -> dict[str, Any]:
    from strategy_codebot.server.domain_intent_gate import classifier_fallback_policy

    return classifier_fallback_policy()
