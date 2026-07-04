"""AI 行动指令提取（绝对信任 Gemini 输出，Python 不做二次篡改）。"""

from __future__ import annotations

import re

# (优先级, 展示标签, 配色)
SIGNAL_BUY = 0
SIGNAL_WATCH = 1
SIGNAL_HOLD = 2
SIGNAL_SELL = 3
SIGNAL_UNKNOWN = 8

SIGNAL_SPECS: list[tuple[int, str, str, tuple[str, ...]]] = [
    (SIGNAL_BUY, "🟢 强烈买入 (BUY NOW)", "#1e8449", ("BUY NOW", "BUYNOW", "强烈买入")),
    (SIGNAL_WATCH, "🟡 放入观察仓 (WATCHLIST)", "#b9770e", ("WATCHLIST", "观察仓")),
    (SIGNAL_HOLD, "⚪ 钝感持有 (HOLD)", "#566573", ("钝感持有",)),
    (SIGNAL_SELL, "🔴 坚决卖出/避开 (SELL/AVOID)", "#c0392b", (
        "SELL/AVOID", "SELL", "AVOID", "坚决卖出", "卖出/避开", "卖出", "避开",
    )),
]

SIGNAL_UNKNOWN_LABEL = "⚪ 待定（AI 未给出明确指令）"
SIGNAL_UNKNOWN_COLOR = "#566573"

_EMOJI_TO_SIGNAL: dict[str, tuple[int, str, str]] = {
    "🟢": (SIGNAL_BUY, "🟢 强烈买入 (BUY NOW)", "#1e8449"),
    "🟡": (SIGNAL_WATCH, "🟡 放入观察仓 (WATCHLIST)", "#b9770e"),
    "⚪": (SIGNAL_HOLD, "⚪ 钝感持有 (HOLD)", "#566573"),
    "🔴": (SIGNAL_SELL, "🔴 坚决卖出/避开 (SELL/AVOID)", "#c0392b"),
}

_ACTION_LINE_RE = re.compile(r"【\s*行动指令\s*】\s*(.*)", re.DOTALL)


def _classify_text(text: str) -> tuple[int, str, str] | None:
    """从指令行文本判定标签；emoji 优先，卖→买→观察→持有。"""
    for emoji, spec in _EMOJI_TO_SIGNAL.items():
        if emoji in text:
            return spec
    up = text.upper()
    for order, label, color, kws in sorted(SIGNAL_SPECS, key=lambda x: -x[0]):
        for kw in kws:
            if kw.upper() in up or kw in text:
                return (order, label, color)
    if "HOLD" in up and "WATCH" not in up:
        return _EMOJI_TO_SIGNAL["⚪"]
    return None


def extract_signal(narrative: str | None) -> tuple[int, str, str, str] | None:
    """精准提取 Gemini 文末【行动指令】，原封不动归类，不做任何降级干预。

    返回 (优先级, 展示标签, 配色, 核心理由)。
    """
    if not narrative:
        return None

    # 取最后一条【行动指令】行（避免误读 SOP 模板或中间草稿）
    signal_line: str | None = None
    for ln in reversed(narrative.splitlines()):
        if re.search(r"【\s*行动指令\s*】", ln):
            signal_line = ln.strip()
            break
    if not signal_line:
        return None

    m = _ACTION_LINE_RE.search(signal_line)
    body = m.group(1).strip() if m else signal_line
    matched = _classify_text(body)
    if matched is None:
        return None

    reason = ""
    parts = re.split(r"[：:]", body, maxsplit=1)
    if len(parts) > 1:
        reason = parts[1].strip().strip("*` ")
    return (matched[0], matched[1], matched[2], reason)


def fcf_yield(market_cap: float | None, free_cashflow: float | None) -> float | None:
    """自由现金流收益率 = FCF / 市值（小数，0.148 = 14.8%）。"""
    if market_cap and market_cap > 0 and free_cashflow is not None:
        return free_cashflow / market_cap
    return None


def lynch_buy_sort_key(
    peg: float | None,
    fcf_y: float | None,
    ticker: str,
) -> tuple:
    """林奇强买排行：PEG 升序优先；无 PEG 则 FCF Yield 降序；皆无则最后。"""
    if peg is not None:
        return (0, peg, 0.0, ticker)
    if fcf_y is not None:
        return (1, 0.0, -fcf_y, ticker)
    return (2, 0.0, 0.0, ticker)


def format_lynch_metrics(peg: float | None, fcf_y: float | None) -> str:
    """看板行前缀：[PEG: 0.17 | FCF Yield: 14.8%]"""
    peg_s = f"{peg:.2f}" if peg is not None else "N/A"
    fcf_s = f"{fcf_y * 100:.1f}%" if fcf_y is not None else "N/A"
    return f"[PEG: {peg_s} | FCF Yield: {fcf_s}]"
