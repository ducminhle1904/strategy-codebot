# Agent Workflow Patterns Fit

Date: 2026-06-29

Sources:

- Anthropic Engineering, "Building effective agents", published 2024-12-19:
  https://www.anthropic.com/engineering/building-effective-agents
- Claude Agent SDK agent loop docs:
  https://code.claude.com/docs/en/agent-sdk/agent-loop.md
- Local repo docs: `docs/product/strategy-codebot.md`, `docs/ARCHITECTURE.md`,
  `docs/HARNESS.md`, `docs/FEATURE_INTAKE.md`, `docs/TEST_MATRIX.md`,
  `docs/decisions/0012-phase-2-parallel-review-runtime.md`,
  `docs/decisions/0013-python-native-runtime-harness.md`.

## Executive Summary

Anthropic's recommendation is workflow-first: start with simple prompts and
evaluation, add multi-step workflows when they improve measured outcomes, and
reserve autonomous agents for open-ended tasks where the required steps cannot
be predicted up front.

That maps well to `strategy-codebot`. The project should keep its current
domain workflow model: backend-owned orchestration, deterministic validation,
typed artifacts, policy gates, and trace evidence. Agent loops are useful only
when bounded by those surfaces.

The highest-fit patterns are:

1. Prompt chaining for strategy artifact generation.
2. Routing for intent, model, tool, and workflow selection.
3. Evaluator-optimizer loops for validation, repair, and review.
4. Parallelization for review, guardrails, and evals.

The lowest-fit pattern for production core behavior is a fully autonomous agent
loop. It is useful for research/scout tasks and bounded repair, but it should
not replace the orchestrator, validator, ToolHarness, artifact store, or
repository harness.

## Current Repo Baseline

Product boundary:

- `strategy-codebot` turns trading strategy ideas into reviewable Pine Script
  v6 and MQL5 artifacts.
- It is not a trading advisor or live trading system.

Runtime boundary:

- The orchestrator owns state transitions.
- Specialists own bounded reviews or code-generation tasks.
- The validator owns proof collection and normalized validation reports.
- The harness auditor owns trace and decision evidence.

Relevant current implementation:

- `src/strategy_codebot/server/llm_orchestrator.py` already has response
  intents, semantic actions, workflow events, tool-call budgets, policy checks,
  and typed run events.
- `src/strategy_codebot/server/llm_clients.py` defines a provider-neutral
  `LLMClient` stream interface with message, tool-call, source, and usage
  events. `AgentsClient` exists but intentionally raises that the Agents SDK
  adapter is not enabled for tool execution in Phase 5.
- `src/strategy_codebot/tool_runtime.py` records ordered tool events and blocks
  `broker_write` and `destructive` risk tiers in enforce mode.
- `src/strategy_codebot/server/workflow_registry.py` lets the model propose
  workflow state, but the backend validates component names, steps, fields,
  actions, and bot start eligibility.
- `docs/decisions/0012-phase-2-parallel-review-runtime.md` already implements
  review-first parallelism with `trading_analyst`, `pine_specialist`,
  `risk_reviewer`, and `critic`.

## Pattern Fit Matrix

| Pattern | Fit | Best Use In This Repo | Main Benefit | Main Guardrail |
| --- | --- | --- | --- | --- |
| Augmented LLM | High | Retrieval, knowledge context, read-only market/document context, tool-backed chat actions | Better answers with domain context and tool feedback | Tool interface must stay documented, typed, tested, and policy-gated |
| Prompt chaining | Very high | Strategy idea -> strategy brief -> schema-valid spec -> Pine/MQL5 artifact -> validation -> review -> repair/final | Makes each step smaller, easier to validate, and easier to debug | Add gates between steps; do not let later stages mutate earlier intent silently |
| Routing | Very high | Intent classification, domain-scope checks, model-stage selection, cost profile routing, workflow/action routing | Keeps prompts specialized and reduces cost/latency for easy cases | Classifier must fail safe to clarification or conservative path |
| Parallelization | High | Multi-role review, policy screening alongside generation, eval dimensions, multi-source research | More coverage and fail-soft confidence without blocking on one reviewer | Aggregate programmatically; never treat model agreement as platform proof |
| Orchestrator-workers | Medium-high | Variable research/scouting, complex implementation planning, multi-source evidence gathering, optional code-maintenance assistant | Handles unknown subtasks better than fixed parallel branches | Backend still owns state transitions and output schema validation |
| Evaluator-optimizer | Very high | Static validation -> repair loop, balanced review -> repair, risk/robustness critique -> revised artifact, bot proposal review | Iterative improvement when criteria are explicit and measurable | Hard iteration limits; validator remains authoritative |
| Autonomous agent loop | Low for production core, medium for bounded internal tasks | Read-only research agent, bounded repo investigation, sandboxed maintenance assistant, constrained repair loop | Useful when steps are unknown and environment feedback matters | No direct uncontrolled Bash/Edit/Write; must go through ToolHarness, policy, budgets, checkpoints, and trace |

## Pattern Notes

### Augmented LLM

Anthropic's base block is an LLM with retrieval, tools, and memory. This matches
the current repo direction. The project already has knowledge context, tool
definitions, market-data and artifact tools, and typed LLM events.

Best next use:

- Improve tool descriptions and argument schemas in `llm_tools`.
- Make every tool output compact and model-friendly.
- Add tests that verify model-facing tool definitions are easy to call and
  cannot imply live trading or profitability proof.

