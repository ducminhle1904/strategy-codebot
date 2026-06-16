# US-002 - MQL5 Compile And Test Runner Design

## Status

Planned

## Goal

Design the Windows runner interface for compiling MQL5 and running MetaTrader 5 Strategy Tester.

## Acceptance Criteria

- Defines expected MetaEditor compile command inputs.
- Defines expected Strategy Tester config inputs.
- Defines normalized report fields.
- Documents missing environment behavior.

## Verify Command

```bash
uv run pytest tests/test_runner.py
```

## Phase

Phase 1
