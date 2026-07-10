"""多维时间轴：历史价格锚点、估值对齐（杜绝现价评判旧财报）。"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .data.base import Fundamentals


def _close_col(df: pd.DataFrame) -> str | None:
    if "Close" in df.columns:
        return "Close"
    if "Adj Close" in df.columns:
        return "Adj Close"
    return None


def _align_datetime_index(idx, ts: pd.Timestamp) -> tuple[pd.DatetimeIndex, pd.Timestamp]:
    """Normalize index/timestamp so comparisons work across tz-aware (e.g. .T) and naive series."""
    idx = pd.DatetimeIndex(pd.to_datetime(idx))
    ts = pd.Timestamp(ts)
    if idx.tz is not None:
        ts = ts.tz_localize(idx.tz) if ts.tz is None else ts.tz_convert(idx.tz)
    elif ts.tz is not None:
        idx = idx.tz_localize(ts.tz)
    return idx, ts


def implied_eps_ttm(price: float | None, trailing_pe: float | None) -> float | None:
    """由现价与 TTM P/E 反推 TTM EPS（用于历史价估值对齐）。"""
    if price and trailing_pe and trailing_pe > 0:
        return price / trailing_pe
    return None


def pe_at_price(price: float | None, eps_ttm: float | None) -> float | None:
    if price and eps_ttm and eps_ttm > 0:
        return price / eps_ttm
    return None


def dividend_adjusted_peg(
    pe: float | None,
    growth_rate: float | None,
    dividend_yield_pct: float | None,
    *,
    growth_cap: float = 0.35,
    growth_cap_trigger: float = 0.50,
) -> float | None:
    """股息修正 PEG = P/E ÷ (CAGR% + 股息率%)。"""
    if pe is None or pe <= 0 or growth_rate is None or growth_rate <= 0:
        return None
    div = dividend_yield_pct or 0.0
    capped = growth_cap if growth_rate > growth_cap_trigger else growth_rate
    denom = capped * 100 + div
    if denom <= 0:
        return None
    return pe / denom


def price_on_date(hist: pd.DataFrame, target: pd.Timestamp) -> float | None:
    """取目标日当日或之前最近一根 K 线的收盘价。"""
    col = _close_col(hist)
    if col is None or hist.empty:
        return None
    idx = pd.DatetimeIndex(pd.to_datetime(hist.index))
    target, _ = _align_datetime_index(idx, target)
    mask = idx <= target
    if not mask.any():
        return None
    return float(hist.loc[mask, col].iloc[-1])


def avg_close_after_date(
    hist: pd.DataFrame,
    as_of: pd.Timestamp,
    trading_days: int = 3,
) -> float | None:
    """财报截止日后 N 个交易日内收盘价均值（价格锚点）。"""
    col = _close_col(hist)
    if col is None or hist.empty:
        return None
    idx = pd.DatetimeIndex(pd.to_datetime(hist.index))
    idx, as_of = _align_datetime_index(idx, as_of)
    after = hist.copy()
    after.index = idx
    after = after[idx >= as_of]
    if after.empty:
        return None
    closes = after[col].dropna().head(trading_days)
    if closes.empty:
        return None
    return float(closes.mean())


def fetch_history(tk: Any, *, period: str = "5y", interval: str = "1d") -> pd.DataFrame:
    try:
        return tk.history(period=period, interval=interval, auto_adjust=False)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def price_one_month_ago(tk: Any) -> float | None:
    hist = fetch_history(tk, period="3mo", interval="1d")
    if hist.empty:
        return None
    col = _close_col(hist)
    if col is None:
        return None
    closes = hist[col].dropna()
    if len(closes) < 2:
        return None
    target = closes.index[-1] - pd.Timedelta(days=30)
    return price_on_date(hist, pd.Timestamp(target))


def latest_statement_end(
    df: pd.DataFrame | None,
) -> pd.Timestamp | None:
    if df is None or df.empty:
        return None
    try:
        latest = sorted(df.columns, key=lambda c: pd.Timestamp(c))[-1]
        return pd.Timestamp(latest)
    except Exception:  # noqa: BLE001
        return None


def pe_range_5y(
    tk: Any,
    eps_ttm: float | None,
    *,
    interval: str = "1mo",
) -> tuple[float | None, float | None]:
    """返回 (5年最低隐含P/E, 5年平均隐含P/E)，基于月度收盘与当前 TTM EPS 代理。"""
    if eps_ttm is None or eps_ttm <= 0:
        return None, None
    hist = fetch_history(tk, period="5y", interval=interval)
    col = _close_col(hist)
    if col is None or hist.empty:
        return None, None
    closes = hist[col].dropna()
    if closes.empty:
        return None, None
    pes = closes / eps_ttm
    pes = pes[pes > 0]
    if pes.empty:
        return None, None
    return float(pes.min()), float(pes.mean())


def build_temporal_anchor(
    tk: Any,
    f: Fundamentals,
    *,
    mode: str,
) -> dict[str, Any]:
    """按报告模式计算时空对齐字段，写入 Fundamentals 扩展属性。"""
    spot = f.price
    spot_pe = f.trailing_pe
    eps = implied_eps_ttm(spot, spot_pe) or (
        f.eps_series[max(f.eps_series)] if f.eps_series else None
    )
    div = f.dividend_yield

    out: dict[str, Any] = {
        "spot_price": spot,
        "spot_pe": spot_pe,
        "valuation_anchor_date": None,
        "valuation_anchor_price": None,
        "valuation_pe": None,
        "price_1mo_ago": None,
        "pe_1mo_ago": None,
        "peg_1mo_ago": None,
        "pe_5y_min": None,
        "pe_5y_avg": None,
    }

    pe_min, pe_avg = pe_range_5y(tk, eps)
    out["pe_5y_min"] = pe_min
    out["pe_5y_avg"] = pe_avg

    if mode == "monthly":
        p1m = price_one_month_ago(tk)
        out["price_1mo_ago"] = p1m
        pe1m = pe_at_price(p1m, eps)
        out["pe_1mo_ago"] = pe1m
        out["peg_1mo_ago"] = dividend_adjusted_peg(
            pe1m, _growth_from_f(f), div,
        )

    if mode in ("quarterly", "annual"):
        income = None
        try:
            income = (
                tk.quarterly_income_stmt if mode == "quarterly" else tk.income_stmt
            )
        except Exception:  # noqa: BLE001
            income = None
        as_of = latest_statement_end(income)
        if as_of is not None:
            hist = fetch_history(tk, period="2y", interval="1d")
            anchor_px = avg_close_after_date(hist, as_of, trading_days=3)
            out["valuation_anchor_date"] = as_of.strftime("%Y-%m-%d")
            out["valuation_anchor_price"] = anchor_px
            if anchor_px and eps:
                out["valuation_pe"] = pe_at_price(anchor_px, eps)

    return out


def _growth_from_f(f: Fundamentals) -> float | None:
    from .metrics import _pick_growth

    g, _ = _pick_growth(f)
    return g


def format_temporal_block(f: Fundamentals) -> str:
    """生成时空对齐说明区块（注入 granularity）。"""
    lines = ["【多维时间轴 · 估值锚点对齐】"]
    spot = f.spot_price if f.spot_price is not None else f.price
    cur = f.currency or ""
    lines.append(f"- 即时现价（spot）: {spot} {cur}")
    if f.spot_pe is not None:
        lines.append(f"- 即时 TTM P/E: {f.spot_pe:.2f}")

    if f.valuation_anchor_date and f.valuation_anchor_price is not None:
        lines.append(
            f"- 财报锚定日 {f.valuation_anchor_date} 后3日均收: "
            f"{f.valuation_anchor_price:.2f} {cur}"
        )
        if f.valuation_pe is not None:
            lines.append(f"- 财报锚定日隐含 P/E: {f.valuation_pe:.2f}")
        if spot and f.valuation_anchor_price:
            drift = (spot - f.valuation_anchor_price) / f.valuation_anchor_price
            lines.append(f"- 自锚定日以来股价漂移: {drift * 100:+.1f}%")
        lines.append(
            "⚠️ 季报/年报会诊：必须用「财报锚定价」评判该期利润是否已被股价透支；"
            "禁止仅用即时现价误判强买。"
        )

    if f.price_1mo_ago is not None:
        lines.append(f"- 约1个月前收盘价: {f.price_1mo_ago:.2f} {cur}")
        if f.pe_1mo_ago is not None:
            lines.append(f"- 约1个月前隐含 P/E: {f.pe_1mo_ago:.2f}")
        if f.peg_1mo_ago is not None:
            lines.append(f"- 约1个月前股息修正 PEG: {f.peg_1mo_ago:.2f}")

    if f.pe_5y_min is not None:
        lines.append(f"- 5年历史最低隐含 P/E: {f.pe_5y_min:.2f}")
    if f.pe_5y_avg is not None:
        lines.append(f"- 5年历史平均隐含 P/E: {f.pe_5y_avg:.2f}")
    if f.pe_5y_avg is not None and f.spot_pe is not None:
        if f.spot_pe < f.pe_5y_avg * 0.85:
            lines.append("- 当前 P/E 低于 5 年均值约 15%+ → 可能处于历史低估带")
        elif f.spot_pe > f.pe_5y_avg * 1.25:
            lines.append("- 当前 P/E 显著高于 5 年均值 → 警惕估值透支")

    return "\n".join(lines)
