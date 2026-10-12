"""SMA-crossover trend-following: buy when the fast SMA is above the slow SMA
(and we're not already in), sell when it crosses back below."""

from engine.strategies.base import Action, Signal, Strategy


def _sma(series: list, window: int) -> float:
    tail = series[-window:]
    return sum(tail) / len(tail)


class MomentumStrategy(Strategy):
    ALLOWED_PARAMS = {"fast_window", "slow_window"}

    def __init__(self, **params):
        super().__init__(**params)
        unexpected = set(self.params) - self.ALLOWED_PARAMS
        if unexpected:
            # a typo'd key (e.g. "fastwindow") would otherwise be silently ignored
            # and the corresponding default quietly used instead.
            raise ValueError(f"unexpected strategy_params for momentum: {sorted(unexpected)}")
        fast_window = self.params.get("fast_window", 5)
        slow_window = self.params.get("slow_window", 20)
        if fast_window <= 0 or slow_window <= 0:
            raise ValueError(f"fast_window and slow_window must be positive, got fast={fast_window} slow={slow_window}")
        if fast_window >= slow_window:
            # not just a style preference: _sma(series, fast_window) is called on a
            # series only guaranteed to be >= slow_window+1 long, so a misconfigured
            # fast_window >= slow_window would silently produce meaningless SMAs
            # instead of an error.
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

        if crossed_up and not holding_position:
            return Signal(Action.BUY, f"fast SMA({fast_window})={fast_now:.6f} crossed above slow SMA({slow_window})={slow_now:.6f}")
        # 2026-10-12 audit finding #13: exit used to require the crossover to land
        # exactly on the latest bar (fast_prev >= slow_prev and fast_now < slow_now).
        # Ticks aren't perfectly reliable (observed gaps up to 147 minutes between
        # 15-minute-interval ticks), so a missed or deduplicated bar right at the
        # crossover left the position open through the entire next cycle. Exiting
        # on state (fast below slow, regardless of which bar it first crossed on)
        # behaves identically in the normal no-gap case and is robust to gaps.
        if holding_position and fast_now < slow_now:
            return Signal(Action.SELL, f"fast SMA({fast_window})={fast_now:.6f} below slow SMA({slow_window})={slow_now:.6f}")
        return Signal(Action.HOLD, "no crossover")
