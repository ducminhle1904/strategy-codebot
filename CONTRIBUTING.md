# Contributing

`strategy-codebot` is a CLI-first project. Keep changes small, tested, and aligned with the current phase boundaries.

## Local Setup

```bash
uv sync
uv run pytest
uv run strategy-codebot doctor
```

## Development Rules

- Do not add live trading, broker integration, profitability claims, Pine runtime validation, or MT5 compile/test automation without an accepted decision record.
- Keep generated artifacts under ignored directories such as `runs/`, `reports/`, `dist/`, and `knowledge/proposals/`.
- Update docs, tests, and harness records when changing public CLI behavior.

## Pull Request Checklist

- `uv run pytest`
- `uv run python -m compileall src tests`
- `uv run strategy-codebot doctor`
- `uv build --out-dir dist`
