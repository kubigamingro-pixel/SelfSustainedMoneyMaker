"""Shared risk controls applied on every tick, regardless of strategy or domain."""

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
    """Recent realized range as a % of the window's starting value -- a simple,
    cheap proxy for 'how much does this thing typically move,' used to gate entries
    where the round-trip cost is likely to exceed any plausible edge."""
    tail = series[-window:]
    if len(tail) < 2 or tail[0] <= 0:
        return 0.0
    return (max(tail) - min(tail)) / tail[0] * 100.0


def passes_cost_filter(series: list, fee_pct: float, slippage_pct: float) -> bool:
    """True if the recent expected move clears MIN_EXPECTED_MOVE_MULTIPLIER times
    the round-trip cost (both legs: 2 x (fee + slippage))."""
    round_trip_cost_pct = 2 * (fee_pct + slippage_pct)
    return expected_move_pct(series) >= MIN_EXPECTED_MOVE_MULTIPLIER * round_trip_cost_pct


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
