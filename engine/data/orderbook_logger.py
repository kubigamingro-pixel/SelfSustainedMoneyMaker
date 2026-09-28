"""Best-effort logging of real Polymarket order-book depth (actual bid/ask,
not an assumption). First step toward validating the flat 2% fee + 0.5%
slippage cost model used by prediction_markets_momentum -- the 2026-09-28
external strategy audit's #1 cross-model recommendation was to replace that
assumption with real market data before trusting any conclusion drawn from it.

This module deliberately never raises: it's a side observation for future
cost-model research, not part of the trading decision. A failure here must
never interrupt or affect a tick's actual trading logic.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

CLOB_BOOK_URL = "https://clob.polymarket.com/book"
LOG_DIR = Path(__file__).resolve().parent.parent.parent / "data_store"

logger = logging.getLogger(__name__)


def _log_path(track_name: str) -> Path:
    LOG_DIR.mkdir(exist_ok=True)
    return LOG_DIR / f"{track_name}_orderbook_log.jsonl"


def log_order_book(track_name: str, token_id: str, mark_price: float):
    """Fetches the real order book for token_id and appends one JSON line with
    best bid, best ask, spread, and how that spread compares to the flat 0.5%
    slippage assumption. Swallows all errors -- see module docstring."""
    if not token_id:
        return
    try:
        resp = requests.get(CLOB_BOOK_URL, params={"token_id": token_id}, timeout=10)
        resp.raise_for_status()
        book = resp.json()

        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not bids or not asks:
            return

        best_bid = max(float(b["price"]) for b in bids)
        best_ask = min(float(a["price"]) for a in asks)
        if best_bid <= 0 or best_ask <= 0:
            return

        mid = (best_bid + best_ask) / 2
        spread_pct = (best_ask - best_bid) / mid * 100 if mid > 0 else float("nan")

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "token_id": token_id,
            "mark_price": mark_price,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread_pct": spread_pct,
        }
        with open(_log_path(track_name), "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        logger.warning("[%s] order-book logging failed (non-fatal, cost-model research only)", track_name, exc_info=True)
