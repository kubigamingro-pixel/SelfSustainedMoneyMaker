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
from pathlib import Path

import requests

GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
HISTORY_DIR = Path(__file__).resolve().parent.parent.parent / "data_store"


def pick_active_market() -> str:
    """Pick the highest-volume active, non-closed market and return its condition id."""
    resp = requests.get(
        GAMMA_MARKETS_URL,
        params={"active": "true", "closed": "false", "order": "volume", "ascending": "false", "limit": 5},
        timeout=15,
    )
    resp.raise_for_status()
    markets = resp.json()
    if not markets:
        raise RuntimeError("No active Polymarket markets returned")
    return markets[0]["conditionId"]


def _history_path(track_name: str) -> Path:
    HISTORY_DIR.mkdir(exist_ok=True)
    return HISTORY_DIR / f"{track_name}_price_history.json"


def fetch_series(track_name: str, condition_id: str = None, history_len: int = 60):
    """Returns (series, last_price, condition_id). condition_id is resolved once
    and should be persisted by the caller (e.g. in tracks.yaml or a state file) to
    keep tracking the same market across ticks -- unless that market has since
    closed/resolved, in which case a new active market is picked and the price
    history is reset (a resolved market's implied probability isn't comparable
    to a freshly-picked one)."""
    if not condition_id:
        condition_id = pick_active_market()

    resp = requests.get(GAMMA_MARKETS_URL, params={"condition_ids": condition_id}, timeout=15)
    resp.raise_for_status()
    markets = resp.json()
    market = markets[0] if markets else None

    if market is None or market.get("closed"):
        condition_id = pick_active_market()
        resp = requests.get(GAMMA_MARKETS_URL, params={"condition_ids": condition_id}, timeout=15)
        resp.raise_for_status()
        markets = resp.json()
        if not markets:
            raise RuntimeError(f"Newly picked market {condition_id} not found")
        market = markets[0]
        _history_path(track_name).unlink(missing_ok=True)

    prices = market.get("outcomePrices")
    if isinstance(prices, str):
        prices = json.loads(prices)
    if not prices:
        raise RuntimeError(f"Market {condition_id} has no outcomePrices in API response")
    last_price = float(prices[0])

    # the CLOB per-outcome token id, for order-book lookups (engine/data/orderbook_logger.py).
    # Best-effort: absence shouldn't break price tracking, which is the critical path.
    token_id = None
    token_ids = market.get("clobTokenIds")
    if isinstance(token_ids, str):
        try:
            token_ids = json.loads(token_ids)
        except (json.JSONDecodeError, TypeError):
            token_ids = None
    if token_ids:
        token_id = token_ids[0]

    path = _history_path(track_name)
    history = []
    if path.exists():
        history = json.loads(path.read_text())
    history.append(last_price)
    history = history[-history_len:]
    path.write_text(json.dumps(history))

    return history, last_price, condition_id, token_id
