import logging

from engine.data.stocks_source import fetch_series, market_is_open
from engine.leveraged_runner import run_tick

TRACK_NAME = "stocks_momentum_regime_2x"
logger = logging.getLogger(__name__)


def run(config: dict):
    if not market_is_open():
        logger.info("[%s] market closed; no-op tick", TRACK_NAME)
        return

    def fetch():
        return fetch_series(config["symbol"])

    run_tick(TRACK_NAME, config, fetch)
