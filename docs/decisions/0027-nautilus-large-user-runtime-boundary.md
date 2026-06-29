# 0027 Nautilus Large-User Runtime Boundary

## Status

Accepted

## Decision

Strategy Codebot will treat NautilusTrader as an account-level paper/live runtime engine, not as the SaaS orchestrator and not as a replacement for the current Pine/PineForge backtest preview path.

Runtime scale is keyed by `runtime_key = user_id + broker_connection_id + account_id + mode + risk_policy_id`. Multiple strategies with the same runtime key may run in one Nautilus runtime so they share account portfolio, risk, and execution state. Different users, broker credentials, accounts, modes, or risk policies require separate runtime boundaries.

Market data collection is a separate shared plane. Nautilus runtimes must subscribe to normalized internal bars/ticks instead of each opening duplicate exchange websocket feeds for the same venue/symbol/timeframe.

Live broker execution remains disabled by default. Nautilus artifacts may prove codegen, local/sim compatibility, paper runtime behavior, and readiness gates, but they are not live-trading evidence until a future decision explicitly approves broker credentials and live execution.

## Consequences

- `nautilus_py` is a native generation/runtime target behind parity and safety gates.
- `StrategySpec` remains the source of truth for Pine and Nautilus outputs.
- Nautilus runtime artifacts are deterministic files under run artifacts.
- Runtime orchestration scales by active account/risk boundary, not strategy count.
- Paper runtimes may be implemented before live runtimes.
- Live readiness can be assessed without placing live orders.
