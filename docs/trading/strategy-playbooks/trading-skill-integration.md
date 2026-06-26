# Trading Skill Integration Playbook

## Boundary

This playbook turns reviewed trading-skill patterns into decision-support checklists for Strategy Codebot. It does not promote raw external skill text, broker execution, live-ready certification, or profitability claims.

## Curated Skill Mapping

- backtest-expert: prefer strategies that break less under stress, not strategies with the highest preview profit. Use sample size, slippage and fee stress, out-of-sample windows, and parameter robustness before treating a preview as useful evidence.
- edge-strategy-reviewer: review edge plausibility, overfit risk, sample adequacy, regime dependency, exit calibration, risk concentration, execution realism, and invalidation quality.
- technical-analyst: require a readable market thesis with trend or range context, support/resistance or structure, volume/liquidity assumption when relevant, pattern trigger, scenario expectation, target, and invalidation. Do not require chart images in this workflow.
- position-sizer: survival-first sizing. Prefer fixed fractional risk such as 1% per trade, cap single-trade risk around 2%, consider portfolio heat, and apply the strictest applicable risk constraint.
- trade-memory-loop: convert outcomes into reviewed lesson candidates only after evidence, dedupe, safety checks, and approval.

## PineForge Preview Review

- Treat PineForge output as local Pine preview evidence only.
- Preserve engine/runtime version, config hash, code hash, data source, fee/slippage assumptions, sample size, and warnings in reports.
- A preview with strong profit metrics but low sample size, zero costs, high drawdown, fragile parameters, or no out-of-sample split should require manual review or rejection.
- Never describe a preview as profitable, safe, certified, or live-ready.

## Promotion Decision Checklist

- Reject when static validation fails, sizing is unsafe, no trades close, risk is unbounded, or the strategy contains guaranteed-profit/live-ready claims.
- Manual review when evidence is incomplete, sample size is low, fee/slippage stress is fragile, or regime assumptions are unclear.
- Research candidate only when static validation passes, preview assumptions are explicit, robustness checks do not raise blockers, and the report stays non-advisory.

## Future Bot Boundary

- AI may propose a structured order intent, but deterministic risk gates must own sizing, stops, exposure, leverage, stale-signal checks, venue capability, and portfolio heat.
- Paper or live execution remains blocked until a later explicit product decision.
