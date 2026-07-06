"""watchlist.yaml 影子持仓状态（held / watch / avoid）解析与查询。"""

from __future__ import annotations

from src.config_loader import StockEntry, load_config
from src.lynch.config import correct_ticker

VALID_USER_STATUSES = frozenset({"held", "watch"})
VALID_WATCHLIST_STATUSES = frozenset({"held", "watch", "avoid"})


def parse_watchlist_status(raw: str | None) -> str:
    """解析 YAML status：held / watch / avoid（默认 watch）。"""
    status = (raw or "watch").lower().strip()
    return status if status in VALID_WATCHLIST_STATUSES else "watch"


def normalize_user_status(raw: str | None) -> str:
    """AI Prompt 用：held / watch（avoid 不应进入分析链路）。"""
    status = parse_watchlist_status(raw)
    return status if status in VALID_USER_STATUSES else "watch"


def is_avoid_status(raw: str | None) -> bool:
    return parse_watchlist_status(raw) == "avoid"


def parse_stock_entry(item: dict) -> StockEntry:
    """从 YAML 条目解析 StockEntry，含 user_status（含 avoid）。"""
    return StockEntry(
        ticker=str(item["ticker"]),
        name=str(item["name"]),
        market=str(item["market"]),
        tier=int(item["tier"]),
        note=str(item.get("note") or ""),
        user_status=parse_watchlist_status(item.get("status")),
    )


def user_status_for_ticker(ticker: str) -> str:
    """查 watchlist 中 ticker 的影子持仓状态；不在列表则 watch。"""
    key = correct_ticker(ticker)
    for stock in load_config().stocks:
        if correct_ticker(stock.ticker) == key:
            return normalize_user_status(stock.user_status)
    return "watch"


def is_ticker_avoided(ticker: str) -> bool:
    """该 ticker 是否在 watchlist 中标记为 avoid（物理隔离）。"""
    key = correct_ticker(ticker)
    for stock in load_config().stocks:
        if correct_ticker(stock.ticker) == key:
            return stock.user_status == "avoid"
    return False


def list_avoided_tickers() -> list[str]:
    return [
        correct_ticker(s.ticker)
        for s in load_config().stocks
        if s.user_status == "avoid"
    ]
