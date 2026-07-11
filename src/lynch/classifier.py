"""林奇公司分类与行业常识（sector-aware）。

从「工业尺子」里拆出金融/科技/周期等物理隔离，供 metrics / funnel / FMP 粗筛共用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .data.base import Fundamentals

# ── 行业字典 ───────────────────────────────────────────────────
_CYCLICAL_SECTORS = {"Energy", "Basic Materials"}
_CYCLICAL_HINTS = (
    "Semiconductor", "Auto", "Steel", "Oil", "Gas", "Mining", "Airline",
    "Chemical", "Shipping", "Aluminum", "Copper", "Homebuild", "Travel",
    "Construction", "Metals", "Machinery", "Paper", "Rubber",
)
_FINANCIAL_HINTS = (
    "Bank", "Insurance", "Capital Markets", "Financial", "Mortgage", "Credit",
)
_INVENTORY_EXEMPT_SECTORS = frozenset({
    "Technology",
    "Communication Services",
    "Financial Services",
    "Financials",
})
_INVENTORY_EXEMPT_INDUSTRY_HINTS = (
    "Software", "Internet", "Information Technology Services",
    "Interactive Media", "Platform", "Asset Management",
)

# 近期营收萎缩阈值：单年跌超 5% 或连续两年负增长 → 禁止「快速增长型」
_REVENUE_SHRINK_YOY = -0.05
_REVENUE_HARD_SHRINK_YOY = -0.10


def financial_from_labels(sector: str | None, industry: str | None) -> bool:
    """金融大类：负债/净现金排雷彻底豁免。"""
    sec = (sector or "").strip()
    if sec in ("Financial Services", "Financials"):
        return True
    if "Financial" in sec or "Bank" in sec or "Insurance" in sec:
        return True
    ind = industry or ""
    return any(h in ind for h in _FINANCIAL_HINTS)


def cyclical_from_labels(sector: str | None, industry: str | None) -> bool:
    """周期股粗判（金融不标周期）。"""
    if financial_from_labels(sector, industry):
        return False
    sec = sector or ""
    ind = industry or ""
    return sec in _CYCLICAL_SECTORS or any(h in ind for h in _CYCLICAL_HINTS)


def inventory_exempt_from_labels(sector: str | None, industry: str | None) -> bool:
    """无库存/轻资产：禁止 DIO 与存货增速 vs 销售排雷。"""
    if financial_from_labels(sector, industry):
        return True
    sec = (sector or "").strip()
    if sec in _INVENTORY_EXEMPT_SECTORS:
        return True
    ind = industry or ""
    return any(h in ind for h in _INVENTORY_EXEMPT_INDUSTRY_HINTS)


def is_financial(f: Fundamentals) -> bool:
    return financial_from_labels(f.sector, f.industry)


def is_cyclical(f: Fundamentals) -> bool:
    return cyclical_from_labels(f.sector, f.industry)


def is_inventory_exempt(f: Fundamentals) -> bool:
    return inventory_exempt_from_labels(f.sector, f.industry)


def _cagr(series: dict[int, float]) -> float | None:
    if len(series) < 2:
        return None
    years = sorted(series)
    first, last = series[years[0]], series[years[-1]]
    span = years[-1] - years[0]
    if span <= 0 or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1 / span) - 1


def growth_rate_for_classify(f: Fundamentals) -> float | None:
    """分类用增速：优先 EPS CAGR，其次单季盈利同比。"""
    growth = _cagr(f.eps_series)
    if growth is None:
        growth = f.earnings_growth_yoy
    return growth


def _series_yoy(series: dict[int, float], *, lag: int = 1) -> float | None:
    """最近一期相对 lag 年前的同比（小数）。"""
    if len(series) < lag + 1:
        return None
    years = sorted(series)
    cur, prev = series[years[-1]], series[years[-1 - lag]]
    if prev == 0:
        return None
    return (cur - prev) / abs(prev)


def recent_revenue_yoy_pair(f: Fundamentals) -> tuple[float | None, float | None]:
    """返回 (最近一年营收YoY, 再前一年营收YoY)。"""
    latest = _series_yoy(f.revenue_series, lag=1)
    prior = _series_yoy(f.revenue_series, lag=2) if len(f.revenue_series) >= 3 else None
    if latest is None and f.revenue_growth_yoy is not None:
        latest = f.revenue_growth_yoy
    return latest, prior


def revenue_is_contracting(
    f: Fundamentals | None = None,
    *,
    revenue_growth: float | None = None,
    revenue_growth_prior: float | None = None,
) -> bool:
    """近期营收持续下降或大幅萎缩 → True（禁止打成快速增长型）。"""
    yoy1, yoy2 = revenue_growth, revenue_growth_prior
    if f is not None and yoy1 is None and yoy2 is None:
        yoy1, yoy2 = recent_revenue_yoy_pair(f)
    if yoy1 is None:
        return False
    if yoy1 <= _REVENUE_HARD_SHRINK_YOY:
        return True
    if yoy1 < _REVENUE_SHRINK_YOY and yoy2 is not None and yoy2 < 0:
        return True
    if yoy1 < _REVENUE_SHRINK_YOY and yoy2 is None:
        # 仅一年数据：跌超 5% 即视为萎缩
        return True
    return False


def demote_fake_fast_grower(
    *,
    growth: float | None,
    dividend_yield_pct: float | None,
    long_term_debt: float | None,
    revenue_contracting: bool,
) -> str:
    """利润 CAGR 看似快增但营收萎缩时的降级：困境 / 缓慢增长。"""
    if growth is not None and growth < 0 and (long_term_debt or 0) > 0:
        return "困境反转型"
    div = dividend_yield_pct or 0.0
    if div >= 4.0:
        return "缓慢增长型"
    # 营收萎缩的「伪成长」默认打入缓慢增长，避免继续贴快增标签
    if revenue_contracting:
        return "缓慢增长型"
    return "稳定增长型"


def classify_company(f: Fundamentals) -> str:
    """林奇六大类：周期 > 困境 > 增速带；快增强制检查近期营收趋势。"""
    growth = growth_rate_for_classify(f)
    div = f.dividend_yield or 0.0

    if cyclical_from_labels(f.sector, f.industry):
        return "周期型"

    if growth is not None and growth < 0 and (f.long_term_debt or 0) > 0:
        return "困境反转型"

    contracting = revenue_is_contracting(f)
    if growth is not None and growth >= 0.20:
        if contracting:
            return demote_fake_fast_grower(
                growth=growth,
                dividend_yield_pct=div,
                long_term_debt=f.long_term_debt,
                revenue_contracting=True,
            )
        return "快速增长型"

    if div >= 4.0 and (growth is None or growth < 0.08):
        return "缓慢增长型"
    return "稳定增长型"


def coarse_classify_from_labels(
    *,
    sector: str | None,
    industry: str | None,
    growth: float | None,
    dividend_yield_pct: float | None,
    long_term_debt: float | None = None,
    revenue_growth: float | None = None,
    revenue_growth_prior: float | None = None,
) -> str:
    """漏斗轻量粗分类（与 classify_company 同优先级）。"""
    if cyclical_from_labels(sector, industry):
        return "周期型"
    if growth is not None and growth < 0 and (long_term_debt or 0) > 0:
        return "困境反转型"
    div = dividend_yield_pct or 0.0
    contracting = revenue_is_contracting(
        revenue_growth=revenue_growth,
        revenue_growth_prior=revenue_growth_prior,
    )
    if growth is not None and growth >= 0.20:
        if contracting:
            return demote_fake_fast_grower(
                growth=growth,
                dividend_yield_pct=div,
                long_term_debt=long_term_debt,
                revenue_contracting=True,
            )
        return "快速增长型"
    if div >= 4.0 and (growth is None or growth < 0.08):
        return "缓慢增长型"
    return "稳定增长型"


def growth_cap_warn(
    f: Fundamentals,
    *,
    cagr: float | None = None,
    company_type: str | None = None,
) -> bool:
    """超高增速紧箍咒：仅【快速增长型】触发；周期/困境反转禁止。"""
    from . import config

    ctype = company_type
    if ctype is None:
        ctype = classify_company(f)
    if ctype in ("周期型", "困境反转型"):
        return False
    if ctype != "快速增长型":
        return False

    threshold = config.GROWTH_CAP_WARN_THRESHOLD
    g = cagr if cagr is not None else _cagr(f.eps_series)
    if g is None:
        g = _cagr(f.net_income_series)
    if g is not None and g >= threshold:
        return True
    yoy = f.earnings_growth_yoy or f.revenue_growth_yoy
    return yoy is not None and yoy >= threshold
