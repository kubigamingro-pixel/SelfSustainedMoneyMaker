# Research round 5: 3 more new strategy ideas, all fail at 1x

- **Date:** 2026-10-12
- **Request:** continuation of "use the harness and find new strategies that we can use with 1x 2x and 10x".
- **Code:** `research_round5.py` (new, standalone, not committed). Same reuse pattern as rounds 2-4. **`research_harness.py` and `PREREGISTRATION.md` were not modified.**

## The 3 new strategies, listed up front (before any were run against data)

1. **Hull Moving Average (HMA) crossover** -- HMA(20), the classic Alan Hull lag-reduced weighted-MA construction (`WMA(sqrt(n))` of `2*WMA(n/2) - WMA(n)`). Buy when price crosses above HMA(20), sell on the reverse cross.
2. **Return-shock mean reversion** -- buy when the latest single-bar return's z-score (vs. the trailing 20-bar return distribution) drops below -2.5, sell when price recovers above its 5-bar SMA.
3. **Awesome Oscillator zero-line cross** -- `SMA(5) - SMA(34)` of close; buy/sell on the histogram crossing zero (no EMA smoothing, no signal line, unlike macd_crossover).

## Exploration results (12 cells: 3 strategies x 2 assets x 2 timeframes, 1x)

| Asset | TF | Strategy | Equity Return% | Summed RT Return% | Fees% | TIM% | Round trips | p-value | Result |
|---|---|---|---|---|---|---|---|---|---|
| BTC | 1h | hull_ma_cross | -50.08% | -68.95% | 34.36% | 2.9% | 189 | 0.957 | fail |
| BTC | 1h | return_shock_mean_reversion | -37.87% | -47.08% | 30.59% | 2.0% | 148 | 0.681 | fail |
| BTC | 1h | awesome_oscillator_cross | -34.72% | -40.92% | 37.63% | 13.5% | 171 | 0.524 | fail |
| BTC | 4h | hull_ma_cross | -51.04% | -68.38% | 69.85% | 21.0% | 347 | 0.193 | fail |
| BTC | 4h | return_shock_mean_reversion | -24.08% | -26.58% | 17.92% | 5.0% | 85 | 0.6825 | fail |
| BTC | 4h | awesome_oscillator_cross | -23.59% | -22.31% | 39.03% | 35.7% | 144 | 0.658 | fail |
| ETH | 1h | hull_ma_cross | -50.23% | -69.25% | 28.59% | 2.3% | 156 | 0.9915 | fail |
| ETH | 1h | return_shock_mean_reversion | -50.25% | -68.56% | 36.48% | 2.5% | 182 | 0.9165 | fail |
| ETH | 1h | awesome_oscillator_cross | -50.14% | -67.19% | 41.95% | 12.1% | 179 | 0.8185 | fail |
| ETH | 4h | hull_ma_cross | -50.07% | -66.21% | 74.46% | 19.7% | 350 | 0.143 | fail |
| ETH | 4h | return_shock_mean_reversion | -25.93% | -27.83% | 19.83% | 5.2% | 94 | 0.557 | fail |
| ETH | 4h | awesome_oscillator_cross | -42.30% | -44.86% | 38.78% | 41.3% | 166 | 0.711 | fail |

**0 of 12 cells passed** -- no low p-value near-misses even on the negative-return side this time (lowest p-value was 0.143, well above the 0.10 threshold). A cleaner, less ambiguous null than round 4's.

## Leverage extension / held-out confirmation: not run

Per the pre-specified rule, nothing passed 1x exploration, so no 2x/10x test and no SOL/XRP/ADA confirmation was run.

## Running total across all "new idea" research rounds

| Round | Ideas tested | Cells | Passed 1x | Survived held-out + multiple-testing |
|---|---|---|---|---|
| 2 | 3 (stochastic, triple SMA ribbon, time-series momentum) | 12 | 0 | -- |
| 3 | 3 (Ichimoku, linear regression channel, CCI) | 12 | 0 | -- |
| 4 | 3 (Keltner breakout, momentum acceleration, dual-TF ROC) | 12 | 1 (BTC 4h keltner_breakout) | No (held-out fails, within chance rate, 10x catastrophic) |
| 5 | 3 (HMA cross, return-shock reversion, Awesome Oscillator) | 12 | 0 | -- |

**18 new strategy ideas tested across 4 rounds (48 cells), plus the original 6 pre-registered ideas (24 cells) = 24 ideas, 72 cells total in this project's history. Exactly 1 cell has ever cleared the 1x significance gate, and it failed held-out confirmation.** No strategy tested to date in this project shows a real, replicable, cost-adjusted edge on BTC/ETH at 1h/4h, at any leverage.

## Caveats

- **Bitstamp, not Kraken**, standing caveat for every crypto replay in this series.
- **The p-value test remains the same cheap approximation** documented in every prior round.
- **Whether these 3 strategies' parameters were fixed before any result was seen cannot be fully proven**, since `research_round5.py` is not committed as of this report -- same limitation flagged every round.

## Commit / data status

No live engine code changed. No live ledger or config touched. `research_harness.py` and `PREREGISTRATION.md` were not modified. `research_round5.py` is new and uncommitted. No new datasets fetched.
