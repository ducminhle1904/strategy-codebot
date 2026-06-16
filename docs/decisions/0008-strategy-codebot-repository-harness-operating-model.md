# 0008 - Strategy Codebot Repository Harness Operating Model

## Status

Accepted

## Context

`strategy-codebot` needs durable context for agent work: product boundaries, target-platform assumptions, validation proof, and decisions.

## Decision

Use the `repository-harness` operating model as the repo-level scaffold.

## Consequences

- `AGENTS.md` remains a short map.
- Detailed knowledge lives under `docs/`.
- Stories, decisions, traces, and validation proof are first-class artifacts.
