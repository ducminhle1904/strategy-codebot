# 0026 PineForge Preview Worker Boundary

## Status

Accepted.

## Decision

Strategy Codebot will integrate PineForge through a separate Node/TypeScript worker service and dedicated `pineforge-runner` instead of running PineForge inside the Python API process.

The Python/FastAPI service remains the owner of conversations, auth, run records, policy gates, rate limits, idempotency, artifact metadata, and SSE replay. `POST /v1/runs` may create `backtest-preview` runs, but those runs are queued in Postgres as `run_jobs` and processed by a horizontally scalable worker. The initial market data policy is public read-only data with cache metadata.

PineForge reports are labeled as PineForge local Pine preview evidence. They are not TradingView proof, MQL5 proof, live-trading evidence, broker execution evidence, or profitability claims.

## Consequences

- `backtest-preview` is a queued run mode.
- The queue contract is a generic `run_jobs` table with Postgres leasing and worker-owned completion.
- The worker allowlist is PineForge runner execution and read-only data adapters.
- `Live.background()`, broker credentials, paper trading, live trading, Telegram alerts, and Docker live mode remain blocked.
- Backtest artifacts use normal artifact APIs and must not expose filesystem paths.
