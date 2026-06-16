# US-007 - CLI Productization

## Status

Planned

## Goal

Make `strategy-codebot` installable, checkable, buildable, and understandable as a CLI product.

## Acceptance Criteria

- Provides MIT license and product metadata.
- Documents source install, doctor, smoke run, knowledge loop, and package build.
- Supports `strategy-codebot version`.
- Supports `strategy-codebot doctor` with optional JSON output.
- Adds GitHub Actions CI and release artifact workflows.
- Builds wheel and source distribution locally with `uv build --out-dir dist`.
- Does not add API, web UI, PyPI publish, live trading, broker integration, Pine runtime validation, or MT5 compile/test automation.

## Verify Command

```bash
uv run pytest
uv run python -m compileall src tests
uv run strategy-codebot doctor --out reports/doctor.json
uv build --out-dir dist
```

## Phase

Phase 5
