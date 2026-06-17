# 0017 - Model Registry Refresh For Trading Strategy Knowledge

## Status

Accepted

## Context

External finance and trading benchmarks disagree on a single best model. Recent trading-focused work shows that static finance QA strength does not automatically translate into profitable sequential trading decisions, while finance workflow evaluations favor different models for reasoning, extraction, summarization, and risk critique.

The registry had generic or aging model identifiers such as `anthropic/claude-sonnet` and `google/gemini-pro`, and it lagged the current OpenAI frontier model line. Live mode now needs model choices that prioritize trading-strategy reasoning and code artifact generation while preserving router-backed alternatives for comparison.

## Decision

Use OpenAI GPT-5.5 as the default model for strategy reasoning, live Pine generation, orchestration, and risk review. GPT-5.5 is the first-choice model because the project needs complex professional reasoning, strategy specification synthesis, and structured code artifact generation more than low-latency chat.

Use Claude Sonnet 4.6 for MQL5 and code-generation fallback paths, Claude Opus 4.8 through direct Anthropic or OpenRouter for deep alternate reasoning, OpenRouter GPT-5.5 for gateway redundancy, and OpenRouter Gemini 3.1 Pro Preview for independent critic/risk perspective.

## Consequences

Model selection remains registry-driven and can be evaluated through `strategy-codebot eval live`. The registry is not a profitability claim and does not choose models for live trading decisions; it only chooses LLMs for generating and reviewing code artifacts under the existing safety boundary.
