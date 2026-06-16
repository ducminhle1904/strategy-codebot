# US-001 - Pine Static Validation

## Status

Planned

## Goal

Implement a first-pass Pine Script v6 static validator.

## Acceptance Criteria

- Detects missing `//@version=6`.
- Distinguishes `indicator()` and `strategy()`.
- Flags obvious repaint hazards.
- Flags missing strategy risk assumptions.
- Produces `validation-report.schema.json`.

## Phase

Phase 1

