"""审计抓取：供阶段 1 / 2 与报告生成复用。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from .calculation_audit import CalculationAuditReport, audit_calculations
from .config import correct_ticker
from .data.base import Fundamentals, QuickScreen
from .data.yahoo import (
    YahooFinanceProvider,
    _base_fundamentals_from_info,
    _fetch_info,
    _finalize_with_temporal,
    _ticker,
)
from .data_quality import DataQualityReport, validate_raw_data
from .report_modes import normalize_mode
from .temporal import latest_statement_end


@dataclass
class FullAuditResult:
    ticker: str
    mode: str
    fundamentals: Fundamentals
    data_quality: DataQualityReport
    calculation: CalculationAuditReport


def _last_bar_date(tk) -> pd.Timestamp | None:
    try:
        hist = tk.history(period="5d", interval="1d", auto_adjust=False)
    except Exception:  # noqa: BLE001
        return None
    if hist is None or hist.empty:
        return None
    return pd.Timestamp(hist.index[-1])


def run_full_audit(
    ticker: str,
    mode: str,
    provider: YahooFinanceProvider | None = None,
) -> FullAuditResult:
    provider = provider or YahooFinanceProvider()
    ticker = correct_ticker(ticker)
    mode = normalize_mode(mode)
    fetched_at = datetime.now(timezone.utc)

    info = _fetch_info(ticker)
    tk = _ticker(ticker)

    income = balance = cash = income_q = None
    try:
        income = tk.income_stmt
        balance = tk.balance_sheet
        cash = tk.cashflow
    except Exception:  # noqa: BLE001
        pass
    try:
        income_q = tk.quarterly_income_stmt
    except Exception:  # noqa: BLE001
        pass

    base = _base_fundamentals_from_info(
        ticker, info, income, balance, cash, mode=mode, source=provider.name,
    )
    f = _finalize_with_temporal(base, tk, mode=mode)
    qs = provider.get_quick_screen(ticker)
    last_bar = _last_bar_date(tk)
    stmt_end = latest_statement_end(income_q if mode == "quarterly" else income)

    dq = validate_raw_data(
        f, info, mode=mode, quick_screen=qs,
        last_bar_date=last_bar.date() if last_bar is not None else None,
        latest_statement_date=stmt_end,
        has_quarterly=income_q is not None and not getattr(income_q, "empty", True),
        has_price_history=last_bar is not None,
        fetched_at=fetched_at,
    )
    calc = audit_calculations(
        f, mode=mode, quick_screen=qs, data_trusted=dq.is_trusted,
    )
    return FullAuditResult(ticker=ticker, mode=mode, fundamentals=f, data_quality=dq, calculation=calc)
