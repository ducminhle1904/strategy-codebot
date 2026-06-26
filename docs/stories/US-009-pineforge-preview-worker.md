# US-009 - PineForge Preview Worker

## Status

Planned

## Goal

Allow chat workflows to create queued PineForge local preview runs that produce reviewable backtest artifacts without adding live trading, broker execution, or platform validation claims.

## Acceptance Criteria

- `POST /v1/runs` supports `mode: "backtest-preview"` with `backtest_config`.
- Backtest preview requests return quickly with run status `queued`.
- Backtest work is persisted as a `run_jobs` row and can be leased by a worker.
- Run events include typed Backtest lifecycle events.
- Backtest artifacts are categorized as user-visible report/evidence/code-adjacent artifacts.
- `/ready` exposes queue depth and oldest queued age for backtest jobs.
- The worker boundary blocks broker, paper/live, Telegram, webhook alert automation, and Docker live surfaces.

## Phase

Phase 6 - PineForge preview foundation
