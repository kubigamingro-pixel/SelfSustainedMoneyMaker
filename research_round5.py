"""Round 5: 3 more new strategy ideas (16th-18th overall across this
project's research rounds), explored on the existing BTC/ETH 1h/4h datasets
at 1x first. Leverage (2x/10x) and held-out (SOL/XRP/ADA) confirmation are
only run for whatever passes the 1x gate, same discipline as rounds 3-4.

Does NOT modify research_harness.py, backtest_all.py, or PREREGISTRATION.md.
Reuses research_harness's load_saved/run_cell/passes and backtest_all's
replay/summarize, same pattern as every prior round. The 3 new strategies
are registered into engine.runner.STRATEGIES/engine.leveraged_runner.STRATEGIES
in this process's memory only -- no engine file on disk is touched.

The 3 new ideas, listed up front (before any were run against data):

1. Hull Moving Average (HMA) crossover: HMA(n) = WMA(sqrt(n)) applied to
   (2*WMA(n/2) - WMA(n)), the classic Alan Hull construction that reduces
   lag versus a plain SMA/EMA. Buy when price crosses above HMA(20), sell
   on the reverse cross. A different smoothing filter than anything tried
   so far (momentum/momentum_regime used plain SMA crossovers, macd_crossover
   used EMA).
2. Return-shock mean reversion: buy when the most recent single-bar return's
   z-score (relative to the trailing 20-bar return distribution) drops below
   -2.5 -- an unusually sharp one-bar move, not a sustained deviation -- and
   sell when price recovers back above its 5-bar SMA. Distinct from RSI/
   Bollinger/CCI, which all react to a sustained price-level deviation, not
   a single shock bar.
3. Awesome Oscillator zero-line cross: SMA(5) - SMA(34) of close; buy when
   this histogram crosses above zero, sell when it crosses below. Distinct
   from macd_crossover -- no EMA smoothing and no separate signal line, just
   the raw SMA-difference's own sign change.
"""

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from research_harness import load_saved, run_cell, passes
from backtest_all import replay as leveraged_replay
from backtest_all import summarize as leveraged_summarize
from engine.config import load_tracks_config
from engine.strategies.base import Action, Signal, Strategy
import engine.runner as _runner_module
import engine.leveraged_runner as _leveraged_runner_module

EXPLORATION_ASSETS = ["BTC", "ETH"]
HELDOUT_ASSETS = ["SOL", "XRP", "ADA"]
TIMEFRAMES = ["1h", "4h"]
LEVERAGE_VARIANTS = ["2x", "10x"]


# --------------------------------------------------------------------------
# The 3 new strategies (full description in the module docstring).
# --------------------------------------------------------------------------

def _wma_at(series, idx, window):
    tail = series[idx - window + 1: idx + 1]
    n = len(tail)
    weights = range(1, n + 1)
    num = sum(w * x for w, x in zip(weights, tail))
    den = n * (n + 1) / 2
    return num / den


