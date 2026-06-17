from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from strategy_codebot.harness_types import STATUS_FAIL, STATUS_PASS

QUALITY_REPORT_PATH = "quality-report.json"

FULL_CAPITAL_SIZING_PATTERNS = (
    r"\b100\s*%\s*(?:of\s*)?(?:available\s*)?(?:capital|equity|account|balance)\b",
    r"\ball\s+(?:available\s+)?(?:capital|equity|account|balance)\b",
    r"\bentire\s+(?:account|balance|capital|equity)\b",
    r"\bfull\s+(?:account|balance|capital|equity)\b",
)
VAGUE_RULE_PATTERNS = (
    r"\bbuy strength\b",
    r"\bavoid bad entries\b",
    r"\brisk management\b",
    r"\btbd\b",
    r"\bto be determined\b",
)


def assess_strategy_quality(
    strategy_spec: dict[str, Any],
    pine_code: str | None,
    *,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    code = pine_code or ""
    script_type = str(strategy_spec.get("script_type", "")).lower()

    _check_position_sizing(strategy_spec, findings)
    _check_script_type_consistency(script_type, code, findings)
    _check_strategy_exits(strategy_spec, script_type, code, findings)
    _check_vague_rules(strategy_spec, findings)
    _check_repaint_risk(code, findings)
    _check_spec_code_alignment(strategy_spec, script_type, code, findings)

    blockers = [finding for finding in findings if finding["severity"] == "blocker"]
    warnings = [finding for finding in findings if finding["severity"] == "warning"]
    score = max(0, 100 - len(blockers) * 30 - len(warnings) * 10)
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "status": STATUS_FAIL if blockers else STATUS_PASS,
        "score": score,
        "findings": findings,
        "blockers": blockers,
        "warnings": warnings,
        "summary": {
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "info_count": len([finding for finding in findings if finding["severity"] == "info"]),
            "validation_status": (validation or {}).get("status"),
        },
    }


def quality_metadata(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {
            "quality_status": None,
            "quality_score": None,
            "quality_blockers": [],
            "quality_warnings": [],
        }
    return {
        "quality_status": report.get("status"),
        "quality_score": report.get("score"),
        "quality_blockers": report.get("blockers", []),
        "quality_warnings": report.get("warnings", []),
    }


def production_gate_with_quality(production_gate: dict[str, Any], quality_report: dict[str, Any]) -> dict[str, Any]:
    blockers = quality_report.get("blockers", [])
    warnings = quality_report.get("warnings", [])
    return {
        **production_gate,
        "status": STATUS_FAIL if blockers else production_gate.get("status", STATUS_PASS),
        "quality_status": quality_report.get("status"),
        "quality_score": quality_report.get("score"),
        "quality_blocker_count": len(blockers),
        "quality_warning_count": len(warnings),
        "quality_blockers": blockers,
    }


def _check_position_sizing(strategy_spec: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    sizing_text = _joined_text(strategy_spec.get("position_sizing"), *(strategy_spec.get("risk_rules") or []))
    if not sizing_text:
        _finding(findings, "warning", "position_sizing", "Strategy has no explicit bounded position sizing.", ["strategy-spec.json#/position_sizing"])
        return
    for pattern in FULL_CAPITAL_SIZING_PATTERNS:
        if re.search(pattern, sizing_text, flags=re.IGNORECASE):
            _finding(findings, "blocker", "position_sizing", "Position sizing uses full-capital or full-balance language.", ["strategy-spec.json#/position_sizing"])
            return


def _check_script_type_consistency(script_type: str, code: str, findings: list[dict[str, Any]]) -> None:
    has_strategy = bool(re.search(r"\bstrategy\s*\(", code))
    has_indicator = bool(re.search(r"\bindicator\s*\(", code))
    if script_type == "indicator" and has_strategy:
        _finding(findings, "blocker", "script_type", "Spec requests an indicator but Pine code declares a strategy.", ["strategy-spec.json#/script_type", "pine/strategy.pine"])
    if script_type == "strategy" and has_indicator:
        _finding(findings, "blocker", "script_type", "Spec requests a strategy but Pine code declares an indicator.", ["strategy-spec.json#/script_type", "pine/strategy.pine"])


def _check_strategy_exits(strategy_spec: dict[str, Any], script_type: str, code: str, findings: list[dict[str, Any]]) -> None:
    if script_type != "strategy":
        return
    if "strategy.exit" not in code:
        _finding(findings, "blocker", "exit_logic", "Strategy Pine code is missing strategy.exit for explicit stop/target handling.", ["pine/strategy.pine"])
    exit_text = _joined_text(*(strategy_spec.get("exit_rules") or []), strategy_spec.get("stop_loss"), strategy_spec.get("take_profit"))
    if not re.search(r"\b(stop|loss|take profit|target|exit)\b", exit_text, flags=re.IGNORECASE):
        _finding(findings, "warning", "exit_logic", "Strategy exit rules do not clearly describe stop-loss or take-profit behavior.", ["strategy-spec.json#/exit_rules"])


def _check_vague_rules(strategy_spec: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    for field in ("entry_rules", "exit_rules", "risk_rules"):
        values = strategy_spec.get(field) or []
        for index, value in enumerate(values):
            text = str(value).strip()
            if len(text.split()) <= 2:
                _finding(findings, "warning", "vague_rule", f"{field} contains an underspecified rule.", [f"strategy-spec.json#/{field}/{index}"])
            for pattern in VAGUE_RULE_PATTERNS:
                if re.search(pattern, text, flags=re.IGNORECASE):
                    _finding(findings, "warning", "vague_rule", f"{field} uses vague trading language: {text}", [f"strategy-spec.json#/{field}/{index}"])


def _check_repaint_risk(code: str, findings: list[dict[str, Any]]) -> None:
    if "lookahead_on" in code:
        _finding(findings, "blocker", "repaint_risk", "Pine code uses lookahead_on, which can introduce repainting.", ["pine/strategy.pine"])
    if "request.security" in code and "lookahead_off" not in code and "barstate.isconfirmed" not in code:
        _finding(findings, "warning", "repaint_risk", "Higher-timeframe request.security usage should explicitly avoid lookahead/repaint risk.", ["pine/strategy.pine"])


def _check_spec_code_alignment(strategy_spec: dict[str, Any], script_type: str, code: str, findings: list[dict[str, Any]]) -> None:
    if script_type == "strategy" and "strategy.entry" not in code:
        _finding(findings, "blocker", "spec_code_alignment", "Strategy spec produced Pine code without strategy.entry.", ["strategy-spec.json#/script_type", "pine/strategy.pine"])
    if script_type == "indicator" and not re.search(r"\bplot(?:shape|char)?\s*\(", code):
        _finding(findings, "warning", "spec_code_alignment", "Indicator code has no visible plot or marker output.", ["pine/strategy.pine"])


def _finding(findings: list[dict[str, Any]], severity: str, category: str, message: str, evidence_refs: list[str]) -> None:
    findings.append(
        {
            "severity": severity,
            "category": category,
            "message": message,
            "evidence_refs": evidence_refs,
        }
    )


def _joined_text(*parts: Any) -> str:
    return " ".join(str(part) for part in parts if part).lower()
