"""报告周期（Mode）常量与调度辅助。"""

from __future__ import annotations

import calendar
from datetime import date

# 全部合法模式
REPORT_MODES = ("daily", "weekly", "monthly", "quarterly", "annual")

# 需要 Gemini 深度会诊的模式（daily 仅在异动触发时走 AI）
AI_MODES = ("daily", "weekly", "monthly", "quarterly", "annual")

MODE_TITLES = {
    "daily": "深度异动狙击手日报",
    "weekly": "全境多桶雷达周报",
    "monthly": "月度动量会诊",
    "quarterly": "持仓生死拷问季报",
    "annual": "林奇逻辑重估年报",
}

SUBJECT_PREFIX = {
    "daily": "【林奇异动狙击】日报",
    "weekly": "【林奇全境雷达】周报",
    "monthly": "【彼得林奇月度会诊】月报",
    "quarterly": "【林奇持仓拷问】季报",
    "annual": "【林奇年终重估】年报",
}


def allows_full_universe(mode: str) -> bool:
    """仅周报允许 scope=full 全市场漏斗。"""
    return mode == "weekly"


def held_only_mode(mode: str) -> bool:
    """季报/年报仅限 held 持仓。"""
    return mode in ("quarterly", "annual")


def is_daily_sniper_mode(mode: str) -> bool:
    return mode == "daily"


def is_ai_mode(mode: str) -> bool:
    return mode in AI_MODES


def last_weekday_of_month(year: int, month: int) -> date:
    """当月最后一个工作日（周一~周五，近似最后交易日）。"""
    last_day = calendar.monthrange(year, month)[1]
    for day in range(last_day, 0, -1):
        d = date(year, month, day)
        if d.weekday() < 5:
            return d
    return date(year, month, last_day)


def is_last_trading_day_of_month(on: date | None = None) -> bool:
    """是否为当月最后一个工作日（用于月报 cron 门禁）。"""
    on = on or date.today()
    return on == last_weekday_of_month(on.year, on.month)


def normalize_mode(mode: str) -> str:
    m = (mode or "weekly").strip().lower()
    if m not in REPORT_MODES:
        raise ValueError(f"未知报告模式: {mode}（可选 {', '.join(REPORT_MODES)}）")
    return m
