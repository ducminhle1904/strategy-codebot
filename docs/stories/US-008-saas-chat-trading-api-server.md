# US-008 - SaaS Chat Trading API Server

## Status

Planned

## Goal

Define the first SaaS API server slice for a future ChatGPT-like trading UI.
The API should support conversations, strategy runs, artifacts, streaming run
events, feedback, authorization boundaries, rate limits, and harness evidence
without adding live trading or broker execution.

## Acceptance Criteria

- Public API planning uses `/v1` as the namespace.
- The first resource set is limited to conversations, messages, runs, run
  events, artifacts, and feedback.
- Conversation APIs are designed for user and workspace authorization from day
  one, even if the first implementation uses a dev auth stub.
- Run APIs are designed to call Strategy Codebot domain workflows instead of
  acting as a generic LLM proxy.
- Artifact APIs use opaque IDs and never expose filesystem paths or database
  internals.
- Streaming uses Server-Sent Events first, with typed events for message
  deltas, tool progress, validation, review, artifact creation, policy blocks,
  run completion, and run failure.
- Every persisted API resource includes `owner_user_id`, `workspace_id`,
  timestamps, and trace or run correlation.
- Policy blocks are planned as server-side checks before tool execution and
  before final user-visible claims.
- Tenant isolation, object-level authorization, and function-level
  authorization are explicit requirements for Phase 1 implementation.
- Rate limits are planned across user, workspace, IP, model, tool, and run
  dimensions.
- Harness trace evidence is required for API server implementation,
  verification, and blocker sessions.
- Market data is deferred. If added later, snapshots must include source,
  retrieval timestamp, symbol, interval, timezone, and provider metadata.

## Out of Scope

- Frontend UI.
- Broker or exchange integration.
- Live order placement.
- Market-order endpoints.
- Billing implementation.
- Live provider orchestration.
- WebSocket realtime UX.
- Server package, dependency, or database migration implementation in Phase 0.

## Phase 1 Foundation

The first implementation slice adds a FastAPI/ASGI server package with:

- Public `GET /health`.
- Header-based dev auth for `/v1/*` using `X-User-Id` and `X-Workspace-Id`.
- In-memory conversation storage injected through the app factory for isolated
  tests.
- `POST /v1/conversations`, `GET /v1/conversations`, and
  `GET /v1/conversations/{id}`.
- `POST /v1/conversations/{id}/messages` for user messages only.
- Object-level authorization tests that return `404` for cross-user or
  cross-workspace conversation access.

This slice intentionally defers assistant responses, run execution, artifact
APIs, feedback APIs, Server-Sent Events, persistence, rate limiting,
production auth, billing, and UI.

## Phase 2 Data Model

The second implementation slice adds the durable API data model behind the
existing Phase 1 endpoints:

- SQLAlchemy models and Alembic migration for a Postgres-first SaaS schema.
- SQLite-compatible repository adapter for local development and tests only.
- Tables for users, workspaces, memberships, conversations, messages, assistant
  runs, run events, tool calls, artifacts, strategy specs, validation reports,
  review reports, policy findings, and usage ledger rows.
- Opaque public IDs for API-owned records.
- Artifact records store private storage keys internally, while public
  serializers expose artifact IDs and metadata only.
- Existing conversation and message endpoint response shapes remain unchanged.

This slice intentionally defers public artifact endpoints, run execution,
Server-Sent Events, rate limiting, production auth, billing, and UI.

## Phase 3 Streaming Chat

The third implementation slice adds the first streaming run surface without
changing the existing non-stream message API:

- `POST /v1/conversations/{id}/messages?stream=true` creates the user message,
  creates an assistant run, and returns `text/event-stream`.
- `GET /v1/runs/{id}/events` replays persisted authorized run events as SSE
  and supports `Last-Event-ID` resume by sequence or event ID.
- `POST /v1/runs/{id}/cancel` records API-level cancellation status.
- `POST /v1/runs/{id}/retry` creates a new queued run linked by
  `retry_of_run_id`.
- The first stream contract is SSE-first; WebSocket realtime UX remains
  deferred.
- The implementation uses a deterministic simulator only. It emits tool,
  message delta, validation, review, and run completion events without LLM
  calls, runner integration, background workers, Redis, or broker/exchange
  access.
