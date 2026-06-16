# Pine Script v6 Rules

## Target

Generated Pine code should default to `//@version=6`.

## Script Types

- `indicator`: visual signal or analysis script.
- `strategy`: TradingView Strategy Tester script with entries, exits, and risk assumptions.

## Required Strategy Assumptions

For `strategy` scripts, generated code should state or encode:

- Commission/slippage assumption.
- Position sizing assumption.
- Stop-loss and take-profit behavior when requested.
- Pyramiding behavior.
- Timeframe and symbol assumptions.

## No-Repaint Checklist

Review code for:

- `request.security` lookahead behavior.
- Higher-timeframe confirmation assumptions.
- Use of unconfirmed realtime bars.
- Future-looking offsets.
- Signals that change after bar close.

## Validation Boundary

TradingView is the authoritative Pine runtime. Phase 1 should start with static validation and a manual TradingView checklist. Do not claim that Pine code compiled or backtested unless there is evidence from TradingView.

