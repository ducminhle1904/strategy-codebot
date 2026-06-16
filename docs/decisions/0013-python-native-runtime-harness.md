# 0013 - Python-Native Runtime Harness

## Status

Accepted

## Context

Phase 2 can generate, validate, and review strategy artifacts, but runtime tool calls are not yet recorded as ordered contract events. The project considered external tool-call harnesses, but Phase 3 should keep the CLI Python-native.

## Decision

Implement the Phase 3 tool/runtime harness inside `strategy-codebot` using Python modules, local JSONL traces, and machine-readable tool contracts.

## Consequences

The CLI can produce runtime-level evidence without adding a Node sidecar or changing the existing Python runtime. Repository-level evidence remains owned by `repository-harness`; runtime traces explain how CLI artifacts were produced.
