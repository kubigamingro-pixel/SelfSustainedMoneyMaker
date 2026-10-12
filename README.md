# Self-Sustained Money Maker — paper-trading comparison engine

**Status: validation phase.** This is a paper-trading (simulated money) comparison
engine. No exchange or broker accounts, no API keys, no real capital involved yet.
The goal is to run strategies in parallel against live public market data and
see which one actually holds up before ever risking real money.

## What it does

21 tracks total: 4 base strategy/market combinations, each run at 1x ($10
starting capital), 2x and 10x leverage ($50 starting capital), plus 3 of those
4 also run with a regime-filter guard on top (also 1x/2x/10x) -- see
`grid.py` for the full 7x3 comparison. All share one engine:

| Track family | Domain | Data source (free, no key) | Strategy |
|---|---|---|---|
| `crypto_momentum` | Crypto (BTC/USD, Kraken) | `ccxt` public OHLCV | SMA crossover trend-following |
| `crypto_mean_reversion` | Crypto (ETH/USD, Kraken) | `ccxt` public OHLCV | RSI(14) oversold/overbought |
| `stocks_momentum` | Stocks (SPY) | `yfinance` | SMA crossover trend-following |
| `prediction_markets_momentum` | Prediction markets | Polymarket Gamma API | SMA crossover on implied probability |
| `*_regime` (crypto/stocks/prediction markets momentum only) | same as above | same as above | SMA crossover, entry also gated on the slow SMA rising (`engine/strategies/momentum_regime_filtered.py`) |

Every tick: fetch the latest price series → check the per-track loss cap (flattens
and stops the track if equity falls below 50% of starting capital) → run the
strategy → simulate a fill (with fee + slippage) → log the trade and equity to a
per-track SQLite ledger in `data_store/`.

### Leveraged variants (added 2026-10-01)

Same 4 strategies/markets above, also run leveraged: 2x and 10x, $50 starting
capital each (8 more tracks, `engine/leveraged_runner.py` + `engine/leverage_risk.py`
— a fully separate code path from the spot engine above, so none of this touches
the proven spot-trading logic). Adds margin accounting, a funding cost (10%/year,
a simple round assumption — real funding is periodic and can flip sign), and a new
failure mode the spot tracks don't have: **per-position liquidation**, which can
force-close a position and lose its margin well before the account-level loss cap
would ever trigger on its own.

**Worth knowing:** with the shared `position_size_pct: 50` and `loss_cap_pct: 50`
config, a *single full liquidation* generally also breaches the account-level
loss cap in the same tick — margin committed (50% of capital) and the loss-cap
floor (50% of capital) are the same number, so losing one full margin leaves
the account right at its floor. Verified directly (forced a 10% adverse move
at 10x leverage in a test). This is a real property of the current config, not
a bug.

As of 2026-10-12, the account-level loss cap for **leveraged tracks only** is
non-terminal ("research mode"): it force-closes the open position (same as a
liquidation) and records a `LOSS_CAP_BREACHED` event, but the track keeps
trading afterward with whatever cash remains — see `engine/leverage_risk.py`.
It used to permanently stop the track, which meant a single bad gap ended that
track's entire sample; this change followed from a 2026-10-12 audit (see
`audits/`) after it had already forced two manual resets. 1x spot tracks are
unaffected and still stop permanently on breach (`engine/risk.py`). `grid.py`
shows breach counts (`[N breaches]`) where this has fired instead of `[STOPPED]`.

**Honesty note:** `prediction_markets_momentum_2x`/`_10x` are a hypothetical
comparison only — Polymarket does not actually offer margin/leveraged trading.
This is "what if this strategy were leveraged," not a simulation of a real
available product for that market.

## Running it

