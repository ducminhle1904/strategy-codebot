# Harness

The harness makes repository knowledge, workflow state, validation evidence, and decisions durable for humans and agents.

## Mental Model

`strategy-codebot` should not rely on chat history to understand what to build. The repository should answer:

- What product is this?
- What target platforms are supported?
- Which agent roles exist?
- Which schemas define the handoff contracts?
- What validation proof is required?
- Which decisions are already locked?

## Phase 0 Scope

Phase 0 creates design artifacts only:

- Harness docs.
- Trading platform rules.
- Agent role specs.
- JSON schemas.
- Model/source registries.
- Initial stories and decisions.

Phase 0 does not implement the runtime, orchestration graph, code generators, validators, or ingestion jobs.

## Task Loop

1. Classify the request with [FEATURE_INTAKE.md](FEATURE_INTAKE.md).
2. Read product and architecture docs.
3. Update or add a story if the work changes the roadmap.
4. Implement only within the current phase boundary.
5. Validate against [TEST_MATRIX.md](TEST_MATRIX.md).
6. Record durable decisions when behavior or architecture changes.

## Development Session Traces

Agents should record a repository trace before the final response for any non-trivial research, investigation, implementation, verification, or blocker session. This is a checkpoint action, not a background daemon and not a per-message log.

Use `strategy-codebot harness dev-trace` with the meaningful session facts:

- `--intake` to link an existing intake, or omit it to auto-create a `maintenance request` intake. Use `--no-link-intake` only when an unlinked trace is intentional.
- `--summary` for the task or finding.
- `--action` for completed work or important tool events.
- `--read` for files, docs, commands, or sources consulted.
- `--changed` for files or artifacts changed, or the command's explicit no-change sentinel.
- `--decision` for choices the next agent should inherit.
- `--error` and `--friction` when the session hit failed tools, policy blocks, or harness friction.

For normal clean sessions, `--friction none`, `--duration 0`, and `--tokens 0` are acceptable when exact runtime metrics are unavailable.

## Duration Tracking

Use `strategy-codebot harness session-start --summary ...` before non-trivial work. The command writes `.strategy-codebot/harness-session.json`; the next `harness dev-trace` uses it to compute elapsed wall-clock `duration_seconds` and removes the state file after a successful trace.

`harness dev-trace` also accepts `--started-at` for explicit epoch or ISO timestamps and `--no-session-state` when the duration is intentionally unavailable. If no duration source is available, the trace records `0` and notes `duration unavailable`.

## Risk Classification

Auto-created dev-trace intakes are classified deterministically unless `--lane` or `--intake-type` is supplied. Small docs-only changes use lane `tiny`; harness, trace, audit, schema, runtime policy, live workflow, model routing, generated execution path, or broad multi-file changes use `high-risk`; normal code/test work uses `normal`.

Harness/trace/audit/observability work uses intake type `harness improvement`. Product generation runs keep `new spec`. Generic dev traces use `maintenance request`.

## Trace Quality Gate

Use `strategy-codebot harness audit-traces` to check repository trace quality. The default audits only the latest trace so legacy rows do not fail normal development. Use `--latest N`, `--since-id ID`, or `--all` when deliberately auditing a broader window.

The gate fails traces that are unlinked, have null friction/errors/runtime fields, or omit required action/read/change/decision arrays. Write `--out` JSON when the audit result should become durable evidence.

## Trace Analytics

Use `strategy-codebot harness summarize-traces` to summarize recent development process signals: linked versus unlinked traces, clean versus error traces, friction, lanes/types, frequently read or changed files, decision timeline, and rows that need cleanup.

Existing pre-standard traces may remain in the database as historical evidence. Do not rewrite or delete them unless a future cleanup task explicitly asks for destructive maintenance.

## Development Evidence

Use `strategy-codebot harness assess-development` when trace quality needs to answer more than "was this recorded?". The assessment reads available run artifacts such as `validation-report.json`, `review-report.json`, runtime trace JSONL files, `live-workflow-trace.json`, and eval reports, then reports verification, review, runtime failure, business-correctness, human-correction, and production-gate evidence.

`strategy-codebot harness summarize-traces --include-development-evidence --artifacts-root ...` includes the same evidence aggregates in the trace summary. High-risk traces without verification metadata or artifact evidence produce audit warnings.

Trace evidence is not proof that code is correct. It proves which quality gates existed, what they reported, and where evidence is missing. Legacy traces are not backfilled in this phase; missing evidence is reported as `unknown`.

## Process vs Engineering Quality

