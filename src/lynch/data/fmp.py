"""Financial Modeling Prep 数据源：静态强缓存 + 实时舆情/8-K + 政要巨鳄雷达。"""

from __future__ import annotations

import dataclasses
import logging
import time
from datetime import date
from typing import Any

import pandas as pd
import requests

from ..config import FMP_API_KEY, FMP_REQUEST_INTERVAL, correct_ticker
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
from .fmp_whale import analyze_whale_signals, get_institutional_snapshots
from .granularity import format_annual_block, format_monthly_block, format_quarterly_block

log = logging.getLogger(__name__)

_STABLE_BASE = "https://financialmodelingprep.com/stable"
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
    """FMP Stable API 客户端（legacy v3 已停用）。"""

    def __init__(self) -> None:
        self.budget = FmpApiBudget()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "LynchStockMonitor/1.0"})
        self._last_request_at = 0.0

    def _throttle(self) -> None:
        gap = FMP_REQUEST_INTERVAL - (time.monotonic() - self._last_request_at)
        if gap > 0:
            time.sleep(gap)

    def get(self, endpoint: str, params: dict | None = None, *, optional: bool = False) -> Any:
        _require_api_key()
        path = endpoint.strip("/")
        q = dict(params or {})
        q["apikey"] = FMP_API_KEY
        url = f"{_STABLE_BASE}/{path}"
        last_exc: Exception | None = None

        for attempt in range(4):
            self.budget.check()
            self._throttle()
            try:
                resp = self._session.get(url, params=q, timeout=30)
            except requests.RequestException as exc:
                raise FundamentalsError(f"FMP 请求失败 {path}: {exc}") from exc
            finally:
                self._last_request_at = time.monotonic()

            if resp.status_code == 429:
                wait = min(60, 2 ** attempt * 5)
                log.warning("FMP 429 限流 %s，%ss 后重试 (%s/4)", path, wait, attempt + 1)
                time.sleep(wait)
                last_exc = FundamentalsError(f"FMP 限流 {path}: 429 Too Many Requests")
                continue

            if resp.status_code in (400, 402, 403, 404):
                if optional:
                    log.debug("FMP optional miss %s (%s): %s", path, resp.status_code, resp.text[:80])
                    self.budget.increment()
                    return []
                raise FundamentalsError(
                    f"FMP {path} 不可用 (HTTP {resp.status_code}): {resp.text[:200]}"
                )

            try:
                resp.raise_for_status()
            except requests.HTTPError as exc:
                raise FundamentalsError(f"FMP 请求失败 {path}: {exc}") from exc

            self.budget.increment()
            if not resp.text or resp.text.strip() in ("", "[]"):
                return []
            try:
                return resp.json()
            except ValueError as exc:
                raise FundamentalsError(f"FMP 返回非 JSON: {path}") from exc

        if last_exc:
            raise last_exc
        raise FundamentalsError(f"FMP 请求失败 {path}: 重试耗尽")


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


def _df_to_fmp_rows(
    df: pd.DataFrame | None,
    mapping: dict[str, str],
) -> list[dict]:
    """Yahoo 宽表 → FMP 纵向 records（date + fields）。"""
    if df is None or df.empty:
        return []
    rows: list[dict] = []
    cols = sorted(df.columns, key=lambda c: pd.Timestamp(c))
    index = {str(i) for i in df.index}
    for col in cols:
        row: dict[str, Any] = {"date": pd.Timestamp(col).strftime("%Y-%m-%d")}
        for src, dst in mapping.items():
            if src in index:
                val = df.loc[src, col]
                if pd.notna(val):
                    try:
                        row[dst] = float(val)
                    except (TypeError, ValueError):
                        pass
        if len(row) > 1:
            rows.append(row)
    return rows


