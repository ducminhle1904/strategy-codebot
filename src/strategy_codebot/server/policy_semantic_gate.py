from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from strategy_codebot.policy_engine import EVIDENCE_STRATEGY_IDEA
from strategy_codebot.server.llm_clients import LLMClient
from strategy_codebot.server.llm_clients import LLM_EVENT_MESSAGE_DELTA
from strategy_codebot.server.llm_clients import stream_client
from strategy_codebot.server.llm_json import extract_json_object
from strategy_codebot.server.model_routing import MODEL_STAGE_CLASSIFIER
from strategy_codebot.server.policy import PolicyFinding

POLICY_INTENTS = {"request_execution", "boundary_statement", "educational", "ambiguous"}
POLICY_TARGETS = {
    "broker_execution",
    "live_order_execution",
    "live_ready_claim",
    "profitability_claim",
    "arbitrary_io",
}
POLARITIES = {"affirm", "deny", "constrain", "unclear"}
SEMANTIC_POLICY_MIN_CONFIDENCE = 0.7


@dataclass(frozen=True)
class PolicySemanticCandidate:
    rule_id: str
    target: str
    matched_text: str
    sentence: str


@dataclass(frozen=True)
class SemanticPolicyDecision:
    policy_intent: str
    target: str
    polarity: str
    confidence: float
    reason_code: str
    source: str = "policy_semantic_gate"
    candidate_rule_ids: tuple[str, ...] = ()


_CANDIDATE_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "broker_execution",
        "broker_execution",
        re.compile(r"\b(?:broker|exchange)\s+(?:execution|integration|deployment)\b", re.IGNORECASE),
    ),
    (
        "live_order_execution",
        "live_order_execution",
        re.compile(r"\blive\s+(?:trading|execution|orders?|trades?)\b", re.IGNORECASE),
    ),
    (
        "live_ready_claim",
        "live_ready_claim",
        re.compile(r"\b(?:live-ready|live ready|safe\s+for\s+live\s+trading|safe\s+to\s+trade)\b", re.IGNORECASE),
    ),
    (
        "profitability_claim",
        "profitability_claim",
        re.compile(r"\b(?:profitability|profitable|guaranteed\s+profits?|claim\s+profitability)\b", re.IGNORECASE),
    ),
)


def collect_semantic_policy_candidates(message_content: str) -> tuple[PolicySemanticCandidate, ...]:
    candidates: list[PolicySemanticCandidate] = []
    seen: set[tuple[str, str]] = set()
    for sentence in _sentences(message_content):
        for rule_id, target, pattern in _CANDIDATE_PATTERNS:
            match = pattern.search(sentence)
            if match is None:
                continue
            key = (rule_id, sentence)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                PolicySemanticCandidate(
                    rule_id=rule_id,
                    target=target,
                    matched_text=sentence[match.start() : match.end()],
                    sentence=sentence.strip(),
                )
            )
    return tuple(candidates)


def semantic_policy_fallback_decision(
    candidates: tuple[PolicySemanticCandidate, ...],
    *,
    source: str = "fallback",
    reason_code: str = "classifier_fallback",
) -> SemanticPolicyDecision:
    target = candidates[0].target if candidates else "arbitrary_io"
    return SemanticPolicyDecision(
        policy_intent="ambiguous",
        target=target,
        polarity="unclear",
        confidence=0.0,
        reason_code=reason_code,
        source=source,
        candidate_rule_ids=tuple(candidate.rule_id for candidate in candidates),
    )


def should_block_semantic_policy(decision: SemanticPolicyDecision) -> bool:
    return (
        decision.policy_intent == "request_execution"
        and decision.polarity == "affirm"
        and decision.confidence >= SEMANTIC_POLICY_MIN_CONFIDENCE
    )


def semantic_policy_block_finding(
    decision: SemanticPolicyDecision,
    candidates: tuple[PolicySemanticCandidate, ...],
) -> PolicyFinding:
    candidate = _candidate_for_target(candidates, decision.target) or (candidates[0] if candidates else None)
    return PolicyFinding(
        severity="blocker",
        code="trading_execution_boundary",
        message="Chat requested live or broker trading execution, which remains outside review-only boundaries.",
        surface="agent.chat.input",
        evidence_level=EVIDENCE_STRATEGY_IDEA,
        rule_id=f"policy_semantic_gate.{decision.target}",
        category="trading_boundary",
        matched_text=candidate.matched_text if candidate is not None else decision.target,
        sentence=candidate.sentence if candidate is not None else "",
    )


