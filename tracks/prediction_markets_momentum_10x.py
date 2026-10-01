import json
from pathlib import Path

from engine.data.prediction_source import fetch_series
from engine.leveraged_runner import run_tick

TRACK_NAME = "prediction_markets_momentum_10x"
CONDITION_STATE = Path(__file__).resolve().parent.parent / "data_store" / f"{TRACK_NAME}_condition.json"


def _load_condition_id():
    if CONDITION_STATE.exists():
        return json.loads(CONDITION_STATE.read_text()).get("condition_id")
    return None


def _save_condition_id(condition_id: str):
    CONDITION_STATE.parent.mkdir(exist_ok=True)
    CONDITION_STATE.write_text(json.dumps({"condition_id": condition_id}))


def run(config: dict):
    condition_id = config.get("symbol") or _load_condition_id()

    def fetch():
        series, last_price, resolved_id, _token_id = fetch_series(TRACK_NAME, condition_id=condition_id)
        _save_condition_id(resolved_id)
        return series, last_price

    run_tick(TRACK_NAME, config, fetch)
