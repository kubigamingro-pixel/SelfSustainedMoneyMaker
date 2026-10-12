"""Prediction-market data via Polymarket's public Gamma API. No API key required
for reading market data. We track a single market's implied YES probability over
time as the "price" series the momentum strategy operates on.

Gamma's markets endpoint only exposes the *current* price, not a rolling history,
so unlike the crypto/stock sources we build the series ourselves by persisting
each observed price locally and appending to it on every call. Momentum decisions
will be flat until enough real ticks have accumulated -- that's an accurate
reflection of not having history, not a bug to paper over.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
HISTORY_DIR = Path(__file__).resolve().parent.parent.parent / "data_store"

# 2026-10-12 audit finding #2: "highest volume active market" with no other
# filter tends to pick markets that are in-play or near resolution -- a 5/20 SMA
# on 15-minute samples of a market swinging 0.57 -> 0.0005 in a few hours is
# measuring a news jump, not momentum, and a BUY near 0.97 has almost no room
# to move before hitting the cap vs. a modelled round-trip cost. These bounds
# are deliberately loose/illustrative, not fitted to this dataset.
MIN_DAYS_TO_RESOLUTION = 2
PRICE_BAND = (0.10, 0.90)
_ELIGIBILITY_CANDIDATE_LIMIT = 50

logger = logging.getLogger(__name__)


def _extract_price(market: dict):
    prices = market.get("outcomePrices")
    if isinstance(prices, str):
        prices = json.loads(prices)
    if not prices:
        return None
    return float(prices[0])


def _extract_token_id(market: dict):
    token_ids = market.get("clobTokenIds")
    if isinstance(token_ids, str):
        try:
            token_ids = json.loads(token_ids)
        except (json.JSONDecodeError, TypeError):
            token_ids = None
    if token_ids:
        return token_ids[0]
    return None


def pick_active_market() -> str:
    """Pick the highest-volume active, non-closed market that also passes a
    basic eligibility filter (price inside PRICE_BAND, at least
    MIN_DAYS_TO_RESOLUTION away from its end date), so entries aren't taken on
    markets that are effectively already decided."""
    resp = requests.get(
        GAMMA_MARKETS_URL,
        params={"active": "true", "closed": "false", "order": "volume", "ascending": "false", "limit": _ELIGIBILITY_CANDIDATE_LIMIT},
        timeout=15,
    )
    resp.raise_for_status()
    markets = resp.json()
    if not markets:
        raise RuntimeError("No active Polymarket markets returned")

    now = datetime.now(timezone.utc)
    for market in markets:
        try:
            price = _extract_price(market)
            if price is None or not (PRICE_BAND[0] <= price <= PRICE_BAND[1]):
                continue
            # 2026-10-12 fix-check review (finding #2): a market with no `endDate`
            # used to skip this check entirely and pass -- the opposite of what
            # the check is for. No end date means no basis to confirm it's not
            # about to resolve, so treat "missing" the same as "fails," not as
            # "exempt."
            end_date_str = market.get("endDate")
            if not end_date_str:
                continue
            end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            if (end_date - now).days < MIN_DAYS_TO_RESOLUTION:
                continue
            return market["conditionId"]
        except (TypeError, ValueError, KeyError):
            continue

    raise RuntimeError(
        f"No active Polymarket market passed the eligibility filter "
        f"(price in {PRICE_BAND}, >= {MIN_DAYS_TO_RESOLUTION}d to resolution) "
        f"among the top {_ELIGIBILITY_CANDIDATE_LIMIT} by volume"
    )


def _history_path(track_name: str) -> Path:
    HISTORY_DIR.mkdir(exist_ok=True)
    return HISTORY_DIR / f"{track_name}_price_history.json"


def _load_history(track_name: str) -> list:
    path = _history_path(track_name)
    if path.exists():
        return json.loads(path.read_text())
    return []


def _append_history(track_name: str, price: float, history_len: int) -> list:
    path = _history_path(track_name)
    history = _load_history(track_name)
    history.append(price)
    history = history[-history_len:]
    path.write_text(json.dumps(history))
    if len(history) >= 5 and len(set(history[-5:])) == 1:
        # Likely a stale quote (no recent trades) rather than a genuinely flat
        # price -- logged only (2026-10-12 audit finding #14); we still trade
        # on it rather than guess, since a real resolved/no-trade state isn't
        # reliably distinguishable from this signal alone.
        logger.warning("[%s] price has been flat at %.6f for >=5 ticks; may be a stale quote", track_name, price)
    return history


def fetch_series(track_name: str, condition_id: str = None, history_len: int = 60, holding: bool = False, include_low: bool = False):
    """Returns (series, last_price, condition_id, token_id, market_closed), or
    with include_low=True, (..., market_closed, period_low).

    market_closed (2026-10-12 fix-check review, finding #1 follow-up) is True
    exactly when the Gamma API has confirmedly marked the *currently held or
    tracked* market `closed`. The first version of the finding #1 fix stopped
    mis-valuing a held position against a different market, but never actually
    settled it -- a YES-resolved position sitting at ~1.0 had no reason to ever
    sell (no strategy crossover fires against a flat, resolved price series),
    so the track would freeze on a dead market forever. market_closed lets the
    caller (the track wrapper) force a settlement sell instead of waiting for
    a strategy signal that will never come. It is NOT set when the market is
    merely unreachable (ambiguous -- might be a transient API hiccup, not a
    real resolution) -- only on a confirmed `closed: true` response.

    include_low exists only so the leveraged Polymarket tracks can share the
    same fetch_series_fn() contract as crypto/stocks (engine/leveraged_runner.py,
    finding #12). There is no real intrabar low here -- Gamma exposes only the
    current price, not OHLC -- so period_low is always just last_price, which
    makes the intrabar liquidation check a no-op for Polymarket (consistent
    with README's existing note that leveraged Polymarket is a hypothetical
    overlay, not a real product).

    condition_id is resolved once and should be persisted by the caller (e.g. in
    tracks.yaml or a state file) to keep tracking the same market across ticks
    -- unless that market has since closed/resolved, in which case a new active
    market is picked and the price history is reset (a resolved market's implied
    probability isn't comparable to a freshly-picked one).

    holding must be True whenever the caller currently holds an open position on
    this market. 2026-10-12 audit finding #1: a closed-market rollover used to
    switch to a brand-new market and the caller would keep valuing/liquidating
    the *old* position at the *new* market's unrelated price (confirmed on the
    2026-10-03/04 incident -- a position bought at 0.9698 on a market that
    resolved near 0.9995 got marked at 0.725, then 0.10, on two other markets).
    So: never rotate away from the held market. Gamma keeps serving a resolved
    market's price for a while after close, so keep reading it; only if it
    becomes completely unreachable do we fall back to holding the last known
    price flat, rather than invent a number."""
    history, last_price, resolved_id, token_id, market_closed = _fetch_series_core(track_name, condition_id, history_len, holding)
    if include_low:
        return history, last_price, resolved_id, token_id, market_closed, last_price
    return history, last_price, resolved_id, token_id, market_closed


def _fetch_series_core(track_name: str, condition_id: str, history_len: int, holding: bool):
    """The actual implementation -- see fetch_series."""
    if not condition_id:
        condition_id = pick_active_market()

    resp = requests.get(GAMMA_MARKETS_URL, params={"condition_ids": condition_id}, timeout=15)
    resp.raise_for_status()
    markets = resp.json()
    market = markets[0] if markets else None

    if market is None or market.get("closed"):
        if holding:
            if market is not None:
                price = _extract_price(market)
                if price is not None:
                    history = _append_history(track_name, price, history_len)
                    # market is not None here, and we're in the `market.get("closed")`
                    # branch (market being None is handled separately below), so this
                    # is a confirmed resolution, not ambiguity -- market_closed=True.
                    return history, price, condition_id, _extract_token_id(market), True
            # market is None: unreachable, not confirmed closed. Ambiguous -- don't
            # claim a settlement that might just be a transient API problem.
            history = _load_history(track_name)
            if not history:
                raise RuntimeError(
                    f"Held market {condition_id} is closed/unreachable and has no "
                    f"local price history to fall back on"
                )
            logger.warning(
                "[%s] held market %s is closed/unreachable; holding last known price %.6f flat",
                track_name, condition_id, history[-1],
            )
            return history, history[-1], condition_id, None, False

        condition_id = pick_active_market()
        resp = requests.get(GAMMA_MARKETS_URL, params={"condition_ids": condition_id}, timeout=15)
        resp.raise_for_status()
        markets = resp.json()
        if not markets:
            raise RuntimeError(f"Newly picked market {condition_id} not found")
        market = markets[0]
        _history_path(track_name).unlink(missing_ok=True)

    last_price = _extract_price(market)
    if last_price is None:
        raise RuntimeError(f"Market {condition_id} has no outcomePrices in API response")

    # the CLOB per-outcome token id, for order-book lookups (engine/data/orderbook_logger.py).
    # Best-effort: absence shouldn't break price tracking, which is the critical path.
    token_id = _extract_token_id(market)

    history = _append_history(track_name, last_price, history_len)

    return history, last_price, condition_id, token_id, False
