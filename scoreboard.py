"""Reads all track ledgers and prints a comparison table, plus writes an
equity-curve chart to logs/equity_curve.png."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from engine.config import load_tracks_config
from engine.ledger import Ledger
from trade_log import round_trips

LOG_DIR = Path(__file__).resolve().parent / "logs"


def avg_hold_hours(trades: list, track_name: str) -> float:
    trips = round_trips(track_name, trades)
    if not trips:
        return float("nan")
    total_seconds = sum(t["duration"].total_seconds() for t in trips)
    return total_seconds / len(trips) / 3600.0


def max_drawdown(equities: list) -> float:
    peak = equities[0]
    worst = 0.0
    for e in equities:
        peak = max(peak, e)
        drawdown = (peak - e) / peak if peak > 0 else 0.0
        worst = max(worst, drawdown)
    return worst * 100


def buy_and_hold_return_pct(curve: list) -> float:
    """% return from simply holding the underlying asset from the first observed
    price to the latest, over the same window the track has been running. Momentum
    strategies are expected to underperform buy-and-hold in choppy/rangebound
    markets (that's the cost of downside insurance) -- so "did it beat buy-and-hold"
    is the fair question, not just "did it make money" (2026-09-28 external audit)."""
    if len(curve) < 2:
        return float("nan")
    first_price = curve[0][1]
    last_price = curve[-1][1]
    if not first_price or first_price <= 0:
        return float("nan")
    return (last_price - first_price) / first_price * 100.0


def win_rate(trades: list, starting_capital: float) -> float:
    """Net-of-cost win rate: was this whole round trip worth it compared to not
    having traded at all -- equity right BEFORE the BUY vs equity right AFTER the
    SELL. (An earlier version compared equity right AFTER the buy, which already
    has that trade's own buy-side cost baked into the baseline -- double-counting
    it as sunk before judging the trade, and overstating win rate as a result.)"""
    sells = [t for t in trades if t[1] == "SELL"]
    if not sells:
        return float("nan")
    wins = 0
    pre_trade_equity = starting_capital
    for t in trades:
        side, equity_after = t[1], t[6]
        if side == "SELL":
            if equity_after > pre_trade_equity:
                wins += 1
            pre_trade_equity = equity_after  # baseline for the next round trip
    return 100 * wins / len(sells)


def main():
    config = load_tracks_config()
    rows = []
    plt.figure(figsize=(10, 6))

    for track_name, track_config in config.items():
        # track_config["starting_capital"] only seeds a brand-new ledger; the
        # ledger's own persisted value is authoritative once a track exists,
        # so return% stays correct even if the config file is edited later.
        ledger = Ledger(track_name, track_config["starting_capital"])
        starting_capital = ledger.get_state().starting_capital
        curve = ledger.equity_curve()
        trades = ledger.trades()
        ledger.close()

        if not curve:
            rows.append((track_name, starting_capital, None, None, len(trades), None, None, None, None, None))
            continue

        equities = [row[2] for row in curve]
        current_equity = equities[-1]
        return_pct = 100 * (current_equity - starting_capital) / starting_capital
        dd = max_drawdown(equities)
        wr = win_rate(trades, starting_capital)
        if track_config.get("domain") == "prediction_markets":
            # The tracked market auto-rotates to a new question whenever the current
            # one resolves (engine/data/prediction_source.py) -- "first price vs last
            # price" would then be comparing two unrelated questions' probabilities,
            # not one asset's return. Buy-and-hold doesn't mean anything here.
            bh = float("nan")
        else:
            bh = buy_and_hold_return_pct(curve)
        alpha = return_pct - bh if bh == bh else float("nan")  # nan-safe (bh!=bh means nan)
        hold_hrs = avg_hold_hours(trades, track_name)

        rows.append((track_name, starting_capital, current_equity, return_pct, len(trades), dd, wr, bh, alpha, hold_hrs))

        xs = list(range(len(equities)))
        plt.plot(xs, equities, label=track_name)

    header = f"{'Track':<32}{'Start':>8}{'Current':>10}{'Return%':>10}{'Trades':>8}{'MaxDD%':>9}{'Win%':>8}{'BuyHold%':>10}{'Alpha%':>10}{'AvgHold':>10}"
    print(header)
    print("-" * len(header))
    for row in rows:
        if row[2] is None:
            print(f"{row[0]:<32}{row[1]:>8.2f}{'--':>10}{'--':>10}{row[4]:>8}{'--':>9}{'--':>8}{'--':>10}{'--':>10}{'--':>10}")
        else:
            name, start, current, ret, n_trades, dd, wr, bh, alpha, hold_hrs = row
            wr_str = "--" if wr != wr else f"{wr:.1f}"  # NaN check
            bh_str = "--" if bh != bh else f"{bh:+.2f}%"
            alpha_str = "--" if alpha != alpha else f"{alpha:+.2f}%"
            hold_str = "--" if hold_hrs != hold_hrs else f"{hold_hrs:.1f}h"
            print(f"{name:<32}{start:>8.2f}{current:>10.4f}{ret:>+9.2f}%{n_trades:>8}{dd:>8.2f}%{wr_str:>8}{bh_str:>10}{alpha_str:>10}{hold_str:>10}")

    if rows:
        plt.axhline(y=rows[0][1], color="gray", linestyle="--", linewidth=0.8, label="starting capital")
    plt.xlabel("tick #")
    plt.ylabel("equity ($)")
    plt.title("Paper-trading track comparison")
    plt.legend()
    LOG_DIR.mkdir(exist_ok=True)
    out_path = LOG_DIR / "equity_curve.png"
    plt.savefig(out_path, dpi=120, bbox_inches="tight")
    print(f"\nEquity curve chart written to {out_path}")


if __name__ == "__main__":
    main()
