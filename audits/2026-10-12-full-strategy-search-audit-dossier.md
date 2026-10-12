# Audit dossier: full strategy-search program (24 ideas, 96 cells, 1 pass, 0 confirmed)

> **Corrected after independent audit** (`audits/2026-10-12-strategy-search-review.md`). The headline verdict is unchanged -- **no strategy shows a real edge after costs, and nothing here supports a live change** -- and the audit independently reproduced every reported number to the decimal, including the BTC 4h Keltner pass and its leverage/held-out follow-up. But several claims below were weaker, or outright wrong, in ways now fixed:
> 1. **Section 7's "~7 expected by chance" figure ignored the positive-return half of the gate.** The observed pass rate (3 of 96, 3.1%) predicts roughly **2** expected passes, not 7. Corrected below, struck through in place.
> 2. **The Keltner pass does not survive formal multiple-testing correction**, a finding this version of the dossier did not state plainly enough. Bonferroni gives 0.0435 x 72 = 3.1 (not significant); Benjamini-Hochberg at q=0.10 needs a rank-1 p around 0.0014 (72 cells) or 0.0010 (96 cells) -- nowhere close.
> 3. **The Keltner pass is fragile in ways not previously reported**: all of its profit comes from years 1-2 of the 4-year window (year 1 +17.80%, year 2 +28.30%), and both year 3 (-9.25%) and year 4 (-13.13%) lost money. On a settings grid around the passing parameters, BTC passes only at `sma_window=20, num_atr>=2.0`; every other `sma_window` tested fails, and **ETH fails at all 16 nearby settings tried.** New section added below.
> 4. **The held-out SOL/XRP/ADA confirmation tested new coins, not new time** -- all three cover the identical 4 calendar years as the BTC/ETH exploration data, and crypto assets move together, so it doesn't isolate "does this generalize to data the strategy has never seen" the way `PREREGISTRATION.md`'s year-4 holdout does. Two of the three held-out assets also lost money on an equity basis (XRP -0.17%, ADA -7.58%) even though their summed round-trip returns were positive.
> 5. **The 6 "new idea" rounds were adaptive, not independent draws** -- later rounds were explicitly built as variants or mirror-opposites of earlier rounds' ideas (e.g. round 7's `vol_shock_momentum` is stated in its own docstring to be "the mirror-opposite hypothesis" of round 5's idea), and ideas considered and deliberately not attempted (Parabolic SAR, Supertrend) are part of the real search space too. **The nominal 24-idea/96-cell count is a floor on the true search size, not the full correction family.**
> 6. **The Keltner low-field plumbing bug (fix #5 in the original Known Bugs list) was fixed for the one call site that triggered it, but the underlying design -- a class-level monkey-patch holding whichever coin's lows were patched last -- was left in place**, meaning the same bug class can recur silently in any future caller that forgets to re-patch, and a wrong-coin run doesn't always produce an obvious 0-trade result. This has now been hardened with a runtime alignment assertion in `research_round4.py` (raises immediately instead of running on misaligned data) -- done as part of acting on this audit, documented in the Known Bugs section.
>
> All corrections are worked through in full below. The original uncorrected claims are struck through in place, not deleted.

**Prepared for:** an independent Claude session auditing this research program's rigor, correctness, and conclusions. This document consolidates 7 rounds of work (1 pre-registered scoring round + 6 "new idea" exploration rounds) that otherwise live scattered across separate per-round report files. Nothing here is new analysis -- it is a faithful consolidation of results already produced and reported, now corrected per an independent audit.

**Self-correction before anything else:** in conversation, after round 6 and round 7, the assistant running this program twice mis-stated the running total as "27 ideas" and then "30 ideas." **The correct count is 24 distinct strategy ideas (6 pre-registered + 18 new, i.e. 3 ideas x 6 exploration rounds) across 96 cells (24 pre-registered cells + 72 new-idea cells)** -- itself independently confirmed in the audit. An auditor should still verify this arithmetic independently (see the Strategy Catalog and Running Tally sections below) rather than trust it, and should additionally weigh finding #5 above: **the true search family is larger than this nominal count**, since the rounds were adaptive and some ideas were considered and dropped rather than tested.

---

## 1. What this program is and why it exists

This project runs 21 parallel paper-trading tracks (7 strategy/domain families x 1x/2x/10x leverage) against live market data. Separately from the live tracks, this research program asks: **does any of a wide variety of classic or invented technical-analysis strategies show a real, cost-adjusted, statistically-significant edge on crypto price data, at any of the three leverage levels the live system uses?** All work here is offline research against saved historical datasets -- no live engine code, live ledgers, or live config were ever touched by any step described in this document.

## 2. Shared methodology (used identically across every round)

- **Engine reuse, not reimplementation:** every strategy, including invented ones, is registered into the live engine's own `engine.runner.STRATEGIES` / `engine.leveraged_runner.STRATEGIES` dictionaries **in each Python process's memory only** -- no engine file on disk is ever modified. Every backtest runs through the exact same fill/fee/sizing/ledger/loss-cap machinery as the 21 live tracks, via `backtest_all.py`'s `replay()`/`summarize()` functions. This means a strategy's backtested behavior (loss-cap breaches, fee calculation, position sizing) is not a simulation of the live system's rules -- it IS the live system's rules, run against historical data instead of live data.
- **Data:** BTC/USD and ETH/USD, via **Bitstamp, not Kraken** (a standing caveat repeated in every round -- the live tracks use Kraken, which only retains ~7 days of history; Bitstamp was used for this research because it provides 4 years of history). 1h and 4h candlesticks. 4 years of history (2022-10-13 to 2026-10-12), saved once to CSV under `audits/2026-10-12-research-harness-datasets/` and never re-fetched. SOL/USD, XRP/USD, ADA/USD were also fetched (same 4-year window, same two timeframes) and held genuinely untouched until a strategy earned the right to be tested on them (see Held-out Confirmation below).
- **Fee/slippage/sizing:** identical to the live `crypto_momentum` track's config (`config/tracks.yaml`): `fee_pct=0.26, slippage_pct=0.05, position_size_pct=50`, unchanged across every strategy and asset tested -- not tuned per cell.
- **The pass gate, identical in every round:** for each (asset, timeframe, strategy) cell, independently:
  1. Compute the strategy's real closed round trips and their summed net-of-cost return (`net_round_trip_returns_pct`, equity-scale, at 50% position sizing).
  2. Compute a p-value against 2,000 random draws of trades with the same count and durations, at random starting points in the same price series, each draw's per-trade return scaled by the same `position_size_pct/100` as the real side (the calibrated, bug-fixed version of `random_timing_pvalue` -- see Known Bugs Found and Fixed, below).
  3. **The cell passes if p < 0.10 AND the real summed round-trip return is positive.**
- **Leverage policy:** every exploration round runs at **1x only** first. A cell is only pushed through 2x/10x leverage testing if it clears the 1x gate -- leverage amplifies an existing per-trade return, it cannot create one that doesn't exist at 1x, so testing leverage on an already-failing cell was judged not worth the compute.
- **Held-out confirmation policy:** a cell that passes 1x exploration on BTC or ETH earns exactly one test on each of SOL, XRP, and ADA at the same timeframe -- assets genuinely untouched by any analysis until that point.
- **Code status:** every new-idea script (`research_round2.py` through `research_round7.py`) is **standalone and was never committed to the repository as of this writing**. Only `research_harness.py` (the reusable base harness) and `PREREGISTRATION.md` (the frozen list of the original 6 strategies) are committed. This is a material limitation an auditor should weigh: **it cannot be independently proven that each round's strategy parameters were fixed before that round's results were seen**, since there is no commit timestamp predating the results. This caveat is repeated honestly in every round's own report.

## 3. Known bugs found and fixed during this program (full disclosure)

An auditor should know these happened, since they affect how much to trust the numbers below:

1. **p-value scaling bug (pre-registration round):** the original `random_timing_pvalue` compared the real strategy's trades (50%-sized, equity-scale) against random draws computed at full 100%-price-scale with full transaction cost -- the random side paid roughly double the real cost drag, inflating the apparent significance rate (9 of 24 cells below p=0.10, vs. ~2-3 expected by chance). Fixed by scaling each random draw's per-trade contribution by `position_size_pct/100`. Re-run after the fix: 4 of 24 cells below p=0.10, consistent with chance.
2. **p-value direction mis-described (pre-registration round):** an earlier draft of the report described a low p-value as meaning "the real strategy did worse than nearly every random draw" -- backwards. A low p-value under this test's convention means the real strategy's result was unusually GOOD relative to random timing, not unusually bad. Corrected in place.
3. **Year-4-not-fresh issue (pre-registration round):** 2 of the 6 original strategies (momentum, momentum_regime) had already been tested on the exact same "year 4" window in an earlier, separate experiment (`audits/2026-10-12-btc-eth-regime-decision-grade.md`), so year 4 wasn't a genuinely fresh holdout for those two. Documented explicitly; the 3 classic strategies' year-4 window was genuinely fresh.
4. **Python version sensitivity (pre-registration round):** Python 3.12+ changed float `sum()` to compensated summation, which can flip an SMA crossover sitting exactly on a tie -- confirmed directly to change one cell's trade count and return between 3.10 and 3.13. Fixed by pinning `.python-version` to `3.13.7` (committed).
5. **Keltner Channel leveraged-replay bug (round 4):** the Keltner strategy's custom use of the `low` price field (injected via a monkey-patch, since the engine's `Strategy.decide()` interface only passes the close series) was applied once per asset/timeframe loop in the 1x exploration phase, but was never re-applied before the subsequent 2x/10x leverage replay -- the leverage stage silently reused ETH 4h's low values while testing BTC 4h, producing 0 trades at both leverage levels. Caught because 0 trades was an implausible result; fixed by re-applying the patch immediately before every leveraged replay call; re-run produced non-trivial trade counts (330+ per leverage variant). **Independent audit finding #7, addressed after the fact:** the original fix patched the one call site that happened to trigger the bug, but left the underlying design unchanged -- a class-level monkey-patch (`KeltnerBreakoutStrategy.decide`) that silently holds whichever coin's lows were patched last. Any future caller that forgets to re-patch before using this strategy would silently run it against the wrong coin's data, and **a wrong-coin run doesn't always degrade to an obvious 0-trade result** -- it only does that when the two coins' price levels are far apart. `research_round4.py` has since been hardened with a cheap O(1) runtime assertion (`_patch_keltner_lows`'s guarded `decide`) that raises immediately if `series` isn't a prefix of the closes the most recent patch was built from, instead of silently producing signals from misaligned data. Re-verified after hardening: all results (1x pass, 2x/10x leverage, SOL/XRP/ADA held-out) reproduce exactly unchanged.

