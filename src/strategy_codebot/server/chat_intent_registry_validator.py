from __future__ import annotations

from typing import Any


class ChatIntentRegistryValidationError(ValueError):
    """Raised when the chat intent registry contract is malformed."""


FALLBACK_POLICY_KEYS = (
    "readonly_web_search_allowed",
    "safe_workflow_kickoff_allowed",
    "workflow_creation_allowed",
    "auto_chain_allowed",
    "tool_actions_allowed",
)


def validate_chat_intent_registry_contract(
    registry: dict[str, Any],
    *,
    label: str,
    known_workflow_ids: set[str],
    configured_model_stages: set[str],
) -> None:
    if registry.get("schema_version") != 1:
        raise ChatIntentRegistryValidationError(f"{label} schema_version must be 1")

    required_arrays = (
        "domain_scopes",
        "response_intents",
        "actions",
        "model_stages",
        "workflow_intents",
        "evidence_signals",
    )
    for key in required_arrays:
        values = registry.get(key)
        if not _string_list(values):
            raise ChatIntentRegistryValidationError(f"{label} {key} must be a unique non-empty string array")
        _ensure_unique(label, key, values)

    scopes = set(registry["domain_scopes"])
    intents = set(registry["response_intents"])
    stages = set(registry["model_stages"])
    workflow_intents = set(registry["workflow_intents"])
    evidence_signals = set(registry["evidence_signals"])

    if registry.get("default_domain_scope") not in scopes:
        raise ChatIntentRegistryValidationError(f"{label} default_domain_scope must be a known scope")
    if registry.get("fallback_response_intent") not in intents:
        raise ChatIntentRegistryValidationError(f"{label} fallback_response_intent must be a known intent")
    for key in ("min_confidence", "off_topic_block_confidence"):
        _validate_probability(label, registry, key)

    if set(stages) != set(configured_model_stages):
        raise ChatIntentRegistryValidationError(f"{label} model_stages must match configured model stage constants")

    _validate_ref_map(
        label,
        registry,
        "intent_domain_scopes",
        keys=intents,
        values=scopes,
        value_label="scope",
    )
    _validate_ref_map(
        label,
        registry,
        "intent_model_stages",
        keys=intents,
        values=stages,
        value_label="stage",
    )
    _validate_response_intent_policies(label, registry, intents=intents, workflow_intents=workflow_intents)
    _validate_response_intent_ui_policies(label, registry, intents=intents)
    _validate_workflow_intent_policies(
        label,
        registry,
        intents=intents,
        scopes=scopes,
        workflow_intents=workflow_intents,
        evidence_signals=evidence_signals,
        known_workflow_ids=known_workflow_ids,
    )
    _validate_classifier_fallback_policy(label, registry)

    guidance = registry.get("model_guidance", [])
    if not isinstance(guidance, list) or any(not isinstance(item, str) for item in guidance):
        raise ChatIntentRegistryValidationError(f"{label} model_guidance must be an array of strings")


def _validate_response_intent_policies(
    label: str,
    registry: dict[str, Any],
    *,
    intents: set[str],
    workflow_intents: set[str],
) -> None:
    policies = registry.get("response_intent_policies")
    if not isinstance(policies, dict):
        raise ChatIntentRegistryValidationError(f"{label} response_intent_policies must be an object")
    if set(policies) != intents:
        raise ChatIntentRegistryValidationError(f"{label} response_intent_policies must cover every response intent")
    for intent, policy in policies.items():
        if not isinstance(policy, dict):
            raise ChatIntentRegistryValidationError(f"{label} response_intent_policies.{intent} must be an object")
        for key in ("current_context_allowed", "readonly_web_search_allowed"):
            if not isinstance(policy.get(key), bool):
                raise ChatIntentRegistryValidationError(
                    f"{label} response_intent_policies.{intent}.{key} must be boolean"
                )
        allowed = policy.get("allowed_workflow_intents")
        if not _string_array(allowed):
            raise ChatIntentRegistryValidationError(
                f"{label} response_intent_policies.{intent}.allowed_workflow_intents "
                "must be a unique string array"
            )
        _ensure_unique(label, f"response_intent_policies.{intent}.allowed_workflow_intents", allowed)
        if set(allowed) - workflow_intents:
            raise ChatIntentRegistryValidationError(
                f"{label} response_intent_policies contains unknown workflow intents"
            )


def _validate_response_intent_ui_policies(
    label: str,
    registry: dict[str, Any],
    *,
    intents: set[str],
) -> None:
    policies = registry.get("response_intent_ui_policies")
    if not isinstance(policies, dict):
        raise ChatIntentRegistryValidationError(f"{label} response_intent_ui_policies must be an object")
    if set(policies) != intents:
        raise ChatIntentRegistryValidationError(f"{label} response_intent_ui_policies must cover every response intent")
    for intent, policy in policies.items():
        if not isinstance(policy, dict):
            raise ChatIntentRegistryValidationError(f"{label} response_intent_ui_policies.{intent} must be an object")
        for key in ("show_strategy_profile", "market_to_strategy_suggestion"):
            if not isinstance(policy.get(key), bool):
                raise ChatIntentRegistryValidationError(
                    f"{label} response_intent_ui_policies.{intent}.{key} must be boolean"
                )


