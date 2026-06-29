# Model Workflow Boundaries

## Artifact Exposure Policy

The model should distinguish user-facing artifacts from internal evidence artifacts.

User-facing artifacts include Pine source, MQL5 source, review reports, backtest dashboards, backtest reports, variant comparison reports, proposed order intent reports, risk gate reports, robustness reports, and manual checklists.

Internal artifacts include raw JSON, raw validation reports, compile reports, raw trades JSON, backtest plans, run metadata, source bundles, adapter source, cache manifests, runner manifests, trace summaries, live metadata, and harness evidence internals. Summarize these internally when useful, but do not present them as artifacts the user needs to open.

## Validation Repair

Use static validation before preview, risk review, or robustness review. If validation fails, repair blockers first. Distinguish static validation, compile report, validation report, and user-facing repair summary.

The model should not proceed as if preview evidence is valid when validation blockers remain. If a report is raw/internal, summarize the blocker and next repair action instead of exposing the raw file.

## Knowledge Proposal

Knowledge proposals are human-review artifacts. They can suggest affected sources, affected docs, evidence references, risk level, recommendations, and next actions. They must not auto-promote content into canonical docs or silently change the knowledge base.

Use knowledge proposals after failed runs, repeated lessons, source drift, or review findings that should become durable guidance.

## Market Research

Market research is for current external facts and must use sources. It is separate from current internal preview evidence. When the user asks for current market context, providers, docs, models, pricing, releases, or versions, fetch and cite external context before answering.

Do not treat `current preview`, `latest backtest`, or `current run` as market news. Those are internal context requests and should use available internal tools or artifacts.

## Retrieval Cues

Use this guidance for prompts containing: artifact exposure, user-facing artifact, internal artifact, raw JSON, compile report, validation report, trades JSON, backtest plan, adapter source, repair validation, static validation blockers, knowledge proposal, auto promote, market research, current market context, sources, citations, current preview evidence.
