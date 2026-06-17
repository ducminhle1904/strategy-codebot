# Agent Instructions

This repo uses a repository harness. Keep this file short: it is a map, not the source of truth.

## Read First

1. [docs/product/strategy-codebot.md](docs/product/strategy-codebot.md) for product intent and boundaries.
2. [docs/HARNESS.md](docs/HARNESS.md) for the collaboration model.
3. [docs/FEATURE_INTAKE.md](docs/FEATURE_INTAKE.md) before classifying work.
4. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before changing design or implementation boundaries.
5. [docs/TEST_MATRIX.md](docs/TEST_MATRIX.md) before claiming completion.

## Stable Rules

- Phase 0 is docs, schemas, configs, and planning records only.
- Do not add live-trading automation unless a future decision explicitly approves it.
- Pine Script validation is static/manual-proof first because TradingView is the authoritative runtime.
- MQL5 validation will require a Windows/MetaTrader runner in a later phase.
- Record durable decisions in `docs/decisions/`.
- Record planned work in `docs/stories/`.
- Before non-trivial scout, investigation, implementation, or review work, run or account for `strategy-codebot harness agent-start --summary ...`; read only its bounded `context_brief`, not raw trace rows or full reports.
- If `agent-start` is unavailable, run both `strategy-codebot harness preflight --latest 10` and `strategy-codebot harness session-start --summary ...`, then record `--preflight-applied` in `dev-trace`.
- For non-trivial research, investigation, implementation, verification, or blocker sessions, record a repository trace with `strategy-codebot harness dev-trace` before the final response.
- After non-trivial trace-related work, run `strategy-codebot harness audit-traces --latest 1`.
