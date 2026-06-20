from __future__ import annotations

import json
import re
from dataclasses import dataclass
from re import Pattern
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from strategy_codebot.paths import repo_root, resolve_repo_path

EVIDENCE_EDUCATION = "education"
EVIDENCE_STRATEGY_IDEA = "strategy_idea"
EVIDENCE_GENERATED_ARTIFACT = "generated_artifact"
EVIDENCE_STATIC_VALIDATION = "static_validation"
EVIDENCE_MANUAL_RUNTIME_PROOF = "manual_runtime_proof"
POLICY_RULES_PATH = "configs/policy-rules.yaml"
POLICY_SAFE_CONTEXT_KEYS = {"non_goals", "policy_observations"}
POLICY_RULE_SEVERITIES = {"blocker", "warning", "info"}
POLICY_RULE_EVIDENCE_LEVELS = {
    EVIDENCE_EDUCATION,
    EVIDENCE_STRATEGY_IDEA,
    EVIDENCE_GENERATED_ARTIFACT,
    EVIDENCE_STATIC_VALIDATION,
    EVIDENCE_MANUAL_RUNTIME_PROOF,
}
POLICY_RULE_SURFACES = {
    "agent.chat.output",
    "agent.run.output",
    "artifact.validation_report",
    "artifact.runtime_trace_summary",
    "policy_text",
    "review.risk",
    "text",
    "tool.generate_pine",
    "user_prompt",
}
_CONTRAST_BOUNDARY_RE = re.compile(r"\b(?:but|however|except|though|although|yet)\b", flags=re.IGNORECASE)


@dataclass(frozen=True)
class PolicySubject:
    surface: str
    payload: Any
    evidence_level: str = EVIDENCE_STRATEGY_IDEA


@dataclass(frozen=True)
class PolicyRule:
    id: str
    category: str
    severity: str
    code: str
    message: str
    block_patterns: tuple[str, ...]
    allow_patterns: tuple[str, ...] = ()
    surfaces: tuple[str, ...] = ()
    evidence_levels: tuple[str, ...] = ()
    block_regexes: tuple[Pattern[str], ...] = ()
    allow_regexes: tuple[Pattern[str], ...] = ()


@dataclass(frozen=True)
class PolicyFinding:
    rule_id: str
    category: str
    severity: str
    code: str
    message: str
    surface: str
    evidence_level: str
    matched_text: str
    sentence: str

    @property
    def claim(self) -> str:
        return self.matched_text


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    findings: tuple[PolicyFinding, ...] = ()

    @property
    def blocked_finding(self) -> PolicyFinding | None:
        return next((finding for finding in self.findings if finding.severity == "blocker"), None)


def evaluate_policy(subject: PolicySubject, *, rules_path: Path | None = None) -> PolicyDecision:
    findings = tuple(
        _dedupe_findings(
            _evaluate_fragments(
                _payload_fragments(subject.payload),
                surface=subject.surface,
                evidence_level=subject.evidence_level,
                rules=load_policy_rules(rules_path),
            ),
        )
    )
    return PolicyDecision(allowed=not any(finding.severity == "blocker" for finding in findings), findings=findings)


def find_policy_findings(
    text: str,
    *,
    surface: str = "text",
    evidence_level: str = EVIDENCE_STRATEGY_IDEA,
    rules_path: Path | None = None,
) -> list[dict[str, str]]:
    findings = _dedupe_findings(
        _evaluate_text(text, surface=surface, evidence_level=evidence_level, rules=load_policy_rules(rules_path))
    )
    return [policy_finding_payload(finding) for finding in findings]


def contains_blocking_policy(text: str, *, surface: str = "text", evidence_level: str = EVIDENCE_STRATEGY_IDEA) -> bool:
    rules = load_policy_rules()
    for finding in _evaluate_text(text, surface=surface, evidence_level=evidence_level, rules=rules):
        if finding.severity == "blocker":
            return True
    return False


