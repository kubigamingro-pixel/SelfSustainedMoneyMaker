# Research round 2: 3 new strategy ideas, BTC/ETH exploration, SOL/XRP/ADA held out

- **Date:** 2026-10-12
- **Request:** using `research_harness.py` without changing `PREREGISTRATION.md`, explore new strategy ideas on the existing BTC/ETH 1h/4h datasets; list the ideas up front; treat SOL, XRP, ADA as a held-out set (fetch and save 4 years of their 1h/4h candles, don't look at them until exploration is finished); run every strategy that passed exploration exactly once on them, with the random-entry test and a multiple-testing correction; save a report to `audits/`; change no live code.
- **Code:** `research_round2.py` (new, standalone). Imports `research_harness.py`'s `fetch_and_save`/`load_saved`/`run_cell`/`random_timing_pvalue`/`passes` unchanged -- **neither `research_harness.py` nor `PREREGISTRATION.md` was modified.** The 3 new strategies are registered into `engine.runner.STRATEGIES`/`engine.leveraged_runner.STRATEGIES` in this process's memory only, same pattern as every prior round -- no engine file on disk touched.
- **Datasets:** SOL/USD, XRP/USD, ADA/USD, 1h and 4h, 4 years each (2022-10-13 to 2026-10-12), fetched and saved to `audits/2026-10-12-research-harness-datasets/{SOL,XRP,ADA}_{1h,4h}.csv` via a separate, prior script invocation -- **before any strategy was run, including against BTC/ETH.** `research_round2.py` itself never fetches; it only loads already-saved CSVs, and only loads the held-out ones in Phase 2, after every exploration decision is already made and printed.

## The 3 new strategies, listed up front (before any were run against data)

1. **Stochastic Oscillator mean-reversion** (`window=14, oversold=20, overbought=80`) -- buy when %K crosses above %D while below the oversold line, sell when %K crosses below %D while above the overbought line. **Close-based, not the textbook high/low version**: this project's saved datasets only carry close and low, not full OHLC, so %K here is computed from the rolling max/min of *closes* over the window, not true intrabar highs/lows -- a documented simplification, not an attempt at a textbook-exact implementation.
2. **Triple SMA ribbon trend-following** (`fast=5, mid=20, slow=50`) -- buy when the three SMAs are fully aligned bullish (fast > mid > slow), sell when that alignment breaks. Distinct from a single 2-SMA crossover or MACD: requires *sustained* 3-way alignment, not one crossing event.
3. **Time-series momentum / Rate-of-Change** (`window=20, entry_threshold_pct=2.0`) -- buy when the 20-bar rate of change exceeds +2%, sell when it turns negative. Pure price-change based, no moving average at all -- the classic "absolute momentum" formulation used by many systematic trend-followers.

None of these parameters were tuned against any of this round's data; all are textbook/common defaults chosen before the first run.

## Method

- **Exploration assets:** BTC/USD, ETH/USD, 1h and 4h, using the **full existing 4-year saved datasets** (already burned for the original 6 pre-registered strategies per `PREREGISTRATION.md` -- using them again here for 3 *new* strategies is exploration only, not a re-test or extension of anything pre-registered, and this round's own results are themselves **not** pre-registered or held-out evidence).
- **Held-out assets:** SOL/USD, XRP/USD, ADA/USD, 1h and 4h -- genuinely untouched by any analysis until Phase 2.
- **Gate (identical to `research_harness.py`'s, unchanged):** p < 0.10 (random-entry-timing test, scaled by `position_size_pct` per the fix from the prior round's review) AND positive summed round-trip return, per (asset, timeframe, strategy) cell independently.
- **Confirmation rule:** a (strategy, timeframe) pair that passes on **any** exploration asset gets tested on **all 3** held-out assets at that same timeframe, exactly once each.
- **Leverage:** 1x only, same scope decision as every prior round.

## Exploration results (12 cells: 3 strategies x 2 assets x 2 timeframes)

| Asset | TF | Strategy | Equity Return% | Summed RT Return% | Fees% | TIM% | Round trips | p-value | Result |
|---|---|---|---|---|---|---|---|---|---|
| BTC | 1h | stochastic_mean_reversion | -50.13% | -67.16% | 43.28% | 21.6% | 240 | 0.732 | fail |
| BTC | 1h | triple_sma_ribbon | -50.13% | -68.07% | 37.35% | 8.0% | 184 | 0.928 | fail |
| BTC | 1h | time_series_momentum | -50.10% | -66.30% | 74.22% | 19.6% | 330 | 0.200 | fail |
| BTC | 4h | stochastic_mean_reversion | -30.02% | -31.87% | 28.39% | 42.5% | 122 | 0.867 | fail |
| BTC | 4h | triple_sma_ribbon | -4.52% | -1.47% | 33.72% | 26.4% | 133 | 0.266 | fail |
| BTC | 4h | time_series_momentum | +6.26% | +11.25% | 55.76% | 37.4% | 180 | 0.142 | fail (p too high) |
| ETH | 1h | stochastic_mean_reversion | -50.50% | -66.30% | 41.23% | 20.1% | 222 | 0.6555 | fail |
| ETH | 1h | triple_sma_ribbon | -41.35% | -48.20% | 62.41% | 14.0% | 303 | 0.055 | fail (negative return) |
| ETH | 1h | time_series_momentum | -50.20% | -66.21% | 61.30% | 16.2% | 287 | 0.324 | fail |
| ETH | 4h | stochastic_mean_reversion | -35.03% | -36.77% | 32.13% | 43.6% | 138 | 0.7385 | fail |
| ETH | 4h | triple_sma_ribbon | +4.26% | +12.55% | 41.73% | 28.3% | 155 | 0.1235 | fail (p too high) |
| ETH | 4h | time_series_momentum | -40.65% | -41.24% | 47.96% | 41.0% | 209 | 0.554 | fail |

**Zero of 12 cells passed.** The lowest p-value found, 0.055 (ETH 1h triple_sma_ribbon), looks tantalizingly close to the 0.10 threshold -- but that cell's real return was -41.35%, meaning it lost money while still scoring low on the random-timing test. As in every prior round, a low p-value here means the real strategy's result was unusual relative to the random baseline, in this case because fee drag on 303 round trips (62.41% of capital in fees alone) makes the random baseline *even worse*, not because the strategy made money. The two-part gate (p<0.10 **and** positive return) correctly rejects this.

## Multiple-testing context

With 12 cells tested at p<0.10, roughly 1-2 passes would be expected by chance alone even with zero real edge anywhere, and (per the same reasoning as the prior round) the 12 cells are not fully independent -- 1h and 4h share price paths within an asset, and BTC/ETH move together. **Finding zero passes is consistent with "no edge in any of these 3 ideas," and is if anything a cleaner result than finding one** (no false positive to explain away, no multiple-testing correction needed on a result that already came back negative everywhere).

## Held-out confirmation: not run

**Per the pre-specified rule, since no (strategy, timeframe) pair passed exploration, none was tested on SOL/XRP/ADA.** The held-out datasets remain exactly as fetched -- saved, unanalyzed by any strategy, available for a future round if a genuinely new idea is pre-registered and shows something in exploration first.

## What this means

**None of the 3 new strategy ideas showed a real, replicable edge after costs on BTC or ETH at 1h or 4h, by the same standard applied to the original 6.** This extends the project's running finding (no strategy tested so far, across 9 total ideas now, survives a properly calibrated significance test on this asset class at these timeframes) rather than reversing it. The held-out SOL/XRP/ADA set was correctly never touched, since nothing earned the right to be tested on it -- its value as a genuine out-of-sample check is fully preserved for next time.

## Caveats

- **Several cells hit the spot engine's (1x, terminal) loss cap partway through exploration** and stopped trading for the remainder -- real, unchanged live engine behavior, not a script artifact (same as every prior round).
- **The Stochastic Oscillator here is close-based, not the textbook high/low version** -- stated plainly above, not hidden in a footnote. A high/low version might behave differently; this project's saved data doesn't support testing it without a separate data-fetching change.
- **Bitstamp, not Kraken**, same standing caveat as every crypto replay in this series.
- **1x only; the p-value test remains the same cheap approximation** documented in the prior round's report (overlapping random draws, no loss-cap stop on the random side, summed not compounded returns).
- **Whether these 3 strategies' parameters were genuinely fixed before any result was seen cannot be fully proven**, since `research_round2.py` is not committed as of this report -- the same limitation the prior round flagged for its own 3 classic strategies.

## Commit / data status

No live engine code changed. No live ledger or config touched. **`research_harness.py` and `PREREGISTRATION.md` were not modified** -- per the request. `research_round2.py` is a new, standalone, uncommitted script. SOL/XRP/ADA datasets are saved under `audits/2026-10-12-research-harness-datasets/` alongside the existing BTC/ETH files.
