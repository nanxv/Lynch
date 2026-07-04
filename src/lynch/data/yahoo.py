"""Yahoo Finance 数据源实现（yfinance）。

选用理由：免费、覆盖美股 + 日股，已验证林奇 SOP 所需字段齐全。
"""

from __future__ import annotations

import random
import time

import pandas as pd
import yfinance as yf

from ..config import correct_ticker
from ..metrics import check_sbi_tradable
from .base import BaseDataProvider, Fundamentals, FundamentalsError, QuickScreen

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_INFO_RETRIES = 3
_session = None


def _get_session():
    """浏览器伪装会话：优先 curl_cffi(impersonate)，否则退回带 UA 的 requests。

    云端(数据中心 IP)访问 Yahoo 极易被反爬限流，浏览器指纹能显著降低被封概率。
    """
    global _session
    if _session is not None:
        return _session
    try:
        from curl_cffi import requests as cffi_requests

        _session = cffi_requests.Session(impersonate="chrome")
    except Exception:  # noqa: BLE001
        import requests

        _session = requests.Session()
        _session.headers.update({"User-Agent": _UA})
    return _session


def _ticker(ticker: str) -> yf.Ticker:
    try:
        return yf.Ticker(ticker, session=_get_session())
    except TypeError:
        return yf.Ticker(ticker)


def _fetch_info(ticker: str) -> dict:
    """带退避重试地获取 .info；连续失败/空数据则抛 FundamentalsError。"""
    last: object = "unknown"
    for attempt in range(_INFO_RETRIES):
        try:
            info = _ticker(ticker).info or {}
            if info and (info.get("regularMarketPrice") is not None or info.get("longName")):
                return info
            last = "空数据(可能被限流)"
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(1.5 * (attempt + 1) + random.random())
    raise FundamentalsError(f"{ticker}: 无法获取 info（{last}）")


def _row_series(df: pd.DataFrame | None, *names: str) -> dict[int, float]:
    if df is None or df.empty:
        return {}
    index = set(df.index.astype(str))
    for name in names:
        if name in index:
            out: dict[int, float] = {}
            for col, val in df.loc[name].items():
                if pd.isna(val):
                    continue
                try:
                    out[int(pd.Timestamp(col).year)] = float(val)
                except Exception:  # noqa: BLE001
                    continue
            return dict(sorted(out.items()))
    return {}


def _latest(df: pd.DataFrame | None, *names: str) -> float | None:
    series = _row_series(df, *names)
    return series[max(series)] if series else None


class YahooFinanceProvider(BaseDataProvider):
    name = "yahoo (yfinance)"

    def _fetch_fundamentals(self, ticker: str) -> Fundamentals:
        ticker = correct_ticker(ticker)
        info = _fetch_info(ticker)
        tk = _ticker(ticker)

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
            exchange=info.get("exchange"),
            trailing_pe=info.get("trailingPE"),
            forward_pe=info.get("forwardPE"),
            earnings_growth_yoy=info.get("earningsGrowth"),
            revenue_growth_yoy=info.get("revenueGrowth"),
            eps_series=_row_series(income, "Diluted EPS", "Basic EPS"),
            net_income_series=_row_series(income, "Net Income", "Net Income Common Stockholders"),
            revenue_series=_row_series(income, "Total Revenue", "Operating Revenue"),
            inventory_series=_row_series(balance, "Inventory"),
            long_term_debt=_latest(balance, "Long Term Debt") or info.get("longTermDebt"),
            total_debt=info.get("totalDebt") or _latest(balance, "Total Debt"),
            stockholders_equity=_latest(balance, "Stockholders Equity", "Common Stock Equity"),
            total_assets=_latest(balance, "Total Assets") or info.get("totalAssets"),
            total_cash=info.get("totalCash") or _latest(balance, "Cash And Cash Equivalents"),
            cash_per_share=info.get("totalCashPerShare"),
            free_cashflow=info.get("freeCashflow") or _latest(cash, "Free Cash Flow"),
            operating_cashflow=info.get("operatingCashflow") or _latest(cash, "Operating Cash Flow"),
            capital_expenditure=_latest(cash, "Capital Expenditure"),
            shares_outstanding=info.get("sharesOutstanding"),
            dividend_yield=info.get("dividendYield"),
            held_percent_institutions=info.get("heldPercentInstitutions"),
            source=self.name,
        )

    def get_quick_screen(self, ticker: str) -> QuickScreen | None:
        ticker = correct_ticker(ticker)
        try:
            info = _fetch_info(ticker)
        except FundamentalsError:
            return None
        if not info:
            return None

        price = info.get("currentPrice") or info.get("regularMarketPrice")
        pe = info.get("trailingPE")
        growth = info.get("earningsGrowth")
        mcap = info.get("marketCap")
        exchange = info.get("exchange")
        cash = info.get("totalCash")
        debt = info.get("totalDebt")
        shares = info.get("sharesOutstanding")
        d2e = info.get("debtToEquity")  # 百分比，如 6.0 表示 6%

        quick_peg = None
        if pe and pe > 0 and growth and growth > 0:
            quick_peg = pe / (growth * 100)

        net_cash_ps = None
        net_cash_ratio = None
        if cash is not None and shares:
            net_cash_ps = (cash - (debt or 0.0)) / shares
            if price and price > 0:
                net_cash_ratio = net_cash_ps / price

        return QuickScreen(
            ticker=ticker.upper(),
            name=info.get("shortName") or info.get("longName"),
            price=price,
            market_cap=mcap,
            exchange=exchange,
            sbi_tradable=check_sbi_tradable(ticker, exchange=exchange, market_cap=mcap),
            trailing_pe=pe,
            growth_yoy=growth,
            quick_peg=quick_peg,
            debt_ratio=(d2e / 100.0) if d2e is not None else None,
            net_cash_per_share=net_cash_ps,
            net_cash_ratio=net_cash_ratio,
        )

    def get_stock_price_change(self, ticker: str, period: str = "5d") -> float | None:
        ticker = correct_ticker(ticker)
        try:
            hist = _ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
        except Exception:  # noqa: BLE001
            return None
        col = "Close" if "Close" in hist.columns else ("Adj Close" if "Adj Close" in hist.columns else None)
        if col is None:
            return None
        closes = hist[col].dropna()
        if len(closes) < 2:
            return None
        return float(closes.iloc[-1] / closes.iloc[0] - 1.0)
