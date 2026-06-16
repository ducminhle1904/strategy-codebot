# strategy-codebot

Harness-first AI agent scaffold for generating and reviewing trading strategy code for Pine Script v6 and MQL5.

This repository is currently in **Phase 0: Design Scaffold**. It contains contracts, schemas, operating docs, and planning records. It does not yet include a runnable agent runtime, Pine compiler automation, MQL5 runner, broker integration, or live-trading automation.

## Start Here

- Agent entrypoint: [AGENTS.md](AGENTS.md)
- Harness model: [docs/HARNESS.md](docs/HARNESS.md)
- Product contract: [docs/product/strategy-codebot.md](docs/product/strategy-codebot.md)
- Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Validation expectations: [docs/TEST_MATRIX.md](docs/TEST_MATRIX.md)
- Strategy request schema: [schemas/strategy-spec.schema.json](schemas/strategy-spec.schema.json)

## Phase 0 Scope

Phase 0 creates the design contracts for future implementation:

- Repository harness layout and operating rules.
- Trading-specific docs for Pine Script v6 and MQL5.
- Agent role definitions for a LangGraph + LiteLLM orchestration runtime.
- Minimal JSON schemas for strategy specs, agent runs, and validation reports.
- Model and source registry examples.
- Initial durable decisions and Phase 1 stories.

## Phase 1 CLI MVP

Install and run with `uv`:

```bash
uv run strategy-codebot run --spec examples/specs/ma-crossover-pine.json --mode dry-run --out runs/example --no-record-harness
uv run strategy-codebot validate-pine --file runs/example/pine/strategy.pine --spec examples/specs/ma-crossover-pine.json --out reports/pine-report.json
uv run strategy-codebot knowledge check --offline --out reports/source-check.json
```

Dry-run mode does not require API keys. Live mode uses LiteLLM-compatible provider configuration from `configs/model-registry.example.yaml` and reads provider credentials from the environment.

When `scripts/bin/harness-cli` exists, `strategy-codebot run` records a local repository-harness trace by default. Use `--no-record-harness` for tests and disposable local runs.

## Non-Goals

- No live trading.
- No broker account integration.
- No profitability claims.
- No generated strategy execution.
- No Pine or MQL5 runtime validation yet.
