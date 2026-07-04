"""Yahoo Finance 数据源：按报告周期（mode）拉取不同颗粒度的财务与市场数据。"""

from __future__ import annotations

import dataclasses
import random
import time
from typing import Any

import pandas as pd
import yfinance as yf

from ..config import correct_ticker
from ..metrics import check_sbi_tradable
from ..report_modes import normalize_mode
from ..temporal import (
    build_temporal_anchor,
    dividend_adjusted_peg,
    format_temporal_block,
    implied_eps_ttm,
    pe_range_5y,
)
from .base import BaseDataProvider, Fundamentals, FundamentalsError, QuickScreen
from .granularity import (
    format_annual_block,
    format_monthly_block,
    format_quarterly_block,
)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_INFO_RETRIES = 3
_session = None

_BUYBACK_ROWS = (
    "Repurchase Of Capital Stock",
    "Common Stock Repurchased",
    "Repurchase of Stock",
)
_DIVIDEND_ROWS = (
    "Common Stock Dividend Paid",
    "Cash Dividends Paid",
    "Payment Of Dividends",
    "Dividends Paid",
)


def _get_session():
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


def _period_label(ts: object) -> str:
    t = pd.Timestamp(ts)
    q = (t.month - 1) // 3 + 1
    return f"{t.year}-Q{q}"


def _row_series_annual(df: pd.DataFrame | None, *names: str) -> dict[int, float]:
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


def _row_series_period(df: pd.DataFrame | None, *names: str) -> dict[str, float]:
    """季度/月度列 → {period_label: value}，列按时间升序。"""
    if df is None or df.empty:
        return {}
    cols = sorted(df.columns, key=lambda c: pd.Timestamp(c))
    index = set(df.index.astype(str))
    for name in names:
        if name not in index:
            continue
        out: dict[str, float] = {}
        for col in cols:
            val = df.loc[name, col]
            if pd.isna(val):
                continue
            try:
                out[_period_label(col)] = float(val)
            except Exception:  # noqa: BLE001
                continue
        if out:
            return out
    return {}


def _latest_annual(df: pd.DataFrame | None, *names: str) -> float | None:
    series = _row_series_annual(df, *names)
    return series[max(series)] if series else None


def _latest_year_value(df: pd.DataFrame | None, row_names: tuple[str, ...]) -> float | None:
    if df is None or df.empty:
        return None
    cols = sorted(df.columns, key=lambda c: pd.Timestamp(c))
    if not cols:
        return None
    latest_col = cols[-1]
    index = set(df.index.astype(str))
    for name in row_names:
        if name in index:
            val = df.loc[name, latest_col]
            if pd.isna(val):
                continue
            try:
                return abs(float(val))  # 回购/股息现金流常为负，取绝对值
            except Exception:  # noqa: BLE001
                continue
    return None


def _pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old)


