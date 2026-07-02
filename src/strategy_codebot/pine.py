from __future__ import annotations

import re
from typing import Any

from strategy_codebot.reporting import aggregate_status, validation_check


def generate_pine(spec: dict[str, Any]) -> str:
    script_type = spec["script_type"]
    title = f"Strategy Codebot {spec.get('symbol') or spec['market']} {spec['timeframe']}"
    entry_text = _pine_note_text(" | ".join(spec["entry_rules"]))
    exit_text = _pine_note_text(" | ".join(spec["exit_rules"]))
    risk_text = _pine_note_text(" | ".join(spec["risk_rules"]))
    note_lines = [
        f'entryNote = "{_escape(entry_text)}"',
        f'exitNote = "{_escape(exit_text)}"',
        f'riskNote = "{_escape(risk_text)}"',
    ]
    signal_lines = [
        "fast = ta.sma(close, 9)",
        "slow = ta.sma(close, 21)",
    ]
    plot_lines = [
        "plot(fast, color=color.orange, title=\"Fast SMA\")",
        "plot(slow, color=color.blue, title=\"Slow SMA\")",
    ]

    if script_type == "indicator":
        return "\n".join(
            [
                "//@version=6",
                f'indicator("{_escape(title)}", overlay=true)',
                *note_lines,
                *signal_lines,
                "longSignal = ta.crossover(fast, slow) and barstate.isconfirmed",
                "exitSignal = ta.crossunder(fast, slow) and barstate.isconfirmed",
                *plot_lines,
                "plotshape(longSignal, title=\"Long signal\", style=shape.triangleup, location=location.belowbar, color=color.green)",
                "plotshape(exitSignal, title=\"Exit signal\", style=shape.triangledown, location=location.abovebar, color=color.red)",
                "",
            ]
        )

    return "\n".join(
        [
            "//@version=6",
            f'strategy("{_escape(title)}", overlay=true, commission_type=strategy.commission.percent, commission_value=0.1, slippage=1, pyramiding=0)',
            *note_lines,
            "riskPercent = input.float(1.0, \"Risk percent\", minval=0.1, maxval=10.0)",
            "stopLossPct = input.float(2.0, \"Stop loss percent\", minval=0.1)",
            "takeProfitPct = input.float(4.0, \"Take profit percent\", minval=0.1)",
            *signal_lines,
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
            *plot_lines,
            "",
        ]
    )


