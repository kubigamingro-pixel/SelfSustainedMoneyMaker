"""Round 7: 3 more new strategy ideas (22nd-24th overall), explored on the
existing BTC/ETH 1h/4h datasets at 1x first. Leverage (2x/10x) and held-out
(SOL/XRP/ADA) confirmation only run for whatever passes the 1x gate, same
discipline as rounds 3-6.

Does NOT modify research_harness.py, backtest_all.py, or PREREGISTRATION.md.

After 5 rounds of indicator variations (21 ideas, 0 confirmed), this round
shifts the hypothesis space slightly rather than inventing another oscillator:

1. Volatility-regime-filtered momentum: the same 5/20 SMA crossover as the
   original momentum strategy, but only takes the bullish-cross entry when
   realized volatility (20-bar stdev of returns) is currently BELOW its own
   trailing 100-bar median -- i.e. trades momentum only in a "quiet" regime,
   skipping it during volatility spikes. A genuinely different regime filter
   than momentum_regime (which filtered by trend direction, not volatility
   level).
2. Donchian midline crossover: buy when price crosses above the MIDLINE of a
   20-bar Donchian channel ((20-bar high + 20-bar low)/2), sell on the
   reverse cross. Distinct from donchian_breakout (which enters at the
   channel's extreme bands, not its center).
3. Volatility-shock-following momentum: the mirror-opposite hypothesis of
   round 5's return-shock mean reversion. Buy when the latest single-bar
   return's magnitude exceeds 3x the recent average absolute return AND the
   shock was to the upside (ride the move instead of fading it); sell on the
   first subsequent down bar.
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

def _sma_at(series, idx, window):
    return sum(series[idx - window + 1: idx + 1]) / window


def _realized_vol_at(series, idx, window):
    tail = series[idx - window + 1: idx + 1]
    returns = [(tail[i] - tail[i - 1]) / tail[i - 1] for i in range(1, len(tail))]
    return statistics.pstdev(returns)


class VolRegimeMomentumStrategy(Strategy):
    ALLOWED_PARAMS = {"fast_window", "slow_window", "vol_window", "vol_lookback"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for vol_regime_momentum: {sorted(unexpected)}")
        fast = self.params.get("fast_window", 5)
        slow = self.params.get("slow_window", 20)
        if not (0 < fast < slow):
            raise ValueError(f"require 0 < fast_window < slow_window, got {fast},{slow}")

    def decide(self, series: list, holding_position: bool) -> Signal:
        fast_w = self.params.get("fast_window", 5)
        slow_w = self.params.get("slow_window", 20)
        vol_w = self.params.get("vol_window", 20)
        vol_lookback = self.params.get("vol_lookback", 100)
        needed = vol_lookback + vol_w + slow_w + 3
        if len(series) < needed:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{needed})")

        idx_now = len(series) - 1
        fast_now, slow_now = _sma_at(series, idx_now, fast_w), _sma_at(series, idx_now, slow_w)
        fast_prev, slow_prev = _sma_at(series, idx_now - 1, fast_w), _sma_at(series, idx_now - 1, slow_w)

        crossed_up = fast_prev <= slow_prev and fast_now > slow_now
        crossed_down = fast_prev >= slow_prev and fast_now < slow_now

        vol_now = _realized_vol_at(series, idx_now, vol_w)
        vol_series = [_realized_vol_at(series, i, vol_w) for i in range(idx_now - vol_lookback + 1, idx_now + 1)]
        vol_median = statistics.median(vol_series)
        low_vol_regime = vol_now < vol_median

        if crossed_up and low_vol_regime and not holding_position:
            return Signal(Action.BUY, f"bullish cross in low-vol regime ({vol_now:.5f} < median {vol_median:.5f})")
        if crossed_down and holding_position:
            return Signal(Action.SELL, "bearish cross")
        return Signal(Action.HOLD, "no vol-regime-gated signal")


class DonchianMidlineCrossStrategy(Strategy):
    ALLOWED_PARAMS = {"window"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for donchian_midline_cross: {sorted(unexpected)}")
        window = self.params.get("window", 20)
        if window <= 1:
            raise ValueError("window must be > 1")

    @staticmethod
    def _midline_at(series, idx, window):
        tail = series[idx - window + 1: idx + 1]
        return (max(tail) + min(tail)) / 2.0

    def decide(self, series: list, holding_position: bool) -> Signal:
        window = self.params.get("window", 20)
        needed = window + 2
        if len(series) < needed:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{needed})")

        idx_now = len(series) - 1
        mid_now = self._midline_at(series, idx_now, window)
        mid_prev = self._midline_at(series, idx_now - 1, window)
        price_now, price_prev = series[idx_now], series[idx_now - 1]

        crossed_up = price_prev <= mid_prev and price_now > mid_now
        crossed_down = price_prev >= mid_prev and price_now < mid_now

        if crossed_up and not holding_position:
            return Signal(Action.BUY, f"price {price_now:.4f} crossed above Donchian midline {mid_now:.4f}")
        if crossed_down and holding_position:
            return Signal(Action.SELL, f"price {price_now:.4f} crossed below Donchian midline {mid_now:.4f}")
        return Signal(Action.HOLD, "no Donchian midline cross")


class VolShockMomentumStrategy(Strategy):
    ALLOWED_PARAMS = {"window", "shock_multiplier"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for vol_shock_momentum: {sorted(unexpected)}")
        window = self.params.get("window", 20)
        mult = self.params.get("shock_multiplier", 3.0)
        if window <= 1:
            raise ValueError("window must be > 1")
        if mult <= 0:
            raise ValueError("shock_multiplier must be positive")

    def decide(self, series: list, holding_position: bool) -> Signal:
        window = self.params.get("window", 20)
        mult = self.params.get("shock_multiplier", 3.0)
        needed = window + 2
        if len(series) < needed:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{needed})")

        tail = series[-(window + 1):]
        returns = [(tail[i] - tail[i - 1]) / tail[i - 1] for i in range(1, len(tail))]
        avg_abs = sum(abs(r) for r in returns[:-1]) / (len(returns) - 1)
        last_return = returns[-1]

        if avg_abs > 0 and last_return > mult * avg_abs and not holding_position:
            return Signal(Action.BUY, f"upside shock: return {last_return:.4f} > {mult}x avg abs {avg_abs:.4f}")
        if last_return < 0 and holding_position:
            return Signal(Action.SELL, f"first down bar after shock: return {last_return:.4f}")
        return Signal(Action.HOLD, "no volatility shock")


NEW_STRATEGIES = {
    "vol_regime_momentum": VolRegimeMomentumStrategy,
    "donchian_midline_cross": DonchianMidlineCrossStrategy,
    "vol_shock_momentum": VolShockMomentumStrategy,
}
_runner_module.STRATEGIES.update(NEW_STRATEGIES)
_leveraged_runner_module.STRATEGIES.update(NEW_STRATEGIES)

NEW_STRATEGY_PARAMS = {
    "vol_regime_momentum": {"fast_window": 5, "slow_window": 20, "vol_window": 20, "vol_lookback": 100},
    "donchian_midline_cross": {"window": 20},
    "vol_shock_momentum": {"window": 20, "shock_multiplier": 3.0},
}


def run_leveraged_cell(strategy_name: str, params: dict, history: list, base_config: dict, leverage_variant: str):
    cell_config = dict(base_config)
    cell_config["strategy"] = strategy_name
    cell_config["strategy_params"] = params
    sim_config, ledger, error = leveraged_replay(f"research7_{strategy_name}", history, cell_config, leverage_variant, None)
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
