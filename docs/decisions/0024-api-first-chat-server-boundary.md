# 0024 API-First Chat Server Boundary

Date: 2026-06-17

## Status

Accepted

## Context

Strategy Codebot is preparing for a future ChatGPT-like UI focused on crypto
and forex strategy workflows. The current production surface is still the CLI:
it generates, validates, reviews, and improves reviewable strategy artifacts.

Adding a public API and SaaS UI introduces new risks that the CLI does not have:
tenant isolation, object-level authorization, cost controls, streaming state,
LLM tool exposure, and user-facing trading claims. The boundary must be decided
before implementation so the first API slice does not become a generic LLM
proxy or an accidental trading execution surface.

## Decision

The future API server will be a domain workflow server for Strategy Codebot, not
a generic LLM proxy.

The default implementation direction is:

- Python ASGI/FastAPI-style server owned by this repo.
- PostgreSQL for durable SaaS data and tenant-owned records.
- Redis for distributed rate limits, stream coordination, short-lived locks, and
  job state.
- Background worker for long strategy generation, validation, review, eval, and
  knowledge jobs.
- Object or artifact storage later, once generated artifacts outgrow local
  development storage.
- SaaS day-one tenancy: every public resource is designed around a user and
  workspace authorization boundary from the first implementation.
- `/v1` as the public API namespace.
- Opaque public IDs only; responses must not expose filesystem paths, database
  internals, or raw provider payload locations.
- First public resources: conversations, messages, runs, run events, artifacts,
  and feedback.
- Server-Sent Events as the first streaming transport for chat and run progress.
  WebSocket support is deferred until bidirectional realtime UX is required.
- Server-owned LLM orchestration. Tools execute only inside the API server after
  authorization, schema validation, policy checks, and budget checks.

The first SSE event contract is:

- `message.delta`
- `tool.started`
- `tool.completed`
- `validation.completed`
- `review.completed`
- `artifact.created`
- `policy.blocked`
- `run.completed`
- `run.failed`

Every persisted API resource must include tenant and trace metadata:

- `owner_user_id`
- `workspace_id`
- `created_at`
- `updated_at`
- trace or run correlation fields appropriate to the resource

Market data is not part of the first API slice. If later added, every market
data snapshot must carry source, retrieval timestamp, symbol, interval,
timezone, and provider metadata.

The existing safety boundary remains active for the API product:

- No live order placement.
- No broker or exchange trade execution.
- No profitability guarantees.
- No platform runtime-success, compile-success, or backtest-success claims
  without external evidence.
- No hidden model or provider decisions in generated reports.

## Consequences

The API phase starts with multi-tenant SaaS constraints instead of a local-only
prototype. This makes Phase 1 heavier, but prevents early API contracts from
leaking local filesystem assumptions, single-user state, or unsafe tool access.

The API server can reuse existing runner, validation, review, runtime harness,
and knowledge modules as internal services. Those modules must not be exposed as
public arbitrary tool execution.

The CLI remains the current production surface until a future API phase is
implemented and verified. This decision records the target boundary only; it
does not add server code, dependencies, database migrations, live provider
orchestration, billing, or UI.
