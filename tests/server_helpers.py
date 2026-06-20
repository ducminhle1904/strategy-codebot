import json


def valid_spec() -> dict:
    return {
        "target_platform": "pine_v6",
        "script_type": "strategy",
        "market": "crypto",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "entry_rules": ["Enter long when fast EMA crosses above slow EMA and bar is confirmed."],
        "exit_rules": ["Exit with strategy.exit using stop loss and take profit levels."],
        "risk_rules": ["Risk 1% account equity per trade and avoid live order placement."],
        "position_sizing": "1% account equity risk per trade",
        "stop_loss": "2% below average entry price",
        "take_profit": "4% above average entry price",
    }


def parse_sse(body: str) -> list[dict]:
    frames = []
    for block in body.strip().split("\n\n"):
        if not block:
            continue
        parsed: dict[str, object] = {}
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("id: "):
                parsed["id"] = line.removeprefix("id: ")
            elif line.startswith("event: "):
                parsed["event"] = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data_lines.append(line.removeprefix("data: "))
        parsed["data"] = json.loads("\n".join(data_lines))
        frames.append(parsed)
    return frames
