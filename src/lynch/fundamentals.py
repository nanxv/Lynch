"""基本面数据层：通过 yfinance (Yahoo Finance) 抓取林奇 SOP 所需的全部字段。

选用 Yahoo Finance 的理由：免费、覆盖美股 + 日股（Alpha Vantage / Finnhub / FMP
的免费档要么仅限美股、要么把国际/日股放在付费墙后），且已是本项目依赖。
数据源经 `Provider` 抽象封装，日后可平滑替换为付费 API（FMP / EODHD 等）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import yfinance as yf


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
    # info 提供的同比增长（小数，0.20 = 20%）
    earnings_growth_yoy: float | None
    revenue_growth_yoy: float | None

    # 财报多年序列 {年份: 数值}，年份升序由调用方处理
    eps_series: dict[int, float] = field(default_factory=dict)
    net_income_series: dict[int, float] = field(default_factory=dict)
    revenue_series: dict[int, float] = field(default_factory=dict)
    inventory_series: dict[int, float] = field(default_factory=dict)

    long_term_debt: float | None = None
    total_debt: float | None = None
    stockholders_equity: float | None = None
    total_cash: float | None = None
    cash_per_share: float | None = None

    free_cashflow: float | None = None
    operating_cashflow: float | None = None
    capital_expenditure: float | None = None

    shares_outstanding: float | None = None
    dividend_yield: float | None = None
    held_percent_institutions: float | None = None

    source: str = "yahoo (yfinance)"


def _row_series(df: pd.DataFrame | None, *names: str) -> dict[int, float]:
    """从财报 DataFrame 中取一行，返回 {年份: 值}，按年份升序。"""
    if df is None or df.empty:
        return {}
    index = df.index.astype(str)
    for name in names:
        if name in set(index):
            row = df.loc[name]
            out: dict[int, float] = {}
            for col, val in row.items():
                if pd.isna(val):
                    continue
                try:
                    year = pd.Timestamp(col).year
                except Exception:  # noqa: BLE001
                    continue
                out[int(year)] = float(val)
            return dict(sorted(out.items()))
    return {}


def _latest(df: pd.DataFrame | None, *names: str) -> float | None:
    series = _row_series(df, *names)
    if not series:
        return None
    return series[max(series)]


class YahooProvider:
    """默认基本面数据源：Yahoo Finance。"""

    name = "yahoo (yfinance)"

    def fetch(self, ticker: str) -> Fundamentals:
        tk = yf.Ticker(ticker)
        try:
            info = tk.info or {}
        except Exception as exc:  # noqa: BLE001
            raise FundamentalsError(f"{ticker}: 无法获取 info ({exc})") from exc

        if not info or info.get("regularMarketPrice") is None and not info.get("longName"):
            raise FundamentalsError(f"{ticker}: Yahoo 返回空数据，代码可能有误")

        try:
            income = tk.income_stmt
            balance = tk.balance_sheet
            cash = tk.cashflow
        except Exception:  # noqa: BLE001
            income = balance = cash = None

        return Fundamentals(
            ticker=ticker.upper(),
            name=info.get("longName") or info.get("shortName"),
            sector=info.get("sector"),
            industry=info.get("industry"),
            currency=info.get("currency"),
            price=info.get("currentPrice") or info.get("regularMarketPrice"),
            market_cap=info.get("marketCap"),
            trailing_pe=info.get("trailingPE"),
            forward_pe=info.get("forwardPE"),
            earnings_growth_yoy=info.get("earningsGrowth"),
            revenue_growth_yoy=info.get("revenueGrowth"),
            eps_series=_row_series(income, "Diluted EPS", "Basic EPS"),
            net_income_series=_row_series(
                income, "Net Income", "Net Income Common Stockholders"
            ),
            revenue_series=_row_series(income, "Total Revenue", "Operating Revenue"),
            inventory_series=_row_series(balance, "Inventory"),
            long_term_debt=_latest(balance, "Long Term Debt")
            or info.get("longTermDebt"),
            total_debt=info.get("totalDebt") or _latest(balance, "Total Debt"),
            stockholders_equity=_latest(
                balance, "Stockholders Equity", "Common Stock Equity"
            ),
            total_cash=info.get("totalCash")
            or _latest(balance, "Cash And Cash Equivalents"),
            cash_per_share=info.get("totalCashPerShare"),
            free_cashflow=info.get("freeCashflow") or _latest(cash, "Free Cash Flow"),
            operating_cashflow=info.get("operatingCashflow")
            or _latest(cash, "Operating Cash Flow"),
            capital_expenditure=_latest(cash, "Capital Expenditure"),
            shares_outstanding=info.get("sharesOutstanding"),
            dividend_yield=info.get("dividendYield"),
            held_percent_institutions=info.get("heldPercentInstitutions"),
            source=self.name,
        )


_DEFAULT_PROVIDER = YahooProvider()


def fetch_fundamentals(ticker: str, provider: YahooProvider | None = None) -> Fundamentals:
    """抓取单只股票的基本面快照。"""
    return (provider or _DEFAULT_PROVIDER).fetch(ticker)
