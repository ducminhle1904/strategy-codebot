# Strategy Patterns Playbook

## Boundary

Patterns describe design templates, not guarantees. Every generated strategy still needs explicit rules, risk limits, validation, and review before any live use.
When a prompt asks for price action without exact rules, convert the request into observable market structure, invalidation, and execution assumptions before coding.

## Trend Following

- Use when market structure and volatility support directional continuation.
- Define trend filter, entry trigger, stop placement, exit logic, and conditions that disable entries in chop.
- Validate with out-of-sample windows that include both trend and range regimes.

## Mean Reversion

- Use when price oscillates inside a measurable range or around a stable reference.
- Define range boundaries, entry threshold, invalidation, maximum hold time, and trend-expansion kill switch.
- Include spread, fees, and slippage because small targets are sensitive to execution cost.

## Breakout

- Use when compression, session open, or volatility expansion can justify continuation.
- Define breakout level, confirmation rule, false-break handling, stop, target, and whether retests are required.
- Avoid assuming every range break is tradable; news and low liquidity can distort signals.

## Volatility Filters

- Use volatility filters to decide when a pattern is allowed, not to hide weak rules.
- Define the volatility measure, lookback, threshold, and behavior during sudden expansion.
- Check that the filter does not simply curve-fit the best historical windows.

## Position Sizing

- Size from risk per trade, invalidation distance, and max drawdown tolerance.
- Keep position sizing separate from signal quality; bigger size does not make a weak signal stronger.
- Do not imply that sizing removes market risk.

## Validation Checklist

- State market, timeframe, session, data source, and execution assumptions.
- Include fees, spread, slippage, commission, and funding or rollover where relevant.
- Test out-of-sample or walk-forward windows.
- Review repaint/lookahead risk for Pine and platform-specific execution limits for MQL5.
- Reject claims of guaranteed profit, no-loss behavior, or live-ready certification.

## Experience Block Template

- When it works: name the regime, market, timeframe, and structural condition that makes the pattern plausible.
- When it fails: name the regime shift, liquidity issue, or execution assumption that invalidates the pattern.
- Required assumptions: state market, timeframe, data source, fees/spread/slippage, and invalidation.
- Implementation traps: call out repaint, future pivots, vague candle language, and unstated session logic.
- Validation checklist: include out-of-sample, walk-forward or regime split, and conservative execution costs.
- Clarifying questions: ask only for missing assumptions that materially change code or risk.
- Reject/repair triggers: no max loss, guaranteed profit claim, live-ready claim, or unlimited averaging.

## Archetype: Trend Pullback

- When it works: trend structure is observable and pullbacks respect a defined invalidation level.
- When it fails: sideways chop, late trend exhaustion, or volatility shock.
- Required assumptions: trend filter, pullback trigger, stop, target, and disable condition.
- Implementation traps: stacking indicators without explaining why they identify trend quality.
- Clarifying questions: Should trend be defined by structure, moving average, higher timeframe, or volatility?

## Archetype: Breakout Retest

- When it works: compression breaks through a meaningful level and retest confirms acceptance.
- When it fails: wick-only fakeouts, news spikes, and low-liquidity stop hunts.
- Required assumptions: breakout level, close/confirmation rule, retest window, stop, and target.
- Implementation traps: entering on future-known support/resistance or undefined range boundaries.
- Reject/repair triggers: no false-break handling or no invalidation after retest failure.

## Archetype: Mean Reversion

- When it works: price oscillates inside a stable range and execution cost is small relative to target.
- When it fails: volatility expansion, trend breakout, or stale range assumptions.
- Required assumptions: range definition, entry threshold, stop outside range, max hold time.
- Implementation traps: using small targets without modeling spread, fees, and slippage.
- Validation checklist: separate range and trend windows before judging robustness.

## Archetype: Liquidity Sweep

- When it works: prior swing liquidity is swept and price reclaims the level on confirmation.
- When it fails: sweep becomes continuation or occurs during a volatility shock.
- Required assumptions: swing definition, sweep threshold, reclaim candle, stop beyond sweep, target.
- Implementation traps: repainting pivots or using future swing points.
- Clarifying questions: Should the reclaim require candle close, wick rejection, or structure shift?

## Archetype: Volatility Expansion

- When it works: compression releases into directional movement with defined risk.
- When it fails: one-bar news spike, illiquid wick, or immediate mean reversion.
- Required assumptions: volatility measure, lookback, threshold, entry confirmation, and kill switch.
- Implementation traps: fitting volatility thresholds to a single historical window.

## Archetype: DCA/Grid Risk

- When it works: bounded range assumptions are explicit and exposure is capped.
- When it fails: trend expansion, leverage, unlimited averaging, or liquidation risk.
- Required assumptions: max orders, spacing, max exposure, stop/kill switch, and drawdown cap.
- Reject/repair triggers: martingale sizing, no max loss, or claim that averaging prevents loss.

## Archetype: No-Indicator Price Action

- When it works: all rules can be expressed with OHLC, swings, closes, ranges, and invalidation.
- When it fails: visual language is vague or depends on future-confirmed pivots.
- Required assumptions: swing lookback, confirmation bar, entry trigger, stop, and target.
- Implementation traps: silently adding indicators after the user asked for price action only.
- Clarifying questions: Which market structure event should trigger entry: BOS, CHoCH, sweep, rejection, or retest?
