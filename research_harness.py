"""Reusable research harness built on top of backtest_all.py.

Built in response to a direct request (2026-10-12) to formalize the ad hoc
BTC/ETH regime experiments into a repeatable tool: fetch and save 4 years of
BTC/ETH 1h/4h candles once, split into an exploration period (years 1-3) and
a held-out confirmation period (year 4, touched only after a strategy passes
exploration), test 6 strategies (3 already in the live engine, 3 classic
ideas picked for this harness -- see CLASSIC_STRATEGIES below), and report
return, fees, time-in-market, and a p-value against random entries at
matching exposure for every cell.

**No live code is changed.** The 3 new strategies are registered into
engine.runner.STRATEGIES / engine.leveraged_runner.STRATEGIES at import time,
in this process's memory only -- no engine file is edited. This lets the
harness reuse the exact same run_tick/replay/cost-filter/ledger machinery the
live system and every prior backtest in this series used, rather than
reimplementing fill/fee/sizing logic a second time (and risking it diverging
from the real thing).

Methodology notes, read before trusting a number:
- "Using saved datasets only": fetched once per (asset, timeframe) via
  fetch_and_save(), written to audits/2026-10-12-research-harness-datasets/
  as CSV. Every subsequent step (year splitting, every strategy test, both
  exploration and confirmation) reads from that saved file, never re-fetches.
- Exploration/confirmation split: the 4-year history is cut into four
  chronological ~1-year segments. Years 1-3 (the oldest three, chronologically
  first) are the exploration set; year 4 (the most recent, closest to "now")
  is the held-out confirmation set, read only for strategies that pass
  exploration -- standard walk-forward discipline: the most recent period is
  the one closest to live conditions, so it's the one reserved as the true
  out-of-sample check.
- p-value vs. random entries at matching exposure: for each strategy's real
  round trips (N trades, each with a known duration in bars), this draws many
  random trade sets of N trades with the SAME durations at random starting
  points in the same price series, computes each draw's net-of-cost summed
  return the same way backtest_all.py's bootstrap_ci does (sum of per-trade
  net % returns, not full compounding -- consistent with every CI already
  reported in this project's prior experiments), and reports the fraction of
  draws that matched or beat the real strategy's actual return. This is the
  same method an earlier independent review used to test the regime-guard
  experiment (audits/2026-10-12-btc-eth-decision-grade-review.md), reused here
  as the harness's standard test for every strategy, not just regime-filtered
  variants.
- A strategy "passes" exploration if: (a) its real summed return beats its own
  random-timing-matched benchmark at p < 0.10, AND (b) that real return is
  positive (net of costs). Both conditions are checked per (strategy, asset,
  timeframe) cell independently -- a strategy can pass on BTC 4h and fail on
  ETH 1h; each cell's confirmation-year check only runs if that specific cell
  passed.
"""

import random
import statistics
import sys
import time as time_module
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ccxt

from backtest_all import replay
from engine.config import load_tracks_config
from engine.strategies.base import Action, Signal, Strategy
from scoreboard import win_rate

DATASET_DIR = Path(__file__).resolve().parent / "audits" / "2026-10-12-research-harness-datasets"
DATASET_DIR.mkdir(parents=True, exist_ok=True)

ASSETS = ["BTC/USD", "ETH/USD"]
TIMEFRAMES = ["1h", "4h"]
N_YEARS = 4
RANDOM_DRAWS = 2000
PASS_P_THRESHOLD = 0.10


# --------------------------------------------------------------------------
# Three classic strategies not currently in the live engine, picked up front:
#
# 1. Donchian channel breakout (the core "Turtle Trading" rule): buy on a new
#    N-bar high, exit on a new M-bar low. A trend-following idea structurally
#    different from an SMA crossover -- it reacts to price extremes directly,
#    not to two averages crossing.
# 2. MACD crossover: buy when the MACD line (12/26 EMA difference) crosses
#    above its 9-period signal line, sell on the opposite cross. A different,
#    very widely used momentum formulation (EMA-based, not SMA-based).
# 3. Bollinger Band mean-reversion: buy when price closes below the lower
#    band (SMA - k*stdev), sell when it closes back above the middle band. A
#    different mean-reversion formulation than the live engine's RSI-based one
#    (volatility-adaptive bands, not a bounded oscillator).
# --------------------------------------------------------------------------

