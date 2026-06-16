# Feature Intake

Use this file to classify work before implementation.

## Input Types

- `new_spec`: new product or architecture contract.
- `spec_slice`: narrower piece of an accepted spec.
- `change_request`: change to an accepted contract.
- `maintenance`: docs, schema, or housekeeping.
- `harness_improvement`: better tracing, validation, or knowledge flow.

## Lanes

### Tiny

Small docs/config edits with no behavior impact.

Required proof:

- Local link/schema parse check if relevant.
- One-line summary.

### Normal

Design or implementation work touching one subsystem.

Required proof:

- Updated docs or schemas.
- Focused validation.
- Story or decision update when needed.

### High-Risk

Work touching live trading, broker integration, automated execution, model routing policy, or validation semantics.

Required proof:

- Explicit story.
- Decision record.
- Risk review.
- Test matrix update.

## Phase 0 Default

Phase 0 scaffold work is `normal` unless it changes safety boundaries, live-trading policy, or provider strategy.