### Prompt Chaining

This is the strongest fit for production strategy generation. Trading strategy
artifact generation has natural gates:

1. Interpret user idea.
2. Normalize to strategy brief and policy-safe assumptions.
3. Produce schema-valid `StrategySpec`.
4. Generate Pine/MQL5 artifact from the spec.
5. Run static/manual-proof validation.
6. Run review.
7. Repair only validation or review findings.
8. Emit final artifacts and trace evidence.

Benefits:

- Better reliability than one large prompt.
- Easier replay and debugging from artifacts.
- Clear user-visible progress.
- Safer policy enforcement between stages.

### Routing

Routing is already visible in response intents, semantic actions, and model-stage
routing. It is especially useful because `strategy-codebot` handles mixed chat:
general help, docs research, strategy building, artifact generation, backtest
preview, market snapshots, and bot proposal/status flows.

Benefits:

- Cheaper models can handle easy or routine cases.
- Specialized prompts reduce prompt collisions.
- Dangerous or out-of-domain requests can be blocked early.
- UI can show the selected workflow instead of a generic chat response.

### Parallelization

The repo already uses this pattern for Phase 2 review. It should stay focused on
independent review dimensions and evals, not primary generation.

Good uses:

- Run `trading_analyst`, `pine_specialist`, `risk_reviewer`, and `critic`
  independently.
- Run policy/risk screening in parallel with artifact generation.
- Run eval dimensions independently: correctness, safety, schema, UX,
  backtest-preview compatibility.
- Run multi-source research where each source family is independent.

Benefits:

- More coverage.
- Better partial failure behavior.
- Faster wall-clock time when calls are independent.

Guardrail:

- Parallel model agreement is not proof. Static validation, platform proof,
  and artifact evidence remain authoritative.

### Orchestrator-workers

This is useful when the subtasks are not predictable. It is a better fit for
research and implementation planning than for the core strategy-generation
pipeline, where fixed gates are safer.

Good uses:

- "Scout both FE and BE" style investigations.
- Multi-source market or docs research.
- Migration planning where touched files are not known up front.
- Large product-quality audits.

Benefits:

- Handles variable scope without hardcoding every branch.
- Can delegate bounded subtasks and synthesize concise findings.

Guardrail:

- The orchestrator should not be an unconstrained model that directly mutates
  runtime state. It should propose subtasks, and backend code should validate
  task boundaries, tool access, and outputs.

### Evaluator-optimizer

This is the most valuable next pattern to strengthen. The repo already has
validation and repair concepts, so the missing value is not architecture; it is
making the loop explicit, measured, and bounded.

Good uses:

- Pine static validation failure -> targeted repair -> revalidate.
- Review required fixes -> targeted repair -> re-review.
- Bot proposal completeness/risk review -> revise proposal -> require human
  confirm-start.
- Knowledge proposal review -> replay recommendations -> human-approved update.

Benefits:

- Converts subjective "make it better" into bounded, evidence-driven loops.
- Improves quality while preserving original strategy intent.
- Gives clear stop conditions: pass, fail, manual_required, max iterations.

### Autonomous Agent Loop

An agent loop is a model-driven loop where the model plans, calls tools, observes
results, and decides the next action until done or stopped. It is useful when
steps are unknown. It is not a good replacement for this project's production
core.

Good uses:

- Read-only research/scout agent.
- Repo investigation agent that can search, inspect tests, and summarize.
- Bounded repair assistant that can propose patches but must route execution
  through existing validation and review.
- Internal maintenance helper for docs and source-registry drift.

Poor uses:

- Live trading.
- Broker execution.
- Paper bot start without explicit backend eligibility and user confirmation.
- Direct arbitrary filesystem or shell access in production user flows.
- Replacing validation, artifact evidence, or repository trace requirements.

Minimum safe shape:

- Expose only registry-backed tools.
- Normalize all tool calls into existing `LLMClientEvent` and run-event shapes.
- Enforce `RunBudget` and max iterations.
- Require human checkpoint for high-risk actions.
- Record every tool call with `ToolHarness`.
- Keep policy engine and workflow registry as backend authority.

## Recommended Priority

1. Strengthen prompt chaining gates and event names for the main strategy
   workflow.
2. Expand evaluator-optimizer loops for validation/repair and review/repair.
3. Continue routing work for intent, model stage, workflow action, and cost
   policy.
4. Keep parallel review and broaden it into eval dimensions where useful.
5. Prototype an agent loop only as a bounded read-only research/scout adapter.
6. Do not enable a general Agents SDK or autonomous tool-execution adapter until
   there is a story, decision record, risk review, test matrix update, and
   explicit ToolHarness integration.

## Practical Benefit Summary

For users:

- More predictable strategy artifact quality.
- Clearer progress: idea, spec, code, validation, review, repair, artifact.
- Fewer generic chat failures.
- Better explanations when the system blocks or asks for clarification.

For engineering:

- Easier debugging through stage outputs and traces.
- Lower provider cost through routing and smaller prompts.
- Better testability because each stage has a contract.
- Safer growth path for agentic behavior without losing product boundaries.

For safety:

- Live trading and broker execution remain outside scope.
- Model outputs do not become proof.
- High-risk tool usage remains blocked or human-gated.
- All meaningful actions remain traceable through runtime and repository
  evidence.
