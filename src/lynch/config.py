"""林奇流水线全局配置（全部可用系统环境变量覆盖，适配 GitHub Secrets/Variables）。"""

from __future__ import annotations

import os

# ── 数据供应层 ──────────────────────────────────────────────────
# 选择数据源：yahoo(默认) / fmp / jquants（后两者为未来扩展预留接口）
DATA_PROVIDER = os.environ.get("DATA_PROVIDER", "yahoo").strip().lower()

# ── 硬编码数据纠错 ─────────────────────────────────────────────
# 6859.T 实际是 Espec Corp，真正的 TOWA 是 6315.T。
TICKER_CORRECTIONS: dict[str, str] = {
    "6859.T": "6315.T",
}

# ── 全市场海选（漏斗顶端）───────────────────────────────────────
# 成分股来源：sp500 / nasdaq100 / jpx（日股全量，约4000只，较慢，需显式开启）
UNIVERSE_SOURCES = [
    s.strip().lower()
    for s in os.environ.get("UNIVERSE_SOURCES", "sp500,nasdaq100").split(",")
    if s.strip()
]
# 单次最多扫描多少只（防止 GitHub Actions 超时 / yfinance 被封）。
MAX_UNIVERSE_SCAN = int(os.environ.get("MAX_UNIVERSE_SCAN", "1200"))
# 第一层漏斗并发线程数（过高会触发 yfinance 限流/封禁）。
SCAN_WORKERS = int(os.environ.get("SCAN_WORKERS", "8"))

# ── 第一层纯代码漏斗阈值（硬指标粗筛）──────────────────────────
# 通过条件（满足其一即留下，且负债不超标）：
#   PEG <= FUNNEL_MAX_PEG（估值划算）  或  每股净现金/股价 >= FUNNEL_MIN_NETCASH_RATIO（隐蔽资产）
FUNNEL_MAX_PEG = float(os.environ.get("FUNNEL_MAX_PEG", "1.5"))
FUNNEL_MIN_NETCASH_RATIO = float(os.environ.get("FUNNEL_MIN_NETCASH_RATIO", "0.30"))
# 总债/权益（yfinance debtToEquity，百分比换算成小数）上限，超过则刷掉。
FUNNEL_MAX_DEBT_RATIO = float(os.environ.get("FUNNEL_MAX_DEBT_RATIO", "0.50"))

# ── 第二层 AI 漏斗：成本熔断 ───────────────────────────────────
# 每次最多调用 Gemini 做完整"四步叙述与裁决"的公司数量硬上限。
MAX_AI_ANALYSIS_COUNT = int(os.environ.get("MAX_AI_ANALYSIS_COUNT", "30"))
# 超额时的排序口径：peg(从低到高，最划算) / net_cash(从高到低，安全垫最厚)
AI_SORT_KEY = os.environ.get("AI_SORT_KEY", "peg").strip().lower()


def correct_ticker(ticker: str) -> str:
    """应用硬编码纠错映射（大小写/后缀不敏感的精确匹配）。"""
    t = ticker.strip().upper()
    return TICKER_CORRECTIONS.get(t, t)
