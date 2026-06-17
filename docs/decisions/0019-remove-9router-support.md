# 0019 - Remove 9Router Support

## Status

Accepted

## Context

The live LLM path needs a small, production-readable provider surface. 9Router runs as a local OpenAI-compatible router and adds local service availability, dashboard provider configuration, and custom model ID ambiguity to the readiness path.

## Decision

Remove 9Router as a supported provider from live generation, model registry fallbacks, cheap model mappings, README examples, and live tests.

Supported live routes remain direct LiteLLM providers plus OpenRouter overrides.

## Consequences

Live eval and production-readiness work no longer depends on a local 9Router service. OpenRouter remains the router path for cheaper model experiments.
