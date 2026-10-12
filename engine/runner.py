"""One tick of one track: fetch data -> risk check -> strategy -> paper broker -> ledger."""

import logging

from engine.ledger import Ledger
from engine.paper_broker import simulate_fill
from engine.risk import check_loss_cap, equity_of, passes_cost_filter, passes_price_band, validate_price
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
    """Force-sells the entire open position outside normal strategy evaluation.
    2026-10-12 fix-check review, finding #1 follow-up: a Polymarket position
    held through its market's resolution has no strategy signal that will ever
    fire to close it (the price series goes flat at the resolution value, so a
    crossover-based exit never triggers) -- the track wrapper calls this
    directly once engine.data.prediction_source.fetch_series reports
    market_closed=True while holding. No-op if the ledger isn't actually
    holding a position. Uses the same fill mechanics as a normal exit."""
    state = ledger.get_state()
    if state.position_qty <= 0:
        return state
    qty = state.position_qty
    fill = simulate_fill("SELL", last_price, qty, fee_pct, slippage_pct)
    proceeds = fill.price * fill.qty - fill.fee
    state.cash += proceeds
    state.position_qty = 0
    state.position_avg_price = 0
    equity = state.cash
    ledger.record_trade("SELL", fill.price, fill.qty, fill.fee, state.cash, equity, reason, ts=ts)
    ledger.record_decision(last_price, "SELL", reason, ts=ts)
    ledger.set_state(state)
    ledger.record_equity(last_price, equity, ts=ts)
    ledger.commit()
    return state


