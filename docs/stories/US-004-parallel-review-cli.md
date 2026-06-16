# US-004 - Parallel Review CLI

## Status

Planned

## Goal

Review an existing strategy-codebot run with multiple specialist roles in parallel and write a normalized review report.

## Acceptance Criteria

- Supports `strategy-codebot review --run-dir <path> --mode dry-run --out <path>`.
- Supports `strategy-codebot run ... --review parallel`.
- Produces `review-report.json` conforming to `schemas/review-report.schema.json`.
- Includes `trading_analyst`, `pine_specialist`, `risk_reviewer`, and `critic` reviewer results.
- Preserves Phase 1 behavior when `--review none` is used or omitted.
- Does not claim TradingView, Pine Strategy Tester, MQL5 compile, MT5 Strategy Tester, profit, or live-trading proof.

## Verify Command

```bash
uv run pytest tests/test_review.py tests/test_cli.py tests/test_runner.py
```

## Phase

Phase 2
