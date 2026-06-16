# 0002 - LangGraph And LiteLLM Default Stack

## Status

Accepted

## Context

The product needs multiple specialist agents, parallel review, model-provider routing, and a future knowledge-curation loop.

## Decision

Use Python LangGraph as the default orchestration runtime and LiteLLM-compatible model names as the default provider gateway abstraction.

## Consequences

- Agent roles are designed as graph nodes or bounded callable workers.
- Model choices stay outside business logic.
- Future implementation can route OpenAI, Anthropic, Google, local, or other providers through one registry.

