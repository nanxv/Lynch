"""全市场成分股动态抓取（漏斗顶端）。

来源：
- sp500      : 维基百科 S&P 500 成分股表
- nasdaq100  : 维基百科 NASDAQ-100 成分股表
- jpx        : 日本交易所(JPX)官方每月发布的上市公司全量 Excel（固定链接）

任一来源失败都会打印告警并跳过，不影响其余来源。所有代码经硬编码纠错后去重。
"""

from __future__ import annotations

import io

import pandas as pd
import requests

from .config import MAX_UNIVERSE_SCAN, UNIVERSE_SOURCES, correct_ticker

_HEADERS = {"User-Agent": "Mozilla/5.0 (Lynch-Agent)"}
_TIMEOUT = 30

_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_NASDAQ100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
# JPX「東証上場銘柄一覧」固定下载链接（每月更新，链接不变）。
_JPX_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"


def _read_html_tables(url: str) -> list[pd.DataFrame]:
    resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    return pd.read_html(io.StringIO(resp.text))


def _sp500() -> list[str]:
    tables = _read_html_tables(_SP500_URL)
    df = tables[0]
    col = "Symbol" if "Symbol" in df.columns else df.columns[0]
    # 维基用 BRK.B，yfinance 用 BRK-B
    return [str(s).replace(".", "-").strip() for s in df[col].dropna()]


def _nasdaq100() -> list[str]:
    tables = _read_html_tables(_NASDAQ100_URL)
    for df in tables:
        for col in df.columns:
            if str(col).lower() in ("ticker", "symbol"):
                return [str(s).replace(".", "-").strip() for s in df[col].dropna()]
    return []


def _jpx() -> list[str]:
    resp = requests.get(_JPX_URL, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    df = pd.read_excel(io.BytesIO(resp.content))
    code_col = next((c for c in df.columns if "コード" in str(c) or str(c).lower() == "code"), None)
    if code_col is None:
        return []
    out: list[str] = []
    for code in df[code_col].dropna():
        s = str(code).strip()
        if s.isdigit():
            out.append(f"{s}.T")
    return out


_FETCHERS = {"sp500": _sp500, "nasdaq100": _nasdaq100, "jpx": _jpx}


def get_universe(sources: list[str] | None = None, cap: int | None = None) -> list[str]:
    """聚合成分股列表：纠错 → 去重 → 截断到上限。"""
    sources = sources or UNIVERSE_SOURCES
    cap = MAX_UNIVERSE_SCAN if cap is None else cap

    seen: set[str] = set()
    universe: list[str] = []
    for src in sources:
        fetch = _FETCHERS.get(src)
        if fetch is None:
            print(f"⚠️  未知的成分股来源: {src}（可选 sp500/nasdaq100/jpx），跳过。")
            continue
        try:
            tickers = fetch()
            print(f"✅ {src}: 抓到 {len(tickers)} 只")
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️  {src} 抓取失败，跳过：{exc}")
            continue
        for t in tickers:
            ct = correct_ticker(t)
            if ct and ct not in seen:
                seen.add(ct)
                universe.append(ct)

    if cap and len(universe) > cap:
        print(f"ℹ️  海选池 {len(universe)} 只 > 上限 {cap}，截断到前 {cap} 只（防超时/封禁）。")
        universe = universe[:cap]
    return universe