def _yahoo_fill_bundle(sym: str, bundle: dict[str, Any]) -> dict[str, Any]:
    """免费档部分 ticker 财报/历史价 402 时，用 Yahoo 补齐静态包（仍走本地缓存）。"""
    need = (
        not bundle.get("income_annual")
        or not bundle.get("balance_annual")
        or not bundle.get("cash_annual")
        or not bundle.get("historical")
    )
    if not need:
        return bundle
    log.info("FMP stable 财报受限，Yahoo 回退补齐: %s", sym)
    from .yahoo import _ticker

    tk = _ticker(sym)
    try:
        income = tk.income_stmt
        balance = tk.balance_sheet
        cash = tk.cashflow
        income_q = tk.quarterly_income_stmt
        balance_q = tk.quarterly_balance_sheet
    except Exception:  # noqa: BLE001
        income = balance = cash = income_q = balance_q = None

    inc_map = {
        "Total Revenue": "revenue",
        "Operating Revenue": "revenue",
        "Net Income": "netIncome",
        "Net Income Common Stockholders": "netIncome",
        "Gross Profit": "grossProfit",
        "Diluted EPS": "epsdiluted",
        "Basic EPS": "eps",
    }
    bal_map = {
        "Inventory": "inventory",
        "Long Term Debt": "longTermDebt",
        "Total Debt": "totalDebt",
        "Stockholders Equity": "totalStockholdersEquity",
        "Common Stock Equity": "totalStockholdersEquity",
        "Total Assets": "totalAssets",
        "Cash And Cash Equivalents": "cashAndCashEquivalents",
    }
    cash_map = {
        "Free Cash Flow": "freeCashFlow",
        "Operating Cash Flow": "operatingCashFlow",
        "Capital Expenditure": "capitalExpenditure",
        "Common Stock Repurchased": "commonStockRepurchased",
        "Repurchase Of Capital Stock": "commonStockRepurchased",
        "Common Stock Dividend Paid": "dividendsPaid",
        "Cash Dividends Paid": "dividendsPaid",
    }

    if not bundle.get("income_annual"):
        bundle["income_annual"] = _df_to_fmp_rows(income, inc_map)
    if not bundle.get("income_quarterly"):
        bundle["income_quarterly"] = _df_to_fmp_rows(income_q, inc_map)
    if not bundle.get("balance_annual"):
        bundle["balance_annual"] = _df_to_fmp_rows(balance, bal_map)
    if not bundle.get("balance_quarterly"):
        bundle["balance_quarterly"] = _df_to_fmp_rows(balance_q, bal_map)
    if not bundle.get("cash_annual"):
        bundle["cash_annual"] = _df_to_fmp_rows(cash, cash_map)

    if not bundle.get("historical"):
        try:
            hist = tk.history(period="2y", interval="1d", auto_adjust=False)
            col = "Close" if "Close" in hist.columns else "Adj Close"
            if col in hist.columns:
                bundle["historical"] = [
                    {"date": idx.strftime("%Y-%m-%d"), "close": float(val)}
                    for idx, val in hist[col].dropna().items()
                ][-_HIST_TIMESERIES:]
        except Exception:  # noqa: BLE001
            bundle["historical"] = []

    bundle["statement_source"] = bundle.get("statement_source") or "yahoo_fallback"
    return bundle


