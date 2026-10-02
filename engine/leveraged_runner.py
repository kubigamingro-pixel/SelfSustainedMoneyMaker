"""One tick of one leveraged track. Mirrors engine/runner.py's flow (fetch ->
risk checks -> strategy -> broker -> ledger) but with margin/liquidation/funding
accounting instead of spot accounting. Long-only, same as the rest of the system.

Deliberately NOT a modification of engine/runner.py: keeping this fully separate
means the proven spot-trading path (used by the original 4 tracks) is never
touched by leverage mechanics and carries zero risk of regression.
"""

import logging
from datetime import datetime, timezone

from engine.ledger import Ledger
from engine.leverage_risk import (
    apply_funding_cost,
    check_account_loss_cap,
    check_liquidation,
    leveraged_equity_of,
)
from engine.paper_broker import simulate_fill
from engine.risk import passes_cost_filter
from engine.strategies.base import Action
from engine.strategies.mean_reversion import MeanReversionStrategy
from engine.strategies.momentum import MomentumStrategy
from engine.strategies.momentum_regime_filtered import MomentumRegimeFilteredStrategy

STRATEGIES = {
    "momentum": MomentumStrategy,
    "mean_reversion": MeanReversionStrategy,
    "momentum_regime": MomentumRegimeFilteredStrategy,
}

logger = logging.getLogger(__name__)


def run_tick(track_name: str, config: dict, fetch_series_fn):
    """fetch_series_fn() -> (series: list[float], last_price: float).
    Isolated per track: exceptions are caught by the caller (scheduler)."""

    fee_pct = config["fee_pct"]
    slippage_pct = config["slippage_pct"]
    loss_cap_pct = config["loss_cap_pct"]
    position_size_pct = config["position_size_pct"]
    leverage = config["leverage"]
    funding_rate_annual_pct = config["funding_rate_annual_pct"]

    ledger = Ledger(track_name, config["starting_capital"])
    state = ledger.get_state()
    starting_capital = state.starting_capital

    if state.stopped:
        logger.info("[%s] track is stopped (loss cap previously triggered); skipping", track_name)
        ledger.close()
        return

    series, last_price = fetch_series_fn()

    if state.position_qty > 0:
        curve = ledger.equity_curve()
        if curve:
            last_ts = datetime.fromisoformat(curve[-1][0])
            hours_elapsed = (datetime.now(timezone.utc) - last_ts).total_seconds() / 3600.0
        else:
            hours_elapsed = 0.0
        state = apply_funding_cost(state, last_price, hours_elapsed, funding_rate_annual_pct)

    state = check_liquidation(ledger, state, last_price, fee_pct, slippage_pct)
    state = check_account_loss_cap(ledger, state, last_price, starting_capital, loss_cap_pct, fee_pct, slippage_pct)
    ledger.set_state(state)
    if state.stopped:
        logger.warning("[%s] LOSS CAP TRIGGERED at price %.6f", track_name, last_price)
        ledger.record_equity(last_price, leveraged_equity_of(state, last_price))
        ledger.close()
        return

    strategy_cls = STRATEGIES[config["strategy"]]
    strategy = strategy_cls(**config.get("strategy_params", {}))
    holding_position = state.position_qty > 0
    signal = strategy.decide(series, holding_position)

    if signal.action == Action.BUY and not holding_position and last_price <= 0:
        logger.warning("[%s] BUY signal ignored: non-positive last_price %.6f", track_name, last_price)

    elif signal.action == Action.BUY and not holding_position and not passes_cost_filter(series, fee_pct, slippage_pct):
        logger.info("[%s] BUY signal filtered: expected move too small vs round-trip cost (%s)", track_name, signal.reason)

    elif signal.action == Action.BUY and not holding_position:
        # margin_budget is the trader's own capital at risk (position_size_pct of
        # free cash, same sizing discipline as the spot tracks); leverage multiplies
        # that into the actual notional controlled.
        margin_budget = state.cash * (position_size_pct / 100.0)
        notional_budget = margin_budget * leverage
        est_fill_price = last_price * (1 + slippage_pct / 100.0)
        qty = notional_budget / (est_fill_price * (1 + fee_pct / 100.0))
        fill = simulate_fill("BUY", last_price, qty, fee_pct, slippage_pct)
        cost = fill.price * fill.qty + fill.fee
        if cost > notional_budget:
            qty *= notional_budget / cost
            fill = simulate_fill("BUY", last_price, qty, fee_pct, slippage_pct)
            cost = fill.price * fill.qty + fill.fee

        fee_paid = fill.fee
        if margin_budget + fee_paid <= state.cash and fill.qty > 0:
            state.cash -= (margin_budget + fee_paid)
            state.margin_committed = margin_budget
            state.position_qty = fill.qty
            state.position_avg_price = fill.price
            equity = leveraged_equity_of(state, last_price)
            ledger.record_trade("BUY", fill.price, fill.qty, fee_paid, state.cash, equity, signal.reason)
            logger.info(
                "[%s] BUY %.6f @ %.6f (%gx, margin %.4f, fee %.6f) -> equity %.4f",
                track_name, fill.qty, fill.price, leverage, margin_budget, fee_paid, equity,
            )
        else:
            logger.warning("[%s] BUY signal fired but order could not be sized affordably", track_name)

    elif signal.action == Action.SELL and holding_position:
        qty = state.position_qty
        fill = simulate_fill("SELL", last_price, qty, fee_pct, slippage_pct)
        pnl = (fill.price - state.position_avg_price) * qty
        recovered = max(0.0, state.margin_committed + pnl - fill.fee)
        state.cash += recovered
        state.position_qty = 0
        state.position_avg_price = 0
        state.margin_committed = 0
        equity = state.cash
        ledger.record_trade("SELL", fill.price, qty, fill.fee, state.cash, equity, signal.reason)
        logger.info("[%s] SELL %.6f @ %.6f (fee %.6f) -> equity %.4f", track_name, qty, fill.price, fill.fee, equity)

    elif signal.action == Action.HOLD:
        logger.info("[%s] HOLD @ %.6f (%s)", track_name, last_price, signal.reason)

    else:
        logger.warning(
            "[%s] %s signal not actionable (holding_position=%s) @ %.6f (%s)",
            track_name, signal.action.value, holding_position, last_price, signal.reason,
        )

    ledger.set_state(state)
    ledger.record_equity(last_price, leveraged_equity_of(state, last_price))
    ledger.close()
