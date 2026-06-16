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
