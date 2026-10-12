"""Shared risk controls applied on every tick, regardless of strategy or domain."""

import math

from engine.ledger import Ledger, State
from engine.paper_broker import simulate_fill

# Cost-aware entry filter (2026-09-28 external strategy audit consensus): several
# tracks were entering trades whose plausible price move was smaller than the
# round-trip cost, guaranteeing a loss even with a correct directional call. These
# are fixed engine-wide constants, not per-track config -- making them tunable
# would just invite curve-fitting the filter to this same small dataset. The
# 2x multiplier matches the audits' explicit suggestion (expected move should
# clear at least ~2x round-trip cost before a signal is worth acting on).
MIN_EXPECTED_MOVE_MULTIPLIER = 2.0
EXPECTED_MOVE_WINDOW = 20


def equity_of(state: State, last_price: float) -> float:
    return state.cash + state.position_qty * last_price


def expected_move_pct(series: list, window: int = EXPECTED_MOVE_WINDOW) -> float:
    """Recent realized range as a % of the window's typical level -- a simple,
    cheap proxy for 'how much does this thing typically move,' used to gate entries
    where the round-trip cost is likely to exceed any plausible edge.

    2026-10-12 audit finding #3: this used to divide (max-min) by tail[0] (the
    window's *first* value) with no trimming. Two separate problems, both fixed
    here:
    1. Dividing by tail[0] breaks down whenever that one sample sits near zero --
       a Polymarket contract at 0.0025 with one tick at 0.016 read as a "540%
       expected move" and sailed through the filter, which is exactly what let
       the 2026-10-02 bad entry (BUY 0.0161, back to 0.0025 next tick) pass.
       Dividing by the window's median instead is far less sensitive to any
       single near-zero sample.
    2. max()-min() is as sensitive to one outlier tick as tail[0] was -- a lone
       spike (good data or bad) still dominates. Trimming the top/bottom of
       the window instead of using the true min/max requires the move to show
       up across more than a couple of ticks before the filter treats it as
       real, which is the audit's explicit suggestion ("a robust range avoids
       passing on single-tick spikes"). A 2026-10-12 fix-check review found
       the first version of this trim (a 10th/90th percentile split) only
       ever excluded exactly 1 sample per end on a 20-sample window, so a
       2-tick spike still read through at the same ~540%. Trimming a fixed 3
       samples off each end (not a percentage, so the behavior doesn't
       quietly change if EXPECTED_MOVE_WINDOW is ever edited) handles spikes
       up to 3 ticks wide."""
    tail = sorted(series[-window:])
    n = len(tail)
    if n < 2:
        return 0.0
    median = tail[n // 2]
    if median <= 0:
        return 0.0
    trim = min(3, (n - 1) // 2)
    lo = tail[trim]
    hi = tail[n - 1 - trim]
    return (hi - lo) / median * 100.0


def validate_price(price) -> bool:
    """True if price is a finite, non-negative number. Zero is valid (a Polymarket
    outcome can legitimately price at exactly 0 near resolution); NaN, inf and
    negative values are not and would corrupt sizing/SMA math downstream.
    2026-10-12 audit finding #14: no data source previously checked this."""
    return isinstance(price, (int, float)) and not isinstance(price, bool) and math.isfinite(price) and price >= 0


def passes_cost_filter(series: list, fee_pct: float, slippage_pct: float) -> bool:
    """True if the recent expected move clears MIN_EXPECTED_MOVE_MULTIPLIER times
    the round-trip cost (both legs: 2 x (fee + slippage))."""
    round_trip_cost_pct = 2 * (fee_pct + slippage_pct)
    return expected_move_pct(series) >= MIN_EXPECTED_MOVE_MULTIPLIER * round_trip_cost_pct


def passes_price_band(last_price: float, price_band) -> bool:
    """True if price_band is unset (most tracks don't have one), or last_price
    falls inside it. 2026-10-12 fix-check review (findings #2/#3): Polymarket's
    pick_active_market() only checked the price band at market-selection time --
    a held or flat-but-not-yet-rotated market drifting to near 0 or 1 afterward
    was never re-checked. This is a generic, domain-agnostic entry gate (set via
    the optional price_band config key, engine/config.py) so it's re-checked at
    the moment a BUY actually fires, not just when the market was first picked.
    Crypto/stocks tracks simply never set price_band, so this is always a no-op
    for them."""
    if price_band is None:
        return True
    return price_band[0] <= last_price <= price_band[1]


def check_loss_cap(
    ledger: Ledger,
    state: State,
    last_price: float,
    starting_capital: float,
    loss_cap_pct: float,
    fee_pct: float = 0.0,
    slippage_pct: float = 0.0,
) -> State:
    """If equity has fallen below loss_cap_pct of starting capital, flatten the position
    and mark the track stopped. A stopped track takes no further action until reset.

    The flatten goes through the same simulate_fill path as a normal exit -- a forced
    stop-loss should not be modeled as *more* favorable (zero fee, zero slippage) than
    a planned exit; if anything a panic exit fares worse in reality."""
    if state.stopped:
        return state

    equity = equity_of(state, last_price)
    floor = starting_capital * (loss_cap_pct / 100.0)
    if equity < floor:
        if state.position_qty > 0:
            qty_flattened = state.position_qty
            fill = simulate_fill("SELL", last_price, qty_flattened, fee_pct, slippage_pct)
            proceeds = fill.price * fill.qty - fill.fee
            state.cash += proceeds
            state.position_qty = 0
            state.position_avg_price = 0
            ledger.record_trade(
                side="SELL",
                price=fill.price,
                qty=qty_flattened,
                fee=fill.fee,
                cash_after=state.cash,
                equity_after=state.cash,
                reason=f"LOSS_CAP_TRIGGERED equity={equity:.4f} floor={floor:.4f}",
            )
        state.stopped = True
    return state