- Streamed token chunks are compacted before persistence: replay exposes one
  final `message.delta` event for the merged assistant text.

This slice intentionally defers real provider orchestration, runner execution,
artifact generation, Redis/background workers, WebSockets, rate limiting,
production auth, billing, and UI.

## Phase 4 Runner Integration

The fourth implementation slice bridges the API server to the existing
deterministic dry-run runner:

- `POST /v1/runs` accepts an authorized conversation ID and strategy spec JSON.
- The API validates the strategy spec, creates an assistant run, persists the
  spec, then calls `run_strategy` with `mode="dry-run"`, `review="parallel"`,
  and runtime tracing enabled.
- Runner artifacts are stored behind opaque artifact IDs for Pine code,
  validation report, review report, manual TradingView checklist, and runtime
  trace summary.
- `GET /v1/artifacts/{id}` returns authorized artifact content without exposing
  filesystem paths, storage keys, or runner output directories.
- Run events record tool start/completion, artifact creation, validation
  completion, review completion, run completion, and runner failure.

This slice intentionally defers live providers, broker or exchange execution,
background workers, Redis, WebSockets, production auth, billing, and UI.

## Phase 5 LLM Orchestration

The fifth implementation slice adds server-owned LLM orchestration without
turning the API into a generic model proxy:

- `POST /v1/conversations/{id}/messages?stream=true&mode=agent` creates a user
  message, creates an assistant run, and streams model/tool events as SSE.
- Existing `stream=true` chat without `mode=agent` keeps the deterministic
  simulator behavior for backward compatibility.
- `POST /v1/runs` accepts optional `mode: "agent"` so the orchestrator can
  derive or refine context before invoking the same deterministic dry-run
  artifact pipeline.
- The default provider adapter is a lazy Responses API wrapper. An Agents SDK
  adapter exists behind the same client interface for later experiments, but
  neither adapter is allowed to execute unrestricted tools directly.
- The server-side tool allowlist is limited to Pine generation, MQL5 design,
  static validation, parallel review, knowledge checks, and knowledge proposal
  artifacts.
- Every tool call is gated by authorization, strict schema validation, policy,
  and in-process budget/rate checks before execution.
- Policy blocks persist `policy_findings` and `policy.blocked` events, while
  usage estimates persist to `usage_ledger`.
- Streamed token deltas are compacted into one persisted final `message.delta`.

This slice intentionally defers live broker or exchange execution, arbitrary
shell/network/filesystem tools, Redis/distributed rate limiting, background
workers, WebSockets, production auth, billing, and UI.

## Phase 6 Trading Guardrails

The sixth implementation slice moves trading safety rules into deterministic
server-side policy checks:

- A shared policy engine evaluates agent output, tool inputs, run strategy
  specs, artifact content, and future market-data payloads.
- Blocked claims include guaranteed profit, risk-free/cannot-lose language,
  live-trading-ready claims, broker integration claims, arbitrary IO actions,
  and compile/backtest success claims without runtime proof.
- Evidence levels distinguish education, strategy ideas, generated artifacts,
  static validation, and manual runtime proof.
- Agent chat blocks unsafe deltas before emitting them, persists
  `policy.blocked`, emits a safe assistant message, and completes the run with
  blocked status.
- `/v1/runs` blocks unsafe but schema-valid strategy specs before calling the
  dry-run runner and returns a blocked run with no artifacts.
- Artifact retrieval rechecks content before returning it to the caller.
- Market-data snapshots are accepted only when source, timestamp, symbol,
  interval, and timezone metadata are present.

This slice intentionally does not add live market data providers, broker or
exchange integration, a public policy endpoint, Redis rate limiting,
WebSockets, production auth, billing, or UI.

## Phase 7 Security And Cost Controls

The seventh implementation slice adds Redis-backed controls around public API
writes and agent execution:

- Redis-backed rate limits cover user, workspace, IP, model, and tool
  dimensions when Redis controls are enabled.
- Protected writes fail closed when Redis controls are enabled but unavailable.
- Per-run balanced budgets cap total tokens, output tokens, tool calls, runtime
  start, and artifact start conditions.
- Optional `Idempotency-Key` support deduplicates message and run creation while
  preserving backward compatibility for clients that do not send the header.
- User-visible errors, events, artifact metadata, and artifact content are
  redacted for secrets, provider payloads, raw prompts, storage keys, and local
  filesystem paths.
