# strategy-codebot

Harness-first AI agent scaffold for generating and reviewing trading strategy code for Pine Script v6 and MQL5.

This repository is currently in **Phase 3: Tool/Runtime Harness**. It contains a CLI MVP for deterministic Pine generation, static validation, knowledge-source checks, parallel review reports, and local runtime tool traces. It does not yet include Pine compiler automation, MQL5 compile/test automation, broker integration, or live-trading automation.

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

Dry-run mode does not require API keys. Live mode uses LiteLLM-compatible provider configuration from `configs/model-registry.example.yaml`, reads provider credentials from the environment, and should be run with the live extra:

```bash
uv run --extra live strategy-codebot run --prompt "Create a Pine v6 moving average crossover strategy" --mode live --out runs/live-example
```

When `scripts/bin/harness-cli` exists, `strategy-codebot run` records a local repository-harness trace by default. Use `--no-record-harness` for tests and disposable local runs.

## Phase 2 Parallel Review

Review an existing run with deterministic offline reviewers:

```bash
uv run strategy-codebot review --run-dir runs/example --mode dry-run --out runs/example/review-report.json --no-record-harness
```

Or create the review report during a run:

```bash
uv run strategy-codebot run --spec examples/specs/ma-crossover-pine.json --mode dry-run --out runs/phase2-example --review parallel --no-record-harness
```

Phase 2 reviewers run in parallel and write `review-report.json`. The report is critique evidence only; `validation-report.json` remains the deterministic validation artifact, and manual TradingView/MT5 proof is still required before claiming platform execution.

## Phase 3 Tool/Runtime Harness

Check the machine-readable tool registry:

```bash
uv run strategy-codebot tools list
uv run strategy-codebot tools check --out reports/tool-check.json
```

Run with runtime trace artifacts:

```bash
uv run strategy-codebot run --spec examples/specs/ma-crossover-pine.json --mode dry-run --out runs/phase3-example --review parallel --runtime-trace --policy observe --no-record-harness
```

Phase 3 writes `runtime-trace.jsonl` and `runtime-summary.json` by default for `run`. Standalone `review` writes `review-runtime-trace.jsonl` and `review-runtime-summary.json` so it does not overwrite the original run trace. Runtime traces explain ordered tool calls; repository-level planning and durable evidence remain in `repository-harness`.

## Non-Goals

- No live trading.
- No broker account integration.
- No profitability claims.
- No generated strategy execution.
- No TradingView or MetaTrader runtime validation yet.
