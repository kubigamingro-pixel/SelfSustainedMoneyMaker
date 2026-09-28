# Performance Review Context — SelfSustainedMoneyMaker

**Purpose of this document:** everything a strategy/quant auditor needs to analyze why
this paper-trading system's win rates and returns are low, without re-litigating
engineering correctness (that's already been extensively verified — see "Engineering
status" below). This is real performance data from a live, running system, not a
backtest.

**As of:** 2026-09-28, day 6 of live paper-trading. All figures below are exact, pulled
directly from the system's SQLite ledgers.

---

## 0. Update (2026-09-28): external audit results and corrections made

This document was sent to five independent AI models (ChatGPT, Deepseek, Claude, Grok,
Kimi) for review. Two real errors in this document's original analysis were caught and
are corrected below:

1. **The "+2.21% net positive raw returns" claim for `prediction_markets_momentum` (old
   section 3) was mathematically wrong.** Summing sequential percentage returns is not
   valid — they must be compounded. Correctly compounded, the raw (pre-cost) return
   across the 7 round trips is **≈ -0.3%**, roughly flat/slightly negative, not
   positive. The honest story is not "good calls ruined by fees," it's "no real edge to
   begin with, and then fees made it much worse."
2. **The win-rate metric itself had a real bug**, independent of the audit's own
   approximations: `scoreboard.py`'s `win_rate()` compared equity *right after* each BUY
   (which already has that trade's own buy-side cost baked into the baseline) instead of
   equity *before* the BUY (the honest "would I have been better off not trading"
   baseline). This overstated win rates. Corrected win rates (verified exactly against
   the ledger, not approximated): `crypto_mean_reversion` **40%** (was reported 60%),
   `prediction_markets_momentum` **14.3%** (was reported 43%). `crypto_momentum`'s 0%
   was already correct either way.

**Changes made in response to the audit consensus** (all five models converged hard on
the same priority order — see section 8 for the full findings):

- Fixed the `win_rate()` bug above.
- Replaced all-in position sizing with a fixed 50% of cash per trade, identical across
  all four tracks (deliberately not tuned per-track — the point is capping single-trade
  variance, not optimizing a number on this small dataset).
- Added a cost-aware entry filter: a BUY signal is now skipped unless the recent
  realized price range (20-bar) is at least 2x the round-trip cost. This directly
  targets the "trading moves smaller than the cost of trading" problem the audit
  identified in `crypto_momentum` and `prediction_markets_momentum`.
- Explicitly did **not** touch SMA windows, RSI thresholds, or add a stop-loss level —
  every auditor was unanimous that doing so now would be curve-fitting to 45 trades.
- Did **not** change the Polymarket cost model yet — the audits disagreed with each
  other on whether 2%+0.5% is too high or too low (real Polymarket fees follow
  `fee = C × feeRate × p × (1-p)`, and spread on 5-6¢ contracts may dominate either way);
  this needs real order-book data to resolve, not a guess.

**Everything below this point is the original pre-correction document**, left intact
as the historical record the audits actually reviewed, except where a correction is
explicitly marked.

---

## 1. What this system is

Four independent paper-trading "tracks," each starting from a simulated $10, run in
parallel to compare whether simple technical strategies hold up against real live
market data. No real money, no real exchange accounts — trade execution is fully
simulated (a market fill at the observed price plus an assumed slippage %, plus a fee %
on notional), but every price used is a real, live price from a real public market.
Runs every 15 minutes via GitHub Actions, has been running continuously since
2026-09-23.

| Track | Market | Data source | Strategy |
|---|---|---|---|
| `crypto_momentum` | BTC/USD, Kraken | live OHLCV via `ccxt` | SMA(5) vs SMA(20) crossover |
| `crypto_mean_reversion` | ETH/USD, Kraken | live OHLCV via `ccxt` | RSI(14), buy <30, sell >70 |
| `stocks_momentum` | SPY | live OHLCV via `yfinance` | SMA(5) vs SMA(20) crossover |
| `prediction_markets_momentum` | Polymarket (highest-volume active market, auto-selected) | live implied probability via Gamma API | SMA(5) vs SMA(20) crossover on probability |

All four use the **same generic strategy code** (`engine/strategies/momentum.py`,
`engine/strategies/mean_reversion.py`) — the only differences between tracks are the
market/data source and the fee/slippage assumptions below. **As of the correction above,
each BUY invests 50% of available cash** (was 100% at the time of the data below — see
section 0). Long-only — there is no short-selling anywhere in the system.

### Cost assumptions per track (this matters a lot — see section 3)

| Track | Fee (per side) | Slippage (per side) | Round-trip cost (~2x) |
|---|---|---|---|
| `crypto_momentum` / `crypto_mean_reversion` | 0.26% | 0.05% | ~0.62% |
| `stocks_momentum` | 0.00% | 0.02% | ~0.04% |
| `prediction_markets_momentum` | 2.00% | 0.50% | ~5.00% |

### Risk controls currently in place

- **Loss cap**: if a track's equity falls below 50% of its starting capital, it
  flattens its position and **permanently stops trading** (hard stop, no recovery).
  None of the four tracks are close to this (worst is at ~75% of starting capital).
- **No take-profit.** Positions only close on the strategy's own exit signal (death
  cross / RSI>70) or the loss cap above. This was a deliberate choice (see section 5).
- **No stop-loss per trade** — only the account-level loss cap. A single trade can run
  arbitrarily far against the position with no per-trade circuit breaker.
- **Position sizing**: fixed 50% of cash per trade (was 100%/all-in at the time of the
  data below — see section 0).
- **Cost-aware entry filter** (added in the correction above): a BUY is skipped if the
  recent 20-bar realized range is below 2x the round-trip cost.

---

## 2. Data as originally reviewed by the audit (2026-09-28, day 6, pre-correction)

**Win rates here are as originally reported — see section 0 for the corrected values.**

| Track | Balance | Return | Trades | Win rate (reported) | Win rate (corrected) | Max drawdown | State |
|---|---|---|---|---|---|---|---|
| `crypto_momentum` | $9.10 | -8.96% | 19 | 0% (0/9 closed) | 0% (unchanged) | 9.05% | holding (open long) |
| `crypto_mean_reversion` | $9.41 | -5.91% | 11 | 60% (3/5 closed) | **40% (2/5)** | 6.13% | holding (open long) |
| `stocks_momentum` | $10.06 | +0.55% | 1 | n/a (still open) | n/a | 0.29% | holding (open long) |
| `prediction_markets_momentum` | $7.53 | **-24.66%** | 14 | 43% (3/7 closed) | **14.3% (1/7)** | 26.36% | flat |
| **Combined** | **$36.10 / $40.00** | **-9.74%** | 45 | — | — | — | — |

---

## 3. Round-trip-by-round-trip breakdown (the actual evidence)

This is the important part — raw price movement per closed round trip, **before**
fees/slippage are applied, so you can see how much of the loss is "the strategy called
it wrong" vs. "the trade was right but costs ate it."

### `crypto_momentum` (SMA 5/20 crossover, BTC/USD)

| # | Buy price | Sell price | Raw price return |
|---|---|---|---|
| 1 | 84324.44 | 84357.10 | +0.039% |
| 2 | 84211.38 | 83654.55 | -0.661% |
| 3 | 84450.30 | 84225.97 | -0.266% |
| 4 | 84447.40 | 84446.36 | -0.001% |
| 5 | 84110.73 | 83877.84 | -0.277% |
| 6 | 84076.82 | 83884.84 | -0.228% |
| 7 | 84044.60 | 83974.99 | -0.083% |
| 8 | 84139.15 | 83958.00 | -0.215% |
| 9 | 84594.18 | 84629.06 | +0.041% |

**Average raw price return per round trip: -0.184%.** Only 2 of 9 round trips even
had positive raw price movement, and both were smaller than the ~0.62% round-trip cost.
**BTC has been trading in an essentially flat/rangebound band (~$83,700–$85,100) for the
entire test window (~1.7% wide).** The audit consensus: this is genuine fast-SMA
whipsaw in a non-trending market, arithmetic (moves too small to cover costs) more than
a signal-quality problem — 0/9 doesn't prove "SMA momentum fails on BTC," only that
this parameterization/timeframe/regime combination is losing. One model (Kimi) flagged
that timing lag in a confirmed-bar crossover and a rangebound regime are "the same
problem" here — a lagged signal costs little in a trend and everything in a range — so
they don't need to be separated to know the fix (a regime/cost filter), even though the
data can't cleanly separate them.

### `crypto_mean_reversion` (RSI 14, buy<30/sell>70, ETH/USD)

| # | Buy price | Sell price | Raw price return |
|---|---|---|---|
| 1 | 2749.65 | 2671.34 | **-2.848%** |
| 2 | 2646.23 | 2660.94 | +0.556% |
| 3 | 2682.76 | 2710.83 | +1.046% |
| 4 | 2690.48 | 2688.73 | -0.065% |
| 5 | 2686.34 | 2693.50 | +0.267% |

**Corrected reading (see section 0): only 2 of 5 round trips were actually net winners
once compared against the honest pre-trade baseline** (trades 2 and 3 — trade 5's tiny
+0.267% raw move didn't clear real costs once measured correctly). The single large
loss (#1, -2.85%) is still much larger than the real wins combined. Audit consensus:
this is a "falling knife" trade — RSI<30 doesn't mean "the bottom," and in a downtrend
RSI can stay oversold a long time. With only 5 closed trades, there is no statistical
basis to tighten the RSI thresholds off this sample; the structural fix (already made)
is position sizing, so one bad trade can't dominate the whole track.

### `prediction_markets_momentum` (SMA 5/20 crossover on Polymarket implied probability)

| # | Buy price | Sell price | Raw price return |
|---|---|---|---|
| 1 | 0.055275 | 0.054725 | -0.995% |
| 2 | 0.052762 | 0.049750 | -5.710% |
| 3 | 0.055275 | 0.065173 | **+17.906%** |
| 4 | 0.065827 | 0.057710 | **-12.331%** |
| 5 | 0.062310 | 0.061192 | -1.793% |
| 6 | 0.063315 | 0.065173 | +2.934% |
| 7 | 0.062310 | 0.063680 | +2.199% |

**Correction (see section 0): the simple sum of these returns (+2.21%) is not a valid
aggregation.** Correctly compounded, the raw pre-cost return is **≈ -0.3%** — roughly
flat, not positive. So this was never "good calls ruined by fees" — the signal itself
had essentially zero edge, and a ~5% round-trip cost applied 7 times (~30-35%
cumulative drag) then turned that flat performance into -24.66%. This remains the
single clearest, most actionable finding in the dataset, just for a worse reason than
originally stated. Multiple models independently noted the cost *model* itself is
probably mis-specified (not necessarily mis-*sized*): real Polymarket fees follow
`fee = C × feeRate × p × (1-p)` rather than a flat 2%, and on a 5-6¢ contract the
bid-ask spread (often a full tick, ~18% of contract value) likely dominates over the
assumed flat 0.5% slippage. The true cost could be higher or lower than 5% — it needs
real order-book data, not a better guess, to resolve.

### `stocks_momentum`

Only one trade so far (bought SPY at $767.42, still open, unrealized +0.55%). Not
enough data to say anything about this strategy/market pairing yet. One model (Kimi)
checked whether stale weekend/after-hours candles could contaminate this track's
signal — verified not an issue: the code already gates signal computation to market
hours (`market_is_open()` in `engine/data/stocks_source.py`), so it simply doesn't
tick outside trading hours.

---

## 4. Engineering status — what's already been ruled out

This system has been through **six rounds of code audit** plus live stress-testing
(including deliberately reproducing a real concurrency bug and confirming the fix), plus
the five-model external strategy audit referenced in section 0. Confirmed fixed,
verified against real data and real production runs:

- A sizing bug that made BUY orders literally never fill (fixed, verified with forced
  trades)
- Config values (`starting_capital`) silently drifting if edited after a track started
  (fixed — now immutable per-track, persisted in the ledger, not re-read from config)
- Division-by-zero crash on a zero/near-zero asset price (a real Polymarket edge case)
- No upper bound on `fee_pct`/`slippage_pct`, which could have driven ledger cash
  negative (fixed, validated to `[0, 100)`)
- The loss-cap flatten was modeled with zero fee/slippage (more favorable than a real
  exit) — fixed to use the same fill simulation as every other trade
- Malformed/missing upstream API data (null candles, NaN closes, missing fields) could
  silently freeze a track on HOLD forever or crash it — hardened
- A real, reproduced git-level race condition where two overlapping CI runs corrupted
  the same binary ledger file — fixed and the fix was verified by deliberately
  recreating the exact race twice
- Strategy parameters and track config now have real validation (window ordering,
  threshold ordering, required keys, sane bounds) — a typo can no longer silently
  produce nonsense instead of an error
- A win-rate calculation bug that compared the wrong baseline (section 0)

**Conclusion: the underperformance in this dataset is not an execution/accounting bug.
It's a combination of (a) a genuine cost-model question on Polymarket, and (b) a
structural design gap (all-in sizing, no cost-aware entry filter) that has now been
addressed. Whether the strategies have real signal edge is still unproven — the sample
is too small either way, per unanimous audit consensus.**

---

## 5. Known design limitations (updated 2026-09-28)

- **Long-only.** No short-selling anywhere. In a downtrend, momentum just exits to
  cash — it can't profit from the decline the way a short would. Audit consensus: not
  the most urgent fix, and for Polymarket specifically, adding shorting now (before
  proving the signal has any edge) would be premature.
- **No take-profit.** Deliberately kept this way — audit consensus agreed: a fixed
  take-profit is likely counter-productive for momentum (trend-following often survives
  via a few large right-tail winners you don't want to cap), and for mean-reversion the
  more defensible change (not yet made) is an earlier exit target (e.g. RSI→50 instead
  of RSI→70), tested as a separate challenger, not a blanket TP.
- ~~All-in position sizing~~ — **fixed 2026-09-28**: 50% of cash per trade, uniform
  across tracks.
- ~~No minimum-expected-move filter~~ — **fixed 2026-09-28**: entries now require the
  recent realized range to clear 2x round-trip cost.
- **No market-regime/trend filter.** Momentum still fires on every crossover
  unconditionally once the cost filter is cleared — it still can't detect "this market
  is rangebound, stand aside" the way a trend filter (e.g. price vs. SMA(50), or ADX)
  would. Audit consensus: this is the next thing to add, but only after more data
  accumulates under the current change — don't stack multiple untested changes at once.
- **Fixed parameters across all tracks that use momentum** (fast=5, slow=20) —
  identical window regardless of the asset's volatility or the timeframe's noise
  characteristics. Audit consensus: do not retune this yet — very high overfitting risk
  on this sample size. Revisit only after 50-100+ closed trades per track.
- **RSI thresholds (30/70) and period (14) are textbook defaults**, not tuned to
  anything about ETH's actual behavior. Same caution applies — do not retune yet.
- **Single fixed poll interval (15 min)** for all tracks and both strategies — no
  attempt to match the timeframe to each market's actual noise/signal characteristics.
- **New, from the audit**: each strategy currently runs on exactly one market, so the
  experiment cannot distinguish "the strategy is bad" from "the market/regime was bad."
  Running momentum on 2+ assets (not just BTC) would decouple these. Not yet
  implemented.
- **New, from the audit**: no buy-and-hold benchmark in the scoreboard. Momentum
  strategies are supposed to underperform buy-and-hold in choppy markets — the fair
  comparison for `crypto_momentum` isn't "did it make money" but "did it lose less than
  holding BTC over the same window." Not yet implemented.

---

## 6. Answers to the original open questions (per the five-model audit)

1. **`crypto_momentum`'s 0% win rate — rangebound market or timing/lag?** Consensus:
   most likely both, and they don't need to be separated — a lagged signal only
   meaningfully hurts in a rangebound regime, so the same fix (cost/regime filter)
   addresses both. To formally distinguish them would require logging forward returns
   at multiple horizons post-crossover (MFE/MAE analysis) — not yet done.
2. **Is the Polymarket 2%+0.5% assumption realistic?** No consensus on direction — some
   models argue it's too high (Polymarket has historically had zero maker fees), others
   argue it's too low (spread on 5-6¢ contracts likely dominates the flat 0.5% slippage
   assumption). Unanimous agreement that it needs real order-book validation, not
   another guess.
3. **Does the ETH -2.85% loss justify a tighter stop?** No — unanimous. n=5 is nowhere
   near enough to calibrate a stop level; a stop sized to avoid that one trade would be
   close to definitional curve-fitting.
4. **How much should 45 trades update our beliefs?** Very little on strategy edge
   specifically (confidence intervals on 5-9 closed trades are enormous). The audits
   trust two things from this data: the Polymarket cost drag is real/deterministic, and
   all-in sizing was a real structural risk (both already addressed).
5. **Prioritized recommendations?** See section 0 for what was implemented. Full
   original recommendations from all five models are preserved in
   `quant_strategy_audit.docx` for reference.

---

## 7. Where to find the raw data

- `data_store/*.db` — SQLite ledgers per track (`state`, `trades`, `equity_curve`
  tables), committed to git every tick, full permanent history
- `python trade_log.py` — chronological trade history with reasoning, all tracks
- `python scoreboard.py` — summary stats table + equity curve chart (win rate now
  net-of-cost, correctly baselined — see section 0)
- `config/tracks.yaml` — exact current parameters per track, including the new
  `position_size_pct`
- `engine/strategies/momentum.py`, `engine/strategies/mean_reversion.py` — exact
  strategy logic
- `engine/risk.py` — the new cost-aware entry filter (`passes_cost_filter`)
- `quant_strategy_audit.docx` — the full, unabridged five-model external audit
