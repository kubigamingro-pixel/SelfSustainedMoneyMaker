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
    state.position_entry_period_low = None
    ledger.record_trade(
        side="SELL", price=fill.price, qty=qty, fee=fill.fee,
        cash_after=state.cash, equity_after=state.cash, reason=reason,
    )
    return state


def liquidation_price(state: State) -> float:
    """The mark price at which this long position's remaining margin would hit
    exactly MAINTENANCE_MARGIN_PCT. Solving remaining_margin_pct(price) ==
    MAINTENANCE_MARGIN_PCT for price:
        margin_committed + (price - avg_price) * qty == margin_committed * (MAINTENANCE_MARGIN_PCT / 100)
        price == avg_price + margin_committed * (MAINTENANCE_MARGIN_PCT / 100 - 1) / qty
    (MAINTENANCE_MARGIN_PCT/100 - 1 is negative, so this is correctly below avg_price
    for a long)."""
    return state.position_avg_price + state.margin_committed * (MAINTENANCE_MARGIN_PCT / 100.0 - 1.0) / state.position_qty


def check_liquidation(
    ledger: Ledger, state: State, last_price: float, fee_pct: float, slippage_pct: float, period_low: float = None,
) -> State:
    """Force-closes the position once losses consume MAINTENANCE_MARGIN_PCT of the
    committed margin. Unlike check_account_loss_cap, this is per-position: the track
    keeps trading afterward, it just lost that one position's margin.

    2026-10-12 audit finding #12: checking only the tick's closing price let
    documented liquidations run to -147%/-728% remaining margin before the next
    close finally crossed the threshold -- a real exchange liquidates the instant
    price crosses it, not just at the next close. period_low (the low of the
    candle since the previous tick, when the data source can provide one --
    Polymarket cannot, see engine/data/prediction_source.py) lets this check catch
    an intrabar breach that recovered by the close, and fill at the liquidation
    price itself (minus slippage) rather than at whatever the close happens to be,
    which is closer to how a real liquidation engine behaves.

    Edge case fixed by a 2026-10-12 fix-check review: if two scheduler ticks
    land inside the same still-open candle (observed 8 times since 10-02), both
    ticks report the same period_low. If a position was opened on the first of
    those ticks, that low can predate the entry -- the position never actually
    experienced it, so liquidating against it would be wrong. state's
    position_entry_period_low (set by the caller at entry time) is compared
    against the current period_low: as long as they're still equal, we're still
    inside the entry's own period, so the intrabar check is skipped (the
    close-price check above still always applies)."""
    if state.position_qty <= 0 or state.margin_committed <= 0:
        return state

    pnl = unrealized_pnl(state, last_price)
    remaining_margin_pct = (state.margin_committed + pnl) / state.margin_committed * 100

    if remaining_margin_pct <= MAINTENANCE_MARGIN_PCT:
        return _force_close(
            ledger, state, last_price, fee_pct, slippage_pct,
            reason=f"LIQUIDATED remaining_margin_pct={remaining_margin_pct:.1f}%",
        )

    same_period_as_entry = (
        state.position_entry_period_low is not None and period_low is not None
        and period_low == state.position_entry_period_low
    )
    if period_low is not None and period_low < last_price and not same_period_as_entry:
        liq_price = liquidation_price(state)
        if period_low <= liq_price:
            return _force_close(
                ledger, state, liq_price, fee_pct, slippage_pct,
                reason=f"LIQUIDATED intrabar low={period_low:.6f} <= liq_price={liq_price:.6f}",
            )
    return state


def check_account_loss_cap(
    ledger: Ledger, state: State, last_price: float, starting_capital: float,
    loss_cap_pct: float, fee_pct: float, slippage_pct: float, ts=None,
) -> State:
    """Account-level backstop, using leveraged-correct equity accounting. In practice
    per-position liquidation (check_liquidation) usually triggers first, since a
    position's margin is only a fraction of total capital -- this exists for the
    case where a sequence of smaller losses across multiple trades cumulatively
    drains the account.

    Non-terminal by design (2026-10-12 audit finding #6, changed from the original
    permanent-stop version): at 50% position sizing and a 50% floor, one gapped
    liquidation (checked only at the tick price, see check_liquidation) usually
    also breaches this floor in the same tick -- so the old "stop forever" behavior
    meant a single bad gap ended a leveraged track's entire sample, which already
    forced two manual resets (see PERFORMANCE_REVIEW.md, and audits/2026-10-12).
    This research harness's job is measuring whether a strategy has edge, which
    needs ongoing samples more than it needs a real trader's capital-preservation
    guarantee.

    A breach now closes the epoch (2026-10-12 fix-check review caught a
    regression in the first version of this change): force-close any open
    position (same mechanics as a liquidation), archive the closed epoch via
    Ledger.reset_epoch(), and start a fresh one at the track's original
    starting_capital. The first version of this fix force-closed the position
    and then just... left cash below the floor, with state.stopped still never
    set -- so the very next entry, costing fees, immediately re-breached the
    same floor and was force-closed again, forever: a fee-burning churn loop
    that trade_log/scoreboard would have silently counted as real trades.
    Resetting the epoch gives the track a clean floor again, exactly like a
    1x track's loss cap gives it a fresh (but permanently stopped) one -- this
    is the leveraged, non-terminal equivalent. 1x spot tracks are unaffected --
    engine/risk.py's check_loss_cap keeps its original terminal behavior; this
    change applies to leveraged tracks only."""
    equity = leveraged_equity_of(state, last_price)
    floor = starting_capital * (loss_cap_pct / 100.0)
    if equity < floor:
        reason = f"LOSS_CAP_BREACHED equity={equity:.4f} floor={floor:.4f}"
        if state.position_qty > 0:
            state = _force_close(ledger, state, last_price, fee_pct, slippage_pct, reason=reason)
        final_equity = leveraged_equity_of(state, last_price)  # == state.cash now (position is closed)
        # 2026-10-12 fix-check (second review): reset_epoch() reads state fresh
        # from the database itself (ledger.get_state()), so without this write
        # first it archived the *pre*-force-close cash/position_qty into the
        # epochs table's final_cash/final_position_qty columns -- final_equity
        # (passed in explicitly) and the trade/equity rows were already correct,
        # only this summary row was stale.
        ledger.set_state(state)
        ledger.reset_epoch(final_equity, reason=reason, ts=ts)
        state = ledger.get_state()
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
