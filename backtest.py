"""Offline backtest harness (2026-10-12 audit finding #5).

Replays a strategy over historical *closed* candles through the exact same
functions the live engine uses -- engine.runner.run_tick, which calls
passes_cost_filter, simulate_fill, and check_loss_cap internally -- so live and
backtest share one code path. This only covers the spot engine (momentum,
mean_reversion, momentum_regime on crypto/stocks); leveraged and Polymarket
replay are not built here (see "Not covered" below).

Why this exists: forward-only testing produces about one round trip per day
per track (PERFORMANCE_REVIEW.md wants 50-100+ closed trades before judging a
track), so settling "does the regime guard actually help" would take months
and restart every time a rule changes. A backtest answers it in seconds.

Usage:
    python backtest.py --source crypto --symbols BTC/USD,ETH/USD,SOL/USD \\
        --strategy momentum --fee-pct 0.26 --slippage-pct 0.05 \\
        --position-size-pct 50 --loss-cap-pct 50 --starting-capital 10 \\
        --days 180 --oos-days 30

    python backtest.py --source stocks --symbols SPY,QQQ --strategy momentum_regime \\
        --fee-pct 0.0 --slippage-pct 0.02 --period 60d --interval 15m --oos-days 14

Data limits (verify before trusting a long --days request):
- crypto (ccxt): paginates via fetch_ohlcv(since=...). Kraken's public OHLC
  endpoint only returns the most recent ~720 candles regardless of `since` in
  practice for some timeframes -- this script requests pagination anyway and
  reports exactly how many candles it actually got back, so a thin result is
  visible rather than silently truncated.
- stocks (yfinance): 15m bars are limited to the last ~60 days by Yahoo itself;
  1h bars go back ~2 years. Requesting more --days than the API allows just
  yields fewer bars than you asked for -- again reported, not silently dropped.

Discipline (per the audit): fix parameters before running, don't tune them
against what you see here. Split in-sample/out-of-sample by time (--oos-days)
and look at the in-sample result once, decide, then check out-of-sample --
treating OOS as a second chance to tune defeats the point of splitting it out.
"""

import argparse
import sys
import time as time_module
from datetime import datetime, timedelta, timezone

import ccxt

from engine.data.stocks_source import _fetch_raw as stocks_fetch_raw
from engine.ledger import Ledger
from engine.runner import STRATEGIES, run_tick
from scoreboard import win_rate

MIN_HISTORY_FOR_STRATEGY = 21  # slow_window(20) + 1, the shared default across strategies


def fetch_crypto_history(symbol: str, exchange_id: str, timeframe: str, days: int):
    """Paginates ccxt fetch_ohlcv backward from now using `since`, returning a
    list of (datetime, close) for every *closed* candle actually returned by
    the exchange. Stops early (and says so) if the exchange won't go back
    `days` -- see the module docstring's data-limits note."""
    exchange_cls = getattr(ccxt, exchange_id)
    exchange = exchange_cls({"enableRateLimit": True})
    duration_ms = exchange.parse_timeframe(timeframe) * 1000
    now_ms = exchange.milliseconds()
    since_ms = now_ms - days * 86400 * 1000

    all_candles = []
    cursor = since_ms
    seen_timestamps = set()
    max_requests = 200  # hard safety cap regardless of how far back `days` asks
    for _ in range(max_requests):
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=1000)
        if not batch:
            break
        new = [c for c in batch if c[0] not in seen_timestamps]
        if not new:
            break
        for c in new:
            seen_timestamps.add(c[0])
        all_candles.extend(new)
        cursor = new[-1][0] + duration_ms
        if cursor >= now_ms:
            break
        time_module.sleep(exchange.rateLimit / 1000.0)

    all_candles.sort(key=lambda c: c[0])
    closed = [c for c in all_candles if c[4] is not None and c[0] + duration_ms <= now_ms]
    history = [(datetime.fromtimestamp(c[0] / 1000.0, tz=timezone.utc), c[4]) for c in closed]

    requested_start = datetime.fromtimestamp(since_ms / 1000.0, tz=timezone.utc)
    if history and history[0][0] > requested_start + timedelta(days=1):
        actual_days = (history[-1][0] - history[0][0]).days
        print(
            f"  [data limit] requested {days}d but {exchange_id} only returned back to "
            f"{history[0][0].date()} ({actual_days}d, {len(history)} candles) -- "
            f"treat results as covering {actual_days}d, not {days}d",
            file=sys.stderr,
        )
    return history


