"""Phase 4 定性 Alpha 探针：机构冷落度 + 内部人真金白银雷达（FMP + Yahoo 回退）。"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Callable

from ..config import (
    INSIDER_LOOKBACK_DAYS,
    INSIDER_MIN_NET_BUYS,
    correct_ticker,
)

log = logging.getLogger(__name__)


def _parse_date(raw: Any) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _trade_side(row: dict) -> str:
    tx = str(row.get("transactionType") or "").upper()
    acq = str(row.get("acquisitionOrDisposition") or "").upper()
    if "PURCHASE" in tx or tx.startswith("P-") or acq == "A":
        return "buy"
    if "SALE" in tx or tx.startswith("S-") or acq == "D":
        return "sell"
    return "other"


def _norm_pct(raw: Any) -> float | None:
    """归一化为 0~1 小数（与 yfinance heldPercentInstitutions 一致）。"""
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v > 1.0:
        v /= 100.0
    if v < 0:
        return None
    return v


def _institutional_pct_from_fmp(api: Any, sym: str) -> float | None:
    """尝试 FMP 机构持股端点（订阅受限时 optional 返回空）。"""
    today = date.today()
    year = today.year
    quarter = (today.month - 1) // 3 + 1
    # 优先最近一季，再往前探一季
    for y, q in ((year, quarter), (year, max(1, quarter - 1)), (year - 1, 4)):
        rows = api.get(
            "institutional-ownership/symbol-positions-summary",
            {"symbol": sym, "year": y, "quarter": q},
            optional=True,
        )
        if not rows:
            continue
        row = rows[0] if isinstance(rows, list) else rows
        if not isinstance(row, dict):
            continue
        for key in (
            "ownershipPercent",
            "percentOfSharesOutstanding",
            "institutionalOwnershipPercent",
            "ownership",
        ):
            pct = _norm_pct(row.get(key))
            if pct is not None:
                return pct
    return None


def _yahoo_institutional_pct(sym: str) -> float | None:
    """FMP 机构端点不可用时的轻量回退（yfinance profile）。"""
    if sym.endswith(".T"):
        return None
    try:
        import yfinance as yf

        info = yf.Ticker(sym).info or {}
        return _norm_pct(info.get("heldPercentInstitutions"))
    except Exception as exc:  # noqa: BLE001
        log.debug("Yahoo 机构持股回退跳过 %s: %s", sym, exc)
        return None


def _insider_from_search(api: Any, sym: str) -> tuple[int, int]:
    rows = api.get(
        "insider-trading/search",
        {"symbol": sym, "page": 0, "limit": 100},
        optional=True,
    )
    if not isinstance(rows, list):
        return 0, 0
    cutoff = date.today() - timedelta(days=INSIDER_LOOKBACK_DAYS)
    buys = sells = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        d = _parse_date(row.get("transactionDate") or row.get("filingDate"))
        if d is not None and d < cutoff:
            continue
        side = _trade_side(row)
        if side == "buy":
            buys += 1
        elif side == "sell":
            sells += 1
    return buys, sells


def _insider_from_statistics(api: Any, sym: str) -> tuple[int, int]:
    """近两季统计回退：acquired vs disposed 笔数。"""
    rows = api.get("insider-trading/statistics", {"symbol": sym}, optional=True)
    if not isinstance(rows, list) or not rows:
        return 0, 0
    today = date.today()
    cur_y, cur_q = today.year, (today.month - 1) // 3 + 1
    recent_keys: set[tuple[int, int]] = {(cur_y, cur_q)}
    if cur_q > 1:
        recent_keys.add((cur_y, cur_q - 1))
    else:
        recent_keys.add((cur_y - 1, 4))
    buys = sells = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            y, q = int(row.get("year")), int(row.get("quarter"))
        except (TypeError, ValueError):
            continue
        if (y, q) not in recent_keys:
            continue
        buys += int(row.get("acquiredTransactions") or row.get("totalPurchases") or 0)
        sells += int(row.get("disposedTransactions") or row.get("totalSales") or 0)
    return buys, sells


def fetch_alpha_intel(api: Any, ticker: str) -> dict[str, Any]:
    """拉取机构持股 + 内部人动向，返回可 merge 进 Fundamentals 的字段 dict。"""
    sym = correct_ticker(ticker)
    held_pct = _institutional_pct_from_fmp(api, sym)
    if held_pct is None:
        held_pct = _yahoo_institutional_pct(sym)

    buys, sells = _insider_from_search(api, sym)
    if buys == 0 and sells == 0:
        buys, sells = _insider_from_statistics(api, sym)

    net_signal = buys >= INSIDER_MIN_NET_BUYS and buys > sells
    return {
        "held_percent_institutions": held_pct,
        "insider_buy_count": buys,
        "insider_sell_count": sells,
        "insider_net_buy_signal": net_signal,
    }


def attach_alpha_intel(fetch_api: Callable[[], Any], ticker: str) -> dict[str, Any]:
    """供 FmpProvider 调用：失败时优雅降级为空增量。"""
    try:
        return fetch_alpha_intel(fetch_api(), ticker)
    except Exception as exc:  # noqa: BLE001
        log.warning("Alpha 探针跳过 %s: %s", ticker, exc)
        return {}