**In production:** [.github/workflows/tick.yml](.github/workflows/tick.yml) runs `python scheduler.py --once`
via GitHub Actions, then commits the updated SQLite ledgers back to this repo.
The workflow only listens for `workflow_dispatch` (no `schedule:` trigger) —
an external cron-job.org job calls the GitHub REST API every 15 minutes to
fire it. This is deliberate, not a placeholder: GitHub's own `schedule`
trigger was confirmed unreliable (went hours with zero self-fired runs, then
only intermittent afterward), so cron-job.org replaced it entirely rather
than running alongside it. Trigger a tick manually with `gh workflow run
tick.yml` or from the Actions tab — both work the same as the cron-job.org path.

**Locally** (for development/testing, not how it runs in production):

```bash
pip install -r requirements.txt

python scheduler.py --once   # one tick of all 21 tracks
python scheduler.py --loop   # or run continuously, sleeping between ticks
```

After a few ticks have accumulated:

```bash
python scoreboard.py
```

Prints a comparison table (return %, max drawdown, trade count, win rate) and
writes `logs/equity_curve.png` with all tracks' equity curves overlaid.

```bash
python trade_log.py
```

Prints every trade ever made, across all tracks, in chronological order,
with the strategy's reasoning for each one (e.g. `RSI(14)=28.4 < oversold
threshold 30`). This is permanent history -- pulled from the same SQLite
ledgers that get committed to the repo every tick, not from ephemeral logs.

```bash
python grid.py
```

Compact 7x3 view: the 7 strategy/market rows (4 base + 3 regime-filtered) x
leverage level (1x/2x/10x, columns) -- the fastest way to compare how leverage
and the regime filter each affect the exact same signal side by side.

```bash
python backtest.py --source crypto --symbols BTC/USD,ETH/USD --strategy momentum \
    --fee-pct 0.26 --slippage-pct 0.05 --days 180 --oos-days 30
```

Offline backtest harness (added 2026-10-12, see `audits/`): replays a strategy
over historical closed candles through the exact same `engine.runner.run_tick`
the live spot tracks use (so backtest and live share one code path), against
an in-memory ledger that never touches `data_store/`. Reports net-of-cost
return, win rate, max drawdown, buy-and-hold alpha, an in-sample/out-of-sample
split, and a bootstrap confidence interval. Spot engine only (crypto/stocks,
no leverage, no Polymarket) -- see the file's own docstring for data-source
history limits (Kraken's public OHLC endpoint caps out around 720 candles) and
other caveats. Run `python backtest.py --help` for all options.

```bash
python reset_track.py crypto_momentum --reason "manual review reset"
python reset_track.py --all --reason "fresh start for the whole comparison"
```

Resets a track to start a new epoch (added 2026-10-12, see `audits/`) without
losing its history: the closed epoch's summary is archived into the ledger's
own `epochs` table and every prior trade/decision/equity row stays queryable
by its original epoch number (`Ledger.trades(epoch=0)`, etc.) -- a reset no
longer means "only git history remembers this happened." `grid.py` shows
`[epoch N]` once a track is past its first epoch. Replaces the old pattern of
hand-editing a track's ledger state directly. Run `python reset_track.py --help`
for the open-position caveat (you must supply `--final-equity` yourself).

## Config

`config/tracks.yaml` holds per-track symbol, fee/slippage assumptions, poll
interval, and the loss-cap threshold. Nothing here should be edited to make a
track "look better" — the point is an honest comparison.

Note: `starting_capital` only seeds a track the *first* time it's created —
once a track's ledger exists, its starting capital is locked in and read from
the ledger itself, not re-read from this file. Editing it later has no effect
on an already-running track (by design, so the loss-cap floor and return%
math can't silently drift out from under a live track).

## What's deliberately not here yet

Real exchange/broker accounts, API keys, real order execution. Those only get
added once the scoreboard shows a track worth funding with the actual $10 — a
decision to make together after seeing real data, not something the app decides
on its own.

## Dev agents

`.claude/agents/` has ~155 specialized Claude Code subagent personas (from
[agency-agents](https://github.com/msitarzewski/agency-agents), installed
locally to this project only) covering engineering, finance, security, testing,
research, and product — useful for reviewing strategy logic, auditing secrets
handling before the real-money phase, etc. They are prompt personas for
development assistance, not part of the runtime engine.
