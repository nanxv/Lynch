"""Local JSON cache to suppress duplicate buy alerts within a TTL window."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .strategy import AlertType, ScanResult

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_PATH = ROOT / ".alert_cache.json"

BUY_ALERT_TYPES = frozenset({AlertType.TIER1_CORE, AlertType.TIER2_BUY})


def alert_cache_key(result: ScanResult) -> str | None:
    if result.alert_type not in BUY_ALERT_TYPES:
        return None
    return f"{result.stock.ticker}:buy"


class AlertCache:
    def __init__(self, path: Path = DEFAULT_CACHE_PATH, ttl_hours: float = 24.0) -> None:
        self.path = path
        self.ttl = timedelta(hours=ttl_hours)

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open(encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except (json.JSONDecodeError, OSError):
            pass
        return {}

    def _save(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _parse_ts(self, raw: str) -> datetime | None:
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    def was_sent_recently(self, key: str, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        raw = self._load().get(key)
        if not raw:
            return False
        sent_at = self._parse_ts(raw)
        if sent_at is None:
            return False
        return now - sent_at < self.ttl

    def should_send(self, result: ScanResult, now: datetime | None = None) -> bool:
        key = alert_cache_key(result)
        if key is None:
            return True
        return not self.was_sent_recently(key, now=now)

    def mark_sent(self, result: ScanResult, now: datetime | None = None) -> None:
        key = alert_cache_key(result)
        if key is None:
            return
        now = now or datetime.now(timezone.utc)
        data = self._load()
        data[key] = now.isoformat()
        self._prune(data, now=now)
        self._save(data)

    def _prune(self, data: dict[str, str], now: datetime) -> None:
        cutoff = now - self.ttl
        for key, raw in list(data.items()):
            sent_at = self._parse_ts(raw)
            if sent_at is None or sent_at < cutoff:
                del data[key]
