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
    _force_close,
    apply_funding_cost,
    check_account_loss_cap,
    check_liquidation,
    leveraged_equity_of,
)
from engine.paper_broker import simulate_fill
from engine.risk import passes_cost_filter, passes_price_band, validate_price
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


def settle_position(ledger: Ledger, last_price: float, reason: str, fee_pct: float, slippage_pct: float, ts=None):
    """Margin-aware equivalent of engine.runner.settle_position -- see its
    docstring (2026-10-12 fix-check review, finding #1 follow-up). Reuses
    engine.leverage_risk._force_close, the same mechanics a liquidation or
    loss-cap breach already uses, so a Polymarket market resolving while a
    leveraged position is open returns margin + pnl the same way those do.

    ts (2026-10-12 simulation-review finding #2): see engine.runner.run_tick's
    matching parameter -- omitted for live use, required for a historical replay."""
    state = ledger.get_state()
    if state.position_qty <= 0:
        return state
    state = _force_close(ledger, state, last_price, fee_pct, slippage_pct, reason=reason, ts=ts)
    ledger.set_state(state)
    ledger.record_equity(last_price, leveraged_equity_of(state, last_price), ts=ts)
    ledger.commit()
    return state


def run_tick(track_name: str, config: dict, fetch_series_fn, ledger: Ledger = None, ts=None):
    """fetch_series_fn() -> (series: list[float], last_price: float, period_low: float).
    period_low is the latest closed candle's low (or just last_price where the
    data source has no real OHLC, e.g. Polymarket) -- used for intrabar
    liquidation (engine/leverage_risk.py, finding #12).

    ledger and ts (2026-10-12, extending the backtest harness to leveraged
    tracks -- see engine/runner.py's matching parameters and docstring for the
    full rationale): omitted for live tracks, which behave exactly as before.
    backtest.py passes these for a replay. ts also stands in for "now" in the
    funding-cost elapsed-time calculation below -- using the real wall clock
    there during a historical replay would compute a funding charge against
    "hours since the historical tick, measured from right now," which is
    nonsense (could be weeks), instead of the simulated elapsed time between
    historical ticks.

    Isolated per track: exceptions are caught by the caller (scheduler)."""

    fee_pct = config["fee_pct"]
    slippage_pct = config["slippage_pct"]
    loss_cap_pct = config["loss_cap_pct"]
    position_size_pct = config["position_size_pct"]
    leverage = config["leverage"]
    funding_rate_annual_pct = config["funding_rate_annual_pct"]
    price_band = config.get("price_band")

    owns_ledger = ledger is None
    if owns_ledger:
        ledger = Ledger(track_name, config["starting_capital"])
    state = ledger.get_state()
    starting_capital = state.starting_capital

    if state.stopped:
        logger.info("[%s] track is stopped (loss cap previously triggered); skipping", track_name)
        if owns_ledger:
            ledger.close()
        return

    series, last_price, period_low = fetch_series_fn()

    if not validate_price(last_price):
        logger.warning("[%s] tick skipped: invalid last_price %r", track_name, last_price)
        ledger.record_decision(last_price if isinstance(last_price, (int, float)) else None, "SKIP", "invalid last_price", ts=ts)
        ledger.commit()
        if owns_ledger:
            ledger.close()
        return

    now = ts if ts is not None else datetime.now(timezone.utc)
    if isinstance(now, str):
        now = datetime.fromisoformat(now)

    if state.position_qty > 0:
        curve = ledger.equity_curve(epoch=state.epoch)
        if curve:
            last_ts = datetime.fromisoformat(curve[-1][0])
            hours_elapsed = (now - last_ts).total_seconds() / 3600.0
        else:
            hours_elapsed = 0.0
        state = apply_funding_cost(state, last_price, hours_elapsed, funding_rate_annual_pct)

    state = check_liquidation(ledger, state, last_price, fee_pct, slippage_pct, period_low, ts=ts)
    state = check_account_loss_cap(ledger, state, last_price, starting_capital, loss_cap_pct, fee_pct, slippage_pct, ts=ts)
    ledger.set_state(state)
    if state.stopped:
        # Reserved for a future genuine permanent-stop path -- check_account_loss_cap
        # no longer sets this itself (2026-10-12 audit finding #6: a research harness
        # measuring edge shouldn't end a track's whole sample on its first gapped
        # liquidation; see LOSS_CAP_BREACHED in engine/leverage_risk.py instead).
        logger.warning("[%s] track stopped at price %.6f", track_name, last_price)
        ledger.record_equity(last_price, leveraged_equity_of(state, last_price), ts=ts)
        ledger.record_decision(last_price, "STOP", "track stopped", ts=ts)
        ledger.commit()
        if owns_ledger:
            ledger.close()
        return

    strategy_cls = STRATEGIES[config["strategy"]]
    strategy = strategy_cls(**config.get("strategy_params", {}))
    holding_position = state.position_qty > 0
    signal = strategy.decide(series, holding_position)

    if signal.action == Action.BUY and not holding_position and last_price <= 0:
        logger.warning("[%s] BUY signal ignored: non-positive last_price %.6f", track_name, last_price)
        ledger.record_decision(last_price, "HOLD", "BUY ignored: non-positive price", ts=ts)

    elif signal.action == Action.BUY and not holding_position and not passes_price_band(last_price, price_band):
        # 2026-10-12 fix-check review (findings #2/#3) -- see engine/runner.py's
        # matching comment.
        logger.info("[%s] BUY signal filtered: price %.6f outside price_band %s", track_name, last_price, price_band)
        ledger.record_decision(last_price, "HOLD", f"BUY filtered by price_band {price_band}", ts=ts)

    elif signal.action == Action.BUY and not holding_position and not passes_cost_filter(series, fee_pct, slippage_pct):
        logger.info("[%s] BUY signal filtered: expected move too small vs round-trip cost (%s)", track_name, signal.reason)
        ledger.record_decision(last_price, "HOLD", f"BUY filtered by cost filter: {signal.reason}", cost_filter_pass=False, ts=ts)

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
            # 2026-10-12 fix-check review, finding #12 edge case: remember which
            # period's low was already "live" at entry, so check_liquidation can
            # skip it if a later tick lands in that same still-open period.
            state.position_entry_period_low = period_low
            equity = leveraged_equity_of(state, last_price)
            ledger.record_trade("BUY", fill.price, fill.qty, fee_paid, state.cash, equity, signal.reason, ts=ts)
            ledger.record_decision(last_price, "BUY", signal.reason, cost_filter_pass=True, ts=ts)
            logger.info(
                "[%s] BUY %.6f @ %.6f (%gx, margin %.4f, fee %.6f) -> equity %.4f",
                track_name, fill.qty, fill.price, leverage, margin_budget, fee_paid, equity,
            )
        else:
            logger.warning("[%s] BUY signal fired but order could not be sized affordably", track_name)
            ledger.record_decision(last_price, "HOLD", "BUY signal fired but unaffordable", cost_filter_pass=True, ts=ts)

    elif signal.action == Action.SELL and holding_position:
        qty = state.position_qty
        fill = simulate_fill("SELL", last_price, qty, fee_pct, slippage_pct)
        pnl = (fill.price - state.position_avg_price) * qty
        recovered = max(0.0, state.margin_committed + pnl - fill.fee)
        state.cash += recovered
        state.position_qty = 0
        state.position_avg_price = 0
        state.margin_committed = 0
        state.position_entry_period_low = None
        equity = state.cash
        ledger.record_trade("SELL", fill.price, qty, fill.fee, state.cash, equity, signal.reason, ts=ts)
        ledger.record_decision(last_price, "SELL", signal.reason, ts=ts)
        logger.info("[%s] SELL %.6f @ %.6f (fee %.6f) -> equity %.4f", track_name, qty, fill.price, fill.fee, equity)

    elif signal.action == Action.HOLD:
        logger.info("[%s] HOLD @ %.6f (%s)", track_name, last_price, signal.reason)
        ledger.record_decision(last_price, "HOLD", signal.reason, ts=ts)

    else:
        logger.warning(
            "[%s] %s signal not actionable (holding_position=%s) @ %.6f (%s)",
            track_name, signal.action.value, holding_position, last_price, signal.reason,
        )
        ledger.record_decision(last_price, "HOLD", f"non-actionable {signal.action.value}: {signal.reason}", ts=ts)

    ledger.set_state(state)
    ledger.record_equity(last_price, leveraged_equity_of(state, last_price), ts=ts)
    ledger.commit()
    if owns_ledger:
        ledger.close()
