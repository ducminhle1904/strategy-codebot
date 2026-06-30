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

## Phase 4 Matrix

| Artifact | Proof | Status |
| --- | --- | --- |
| Knowledge snapshot schema | `uv run pytest tests/test_knowledge_loop.py::test_source_snapshot_schema_validates_offline_snapshot` validates offline snapshots | planned |
| Deterministic source hashing | `uv run pytest tests/test_knowledge_loop.py::test_snapshot_hashing_is_deterministic_for_internal_docs_and_offline_urls` verifies stable hashes | planned |
| Knowledge diff | `uv run pytest tests/test_knowledge_loop.py::test_diff_reports_changed_added_removed_and_unchanged` verifies changed, added, removed, and unchanged sources | planned |
| Knowledge audit | `uv run pytest tests/test_knowledge_loop.py::test_audit_extracts_warnings_and_failed_tools` verifies run evidence extraction | planned |
| Knowledge proposal | `uv run pytest tests/test_knowledge_loop.py::test_proposal_combines_diff_and_audit_without_editing_docs` verifies proposal evidence and no canonical-doc mutation | planned |
| Knowledge CLI chain | `uv run pytest tests/test_cli.py::test_cli_knowledge_snapshot_diff_audit_and_propose` verifies snapshot, diff, audit, and propose commands | planned |
| Tool registry extension | `uv run strategy-codebot tools check --out reports/tool-check.json` validates Phase 4 tool contracts | planned |

## Phase 5 Matrix

| Artifact | Proof | Status |
| --- | --- | --- |
| CLI version | `uv run pytest tests/test_cli.py::test_cli_version_prints_package_version` verifies version output | planned |
| Doctor report | `uv run strategy-codebot doctor --out reports/doctor.json` writes product readiness checks | planned |
| Package metadata | `uv run pytest tests/test_schema.py::test_package_metadata_is_product_ready` verifies license, authors, URLs, and classifiers | planned |
| CI workflow | `.github/workflows/ci.yml` runs tests, compileall, parse checks, doctor, registry checks, and dry-run smoke | planned |
| Release artifacts workflow | `.github/workflows/release-artifacts.yml` builds and uploads wheel plus sdist artifacts | planned |
| Local package build | `uv build --out-dir dist` creates installable distributions | planned |

## App Workflow Matrix

| Artifact | Proof | Status |
| --- | --- | --- |
| Workflow registry task contract | `uv run pytest tests/test_workflow_registry_contract.py tests/test_docker_deployment.py::test_workflow_registry_contract_generated_files_are_current -q` validates shared FE/BE generated registry currentness | pass |
| Workflow task inbox backend | `uv run pytest tests/test_server_llm_orchestration.py -q` verifies task creation, response validation, tenant isolation, and paper-bot workflow gates | pass |
| Workflow rail task UI | `cd apps/web && npm test -- src/lib/workflow-ui.test.ts src/components/strategy/workflow-panel.test.tsx` verifies generic task normalization, rendering, and structured submit callbacks | pass |
| Workflow rail integration typing | `cd apps/web && npx tsc --noEmit --pretty false` verifies workspace task handlers and backend client integration | pass |
| Workflow container rebuild | `docker compose -f compose.yml build api chat-worker web` rebuilds API, chat worker, and web images after registry/API/UI changes | pass |

## Evidence Rules

- Do not mark Pine strategy backtests as passed without TradingView evidence.
- Do not mark MQL5 compile/test as passed without MetaEditor/MetaTrader evidence.
- Do not treat multi-agent agreement as a substitute for deterministic validation.
- Do not treat `review-report.json` as a replacement for `validation-report.json`.
- Do not treat runtime traces as TradingView or MetaTrader execution proof.
- Do not auto-promote knowledge proposals into canonical docs without human review.
- Do not treat GitHub artifacts as PyPI publication or trading-runtime proof.
