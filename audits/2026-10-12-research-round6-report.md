# Research round 6: 3 more new strategy ideas, all fail at 1x

- **Date:** 2026-10-12
- **Request:** continuation of "use the harness and find new strategies that we can use with 1x 2x and 10x".
- **Code:** `research_round6.py` (new, standalone, not committed). Same reuse pattern as rounds 2-5. **`research_harness.py` and `PREREGISTRATION.md` were not modified.**

## The 3 new strategies, listed up front (before any were run against data)

1. **Fisher Transform turning-point** -- normalize price's position within a rolling 10-bar range to [-1, 1], apply the Ehlers Fisher log-transform (stretches extremes far more than a linear z-score). Buy when Fisher turns up from a local trough, sell when it turns down from a local peak.
2. **Rolling percentile-rank mean reversion** -- buy when the current close's percentile rank within the trailing 20 bars is <=10%, sell when >=90% while holding. Ordinal ranking, not a parametric formula.
3. **Connors RSI composite** -- average of short RSI(3) on price, streak-RSI(2) on consecutive up/down run length, and the percentile rank of today's return among the last 100. Buy when composite < 10, sell when > 70.

## Exploration results (12 cells: 3 strategies x 2 assets x 2 timeframes, 1x)

| Asset | TF | Strategy | Equity Return% | Summed RT Return% | Fees% | TIM% | Round trips | p-value | Result |
|---|---|---|---|---|---|---|---|---|---|
| BTC | 1h | fisher_transform | -50.08% | -69.14% | 38.41% | 1.1% | 206 | 0.916 | fail |
| BTC | 1h | percentile_rank_mean_reversion | -50.19% | -67.64% | 51.17% | 18.8% | 289 | 0.419 | fail |
| BTC | 1h | connors_rsi | -50.31% | -69.28% | 59.66% | 3.4% | 311 | 0.0035 | fail (negative return) |
| BTC | 4h | fisher_transform | -50.36% | -69.12% | 48.59% | 5.6% | 257 | 0.309 | fail |
| BTC | 4h | percentile_rank_mean_reversion | -17.85% | -16.06% | 39.08% | 40.1% | 160 | 0.5575 | fail |
| BTC | 4h | connors_rsi | -50.14% | -67.73% | 37.29% | 11.5% | 211 | 0.7965 | fail |
| ETH | 1h | fisher_transform | -50.22% | -69.22% | 38.18% | 1.0% | 202 | 0.887 | fail |
| ETH | 1h | percentile_rank_mean_reversion | -52.61% | -71.01% | 48.59% | 17.2% | 259 | 0.5495 | fail |
| ETH | 1h | connors_rsi | -50.11% | -68.63% | 62.24% | 3.7% | 323 | 0.0045 | fail (negative return) |
| ETH | 4h | fisher_transform | -50.08% | -68.33% | 46.24% | 5.8% | 261 | 0.2785 | fail |
| ETH | 4h | percentile_rank_mean_reversion | -27.54% | -25.71% | 42.83% | 43.6% | 178 | 0.5285 | fail |
| ETH | 4h | connors_rsi | -50.53% | -67.44% | 42.83% | 11.5% | 227 | 0.5985 | fail |

**0 of 12 cells passed.** `connors_rsi` produced two very low p-values (0.0035, 0.0045 on BTC/ETH 1h) but both lost ~69% in round-trip terms -- the now-familiar pattern: heavy fee churn (60%+ of capital on 300+ round trips) makes the random-timing baseline even worse than the real strategy, which is not the same as the real strategy making money. The two-part gate correctly rejects both.

## Leverage extension / held-out confirmation: not run

Nothing passed 1x exploration.

## Running total across all "new idea" research rounds

| Round | Ideas tested | Cells | Passed 1x | Survived held-out + multiple-testing |
|---|---|---|---|---|
| 2 | 3 (stochastic, triple SMA ribbon, time-series momentum) | 12 | 0 | -- |
| 3 | 3 (Ichimoku, linear regression channel, CCI) | 12 | 0 | -- |
| 4 | 3 (Keltner breakout, momentum acceleration, dual-TF ROC) | 12 | 1 | No |
| 5 | 3 (HMA cross, return-shock reversion, Awesome Oscillator) | 12 | 0 | -- |
| 6 | 3 (Fisher Transform, percentile-rank reversion, Connors RSI) | 12 | 0 | -- |

**21 new strategy ideas tested across 5 rounds (60 cells), plus the original 6 pre-registered (24 cells) = 27 ideas, 84 cells total. Exactly 1 cell has ever cleared the 1x gate, and it failed held-out confirmation.** No strategy tested to date shows a real, replicable, cost-adjusted edge on BTC/ETH at 1h/4h, at any leverage.

## Caveats

- **Bitstamp, not Kraken**, standing caveat for every crypto replay in this series.
- **The p-value test remains the same cheap approximation** documented in every prior round.
- **Whether these 3 strategies' parameters were fixed before any result was seen cannot be fully proven**, since `research_round6.py` is not committed as of this report.

## Commit / data status

No live engine code changed. No live ledger or config touched. `research_harness.py` and `PREREGISTRATION.md` were not modified. `research_round6.py` is new and uncommitted. No new datasets fetched.
