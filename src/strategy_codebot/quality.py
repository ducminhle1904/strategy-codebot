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
TRADER_ASSUMPTION_RUBRIC = (
    ("market_premise", (r"\bregime\b", r"\btrend\b", r"\brange\b", r"\bvolatil", r"\bliquidity\b", r"\bsession\b", r"\bmarket structure\b"), "State the market regime or liquidity condition where the setup is expected to make sense.", True),
    ("edge_plausibility", (r"\bedge\b", r"\bthesis\b", r"\bpremise\b", r"\bwhy\b", r"\binefficien", r"\bliquidity sweep\b", r"\bmean reversion\b", r"\bmomentum\b"), "State why the setup should have an edge in the named regime without claiming expected profit.", False),
    ("entry_trigger", (r"\bconfirmed\b", r"\breclaim\b", r"\bretest\b", r"\bbreak of structure\b", r"\bbos\b", r"\brejection\b", r"\bsweep\b", r"\bcross(?:es|ing)?\b"), "Make the entry trigger observable and confirmed, not discretionary.", False),
    ("invalidation", (r"\binvalidat", r"\bstop\b", r"\bstop-loss\b", r"\bwick\b", r"\bswept\b", r"\blevel breaks\b"), "Define the price action that proves the setup wrong before coding entries.", True),
    ("structure_target", (r"\btarget\b", r"\btake profit\b", r"\brisk[- ]?reward\b", r"\b\\d+(?:\\.\\d+)?r\b", r"\bstructure\b"), "Tie exits to structure targets or a bounded reward/risk fallback.", True),
    ("exit_calibration", (r"\bexit\b", r"\btake profit\b", r"\btarget\b", r"\brisk[- ]?reward\b", r"\b\\d+(?:\\.\\d+)?r\b", r"\bholding\b"), "Calibrate exits with structure, bounded reward/risk, or holding assumptions instead of open-ended exits.", False),
    ("risk_concentration", (r"\b1\s*%\b", r"\b2\s*%\b", r"\bfixed risk\b", r"\bportfolio heat\b", r"\bexposure\b", r"\bcorrelation\b", r"\bleverage\b"), "State bounded per-trade risk and exposure or portfolio-heat assumptions.", True),
    ("execution_realism", (r"\bslippage\b", r"\bspread\b", r"\bfee\b", r"\bfill\b", r"\bliquidity\b", r"\bsession\b", r"\bexecution\b"), "Account for spread, slippage, fees, liquidity, session, or fill assumptions that affect preview realism.", False),
    ("sample_adequacy", (r"\bsample\b", r"\btrade count\b", r"\bout-of-sample\b", r"\bwalk-forward\b", r"\bbacktest\b", r"\bvalidation\b"), "State sample-size, backtest, out-of-sample, or validation assumptions before judging robustness.", False),
    ("false_break_handling", (r"\bfakeout\b", r"\bfalse break\b", r"\bfailed reclaim\b", r"\bavoid chasing\b", r"\breclaim\b", r"\bsweep\b"), "Describe fakeout or failed-reclaim handling so the strategy does not chase every break.", False),
    ("session_liquidity_timeframe", (r"\bsession\b", r"\blondon\b", r"\bnew york\b", r"\basia\b", r"\bweekend\b", r"\bliquidity\b", r"\btimeframe\b", r"\b\d+[mhdw]\b"), "Name timeframe, session, or liquidity assumptions that affect execution quality.", False),
)
TRADER_ASSUMPTION_CHECKS = tuple((name, patterns) for name, patterns, _hint, _critical in TRADER_ASSUMPTION_RUBRIC)
TRADER_ASSUMPTION_HINTS = {name: hint for name, _patterns, hint, _critical in TRADER_ASSUMPTION_RUBRIC}
BOUNDED_RISK_PATTERNS = (r"\b1\s*%\b", r"\b2\s*%\b", r"\bfixed risk\b", r"\bfixed fractional\b", r"\bfixed units\b", r"\bbounded\b")
RISK_CONCENTRATION_PATTERNS = (r"\bportfolio heat\b", r"\bexposure\b", r"\bcorrelat", r"\bleverage\b")
OVERFIT_TERMS = (
    "optimize",
    "curve fit",
    "walk-forward",
    "out-of-sample",
    "few inputs",
    "bounded inputs",
    "avoid overfit",
    "manual validation",
)
PRICE_ACTION_FORBIDDEN_TERMS = ("ta.atr", "ta.sma", "ta.ema", "ta.wma", "ta.rma", "ta.rsi", "ta.macd", "ta.stoch")
SOFT_GATE_CRITICAL_ASSUMPTIONS = {name for name, _patterns, _hint, critical in TRADER_ASSUMPTION_RUBRIC if critical}


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
    sophistication = _assess_strategy_sophistication(strategy_spec, code)
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "status": STATUS_FAIL if blockers else STATUS_PASS,
        "score": score,
        "safety_quality": {
            "status": STATUS_FAIL if blockers else STATUS_PASS,
            "score": score,
            "blockers": blockers,
            "warnings": warnings,
        },
        "strategy_sophistication": sophistication,
        "sophistication_score": sophistication["score"],
        "sophistication_grade": sophistication["grade"],
        "missing_trader_assumptions": sophistication["missing_trader_assumptions"],
        "improvement_hints": sophistication["improvement_hints"],
        "warn_only": True,
        "findings": findings,
        "blockers": blockers,
        "warnings": warnings,
        "summary": {
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "info_count": len([finding for finding in findings if finding["severity"] == "info"]),
            "validation_status": (validation or {}).get("status"),
            "sophistication_grade": sophistication["grade"],
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
        "sophistication_score": report.get("sophistication_score"),
        "sophistication_grade": report.get("sophistication_grade"),
        "missing_trader_assumptions": report.get("missing_trader_assumptions", []),
    }


