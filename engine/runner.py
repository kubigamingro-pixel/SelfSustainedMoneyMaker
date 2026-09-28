"""One tick of one track: fetch data -> risk check -> strategy -> paper broker -> ledger."""

import logging

from engine.ledger import Ledger
from engine.paper_broker import simulate_fill
from engine.risk import check_loss_cap, equity_of, passes_cost_filter
from engine.strategies.base import Action
from engine.strategies.mean_reversion import MeanReversionStrategy
from engine.strategies.momentum import MomentumStrategy

STRATEGIES = {
    "momentum": MomentumStrategy,
    "mean_reversion": MeanReversionStrategy,
}

logger = logging.getLogger(__name__)


def run_tick(track_name: str, config: dict, fetch_series_fn):
    """fetch_series_fn() -> (series: list[float], last_price: float).
    Isolated per track: exceptions are caught by the caller (scheduler)."""

    fee_pct = config["fee_pct"]
    slippage_pct = config["slippage_pct"]
    loss_cap_pct = config["loss_cap_pct"]
    position_size_pct = config["position_size_pct"]

    # config["starting_capital"] only seeds a brand-new ledger; once created, the
    # ledger's own persisted starting_capital is authoritative (see engine/ledger.py)
    # so risk math never silently drifts if the config file is edited later.
    ledger = Ledger(track_name, config["starting_capital"])
    state = ledger.get_state()
    starting_capital = state.starting_capital

    if state.stopped:
        logger.info("[%s] track is stopped (loss cap previously triggered); skipping", track_name)
        ledger.close()
        return

    series, last_price = fetch_series_fn()

    state = check_loss_cap(ledger, state, last_price, starting_capital, loss_cap_pct, fee_pct, slippage_pct)
    ledger.set_state(state)
    if state.stopped:
        logger.warning("[%s] LOSS CAP TRIGGERED at price %.6f", track_name, last_price)
        ledger.record_equity(last_price, equity_of(state, last_price))
        ledger.close()
        return

    strategy_cls = STRATEGIES[config["strategy"]]
    strategy = strategy_cls(**config.get("strategy_params", {}))
    holding_position = state.position_qty > 0
    signal = strategy.decide(series, holding_position)

    if signal.action == Action.BUY and not holding_position and last_price <= 0:
        # A zero/negative price is nonsensical to buy at, and would divide-by-zero
        # in the sizing below. Real case: a Polymarket outcome can legitimately
        # price at exactly 0 near resolution.
        logger.warning("[%s] BUY signal ignored: non-positive last_price %.6f", track_name, last_price)

    elif signal.action == Action.BUY and not holding_position and not passes_cost_filter(series, fee_pct, slippage_pct):
        # 2026-09-28 external audit consensus: several tracks were entering trades
        # whose plausible price move (recent realized range) was smaller than the
        # round-trip cost, so even a correct directional call couldn't win. Skip
        # the entry entirely rather than take a structurally negative-expectancy trade.
        logger.info("[%s] BUY signal filtered: expected move too small vs round-trip cost (%s)", track_name, signal.reason)

    elif signal.action == Action.BUY and not holding_position:
        # Size against the price we'll actually pay (post-slippage, pre-fee), then
        # fee is layered on top of that notional -- sizing off last_price directly
        # would make cost > budget by construction and the order would never fill.
        # budget is a fixed fraction of cash (position_size_pct), not all of it --
        # all-in sizing turns every strategy error into a full account-level shock.
        budget = state.cash * (position_size_pct / 100.0)
        est_fill_price = last_price * (1 + slippage_pct / 100.0)
        qty = budget / (est_fill_price * (1 + fee_pct / 100.0))
        fill = simulate_fill("BUY", last_price, qty, fee_pct, slippage_pct)
        cost = fill.price * fill.qty + fill.fee
        if cost > budget:
            # floating-point safety margin only; should not trigger given the sizing above
            qty *= budget / cost
            fill = simulate_fill("BUY", last_price, qty, fee_pct, slippage_pct)
            cost = fill.price * fill.qty + fill.fee

        if cost <= budget and fill.qty > 0:
            state.cash -= cost
            state.position_qty = fill.qty
            state.position_avg_price = fill.price
            equity = equity_of(state, last_price)
            ledger.record_trade("BUY", fill.price, fill.qty, fill.fee, state.cash, equity, signal.reason)
            logger.info("[%s] BUY %.6f @ %.6f (fee %.6f) -> equity %.4f", track_name, fill.qty, fill.price, fill.fee, equity)
        else:
            logger.warning("[%s] BUY signal fired but order could not be sized affordably (budget=%.6f, cost=%.6f)", track_name, budget, cost)

    elif signal.action == Action.SELL and holding_position:
        qty = state.position_qty
        fill = simulate_fill("SELL", last_price, qty, fee_pct, slippage_pct)
        proceeds = fill.price * fill.qty - fill.fee
        state.cash += proceeds
        state.position_qty = 0
        state.position_avg_price = 0
        equity = state.cash
        ledger.record_trade("SELL", fill.price, fill.qty, fill.fee, state.cash, equity, signal.reason)
        logger.info("[%s] SELL %.6f @ %.6f (fee %.6f) -> equity %.4f", track_name, fill.qty, fill.price, fill.fee, equity)

    elif signal.action == Action.HOLD:
        logger.info("[%s] HOLD @ %.6f (%s)", track_name, last_price, signal.reason)

    else:
        # Strategies already guard BUY-while-holding and SELL-while-not-holding,
        # so this shouldn't be reachable -- but if it ever is, log what actually
        # happened instead of silently mislabeling it as HOLD.
        logger.warning(
            "[%s] %s signal not actionable (holding_position=%s) @ %.6f (%s)",
            track_name, signal.action.value, holding_position, last_price, signal.reason,
        )

    ledger.set_state(state)
    ledger.record_equity(last_price, equity_of(state, last_price))
    ledger.close()
