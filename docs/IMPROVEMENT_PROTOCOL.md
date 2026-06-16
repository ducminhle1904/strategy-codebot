# Improvement Protocol

The knowledge base and harness should improve from observed failures.

## Triggers

- Repeated strategy-generation mistakes.
- Repeated validation failures.
- Official docs changed.
- Critic agent flags missing policy.
- Human review finds unclear or stale docs.

## Flow

1. Record the issue as a backlog or story item.
2. Identify whether the fix belongs in docs, schema, prompt, validator, or source registry.
3. Add or update an eval case when practical.
4. Promote durable knowledge into repo docs rather than leaving it only in RAG.
5. Record a decision if the fix changes architecture or safety policy.