def _fetch_static_bundle(ticker: str) -> dict[str, Any]:
    """拉取并持久化低频静态财报包（stable API；受限标的 Yahoo 回退）。"""
    sym = correct_ticker(ticker)
    api = _api()
    bundle: dict[str, Any] = {
        "profile": _first_row(api.get("profile", {"symbol": sym})),
        "statement_source": "fmp_stable",
    }
    stmt_params = {"symbol": sym}
    bundle["income_annual"] = _first_list(
        api.get(
            "income-statement",
            {**stmt_params, "period": "annual", "limit": _STATEMENT_LIMIT},
            optional=True,
        ),
    )
    bundle["income_quarterly"] = _first_list(
        api.get(
            "income-statement",
            {**stmt_params, "period": "quarter", "limit": _QUARTERLY_LIMIT},
            optional=True,
        ),
    )
    bundle["balance_annual"] = _first_list(
        api.get(
            "balance-sheet-statement",
            {**stmt_params, "period": "annual", "limit": _STATEMENT_LIMIT},
            optional=True,
        ),
    )
    bundle["cash_annual"] = _first_list(
        api.get(
            "cash-flow-statement",
            {**stmt_params, "period": "annual", "limit": _STATEMENT_LIMIT},
            optional=True,
        ),
    )
    bundle["balance_quarterly"] = _first_list(
        api.get(
            "balance-sheet-statement",
            {**stmt_params, "period": "quarter", "limit": _QUARTERLY_LIMIT},
            optional=True,
        ),
    )
    hist = _first_list(
        api.get("historical-price-eod/full", {**stmt_params}, optional=True),
    )
    bundle["historical"] = hist[-_HIST_TIMESERIES:] if hist else []

    bundle = _yahoo_fill_bundle(sym, bundle)
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
    """实时报价 — 禁止缓存。免费档 quote 受限时回退 profile（含 price/change）。"""
    sym = correct_ticker(ticker)
    api = _api()
    profile = _first_row(api.get("profile", {"symbol": sym}))
    quote = _first_row(api.get("quote", {"symbol": sym}, optional=True))
    row: dict[str, Any] = dict(profile)
    for k, v in quote.items():
        if v is not None:
            row[k] = v
    price = row.get("price")
    change = row.get("change")
    if row.get("previousClose") is None and price is not None and change is not None:
        try:
            row["previousClose"] = float(price) - float(change)
        except (TypeError, ValueError):
            pass
    return row


def _fetch_stock_news(ticker: str, *, limit: int = 5) -> list[dict]:
    """实时新闻 — 禁止缓存。受限时返回空列表（由 Yahoo 回退拼装）。"""
    sym = correct_ticker(ticker)
    data = _api().get("news/stock", {"symbols": sym, "limit": limit}, optional=True)
    return _first_list(data)[:limit]


def _fetch_8k_filings(ticker: str, *, limit: int = 5) -> list[dict]:
    """SEC 8-K 披露 — 禁止缓存。"""
    sym = correct_ticker(ticker)
    data = _first_list(_api().get("sec-filings-8k", {"symbol": sym}, optional=True))
    matched = [f for f in data if str(f.get("symbol") or "").upper() == sym.upper()]
    return (matched or data)[:limit]


def _yahoo_news_block(ticker: str) -> str:
    from .yahoo import _fetch_recent_news_block, _ticker

    return _fetch_recent_news_block(_ticker(correct_ticker(ticker)))


def _build_sensitive_intel_block(ticker: str) -> str:
    """高敏黑天鹅天线：FMP stable 新闻 + 8-K；新闻受限时 Yahoo 回退。"""
    sym = correct_ticker(ticker)
    header = "【最新市场舆情监控（过去数日核心头条）】"
    lines = [header]
    news = _fetch_stock_news(sym)
    if news:
        for item in news:
            title = str(item.get("title") or item.get("text") or "").strip()
            pub = str(item.get("site") or item.get("publisher") or "未知").strip()
            if title:
                lines.append(f"- {title} (来源: {pub})")
    else:
        yahoo_block = _yahoo_news_block(sym)
        if yahoo_block and "无有效标题" not in yahoo_block and "暂无" not in yahoo_block:
            return yahoo_block
        lines.append("- （暂无可用新闻 feed）")

    filings = _fetch_8k_filings(sym)
    if filings:
        lines.append("")
        lines.append("【SEC 8-K 重大事件披露（实时，禁止缓存）】")
        for f in filings:
            title = str(f.get("title") or f.get("form") or "8-K 披露").strip()
            dt = str(
                f.get("filingDate") or f.get("acceptedDate") or f.get("date") or "?"
            )[:10]
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
    mcap = quote.get("marketCap") or profile.get("marketCap") or profile.get("mktCap")
    shares = profile.get("sharesOutstanding") or _latest_annual(
        income_a, "weightedAverageShsOutDil",
    ) or _latest_annual(income_a, "weightedAverageShsOut")

    if pe is None and price:
        eps_ttm = _latest_annual(income_a, "epsdiluted") or _latest_annual(income_a, "eps")
        if eps_ttm and eps_ttm > 0:
            try:
                pe = float(price) / float(eps_ttm)
            except (TypeError, ValueError):
                pe = None

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

    stmt_src = bundle.get("statement_source") or "fmp_stable"
    source = "fmp stable (Financial Modeling Prep)"
    if stmt_src == "yahoo_fallback":
        source = "fmp stable + yahoo 财报回退"

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
        source=source,
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


