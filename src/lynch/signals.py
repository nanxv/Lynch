"""AI 行动指令提取与估值一票否决（防止叙述与标签精神分裂）。"""

from __future__ import annotations

import re

from .metrics import LynchMetrics

# (优先级, 展示标签, 配色) —— 数字越小越优先展示
SIGNAL_BUY = 0
SIGNAL_WATCH = 1
SIGNAL_HOLD = 2
SIGNAL_SELL = 3
SIGNAL_UNKNOWN = 8

SIGNAL_SPECS: list[tuple[int, str, str, tuple[str, ...]]] = [
    (SIGNAL_BUY, "🟢 强烈买入 (BUY NOW)", "#1e8449", ("BUY NOW", "BUYNOW", "强烈买入")),
    (SIGNAL_WATCH, "🟡 放入观察仓 (WATCHLIST)", "#b9770e", ("WATCHLIST", "观察仓")),
    (SIGNAL_HOLD, "⚪ 钝感持有 (HOLD)", "#566573", ("钝感持有",)),
    (SIGNAL_SELL, "🔴 坚决卖出/避开 (SELL/AVOID)", "#c0392b", ("SELL/AVOID", "SELL", "AVOID", "坚决卖出", "卖出/避开", "卖出", "避开")),
]

SIGNAL_UNKNOWN_LABEL = "⚪ 待定（AI 未给出明确指令）"
SIGNAL_UNKNOWN_COLOR = "#566573"

# 行内 emoji → 信号（最可靠，优先于关键词）
_EMOJI_TO_SIGNAL: dict[str, tuple[int, str, str]] = {
    "🟢": (SIGNAL_BUY, "🟢 强烈买入 (BUY NOW)", "#1e8449"),
    "🟡": (SIGNAL_WATCH, "🟡 放入观察仓 (WATCHLIST)", "#b9770e"),
    "⚪": (SIGNAL_HOLD, "⚪ 钝感持有 (HOLD)", "#566573"),
    "🔴": (SIGNAL_SELL, "🔴 坚决卖出/避开 (SELL/AVOID)", "#c0392b"),
}

# 估值否决：叙述中出现这些词且指令为强买时，强制调降
_HIGH_VALUATION_PHRASES = (
    "高位接盘", "飞得太高", "预期打满", "透支", "畸高", "估值过高",
    "太贵", "偏贵", "不便宜", "没有安全边际", "击球的好时机已过",
)


def _classify_line(line: str) -> tuple[int, str, str] | None:
    """从单行文本判定行动指令。emoji 优先，再按 卖→买→观察→持有 顺序匹配（避免「持有」误伤）。"""
    for emoji, spec in _EMOJI_TO_SIGNAL.items():
        if emoji in line:
            return spec

    up = line.upper()
    # 卖 → 买 → 观察 → 持有（HOLD 仅匹配完整短语，不用裸「持有」）
    for order, label, color, kws in sorted(SIGNAL_SPECS, key=lambda x: -x[0]):
        for kw in kws:
            if kw.upper() in up or kw in line:
                return (order, label, color)
    if "HOLD" in up and "WATCH" not in up:
        return _EMOJI_TO_SIGNAL["⚪"]
    return None


def _find_signal_line(narrative: str) -> str | None:
    """定位最终行动指令行：必须取最后一条，绝不能取章节标题或 SOP 模板。"""
    lines = narrative.splitlines()

    # 1) 最可靠：最后一行含【行动指令】
    for ln in reversed(lines):
        if re.search(r"【\s*行动指令\s*】", ln):
            return ln.strip()

    # 2) 最后一行同时含「行动指令」+ 四色 emoji 之一（排除纯章节标题）
    for ln in reversed(lines):
        if "行动指令" in ln and any(e in ln for e in _EMOJI_TO_SIGNAL):
            return ln.strip()

    # 3) 文末 25 行内最后一行能分类出信号的行
    for ln in reversed(lines[-25:]):
        if _classify_line(ln):
            return ln.strip()
    return None


def extract_signal(narrative: str | None) -> tuple[int, str, str, str] | None:
    """从 Gemini 叙述末尾提取【行动指令】。返回 (优先级, 展示标签, 配色, 核心理由)。"""
    if not narrative:
        return None
    signal_line = _find_signal_line(narrative)
    if not signal_line:
        return None

    matched = _classify_line(signal_line)
    if matched is None:
        return None

    reason = ""
    cleaned = signal_line.strip().lstrip(">#*-• ").strip()
    cleaned = re.sub(r"^\*{0,2}【\s*行动指令\s*】\*{0,2}\s*", "", cleaned)
    cleaned = re.sub(r"^#{1,6}\s*", "", cleaned)
    parts = re.split(r"[：:]", cleaned, maxsplit=1)
    if len(parts) > 1:
        reason = parts[1].strip().strip("*` ")
    return (matched[0], matched[1], matched[2], reason)


def enforce_valuation_veto(
    signal: tuple[int, str, str, str],
    metrics: LynchMetrics,
    narrative: str | None = None,
) -> tuple[int, str, str, str]:
    """估值一票否决：PEG 畸高或叙述明确高位风险时，严禁保留「强烈买入」。"""
    order, label, color, reason = signal
    if order != SIGNAL_BUY:
        return signal

    peg = metrics.peg
    peg_m = metrics.by_key("peg")
    veto_notes: list[str] = []

    if peg is not None and peg > 2.0:
        veto_notes.append(f"股息修正PEG {peg:.2f}>2")
    elif peg_m and peg_m.flag == "red":
        veto_notes.append("PEG 红灯")

    if narrative:
        tail = narrative[-1200:]
        if any(p in tail for p in _HIGH_VALUATION_PHRASES):
            veto_notes.append("叙述判定估值偏高/高位接盘风险")

    if not veto_notes:
        return signal

    note = "；".join(veto_notes)
    merged = f"{reason}（估值否决：{note}，已从强买调降为观察仓）" if reason else f"估值否决：{note}"
    return (SIGNAL_WATCH, "🟡 放入观察仓 (WATCHLIST)", "#b9770e", merged)


def resolve_action_signal(
    narrative: str | None,
    metrics: LynchMetrics,
) -> tuple[int, str, str, str] | None:
    """提取 + 估值否决，保证看板与详情标签一致。"""
    sig = extract_signal(narrative)
    if sig is None:
        return None
    return enforce_valuation_veto(sig, metrics, narrative)
