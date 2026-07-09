"""林奇流水线全局配置（全部可用系统环境变量覆盖，适配 GitHub Secrets/Variables）。"""

from __future__ import annotations

import os


def _env_str(key: str, default: str) -> str:
    """读字符串环境变量；GitHub 未配置的 var/secret 会注入空串，需 or 回退默认值。"""
    return (os.environ.get(key) or default).strip()


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or not str(raw).strip():
        return default
    return int(str(raw).strip())


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None or not str(raw).strip():
        return default
    return float(str(raw).strip())


# ── 数据供应层 ──────────────────────────────────────────────────
# 选择数据源：yahoo / fmp(默认升级目标) / jquants
DATA_PROVIDER = _env_str("DATA_PROVIDER", "yahoo").lower()
FMP_API_KEY = _env_str("FMP_API_KEY", "")
# Starter 套餐：300 次/分钟（用间隔节流）；日额度 0=不封顶（付费档无 250/天限制）
FMP_REQUEST_INTERVAL = _env_float("FMP_REQUEST_INTERVAL", 0.21)
FMP_PER_MINUTE_LIMIT = _env_int("FMP_PER_MINUTE_LIMIT", 300)
# 仅免费档需要日封顶；设为 0 表示禁用（Starter/Premium 请保持 0）
FMP_DAILY_QUOTA = _env_int("FMP_DAILY_QUOTA", 0)

# ── 硬编码数据纠错 ─────────────────────────────────────────────
# 6859.T 实际是 Espec Corp，真正的 TOWA 是 6315.T。
TICKER_CORRECTIONS: dict[str, str] = {
    "6859.T": "6315.T",
}

# ── 全市场海选（漏斗顶端）───────────────────────────────────────
# 成分股来源：
#   us_sbi     : FMP screener · NYSE/NASDAQ/AMEX · 市值≥3亿美元 · 排除 ETF/基金（≈SBI 可直购美股）
#   us         : SEC 官方全美股全量（约1万只，配合随机抽样）
#   us_midcap  : FMP company-screener 美股市值 $10亿~$100亿（林奇中小盘猎场）
#   sp500      : 维基 S&P 500
#   nasdaq100  : 维基 NASDAQ-100
#   jpx        : 日股全量（约4000只，较慢，需显式开启）
UNIVERSE_SOURCES = [
    s.strip().lower()
    for s in _env_str("UNIVERSE_SOURCES", "us_sbi").split(",")
    if s.strip()
] or ["us_sbi"]
# 全美股（us 源）每次运行随机无放回抽样的数量。
US_MARKET_SAMPLE_SIZE = _env_int("US_MARKET_SAMPLE_SIZE", 500)
# 中小盘 screener：市值下限/上限（美元）、单次拉取上限。
MIDCAP_MIN_MARKET_CAP = _env_int("MIDCAP_MIN_MARKET_CAP", 1_000_000_000)
MIDCAP_MAX_MARKET_CAP = _env_int("MIDCAP_MAX_MARKET_CAP", 10_000_000_000)
MIDCAP_SCREEN_LIMIT = _env_int("MIDCAP_SCREEN_LIMIT", 3000)
# SBI 可买美股池：与 metrics.check_sbi_tradable 市值门槛对齐（默认 $3亿）。
SBI_UNIVERSE_MIN_MARKET_CAP = _env_int("SBI_UNIVERSE_MIN_MARKET_CAP", 300_000_000)
SBI_UNIVERSE_EXCHANGES = [
    s.strip().upper()
    for s in _env_str("SBI_UNIVERSE_EXCHANGES", "NYSE,NASDAQ,AMEX").split(",")
    if s.strip()
] or ["NYSE", "NASDAQ", "AMEX"]
SBI_UNIVERSE_LIMIT = _env_int("SBI_UNIVERSE_LIMIT", 10000)
# 单次最多扫描多少只（防超时）。SBI 全池默认不截断（0=不截断）。
MAX_UNIVERSE_SCAN = _env_int("MAX_UNIVERSE_SCAN", 0)
# 第一层漏斗并发线程数。
SCAN_WORKERS = _env_int("SCAN_WORKERS", 8)

