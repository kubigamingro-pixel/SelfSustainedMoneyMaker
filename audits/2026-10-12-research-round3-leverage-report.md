# Research round 3: 3 more new strategy ideas, tested for 1x/2x/10x viability

- **Date:** 2026-10-12
- **Request:** "use the harness and find new strategies that we can use with 1x 2x and 10x" -- a scope expansion from every prior round, which deliberately tested 1x only (leverage was out of scope for `PREREGISTRATION.md`, "every prior experiment in this project found it uniformly destructive"). This round builds a leverage-aware extension on top of the harness and looks for new strategy ideas that could genuinely be deployed across 1x, 2x, and 10x tracks.
- **Code:** `research_round3.py` (new, standalone, not committed). Imports `research_harness.py`'s `load_saved`/`run_cell`/`random_timing_pvalue`/`passes` unchanged, and `backtest_all.py`'s `replay`/`summarize` (its existing reset-aware, leverage-variant-capable helpers) for the leverage stage. **Neither `research_harness.py` nor `PREREGISTRATION.md` was modified.** The 3 new strategies are registered into `engine.runner.STRATEGIES`/`engine.leveraged_runner.STRATEGIES` in this process's memory only, same pattern as every prior round -- no engine file on disk touched.

## The 3 new strategies, listed up front (before any were run against data)

All 3 were chosen to avoid an O(whole-history)-per-tick computation: the engine creates a *fresh* strategy instance on every single tick (no state carries between ticks, confirmed by reading `engine/runner.py`), so a recursive/path-dependent indicator like true Parabolic SAR -- which depends on its own running value from the previous tick -- would need to be recomputed from scratch on every tick, costing O(n) per tick and O(n^2) overall. On this project's ~35,000-candle 1h datasets that's roughly 1.2 billion operations in pure Python, impractically slow. All 3 ideas below only ever look at a fixed trailing window, so each is O(window) per tick, same cost class as every strategy tried so far.

