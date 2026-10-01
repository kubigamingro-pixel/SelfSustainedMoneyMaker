"""Runs all configured tracks. Each track's errors are isolated so one failing
data source doesn't take down the others.

In production this is driven by .github/workflows/tick.yml on a schedule.
--once and --loop below are for local development/testing.

Usage:
    python scheduler.py --once      # single tick of all tracks
    python scheduler.py --loop      # long-lived process, sleeps between ticks per-track
"""

import argparse
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from engine.config import load_tracks_config
from tracks import (
    crypto_mean_reversion,
    crypto_mean_reversion_2x,
    crypto_mean_reversion_10x,
    crypto_momentum,
    crypto_momentum_2x,
    crypto_momentum_10x,
    prediction_markets_momentum,
    prediction_markets_momentum_2x,
    prediction_markets_momentum_10x,
    stocks_momentum,
    stocks_momentum_2x,
    stocks_momentum_10x,
)

LOG_DIR = Path(__file__).resolve().parent / "logs"

TRACK_MODULES = {
    "crypto_momentum": crypto_momentum,
    "crypto_mean_reversion": crypto_mean_reversion,
    "stocks_momentum": stocks_momentum,
    "prediction_markets_momentum": prediction_markets_momentum,
    "crypto_momentum_2x": crypto_momentum_2x,
    "crypto_momentum_10x": crypto_momentum_10x,
    "crypto_mean_reversion_2x": crypto_mean_reversion_2x,
    "crypto_mean_reversion_10x": crypto_mean_reversion_10x,
    "stocks_momentum_2x": stocks_momentum_2x,
    "stocks_momentum_10x": stocks_momentum_10x,
    "prediction_markets_momentum_2x": prediction_markets_momentum_2x,
    "prediction_markets_momentum_10x": prediction_markets_momentum_10x,
}


def setup_logging():
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_DIR / "scheduler.log"),
            logging.StreamHandler(),
        ],
    )


def run_all_once(config: dict, logger: logging.Logger):
    logger.info("=== tick start %s ===", datetime.now(timezone.utc).isoformat())
    for track_name, module in TRACK_MODULES.items():
        track_config = config.get(track_name)
        if not track_config:
            logger.warning("no config for track %s, skipping", track_name)
            continue
        try:
            module.run(track_config)
        except Exception:
            logger.exception("[%s] tick failed, isolated from other tracks", track_name)
    logger.info("=== tick end ===")


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="run a single tick of all tracks and exit")
    mode.add_argument("--loop", action="store_true", help="run continuously, sleeping between ticks")
    parser.add_argument("--interval-minutes", type=float, default=None, help="override poll interval for --loop (minutes)")
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger("scheduler")

    if args.once:
        run_all_once(load_tracks_config(), logger)
        return

    interval = args.interval_minutes
    while True:
        config = load_tracks_config()
        run_all_once(config, logger)
        if interval is None:
            sleep_minutes = min(c["poll_minutes"] for c in config.values())
        else:
            sleep_minutes = interval
        logger.info("sleeping %.1f minutes until next tick", sleep_minutes)
        time.sleep(sleep_minutes * 60)


if __name__ == "__main__":
    main()
