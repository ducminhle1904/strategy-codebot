# Trace Specification

Agent runs must produce enough evidence for future audit and improvement.

## Required Fields

- `run_id`
- `created_at`
- `agent_role`
- `model`
- `provider`
- `prompt_version`
- `input_refs`
- `retrieved_sources`
- `tool_calls`
- `output_refs`
- `validation_refs`
- `status`
- `warnings`

## Quality Tiers

- Minimal: records agent role, model, input, output, and status.
- Standard: also records retrieved sources, tool calls, and validation refs.
- Detailed: also records critic feedback, retry reasons, cost/latency, and follow-up backlog items.

Phase 1 targets Standard `agent-run.json` traces.

## Runtime Tool Events

Phase 3 adds local runtime traces:

- `runtime-trace.jsonl`: ordered `tool.started`, `tool.completed`, `tool.failed`, and `tool.blocked` events.
- `runtime-summary.json`: event counts, completed tools, failed tools, blocked tools, policy mode, and output refs.
- `review-runtime-trace.jsonl` and `review-runtime-summary.json`: standalone review command traces, kept separate so review does not overwrite the original run trace.

Runtime traces do not replace `agent-run.json`, `validation-report.json`, or `review-report.json`; they explain how those artifacts were produced.

## Knowledge Improvement Evidence

Phase 4 knowledge proposals may reference:

- `knowledge/snapshots/*.json`
- `reports/knowledge-diff.json`
- `reports/knowledge-audit.json`
- `knowledge/proposals/*.json`
- run-level `validation-report.json`, `review-report.json`, `runtime-summary.json`, and runtime JSONL traces

Knowledge proposals are evidence artifacts. They do not prove TradingView or MetaTrader execution, and they do not authorize automatic edits to canonical docs.

## Productization Evidence

Phase 5 release readiness evidence includes:

- `reports/doctor.json`
- `reports/tool-check.json`
- `reports/source-check.json`
- `runs/phase5-smoke/`
- `dist/*.whl`
- `dist/*.tar.gz`

GitHub artifact builds are packaging evidence only. They do not imply live trading support, platform runtime validation, or PyPI publication.