## 4. The strategy catalog: all 24 distinct ideas

### 4a. The original 6 (pre-registered, frozen in `PREREGISTRATION.md`, committed)

| # | Strategy | Parameters | Origin |
|---|---|---|---|
| 1 | `momentum` | `fast_window=5, slow_window=20` | Already live in the engine |
| 2 | `momentum_regime` | `fast_window=5, slow_window=20` | Already live in the engine |
| 3 | `mean_reversion` | `rsi_period=14, oversold=30, overbought=70` | Already live in the engine |
| 4 | `donchian_breakout` | `entry_window=20, exit_window=10` | Classic ("Turtle Trading" core rule) |
| 5 | `macd_crossover` | `fast_period=12, slow_period=26, signal_period=9` | Classic |
| 6 | `bollinger_mean_reversion` | `window=20, num_std=2.0` | Classic |

### 4b. Round 2 (3 ideas, `research_round2.py`, not committed)

| # | Strategy | Parameters | Mechanism |
|---|---|---|---|
| 7 | `stochastic_mean_reversion` | `window=14, oversold=20, overbought=80` | Close-based %K/%D stochastic oscillator (no true OHLC in saved data, so %K uses rolling max/min of closes, not intrabar highs/lows) |
| 8 | `triple_sma_ribbon` | `fast=5, mid=20, slow=50` | Buy on full 3-way bullish SMA alignment, sell when alignment breaks |
| 9 | `time_series_momentum` | `window=20, entry_threshold_pct=2.0` | Pure rate-of-change, no moving average |

