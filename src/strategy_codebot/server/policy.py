from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from strategy_codebot.policy_engine import EVIDENCE_EDUCATION
from strategy_codebot.policy_engine import EVIDENCE_GENERATED_ARTIFACT
from strategy_codebot.policy_engine import EVIDENCE_MANUAL_RUNTIME_PROOF
from strategy_codebot.policy_engine import EVIDENCE_STATIC_VALIDATION
from strategy_codebot.policy_engine import EVIDENCE_STRATEGY_IDEA
from strategy_codebot.policy_engine import PolicyFinding as EnginePolicyFinding
from strategy_codebot.policy_engine import PolicySubject as EnginePolicySubject
from strategy_codebot.policy_engine import evaluate_policy as evaluate_text_policy
from strategy_codebot.policy_engine import policy_finding_payload as engine_policy_finding_payload

SAFE_BLOCKED_MESSAGE = "I cannot execute that request because it violates the server-side trading policy."
MARKET_DATA_REQUIRED_FIELDS = frozenset({"timestamp", "source", "symbol", "interval", "timezone"})
AGENT_LOOP_ALLOWED_RISK_TIERS = frozenset({"read"})
AGENT_LOOP_BLOCKED_TOOL_TERMS = frozenset(
    {
        "broker",
        "edit",
        "exec",
        "filesystem",
        "file-system",
        "fs.",
        "live",
        "order",
        "paper-start",
        "paper.start",
        "paper_start",
        "repo-write",
        "repo.write",
        "repo_write",
        "shell",
        "start-paper",
        "start_paper",
        "write",
    }
)


@dataclass(frozen=True)
class PolicySubject:
    surface: str
    payload: Any
    evidence_level: str = EVIDENCE_STRATEGY_IDEA


@dataclass(frozen=True)
class PolicyFinding:
    severity: str
    code: str
    message: str
    surface: str
    evidence_level: str
    rule_id: str = ""
    category: str = ""
    matched_text: str = ""
    sentence: str = ""


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    findings: tuple[PolicyFinding, ...] = ()

    @property
    def blocked_finding(self) -> PolicyFinding | None:
        return next((finding for finding in self.findings if finding.severity == "blocker"), None)


def evaluate_policy(subject: PolicySubject) -> PolicyDecision:
    decision = evaluate_text_policy(
        EnginePolicySubject(surface=subject.surface, payload=subject.payload, evidence_level=subject.evidence_level)
    )
    findings = [
        _server_policy_finding(finding)
        for finding in decision.findings
        if finding.severity == "blocker"
    ]
    findings.extend(_market_data_findings(subject))
    return PolicyDecision(allowed=not findings, findings=tuple(findings))


def evaluate_agent_loop_tool_policy(tool_id: str, risk_tier: str | None) -> PolicyDecision:
    normalized_risk = (risk_tier or "unknown").strip().lower()
    if normalized_risk not in AGENT_LOOP_ALLOWED_RISK_TIERS:
        return PolicyDecision(
            allowed=False,
            findings=(
                PolicyFinding(
                    severity="blocker",
                    code="agent_loop_tool_risk_blocked",
                    message=f"{tool_id} is blocked by risk tier {normalized_risk}.",
                    surface="agent_loop.tool",
                    evidence_level=EVIDENCE_STRATEGY_IDEA,
                ),
            ),
        )

    normalized_id = tool_id.strip().lower().replace("_", "-")
    matched_term = next((term for term in sorted(AGENT_LOOP_BLOCKED_TOOL_TERMS) if term in normalized_id), "")
    if matched_term:
        return PolicyDecision(
            allowed=False,
            findings=(
                PolicyFinding(
                    severity="blocker",
                    code="agent_loop_tool_surface_blocked",
                    message=f"{tool_id} is blocked for bounded scout loops.",
                    surface="agent_loop.tool",
                    evidence_level=EVIDENCE_STRATEGY_IDEA,
                    matched_text=matched_term,
                ),
            ),
        )

    return PolicyDecision(allowed=True)


def policy_finding_payload(finding: PolicyFinding) -> dict[str, str]:
    if finding.rule_id and finding.category:
        payload = engine_policy_finding_payload(
            EnginePolicyFinding(
                rule_id=finding.rule_id,
                category=finding.category,
                severity=finding.severity,
                code=finding.code,
                message=finding.message,
                surface=finding.surface,
                evidence_level=finding.evidence_level,
                matched_text=finding.matched_text,
                sentence=finding.sentence,
            )
        )
        return {key: value for key, value in payload.items() if value}
    payload = {
        "severity": finding.severity,
        "code": finding.code,
        "message": finding.message,
        "surface": finding.surface,
        "evidence_level": finding.evidence_level,
    }
    if finding.rule_id:
        payload["rule_id"] = finding.rule_id
    if finding.category:
        payload["category"] = finding.category
    if finding.matched_text:
        payload["matched_text"] = finding.matched_text
    if finding.sentence:
        payload["sentence"] = finding.sentence
    return payload


def _server_policy_finding(finding: EnginePolicyFinding) -> PolicyFinding:
    return PolicyFinding(
        severity=finding.severity,
        code=finding.code,
        message=finding.message,
        surface=finding.surface,
        evidence_level=finding.evidence_level,
        rule_id=finding.rule_id,
        category=finding.category,
        matched_text=finding.matched_text,
        sentence=finding.sentence,
    )


def _finding(subject: PolicySubject, code: str, message: str) -> PolicyFinding:
    return PolicyFinding(
        severity="blocker",
        code=code,
        message=message,
        surface=subject.surface,
        evidence_level=subject.evidence_level,
    )


def _market_data_findings(subject: PolicySubject) -> list[PolicyFinding]:
    findings: list[PolicyFinding] = []
    for snapshot in _iter_market_data_snapshots(subject.payload):
        if not isinstance(snapshot, dict):
            findings.append(
                _finding(subject, "market_data_metadata_missing", "Market data snapshot must be an object with metadata.")
            )
            continue
        missing = sorted(MARKET_DATA_REQUIRED_FIELDS.difference(snapshot))
        if missing:
            findings.append(
                _finding(
                    subject,
                    "market_data_metadata_missing",
                    "Market data snapshot missing required metadata: " + ", ".join(missing),
                )
            )
    return findings


def _iter_market_data_snapshots(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"market_data", "market_data_snapshot"}:
                if isinstance(item, list):
                    yield from item
                else:
                    yield item
            else:
                yield from _iter_market_data_snapshots(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_market_data_snapshots(item)
