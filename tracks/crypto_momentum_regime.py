from engine.data.crypto_source import fetch_series
from engine.runner import run_tick

TRACK_NAME = "crypto_momentum_regime"


def run(config: dict):
    def fetch():
        return fetch_series(config["symbol"], exchange_id=config.get("exchange", "kraken"))

    run_tick(TRACK_NAME, config, fetch)