### 4c. Round 3 (3 ideas, `research_round3.py`, not committed)

| # | Strategy | Parameters | Mechanism |
|---|---|---|---|
| 10 | `ichimoku_cross` | `tenkan_window=9, kijun_window=26` | Close-based Tenkan/Kijun midpoint crossover |
| 11 | `linear_regression_channel` | `window=20, num_std=2.0` | Mean-reversion vs. a rolling least-squares trendline (not a flat SMA like Bollinger) |
| 12 | `cci_mean_reversion` | `window=20, oversold=-100, overbought=100` | Commodity Channel Index, scaled by mean absolute deviation rather than standard deviation |

### 4d. Round 4 (3 ideas, `research_round4.py`, not committed) -- **the one pass**

| # | Strategy | Parameters | Mechanism |
|---|---|---|---|
| 13 | `keltner_breakout` | `sma_window=20, atr_window=14, num_atr=2.0` | Volatility-band breakout; ATR proxy = `max(close-low, abs(close-prev_close))` -- the first strategy in this program to use the saved `low` field |
| 14 | `momentum_acceleration` | `window=20` | "ROC of ROC" -- requires the rate of change to be both positive and accelerating |
| 15 | `dual_timeframe_roc` | `short_window=10, long_window=50` | Requires two-horizon ROC agreement (both positive) |

### 4e. Round 5 (3 ideas, `research_round5.py`, not committed)

| # | Strategy | Parameters | Mechanism |
|---|---|---|---|
| 16 | `hull_ma_cross` | `window=20` | Lag-reduced Hull Moving Average crossover |
| 17 | `return_shock_mean_reversion` | `window=20, z_threshold=-2.5, exit_sma_window=5` | Fades an unusually large single-bar down-move |
| 18 | `awesome_oscillator_cross` | `fast_window=5, slow_window=34` | `SMA(5)-SMA(34)` zero-line cross (no EMA smoothing or signal line, unlike MACD) |

### 4f. Round 6 (3 ideas, `research_round6.py`, not committed)

| # | Strategy | Parameters | Mechanism |
|---|---|---|---|
| 19 | `fisher_transform` | `window=10` | Ehlers Fisher log-transform of price's position in a rolling range; trades turning points |
| 20 | `percentile_rank_mean_reversion` | `window=20, lower_pct=10, upper_pct=90` | Ordinal percentile rank of price within its trailing window, not a parametric z-score |
| 21 | `connors_rsi` | `rsi_period=3, streak_rsi_period=2, roc_lookback=100, oversold=10, overbought=70` | Classic composite: short RSI + streak-length RSI + return-rank, averaged |

