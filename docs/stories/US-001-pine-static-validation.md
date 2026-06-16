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

## Verify Command

```bash
uv run pytest tests/test_pine.py tests/test_cli.py
```

## Phase

Phase 1
