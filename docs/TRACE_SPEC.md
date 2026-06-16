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

Phase 1 should target Standard traces.

