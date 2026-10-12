import json
import logging
from pathlib import Path

from engine.data.prediction_source import fetch_series
from engine.ledger import Ledger
from engine.leveraged_runner import run_tick, settle_position

TRACK_NAME = "prediction_markets_momentum_regime_2x"
CONDITION_STATE = Path(__file__).resolve().parent.parent / "data_store" / f"{TRACK_NAME}_condition.json"
logger = logging.getLogger(__name__)


def _load_condition_id():
    if CONDITION_STATE.exists():
        return json.loads(CONDITION_STATE.read_text()).get("condition_id")
    return None


def _save_condition_id(condition_id: str):
    CONDITION_STATE.parent.mkdir(exist_ok=True)
    CONDITION_STATE.write_text(json.dumps({"condition_id": condition_id}))


def run(config: dict):
    condition_id = config.get("symbol") or _load_condition_id()

    # Peeked before fetching so a closed market can never be abandoned while a
    # position is open on it (2026-10-12 audit finding #1 -- see
    # engine/data/prediction_source.fetch_series's holding= docstring).
    ledger = Ledger(TRACK_NAME, config["starting_capital"])
    holding = ledger.get_state().position_qty > 0
    ledger.close()

    series, last_price, resolved_id, _token_id, market_closed, period_low = fetch_series(
        TRACK_NAME, condition_id=condition_id, holding=holding, include_low=True
    )
    _save_condition_id(resolved_id)

    if holding and market_closed:
        # 2026-10-12 fix-check review, finding #1 follow-up: see
        # tracks/prediction_markets_momentum.py's matching comment.
        ledger2 = Ledger(TRACK_NAME, config["starting_capital"])
        settle_position(ledger2, last_price, "MARKET_RESOLVED", config["fee_pct"], config["slippage_pct"])
        ledger2.close()
        logger.info("[%s] settled resolved market %s at %.6f", TRACK_NAME, resolved_id, last_price)
        return

    def fetch():
        return series, last_price, period_low

    run_tick(TRACK_NAME, config, fetch)