def production_gate_with_quality(production_gate: dict[str, Any], quality_report: dict[str, Any]) -> dict[str, Any]:
    blockers = quality_report.get("blockers", [])
    warnings = quality_report.get("warnings", [])
    soft_gate = _sophistication_soft_gate(quality_report, repair_count=int(production_gate.get("repair_count") or 0))
    sophistication_fixes = [
        f"sophistication:{item}"
        for item in quality_report.get("missing_trader_assumptions", [])
    ]
    existing_warning_fixes = production_gate.get("warning_required_fixes", [])
    if not isinstance(existing_warning_fixes, list):
        existing_warning_fixes = []
    existing_blocking_fixes = production_gate.get("blocking_required_fixes", [])
    if not isinstance(existing_blocking_fixes, list):
        existing_blocking_fixes = []
    return {
        **production_gate,
        "status": STATUS_FAIL if blockers or soft_gate["blockers"] else production_gate.get("status", STATUS_PASS),
        "quality_gate_mode": "soft",
        "quality_status": quality_report.get("status"),
        "quality_score": quality_report.get("score"),
        "sophistication_score": quality_report.get("sophistication_score"),
        "sophistication_grade": quality_report.get("sophistication_grade"),
        "sophistication_warn_only": not bool(soft_gate["blockers"]),
        "missing_trader_assumptions": quality_report.get("missing_trader_assumptions", []),
        "sophistication_required_fixes": sophistication_fixes,
        "sophistication_blockers": soft_gate["blockers"],
        "sophistication_warnings": soft_gate["warnings"],
        "repair_attempted_for_quality": soft_gate["repair_attempted"],
        "blocking_required_fixes": [*existing_blocking_fixes, *soft_gate["blockers"]],
        "warning_required_fixes": [*existing_warning_fixes, *soft_gate["warnings"]],
        "quality_blocker_count": len(blockers),
        "quality_warning_count": len(warnings),
        "quality_blockers": blockers,
    }