def fetch_stock_history(symbol: str, period: str, interval: str):
    """yfinance's own period/interval limits apply directly (15m -> ~60d, 1h ->
    ~2y, see module docstring); there's no pagination workaround for stocks."""
    closes, _last, _low = stocks_fetch_raw(symbol, period, interval)
    # stocks_source caches by (symbol, period, interval) already fetched once
    # here; re-fetch the index separately since _fetch_raw only returns closes.
    import yfinance as yf
    hist = yf.Ticker(symbol).history(period=period, interval=interval)
    hist = hist[hist["Close"].notna()]
    timestamps = list(hist.index[-len(closes):])
    return list(zip(timestamps, closes))


def net_round_trip_returns_pct(trades: list, starting_capital: float) -> list:
    """Net-of-cost % return per round trip: equity right before the BUY vs.
    equity right after the matching SELL -- the same baseline scoreboard.py's
    win_rate() uses, not the gross price move (which ignores fees/slippage)."""
    returns = []
    pre_trade_equity = starting_capital
    pending_pre = None
    for row in trades:
        side, equity_after = row[1], row[6]
        if side == "BUY":
            pending_pre = pre_trade_equity
        elif side == "SELL":
            if pending_pre is not None and pending_pre > 0:
                returns.append(100 * (equity_after - pending_pre) / pending_pre)
            pre_trade_equity = equity_after
            pending_pre = None
    return returns


def max_drawdown_pct(equities: list) -> float:
    peak = equities[0]
    worst = 0.0
    for e in equities:
        peak = max(peak, e)
        worst = max(worst, (peak - e) / peak if peak > 0 else 0.0)
    return worst * 100


def bootstrap_ci(round_trip_returns: list, n_resamples: int = 2000, ci: float = 0.90):
    """Resample round-trip returns (%, per trade) with replacement n_resamples
    times, summing each resample to a total return, and report the [lo, hi]
    percentile interval. A quick, dependency-free stand-in for a proper
    bootstrap -- good enough to show whether a result could plausibly be noise
    (a CI straddling 0) vs. a real edge (CI entirely above 0), not a rigorous
    stats-package-grade interval."""
    import random
    if len(round_trip_returns) < 2:
        return None
    totals = []
    n = len(round_trip_returns)
    for _ in range(n_resamples):
        totals.append(sum(random.choice(round_trip_returns) for _ in range(n)))
    totals.sort()
    lo_idx = int(n_resamples * (1 - ci) / 2)
    hi_idx = int(n_resamples * (1 - (1 - ci) / 2))
    return totals[lo_idx], totals[hi_idx]


