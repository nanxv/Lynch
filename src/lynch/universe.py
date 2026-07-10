"""全市场成分股动态抓取（漏斗顶端）。

来源：
- us_sbi     : FMP screener · NYSE/NASDAQ/AMEX · 市值≥3亿美元 · 排除 ETF/基金（≈SBI 可直购美股）
- us         : SEC 官方全美股全量接口（约1万只），配合随机无放回抽样防封
- us_midcap  : FMP company-screener 美股中小盘（默认 $10亿~$100亿）
- sp500      : 维基百科 S&P 500 成分股表
- nasdaq100  : 维基百科 NASDAQ-100 成分股表
- jpx        : 日本交易所(JPX)官方每月发布的上市公司全量 Excel（固定链接）

任一来源失败都会打印告警并跳过，不影响其余来源。所有代码经硬编码纠错后去重。
"""

from __future__ import annotations

import io
import random
import time

import pandas as pd
import requests

from .config import (
    FMP_API_KEY,
    MAX_UNIVERSE_SCAN,
    MIDCAP_MAX_MARKET_CAP,
    MIDCAP_MIN_MARKET_CAP,
    MIDCAP_SCREEN_LIMIT,
    SBI_UNIVERSE_EXCHANGES,
    SBI_UNIVERSE_LIMIT,
    SBI_UNIVERSE_MIN_MARKET_CAP,
    UNIVERSE_SOURCES,
    US_MARKET_SAMPLE_SIZE,
    correct_ticker,
)

_HEADERS = {"User-Agent": "Mozilla/5.0 (Lynch-Agent)"}
_TIMEOUT = 30

# SEC 要求请求头带可识别的 User-Agent（含联系方式），否则会被拒绝。
_SEC_HEADERS = {"User-Agent": "Lynch Stock Agent contact@lynch-agent.example"}
_SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_NASDAQ100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
# JPX「東証上場銘柄一覧」固定下载链接（每月更新，链接不变）。
_JPX_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"


def _read_html_tables(url: str) -> list[pd.DataFrame]:
    resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    return pd.read_html(io.StringIO(resp.text))


def _us_sec() -> list[str]:
    """SEC 官方全美股：company_tickers.json → 纯字母常规代码（剔除含 - / . 的复杂类股份）。"""
    resp = requests.get(_SEC_TICKERS_URL, headers=_SEC_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    # 结构为 {"0": {"cik_str":..,"ticker":"AAPL","title":..}, "1": {...}, ...}
    rows = data.values() if isinstance(data, dict) else data
    seen: set[str] = set()
    out: list[str] = []
    for row in rows:
        t = str(row.get("ticker", "")).strip().upper()
        # 只保留纯字母常规美股代码（剔除 BRK-B / BF.B 等含 - 或 . 的类股份）
        if t and t.isalpha() and t not in seen:
            seen.add(t)
            out.append(t)
    return out


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


def _us_midcap() -> list[str]:
    """FMP company-screener：美股中小盘（默认市值 $10亿~$100亿）— 林奇猎场扩容。"""
    if not FMP_API_KEY:
        raise RuntimeError("us_midcap 需要 FMP_API_KEY")
    # 分页：FMP limit 上限常见为单次返回条数；多页拉满 MIDCAP_SCREEN_LIMIT
    out: list[str] = []
    seen: set[str] = set()
    page = 0
    page_size = min(1000, MIDCAP_SCREEN_LIMIT)
    while len(out) < MIDCAP_SCREEN_LIMIT:
        params = {
            "marketCapMoreThan": MIDCAP_MIN_MARKET_CAP,
            "marketCapLowerThan": MIDCAP_MAX_MARKET_CAP,
            "isActivelyTrading": "true",
            "limit": page_size,
            "page": page,
            "apikey": FMP_API_KEY,
        }
        resp = requests.get(
            "https://financialmodelingprep.com/stable/company-screener",
            params=params,
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list) or not data:
            break
        for row in data:
            t = str(row.get("symbol") or "").strip().upper()
            if not t or t in seen:
                continue
            # 过滤非普通股代码（含 - . /）
            if not t.replace("-", "").replace(".", "").isalnum():
                continue
            if "." in t and not t.endswith(".T"):
                continue
            seen.add(t)
            out.append(t)
            if len(out) >= MIDCAP_SCREEN_LIMIT:
                break
        if len(data) < page_size:
            break
        page += 1
        if page > 20:
            break
    return out


def _us_sbi() -> list[str]:
    """≈SBI/NISA 可直购美股：主板 + 市值≥门槛，排除 ETF/基金/OTC 风格代码。

    与 metrics.check_sbi_tradable 对齐的启发式；非券商实时可交易名单。
    """
    if not FMP_API_KEY:
        raise RuntimeError("us_sbi 需要 FMP_API_KEY")
    out: list[str] = []
    seen: set[str] = set()
    page_size = 1000
    for exchange in SBI_UNIVERSE_EXCHANGES:
        page = 0
        while len(out) < SBI_UNIVERSE_LIMIT:
            params = {
                "marketCapMoreThan": SBI_UNIVERSE_MIN_MARKET_CAP,
                "isActivelyTrading": "true",
                "exchange": exchange,
                "limit": page_size,
                "page": page,
                "apikey": FMP_API_KEY,
            }
            resp = requests.get(
                "https://financialmodelingprep.com/stable/company-screener",
                params=params,
                headers=_HEADERS,
                timeout=_TIMEOUT,
            )
            if resp.status_code == 429:
                time.sleep(5)
                continue
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list) or not data:
                break
            for row in data:
                if row.get("isEtf") or row.get("isFund"):
                    continue
                t = str(row.get("symbol") or "").strip().upper()
                if not t or t in seen:
                    continue
                # 常规普通股：允许 BRK-B 类；排除带点的复杂代码 / 5 位 F·Y OTC ADR
                if not t.replace("-", "").isalnum():
                    continue
                if "." in t:
                    continue
                if len(t) == 5 and t.endswith(("F", "Y")):
                    continue
                seen.add(t)
                out.append(t)
                if len(out) >= SBI_UNIVERSE_LIMIT:
                    break
            if len(data) < page_size:
                break
            page += 1
            if page > 40:
                break
            time.sleep(0.22)
    return out


