"""Round 4: 3 more new strategy ideas (13th-15th overall across this
project's research rounds), explored on the existing BTC/ETH 1h/4h datasets
at 1x first. Leverage (2x/10x) is only tested for whatever passes the 1x
gate, same discipline as round 3 -- leverage can't create an edge that isn't
there at 1x, it can only amplify one that is.

Does NOT modify research_harness.py, backtest_all.py, or PREREGISTRATION.md.
Imports research_harness's load_saved/run_cell/passes for the 1x stage, and
backtest_all's replay/summarize for the leverage stage (same reuse pattern
as rounds 2 and 3). The 3 new strategies are registered into
engine.runner.STRATEGIES/engine.leveraged_runner.STRATEGIES in this
process's memory only -- no engine file on disk is touched.

The 3 new ideas, listed up front (before any were run against data):

1. Keltner Channel breakout: middle = SMA(close, 20); range = ATR(14) using
   a true-range proxy of max(close - low, abs(close - prev_close)) -- this
   project's saved data has close + low (not full OHLC), so this is the
   first strategy in this project's research rounds to actually use the
   saved `low` field rather than ignoring it. Buy when close breaks above
   the upper band (middle + 2*ATR) while not holding; sell when close drops
   back below the middle line while holding.
2. Momentum acceleration ("ROC of ROC"): buy when the 20-bar rate of change
   is positive AND increasing versus the prior bar (momentum building, not
   just present), sell when that acceleration turns negative. Distinct from
   round 2's time_series_momentum, which only checked the ROC's level, not
   its trend.
3. Dual-timeframe ROC confirmation: buy only when a 10-bar ROC AND a 50-bar
   ROC are simultaneously positive (two-horizon agreement required), sell
   when either turns negative. A classic "dual momentum" confluence idea,
   adapted here to single-asset timing rather than cross-asset allocation.
"""

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

