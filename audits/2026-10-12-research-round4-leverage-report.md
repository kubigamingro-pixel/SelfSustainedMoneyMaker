# Research round 4: 3 more new strategy ideas, one 1x pass, leverage + held-out check

- **Date:** 2026-10-12
- **Request:** continuation of "use the harness and find new strategies that we can use with 1x 2x and 10x" after round 3 found nothing. This round found one cell that clears the 1x gate and pushes it through the full chain this project's methodology requires: leverage extension (2x/10x) and held-out confirmation (SOL/XRP/ADA), before drawing any conclusion.
- **Code:** `research_round4.py` (new, standalone, not committed). Same reuse pattern as rounds 2-3: imports `research_harness.py`'s `load_saved`/`run_cell`/`passes` unchanged, and `backtest_all.py`'s `replay`/`summarize` for the leverage stage. **`research_harness.py` and `PREREGISTRATION.md` were not modified.** The 3 new strategies are registered into `engine.runner.STRATEGIES`/`engine.leveraged_runner.STRATEGIES` in-process only.

## The 3 new strategies, listed up front (before any were run against data)

1. **Keltner Channel breakout**: middle = SMA(close, 20); band = middle ± 2×ATR(14), where ATR uses a true-range proxy of `max(close - low, abs(close - prev_close))` — this is the first strategy in this project's research rounds to actually use the saved `low` field rather than only close. Buy when close breaks above the upper band (not holding); sell when close drops back below the middle line (holding).
2. **Momentum acceleration ("ROC of ROC")**: buy when the 20-bar rate of change is positive AND increasing versus the prior bar; sell when that acceleration turns negative. Distinct from round 2's plain ROC strategy, which only checked the level.
3. **Dual-timeframe ROC confirmation**: buy only when a 10-bar ROC and a 50-bar ROC are simultaneously positive; sell when either turns negative.

## Exploration results (12 cells: 3 strategies x 2 assets x 2 timeframes, 1x)

| Asset | TF | Strategy | Equity Return% | Summed RT Return% | Fees% | TIM% | Round trips | p-value | Result |
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

**1 of 12 cells passed: BTC 4h keltner_breakout** (p=0.0435, +25.29% summed round-trip return). Two other cells (BTC 1h momentum_acceleration p=0.0555, BTC 1h dual_timeframe_roc p=0.0425) had low p-values too but **lost money** (-68.85% and -68.21% round-trip) — same pattern flagged in every prior round: a low p-value there means the random baseline did even worse under heavy fee churn (46-57% fees on 250-300 round trips), not that the strategy made money. The two-part gate correctly rejects both.

**Note the asset inconsistency**: keltner_breakout passed on BTC 4h but failed outright on ETH 4h (p=0.4225, -28.20%) — the same signal on a highly correlated asset at the same timeframe went the opposite direction, which is a yellow flag for the pass being noise rather than a structural edge.

## Leverage extension (2x/10x, BTC 4h keltner_breakout only)

