# Audit: full strategy search (change-review, tests)

- **Date:** 2026-10-12
- **Repo/branch:** kubigamingro-pixel/SelfSustainedMoneyMaker, local working tree (HEAD `3427e306`). `research_round2.py` through `research_round7.py` are untracked.
- **Path:** D:\SelfSustainedMoneyMaker
- **Target:** `audits/2026-10-12-full-strategy-search-audit-dossier.md`, `PREREGISTRATION.md`, `research_harness.py`, `research_round2.py` through `research_round7.py`, and the saved datasets
- **Read-only:** no code, config or live ledger was changed. Re-runs used copies of the code and the saved CSVs on Python 3.13 (the pinned version is 3.13.7), with in-memory ledgers.

## Summary

The dossier's conclusion holds: **after 24 ideas and 96 cells, nothing shows a real edge after costs, and nothing should change live.** Everything I re-ran reproduces to the decimal, including the BTC 4h Keltner pass (p = 0.0435) and its leverage and held-out results. The Keltner leverage bug fix is real: I reproduced the original 0-trade bug and confirmed the fix. PREREGISTRATION.md and research_harness.py are unchanged since their commit. The Keltner pass does not survive multiple-testing correction. It is also fragile: all of its profit comes from the first two years, ETH fails at every nearby setting, and on BTC it fails at most nearby settings. Keltner is worth at most a cheap, separate preregistration scored on future data, not more research on this data.

## Findings

| # | Severity | Effort | Type | Area | Finding | Evidence |
|---|---|---|---|---|---|---|
| 1 | High | — | tests | multiple testing | The Keltner pass does not survive correction across the search: Bonferroni gives 3.1 over 72 new-idea cells and 4.2 over 96 cells, and Benjamini-Hochberg passes nothing | p = 0.0435 (reproduced); the BH rank-1 bar at q = 0.10 is 0.0014 for 72 cells and 0.0010 for 96 |
| 2 | High | — | tests | Keltner robustness | All of BTC 4h Keltner's profit comes from years 1-2; years 3-4 lose money | Per year: +17.80%, +28.30%, -9.25%, -13.13% (equity) |
| 3 | Medium | — | tests | Keltner robustness | The pass appears only at sma_window = 20. Every other window fails on BTC, and ETH fails at all 16 settings tried | Settings grid table below |
| 4 | Medium | S | change-review | held-out design | SOL/XRP/ADA cover the same 4 calendar years as BTC/ETH, and crypto moves together, so the held-out test is new coins but not new time | Same 2022-10-13→2026-10-12 window; see Details |
| 5 | Medium | — | change-review | adaptive rounds | The 6 rounds were adaptive (later ideas were chosen from earlier results), so the effective search is larger than 24 ideas | `research_round7.py:8-27` (ideas built as variants and mirrors of earlier ones) |
| 6 | Low | S | change-review | dossier math | "About 7 passes expected by chance in 72 cells" ignores the positive-return condition; the observed pass rate is 3 of 96 (3.1%), which predicts about 2 | Dossier §7; per-round tables |
| 7 | Low | S | tests | Keltner plumbing | The `low` field still reaches Keltner through a class-level monkey-patch, so the same class of bug can recur in any new caller that forgets to re-patch | `research_round4.py:187-191`, `:196`, `:226`, `:264` |
| 8 | Low | S | change-review | provenance | The round scripts are still uncommitted, so it can't be proved that ideas and parameters were fixed before results, and nothing proves no ad hoc look at SOL/XRP/ADA | `git status`; the dossier says this too |
| 9 | Info | — | change-review | prereg | PREREGISTRATION.md, research_harness.py and .python-version are committed in `74d92400` and unchanged since (`git diff HEAD` is empty) | `git log`, `git diff --stat HEAD` |
| 10 | Info | — | tests | bug fixes | All 5 disclosed fixes check out. The p-value fix scales both the price move and the cost by position size. I reproduced the Keltner leverage bug (0 trades when ETH lows feed a BTC replay) and its fix (2x +17.83%, 330 trades; 10x -92.23%, 4 resets) | `research_harness.py:367-379`; re-run output |

## Details

### Reproduction

Every cell I re-ran matches the dossier exactly:

| Cell | Equity % | Summed % | Round trips | p | Dossier |
|---|---|---|---|---|---|
| BTC 4h keltner | +21.75 | +25.29 | 165 | 0.0435 | match |
| ETH 4h keltner | -30.41 | -28.20 | 187 | 0.4225 | match |
| SOL 4h keltner (held-out) | +41.79 | +50.33 | 195 | 0.103 | match |
| XRP 4h keltner (held-out) | -0.17 | +19.92 | 178 | 0.157 | match |
| ADA 4h keltner (held-out) | -7.58 | +5.71 | 190 | 0.1365 | match |

I also added a check inside the Keltner patch that the `lows` slice lines up with the close series on every bar. It found 0 mismatches across all runs, so the strategy does not use future data.

### 1. Multiple testing

The search is at least 96 cells (24 preregistered + 72 new), plus 2 leverage variants and 3 held-out tests. The new-idea family alone gives Bonferroni 0.0435 × 72 = 3.1, which is not significant. Benjamini-Hochberg at q = 0.10 needs a rank-1 p of about 0.0014 (72 cells) or 0.0010 (96 cells). The only cells that low have negative returns (round 3 BTC 1h linear_regression_channel, p = 0.001, -59%). One pass in 72 is what chance alone produces at the observed rate.

### 2–3. Keltner is fragile

BTC 4h by year: year 1 +17.80% (p 0.04), year 2 +28.30% (p 0.0885), year 3 -9.25%, year 4 -13.13%. The edge sits entirely in the 2022-2024 recovery and has been negative for the most recent two years.

