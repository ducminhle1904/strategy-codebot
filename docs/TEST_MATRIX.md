# Test Matrix

## Status Values

- `planned`: validation is designed but not implemented.
- `manual_required`: human or external platform proof is required.
- `pass`: validation completed successfully.
- `fail`: validation completed and found a blocker.
- `skipped`: validation is intentionally not applicable.

## Phase 0 Matrix

| Artifact | Proof | Status |
| --- | --- | --- |
| Harness docs | Required docs exist with clear headings | planned |
| Strategy spec schema | JSON schema parses | planned |
| Agent run schema | JSON schema parses | planned |
| Validation report schema | JSON schema parses | planned |
| Model registry | YAML parses and maps agents to LiteLLM-style models | planned |
| Source registry | YAML parses and includes official Pine/MQL5 sources | planned |
| Agent roles | Each role has responsibility, inputs, outputs, and stop conditions | planned |
| Pine rules | States static/manual validation boundary | planned |
| MQL5 rules | States future Windows/MT5 runner boundary | planned |
| Decisions/stories | IDs and statuses are consistent | planned |

## Phase 1 Matrix

| Artifact | Proof | Status |
| --- | --- | --- |
| CLI dry-run | `uv run strategy-codebot run --spec examples/specs/ma-crossover-pine.json --mode dry-run --no-record-harness` creates run artifacts | planned |
| Pine static validator | `uv run pytest tests/test_pine.py` covers pass, fail, warning, and missing-risk cases | planned |
| MQL5 runner design | `uv run pytest tests/test_runner.py` verifies `manual_required` runner design for combined targets | planned |
| Knowledge source check | `uv run strategy-codebot knowledge check --offline --out reports/source-check.json` validates registry metadata | planned |
| Harness trace wrapper | `uv run pytest tests/test_harness.py` verifies trace command construction without mutating harness state | planned |

## Evidence Rules

- Do not mark Pine strategy backtests as passed without TradingView evidence.
- Do not mark MQL5 compile/test as passed without MetaEditor/MetaTrader evidence.
- Do not treat multi-agent agreement as a substitute for deterministic validation.