def _fetch_symbol_politician_trades(api: _FmpClient, sym: str) -> list[dict]:
    """stable API 要求按 symbol 查询议员交易（全局 RSS 已不可用）。"""
    senate = _first_list(api.get("senate-trades", {"symbol": sym, "limit": 50}, optional=True)) or []
    house = _first_list(api.get("house-trades", {"symbol": sym, "limit": 50}, optional=True)) or []
    for row in senate:
        row["_chamber"] = "senate"
    for row in house:
        row["_chamber"] = "house"
    return senate + house


def _whale_intel(ticker: str) -> tuple[str, str]:
    api = _api()
    sym = correct_ticker(ticker)

    def fetch_dates(cik: str):
        return _first_list(
            api.get("institutional-ownership/portfolio-date", {"cik": cik}, optional=True),
        )

    def fetch_portfolio(cik: str, dt: str):
        return _first_list(
            api.get(
                "institutional-ownership/portfolio-holdings",
                {"cik": cik, "date": dt, "page": 0},
                optional=True,
            ),
        )

    trades = _fetch_symbol_politician_trades(api, sym)
    inst = get_institutional_snapshots(fetch_dates, fetch_portfolio)
    return analyze_whale_signals(sym, trades, inst)


def _cagr_from_total_growth(total: float | None, years: float) -> float | None:
    """将 N 年累计每股增长还原为近似 CAGR（小数）。"""
    if total is None or years <= 0:
        return None
    try:
        t = float(total)
    except (TypeError, ValueError):
        return None
    base = 1.0 + t
    if base <= 0:
        return None
    return base ** (1.0 / years) - 1.0


def _coarse_growth_from_fg(fg: dict) -> float | None:
    """优先 5y / 3y 每股净利累计增长 → CAGR；再退回最近一年 eps 增速。"""
    for key, yrs in (("fiveYNetIncomeGrowthPerShare", 5.0), ("threeYNetIncomeGrowthPerShare", 3.0)):
        cagr = _cagr_from_total_growth(fg.get(key), yrs)
        if cagr is not None and cagr > 0:
            return cagr
    for key in ("epsdilutedGrowth", "epsgrowth", "netIncomeGrowth"):
        v = fg.get(key)
        if v is not None:
            try:
                g = float(v)
            except (TypeError, ValueError):
                continue
            if g > 0:
                return g
    return None


def _div_yield_pct(profile: dict, price: float | None) -> float | None:
    """股息率（百分比）。优先 lastDiv/price，其次 profile.dividendYield。"""
    last_div = profile.get("lastDiv")
    if last_div is not None and price and price > 0:
        try:
            return float(last_div) / float(price) * 100.0
        except (TypeError, ValueError):
            pass
    dy = profile.get("dividendYield")
    if dy is None:
        return None
    try:
        v = float(dy)
    except (TypeError, ValueError):
        return None
    return v * 100.0 if v <= 1.0 else v


def _ltd_equity_from_ratios(ratios: dict) -> float | None:
    """长期负债/资本近似。"""
    for key in ("longTermDebtToCapitalRatioTTM", "longTermDebtToEquityRatioTTM"):
        v = ratios.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def _fetch_latest_balance(api: _FmpClient, sym: str) -> dict:
    rows = _first_list(
        api.get(
            "balance-sheet-statement",
            {"symbol": sym, "period": "annual", "limit": 1},
            optional=True,
        ),
    )
    return rows[0] if rows else {}


