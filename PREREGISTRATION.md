# Pre-registration: frozen crypto research-harness rules

This file exists because years 1-4 of the only data this project has looked
at (2022-10-13 to 2026-10-12, BTC/ETH 1h/4h via Bitstamp) are now burned:
every strategy below has already been explored and/or confirmation-tested
against all four of those years (`audits/2026-10-12-research-harness-report.md`,
corrected version). **No result from that data may be used again to justify,
tune, add, or remove a rule below.** The only honest test left is scoring
this exact, frozen list against data that didn't exist yet when this file was
committed.

Freezing a list and a gate in writing, before looking at new data, is what
makes the next scoring run a real out-of-sample test instead of another
round of the same look-and-pick process that produced (and then lost) 2
false positives out of 24 cells in the prior round.

## Environment

- **Python version: 3.13.7**, pinned in `.python-version` (repo root). Per
  `audits/2026-10-12-research-harness-review.md` finding #5: Python 3.12+
  changed float `sum()` to compensated summation, which can flip an SMA
  crossover sitting exactly on a tie -- a different interpreter version is
  not guaranteed to reproduce every cell of this harness to the decimal
  (confirmed directly: 3.10 gave a different trade count and return for one
  cell than 3.13 did on the same data). Use 3.13.7 for the scoring run, or
  explicitly note the version used and treat small differences as expected,
  not evidence of a bug.
- **Code:** `research_harness.py`, committed alongside this file, unchanged
  from the commit that introduces this file. `backtest_all.py` (for
  `fetch_crypto_history_with_low`/`replay`) is unchanged live-adjacent code,
  already committed separately.

## The 6 frozen strategies and their exact parameters

No parameter below may change before the scoring run. If a parameter needs
to change, that is a new pre-registration (a new file, a new commit, a new
frozen scoring window), not an edit to this one.

| Strategy | Parameters |
|---|---|
| `momentum` | `fast_window=5, slow_window=20` |
| `momentum_regime` | `fast_window=5, slow_window=20` |
| `mean_reversion` | `rsi_period=14, oversold=30, overbought=70` |
| `donchian_breakout` | `entry_window=20, exit_window=10` |
| `macd_crossover` | `fast_period=12, slow_period=26, signal_period=9` |
| `bollinger_mean_reversion` | `window=20, num_std=2.0` |

These match `research_harness.ALL_STRATEGIES` exactly as committed. The first
3 are the live engine's own strategies (reused via `engine.runner.STRATEGIES`);
the last 3 are classic ideas picked before any of this project's data was
looked at through them, registered into the same `STRATEGIES` dict in-memory
only (no live engine file touched) so they run through the identical
fill/fee/sizing/ledger machinery as everything else in this project.

## Assets, timeframes, and fee/sizing assumptions

- **BTC/USD, ETH/USD**, via Bitstamp (not Kraken -- see every prior crypto
  replay in this project for that standing caveat).
- **1h and 4h candles.**
- **1x only.** Leverage is out of scope for this pre-registration; every
  prior experiment in this project found it uniformly destructive.
- **Fee/slippage/position sizing:** identical to the live `crypto_momentum`
  track's config (`config/tracks.yaml`) -- `fee_pct=0.26, slippage_pct=0.05,
  position_size_pct=50`, reused as-is for every strategy and asset tested,
  not tuned per cell.

## The pass gate (exact, unchanged from the corrected report)

For each (asset, timeframe, strategy) cell, independently:

1. Compute the strategy's real closed round trips and their summed net-of-cost
   return (`net_round_trip_returns_pct`, equity-scale, at 50% position sizing).
2. Compute a p-value against 2,000 random draws of trades with the same
   count and durations, at random starting points in the same price series,
   each draw's per-trade return scaled by the same `position_size_pct/100`
   as the real side (`research_harness.random_timing_pvalue`, the corrected,
   calibrated version).
3. **The cell passes if p < 0.10 AND the real summed return is positive.**

**Multiple-testing note, carried forward from the corrected report:** 6
strategies x 4 asset/timeframe combos = 24 cells. At a 0.10 threshold, roughly
2-3 passes are expected by chance alone even with zero real edge anywhere,
and the 24 cells are not independent (1h/4h share price paths; BTC/ETH move
together) -- the effective test count is closer to 4-6. **A single pass on
the next scoring run is not sufficient to call a rule real.** Apply a formal
correction (Bonferroni or Benjamini-Hochberg) across whatever cells are
scored before treating any single pass as evidence.

## Scoring window

- **Pre-registration date (this commit): 2026-10-12.**
- **Scoring data:** BTC/USD and ETH/USD, 1h and 4h, via Bitstamp, from
  **2026-10-12 onward** -- data that did not exist when this file was
  committed.
- **Earliest scoring run: 2027-04-12** (6 months out). **Latest: 2027-10-12**
  (12 months out). Run the scoring exactly once, inside that window, using
  the frozen strategy list, parameters, and gate above, unchanged.
- **Before that date:** no new data from this window may be fetched, looked
  at, or used to adjust anything above. Doing so burns this pre-registration
  the same way years 1-4 are already burned, and a new one would be needed.

## What a pass on the scoring run would, and would not, mean

A strategy clearing the gate once, on fresh data, after this freeze, would be
the first result in this project not explainable by look-and-pick selection
on already-examined history. It would still only be one scoring run on one
window -- not sufficient on its own to change any live track. That remains a
separate, deliberate decision for the project owner to make explicitly, not
a config change that follows automatically from a passing cell.
