# Performance Review Context — SelfSustainedMoneyMaker

**Purpose of this document:** everything a strategy/quant auditor needs to analyze why
this paper-trading system's win rates and returns are low, without re-litigating
engineering correctness (that's already been extensively verified — see "Engineering
status" below). This is real performance data from a live, running system, not a
backtest.

**As of:** 2026-09-28, day 6 of live paper-trading. All figures below are exact, pulled
directly from the system's SQLite ledgers.

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
market/data source and the fee/slippage assumptions below. Every BUY invests **100% of
available cash** in one shot (no partial position sizing, no risk-per-trade concept).
Long-only — there is no short-selling anywhere in the system.

### Cost assumptions per track (this matters a lot — see section 3)

| Track | Fee (per side) | Slippage (per side) | Round-trip cost (~2x) |
|---|---|---|---|
| `crypto_momentum` / `crypto_mean_reversion` | 0.26% | 0.05% | ~0.62% |
| `stocks_momentum` | 0.00% | 0.02% | ~0.04% |
| `prediction_markets_momentum` | 2.00% | 0.50% | ~5.00% |

### Risk controls currently in place

- **Loss cap only**: if a track's equity falls below 50% of its starting capital, it
  flattens its position and **permanently stops trading** (hard stop, no recovery).
  None of the four tracks are close to this (worst is at ~75% of starting capital).
- **No take-profit.** Positions only close on the strategy's own exit signal (death
  cross / RSI>70) or the loss cap above. This was a deliberate choice (see section 5).
- **No stop-loss per trade** — only the account-level loss cap. A single trade can run
  arbitrarily far against the position with no per-trade circuit breaker.
- **No position sizing logic** — every trade is "bet the whole stack."

---

## 2. Current standing (as of 2026-09-28, day 6)

| Track | Balance | Return | Trades | Win rate | Max drawdown | State |
|---|---|---|---|---|---|---|
| `crypto_momentum` | $9.10 | -8.96% | 19 | **0%** (0/9 closed) | 9.05% | holding (open long) |
| `crypto_mean_reversion` | $9.41 | -5.91% | 11 | 60% (3/5 closed) | 6.13% | holding (open long) |
| `stocks_momentum` | $10.06 | +0.55% | 1 | n/a (still open) | 0.29% | holding (open long) |
| `prediction_markets_momentum` | $7.53 | **-24.66%** | 14 | 43% (3/7 closed) | 26.36% | flat |
| **Combined** | **$36.10 / $40.00** | **-9.74%** | 45 | — | — | — |

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
entire test window.** This is the textbook failure mode of a fast SMA crossover: in a
non-trending market, it just generates a string of small whipsaw losses. This is not
noise — 0 wins in 9 closed trades, with an average raw edge that's negative even before
costs, is a real signal that **this parameterization is not suited to this market
regime.**

### `crypto_mean_reversion` (RSI 14, buy<30/sell>70, ETH/USD)

| # | Buy price | Sell price | Raw price return |
|---|---|---|---|
| 1 | 2749.65 | 2671.34 | **-2.848%** |
| 2 | 2646.23 | 2660.94 | +0.556% |
| 3 | 2682.76 | 2710.83 | +1.046% |
| 4 | 2690.48 | 2688.73 | -0.065% |
| 5 | 2686.34 | 2693.50 | +0.267% |

