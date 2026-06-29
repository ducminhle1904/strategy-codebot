# Backtest Preview Workflow

## Product Boundary

Backtest preview is local sandbox evidence for review. It is not TradingView proof, broker proof, live-trading evidence, or a profitability claim. Use `review-only evidence`, `local preview`, and `sandbox preview` wording.

The model may prepare a backtest plan, queue a local preview, fetch bounded summary metrics, fetch bounded indexed trades, and fetch a downsampled equity curve sample. The model must not claim that a strategy is proven profitable, safe, or live-ready because a preview completed.

## Tool Use

Use `create_backtest_plan` when the user wants to plan a local preview from Pine Script source or strategy context.

Use `run_backtest_preview` when the user clearly asks to simulate, paper test, preview performance, run a local backtest, chạy thử, thử hiệu quả, or chay thu. This queues sandbox work; do not imply immediate completion.

Use `get_backtest_summary` for bounded DB-indexed report metrics after a completed preview. Use `query_backtest_trades` for explicit trade row requests such as first N trades, latest trades, top winners, or top losers. Use `get_equity_curve_sample` for downsampled equity curve review instead of loading raw artifacts.

## Explanation Rules

State preview assumptions such as symbol, timeframe, date range, initial capital, fees, slippage, and sample size when available. If fields are missing or `N/A`, say more evidence is needed instead of presenting the preview as conclusive.

Internal preview artifacts such as raw trades JSON, equity curve JSON, source bundles, runner manifests, and cache manifests are implementation evidence. Chat should summarize the bounded report or table rather than directing users to inspect raw internal files.

## Retrieval Cues

Use this guidance for prompts containing: backtest preview, local preview, sandbox preview, run backtest, simulate strategy, paper test, preview performance, summarize backtest, trades table, first trades, top winners, top losers, equity curve, chạy thử, thử hiệu quả, chay thu.
