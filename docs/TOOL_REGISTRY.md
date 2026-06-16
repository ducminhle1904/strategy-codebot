# Tool Registry

The tool registry records runtime tools that the CLI may call. Phase 3 keeps this registry machine-readable in `configs/tool-registry.yaml`.

## Capability Vocabulary

- `pine-static-validation`: inspect Pine Script for version, strategy shape, repaint hazards, and missing risk controls.
- `pine-manual-checklist`: produce TradingView manual validation steps.
- `mql5-compile`: compile `.mq5` files with MetaEditor.
- `mt5-backtest`: run MetaTrader 5 Strategy Tester from a config.
- `knowledge-refresh`: fetch and diff trusted source docs.
- `strategy-risk-review`: review strategy assumptions and safety boundaries.
- `harness-trace`: record run evidence into the repository harness.

## Provider Fields

Future tool records should include:

- `name`
- `kind`: `cli`, `binary`, `mcp`, `skill`, `http`
- `capability`
- `command` or endpoint
- `status`: `present`, `missing`, `unknown`
- `evidence_required`

## Phase 0

Phase 0 only documents capabilities. It does not require installed validators.

## Phase 3 Contract Fields

Each tool contract includes:

- `id`
- `capability`
- `risk_tier`
- `input_schema_ref`
- `output_schema_ref`
- `evidence_required`
- `phase_status`

The runtime harness records each tool invocation as ordered JSONL events. Missing future tools must degrade cleanly; implemented Phase 3 tools must pass `strategy-codebot tools check`.
