from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from strategy_codebot.paths import repo_root
from strategy_codebot.server.artifact_kinds import BACKTEST_REPORT_ARTIFACT_KIND
from strategy_codebot.server.artifact_kinds import BACKTEST_RUN_METADATA_ARTIFACT_KIND
from strategy_codebot.server.artifact_kinds import RISK_GATE_REPORT_ARTIFACT_KIND
from strategy_codebot.server.artifact_kinds import ROBUSTNESS_REPORT_ARTIFACT_KIND
from strategy_codebot.server.chat_intent_registry_contract import CHAT_INTENT_REGISTRY as GENERATED_CHAT_INTENT_REGISTRY
from strategy_codebot.server.chat_intent_registry_contract import CHAT_INTENT_MODEL_STAGES as GENERATED_CHAT_INTENT_MODEL_STAGES
from strategy_codebot.server.chat_intent_registry_validator import validate_chat_intent_registry_contract
from strategy_codebot.server.workflow_registry_contract import WORKFLOW_DEFINITIONS

CHAT_INTENT_REGISTRY_PATH = repo_root() / "contracts" / "chat-intent-registry.json"


@dataclass(frozen=True)
class DomainScopeDecision:
    allowed: bool
    scope: str
    reason: str
    confidence: float

    def payload(self) -> dict[str, Any]:
        return {
            "domain_scope": self.scope,
            "domain_scope_allowed": self.allowed,
            "domain_scope_confidence": round(max(0.0, min(1.0, self.confidence)), 3),
            "domain_scope_reason": self.reason,
        }


def load_chat_intent_registry() -> dict[str, Any]:
    registry = json.loads(CHAT_INTENT_REGISTRY_PATH.read_text(encoding="utf-8"))
    validate_chat_intent_registry(registry)
    return registry


def validate_chat_intent_registry(registry: dict[str, Any]) -> None:
    validate_chat_intent_registry_contract(
        registry,
        label="chat intent registry",
        known_workflow_ids=set(WORKFLOW_DEFINITIONS),
        configured_model_stages=set(GENERATED_CHAT_INTENT_MODEL_STAGES),
    )


CHAT_INTENT_REGISTRY = GENERATED_CHAT_INTENT_REGISTRY
validate_chat_intent_registry(CHAT_INTENT_REGISTRY)
DOMAIN_SCOPES = set(CHAT_INTENT_REGISTRY["domain_scopes"])
RESPONSE_INTENTS = set(CHAT_INTENT_REGISTRY["response_intents"])
CHAT_INTENT_ACTIONS = set(CHAT_INTENT_REGISTRY["actions"])
CHAT_INTENT_MODEL_STAGES = set(CHAT_INTENT_REGISTRY["model_stages"])
WORKFLOW_INTENTS = set(CHAT_INTENT_REGISTRY["workflow_intents"])
EVIDENCE_SIGNALS = set(CHAT_INTENT_REGISTRY["evidence_signals"])
CHAT_INTENT_MIN_CONFIDENCE = float(CHAT_INTENT_REGISTRY["min_confidence"])
OFF_TOPIC_BLOCK_CONFIDENCE = float(CHAT_INTENT_REGISTRY["off_topic_block_confidence"])


def chat_intent_registry_guidance() -> str:
    return " ".join(str(item) for item in CHAT_INTENT_REGISTRY.get("model_guidance", []) if isinstance(item, str))


def normalize_evidence_signals(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple | set):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, str) and item in EVIDENCE_SIGNALS and item not in seen:
            result.append(item)
            seen.add(item)
    return tuple(result)


def evidence_signals_from_regex(regex_evidence: dict[str, bool] | None) -> tuple[str, ...]:
    if not isinstance(regex_evidence, dict):
        return ()
    return normalize_evidence_signals([key for key, value in regex_evidence.items() if value])


def normalize_domain_scope(value: Any, *, fallback: str | None = None) -> str:
    candidate = value if isinstance(value, str) else None
    if candidate in DOMAIN_SCOPES:
        return candidate
    if fallback in DOMAIN_SCOPES:
        return str(fallback)
    return str(CHAT_INTENT_REGISTRY["default_domain_scope"])


def normalize_workflow_intent(value: Any) -> str | None:
    return value if isinstance(value, str) and value in WORKFLOW_INTENTS else None


def domain_scope_for_response_intent(response_intent: str | None) -> str:
    intent_scopes = CHAT_INTENT_REGISTRY.get("intent_domain_scopes", {})
    if isinstance(response_intent, str) and isinstance(intent_scopes, dict):
        scope = intent_scopes.get(response_intent)
        if isinstance(scope, str) and scope in DOMAIN_SCOPES:
            return scope
    return str(CHAT_INTENT_REGISTRY["default_domain_scope"])


def model_stage_for_response_intent(response_intent: str | None, *, fallback: str) -> str:
    intent_stages = CHAT_INTENT_REGISTRY.get("intent_model_stages", {})
    if isinstance(response_intent, str) and isinstance(intent_stages, dict):
        stage = intent_stages.get(response_intent)
        if isinstance(stage, str) and stage in CHAT_INTENT_MODEL_STAGES:
            return stage
    return fallback


