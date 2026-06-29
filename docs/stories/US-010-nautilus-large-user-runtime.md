# US-010 Nautilus Large-User Runtime

## Story

As a Strategy Codebot operator, I need NautilusTrader support to scale by active account/risk runtime so many users can run many paper/live-ready strategies without creating one container per strategy.

## Acceptance Criteria

- `nautilus_py` artifacts are generated from `StrategySpec` without replacing current Pine/PineForge behavior.
- Unsupported Nautilus V1 strategy features fail closed.
- Runtime grouping uses `RuntimeKey`.
- Multiple strategies with the same runtime key attach to one runtime record.
- Market data fanout shares one upstream collector per venue/symbol/timeframe/data type.
- Paper/runtime events include heartbeat, strategy, signal, order, fill, position, PnL, risk, and error event types.
- Live execution remains disabled by default behind explicit readiness gates.
