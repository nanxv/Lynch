"""watchlist.yaml 影子持仓状态（held / watch）解析与查询。"""

from __future__ import annotations

from src.config_loader import StockEntry, load_config
from src.lynch.config import correct_ticker

VALID_USER_STATUSES = frozenset({"held", "watch"})


def normalize_user_status(raw: str | None) -> str:
    """合法值 held / watch；其余回退 watch。"""
    status = (raw or "watch").lower().strip()
    return status if status in VALID_USER_STATUSES else "watch"


def parse_stock_entry(item: dict) -> StockEntry:
    """从 YAML 条目解析 StockEntry，含 user_status。"""
    return StockEntry(
        ticker=str(item["ticker"]),
        name=str(item["name"]),
        market=str(item["market"]),
        tier=int(item["tier"]),
        note=str(item.get("note") or ""),
        user_status=normalize_user_status(item.get("status")),
    )


def user_status_for_ticker(ticker: str) -> str:
    """查 watchlist 中 ticker 的影子持仓状态；不在列表则 watch。"""
    key = correct_ticker(ticker)
    for stock in load_config().stocks:
        if correct_ticker(stock.ticker) == key:
            return stock.user_status
    return "watch"
