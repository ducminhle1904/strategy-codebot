# 0023 Model Combo Stability Matrix

Date: 2026-06-17

## Status

Accepted

## Context

The live workflow now supports multi-stage model routing, but a passing Gemini-only smoke run does not prove the mixed cheap-profile registry is stable. The cheap OpenRouter mapping currently uses Kimi, MiniMax, and Qwen, so default changes need repeatable evidence across the same prompts and gates.

## Decision

Add a model-combo matrix eval layer on top of `eval live`.

- Keep the current registry defaults unchanged until matrix data justifies a change.
- Use `examples/evals/live-smoke.yaml` as the first-tier gate.
- Run `examples/evals/live-core.yaml` only for combos that pass smoke when `--run-full` is enabled.
- Record results in `model-matrix-report.json` with pass rate, static validation rate, repair average, blocking failure classes, artifact-missing counts, and the recommended accepted combo.
- Skip credential-gated combos instead of treating missing local secrets as model-quality failures.
- Route live parallel reviewers through the same live options as the generation workflow, so OpenRouter combo tests do not silently depend on OpenAI credentials.
- Count static-validation pass rate only for final-artifact cases whose expected statuses include `pass`; expected `manual_required` boundary cases are still pass/fail gated by their declared expectation.

## Consequences

Model routing changes can now be based on repeatable artifacts instead of one-off live runs. Gemini-only remains a baseline, while Kimi/MiniMax/Qwen hybrids must pass the same matrix gates before being promoted in `provider_model_mappings.openrouter.cheap_quality`.
