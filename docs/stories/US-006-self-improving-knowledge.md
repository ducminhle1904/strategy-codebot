# US-006 - Self-Improving Knowledge

## Status

Planned

## Goal

Create a local knowledge loop that snapshots trusted sources, diffs source changes, audits run evidence, and produces proposal artifacts for human review.

## Acceptance Criteria

- Supports offline-safe `knowledge snapshot`.
- Supports `knowledge diff` for changed, added, removed, and unchanged source entries.
- Supports `knowledge audit` for validation, review, runtime-summary, and runtime-trace evidence.
- Supports `knowledge propose` using diff plus audit evidence.
- Registers Phase 4 knowledge tools in `configs/tool-registry.yaml`.
- Does not edit canonical docs from knowledge commands.

## Verify Command

```bash
uv run pytest tests/test_knowledge_loop.py tests/test_cli.py tests/test_tool_runtime.py
```

## Phase

Phase 4
