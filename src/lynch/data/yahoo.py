"""Yahoo Finance 数据源实现（yfinance）。

选用理由：免费、覆盖美股 + 日股，已验证林奇 SOP 所需字段齐全。
"""

from __future__ import annotations

import pandas as pd
import yfinance as yf

from ..config import correct_ticker
from .base import BaseDataProvider, Fundamentals, FundamentalsError, QuickScreen


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
        tk = yf.Ticker(ticker)
        try:
            info = tk.info or {}
        except Exception as exc:  # noqa: BLE001
            raise FundamentalsError(f"{ticker}: 无法获取 info ({exc})") from exc

        if not info or (info.get("regularMarketPrice") is None and not info.get("longName")):
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
            net_income_series=_row_series(income, "Net Income", "Net Income Common Stockholders"),
            revenue_series=_row_series(income, "Total Revenue", "Operating Revenue"),
            inventory_series=_row_series(balance, "Inventory"),
            long_term_debt=_latest(balance, "Long Term Debt") or info.get("longTermDebt"),
            total_debt=info.get("totalDebt") or _latest(balance, "Total Debt"),
            stockholders_equity=_latest(balance, "Stockholders Equity", "Common Stock Equity"),
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
            info = yf.Ticker(ticker).info or {}
        except Exception:  # noqa: BLE001
            return None
        if not info:
            return None

        price = info.get("currentPrice") or info.get("regularMarketPrice")
        pe = info.get("trailingPE")
        growth = info.get("earningsGrowth")
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
            hist = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
        except Exception:  # noqa: BLE001
            return None
        col = "Close" if "Close" in hist.columns else ("Adj Close" if "Adj Close" in hist.columns else None)
        if col is None:
            return None
        closes = hist[col].dropna()
        if len(closes) < 2:
            return None
        return float(closes.iloc[-1] / closes.iloc[0] - 1.0)
