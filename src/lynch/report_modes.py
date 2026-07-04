"""报告周期（Mode）常量与调度辅助。"""

from __future__ import annotations

import calendar
from datetime import date

# 全部合法模式
REPORT_MODES = ("daily", "weekly", "monthly", "quarterly", "annual")

# 需要 Gemini 深度会诊的模式（daily 仅硬指标 + 狙击例外）
AI_MODES = ("weekly", "monthly", "quarterly", "annual")

MODE_TITLES = {
    "daily": "自选股监控日报",
    "weekly": "深度分析周报",
    "monthly": "月度动量会诊",
    "quarterly": "财报季度会诊",
    "annual": "年终持仓审视",
}

SUBJECT_PREFIX = {
    "daily": "【彼得林奇自选股监控】日报",
    "weekly": "【彼得林奇深度分析】周报",
    "monthly": "【彼得林奇月度会诊】月报",
    "quarterly": "【彼得林奇财报季会诊】季报",
    "annual": "【彼得林奇年终审视】年报",
}


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
