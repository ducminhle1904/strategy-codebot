# Forex Strategy Playbook

## Boundary

This playbook is decision support for strategy design and review. It must not be used to claim that a forex strategy is profitable, safe, or ready for live trading.

## Market Regimes

- Session trend: London and New York overlap can support momentum logic, but entries still need explicit trend, pullback, and invalidation rules.
- Range session: Asian session ranges can support mean-reversion ideas only when spread is stable and range boundaries are observable.
- News shock: high-impact macro releases can invalidate normal spread, slippage, and stop assumptions.
- Carry or macro drift: higher-timeframe bias may matter, but the strategy must still define mechanical entry and exit conditions.

## Forex-Specific Checks

- Session filter: define whether the strategy trades Asian, London, New York, overlap, rollover, or all sessions.
- Spread and rollover: avoid entries near rollover or low-liquidity periods unless the strategy explicitly models widened spread.
- Pair behavior: majors, minors, and exotics have different liquidity, spread, volatility, and execution assumptions.
- Correlation: avoid treating correlated pairs as independent confirmation without limiting aggregate exposure.
- Broker execution: backtests must state spread, commission, slippage, stop distance, and order execution assumptions.

## Strategy Fit

- Breakout strategies fit session opens or range expansion, but need false-break handling and news filters.
- Mean-reversion strategies fit stable ranges, but need a trend filter and maximum range age.
- Pullback strategies fit directional sessions, but need clear structure, invalidation, and risk sizing.
- Scalping strategies are highly sensitive to spread, latency, and broker rules; backtest assumptions must be conservative.

## Anti-Patterns

- Copying crypto-style 24/7 assumptions into forex without session handling.
- Ignoring spread widening at rollover, news, or illiquid sessions.
- Optimizing a strategy on one pair and assuming it transfers to all pairs.
- Claiming live readiness without separate broker, execution, and risk review.

## Session Playbooks

### Asian Range

- When it works: majors are range-bound, spread is stable, and range boundaries are observable.
- When it fails: pre-news positioning, holiday liquidity, or unexpected JPY/AUD/NZD catalyst.
- Required assumptions: session window, pair, max spread, range age, and invalidation.
- Implementation traps: treating every overnight range as tradable without a volatility filter.
- Validation checklist: test by pair and exclude rollover spread spikes.

### London Breakout

- When it works: prior range compression resolves into directional liquidity during London open.
- When it fails: false break before New York, high-impact news, or spread widening.
- Required assumptions: range window, breakout confirmation, retest rule, news filter, and stop model.
- Clarifying questions: Should entry trigger on close beyond range, retest, or momentum continuation?
- Reject/repair triggers: no session clock, no spread filter, or no false-break handling.

### New York Continuation or Reversal

- When it works: London move sets structure and New York either continues or rejects it at defined levels.
- When it fails: macro release, liquidity transition, or correlated USD shock.
- Required assumptions: New York window, news calendar handling, and pair exposure.
- Implementation traps: using local machine timezone without explicit session conversion.
- Validation checklist: split tests around news and non-news days.

## Pair Differences

### Majors

- When it works: lower spread and deeper liquidity make execution assumptions easier to model.
- When it fails: macro shocks or correlated USD exposure dominate setup quality.
- Required assumptions: spread, commission, session, and news filter.

### Minors and Exotics

- When it works: strategy includes conservative spread/slippage and avoids illiquid windows.
- When it fails: wide spread, gaps, broker restrictions, or sudden liquidity withdrawal.
- Required assumptions: max spread, minimum volatility, max slippage, and broker stop-distance rules.
- Reject/repair triggers: using major-pair assumptions on an exotic pair.

## Practical Archetypes

### Range Mean Reversion

- When it works: range boundaries are stable and volatility is not expanding.
- When it fails: trend breakout, news shock, or range boundary becomes stale.
- Required assumptions: range definition, entry threshold, stop outside range, max hold time.
- Implementation traps: optimizing threshold values on one pair/session.

### Trend Pullback

- When it works: higher-timeframe direction is clear and pullback has defined invalidation.
- When it fails: trend exhaustion, low-volume session, or correlated pair exposure.
- Required assumptions: trend filter, pullback depth, confirmation, stop, and target.
- Clarifying questions: Which timeframe defines trend and which timeframe triggers entry?

## Ask Before Coding

- Which pair group: major, minor, or exotic?
- Which session: Asian, London, New York, overlap, rollover, or all sessions?
- Should news windows disable entries?
- What spread/slippage/commission assumptions should the backtest use?
- How should correlated exposure across pairs be limited?
