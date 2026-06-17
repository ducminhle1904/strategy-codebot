# 0018 - Cheap Router Model Mappings

## Status

Accepted

## Context

OpenRouter makes it practical to test cheaper models without changing live-generation code. The project needs explicit mappings so agents can choose low-cost candidates intentionally instead of guessing provider-specific model IDs.

External model pages identify Kimi, MiniMax, and Qwen models as cheap or free candidates for reasoning, coding, and long-context work. These are not trusted defaults for production artifacts; they are candidates for cost-controlled eval runs.

## Decision

Record cheap-quality mappings in `configs/model-registry.example.yaml` under `provider_model_mappings`.

Use Kimi K2 Thinking as the low-cost strategy-reasoning candidate, Qwen3 Coder for low-cost code probes, and MiniMax M3 for long-context worker trials through OpenRouter model IDs.

## Consequences

Users can pass mapped models with `--model` for cheap live/eval runs. Every cheap route must still pass strict JSON schema validation, static Pine validation, safety policy checks, and the live eval suite before its artifacts are considered reviewable.