def _sma(series, window):
    tail = series[-window:]
    return sum(tail) / len(tail)


def _ema_series(series, period):
    """Returns the EMA value at every point from the first `period` samples
    onward (needed for MACD, which is a difference of two EMAs over time)."""
    if len(series) < period:
        return []
    k = 2.0 / (period + 1)
    ema = [sum(series[:period]) / period]
    for price in series[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema


class DonchianBreakoutStrategy(Strategy):
    ALLOWED_PARAMS = {"entry_window", "exit_window"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for donchian_breakout: {sorted(unexpected)}")
        entry_window = self.params.get("entry_window", 20)
        exit_window = self.params.get("exit_window", 10)
        if entry_window <= 0 or exit_window <= 0:
            raise ValueError("entry_window/exit_window must be positive")

    def decide(self, series: list, holding_position: bool) -> Signal:
        entry_window = self.params.get("entry_window", 20)
        exit_window = self.params.get("exit_window", 10)
        needed = max(entry_window, exit_window) + 1
        if len(series) < needed:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{needed})")

        price = series[-1]
        if not holding_position:
            prior_high = max(series[-entry_window - 1:-1])
            if price > prior_high:
                return Signal(Action.BUY, f"new {entry_window}-bar high: {price:.6f} > {prior_high:.6f}")
            return Signal(Action.HOLD, "no breakout")
        else:
            prior_low = min(series[-exit_window - 1:-1])
            if price < prior_low:
                return Signal(Action.SELL, f"new {exit_window}-bar low: {price:.6f} < {prior_low:.6f}")
            return Signal(Action.HOLD, "holding, no exit breakdown")


class MACDCrossoverStrategy(Strategy):
    ALLOWED_PARAMS = {"fast_period", "slow_period", "signal_period"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for macd_crossover: {sorted(unexpected)}")
        fast = self.params.get("fast_period", 12)
        slow = self.params.get("slow_period", 26)
        signal = self.params.get("signal_period", 9)
        if fast <= 0 or slow <= 0 or signal <= 0:
            raise ValueError("fast_period/slow_period/signal_period must be positive")
        if fast >= slow:
            raise ValueError(f"fast_period ({fast}) must be less than slow_period ({slow})")

    def decide(self, series: list, holding_position: bool) -> Signal:
        fast_period = self.params.get("fast_period", 12)
        slow_period = self.params.get("slow_period", 26)
        signal_period = self.params.get("signal_period", 9)
        needed = slow_period + signal_period + 1
        if len(series) < needed:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{needed})")

        fast_ema = _ema_series(series, fast_period)
        slow_ema = _ema_series(series, slow_period)
        offset = len(fast_ema) - len(slow_ema)
        macd_line = [f - s for f, s in zip(fast_ema[offset:], slow_ema)]
        if len(macd_line) < signal_period + 1:
            return Signal(Action.HOLD, "insufficient MACD history")
        signal_line = _ema_series(macd_line, signal_period)

        macd_now, macd_prev = macd_line[-1], macd_line[-2]
        sig_now, sig_prev = signal_line[-1], signal_line[-2]
        crossed_up = macd_prev <= sig_prev and macd_now > sig_now
        crossed_down = macd_prev >= sig_prev and macd_now < sig_now

        if crossed_up and not holding_position:
            return Signal(Action.BUY, f"MACD {macd_now:.4f} crossed above signal {sig_now:.4f}")
        if crossed_down and holding_position:
            return Signal(Action.SELL, f"MACD {macd_now:.4f} crossed below signal {sig_now:.4f}")
        return Signal(Action.HOLD, "no MACD crossover")


class BollingerMeanReversionStrategy(Strategy):
    ALLOWED_PARAMS = {"window", "num_std"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for bollinger_mean_reversion: {sorted(unexpected)}")
        window = self.params.get("window", 20)
        num_std = self.params.get("num_std", 2.0)
        if window <= 1:
            raise ValueError("window must be > 1")
        if num_std <= 0:
            raise ValueError("num_std must be positive")

    def decide(self, series: list, holding_position: bool) -> Signal:
        window = self.params.get("window", 20)
        num_std = self.params.get("num_std", 2.0)
        if len(series) < window + 1:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{window})")

        tail = series[-window:]
        mid = sum(tail) / window
        stdev = statistics.pstdev(tail)
        lower = mid - num_std * stdev
        upper = mid - 0 * stdev  # exit at the middle band, not the upper band
        price = series[-1]

        if price < lower and not holding_position:
            return Signal(Action.BUY, f"price {price:.6f} below lower band {lower:.6f} (mid {mid:.6f}, {num_std}std)")
        if price > mid and holding_position:
            return Signal(Action.SELL, f"price {price:.6f} back above mid band {mid:.6f}")
        return Signal(Action.HOLD, "within bands")


# Register the 3 new strategies into the live engine's own STRATEGIES dicts,
# IN MEMORY ONLY, for the duration of this process -- no file on disk is
# touched. This is what lets replay()/run_tick() run these exactly like any
# live strategy (same cost filter, same sizing, same ledger), rather than a
# second, parallel implementation that could diverge from the real thing.
import engine.runner as _runner_module
import engine.leveraged_runner as _leveraged_runner_module

CLASSIC_STRATEGIES = {
    "donchian_breakout": DonchianBreakoutStrategy,
    "macd_crossover": MACDCrossoverStrategy,
    "bollinger_mean_reversion": BollingerMeanReversionStrategy,
}
_runner_module.STRATEGIES.update(CLASSIC_STRATEGIES)
_leveraged_runner_module.STRATEGIES.update(CLASSIC_STRATEGIES)

ALL_STRATEGIES = {
    "momentum": {},
    "momentum_regime": {},
    "mean_reversion": {"rsi_period": 14, "oversold": 30, "overbought": 70},
    "donchian_breakout": {"entry_window": 20, "exit_window": 10},
    "macd_crossover": {"fast_period": 12, "slow_period": 26, "signal_period": 9},
    "bollinger_mean_reversion": {"window": 20, "num_std": 2.0},
}


# --------------------------------------------------------------------------
# Dataset fetch/save/load -- fetched once, read from disk for everything else.
# --------------------------------------------------------------------------

def fetch_and_save(symbol: str, timeframe: str, years: int = N_YEARS):
    asset_label = symbol.split("/")[0]
    path = DATASET_DIR / f"{asset_label}_{timeframe}.csv"
    since_iso = (datetime.now(timezone.utc) - timedelta(days=365 * years)).strftime("%Y-%m-%dT00:00:00Z")

    exchange = ccxt.bitstamp({"enableRateLimit": True})
    duration_ms = exchange.parse_timeframe(timeframe) * 1000
    now_ms = exchange.milliseconds()
    since_ms = exchange.parse8601(since_iso)

    all_candles, cursor, seen = [], since_ms, set()
    for _ in range(2000):
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
    history = [(datetime.fromtimestamp(c[0] / 1000.0, tz=timezone.utc), c[4], c[3]) for c in closed]

    with open(path, "w") as f:
        f.write("timestamp,price,low\n")
        for ts, price, low in history:
            f.write(f"{ts.isoformat()},{price},{low}\n")
    return path, history


def load_saved(symbol: str, timeframe: str):
    asset_label = symbol.split("/")[0]
    path = DATASET_DIR / f"{asset_label}_{timeframe}.csv"
    history = []
    with open(path) as f:
        next(f)  # header
        for line in f:
            ts_str, price_str, low_str = line.strip().split(",")
            history.append((datetime.fromisoformat(ts_str), float(price_str), float(low_str)))
    return history


def split_years(history: list, n_years: int = N_YEARS):
    """Splits into n_years chronological ~1-year segments, oldest first.
    Returns a list of (label, sub_history); label 1 is oldest, label n_years
    is most recent."""
    window_end = history[-1][0]
    boundaries = [window_end - timedelta(days=365 * k) for k in range(n_years, -1, -1)]
    segments = []
    for i in range(n_years):
        start, end = boundaries[i], boundaries[i + 1]
        seg = [h for h in history if start <= h[0] < end or (i == n_years - 1 and h[0] == end)]
        segments.append((i + 1, seg))
    return segments


# --------------------------------------------------------------------------
# Random-timing p-value test
# --------------------------------------------------------------------------

def _round_trip_bar_durations(trades: list, timestamps: list):
    """Converts real trade timestamps into (start_index, duration_in_bars)
    pairs against the given price-series timestamps, for closed round trips
    only (an open position at the end of the window is excluded, since a
    random draw needs a known, fixed duration to be comparable)."""
    ts_index = {ts: i for i, ts in enumerate(timestamps)}
    durations = []
    buy_ts = None
    for ts_str, side, *_rest in trades:
        ts = datetime.fromisoformat(ts_str)
        if side == "BUY":
            buy_ts = ts
        elif side == "SELL" and buy_ts is not None:
            if buy_ts in ts_index and ts in ts_index:
                durations.append(ts_index[ts] - ts_index[buy_ts])
            buy_ts = None
    return durations


def random_timing_pvalue(prices: list, timestamps: list, trades: list, fee_pct: float, slippage_pct: float,
                          position_size_pct: float, real_summed_return_pct: float,
                          n_draws: int = RANDOM_DRAWS, seed: int = 0):
    """p = fraction of n_draws random trade sets (same count and durations as
    the real strategy's closed round trips, random start points in the same
    price series) whose summed net-of-cost return matches or beats the real
    strategy's summed return. Net-of-cost here follows the same convention as
    backtest_all.py's bootstrap_ci: sum of per-trade net % returns, not full
    compounding. Returns (p_value, n_round_trips_used) -- None if there are
    fewer than 2 closed round trips (too little to test).

    2026-10-12 review fix (finding #1, a real miscalibration): the real side
    (real_summed_return_pct, from net_round_trip_returns_pct) is an EQUITY-scale
    return -- at position_size_pct=50, a round trip moves equity by about half
    the raw price move and pays about half the round-trip cost (both scaled by
    the same 50% position size). The first version of this function compared
    that against a random side computed at FULL (100%) notional with the FULL
    round-trip cost -- not the same units. This made the random baseline carry
    roughly double the real side's cost drag, which produced a spuriously low
    p-value for any strategy that traded a lot, regardless of whether its
    timing was actually better than random (confirmed: 9/24 cells below p=0.10
    in the unscaled version, vs. the ~2-3 expected by chance; re-scaled, only
    4/24 land below 0.10). Fixed by applying the same position_size_pct scaling
    to both the raw price move and the cost on the random side, matching the
    real side's actual unit."""
    durations = _round_trip_bar_durations(trades, timestamps)
    if len(durations) < 2:
        return None, len(durations)

    round_trip_cost_pct = 2 * (fee_pct + slippage_pct)
    exposure = position_size_pct / 100.0
    rng = random.Random(seed)
    n = len(prices)
    draws_meeting_or_beating = 0
    for _ in range(n_draws):
        draw_total = 0.0
        for dur in durations:
            if dur <= 0 or dur >= n:
                continue
            start = rng.randint(0, n - dur - 1)
            raw_return_pct = 100 * (prices[start + dur] - prices[start]) / prices[start]
            draw_total += (raw_return_pct - round_trip_cost_pct) * exposure
        if draw_total >= real_summed_return_pct:
            draws_meeting_or_beating += 1
    return draws_meeting_or_beating / n_draws, len(durations)


# --------------------------------------------------------------------------
# One cell: run a strategy over one history segment, report full metrics.
# --------------------------------------------------------------------------

def run_cell(strategy_name: str, strategy_params: dict, history: list, base_config: dict):
    cell_config = dict(base_config)
    cell_config["strategy"] = strategy_name
    cell_config["strategy_params"] = strategy_params
    sim_track_label = f"research_{strategy_name}"
    sim_config, ledger, error = replay(sim_track_label, history, cell_config, "1x", None)

    curve = ledger.equity_curve()
    trades = ledger.trades()
    starting_capital = sim_config["starting_capital"]
    final_equity = curve[-1][2] if curve else starting_capital
    return_pct = 100 * (final_equity - starting_capital) / starting_capital

    from backtest_all import net_round_trip_returns_pct
    rt_returns = net_round_trip_returns_pct(trades, starting_capital)
    n_round_trips = len(rt_returns)
    summed_rt_return_pct = sum(rt_returns)
    wr = win_rate(trades, starting_capital)

    fee_pct = cell_config["fee_pct"]
    slippage_pct = cell_config["slippage_pct"]
    total_fees_paid = sum(t[4] for t in trades)  # fee column
    fees_pct_of_capital = 100 * total_fees_paid / starting_capital

    timestamps = [h[0] for h in history]
    prices = [h[1] for h in history]
    window_start, window_end = timestamps[0], timestamps[-1]

    # time in market
    total_span = (window_end - window_start).total_seconds()
    in_market = 0.0
    buy_ts = None
    for ts_str, side, *_rest in trades:
        ts = datetime.fromisoformat(ts_str)
        if side == "BUY":
            buy_ts = ts
        elif side == "SELL" and buy_ts is not None:
            in_market += (ts - buy_ts).total_seconds()
            buy_ts = None
    if buy_ts is not None:
        in_market += (window_end - buy_ts).total_seconds()
    tim_pct = 100 * in_market / total_span if total_span > 0 else 0.0

    position_size_pct = cell_config["position_size_pct"]
    p_value, n_rt_tested = random_timing_pvalue(
        prices, timestamps, trades, fee_pct, slippage_pct, position_size_pct, summed_rt_return_pct
    )

    ledger.close()
    return {
        "return_pct": return_pct, "n_round_trips": n_round_trips, "win_rate_pct": wr,
        "fees_pct_of_capital": fees_pct_of_capital, "time_in_market_pct": tim_pct,
        "summed_rt_return_pct": summed_rt_return_pct, "p_value": p_value, "error": error,
    }


def passes(cell_result: dict) -> bool:
    if cell_result["p_value"] is None:
        return False
    return cell_result["p_value"] < PASS_P_THRESHOLD and cell_result["summed_rt_return_pct"] > 0


def main():
    # 2026-10-12 review finding #5: results depend on the Python version
    # (3.12+ changed float sum() to compensated summation, which can flip an
    # SMA crossover sitting exactly on a tie). Recorded so any report this
    # produces states which interpreter it ran under.
    print(f"Python: {sys.version}")

    config = load_tracks_config()
    base_config = config["crypto_momentum"]  # fee/slippage/position_size_pct, shared by all crypto tracks

    print("=== Fetching and saving 4 years of data (only if not already saved) ===")
    for symbol in ASSETS:
        for timeframe in TIMEFRAMES:
            asset_label = symbol.split("/")[0]
            path = DATASET_DIR / f"{asset_label}_{timeframe}.csv"
            if path.exists():
                print(f"  {path.name} already saved, skipping fetch")
                continue
            path, history = fetch_and_save(symbol, timeframe)
            print(f"  fetched and saved {path.name}: {len(history)} candles")

    exploration_results = {}
    confirmation_results = {}

    for symbol in ASSETS:
        for timeframe in TIMEFRAMES:
            asset_label = symbol.split("/")[0]
            history = load_saved(symbol, timeframe)
            years = split_years(history)
            exploration_history = years[0][1] + years[1][1] + years[2][1]
            confirmation_history = years[3][1]
            print(f"\n{asset_label} {timeframe}: exploration={len(exploration_history)} candles "
                  f"({exploration_history[0][0]} -> {exploration_history[-1][0]}), "
                  f"confirmation={len(confirmation_history)} candles "
                  f"({confirmation_history[0][0]} -> {confirmation_history[-1][0]})")

            for strategy_name, strategy_params in ALL_STRATEGIES.items():
                key = (asset_label, timeframe, strategy_name)
                print(f"  exploring {strategy_name}...")
                r = run_cell(strategy_name, strategy_params, exploration_history, base_config)
                exploration_results[key] = r
                cell_passes = passes(r)
                print(f"    equity_return={r['return_pct']:.2f}% summed_rt_return={r['summed_rt_return_pct']:.2f}% "
                      f"fees={r['fees_pct_of_capital']:.2f}% TIM={r['time_in_market_pct']:.1f}% "
                      f"p={r['p_value']} n_rt={r['n_round_trips']} -> {'PASS' if cell_passes else 'fail'}")
                if cell_passes:
                    print(f"  confirming {strategy_name} on year 4 (already examined earlier for momentum/momentum_regime -- see report)...")
                    cr = run_cell(strategy_name, strategy_params, confirmation_history, base_config)
                    confirmation_results[key] = cr
                    print(f"    confirmation equity_return={cr['return_pct']:.2f}% summed_rt_return={cr['summed_rt_return_pct']:.2f}% p={cr['p_value']}")

    return exploration_results, confirmation_results


if __name__ == "__main__":
    main()