def _sophistication_soft_gate(quality_report: dict[str, Any], *, repair_count: int) -> dict[str, Any]:
    grade = str(quality_report.get("sophistication_grade") or "")
    missing = [str(item) for item in quality_report.get("missing_trader_assumptions", [])]
    warnings = [f"sophistication:{item}" for item in missing]
    critical_missing = [item for item in missing if item in SOFT_GATE_CRITICAL_ASSUMPTIONS]
    blockers: list[str] = []
    if grade == "weak" and (critical_missing or len(missing) >= 3):
        blockers = [f"sophistication:{item}" for item in (critical_missing or missing)]
    if grade == "weak" and repair_count > 0 and missing:
        blockers = sorted(set([*blockers, *warnings]))
    return {
        "blockers": blockers,
        "warnings": [warning for warning in warnings if warning not in blockers],
        "repair_attempted": repair_count > 0 and bool(missing),
    }


def _assess_strategy_sophistication(strategy_spec: dict[str, Any], code: str) -> dict[str, Any]:
    text = _joined_text(
        strategy_spec.get("name"),
        strategy_spec.get("market"),
        strategy_spec.get("timeframe"),
        strategy_spec.get("position_sizing"),
        strategy_spec.get("stop_loss"),
        strategy_spec.get("take_profit"),
        *(strategy_spec.get("entry_rules") or []),
        *(strategy_spec.get("exit_rules") or []),
        *(strategy_spec.get("risk_rules") or []),
        *(strategy_spec.get("constraints") or []),
        code,
    )
    missing = [
        name
        for name, patterns in TRADER_ASSUMPTION_CHECKS
        if not (_risk_concentration_present(text) if name == "risk_concentration" else _matches_any(text, patterns))
    ]
    hints = [_sophistication_hint(name) for name in missing]
    points = {
        name: name not in missing
        for name, _patterns in TRADER_ASSUMPTION_CHECKS
    }
    overfit_awareness = any(term in text for term in OVERFIT_TERMS) or _pine_input_count(code) <= 8
    points["overfit_awareness"] = overfit_awareness
    if not overfit_awareness:
        missing.append("overfit_awareness")
        hints.append("Limit tunable inputs and state validation assumptions to reduce curve-fit risk.")
    price_action_only = _looks_price_action_only(text)
    forbidden_indicators = [term for term in PRICE_ACTION_FORBIDDEN_TERMS if term in code.lower()]
    if price_action_only and forbidden_indicators:
        missing.append("price_action_purity")
        hints.append("Remove forbidden indicator helpers and express the setup with OHLC structure only.")
        points["price_action_purity"] = False
    else:
        points["price_action_purity"] = True
    passed = sum(1 for passed_check in points.values() if passed_check)
    score = int(round((passed / max(1, len(points))) * 100))
    if score >= 90:
        grade = "strong"
    elif score >= 80:
        grade = "acceptable"
    else:
        grade = "weak"
    return {
        "status": STATUS_PASS,
        "score": score,
        "grade": grade,
        "warn_only": True,
        "rubric_sources": [
            "edge-strategy-reviewer",
            "technical-analyst",
            "position-sizer",
            "backtest-expert",
        ],
        "checks": points,
        "missing_trader_assumptions": missing,
        "improvement_hints": hints,
    }


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _risk_concentration_present(text: str) -> bool:
    return _matches_any(text, BOUNDED_RISK_PATTERNS) and _matches_any(text, RISK_CONCENTRATION_PATTERNS)


def _sophistication_hint(name: str) -> str:
    return TRADER_ASSUMPTION_HINTS.get(name, f"Add a concrete {name.replace('_', ' ')} assumption.")


def _pine_input_count(code: str) -> int:
    return len(re.findall(r"\binput\.", code))


def _looks_price_action_only(text: str) -> bool:
    return "price action" in text and any(term in text for term in ("no indicator", "without indicator", "forbid indicator", "forbids indicator", "ohlc"))


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
