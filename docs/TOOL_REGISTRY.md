# Tool Registry

The tool registry records optional capabilities that future agents may use. Missing tools should degrade cleanly instead of failing unrelated work.

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