def _net_cash_from_balance(
    bal: dict,
    *,
    price: float | None,
    shares: float | None,
) -> tuple[float | None, float | None]:
    cash = bal.get("cashAndCashEquivalents")
    if cash is None:
        cash = bal.get("cashAndShortTermInvestments")
    debt = bal.get("totalDebt")
    sh = shares
    if cash is None or not sh or sh <= 0:
        return None, None
    try:
        net_ps = (float(cash) - float(debt or 0.0)) / float(sh)
    except (TypeError, ValueError):
        return None, None
    ratio = (net_ps / price) if price and price > 0 else None
    return net_ps, ratio


def _ltd_equity_from_balance(bal: dict) -> float | None:
    ltd = bal.get("longTermDebt")
    eq = bal.get("totalStockholdersEquity") or bal.get("totalEquity")
    if ltd is None or eq is None:
        return None
    try:
        eq_f = float(eq)
        if eq_f <= 0:
            return None
        return float(ltd) / eq_f
    except (TypeError, ValueError):
        return None


def _augment_quick_screen_from_cache(q: QuickScreen, bundle: dict) -> QuickScreen:
    """用已缓存的资产负债表补充净现金 / LTD（不覆盖已有有效值）。"""
    balance_a = bundle.get("balance_annual") or []
    profile = bundle.get("profile") or {}
    if not balance_a:
        return q
    bal = balance_a[-1] if isinstance(balance_a[-1], dict) else {}
    price = q.price or profile.get("price")
    shares = profile.get("sharesOutstanding")
    updates: dict[str, Any] = {}
    if q.net_cash_ratio is None:
        net_ps, ratio = _net_cash_from_balance(bal, price=price, shares=shares)
        if net_ps is not None:
            updates["net_cash_per_share"] = net_ps
        if ratio is not None:
            updates["net_cash_ratio"] = ratio
    if q.debt_ratio is None and not q.is_financial:
        ltd = _ltd_equity_from_balance(bal)
        if ltd is not None:
            updates["debt_ratio"] = ltd
    return dataclasses.replace(q, **updates) if updates else q


