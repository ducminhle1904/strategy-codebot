# Improvement Protocol

The knowledge base and harness should improve from observed failures.

## Triggers

- Repeated strategy-generation mistakes.
- Repeated validation failures.
- Official docs changed.
- Critic agent flags missing policy.
- Human review finds unclear or stale docs.

## Phase 4 Flow

1. Create a source snapshot from `configs/source-registry.yaml`.
2. Diff the snapshot against a prior baseline.
3. Audit validation, review, and runtime artifacts from one or more runs.
4. Generate a knowledge proposal with affected sources, affected docs, evidence refs, risk level, recommendations, and next actions.
5. Keep the proposal as review evidence until a human accepts or rejects it.

Phase 4 does not mutate canonical docs such as `docs/trading/*.md`. Promotion into docs requires a separate human-approved change.

## Proposal Lifecycle

- `draft`: no actionable source or run evidence yet.
- `needs_review`: source diff or run audit evidence may require a docs, schema, prompt, validator, or registry update.
- `accepted`: a human reviewer approves the proposal for a concrete follow-up change.
- `rejected`: a human reviewer decides the proposal should not be promoted.

Official source changes are always manual-review evidence. They must not be auto-accepted or converted into trading guidance without review.