**3 of 5 round trips won (60%), but the single loss (#1, -2.85%) is bigger than all
three wins combined (+1.869%).** This is the classic mean-reversion tail risk: the
strategy correctly identifies "oversold" and buys, but if the market keeps falling
before the bounce that finally pushes RSI back to 70, that one trade can wipe out
several smaller wins. Trades 2–5 (after the first) show the strategy working as
intended on smaller swings.

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

**Sum of raw price returns across all 7 round trips: +2.21% — net POSITIVE.** But the
realized account return is **-24.66%**. The gap between "the calls were actually
slightly net-correct on price" and "the account lost a quarter of its value" is almost
entirely the **5% round-trip cost assumption (2% fee + 0.5% slippage, each side)**
applied 7 times (~35% cumulative cost drag on the capital that passed through these
trades). **This is the single clearest, most actionable finding in this dataset: the
fee/slippage assumption for this track makes any strategy that trades this frequently
structurally unviable, regardless of signal quality.** Either the fee assumption is
unrealistically high for how this would actually execute on Polymarket, or the strategy
needs to trade far less often, or both.

### `stocks_momentum`

Only one trade so far (bought SPY at $767.42, still open, unrealized +0.55%). Not
enough data to say anything about this strategy/market pairing yet.

---

## 4. Engineering status — what's already been ruled out

This system has been through **six rounds of code audit** plus live stress-testing
(including deliberately reproducing a real concurrency bug and confirming the fix).
Confirmed fixed, verified against real data and real production runs:

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

**Conclusion for the auditor: the underperformance you're looking at is very unlikely
to be an execution/accounting bug at this point. Treat it as a strategy design and
parameter-calibration problem**, not a "check if the code is honest" problem — that's
already been done extensively.

---

## 5. Known design limitations (not bugs — deliberate scope decisions, open to revisiting)

- **Long-only.** No short-selling anywhere. In a downtrend, momentum just exits to
  cash — it can't profit from the decline the way a short would.
- **No take-profit.** Deliberately decided against adding one (to keep this a clean
  test of whether the strategies' own exit signals are good exits) — but this means a
  position can round-trip a large paper gain back to a loss before the strategy's own
  signal fires. This is an open question worth the auditor's opinion on.
- **All-in position sizing.** Every BUY commits 100% of cash. No volatility-adjusted
  sizing, no partial scaling in/out, no risk-per-trade budget.
- **No market-regime filter.** The momentum strategy has no way to detect "this market
  is rangebound, don't trade" vs. "this market is trending, trade" — it fires on every
  crossover unconditionally, which is exactly what's hurting `crypto_momentum` right
  now (see section 3).
- **No minimum-expected-move filter.** A crossover predicting a tiny move still trades
  even when the round-trip cost is larger than any plausible edge.
- **Fixed parameters across all tracks that use momentum** (fast=5, slow=20) —
  identical window regardless of the asset's volatility or the timeframe's noise
  characteristics. BTC, SPY, and Polymarket probabilities have very different natural
  volatility, and using the same window for all three is an obvious place to look for
  improvement.
- **RSI thresholds (30/70) and period (14) are textbook defaults**, not tuned to
  anything about ETH's actual behavior.
- **Single fixed poll interval (15 min)** for all tracks and both strategies — no
  attempt to match the timeframe to each market's actual noise/signal characteristics.

---

## 6. Open questions for the auditor

1. Is the `crypto_momentum` 0%-win-rate pattern (section 3) actually a rangebound
   market, or is there a subtler timing/lag issue in how the crossover is computed?
2. Is the `prediction_markets_momentum` fee/slippage assumption (2% + 0.5%,
   ~5% round-trip) realistic for Polymarket, or overly punitive? If realistic, what
   strategy characteristics would actually be viable under that cost structure
   (lower frequency? bigger expected moves per trade? avoid momentum entirely?).
3. Does `crypto_mean_reversion`'s single large loss (section 3, trade #1, -2.85%)
   suggest the RSI threshold or exit rule should be tightened (e.g. a hard stop-loss
   per trade, not just at the account level), or is one bad trade in five an
   acceptable/expected variance for this strategy type?
4. Given 45 trades total across 6 days, how much of this should actually update our
   beliefs about the strategies vs. still be treated as noise? Please be explicit about
   sample-size/overfitting risk in any recommendation — we do not want parameter
   changes that are just curve-fit to this one short window.
5. Any recommendation should be concrete and prioritized: what to try first, what
   evidence would confirm or kill it, and how long to run it before judging.

---

## 7. Where to find the raw data

- `data_store/*.db` — SQLite ledgers per track (`state`, `trades`, `equity_curve`
  tables), committed to git every tick, full permanent history
- `python trade_log.py` — chronological trade history with reasoning, all tracks
- `python scoreboard.py` — summary stats table + equity curve chart
- `config/tracks.yaml` — exact current parameters per track
- `engine/strategies/momentum.py`, `engine/strategies/mean_reversion.py` — exact
  strategy logic
