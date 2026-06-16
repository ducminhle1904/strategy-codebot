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

## Phase 2 Matrix

| Artifact | Proof | Status |
| --- | --- | --- |
| Review report schema | `uv run pytest tests/test_review.py::test_review_report_schema_accepts_valid_report` validates `review-report.json` | planned |
| Parallel dry-run reviewers | `uv run pytest tests/test_review.py::test_dry_run_parallel_review_returns_four_reviewers` verifies four reviewer roles | planned |
| Reviewer failure isolation | `uv run pytest tests/test_review.py::test_reviewer_exception_yields_partial_report` verifies fail-soft behavior | planned |
| Risk policy review | `uv run pytest tests/test_review.py::test_risk_reviewer_blocks_profit_and_live_trading_claims` verifies blocked claims | planned |
| MQL5 boundary review | `uv run pytest tests/test_review.py::test_mql5_target_keeps_manual_required_boundary` verifies manual-required status | planned |
| Review CLI | `uv run pytest tests/test_cli.py::test_cli_review_existing_run_writes_report` verifies standalone review command | planned |
| Integrated review run | `uv run pytest tests/test_runner.py::test_integrated_parallel_review_creates_review_artifact` verifies `run --review parallel` | planned |

## Phase 3 Matrix

| Artifact | Proof | Status |
| --- | --- | --- |
| Tool registry | `uv run strategy-codebot tools check --out reports/tool-check.json` validates tool contracts | planned |
| Tool contract schema | `uv run pytest tests/test_tool_runtime.py::test_tool_registry_contracts_are_valid` validates each registry entry | planned |
| Runtime event trace | `uv run pytest tests/test_tool_runtime.py::test_runtime_trace_jsonl_and_summary_validate` validates JSONL events and summary | planned |
| Runtime policy blocking | `uv run pytest tests/test_tool_runtime.py::test_tool_harness_enforce_blocks_prohibited_risk_tier` verifies enforce-mode block events | planned |
| Runtime run artifacts | `uv run pytest tests/test_runner.py::test_dry_run_creates_pine_artifacts` verifies trace and summary files | planned |
| Runtime trace opt-out | `uv run pytest tests/test_runner.py::test_no_runtime_trace_preserves_phase_2_artifact_shape` verifies `--no-runtime-trace` behavior | planned |
| Standalone review trace | `uv run pytest tests/test_cli.py::test_cli_review_does_not_overwrite_run_runtime_trace` verifies review trace isolation | planned |

## Evidence Rules

- Do not mark Pine strategy backtests as passed without TradingView evidence.
- Do not mark MQL5 compile/test as passed without MetaEditor/MetaTrader evidence.
- Do not treat multi-agent agreement as a substitute for deterministic validation.
- Do not treat `review-report.json` as a replacement for `validation-report.json`.
- Do not treat runtime traces as TradingView or MetaTrader execution proof.