def validate_pine(code: str, spec: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    warnings: list[str] = []

    checks.append(validation_check("version_header", code.lstrip().startswith("//@version=6"), "Pine script must start with //@version=6."))

    expected = spec["script_type"]
    has_strategy = bool(re.search(r"\bstrategy\s*\(", code))
    has_indicator = bool(re.search(r"\bindicator\s*\(", code))
    if expected == "strategy":
        checks.append(validation_check("script_type", has_strategy, "Expected strategy() declaration."))
    elif expected == "indicator":
        checks.append(validation_check("script_type", has_indicator, "Expected indicator() declaration."))
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
    checks.append(validation_check("repaint_hazards", not repaint_findings, "; ".join(repaint_findings) or "No obvious repaint hazards found."))

    if expected == "strategy":
        risk_fields = [spec.get("position_sizing"), spec.get("stop_loss"), spec.get("take_profit")]
        has_risk_rules = bool(spec.get("risk_rules"))
        has_strategy_exit = "strategy.exit" in code
        checks.append(validation_check("risk_assumptions", has_risk_rules and any(risk_fields), "Strategy spec should include position sizing, stop loss, or take profit assumptions."))
        if not has_strategy_exit:
            warnings.append("strategy.exit is missing; stop-loss/take-profit behavior may be incomplete.")
    else:
        checks.append({"name": "risk_assumptions", "status": "skipped", "details": "Risk assumptions are only required for Pine strategies."})

    status = aggregate_status({check["status"] for check in checks})
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


def validate_pineforge_pine(code: str, spec: dict[str, Any]) -> dict[str, Any]:
    validation = validate_pine(code, spec)
    checks = list(validation["checks"])
    warnings = list(validation["warnings"])
    lowered_executable_code = _strip_pine_strings(_strip_pine_comments(code)).lower()

    blocked_patterns = {
        "alert": r"\balert\s*\(",
        "alertcondition": r"\balertcondition\s*\(",
        "request_seed": r"\brequest\.seed\s*\(",
        "request_security": r"\brequest\.security\s*\(",
    }
    blocked = [name for name, pattern in blocked_patterns.items() if re.search(pattern, lowered_executable_code)]
    checks.append(
        validation_check(
            "pineforge_blocked_constructs",
            not blocked,
            f"Blocked PineForge POC constructs: {', '.join(blocked)}" if blocked else "No blocked offline backtest constructs found.",
        )
    )

    has_strategy_entry = bool(re.search(r"\bstrategy\.entry\s*\(", code))
    has_exit_or_close = bool(re.search(r"\bstrategy\.(?:exit|close)\s*\(", code))
    checks.append(validation_check("pineforge_strategy_entry", has_strategy_entry, "PineForge POC requires at least one strategy.entry call."))
    checks.append(
        validation_check(
            "pineforge_exit_logic",
            has_exit_or_close,
            "PineForge POC requires strategy.exit or explicit strategy.close exit logic.",
        )
    )
    uses_invalid_cash_sizing = _spec_mentions_cash_notional(spec) and _uses_large_fixed_quantity(code)
    checks.append(
        validation_check(
            "pineforge_position_sizing",
            not uses_invalid_cash_sizing,
            (
                "Cash/notional position sizing must not be encoded as strategy.fixed quantity; "
                "use explicit qty = cash_per_trade / close before queueing a local preview."
            )
            if uses_invalid_cash_sizing
            else "No fixed-quantity encoding conflict found for cash/notional sizing.",
        )
    )

    status = aggregate_status({check["status"] for check in checks})
    if warnings and status == "pass":
        status = "manual_required"
    return {
        **validation,
        "status": status,
        "checks": checks,
        "evidence": [*validation["evidence"], "pineforge-poc-guardrail"],
        "warnings": warnings,
        "next_actions": (
            ["Run PineForge local transpile/compile/backtest before treating this as local preview evidence."]
            if status in {"pass", "manual_required"}
            else ["Fix failing PineForge guardrail checks before queueing a PineForge preview."]
        ),
    }


def _strip_pine_comments(code: str) -> str:
    without_block_comments = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    return re.sub(r"//.*", "", without_block_comments)


def _strip_pine_strings(code: str) -> str:
    chars: list[str] = []
    quote: str | None = None
    escaped = False
    for char in code:
        if quote is not None:
            if escaped:
                chars.append(" ")
                escaped = False
                continue
            if char == "\\":
                chars.append(" ")
                escaped = True
                continue
            if char == quote:
                chars.append(char)
                quote = None
                continue
            chars.append("\n" if char == "\n" else " ")
            continue
        if char in {"'", '"'}:
            quote = char
            chars.append(char)
            continue
        chars.append(char)
    return "".join(chars)


def _pine_note_text(value: str) -> str:
    parts = [part.strip() for part in re.split(r"\s*\|\s*", value) if part.strip()]
    filtered = [
        part
        for part in parts
        if not re.search(r"\b(?:broker|live\s+trading|live\s+order|paper\s+trading|paper\s+simulation|runtime)\b", part, flags=re.IGNORECASE)
    ]
    return " | ".join(filtered or parts[:1])


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


def _next_actions(status: str) -> list[str]:
    if status == "pass":
        return ["Run manual TradingView validation before claiming compile or backtest success."]
    if status == "manual_required":
        return ["Review warnings manually in TradingView."]
    return ["Fix failing static validation checks before manual TradingView testing."]


def _spec_mentions_cash_notional(spec: dict[str, Any]) -> bool:
    values: list[str] = []
    for key in ("position_sizing", "risk_rules", "assumptions", "constraints", "user_notes"):
        value = spec.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value)
    text = " ".join(values).lower()
    return bool(re.search(r"(\$|usd|usdt|notional|cash|dollar|fixed\s+\$)", text))


def _uses_large_fixed_quantity(code: str) -> bool:
    strategy_call_match = re.search(r"strategy\s*\((.*?)\)", code, flags=re.IGNORECASE | re.DOTALL)
    strategy_call = strategy_call_match.group(1) if strategy_call_match else code
    if not re.search(r"default_qty_type\s*=\s*strategy\.fixed", strategy_call, flags=re.IGNORECASE):
        return False
    value_match = re.search(r"default_qty_value\s*=\s*([0-9]+(?:\.[0-9]+)?)", strategy_call, flags=re.IGNORECASE)
    if value_match is None:
        return False
    return float(value_match.group(1)) >= 10


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
