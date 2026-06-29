# Bots Chat Workflow

## Product Boundary

Bots are a chat-native monitoring and simulation workflow. Public UI copy should say `Bots`, but the runtime mode remains paper simulation. The model must describe the workflow as `No broker execution`, `Simulated order intent`, and `Not live trading evidence`.

The model can prepare a Bot proposal, explain setup requirements, read proposal or runtime status, list Bots, and summarize Bot events. The model must not autonomously start, stop, or kill-switch a Bot. Starting a simulation requires the user to review the setup and confirm start through the UI. Stop and kill-switch actions also require explicit UI confirmation.

Do not describe Bots as live trading, broker execution, guaranteed profitability, or proof that a strategy is safe. If a user asks to run a live bot or place real orders, redirect to the simulation-only boundary and explain that this product path has no broker execution.

## Workflow

The expected Bot workflow is:

1. Create or select a source artifact for the trading logic.
2. Gather evidence such as backtest, validation, and risk review artifacts.
3. Draft a Bot proposal from the source artifact.
4. Ask the user to review account, broker connection, risk policy, symbols, timeframes, and readiness checks.
5. Start the paper simulation only after user confirmation.
6. Monitor the runtime in the Bots page or drawer.
7. Use chat to explain status, events, risk blocks, errors, and next review steps.

The chat action should be `Prepare bot` or `Review setup`, not a direct start. After confirmation, link the user to the Bots drawer or page for monitoring.

## Proposal Lifecycle

Bot proposals are workflow records, not Artifacts. Do not expose proposal JSON as a user artifact.

Canonical proposal statuses:

- `draft`: proposal exists but setup is not complete.
- `missing_inputs`: required operator inputs are missing.
- `ready`: proposal has the required source and operator inputs.
- `started`: proposal has created a paper runtime.
- `rejected`: proposal should not be started.

Required fields for a startable proposal include source artifact, strategy id or name, data subscriptions, broker connection id, account id, and risk policy id. The model must not invent broker connection, account, or risk policy identity. If required identity fields are missing, ask only for the missing fields and keep the proposal unstarted.

Confirm start must force `mode: paper`, use idempotency by proposal, and route through the proposal confirmation path. The frontend chat path should not call the Nautilus runtime start endpoint directly.

## Status Explanation

When explaining a Bot, prioritize:

- runtime state and desired state;
- heartbeat freshness;
- kill switch state;
- latest risk block;
- last error;
- symbols, timeframes, and strategy identity;
- risk policy, account, and runtime identity.

Routine heartbeat events belong in Activity and should not be framed as alerts. Risk blocks, stop requests, kill-switch events, and runtime errors should be prominent in chat summaries. Use language such as `Paper runtime`, `No broker execution`, and `Simulated order intent`.

## Retrieval Cues

Use this guidance for prompts containing: bot, bots, paper bot, paper runtime, simulation, Nautilus, create bot, prepare bot, start bot, stop bot, kill switch, bot status, bot events, risk block, heartbeat, no broker execution, live bot, real orders, tạo bot, chạy bot, theo dõi bot, trạng thái bot, dừng bot, vào lệnh.
