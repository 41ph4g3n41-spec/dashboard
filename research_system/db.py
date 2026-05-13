"""SQLite schema + helpers."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import DB_PATH

log = logging.getLogger(__name__)


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
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_dedupe_key(source: str, url: str | None, headline: str) -> str:
    base = (url or "") + "||" + (headline or "")
    return source + ":" + hashlib.sha1(base.encode("utf-8")).hexdigest()


def ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as cx:
        cx.executescript(SCHEMA)


@contextmanager
def connect():
    cx = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA journal_mode=WAL;")
    cx.execute("PRAGMA foreign_keys=ON;")
    try:
        yield cx
    finally:
        cx.close()


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
    except sqlite3.IntegrityError:
        return None  # dedupe hit


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
