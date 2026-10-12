"""4x3 grid view: the 4 strategies/markets (rows) x leverage level (columns:
1x/2x/10x). Complements scoreboard.py's full detail table with a compact
side-by-side comparison of how leverage affects the exact same signal.
"""

from engine.config import load_tracks_config
from engine.ledger import Ledger

ROWS = [
    ("crypto_momentum", "BTC momentum"),
    ("crypto_mean_reversion", "ETH mean-rev"),
    ("stocks_momentum", "SPY momentum"),
    ("prediction_markets_momentum", "Polymarket momentum"),
    # Regime-filtered variants (added 2026-10-02, 2x/10x added 2026-10-02 as well):
    # same markets, same cost/sizing, but the entry is gated on the slow SMA also
    # rising -- see engine/strategies/momentum_regime_filtered.py.
    ("crypto_momentum_regime", "BTC momentum+regime"),
    ("stocks_momentum_regime", "SPY momentum+regime"),
    ("prediction_markets_momentum_regime", "Polymarket momentum+regime"),
]
COLUMNS = [("", "1x"), ("_2x", "2x"), ("_10x", "10x")]


def cell_text(base_name: str, suffix: str) -> str:
    track_name = base_name + suffix
    config = load_tracks_config()
    track_config = config.get(track_name)
    if not track_config:
        return "n/a"

    ledger = Ledger(track_name, track_config["starting_capital"])
    state = ledger.get_state()
    # Scoped to the current epoch only (2026-10-12 audit finding #10): a reset no
    # longer deletes prior trades/equity rows, it just tags them with the old
    # epoch number and starts a new one -- so "current" stats must filter to
    # state.epoch, or a reset track would show cumulative pre-reset numbers
    # again instead of a fresh start.
    curve = ledger.equity_curve(epoch=state.epoch)
    trades = ledger.trades(epoch=state.epoch)
    n_trades = len(trades)
    n_prior_epochs = state.epoch
    ledger.close()

    starting_capital = state.starting_capital
    if not curve:
        return f"${starting_capital:.0f} start, 0 trades"

    current_equity = curve[-1][2]
    return_pct = 100 * (current_equity - starting_capital) / starting_capital
    # Leveraged tracks' loss cap is non-terminal (2026-10-12 audit finding #6): it
    # force-closes the position but keeps trading, so [STOPPED] no longer applies
    # to them. Surface breach count instead so the event stays visible in the grid.
    n_breaches = sum(1 for t in trades if "LOSS_CAP_BREACHED" in (t[7] or ""))
    status = ""
    if state.stopped:
        status += " [STOPPED]"
    elif n_breaches:
        status += f" [{n_breaches} breach{'es' if n_breaches != 1 else ''}]"
    if n_prior_epochs:
        # 2026-10-12 audit finding #10: a reset is now a new epoch, not erased
        # history -- `python reset_track.py` and the ledger's `epochs` table
        # hold what happened before. See README for the query.
        status += f" [epoch {n_prior_epochs}]"
    return f"{return_pct:+.2f}% (${current_equity:.2f}, {n_trades}tr){status}"


def main():
    col_width = 28
    label_width = 22

    header = " " * label_width + "".join(f"{label:^{col_width}}" for _, label in COLUMNS)
    print(header)
    print("-" * len(header))

    for base_name, row_label in ROWS:
        line = f"{row_label:<{label_width}}"
        for suffix, _ in COLUMNS:
            line += f"{cell_text(base_name, suffix):^{col_width}}"
        print(line)

    print()
    print("Format: return% (current balance, trade count) [STOPPED if loss-capped]")
    print("Starting capital: $10 for all 1x tracks, $50 for all 2x/10x tracks.")


if __name__ == "__main__":
    main()
