"""美股交易日历 — 日报/狙击门禁（NYSE 近似，以 SPY 日 K 为准）。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

US_EASTERN = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# 日报 cron 在 UTC 21:00 触发，对应美东当日常规收盘后（16:00 ET）
DAILY_CRON_HOUR_UTC = 21


def us_eastern_date(when: datetime | None = None) -> date:
    """将时刻映射为美东日历日。"""
    when = when or datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.astimezone(US_EASTERN).date()


def expected_daily_session_date(when: datetime | None = None) -> date:
    """UTC 21:00 触发的日报所应对齐的美股交易 session（美东日期）。"""
    return us_eastern_date(when)


@lru_cache(maxsize=8)
def _spy_trading_dates(end_iso: str, lookback: int) -> frozenset[date]:
    """拉取 SPY 日 K 索引，得到实际成交的美东日期集合（含早收日，排除休市）。"""
    end = date.fromisoformat(end_iso)
    start = end - timedelta(days=lookback + 14)
    try:
        import yfinance as yf
    except ImportError:
        return frozenset()

    try:
        hist = yf.Ticker("SPY").history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            interval="1d",
            auto_adjust=False,
        )
    except Exception:  # noqa: BLE001
        return frozenset()

    if hist is None or hist.empty:
        return frozenset()

    out: set[date] = set()
    for ts in hist.index:
        if hasattr(ts, "tz_convert"):
            ts = ts.tz_convert(US_EASTERN)
        elif ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC).astimezone(US_EASTERN)
        else:
            ts = ts.astimezone(US_EASTERN)
        out.add(ts.date())
    return frozenset(out)


def us_trading_dates(end: date | None = None, *, lookback: int = 45) -> frozenset[date]:
    end = end or date.today()
    return _spy_trading_dates(end.isoformat(), lookback)


def is_us_trading_day(session: date, *, calendar: frozenset[date] | None = None) -> bool:
    if session.weekday() >= 5:
        return False
    cal = calendar if calendar is not None else us_trading_dates(session)
    if not cal:
        # 无法拉日历时保守放行，避免 GitHub Actions 因网络偶发失败永久停报
        return session.weekday() < 5
    return session in cal


def last_us_trading_day(on_or_before: date | None = None) -> date | None:
    """on_or_before 及之前最近一个美股成交日。"""
    on_or_before = on_or_before or date.today()
    cal = us_trading_dates(on_or_before)
    for offset in range(15):
        d = on_or_before - timedelta(days=offset)
        if is_us_trading_day(d, calendar=cal):
            return d
    return None


def daily_report_skip_reason(when: datetime | None = None) -> str | None:
    """若不应发日报，返回人类可读原因；否则 None。"""
    when = when or datetime.now(UTC)
    session = expected_daily_session_date(when)
    if session.weekday() >= 5:
        return f"美股周末休市（美东 {session.isoformat()}）"
    cal = us_trading_dates(session)
    if not is_us_trading_day(session, calendar=cal):
        prev = last_us_trading_day(session - timedelta(days=1))
        prev_s = f"，上一交易日 {prev.isoformat()}" if prev else ""
        return f"美股休市（美东 {session.isoformat()}，法定节假日或暂停交易{prev_s}）"
    return None


def should_run_daily_report(when: datetime | None = None, *, force: bool = False) -> tuple[bool, str]:
    """日报是否应执行。force=True 时跳过门禁（调试用）。"""
    if force:
        return True, ""
    reason = daily_report_skip_reason(when)
    if reason:
        return False, reason
    session = expected_daily_session_date(when)
    return True, session.isoformat()
