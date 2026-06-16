from pathlib import Path

from strategy_codebot.pine import generate_pine, validate_pine
from strategy_codebot.schemas import load_strategy_spec


def test_generated_pine_strategy_passes_static_validation() -> None:
    spec = load_strategy_spec(Path("examples/specs/ma-crossover-pine.json"))
    code = generate_pine(spec)
    report = validate_pine(code, spec)

    assert code.startswith("//@version=6")
    assert "strategy.exit" in code
    assert report["status"] == "pass"


def test_validator_flags_missing_version_and_wrong_type() -> None:
    spec = {
        "target_platform": "pine_v6",
        "script_type": "strategy",
        "market": "crypto",
        "timeframe": "1h",
        "entry_rules": ["entry"],
        "exit_rules": ["exit"],
        "risk_rules": ["risk"],
        "position_sizing": "1%",
    }
    report = validate_pine('indicator("Bad", overlay=true)', spec)

    assert report["status"] == "fail"
    assert {check["name"] for check in report["checks"] if check["status"] == "fail"} >= {"version_header", "script_type"}


def test_validator_flags_repaint_hazards_and_missing_risk_controls() -> None:
    spec = {
        "target_platform": "pine_v6",
        "script_type": "strategy",
        "market": "crypto",
        "timeframe": "1h",
        "entry_rules": ["entry"],
        "exit_rules": ["exit"],
        "risk_rules": ["risk"],
    }
    code = "\n".join(
        [
            "//@version=6",
            'strategy("Bad", overlay=true)',
            "x = request.security(syminfo.tickerid, \"D\", close, lookahead=barmerge.lookahead_on)",
            "plot(x, offset=-1)",
        ]
    )
    report = validate_pine(code, spec)

    assert report["status"] == "fail"
    failing = {check["name"] for check in report["checks"] if check["status"] == "fail"}
    assert "repaint_hazards" in failing
    assert "risk_assumptions" in failing
