# 0012 - Phase 2 Parallel Review Runtime

## Status

Accepted

## Context

Phase 1 generates Pine artifacts and deterministic validation reports through a single-agent CLI path. Phase 2 needs multiple agent roles to review the same run without treating model agreement as platform proof.

## Decision

Implement Phase 2 as a review-first runtime:

- Keep generation single-agent.
- Run `trading_analyst`, `pine_specialist`, `risk_reviewer`, and `critic` reviews in parallel.
- Use deterministic dry-run reviewers for default tests and offline usage.
- Use LiteLLM-backed reviewers only in live mode.
- Isolate reviewer failures and report them as partial review results.

## Consequences

The CLI gains useful multi-agent review without requiring provider keys or platform runners. Validation reports remain the deterministic proof source, while review reports capture critique, conflicts, warnings, and next actions.