Process quality comes from linked detailed traces, duration, risk lane, decisions, errors, and friction. Engineering quality comes from artifacts and explicit outcomes: tests, validation, review, runtime tools, business-correctness gates, human corrections, and production/live impact. Treat `unknown` as a prompt to add evidence, not as a pass.

## High-Risk Review Evidence

High-risk work should include review evidence. Preferred evidence is a `review-report.json` artifact from `run --review parallel` or `review --record-harness`. For manual `harness dev-trace`, `review_outcome=pass` is acceptable only when paired with bounded `review_evidence`; `review_outcome=skipped` is acceptable only when paired with `review_justification`.

`harness gate-development --policy observe` warns on skipped or manual-required high-risk review outcomes. `--policy enforce` fails high-risk traces with missing review evidence, failed/blocked review, or skipped/manual-required review without justification. The gate records only short metadata and never dumps raw review reports into context.

## Passive Quality Loop

Use `strategy-codebot harness agent-start --summary ...` before non-trivial scout, investigation, implementation, or review work. It runs bounded preflight, writes `.strategy-codebot/harness-preflight.json`, starts the session timer, and records startup state that the next `harness dev-trace` consumes as `preflight_applied=true`.

Use `strategy-codebot harness preflight` at the start of non-trivial work when recent trace history should guide the session. It writes a bounded `context_brief` to `.strategy-codebot/harness-preflight.json` by default. The brief is intentionally small: no raw trace rows, no raw artifacts, and no long JSON dumps.

Use `strategy-codebot harness gate-development` when trace-derived quality policy should warn or block. The default `--policy observe` reports issues without failing. Use `--policy enforce` only when the caller explicitly wants missing evidence, failed audits, production gate failures, or high-risk zero-duration traces to fail the command.

Use `strategy-codebot harness recommend-next` for local recommendation backlog output. It writes `.strategy-codebot/harness-recommendations.json` by default. It does not create stories unless `--write-story` is explicitly supplied.

Use `strategy-codebot harness memory-candidates` only to generate candidate insights from repeated warnings. It never writes Codex memory or global context. Memory promotion must be explicitly requested by the user and handled through the normal memory-update workflow.

Use `strategy-codebot harness intelligence-report` to aggregate local eval/live artifacts into a model-stage scorecard, failure summary, and route recommendations. It reads only explicit artifact roots or local JSON reports and writes a bounded report under `.strategy-codebot/` by default.

Use `strategy-codebot harness propose-lessons` to turn repeated failures into review-only proposal artifacts. Proposals may include knowledge-candidate IDs for prompt, validator, or playbook gaps, but they are not approved knowledge and do not mutate source registries.

Use `strategy-codebot harness replay-recommendations` to run an eval suite against proposal output and record whether proposals are ready for human review. Replay results are evidence, not auto-approval.

Use `strategy-codebot harness propose-improvements` to turn reviewed lesson proposals into improvement candidates such as route policy patches, KB candidate reviews, or eval gaps. Candidates are review-only and never mutate registries, KB, or memory.

Use `strategy-codebot harness apply-approved-improvement` only after a candidate artifact has `status=approved`. The command writes a patch artifact for a later implementation turn; it does not edit repo-tracked files directly.

Live eval writes checkpointed root `eval-report.json` progress while cases run. Long or detached runs should be polled until `is_complete=true`; if a run is interrupted, the latest report and per-case artifacts remain partial evidence.

## Anti-Pollution Contract

- Do not place raw trace rows, raw artifacts, full reports, or long JSON in model context.
- Read only bounded `context_brief` bullets during preflight.
- Record `preflight_applied=true` in dev traces when preflight guided the session.
- Keep generated loop reports under ignored `.strategy-codebot/` unless `--out` or `--write-story` is explicitly supplied.
- Do not update `AGENTS.md`, global context, or Codex memory from trace insights automatically.
- Treat recommendations and memory candidates as proposals, not durable knowledge.
- Treat harness intelligence proposals and replay results as review inputs. Do not auto-promote them into KB, routing registries, source registries, or memory.
- Treat harness improvement candidates and approved patch artifacts as review inputs. Do not auto-apply them to repo-tracked config, KB, source registries, or memory.
- Treat `evidence_completeness.confidence=partial` as useful diagnostic signal, not full-suite proof.

## Done Definition

A task is done only when:

- Relevant docs or schemas are updated.
- Local validation evidence exists.
- The test matrix row is satisfied.
- Any new durable tradeoff is recorded in `docs/decisions/`.
- Non-trivial agent work has a `harness dev-trace` repository trace.
