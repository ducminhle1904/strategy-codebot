# 0021 Agent Harness Observability Readiness

## Status

Accepted

## Context

Live multi-agent generation now produces strategy artifacts, metadata, and workflow traces, but production debugging needs clearer agent lifecycle events, failure attribution, and vendor-neutral telemetry export. External observability platforms are useful, but the project should keep local artifacts as the source of truth.

## Decision

Add an additive agent harness layer on top of existing runtime artifacts:

- Extend runtime trace events with agent lifecycle, LLM, guardrail, repair, and completion event types.
- Add `strategy-codebot harness inspect` to summarize run artifacts, stage timeline, model/provider usage, policy findings, missing artifacts, and failure attribution.
- Add local `--otel-export` JSONL output for `run` and `eval live` using OpenTelemetry/GenAI-inspired fields without sending network telemetry.
- Keep multi-agent orchestration internal for now; no external sub-agent runtime is introduced.

## Consequences

The CLI can debug agent runs without a vendor dependency while preserving compatibility with current `agent-run.json`, `runtime-trace.jsonl`, `runtime-summary.json`, `live-metadata.json`, and `live-workflow-trace.json` artifacts. Future vendor integrations can consume the local export instead of changing generation flow.
