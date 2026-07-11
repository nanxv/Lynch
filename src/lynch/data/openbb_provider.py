"""OpenBB 渐进式外挂（Layer 3 专用 · 仅免费源）。

探针一律走 OpenBB v4 + 免费 provider（FRED / SEC EDGAR / yfinance）。
【严禁】在 Layer 1 漏斗或 Layer 2 Flash 扫射中调用本模块。
任一探针失败必须降级为「暂无 OpenBB 扩展数据」，不得拖垮流水线。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

NO_OPENBB_DATA = "暂无 OpenBB 扩展数据"
_MDA_MAX_CHARS = 2000

# 周期股 sector → FRED 序列 + 中文标签（免费 FRED；无 key 时回退 yfinance 期货）
_CYCLICAL_FRED: dict[str, tuple[str, str]] = {
    "energy": ("DCOILWTICO", "WTI 原油"),
    "basic materials": ("PCOPPUSDM", "铜价(全球)"),
    "materials": ("PCOPPUSDM", "铜价(全球)"),
    "industrials": ("PCOPPUSDM", "铜价(全球)"),
}
_CYCLICAL_YF_FALLBACK: dict[str, tuple[str, str]] = {
    "energy": ("CL=F", "WTI 原油期货"),
    "basic materials": ("HG=F", "铜期货"),
    "materials": ("HG=F", "铜期货"),
    "industrials": ("HG=F", "铜期货"),
}

_obb: Any | None = None
_obb_failed = False


def _get_obb() -> Any | None:
    """懒加载 OpenBB 客户端；未安装或初始化失败则永久跳过。"""
    global _obb, _obb_failed
    if _obb_failed:
        return None
    if _obb is not None:
        return _obb
    try:
        from openbb import obb  # type: ignore[import-untyped]

        _obb = obb
        return _obb
    except Exception as exc:  # noqa: BLE001
        _obb_failed = True
        logger.warning("OpenBB 不可用，外挂降级: %s", exc)
        return None


def _norm_sector(sector: str | None, industry: str | None) -> str:
    blob = f"{sector or ''} {industry or ''}".lower()
    if any(k in blob for k in ("energy", "oil", "gas", "能源", "石油")):
        return "energy"
    if any(k in blob for k in ("material", "mining", "copper", "metal", "材料", "矿业", "钢铁")):
        return "materials"
    if any(k in blob for k in ("industrial", "工业")):
        return "industrials"
    return (sector or "").strip().lower()


def _is_cyclical_context(
    sector: str | None,
    industry: str | None,
    *,
    company_type: str | None = None,
    is_cyclical: bool = False,
) -> bool:
    if is_cyclical or (company_type or "") == "周期型":
        return True
    key = _norm_sector(sector, industry)
    return key in _CYCLICAL_FRED


def _pct_change(series: list[float]) -> float | None:
    if len(series) < 2:
        return None
    start, end = series[0], series[-1]
    if start == 0 or start is None or end is None:
        return None
    return (end - start) / abs(start)


def _sma(series: list[float], window: int) -> float | None:
    if len(series) < window or window <= 0:
        return None
    chunk = series[-window:]
    return sum(chunk) / len(chunk)


def _fred_close_series(symbol: str, *, months: int = 3) -> list[tuple[str, float]]:
    obb = _get_obb()
    if obb is None:
        return []
    start = (date.today() - timedelta(days=months * 31)).isoformat()
    try:
        result = obb.economy.fred_series(symbol=symbol, start_date=start, provider="fred")
        df = result.to_dataframe() if hasattr(result, "to_dataframe") else result.to_df()
    except Exception as exc:  # noqa: BLE001
        logger.info("FRED %s 失败: %s", symbol, exc)
        return []
    if df is None or getattr(df, "empty", True):
        return []
    # 常见列：date + value / 或 symbol 列名
    value_col = None
    for c in df.columns:
        cl = str(c).lower()
        if cl in ("value", "close", symbol.lower()) or "value" in cl:
            value_col = c
            break
    if value_col is None:
        numeric = [c for c in df.columns if str(c).lower() != "date"]
        value_col = numeric[0] if numeric else None
    if value_col is None:
        return []
    out: list[tuple[str, float]] = []
    for _, row in df.iterrows():
        try:
            v = float(row[value_col])
        except (TypeError, ValueError):
            continue
        d = row.get("date", "") if hasattr(row, "get") else getattr(row, "date", "")
        out.append((str(d)[:10], v))
    return out


def _yf_close_series(symbol: str, *, months: int = 3) -> list[tuple[str, float]]:
    """yfinance 平替（经 OpenBB equity.price.historical，失败再直连 yfinance）。"""
    start = (date.today() - timedelta(days=months * 31)).isoformat()
    obb = _get_obb()
    if obb is not None:
        try:
            result = obb.equity.price.historical(
                symbol=symbol, start_date=start, provider="yfinance",
            )
            df = result.to_dataframe() if hasattr(result, "to_dataframe") else result.to_df()
            if df is not None and not getattr(df, "empty", True):
                close_col = "close" if "close" in df.columns else (
                    "Close" if "Close" in df.columns else None
                )
                if close_col:
                    out: list[tuple[str, float]] = []
                    for idx, row in df.iterrows():
                        try:
                            v = float(row[close_col])
                        except (TypeError, ValueError):
                            continue
                        d = str(idx)[:10] if not hasattr(row, "get") else str(
                            row.get("date", idx)
                        )[:10]
                        out.append((d, v))
                    if out:
                        return out
        except Exception as exc:  # noqa: BLE001
            logger.info("OpenBB yfinance historical %s 失败: %s", symbol, exc)
    try:
        import yfinance as yf

        hist = yf.Ticker(symbol).history(start=start, auto_adjust=True)
        if hist is None or hist.empty:
            return []
        return [(str(i.date()), float(r["Close"])) for i, r in hist.iterrows()]
    except Exception as exc:  # noqa: BLE001
        logger.info("yfinance %s 失败: %s", symbol, exc)
        return []


def fetch_macro_trend(
    sector: str | None = None,
    industry: str | None = None,
    *,
    company_type: str | None = None,
    is_cyclical: bool = False,
) -> str:
    """宏观周期探针：仅对能源/材料等周期语境拉取近 3 个月大宗商品趋势。"""
    try:
        if not _is_cyclical_context(
            sector, industry, company_type=company_type, is_cyclical=is_cyclical,
        ):
            return "非周期语境：跳过大宗商品宏观探针。"

        key = _norm_sector(sector, industry)
        fred_sym, label = _CYCLICAL_FRED.get(key, ("DCOILWTICO", "WTI 原油"))
        points = _fred_close_series(fred_sym, months=3)
        source = f"FRED:{fred_sym}"

        if len(points) < 5:
            yf_sym, yf_label = _CYCLICAL_YF_FALLBACK.get(key, ("CL=F", "WTI 原油期货"))
            label = yf_label
            points = _yf_close_series(yf_sym, months=3)
            source = f"yfinance:{yf_sym}"

        if len(points) < 5:
            return NO_OPENBB_DATA

        values = [v for _, v in points]
        chg = _pct_change(values)
        sma20 = _sma(values, min(20, len(values)))
        last_d, last_v = points[-1]
        chg_s = f"{chg * 100:+.1f}%" if chg is not None else "N/A"
        sma_s = f"{sma20:.2f}" if sma20 is not None else "N/A"
        above = ""
        if sma20 is not None:
            above = "站上" if last_v >= sma20 else "跌破"
            above = f"｜现价相对近月均线：{above}"
        return (
            f"{label}近3月：最新 {last_v:.2f}（{last_d}）｜涨跌幅 {chg_s}"
            f"｜近月均线 {sma_s}{above}｜源 {source}"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_macro_trend 异常: %s", exc)
        return NO_OPENBB_DATA


def fetch_sec_mda(ticker: str) -> str:
    """高管真心话：最新 10-Q/10-K MD&A 摘要（截断前 2000 字）。"""
    sym = (ticker or "").split(".")[0].upper().strip()
    if not sym or sym.endswith(".T"):  # 日股无 SEC
        return NO_OPENBB_DATA
    try:
        obb = _get_obb()
        if obb is None:
            return NO_OPENBB_DATA
        result = obb.equity.fundamental.management_discussion_analysis(
            symbol=sym, provider="sec",
        )
        raw = getattr(result, "results", None)
        # OpenBB SEC 可能返回单条 Data 对象，也可能是 list
        if raw is None:
            rows: list[Any] = []
        elif isinstance(raw, list):
            rows = raw
        else:
            rows = [raw]
        content = ""
        meta = ""
        if rows:
            row = rows[0]
            content = str(getattr(row, "content", None) or getattr(row, "Content", None) or "")
            year = getattr(row, "calendar_year", None)
            period = getattr(row, "calendar_period", None)
            if year or period is not None:
                period_s = f"Q{period}" if isinstance(period, int) else str(period)
                meta = f"（{year or '?'} {period_s}）"
        if not content:
            try:
                df = result.to_dataframe() if hasattr(result, "to_dataframe") else result.to_df()
                if df is not None and not getattr(df, "empty", True):
                    if "content" in df.columns:
                        content = str(df.iloc[0]["content"] or "")
                    else:
                        # 偶发把整行 Data 塞进单列
                        cell = df.iloc[0, -1]
                        content = str(getattr(cell, "content", None) or cell or "")
            except Exception:  # noqa: BLE001
                pass
        text = " ".join(content.split())
        if not text:
            return NO_OPENBB_DATA
        clipped = text[:_MDA_MAX_CHARS]
        if len(text) > _MDA_MAX_CHARS:
            clipped += "…（已截断）"
        return f"SEC MD&A 摘要{meta}：\n{clipped}"
    except Exception as exc:  # noqa: BLE001
        logger.info("fetch_sec_mda(%s) 失败: %s", sym, exc)
        return NO_OPENBB_DATA


def fetch_options_sentiment(ticker: str) -> str:
    """期权情绪：Put/Call Volume Ratio（OpenBB derivatives/equity.options · yfinance）。"""
    sym = (ticker or "").split(".")[0].upper().strip()
    if not sym or sym.endswith(".T"):
        return NO_OPENBB_DATA
    try:
        obb = _get_obb()
        if obb is None:
            return NO_OPENBB_DATA

        df = None
        # v4 主路径：derivatives.options.chains；兼容 equity.options
        for getter in (
            lambda: obb.derivatives.options.chains(symbol=sym, provider="yfinance"),
            lambda: getattr(obb.equity, "options").chains(symbol=sym, provider="yfinance"),
        ):
            try:
                result = getter()
                df = result.to_dataframe() if hasattr(result, "to_dataframe") else result.to_df()
                if df is not None and not getattr(df, "empty", True):
                    break
            except Exception:  # noqa: BLE001
                df = None
                continue

        if df is None or getattr(df, "empty", True):
            return NO_OPENBB_DATA

        type_col = None
        for c in ("option_type", "optionType", "type", "contract_type"):
            if c in df.columns:
                type_col = c
                break
        vol_col = None
        for c in ("volume", "Volume"):
            if c in df.columns:
                vol_col = c
                break
        if type_col is None or vol_col is None:
            return NO_OPENBB_DATA

        put_vol = 0.0
        call_vol = 0.0
        for _, row in df.iterrows():
            try:
                vol = float(row[vol_col] or 0)
            except (TypeError, ValueError):
                continue
            t = str(row[type_col]).lower()
            if "put" in t:
                put_vol += vol
            elif "call" in t:
                call_vol += vol

        if call_vol <= 0 and put_vol <= 0:
            return NO_OPENBB_DATA
        if call_vol <= 0:
            ratio_s = "∞（无 Call 成交）"
            tone = "极端看跌情绪"
        else:
            ratio = put_vol / call_vol
            ratio_s = f"{ratio:.2f}"
            if ratio >= 1.2:
                tone = "偏恐慌/对冲（Put 偏多）"
            elif ratio <= 0.7:
                tone = "偏乐观/追涨（Call 偏多）"
            else:
                tone = "中性"
        return (
            f"Put/Call Volume Ratio={ratio_s}（Put 量 {put_vol:,.0f} / Call 量 {call_vol:,.0f}）"
            f"｜情绪：{tone}｜源 yfinance via OpenBB"
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("fetch_options_sentiment(%s) 失败: %s", sym, exc)
        return NO_OPENBB_DATA


def build_openbb_sidecar_block(
    ticker: str,
    *,
    sector: str | None = None,
    industry: str | None = None,
    company_type: str | None = None,
    is_cyclical: bool = False,
) -> str:
    """组装 Layer 3 追加板块。任何异常 → 降级文案，绝不抛出。"""
    try:
        macro = fetch_macro_trend(
            sector, industry, company_type=company_type, is_cyclical=is_cyclical,
        )
        mda = fetch_sec_mda(ticker)
        opt = fetch_options_sentiment(ticker)
        if (
            macro == NO_OPENBB_DATA
            and mda == NO_OPENBB_DATA
            and opt == NO_OPENBB_DATA
        ):
            body = NO_OPENBB_DATA
        else:
            body = "\n".join(
                [
                    f"· 宏观商品趋势：{macro}",
                    f"· 管理层 MD&A：{mda}",
                    f"· Put/Call 情绪：{opt}",
                ]
            )
        return (
            "\n\n---\n\n"
            "【OpenBB 深度定性外挂 (免费源)】\n"
            f"{body}"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("build_openbb_sidecar_block(%s) 异常: %s", ticker, exc)
        return (
            "\n\n---\n\n"
            "【OpenBB 深度定性外挂 (免费源)】\n"
            f"{NO_OPENBB_DATA}"
        )
