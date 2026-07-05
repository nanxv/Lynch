"""Repository helpers for watchlist, alerts, dedup, and push tokens."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import yaml

from ..config_loader import ROOT, StockEntry
from .database import Database, utc_now


class WatchlistRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_stocks(self) -> list[StockEntry]:
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT ticker, name, market, tier, note FROM watchlist ORDER BY ticker"
            ).fetchall()
        return [
            StockEntry(
                ticker=row["ticker"],
                name=row["name"],
                market=row["market"],
                tier=row["tier"],
                note=row["note"] or "",
            )
            for row in rows
        ]

    def add_stock(self, entry: StockEntry) -> StockEntry:
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO watchlist (ticker, name, market, tier, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    name=excluded.name,
                    market=excluded.market,
                    tier=excluded.tier,
                    note=excluded.note
                """,
                (
                    entry.ticker.upper(),
                    entry.name,
                    entry.market.upper(),
                    entry.tier,
                    entry.note,
                    utc_now(),
                ),
            )
        return entry

    def remove_stock(self, ticker: str) -> bool:
        with self.db.session() as conn:
            cur = conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker.upper(),))
            return cur.rowcount > 0

    def seed_from_yaml_if_empty(self, watchlist_path=None) -> int:
        watchlist_path = watchlist_path or ROOT / "watchlist.yaml"
        if self.list_stocks():
            return 0
        if not watchlist_path.exists():
            return 0
        with watchlist_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        count = 0
        from src.lynch.watchlist import parse_stock_entry

        for item in raw.get("stocks", []):
            self.add_stock(parse_stock_entry(item))
            count += 1
        return count


class AlertHistoryRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(
        self,
        *,
        ticker: str,
        name: str,
        market: str,
        tier: int,
        alert_type: str,
        title: str,
        body: str,
        price: float | None,
        deviation_pct: float | None,
    ) -> int:
        with self.db.session() as conn:
            cur = conn.execute(
                """
                INSERT INTO alert_history (
                    ticker, name, market, tier, alert_type, title, body,
                    price, deviation_pct, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    name,
                    market,
                    tier,
                    alert_type,
                    title,
                    body,
                    price,
                    deviation_pct,
                    utc_now(),
                ),
            )
            return int(cur.lastrowid)

    def list_recent(self, limit: int = 100) -> list[dict]:
        with self.db.session() as conn:
            rows = conn.execute(
                """
                SELECT id, ticker, name, market, tier, alert_type, title, body,
                       price, deviation_pct, created_at
                FROM alert_history
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


class DedupRepository:
    def __init__(self, db: Database, ttl_hours: float = 24.0) -> None:
        self.db = db
        self.ttl = timedelta(hours=ttl_hours)

    def was_sent_recently(self, cache_key: str, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT sent_at FROM alert_dedup WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if not row:
            return False
        sent_at = datetime.fromisoformat(row["sent_at"])
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        return now - sent_at < self.ttl

    def mark_sent(self, cache_key: str, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO alert_dedup (cache_key, sent_at)
                VALUES (?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET sent_at = excluded.sent_at
                """,
                (cache_key, now.isoformat()),
            )
            cutoff = (now - self.ttl).isoformat()
            conn.execute("DELETE FROM alert_dedup WHERE sent_at < ?", (cutoff,))


class PushTokenRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def register(self, expo_push_token: str, device_label: str = "") -> None:
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO push_tokens (expo_push_token, device_label, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(expo_push_token) DO UPDATE SET
                    device_label = excluded.device_label
                """,
                (expo_push_token, device_label, utc_now()),
            )

    def list_tokens(self) -> list[str]:
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT expo_push_token FROM push_tokens ORDER BY id"
            ).fetchall()
        return [row["expo_push_token"] for row in rows]

    def remove(self, expo_push_token: str) -> bool:
        with self.db.session() as conn:
            cur = conn.execute(
                "DELETE FROM push_tokens WHERE expo_push_token = ?",
                (expo_push_token,),
            )
            return cur.rowcount > 0
