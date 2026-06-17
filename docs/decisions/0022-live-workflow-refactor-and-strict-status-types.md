# 0022 Live Workflow Refactor and Strict Status Types

## Status

Accepted

## Context

The live workflow evolved from single-model smoke testing into a multi-stage agent pipeline. The orchestration needed a smaller internal API surface, eval runs needed bounded parallelism, and harness events needed a stable status and failure taxonomy for reliable gates and downstream observability.

## Decision

- `run_strategy` no longer accepts legacy live kwargs such as `workflow`, `model_override`, `cost_profile`, or `save_raw_provider`; callers pass `LiveRunOptions` instead.
- Live stage execution uses a `StageRunContext` object to carry registry, LiteLLM access, attempts, stage records, raw responses, options, and policy through the workflow.
- `strategy-codebot eval live` runs cases with bounded concurrency, defaulting to `2`, while preserving suite order in reports.
- Runtime, lifecycle, and eval producer code uses shared status and failure-class constants. `schemas/tool-event.schema.json` mirrors those values as enums.

## Consequences

The public CLI flags stay stable, but the Python runner API is intentionally cleaner. Live eval users can lower `--concurrency` to `1` for sensitive provider keys. Strict event enums make invalid telemetry fail early instead of leaking inconsistent status or failure strings into artifacts.
