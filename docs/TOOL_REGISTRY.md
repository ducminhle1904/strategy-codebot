# Tool Registry

The tool registry records runtime tools that the CLI may call. Phase 3 introduced the machine-readable registry in `configs/tool-registry.yaml`; Phase 4 extends it with knowledge snapshot, diff, audit, and proposal tools.

## Capability Vocabulary

- `pine-static-validation`: inspect Pine Script for version, strategy shape, repaint hazards, and missing risk controls.
- `pine-manual-checklist`: produce TradingView manual validation steps.
- `mql5-compile`: compile `.mq5` files with MetaEditor.
- `mt5-backtest`: run MetaTrader 5 Strategy Tester from a config.
- `knowledge-refresh`: fetch and diff trusted source docs.
- `knowledge-snapshot`: record source metadata and content hashes.
- `knowledge-diff`: compare two knowledge snapshots.
- `knowledge-run-audit`: inspect validation, review, and runtime evidence from a run directory.
- `knowledge-improvement-proposal`: produce proposal artifacts for human review.
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

## Phase 4 Knowledge Contracts

Knowledge tools produce local evidence artifacts:

- `knowledge_snapshot` writes `knowledge-snapshot.schema.json` payloads.
- `knowledge_diff` writes `knowledge-diff.schema.json` payloads.
- `knowledge_audit` reads run evidence and writes an audit report.
- `knowledge_proposal` writes `knowledge-proposal.schema.json` payloads.

These tools may recommend doc updates, but they must not edit canonical docs. Generated snapshots and proposals are ignored by default unless copied into examples or fixtures intentionally.
