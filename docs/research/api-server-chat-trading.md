# API Server Research: Chat-Style Trading Assistant

Date: 2026-06-17

## Scope

This note scouts API-server best practices for a future ChatGPT-like UI focused on crypto/forex strategy work.

The server should extend Strategy Codebot's current product boundary:

- Generate, validate, review, and improve reviewable trading strategy artifacts.
- Support Pine Script v6 and MQL5 workflows.
- Provide conversational UX with streaming responses.
- Preserve the safety boundary: no broker integration, no live order placement, no profitability guarantees, and no platform runtime-success claims without external evidence.

## Recommended Server Shape

Start with a Python API service because the repo is already Python, Typer, Pydantic, pytest, and optional OpenAI/LangGraph/LiteLLM integrations.

Recommended MVP stack:

- FastAPI or equivalent ASGI server for HTTP, SSE, and optional WebSocket support.
- PostgreSQL for durable users, conversations, messages, artifacts, runs, trace summaries, and audit metadata.
- Redis for distributed rate limits, stream coordination, short-lived locks, and job state.
- Background worker for long tasks: strategy generation, review, validation, eval, and knowledge proposal jobs.
- Object/blob storage for generated files and raw artifacts if artifacts outgrow Postgres.
- Existing `strategy_codebot.runner`, `review`, `pine`, `mql5`, `tool_runtime`, and `harness` modules as internal application services, not directly exposed as public tools.

Do not make the first API a generic "LLM proxy". Make it a domain application server with explicit trading-safe workflows.

## Product API Surfaces

Minimal first API:

- `POST /v1/conversations`
  - Creates a conversation.
  - Stores domain mode: `strategy_design`, `pine_generation`, `mql5_design`, `review`, `validation`, `education`.

- `GET /v1/conversations`
  - Lists conversations owned by the authenticated user.

- `GET /v1/conversations/{conversation_id}`
  - Returns message history, run state, and artifact refs.
  - Must enforce object-level authorization on every lookup.

- `POST /v1/conversations/{conversation_id}/messages`
  - Accepts user message and optional structured inputs.
  - Returns either a message result or a stream handle.

- `GET /v1/conversations/{conversation_id}/events`
  - SSE stream for assistant deltas, tool progress, validation status, review status, and final artifact references.

- `POST /v1/runs`
  - Starts structured strategy generation/review from a `strategy-spec` payload.
  - Useful when UI wants a form-first workflow instead of free chat.

- `GET /v1/runs/{run_id}`
  - Returns status, summary, validation/review state, and artifact refs.

- `GET /v1/artifacts/{artifact_id}`
  - Downloads or renders generated Pine, reports, checklists, and JSON artifacts.

- `POST /v1/feedback`
  - Captures thumbs, correction notes, and human decisions for eval and harness loops.

Avoid exposing:

- Raw provider credentials.
- Arbitrary tool execution.
- Broker/exchange order endpoints.
- Direct filesystem paths.
- Unbounded chat completions without domain routing and policy checks.

## Streaming Model

Use Server-Sent Events for the first web UI because assistant output is server-to-client, simple to reconnect, and maps well to typed event streams.

Recommended SSE event types:

- `message.created`
- `message.delta`
- `tool.started`
- `tool.progress`
- `tool.completed`
- `validation.completed`
- `review.completed`
- `artifact.created`
- `policy.blocked`
- `run.completed`
- `run.failed`

Use WebSockets later only if the product needs bidirectional realtime control, multiplayer sessions, voice, or browser-side interruption semantics that SSE plus cancel endpoints cannot cover.

Every streamed event should include:

- `event_id`
- `conversation_id`
- `run_id`
- `sequence`
- `type`
- `created_at`
- compact `payload`

Persist durable events separately from transient token deltas. Token deltas can be compacted; final messages, tool calls, validation reports, policy decisions, and artifacts should be durable.

## LLM Orchestration

Recommended default:

- Server owns the agent loop.
- The model can request narrow application tools.
- The API server executes tools after authorization, schema validation, policy checks, and budget checks.
- Tool results go back into the model context.
- Final response is stored and streamed.

Tool categories:

- Read-only market context tools: price/candle snapshots, symbol metadata, news/source retrieval.
- Codegen tools: generate Pine, generate MQL5 design, produce manual checklist.
- Validation tools: static Pine validation, schema validation, risk-policy validation.
- Review tools: trading logic review, Pine specialist review, risk review, critic review.
- Knowledge tools: source registry checks, snapshot/diff/audit/proposal.

Hard rule: market data and strategy tools must produce evidence-bearing context, not hidden recommendations. The assistant should cite or reference the data snapshot it used.

## Trading Domain Guardrails

Server-side guardrails should be policy code, not only prompt text.

Required guardrails:

- No live order placement.
- No broker/exchange trade execution.
- No "guaranteed profit", "risk-free", "cannot lose", or "backtested successfully" claims without proof.
- No hidden model/provider decisions in generated reports.
- No personalized financial advice unless the product later has legal/compliance approval.
- User-visible distinction between educational analysis, strategy artifact generation, static validation, manual validation, and runtime proof.

For crypto/forex:

- Treat market data as stale unless timestamped.
- Require source, symbol, interval, timezone, and retrieval time on every data snapshot.
- Separate spot crypto, derivatives, forex, CFDs, and synthetic symbols in data contracts.
- Do not mix exchange-specific symbols without normalization metadata.
- Keep "strategy idea" separate from "trade recommendation".

## Data Model

