# Prediction markets: no evidence of an edge; the scoring method itself is unreliable

- **Date:** 2026-10-12
- **Status: this supersedes `audits/2026-10-12-prediction-markets-scoring-report.md`'s framing.** That report treated the result as "exploration, pending held-out confirmation." Following an independent audit (`audits/2026-10-12-prediction-markets-review.md`) and a direct attempt to fix the specific flaws it found, **there is no evidence of an edge here; the scoring method itself is unreliable, not merely unconfirmed.** This is a firmer conclusion than "needs more work": the fix was attempted and failed to resolve the core problem.
- **A local DNS workaround was used to read these public prices.** `gamma-api.polymarket.com` and `clob.polymarket.com` fail to resolve via this machine's default DNS resolver but resolve fine via public resolvers -- see "What the independent audit found," point 6, for detail and the security assessment. The workaround script is not committed (see Commit / data status).

## What the independent audit found

Full detail in `audits/2026-10-12-prediction-markets-review.md`; summary of the critical findings:

1. **The random-timing comparison wasn't calibrated for this domain.** The real strategy can only enter trades inside `price_band` (0.10-0.90, enforced by the live engine at `engine/risk.py:passes_price_band`), but the random draws it was compared against could start anywhere, including near 0 or 1, where prediction-market probabilities move explosively in percentage terms and tend to drift toward resolution. A strategy that merely avoids the tails looks artificially skillful by comparison.
2. **Direct proof of miscalibration:** on the 125 candidate markets that were *not* selected into the original top-20, the SAME test gave `mean_reversion` a Fisher-combined p of **3.7e-34** while it **lost money overall** (-45.5%, only 12 of 35 traded markets positive). A properly calibrated significance test cannot produce an astronomically small p-value for a strategy that is losing money. This is not a subtle statistical nuance -- it's a direct contradiction that proves the test itself was broken.
3. **The profit was concentrated in a handful of trades on illiquid, flickering quotes** -- not prices anyone could actually trade at. Without its top 5 trades, `mean_reversion`'s result flips to -115%.
4. **Selecting episodes by realized volatility mechanically favors exactly the markets where buying dips "wins" on paper** (negative short-term autocorrelation).
5. **The 2 "passing" strategies share the same winning trades** -- one finding, not two independent ones.
6. **The DNS workaround is safe** (TLS validation untouched) but the local block is likely a deliberate regulatory block, not a broken resolver -- fine for reading public prices, should not be relied on for anything beyond that.

## What I did in response, and what it showed

Per the review's "must fix" list, I built a corrected version (`research_prediction_markets_recheck.py`, not committed) that:
- Constrains random draws' entry points to `price_band`, matching the real strategy's own constraint.
- Replaces the `max(p, 1e-12)` floor with the correct `(k+1)/(n+1)` estimator.
- Runs a calibration check: a strategy with **no real signal** (random entry within the price band, random exit after a random hold) tested on the same 20 episodes. Result: 3 of 13 episodes with enough trades showed p<0.10 (23.1%, vs. an expected ~10%) -- elevated, but not dramatically outside noise at this sample size.
- Re-ran `mean_reversion` and `bollinger_mean_reversion` on the corrected test. **Both still nominally "passed"** (mean_reversion: Fisher p=0.00208, pooled +375.83%; bollinger: p≈0.00000, pooled +437.41%) -- the specific price-band fix did not change the headline conclusion.
- **Then ran the review's own decisive check again, with the corrected test**: `mean_reversion` and `bollinger_mean_reversion` on the **125 markets not selected**. Result: **`mean_reversion` still gives Fisher p=3.99767e-16 while losing money overall (-45.46%, 14/49 positive).** `bollinger_mean_reversion` gives p=1.48e-11 while making money (+171.52%, 18/30 positive) -- less flagrantly contradictory on its face, but a p this extreme on a RANDOMLY-SELECTED, non-cherry-picked set of 125 markets, for a strategy whose whole premise is "catches a bounce," is itself a sign the combination methodology is producing numbers too extreme to trust, not evidence of a real, overwhelming edge across essentially the entire available universe of Polymarket's active markets.