def run_one_backtest(symbol: str, history: list, args) -> dict:
    """history: list of (datetime, price), oldest first, already closed-only.
    Returns a metrics dict, or {"error": ...} if there wasn't enough history."""
    if len(history) < MIN_HISTORY_FOR_STRATEGY + 1:
        return {"error": f"only {len(history)} candles, need >= {MIN_HISTORY_FOR_STRATEGY + 1}"}

    split_idx = len(history)
    oos_start_idx = None
    if args.oos_days:
        cutoff = history[-1][0] - timedelta(days=args.oos_days)
        for i, (ts, _price) in enumerate(history):
            if ts >= cutoff:
                oos_start_idx = i
                break

    track_name = f"backtest_{symbol.replace('/', '_')}_{args.strategy}"
    ledger = Ledger(track_name, args.starting_capital, db_path=":memory:")

    if args.strategy == "mean_reversion":
        strategy_params = {"rsi_period": args.rsi_period, "oversold": args.oversold, "overbought": args.overbought}
    else:
        strategy_params = {"fast_window": args.fast_window, "slow_window": args.slow_window}

    config = {
        "fee_pct": args.fee_pct,
        "slippage_pct": args.slippage_pct,
        "loss_cap_pct": args.loss_cap_pct,
        "position_size_pct": args.position_size_pct,
        "starting_capital": args.starting_capital,
        "strategy": args.strategy,
        "strategy_params": strategy_params,
    }

    prices = [p for _ts, p in history]
    for i in range(MIN_HISTORY_FOR_STRATEGY, len(history)):
        ts, price = history[i]
        window = prices[: i + 1]

        def fetch(_window=window, _price=price):
            return _window, _price

        run_tick(track_name, config, fetch, ledger=ledger, ts=ts)

    curve = ledger.equity_curve()
    trades = ledger.trades()
    ledger.close()

    if not curve:
        return {"error": "no equity curve produced"}

    equities = [row[2] for row in curve]
    timestamps = [row[0] for row in curve]
    final_equity = equities[-1]
    return_pct = 100 * (final_equity - args.starting_capital) / args.starting_capital
    dd = max_drawdown_pct(equities)
    bh_return = 100 * (prices[-1] - prices[MIN_HISTORY_FOR_STRATEGY]) / prices[MIN_HISTORY_FOR_STRATEGY]
    alpha = return_pct - bh_return

    rt_returns_pct = net_round_trip_returns_pct(trades, args.starting_capital)
    ci = bootstrap_ci(rt_returns_pct) if rt_returns_pct else None
    wr = win_rate(trades, args.starting_capital)

    result = {
        "symbol": symbol,
        "candles": len(history),
        "span_days": (history[-1][0] - history[0][0]).days,
        "return_pct": return_pct,
        "n_trades": len(trades),
        "n_round_trips": len(rt_returns_pct),
        "win_rate_pct": wr,
        "max_dd_pct": dd,
        "buy_hold_pct": bh_return,
        "alpha_pct": alpha,
        "bootstrap_ci_90": ci,
    }

    if oos_start_idx is not None and oos_start_idx < len(timestamps):
        # Find where the OOS period starts in the equity curve (same index space
        # as `history`, offset by MIN_HISTORY_FOR_STRATEGY ticks already consumed).
        oos_curve_idx = max(0, oos_start_idx - MIN_HISTORY_FOR_STRATEGY)
        if 0 < oos_curve_idx < len(equities):
            is_equity = equities[oos_curve_idx]
            oos_return = 100 * (final_equity - is_equity) / is_equity if is_equity > 0 else float("nan")
            result["in_sample_days"] = args.days - args.oos_days if args.days else None
            result["oos_days"] = args.oos_days
            result["oos_return_pct"] = oos_return
            result["in_sample_return_pct"] = 100 * (is_equity - args.starting_capital) / args.starting_capital

    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=["crypto", "stocks"], required=True)
    parser.add_argument("--symbols", required=True, help="comma-separated, e.g. BTC/USD,ETH/USD or SPY,QQQ")
    parser.add_argument("--exchange", default="kraken")
    parser.add_argument("--timeframe", default="15m", help="crypto only (ccxt timeframe string)")
    parser.add_argument("--interval", default="15m", help="stocks only (yfinance interval string)")
    parser.add_argument("--period", default="60d", help="stocks only (yfinance period string, its own max for 15m is ~60d)")
    parser.add_argument("--days", type=int, default=180, help="crypto only: how far back to request (actual coverage may be less, see output)")
    parser.add_argument("--strategy", required=True, choices=sorted(STRATEGIES))
    parser.add_argument("--fast-window", type=int, default=5, help="momentum/momentum_regime only")
    parser.add_argument("--slow-window", type=int, default=20, help="momentum/momentum_regime only")
    parser.add_argument("--rsi-period", type=int, default=14, help="mean_reversion only")
    parser.add_argument("--oversold", type=float, default=30, help="mean_reversion only")
    parser.add_argument("--overbought", type=float, default=70, help="mean_reversion only")
    parser.add_argument("--fee-pct", type=float, required=True)
    parser.add_argument("--slippage-pct", type=float, required=True)
    parser.add_argument("--position-size-pct", type=float, default=50)
    parser.add_argument("--loss-cap-pct", type=float, default=50)
    parser.add_argument("--starting-capital", type=float, default=10.0)
    parser.add_argument("--oos-days", type=int, default=0, help="reserve the last N days as out-of-sample; 0 disables the split")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if args.strategy == "mean_reversion":
        strategy_param_str = f"rsi_period={args.rsi_period} oversold={args.oversold} overbought={args.overbought}"
    else:
        strategy_param_str = f"fast_window={args.fast_window} slow_window={args.slow_window}"
    print(f"Backtesting strategy={args.strategy} over {len(symbols)} symbol(s), source={args.source}")
    print("Fixed parameters (not tuned against this run's output):")
    print(f"  {strategy_param_str} fee_pct={args.fee_pct} "
          f"slippage_pct={args.slippage_pct} position_size_pct={args.position_size_pct} loss_cap_pct={args.loss_cap_pct}")
    print()

    results = []
    for symbol in symbols:
        print(f"Fetching {symbol}...")
        if args.source == "crypto":
            history = fetch_crypto_history(symbol, args.exchange, args.timeframe, args.days)
        else:
            history = fetch_stock_history(symbol, args.period, args.interval)
        print(f"  got {len(history)} closed candles"
              + (f" ({(history[-1][0]-history[0][0]).days}d span)" if len(history) >= 2 else ""))
        results.append(run_one_backtest(symbol, history, args))

    header = f"{'Symbol':<12}{'Candles':>9}{'Span(d)':>9}{'Return%':>10}{'Trades':>8}{'Win%':>7}{'MaxDD%':>9}{'BuyHold%':>10}{'Alpha%':>9}{'90% CI (sum of trade %)':>28}"
    print()
    print(header)
    print("-" * len(header))
    for r, symbol in zip(results, symbols):
        if "error" in r:
            print(f"{symbol:<12}{'--':>9}{'--':>9}{'--':>10}{'--':>8}{'--':>7}{'--':>9}{'--':>10}{'--':>9}  {r['error']}")
            continue
        ci_str = f"[{r['bootstrap_ci_90'][0]:+.1f}%, {r['bootstrap_ci_90'][1]:+.1f}%]" if r["bootstrap_ci_90"] else "--"
        wr_str = "--" if r["win_rate_pct"] != r["win_rate_pct"] else f"{r['win_rate_pct']:.0f}%"
        print(
            f"{symbol:<12}{r['candles']:>9}{r['span_days']:>9}{r['return_pct']:>+9.2f}%"
            f"{r['n_trades']:>8}{wr_str:>7}{r['max_dd_pct']:>8.2f}%{r['buy_hold_pct']:>+9.2f}%{r['alpha_pct']:>+8.2f}%  {ci_str:>26}"
        )
        if "oos_return_pct" in r:
            print(f"{'':<12}in-sample ({r['in_sample_days']}d): {r['in_sample_return_pct']:+.2f}%   "
                  f"out-of-sample ({r['oos_days']}d): {r['oos_return_pct']:+.2f}%")

    print()
    print("CI is a 90% bootstrap interval over the sum of round-trip returns (%), resampled with")
    print("replacement -- a quick noise check, not a rigorous stats-package-grade interval. A CI")
    print("straddling 0% means this run's result is plausibly noise; the fewer trades, the wider it is.")
    print()
    print("Known limitations: spot engine only (no leverage, no Polymarket -- see this file's docstring);")
    print("forced loss-cap-flatten trades are timestamped with wall-clock time, not the historical bar,")
    print("since engine/risk.py's check_loss_cap doesn't take an injected clock (cosmetic only -- doesn't")
    print("affect return%, trade count, or drawdown).")


if __name__ == "__main__":
    main()
