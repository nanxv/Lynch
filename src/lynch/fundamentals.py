"""向后兼容 shim：基本面抓取已迁移到 `src/lynch/data/` 供应层。

保留 `Fundamentals` / `FundamentalsError` / `fetch_fundamentals` 旧接口，
内部统一委托给 `data.get_provider()`。
"""

from __future__ import annotations

from .data import Fundamentals, FundamentalsError, get_provider
from .data.base import BaseDataProvider

__all__ = ["Fundamentals", "FundamentalsError", "fetch_fundamentals"]


def fetch_fundamentals(ticker: str, provider: BaseDataProvider | None = None) -> Fundamentals:
    return (provider or get_provider()).get_fundamentals(ticker)
