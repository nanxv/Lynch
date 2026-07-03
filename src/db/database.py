"""SQLite persistence for watchlist, alerts, dedup cache, and push tokens."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = ROOT / "data" / "stock_monitor.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    ticker TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    market TEXT NOT NULL CHECK (market IN ('JP', 'US')),
    tier INTEGER NOT NULL CHECK (tier IN (1, 2)),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    name TEXT NOT NULL,
    market TEXT NOT NULL,
    tier INTEGER NOT NULL,
    alert_type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    price REAL,
    deviation_pct REAL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_dedup (
    cache_key TEXT PRIMARY KEY,
    sent_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS push_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expo_push_token TEXT NOT NULL UNIQUE,
    device_label TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path = DEFAULT_DB_PATH) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def session(self):
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self.session() as conn:
            conn.executescript(SCHEMA)
