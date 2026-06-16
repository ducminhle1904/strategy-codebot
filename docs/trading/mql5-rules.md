# MQL5 Rules

## Target

Generated MQL5 code should target MetaTrader 5 and use `.mq5` source files.

## Program Types

- Expert Advisor.
- Indicator.
- Script.

## Expert Advisor Structure

Expert Advisors should use appropriate lifecycle handlers:

- `OnInit`
- `OnDeinit`
- `OnTick`
- Optional `OnTester` for tester metrics.

## Trade Execution Guardrails

Generated EAs should include explicit assumptions for:

- Symbol and timeframe.
- Magic number.
- Maximum spread.
- Maximum open positions.
- Stop-loss requirement.
- Lot sizing or risk percentage.

## Validation Boundary

Automated MQL5 validation requires a Windows runner with MetaEditor and MetaTrader 5. Phase 0 only defines the expected interface. A later phase should compile with MetaEditor and run Strategy Tester from a config file.