class KeltnerBreakoutStrategy(Strategy):
    ALLOWED_PARAMS = {"sma_window", "atr_window", "num_atr"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for keltner_breakout: {sorted(unexpected)}")
        sma_w = self.params.get("sma_window", 20)
        atr_w = self.params.get("atr_window", 14)
        num_atr = self.params.get("num_atr", 2.0)
        if sma_w <= 1 or atr_w <= 1:
            raise ValueError("sma_window and atr_window must be > 1")
        if num_atr <= 0:
            raise ValueError("num_atr must be positive")

    @staticmethod
    def _atr(closes, lows, window):
        trs = []
        for i in range(len(closes) - window, len(closes)):
            tr = max(closes[i] - lows[i], abs(closes[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs) / window

    def decide(self, series: list, holding_position: bool) -> Signal:
        return self._decide_impl(series, None, holding_position)

    def _decide_impl(self, series: list, lows: list, holding_position: bool) -> Signal:
        sma_w = self.params.get("sma_window", 20)
        atr_w = self.params.get("atr_window", 14)
        num_atr = self.params.get("num_atr", 2.0)
        needed = max(sma_w, atr_w) + 1
        if len(series) < needed or lows is None or len(lows) < needed:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{needed})")

        sma_now = sum(series[-sma_w:]) / sma_w
        atr_now = self._atr(series, lows, atr_w)
        upper = sma_now + num_atr * atr_now
        price = series[-1]

        if price > upper and not holding_position:
            return Signal(Action.BUY, f"price {price:.4f} broke above Keltner upper {upper:.4f}")
        if price < sma_now and holding_position:
            return Signal(Action.SELL, f"price {price:.4f} dropped below Keltner middle {sma_now:.4f}")
        return Signal(Action.HOLD, "within Keltner channel")


class MomentumAccelerationStrategy(Strategy):
    ALLOWED_PARAMS = {"window"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for momentum_acceleration: {sorted(unexpected)}")
        window = self.params.get("window", 20)
        if window <= 0:
            raise ValueError("window must be positive")

    @staticmethod
    def _roc(series, window, end_offset=0):
        idx_now = len(series) - 1 - end_offset
        idx_then = idx_now - window
        return 100 * (series[idx_now] - series[idx_then]) / series[idx_then]

    def decide(self, series: list, holding_position: bool) -> Signal:
        window = self.params.get("window", 20)
        needed = window + 2
        if len(series) < needed:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{needed})")

        roc_now = self._roc(series, window, end_offset=0)
        roc_prev = self._roc(series, window, end_offset=1)
        accel = roc_now - roc_prev

        if roc_now > 0 and accel > 0 and not holding_position:
            return Signal(Action.BUY, f"ROC {roc_now:.2f}% accelerating (+{accel:.2f})")
        if accel < 0 and holding_position:
            return Signal(Action.SELL, f"ROC acceleration turned negative ({accel:.2f})")
        return Signal(Action.HOLD, "no momentum acceleration signal")


class DualTimeframeROCStrategy(Strategy):
    ALLOWED_PARAMS = {"short_window", "long_window"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for dual_timeframe_roc: {sorted(unexpected)}")
        short_w = self.params.get("short_window", 10)
        long_w = self.params.get("long_window", 50)
        if not (0 < short_w < long_w):
            raise ValueError(f"require 0 < short_window < long_window, got {short_w},{long_w}")

    @staticmethod
    def _roc(series, window):
        return 100 * (series[-1] - series[-1 - window]) / series[-1 - window]

    def decide(self, series: list, holding_position: bool) -> Signal:
        short_w = self.params.get("short_window", 10)
        long_w = self.params.get("long_window", 50)
        needed = long_w + 1
        if len(series) < needed:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{needed})")

        roc_short = self._roc(series, short_w)
        roc_long = self._roc(series, long_w)

        if roc_short > 0 and roc_long > 0 and not holding_position:
            return Signal(Action.BUY, f"both ROC({short_w})={roc_short:.2f}% and ROC({long_w})={roc_long:.2f}% positive")
        if (roc_short < 0 or roc_long < 0) and holding_position:
            return Signal(Action.SELL, f"ROC({short_w})={roc_short:.2f}% or ROC({long_w})={roc_long:.2f}% turned negative")
        return Signal(Action.HOLD, "no dual-timeframe agreement")


NEW_STRATEGIES = {
    "keltner_breakout": KeltnerBreakoutStrategy,
    "momentum_acceleration": MomentumAccelerationStrategy,
    "dual_timeframe_roc": DualTimeframeROCStrategy,
}
_runner_module.STRATEGIES.update(NEW_STRATEGIES)
_leveraged_runner_module.STRATEGIES.update(NEW_STRATEGIES)

NEW_STRATEGY_PARAMS = {
    "keltner_breakout": {"sma_window": 20, "atr_window": 14, "num_atr": 2.0},
    "momentum_acceleration": {"window": 20},
    "dual_timeframe_roc": {"short_window": 10, "long_window": 50},
}


def _patch_keltner_lows(history):
    """Per audits/2026-10-12-strategy-search-review.md finding #7: the
    class-level monkey-patch below is the same class of bug that produced
    the 0-trade leveraged-replay failure on BTC 4h keltner_breakout -- any
    caller that forgets to re-patch before using this strategy on a
    different asset/timeframe silently runs it against the wrong coin's
    lows, and it is NOT always an obvious 0-trade result (it only degrades
    that visibly when the two coins' price levels are far apart). Guard it
    with a cheap O(1) alignment check -- the strategy raises immediately if
    `series` isn't a prefix of the closes this patch was built from, instead
    of silently producing wrong signals."""
    closes = [row[1] for row in history]
    lows = [row[2] for row in history]

    def _guarded_decide(self, series, holding_position, _closes=closes, _lows=lows):
        n = len(series)
        if n > len(_closes) or series[-1] != _closes[n - 1]:
            raise RuntimeError(
                "keltner_breakout: series does not match the lows this patch was built "
                "for -- re-patch with _patch_keltner_lows(history) for the correct asset/"
                "timeframe before calling decide() again"
            )
        return self._decide_impl(series, _lows[:n], holding_position)

    KeltnerBreakoutStrategy.decide = _guarded_decide


def run_leveraged_cell(strategy_name: str, params: dict, history: list, base_config: dict, leverage_variant: str):
    if strategy_name == "keltner_breakout":
        _patch_keltner_lows(history)
    cell_config = dict(base_config)
    cell_config["strategy"] = strategy_name
    cell_config["strategy_params"] = params
    sim_config, ledger, error = leveraged_replay(f"research4_{strategy_name}", history, cell_config, leverage_variant, None)
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
            _patch_keltner_lows(history)
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


def run_heldout_confirmation():
    """Per the round-2 rule: a passing (asset, timeframe, strategy) earns a
    one-shot test on the genuinely held-out SOL/XRP/ADA set at the same
    timeframe, before anything is trusted."""
    config = load_tracks_config()
    base_config = config["crypto_momentum"]
    params = NEW_STRATEGY_PARAMS["keltner_breakout"]
    print("=== Phase 3: held-out confirmation of BTC 4h keltner_breakout on SOL/XRP/ADA 4h ===")
    for asset in ["SOL", "XRP", "ADA"]:
        history = load_saved(f"{asset}/USD", "4h")
        _patch_keltner_lows(history)
        r = run_cell("keltner_breakout", params, history, base_config)
        print(f"  {asset} 4h keltner_breakout: equity_return={r['return_pct']:.2f}% "
              f"summed_rt={r['summed_rt_return_pct']:.2f}% fees={r['fees_pct_of_capital']:.2f}% "
              f"TIM={r['time_in_market_pct']:.1f}% n_rt={r['n_round_trips']} p={r['p_value']} "
              f"-> {'PASS' if passes(r) else 'fail'}")


if __name__ == "__main__":
    main()
    run_heldout_confirmation()