def response_intent_policy(response_intent: str | None) -> dict[str, Any]:
    policies = CHAT_INTENT_REGISTRY.get("response_intent_policies", {})
    if isinstance(response_intent, str) and isinstance(policies, dict):
        policy = policies.get(response_intent)
        if isinstance(policy, dict):
            return policy
    return {}


def workflow_intent_policy(workflow_intent: str | None) -> dict[str, Any]:
    policies = CHAT_INTENT_REGISTRY.get("workflow_intent_policies", {})
    if isinstance(workflow_intent, str) and isinstance(policies, dict):
        policy = policies.get(workflow_intent)
        if isinstance(policy, dict):
            return policy
    return {}


def workflow_timeout_fallback_policy(workflow_intent: str | None) -> dict[str, Any]:
    policy = workflow_intent_policy(workflow_intent).get("timeout_fallback")
    return policy if isinstance(policy, dict) else {}


def classifier_fallback_policy() -> dict[str, Any]:
    policy = CHAT_INTENT_REGISTRY.get("classifier_fallback_policy", {})
    return policy if isinstance(policy, dict) else {}


def response_intent_allows_workflow(response_intent: str | None, workflow_intent: str | None) -> bool:
    if workflow_intent is None:
        return False
    allowed = response_intent_policy(response_intent).get("allowed_workflow_intents", [])
    return isinstance(allowed, list) and workflow_intent in allowed


def workflow_id_for_intent(workflow_intent: str | None) -> str | None:
    workflow_id = workflow_intent_policy(workflow_intent).get("workflow_id")
    return workflow_id if isinstance(workflow_id, str) and workflow_id else None


def should_block_domain_scope(scope: str, confidence: float) -> bool:
    return scope == "off_topic" and confidence >= OFF_TOPIC_BLOCK_CONFIDENCE


def precheck_domain_scope(message_content: str, *, artifact_kinds: set[str] | None = None) -> DomainScopeDecision | None:
    normalized = " ".join((message_content or "").lower().split())
    artifact_kinds = artifact_kinds or set()
    if not normalized:
        return DomainScopeDecision(True, "product_help", "empty_or_whitespace", 0.8)
    if _small_talk_or_context_followup_precheck(normalized):
        return DomainScopeDecision(True, "context_followup", "small_talk_or_context_followup", 0.72)
    if _artifact_context_followup_precheck(normalized, artifact_kinds):
        return DomainScopeDecision(True, "artifact_followup", "artifact_context_signal", 0.86)
    if _explicit_off_topic_precheck(normalized):
        return DomainScopeDecision(False, "off_topic", "explicit_off_topic_request", 0.9)
    return None


def classify_domain_scope_compat(
    message_content: str,
    *,
    artifact_kinds: set[str] | None = None,
) -> DomainScopeDecision:
    precheck = precheck_domain_scope(message_content, artifact_kinds=artifact_kinds)
    if precheck is not None:
        return precheck
    normalized = " ".join((message_content or "").lower().split())
    if _general_task_lexical_hint(normalized):
        return DomainScopeDecision(True, "ambiguous", "semantic_classifier_required", 0.55)
    return DomainScopeDecision(True, "ambiguous", "ambiguous_context", 0.55)


def _artifact_kind_matches(kind: str, terms: tuple[str, ...]) -> bool:
    normalized = kind.lower()
    return any(term in normalized for term in terms)


def _artifact_context_followup_precheck(normalized: str, artifact_kinds: set[str]) -> bool:
    if not artifact_kinds:
        return False
    has_strategy_context = any(
        _artifact_kind_matches(
            kind,
            (
                BACKTEST_REPORT_ARTIFACT_KIND,
                BACKTEST_RUN_METADATA_ARTIFACT_KIND,
                ROBUSTNESS_REPORT_ARTIFACT_KIND,
                RISK_GATE_REPORT_ARTIFACT_KIND,
                "backtest",
                "pine",
                "strategy",
            ),
        )
        for kind in artifact_kinds
    )
    if not has_strategy_context:
        return False
    return bool(
        re.search(
            r"\b(?:this|current|the)\s+(?:evidence|preview|report|result|results|strategy|artifact)\b"
            r"|\b(?:review|summarize|explain|inspect)\s+(?:this|the|current)\b",
            normalized,
        )
    )


def _small_talk_or_context_followup_precheck(normalized: str) -> bool:
    small_talk = {"hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "chào", "cảm ơn", "oke"}
    if normalized in small_talk:
        return True
    return bool(re.search(r"\b(?:what next|what did i mention|giờ sao|làm gì tiếp|nên làm gì|cái này)\b", normalized))


def _explicit_off_topic_precheck(normalized: str) -> bool:
    if any(term in normalized for term in ("pine script", "trading script", "strategy code", "mql5")):
        return False
    return bool(
        re.search(
            r"\b(?:todo app|cover letter|essay|homework|legal contract|marketing copy|math problem|"
            r"medical|poem|python script|react component|recipe|resume|song lyrics|sql query|travel itinerary)\b"
            r"|\bwrite an email\b"
            r"|\b(?:viết email|làm thơ|nấu ăn|du lịch|bài tập|hợp đồng)\b",
            normalized,
        )
    )


def _general_task_lexical_hint(normalized: str) -> bool:
    return bool(
        len(normalized.split()) >= 3
        and re.search(r"\b(?:build|create|draft|explain|generate|how to|summarize|translate|write|tạo|viết)\b", normalized)
    )
