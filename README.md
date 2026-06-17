# strategy-codebot

Harness-first AI agent scaffold for generating and reviewing trading strategy code for Pine Script v6 and MQL5.

This repository is currently in **Phase 5: Productization**. It contains a CLI product for deterministic Pine generation, static validation, knowledge-source checks, parallel review reports, local runtime tool traces, proposal-first knowledge improvement artifacts, doctor checks, and GitHub artifact builds. It does not include Pine compiler automation, MQL5 compile/test automation, broker integration, live-trading automation, or automatic promotion of knowledge proposals into canonical docs.

## Start Here

- Agent entrypoint: [AGENTS.md](AGENTS.md)
- Harness model: [docs/HARNESS.md](docs/HARNESS.md)
- Product contract: [docs/product/strategy-codebot.md](docs/product/strategy-codebot.md)
- Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Validation expectations: [docs/TEST_MATRIX.md](docs/TEST_MATRIX.md)
- Strategy request schema: [schemas/strategy-spec.schema.json](schemas/strategy-spec.schema.json)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
- Security policy: [SECURITY.md](SECURITY.md)

## Install From Source

```bash
git clone https://github.com/ducminhle1904/strategy-codebot.git
cd strategy-codebot
uv sync
uv run strategy-codebot version
uv run strategy-codebot doctor
```

Build local release artifacts:

```bash
uv build --out-dir dist
```

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

Dry-run mode does not require API keys. Live mode uses a multi-model LiteLLM workflow by default, reads provider credentials from the environment, requests strict JSON Schema output at each stage, and should be run with the live extra:

```bash
export OPENAI_API_KEY=...
uv run --extra live strategy-codebot run --prompt "Create a Pine v6 moving average crossover strategy" --mode live --out runs/live-example --policy enforce --save-raw-provider
uv run --extra live strategy-codebot eval live --suite examples/evals/live-core.yaml --out runs/evals/live-openai --policy enforce --save-raw-provider
```

The live workflow stages are `strategy_reasoning`, `strategy_coding`, `pine_code_generation`, `balanced_review`, and `repair`. Use `--model-stage stage=model` to override one stage. Use `--workflow single --model ...` only for single-model debugging. Live runs inject curated local trading/Pine/risk knowledge by default and write `knowledge-context.json`; use `--knowledge-context off` for prompt-only debugging. Live eval runs default to `--concurrency 2` and are capped at `8`; lower this to `--concurrency 1` when testing rate-limit-sensitive provider keys. Each live eval case has a hard timeout via `--case-timeout-seconds` so stalled provider calls still produce failure artifacts.

OpenRouter is also supported for cheap profile runs and explicit stage overrides:

```bash
export OPENROUTER_API_KEY=...
uv run --extra live strategy-codebot run --prompt "Create a Pine v6 moving average crossover strategy" --mode live --cost-profile cheap --out runs/live-openrouter-cheap --policy enforce
uv run --extra live strategy-codebot run --prompt "Create a Pine v6 moving average crossover strategy" --mode live --model-stage pine_code_generation=openrouter/moonshotai/kimi-k2.5 --out runs/live-openrouter-stage --policy enforce
```

Use the model-combo matrix before changing cheap-profile defaults. It runs a small smoke suite first; add `--run-full` only when you want full `live-core` gating for combos that passed smoke:

```bash
uv run --extra live strategy-codebot eval matrix --out runs/evals/model-matrix --policy enforce --concurrency 1
uv run --extra live strategy-codebot eval matrix --out runs/evals/model-matrix-full --policy enforce --concurrency 1 --run-full
uv run --extra live strategy-codebot eval matrix --combo baseline_gemini_all --combo hybrid_gemini_reasoning_review --out runs/evals/model-matrix-subset --policy enforce --concurrency 1
```

The matrix report is written to `model-matrix-report.json`; stage/model health is written to `model-health.json`. A combo is accepted only when it clears pass-rate, static validation, deterministic quality, knowledge-context, repair-count, blocking-failure, timeout/stall, and artifact-completeness gates. Live runs also write `quality-report.json` with deterministic trading-quality findings; blockers fail the production gate while warnings remain observability signals. `quality_profile` is skipped unless one of the configured provider credentials is present.

Cheap-quality model mappings are recorded in `configs/model-registry.example.yaml`. Useful overrides:

| Provider | Strategy reasoning | Pine/code probe | Long-context worker |
| --- | --- | --- | --- |
| OpenRouter | `openrouter/moonshotai/kimi-k2.5` | `openrouter/moonshotai/kimi-k2.5` | `openrouter/minimax/minimax-m3` |

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

Inspect a completed run and export vendor-neutral local telemetry:

```bash
uv run strategy-codebot harness inspect --run-dir runs/phase3-example --out runs/phase3-example/agent-harness-report.json
uv run strategy-codebot run --spec examples/specs/ma-crossover-pine.json --mode dry-run --out runs/otel-example --otel-export runs/otel-example/otel-trace.jsonl --no-record-harness
```

`agent-harness-report.json` summarizes timeline, model/provider usage, policy findings, missing artifacts, and failure attribution. `--otel-export` writes OpenTelemetry/GenAI-inspired JSONL spans locally; it does not send network telemetry.

## Phase 4 Self-Improving Knowledge

Create an offline source snapshot, compare it against a baseline, audit run evidence, and produce a proposal:

```bash
uv run strategy-codebot knowledge snapshot --registry configs/source-registry.yaml --offline --out knowledge/snapshots/offline-current.json
uv run strategy-codebot knowledge diff --baseline examples/knowledge/baseline-snapshot.json --current knowledge/snapshots/offline-current.json --out reports/knowledge-diff.json
uv run strategy-codebot knowledge audit --runs runs/phase3-example --out reports/knowledge-audit.json
uv run strategy-codebot knowledge propose --diff reports/knowledge-diff.json --audit reports/knowledge-audit.json --out knowledge/proposals/phase4-proposal.json
```

Phase 4 creates evidence and recommendations only. Generated snapshots and proposals are ignored by default, and commands must not edit canonical docs such as `docs/trading/*.md`.

## Phase 5 Productization

Check product readiness and build installable distributions:

```bash
uv run strategy-codebot version
uv run strategy-codebot doctor --out reports/doctor.json
uv run strategy-codebot tools check --out reports/tool-check.json
uv run strategy-codebot knowledge check --offline --out reports/source-check.json
uv build --out-dir dist
```

GitHub Actions runs CI on `main` and pull requests. The release-artifacts workflow runs on `workflow_dispatch` or `v*` tags and uploads `dist/*.whl` plus `dist/*.tar.gz` as GitHub artifacts. Phase 5 does not publish to PyPI.

## Non-Goals

- No live trading.
- No broker account integration.
- No profitability claims.
- No generated strategy execution.
- No TradingView or MetaTrader runtime validation yet.
- No automatic mutation of canonical knowledge docs from proposals.