def policy_finding_payload(finding: PolicyFinding) -> dict[str, str]:
    return {
        "rule_id": finding.rule_id,
        "category": finding.category,
        "severity": finding.severity,
        "code": finding.code,
        "message": finding.message,
        "surface": finding.surface,
        "evidence_level": finding.evidence_level,
        "matched_text": finding.matched_text,
        "sentence": finding.sentence,
        "claim": finding.matched_text,
        "reason": finding.message,
    }


def validate_policy_rules(path: Path | None = None) -> dict[str, Any]:
    errors: list[str] = []
    rules = _load_policy_rules(resolve_repo_path(path or repo_root() / POLICY_RULES_PATH), errors=errors)
    return {
        "status": "pass" if not errors else "fail",
        "rule_count": len(rules),
        "errors": errors,
    }


@lru_cache(maxsize=8)
def _cached_policy_rules(path: str) -> tuple[PolicyRule, ...]:
    errors: list[str] = []
    rules = tuple(_load_policy_rules(Path(path), errors=errors))
    if errors:
        raise ValueError("Invalid policy rules: " + "; ".join(errors))
    return rules


def load_policy_rules(path: Path | None = None) -> tuple[PolicyRule, ...]:
    resolved = resolve_repo_path(path or repo_root() / POLICY_RULES_PATH)
    return _cached_policy_rules(str(resolved))


def _load_policy_rules(path: Path, *, errors: list[str]) -> list[PolicyRule]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        errors.append(f"unable to read policy rules: {type(exc).__name__}: {exc}")
        return []
    raw_rules = payload.get("rules")
    if not isinstance(raw_rules, list):
        errors.append("policy rules must contain a rules list")
        return []
    rules: list[PolicyRule] = []
    seen_ids: set[str] = set()
    for index, raw_rule in enumerate(raw_rules):
        if not isinstance(raw_rule, dict):
            errors.append(f"rule {index} must be a mapping")
            continue
        missing = [key for key in ("id", "category", "severity", "code", "message", "block_patterns") if not raw_rule.get(key)]
        if missing:
            errors.append(f"rule {raw_rule.get('id', index)} missing: {', '.join(missing)}")
            continue
        rule_id = str(raw_rule["id"])
        if rule_id in seen_ids:
            errors.append(f"duplicate rule id: {rule_id}")
        seen_ids.add(rule_id)
        block_patterns = tuple(str(pattern) for pattern in raw_rule.get("block_patterns", []))
        allow_patterns = tuple(str(pattern) for pattern in raw_rule.get("allow_patterns", []))
        severity = str(raw_rule["severity"])
        if severity not in POLICY_RULE_SEVERITIES:
            errors.append(f"rule {rule_id} invalid severity: {severity}")
        surfaces = tuple(str(surface) for surface in raw_rule.get("surfaces", []) or ())
        for surface in surfaces:
            if surface not in POLICY_RULE_SURFACES:
                errors.append(f"rule {rule_id} invalid surface: {surface}")
        evidence_levels = tuple(str(level) for level in raw_rule.get("evidence_levels", []) or ())
        for level in evidence_levels:
            if level not in POLICY_RULE_EVIDENCE_LEVELS:
                errors.append(f"rule {rule_id} invalid evidence_level: {level}")
        block_regexes: list[Pattern[str]] = []
        allow_regexes: list[Pattern[str]] = []
        rule_error_count = len(errors)
        for pattern in block_patterns:
            try:
                block_regexes.append(re.compile(pattern, flags=re.IGNORECASE))
            except re.error as exc:
                errors.append(f"rule {rule_id} invalid regex {pattern!r}: {exc}")
        for pattern in allow_patterns:
            try:
                allow_regexes.append(re.compile(pattern, flags=re.IGNORECASE))
            except re.error as exc:
                errors.append(f"rule {rule_id} invalid regex {pattern!r}: {exc}")
        if len(errors) != rule_error_count:
            continue
        rules.append(
            PolicyRule(
                id=rule_id,
                category=str(raw_rule["category"]),
                severity=severity,
                code=str(raw_rule["code"]),
                message=str(raw_rule["message"]),
                block_patterns=block_patterns,
                allow_patterns=allow_patterns,
                surfaces=surfaces,
                evidence_levels=evidence_levels,
                block_regexes=tuple(block_regexes),
                allow_regexes=tuple(allow_regexes),
            )
        )
    return rules


