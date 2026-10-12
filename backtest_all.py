"""Simulate every one of the 21 live tracks (all 7 strategy/domain families x
1x/2x/10x) under the current rules, replayed from each family's own real
historical start -- not just the spot-only, single-symbol runs backtest.py
supports.

Built in response to a direct request (2026-10-12): "how would all the
traders have behaved with the new settings but starting from the beginning
of the paper trading experiment." This is a one-off wide replay, not a
general-purpose tool like backtest.py -- it hardcodes the 7 families and
reuses backtest.py's/engine's pieces rather than taking CLI flags for them.

Revised 2026-10-12 after an independent review (audits/2026-10-12-simulation-
review.md) of the first version found the Polymarket replay was not rotation-
aware (the single biggest issue -- see replay_polymarket() below), forced-
close trades were timestamped with wall-clock time instead of the replay's
historical tick (fixed in the engine itself: ts= now threads through
check_loss_cap, check_liquidation, check_account_loss_cap, and both
settle_position functions), and drawdown/win-rate were computed incorrectly
across epoch resets. All are fixed here; see each function's docstring for
what changed and why.

What "from the beginning" means per family (each family's actual first live
tick, per its ledger's epoch-0 equity_curve). Momentum and momentum+regime
variants of the same domain are deliberately run over the IDENTICAL window
(both from 2026-09-28) so the "does the regime guard help" comparison is
head-to-head -- the first version used each variant's own real start date
(regime families only went live 2026-10-02), which meant the comparison
wasn't on the same data (review finding #5). A replay doesn't care that the
regime strategy didn't exist live on 2026-09-28.
    crypto_momentum, crypto_mean_reversion, stocks_momentum,
    crypto_momentum_regime, stocks_momentum_regime        -- all from 2026-09-28
    prediction_markets_momentum, prediction_markets_momentum_regime -- 2026-10-05
      (this is each track's own real epoch-0 start; momentum and
      momentum+regime happen to share the same start here already)
(crypto_mean_reversion has no _regime variant; the regime strategy only ever
ran on momentum.)

Data sources, and why:
- crypto (BTC/USD, ETH/USD): Kraken (what the live tracks actually use) only
  serves ~7 days of history via its public API -- confirmed directly. The
  experiment is 15 days old, so Kraken alone can't answer "from the
  beginning." Bitstamp and Coinbase were checked directly and both paginate
  back far enough (Bitstamp needed fewer requests to cover the whole window,
  so it's used here) -- prices will differ slightly from what Kraken itself
  showed at any given moment, a real but small source of noise, not an error.
- stocks (SPY): yfinance, same as live. Its own ~60-day cap for 15m bars
  covers the whole 15-day experiment window with room to spare. The
  in-progress (not-yet-closed) bar is dropped the same way
  engine/data/stocks_source.py does for live ticks (review finding #7 --
  the first version of this file didn't).
- Polymarket: there is no public API for historical implied-probability
  series (Gamma exposes only the current price). Each Polymarket track's own
  ledger already holds its real, tick-by-tick observed price history in
  epoch 0's equity_curve table -- that IS the real historical record, reused
  here as the input series. The ledger is opened read-only (a temp copy) so
  this script truly never writes to a live ledger (review finding #6 -- the
  first version opened it via Ledger(), which runs schema DDL + commits
  against the real file even though it only ever read from it).

  Rotation handling (review finding #1, the main fix in this revision): the
  recorded history spans several different Polymarket markets end-to-end (the
  live track auto-rotates whenever its tracked market closes). The live
  engine resets the strategy's price window on every rotation and (since
  today's remediation) settles any held position at the old market's last
  price first. The first version of this replay fed the whole multi-market
  log through the strategy as a single continuous series, so a rotation's
  price jump could read as a crossover purely from switching markets, not
  from any real signal. This version reads the track's actual historical
  rotation events (and their real timestamps) from `git log` on the
  committed data_store/<track>_condition.json file -- every commit to that
  file is a genuine rotation (the live wrapper only rewrites it when the
  resolved market id actually changes), so these are exact, not inferred --
  and replays each market segment independently, resetting the price window
  and settling any open position at the segment boundary, the same as live.

Leverage: 2x/10x apply the exact same signal from the exact same price series
as the 1x run of that family (one historical timeline per family, matching
how the live system is designed -- leverage amplifies the same signal, it
isn't a separately-signaled track), using engine.leveraged_runner against an
in-memory ledger exactly like the 1x run uses engine.runner.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import time as time_module
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt
import yfinance as yf

from backtest import bootstrap_ci, net_round_trip_returns_pct
from engine.config import load_tracks_config
from engine.ledger import DATA_STORE, Ledger
from engine.leveraged_runner import run_tick as leveraged_run_tick
from engine.leveraged_runner import settle_position as leveraged_settle_position
from engine.runner import run_tick as spot_run_tick
from engine.runner import settle_position as spot_settle_position
from scoreboard import win_rate

MIN_HISTORY = 21  # slow_window(20) + 1, shared by all strategies here


def fetch_crypto_history_with_low(symbol: str, exchange_id: str, timeframe: str, since_iso: str):
    """Paginates ccxt fetch_ohlcv forward from since_iso, keeping each candle's
    low (needed for leveraged intrabar liquidation, finding #12). Returns
    (datetime, close, low) tuples, oldest first, closed candles only."""
    exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    duration_ms = exchange.parse_timeframe(timeframe) * 1000
    now_ms = exchange.milliseconds()
    since_ms = exchange.parse8601(since_iso)

    all_candles = []
    cursor = since_ms
    seen = set()
    for _ in range(200):
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=1000)
        if not batch:
            break
        new = [c for c in batch if c[0] not in seen]
        if not new:
            break
        seen.update(c[0] for c in new)
        all_candles.extend(new)
        cursor = new[-1][0] + duration_ms
        if cursor >= now_ms:
            break
        time_module.sleep(exchange.rateLimit / 1000.0)

    all_candles.sort(key=lambda c: c[0])
    closed = [c for c in all_candles if c[4] is not None and c[0] + duration_ms <= now_ms]
    return [(datetime.fromtimestamp(c[0] / 1000.0, tz=timezone.utc), c[4], c[3]) for c in closed]


def fetch_stock_history_with_low(symbol: str, period: str, interval: str, since: datetime):
    """Like engine/data/stocks_source.py's live fetch: drops the in-progress
    bar if the market is still inside it (review finding #7 -- the first
    version of this file didn't, so a replay run during market hours could
    end on a partial bar)."""
    hist = yf.Ticker(symbol).history(period=period, interval=interval)
    hist = hist[hist["Close"].notna()]

    bar_len = timedelta(minutes=15) if interval.endswith("m") and interval[:-1].isdigit() else timedelta(hours=1)
    if interval.endswith("m"):
        bar_len = timedelta(minutes=int(interval[:-1]))
    now = datetime.now(timezone.utc)
    if len(hist) and (hist.index[-1].to_pydatetime().astimezone(timezone.utc) + bar_len) > now:
        hist = hist.iloc[:-1]

    hist = hist[hist.index >= since.astimezone(hist.index.tz)] if len(hist) else hist
    out = []
    for ts, row in hist.iterrows():
        out.append((ts.to_pydatetime().astimezone(timezone.utc), float(row["Close"]), float(row["Low"])))
    return out


def get_rotation_events(track_name: str) -> list:
    """Every genuine market rotation this track has ever made, oldest first,
    as (timestamp, condition_id) -- read from the real git history of its
    committed data_store/<track>_condition.json. Every commit to that file IS
    a real rotation: the live wrapper only rewrites it when the resolved
    market id actually changed (see tracks/prediction_markets_*.py). This is
    exact, not inferred from price jumps -- a 2026-10-12 review's suggested
    fix, verified against this repo's actual commit history."""
    path = f"data_store/{track_name}_condition.json"
    out = subprocess.run(
        ["git", "log", "--follow", "--format=COMMIT %aI", "-p", "--", path],
        capture_output=True, text=True, check=True, cwd=Path(__file__).resolve().parent,
    ).stdout
    events = []
    current_ts = None
    for line in out.splitlines():
        if line.startswith("COMMIT "):
            current_ts = line[len("COMMIT "):]
        elif line.startswith("+{") and '"condition_id"' in line:
            try:
                cid = json.loads(line[1:])["condition_id"]
                events.append((datetime.fromisoformat(current_ts), cid))
            except (json.JSONDecodeError, KeyError, ValueError):
                pass
    events.reverse()
    return events


def load_polymarket_history(base_track_name: str):
    """Reuses the track's own real recorded history (see module docstring).
    Opens a temporary COPY of the live ledger, read-only in effect -- the
    original file is never written to (review finding #6)."""
    live_path = DATA_STORE / f"{base_track_name}.db"
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / f"{base_track_name}.db"
        shutil.copy2(live_path, tmp_path)
        ledger = Ledger(base_track_name, 10.0, db_path=tmp_path)
        curve = ledger.equity_curve(epoch=0)
        ledger.close()
    history = [(datetime.fromisoformat(ts), price, price) for ts, price, _equity in curve if price is not None]
    return history


def segment_by_rotation(history: list, rotation_events: list):
    """Splits history into one list per market the track was actually on,
    using real rotation timestamps (see get_rotation_events). Each segment is
    everything from one rotation up to (not including) the next.

    2026-10-12 fix-check (second review): the condition-file commit lands
    about 18s *after* the tick that already fetched the new market's first
    price -- the live wrapper fetches, decides, then the CI job commits the
    updated condition file afterward. So the new market's first price is
    actually the last history entry at or before the commit timestamp, not
    the first entry at or after it (confirmed: `2026-10-05 16:46:08 @ 0.53`,
    `10-11 14:15:55 @ 0.245`, `10-12 00:15:56 @ 0.43` -- each a plausible
    in-market price, versus ~0.0005 one tick earlier, the dying old market).
    Each boundary is adjusted to that entry's own timestamp before
    segmenting, so it's correctly included in the NEW segment. Confirmed by
    the second review to produce identical results on this dataset (the cost
    filter already blocked those one-tick jumps from becoming fake
    crossovers either way) -- fixed anyway since a position held across a
    rotation would otherwise settle at the new market's price instead of the
    old one's."""
    def adjusted(commit_ts):
        candidates = [ts for ts, _p, _l in history if ts <= commit_ts]
        return candidates[-1] if candidates else commit_ts

    boundaries = sorted(adjusted(ts) for ts, _cid in rotation_events if history[0][0] < ts <= history[-1][0])
    boundaries = sorted(set(boundaries))
    segments = []
    current = []
    bi = 0
    for entry in history:
        ts = entry[0]
        while bi < len(boundaries) and ts >= boundaries[bi]:
            if current:
                segments.append(current)
            current = []
            bi += 1
        current.append(entry)
    if current:
        segments.append(current)
    return segments


def replay_spot_or_leveraged(sim_track_name: str, ledger: Ledger, prices_window_base: list, history_segment: list,
                             config: dict, is_leveraged: bool, settle_at_segment_end: bool):
    """Runs one market segment's ticks through the live engine (spot or
    leveraged) against the given (already-open, in-memory) ledger, continuing
    the price window from prices_window_base (so MIN_HISTORY only has to be
    satisfied once per segment, matching the live engine's price-history
    reset on rotation). If settle_at_segment_end and the ledger is still
    holding a position after the last tick in this segment, force-sells at
    that last price, timestamped at that last tick -- the replay's proxy for
    "the live engine settles a held position when its market closes" (finding
    #1's actual fix). The true resolution price isn't known from this data;
    the last observed price before rotation is the best available proxy,
    labeled as such in the trade reason."""
    run_tick = leveraged_run_tick if is_leveraged else spot_run_tick
    prices = list(prices_window_base)
    for ts, price, low in history_segment:
        prices.append(price)
        if len(prices) < MIN_HISTORY:
            continue
        window = list(prices)
        if is_leveraged:
            def fetch(_w=window, _p=price, _lo=low):
                return _w, _p, _lo
        else:
            def fetch(_w=window, _p=price):
                return _w, _p
        try:
            run_tick(sim_track_name, config, fetch, ledger=ledger, ts=ts)
        except Exception as e:
            print(f"  [{sim_track_name}] tick at {ts} raised {e!r} -- stopping this replay early", file=sys.stderr)
            return prices, str(e)

    if settle_at_segment_end and history_segment:
        state = ledger.get_state()
        if state.position_qty > 0:
            last_ts, last_price, _low = history_segment[-1]
            fee_pct = config["fee_pct"]
            slippage_pct = config["slippage_pct"]
            reason = "MARKET_ROTATION_SETTLEMENT (replay proxy: last observed price before rotation, not the true resolution price)"
            if is_leveraged:
                leveraged_settle_position(ledger, last_price, reason, fee_pct, slippage_pct, ts=last_ts)
            else:
                spot_settle_position(ledger, last_price, reason, fee_pct, slippage_pct, ts=last_ts)
    return prices, None


def replay(track_name: str, history: list, config: dict, leverage_variant: str, rotation_events: list = None):
    """leverage_variant: '1x', '2x', or '10x'. Builds the right per-variant
    config (so fee/slippage/strategy_params/price_band all match tracks.yaml,
    only starting_capital/leverage/funding differ). rotation_events, when
    given (Polymarket only), splits history into per-market segments and
    resets the price window / settles any open position at each boundary
    (see segment_by_rotation and replay_spot_or_leveraged). Returns
    (sim_config, ledger) -- caller is responsible for reading results and
    closing the ledger."""
    is_leveraged = leverage_variant != "1x"

    sim_config = dict(config)
    if is_leveraged:
        sim_config["starting_capital"] = 50.0
        sim_config["leverage"] = 2 if leverage_variant == "2x" else 10
        sim_config.setdefault("funding_rate_annual_pct", 10)
    else:
        sim_config["starting_capital"] = 10.0

    sim_track_name = f"sim_{track_name}_{leverage_variant}"
    ledger = Ledger(sim_track_name, sim_config["starting_capital"], db_path=":memory:")

    error = None
    if rotation_events is not None:
        segments = segment_by_rotation(history, rotation_events)
        for i, segment in enumerate(segments):
            is_last = i == len(segments) - 1
            _prices, error = replay_spot_or_leveraged(
                sim_track_name, ledger, [], segment, sim_config, is_leveraged,
                settle_at_segment_end=not is_last,
            )
            if error:
                break
    else:
        _prices, error = replay_spot_or_leveraged(
            sim_track_name, ledger, [], history, sim_config, is_leveraged, settle_at_segment_end=False,
        )

    return sim_config, ledger, error


def chained_equities(ledger: Ledger, current_epoch: int) -> list:
    """Equity values across every epoch this replay went through, each epoch's
    values rescaled by the cumulative multiplier at the point that epoch
    started -- i.e. "if this had been one continuous bankroll, rescaled at
    each reset instead of reset to a fixed starting_capital." Fixes review
    finding #3: computing drawdown on the raw equity_curve() jumps back up to
    starting_capital at every reset, so a track that lost 94% could show a
    71% max drawdown -- numerically impossible for one bankroll."""
    epochs = ledger.epochs()
    multiplier = 1.0
    combined = []
    for epoch, _ended_ts, _reason, ep_starting_capital, _final_cash, ep_final_equity, _final_qty, _n_trades in epochs:
        curve = ledger.equity_curve(epoch=epoch)
        combined.extend(multiplier * equity for _ts, _price, equity in curve)
        if ep_starting_capital > 0:
            multiplier *= ep_final_equity / ep_starting_capital
    curve = ledger.equity_curve(epoch=current_epoch)
    combined.extend(multiplier * equity for _ts, _price, equity in curve)
    return combined


def chained_max_drawdown_pct(equities: list) -> float:
    if not equities:
        return 0.0
    peak = equities[0]
    worst = 0.0
    for e in equities:
        peak = max(peak, e)
        worst = max(worst, (peak - e) / peak if peak > 0 else 0.0)
    return worst * 100


def epoch_aware_win_rate_and_ci(ledger: Ledger, current_epoch: int, starting_capital: float):
    """Review finding #4: win_rate()/net_round_trip_returns_pct() both chain
    `pre_trade_equity` forward from the previous SELL's equity_after, which is
    correct *within* one epoch but wrong across a reset -- a fresh epoch's
    first SELL would get compared against the previous epoch's low ending
    equity and could count as a win when every real trade lost. Fixed by
    computing each epoch's trades separately (each epoch always starts at the
    same starting_capital, by construction of this replay), then combining
    the per-epoch round-trip counts/wins and return lists afterward."""
    epochs = ledger.epochs()
    epoch_numbers = [e[0] for e in epochs] + [current_epoch]
    total_wins = 0
    total_round_trips = 0
    all_rt_returns = []
    for epoch in epoch_numbers:
        trades = ledger.trades(epoch=epoch)
        rt_returns = net_round_trip_returns_pct(trades, starting_capital)
        all_rt_returns.extend(rt_returns)
        total_round_trips += len(rt_returns)
        total_wins += sum(1 for r in rt_returns if r > 0)
    wr = 100 * total_wins / total_round_trips if total_round_trips else float("nan")
    ci = bootstrap_ci(all_rt_returns) if all_rt_returns else None
    return wr, ci


def capital_deployed_loss_pct(ledger: Ledger, current_epoch: int, starting_capital: float):
    """Review note on the return-chaining fix: a chained % return (e.g.
    -79.86%) reads like "lost 80% of your money," but for a track that reset
    N times, N+1 separate starting_capital stakes were actually deployed.
    Reports what share of total capital *put in* was actually lost -- e.g.
    "2x Polymarket put in $150 across 3 stakes and lost 37% of it" -- a
    different, also-true number worth showing next to the chained return.

    2026-10-12 fix-check (second review): the first version counted every
    epoch as a deployed stake, including a fresh post-reset epoch that never
    placed a trade -- that epoch's untouched starting_capital inflated the
    denominator and roughly halved the reported loss % for a track that
    reset once and then stopped trading. Only an epoch with at least one
    trade actually had capital "deployed" in the sense this metric means;
    an epoch with zero trades contributed no capital at risk and is excluded."""
    epochs = ledger.epochs()
    deployed = 0.0
    final_total = 0.0
    for epoch, _ended_ts, _reason, ep_starting_capital, _final_cash, ep_final_equity, _final_qty, n_trades in epochs:
        if n_trades > 0:
            deployed += ep_starting_capital
            final_total += ep_final_equity
    if ledger.trades(epoch=current_epoch):
        deployed += starting_capital
        current_curve = ledger.equity_curve(epoch=current_epoch)
        final_total += current_curve[-1][2] if current_curve else starting_capital
    if deployed <= 0:
        return 0.0
    lost = deployed - final_total
    return 100 * lost / deployed


def summarize(sim_config, ledger, error, first_price, last_price):
    starting_capital = sim_config["starting_capital"]
    epochs = ledger.epochs()
    current_epoch = epochs[-1][0] + 1 if epochs else 0
    curve = ledger.equity_curve(epoch=current_epoch)
    trades_current = ledger.trades(epoch=current_epoch)
    all_trades = sum((ledger.trades(epoch=e[0]) for e in epochs), []) + trades_current

    if not curve and not epochs:
        return {"return_pct": 0.0, "n_trades": len(all_trades), "win_rate_pct": float("nan"), "max_dd_pct": 0.0,
                "buy_hold_pct": float("nan"), "alpha_pct": float("nan"), "n_epoch_resets": 0, "ci": None,
                "capital_lost_pct": 0.0, "error": error}

    multiplier = 1.0
    for _epoch, _ended_ts, _reason, ep_starting_capital, _final_cash, ep_final_equity, _final_qty, _n_trades in epochs:
        if ep_starting_capital > 0:
            multiplier *= ep_final_equity / ep_starting_capital
    final_equity = curve[-1][2] if curve else starting_capital
    if starting_capital > 0:
        multiplier *= final_equity / starting_capital
    return_pct = (multiplier - 1.0) * 100

    dd = chained_max_drawdown_pct(chained_equities(ledger, current_epoch))
    bh = 100 * (last_price - first_price) / first_price if first_price else float("nan")
    alpha = return_pct - bh if bh == bh else float("nan")
    wr, ci = epoch_aware_win_rate_and_ci(ledger, current_epoch, starting_capital)
    capital_lost_pct = capital_deployed_loss_pct(ledger, current_epoch, starting_capital)

    return {
        "return_pct": return_pct, "n_trades": len(all_trades), "win_rate_pct": wr, "max_dd_pct": dd,
        "buy_hold_pct": bh, "alpha_pct": alpha, "n_epoch_resets": len(epochs), "ci": ci,
        "capital_lost_pct": capital_lost_pct, "error": error,
    }


# 2026-10-12, second revision: "use all the data prices we have." Checked
# directly first: Bitstamp's 15m crypto history goes back years (tested to
# 2020+), but yfinance's 15m SPY bars are hard-capped at ~60 days -- confirmed
# by requesting period="max" and getting exactly a ~60-day window back.
# Pulling years of crypto history while stocks stays pinned to 60 days would
# compare each family over a different-length window, not apples-to-apples.
# Asked directly: 60 days for crypto and stocks both (the real ceiling, set
# by yfinance) -- this is now a materially longer window than the first two
# revisions used (2026-09-28, ~14 days). Polymarket is unchanged: its own
# recorded history (~7 days) already IS all the data that exists for it, per
# the module docstring's rotation-handling section above.
_SIXTY_DAYS_AGO = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%dT00:00:00Z")

FAMILIES = [
    # (track_name, kind, fetch_args)
    ("crypto_momentum", "crypto", ("BTC/USD", "bitstamp", "15m", _SIXTY_DAYS_AGO)),
    ("crypto_mean_reversion", "crypto", ("ETH/USD", "bitstamp", "15m", _SIXTY_DAYS_AGO)),
    ("stocks_momentum", "stocks", ("SPY", "60d", "15m", _SIXTY_DAYS_AGO)),
    ("crypto_momentum_regime", "crypto", ("BTC/USD", "bitstamp", "15m", _SIXTY_DAYS_AGO)),
    ("stocks_momentum_regime", "stocks", ("SPY", "60d", "15m", _SIXTY_DAYS_AGO)),
    ("prediction_markets_momentum", "polymarket", ("prediction_markets_momentum",)),
    ("prediction_markets_momentum_regime", "polymarket", ("prediction_markets_momentum_regime",)),
]


def main():
    config = load_tracks_config()
    results = {}
    datasets_dir = Path(__file__).resolve().parent / "audits" / "2026-10-12-simulation-datasets"
    datasets_dir.mkdir(exist_ok=True)

    for track_name, kind, args in FAMILIES:
        print(f"Fetching history for {track_name} ({kind})...")
        rotation_events = None
        if kind == "crypto":
            symbol, exchange_id, timeframe, since_iso = args
            history = fetch_crypto_history_with_low(symbol, exchange_id, timeframe, since_iso)
        elif kind == "stocks":
            symbol, period, interval, since_iso = args
            since = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
            history = fetch_stock_history_with_low(symbol, period, interval, since)
        else:
            (base_track,) = args
            history = load_polymarket_history(base_track)
            rotation_events = get_rotation_events(base_track)
        print(f"  got {len(history)} points, {history[0][0]} -> {history[-1][0]}" if history else "  got 0 points")

        # review finding: save the fetched dataset alongside the report for reproducibility.
        with open(datasets_dir / f"{track_name}.csv", "w") as f:
            f.write("timestamp,price,low\n")
            for ts, price, low in history:
                f.write(f"{ts.isoformat()},{price},{low}\n")

        if len(history) < MIN_HISTORY + 1:
            results[track_name] = {"error": f"only {len(history)} points, need >= {MIN_HISTORY + 1}"}
            continue

        first_price = history[0][1]
        last_price = history[-1][1]
        family_result = {}
        for variant in ("1x", "2x", "10x"):
            sim_config, ledger, error = replay(track_name, history, config[track_name], variant, rotation_events)
            family_result[variant] = summarize(sim_config, ledger, error, first_price, last_price)
            ledger.close()
        results[track_name] = family_result

    print()
    header = f"{'Family':<36}{'Col':<5}{'Return%':>10}{'CapLost%':>10}{'Trades':>8}{'Win%':>7}{'MaxDD%':>9}{'BuyHold%':>10}{'Alpha%':>9}{'Resets':>8}"
    print(header)
    print("-" * len(header))
    for track_name, _kind, _args in FAMILIES:
        r = results[track_name]
        if "error" in r:
            print(f"{track_name:<36}{'--':<5}{'--':>10}  {r['error']}")
            continue
        for variant in ("1x", "2x", "10x"):
            v = r[variant]
            wr_str = "--" if v["win_rate_pct"] != v["win_rate_pct"] else f"{v['win_rate_pct']:.0f}"
            bh_str = "--" if v["buy_hold_pct"] != v["buy_hold_pct"] else f"{v['buy_hold_pct']:+.2f}%"
            alpha_str = "--" if v["alpha_pct"] != v["alpha_pct"] else f"{v['alpha_pct']:+.2f}%"
            name = track_name if variant == "1x" else ""
            err_flag = "  [REPLAY STOPPED EARLY: " + v["error"] + "]" if v.get("error") else ""
            print(
                f"{name:<36}{variant:<5}{v['return_pct']:>+9.2f}%{v['capital_lost_pct']:>9.2f}%{v['n_trades']:>8}{wr_str:>7}"
                f"{v['max_dd_pct']:>8.2f}%{bh_str:>10}{alpha_str:>9}{v['n_epoch_resets']:>8}{err_flag}"
            )

    print()
    print("CapLost% = share of total capital actually put in (starting_capital x (resets+1) separate stakes)")
    print("that was lost overall -- a different, also-true number next to the chained Return% (which reads as")
    print("'% of one continuous bankroll', not '% of total capital deployed').")
    print()
    print("Resets = how many times that simulated run hit the (non-terminal, leveraged-only) account loss cap")
    print("and started a fresh epoch mid-replay.")
    print()
    print(f"Fetched datasets saved to {datasets_dir}/ for reproducibility.")
    print()
    print("Caveats (see this file's module docstring for the full explanation):")
    print("- crypto history is from Bitstamp, not Kraken (the live tracks' actual exchange) -- Kraken only")
    print("  serves ~7 days publicly. Prices will differ slightly from what was actually live-traded.")
    print("- Polymarket rotation settlement uses the last observed price before rotation as a proxy for the")
    print("  true resolution price, which this data does not contain.")
    print("- win_rate/CI and max drawdown are now epoch-aware (fixed 2026-10-12, see this file's docstrings),")
    print("  but round trips are still computed per-epoch independently, not across a position that happened")
    print("  to span a reset (positions are always closed before a reset, by construction, so this is exact).")


if __name__ == "__main__":
    main()