- Prompt-injection text from retrieved market, news, or source content remains
  untrusted and cannot override server-side policy or tool allowlists.

This slice intentionally does not add background workers, billing, production
auth, WebSockets, broker or exchange integration, live market-data providers,
or UI.

## Phase 8 Observability And Harness

The eighth implementation slice adds API-run observability and harness evidence
mapping without letting the SaaS server write directly to `harness.db`:

- Assistant runs include `request_id` and `trace_id` alongside conversation and
  run correlation IDs.
- Write requests accept optional `X-Request-Id`; otherwise the server generates
  an opaque `req_...` ID. Runs generate `trace_...` IDs when missing.
- SSE event data and replayed run events expose sanitized request,
  conversation, run, and trace correlation IDs.
- Stage latency is persisted as `observability.stage.completed` run events for
  model, tool, runner, artifact, validation, review, policy, and response
  finalization paths where applicable.
- `GET /v1/runs/{run_id}/observability` returns authorized summaries for
  latency, tool calls, policy findings, usage/token estimates, artifact count,
  and event count.
- A harness-compatible `harness_evidence_summary` artifact is generated from
  API run evidence. It contains stable evidence fields and never exposes raw
  prompts, provider payloads, storage keys, or filesystem paths.
- `POST /v1/feedback` captures human corrections against authorized
  conversations, runs, messages, or artifacts and returns metadata only.
- Trading-chat safety eval coverage includes forbidden profit claims,
  live-trading/broker requests, unsafe retrieved source text, and valid
  educational risk-boundary responses.

This slice intentionally does not add external observability backends,
OpenTelemetry export, production auth, billing, background workers, WebSockets,
broker or exchange integration, live market-data providers, or UI.

## Phase 9 UI Readiness API

The ninth implementation slice prepares the API contract for a future
ChatGPT-like trading UI without building the frontend:

- `GET /v1/conversations/{conversation_id}/messages` returns authorized chat
  history in oldest-first order.
- `GET /v1/conversations/sidebar` returns tenant-scoped sidebar summaries with
  message previews, message counts, latest run IDs, and latest run status.
- `GET /v1/conversations/{conversation_id}/state` returns a UI bootstrap
  payload for conversation, messages, latest run, latest run artifacts, and
  feedback target hints.
- `GET /v1/artifacts/{artifact_id}/preview` returns sanitized bounded previews
  for text, Pine, Markdown, and JSON artifacts while keeping full content
  retrieval on the existing artifact endpoint.
- `GET /v1/runs/{run_id}/progress` streams normalized progress SSE events
  derived from persisted run events, with `Last-Event-ID` resume support.
- `GET /v1/feedback/options` exposes stable rating and correction category
  choices for UI controls.

This slice intentionally does not add frontend UI, WebSockets, production auth,
billing, background workers, broker or exchange integration, live providers, or
live trading.

## Backend Production Readiness Slice

The backend-only production readiness slice adds production-facing API
foundation without building frontend UI or broker/order execution:

- `GET /ready` performs deep readiness checks for the repository, artifact
  store, security controls, LLM provider configuration, and run worker.
  `GET /health` remains a shallow unauthenticated liveness check.
- `/v1/runs` now supports `mode: "live-generation"` to route a validated
  strategy spec through the existing live generation runner path and persist
  generated Pine, validation, review, manual checklist, runtime summary,
  agent-run, live metadata, workflow trace, and quality report artifacts when
  produced.
- Run execution goes through a server worker abstraction. The current
  implementation uses an inline worker so existing API behavior remains
  synchronous; external queue workers remain a deployment follow-up.
- Public responses continue to expose opaque run/artifact IDs and metadata
  only, never local filesystem paths, storage keys, raw provider payloads, or
  secrets.

This slice intentionally skips frontend UI, broker/order execution,
exchange/broker integration, billing, and WebSocket transport.

## Phase

API Initiative Phase 0

## Risk Lane

High-risk. This work introduces a future public API, SaaS tenancy, auth
boundaries, LLM/tool exposure, cost controls, and user-facing trading safety
requirements.

## References

- [API Server Research: Chat-Style Trading Assistant](../research/api-server-chat-trading.md)
- [0024 API-First Chat Server Boundary](../decisions/0024-api-first-chat-server-boundary.md)
