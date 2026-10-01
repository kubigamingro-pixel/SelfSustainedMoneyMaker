"""SQLite-backed ledger: cash, position, trade history, equity curve, per track."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DATA_STORE = Path(__file__).resolve().parent.parent / "data_store"


@dataclass
class State:
    cash: float
    position_qty: float
    position_avg_price: float
    stopped: bool
    starting_capital: float
    margin_committed: float = 0.0  # leveraged tracks only; always 0 for spot tracks


class Ledger:
    def __init__(self, track_name: str, starting_capital: float):
        """starting_capital is only used to seed a track the first time it's ever
        created. Once persisted, it's immutable -- later Ledger(...) calls with a
        different value (e.g. because config/tracks.yaml was edited) do NOT change
        it, since the loss-cap floor and return% math must stay anchored to what
        the track actually started with, not to whatever the config currently says."""
        DATA_STORE.mkdir(exist_ok=True)
        self.track_name = track_name
        self.db_path = DATA_STORE / f"{track_name}.db"
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cash REAL NOT NULL,
                position_qty REAL NOT NULL,
                position_avg_price REAL NOT NULL,
                stopped INTEGER NOT NULL,
                starting_capital REAL NOT NULL DEFAULT 0,
                margin_committed REAL NOT NULL DEFAULT 0
            )"""
        )
        # migrate ledgers created before margin_committed existed as a column (all
        # spot tracks predate it) -- 0 is the correct value for every one of them,
        # no heuristic needed, unlike the starting_capital migration below.
        columns = [row[1] for row in self._conn.execute("PRAGMA table_info(state)").fetchall()]
        if "margin_committed" not in columns:
            self._conn.execute("ALTER TABLE state ADD COLUMN margin_committed REAL NOT NULL DEFAULT 0")
        # migrate ledgers created before starting_capital existed as a column.
        # Heuristic backfill (starting_capital = cash) is only correct for a track
        # that hadn't traded yet at migration time -- verified true for all 4 real
        # ledgers when this ran. It would under-backfill (leave 0, disabling the
        # loss cap) for a track already holding a position pre-migration; not a
        # live concern since the migration has already run against production.
        columns = [row[1] for row in self._conn.execute("PRAGMA table_info(state)").fetchall()]
        if "starting_capital" not in columns:
            self._conn.execute("ALTER TABLE state ADD COLUMN starting_capital REAL NOT NULL DEFAULT 0")
            self._conn.execute("UPDATE state SET starting_capital = cash WHERE starting_capital = 0 AND position_qty = 0")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                qty REAL NOT NULL,
                fee REAL NOT NULL,
                cash_after REAL NOT NULL,
                equity_after REAL NOT NULL,
                reason TEXT
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS equity_curve (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                price REAL,
                equity REAL NOT NULL
            )"""
        )
        self._conn.commit()

        row = self._conn.execute("SELECT cash FROM state WHERE id = 1").fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO state (id, cash, position_qty, position_avg_price, stopped, starting_capital) VALUES (1, ?, 0, 0, 0, ?)",
                (starting_capital, starting_capital),
            )
            self._conn.commit()

    def get_state(self) -> State:
        row = self._conn.execute(
            "SELECT cash, position_qty, position_avg_price, stopped, starting_capital, margin_committed FROM state WHERE id = 1"
        ).fetchone()
        return State(cash=row[0], position_qty=row[1], position_avg_price=row[2], stopped=bool(row[3]), starting_capital=row[4], margin_committed=row[5])

    def set_state(self, state: State):
        # starting_capital is intentionally not updated here -- it's immutable after creation.
        self._conn.execute(
            "UPDATE state SET cash = ?, position_qty = ?, position_avg_price = ?, stopped = ?, margin_committed = ? WHERE id = 1",
            (state.cash, state.position_qty, state.position_avg_price, int(state.stopped), state.margin_committed),
        )
        self._conn.commit()

    def record_trade(self, side: str, price: float, qty: float, fee: float, cash_after: float, equity_after: float, reason: str = ""):
        self._conn.execute(
            "INSERT INTO trades (ts, side, price, qty, fee, cash_after, equity_after, reason) VALUES (?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), side, price, qty, fee, cash_after, equity_after, reason),
        )
        self._conn.commit()

    def record_equity(self, price: float, equity: float):
        self._conn.execute(
            "INSERT INTO equity_curve (ts, price, equity) VALUES (?,?,?)",
            (datetime.now(timezone.utc).isoformat(), price, equity),
        )
        self._conn.commit()

    def equity_curve(self):
        return self._conn.execute("SELECT ts, price, equity FROM equity_curve ORDER BY id").fetchall()

    def trades(self):
        return self._conn.execute("SELECT ts, side, price, qty, fee, cash_after, equity_after, reason FROM trades ORDER BY id").fetchall()

    def close(self):
        self._conn.close()