Core tables:

- `users`
- `workspaces`
- `conversation_threads`
- `conversation_messages`
- `assistant_runs`
- `run_events`
- `tool_calls`
- `artifacts`
- `strategy_specs`
- `validation_reports`
- `review_reports`
- `market_data_snapshots`
- `policy_findings`
- `feedback_events`
- `usage_ledger`

Security fields to include everywhere:

- `owner_user_id`
- `workspace_id`
- `created_at`
- `updated_at`
- `deleted_at`
- `source`
- `trace_id`
- `idempotency_key` where writes can be retried

Use row-level isolation at the database layer in addition to application authorization if the deployment supports it.

## Security Requirements

API baseline:

- Object-level authorization on every resource ID.
- Function-level authorization on admin, eval, knowledge, model, and artifact endpoints.
- Input schema validation with strict unknown-field behavior for public writes.
- Output filtering to prevent leaking raw prompts, secrets, provider payloads, and cross-user artifacts.
- Per-user, per-IP, per-workspace, per-model, and per-tool rate limits.
- Request body limits, file limits, token budget limits, and max tool-call depth.
- Idempotency keys for message send, run creation, and feedback writes.
- Structured audit logs for auth failures, policy blocks, tool calls, artifact access, and provider errors.
- Secret isolation: never send provider keys to the browser.
- Prompt-injection defenses around retrieved market/news/source content.

LLM-specific security:

- Treat user prompts, uploaded specs, retrieved web/news text, market metadata, and previous assistant output as untrusted.
- Give tools least privilege and explicit schemas.
- Do not allow generated code or tool arguments to trigger shell/network/file actions directly.
- Record tool-call decisions and blocked actions.
- Add human approval before any future sensitive workflow.

## Rate Limits And Cost Controls

Use layered limits:

- Gateway-level basic IP/user request limits.
- Redis-backed distributed limits for model/tool budgets.
- Per-run caps: max messages, max context tokens, max tool calls, max artifacts, max runtime seconds.
- Per-workspace monthly usage ledger.
- Backpressure for long review/eval jobs.

Useful limit dimensions:

- `user_id`
- `workspace_id`
- `ip`
- `model`
- `tool_id`
- `conversation_id`
- `market_data_provider`

## Observability

Each assistant run should emit:

- Request ID.
- Conversation ID.
- Run ID.
- Trace ID.
- Model/provider/stage metadata.
- Tool-call list.
- Policy findings.
- Validation/review decisions.
- Token and cost estimate.
- Latency by stage.
- Error classification.

Keep raw provider payloads disabled by default. Allow explicit debug capture only in trusted dev/admin mode with retention limits and redaction.

## Suggested MVP Roadmap

1. Define API story and decision docs.
   - Add a high-risk story for API server introduction.
   - Add a decision for "API-first chat server boundary".

2. Add server package skeleton.
   - `src/strategy_codebot/server/`
   - `app.py`, `auth.py`, `schemas.py`, `routes/`, `services/`
   - No database mutation beyond local dev storage in the first skeleton if scope needs to stay small.

3. Implement read/write conversation API with local SQLite or Postgres abstraction.
   - No LLM initially.
   - Auth stub or local dev auth only.

4. Add streaming assistant run endpoint.
   - Dry-run deterministic responder first.
   - SSE event contract and tests.

5. Bridge to existing runner.
   - `POST /v1/runs` calls current deterministic run flow.
   - Artifacts returned by stable IDs.

6. Add LLM orchestration behind policy gates.
   - Responses API or Agents SDK behind an internal service.
   - Tool registry maps to existing safe tool runtime.

7. Add review/validation/eval/harness traces.
   - Preserve current repository harness evidence pattern.
   - Add API run traces without exposing raw trace rows to users.

8. Harden security.
   - Authorization tests.
   - Rate-limit tests.
   - Prompt-injection tests.
   - Cross-tenant artifact access tests.

## Open Questions

- Will the first UI be single-user/local, SaaS multi-tenant, or internal alpha?
- Should the API be Python FastAPI only, or split into a Node/Next BFF plus Python worker?
- Which market data provider is allowed for alpha, and what are its redistribution terms?
- Will user accounts require billing/usage limits from day one?
- Should chat memory be model-provider state, application-owned state, or both?
- Do we need compliance review before allowing personalized portfolio/trade context?

## Sources

- OpenAI Responses API: https://developers.openai.com/api/reference/responses/overview
- OpenAI streaming responses: https://developers.openai.com/api/docs/guides/streaming-responses
- OpenAI function calling: https://developers.openai.com/api/docs/guides/function-calling
- OpenAI guardrails and human review: https://developers.openai.com/api/docs/guides/agents/guardrails-approvals
- OpenAI Agents SDK tracing: https://openai.github.io/openai-agents-python/tracing/
- OWASP API Security Project: https://owasp.org/www-project-api-security/
- OWASP Top 10 for LLM Applications: https://owasp.org/www-project-top-10-for-large-language-model-applications/
- MDN Server-Sent Events: https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events
- PostgreSQL Row Security Policies: https://www.postgresql.org/docs/current/ddl-rowsecurity.html
- Redis rate limiter: https://redis.io/docs/latest/develop/use-cases/rate-limiter/
- CFTC virtual currency trading risks: https://www.cftc.gov/LearnAndProtect/AdvisoriesAndArticles/understand_risks_of_virtual_currency.html
- FINRA crypto assets topic: https://www.finra.org/rules-guidance/key-topics/crypto-assets
