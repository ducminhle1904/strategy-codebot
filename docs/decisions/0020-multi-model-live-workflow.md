# 0020 - Multi-Model Live Workflow

## Status

Accepted

## Context

Single-model live generation only proves that one provider can return a valid final payload. Production-readiness needs explicit stage ownership, context handoff, review, repair, and traceable model usage.

## Decision

Make `run --mode live` use a multi-agent workflow by default. The workflow stages are `strategy_reasoning`, `strategy_coding`, `pine_code_generation`, `balanced_review`, and `repair`.

Each stage receives a structured context packet and returns structured JSON with assumptions, handoff notes, and policy observations. The workflow records `live-workflow-trace.json` plus stage metadata in `live-metadata.json`.

`--workflow single` remains available for debug runs. `--model` is limited to single workflow. Multi-agent model overrides use `--model-stage stage=model`. The `cheap` cost profile uses OpenRouter mappings only.

## Consequences

Live artifacts are produced by a coordinated model pipeline instead of a single completion. The final spec/code must pass schema validation, static Pine validation, safety policy checks, and balanced review after at most two repair loops.