Settings grid on the burned BTC/ETH data (atr_window = 14), showing p, with the summed-return sign in brackets:

| sma \ num_atr | 1.5 | 2.0 | 2.5 | 3.0 |
|---|---|---|---|---|
| BTC 10 | 0.66 (−) | 0.18 (−) | 0.09 (−) | 0.15 (−) |
| BTC 20 | 0.14 (+) | **0.044 (+)** | **0.037 (+)** | **0.059 (+)** |
| BTC 30 | 0.31 (−) | 0.21 (+) | 0.30 (+) | 0.19 (+) |
| BTC 50 | 0.36 (+) | 0.29 (+) | 0.30 (+) | 0.28 (+) |
| ETH (all 16) | lowest p 0.154, 9 of 16 negative | | | |

The pass is a small island at sma = 20, num_atr ≥ 2, on one coin. A real edge would usually show up in nearby settings and on ETH. To keep this a pure check of the existing result, I did not run these settings on SOL/XRP/ADA.

### 4. The held-out test checks other coins, not later time

SOL/XRP/ADA were held out by coin, but they cover the same 4 years as the exploration data. Keltner made its money on BTC in years 1-2, a rising period for all crypto, so the same years on correlated coins share much of that market move. Combining the three held-out p-values (Fisher's method) gives p ≈ 0.057, or 0.083 with ETH included. That overstates the evidence, because Fisher's method assumes the three coins are independent and they are not. Two of the three held-out coins also lost money on an equity basis (XRP -0.17%, ADA -7.58%). By the rule set in advance, 0 of 3 passed.

### Were the held-out coins looked at only once?

As far as the files show, yes. Only `research_round4.py:254-269` loads SOL/XRP/ADA. Rounds 2, 5, 6 and 7 load them only after a pass, and none passed. Round 3 has no held-out code. Each round report's printed output is consistent with this. Because nothing is committed, a one-off look outside these scripts can't be ruled out (inferred, not proven). The 4h held-out files are now used once for Keltner. The 1h files are still unused. My reproduction re-ran the same single test, with nothing new selected.

### 5. Adaptive rounds

Each round's 3 ideas were picked after reading the earlier rounds. Round 7 says it outright: `vol_regime_momentum` reworks the regime idea, and `vol_shock_momentum` is "the mirror-opposite hypothesis" of round 5's idea. Round 4's leverage and held-out tests ran only because round 4 passed. Ideas considered and dropped (Parabolic SAR, Supertrend) are part of the search too. Nominal counts are therefore a floor. The fair correction uses at least 96 tests, and even that understates the search.

### 7. The leverage-bug class is still open

The fix re-patches before each leveraged replay (`research_round4.py:196`). The underlying design is unchanged: `KeltnerBreakoutStrategy.decide` is swapped at class level, and the swap holds whichever history was patched last. Any new caller that runs Keltner without patching first (another round, a scoring run, a different coin) silently uses the wrong coin's lows. A wrong-coin run is not always an obvious 0-trade result: it only drops to 0 when the coins' price levels are far apart. Recommended change: pass lows explicitly, for example by having `run_cell` accept an optional lows list. Or add an assertion that `len(_lows)` equals the history length and that the closes match, like the check I added.

## Recommended action plan

**Must fix:** none. No live change is supported, and none was made.

**Quick wins**
1. Commit `research_round2.py` through `research_round7.py` and their reports now. They are already after the results, but committing stops later edits and gives the next audit a fixed reference.
2. Replace the Keltner class-level patch with explicit lows, or add the alignment assertion (finding 7).
3. In the dossier's §7, change "about 7 expected" to the observed-rate estimate (about 2), and state that the corrected family is ≥ 96 tests.

**Next**
4. **Keltner preregistration: yes, but only as a cheap, separate, frozen file.** Commit e.g. `PREREGISTRATION-keltner.md` before any new data arrives. It should fix exactly `sma_window=20, atr_window=14, num_atr=2.0`, 4h, 1x, on BTC, ETH, SOL, XRP and ADA, with the same gate and scoring window as `PREREGISTRATION.md` (2027-04-12 to 2027-10-12). It should also name the correction up front: Holm or BH across all 24 + 5 forward cells. Do not move to num_atr = 2.5 because it looked better above; that would be fitting to burned data. Expect it to fail: the year-by-year and settings results suggest noise. It costs one commit and is the only honest way to settle it.
5. Stop new indicator rounds on this BTC/ETH 2022-2026 data. Six rounds have mapped it, and each further pass will be another chance hit. If you want more tests before 2027, use the untouched 1h SOL/XRP/ADA files once, for a short list frozen in a commit first, and decide the correction before running.

**Later**
6. Add a check by time period to the gate (for example, each half of the window must be positive), so a result that rests on one market phase, like finding 2, fails automatically.

## Not checked

- Rounds 2, 3, 5, 6 and 7 were not re-run cell by cell. I re-ran round 4 (Keltner, the only pass) and relied on my earlier re-run of the preregistered round. The other rounds' tables were spot-checked for gate arithmetic (for example BTC 1h linear_regression_channel: p 0.001 with a negative return is a correct fail).
- Year-by-year and settings checks were run only on BTC/ETH. SOL/XRP/ADA were not re-sliced, to avoid using the held-out data further.
- Gitleaks and Semgrep were not run: the change is research scripts with no deploy surface, and the scanners were run in the earlier security audit of this repo.
- I could not prove that no ad hoc command looked at SOL/XRP/ADA outside the scripts (nothing is committed).
