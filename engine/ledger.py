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
    epoch: int = 0  # 2026-10-12 audit finding #10 -- see Ledger.reset_epoch
    # 2026-10-12 fix-check review, finding #12 edge case: the period_low used for
    # intrabar liquidation (engine/leverage_risk.py) can repeat across two
    # scheduler ticks that both land inside the same still-open candle. If a
    # position was opened on the first of those ticks, that repeated low can
    # predate the entry -- the position never actually experienced it. Stores
    # the period_low that was already "live" at entry time so check_liquidation
    # can skip the intrabar check for as long as it's still the same period.
    position_entry_period_low: float = None


class Ledger:
    def __init__(self, track_name: str, starting_capital: float, db_path=None):
        """starting_capital is only used to seed a track the first time it's ever
        created. Once persisted, it's immutable -- later Ledger(...) calls with a
        different value (e.g. because config/tracks.yaml was edited) do NOT change
        it, since the loss-cap floor and return% math must stay anchored to what
        the track actually started with, not to whatever the config currently says.

        db_path (2026-10-12 audit finding #5, backtest harness): overrides the
        default data_store/<track_name>.db location. Pass ":memory:" for a
        throwaway in-memory ledger (what backtest.py uses, so replaying years of
        history never touches disk or the real data_store/) -- every other
        caller omits this and gets the normal on-disk path."""
        self.track_name = track_name
        if db_path == ":memory:":
            self.db_path = ":memory:"
        else:
            DATA_STORE.mkdir(exist_ok=True)
            self.db_path = Path(db_path) if db_path is not None else DATA_STORE / f"{track_name}.db"
            if self.db_path.parent != DATA_STORE:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cash REAL NOT NULL,
                position_qty REAL NOT NULL,
                position_avg_price REAL NOT NULL,
                stopped INTEGER NOT NULL,
                starting_capital REAL NOT NULL DEFAULT 0,
                margin_committed REAL NOT NULL DEFAULT 0,
                epoch INTEGER NOT NULL DEFAULT 0,
                position_entry_period_low REAL
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
        # migrate ledgers created before epoch existed (2026-10-12 audit finding
        # #10): every real ledger so far is epoch 0 by construction (manual resets
        # before this wiped the row entirely rather than incrementing a counter).
        columns = [row[1] for row in self._conn.execute("PRAGMA table_info(state)").fetchall()]
        if "epoch" not in columns:
            self._conn.execute("ALTER TABLE state ADD COLUMN epoch INTEGER NOT NULL DEFAULT 0")
        # migrate ledgers created before position_entry_period_low existed
        # (2026-10-12 fix-check review, finding #12 edge case). NULL is correct
        # for every pre-existing ledger: an already-open position just means the
        # intrabar check applies normally from the next tick, same as before
        # this column existed.
        columns = [row[1] for row in self._conn.execute("PRAGMA table_info(state)").fetchall()]
        if "position_entry_period_low" not in columns:
            self._conn.execute("ALTER TABLE state ADD COLUMN position_entry_period_low REAL")
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
                reason TEXT,
                epoch INTEGER NOT NULL DEFAULT 0
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS equity_curve (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                price REAL,
                equity REAL NOT NULL,
                epoch INTEGER NOT NULL DEFAULT 0
            )"""
        )
        # 2026-10-12 audit finding #9: HOLD/filtered/guard-failed decisions were only
        # ever visible in logs/ (gitignored) or an expired CI run log, so answering
        # "is the regime guard actually the binding constraint" required replaying
        # ticks from scratch. One row per tick here makes that a SQL query instead.
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                price REAL,
                action TEXT NOT NULL,
                reason TEXT,
                cost_filter_pass INTEGER,
                epoch INTEGER NOT NULL DEFAULT 0
            )"""
        )
        for table in ("trades", "equity_curve", "decisions"):
            cols = [row[1] for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if "epoch" not in cols:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN epoch INTEGER NOT NULL DEFAULT 0")
        # 2026-10-12 audit finding #10: a reset used to overwrite the live ledger in
        # place (git history was the only record that a prior epoch ever existed),
        # which forced two manual resets to also become undocumented data loss
        # events for scoreboard.py/grid.py. This table holds one row per *closed*
        # epoch (written by reset_epoch()), so a track's full history survives
        # resets inside the database itself, queryable, not just archaeology.
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS epochs (
                epoch INTEGER NOT NULL,
                ended_ts TEXT NOT NULL,
                reason TEXT,
                starting_capital REAL NOT NULL,
                final_cash REAL NOT NULL,
                final_equity REAL NOT NULL,
                final_position_qty REAL NOT NULL,
                n_trades INTEGER NOT NULL,
                PRIMARY KEY (epoch)
            )"""
        )
        self._conn.commit()

        row = self._conn.execute("SELECT cash FROM state WHERE id = 1").fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO state (id, cash, position_qty, position_avg_price, stopped, starting_capital, epoch) VALUES (1, ?, 0, 0, 0, ?, 0)",
                (starting_capital, starting_capital),
            )
            self._conn.commit()

    def get_state(self) -> State:
        row = self._conn.execute(
            "SELECT cash, position_qty, position_avg_price, stopped, starting_capital, margin_committed, epoch, "
            "position_entry_period_low FROM state WHERE id = 1"
        ).fetchone()
        return State(
            cash=row[0], position_qty=row[1], position_avg_price=row[2], stopped=bool(row[3]),
            starting_capital=row[4], margin_committed=row[5], epoch=row[6], position_entry_period_low=row[7],
        )

    def set_state(self, state: State):
        # starting_capital and epoch are intentionally not updated here -- both
        # immutable after creation except through reset_epoch(). Does NOT commit
        # (see commit()/close()) -- a tick typically calls this plus
        # record_trade/record_equity and they should land together or not at all
        # (2026-10-12 audit finding #15: separate commits per call left a window
        # where a crash between them recorded a trade with no matching state).
        self._conn.execute(
            "UPDATE state SET cash = ?, position_qty = ?, position_avg_price = ?, stopped = ?, margin_committed = ?, "
            "position_entry_period_low = ? WHERE id = 1",
            (state.cash, state.position_qty, state.position_avg_price, int(state.stopped), state.margin_committed,
             state.position_entry_period_low),
        )

    @staticmethod
    def _ts(ts) -> str:
        """ts=None -> wall-clock now (live trading). 2026-10-12 audit finding #5:
        the backtest harness passes the historical candle's own timestamp here
        instead, so replayed trades/equity/decisions carry real historical dates
        rather than "now" repeated for every row."""
        if ts is None:
            return datetime.now(timezone.utc).isoformat()
        if isinstance(ts, str):
            return ts
        return ts.isoformat()

    def _current_epoch(self) -> int:
        row = self._conn.execute("SELECT epoch FROM state WHERE id = 1").fetchone()
        return row[0] if row else 0

    def record_trade(self, side: str, price: float, qty: float, fee: float, cash_after: float, equity_after: float, reason: str = "", ts=None):
        self._conn.execute(
            "INSERT INTO trades (ts, side, price, qty, fee, cash_after, equity_after, reason, epoch) VALUES (?,?,?,?,?,?,?,?,?)",
            (self._ts(ts), side, price, qty, fee, cash_after, equity_after, reason, self._current_epoch()),
        )

    def record_equity(self, price: float, equity: float, ts=None):
        self._conn.execute(
            "INSERT INTO equity_curve (ts, price, equity, epoch) VALUES (?,?,?,?)",
            (self._ts(ts), price, equity, self._current_epoch()),
        )

    def record_decision(self, price: float, action: str, reason: str = "", cost_filter_pass: bool = None, ts=None):
        self._conn.execute(
            "INSERT INTO decisions (ts, price, action, reason, cost_filter_pass, epoch) VALUES (?,?,?,?,?,?)",
            (
                self._ts(ts),
                price,
                action,
                reason,
                None if cost_filter_pass is None else int(cost_filter_pass),
                self._current_epoch(),
            ),
        )

    def reset_epoch(self, final_equity: float, reason: str, new_starting_capital: float = None, ts=None) -> int:
        """Archives the current epoch's summary into the epochs table, then resets
        live state to start a new epoch -- cash/position/stopped/margin cleared,
        epoch incremented. Returns the new epoch number.

        2026-10-12 audit finding #10: replaces the old pattern of resetting a
        track by directly rewriting its ledger's state row (commits ab7b3037,
        2260457f) -- which left the fact that a prior epoch existed, and its
        outcome, visible only in git history. trades/equity_curve/decisions rows
        are NOT deleted or modified -- they stay tagged with their original
        epoch number, so e.g. `SELECT * FROM trades WHERE epoch = 0` still
        answers "what happened in the epoch that got reset," right there in the
        database. final_equity must be supplied by the caller (reset_track.py)
        since the ledger itself has no market price to value an open position
        with. new_starting_capital lets a reset also change the track's
        starting capital (e.g. a rules change); omit it to keep the current one."""
        state = self.get_state()
        trades_in_epoch = self._conn.execute(
            "SELECT COUNT(*) FROM trades WHERE epoch = ?", (state.epoch,)
        ).fetchone()[0]
        self._conn.execute(
            """INSERT INTO epochs (epoch, ended_ts, reason, starting_capital, final_cash, final_equity, final_position_qty, n_trades)
               VALUES (?,?,?,?,?,?,?,?)""",
            (state.epoch, self._ts(ts), reason, state.starting_capital, state.cash, final_equity, state.position_qty, trades_in_epoch),
        )
        capital = new_starting_capital if new_starting_capital is not None else state.starting_capital
        self._conn.execute(
            "UPDATE state SET cash = ?, position_qty = 0, position_avg_price = 0, stopped = 0, margin_committed = 0, "
            "position_entry_period_low = NULL, starting_capital = ?, epoch = epoch + 1 WHERE id = 1",
            (capital, capital),
        )
        self.commit()
        return state.epoch + 1

    def commit(self):
        """Call once per tick after all set_state/record_* calls for that tick --
        they intentionally don't auto-commit so a whole tick lands atomically."""
        self._conn.commit()

    def equity_curve(self, epoch: int = None):
        if epoch is None:
            return self._conn.execute("SELECT ts, price, equity FROM equity_curve ORDER BY id").fetchall()
        return self._conn.execute("SELECT ts, price, equity FROM equity_curve WHERE epoch = ? ORDER BY id", (epoch,)).fetchall()

    def trades(self, epoch: int = None):
        if epoch is None:
            return self._conn.execute("SELECT ts, side, price, qty, fee, cash_after, equity_after, reason FROM trades ORDER BY id").fetchall()
        return self._conn.execute(
            "SELECT ts, side, price, qty, fee, cash_after, equity_after, reason FROM trades WHERE epoch = ? ORDER BY id", (epoch,)
        ).fetchall()

    def decisions(self, epoch: int = None):
        if epoch is None:
            return self._conn.execute("SELECT ts, price, action, reason, cost_filter_pass FROM decisions ORDER BY id").fetchall()
        return self._conn.execute(
            "SELECT ts, price, action, reason, cost_filter_pass FROM decisions WHERE epoch = ? ORDER BY id", (epoch,)
        ).fetchall()

    def epochs(self):
        """Summaries of every *closed* epoch (the current live one isn't in here
        until the next reset_epoch() call)."""
        return self._conn.execute(
            "SELECT epoch, ended_ts, reason, starting_capital, final_cash, final_equity, final_position_qty, n_trades FROM epochs ORDER BY epoch"
        ).fetchall()

    def close(self):
        self._conn.close()
