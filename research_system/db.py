"""SQLite schema + helpers.

Backend is pluggable:
  - default: local SQLite file (DB_PATH)
  - if TURSO_DATABASE_URL + TURSO_AUTH_TOKEN env vars are set, uses
    libsql-experimental embedded-replica mode — local SQLite that
    auto-syncs to Turso (hosted libSQL) so the VPS scheduler and
    Streamlit Cloud dashboard share one logical DB.

The wrapper exposes a sqlite3-compatible API so the rest of the
codebase doesn't change.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import DB_PATH

log = logging.getLogger(__name__)


# ----------------------------- libsql adapters --------------------------

def _turso_enabled() -> bool:
    return bool(os.getenv("TURSO_DATABASE_URL"))


class _DictRow:
    """sqlite3.Row-lookalike: supports row[int], row['col'], dict(row),
    keys(), and len(). Built from a description tuple + values tuple."""
    __slots__ = ("_cols", "_vals", "_lookup")

    def __init__(self, cols: tuple[str, ...], vals: Sequence):
        self._cols = cols
        self._vals = tuple(vals)
        self._lookup = {c: i for i, c in enumerate(cols)}

    def __getitem__(self, k):
        if isinstance(k, int):
            return self._vals[k]
        return self._vals[self._lookup[k]]

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def keys(self):
        return list(self._cols)

    def __repr__(self):
        return f"<Row {dict(zip(self._cols, self._vals))}>"


class _LibsqlCursor:
    def __init__(self, cur):
        self._cur = cur

    @property
    def description(self):
        return self._cur.description

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return getattr(self._cur, "rowcount", -1)

    def _cols(self) -> tuple[str, ...]:
        desc = self._cur.description or ()
        return tuple(d[0] for d in desc)

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        return _DictRow(self._cols(), row)

    def fetchall(self):
        cols = self._cols()
        return [_DictRow(cols, r) for r in self._cur.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class _LibsqlConn:
    """sqlite3.Connection-shim wrapping libsql_experimental.Connection."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql: str, params: Sequence = ()):
        cur = self._raw.execute(sql, tuple(params)) if params else self._raw.execute(sql)
        return _LibsqlCursor(cur)

    def executescript(self, script: str):
        return self._raw.executescript(script)

    def commit(self):
        return self._raw.commit()

    def close(self):
        try:
            self._raw.sync()           # one last push before closing
        except Exception:
            pass
        return self._raw.close()

    def sync(self):
        try:
            return self._raw.sync()
        except Exception as e:
            log.debug("libsql sync skipped: %s", e)


def _open_libsql() -> _LibsqlConn:
    try:
        import libsql_experimental as libsql
    except ImportError as e:
        raise RuntimeError(
            "TURSO_DATABASE_URL is set but libsql-experimental isn't installed. "
            "Either:\n"
            "  • install it:  pip install libsql-experimental\n"
            "  • or unset TURSO_DATABASE_URL to use local SQLite\n"
            f"(import error: {e})"
        ) from e
    url = os.environ["TURSO_DATABASE_URL"]
    token = os.environ.get("TURSO_AUTH_TOKEN", "")
    # Local replica file lives next to the regular DB
    local = str(DB_PATH.with_suffix(".replica.db"))
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    interval = float(os.getenv("TURSO_SYNC_INTERVAL", "30"))
    raw = libsql.connect(
        local,
        sync_url=url,
        auth_token=token,
        sync_interval=interval,
        isolation_level=None,   # autocommit, like the sqlite3 path
    )
    return _LibsqlConn(raw)


def _is_unique_violation(exc: BaseException) -> bool:
    """libsql raises ValueError on constraint failure; sqlite3 raises
    IntegrityError. Treat both as 'dedupe collision'."""
    if isinstance(exc, sqlite3.IntegrityError):
        return True
    if isinstance(exc, ValueError) and "constraint failed" in str(exc).lower():
        return True
    return False


