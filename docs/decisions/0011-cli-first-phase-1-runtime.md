# 0011 - CLI-First Phase 1 Runtime

## Status

Accepted

## Context

Phase 1 needs to prove that `strategy-codebot` is useful without adding web/API surface area or live-trading integrations.

## Decision

Implement Phase 1 as a Python CLI with dry-run support, optional LiteLLM live mode, static Pine validation, MQL5 runner design output, and repository-harness trace integration.

## Consequences

- The MVP can run locally and in tests without API keys.
- Future API or web surfaces should wrap the CLI/runtime behavior instead of redefining contracts.
- MQL5 remains design/report-only until a Windows/MetaTrader runner phase.

