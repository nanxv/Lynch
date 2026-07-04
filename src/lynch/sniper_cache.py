"""盘中狙击防刷：同一标的同一交易日最多发一次警报。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_CACHE_DIR = _ROOT / "data" / "sniper_cache"


def _cache_path(d: date | None = None) -> Path:
    d = d or date.today()
    return _CACHE_DIR / f"{d.isoformat()}.json"


def _load_set(d: date | None = None) -> set[str]:
    path = _cache_path(d)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(t).upper() for t in data.get("tickers", [])}
    except Exception:  # noqa: BLE001
        return set()


def already_alerted(ticker: str, *, on: date | None = None) -> bool:
    return ticker.upper() in _load_set(on)


def mark_alerted(ticker: str, *, on: date | None = None) -> None:
    path = _cache_path(on)
    path.parent.mkdir(parents=True, exist_ok=True)
    tickers = _load_set(on)
    tickers.add(ticker.upper())
    path.write_text(
        json.dumps({"date": (on or date.today()).isoformat(), "tickers": sorted(tickers)},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
