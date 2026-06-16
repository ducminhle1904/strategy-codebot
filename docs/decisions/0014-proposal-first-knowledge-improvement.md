# 0014 - Proposal-First Knowledge Improvement

## Status

Accepted

## Context

Phase 3 records runtime evidence, but the knowledge layer still only checks the source registry. The project needs a loop that can notice source freshness, source diffs, validation warnings, review findings, and runtime failures without silently changing canonical trading docs.

## Decision

Implement Phase 4 as a proposal-first knowledge loop. The CLI creates snapshots, diffs, audits, and proposals, but it does not mutate canonical docs such as `docs/trading/*.md`.

## Consequences

Official source changes and repeated run evidence become reviewable artifacts. Human approval is still required before any proposal is promoted into durable docs, schemas, prompts, validators, or registry changes.
