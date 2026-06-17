# 0016 - Production-Ready Live LLM Eval Harness

## Status

Accepted

## Context

Live mode previously proved that LiteLLM could be called, but it relied on raw JSON parsing and had no repeatable live-readiness gate. The product needs a stronger provider flow without changing the safety boundary: generated artifacts remain reviewable code outputs, not live-trading automation.

## Decision

Use OpenAI as the default `pine_specialist` provider through LiteLLM, with Anthropic and OpenRouter as supported fallbacks or explicit model overrides. OpenRouter uses LiteLLM's native `openrouter/...` provider prefix.

Require strict JSON Schema output for live generation, normalize live provider failures, and keep live generation under runtime policy enforcement.

Add a gated `strategy-codebot eval live` command backed by a 20+ case suite. The eval harness records per-case artifacts, latency/usage metadata, and full raw provider responses when requested. CI remains mocked; real live evals require explicit provider credentials.

## Consequences

Live mode has a production-readiness workflow that can be run manually before trusting generated artifacts for review. The repository still does not claim TradingView or MetaTrader runtime validation, broker connectivity, live execution, or profitability.