### 4g. Round 7 (3 ideas, `research_round7.py`, not committed)

| # | Strategy | Parameters | Mechanism |
|---|---|---|---|
| 22 | `vol_regime_momentum` | `fast_window=5, slow_window=20, vol_window=20, vol_lookback=100` | The original momentum crossover, gated to only enter when realized volatility is below its own trailing 100-bar median |
| 23 | `donchian_midline_cross` | `window=20` | Price crossing the midline (not the extreme bands) of a 20-bar Donchian channel |
| 24 | `vol_shock_momentum` | `window=20, shock_multiplier=3.0` | Rides an upside one-bar shock instead of fading it (the mirror-opposite hypothesis of #17) |

**Design constraint applied to every invented strategy:** the live engine recreates a fresh strategy instance on every tick (confirmed by reading `engine/runner.py`), so no strategy here depends on state carried from a previous tick. This ruled out implementing true Parabolic SAR or a true (trailing-band) Supertrend, since both require their own prior value and would cost O(n) per tick / O(n^2) total on this project's ~35,000-candle 1h datasets -- flagged explicitly in the round 4 and round 6 reports as ideas considered and deliberately not attempted, not overlooked.

## 5. Full results, round by round

### 5a. Pre-registration round (24 cells, 1x, years 1-3 exploration / year 4 confirmation)

Full table reproduced from `audits/2026-10-12-research-harness-report.md` (the corrected version):

| Asset | TF | Strategy | Equity Return% | Summed RT Return% | Fees% | TIM% | RT | p | Result |
|---|---|---|---|---|---|---|---|---|---|
| BTC | 1h | momentum | -16.16% | -16.60% | 38.68% | 11.7% | 156 | 0.087 | fail (negative return) |
| BTC | 1h | momentum_regime | -14.70% | -15.01% | 16.55% | 4.6% | 68 | 0.4575 | fail |
| BTC | 1h | mean_reversion | -33.98% | -39.17% | 43.01% | 31.3% | 212 | 0.61 | fail |
| BTC | 1h | donchian_breakout | -50.08% | -67.18% | 55.77% | 18.1% | 282 | 0.4625 | fail |
| BTC | 1h | macd_crossover | -50.00% | -67.75% | 44.24% | 15.2% | 238 | 0.729 | fail |
| BTC | 1h | bollinger_mean_reversion | -39.09% | -48.49% | 38.06% | 12.6% | 198 | 0.5155 | fail |
| BTC | 4h | momentum | -3.09% | +1.24% | 35.99% | 34.4% | 132 | 0.427 | fail |
| BTC | 4h | momentum_regime | -0.40% | +1.30% | 15.73% | 18.4% | 61 | 0.4905 | fail |
| BTC | 4h | mean_reversion | -19.15% | -16.94% | 16.34% | 40.6% | 72 | 0.9375 | fail |
| BTC | 4h | donchian_breakout | +21.67% | +22.92% | 38.23% | 33.8% | 121 | 0.166 | fail (p too high) |
| BTC | 4h | macd_crossover | -28.87% | -30.55% | 41.85% | 38.0% | 183 | 0.714 | fail |
| BTC | 4h | bollinger_mean_reversion | -11.73% | -7.90% | 21.09% | 19.4% | 87 | 0.5335 | fail |
| ETH | 1h | momentum | -15.51% | -14.36% | 57.96% | 18.0% | 266 | 0.008 | fail (negative return) |
| ETH | 1h | **momentum_regime** | **+2.70%** | **+5.92%** | 23.78% | 7.3% | 101 | **0.0195** | **PASS** |
| ETH | 1h | mean_reversion | -50.64% | -65.10% | 51.68% | 38.2% | 264 | 0.686 | fail |
| ETH | 1h | donchian_breakout | -50.02% | -66.47% | 57.24% | 18.4% | 280 | 0.417 | fail |
| ETH | 1h | macd_crossover | -50.17% | -66.61% | 54.82% | 17.1% | 283 | 0.3975 | fail |
| ETH | 1h | bollinger_mean_reversion | -46.15% | -59.06% | 57.07% | 16.9% | 286 | 0.247 | fail |
| ETH | 4h | momentum | -1.04% | +7.54% | 39.47% | 41.5% | 163 | 0.274 | fail |
| ETH | 4h | momentum_regime | +3.24% | +8.52% | 17.71% | 20.2% | 75 | 0.2965 | fail (p too high) |
| ETH | 4h | mean_reversion | -20.39% | -11.71% | 19.89% | 43.0% | 80 | 0.763 | fail |
| ETH | 4h | donchian_breakout | -2.45% | +3.85% | 34.32% | 36.3% | 137 | 0.3195 | fail |
| ETH | 4h | **macd_crossover** | **+27.80%** | **+32.34%** | 58.42% | 47.1% | 213 | **0.075** | **PASS** |
| ETH | 4h | bollinger_mean_reversion | -24.74% | -21.64% | 26.49% | 22.8% | 107 | 0.6075 | fail |

**2 of 24 passed exploration.** Both failed year-4 confirmation:

| Asset | TF | Strategy | Equity Return% | Summed RT Return% | p-value | Result |
|---|---|---|---|---|---|---|
| ETH | 1h | momentum_regime | -17.56% | -18.92% | 0.726 | Did not replicate |
| ETH | 4h | macd_crossover | -35.78% | -43.45% | 0.783 | Did not replicate |

Additional detail an auditor should know: ETH 1h momentum_regime's exploration pass was carried almost entirely by one strong year (year 3 alone: +39.27%; years 1-2 combined: roughly -27%) -- a previously-flagged "one asset in one year, seen twice" pattern, not broad-based evidence. Also, year 4 for this specific cell was not a genuinely fresh holdout (an earlier, separate experiment already tested this exact combination on this exact window). ETH 4h macd_crossover's year-4 test was genuinely fresh. Formal correction (Bonferroni, Benjamini-Hochberg) rejects both passes: Bonferroni-adjusting the best p-value (0.0195) for 24 tests gives ~0.47; Benjamini-Hochberg at q=0.10 would need a rank-1 p-value around 0.004 to pass anything.

### 5b. Round 2 (12 cells, 1x, BTC/ETH exploration only -- no pass, so no held-out test)

| Asset | TF | Strategy | Equity Return% | Summed RT% | Fees% | TIM% | RT | p | Result |
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

**0 of 12 passed.** Held-out SOL/XRP/ADA correctly never touched (no cell earned the right).

### 5c. Round 3 (12 cells, 1x -- no pass)

| Asset | TF | Strategy | Equity Return% | Summed RT% | Fees% | TIM% | RT | p | Result |
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

**0 of 12 passed.**

### 5d. Round 4 (12 cells, 1x -- 1 pass, plus leverage + held-out follow-up)

| Asset | TF | Strategy | Equity Return% | Summed RT% | Fees% | TIM% | RT | p | Result |
|---|---|---|---|---|---|---|---|---|---|
| BTC | 1h | keltner_breakout | -50.09% | -67.14% | 62.30% | 14.6% | 322 | 0.153 | fail |
| BTC | 1h | momentum_acceleration | -50.01% | -68.85% | 46.47% | 1.3% | 252 | 0.0555 | fail (negative return) |
| BTC | 1h | dual_timeframe_roc | -50.06% | -68.21% | 57.19% | 5.9% | 301 | 0.0425 | fail (negative return) |
| **BTC** | **4h** | **keltner_breakout** | **+21.75%** | **+25.29%** | 57.78% | 31.8% | 165 | **0.0435** | **PASS** |
| BTC | 4h | momentum_acceleration | -50.31% | -69.00% | 44.83% | 4.7% | 232 | 0.5595 | fail |
| BTC | 4h | dual_timeframe_roc | -46.17% | -57.62% | 75.44% | 30.0% | 377 | 0.1165 | fail |
| ETH | 1h | keltner_breakout | -50.38% | -67.48% | 60.18% | 14.0% | 306 | 0.2305 | fail |
| ETH | 1h | momentum_acceleration | -50.09% | -68.92% | 53.53% | 1.4% | 279 | 0.011 | fail (negative return) |
| ETH | 1h | dual_timeframe_roc | -50.12% | -68.61% | 45.58% | 4.3% | 236 | 0.4495 | fail |
| ETH | 4h | keltner_breakout | -30.41% | -28.20% | 44.20% | 32.3% | 187 | 0.4225 | fail |
| ETH | 4h | momentum_acceleration | -50.08% | -68.57% | 48.74% | 5.0% | 247 | 0.3625 | fail |
| ETH | 4h | dual_timeframe_roc | -50.20% | -65.89% | 51.96% | 18.0% | 261 | 0.4535 | fail |

**1 of 12 passed: BTC 4h keltner_breakout.** Note the **same signal failed outright on ETH 4h** (p=0.4225, -28.20%), the same timeframe -- an asset-inconsistency red flag.

**Leverage extension (BTC 4h keltner_breakout only):**

| Variant | Return% | Max DD% | Trades | Win rate% | Epoch resets |
|---|---|---|---|---|---|
| 1x | +21.75% | not computed at this stage | 330 legs / 165 RT | n/a | 0 |
| 2x | +17.83% | 66.31% | 330 | 33.9% | 0 |
| 10x | -92.23% | 99.81% | 334 | 33.5% | 4 |

**Held-out confirmation (SOL/XRP/ADA, 4h, keltner_breakout):**

| Asset | TF | Strategy | Equity Return% | Summed RT% | Fees% | TIM% | RT | p | Result |
|---|---|---|---|---|---|---|---|---|---|
| SOL | 4h | keltner_breakout | +41.79% | +50.33% | 83.39% | 32.5% | 195 | 0.103 | fail (p just above 0.10) |
| XRP | 4h | keltner_breakout | -0.17% | +19.92% | 41.95% | 27.1% | 178 | 0.157 | fail |
| ADA | 4h | keltner_breakout | -7.58% | +5.71% | 48.82% | 30.7% | 190 | 0.1365 | fail |

**0 of 3 held-out assets formally passed** (SOL closest, p=0.103). Worth noting for an auditor: all 3 held-out assets showed a positive summed round-trip return, the same direction as the BTC pass -- a directionally consistent result across 4 independent assets, but none individually clears the pre-specified significance bar, and none should be read as confirming the BTC result on its own.

**Correction per the independent audit: this held-out test checks new coins, not new time.** SOL, XRP, and ADA were held out by *coin*, but all three cover the identical 2022-10-13 to 2026-10-12 window as the BTC/ETH exploration data. Keltner's BTC profit came almost entirely from years 1-2 (see §5d-1 below), a rising period for crypto broadly, and correlated coins over that same window share much of that market move -- so this test does not establish that the strategy generalizes to data it has never been exposed to in any form, the way a true out-of-sample holdout would. Combining the three held-out p-values via Fisher's method gives p ~= 0.057 (or 0.083 including ETH's p=0.4225), which **overstates** the evidence because Fisher's method assumes independence and these assets are correlated. Two of the three held-out assets (XRP, ADA) also lost money on an equity basis despite a positive summed round-trip return. By the rule set in advance (p<0.10 AND positive summed return, per asset, independently), 0 of 3 passed, and that is the correct reading.

### 5d-1. Keltner robustness check (added per the independent audit, not in the original dossier)

**Year-by-year breakdown, BTC 4h keltner_breakout** (equity-scale return per calendar year within the 4-year window):

| Year | Equity Return% | p-value |
|---|---|---|
| 1 (2022-10-13 to 2023-10-13) | +17.80% | 0.04 |
| 2 (2023-10-13 to 2024-10-13) | +28.30% | 0.0885 |
| 3 (2024-10-13 to 2025-10-13) | -9.25% | -- |
| 4 (2025-10-13 to 2026-10-12) | -13.13% | -- |

**All of this cell's profit came from years 1-2; years 3-4 both lost money.** The pass is carried by a two-year window, not a persistent edge across the full 4 years it was tested on.

**Settings grid around the passing parameters** (`atr_window=14` held fixed; p-value shown, with the summed-return sign in brackets):

| sma_window \ num_atr | 1.5 | 2.0 | 2.5 | 3.0 |
|---|---|---|---|---|
| BTC 10 | 0.66 (-) | 0.18 (-) | 0.09 (-) | 0.15 (-) |
| BTC 20 | 0.14 (+) | **0.044 (+)** | **0.037 (+)** | **0.059 (+)** |
| BTC 30 | 0.31 (-) | 0.21 (+) | 0.30 (+) | 0.19 (+) |
| BTC 50 | 0.36 (+) | 0.29 (+) | 0.30 (+) | 0.28 (+) |
| ETH (all 16 combinations) | lowest p = 0.154, 9 of 16 negative | | | |

**The pass is a small island at `sma_window=20, num_atr>=2.0`, on one coin only.** Every other `sma_window` tested fails on BTC, and ETH fails at all 16 nearby settings tried (lowest p found: 0.154). A real, structural edge would typically show up across a neighborhood of nearby settings and on a highly correlated second asset; this one doesn't. (These settings were deliberately not run against SOL/XRP/ADA, to keep that held-out set's one confirmed use -- the original frozen parameters only -- clean.)

### 5e. Round 5 (12 cells, 1x -- no pass)

| Asset | TF | Strategy | Equity Return% | Summed RT% | Fees% | TIM% | RT | p | Result |
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

**0 of 12 passed.** No near-misses -- the lowest p-value was 0.143, well above threshold.

### 5f. Round 6 (12 cells, 1x -- no pass)

| Asset | TF | Strategy | Equity Return% | Summed RT% | Fees% | TIM% | RT | p | Result |
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

**0 of 12 passed.** `connors_rsi` produced two very low p-values (0.0035, 0.0045) but both lost ~69% -- the by-now-familiar pattern where heavy fee churn (60%+ of capital on 300+ round trips) makes the random-timing baseline even worse than the real strategy, not the same thing as the real strategy making money.

### 5g. Round 7 (12 cells, 1x -- no pass)

| Asset | TF | Strategy | Equity Return% | Summed RT% | Fees% | TIM% | RT | p | Result |
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

**0 of 12 passed.** Side-note: `vol_regime_momentum` on BTC 1h cut trade count sharply (31 round trips, vs. 200-300+ typical) and produced the smallest loss across all 7 rounds (-3.99%) -- the volatility-regime filter clearly reduces fee churn, but didn't produce a positive or significant result.

## 6. Running tally (corrected)

| Round | Scope | Ideas | Cells | Passed 1x | Survived leverage + held-out |
|---|---|---|---|---|---|
| Pre-registration | BTC/ETH, years 1-3 explore / year 4 confirm | 6 | 24 | 2 | 0 (both failed year 4) |
| Round 2 | BTC/ETH explore only | 3 | 12 | 0 | -- |
| Round 3 | BTC/ETH explore only | 3 | 12 | 0 | -- |
| Round 4 | BTC/ETH explore + leverage + held-out | 3 | 12 | 1 | 0 (failed held-out, 10x catastrophic) |
| Round 5 | BTC/ETH explore only | 3 | 12 | 0 | -- |
| Round 6 | BTC/ETH explore only | 3 | 12 | 0 | -- |
| Round 7 | BTC/ETH explore only | 3 | 12 | 0 | -- |
| **Total** | | **24 distinct ideas** (6 + 3x6) | **96 cells** (24 + 72) | **3 cells across all rounds** (2 pre-registration + 1 round 4) | **0 confirmed** |

**Headline finding, unchanged across the entire program: no strategy tested to date -- 24 distinct ideas, 96 cells, spanning trend-following, mean-reversion, breakout, momentum, oscillator, regime-filter, and pattern-based families -- shows a real, replicable, cost-adjusted edge on BTC or ETH at 1h or 4h, at any leverage level (1x, 2x, or 10x).**

## 7. Multiple-testing context (combined across the whole program)

- **Pre-registration round (24 cells):** formally corrected in the original report. Bonferroni-adjusting the best p-value (0.0195) for 24 tests gives ~0.47 (not significant). Benjamini-Hochberg at q=0.10 passes zero cells.
- **New-idea rounds 2-7 combined (72 cells):** ~~at a p<0.10 threshold with zero real edge anywhere, roughly 7 passes would be expected by chance alone across 72 cells.~~ **Corrected per the independent audit: this ignored the positive-return half of the gate.** The pass gate requires p<0.10 AND a positive summed return, not just p<0.10 alone -- at the observed pass rate (3 of 96 cells overall, 3.1%), the expectation under "no real edge anywhere" is closer to **2** passes across 72 cells, not 7. **Exactly 1 was found** in the 72 new-idea cells -- still within the chance band, just a narrower one than originally stated.
- **The Keltner pass does not survive formal correction.** Bonferroni-adjusting its p-value (0.0435) across the 72-cell new-idea family gives 0.0435 x 72 = **3.1** (nowhere near significant; a corrected p-value must stay <=1.0 to mean anything, and 3.1 reflects just how unremarkable 0.0435 is once 72 chances are accounted for). Benjamini-Hochberg at q=0.10 requires the rank-1 p-value to be below **~0.0014** across 72 cells, or **~0.0010** across the full 96-cell combined family, to pass anything -- 0.0435 is roughly 30-40x too high. **The only cells with a p-value that low belong to strategies that lost money** (e.g. round 3's BTC 1h linear_regression_channel, p=0.001, -59.19% summed return), confirming the gate is correctly rejecting them rather than missing a stronger signal.
- **The true correction family is larger than 96 cells, and possibly larger than can be precisely bounded.** The independent audit's finding #5: the 6 new-idea rounds were **adaptive, not independent draws** -- later rounds' ideas were explicitly built as variants or mirror-opposites of earlier rounds' results (round 7's own docstring states `vol_shock_momentum` is "the mirror-opposite hypothesis" of round 5's `return_shock_mean_reversion"), and round 4's leverage and held-out follow-up tests only ran *because* round 4 happened to pass. Ideas considered and deliberately not attempted (true Parabolic SAR, true Supertrend, both ruled out on performance grounds rather than tested and failed) are part of the real search space too, even though they were never run. **Treat the 24-idea/96-cell count throughout this document as a floor on the true search size, not an exact, correctable family** -- a materially more conservative posture than earlier drafts of this dossier implied.
- **Important non-independence caveat, repeated from the pre-registration round and applicable to every round since:** 1h and 4h cells for the same asset share the same underlying price path, and BTC/ETH tend to move together, so the effective number of independent tests across any given round is smaller than the nominal cell count -- making the "expected by chance" figures above conservative upper bounds on how unusual any given pass really is, in the direction of understating (not overstating) how unremarkable the Keltner pass is.

## 8. What an auditor should specifically check

1. **Verify the corrected idea/cell count** (24 ideas / 96 cells) against the per-round tables in Section 5 -- this document's own count was wrong twice during the live conversation before this correction.
2. **Re-derive the p=0.0435 BTC 4h keltner_breakout result independently**, since it's the only cell that ever passed among the 18 new ideas, and both the 1x pass and the subsequent leverage/held-out follow-up depended on a monkey-patch workaround for injecting the `low` price field that had one real, caught-and-fixed bug already (Section 3, item 5). Confirm there isn't a second instance of the same class of bug still lurking in how `lows` are threaded through for this or similar future strategies.
3. **Check whether the held-out SOL/XRP/ADA datasets are still untouched** by anything other than the one keltner_breakout confirmation test in round 4 -- the value of that held-out set for any future round depends on it never being looked at casually.
4. **Confirm no round committed any strategy script** (`research_round2.py` through `research_round7.py` should all be absent from git history as of this document, consistent with the "pre-registration can't be fully proven" caveat repeated throughout).
5. **Spot-check the pass gate's arithmetic** (p < 0.10 AND positive summed return) against a couple of the "fail (negative return)" rows in Section 5 -- e.g. round 3's BTC 1h linear_regression_channel (p=0.001, summed return -59.19%) -- to confirm the gate is being applied as described, not loosely.
6. **Consider whether continuing to test more technical-analysis variants on this exact BTC/ETH-via-Bitstamp, 1h/4h dataset is likely to be productive**, given 6 consecutive "new idea" rounds spanning every major TA family have produced only 1 pass that itself failed confirmation.

## 9a. Independent audit's recommended action plan (adopted)

Per `audits/2026-10-12-strategy-search-review.md`:

- **Must fix: none.** No live change is supported, and none was made.
- **Done, per this audit:**
  1. `research_round2.py` through `research_round7.py` and their reports committed (closes the provenance gap -- see Section 10).
  2. The Keltner class-level monkey-patch hardened with an explicit alignment assertion rather than redesigned to pass lows as an argument (the latter would require modifying the frozen `research_harness.py`'s `run_cell`, which this program has deliberately avoided throughout to keep that file's provenance clean).
  3. This dossier's §7 multiple-testing math corrected (observed-rate estimate, ~2 not ~7; corrected family stated as >=96 cells, with the adaptive-rounds caveat that even that is a floor).
- **Keltner pre-registration:** a separate, cheap, frozen file, `PREREGISTRATION-keltner.md`, committed alongside this dossier. Fixes exactly `sma_window=20, atr_window=14, num_atr=2.0`, 4h, 1x, on BTC/ETH/SOL/XRP/ADA, scored once in the same 2027-04-12 to 2027-10-12 window as `PREREGISTRATION.md`, with Holm or Benjamini-Hochberg correction applied across the combined 24+5=29-cell forward family (not per-file). Deliberately does **not** move to `num_atr=2.5` or `3.0`, even though both scored a marginally lower p-value on the burned data in the audit's settings grid -- doing so would be fitting to already-examined data, the exact failure mode this freeze exists to prevent.
- **Stop new indicator rounds on this BTC/ETH 2022-2026 data.** Adopted: no round 8 planned. Six rounds have mapped this dataset thoroughly; any further pass found here would be another likely chance hit, not new evidence. If more exploration is wanted before the 2027 scoring window, the audit's suggested alternative is the still-unused 1h SOL/XRP/ADA files, with a short strategy list frozen in a commit *before* running, and the correction method decided in advance.
- **Later / not yet done:** the audit's suggestion to add a time-period robustness check to the pass gate itself (e.g., requiring each half of the tested window to be independently positive, which would have auto-failed the Keltner cell) is a design change to `research_harness.py`'s methodology, not a data correction -- left as a suggestion for any future harness revision, not implemented here since it would change the frozen gate's definition.

## 9. File index

- `PREREGISTRATION.md` (committed) -- the frozen original 6 strategies, pass gate, and scoring-window rules.
- `PREREGISTRATION-keltner.md` (new, committed alongside this dossier) -- the separate, cheap forward freeze for `keltner_breakout`, per Section 9a.
- `research_harness.py` (committed) -- the reusable base harness (`fetch_and_save`, `load_saved`, `run_cell`, `random_timing_pvalue`, `passes`).
- `research_round2.py` through `research_round7.py` (committed alongside this dossier, per Section 9a) -- each round's 3 new strategy implementations plus its own run script. `research_round4.py` additionally carries the hardened Keltner alignment assertion described in Section 3, item 5.
- `audits/2026-10-12-research-harness-report.md` -- pre-registration round, corrected version.
- `audits/2026-10-12-research-harness-review.md` -- the external review that drove the pre-registration round's corrections.
- `audits/2026-10-12-strategy-search-review.md` -- the independent audit of this entire program, whose findings are incorporated throughout this document.
- `audits/2026-10-12-research-round2-report.md` through `audits/2026-10-12-research-round7-report.md` -- each new-idea round's own report.
- `audits/2026-10-12-research-round4-leverage-report.md` -- the round that included the one 1x pass, its leverage extension, and its held-out confirmation.
- `audits/2026-10-12-research-harness-datasets/{BTC,ETH,SOL,XRP,ADA}_{1h,4h}.csv` -- all saved price data, fetched once each.

## 10. Commit / data status

No live engine code was changed by any round in this program. No live ledger or config was touched. `research_harness.py` and `PREREGISTRATION.md` remain exactly as originally committed and frozen. Per the independent audit's recommendation (finding #8: uncommitted scripts meant it couldn't be proven parameters were fixed before results), `research_round2.py` through `research_round7.py`, their per-round reports, this dossier, the audit itself, and `PREREGISTRATION-keltner.md` are being committed together. No new datasets were fetched beyond what is listed in Section 9.