def _hma_at(series, idx, n):
    half = max(1, n // 2)
    sqrt_n = max(1, round(n ** 0.5))
    raw_tail = [2 * _wma_at(series, idx - j, half) - _wma_at(series, idx - j, n) for j in range(sqrt_n - 1, -1, -1)]
    L = len(raw_tail)
    weights = range(1, L + 1)
    num = sum(w * x for w, x in zip(weights, raw_tail))
    den = L * (L + 1) / 2
    return num / den


class HullMACrossStrategy(Strategy):
    ALLOWED_PARAMS = {"window"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for hull_ma_cross: {sorted(unexpected)}")
        window = self.params.get("window", 20)
        if window <= 3:
            raise ValueError("window must be > 3")

    def decide(self, series: list, holding_position: bool) -> Signal:
        n = self.params.get("window", 20)
        half = max(1, n // 2)
        sqrt_n = max(1, round(n ** 0.5))
        needed = n + sqrt_n + half + 3
        if len(series) < needed:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{needed})")

        idx_now = len(series) - 1
        idx_prev = idx_now - 1
        hma_now = _hma_at(series, idx_now, n)
        hma_prev = _hma_at(series, idx_prev, n)
        price_now, price_prev = series[idx_now], series[idx_prev]

        crossed_up = price_prev <= hma_prev and price_now > hma_now
        crossed_down = price_prev >= hma_prev and price_now < hma_now

        if crossed_up and not holding_position:
            return Signal(Action.BUY, f"price {price_now:.4f} crossed above HMA({n}) {hma_now:.4f}")
        if crossed_down and holding_position:
            return Signal(Action.SELL, f"price {price_now:.4f} crossed below HMA({n}) {hma_now:.4f}")
        return Signal(Action.HOLD, "no HMA cross")


class ReturnShockMeanReversionStrategy(Strategy):
    ALLOWED_PARAMS = {"window", "z_threshold", "exit_sma_window"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for return_shock_mean_reversion: {sorted(unexpected)}")
        window = self.params.get("window", 20)
        z_threshold = self.params.get("z_threshold", -2.5)
        exit_sma = self.params.get("exit_sma_window", 5)
        if window <= 2:
            raise ValueError("window must be > 2")
        if z_threshold >= 0:
            raise ValueError("z_threshold must be negative")
        if exit_sma <= 0:
            raise ValueError("exit_sma_window must be positive")

    def decide(self, series: list, holding_position: bool) -> Signal:
        window = self.params.get("window", 20)
        z_threshold = self.params.get("z_threshold", -2.5)
        exit_sma_window = self.params.get("exit_sma_window", 5)
        needed = window + 2
        if len(series) < needed:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{needed})")

        tail = series[-(window + 1):]
        returns = [(tail[i] - tail[i - 1]) / tail[i - 1] for i in range(1, len(tail))]
        last_return = returns[-1]
        mean_r = statistics.mean(returns)
        std_r = statistics.pstdev(returns)
        z = (last_return - mean_r) / std_r if std_r > 0 else 0.0

        price = series[-1]
        exit_sma = sum(series[-exit_sma_window:]) / exit_sma_window

        if z < z_threshold and not holding_position:
            return Signal(Action.BUY, f"return z-score {z:.2f} < {z_threshold} (shock)")
        if price > exit_sma and holding_position:
            return Signal(Action.SELL, f"price {price:.6f} recovered above SMA({exit_sma_window}) {exit_sma:.6f}")
        return Signal(Action.HOLD, "no return-shock signal")


class AwesomeOscillatorCrossStrategy(Strategy):
    ALLOWED_PARAMS = {"fast_window", "slow_window"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for awesome_oscillator_cross: {sorted(unexpected)}")
        fast = self.params.get("fast_window", 5)
        slow = self.params.get("slow_window", 34)
        if not (0 < fast < slow):
            raise ValueError(f"require 0 < fast_window < slow_window, got {fast},{slow}")

    @staticmethod
    def _ao(series, fast, slow, offset=0):
        end = len(series) - offset
        fast_avg = sum(series[end - fast:end]) / fast
        slow_avg = sum(series[end - slow:end]) / slow
        return fast_avg - slow_avg

    def decide(self, series: list, holding_position: bool) -> Signal:
        fast = self.params.get("fast_window", 5)
        slow = self.params.get("slow_window", 34)
        needed = slow + 2
        if len(series) < needed:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{needed})")

        ao_now = self._ao(series, fast, slow, offset=0)
        ao_prev = self._ao(series, fast, slow, offset=1)

        crossed_up = ao_prev <= 0 and ao_now > 0
        crossed_down = ao_prev >= 0 and ao_now < 0

        if crossed_up and not holding_position:
            return Signal(Action.BUY, f"AO crossed above zero: {ao_now:.6f}")
        if crossed_down and holding_position:
            return Signal(Action.SELL, f"AO crossed below zero: {ao_now:.6f}")
        return Signal(Action.HOLD, "no AO zero cross")


NEW_STRATEGIES = {
    "hull_ma_cross": HullMACrossStrategy,
    "return_shock_mean_reversion": ReturnShockMeanReversionStrategy,
    "awesome_oscillator_cross": AwesomeOscillatorCrossStrategy,
}
_runner_module.STRATEGIES.update(NEW_STRATEGIES)
_leveraged_runner_module.STRATEGIES.update(NEW_STRATEGIES)

NEW_STRATEGY_PARAMS = {
    "hull_ma_cross": {"window": 20},
    "return_shock_mean_reversion": {"window": 20, "z_threshold": -2.5, "exit_sma_window": 5},
    "awesome_oscillator_cross": {"fast_window": 5, "slow_window": 34},
}


def run_leveraged_cell(strategy_name: str, params: dict, history: list, base_config: dict, leverage_variant: str):
    cell_config = dict(base_config)
    cell_config["strategy"] = strategy_name
    cell_config["strategy_params"] = params
    sim_config, ledger, error = leveraged_replay(f"research5_{strategy_name}", history, cell_config, leverage_variant, None)
    first_price, last_price = history[0][1], history[-1][1]
    result = leveraged_summarize(sim_config, ledger, error, first_price, last_price)
    ledger.close()
    return result


def main():
    config = load_tracks_config()
    base_config = config["crypto_momentum"]

    print("=== Phase 1: 1x exploration on BTC/ETH ===")
    passing_pairs = set()
    for asset in EXPLORATION_ASSETS:
        for timeframe in TIMEFRAMES:
            history = load_saved(f"{asset}/USD", timeframe)
            for strategy_name, params in NEW_STRATEGY_PARAMS.items():
                r = run_cell(strategy_name, params, history, base_config)
                cell_passes = passes(r)
                print(f"  {asset} {timeframe} {strategy_name}: equity_return={r['return_pct']:.2f}% "
                      f"summed_rt={r['summed_rt_return_pct']:.2f}% fees={r['fees_pct_of_capital']:.2f}% "
                      f"TIM={r['time_in_market_pct']:.1f}% n_rt={r['n_round_trips']} p={r['p_value']} "
                      f"-> {'PASS' if cell_passes else 'fail'}")
                if cell_passes:
                    passing_pairs.add((asset, timeframe, strategy_name))

    print(f"\n=== Phase 1 complete. Cells passing 1x exploration: {sorted(passing_pairs)} ===")
    print("=== Phase 2: for passing cells only, test 2x and 10x (same signal, same asset/timeframe) ===")

    if not passing_pairs:
        print("  nothing passed 1x -- no leverage extension to test. No strategy here is usable at 1x, 2x or 10x.")
        return passing_pairs

    for asset, timeframe, strategy_name in sorted(passing_pairs):
        params = NEW_STRATEGY_PARAMS[strategy_name]
        history = load_saved(f"{asset}/USD", timeframe)
        for variant in LEVERAGE_VARIANTS:
            r = run_leveraged_cell(strategy_name, params, history, base_config, variant)
            print(f"  {asset} {timeframe} {strategy_name} {variant}: return={r['return_pct']:.2f}% "
                  f"capital_lost={r['capital_lost_pct']:.2f}% n_trades={r['n_trades']} "
                  f"win={r['win_rate_pct']} maxDD={r['max_dd_pct']:.2f}% resets={r['n_epoch_resets']} "
                  f"ci={r['ci']}")

    print("=== Phase 3: held-out confirmation on SOL/XRP/ADA (same timeframe, same strategy, exactly once each) ===")
    strategies_by_timeframe = {}
    for asset, timeframe, strategy_name in passing_pairs:
        strategies_by_timeframe.setdefault(timeframe, set()).add(strategy_name)
    for timeframe, strategy_names in strategies_by_timeframe.items():
        for strategy_name in strategy_names:
            params = NEW_STRATEGY_PARAMS[strategy_name]
            for asset in HELDOUT_ASSETS:
                history = load_saved(f"{asset}/USD", timeframe)
                r = run_cell(strategy_name, params, history, base_config)
                print(f"  {asset} {timeframe} {strategy_name}: equity_return={r['return_pct']:.2f}% "
                      f"summed_rt={r['summed_rt_return_pct']:.2f}% fees={r['fees_pct_of_capital']:.2f}% "
                      f"TIM={r['time_in_market_pct']:.1f}% n_rt={r['n_round_trips']} p={r['p_value']} "
                      f"-> {'PASS' if passes(r) else 'fail'}")

    return passing_pairs


if __name__ == "__main__":
    main()
