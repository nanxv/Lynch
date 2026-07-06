"""数据供应层工厂：按环境变量 DATA_PROVIDER 动态选择数据源。"""

from __future__ import annotations

from .. import config
from .base import BaseDataProvider, Fundamentals, FundamentalsError, QuickScreen, classify_company
from .fmp import FmpProvider
from .yahoo import YahooFinanceProvider

__all__ = [
    "BaseDataProvider",
    "Fundamentals",
    "FundamentalsError",
    "QuickScreen",
    "classify_company",
    "YahooFinanceProvider",
    "FmpProvider",
    "get_provider",
]

_CACHE: dict[str, BaseDataProvider] = {}


def get_provider(name: str | None = None) -> BaseDataProvider:
    """返回数据源实例（单例缓存）。默认取 config.DATA_PROVIDER（env: DATA_PROVIDER）。"""
    key = (name or config.DATA_PROVIDER or "yahoo").strip().lower()

    if key in _CACHE:
        return _CACHE[key]

    if key in ("yahoo", "yfinance"):
        provider: BaseDataProvider = YahooFinanceProvider()
    elif key == "fmp":
        provider: BaseDataProvider = FmpProvider()
    elif key in ("jquants", "j-quants"):
        raise NotImplementedError(
            "J-Quants 数据源尚未实现。请实现 data/jquants.py::JQuantsProvider(BaseDataProvider) 后在此注册。"
        )
    else:
        raise ValueError(f"未知的 DATA_PROVIDER: {key}（可选 yahoo / fmp / jquants）")

    _CACHE[key] = provider
    return provider
