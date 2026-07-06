"""Financial Modeling Prep 数据源：静态强缓存 + 实时舆情/8-K + 政要巨鳄雷达。"""

from __future__ import annotations

import dataclasses
import logging
from datetime import date
from typing import Any

import pandas as pd
import requests

from ..config import FMP_API_KEY, correct_ticker
from ..metrics import check_sbi_tradable
from ..report_modes import normalize_mode
from ..temporal import (
    dividend_adjusted_peg,
    format_temporal_block,
    implied_eps_ttm,
    pe_at_price,
)
from .base import BaseDataProvider, Fundamentals, FundamentalsError, QuickScreen
from .fmp_cache import (
    DAILY_QUOTA,
    FmpApiBudget,
    load_static_cache,
    needs_static_refresh,
    save_static_cache,
)
from .fmp_whale import analyze_whale_signals, get_institutional_snapshots, get_politician_trades
from .granularity import format_annual_block, format_monthly_block, format_quarterly_block

log = logging.getLogger(__name__)

_BASE = "https://financialmodelingprep.com/api"
_STATEMENT_LIMIT = 6
_QUARTERLY_LIMIT = 8
_HIST_TIMESERIES = 260


def _require_api_key() -> str:
    if not FMP_API_KEY:
        raise FundamentalsError(
            "未配置 FMP_API_KEY。请在 .env 或 GitHub Secrets 中设置后使用 DATA_PROVIDER=fmp。"
        )
    return FMP_API_KEY


