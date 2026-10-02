"""SMA-crossover trend-following, same as momentum.py, but gated by a regime
guard before entering: only buy a crossover if the slow SMA itself is rising,
i.e. the broader trend already supports the direction, not just a momentary
blip. Exit is left unconditional (same as the plain momentum strategy) --
filters apply to entries only, consistent with engine/risk.py's cost filter.

Pattern borrowed from freqtrade's default sample strategy (MIT licensed,
https://github.com/freqtrade/freqtrade), which combines its entry signal with
independent "guard" conditions (e.g. "tema is raising") rather than acting on
a single indicator crossing a threshold in isolation. Adapted here to our SMA
crossover rather than copied, since freqtrade's guards are RSI/Bollinger-Band
based and we don't track those indicators.

This exists as a fully separate strategy class, not a modification of
MomentumStrategy -- so the original momentum tracks are completely unaffected
and this can be compared against them head-to-head on identical data.
"""

from engine.strategies.base import Action, Signal, Strategy
from engine.strategies.momentum import _sma


class MomentumRegimeFilteredStrategy(Strategy):
    ALLOWED_PARAMS = {"fast_window", "slow_window"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            raise ValueError(f"unexpected strategy_params for momentum_regime: {sorted(unexpected)}")
        fast_window = self.params.get("fast_window", 5)
        slow_window = self.params.get("slow_window", 20)
        if fast_window <= 0 or slow_window <= 0:
            raise ValueError(f"fast_window and slow_window must be positive, got fast={fast_window} slow={slow_window}")
        if fast_window >= slow_window:
            raise ValueError(f"fast_window ({fast_window}) must be less than slow_window ({slow_window})")

    def decide(self, series: list, holding_position: bool) -> Signal:
        fast_window = self.params.get("fast_window", 5)
        slow_window = self.params.get("slow_window", 20)

        if len(series) < slow_window + 1:
            return Signal(Action.HOLD, f"insufficient history ({len(series)}/{slow_window})")

        fast_now = _sma(series, fast_window)
        slow_now = _sma(series, slow_window)
        fast_prev = _sma(series[:-1], fast_window)
        slow_prev = _sma(series[:-1], slow_window)

        crossed_up = fast_prev <= slow_prev and fast_now > slow_now
        crossed_down = fast_prev >= slow_prev and fast_now < slow_now
        slow_rising = slow_now > slow_prev  # regime guard: broader trend actually rising

        if crossed_up and not holding_position and slow_rising:
            return Signal(
                Action.BUY,
                f"fast SMA({fast_window})={fast_now:.6f} crossed above slow SMA({slow_window})={slow_now:.6f}, "
                f"regime guard OK (slow SMA rising {slow_prev:.6f} -> {slow_now:.6f})",
            )
        if crossed_up and not holding_position and not slow_rising:
            return Signal(
                Action.HOLD,
                f"crossover fired but regime guard failed: slow SMA not rising ({slow_prev:.6f} -> {slow_now:.6f})",
            )
        if crossed_down and holding_position:
            # exit stays unconditional, same as the plain momentum strategy
            return Signal(Action.SELL, f"fast SMA({fast_window})={fast_now:.6f} crossed below slow SMA({slow_window})={slow_now:.6f}")
        return Signal(Action.HOLD, "no crossover")