_FETCHERS = {
    "us": _us_sec,
    "us_sbi": _us_sbi,
    "us_midcap": _us_midcap,
    "sp500": _sp500,
    "nasdaq100": _nasdaq100,
    "jpx": _jpx,
}


def get_universe(
    sources: list[str] | None = None,
    cap: int | None = None,
    *,
    us_sample: int | None = None,
    seed: int | None = None,
) -> list[str]:
    """聚合成分股列表：抓取 → 纠错去重 → (us 源随机抽样) → 截断到上限。

    us_sample: 全美股(us 源)每次随机无放回抽样的数量；None 时用配置默认。
    seed: 抽样随机种子（仅测试用），默认 None = 每次随机（一周轮动扫遍全市场）。
    """
    sources = sources or UNIVERSE_SOURCES
    cap = MAX_UNIVERSE_SCAN if cap is None else cap
    us_sample = US_MARKET_SAMPLE_SIZE if us_sample is None else us_sample
    rng = random.Random(seed)

    seen: set[str] = set()
    universe: list[str] = []
    for src in sources:
        fetch = _FETCHERS.get(src)
        if fetch is None:
            print(f"⚠️  未知的成分股来源: {src}（可选 us_sbi/us/us_midcap/sp500/nasdaq100/jpx），跳过。")
            continue
        try:
            tickers = fetch()
            print(f"✅ {src}: 抓到 {len(tickers)} 只")
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️  {src} 抓取失败，跳过：{exc}")
            continue

        # 全美股全量太大，直接并发 1 万次极易被 Yahoo 封 IP → 随机无放回抽样。
        if src == "us" and us_sample and len(tickers) > us_sample:
            tickers = rng.sample(tickers, us_sample)
            print(f"🎲 us: 从全量随机抽样 {us_sample} 只（防封 + 一周轮动扫全市场）")

        for t in tickers:
            ct = correct_ticker(t)
            if ct and ct not in seen:
                seen.add(ct)
                universe.append(ct)

    if "us_sbi" in sources and len([t for t in universe if not str(t).endswith(".T")]) < 1500:
        print(
            "⚠️  us_sbi 海选池偏少（<1500）。正常约 3000+；"
            "请检查 FMP_API_KEY / 套餐 screener 配额，或是否设置了 MAX_UNIVERSE_SCAN 截断。"
        )

    if cap and len(universe) > cap:
        print(f"ℹ️  海选池 {len(universe)} 只 > 上限 {cap}，截断到前 {cap} 只（防超时/封禁）。")
        universe = universe[:cap]
    return universe
