"""盘中实时狙击 + 收盘后日报狙击。"""

from __future__ import annotations

from typing import Any

from . import config, llm, notify
from .agent import _system_prompt, build_data_block
from .data.base import BaseDataProvider, Fundamentals
from .data.yahoo import YahooFinanceProvider
from .llm import LLMError, SNIPER_DRILL_MAX_TOKENS, build_task_prompt
from .metrics import LynchMetrics, compute_metrics
from .signals import SIGNAL_BUY, extract_signal
from .sniper_cache import already_alerted, mark_alerted
from .watchlist import normalize_user_status

SNIPER_DROP_THRESHOLD = -0.05
SNIPER_PEG_MAX = 0.5


def is_sniper_candidate(
    f: Fundamentals,
    m: LynchMetrics,
    day_change: float | None,
) -> bool:
    if not m.sbi_tradable:
        return False
    if day_change is None or day_change > SNIPER_DROP_THRESHOLD:
        return False
    if m.peg is None or m.peg >= SNIPER_PEG_MAX:
        return False
    return True


def is_realtime_candidate(snap: dict[str, Any]) -> bool:
    if not snap.get("sbi_tradable"):
        return False
    chg = snap.get("intraday_change")
    if chg is None or chg > SNIPER_DROP_THRESHOLD:
        return False
    peg = snap.get("instant_peg")
    if peg is None or peg >= SNIPER_PEG_MAX:
        return False
    return True


def run_realtime_sniper_alert(
    ticker: str,
    *,
    provider: BaseDataProvider,
    user_status: str = "watch",
    send: bool = True,
) -> bool:
    """盘中实时狙击：即时价/昨收跌幅 + 即时 PEG + 防刷 + Gmail。"""
    if not isinstance(provider, YahooFinanceProvider):
        provider = YahooFinanceProvider()
    if not llm.is_configured():
        print(f"  ℹ️  {ticker} 盘中狙击跳过：无 GEMINI_API_KEY")
        return False
    if already_alerted(ticker):
        print(f"  ℹ️  {ticker} 今日已发过盘中警报，跳过。")
        return False

    snap = provider.get_intraday_snapshot(ticker)
    if not is_realtime_candidate(snap):
        return False

    f = provider.get_fundamentals(ticker, mode="daily")
    m = compute_metrics(f)
    chg = snap["intraday_change"]
    chg_pct = f"{chg * 100:.1f}%"
    pe5y = snap.get("pe_5y_min")
    pe5y_s = f"{pe5y:.1f}" if pe5y is not None else "N/A"
    spot = snap.get("spot")
    price_s = f"{spot:.2f} {snap.get('currency') or ''}" if spot else "N/A"

    data_block = build_data_block(f, m)
    peg_val = snap.get("instant_peg")
    peg_line = f"{peg_val:.2f}" if peg_val is not None else "N/A"
    task = build_task_prompt("daily", normalize_user_status(user_status))
    user_content = (
        f"{task}\n\n"
        f"盘中即时跌幅（相对昨收）: {chg_pct}\n"
        f"即时股息修正 PEG: {peg_line}\n"
        f"5年历史最低隐含 P/E: {pe5y_s}\n"
        f"即时现价: {price_s}\n\n{data_block}\n\n"
        "请严格引用上面的真实数字，并在最末尾单独一行给出唯一的【行动指令】。"
    )
    try:
        deep_model, deep_tier = llm.resolve_deep_model_and_tier(config.GEMINI_PRO_MODEL)
        narrative = llm.generate(
            _system_prompt(),
            user_content,
            max_tokens=SNIPER_DRILL_MAX_TOKENS,
            model=deep_model,
            api_tier=deep_tier,
        )
    except LLMError as exc:
        print(f"  ⚠️  {ticker} 盘中 Gemini 失败：{exc}")
        return False

    sig = extract_signal(narrative)
    if not sig or sig[0] != SIGNAL_BUY:
        print(f"  ℹ️  {ticker} 盘中演练未确认强买，不发信。")
        return False

    name = snap.get("name") or ticker
    if send:
        notify.send_realtime_sniper_alert(
            ticker=ticker,
            name=name,
            change_pct=chg_pct,
            peg=snap.get("instant_peg"),
            price=price_s,
            pe_5y_min=pe5y,
            narrative=narrative,
        )
        mark_alerted(ticker)
        print(f"  🚨 {ticker} 盘中深夜特快已发送！")
    return True


def run_sniper_alert(
    ticker: str,
    *,
    provider: BaseDataProvider,
    day_change: float | None = None,
    user_status: str = "watch",
    send: bool = True,
) -> bool:
    """收盘后日报狙击（非盘中）。"""
    if not llm.is_configured():
        print(f"  ℹ️  {ticker} 狙击触发但无 GEMINI_API_KEY，跳过加急邮件。")
        return False

    f = provider.get_fundamentals(ticker, mode="daily")
    m = compute_metrics(f)
    if day_change is None:
        day_change = provider.get_daily_price_change(ticker)
    if not is_sniper_candidate(f, m, day_change):
        return False

    data_block = build_data_block(f, m)
    chg_pct = f"{day_change * 100:.1f}%" if day_change is not None else "N/A"
    task = build_task_prompt("daily", normalize_user_status(user_status))
    user_content = (
        f"{task}\n\n"
        f"今日单日跌幅：{chg_pct}\n\n{data_block}\n\n"
        "请严格引用上面的真实数字，并在最末尾单独一行给出唯一的【行动指令】。"
    )
    try:
        deep_model, deep_tier = llm.resolve_deep_model_and_tier(config.GEMINI_PRO_MODEL)
        narrative = llm.generate(
            _system_prompt(),
            user_content,
            max_tokens=SNIPER_DRILL_MAX_TOKENS,
            model=deep_model,
            api_tier=deep_tier,
        )
    except LLMError as exc:
        print(f"  ⚠️  {ticker} 狙击 Gemini 失败：{exc}")
        return False

    sig = extract_signal(narrative)
    if not sig or sig[0] != SIGNAL_BUY:
        print(f"  ℹ️  {ticker} 狙击演练未确认强买，不发加急邮件。")
        return False

    price = f"{f.price:.2f} {f.currency or ''}" if f.price else "N/A"
    if send:
        notify.send_sniper_alert(
            ticker=ticker,
            name=f.name or ticker,
            day_change_pct=chg_pct,
            peg=m.peg,
            price=price,
            narrative=narrative,
        )
        print(f"  🚨 {ticker} 狙击加急邮件已发送！")
    return True
