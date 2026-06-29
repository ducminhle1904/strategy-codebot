# Variant And Robustness Workflow

## Product Boundary

Variant Lab and Robustness Report are review-only workflows built on local backtest preview evidence. They compare assumptions and flag risk; they do not prove profitability, production readiness, or live-trading suitability.

When the user says queue variant lab, compare variants, build robustness report, review sample size, or review slippage, retrieve this workflow before generic strategy playbooks. Map those requests to `run_backtest_variant_lab` and `build_robustness_report`.

## Variant Lab

Use `run_backtest_variant_lab` when the user asks to compare parameter variants, optimization ideas, or alternative setups under the same preview assumptions. Keep parent and child runs tied to a shared source, shared data window, shared fees, and shared slippage assumptions.

Explain Variant Lab results as comparative evidence. Prefer language such as `candidate`, `variant`, `same assumptions`, and `needs review`. Do not recommend a variant solely because it has the highest net profit or Sharpe.

## Robustness Report

Use `build_robustness_report` after a completed preview report when the user asks about robustness, suspicious metrics, overfit risk, sample quality, fees, slippage, drawdown, OOS concerns, or whether evidence is enough.

Prominent checks include trade count, win rate, profit factor, net profit, max drawdown, fees, slippage, sample days, loss streaks, top winners, top losers, and warning counts. If metrics are absent or `N/A`, say the report needs more evidence and avoid over-interpreting it.

## Retrieval Cues

Use this guidance for prompts containing: variant lab, compare variants, parameter variants, optimize parameters, robustness report, robust enough, suspicious metrics, overfit, OOS, sample size, sample days, fees, slippage, drawdown, loss streak, profit factor.
