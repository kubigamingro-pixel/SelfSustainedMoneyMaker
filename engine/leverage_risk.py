"""Risk controls and accounting specific to leveraged positions: margin-based
equity, liquidation, and funding cost. Long-only, same as the rest of the system
-- leverage amplifies the existing long-only strategies, it does not add shorting.

Separate from engine/risk.py (spot accounting) by design: a leveraged position's
cash does NOT equal cash + qty*price (that would double-count the full notional
as if it were all owned outright). Keeping these as two distinct modules means
the proven spot-trading path (engine/runner.py, engine/risk.py) is never touched
by this code and carries zero risk of regression.
"""

from engine.ledger import Ledger, State
from engine.paper_broker import simulate_fill

# Engine-wide constants, not per-track config -- making these tunable per track
# would just invite curve-fitting them to whatever this dataset happens to show,
# same reasoning as MIN_EXPECTED_MOVE_MULTIPLIER in engine/risk.py.
MAINTENANCE_MARGIN_PCT = 20  # liquidate once losses consume 80% of committed margin,
                             # leaving a 20% buffer -- mirrors the shape of a real
                             # exchange's liquidation engine, not tuned to any one
                             # exchange's exact schedule.


def unrealized_pnl(state: State, last_price: float) -> float:
    if state.position_qty <= 0:
        return 0.0
    return (last_price - state.position_avg_price) * state.position_qty


def leveraged_equity_of(state: State, last_price: float) -> float:
    """Free cash (excludes locked margin) + margin currently committed + unrealized
    PnL on the open position. Equivalent to engine.risk.equity_of when there's no
    leverage (margin_committed=0, position_qty=0)."""
    return state.cash + state.margin_committed + unrealized_pnl(state, last_price)


def _force_close(ledger: Ledger, state: State, last_price: float, fee_pct: float, slippage_pct: float, reason: str) -> State:
    qty = state.position_qty
    fill = simulate_fill("SELL", last_price, qty, fee_pct, slippage_pct)
    pnl = (fill.price - state.position_avg_price) * qty
    # A forced close (liquidation or loss-cap) returns whatever margin+pnl is left,
    # never negative -- real exchanges absorb further loss via an insurance fund;
    # the trader never owes more than their margin.
    recovered = max(0.0, state.margin_committed + pnl - fill.fee)
    state.cash += recovered
    state.position_qty = 0
    state.position_avg_price = 0
    state.margin_committed = 0
    ledger.record_trade(
        side="SELL", price=fill.price, qty=qty, fee=fill.fee,
        cash_after=state.cash, equity_after=state.cash, reason=reason,
    )
    return state


def check_liquidation(ledger: Ledger, state: State, last_price: float, fee_pct: float, slippage_pct: float) -> State:
    """Force-closes the position once losses consume MAINTENANCE_MARGIN_PCT of the
    committed margin. Unlike check_account_loss_cap, this is per-position: the track
    keeps trading afterward, it just lost that one position's margin."""
    if state.position_qty <= 0 or state.margin_committed <= 0:
        return state

    pnl = unrealized_pnl(state, last_price)
    remaining_margin_pct = (state.margin_committed + pnl) / state.margin_committed * 100

    if remaining_margin_pct <= MAINTENANCE_MARGIN_PCT:
        return _force_close(
            ledger, state, last_price, fee_pct, slippage_pct,
            reason=f"LIQUIDATED remaining_margin_pct={remaining_margin_pct:.1f}%",
        )
    return state


def check_account_loss_cap(
    ledger: Ledger, state: State, last_price: float, starting_capital: float,
    loss_cap_pct: float, fee_pct: float, slippage_pct: float,
) -> State:
    """Account-level backstop (mirrors engine.risk.check_loss_cap's semantics: a
    permanent hard stop), using leveraged-correct equity accounting. In practice
    per-position liquidation usually triggers first, since a position's margin is
    only a fraction of total capital -- this exists for the case where a sequence
    of smaller losses across multiple trades cumulatively drains the account."""
    if state.stopped:
        return state

    equity = leveraged_equity_of(state, last_price)
    floor = starting_capital * (loss_cap_pct / 100.0)
    if equity < floor:
        if state.position_qty > 0:
            state = _force_close(
                ledger, state, last_price, fee_pct, slippage_pct,
                reason=f"LOSS_CAP_TRIGGERED equity={equity:.4f} floor={floor:.4f}",
            )
        state.stopped = True
    return state


def apply_funding_cost(state: State, last_price: float, hours_elapsed: float, funding_rate_annual_pct: float) -> State:
    """Simplified funding cost: notional (at current mark price) * annualized rate *
    (hours/8760), charged against cash each tick a position is open. Real perpetual-
    futures funding is periodic (often every 8h) and can flip sign (longs sometimes
    get PAID to hold); this always charges the long side -- a deliberately simple,
    clearly-labeled assumption, not a real exchange's funding curve."""
    if state.position_qty <= 0 or hours_elapsed <= 0:
        return state
    notional = state.position_qty * last_price
    cost = notional * (funding_rate_annual_pct / 100.0) * (hours_elapsed / 8760.0)
    state.cash -= cost
    return state
