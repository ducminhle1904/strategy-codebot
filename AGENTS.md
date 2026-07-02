# Agent Instructions

This repo uses a repository harness. Keep this file short: it is a map, not the source of truth.

## Read First

1. [docs/product/strategy-codebot.md](docs/product/strategy-codebot.md) for product intent and boundaries.
2. [docs/HARNESS.md](docs/HARNESS.md) for the collaboration model.
3. [docs/FEATURE_INTAKE.md](docs/FEATURE_INTAKE.md) before classifying work.
4. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before changing design or implementation boundaries.
5. [docs/TEST_MATRIX.md](docs/TEST_MATRIX.md) before claiming completion.

## Stable Rules

- Phase 0 is docs, schemas, configs, and planning records only.
- Do not add live-trading automation unless a future decision explicitly approves it.
- Pine Script validation is static/manual-proof first because TradingView is the authoritative runtime.
- MQL5 validation will require a Windows/MetaTrader runner in a later phase.
- Record durable decisions in `docs/decisions/`.
- Record planned work in `docs/stories/`.
- Before non-trivial scout, investigation, implementation, or review work, run or account for `strategy-codebot harness agent-start --summary ...`; read only its bounded `context_brief`, not raw trace rows or full reports.
- If `agent-start` is unavailable, run both `strategy-codebot harness preflight --latest 10` and `strategy-codebot harness session-start --summary ...`, then record `--preflight-applied` in `dev-trace`.
- For non-trivial research, investigation, implementation, verification, or blocker sessions, record a repository trace with `strategy-codebot harness dev-trace` before the final response.
- After non-trivial trace-related work, run `strategy-codebot harness audit-traces --latest 1`.

## Product Trace Lessons

- Treat chat, workflow rail, task events, skipped/not-applicable steps, and render timing as one product contract across backend events, frontend normalizers, panels, and tests.
- Strategy-to-Paper-Bot and Nautilus runtime work is not complete at code/tests only; verify rebuilt containers, SSE/API state, persisted runtime/session state, stop/finalization behavior, and the audit path.
- Model/provider routing is user-facing product behavior: surface timeouts, rate limits, malformed responses, free/paid route limits, policy rejection, and fallback state explicitly.
- Prefer registry-first product contracts for intents, actions, workflow steps, KB selection, and event presentation; avoid hardcoded FE/BE heuristics unless a trace-backed decision says otherwise.
- Do not fix intent or policy false positives by growing multilingual keyword allowlists. Deterministic checks should only be high-confidence prechecks/evidence; semantic LLM classifiers may propose intent/polarity, and registry/backend validators decide the final state.
- Workflow UI state must come from structured backend events and durable task records, not assistant prose. The rail is progress/info; blocking human-in-the-loop questions belong in typed task prompts that hide the composer until resolved.
- Risky transitions require canonical audit events in `run_events`: model proposal, backend validation/rejection, user gate, and execution decision. Next.js logs are bridge/operational evidence, not the durable source of truth.
- Treat artifacts and knowledge-base freshness as product behavior: preserve artifact ordering, required-source promotion, stale-chunk pruning, and suppression of raw or empty validation/trade/plan reports.
- When Clerk or browser auth blocks direct UI proof, verify product behavior through backend stream/state checks plus focused frontend tests, and record the auth blocker clearly.

## Chat Session Audit Playbook

- Given a `conv_...` id, audit the live runtime timeline first: Docker/OrbStack service logs plus Postgres records for `conversation_messages`, `assistant_runs`, `run_events`, `tool_calls`, workflow tasks, and artifacts when relevant.
- Reconstruct the session in order: user messages, assistant runs, run status/errors, provider/model route events, classifier events, `model_action.*`, workflow/task events, tool events, continuation events, and final persisted assistant messages.
- Treat `run_events` and durable task/artifact records as canonical. Use Next.js/Copilot route logs only to correlate stream bridge behavior, request ids, frontend timeouts, or missing UI rendering.
- Compare expected events against actual events: identify missing `chat.workflow.updated`, `workflow.task.created`, `tool.started/completed`, `message.delta`, `run.completed`, or failure events before blaming the UI or model prose.
- Do not dump raw prompts, secrets, broker/account ids, full artifacts, or raw tool output in the audit summary. Report safe ids, timestamps, event names, status, reason codes, and short payload summaries.
- End audits with one concrete root cause, the evidence trail, and the smallest backend/frontend fix path; note explicitly when evidence is missing or auth/browser access blocked direct UI proof.
