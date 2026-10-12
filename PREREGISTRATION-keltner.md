# Pre-registration: Keltner Channel breakout, frozen forward test

This is a **separate, cheap pre-registration** for exactly one strategy --
`keltner_breakout` -- the single cell that passed exploration anywhere in
the 24-idea, 96-cell strategy search documented in
`audits/2026-10-12-full-strategy-search-audit-dossier.md` and independently
re-audited in `audits/2026-10-12-strategy-search-review.md`. It exists
because that audit found the pass does not survive multiple-testing
correction (Bonferroni gives 3.1 across the 72 new-idea cells; Benjamini-
Hochberg passes nothing), is not robust to nearby settings (ETH fails at
all 16 nearby settings tried; BTC passes only at `sma_window=20,
num_atr>=2.0`), and is not robust across time (all of its profit came from
years 1-2; years 3-4 both lost money). The only honest way to settle
whether this is real is to freeze it now and score it once on data that
doesn't exist yet -- exactly the discipline `PREREGISTRATION.md` already
applies to the original 6 strategies.

**This file does not change `PREREGISTRATION.md`.** It is a second, smaller
pre-registration, scored in the same window, with its own correction
applied jointly across both files' forward cells (see Scoring window,
below).

## Environment

- **Python version: 3.13.7**, same as `PREREGISTRATION.md` and pinned in the
  same `.python-version` (repo root).
- **Code:** the strategy implementation is `KeltnerBreakoutStrategy` in
  `research_round4.py` (not committed as of this file -- see Commit status,
  below, for why that is being corrected alongside this freeze).

## The frozen parameters

No parameter below may change before the scoring run, including in response
to the settings-grid results in `audits/2026-10-12-strategy-search-review.md`
that showed `num_atr=2.5` or `num_atr=3.0` scoring a lower p-value on the
burned BTC/ETH data than the originally-tested `num_atr=2.0`. **Moving to
either of those now would be fitting to data this strategy has already been
tested against -- the exact mistake this file exists to prevent.** The
parameters below are exactly what was tested in exploration, unchanged:

| Strategy | Parameters |
|---|---|
| `keltner_breakout` | `sma_window=20, atr_window=14, num_atr=2.0` |

Mechanism, for reference: middle band = SMA(close, 20); band = middle +/-
`num_atr` x ATR(14), where ATR uses a true-range proxy of
`max(close - low, abs(close - prev_close))` (this project's saved data has
close + low, not full OHLC). Buy when close breaks above the upper band
while not holding; sell when close drops back below the middle line while
holding.

## Assets, timeframes, and fee/sizing assumptions

- **BTC/USD, ETH/USD, SOL/USD, XRP/USD, ADA/USD**, via Bitstamp (not Kraken
  -- the standing caveat throughout this project's crypto research).
- **4h candles only.** Keltner was never tested at 1h in a way that passed,
  and 1h was not explored for this specific settings combination beyond
  what's already in the burned dataset -- 4h is the only timeframe this
  freeze covers.
- **1x only.** The same strategy was tested at 2x and 10x in round 4
  (`audits/2026-10-12-research-round4-leverage-report.md`): 2x preserved
  the headline return on the burned data but with a 66% max drawdown on a
  34% win rate, and 10x was catastrophic (-92.23%, 4 loss-cap breaches).
  Leverage is out of scope for this freeze for the same reason it's out of
  scope for `PREREGISTRATION.md` -- nothing here supports deploying it
  leveraged, pass or fail.
- **Fee/slippage/position sizing:** identical to the live `crypto_momentum`
  track's config (`config/tracks.yaml`) -- `fee_pct=0.26, slippage_pct=0.05,
  position_size_pct=50`, same as every cell in this project's research
  series.

## The pass gate (identical to `PREREGISTRATION.md`'s)

For each (asset, 4h) cell, independently:

1. Compute the strategy's real closed round trips and their summed net-of-
   cost return (`net_round_trip_returns_pct`, equity-scale, at 50% position
   sizing).
2. Compute a p-value against 2,000 random draws of trades with the same
   count and durations, at random starting points in the same price series,
   each draw's per-trade return scaled by the same `position_size_pct/100`
   as the real side.
3. **The cell passes if p < 0.10 AND the real summed return is positive.**

## Multiple-testing correction, named up front

This freeze adds **5 forward cells** (BTC, ETH, SOL, XRP, ADA, all at 4h)
to the **24 forward cells** `PREREGISTRATION.md` already commits to scoring
in the same window. Both files are scored together, as **one combined
family of 29 cells**, with a formal correction applied across all 29 before
treating any single pass as evidence -- Holm or Benjamini-Hochberg, not a
per-file correction. Scoring the two files separately and applying
correction within each one independently would understate how many chances
this project has given itself to find a false positive; it must be judged
as what it is, one search.

## What the existing evidence already suggests

**Expect this to fail.** The year-by-year breakdown on the burned data
(year 1 +17.80% at p=0.04, year 2 +28.30% at p=0.0885, year 3 -9.25%, year 4
-13.13%, all BTC 4h) shows the edge sitting entirely in a two-year rising
period and inverting in the two years since. Combined with ETH failing at
every one of 16 nearby settings tried and the original pass not surviving
Bonferroni or Benjamini-Hochberg across the 72-cell family it came from,
the existing evidence points toward noise, not a real edge. This freeze
exists to settle that with one clean forward test, not because the prior
evidence is promising.

## Scoring window

- **Pre-registration date (this commit): 2026-10-12** -- same day as
  `PREREGISTRATION.md`, so both freezes start their forward-only clock
  together.
- **Scoring data:** BTC/USD, ETH/USD, SOL/USD, XRP/USD, ADA/USD, 4h, via
  Bitstamp, from **2026-10-12 onward** -- data that did not exist when this
  file was committed.
- **Earliest scoring run: 2027-04-12. Latest: 2027-10-12** -- identical
  window to `PREREGISTRATION.md`, scored once, together, as described
  above.
- **Before that date:** no new data from this window may be fetched, looked
  at, or used to adjust anything above.

## What a pass would, and would not, mean

Given the fragility already documented (one setting, one asset, two years
of profit followed by two years of losses, failing every formal multiple-
testing correction applied so far), a pass on the forward scoring run would
be a meaningfully stronger signal than the original exploration pass was --
but still only one run on one window, on a strategy whose entire prior
track record is consistent with noise. It would not, on its own, justify
deploying this to a live track; that remains a separate, deliberate
decision for the project owner.

## Commit status

This file is being committed alongside `research_round2.py` through
`research_round7.py` and their reports, closing the provenance gap flagged
in `audits/2026-10-12-strategy-search-review.md` finding #8 (round scripts
were previously uncommitted, so it couldn't be proven their parameters were
fixed before results were seen). Going forward, this file and the committed
`research_round4.py` are the record of exactly what was frozen and when.
