from __future__ import annotations

import re
from typing import Any


def generate_pine(spec: dict[str, Any]) -> str:
    script_type = spec["script_type"]
    title = f"Strategy Codebot {spec.get('symbol') or spec['market']} {spec['timeframe']}"
    entry_text = " | ".join(spec["entry_rules"])
    exit_text = " | ".join(spec["exit_rules"])
    risk_text = " | ".join(spec["risk_rules"])

    if script_type == "indicator":
        return "\n".join(
            [
                "//@version=6",
                f'indicator("{_escape(title)}", overlay=true)',
                f'entryNote = "{_escape(entry_text)}"',
                f'exitNote = "{_escape(exit_text)}"',
                f'riskNote = "{_escape(risk_text)}"',
                "fast = ta.sma(close, 9)",
                "slow = ta.sma(close, 21)",
                "longSignal = ta.crossover(fast, slow) and barstate.isconfirmed",
                "exitSignal = ta.crossunder(fast, slow) and barstate.isconfirmed",
                "plot(fast, color=color.orange, title=\"Fast SMA\")",
                "plot(slow, color=color.blue, title=\"Slow SMA\")",
                "plotshape(longSignal, title=\"Long signal\", style=shape.triangleup, location=location.belowbar, color=color.green)",
                "plotshape(exitSignal, title=\"Exit signal\", style=shape.triangledown, location=location.abovebar, color=color.red)",
                "",
            ]
        )

    return "\n".join(
        [
            "//@version=6",
            f'strategy("{_escape(title)}", overlay=true, commission_type=strategy.commission.percent, commission_value=0.1, slippage=1, pyramiding=0)',
            f'entryNote = "{_escape(entry_text)}"',
            f'exitNote = "{_escape(exit_text)}"',
            f'riskNote = "{_escape(risk_text)}"',
            "riskPercent = input.float(1.0, \"Risk percent\", minval=0.1, maxval=10.0)",
            "stopLossPct = input.float(2.0, \"Stop loss percent\", minval=0.1)",
            "takeProfitPct = input.float(4.0, \"Take profit percent\", minval=0.1)",
            "fast = ta.sma(close, 9)",
            "slow = ta.sma(close, 21)",
            "longCondition = ta.crossover(fast, slow) and barstate.isconfirmed",
            "flatCondition = ta.crossunder(fast, slow) and barstate.isconfirmed",
            "if longCondition and strategy.position_size == 0",
            "    strategy.entry(\"Long\", strategy.long)",
            "if strategy.position_size > 0",
            "    stopPrice = strategy.position_avg_price * (1 - stopLossPct / 100)",
            "    limitPrice = strategy.position_avg_price * (1 + takeProfitPct / 100)",
            "    strategy.exit(\"Long exit\", \"Long\", stop=stopPrice, limit=limitPrice)",
            "if flatCondition",
            "    strategy.close(\"Long\")",
            "plot(fast, color=color.orange, title=\"Fast SMA\")",
            "plot(slow, color=color.blue, title=\"Slow SMA\")",
            "",
        ]
    )


def validate_pine(code: str, spec: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    warnings: list[str] = []

    checks.append(_check("version_header", code.lstrip().startswith("//@version=6"), "Pine script must start with //@version=6."))

    expected = spec["script_type"]
    has_strategy = bool(re.search(r"\bstrategy\s*\(", code))
    has_indicator = bool(re.search(r"\bindicator\s*\(", code))
    if expected == "strategy":
        checks.append(_check("script_type", has_strategy, "Expected strategy() declaration."))
    elif expected == "indicator":
        checks.append(_check("script_type", has_indicator, "Expected indicator() declaration."))
    else:
        checks.append({"name": "script_type", "status": "manual_required", "details": "Pine validator does not validate MQL5 Expert Advisor scripts."})

    repaint_patterns = {
        "lookahead_on": "Uses lookahead_on, which can repaint.",
        "negative_offset": "Uses a negative offset, which may imply future-looking plots.",
        "unconfirmed_realtime": "Uses realtime barstate logic without a confirmed-bar guard.",
    }
    repaint_findings = []
    if "lookahead_on" in code:
        repaint_findings.append(repaint_patterns["lookahead_on"])
    if re.search(r"offset\s*=\s*-\d+", code):
        repaint_findings.append(repaint_patterns["negative_offset"])
    if "barstate.isrealtime" in code and "barstate.isconfirmed" not in code:
        repaint_findings.append(repaint_patterns["unconfirmed_realtime"])
    if "request.security" in code and "lookahead" not in code:
        warnings.append("request.security appears without an explicit lookahead setting; review manually for repaint behavior.")
    checks.append(_check("repaint_hazards", not repaint_findings, "; ".join(repaint_findings) or "No obvious repaint hazards found."))

    if expected == "strategy":
        risk_fields = [spec.get("position_sizing"), spec.get("stop_loss"), spec.get("take_profit")]
        has_risk_rules = bool(spec.get("risk_rules"))
        has_strategy_exit = "strategy.exit" in code
        checks.append(_check("risk_assumptions", has_risk_rules and any(risk_fields), "Strategy spec should include position sizing, stop loss, or take profit assumptions."))
        if not has_strategy_exit:
            warnings.append("strategy.exit is missing; stop-loss/take-profit behavior may be incomplete.")
    else:
        checks.append({"name": "risk_assumptions", "status": "skipped", "details": "Risk assumptions are only required for Pine strategies."})

    status = "fail" if any(check["status"] == "fail" for check in checks) else "pass"
    if warnings and status == "pass":
        status = "manual_required"

    return {
        "platform": "pine_v6",
        "status": status,
        "checks": checks,
        "evidence": ["static-pine-validator"],
        "warnings": warnings,
        "next_actions": _next_actions(status),
    }


def manual_checklist(spec: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Manual TradingView Checklist",
            "",
            "- Open TradingView Pine Editor.",
            "- Paste `pine/strategy.pine`.",
            "- Confirm the script compiles as Pine Script v6.",
            f"- Confirm script type matches `{spec['script_type']}`.",
            "- Add the script to a chart for the intended symbol/timeframe.",
            "- Review Strategy Tester results without treating them as profit guarantees.",
            "- Check commission, slippage, pyramiding, stop-loss, and take-profit assumptions.",
            "- Record screenshots or exported results before claiming runtime validation.",
            "",
        ]
    )


def _check(name: str, condition: bool, details: str) -> dict[str, str]:
    return {"name": name, "status": "pass" if condition else "fail", "details": details}


def _next_actions(status: str) -> list[str]:
    if status == "pass":
        return ["Run manual TradingView validation before claiming compile or backtest success."]
    if status == "manual_required":
        return ["Review warnings manually in TradingView."]
    return ["Fix failing static validation checks before manual TradingView testing."]


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')