def _compute_rsi(closes: pd.Series, period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    if pd.isna(avg_loss.iloc[-1]) or avg_loss.iloc[-1] == 0:
        return 100.0 if avg_gain.iloc[-1] and avg_gain.iloc[-1] > 0 else 50.0
    rs = avg_gain.iloc[-1] / avg_loss.iloc[-1]
    return float(100 - (100 / (1 + rs)))


def _price_momentum(tk: yf.Ticker) -> tuple[float | None, float | None]:
    """近20交易日涨跌幅、RSI(14)。"""
    try:
        hist = tk.history(period="3mo", interval="1d", auto_adjust=False)
    except Exception:  # noqa: BLE001
        return None, None
    col = "Close" if "Close" in hist.columns else ("Adj Close" if "Adj Close" in hist.columns else None)
    if col is None:
        return None, None
    closes = hist[col].dropna()
    if len(closes) < 2:
        return None, None
    n = min(20, len(closes) - 1)
    change_20d = float(closes.iloc[-1] / closes.iloc[-1 - n] - 1.0)
    rsi = _compute_rsi(closes)
    return change_20d, rsi


def _gross_margin_by_period(
    income_q: pd.DataFrame | None,
) -> dict[str, float]:
    if income_q is None or income_q.empty:
        return {}
    rev = _row_series_period(income_q, "Total Revenue", "Operating Revenue")
    gp = _row_series_period(income_q, "Gross Profit")
    out: dict[str, float] = {}
    for p, r in rev.items():
        g = gp.get(p)
        if g is not None and r and r != 0:
            out[p] = g / r
    return out


def _build_quarterly_granularity(tk: yf.Ticker, currency: str | None) -> tuple[str, dict[str, Any]]:
    try:
        income_q = tk.quarterly_income_stmt
        balance_q = tk.quarterly_balance_sheet
    except Exception:  # noqa: BLE001
        income_q = balance_q = None

    revenue = _row_series_period(income_q, "Total Revenue", "Operating Revenue")
    net_income = _row_series_period(
        income_q, "Net Income", "Net Income Common Stockholders",
    )
    inventory = _row_series_period(balance_q, "Inventory")
    gross_margin = _gross_margin_by_period(income_q)

    periods = sorted(set(revenue) | set(net_income) | set(inventory))
    qoq: dict[str, float | None] = {}
    yoy_q: dict[str, float | None] = {}

    if len(periods) >= 2:
        p0, p1 = periods[-2], periods[-1]
        qoq["revenue"] = _pct_change(revenue.get(p1), revenue.get(p0))
        qoq["net_income"] = _pct_change(net_income.get(p1), net_income.get(p0))
        qoq["inventory"] = _pct_change(inventory.get(p1), inventory.get(p0))
        gm0, gm1 = gross_margin.get(p0), gross_margin.get(p1)
        qoq["gross_margin"] = (gm1 - gm0) if gm0 is not None and gm1 is not None else None

    if len(periods) >= 5:
        p_old, p_new = periods[-5], periods[-1]
        yoy_q["revenue"] = _pct_change(revenue.get(p_new), revenue.get(p_old))
        yoy_q["net_income"] = _pct_change(net_income.get(p_new), net_income.get(p_old))
        yoy_q["inventory"] = _pct_change(inventory.get(p_new), inventory.get(p_old))

    block, raw = format_quarterly_block(
        periods=periods,
        revenue=revenue,
        net_income=net_income,
        inventory=inventory,
        gross_margin=gross_margin,
        qoq=qoq,
        yoy_q=yoy_q,
        currency=currency,
    )
    return block, raw


def _build_annual_granularity(
    tk: yf.Ticker,
    income: pd.DataFrame | None,
    balance: pd.DataFrame | None,
    cash: pd.DataFrame | None,
    currency: str | None,
    *,
    pe_5y_min: float | None = None,
    pe_5y_avg: float | None = None,
    spot_pe: float | None = None,
) -> tuple[str, dict[str, Any]]:
    revenue_series = _row_series_annual(income, "Total Revenue", "Operating Revenue")
    net_income_series = _row_series_annual(
        income, "Net Income", "Net Income Common Stockholders",
    )
    gross_profit = _row_series_annual(income, "Gross Profit")
    gross_margin_series: dict[int, float] = {}
    for y, rev in revenue_series.items():
        gp = gross_profit.get(y)
        if gp is not None and rev:
            gross_margin_series[y] = gp / rev

    equity_series = _row_series_annual(balance, "Stockholders Equity", "Common Stock Equity")
    roic_proxy: dict[int, float] = {}
    for y, ni in net_income_series.items():
        eq = equity_series.get(y)
        if eq and eq > 0:
            roic_proxy[y] = ni / eq

    buyback = _latest_year_value(cash, _BUYBACK_ROWS)
    dividend = _latest_year_value(cash, _DIVIDEND_ROWS)
    span = len(revenue_series) if revenue_series else 0

    block, raw = format_annual_block(
        revenue_series=revenue_series,
        net_income_series=net_income_series,
        gross_margin_series=gross_margin_series,
        buyback_latest_year=buyback,
        dividend_paid_latest_year=dividend,
        roic_proxy_series=roic_proxy,
        currency=currency,
        span_years=span,
        pe_5y_min=pe_5y_min,
        pe_5y_avg=pe_5y_avg,
        spot_pe=spot_pe,
    )
    return block, raw


def _build_monthly_granularity(
    tk: yf.Ticker,
    f: Fundamentals,
    peg_now: float | None,
    peg_prior: float | None,
    currency: str | None,
) -> tuple[str, dict[str, Any]]:
    change_20d, rsi = _price_momentum(tk)
    peg_delta = _pct_change(peg_now, peg_prior) if peg_prior else None
    if f.peg_1mo_ago is not None and peg_now is not None:
        peg_delta = _pct_change(peg_now, f.peg_1mo_ago)
    return format_monthly_block(
        change_20d=change_20d,
        rsi_14=rsi,
        peg_now=peg_now,
        peg_prior=peg_prior,
        peg_delta=peg_delta,
        price_now=f.spot_price or f.price,
        price_1mo_ago=f.price_1mo_ago,
        pe_now=f.spot_pe or f.trailing_pe,
        pe_1mo_ago=f.pe_1mo_ago,
        peg_1mo_ago=f.peg_1mo_ago,
        currency=currency,
    )


def _base_fundamentals_from_info(
    ticker: str,
    info: dict,
    income: pd.DataFrame | None,
    balance: pd.DataFrame | None,
    cash: pd.DataFrame | None,
    *,
    mode: str,
    source: str,
) -> Fundamentals:
    spot = info.get("regularMarketPrice") or info.get("currentPrice")
    return Fundamentals(
        ticker=ticker.upper(),
        name=info.get("longName") or info.get("shortName"),
        sector=info.get("sector"),
        industry=info.get("industry"),
        currency=info.get("currency"),
        price=spot,
        spot_price=spot,
        spot_pe=info.get("trailingPE"),
        market_cap=info.get("marketCap"),
        exchange=info.get("exchange"),
        trailing_pe=info.get("trailingPE"),
        forward_pe=info.get("forwardPE"),
        earnings_growth_yoy=info.get("earningsGrowth"),
        revenue_growth_yoy=info.get("revenueGrowth"),
        eps_series=_row_series_annual(income, "Diluted EPS", "Basic EPS"),
        net_income_series=_row_series_annual(
            income, "Net Income", "Net Income Common Stockholders",
        ),
        revenue_series=_row_series_annual(income, "Total Revenue", "Operating Revenue"),
        inventory_series=_row_series_annual(balance, "Inventory"),
        long_term_debt=_latest_annual(balance, "Long Term Debt") or info.get("longTermDebt"),
        total_debt=info.get("totalDebt") or _latest_annual(balance, "Total Debt"),
        stockholders_equity=_latest_annual(
            balance, "Stockholders Equity", "Common Stock Equity",
        ),
        total_assets=_latest_annual(balance, "Total Assets") or info.get("totalAssets"),
        total_cash=info.get("totalCash") or _latest_annual(
            balance, "Cash And Cash Equivalents",
        ),
        cash_per_share=info.get("totalCashPerShare"),
        free_cashflow=info.get("freeCashflow") or _latest_annual(cash, "Free Cash Flow"),
        operating_cashflow=info.get("operatingCashflow") or _latest_annual(
            cash, "Operating Cash Flow",
        ),
        capital_expenditure=_latest_annual(cash, "Capital Expenditure"),
        shares_outstanding=info.get("sharesOutstanding"),
        dividend_yield=info.get("dividendYield"),
        held_percent_institutions=info.get("heldPercentInstitutions"),
        source=source,
        report_mode=mode,
    )


def _finalize_with_temporal(
    f: Fundamentals,
    tk: yf.Ticker,
    *,
    mode: str,
    extra_granularity: str = "",
) -> Fundamentals:
    """注入时间轴字段 + 拼接 granularity_block。"""
    from ..history import load_record_near_days_ago
    from ..metrics import compute_metrics, _pick_growth

    anchor = build_temporal_anchor(tk, f, mode=mode)
    temporal = {
        k: v for k, v in anchor.items()
        if k in Fundamentals.__dataclass_fields__
    }
    f = dataclasses.replace(f, **temporal)

    blocks: list[str] = []
    growth, _ = _pick_growth(f)

    if mode == "monthly":
        peg_now = compute_metrics(f).peg
        prior_rec = load_record_near_days_ago(f.ticker, days=30)
        peg_prior = prior_rec.peg if prior_rec else f.peg_1mo_ago
        monthly_block, _ = _build_monthly_granularity(
            tk, f, peg_now, peg_prior, f.currency,
        )
        blocks.append(monthly_block)
    elif mode == "quarterly":
        q_block, _ = _build_quarterly_granularity(tk, f.currency)
        blocks.append(q_block)
    elif mode == "annual":
        try:
            income = tk.income_stmt
            balance = tk.balance_sheet
            cash = tk.cashflow
        except Exception:  # noqa: BLE001
            income = balance = cash = None
        a_block, _ = _build_annual_granularity(
            tk, income, balance, cash, f.currency,
            pe_5y_min=f.pe_5y_min,
            pe_5y_avg=f.pe_5y_avg,
            spot_pe=f.spot_pe,
        )
        blocks.append(a_block)

    if mode in ("quarterly", "annual", "monthly"):
        blocks.append(format_temporal_block(f))

    if extra_granularity:
        blocks.insert(0, extra_granularity)

    gran = "\n\n".join(b for b in blocks if b)
    return dataclasses.replace(f, granularity_block=gran)


class YahooFinanceProvider(BaseDataProvider):
    name = "yahoo (yfinance)"

    def _fetch_fundamentals(self, ticker: str, *, mode: str = "weekly") -> Fundamentals:
        mode = normalize_mode(mode)
        ticker = correct_ticker(ticker)
        info = _fetch_info(ticker)
        tk = _ticker(ticker)

        try:
            income = tk.income_stmt
            balance = tk.balance_sheet
            cash = tk.cashflow
        except Exception:  # noqa: BLE001
            income = balance = cash = None

        base = _base_fundamentals_from_info(
            ticker, info, income, balance, cash, mode=mode, source=self.name,
        )
        return _finalize_with_temporal(base, tk, mode=mode)

    def get_intraday_snapshot(self, ticker: str) -> dict[str, Any]:
        """盘中价量快照：regularMarketPrice、previousClose、即时跌幅、即时 PEG 估算。"""
        ticker = correct_ticker(ticker)
        info = _fetch_info(ticker)
        tk = _ticker(ticker)
        spot = info.get("regularMarketPrice") or info.get("currentPrice")
        prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
        intraday_chg = None
        if spot and prev_close and prev_close > 0:
            intraday_chg = float(spot / prev_close - 1.0)

        try:
            income = tk.income_stmt
        except Exception:  # noqa: BLE001
            income = None

        base = _base_fundamentals_from_info(
            ticker, info, income, None, None, mode="daily", source=self.name,
        )
        from ..metrics import _pick_growth

        growth, _ = _pick_growth(base)
        pe = info.get("trailingPE")
        instant_peg = dividend_adjusted_peg(pe, growth, base.dividend_yield)
        eps = implied_eps_ttm(spot, pe)
        pe_min, _ = pe_range_5y(tk, eps)

        return {
            "ticker": ticker.upper(),
            "name": base.name,
            "spot": spot,
            "previous_close": prev_close,
            "intraday_change": intraday_chg,
            "instant_peg": instant_peg,
            "trailing_pe": pe,
            "pe_5y_min": pe_min,
            "sbi_tradable": check_sbi_tradable(
                ticker, exchange=base.exchange, market_cap=base.market_cap,
            ),
            "currency": base.currency,
        }

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
        d2e = info.get("debtToEquity")

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

    def get_daily_price_change(self, ticker: str) -> float | None:
        ticker = correct_ticker(ticker)
        try:
            hist = _ticker(ticker).history(period="5d", interval="1d", auto_adjust=False)
        except Exception:  # noqa: BLE001
            return None
        col = "Close" if "Close" in hist.columns else ("Adj Close" if "Adj Close" in hist.columns else None)
        if col is None:
            return None
        closes = hist[col].dropna()
        if len(closes) < 2:
            return None
        return float(closes.iloc[-1] / closes.iloc[-2] - 1.0)
