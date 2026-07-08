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
    report_mode: str = "weekly"
    granularity_block: str = ""  # 模式专属高敏数据（月/季/年）

    # ── 多维时间轴（估值锚点，避免现价评判旧财报）──
    spot_price: float | None = None
    spot_pe: float | None = None
    valuation_anchor_date: str | None = None
    valuation_anchor_price: float | None = None
    valuation_pe: float | None = None
    price_1mo_ago: float | None = None
    pe_1mo_ago: float | None = None
    peg_1mo_ago: float | None = None
    pe_5y_min: float | None = None
    pe_5y_avg: float | None = None

    # ── 舆情安全网（实时新闻 + 8-K，禁止缓存）──
    recent_news_block: str = ""

    # ── 政要巨鳄雷达（议员交易 + 13F，周缓存匹配）──
    whale_alert_block: str = ""
    whale_alert_brief: str = ""  # 简报前置一行，如 [🚨 政要异动] ...


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
    quick_peg: float | None = None  # 粗略股息修正 PEG（非厂商 PEG TTM）
    debt_ratio: float | None = None  # 长期负债/股东权益（小数）；金融股可空并豁免
    net_cash_per_share: float | None = None
    net_cash_ratio: float | None = None  # 每股净现金/股价
    is_priority: bool = False  # 是否来自"必看列表"（watchlist）

    # ── Phase 1/2 多通道漏斗字段 ──
    sector: str | None = None
    industry: str | None = None
    is_financial: bool = False
    is_cyclical: bool = False
    inventory_growth: float | None = None  # 存货 YoY（小数）
    sales_growth: float | None = None  # 营收 YoY（小数）
    asset_play_hint: bool = False  # 净现金通道放行时打标
    pass_channels: tuple[str, ...] = ()  # peg / cyclical / net_cash / stalwart / slow_div / turnaround

    dividend_yield: float | None = None  # 百分比
    pe_5y_avg: float | None = None
    payout_ratio: float | None = None  # 小数 0~1+
    fcf_positive: bool | None = None
    ltd_yoy: float | None = None  # 长期债同比（小数）
    net_cash_yoy: float | None = None  # 净现金同比（相对变化）
    coarse_class: str | None = None  # 粗分类主类（不含隐蔽资产主类）
    turnaround_hint: bool = False



class BaseDataProvider(ABC):
    """数据源抽象基类。子类只需实现 3 个抽象方法。"""

    name: str = "base"

    def __init__(self) -> None:
        self._cache: dict[str, Fundamentals] = {}

    # ── 子类必须实现 ──────────────────────────────────────────
    @abstractmethod
    def _fetch_fundamentals(self, ticker: str, *, mode: str = "weekly") -> Fundamentals:
        """按报告周期抓取基本面（颗粒度因 mode 而异）。"""

    @abstractmethod
    def get_quick_screen(self, ticker: str) -> QuickScreen | None:
        """第一层漏斗用的廉价粗筛快照；失败返回 None。"""

    @abstractmethod
    def get_stock_price_change(self, ticker: str, period: str = "5d") -> float | None:
        """区间涨跌幅（小数，0.05 = +5%）；用于每日股价异动监控。"""

    @abstractmethod
    def get_daily_price_change(self, ticker: str) -> float | None:
        """最近一个交易日相对前一交易日的涨跌幅（小数，-0.05 = 单日跌 5%）。"""

    # ── 通用（带缓存，键含 report_mode）────────────────────────
    def get_fundamentals(self, ticker: str, *, mode: str = "weekly") -> Fundamentals:
        key = f"{ticker.upper()}::{mode}"
        if key not in self._cache:
            self._cache[key] = self._fetch_fundamentals(ticker, mode=mode)
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


def financial_from_labels(sector: str | None, industry: str | None) -> bool:
    """金融业（银行/保险等）——负债天生极高，漏斗与排雷均豁免负债门。"""
    if (sector or "") == "Financial Services":
        return True
    ind = industry or ""
    return any(h in ind for h in _FINANCIAL_HINTS)


def cyclical_from_labels(sector: str | None, industry: str | None) -> bool:
    """周期股粗判——漏斗底部旁路与深度豁免共用。"""
    if financial_from_labels(sector, industry):
        return False
    sec = sector or ""
    ind = industry or ""
    return sec in _CYCLICAL_SECTORS or any(h in ind for h in _CYCLICAL_HINTS)


def is_financial(f: "Fundamentals") -> bool:
    """金融业（银行/保险等）——负债天生极高，需豁免负债排雷。"""
    return financial_from_labels(f.sector, f.industry)


def is_cyclical(f: "Fundamentals") -> bool:
    """周期股——高 P/E/亏损/短期利润暴跌往往是底部，需反向判定、豁免常规排雷。"""
    return cyclical_from_labels(f.sector, f.industry)


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
    """林奇六大类粗分：周期 > 困境反转 > 增速带（快/慢/稳）；隐蔽资产仅作附加提示不抢主类。

    LLM 周报会做权威复核。净现金厚 → 调用方可用 asset_play_hint，不再覆盖主类。
    """
    growth = _cagr(f.eps_series)
    if growth is None:
        growth = f.earnings_growth_yoy
    div = f.dividend_yield or 0.0  # 已是百分比

    # 1) 周期型：硬行业字典最高优先级
    if cyclical_from_labels(f.sector, f.industry):
        return "周期型"

    # 2) 困境反转型：利润衰退且仍有长期债压顶（趋势细筛在漏斗通道 F）
    if growth is not None and growth < 0 and (f.long_term_debt or 0) > 0:
        return "困境反转型"

    # 3) 按增速 / 股息切带
    if growth is not None and growth >= 0.20:
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
) -> str:
    """漏斗轻量粗分类（与 classify_company 同优先级，不依赖完整 Fundamentals）。"""
    if cyclical_from_labels(sector, industry):
        return "周期型"
    if growth is not None and growth < 0 and (long_term_debt or 0) > 0:
        return "困境反转型"
    div = dividend_yield_pct or 0.0
    if growth is not None and growth >= 0.20:
        return "快速增长型"
    if div >= 4.0 and (growth is None or growth < 0.08):
        return "缓慢增长型"
    return "稳定增长型"
