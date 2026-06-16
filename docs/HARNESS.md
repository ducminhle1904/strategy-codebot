# Harness

The harness makes repository knowledge, workflow state, validation evidence, and decisions durable for humans and agents.

## Mental Model

`strategy-codebot` should not rely on chat history to understand what to build. The repository should answer:

- What product is this?
- What target platforms are supported?
- Which agent roles exist?
- Which schemas define the handoff contracts?
- What validation proof is required?
- Which decisions are already locked?

## Phase 0 Scope

Phase 0 creates design artifacts only:

- Harness docs.
- Trading platform rules.
- Agent role specs.
- JSON schemas.
- Model/source registries.
- Initial stories and decisions.

Phase 0 does not implement the runtime, orchestration graph, code generators, validators, or ingestion jobs.

## Task Loop

1. Classify the request with [FEATURE_INTAKE.md](FEATURE_INTAKE.md).
2. Read product and architecture docs.
3. Update or add a story if the work changes the roadmap.
4. Implement only within the current phase boundary.
5. Validate against [TEST_MATRIX.md](TEST_MATRIX.md).
6. Record durable decisions when behavior or architecture changes.

## Done Definition

A task is done only when:

- Relevant docs or schemas are updated.
- Local validation evidence exists.
- The test matrix row is satisfied.
- Any new durable tradeoff is recorded in `docs/decisions/`.

