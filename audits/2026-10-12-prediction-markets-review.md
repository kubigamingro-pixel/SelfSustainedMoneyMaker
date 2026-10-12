# Audit: prediction-markets scoring (change-review, tests)

- **Date:** 2026-10-12
- **Repo/branch:** kubigamingro-pixel/SelfSustainedMoneyMaker, local working tree (HEAD `4591211f`); `research_prediction_markets_scoring.py` and `_local_dns_workaround.py` are untracked
- **Path:** D:\SelfSustainedMoneyMaker
- **Target:** `audits/2026-10-12-prediction-markets-scoring-report.md`, `research_prediction_markets_scoring.py`, `_local_dns_workaround.py`, and the 150 saved CSVs in `audits/2026-10-12-prediction-markets-research-datasets/`
- **Read-only:** no code, config or live ledger was changed, and nothing was fetched. The re-runs loaded the saved CSVs into copies of the code (Python 3.10 on your PC's Linux shell), with in-memory ledgers.

## Summary

**The mean-reversion "pass" is not evidence of an edge.** I found no code bug or look-ahead behind p ≈ 1e-6, and the re-run reproduces the report (Fisher p 0.0029 and 1.1e-6, pooled +376% and +442%). The problem is what the numbers measure. The random-entry test is not calibrated on this data. On the 125 markets that were *not* picked, mean_reversion gets a Fisher p of **3.7e-34 while losing money overall** (-45.5%, only 12 of 35 markets positive). The profit itself comes from a handful of trades on thin, in-play order books. Without its top 5 trades, mean_reversion is **-115%**. The best trades buy at quote levels that look like flicker in an empty order book (e.g. 13.5¢ → 48¢ and back, repeatedly, days before a football match), not prices anyone could trade at. Nothing here supports a live change, and a real confirmation needs a new design, not just fresh markets.

## Findings

| # | Severity | Effort | Type | Area | Finding | Evidence |
|---|---|---|---|---|---|---|
| 1 | Critical | M | tests | p-value null | Real trades are blocked outside the 0.10-0.90 price band, but random draws can start anywhere, including near 0 where % moves explode, and long random draws bleed in markets drifting to 0 | `engine/runner.py:120`; `research_harness.py:367-379` has no band; 44 of 145 markets touch <5¢, and 68 end below their start |
| 2 | Critical | — | tests | calibration | Proof the test is miscalibrated: on the 125 markets not selected, mean_reversion gets Fisher p 3.7e-34 and bollinger 1.3e-25 while mean_reversion loses money | Re-run: rest125 mean_reversion pooled -45.5%, 15 of 35 markets p<0.10 |
| 3 | High | S | tests | p = 0 handling | A per-market p of exactly 0 (none of 2000 draws beat the real result) is clamped to 1e-12, which alone adds about 55 to the Fisher statistic | `research_prediction_markets_scoring.py:148`; 3 markets with p = 0 in rest125. It should be (k+1)/(n+1), a floor of about 0.0005 |
| 4 | High | — | tests | fills | Results rest on fills at the CLOB history price in thin books, a few trades carry everything, and several look like empty-book quote flicker | Top 5 trades = 129% of mean_reversion's total; Oviedo O/U 5.5 flips 0.48↔0.135/0.075 again and again pre-match; 130 one-tick spikes (>5¢ out and back) across the 20 markets |
| 5 | High | S | change-review | selection | Ranking markets by tick-to-tick volatility mechanically favors noisy, flickering books, which is exactly where buying dips "wins" and trend-following "loses" | `research_prediction_markets_scoring.py:204-210`; lag-1 autocorrelation as low as -0.59 among the 20 picks |
| 6 | Medium | — | tests | correction | Correcting across 6 strategies is too narrow, and the 2 "passes" are one finding: both strategies share the same big trades (Oviedo, Thunder vs Hawks, Al Ahli) | Bonferroni over the program's 132 tests: 0.0029 × 132 = 0.38 (fails). Bollinger still clears it numerically, but finding 2 makes the p-value itself untrustworthy |
| 7 | Medium | S | tests | independence | 3 dependent pairs sit inside the 20 picks: the CS 6666 map 2 and BO3 markets (same match), the US x Yemen ceasefire Oct 12 and Oct 15 markets (nested), and BTC >$80k and >$82k (same price) | Episode list in the report |
| 8 | Medium | — | change-review | settlement | Markets are cut at the fetch time, not at resolution; open positions are marked at the last history price and dropped from "summed return" | `research_harness.py` `summed_rt_return_pct`; not hiding losses here, since equity-basis totals are higher (MR +499% vs +376% summed) |
| 9 | Low | S | change-review | data | Duplicate timestamps in 10 CSVs collapse in the timestamp→index map used by the p-value test | e.g. `Real_Oviedo_vs__SD_Eibar__O_U_5_5_…csv` |
| 10 | Low | S | change-review | report | Internal inconsistencies: mean_reversion p is 0.0029 in the table but "0.0011" in the duplicate-removal check, and "8 episodes with trades" where the re-run finds 9 p-values | Report "Results" vs "Limitations #3" |
| 11 | Info | — | security | DNS workaround | Safe for TLS: only `getaddrinfo` is patched, requests still sends the real hostname for SNI and verifies the certificate against it, and no `verify=False` exists anywhere. The failure mode is safe: a stale IP fails TLS loudly | `_local_dns_workaround.py:27-34`; grep for `verify=`/`CERT_NONE` empty |
| 12 | Info | — | security | DNS cause | The local NXDOMAIN is probably a deliberate block, not a broken resolver. Several countries' regulators block Polymarket at DNS level (inferred, not checked). Reading public price data is low-stakes, but this matters before anything like real trading | `_local_dns_workaround.py:1-7` |

## Details

### Reproduction

From the saved CSVs: 145 qualifying markets, the same top 20 by volatility, and mean_reversion Fisher p 0.00290 with pooled summed +375.8% (exact match). bollinger_mean_reversion gives Fisher 1.1e-6 and +442% vs the report's 1.4e-6 and +437%. That gap is likely the Python-version float difference found earlier (my shell is 3.10, the run used 3.13). The trend strategies match exactly.

### 1–3. Why p ≈ 1e-6 is real arithmetic but not evidence

There is no look-ahead: decisions use the tick's own price, the same as every earlier round. The Fisher function is a correct closed form. The issue is the null:

- The real strategy can only enter between 0.10 and 0.90 (`price_band`, `engine/runner.py:120`). The random draws (`research_harness.py:367-379`) can start anywhere, including at 0.0005, where one tick is a +100% move, or in a market sliding to 0, where a long position loses almost everything. Prediction markets drift: 68 of 145 ended below their start. A strategy that merely stays out of the tails "beats random".
- The re-run on the **125 markets not selected** shows this directly. mean_reversion gets Fisher p **3.7e-34** with a **-45.5%** pooled return (12 of 35 markets positive), and bollinger gets 1.3e-25. A calibrated test cannot give a p that small for a strategy that loses money.
- Three of those markets had p = 0 exactly. The `max(p, 1e-12)` clamp turns each into a contribution of 55 to the chi-square statistic. With 2,000 draws the honest floor is (0+1)/2001 ≈ 0.0005.

In the top 20, no market had p = 0, so the clamp is not what drives 1.1e-6. The miscalibration in the first bullet is.

### 4. Fills and thin books

The CLOB `prices-history` series is a single price per 10 minutes, with no bid/ask and no size. The re-run shows:

- **Concentration:** mean_reversion's 61 round trips sum to +399% (same-tick fills, equity scale). The top 5 trades are 129% of that, and the other 56 trades sum to **-115%**. For bollinger, the top 5 are 76% of the total.
- **Flicker:** the biggest trade, Real Oviedo vs SD Eibar O/U 5.5, buys at 0.135 and sells at 0.48. In the days before the match, that market's price flips between about 0.48 and 0.135/0.075 dozens of times (rows 785-916 of the CSV). That is what a midpoint does when one side of an empty book disappears and comes back. It is not a price where anyone could buy size. Across the 20 markets there are 130 one-tick spikes (>5¢ out and back).
- **In-play jumps:** the other top trades are live games (Thunder vs Hawks, Al Ahli, Counter-Strike), where a score swings the price by 30-60¢. On these markets real books are often pulled or delayed during play.
- Smoothing one-tick spikes (median of 3) or filling on the next tick barely changes the result (Fisher 7e-4 / 1.5e-5). So the flicker lasts several ticks, which matches empty-book quotes better than real trades.

The 2% fee + 0.5% slippage assumption is a percentage of price. On a 13.5¢ contract, a single 1¢ spread is already 7.4% each way.

### 5. Selection by volatility

`volatility = RMS of tick-to-tick changes` (`:204-206`) is highest for series with lots of back-and-forth noise. Back-and-forth noise is exactly negative short-term autocorrelation, which mean reversion profits from on paper and trend-following loses on (lag-1 autocorrelation ranges from -0.59 to +0.17 in the picks). The selection does more than look at the same data: it picks *for* the property that produces the result.

### 6. Correction

Across the program (132 tests), Bonferroni on mean_reversion gives 0.38, so it fails. Bollinger's 1.1e-6 would clear any multiple-testing correction numerically, but finding 2 shows the per-market p-values are not valid probabilities on this data, so no correction can rescue them. The two strategies also share the same winning trades, so they count as one finding, not two.

### 11–12. DNS workaround

The patch maps `gamma-api.polymarket.com` and `clob.polymarket.com` to `104.18.34.205` inside `socket.getaddrinfo`. urllib3 still sets SNI and checks the certificate against the URL's hostname, and nothing disables verification, so TLS protection is unchanged. If the IP goes stale or is wrong, the connection fails rather than silently trusting a different server. It is only used by this research script. One point to know: a resolver returning NXDOMAIN only for Polymarket hosts, while public resolvers answer, looks like a deliberate block (some national regulators block Polymarket this way). That's an inference I did not verify. Fetching public prices is low-stakes, but don't build anything that trades from this machine on top of it.

## What a genuine confirmation would need

1. **Fix the test first.** The random draws must obey the same price band and cost filter as the real strategy, and use (k+1)/(n+1) for p-values. Check calibration before scoring anything: run the test on shuffled or random-signal strategies over the same markets and confirm that about 10% of markets land below 0.10.
2. **Use tradeable prices.** Score fills against recorded bid/ask, entering at the ask and exiting at the bid, with a minimum book depth. Or record order books going forward the way the live track already polls prices. A mid-price series cannot confirm a mean-reversion edge.
3. **Freeze selection without the outcome variable.** Pick markets by rules that ignore price behavior (e.g. every market above a 24h-volume and spread threshold at a fixed time, with near-duplicate and same-event markets collapsed to one), and write that down in a commit.
4. **Settle at resolution.** Hold each market to its actual resolution (0 or 1) or to a fixed exit time chosen in advance.
5. **Score once on markets that don't exist yet.** Commit steps 1-4 plus `mean_reversion` and `bollinger_mean_reversion` at their current parameters as a third preregistration, then score markets that open after the commit. Because these markets live days to weeks, a 1-3 month window could be enough. Treat the two strategies as one hypothesis, and correct across the whole program.

## Recommended action plan

**Must fix (before any further prediction-market claims)**
1. Make `random_timing_pvalue` respect `price_band`, and replace the `1e-12` clamp with (k+1)/(n+1).
2. Add a calibration check (null strategy → about 10% of p-values below 0.10) to the harness, and fail the run if it doesn't hold.
3. Correct the report's headline: "survives correction" should read "artifact of an uncalibrated test on mid-price data". Fix the 0.0029 vs 0.0011 and 8 vs 9 inconsistencies.

**Quick wins**
4. Drop duplicate timestamps on load, and collapse same-event markets before combining.
5. Commit `research_prediction_markets_scoring.py` and `_local_dns_workaround.py` with the report, so the next review has a fixed reference.

**Next**
6. If you want to pursue this, write the preregistration in "What a genuine confirmation would need" and start recording bid/ask snapshots now. Otherwise, the program's "natural stopping point" is a fair call: no family has shown a tradeable edge.

## Not checked

- I did not re-fetch markets or check live order books, so the claim that the biggest trades sit on untradeable quotes is inferred from the shape of the price series. No bid/ask data was saved.
- The re-runs used Python 3.10. The trend strategies and mean_reversion matched exactly, and bollinger matched within about 1%.
- Whether Polymarket is blocked by a regulator where you are was not verified.
- Gitleaks and Semgrep were not run: two research scripts with no secrets or deploy surface. A manual check found no credentials and no TLS bypass.
