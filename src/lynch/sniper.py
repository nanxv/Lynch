"""日间 Gmail 即时狙击预警：暴跌 + 低 PEG 击球区 → Gemini 两分钟演练 → 强买则加急邮件。"""

from __future__ import annotations

from . import llm, notify
from .agent import _system_prompt, build_data_block
from .data.base import BaseDataProvider, Fundamentals
from .llm import LLMError, SNIPER_DRILL_MAX_TOKENS
from .metrics import LynchMetrics, compute_metrics
from .signals import SIGNAL_BUY, extract_signal

SNIPER_DROP_THRESHOLD = -0.05  # 单日暴跌 > 5%
SNIPER_PEG_MAX = 0.5

_SNIPER_DRILL_PROMPT = """【紧急狙击 · 两分钟大白话演练】
该股今日暴跌且股息修正 PEG 已跌入极佳击球区（<0.5）。请彼得·林奇附体：

1. 用**三句话以内**做「两分钟演练」——像对邻居解释为什么现在可能是特价，为什么不是接飞刀。
2. 必须引用数据区块里的真实数字（跌幅、PEG、负债、存货）。
3. 最后一行**必须且只能**输出一个【行动指令】标签（🟢强烈买入 / 🟡观察仓 / 🔴卖出避开 / ⚪持有）。

若故事变坏或只是情绪杀跌但基本面恶化，不得给 🟢 强烈买入。"""


def is_sniper_candidate(
    f: Fundamentals,
    m: LynchMetrics,
    day_change: float | None,
) -> bool:
    """SBI 可交易 + 单日跌幅>5% + 即时 PEG<0.5。"""
    if not m.sbi_tradable:
        return False
    if day_change is None or day_change > SNIPER_DROP_THRESHOLD:
        return False
    if m.peg is None or m.peg >= SNIPER_PEG_MAX:
        return False
    return True


def run_sniper_alert(
    ticker: str,
    *,
    provider: BaseDataProvider,
    day_change: float | None = None,
    send: bool = True,
) -> bool:
    """触发狙击流程：Gemini 演练 → 强买则发加急 Gmail。返回是否已发信。"""
    if not llm.is_configured():
        print(f"  ℹ️  {ticker} 狙击触发但无 GEMINI_API_KEY，跳过加急邮件。")
        return False

    f = provider.get_fundamentals(ticker)
    m = compute_metrics(f)
    if day_change is None:
        day_change = provider.get_daily_price_change(ticker)
    if not is_sniper_candidate(f, m, day_change):
        return False

    data_block = build_data_block(f, m)
    chg_pct = f"{day_change * 100:.1f}%" if day_change is not None else "N/A"
    user_content = (
        f"{_SNIPER_DRILL_PROMPT}\n\n"
        f"今日单日跌幅：{chg_pct}\n\n{data_block}"
    )
    try:
        narrative = llm.generate(
            _system_prompt(), user_content, max_tokens=SNIPER_DRILL_MAX_TOKENS,
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