First run had a bug (the Keltner strategy's custom `low`-field monkey-patch wasn't re-applied before the leveraged replay, so it silently reused ETH 4h's low values and produced 0 trades) — fixed and rerun before reporting:

| Variant | Return% | Max DD% | Trades | Win rate% | Epoch resets |
|---|---|---|---|---|---|
| 1x | +21.75% | (not computed at this stage) | 330 legs / 165 RT | n/a | 0 |
| 2x | +17.83% | 66.31% | 330 | 33.9% | 0 |
| 10x | -92.23% | 99.81% | 334 | 33.5% | 4 |

**2x roughly preserves the headline return but with a 66% max drawdown** — more than double what a trader would tolerate for a strategy whose win rate is only 34%. **10x is catastrophic**: -92.23%, 4 separate loss-cap breaches (liquidation-and-reset cycles), consistent with every other leveraged result this project has produced. Even taking the 1x pass at face value, this is not a strategy you'd want running at 10x, and 2x only survives on raw return while carrying drawdown risk most of this project's prior-tested ideas never approached.

## Held-out confirmation on SOL/XRP/ADA 4h (per the round-2 rule)

A cell that passes exploration earns exactly one test per held-out asset, before any conclusion is drawn:

| Asset | TF | Strategy | Equity Return% | Summed RT Return% | Fees% | TIM% | Round trips | p-value | Result |
|---|---|---|---|---|---|---|---|---|---|
| SOL | 4h | keltner_breakout | +41.79% | +50.33% | 83.39% | 32.5% | 195 | 0.103 | fail (p just above 0.10) |
| XRP | 4h | keltner_breakout | -0.17% | +19.92% | 41.95% | 27.1% | 178 | 0.157 | fail |
| ADA | 4h | keltner_breakout | -7.58% | +5.71% | 48.82% | 30.7% | 190 | 0.1365 | fail |

**0 of 3 held-out assets formally pass the p<0.10 gate.** The closest, SOL, misses by a hair (p=0.103). **Worth noting honestly: all 3 held-out assets, like BTC, show a positive summed round-trip return** — a directionally consistent result across 4 independent assets is more than this project's prior rounds produced, and is not nothing. But by the pre-specified rule, a near-miss is still a fail, and none of the 3 confirms the pattern at the agreed significance level.

## Multiple-testing context (across all "new idea" rounds, not pre-registered)

Rounds 2, 3, and 4 together have now tested 3×12 = 36 new-strategy cells (not counting the original 24 pre-registered ones). At a p<0.10 threshold with zero real edge anywhere, roughly 3-4 passes would be expected by chance across 36 cells — **finding exactly 1 is within, not above, that chance band.** Combined with the failed held-out confirmation and the asset inconsistency (BTC pass, ETH fail at the same timeframe), **this does not clear the bar this project has used throughout to call something a real, replicable edge.**

## What this means for "strategies we can use with 1x, 2x and 10x"

**Still none.** BTC 4h keltner_breakout is the most interesting single result across 15 strategy ideas tested so far (consistent positive direction on 4 assets, a leverage-extension pipeline that now works end-to-end), but it fails formal held-out confirmation, falls within the chance rate expected from 36 cells tested, shows a concerning asset inconsistency at the same timeframe it passed on, and — even if real — is destroyed at 10x and only marginally viable at 2x given a 66% drawdown on a 34% win rate. **Recommendation: do not deploy this to any live track.** If this idea is worth pursuing further, the honest next step would be a fresh pre-registration (freezing keltner_breakout's exact parameters now, before looking at any more data) and scoring it once on new data months from now — exactly the discipline `PREREGISTRATION.md` already established for the original 6 strategies.

## Caveats

- **Keltner's ATR proxy uses the saved `low` field but still approximates true range** (no high field available, so gap-up true range can't be captured the textbook way).
- **Bitstamp, not Kraken**, standing caveat for every crypto replay in this series.
- **The p-value test remains the same cheap approximation** documented in prior rounds (overlapping random draws, no loss-cap stop on the random side, summed not compounded returns).
- **The leveraged-replay bug found and fixed mid-round** (stale `low` values from the wrong asset silently producing 0 trades) is a reminder that this round's leverage-extension code, while now verified to produce non-trivial trade counts, has not been independently reviewed the way `backtest_all.py`'s leveraged replay was in earlier rounds.
- **Whether these 3 strategies' parameters were fixed before any result was seen cannot be fully proven**, since `research_round4.py` is not committed as of this report — same limitation flagged every round.

## Commit / data status

No live engine code changed. No live ledger or config touched. `research_harness.py` and `PREREGISTRATION.md` were not modified. `research_round4.py` is new and uncommitted. No new datasets fetched — reused the existing BTC/ETH/SOL/XRP/ADA 1h/4h CSVs already saved under `audits/2026-10-12-research-harness-datasets/`.
