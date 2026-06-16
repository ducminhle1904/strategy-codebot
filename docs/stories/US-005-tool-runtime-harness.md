# US-005 - Tool Runtime Harness

## Status

Planned

## Goal

Record strategy-codebot runtime work as ordered tool events with machine-readable contracts, policy mode, and local trace artifacts.

## Acceptance Criteria

- Provides `configs/tool-registry.yaml`.
- Provides schema validation for tool contracts, tool events, and runtime summaries.
- Supports `strategy-codebot tools list` and `strategy-codebot tools check`.
- Writes `runtime-trace.jsonl` and `runtime-summary.json` for `run` and `review` by default.
- Supports `--no-runtime-trace` to preserve Phase 2 artifact shape.
- Supports `--policy observe|enforce`, defaulting to `observe`.
- Does not add Pine runtime validation, MT5 compile/test automation, broker integration, or live trading.

## Verify Command

```bash
uv run pytest tests/test_tool_runtime.py tests/test_cli.py tests/test_runner.py
```

## Phase

Phase 3