SCHEMA = """
CREATE TABLE IF NOT EXISTS updates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT,
    source      TEXT NOT NULL,         -- nse | bse | rss:moneycontrol | pib | ...
    type        TEXT,                  -- announcement | news | press_release
    headline    TEXT NOT NULL,
    body        TEXT,
    url         TEXT,
    dedupe_key  TEXT UNIQUE NOT NULL,
    fetched_at  TEXT NOT NULL,
    processed   INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_updates_ticker     ON updates(ticker);
CREATE INDEX IF NOT EXISTS idx_updates_fetched_at ON updates(fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_updates_processed  ON updates(processed);

CREATE TABLE IF NOT EXISTS analyses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    update_id       INTEGER NOT NULL REFERENCES updates(id),
    ticker          TEXT,
    impact          TEXT,
    urgency         TEXT,
    thesis_effect   TEXT,
    action          TEXT,
    reasoning       TEXT,
    follow_ups      TEXT,              -- JSON list
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_analyses_ticker     ON analyses(ticker);
CREATE INDEX IF NOT EXISTS idx_analyses_urgency    ON analyses(urgency);
CREATE INDEX IF NOT EXISTS idx_analyses_created_at ON analyses(created_at DESC);

CREATE TABLE IF NOT EXISTS daily_briefs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT UNIQUE NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prices (
    ticker      TEXT NOT NULL,
    asof_date   TEXT NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      INTEGER,
    PRIMARY KEY (ticker, asof_date)
);
"""


def now_iso() -> str:
    """UTC timestamp in SQLite-friendly format: 'YYYY-MM-DD HH:MM:SS'.
    Must match the format SQLite's datetime() returns so that
    string comparisons in WHERE clauses behave as date comparisons.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def make_dedupe_key(source: str, url: str | None, headline: str) -> str:
    base = (url or "") + "||" + (headline or "")
    return source + ":" + hashlib.sha1(base.encode("utf-8")).hexdigest()


def ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as cx:
        cx.executescript(SCHEMA)


@contextmanager
def connect():
    if _turso_enabled():
        cx = _open_libsql()
        try:
            yield cx
        finally:
            cx.close()
        return
    raw = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA journal_mode=WAL;")
    raw.execute("PRAGMA foreign_keys=ON;")
    try:
        yield raw
    finally:
        raw.close()


# ----------------------------- updates ----------------------------------

def insert_update(
    *,
    ticker: str | None,
    source: str,
    type_: str,
    headline: str,
    body: str | None,
    url: str | None,
) -> int | None:
    """Insert an update. Returns rowid, or None if it was a duplicate."""
    dedupe = make_dedupe_key(source, url, headline)
    fetched_at = now_iso()
    try:
        with connect() as cx:
            cur = cx.execute(
                """INSERT INTO updates
                   (ticker, source, type, headline, body, url, dedupe_key, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (ticker, source, type_, headline, body, url, dedupe, fetched_at),
            )
            return cur.lastrowid
    except Exception as e:
        if _is_unique_violation(e):
            return None  # dedupe hit
        raise


def unprocessed_updates(limit: int = 50) -> list[sqlite3.Row]:
    with connect() as cx:
        return list(
            cx.execute(
                "SELECT * FROM updates WHERE processed = 0 ORDER BY id ASC LIMIT ?",
                (limit,),
            )
        )


def mark_processed(update_id: int) -> None:
    with connect() as cx:
        cx.execute("UPDATE updates SET processed = 1 WHERE id = ?", (update_id,))


def recent_updates(hours: int = 18, ticker: str | None = None) -> list[sqlite3.Row]:
    sql = (
        "SELECT * FROM updates "
        "WHERE fetched_at >= datetime('now', ?)"
    )
    params: list[Any] = [f"-{hours} hours"]
    if ticker:
        sql += " AND ticker = ?"
        params.append(ticker)
    sql += " ORDER BY fetched_at DESC"
    with connect() as cx:
        return list(cx.execute(sql, params))


