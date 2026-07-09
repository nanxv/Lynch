"""日报深度异动狙击手：触发检测 + 轻量预筛（不拉全量财报）。"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from . import config
from .config import correct_ticker
from .data.base import BaseDataProvider


def _parse_date(raw: Any) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def has_recent_8k(ticker: str, *, lookback_days: int = 7) -> bool:
    """近 N 日是否有 SEC 8-K 披露（实时端点）。"""
    try:
        from .data.fmp import _fetch_8k_filings
    except ImportError:
        return False
    sym = correct_ticker(ticker)
    cutoff = date.today() - timedelta(days=lookback_days)
    for row in _fetch_8k_filings(sym, limit=5):
        d = _parse_date(row.get("filingDate") or row.get("acceptedDate") or row.get("date"))
        if d is not None and d >= cutoff:
            return True
    return False


def has_recent_insider_activity(ticker: str) -> bool:
    """近半年内部人净买入信号（轻量 FMP 探针）。"""
    try:
        from .data.fmp import _api
        from .data.fmp_alpha import _insider_from_search
    except ImportError:
        return False
    sym = correct_ticker(ticker)
    try:
        buys, sells = _insider_from_search(_api(), sym)
    except Exception:  # noqa: BLE001
        return False
    return buys >= config.INSIDER_MIN_NET_BUYS and buys > sells


def check_daily_trigger(
    provider: BaseDataProvider,
    ticker: str,
    *,
    threshold: float | None = None,
) -> tuple[bool, list[str], float | None]:
    """判定是否触发日报狙击。返回 (触发?, 原因列表, 当日涨跌幅)。"""
    threshold = config.DAILY_PRICE_CHANGE_THRESHOLD if threshold is None else threshold
    reasons: list[str] = []
    day_change: float | None = None
    try:
        day_change = provider.get_daily_price_change(ticker)
    except Exception:  # noqa: BLE001
        pass
    if day_change is not None and abs(day_change) >= threshold:
        reasons.append(f"价格波动 {day_change * 100:+.1f}%")
    if has_recent_8k(ticker):
        reasons.append("突发 8-K 披露")
    if has_recent_insider_activity(ticker):
        reasons.append("内部人净买入")
    return bool(reasons), reasons, day_change