def _light_quick_screen(sym: str) -> QuickScreen | None:
    """漏斗粗筛 Phase1：profile + ratios-ttm + financial-growth；按需拉 balance。

    - quick_peg = 粗略股息修正 PEG（废弃厂商 priceToEarningsGrowthRatioTTM）
    - debt_ratio = 长期负债/权益（近似）
    - PEG/周期未放行时再拉 balance 抢救净现金通道
    """
    from .. import config
    from .base import cyclical_from_labels, financial_from_labels

    api = _api()
    profile = _first_row(api.get("profile", {"symbol": sym}))
    if not profile:
        return None
    ratios = _first_row(api.get("ratios-ttm", {"symbol": sym}, optional=True)) or {}
    quote = _first_row(api.get("quote", {"symbol": sym}, optional=True)) or {}
    fg = _first_row(
        api.get(
            "financial-growth",
            {"symbol": sym, "period": "annual", "limit": 1},
            optional=True,
        ),
    ) or {}

    price = quote.get("price") or profile.get("price")
    try:
        price_f = float(price) if price is not None else None
    except (TypeError, ValueError):
        price_f = None

    pe = quote.get("pe") or ratios.get("priceToEarningsRatioTTM")
    try:
        pe_f = float(pe) if pe is not None else None
    except (TypeError, ValueError):
        pe_f = None

    growth = _coarse_growth_from_fg(fg)
    div_pct = _div_yield_pct(profile, price_f)
    coarse_peg = dividend_adjusted_peg(pe_f, growth, div_pct)

    mcap = quote.get("marketCap") or profile.get("mktCap") or profile.get("marketCap")
    exchange = profile.get("exchangeShortName") or profile.get("exchange")
    sector = profile.get("sector")
    industry = profile.get("industry")
    fin = financial_from_labels(sector, industry)
    cyc = cyclical_from_labels(sector, industry)

    debt_ratio = None if fin else _ltd_equity_from_ratios(ratios)

    inv_g = fg.get("inventoryGrowth")
    sales_g = fg.get("revenueGrowth")
    try:
        inv_f = float(inv_g) if inv_g is not None else None
    except (TypeError, ValueError):
        inv_f = None
    try:
        sales_f = float(sales_g) if sales_g is not None else None
    except (TypeError, ValueError):
        sales_f = None

    shares = profile.get("sharesOutstanding")
    try:
        shares_f = float(shares) if shares is not None else None
    except (TypeError, ValueError):
        shares_f = None

    peg_ok = coarse_peg is not None and 0 < coarse_peg <= config.FUNNEL_MAX_PEG
    cyclical_candidate = cyc and (coarse_peg is None or pe_f is None or pe_f <= 0)
    cyclical_inventory_ok = inv_f is None or sales_f is None or inv_f <= sales_f
    cyclical_ok = cyclical_candidate and cyclical_inventory_ok

    net_cash_ps = None
    net_cash_ratio = None
    if not peg_ok and not cyclical_ok:
        bal = _fetch_latest_balance(api, sym)
        if bal:
            if shares_f is None:
                # profile 无股本时尝试用 quote；balance 通常无 sharesOutstanding
                try:
                    shares_f = float(quote["sharesOutstanding"]) if quote.get("sharesOutstanding") else None
                except (TypeError, ValueError, KeyError):
                    shares_f = None
            net_cash_ps, net_cash_ratio = _net_cash_from_balance(
                bal, price=price_f, shares=shares_f,
            )
            if debt_ratio is None and not fin:
                debt_ratio = _ltd_equity_from_balance(bal)

    return QuickScreen(
        ticker=sym,
        name=profile.get("companyName"),
        price=price_f,
        market_cap=mcap,
        exchange=exchange,
        sbi_tradable=check_sbi_tradable(sym, exchange=exchange, market_cap=mcap),
        trailing_pe=pe_f,
        growth_yoy=growth,
        quick_peg=coarse_peg,
        debt_ratio=debt_ratio,
        net_cash_per_share=net_cash_ps,
        net_cash_ratio=net_cash_ratio,
        sector=sector,
        industry=industry,
        is_financial=fin,
        is_cyclical=cyc,
        inventory_growth=inv_f,
        sales_growth=sales_f,
    )



class FmpProvider(BaseDataProvider):
    name = "fmp stable (Financial Modeling Prep)"

    def _fetch_fundamentals(self, ticker: str, *, mode: str = "weekly") -> Fundamentals:
        mode = normalize_mode(mode)
        sym = correct_ticker(ticker)
        bundle = _get_static_bundle(sym)
        if not bundle.get("profile"):
            raise FundamentalsError(f"{sym}: FMP 无 profile 数据（可能为非美股代码）")

        quote = _fetch_quote(sym)
        base = _build_fundamentals_from_bundle(sym, bundle, quote, mode=mode)

        news_block = _build_sensitive_intel_block(sym)
        try:
            whale_brief, whale_block = _whale_intel(sym)
        except Exception as exc:  # noqa: BLE001
            log.warning("巨鳄雷达跳过 %s: %s", sym, exc)
            whale_brief, whale_block = "", ""

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
            q = _light_quick_screen(sym)
            if q is None:
                return None
            bundle = _get_static_bundle(sym, allow_refresh=False)
            return _augment_quick_screen_from_cache(q, bundle)
        except (FundamentalsError, RuntimeError):
            return None

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
        if DAILY_QUOTA <= 0:
            return f"FMP 今日 API 用量: {b.count()}（Starter 无日封顶，限速 300/分钟）"
        return f"FMP 今日 API 用量: {b.count()}/{DAILY_QUOTA}"
