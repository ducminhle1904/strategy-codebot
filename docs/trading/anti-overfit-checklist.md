# Anti-Overfit Checklist

Use this checklist when reviewing strategy logic or backtest output.

## Warning Signs

- Too many optimized parameters for too little data.
- Strategy only works on one symbol or narrow date range.
- Unrealistic spread, commission, or slippage.
- Entry/exit rules tuned to isolated historical events.
- No out-of-sample or walk-forward review.
- Extremely high win rate with poor risk explanation.

## Required Review Questions

- What market regime is this strategy designed for?
- What causes the strategy to fail?
- Which assumptions are platform-specific?
- Are risk controls explicit and testable?
- Can a simpler version explain most of the result?

