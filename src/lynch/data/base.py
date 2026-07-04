"""数据供应层抽象基类与标准数据结构。

`BaseDataProvider` 定义了林奇 SOP 所需的标准方法，任何数据源（Yahoo / FMP / J-Quants）
只要实现少量抽象方法即可接入，上层的漏斗与 Agent 完全不感知底层数据源。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class FundamentalsError(Exception):
    """基本面数据缺失或无法获取时抛出。"""


@dataclass(frozen=True)
class Fundamentals:
    """林奇 SOP 所需的原始基本面快照。缺失字段以 None 表示。"""

    ticker: str
    name: str | None
    sector: str | None
    industry: str | None
    currency: str | None

    price: float | None
    market_cap: float | None

    trailing_pe: float | None
    forward_pe: float | None
    earnings_growth_yoy: float | None
    revenue_growth_yoy: float | None

    eps_series: dict[int, float] = field(default_factory=dict)
    net_income_series: dict[int, float] = field(default_factory=dict)
    revenue_series: dict[int, float] = field(default_factory=dict)
    inventory_series: dict[int, float] = field(default_factory=dict)

    long_term_debt: float | None = None
    total_debt: float | None = None
    stockholders_equity: float | None = None
    total_assets: float | None = None
    total_cash: float | None = None
    cash_per_share: float | None = None

    free_cashflow: float | None = None
    operating_cashflow: float | None = None
    capital_expenditure: float | None = None

    shares_outstanding: float | None = None
    dividend_yield: float | None = None
    held_percent_institutions: float | None = None
    exchange: str | None = None

    source: str = "base"


@dataclass(frozen=True)
class QuickScreen:
    """第一层漏斗用的轻量快照（尽量只用一次廉价请求得到）。"""

    ticker: str
    name: str | None = None
    price: float | None = None
    market_cap: float | None = None
    exchange: str | None = None
    sbi_tradable: bool | None = None
    trailing_pe: float | None = None
    growth_yoy: float | None = None
    quick_peg: float | None = None
    debt_ratio: float | None = None  # 总债/权益（小数）
    net_cash_per_share: float | None = None
    net_cash_ratio: float | None = None  # 每股净现金/股价
    is_priority: bool = False  # 是否来自"必看列表"（watchlist）


class BaseDataProvider(ABC):
    """数据源抽象基类。子类只需实现 3 个抽象方法。"""

    name: str = "base"

    def __init__(self) -> None:
        self._cache: dict[str, Fundamentals] = {}

    # ── 子类必须实现 ──────────────────────────────────────────
    @abstractmethod
    def _fetch_fundamentals(self, ticker: str) -> Fundamentals:
        """抓取完整基本面（可能较慢，含财报三张表）。"""

    @abstractmethod
    def get_quick_screen(self, ticker: str) -> QuickScreen | None:
        """第一层漏斗用的廉价粗筛快照；失败返回 None。"""

    @abstractmethod
    def get_stock_price_change(self, ticker: str, period: str = "5d") -> float | None:
        """区间涨跌幅（小数，0.05 = +5%）；用于每日股价异动监控。"""

    # ── 通用（带缓存）─────────────────────────────────────────
    def get_fundamentals(self, ticker: str) -> Fundamentals:
        key = ticker.upper()
        if key not in self._cache:
            self._cache[key] = self._fetch_fundamentals(ticker)
        return self._cache[key]

    # ── 林奇 SOP 标准 getter（基于完整基本面派生）────────────
    def get_company_type(self, ticker: str) -> str:
        return classify_company(self.get_fundamentals(ticker))

    def get_peg(self, ticker: str) -> float | None:
        from ..metrics import compute_metrics

        return compute_metrics(self.get_fundamentals(ticker)).peg

    def get_debt_to_equity(self, ticker: str) -> float | None:
        from ..metrics import compute_metrics

        m = compute_metrics(self.get_fundamentals(ticker)).by_key("debt")
        return m.value if m else None

    def get_inventory_vs_sales(self, ticker: str) -> float | None:
        from ..metrics import compute_metrics

        m = compute_metrics(self.get_fundamentals(ticker)).by_key("inventory")
        return m.value if m else None

    def get_net_cash(self, ticker: str) -> float | None:
        from ..metrics import compute_metrics

        m = compute_metrics(self.get_fundamentals(ticker)).by_key("net_cash")
        return m.value if m else None


_CYCLICAL_SECTORS = {"Energy", "Basic Materials"}
_CYCLICAL_HINTS = (
    "Semiconductor", "Auto", "Steel", "Oil", "Gas", "Mining", "Airline",
    "Chemical", "Shipping", "Aluminum", "Copper", "Homebuild", "Travel",
    "Construction", "Metals", "Machinery", "Paper", "Rubber",
)
_FINANCIAL_HINTS = ("Bank", "Insurance", "Capital Markets", "Financial", "Mortgage", "Credit")


def is_financial(f: "Fundamentals") -> bool:
    """金融业（银行/保险等）——负债天生极高，需豁免负债排雷。"""
    if (f.sector or "") == "Financial Services":
        return True
    industry = f.industry or ""
    return any(h in industry for h in _FINANCIAL_HINTS)


def is_cyclical(f: "Fundamentals") -> bool:
    """周期股——高 P/E/亏损/短期利润暴跌往往是底部，需反向判定、豁免常规排雷。"""
    if is_financial(f):
        return False
    sector = f.sector or ""
    industry = f.industry or ""
    return sector in _CYCLICAL_SECTORS or any(h in industry for h in _CYCLICAL_HINTS)


def _cagr(series: dict[int, float]) -> float | None:
    if len(series) < 2:
        return None
    years = sorted(series)
    first, last = series[years[0]], series[years[-1]]
    span = years[-1] - years[0]
    if span <= 0 or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1 / span) - 1


def classify_company(f: Fundamentals) -> str:
    """基于代码规则给出林奇六大类的初步判定（LLM 会在周报里做权威复核）。"""
    growth = _cagr(f.eps_series)
    if growth is None:
        growth = f.earnings_growth_yoy
    div = f.dividend_yield or 0.0  # 已是百分比

    net_cash_ratio = None
    if f.total_cash is not None and f.shares_outstanding and f.price and f.price > 0:
        net_cash = f.total_cash - (f.total_debt or 0.0)
        net_cash_ratio = (net_cash / f.shares_outstanding) / f.price

    industry = f.industry or ""
    sector = f.sector or ""
    is_cyclical = sector in _CYCLICAL_SECTORS or any(h in industry for h in _CYCLICAL_HINTS)

    if net_cash_ratio is not None and net_cash_ratio >= 0.30:
        return "隐蔽资产型"
    if growth is not None and growth < 0 and (f.long_term_debt or 0) > 0:
        return "困境反转型"
    if is_cyclical:
        return "周期型"
    if growth is not None and growth >= 0.20:
        return "快速增长型"
    if div >= 4.0 and (growth is None or growth < 0.08):
        return "缓慢增长型"
    return "稳定增长型"
