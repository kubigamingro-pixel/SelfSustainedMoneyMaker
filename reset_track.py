"""Reset a track to start a new epoch, without losing its history.

2026-10-12 audit finding #10: previous resets (commits ab7b3037, 2260457f)
rewrote a track's ledger state directly, so the only record that a prior
epoch ever existed was git history and PERFORMANCE_REVIEW.md's prose. This
script instead calls Ledger.reset_epoch(), which archives the closed epoch's
summary into the ledger's own `epochs` table and keeps every trade/decision/
equity row tagged with its original epoch number -- so the pre-reset history
stays queryable from the database itself, not just from git log.

Usage:
    python reset_track.py crypto_momentum --reason "manual review reset"
    python reset_track.py prediction_markets_momentum --reason "rollover bug fix (see audits/2026-10-12)"
    python reset_track.py --all --reason "fresh start for the whole comparison"
    python reset_track.py crypto_momentum_2x --reason "..." --new-starting-capital 50

A track's final equity for the closed epoch is computed automatically: cash
if flat, or the last equity_curve row of its current epoch if it holds an
open position (2026-10-12 fix-check review, finding N1 -- the first version
of this script required --final-equity to be supplied by hand for any track
holding a position, which meant `--all` would reset the first several tracks
and then raise SystemExit at the first holding one, leaving the whole set
half-reset). Every track is validated -- resolvable, has the data needed to
compute its final equity -- *before* any track is actually reset, so a
problem with one track can't leave others already reset and the rest not.
--final-equity still exists as a manual override, for a single track only
(one number can't meaningfully override every track's equity under --all)."""

import argparse

from engine.config import load_tracks_config
from engine.ledger import Ledger


def compute_final_equity(ledger: Ledger, state) -> float:
    """Cash if flat. If holding, the last recorded equity_curve value for the
    current epoch -- the most recent live-tick mark-to-market, which is the
    best information already on hand without a fresh price fetch."""
    if state.position_qty <= 0:
        return state.cash
    curve = ledger.equity_curve(epoch=state.epoch)
    if not curve:
        raise ValueError(
            f"holds an open position ({state.position_qty} units) but has no "
            f"equity_curve row in epoch {state.epoch} to value it from -- pass "
            f"--final-equity explicitly"
        )
    return curve[-1][2]


def plan_reset(track_name: str, starting_capital: float, final_equity_override: float = None):
    """Phase 1: read-only. Opens the ledger just to compute what the reset
    would do, and closes it again -- nothing is written yet."""
    ledger = Ledger(track_name, starting_capital)
    state = ledger.get_state()
    try:
        final_equity = final_equity_override if final_equity_override is not None else compute_final_equity(ledger, state)
    finally:
        ledger.close()
    return state, final_equity


def apply_reset(track_name: str, starting_capital: float, state, final_equity: float, reason: str, new_starting_capital: float = None):
    """Phase 2: actually performs the reset (only called after every target
    track has successfully completed phase 1)."""
    ledger = Ledger(track_name, starting_capital)
    old_epoch = state.epoch
    new_epoch = ledger.reset_epoch(final_equity, reason, new_starting_capital=new_starting_capital)
    print(
        f"{track_name}: epoch {old_epoch} archived (final_equity={final_equity:.4f}, reason={reason!r}) "
        f"-> now epoch {new_epoch}, starting_capital={new_starting_capital if new_starting_capital is not None else state.starting_capital}"
    )
    ledger.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("track_name", nargs="?", help="track to reset, e.g. crypto_momentum (omit with --all)")
    parser.add_argument("--all", action="store_true", help="reset every track in config/tracks.yaml")
    parser.add_argument("--reason", required=True, help="why this track is being reset -- stored in the epochs table")
    parser.add_argument("--final-equity", type=float, default=None, help="manual override; single-track only, not valid with --all")
    parser.add_argument("--new-starting-capital", type=float, default=None, help="change starting capital for the new epoch; default keeps it unchanged")
    args = parser.parse_args()

    if bool(args.track_name) == bool(args.all):
        raise SystemExit("pass exactly one of: a track_name, or --all")
    if args.all and args.final_equity is not None:
        raise SystemExit("--final-equity cannot be used with --all -- one number can't override every track's equity; reset that track individually instead")

    config = load_tracks_config()

    if args.all:
        targets = list(config.items())
    else:
        if args.track_name not in config:
            raise SystemExit(f"unknown track {args.track_name!r}; known tracks: {sorted(config)}")
        targets = [(args.track_name, config[args.track_name])]

    # Phase 1: validate every target before touching any of them.
    plans = []
    errors = []
    for track_name, track_config in targets:
        try:
            state, final_equity = plan_reset(track_name, track_config["starting_capital"], args.final_equity)
            plans.append((track_name, track_config["starting_capital"], state, final_equity))
        except ValueError as e:
            errors.append(f"{track_name}: {e}")

    if errors:
        raise SystemExit(
            "Aborting -- nothing was reset. The following track(s) failed validation:\n  "
            + "\n  ".join(errors)
        )

    # Phase 2: every target passed phase 1, so it's safe to apply all of them.
    for track_name, starting_capital, state, final_equity in plans:
        apply_reset(track_name, starting_capital, state, final_equity, args.reason, args.new_starting_capital)


if __name__ == "__main__":
    main()