# ── 第一层多通道漏斗阈值（Phase 1 + Phase 2）────────────────────
# 负债门：长期负债/股东权益；金融股无条件豁免。
# 通道 OR：peg | cyclical | net_cash | stalwart | slow_div | turnaround
FUNNEL_MAX_PEG = _env_float("FUNNEL_MAX_PEG", 1.5)
FUNNEL_MIN_NETCASH_RATIO = _env_float("FUNNEL_MIN_NETCASH_RATIO", 0.30)
FUNNEL_MAX_DEBT_RATIO = _env_float("FUNNEL_MAX_DEBT_RATIO", 0.33)
# 稳增/慢增通道可用更宽负债上限（长期债/权益）；快增 PEG 通道仍用 0.33
FUNNEL_STALWART_MAX_DEBT_RATIO = _env_float("FUNNEL_STALWART_MAX_DEBT_RATIO", 1.50)
FUNNEL_STALWART_PE_DISCOUNT = _env_float("FUNNEL_STALWART_PE_DISCOUNT", 0.85)
# 稳增错杀：当前 P/E 相对 5y 均 P/E 的倍数上限；默认 1.0（≤历史均即可进漏斗，0.85 更严）
FUNNEL_STALWART_PE_VS_AVG_MAX = _env_float("FUNNEL_STALWART_PE_VS_AVG_MAX", 1.0)
FUNNEL_MIN_DIV_YIELD = _env_float("FUNNEL_MIN_DIV_YIELD", 4.0)
# 稳增股息旁路（慢增达不到 4% 时）：股息% + 分红可持续 + 非快增周期
FUNNEL_STALWART_MIN_DIV_YIELD = _env_float("FUNNEL_STALWART_MIN_DIV_YIELD", 2.0)
FUNNEL_MAX_PAYOUT_RATIO = _env_float("FUNNEL_MAX_PAYOUT_RATIO", 0.80)
FUNNEL_TURNAROUND_LTD_YOY = _env_float("FUNNEL_TURNAROUND_LTD_YOY", -0.10)

# ── 周期股分相（林奇反直觉法则）────────────────────────────────
# 底部信号：P/E 高于此值视为「利润难看」；顶部陷阱：P/E 低于此且盈利强劲
CYCLICAL_PE_DISTRESS = _env_float("CYCLICAL_PE_DISTRESS", 30.0)
CYCLICAL_PE_TRAP_MAX = _env_float("CYCLICAL_PE_TRAP_MAX", 15.0)
CYCLICAL_EARNINGS_STRONG = _env_float("CYCLICAL_EARNINGS_STRONG", 0.15)  # 盈利同比 ≥15%
CYCLICAL_INV_GAP_TOP = _env_float("CYCLICAL_INV_GAP_TOP", 0.05)  # 存货增速领先销售 ≥5pp
CYCLICAL_INV_SALES_MULT = _env_float("CYCLICAL_INV_SALES_MULT", 2.0)  # 或存货增速 > 销售×2
CYCLICAL_PE_VS_5Y_TRAP = _env_float("CYCLICAL_PE_VS_5Y_TRAP", 0.85)  # P/E ≤ 5y均×此值
CYCLICAL_TRAP_PEG_MAX = _env_float("CYCLICAL_TRAP_PEG_MAX", 0.8)  # 低PEG+存货堆
CYCLICAL_PEAK_TOLERANCE = _env_float("CYCLICAL_PEAK_TOLERANCE", 0.05)  # 净利≈近年最高
# DIO（存货周转天数）近期拉长超过此比例 → 周期见顶隐性库存红灯
CYCLICAL_DIO_WORSEN_THRESHOLD = _env_float("CYCLICAL_DIO_WORSEN_THRESHOLD", 0.15)

# ── 第二层 AI 漏斗：成本熔断 ───────────────────────────────────
# 每次最多调用 Gemini 做完整"四步叙述与裁决"的公司数量硬上限。
MAX_AI_ANALYSIS_COUNT = _env_int("MAX_AI_ANALYSIS_COUNT", 30)
# 超额时的排序口径：peg(从低到高，最划算) / net_cash(从高到低，安全垫最厚)
AI_SORT_KEY = _env_str("AI_SORT_KEY", "peg").lower()

# ── 默认市场重心（USD 资产为主；可通过 MARKET 环境变量覆盖）────────
# ALL = 美日混合 | US = 仅美股（SEC 抽样 + 非 .T 代码）| JP = 仅日股
DEFAULT_MARKET = _env_str("MARKET", "US").upper()
if DEFAULT_MARKET not in ("ALL", "US", "JP"):
    DEFAULT_MARKET = "US"


def correct_ticker(ticker: str) -> str:
    """应用硬编码纠错映射（大小写/后缀不敏感的精确匹配）。"""
    t = ticker.strip().upper()
    return TICKER_CORRECTIONS.get(t, t)
