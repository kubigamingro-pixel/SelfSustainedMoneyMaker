"""Round 3: explore 3 more new strategy ideas on the existing BTC/ETH 1h/4h
datasets at 1x, then -- for whatever passes the 1x gate -- test whether that
same edge survives at 2x and 10x (leverage amplifies the same signal, it
doesn't get separately gated, matching how every leveraged track in this
project works). This directly answers "find new strategies we can use with
1x, 2x and 10x": a strategy only qualifies as usable across all three if it
passes 1x AND leverage doesn't destroy it.

Does NOT modify research_harness.py, backtest_all.py, or PREREGISTRATION.md.
Imports research_harness's fetch/load/run_cell/random_timing_pvalue/passes
for the 1x stage, and backtest_all's replay/summarize/chained_equities/etc.
for the leveraged stage (those reset-aware helpers already exist there from
an earlier round, built for exactly this kind of chained-equity leveraged
reporting). The 3 new strategies are registered into
engine.runner.STRATEGIES/engine.leveraged_runner.STRATEGIES in this
process's memory only -- no engine file on disk is touched.

The 3 new ideas, listed up front (before any were run against data). All are
O(window) per tick, not O(whole-history) -- the engine creates a fresh
strategy instance every tick (no state carries between ticks), so a
recursive/path-dependent indicator like true Parabolic SAR would need an
O(n) recompute on every one of n ticks (O(n^2) total) -- impractical on this
project's ~35,000-candle 1h datasets. All 3 below avoid that by only ever
looking at a fixed trailing window:

1. Ichimoku Tenkan/Kijun cross (close-based, since this project's saved data
   has close + low only, not full OHLC): Tenkan-sen = midpoint of the
   highest/lowest close over the last 9 bars; Kijun-sen = the same over 26
   bars. Buy when Tenkan crosses above Kijun, sell on the reverse cross.
2. Linear regression channel mean-reversion: fit a least-squares line to the
   last 20 closes; buy when price closes 2+ standard deviations (of the
   fit's residuals) below the line, sell when price reverts back above it.
   Distinct from Bollinger Bands (which band a flat SMA, not a sloped trend
   line) -- this strategy only calls a price "unusually low" relative to
   where the recent trend itself says it should be.
3. Commodity Channel Index (CCI) mean-reversion: CCI(20) = (close -
   SMA(close,20)) / (0.015 x mean absolute deviation of close from that SMA).
   Buy when CCI < -100, sell when CCI > +100 while holding. A different
   normalization than RSI (bounded 0-100 via gain/loss ratio), Stochastic
   (range position), or Bollinger (raw standard deviation) -- CCI scales by
   mean *absolute* deviation, which is less sensitive to a single outlier
   tick than a standard deviation is.
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
TIMEFRAMES = ["1h", "4h"]
LEVERAGE_VARIANTS = ["2x", "10x"]


# --------------------------------------------------------------------------
# The 3 new strategies (full description in the module docstring).
# --------------------------------------------------------------------------

class IchimokuCrossStrategy(Strategy):
    ALLOWED_PARAMS = {"tenkan_window", "kijun_window"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for ichimoku_cross: {sorted(unexpected)}")
        tenkan = self.params.get("tenkan_window", 9)
        kijun = self.params.get("kijun_window", 26)
        if not (0 < tenkan < kijun):
            raise ValueError(f"require 0 < tenkan_window < kijun_window, got {tenkan},{kijun}")

    @staticmethod
    def _midpoint(series, window):
        tail = series[-window:]
        return (max(tail) + min(tail)) / 2.0

    def decide(self, series: list, holding_position: bool) -> Signal:
        tenkan_w = self.params.get("tenkan_window", 9)
        kijun_w = self.params.get("kijun_window", 26)
        needed = kijun_w + 1
        if len(series) < needed:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{needed})")

        tenkan_now = self._midpoint(series, tenkan_w)
        kijun_now = self._midpoint(series, kijun_w)
        tenkan_prev = self._midpoint(series[:-1], tenkan_w)
        kijun_prev = self._midpoint(series[:-1], kijun_w)

        crossed_up = tenkan_prev <= kijun_prev and tenkan_now > kijun_now
        crossed_down = tenkan_prev >= kijun_prev and tenkan_now < kijun_now

        if crossed_up and not holding_position:
            return Signal(Action.BUY, f"Tenkan {tenkan_now:.4f} crossed above Kijun {kijun_now:.4f}")
        if crossed_down and holding_position:
            return Signal(Action.SELL, f"Tenkan {tenkan_now:.4f} crossed below Kijun {kijun_now:.4f}")
        return Signal(Action.HOLD, "no Ichimoku cross")


class LinearRegressionChannelStrategy(Strategy):
    ALLOWED_PARAMS = {"window", "num_std"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for linear_regression_channel: {sorted(unexpected)}")
        window = self.params.get("window", 20)
        num_std = self.params.get("num_std", 2.0)
        if window <= 2:
            raise ValueError("window must be > 2")
        if num_std <= 0:
            raise ValueError("num_std must be positive")

    @staticmethod
    def _fit_line(tail):
        n = len(tail)
        xs = list(range(n))
        mean_x = (n - 1) / 2.0
        mean_y = sum(tail) / n
        cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, tail))
        var_x = sum((x - mean_x) ** 2 for x in xs)
        slope = cov / var_x if var_x > 0 else 0.0
        intercept = mean_y - slope * mean_x
        fitted = [intercept + slope * x for x in xs]
        residuals = [y - f for y, f in zip(tail, fitted)]
        resid_std = statistics.pstdev(residuals) if len(residuals) > 1 else 0.0
        line_now = intercept + slope * (n - 1)
        return line_now, resid_std

    def decide(self, series: list, holding_position: bool) -> Signal:
        window = self.params.get("window", 20)
        num_std = self.params.get("num_std", 2.0)
        if len(series) < window + 1:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{window})")

        tail = series[-window:]
        line_now, resid_std = self._fit_line(tail)
        price = series[-1]
        lower = line_now - num_std * resid_std

        if price < lower and not holding_position:
            return Signal(Action.BUY, f"price {price:.6f} below regression line {line_now:.6f} - {num_std}std")
        if price > line_now and holding_position:
            return Signal(Action.SELL, f"price {price:.6f} back above regression line {line_now:.6f}")
        return Signal(Action.HOLD, "within regression channel")


class CCIMeanReversionStrategy(Strategy):
    ALLOWED_PARAMS = {"window", "oversold", "overbought"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for cci_mean_reversion: {sorted(unexpected)}")
        window = self.params.get("window", 20)
        oversold = self.params.get("oversold", -100)
        overbought = self.params.get("overbought", 100)
        if window <= 1:
            raise ValueError("window must be > 1")
        if oversold >= overbought:
            raise ValueError("oversold must be < overbought")

    @staticmethod
    def _cci(tail):
        n = len(tail)
        sma = sum(tail) / n
        mean_abs_dev = sum(abs(x - sma) for x in tail) / n
        if mean_abs_dev == 0:
            return 0.0
        return (tail[-1] - sma) / (0.015 * mean_abs_dev)

    def decide(self, series: list, holding_position: bool) -> Signal:
        window = self.params.get("window", 20)
        oversold = self.params.get("oversold", -100)
        overbought = self.params.get("overbought", 100)
        if len(series) < window + 1:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{window})")

        cci = self._cci(series[-window:])

        if cci < oversold and not holding_position:
            return Signal(Action.BUY, f"CCI({window})={cci:.1f} < oversold {oversold}")
        if cci > overbought and holding_position:
            return Signal(Action.SELL, f"CCI({window})={cci:.1f} > overbought {overbought}")
        return Signal(Action.HOLD, f"CCI({window})={cci:.1f} within range")


NEW_STRATEGIES = {
    "ichimoku_cross": IchimokuCrossStrategy,
    "linear_regression_channel": LinearRegressionChannelStrategy,
    "cci_mean_reversion": CCIMeanReversionStrategy,
}
_runner_module.STRATEGIES.update(NEW_STRATEGIES)
_leveraged_runner_module.STRATEGIES.update(NEW_STRATEGIES)

NEW_STRATEGY_PARAMS = {
    "ichimoku_cross": {"tenkan_window": 9, "kijun_window": 26},
    "linear_regression_channel": {"window": 20, "num_std": 2.0},
    "cci_mean_reversion": {"window": 20, "oversold": -100, "overbought": 100},
}


def run_leveraged_cell(strategy_name: str, params: dict, history: list, base_config: dict, leverage_variant: str):
    """Reuses backtest_all.py's reset-aware chained-equity reporting (built for
    exactly this: a leveraged replay whose loss cap can breach and reset mid-
    window). Returns the same dict shape as backtest_all.summarize()."""
    cell_config = dict(base_config)
    cell_config["strategy"] = strategy_name
    cell_config["strategy_params"] = params
    sim_config, ledger, error = leveraged_replay(f"research3_{strategy_name}", history, cell_config, leverage_variant, None)
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
    else:
        for asset, timeframe, strategy_name in sorted(passing_pairs):
            params = NEW_STRATEGY_PARAMS[strategy_name]
            history = load_saved(f"{asset}/USD", timeframe)
            for variant in LEVERAGE_VARIANTS:
                r = run_leveraged_cell(strategy_name, params, history, base_config, variant)
                print(f"  {asset} {timeframe} {strategy_name} {variant}: return={r['return_pct']:.2f}% "
                      f"capital_lost={r['capital_lost_pct']:.2f}% n_trades={r['n_trades']} "
                      f"win={r['win_rate_pct']} maxDD={r['max_dd_pct']:.2f}% resets={r['n_epoch_resets']} "
                      f"ci={r['ci']}")


if __name__ == "__main__":
    main()
