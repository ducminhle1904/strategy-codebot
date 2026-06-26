from __future__ import annotations

from strategy_codebot.quality import assess_strategy_quality, production_gate_with_quality


def _safe_strategy() -> dict:
    return {
        "target_platform": "pine_v6",
        "script_type": "strategy",
        "market": "crypto",
        "timeframe": "1h",
        "entry_rules": ["Enter long when fast EMA crosses above slow EMA and bar is confirmed."],
        "exit_rules": ["Exit with strategy.exit using stop loss and take profit levels."],
        "risk_rules": ["Risk 1% account equity per trade."],
        "position_sizing": "1% account equity risk per trade",
        "stop_loss": "2 ATR stop",
        "take_profit": "2R target",
    }


def test_quality_blocks_full_capital_sizing() -> None:
    spec = _safe_strategy()
    spec["position_sizing"] = "Use 100% of available capital"

    report = assess_strategy_quality(spec, '//@version=6\nstrategy("x")\nstrategy.entry("L", strategy.long)\nstrategy.exit("X", "L")')

    assert report["status"] == "fail"
    assert report["blockers"][0]["category"] == "position_sizing"


def test_quality_blocks_missing_strategy_exit() -> None:
    report = assess_strategy_quality(_safe_strategy(), '//@version=6\nstrategy("x")\nstrategy.entry("L", strategy.long)')

    assert report["status"] == "fail"
    assert any(finding["category"] == "exit_logic" for finding in report["blockers"])


def test_quality_allows_conservative_risk_and_indicator_output() -> None:
    spec = {
        "target_platform": "pine_v6",
        "script_type": "indicator",
        "market": "forex",
        "timeframe": "1d",
        "entry_rules": ["Mark pullback entries after RSI recovers from oversold levels."],
        "exit_rules": ["Show visual exit marker when RSI becomes overbought."],
        "risk_rules": ["Manual validation required; no autonomous orders are placed."],
        "position_sizing": "Not applicable for indicator-only script.",
    }

    report = assess_strategy_quality(spec, '//@version=6\nindicator("x")\nplotshape(close > open)')

    assert report["status"] == "pass"
    assert report["blockers"] == []


def test_quality_marks_basic_valid_strategy_as_weak_sophistication_warn_only() -> None:
    report = assess_strategy_quality(_safe_strategy(), '//@version=6\nstrategy("x")\nstrategy.entry("L", strategy.long)\nstrategy.exit("X", "L")')

    assert report["status"] == "pass"
    assert report["safety_quality"]["status"] == "pass"
    assert report["strategy_sophistication"]["warn_only"] is True
    assert report["sophistication_grade"] == "weak"
    assert "market_premise" in report["missing_trader_assumptions"]
    assert "risk_concentration" in report["missing_trader_assumptions"]
    assert report["improvement_hints"]


def test_quality_requires_exposure_context_for_risk_concentration() -> None:
    spec = _safe_strategy() | {
        "entry_rules": [
            "Premise: liquidity sweep edge in a range-to-trend regime during London session liquidity.",
            "Enter after confirmed reclaim and retest rejection; avoid chasing failed reclaim fakeouts.",
        ],
        "exit_rules": ["Stop beyond swept wick invalidation and target prior structure high or 1.5R bounded fallback."],
        "risk_rules": ["Risk 1% account equity per trade, include fees/slippage assumptions, and avoid overfit with backtest sample-size and out-of-sample validation."],
        "constraints": ["Price action only with no indicators; use OHLC structure and confirmed candles."],
        "stop_loss": "Stop beyond swept wick invalidation.",
        "take_profit": "Structure target or 1.5R risk-reward fallback.",
    }

    report = assess_strategy_quality(
        spec,
        '//@version=6\nstrategy("x")\nstrategy.entry("L", strategy.long)\nstrategy.exit("X", "L")',
    )

    assert report["strategy_sophistication"]["checks"]["risk_concentration"] is False
    assert "risk_concentration" in report["missing_trader_assumptions"]


def test_quality_scores_trader_grade_price_action_higher() -> None:
    spec = _safe_strategy() | {
        "entry_rules": [
            "Premise: liquidity sweep edge in a range-to-trend regime. Enter only during London or New York liquidity when the 1h market regime is range-to-trend and price sweeps prior structure low then reclaims above the level on a confirmed close.",
            "Avoid chasing a failed reclaim or false break; wait for retest rejection before entry.",
        ],
        "exit_rules": ["Exit with strategy.exit using stop beyond the swept wick invalidation and target prior structure high or 1.5R bounded fallback."],
        "risk_rules": ["Risk 1% account equity per trade, cap exposure and portfolio heat, include fees/slippage assumptions, and avoid overfit by using few inputs with backtest sample-size and out-of-sample validation."],
        "constraints": ["Price action only with no indicators; use OHLC structure, sweep, reclaim, BOS/retest, and confirmed candles."],
        "stop_loss": "Stop beyond swept wick invalidation.",
        "take_profit": "Structure target or 1.5R risk-reward fallback.",
    }

    report = assess_strategy_quality(
        spec,
        '//@version=6\nstrategy("x")\nstrategy.entry("L", strategy.long)\nstrategy.exit("X", "L")',
    )

    assert report["status"] == "pass"
    assert report["sophistication_grade"] == "strong"
    assert report["sophistication_score"] > 85
    assert "market_premise" not in report["missing_trader_assumptions"]
    assert report["strategy_sophistication"]["checks"]["edge_plausibility"] is True
    assert report["strategy_sophistication"]["checks"]["execution_realism"] is True
    assert "edge-strategy-reviewer" in report["strategy_sophistication"]["rubric_sources"]


def test_production_gate_soft_blocks_weak_sophistication() -> None:
    quality = assess_strategy_quality(_safe_strategy(), '//@version=6\nstrategy("x")\nstrategy.entry("L", strategy.long)\nstrategy.exit("X", "L")')
    gate = production_gate_with_quality({"status": "pass"}, quality)

    assert gate["status"] == "fail"
    assert gate["quality_gate_mode"] == "soft"
    assert gate["sophistication_grade"] == "weak"
    assert gate["sophistication_warn_only"] is False
    assert any(item.startswith("sophistication:") for item in gate["sophistication_blockers"])


def test_production_gate_allows_strong_sophistication() -> None:
    spec = _safe_strategy() | {
        "entry_rules": [
            "Premise: liquidity sweep edge in a range-to-trend regime. Enter during London liquidity when price sweeps prior low, reclaims, and retests with confirmed rejection.",
            "Avoid chasing failed reclaim fakeouts.",
        ],
        "exit_rules": ["Stop beyond swept wick invalidation and target prior structure high or 1.5R bounded fallback."],
        "risk_rules": ["Risk 1% account equity, cap exposure, include fees/slippage, and avoid overfit with few bounded inputs, backtest sample-size review, and out-of-sample validation."],
        "stop_loss": "Stop beyond swept wick invalidation.",
        "take_profit": "Structure target or 1.5R risk-reward fallback.",
    }
    quality = assess_strategy_quality(spec, '//@version=6\nstrategy("x")\nstrategy.entry("L", strategy.long)\nstrategy.exit("X", "L")')
    gate = production_gate_with_quality({"status": "pass"}, quality)

    assert gate["status"] == "pass"
    assert gate["quality_gate_mode"] == "soft"
    assert gate["sophistication_grade"] == "strong"
    assert gate["sophistication_blockers"] == []