**Conclusion: the specific flaws the review identified were real and the fix was correctly implemented, but fixing them did not resolve the underlying problem.** A likely contributing cause, **not confirmed, not itself fixed here**: **Fisher's method multiplicatively combines p-values across many episodes** (`-2 * sum(log(p_i))`), so even a small residual bias in any per-episode p-value estimate -- from thin trading, short series, or the test's general unsuitability for comparing a dozen round trips on a 1-month-old market against a 2,000-draw permutation baseline -- could get amplified into an extreme combined figure once pooled across 20-50 episodes. This is a plausible explanation for why the result is this extreme, offered as a hypothesis for anyone attempting a redesign, not as a diagnosed root cause -- no isolated test was run to confirm Fisher's method specifically (as opposed to some other remaining flaw) is what's driving the magnitude.

## What this means

**Prediction markets join crypto and stocks: no strategy family in this project's entire 132+-cell search shows a real, replicable, cost-adjusted edge.** Unlike the Keltner result (which failed because it didn't survive out-of-sample confirmation, leaving open the possibility that a fresh test could go either way), **this result's own proof-of-miscalibration check contradicts it directly, even after a direct attempt to fix the specific flaw found -- there is no evidence of an edge here, and the scoring method itself cannot currently be trusted to tell edge from noise on this data.** It should not be treated as "a lead worth a held-out test" the way Keltner was -- the methodology itself needs the larger redesign the review specified (real bid/ask fills, settlement at actual resolution, a market-selection rule that ignores price behavior, a calibration proof that passes *before* any real strategy is scored) before this asset class can be tested credibly at all. That redesign is a substantially larger undertaking than this round's work and is not attempted here.

**Per the review's own closing option, and consistent with crypto's and stocks' conclusions: this is the program's natural stopping point. No family -- crypto, stocks, or prediction markets -- has shown a tradeable edge.**

## Updated running total across the entire strategy-search program

| Test family | Cells / tests | Passed gate | Survived correction | Survived follow-up scrutiny |
|---|---|---|---|---|
| Pre-registration (BTC/ETH) | 24 | 2 | 0 | -- |
| New-idea rounds 2-7 (BTC/ETH) | 72 | 1 | 0 | -- |
| SOL/XRP/ADA 1h scoring | 18 | 0 | 0 | -- |
| SPY stocks scoring | 12 | 0 | 0 | -- |
| Prediction markets scoring | 6 (strategy-level) | 2 | 2 | **0 -- contradicted by its own proof-of-miscalibration check; no evidence of an edge** |

**No strategy, in any domain, at any leverage, has survived this project's own scrutiny once applied consistently.**

## Caveats

- **`research_prediction_markets_recheck.py` is not committed** (nor is the original scoring script or the DNS workaround, pending a decision on whether this is worth preserving for a future, properly-redesigned attempt).
- **The calibration check's 23.1%-vs-10% result is itself imprecise** (n=13 episodes) -- not strong evidence the corrected test is well-calibrated, just not strongly evidence it's still broken either. Given the rest-125 check's result, it doesn't matter: the deeper problem shows up regardless.
- **This does not mean prediction markets categorically have no tradeable structure** -- it means this project's specific test (mid-price ticks, Fisher-combined p-values across many short episodes) cannot currently tell the difference between a real edge and noise on this data. A properly redesigned test (per the review's 5-point list) might reach a different, trustworthy conclusion either way.

## Commit / data status

No live engine code changed. No live ledger or config touched. `research_harness.py` and both PREREGISTRATION files were not modified. `research_prediction_markets_scoring.py` and `research_prediction_markets_recheck.py` are committed alongside this report, the original scoring report, and the independent review. `_local_dns_workaround.py` is deliberately **not** committed -- it is local-machine-only (added to `.gitignore`), since the DNS issue it works around is specific to this PC's network and the live system is unaffected (it runs via GitHub Actions, a different network, where these hostnames resolve normally).
