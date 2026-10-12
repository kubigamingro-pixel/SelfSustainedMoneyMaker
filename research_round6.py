"""Round 6: 3 more new strategy ideas (19th-21st overall across this
project's research rounds), explored on the existing BTC/ETH 1h/4h datasets
at 1x first. Leverage (2x/10x) and held-out (SOL/XRP/ADA) confirmation only
run for whatever passes the 1x gate, same discipline as rounds 3-5.

Does NOT modify research_harness.py, backtest_all.py, or PREREGISTRATION.md.
Reuses research_harness's load_saved/run_cell/passes and backtest_all's
replay/summarize. The 3 new strategies are registered into
engine.runner.STRATEGIES/engine.leveraged_runner.STRATEGIES in this
process's memory only.

Avoided this round: anything recursive/stateful like true Parabolic SAR or
Supertrend's trailing bands, which need their own previous value carried
tick-to-tick -- the engine recreates a fresh strategy instance every tick
(confirmed reading engine/runner.py), so a recursive indicator would need an
O(n) recompute on every one of n ticks (O(n^2) total), impractical on this
project's ~35,000-candle 1h datasets.

The 3 new ideas, listed up front (before any were run against data):

1. Fisher Transform turning-point: normalize price's position within a
   rolling 10-bar range to [-1, 1], then apply Ehlers' Fisher log-transform
   (0.5 * ln((1+x)/(1-x))), which stretches values near the extremes far
   more than a linear z-score or percentile would. Buy when the Fisher
   value turns up from a local trough (its bar-over-bar change flips from
   <=0 to >0), sell when it turns down from a local peak.
2. Rolling percentile-rank mean reversion: buy when the current close's
   percentile rank within the trailing 20 bars is <= 10% (near the bottom
   of its recent range), sell when that rank is >= 90% while holding.
   Ordinal ranking, not a parametric z-score/RSI/CCI formula.
3. Connors RSI composite: the classic 3-part blend -- short RSI(3) on raw
   price, a streak-RSI(2) on the length of the current consecutive up/down
   run, and the percentile rank of today's 1-bar return among the last 100
   returns. Buy when the composite average is < 10 (deeply oversold on all
   3 measures at once), sell when it's > 70 while holding.
"""

import math
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

def _fisher_value(series, idx, window):
    tail = series[idx - window + 1: idx + 1]
    hi, lo = max(tail), min(tail)
    if hi == lo:
        return 0.0
    x = 2 * ((series[idx] - lo) / (hi - lo) - 0.5)
    x = max(-0.999, min(0.999, x))
    return 0.5 * math.log((1 + x) / (1 - x))


class FisherTransformStrategy(Strategy):
    ALLOWED_PARAMS = {"window"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for fisher_transform: {sorted(unexpected)}")
        window = self.params.get("window", 10)
        if window <= 2:
            raise ValueError("window must be > 2")

    def decide(self, series: list, holding_position: bool) -> Signal:
        window = self.params.get("window", 10)
        needed = window + 3
        if len(series) < needed:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{needed})")

        idx_now = len(series) - 1
        f_now = _fisher_value(series, idx_now, window)
        f_prev = _fisher_value(series, idx_now - 1, window)
        f_prev2 = _fisher_value(series, idx_now - 2, window)

        delta_now = f_now - f_prev
        delta_prev = f_prev - f_prev2

        turned_up = delta_prev <= 0 and delta_now > 0
        turned_down = delta_prev >= 0 and delta_now < 0

        if turned_up and not holding_position:
            return Signal(Action.BUY, f"Fisher({window}) turned up: {f_prev:.3f} -> {f_now:.3f}")
        if turned_down and holding_position:
            return Signal(Action.SELL, f"Fisher({window}) turned down: {f_prev:.3f} -> {f_now:.3f}")
        return Signal(Action.HOLD, "no Fisher turning point")


class PercentileRankMeanReversionStrategy(Strategy):
    ALLOWED_PARAMS = {"window", "lower_pct", "upper_pct"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for percentile_rank_mean_reversion: {sorted(unexpected)}")
        window = self.params.get("window", 20)
        lower = self.params.get("lower_pct", 10)
        upper = self.params.get("upper_pct", 90)
        if window <= 1:
            raise ValueError("window must be > 1")
        if not (0 <= lower < upper <= 100):
            raise ValueError("require 0 <= lower_pct < upper_pct <= 100")

    def decide(self, series: list, holding_position: bool) -> Signal:
        window = self.params.get("window", 20)
        lower = self.params.get("lower_pct", 10)
        upper = self.params.get("upper_pct", 90)
        if len(series) < window + 1:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{window + 1})")

        tail = series[-window:]
        price = series[-1]
        rank = 100 * sum(1 for x in tail if x <= price) / len(tail)

        if rank <= lower and not holding_position:
            return Signal(Action.BUY, f"percentile rank {rank:.1f} <= {lower}")
        if rank >= upper and holding_position:
            return Signal(Action.SELL, f"percentile rank {rank:.1f} >= {upper}")
        return Signal(Action.HOLD, f"percentile rank {rank:.1f} within range")


