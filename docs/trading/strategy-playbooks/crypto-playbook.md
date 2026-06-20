# Crypto Strategy Playbook

## Boundary

This playbook is decision support for strategy design and review. It must not be used to claim that a crypto strategy is profitable, safe, or ready for live trading.

## Market Regimes

- Trend expansion: favor breakout or pullback logic only after defining the trend filter, invalidation level, and maximum adverse move.
- Range compression: favor mean-reversion or liquidity sweep ideas only when range highs/lows are observable and stops are outside the swept extreme.
- Volatility shock: avoid assuming normal spread, fill, or slippage behavior after liquidation cascades, exchange incidents, or major news.
- Low-liquidity chop: reduce confidence in candle patterns when volume is thin, wicks are frequent, or the instrument trades differently across venues.

## Crypto-Specific Checks

- Liquidity: confirm the symbol has enough volume for the intended timeframe and position size.
- Perpetuals: account for funding windows, premium/discount behavior, and liquidation-driven wicks before treating a move as clean price action.
- Exchange fragmentation: avoid assuming one exchange candle represents the full market for thin assets.
- Weekend behavior: crypto trades continuously, so session filters should be explicit rather than copied from forex or equities.
- Stablecoin and venue risk: do not treat all quote assets, exchanges, or liquidity pools as interchangeable.

## Strategy Fit

- Momentum and breakout strategies fit sustained trend or volatility expansion regimes, but need false-break filters and slippage assumptions.
- Mean-reversion strategies fit bounded ranges, but need regime filters that stop trading during trend expansion.
- Liquidity sweep strategies should define the prior swing, sweep threshold, reclaim candle, stop beyond the sweep, and bounded target.
- Indicator strategies should explain why the indicator is useful for the market state instead of stacking signals to overfit history.

## Anti-Patterns

- Optimizing many parameters on a single bull or bear market window.
- Ignoring fees, spread, slippage, funding, and order execution assumptions.
- Treating a backtest on one exchange as proof that the strategy generalizes.
- Claiming live readiness without separate risk controls, execution testing, and approval.

## Timeframe Guidance

### Scalping

- When it works: high-liquidity pairs, tight spread, stable venue, and a setup with explicit invalidation.
- When it fails: thin books, exchange outages, fast liquidation wicks, and fee-heavy targets.
- Required assumptions: venue, fee tier, maker/taker behavior, max spread, and minimum volume.
- Implementation traps: candle-close logic may miss intrabar wick risk; small targets are dominated by costs.
- Validation checklist: include fees, slippage, spread proxy, and a maximum adverse excursion review.

### Intraday

- When it works: clear volatility regime, enough volume, and a defined session or funding window.
- When it fails: chop after a volatility event or false continuation after a liquidation cascade.
- Required assumptions: timeframe, exchange, session filter, and volatility filter.
- Implementation traps: copying forex session logic into 24/7 crypto without defining weekend behavior.
- Validation checklist: split tests across weekday/weekend and high/low volatility windows.

### Swing

- When it works: broader trend structure and risk per trade are defined before entries.
- When it fails: altcoin beta overwhelms the signal or funding/venue risk changes during the hold.
- Required assumptions: holding period, stop model, funding exposure, and position sizing.
- Implementation traps: treating funding and overnight gap-like liquidation events as irrelevant.
- Validation checklist: test bull, bear, and sideways windows separately.

## Asset Differences

### BTC and ETH

- When it works: liquidity is deeper, signals often reflect broad market risk appetite, and execution assumptions are easier to model.
- When it fails: macro shock, ETF/news events, or liquidation clusters distort clean structure.
- Required assumptions: spot vs perpetual, exchange, funding, and volatility regime.
- Reject/repair triggers: missing fee/funding assumptions or claiming one exchange backtest proves broad robustness.

### Altcoins

- When it works: liquidity is sufficient and the strategy explicitly handles wider wicks and thinner order books.
- When it fails: sudden delistings, unlocks, market-maker withdrawal, or spread expansion.
- Required assumptions: minimum volume, max spread, exchange count, and symbol age.
- Reject/repair triggers: no liquidity filter, no slippage assumption, or overfitting a single pump window.

## Practical Archetypes

### Breakout and Fakeout

- When it works: compression is observable and volatility expansion follows a meaningful level.
- When it fails: low-liquidity stop hunts, false breaks around obvious highs/lows, or funding-driven volatility around perpetual swap funding windows.
- Required assumptions: breakout level, confirmation candle, retest rule, stop placement, and invalidation.
- Implementation traps: entering on wick break only; ignoring whether the close reclaimed the range.
- Clarifying questions: Should entry require candle close, retest, volume/volatility filter, or all three?

### Liquidity Sweep and Reclaim

- When it works: price sweeps a prior swing, fails to hold, and reclaims on a confirmed candle.
- When it fails: the sweep becomes true continuation or occurs in a news/liquidation shock.
- Required assumptions: swing definition, sweep threshold, reclaim rule, stop beyond sweep, sweep extreme, funding context, and target model.
- Implementation traps: using future pivots, repainting swing points, or undefined wick/body thresholds.
- Reject/repair triggers: no explicit swept level or no invalidation beyond the sweep extreme.

### DCA and Grid Caution

- When it works: bounded range assumptions are explicit and exposure caps are hard-coded.
- When it fails: trend expansion, cascading drawdown, leverage, or unlimited averaging.
- Required assumptions: max entries, max exposure, spacing, stop/kill switch, and drawdown cap.
- Reject/repair triggers: martingale sizing, no max loss, no trend-expansion kill switch, or no liquidation model.

## Ask Before Coding

- Which market: BTC, ETH, liquid major altcoin, or thin altcoin?
- Which venue and instrument: spot, perpetual, futures, or CFD?
- Which timeframe and holding period?
- Are funding, fees, spread, and slippage included?
- What disables the strategy: volatility shock, low liquidity, trend/range regime change, or news?
