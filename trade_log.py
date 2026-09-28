"""Prints every trade across all tracks, chronologically, with the strategy's
reasoning for each one, plus a round-trip analysis (hold duration, gross move,
and how much of that move was eaten by cost). Complements scoreboard.py's
summary stats with the actual decision-by-decision history."""

from datetime import datetime

from engine.config import load_tracks_config
from engine.ledger import Ledger


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _format_duration(delta) -> str:
    total_minutes = int(delta.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d {hours}h"
    return f"{hours}h {minutes}m"


def round_trips(track_name: str, trades: list) -> list:
    """Pairs BUY->SELL rows into round trips with duration, gross price move,
    and cost-as-%-of-move (using the actual fees paid, not a config assumption)."""
    trips = []
    buy = None
    for ts, side, price, qty, fee, cash_after, equity_after, reason in trades:
        if side == "BUY":
            buy = (ts, price, qty, fee)
        elif side == "SELL" and buy is not None:
            buy_ts, buy_price, buy_qty, buy_fee = buy
            duration = _parse_ts(ts) - _parse_ts(buy_ts)
            gross_move_pct = (price - buy_price) / buy_price * 100.0
            buy_notional = buy_price * buy_qty
            total_cost = buy_fee + fee
            cost_pct_of_notional = (total_cost / buy_notional * 100.0) if buy_notional > 0 else float("nan")
            cost_share_of_move = (cost_pct_of_notional / abs(gross_move_pct) * 100.0) if gross_move_pct != 0 else float("inf")
            trips.append({
                "track": track_name, "buy_ts": buy_ts, "sell_ts": ts, "duration": duration,
                "buy_price": buy_price, "sell_price": price, "gross_move_pct": gross_move_pct,
                "cost_pct_of_notional": cost_pct_of_notional, "cost_share_of_move": cost_share_of_move,
            })
            buy = None
    return trips


def main():
    config = load_tracks_config()
    all_trades = []
    all_trips = []

    for track_name, track_config in config.items():
        ledger = Ledger(track_name, track_config["starting_capital"])
        trades = ledger.trades()
        ledger.close()

        for ts, side, price, qty, fee, cash_after, equity_after, reason in trades:
            all_trades.append((ts, track_name, side, price, qty, fee, equity_after, reason))
        all_trips.extend(round_trips(track_name, trades))

    all_trades.sort(key=lambda t: t[0])

    if not all_trades:
        print("No trades yet.")
        return

    header = f"{'Timestamp (UTC)':<20}{'Track':<30}{'Side':<6}{'Qty':>14}{'Price':>14}{'Fee':>9}{'Equity':>10}  Reason"
    print(header)
    print("-" * len(header))
    for ts, track, side, price, qty, fee, equity_after, reason in all_trades:
        ts_short = ts.split(".")[0].replace("T", " ")  # "2026-09-23 08:21:00", drop microseconds/offset (always UTC)
        print(f"{ts_short:<20}{track:<30}{side:<6}{qty:>14.6f}{price:>14.6f}{fee:>9.4f}{equity_after:>10.4f}  {reason}")

    print(f"\n{len(all_trades)} trade(s) total across {len(config)} tracks.")

    if all_trips:
        all_trips.sort(key=lambda t: t["buy_ts"])
        print("\n\nROUND-TRIP ANALYSIS (hold duration, gross move, cost as % of that move)")
        rt_header = f"{'Track':<30}{'Held':>10}{'GrossMove%':>12}{'RTCost%':>10}{'CostOfMove%':>13}"
        print(rt_header)
        print("-" * len(rt_header))
        for t in all_trips:
            cost_share_str = "inf" if t["cost_share_of_move"] == float("inf") else f"{t['cost_share_of_move']:.0f}%"
            print(f"{t['track']:<30}{_format_duration(t['duration']):>10}{t['gross_move_pct']:>+11.3f}%{t['cost_pct_of_notional']:>9.2f}%{cost_share_str:>13}")
        print("\nRTCost% = actual fees paid (both legs) as % of position notional -- the real")
        print("cost, not a config assumption. CostOfMove% = what share of the gross price")
        print("move that cost consumed (>100% means cost alone exceeded the entire move).")


if __name__ == "__main__":
    main()