def _evaluate_text(text: str, *, surface: str, evidence_level: str, rules: tuple[PolicyRule, ...]) -> list[PolicyFinding]:
    return _evaluate_fragments(
        _policy_text_fragments(text),
        surface=surface,
        evidence_level=evidence_level,
        rules=rules,
    )


def _evaluate_fragments(fragments: list[str], *, surface: str, evidence_level: str, rules: tuple[PolicyRule, ...]) -> list[PolicyFinding]:
    findings: list[PolicyFinding] = []
    for fragment in fragments:
        for sentence in _policy_sentences(fragment):
            for rule in rules:
                if not _rule_applies(rule, surface=surface, evidence_level=evidence_level):
                    continue
                for regex in rule.block_regexes:
                    match = regex.search(sentence)
                    if match is None or _is_allowed_match(sentence, match, rule.allow_regexes):
                        continue
                    matched_text = sentence[match.start() : match.end()]
                    findings.append(
                        PolicyFinding(
                            rule_id=rule.id,
                            category=rule.category,
                            severity=rule.severity,
                            code=rule.code,
                            message=rule.message.format(matched_text=matched_text),
                            surface=surface,
                            evidence_level=evidence_level,
                            matched_text=matched_text,
                            sentence=sentence,
                        )
                    )
                    break
    return findings


def _payload_fragments(payload: Any) -> list[str]:
    if isinstance(payload, str):
        return _policy_text_fragments(payload)
    fragments: list[str] = []
    _collect_policy_fragments(payload, fragments)
    return fragments


def _rule_applies(rule: PolicyRule, *, surface: str, evidence_level: str) -> bool:
    surface_allowed = not rule.surfaces or surface in rule.surfaces
    evidence_allowed = not rule.evidence_levels or evidence_level in rule.evidence_levels
    return surface_allowed and evidence_allowed


def _is_allowed_match(sentence: str, block_match: re.Match[str], allow_regexes: tuple[Pattern[str], ...]) -> bool:
    for allow_regex in allow_regexes:
        for allow_match in allow_regex.finditer(sentence):
            overlaps = allow_match.start() <= block_match.start() and block_match.end() <= allow_match.end()
            if not overlaps:
                continue
            between = sentence[allow_match.start() : block_match.start()]
            if _CONTRAST_BOUNDARY_RE.search(between):
                continue
            return True
    return False


def _policy_text_fragments(text: str) -> list[str]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return [text]
    fragments: list[str] = []
    _collect_policy_fragments(payload, fragments)
    return fragments


def _collect_policy_fragments(value: Any, fragments: list[str], *, key: str | None = None) -> None:
    if key in POLICY_SAFE_CONTEXT_KEYS:
        return
    if isinstance(value, str):
        fragments.append(value)
    elif isinstance(value, dict):
        for child_key, child_value in value.items():
            _collect_policy_fragments(child_value, fragments, key=str(child_key))
    elif isinstance(value, list):
        for item in value:
            _collect_policy_fragments(item, fragments, key=key)


def _policy_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|[\n\r;]+", normalized) if part.strip()]


def _dedupe_findings(findings: list[PolicyFinding]) -> list[PolicyFinding]:
    output: list[PolicyFinding] = []
    seen: set[tuple[str, str, str]] = set()
    for finding in findings:
        key = (finding.rule_id, finding.sentence, finding.matched_text.lower())
        if key in seen:
            continue
        seen.add(key)
        output.append(finding)
    return output
