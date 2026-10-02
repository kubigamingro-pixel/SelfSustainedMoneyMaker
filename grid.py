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
    curve = ledger.equity_curve()
    n_trades = len(ledger.trades())
    ledger.close()

    starting_capital = state.starting_capital
    if not curve:
        return f"${starting_capital:.0f} start, 0 trades"

    current_equity = curve[-1][2]
    return_pct = 100 * (current_equity - starting_capital) / starting_capital
    status = " [STOPPED]" if state.stopped else ""
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