def _simple_rsi(values, period):
    if len(values) < period + 1:
        return 50.0
    tail = values[-(period + 1):]
    gains = [max(0.0, tail[i] - tail[i - 1]) for i in range(1, len(tail))]
    losses = [max(0.0, tail[i - 1] - tail[i]) for i in range(1, len(tail))]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _streak_at(series, idx, max_lookback=30):
    if idx < 1:
        return 0
    diff = series[idx] - series[idx - 1]
    if diff == 0:
        return 0
    sign = 1 if diff > 0 else -1
    length = 0
    j = idx
    while j >= 1 and length < max_lookback:
        d = series[j] - series[j - 1]
        if d == 0:
            break
        s = 1 if d > 0 else -1
        if s != sign:
            break
        length += 1
        j -= 1
    return sign * length


class ConnorsRSIStrategy(Strategy):
    ALLOWED_PARAMS = {"rsi_period", "streak_rsi_period", "roc_lookback", "oversold", "overbought"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for connors_rsi: {sorted(unexpected)}")
        rsi_period = self.params.get("rsi_period", 3)
        streak_period = self.params.get("streak_rsi_period", 2)
        roc_lookback = self.params.get("roc_lookback", 100)
        oversold = self.params.get("oversold", 10)
        overbought = self.params.get("overbought", 70)
        if rsi_period <= 0 or streak_period <= 0 or roc_lookback <= 1:
            raise ValueError("rsi_period, streak_rsi_period must be positive; roc_lookback must be > 1")
        if not (0 <= oversold < overbought <= 100):
            raise ValueError("require 0 <= oversold < overbought <= 100")

    def decide(self, series: list, holding_position: bool) -> Signal:
        rsi_period = self.params.get("rsi_period", 3)
        streak_period = self.params.get("streak_rsi_period", 2)
        roc_lookback = self.params.get("roc_lookback", 100)
        oversold = self.params.get("oversold", 10)
        overbought = self.params.get("overbought", 70)
        needed = roc_lookback + 5
        if len(series) < needed:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{needed})")

        idx_now = len(series) - 1
        rsi_short = _simple_rsi(series[-(rsi_period + 1):], rsi_period)

        streak_series = [_streak_at(series, i) for i in range(idx_now - streak_period - 1, idx_now + 1)]
        streak_rsi = _simple_rsi(streak_series, streak_period)

        window = series[idx_now - roc_lookback: idx_now + 1]
        returns = [(window[i] - window[i - 1]) / window[i - 1] for i in range(1, len(window))]
        today_return = returns[-1]
        roc_rank = 100 * sum(1 for r in returns[:-1] if r <= today_return) / (len(returns) - 1)

        crsi = (rsi_short + streak_rsi + roc_rank) / 3

        if crsi < oversold and not holding_position:
            return Signal(Action.BUY, f"ConnorsRSI {crsi:.1f} < {oversold}")
        if crsi > overbought and holding_position:
            return Signal(Action.SELL, f"ConnorsRSI {crsi:.1f} > {overbought}")
        return Signal(Action.HOLD, f"ConnorsRSI {crsi:.1f} within range")


NEW_STRATEGIES = {
    "fisher_transform": FisherTransformStrategy,
    "percentile_rank_mean_reversion": PercentileRankMeanReversionStrategy,
    "connors_rsi": ConnorsRSIStrategy,
}
_runner_module.STRATEGIES.update(NEW_STRATEGIES)
_leveraged_runner_module.STRATEGIES.update(NEW_STRATEGIES)

NEW_STRATEGY_PARAMS = {
    "fisher_transform": {"window": 10},
    "percentile_rank_mean_reversion": {"window": 20, "lower_pct": 10, "upper_pct": 90},
    "connors_rsi": {"rsi_period": 3, "streak_rsi_period": 2, "roc_lookback": 100, "oversold": 10, "overbought": 70},
}


def run_leveraged_cell(strategy_name: str, params: dict, history: list, base_config: dict, leverage_variant: str):
    cell_config = dict(base_config)
    cell_config["strategy"] = strategy_name
    cell_config["strategy_params"] = params
    sim_config, ledger, error = leveraged_replay(f"research6_{strategy_name}", history, cell_config, leverage_variant, None)
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
