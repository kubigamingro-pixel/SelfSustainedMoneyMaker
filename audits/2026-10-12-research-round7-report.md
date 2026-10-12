# Research round 7: regime-filter and shock-following ideas, all fail at 1x

- **Date:** 2026-10-12
- **Request:** continuation of "use the harness and find new strategies that we can use with 1x 2x and 10x".
- **Code:** `research_round7.py` (new, standalone, not committed). Same reuse pattern as rounds 2-6. **`research_harness.py` and `PREREGISTRATION.md` were not modified.**

## The 3 new strategies, listed up front (before any were run against data)

After 5 rounds of indicator variations (21 ideas, 0 confirmed), this round shifted the hypothesis space rather than inventing another oscillator:

1. **Volatility-regime-filtered momentum** -- the same 5/20 SMA crossover as the original momentum strategy, but only takes the bullish entry when realized 20-bar volatility is currently below its own trailing 100-bar median (trade momentum only in a "quiet" regime). Distinct from `momentum_regime` (which filters by trend direction, not volatility level).
2. **Donchian midline crossover** -- buy when price crosses above the midline of a 20-bar Donchian channel, sell on the reverse cross. Distinct from `donchian_breakout` (which enters at the channel's extreme bands).
3. **Volatility-shock-following momentum** -- the mirror-opposite hypothesis of round 5's return-shock mean reversion: buy when a single-bar return exceeds 3x the recent average absolute return to the upside (ride the shock instead of fading it), sell on the first down bar after.

## Exploration results (12 cells: 3 strategies x 2 assets x 2 timeframes, 1x)

| Asset | TF | Strategy | Equity Return% | Summed RT Return% | Fees% | TIM% | Round trips | p-value | Result |
|---|---|---|---|---|---|---|---|---|---|
| BTC | 1h | vol_regime_momentum | -4.21% | -3.99% | 8.13% | 1.8% | 31 | 0.2415 | fail |
| BTC | 1h | donchian_midline_cross | -50.26% | -67.87% | 49.59% | 7.9% | 259 | 0.3595 | fail |
| BTC | 1h | vol_shock_momentum | -50.03% | -68.85% | 42.30% | 1.2% | 226 | 0.5005 | fail |
| BTC | 4h | vol_regime_momentum | -18.70% | -19.40% | 18.16% | 13.6% | 75 | 0.696 | fail |
| BTC | 4h | donchian_midline_cross | -50.17% | -66.40% | 40.55% | 22.3% | 209 | 0.865 | fail |
| BTC | 4h | vol_shock_momentum | -29.45% | -34.12% | 43.69% | 4.4% | 185 | 0.0195 | fail (negative return) |
| ETH | 1h | vol_regime_momentum | -41.62% | -52.98% | 22.49% | 4.4% | 112 | 0.949 | fail |
| ETH | 1h | donchian_midline_cross | -50.12% | -68.13% | 47.59% | 7.2% | 248 | 0.41 | fail |
| ETH | 1h | vol_shock_momentum | -50.06% | -68.98% | 37.82% | 1.0% | 203 | 0.868 | fail |
| ETH | 4h | vol_regime_momentum | -17.03% | -13.55% | 23.43% | 18.7% | 101 | 0.4395 | fail |
| ETH | 4h | donchian_midline_cross | -42.90% | -47.05% | 77.66% | 43.9% | 374 | 0.1955 | fail |
| ETH | 4h | vol_shock_momentum | -31.12% | -35.61% | 51.20% | 5.2% | 232 | 0.009 | fail (negative return) |

**0 of 12 cells passed.** `vol_shock_momentum` produced low p-values on both BTC and ETH 4h (0.0195, 0.009) but both lost money -- same heavy-fee-churn pattern seen repeatedly in prior rounds. Interesting secondary note: the volatility-regime filter sharply cut trade counts (as low as 31 round trips on BTC 1h, vs. 200-300+ typical) and losses were smaller in that cell (-3.99%) than almost anywhere else tested across all 7 rounds -- the filter clearly reduces fee churn, but didn't produce a positive or significant result.

## Leverage extension / held-out confirmation: not run

Nothing passed 1x exploration.

## Running total across all "new idea" research rounds

| Round | Ideas tested | Cells | Passed 1x | Survived held-out + multiple-testing |
|---|---|---|---|---|
| 2 | 3 | 12 | 0 | -- |
| 3 | 3 | 12 | 0 | -- |
| 4 | 3 | 12 | 1 | No |
| 5 | 3 | 12 | 0 | -- |
| 6 | 3 | 12 | 0 | -- |
| 7 | 3 | 12 | 0 | -- |

**24 new strategy ideas tested across 6 rounds (72 cells), plus the original 6 pre-registered (24 cells) = 30 ideas, 96 cells total. Exactly 1 cell has ever cleared the 1x gate, and it failed held-out confirmation.** No strategy tested to date shows a real, replicable, cost-adjusted edge on BTC/ETH at 1h/4h, at any leverage.

## Caveats

- Same standing caveats as every prior round: Bitstamp not Kraken, the p-value test is a cheap approximation, and `research_round7.py` is uncommitted so pre-registration of its exact parameters can't be independently verified.

## Commit / data status

No live engine code changed. No live ledger or config touched. `research_harness.py` and `PREREGISTRATION.md` were not modified. `research_round7.py` is new and uncommitted. No new datasets fetched.