class PolicySemanticGateClassifier:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def classify(
        self,
        message_content: str,
        *,
        candidates: tuple[PolicySemanticCandidate, ...],
        surface: str,
        evidence_level: str,
    ) -> SemanticPolicyDecision:
        if not candidates:
            return semantic_policy_fallback_decision(candidates, reason_code="no_candidates")
        prompt = {
            "surface": surface,
            "evidence_level": evidence_level,
            "user_message": message_content[:2000],
            "candidate_findings": [_candidate_payload(candidate) for candidate in candidates],
            "allowed": {
                "policy_intent": sorted(POLICY_INTENTS),
                "target": sorted(POLICY_TARGETS),
                "polarity": sorted(POLARITIES),
            },
            "rules": [
                "Classify semantic intent only; do not authorize any tool or runtime action.",
                "Boundary statements such as paper-only, no broker execution, no live trading, or no auto-start are not execution requests.",
                "Block-worthy semantics are affirmative requests to connect brokers, execute live orders, claim live readiness, or bypass review.",
                "When unclear or low confidence, return ambiguous with polarity unclear.",
            ],
        }
        try:
            chunks: list[str] = []
            for event in stream_client(
                self.client,
                messages=[
                    {"role": "system", "content": _system_prompt()},
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
                tools=[],
                routing_context={"stage": MODEL_STAGE_CLASSIFIER},
            ):
                if event.type == LLM_EVENT_MESSAGE_DELTA and event.text:
                    chunks.append(event.text)
        except Exception:
            return semantic_policy_fallback_decision(candidates)
        return _parse_decision("".join(chunks), candidates) or semantic_policy_fallback_decision(candidates)


def _parse_decision(
    raw_text: str,
    candidates: tuple[PolicySemanticCandidate, ...],
) -> SemanticPolicyDecision | None:
    decoded = extract_json_object(raw_text)
    if not decoded:
        return None
    policy_intent = _enum(decoded.get("policy_intent"), POLICY_INTENTS)
    target = _enum(decoded.get("target"), POLICY_TARGETS)
    polarity = _enum(decoded.get("polarity"), POLARITIES)
    if policy_intent is None or polarity is None:
        return None
    candidate_targets = {candidate.target for candidate in candidates}
    if target is None or target not in candidate_targets:
        target = candidates[0].target
    confidence = _confidence(decoded.get("confidence"))
    reason_code = _safe_reason_code(decoded.get("reason_code")) or "semantic_policy_decision"
    return SemanticPolicyDecision(
        policy_intent=policy_intent,
        target=target,
        polarity=polarity,
        confidence=confidence,
        reason_code=reason_code,
        candidate_rule_ids=tuple(candidate.rule_id for candidate in candidates),
    )


def _system_prompt() -> str:
    return (
        "You are a strict semantic policy classifier. Return one JSON object only with "
        "policy_intent, target, polarity, confidence, and reason_code. Use only the enum values supplied. "
        "You classify whether the user is requesting prohibited execution or merely stating constraints."
    )


def _candidate_payload(candidate: PolicySemanticCandidate) -> dict[str, str]:
    return {
        "rule_id": candidate.rule_id,
        "target": candidate.target,
        "matched_text": candidate.matched_text,
        "sentence": candidate.sentence[:500],
    }


def _candidate_for_target(
    candidates: tuple[PolicySemanticCandidate, ...],
    target: str,
) -> PolicySemanticCandidate | None:
    for candidate in candidates:
        if candidate.target == target:
            return candidate
    return None


def _sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    return [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+|\n+", normalized) if part.strip()]


def _enum(value: Any, allowed: set[str]) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in allowed else None


def _confidence(value: Any) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return 0.0


def _safe_reason_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip().lower()).strip("_")
    return normalized[:80] or None
