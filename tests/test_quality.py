from __future__ import annotations

from strategy_codebot.quality import assess_strategy_quality


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
