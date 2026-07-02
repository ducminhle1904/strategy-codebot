# Agent Instructions

This repo uses a repository harness. Keep this file short: it is a map, not the source of truth.

## Read First

1. [docs/product/strategy-codebot.md](docs/product/strategy-codebot.md) for product intent and boundaries.
2. [docs/HARNESS.md](docs/HARNESS.md) for the collaboration model.
3. [docs/FEATURE_INTAKE.md](docs/FEATURE_INTAKE.md) before classifying work.
4. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before changing design or implementation boundaries.
5. [docs/TEST_MATRIX.md](docs/TEST_MATRIX.md) before claiming completion.

## Stable Rules

- Record durable decisions in `docs/decisions/`.
- Record planned work in `docs/stories/`.
- Before non-trivial scout, investigation, implementation, or review work, run or account for `strategy-codebot harness agent-start --summary ...`; read only its bounded `context_brief`, not raw trace rows or full reports.
- If `agent-start` is unavailable, run both `strategy-codebot harness preflight --latest 10` and `strategy-codebot harness session-start --summary ...`, then record `--preflight-applied` in `dev-trace`.
- For non-trivial research, investigation, implementation, verification, or blocker sessions, record a repository trace with `strategy-codebot harness dev-trace` before the final response.
- After non-trivial trace-related work, run `strategy-codebot harness audit-traces --latest 1`.

## Frontend Source Rules

- Treat `apps/web/src/app` as thin Next.js routing/auth/composition only; keep feature behavior in `apps/web/src/features/*`.
- Use the feature-sliced direction already started: `features/<domain>/{api,model,ui}` for domain code, `shared/contracts` for generated/shared contracts, and `server/*` for Next/server adapters.
- Prefer the import flow `app -> features -> shared`; do not import client components from server adapters or add domain behavior back into broad `lib`/`components` buckets.
- Treat `apps/web/src/components/strategy/*` and `apps/web/src/lib/*` files that only re-export feature modules as compatibility wrappers. New source should import from the owning feature or shared leaf module.
- Avoid cross-feature barrel imports when a barrel exports heavy pages or workspace modules; use leaf imports such as `@/features/workspace/ui/conversation-sidebar` or `@/features/artifacts/model/artifact-workspace` to prevent cycles.
- Keep generated workflow registry TypeScript in `apps/web/src/shared/contracts/workflow-registry-contract.ts`; `apps/web/src/lib/workflow-registry-contract.ts` is a compatibility wrapper.
- Verify frontend changes with `npm --prefix apps/web run build`, `npm --prefix apps/web test -- --run`, and `npm --prefix apps/web run lint`; for structural refactors, also check for feature import cycles before claiming completion.

## Backend Source Rules

- Treat `src/strategy_codebot/server/app.py` as FastAPI composition and legacy orchestration only. Do not add new domain endpoints inline there when an owning `server/modules/<domain>/router.py` can hold them.
- Keep backend module ownership in `src/strategy_codebot/server/modules/catalog.py`: add path prefixes, OpenAPI tags, and tool ownership there before adding route/tag/tool heuristics elsewhere.
- Put extracted API routes in `server/modules/<domain>/router.py`; pass dependencies through a small `<Domain>RouterDeps` dataclass instead of growing long router factory parameter lists.
- Keep stable cross-module facades in `src/strategy_codebot/server/contracts/`. Import registry/workflow contracts from there when code crosses module boundaries.
- Keep concrete tool handlers and `TOOL_DEFINITIONS` in `llm_tools.py`; compose `TOOL_HANDLERS` through `modules/tools.py` and catalog ownership. Do not recreate per-domain string-to-`globals()` handler maps.
- Keep `ConversationRepository` as the precise static Protocol in `repository.py`. Use `contracts/repository_ports.py` descriptors for boundary coverage, not runtime `isinstance` repository validation.
- Treat `LLMOrchestrator.services` as a derived service-port view. Do not cache duplicate mutable dependencies unless all call sites use that cache as the single source of truth.
- Verify backend structural changes with `uv run pytest tests/test_server_module_boundaries.py -q`, relevant server tests, `uv run strategy-codebot tools check`, and full `uv run pytest -q` before claiming completion.

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