class _FmpClient:
    def __init__(self) -> None:
        self.budget = FmpApiBudget()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "LynchStockMonitor/1.0"})

    def get(self, path: str, params: dict | None = None) -> Any:
        _require_api_key()
        self.budget.check()
        url = f"{_BASE}/{path.lstrip('/')}"
        q = dict(params or {})
        q["apikey"] = FMP_API_KEY
        try:
            resp = self._session.get(url, params=q, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise FundamentalsError(f"FMP 请求失败 {path}: {exc}") from exc
        self.budget.increment()
        if not resp.text or resp.text.strip() in ("", "[]"):
            return []
        try:
            return resp.json()
        except ValueError as exc:
            raise FundamentalsError(f"FMP 返回非 JSON: {path}") from exc


_client: _FmpClient | None = None


def _api() -> _FmpClient:
    global _client
    if _client is None:
        _client = _FmpClient()
    return _client


def _first_list(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _first_row(data: Any) -> dict:
    rows = _first_list(data)
    return rows[0] if rows else {}


def _annual_series(rows: list[dict], field: str) -> dict[int, float]:
    out: dict[int, float] = {}
    for r in rows:
        d, v = r.get("date"), r.get(field)
        if d is None or v is None:
            continue
        try:
            out[int(str(d)[:4])] = float(v)
        except (TypeError, ValueError):
            continue
    return dict(sorted(out.items()))


def _quarter_label(d: str) -> str:
    t = pd.Timestamp(d)
    q = (t.month - 1) // 3 + 1
    return f"{t.year}-Q{q}"


def _quarter_series(rows: list[dict], field: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in sorted(rows, key=lambda x: str(x.get("date") or "")):
        d, v = r.get("date"), r.get(field)
        if d is None or v is None:
            continue
        try:
            out[_quarter_label(str(d))] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def _pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old)


def _fetch_static_bundle(ticker: str) -> dict[str, Any]:
    """拉取并持久化低频静态财报包（仅在新窗口或缺失时调用）。"""
    sym = correct_ticker(ticker)
    bundle: dict[str, Any] = {}
    bundle["profile"] = _first_row(_api().get(f"v3/profile/{sym}"))
    bundle["income_annual"] = _first_list(
        _api().get(f"v3/income-statement/{sym}", {"period": "annual", "limit": _STATEMENT_LIMIT}),
    )
    bundle["income_quarterly"] = _first_list(
        _api().get(
            f"v3/income-statement/{sym}",
            {"period": "quarter", "limit": _QUARTERLY_LIMIT},
        ),
    )
    bundle["balance_annual"] = _first_list(
        _api().get(f"v3/balance-sheet-statement/{sym}", {"period": "annual", "limit": _STATEMENT_LIMIT}),
    )
    bundle["cash_annual"] = _first_list(
        _api().get(f"v3/cash-flow-statement/{sym}", {"period": "annual", "limit": _STATEMENT_LIMIT}),
    )
    bundle["balance_quarterly"] = _first_list(
        _api().get(
            f"v3/balance-sheet-statement/{sym}",
            {"period": "quarter", "limit": _QUARTERLY_LIMIT},
        ),
    )
    hist = _api().get(
        f"v3/historical-price-full/{sym}",
        {"serietype": "line", "timeseries": _HIST_TIMESERIES},
    )
    if isinstance(hist, dict):
        bundle["historical"] = hist.get("historical") or []
    else:
        bundle["historical"] = []
    save_static_cache(sym, bundle)
    return bundle


def _get_static_bundle(ticker: str, *, allow_refresh: bool = True) -> dict[str, Any]:
    sym = correct_ticker(ticker)
    if allow_refresh and needs_static_refresh(sym):
        log.info("FMP 刷新静态缓存: %s", sym)
        return _fetch_static_bundle(sym)
    cached = load_static_cache(sym)
    if cached:
        return cached
    if allow_refresh:
        return _fetch_static_bundle(sym)
    return {}


def _fetch_quote(ticker: str) -> dict:
    """实时报价 — 禁止缓存。"""
    return _first_row(_api().get(f"v3/quote/{correct_ticker(ticker)}"))


def _fetch_stock_news(ticker: str, *, limit: int = 5) -> list[dict]:
    """实时新闻 — 禁止缓存。"""
    sym = correct_ticker(ticker)
    data = _api().get("v3/stock_news", {"tickers": sym, "limit": limit})
    return _first_list(data)[:limit]


def _fetch_8k_filings(ticker: str, *, limit: int = 5) -> list[dict]:
    """SEC 8-K 披露 — 禁止缓存。"""
    sym = correct_ticker(ticker)
    data = _api().get(f"v3/sec_filings/{sym}", {"type": "8-K", "page": 0})
    return _first_list(data)[:limit]


def _build_sensitive_intel_block(ticker: str) -> str:
    """高敏黑天鹅天线：新闻 + 8-K，每次运行实时拉取。"""
    header = "【最新市场舆情监控（过去数日核心头条）】"
    lines = [header]
    news = _fetch_stock_news(ticker)
    if not news:
        lines.append("- （FMP 暂无最新新闻）")
    else:
        for item in news:
            title = str(item.get("title") or item.get("text") or "").strip()
            pub = str(item.get("site") or item.get("publisher") or "未知").strip()
            if title:
                lines.append(f"- {title} (来源: {pub})")

    filings = _fetch_8k_filings(ticker)
    if filings:
        lines.append("")
        lines.append("【SEC 8-K 重大事件披露（实时，禁止缓存）】")
        for f in filings:
            title = str(f.get("title") or f.get("type") or "8-K 披露").strip()
            dt = str(f.get("acceptedDate") or f.get("fillingDate") or f.get("date") or "?")[:10]
            lines.append(f"- {title} ({dt})")

    return "\n".join(lines)


def _latest_annual(rows: list[dict], field: str) -> float | None:
    series = _annual_series(rows, field)
    return series[max(series)] if series else None


def _growth_yoy(rows: list[dict], field: str) -> float | None:
    if len(rows) < 2:
        return None
    sorted_rows = sorted(rows, key=lambda r: str(r.get("date") or ""))
    cur, prev = sorted_rows[-1].get(field), sorted_rows[-2].get(field)
    return _pct_change(
        float(cur) if cur is not None else None,
        float(prev) if prev is not None else None,
    )


def _hist_df(historical: list[dict]) -> pd.DataFrame:
    if not historical:
        return pd.DataFrame()
    df = pd.DataFrame(historical)
    if "date" in df.columns and "close" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        return df
    return pd.DataFrame()


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


def _price_momentum(historical: list[dict]) -> tuple[float | None, float | None]:
    df = _hist_df(historical)
    if df.empty or "close" not in df.columns:
        return None, None
    closes = df["close"].dropna()
    if len(closes) < 2:
        return None, None
    n = min(20, len(closes) - 1)
    change_20d = float(closes.iloc[-1] / closes.iloc[-1 - n] - 1.0)
    return change_20d, _compute_rsi(closes)


def _price_on_date(historical: list[dict], target: pd.Timestamp) -> float | None:
    df = _hist_df(historical)
    if df.empty:
        return None
    mask = df.index <= target
    if not mask.any():
        return None
    return float(df.loc[mask, "close"].iloc[-1])


def _avg_close_after(historical: list[dict], as_of: pd.Timestamp, days: int = 3) -> float | None:
    df = _hist_df(historical)
    if df.empty:
        return None
    after = df[df.index >= as_of]
    if after.empty:
        return None
    closes = after["close"].dropna().head(days)
    if closes.empty:
        return None
    return float(closes.mean())


def _pe_range_from_hist(historical: list[dict], eps_ttm: float | None) -> tuple[float | None, float | None]:
    if eps_ttm is None or eps_ttm <= 0:
        return None, None
    df = _hist_df(historical)
    if df.empty:
        return None, None
    monthly = df["close"].resample("ME").last().dropna()
    if monthly.empty:
        return None, None
    pes = monthly / eps_ttm
    pes = pes[pes > 0]
    if pes.empty:
        return None, None
    return float(pes.min()), float(pes.mean())


def _build_quarterly_granularity(
    income_q: list[dict],
    balance_q: list[dict],
    currency: str | None,
) -> str:
    revenue = _quarter_series(income_q, "revenue")
    net_income = _quarter_series(income_q, "netIncome")
    inventory = _quarter_series(balance_q, "inventory")
    gross_profit = _quarter_series(income_q, "grossProfit")
    gross_margin: dict[str, float] = {}
    for p, rev in revenue.items():
        gp = gross_profit.get(p)
        if gp is not None and rev:
            gross_margin[p] = gp / rev

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

    block, _ = format_quarterly_block(
        periods=periods,
        revenue=revenue,
        net_income=net_income,
        inventory=inventory,
        gross_margin=gross_margin,
        qoq=qoq,
        yoy_q=yoy_q,
        currency=currency,
    )
    return block


def _build_annual_granularity(
    income: list[dict],
    balance: list[dict],
    cash: list[dict],
    currency: str | None,
    *,
    pe_5y_min: float | None,
    pe_5y_avg: float | None,
    spot_pe: float | None,
) -> str:
    revenue_series = _annual_series(income, "revenue")
    net_income_series = _annual_series(income, "netIncome")
    gross_profit = _annual_series(income, "grossProfit")
    gross_margin_series: dict[int, float] = {}
    for y, rev in revenue_series.items():
        gp = gross_profit.get(y)
        if gp is not None and rev:
            gross_margin_series[y] = gp / rev

    equity_series = _annual_series(balance, "totalStockholdersEquity")
    roic_proxy: dict[int, float] = {}
    for y, ni in net_income_series.items():
        eq = equity_series.get(y)
        if eq and eq > 0:
            roic_proxy[y] = ni / eq

    buyback = _latest_annual(cash, "commonStockRepurchased")
    dividend = _latest_annual(cash, "dividendsPaid")
    if buyback is not None:
        buyback = abs(buyback)
    if dividend is not None:
        dividend = abs(dividend)

    block, _ = format_annual_block(
        revenue_series=revenue_series,
        net_income_series=net_income_series,
        gross_margin_series=gross_margin_series,
        buyback_latest_year=buyback,
        dividend_paid_latest_year=dividend,
        roic_proxy_series=roic_proxy,
        currency=currency,
        span_years=len(revenue_series),
        pe_5y_min=pe_5y_min,
        pe_5y_avg=pe_5y_avg,
        spot_pe=spot_pe,
    )
    return block


def _build_fundamentals_from_bundle(
    ticker: str,
    bundle: dict[str, Any],
    quote: dict,
    *,
    mode: str,
) -> Fundamentals:
    sym = correct_ticker(ticker)
    profile = bundle.get("profile") or {}
    income_a = bundle.get("income_annual") or []
    income_q = bundle.get("income_quarterly") or []
    balance_a = bundle.get("balance_annual") or []
    cash_a = bundle.get("cash_annual") or []
    historical = bundle.get("historical") or []

    price = quote.get("price") or profile.get("price")
    pe = quote.get("pe") or profile.get("pe")
    mcap = quote.get("marketCap") or profile.get("mktCap")
    shares = profile.get("sharesOutstanding") or _latest_annual(
        income_a, "weightedAverageShsOutDil",
    )

    total_cash = _latest_annual(balance_a, "cashAndCashEquivalents")
    total_debt = _latest_annual(balance_a, "totalDebt")
    equity = _latest_annual(balance_a, "totalStockholdersEquity")

    cash_ps = None
    if total_cash is not None and shares:
        cash_ps = total_cash / shares

    div_yield = None
    if profile.get("lastDiv") and price and price > 0:
        try:
            div_yield = float(profile["lastDiv"]) / float(price) * 100
        except (TypeError, ValueError):
            pass

    eps_series = _annual_series(income_a, "epsdiluted") or _annual_series(income_a, "eps")

    return Fundamentals(
        ticker=sym,
        name=profile.get("companyName") or quote.get("name"),
        sector=profile.get("sector"),
        industry=profile.get("industry"),
        currency=profile.get("currency") or quote.get("currency"),
        price=price,
        spot_price=price,
        spot_pe=pe,
        market_cap=mcap,
        exchange=profile.get("exchangeShortName") or profile.get("exchange"),
        trailing_pe=pe,
        forward_pe=None,
        earnings_growth_yoy=_growth_yoy(income_a, "netIncome"),
        revenue_growth_yoy=_growth_yoy(income_a, "revenue"),
        eps_series=eps_series,
        net_income_series=_annual_series(income_a, "netIncome"),
        revenue_series=_annual_series(income_a, "revenue"),
        inventory_series=_annual_series(balance_a, "inventory"),
        long_term_debt=_latest_annual(balance_a, "longTermDebt"),
        total_debt=total_debt,
        stockholders_equity=equity,
        total_assets=_latest_annual(balance_a, "totalAssets"),
        total_cash=total_cash,
        cash_per_share=cash_ps,
        free_cashflow=_latest_annual(cash_a, "freeCashFlow"),
        operating_cashflow=_latest_annual(cash_a, "operatingCashFlow"),
        capital_expenditure=_latest_annual(cash_a, "capitalExpenditure"),
        shares_outstanding=shares,
        dividend_yield=div_yield,
        held_percent_institutions=None,
        source="fmp (Financial Modeling Prep)",
        report_mode=mode,
    )


def _finalize_fmp(
    f: Fundamentals,
    bundle: dict[str, Any],
    *,
    mode: str,
) -> Fundamentals:
    from ..history import load_record_near_days_ago
    from ..metrics import _pick_growth, compute_metrics

    historical = bundle.get("historical") or []
    income_a = bundle.get("income_annual") or []
    income_q = bundle.get("income_quarterly") or []
    balance_q = bundle.get("balance_quarterly") or []
    balance_a = bundle.get("balance_annual") or []
    cash_a = bundle.get("cash_annual") or []

    eps = implied_eps_ttm(f.spot_price, f.trailing_pe) or (
        f.eps_series[max(f.eps_series)] if f.eps_series else None
    )
    pe_min, pe_avg = _pe_range_from_hist(historical, eps)
    temporal: dict[str, Any] = {
        "pe_5y_min": pe_min,
        "pe_5y_avg": pe_avg,
    }

    growth, _ = _pick_growth(f)
    div = f.dividend_yield

    if mode == "monthly":
        target = pd.Timestamp(date.today()) - pd.Timedelta(days=30)
        p1m = _price_on_date(historical, target)
        temporal["price_1mo_ago"] = p1m
        pe1m = pe_at_price(p1m, eps)
        temporal["pe_1mo_ago"] = pe1m
        temporal["peg_1mo_ago"] = dividend_adjusted_peg(pe1m, growth, div)

    if mode in ("quarterly", "annual") and income_q:
        latest_date = sorted(income_q, key=lambda r: str(r.get("date") or ""))[-1].get("date")
        if latest_date:
            as_of = pd.Timestamp(str(latest_date))
            anchor_px = _avg_close_after(historical, as_of, days=3)
            temporal["valuation_anchor_date"] = str(latest_date)[:10]
            temporal["valuation_anchor_price"] = anchor_px
            if anchor_px and eps:
                temporal["valuation_pe"] = pe_at_price(anchor_px, eps)

    f = dataclasses.replace(f, **{k: v for k, v in temporal.items() if k in Fundamentals.__dataclass_fields__})

    blocks: list[str] = []
    if mode == "monthly":
        peg_now = compute_metrics(f).peg
        prior_rec = load_record_near_days_ago(f.ticker, days=30)
        peg_prior = prior_rec.peg if prior_rec else f.peg_1mo_ago
        change_20d, rsi = _price_momentum(historical)
        monthly_block, _ = format_monthly_block(
            change_20d=change_20d,
            rsi_14=rsi,
            peg_now=peg_now,
            peg_prior=peg_prior,
            peg_delta=_pct_change(peg_now, peg_prior),
            price_now=f.spot_price or f.price,
            price_1mo_ago=f.price_1mo_ago,
            pe_now=f.spot_pe or f.trailing_pe,
            pe_1mo_ago=f.pe_1mo_ago,
            peg_1mo_ago=f.peg_1mo_ago,
            currency=f.currency,
        )
        blocks.append(monthly_block)
    elif mode == "quarterly":
        blocks.append(_build_quarterly_granularity(income_q, balance_q, f.currency))
    elif mode == "annual":
        blocks.append(
            _build_annual_granularity(
                income_a, balance_a, cash_a, f.currency,
                pe_5y_min=f.pe_5y_min,
                pe_5y_avg=f.pe_5y_avg,
                spot_pe=f.spot_pe,
            ),
        )

    if mode in ("quarterly", "annual", "monthly"):
        blocks.append(format_temporal_block(f))

    gran = "\n\n".join(b for b in blocks if b)
    return dataclasses.replace(f, granularity_block=gran)


def _whale_intel(ticker: str) -> tuple[str, str]:
    api = _api()

    def fetch_senate():
        return _first_list(api.get("v4/senate-trading-rss-feed", {"page": 0}))

    def fetch_house():
        for path, params in (
            ("v4/house-trading-rss-feed", {"page": 0}),
            ("v4/house-trading", {}),
        ):
            try:
                rows = _first_list(api.get(path, params))
                if rows:
                    return rows
            except FundamentalsError:
                continue
        return []

    def fetch_dates(cik: str):
        return _first_list(api.get("v4/institutional-ownership/portfolio-date", {"cik": cik}))

    def fetch_portfolio(cik: str, dt: str):
        return _first_list(
            api.get(
                "v4/institutional-ownership/portfolio-holdings",
                {"cik": cik, "date": dt, "page": 0},
            ),
        )

    trades = get_politician_trades(fetch_senate, fetch_house)
    inst = get_institutional_snapshots(fetch_dates, fetch_portfolio)
    return analyze_whale_signals(ticker, trades, inst)


class FmpProvider(BaseDataProvider):
    name = "fmp (Financial Modeling Prep)"

    def _fetch_fundamentals(self, ticker: str, *, mode: str = "weekly") -> Fundamentals:
        mode = normalize_mode(mode)
        sym = correct_ticker(ticker)
        bundle = _get_static_bundle(sym)
        if not bundle.get("profile"):
            raise FundamentalsError(f"{sym}: FMP 无 profile 数据（可能为非美股代码）")

        quote = _fetch_quote(sym)
        base = _build_fundamentals_from_bundle(sym, bundle, quote, mode=mode)

        news_block = _build_sensitive_intel_block(sym)
        whale_brief, whale_block = _whale_intel(sym)

        base = dataclasses.replace(
            base,
            recent_news_block=news_block,
            whale_alert_block=whale_block,
            whale_alert_brief=whale_brief,
        )
        return _finalize_fmp(base, bundle, mode=mode)

    def get_intraday_snapshot(self, ticker: str) -> dict[str, Any]:
        sym = correct_ticker(ticker)
        quote = _fetch_quote(sym)
        bundle = _get_static_bundle(sym, allow_refresh=False)
        profile = bundle.get("profile") or {}
        base = _build_fundamentals_from_bundle(sym, bundle, quote, mode="daily")
        from ..metrics import _pick_growth

        growth, _ = _pick_growth(base)
        pe = quote.get("pe")
        spot = quote.get("price")
        prev = quote.get("previousClose")
        intraday_chg = None
        if spot and prev and prev > 0:
            intraday_chg = float(spot / prev - 1.0)
        instant_peg = dividend_adjusted_peg(pe, growth, base.dividend_yield)
        eps = implied_eps_ttm(spot, pe)
        pe_min, _ = _pe_range_from_hist(bundle.get("historical") or [], eps)

        return {
            "ticker": sym,
            "name": base.name,
            "spot": spot,
            "previous_close": prev,
            "intraday_change": intraday_chg,
            "instant_peg": instant_peg,
            "trailing_pe": pe,
            "pe_5y_min": pe_min,
            "sbi_tradable": check_sbi_tradable(
                sym, exchange=base.exchange, market_cap=base.market_cap,
            ),
            "currency": base.currency,
        }

    def get_quick_screen(self, ticker: str) -> QuickScreen | None:
        sym = correct_ticker(ticker)
        try:
            quote = _fetch_quote(sym)
            bundle = _get_static_bundle(sym, allow_refresh=False)
        except (FundamentalsError, RuntimeError):
            return None
        if not quote:
            return None

        profile = bundle.get("profile") or {}
        balance_a = bundle.get("balance_annual") or []
        income_a = bundle.get("income_annual") or []

        price = quote.get("price")
        pe = quote.get("pe")
        growth = _growth_yoy(income_a, "netIncome")
        mcap = quote.get("marketCap") or profile.get("mktCap")
        exchange = profile.get("exchangeShortName") or profile.get("exchange")

        cash = _latest_annual(balance_a, "cashAndCashEquivalents")
        debt = _latest_annual(balance_a, "totalDebt")
        equity = _latest_annual(balance_a, "totalStockholdersEquity")
        shares = profile.get("sharesOutstanding")

        quick_peg = None
        if pe and pe > 0 and growth and growth > 0:
            quick_peg = pe / (growth * 100)

        net_cash_ps = None
        net_cash_ratio = None
        if cash is not None and shares:
            net_cash_ps = (cash - (debt or 0.0)) / shares
            if price and price > 0:
                net_cash_ratio = net_cash_ps / price

        debt_ratio = None
        if debt is not None and equity and equity > 0:
            debt_ratio = debt / equity

        return QuickScreen(
            ticker=sym,
            name=profile.get("companyName"),
            price=price,
            market_cap=mcap,
            exchange=exchange,
            sbi_tradable=check_sbi_tradable(sym, exchange=exchange, market_cap=mcap),
            trailing_pe=pe,
            growth_yoy=growth,
            quick_peg=quick_peg,
            debt_ratio=debt_ratio,
            net_cash_per_share=net_cash_ps,
            net_cash_ratio=net_cash_ratio,
        )

    def get_stock_price_change(self, ticker: str, period: str = "5d") -> float | None:
        sym = correct_ticker(ticker)
        days = 5
        if period.endswith("d"):
            try:
                days = int(period[:-1])
            except ValueError:
                days = 5
        bundle = _get_static_bundle(sym, allow_refresh=False)
        df = _hist_df(bundle.get("historical") or [])
        if df.empty or len(df) < 2:
            return None
        n = min(days, len(df) - 1)
        closes = df["close"].dropna()
        if len(closes) < n + 1:
            return None
        return float(closes.iloc[-1] / closes.iloc[-1 - n] - 1.0)

    def get_daily_price_change(self, ticker: str) -> float | None:
        sym = correct_ticker(ticker)
        try:
            quote = _fetch_quote(sym)
        except (FundamentalsError, RuntimeError):
            return None
        spot = quote.get("price")
        prev = quote.get("previousClose")
        if spot and prev and prev > 0:
            return float(spot / prev - 1.0)
        return None

    def api_usage_summary(self) -> str:
        b = _api().budget
        return f"FMP 今日 API 用量: {b.count()}/{DAILY_QUOTA}"
