# Risk Gate And Order Intent Workflow

## Product Boundary

Proposed order intent and risk gate outputs are review-only controls. They are not broker orders, live trading instructions, approval to execute, or evidence that execution is safe.

The model may draft a proposed order intent for review. Deterministic risk gates own sizing, stops, targets, exposure, leverage, stale-signal checks, venue assumptions, and portfolio heat checks. The model must not bypass or self-approve these gates.

## Proposed Order Intent

Use `create_proposed_intent` when the user wants a structured intent draft from the current setup. The result should be described as a draft intent that requires review. It should not be described as an order sent to a broker or a paper/live execution.

If required assumptions are missing, ask for the missing setup instead of inventing symbol, side, size, stop, target, account, venue, or leverage.

## Risk Gate

Use `run_risk_gate` when the setup needs deterministic checks before any downstream review. Explain failures as blockers or required changes, not as optional suggestions. A passing risk gate is still review-only and does not authorize broker execution.

## Retrieval Cues

Use this guidance for prompts containing: proposed order intent, order intent, create intent, risk gate, run risk gate, sizing, stop, target, exposure, leverage, stale signal, venue assumptions, portfolio heat, execution approval, approve trade, vào lệnh.
