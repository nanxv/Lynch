"""彼得·林奇专属分析 Agent — 双层漏斗 + 数据供应层解耦 + 邮件双模流水线。"""

from .agent import LynchAnalysis, analyze_company
from .data import Fundamentals, FundamentalsError, QuickScreen, get_provider
from .fundamentals import fetch_fundamentals
from .metrics import LynchMetrics, compute_metrics

__all__ = [
    "Fundamentals",
    "FundamentalsError",
    "QuickScreen",
    "get_provider",
    "fetch_fundamentals",
    "LynchMetrics",
    "compute_metrics",
    "LynchAnalysis",
    "analyze_company",
]
