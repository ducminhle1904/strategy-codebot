# 0003 - Pine And MQL5 Platform Boundaries

## Status

Accepted

## Context

Pine Script and MQL5 have different authoritative runtimes and validation surfaces.

## Decision

Scaffold both Pine Script v6 and MQL5 from Phase 0, but keep validation assumptions platform-specific.

## Consequences

- Pine validation starts with static checks and manual TradingView proof.
- MQL5 validation expects a future Windows runner with MetaEditor and MetaTrader 5.
- Validation reports must state `manual_required` instead of inventing proof.