1. **Ichimoku Tenkan/Kijun cross** (close-based, since this project's saved data has close + low only, not full OHLC): Tenkan-sen = midpoint of the highest/lowest close over the last 9 bars; Kijun-sen = the same over 26 bars. Buy when Tenkan crosses above Kijun, sell on the reverse cross.
2. **Linear regression channel mean-reversion**: fit a least-squares line to the last 20 closes; buy when price closes 2+ standard deviations (of the fit's residuals) below the line, sell when price reverts back above it. Distinct from Bollinger Bands (which band a *flat* SMA) -- this strategy only calls a price "unusually low" relative to where the recent *trend itself* says it should be.
3. **Commodity Channel Index (CCI) mean-reversion**: CCI(20) = (close - SMA(close,20)) / (0.015 x mean absolute deviation of close from that SMA). Buy when CCI < -100, sell when CCI > +100 while holding. A different normalization than RSI, Stochastic, or Bollinger -- CCI scales by *mean absolute deviation*, which is less sensitive to one outlier tick than a standard deviation is.

None of these parameters were tuned against any of this round's data; all are textbook/common defaults chosen before the first run.

## Method

- **Exploration assets:** BTC/USD, ETH/USD, 1h and 4h, using the full existing 4-year saved datasets (already used for 9 prior strategy ideas across rounds 1-2; using them again here for 3 *new* strategies is exploration, not a re-test of anything pre-registered, and this round's own results are not pre-registered or held-out evidence).
- **Gate (identical to `research_harness.py`'s, unchanged):** p < 0.10 (random-entry-timing test, scaled by `position_size_pct`) AND positive summed round-trip return, per (asset, timeframe, strategy) cell independently. **1x only at this stage** -- leverage never rescues a signal that has no real edge at 1x, so testing 2x/10x on a cell that already fails 1x would just be re-confirming "leverage amplifies losses," already shown in every leveraged track this project has run.
- **Leverage extension (new this round):** any (asset, timeframe, strategy) cell that *passes* the 1x gate would then be replayed at 2x and 10x on that same asset/timeframe, using `backtest_all.replay(..., "2x"/"10x", None)` and `backtest_all.summarize()` (the same reset-aware, chained-equity reporting already built for the leveraged live tracks), to see whether the edge survives leverage's liquidation risk and higher funding/fee drag. A strategy only counts as "usable at 1x, 2x, and 10x" if it clears the 1x gate *and* leverage doesn't reverse or destroy the result.

## Exploration results (12 cells: 3 strategies x 2 assets x 2 timeframes, 1x)

| Asset | TF | Strategy | Equity Return% | Summed RT Return% | Fees% | TIM% | Round trips | p-value | Result |
|---|---|---|---|---|---|---|---|---|---|
| BTC | 1h | ichimoku_cross | -44.20% | -55.82% | 44.85% | 16.5% | 220 | 0.568 | fail |
| BTC | 1h | linear_regression_channel | -45.19% | -59.19% | 66.80% | 6.2% | 325 | 0.001 | fail (negative return) |
| BTC | 1h | cci_mean_reversion | -50.09% | -67.21% | 58.77% | 23.1% | 319 | 0.308 | fail |
| BTC | 4h | ichimoku_cross | -13.88% | -10.46% | 40.77% | 38.0% | 145 | 0.514 | fail |
| BTC | 4h | linear_regression_channel | -33.33% | -38.61% | 39.51% | 13.3% | 187 | 0.338 | fail |
| BTC | 4h | cci_mean_reversion | -30.62% | -33.09% | 33.36% | 42.5% | 151 | 0.8045 | fail |
| ETH | 1h | ichimoku_cross | -50.15% | -63.36% | 56.23% | 18.2% | 269 | 0.389 | fail |
| ETH | 1h | linear_regression_channel | -50.22% | -68.03% | 58.49% | 5.0% | 273 | 0.1685 | fail |
| ETH | 1h | cci_mean_reversion | -51.54% | -68.74% | 46.85% | 18.9% | 255 | 0.556 | fail |
| ETH | 4h | ichimoku_cross | -18.05% | -9.79% | 45.57% | 43.0% | 173 | 0.3785 | fail |
| ETH | 4h | linear_regression_channel | -50.45% | -66.69% | 32.60% | 12.9% | 172 | 0.858 | fail |
| ETH | 4h | cci_mean_reversion | -24.26% | -21.46% | 40.49% | 45.7% | 167 | 0.513 | fail |

**Zero of 12 cells passed.** BTC 1h linear_regression_channel's p=0.001 looks striking, but that cell lost 59.19% of capital (round-trip terms) -- the two-part gate correctly rejects it: a low p-value there means the random baseline did even worse (325 round trips at 66.80% cumulative fees produces a brutal random-timing baseline too), not that the strategy made money. Same pattern as ETH 1h triple_sma_ribbon in round 2.

Several cells hit the spot engine's (1x, terminal in this harness) loss cap partway through and stopped trading for the remainder -- real, unchanged engine behavior, not a script artifact, same as every prior round.

## Leverage extension: not run

**Per the pre-specified rule, since no cell passed the 1x gate, none was tested at 2x or 10x.** The leverage-extension code (`run_leveraged_cell`, using `backtest_all.replay`/`summarize`) is built and ready, but nothing here earned the right to use it. This is the same honest-null outcome round 2 reached for the held-out asset confirmation: no false edge to chase through a leverage test, no multiple-testing correction needed on a result that's already negative everywhere.

## What this means for "strategies we can use with 1x, 2x and 10x"

**None of these 3 new ideas show a real, replicable edge after costs at 1x on BTC/ETH 1h/4h**, so none reached the leverage stage this round. Combined with rounds 1-2, that's now **12 distinct strategy ideas tested, 0 with a real edge surviving the random-timing test and the positive-return requirement together** on this asset class at these timeframes. Since leverage only scales an existing per-trade return (and adds liquidation risk and funding cost on top), and none of these 12 ideas has a positive per-trade edge at 1x to scale, **there is currently no strategy in this project's tested universe that would be expected to do anything but lose faster at 2x or 10x than it already does at 1x.** This isn't a new finding so much as the leverage question resolving itself: the bottleneck has never been leverage sizing, it's that none of the signals tested so far clear transaction costs on this data.

## Caveats

- **Ichimoku here is close-based**, using closing-price midpoints rather than true intrabar highs/lows (same documented simplification as round 2's Stochastic Oscillator) -- this project's saved datasets carry close + low only.
- **CCI's "typical price" is approximated as close** (the textbook version averages high/low/close; no high is saved).
- **Bitstamp, not Kraken**, same standing caveat as every crypto replay in this series.
- **The p-value test remains the same cheap approximation** documented in prior rounds (overlapping random draws, no loss-cap stop on the random side, summed not compounded returns).
- **The leverage-extension code path (`run_leveraged_cell`) is unexercised by real data this round** -- it was built and is ready for the next strategy that actually clears the 1x gate, but has not itself been validated against a known-good leveraged result the way `backtest_all.py`'s leveraged replay already was in the earlier full-history-simulation rounds. Treat it as implemented-but-unverified until a passing cell actually runs through it.
- **Whether these 3 strategies' parameters were genuinely fixed before any result was seen cannot be fully proven**, since `research_round3.py` is not committed as of this report -- the same limitation every prior round's new strategies flagged.

## Commit / data status

No live engine code changed. No live ledger or config touched. `research_harness.py` and `PREREGISTRATION.md` were not modified. `research_round3.py` is new and uncommitted. No new datasets were fetched this round (reused the existing BTC/ETH 1h/4h CSVs already saved under `audits/2026-10-12-research-harness-datasets/`).