def run_tick(track_name: str, config: dict, fetch_series_fn, ledger: Ledger = None, ts=None):
    """fetch_series_fn() -> (series: list[float], last_price: float).
    Isolated per track: exceptions are caught by the caller (scheduler).

    ledger and ts (2026-10-12 audit finding #5, backtest harness) are normally
    omitted -- live tracks create their own on-disk ledger and timestamp every
    row with the real wall clock, exactly as before. backtest.py instead opens
    one in-memory ledger and calls run_tick once per historical bar, passing
    that bar's own historical timestamp as ts and reusing the same ledger
    object across calls (so the whole replay never touches disk and every row
    carries a real historical date instead of "now" repeated thousands of
    times). When ledger is passed in, this function never closes it -- that's
    the caller's responsibility, since the caller will keep using it."""

    fee_pct = config["fee_pct"]
    slippage_pct = config["slippage_pct"]
    loss_cap_pct = config["loss_cap_pct"]
    position_size_pct = config["position_size_pct"]
    price_band = config.get("price_band")

    # config["starting_capital"] only seeds a brand-new ledger; once created, the
    # ledger's own persisted starting_capital is authoritative (see engine/ledger.py)
    # so risk math never silently drifts if the config file is edited later.
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

    series, last_price = fetch_series_fn()

    if not validate_price(last_price):
        # 2026-10-12 audit finding #14: no source previously checked for NaN/inf/
        # negative prices before feeding them into sizing and SMA math. Skip the
        # tick rather than trade (or divide-by-zero) on garbage data.
        logger.warning("[%s] tick skipped: invalid last_price %r", track_name, last_price)
        ledger.record_decision(last_price if isinstance(last_price, (int, float)) else None, "SKIP", "invalid last_price", ts=ts)
        ledger.commit()
        if owns_ledger:
            ledger.close()
        return

    state = check_loss_cap(ledger, state, last_price, starting_capital, loss_cap_pct, fee_pct, slippage_pct, ts=ts)
    ledger.set_state(state)
    if state.stopped:
        logger.warning("[%s] LOSS CAP TRIGGERED at price %.6f", track_name, last_price)
        ledger.record_equity(last_price, equity_of(state, last_price), ts=ts)
        ledger.record_decision(last_price, "STOP", "loss cap triggered", ts=ts)
        ledger.commit()
        if owns_ledger:
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
        ledger.record_decision(last_price, "HOLD", "BUY ignored: non-positive price", ts=ts)

    elif signal.action == Action.BUY and not holding_position and not passes_price_band(last_price, price_band):
        # 2026-10-12 fix-check review (findings #2/#3): pick_active_market()'s
        # price-band filter only applies when a market is first picked -- a
        # market that drifts outside the band afterward (or a market with no
        # endDate, which previously skipped that check entirely -- fixed
        # separately in prediction_source.py) was never re-checked at entry.
        # This re-checks it at the moment a BUY actually fires.
        logger.info("[%s] BUY signal filtered: price %.6f outside price_band %s", track_name, last_price, price_band)
        ledger.record_decision(last_price, "HOLD", f"BUY filtered by price_band {price_band}", ts=ts)

    elif signal.action == Action.BUY and not holding_position and not passes_cost_filter(series, fee_pct, slippage_pct):
        # 2026-09-28 external audit consensus: several tracks were entering trades
        # whose plausible price move (recent realized range) was smaller than the
        # round-trip cost, so even a correct directional call couldn't win. Skip
        # the entry entirely rather than take a structurally negative-expectancy trade.
        logger.info("[%s] BUY signal filtered: expected move too small vs round-trip cost (%s)", track_name, signal.reason)
        ledger.record_decision(last_price, "HOLD", f"BUY filtered by cost filter: {signal.reason}", cost_filter_pass=False, ts=ts)

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

        # 2026-10-12 simulation-v3 review: the rescale above can still leave cost
        # a single floating-point rounding step above budget (re-running
        # simulate_fill after rescaling isn't guaranteed to round the same way
        # twice), which failed `cost <= budget` by a sub-cent margin and dropped
        # an otherwise-affordable BUY as "unaffordable". Never observed live or
        # in any saved replay (0 occurrences across everything checked), but hit
        # 10/184 times in an unfiltered-cost-filter experiment -- a tiny relative
        # tolerance absorbs the rounding without meaningfully loosening the
        # actual affordability check.
        if cost <= budget * (1 + 1e-9) and fill.qty > 0:
            state.cash -= cost
            state.position_qty = fill.qty
            state.position_avg_price = fill.price
            equity = equity_of(state, last_price)
            ledger.record_trade("BUY", fill.price, fill.qty, fill.fee, state.cash, equity, signal.reason, ts=ts)
            ledger.record_decision(last_price, "BUY", signal.reason, cost_filter_pass=True, ts=ts)
            logger.info("[%s] BUY %.6f @ %.6f (fee %.6f) -> equity %.4f", track_name, fill.qty, fill.price, fill.fee, equity)
        else:
            logger.warning("[%s] BUY signal fired but order could not be sized affordably (budget=%.6f, cost=%.6f)", track_name, budget, cost)
            ledger.record_decision(last_price, "HOLD", "BUY signal fired but unaffordable", cost_filter_pass=True, ts=ts)

    elif signal.action == Action.SELL and holding_position:
        qty = state.position_qty
        fill = simulate_fill("SELL", last_price, qty, fee_pct, slippage_pct)
        proceeds = fill.price * fill.qty - fill.fee
        state.cash += proceeds
        state.position_qty = 0
        state.position_avg_price = 0
        equity = state.cash
        ledger.record_trade("SELL", fill.price, fill.qty, fill.fee, state.cash, equity, signal.reason, ts=ts)
        ledger.record_decision(last_price, "SELL", signal.reason, ts=ts)
        logger.info("[%s] SELL %.6f @ %.6f (fee %.6f) -> equity %.4f", track_name, fill.qty, fill.price, fill.fee, equity)

    elif signal.action == Action.HOLD:
        logger.info("[%s] HOLD @ %.6f (%s)", track_name, last_price, signal.reason)
        ledger.record_decision(last_price, "HOLD", signal.reason, ts=ts)

    else:
        # Strategies already guard BUY-while-holding and SELL-while-not-holding,
        # so this shouldn't be reachable -- but if it ever is, log what actually
        # happened instead of silently mislabeling it as HOLD.
        logger.warning(
            "[%s] %s signal not actionable (holding_position=%s) @ %.6f (%s)",
            track_name, signal.action.value, holding_position, last_price, signal.reason,
        )
        ledger.record_decision(last_price, "HOLD", f"non-actionable {signal.action.value}: {signal.reason}", ts=ts)

    ledger.set_state(state)
    ledger.record_equity(last_price, equity_of(state, last_price), ts=ts)
    ledger.commit()
    if owns_ledger:
        ledger.close()
