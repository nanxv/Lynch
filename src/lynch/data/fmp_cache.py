"""FMP 本地持久化缓存与 API 日额度追踪（免费档 250 次/天）。"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]

log = logging.getLogger(__name__)

CACHE_ROOT = ROOT / "data" / "cache" / "fmp"
STATIC_DIR = CACHE_ROOT / "static"
WHALE_DIR = CACHE_ROOT / "whale"
USAGE_FILE = CACHE_ROOT / "api_usage.json"

DAILY_QUOTA = 250
EARNINGS_LAG_DAYS = 50  # 季末后约 N 天视为新财报窗口


def _ensure_dirs() -> None:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    WHALE_DIR.mkdir(parents=True, exist_ok=True)


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_json(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:  # noqa: BLE001
        log.warning("读取缓存失败 %s: %s", path, exc)
        return None


def _save_json(path: Path, data: Any) -> None:
    _ensure_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


class FmpApiBudget:
    """当日 FMP 请求计数（持久化，跨进程共享）。"""

    def __init__(self, path: Path = USAGE_FILE) -> None:
        self.path = path

    def _load(self) -> dict[str, Any]:
        raw = _load_json(self.path)
        if not isinstance(raw, dict):
            return {"date": _today_str(), "count": 0}
        if raw.get("date") != _today_str():
            return {"date": _today_str(), "count": 0}
        return raw

    def count(self) -> int:
        return int(self._load().get("count", 0))

    def remaining(self) -> int:
        return max(0, DAILY_QUOTA - self.count())

    def increment(self, n: int = 1) -> int:
        _ensure_dirs()
        data = self._load()
        data["count"] = int(data.get("count", 0)) + n
        data["date"] = _today_str()
        _save_json(self.path, data)
        used = data["count"]
        if used >= DAILY_QUOTA - 20:
            log.warning("FMP API 额度告警：今日已用 %s/%s", used, DAILY_QUOTA)
        return used

    def check(self) -> None:
        if self.count() >= DAILY_QUOTA:
            raise RuntimeError(
                f"FMP 免费档日额度已耗尽（{DAILY_QUOTA} 次/天），请明日再试或升级套餐。"
            )


def _quarter_end(d: date) -> date:
    m = d.month
    if m <= 3:
        return date(d.year, 3, 31)
    if m <= 6:
        return date(d.year, 6, 30)
    if m <= 9:
        return date(d.year, 9, 30)
    return date(d.year, 12, 31)


def latest_reportable_quarter_end(today: date | None = None) -> date:
    """截至 today，市场通常已披露完毕的最新财季截止日。"""
    today = today or date.today()
    candidate = _quarter_end(today)
    while today < candidate + timedelta(days=EARNINGS_LAG_DAYS):
        candidate = _quarter_end(candidate - timedelta(days=92))
    return candidate


def _latest_income_date(income_annual: list[dict] | None) -> str | None:
    if not income_annual:
        return None
    dates = [str(r.get("date") or "")[:10] for r in income_annual if r.get("date")]
    return max(dates) if dates else None


def static_cache_path(ticker: str) -> Path:
    safe = ticker.upper().replace("/", "_")
    return STATIC_DIR / f"{safe}.json"


def load_static_cache(ticker: str) -> dict[str, Any] | None:
    raw = _load_json(static_cache_path(ticker))
    return raw if isinstance(raw, dict) else None


def save_static_cache(ticker: str, bundle: dict[str, Any]) -> None:
    income = bundle.get("income_annual")
    meta = {
        "ticker": ticker.upper(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "latest_income_date": _latest_income_date(income if isinstance(income, list) else None),
    }
    bundle["meta"] = meta
    _save_json(static_cache_path(ticker), bundle)


def needs_static_refresh(ticker: str, *, today: date | None = None) -> bool:
    """缺失缓存，或已跨入新财报发布窗口 → 允许消耗 API 刷新静态包。"""
    cached = load_static_cache(ticker)
    if not cached:
        return True
    meta = cached.get("meta") or {}
    latest = meta.get("latest_income_date")
    if not latest:
        return True
    try:
        cached_dt = date.fromisoformat(str(latest)[:10])
    except ValueError:
        return True
    reportable = latest_reportable_quarter_end(today)
    return cached_dt < reportable


def whale_cache_path(name: str) -> Path:
    return WHALE_DIR / f"{name}.json"


def load_whale_cache(name: str) -> dict[str, Any] | None:
    raw = _load_json(whale_cache_path(name))
    return raw if isinstance(raw, dict) else None


def save_whale_cache(name: str, data: dict[str, Any]) -> None:
    data["fetched_at"] = datetime.now(timezone.utc).isoformat()
    _save_json(whale_cache_path(name), data)


def whale_cache_stale(name: str, *, max_age_days: int = 7) -> bool:
    cached = load_whale_cache(name)
    if not cached:
        return True
    fetched = cached.get("fetched_at")
    if not fetched:
        return True
    try:
        ts = datetime.fromisoformat(str(fetched).replace("Z", "+00:00"))
    except ValueError:
        return True
    age = datetime.now(timezone.utc) - ts
    return age > timedelta(days=max_age_days)
