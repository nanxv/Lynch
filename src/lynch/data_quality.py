"""原始数据质量质检（阶段 1）：完整性 / 新鲜度 / 自洽 / 跨源 / 合理性 / 溯源。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Literal

import pandas as pd

from .data.base import Fundamentals, QuickScreen
from .market_calendar import last_us_trading_day, us_eastern_date
from .metrics import _yoy, compute_metrics

IssueLevel = Literal["fail", "warn", "info"]

# 年表「最新财年」距今天数阈值（非季报模式）
_STALE_WARN_MONTHS = 15
_STALE_FAIL_MONTHS = 24
# 勾稽允许相对误差
_MCAP_TOLERANCE = 0.15
_PE_TOLERANCE = 0.12
_DEBT_TOLERANCE = 0.50
_GROWTH_DIVERGENCE = 0.25  # info.earningsGrowth vs 年表 YoY 差值


@dataclass(frozen=True)
class DataQualityIssue:
    level: IssueLevel
    dimension: str
    field: str
    message: str
    expected: str | None = None
    actual: str | None = None
    source_a: str | None = None
    source_b: str | None = None


@dataclass
class DataQualityReport:
    ticker: str
    mode: str
    fetched_at: datetime
    issues: list[DataQualityIssue] = field(default_factory=list)
    provenance: dict[str, str] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)

    @property
    def fail_count(self) -> int:
        return sum(1 for i in self.issues if i.level == "fail")

    @property
    def warn_count(self) -> int:
        return sum(1 for i in self.issues if i.level == "warn")

    @property
    def is_trusted(self) -> bool:
        return self.fail_count == 0

    @property
    def score(self) -> float:
        s = 100.0 - self.fail_count * 30 - self.warn_count * 8
        return max(0.0, min(100.0, s))


# ── 各模式最低字段集（blocking = 缺失则 fail）────────────────
_MODE_REQUIRED: dict[str, tuple[str, ...]] = {
    "daily": ("price", "trailing_pe"),
    "weekly": ("price", "trailing_pe", "growth_series", "equity"),
    "monthly": ("price", "trailing_pe", "growth_series", "equity", "price_history"),
    "quarterly": ("price", "trailing_pe", "growth_series", "equity", "quarterly_income"),
    "annual": ("price", "trailing_pe", "revenue_series_5y", "equity"),
}


def _pct_diff(a: float, b: float) -> float | None:
    if b == 0:
        return None
    return abs(a - b) / abs(b)


def _fmt_num(v: float | None, *, pct: bool = False) -> str:
    if v is None:
        return "—"
    if pct:
        return f"{v * 100:.1f}%"
    if abs(v) >= 1e9:
        return f"{v / 1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"{v / 1e6:.2f}M"
    return f"{v:.4g}"


def _latest_fiscal_year(f: Fundamentals) -> int | None:
    years = set(f.eps_series) | set(f.net_income_series) | set(f.revenue_series)
    return max(years) if years else None


def _months_since_fiscal_year(fy: int, ref: date) -> float:
    """近似：假设财年截止于该自然年 12 月。"""
    end = date(fy, 12, 31)
    return (ref.year - end.year) * 12 + (ref.month - end.month)


def _series_yoy(series: dict[int, float]) -> float | None:
    return _yoy(series)


def _add(
    issues: list[DataQualityIssue],
    level: IssueLevel,
    dimension: str,
    field: str,
    message: str,
    **kw: str | None,
) -> None:
    issues.append(DataQualityIssue(level, dimension, field, message, **kw))


def build_provenance(f: Fundamentals, info: dict[str, Any]) -> dict[str, str]:
    """关键字段 → Yahoo 来源路径（供人工抽检）。"""
    prov: dict[str, str] = {
        "price": "info.regularMarketPrice | info.currentPrice",
        "trailing_pe": "info.trailingPE",
        "forward_pe": "info.forwardPE",
        "market_cap": "info.marketCap",
        "shares_outstanding": "info.sharesOutstanding",
        "total_cash": "info.totalCash | balance.Cash And Cash Equivalents",
        "total_debt": "info.totalDebt | balance.Total Debt",
        "long_term_debt": "balance.Long Term Debt | info.longTermDebt",
        "stockholders_equity": "balance.Stockholders Equity",
        "free_cashflow": "info.freeCashflow | cashflow.Free Cash Flow",
        "dividend_yield": "info.dividendYield",
        "earnings_growth_yoy": "info.earningsGrowth（单季同比，非 CAGR）",
        "revenue_growth_yoy": "info.revenueGrowth",
        "eps_series": "income_stmt.Diluted EPS | Basic EPS（年度列）",
        "net_income_series": "income_stmt.Net Income（年度列）",
        "revenue_series": "income_stmt.Total Revenue（年度列）",
        "inventory_series": "balance_sheet.Inventory（年度列）",
    }
    if info.get("regularMarketTime"):
        prov["quote_time"] = f"info.regularMarketTime={info['regularMarketTime']}"
    return prov


def _check_completeness(
    f: Fundamentals,
    mode: str,
    *,
    has_quarterly: bool,
    has_price_history: bool,
    issues: list[DataQualityIssue],
    missing: list[str],
) -> None:
    required = _MODE_REQUIRED.get(mode, _MODE_REQUIRED["weekly"])
    for key in required:
        ok = True
        if key == "price":
            ok = f.price is not None and f.price > 0
        elif key == "trailing_pe":
            ok = f.trailing_pe is not None  # 亏损股可为负，但 None 不行
        elif key == "growth_series":
            ok = len(f.eps_series) >= 2 or len(f.net_income_series) >= 2
        elif key == "equity":
            ok = f.stockholders_equity is not None
        elif key == "revenue_series_5y":
            ok = len(f.revenue_series) >= 5
        elif key == "quarterly_income":
            ok = has_quarterly
        elif key == "price_history":
            ok = has_price_history
        if not ok:
            missing.append(key)
            _add(
                issues, "fail", "completeness", key,
                f"模式 {mode} 必填字段缺失：{key}",
            )


def _check_freshness(
    f: Fundamentals,
    mode: str,
    *,
    ref_date: date,
    last_bar_date: date | None,
    latest_statement_date: date | None,
    issues: list[DataQualityIssue],
) -> None:
    if last_bar_date is not None:
        last_us = last_us_trading_day(ref_date)
        if last_us and last_bar_date < last_us:
            lag = (last_us - last_bar_date).days
            level: IssueLevel = "fail" if lag > 2 else "warn"
            _add(
                issues, level, "freshness", "price_history",
                f"日 K 末根 {last_bar_date} 落后最近美股交易日 {last_us}（{lag} 天）",
                expected=str(last_us), actual=str(last_bar_date),
                source_a="history.close[-1]", source_b="market_calendar.last_us_trading_day",
            )

    fy = _latest_fiscal_year(f)
    if fy is not None and mode not in ("daily", "quarterly"):
        age_m = _months_since_fiscal_year(fy, ref_date)
        if age_m > _STALE_FAIL_MONTHS:
            _add(
                issues, "fail", "freshness", "annual_statements",
                f"年表最新财年 {fy} 距今约 {age_m:.0f} 个月，财报严重滞后",
                actual=str(fy),
                source_a="income_stmt/balance_sheet 最新列",
            )
        elif age_m > _STALE_WARN_MONTHS:
            _add(
                issues, "warn", "freshness", "annual_statements",
                f"年表最新财年 {fy} 距今约 {age_m:.0f} 个月，价新表旧风险",
                actual=str(fy),
            )

    if latest_statement_date is not None:
        stmt_age = (ref_date - latest_statement_date.date()).days
        if mode == "quarterly" and stmt_age > 120:
            _add(
                issues, "warn", "freshness", "quarterly_statements",
                f"季度报表最新列 {latest_statement_date.date()} 已 {stmt_age} 天未更新",
                source_a="quarterly_income_stmt 最新列",
            )


def _check_internal_consistency(
    f: Fundamentals,
    issues: list[DataQualityIssue],
) -> None:
    price, shares, mcap = f.price, f.shares_outstanding, f.market_cap
    if price and shares and mcap and mcap > 0:
        implied = price * shares
        diff = _pct_diff(implied, mcap)
        if diff is not None and diff > _MCAP_TOLERANCE:
            _add(
                issues, "warn", "consistency", "market_cap",
                f"市值勾稽偏差 {diff:.0%}：price×shares={_fmt_num(implied)} vs marketCap={_fmt_num(mcap)}",
                expected=_fmt_num(implied), actual=_fmt_num(mcap),
                source_a="price×sharesOutstanding", source_b="info.marketCap",
            )

    pe, price = f.trailing_pe, f.price
    if pe and pe > 0 and price:
        implied_eps = price / pe
        # 反算 P/E 应一致
        back_pe = price / implied_eps
        diff = _pct_diff(back_pe, pe)
        if diff is not None and diff > 0.01:
            _add(
                issues, "fail", "consistency", "trailing_pe",
                f"P/E 与 price/EPS 不自洽",
                source_a="info.trailingPE", source_b="price/implied_eps",
            )

    if f.stockholders_equity is not None and f.stockholders_equity <= 0:
        _add(
            issues, "warn", "consistency", "stockholders_equity",
            f"股东权益 ≤ 0（{_fmt_num(f.stockholders_equity)}），负债比指标失真",
            source_a="balance.Stockholders Equity",
        )

    for name, series in (
        ("eps_series", f.eps_series),
        ("revenue_series", f.revenue_series),
        ("net_income_series", f.net_income_series),
    ):
        if len(series) < 2:
            continue
        years = sorted(series)
        if years[-1] > date.today().year + 1:
            _add(
                issues, "fail", "consistency", name,
                f"序列含未来年份 {years[-1]}，列解析可能错误",
            )
        gaps = [years[i + 1] - years[i] for i in range(len(years) - 1)]
        if any(g > 2 for g in gaps):
            _add(
                issues, "warn", "consistency", name,
                f"年度序列存在 >2 年缺口：{years}",
                source_a=f"income_stmt → {name}",
            )


def _check_cross_source(
    f: Fundamentals,
    info: dict[str, Any],
    qs: QuickScreen | None,
    issues: list[DataQualityIssue],
) -> None:
    # info.debtToEquity 为百分比数值（如 42.5 = 42.5%）
    d2e = info.get("debtToEquity")
    ltd, eq = f.long_term_debt, f.stockholders_equity
    if d2e is not None and ltd is not None and eq is not None and eq > 0:
        stmt_ratio = ltd / eq
        info_ratio = d2e / 100.0
        diff = _pct_diff(stmt_ratio, info_ratio)
        if diff is not None and diff > _DEBT_TOLERANCE:
            _add(
                issues, "warn", "cross_source", "debt_ratio",
                f"负债口径不一致：年表 ltd/equity={stmt_ratio:.0%} vs info.debtToEquity={info_ratio:.0%}",
                expected=_fmt_num(stmt_ratio, pct=True), actual=_fmt_num(info_ratio, pct=True),
                source_a="balance.Long Term Debt / Equity",
                source_b="info.debtToEquity/100（漏斗 quick_screen 用此）",
            )

    info_g = info.get("earningsGrowth")
    stmt_g = _series_yoy(f.net_income_series) or _series_yoy(f.eps_series)
    if info_g is not None and stmt_g is not None:
        if abs(info_g - stmt_g) > _GROWTH_DIVERGENCE:
            _add(
                issues, "warn", "cross_source", "earnings_growth",
                f"增速来源打架：info.earningsGrowth={_fmt_num(info_g, pct=True)} "
                f"vs 年表 YoY={_fmt_num(stmt_g, pct=True)}",
                source_a="info.earningsGrowth（漏斗 PEG 用）",
                source_b="年表 net_income/EPS YoY（正式 CAGR 用序列）",
            )

    info_fcf = info.get("freeCashflow")
    if info_fcf is not None and f.free_cashflow is not None:
        diff = _pct_diff(float(info_fcf), float(f.free_cashflow))
        if diff is not None and diff > 0.05:
            _add(
                issues, "warn", "cross_source", "free_cashflow",
                f"FCF 不一致：info={_fmt_num(info_fcf)} vs 解析={_fmt_num(f.free_cashflow)}",
                source_a="info.freeCashflow", source_b="cashflow.Free Cash Flow",
            )

    if qs and qs.quick_peg is not None:
        full = compute_metrics(f).peg
        if full is not None:
            diff = _pct_diff(qs.quick_peg, full)
            if diff is not None and diff > 0.20:
                _add(
                    issues, "info", "cross_source", "peg",
                    f"漏斗 quick_peg={qs.quick_peg:.2f} vs 正式 PEG={full:.2f}（口径不同，非必然错误）",
                    source_a="get_quick_screen: P/E÷(earningsGrowth×100)",
                    source_b="compute_metrics: 股息修正 CAGR PEG",
                )


def _check_plausibility(
    f: Fundamentals,
    info: dict[str, Any],
    issues: list[DataQualityIssue],
) -> None:
    if f.price is not None and f.price <= 0:
        _add(issues, "fail", "plausibility", "price", "现价 ≤ 0", source_a="info.price")

    if f.trailing_pe is not None:
        if f.trailing_pe < 0:
            _add(
                issues, "info", "plausibility", "trailing_pe",
                f"TTM P/E 为负（{_fmt_num(f.trailing_pe)}），亏损或周期底部，需人工解读",
            )
        elif f.trailing_pe > 500:
            _add(
                issues, "warn", "plausibility", "trailing_pe",
                f"TTM P/E 异常高（{f.trailing_pe:.0f}），疑为分母过小或数据异常",
            )

    div = f.dividend_yield
    if div is not None and div > 0:
        if div < 1.0:
            _add(
                issues, "warn", "plausibility", "dividend_yield",
                f"dividendYield={div:.4f} 在 (0,1) 区间，疑为小数形式（应为百分比数值如 1.51）",
                source_a="info.dividendYield",
            )
        elif div > 15:
            _add(
                issues, "warn", "plausibility", "dividend_yield",
                f"dividendYield={div:.1f}% 异常偏高，请对照 Yahoo 股息页",
            )

    inv_yoy = _series_yoy(f.inventory_series)
    rev_yoy = _series_yoy(f.revenue_series)
    if inv_yoy is not None and inv_yoy > 2.0 and (rev_yoy is None or rev_yoy < 0.5):
        _add(
            issues, "warn", "plausibility", "inventory_series",
            f"存货 YoY {_fmt_num(inv_yoy, pct=True)} 极端偏高，请核实 balance 列",
        )


def validate_raw_data(
    f: Fundamentals,
    info: dict[str, Any],
    *,
    mode: str = "weekly",
    quick_screen: QuickScreen | None = None,
    last_bar_date: date | None = None,
    latest_statement_date: pd.Timestamp | None = None,
    has_quarterly: bool = False,
    has_price_history: bool = False,
    ref_date: date | None = None,
    fetched_at: datetime | None = None,
) -> DataQualityReport:
    """阶段 1：原始数据质量质检。"""
    ref = ref_date or us_eastern_date()
    issues: list[DataQualityIssue] = []
    missing: list[str] = []

    _check_completeness(
        f, mode,
        has_quarterly=has_quarterly,
        has_price_history=has_price_history,
        issues=issues, missing=missing,
    )
    _check_freshness(
        f, mode,
        ref_date=ref,
        last_bar_date=last_bar_date,
        latest_statement_date=latest_statement_date,
        issues=issues,
    )
    _check_internal_consistency(f, issues)
    _check_cross_source(f, info, quick_screen, issues)
    _check_plausibility(f, info, issues)

    return DataQualityReport(
        ticker=f.ticker,
        mode=mode,
        fetched_at=fetched_at or datetime.now(timezone.utc),
        issues=issues,
        provenance=build_provenance(f, info),
        missing_fields=missing,
    )


def format_report(report: DataQualityReport) -> str:
    """人类可读质检报告。"""
    lines = [
        f"{report.ticker} · {report.mode} · {report.fetched_at.strftime('%Y-%m-%d %H:%M UTC')}",
        "━" * 48,
    ]
    if not report.issues:
        lines.append("[PASS] 未发现数据质量问题")
    else:
        order = {"fail": 0, "warn": 1, "info": 2}
        for iss in sorted(report.issues, key=lambda i: (order[i.level], i.dimension)):
            tag = iss.level.upper()
            lines.append(f"[{tag}] {iss.dimension:14} {iss.field}")
            lines.append(f"       {iss.message}")
            if iss.source_a:
                lines.append(f"       ↳ {iss.source_a}" + (f" | {iss.source_b}" if iss.source_b else ""))

    lines.append("─" * 48)
    trust = "YES" if report.is_trusted else "NO"
    lines.append(
        f"→ trusted: {trust}  score: {report.score:.0f}  "
        f"({report.fail_count} fail, {report.warn_count} warn)"
    )
    if report.missing_fields:
        lines.append(f"→ missing: {', '.join(report.missing_fields)}")

    lines.append("")
    lines.append("【字段溯源】")
    for k, v in sorted(report.provenance.items()):
        lines.append(f"  {k}: {v}")

    return "\n".join(lines)


def report_to_dict(report: DataQualityReport) -> dict[str, Any]:
    return {
        "ticker": report.ticker,
        "mode": report.mode,
        "fetched_at": report.fetched_at.isoformat(),
        "is_trusted": report.is_trusted,
        "score": report.score,
        "missing_fields": report.missing_fields,
        "provenance": report.provenance,
        "issues": [
            {
                "level": i.level,
                "dimension": i.dimension,
                "field": i.field,
                "message": i.message,
                "expected": i.expected,
                "actual": i.actual,
                "source_a": i.source_a,
                "source_b": i.source_b,
            }
            for i in report.issues
        ],
    }
