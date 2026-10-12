"""Round 2: explore 3 new strategy ideas on the existing BTC/ETH 1h/4h
datasets, then test whatever passes exactly once on a genuinely held-out
asset set (SOL, XRP, ADA) that was fetched and saved but not analyzed until
exploration finished.

Does NOT modify research_harness.py or PREREGISTRATION.md -- builds on
research_harness's existing fetch_and_save/load_saved/run_cell/
random_timing_pvalue/passes machinery, importing it unchanged, the same way
research_harness.py itself built on backtest_all.py without modifying it.
The 3 new strategies are registered into engine.runner.STRATEGIES /
engine.leveraged_runner.STRATEGIES in this process's memory only (same
pattern research_harness.py already uses for its own 3 classic strategies) --
no engine file on disk is touched.

The 3 new ideas, listed up front (per the request) before any of them were
run against data:

1. Stochastic Oscillator mean-reversion (close-based %K/%D -- this project's
   saved datasets have close + low only, not full OHLC, so this is a
   simplified close-based variant, not the textbook high/low version): buy
   when %K crosses above %D while below 20 (oversold), sell when %K crosses
   below %D while above 80 (overbought).
2. Triple SMA ribbon trend-following (5/20/50): buy when fast > mid > slow
   (full bullish alignment), sell when that alignment breaks. Distinct from
   a single 2-SMA crossover or MACD -- requires sustained 3-way alignment,
   not one crossing event.
3. Time-series momentum / Rate-of-Change: buy when the N-bar rate of change
   is positive and above a threshold, sell when it turns negative. Pure
   price-change based (no moving average at all), used by many systematic
   trend-followers.

Exploration set: BTC/USD, ETH/USD, 1h and 4h, the full already-saved 4-year
datasets (per PREREGISTRATION.md, this data is already burned for the
original 6 strategies -- using it again here for 3 genuinely new strategies
is exploration, not a re-test of anything pre-registered, and is NOT itself
pre-registered or held-out evidence).

Held-out set: SOL/USD, XRP/USD, ADA/USD, 1h and 4h, fetched and saved by a
separate script invocation *before* this file ran any analysis -- enforced
here by control flow (fetch_and_save was called in isolation first; this
file's own code never fetches the held-out data, only loads the already-saved
CSVs, and only in the confirmation phase, after every exploration decision is
already made and printed).

Gate: identical to research_harness.py's `passes()` -- p < 0.10 (random-entry
test, position_size_pct-scaled) AND positive summed round-trip return,
per (strategy, timeframe) cell on the exploration assets. A (strategy,
timeframe) pair that passes on ANY exploration asset (BTC and/or ETH) is then
tested on all 3 held-out assets at that SAME timeframe, exactly once each.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import statistics

from research_harness import (
    DATASET_DIR, load_saved, run_cell, passes, PASS_P_THRESHOLD,
)
from engine.config import load_tracks_config
from engine.strategies.base import Action, Signal, Strategy
import engine.runner as _runner_module
import engine.leveraged_runner as _leveraged_runner_module

EXPLORATION_ASSETS = ["BTC", "ETH"]
HELDOUT_ASSETS = ["SOL", "XRP", "ADA"]
TIMEFRAMES = ["1h", "4h"]


# --------------------------------------------------------------------------
# The 3 new strategies (see module docstring for the full description of each,
# written before any of them were run against data).
# --------------------------------------------------------------------------

def _sma(series, window):
    return sum(series[-window:]) / window


class StochasticMeanReversionStrategy(Strategy):
    ALLOWED_PARAMS = {"window", "oversold", "overbought"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for stochastic_mean_reversion: {sorted(unexpected)}")
        window = self.params.get("window", 14)
        oversold = self.params.get("oversold", 20)
        overbought = self.params.get("overbought", 80)
        if window <= 1:
            raise ValueError("window must be > 1")
        if not (0 <= oversold < overbought <= 100):
            raise ValueError("require 0 <= oversold < overbought <= 100")

    def _percent_k(self, series, window):
        tail = series[-window:]
        lo, hi = min(tail), max(tail)
        if hi == lo:
            return 50.0
        return 100 * (series[-1] - lo) / (hi - lo)

    def decide(self, series: list, holding_position: bool) -> Signal:
        window = self.params.get("window", 14)
        oversold = self.params.get("oversold", 20)
        overbought = self.params.get("overbought", 80)
        needed = window + 4  # +3 for the %D smoothing, +1 for a "prev" comparison
        if len(series) < needed:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{needed})")

        k_now = self._percent_k(series, window)
        k_series = [self._percent_k(series[: -(2 - i)] if i < 2 else series, window) for i in range(3)]
        d_now = sum(k_series) / 3
        k_prev = self._percent_k(series[:-1], window)
        d_prev_series = [self._percent_k(series[:-1][: -(2 - i)] if i < 2 else series[:-1], window) for i in range(3)]
        d_prev = sum(d_prev_series) / 3

        crossed_up = k_prev <= d_prev and k_now > d_now
        crossed_down = k_prev >= d_prev and k_now < d_now

        if crossed_up and k_now < oversold and not holding_position:
            return Signal(Action.BUY, f"%K {k_now:.1f} crossed above %D {d_now:.1f}, below oversold {oversold}")
        if crossed_down and k_now > overbought and holding_position:
            return Signal(Action.SELL, f"%K {k_now:.1f} crossed below %D {d_now:.1f}, above overbought {overbought}")
        return Signal(Action.HOLD, "no stochastic signal")


class TripleSMARibbonStrategy(Strategy):
    ALLOWED_PARAMS = {"fast_window", "mid_window", "slow_window"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for triple_sma_ribbon: {sorted(unexpected)}")
        fast = self.params.get("fast_window", 5)
        mid = self.params.get("mid_window", 20)
        slow = self.params.get("slow_window", 50)
        if not (fast < mid < slow):
            raise ValueError(f"require fast_window < mid_window < slow_window, got {fast},{mid},{slow}")

    def decide(self, series: list, holding_position: bool) -> Signal:
        fast_w = self.params.get("fast_window", 5)
        mid_w = self.params.get("mid_window", 20)
        slow_w = self.params.get("slow_window", 50)
        needed = slow_w + 1
        if len(series) < needed:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{needed})")

        fast_now, mid_now, slow_now = _sma(series, fast_w), _sma(series, mid_w), _sma(series, slow_w)
        fast_prev, mid_prev, slow_prev = _sma(series[:-1], fast_w), _sma(series[:-1], mid_w), _sma(series[:-1], slow_w)

        aligned_bull_now = fast_now > mid_now > slow_now
        aligned_bull_prev = fast_prev > mid_prev > slow_prev

        if aligned_bull_now and not aligned_bull_prev and not holding_position:
            return Signal(Action.BUY, f"ribbon aligned bullish: {fast_now:.4f} > {mid_now:.4f} > {slow_now:.4f}")
        if not aligned_bull_now and aligned_bull_prev and holding_position:
            return Signal(Action.SELL, "ribbon alignment broke")
        if holding_position and not aligned_bull_now:
            # also exit if alignment already broken (not just on the break tick),
            # in case a tick was missed right at the break
            return Signal(Action.SELL, "ribbon not aligned bullish, exiting")
        return Signal(Action.HOLD, "no ribbon signal")


class TimeSeriesMomentumStrategy(Strategy):
    ALLOWED_PARAMS = {"window", "entry_threshold_pct"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for time_series_momentum: {sorted(unexpected)}")
        window = self.params.get("window", 20)
        threshold = self.params.get("entry_threshold_pct", 2.0)
        if window <= 0:
            raise ValueError("window must be positive")
        if threshold < 0:
            raise ValueError("entry_threshold_pct must be >= 0")

    def decide(self, series: list, holding_position: bool) -> Signal:
        window = self.params.get("window", 20)
        threshold = self.params.get("entry_threshold_pct", 2.0)
        if len(series) < window + 1:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{window + 1})")

        roc_now = 100 * (series[-1] - series[-1 - window]) / series[-1 - window]

        if roc_now > threshold and not holding_position:
            return Signal(Action.BUY, f"{window}-bar ROC {roc_now:.2f}% > +{threshold}%")
        if roc_now < 0 and holding_position:
            return Signal(Action.SELL, f"{window}-bar ROC {roc_now:.2f}% turned negative")
        return Signal(Action.HOLD, "no ROC signal")


NEW_STRATEGIES = {
    "stochastic_mean_reversion": StochasticMeanReversionStrategy,
    "triple_sma_ribbon": TripleSMARibbonStrategy,
    "time_series_momentum": TimeSeriesMomentumStrategy,
}
_runner_module.STRATEGIES.update(NEW_STRATEGIES)
_leveraged_runner_module.STRATEGIES.update(NEW_STRATEGIES)

NEW_STRATEGY_PARAMS = {
    "stochastic_mean_reversion": {"window": 14, "oversold": 20, "overbought": 80},
    "triple_sma_ribbon": {"fast_window": 5, "mid_window": 20, "slow_window": 50},
    "time_series_momentum": {"window": 20, "entry_threshold_pct": 2.0},
}


def main():
    config = load_tracks_config()
    base_config = config["crypto_momentum"]

    print("=== Phase 1: exploration on BTC/ETH (already-saved, already-burned-for-other-strategies data) ===")
    exploration_results = {}
    passing_pairs = set()  # (strategy_name, timeframe)
    for asset in EXPLORATION_ASSETS:
        for timeframe in TIMEFRAMES:
            history = load_saved(f"{asset}/USD", timeframe)
            for strategy_name, params in NEW_STRATEGY_PARAMS.items():
                r = run_cell(strategy_name, params, history, base_config)
                exploration_results[(asset, timeframe, strategy_name)] = r
                cell_passes = passes(r)
                print(f"  {asset} {timeframe} {strategy_name}: equity_return={r['return_pct']:.2f}% "
                      f"summed_rt={r['summed_rt_return_pct']:.2f}% fees={r['fees_pct_of_capital']:.2f}% "
                      f"TIM={r['time_in_market_pct']:.1f}% n_rt={r['n_round_trips']} p={r['p_value']} "
                      f"-> {'PASS' if cell_passes else 'fail'}")
                if cell_passes:
                    passing_pairs.add((strategy_name, timeframe))

    print(f"\n=== Phase 1 complete. (strategy, timeframe) pairs passing exploration: {sorted(passing_pairs)} ===")
    print("=== Phase 2: held-out confirmation on SOL/XRP/ADA (not looked at before this point) ===")

    confirmation_results = {}
    if not passing_pairs:
        print("  nothing passed exploration -- no held-out testing performed.")
    else:
        for strategy_name, timeframe in sorted(passing_pairs):
            params = NEW_STRATEGY_PARAMS[strategy_name]
            for asset in HELDOUT_ASSETS:
                history = load_saved(f"{asset}/USD", timeframe)
                r = run_cell(strategy_name, params, history, base_config)
                confirmation_results[(asset, timeframe, strategy_name)] = r
                print(f"  {asset} {timeframe} {strategy_name}: equity_return={r['return_pct']:.2f}% "
                      f"summed_rt={r['summed_rt_return_pct']:.2f}% n_rt={r['n_round_trips']} p={r['p_value']}")

    return exploration_results, passing_pairs, confirmation_results


if __name__ == "__main__":
    main()
