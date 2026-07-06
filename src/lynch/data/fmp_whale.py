"""政要巨鳄雷达：议员交易 + 13F 机构持仓（按周缓存，Ticker 本地匹配）。"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from .fmp_cache import (
    load_whale_cache,
    save_whale_cache,
    whale_cache_stale,
)

log = logging.getLogger(__name__)

# 重点跟踪机构（CIK → 中文简称）
WHALE_FUNDS: dict[str, str] = {
    "0001067983": "伯克希尔·哈撒韦",
    "0001350694": "桥水基金",
    "0001037389": "文艺复兴科技",
}

POLITICIAN_CACHE = "politician_trades"
INSTITUTIONAL_CACHE = "institutional_13f"


def _parse_date(raw: Any) -> date | None:
    if not raw:
        return None
    s = str(raw)[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _norm_symbol(sym: Any) -> str:
    return str(sym or "").strip().upper()


def _trade_side(raw: str | None) -> str:
    t = (raw or "").lower()
    if "purchase" in t or "buy" in t:
        return "buy"
    if "sale" in t or "sell" in t:
        return "sell"
    return "other"


def _politician_name(row: dict) -> str:
    for key in ("representative", "senator", "firstName", "lastName", "office"):
        if row.get(key):
            parts = []
            if row.get("firstName"):
                parts.append(str(row["firstName"]))
            if row.get("lastName"):
                parts.append(str(row["lastName"]))
            if parts:
                return " ".join(parts)
            return str(row[key])
    return "国会议员"


def refresh_politician_trades(fetch_senate, fetch_house) -> list[dict]:
    """拉取全局议员交易列表并写入周缓存（消耗 1-2 次 API）。"""
    senate = fetch_senate() or []
    house = fetch_house() or []
    if not isinstance(senate, list):
        senate = []
    if not isinstance(house, list):
        house = []
    for row in senate:
        row["_chamber"] = "senate"
    for row in house:
        row["_chamber"] = "house"
    merged = senate + house
    save_whale_cache(POLITICIAN_CACHE, {"trades": merged})
    return merged


def get_politician_trades(fetch_senate, fetch_house) -> list[dict]:
    if whale_cache_stale(POLITICIAN_CACHE):
        return refresh_politician_trades(fetch_senate, fetch_house)
    cached = load_whale_cache(POLITICIAN_CACHE) or {}
    trades = cached.get("trades")
    return trades if isinstance(trades, list) else []


def refresh_institutional_holdings(fetch_portfolio_dates, fetch_portfolio) -> dict[str, Any]:
    """按机构拉取最近两期 13F 持仓（每周刷新，每机构 2 次 API）。"""
    snapshots: dict[str, Any] = {}
    for cik, label in WHALE_FUNDS.items():
        try:
            dates = fetch_portfolio_dates(cik) or []
        except Exception as exc:  # noqa: BLE001
            log.warning("13F dates %s: %s", cik, exc)
            continue
        if not isinstance(dates, list) or not dates:
            continue
        date_strs = sorted(
            [str(d.get("date") or d)[:10] for d in dates if d],
            reverse=True,
        )
        if not date_strs:
            continue
        periods: list[dict] = []
        for ds in date_strs[:2]:
            try:
                holdings = fetch_portfolio(cik, ds) or []
            except Exception as exc:  # noqa: BLE001
                log.warning("13F holdings %s@%s: %s", cik, ds, exc)
                continue
            if isinstance(holdings, list):
                periods.append({"date": ds, "holdings": holdings})
        if periods:
            snapshots[cik] = {"label": label, "periods": periods}
    save_whale_cache(INSTITUTIONAL_CACHE, {"funds": snapshots})
    return snapshots


def get_institutional_snapshots(fetch_portfolio_dates, fetch_portfolio) -> dict[str, Any]:
    if whale_cache_stale(INSTITUTIONAL_CACHE):
        return refresh_institutional_holdings(fetch_portfolio_dates, fetch_portfolio)
    cached = load_whale_cache(INSTITUTIONAL_CACHE) or {}
    funds = cached.get("funds")
    return funds if isinstance(funds, dict) else {}


def _holding_weight(h: dict, total_value: float | None = None) -> float | None:
    for key in ("weight", "weightPercent", "portfolioPercent"):
        v = h.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    mv = h.get("marketValue") or h.get("value")
    if mv is not None and total_value and total_value > 0:
        try:
            return float(mv) / float(total_value) * 100
        except (TypeError, ValueError):
            pass
    return None


def _period_holdings_map(holdings: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    total = sum(float(h.get("marketValue") or h.get("value") or 0) for h in holdings)
    for h in holdings:
        sym = _norm_symbol(h.get("symbol") or h.get("ticker"))
        if sym:
            out[sym] = {**h, "_weight": _holding_weight(h, total)}
    return out


def analyze_whale_signals(
    ticker: str,
    politician_trades: list[dict],
    institutional: dict[str, Any],
    *,
    lookback_days: int = 14,
) -> tuple[str, str]:
    """返回 (brief 简报一行, block 详述区块)。"""
    sym = _norm_symbol(ticker)
    cutoff = date.today() - timedelta(days=lookback_days)
    alerts: list[str] = []
    block_lines = ["【政要巨鳄雷达（议员交易 + 13F 机构动向）】"]

    # ── 议员交易 ──
    recent = [
        t for t in politician_trades
        if _norm_symbol(t.get("symbol") or t.get("ticker")) == sym
        and (_d := _parse_date(t.get("transactionDate") or t.get("disclosureDate"))) is not None
        and _d >= cutoff
    ]
    if recent:
        block_lines.append(f"— 近 {lookback_days} 天国会议员交易 —")
        for t in recent[:5]:
            name = _politician_name(t)
            side = _trade_side(str(t.get("type") or t.get("transactionType") or ""))
            amt = t.get("amount") or t.get("value") or "金额未披露"
            dt = t.get("transactionDate") or t.get("disclosureDate") or "?"
            side_cn = "建仓/买入" if side == "buy" else ("减持/卖出" if side == "sell" else "交易")
            block_lines.append(f"- {name} {side_cn} ({dt}, {amt})")
            if side == "buy":
                label = "佩洛西" if "pelosi" in name.lower() else name
                alerts.append(f"{label}/国会议员于近 {lookback_days} 天内建仓该股")
            elif side == "sell":
                alerts.append(f"{name} 于近 {lookback_days} 天内减持该股")

    # ── 13F 机构变动 ──
    for cik, fund in institutional.items():
        label = fund.get("label") or WHALE_FUNDS.get(cik, cik)
        periods = fund.get("periods") or []
        if len(periods) < 2:
            continue
        cur_map = _period_holdings_map(periods[0].get("holdings") or [])
        prev_map = _period_holdings_map(periods[1].get("holdings") or [])
        cur = cur_map.get(sym)
        prev = prev_map.get(sym)
        cur_w = cur.get("_weight") if cur else None
        prev_w = prev.get("_weight") if prev else None
        if cur is None and prev is not None:
            alerts.append(f"{label} 本季度已清仓 {sym}")
            block_lines.append(f"- {label}：上季持有 → 本季已退出持仓")
        elif prev is None and cur is not None:
            pct = f"{cur_w:.1f}%" if cur_w is not None else "新建仓"
            alerts.append(f"{label} 本季度新建仓 {sym}（{pct}）")
            block_lines.append(f"- {label}：本季新建仓（权重约 {pct}）")
        elif cur_w is not None and prev_w is not None and prev_w > 0:
            chg = (cur_w - prev_w) / prev_w * 100
            if chg <= -20:
                alerts.append(f"{label} 本季度减持 {sym} 约 {abs(chg):.0f}%")
                block_lines.append(
                    f"- {label}：持仓权重 {prev_w:.2f}% → {cur_w:.2f}%（减持 {abs(chg):.0f}%）"
                )
            elif chg >= 20:
                alerts.append(f"{label} 本季度增持 {sym} 约 {chg:.0f}%")
                block_lines.append(
                    f"- {label}：持仓权重 {prev_w:.2f}% → {cur_w:.2f}%（增持 {chg:.0f}%）"
                )

    if len(block_lines) == 1:
        block_lines.append("- 本周暂无政要/顶级机构针对该股的显著异动")

    brief = ""
    if alerts:
        brief = "[🚨 政要异动] " + "；".join(alerts[:2])

    return brief, "\n".join(block_lines)