# ----------------------------- analyses ---------------------------------

def insert_analysis(
    *,
    update_id: int,
    ticker: str | None,
    impact: str,
    urgency: str,
    thesis_effect: str,
    action: str,
    reasoning: str,
    follow_ups: Iterable[str],
) -> int:
    with connect() as cx:
        cur = cx.execute(
            """INSERT INTO analyses
               (update_id, ticker, impact, urgency, thesis_effect,
                action, reasoning, follow_ups, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                update_id,
                ticker,
                impact,
                urgency,
                thesis_effect,
                action,
                reasoning,
                json.dumps(list(follow_ups)),
                now_iso(),
            ),
        )
        return cur.lastrowid


def recent_analyses(hours: int = 18, ticker: str | None = None) -> list[sqlite3.Row]:
    sql = (
        "SELECT a.*, u.headline, u.url, u.source "
        "FROM analyses a JOIN updates u ON a.update_id = u.id "
        "WHERE a.created_at >= datetime('now', ?)"
    )
    params: list[Any] = [f"-{hours} hours"]
    if ticker:
        sql += " AND a.ticker = ?"
        params.append(ticker)
    sql += " ORDER BY a.created_at DESC"
    with connect() as cx:
        return list(cx.execute(sql, params))


def feed_rows(limit: int = 200, ticker: str | None = None,
              impact: str | None = None, urgency: str | None = None):
    sql = (
        "SELECT u.id as update_id, u.ticker, u.source, u.headline, u.url, "
        "       u.fetched_at, a.impact, a.urgency, a.thesis_effect, "
        "       a.action, a.reasoning, a.follow_ups "
        "FROM updates u LEFT JOIN analyses a ON a.update_id = u.id "
        "WHERE 1=1"
    )
    params: list[Any] = []
    if ticker:
        sql += " AND u.ticker = ?"
        params.append(ticker)
    if impact:
        sql += " AND a.impact = ?"
        params.append(impact)
    if urgency:
        sql += " AND a.urgency = ?"
        params.append(urgency)
    sql += " ORDER BY u.fetched_at DESC LIMIT ?"
    params.append(limit)
    with connect() as cx:
        return list(cx.execute(sql, params))


# ----------------------------- briefs -----------------------------------

def upsert_brief(date_str: str, content: str) -> None:
    with connect() as cx:
        cx.execute(
            """INSERT INTO daily_briefs(date, content, created_at)
               VALUES (?,?,?)
               ON CONFLICT(date) DO UPDATE SET
                   content=excluded.content,
                   created_at=excluded.created_at""",
            (date_str, content, now_iso()),
        )


def latest_brief() -> sqlite3.Row | None:
    with connect() as cx:
        row = cx.execute(
            "SELECT * FROM daily_briefs ORDER BY date DESC LIMIT 1"
        ).fetchone()
        return row


# ----------------------------- prices -----------------------------------

def upsert_price(ticker: str, asof: str, o: float, h: float, l: float,
                 c: float, v: int) -> None:
    with connect() as cx:
        cx.execute(
            """INSERT INTO prices(ticker, asof_date, open, high, low, close, volume)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(ticker, asof_date) DO UPDATE SET
                   open=excluded.open, high=excluded.high, low=excluded.low,
                   close=excluded.close, volume=excluded.volume""",
            (ticker, asof, o, h, l, c, v),
        )


def latest_prices() -> dict[str, sqlite3.Row]:
    with connect() as cx:
        rows = cx.execute(
            """SELECT ticker, MAX(asof_date) as asof_date, open, high, low, close, volume
               FROM prices GROUP BY ticker"""
        ).fetchall()
        return {r["ticker"]: r for r in rows}


if __name__ == "__main__":
    ensure_db()
    print(f"DB ready at {DB_PATH}")