def _validate_workflow_intent_policies(
    label: str,
    registry: dict[str, Any],
    *,
    intents: set[str],
    scopes: set[str],
    workflow_intents: set[str],
    evidence_signals: set[str],
    known_workflow_ids: set[str],
) -> None:
    policies = registry.get("workflow_intent_policies")
    if not isinstance(policies, dict):
        raise ChatIntentRegistryValidationError(f"{label} workflow_intent_policies must be an object")
    if set(policies) != workflow_intents:
        raise ChatIntentRegistryValidationError(f"{label} workflow_intent_policies must cover every workflow intent")
    for workflow_intent, policy in policies.items():
        if not isinstance(policy, dict):
            raise ChatIntentRegistryValidationError(f"{label} workflow_intent_policies.{workflow_intent} must be an object")
        workflow_id = policy.get("workflow_id")
        if not isinstance(workflow_id, str) or workflow_id not in known_workflow_ids:
            raise ChatIntentRegistryValidationError(f"{label} workflow_intent_policies contains unknown workflow ids")
        creation_response_intents = policy.get("creation_response_intents")
        if not _string_array(creation_response_intents):
            raise ChatIntentRegistryValidationError(
                f"{label} workflow_intent_policies.{workflow_intent}.creation_response_intents "
                "must be a unique string array"
            )
        _ensure_unique(
            label,
            f"workflow_intent_policies.{workflow_intent}.creation_response_intents",
            creation_response_intents,
        )
        if set(creation_response_intents) - intents:
            raise ChatIntentRegistryValidationError(
                f"{label} workflow_intent_policies contains unknown creation response intents"
            )
        for key in ("resume_existing", "requires_explicit_intent"):
            if not isinstance(policy.get(key), bool):
                raise ChatIntentRegistryValidationError(
                    f"{label} workflow_intent_policies.{workflow_intent}.{key} must be boolean"
                )
        _validate_workflow_timeout_fallback(
            label,
            workflow_intent,
            policy.get("timeout_fallback"),
            intents=intents,
            scopes=scopes,
            evidence_signals=evidence_signals,
        )


def _validate_workflow_timeout_fallback(
    label: str,
    workflow_intent: str,
    policy: Any,
    *,
    intents: set[str],
    scopes: set[str],
    evidence_signals: set[str],
) -> None:
    if policy is None:
        return
    if not isinstance(policy, dict):
        raise ChatIntentRegistryValidationError(
            f"{label} workflow_intent_policies.{workflow_intent}.timeout_fallback must be an object"
        )
    if not isinstance(policy.get("enabled"), bool):
        raise ChatIntentRegistryValidationError(
            f"{label} workflow_intent_policies.{workflow_intent}.timeout_fallback.enabled must be boolean"
        )
    for key in ("required_evidence_signals", "denied_evidence_signals"):
        values = policy.get(key)
        if not _string_array(values):
            raise ChatIntentRegistryValidationError(
                f"{label} workflow_intent_policies.{workflow_intent}.timeout_fallback.{key} "
                "must be a unique string array"
            )
        _ensure_unique(label, f"workflow_intent_policies.{workflow_intent}.timeout_fallback.{key}", values)
        if set(values) - evidence_signals:
            raise ChatIntentRegistryValidationError(
                f"{label} workflow_intent_policies.{workflow_intent}.timeout_fallback.{key} "
                "contains unknown evidence signals"
            )
    if policy.get("response_intent") not in intents:
        raise ChatIntentRegistryValidationError(
            f"{label} workflow_intent_policies.{workflow_intent}.timeout_fallback.response_intent "
            "must be a known response intent"
        )
    if policy.get("domain_scope") not in scopes:
        raise ChatIntentRegistryValidationError(
            f"{label} workflow_intent_policies.{workflow_intent}.timeout_fallback.domain_scope "
            "must be a known domain scope"
        )
    if not isinstance(policy.get("source"), str) or not policy["source"].strip():
        raise ChatIntentRegistryValidationError(
            f"{label} workflow_intent_policies.{workflow_intent}.timeout_fallback.source must be string"
        )


def _validate_classifier_fallback_policy(label: str, registry: dict[str, Any]) -> None:
    policy = registry.get("classifier_fallback_policy")
    if not isinstance(policy, dict):
        raise ChatIntentRegistryValidationError(f"{label} classifier_fallback_policy must be an object")
    if set(policy) != set(FALLBACK_POLICY_KEYS):
        raise ChatIntentRegistryValidationError(f"{label} classifier_fallback_policy must cover every fallback key")
    for key in FALLBACK_POLICY_KEYS:
        if not isinstance(policy.get(key), bool):
            raise ChatIntentRegistryValidationError(f"{label} classifier_fallback_policy.{key} must be boolean")


def _validate_ref_map(
    label: str,
    registry: dict[str, Any],
    key: str,
    *,
    keys: set[str],
    values: set[str],
    value_label: str,
) -> None:
    mapping = registry.get(key)
    if not isinstance(mapping, dict):
        raise ChatIntentRegistryValidationError(f"{label} {key} must be an object")
    if set(mapping) != keys:
        raise ChatIntentRegistryValidationError(f"{label} {key} must cover every response intent")
    unknown = {value for value in mapping.values() if value not in values}
    if unknown:
        if value_label == "stage":
            raise ChatIntentRegistryValidationError(f"{label} {key} contains unknown stages or unknown stage refs")
        raise ChatIntentRegistryValidationError(f"{label} {key} contains unknown {value_label} refs")


def _validate_probability(label: str, registry: dict[str, Any], key: str) -> None:
    value = registry.get(key)
    if not isinstance(value, int | float) or not 0 <= float(value) <= 1:
        raise ChatIntentRegistryValidationError(f"{label} {key} must be a number between 0 and 1")


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item for item in value)


def _string_array(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def _ensure_unique(label: str, name: str, values: list[str]) -> None:
    if len(values) != len(set(values)):
        raise ChatIntentRegistryValidationError(f"{label} {name} contains duplicate ids")
