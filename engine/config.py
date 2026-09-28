"""Loads and validates config/tracks.yaml. Shared by scheduler.py and
scoreboard.py so both fail loudly on a bad config instead of one of them
silently producing wrong numbers."""

from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "tracks.yaml"

REQUIRED_KEYS = ("starting_capital", "fee_pct", "slippage_pct", "loss_cap_pct", "poll_minutes", "strategy", "position_size_pct")


def _validate_track(name: str, cfg: dict):
    missing = [k for k in REQUIRED_KEYS if k not in cfg]
    if missing:
        raise ValueError(f"[{name}] missing required config key(s): {missing}")

    # imported lazily to avoid engine.config <-> engine.runner import ordering issues
    from engine.runner import STRATEGIES

    if cfg["strategy"] not in STRATEGIES:
        raise ValueError(f"[{name}] unknown strategy {cfg['strategy']!r}, must be one of {sorted(STRATEGIES)}")

    if cfg["starting_capital"] <= 0:
        raise ValueError(f"[{name}] starting_capital must be positive, got {cfg['starting_capital']}")
    if not (0 <= cfg["fee_pct"] < 100):
        # a fee >= 100% of notional would make proceeds negative on a SELL
        # (paper_broker.py: proceeds = notional - fee), driving cash negative --
        # nonsensical for a ledger that should never owe money it doesn't have.
        raise ValueError(f"[{name}] fee_pct must be in [0, 100), got {cfg['fee_pct']}")
    if not (0 <= cfg["slippage_pct"] < 100):
        raise ValueError(f"[{name}] slippage_pct must be in [0, 100), got {cfg['slippage_pct']}")
    if not (0 < cfg["loss_cap_pct"] <= 100):
        raise ValueError(f"[{name}] loss_cap_pct must be in (0, 100], got {cfg['loss_cap_pct']}")
    if cfg["poll_minutes"] <= 0:
        raise ValueError(f"[{name}] poll_minutes must be positive, got {cfg['poll_minutes']}")
    if not (0 < cfg["position_size_pct"] <= 100):
        raise ValueError(f"[{name}] position_size_pct must be in (0, 100], got {cfg['position_size_pct']}")


def load_tracks_config(path: Path = CONFIG_PATH) -> dict:
    data = yaml.safe_load(path.read_text())
    tracks = data.get("tracks") if data else None
    if not tracks:
        raise ValueError(f"{path} has no 'tracks' section")

    for name, cfg in tracks.items():
        _validate_track(name, cfg)

    return tracks
