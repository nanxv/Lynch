#!/usr/bin/env python3
"""彼得·林奇自动化流水线（GitHub Actions 后台，邮件版，双层漏斗）。

【立体化投研周期】坚决拒绝盘中实时告警/高频轮询，只有四种静默节奏：
  --mode daily      日报（周二~周六，美股收盘后）：纯代码量化，不调用 Gemini，秒级。
                    重点抓「单日暴跌>5%（特价机会）」「存货暴增（红灯）」。
  --mode weekly     周报（每周六）：启动 Gemini，对初筛前 N 只做完整林奇四步裁决 + 成本熔断。
  --mode quarterly  财报季会诊（1/4/7/10 月最后一天）：Gemini 侧重资产负债表环比恶化/改善 + CAGR 是否脱轨。
  --mode annual     年终审视（12/31）：Gemini 做持仓清理，审视成长性迁移，给出【剔除自选股池名单】。

【双层漏斗 / 沙漏机制】（--scope full 时）：
  全市场成分股(万级) → 第一层纯代码硬指标漏斗(刷掉95%) → 第二层 AI 上限熔断(≤MAX_AI_ANALYSIS_COUNT) → 邮件

【必看列表】watchlist.yaml 降级为"高优先级必看列表"：永远纳入分析、永远优先送 AI。

所有凭证/参数均从系统环境变量读取（适配 GitHub Secrets）：
  DATA_PROVIDER / MAX_AI_ANALYSIS_COUNT / UNIVERSE_SOURCES / MAX_UNIVERSE_SCAN / AI_SORT_KEY
  GEMINI_API_KEY / GEMINI_MODEL
  SMTP_SERVER / SMTP_PORT / SMTP_USERNAME / SMTP_PASSWORD / RECEIVER_EMAIL
"""

from __future__ import annotations

import argparse
import dataclasses
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:  # noqa: BLE001
    pass

from src.config_loader import load_config  # noqa: E402
from src.lynch import analyze_company, config, get_provider, llm, notify  # noqa: E402
from src.lynch.agent import LynchAnalysis  # noqa: E402
from src.lynch.config import correct_ticker  # noqa: E402
from src.lynch.data.base import QuickScreen  # noqa: E402
from src.lynch.fundamentals import FundamentalsError  # noqa: E402
from src.lynch.funnel import (  # noqa: E402
    cyclical_watch,
    fatal_warnings,
    first_funnel,
    is_quality_pick,
    rank_and_cap,
)
from src.lynch.llm import LLMError  # noqa: E402
from src.lynch.universe import get_universe  # noqa: E402

_FLAG_ICON = {"green": "🟢", "yellow": "🟡", "red": "🔴"}

# 需要调用 Gemini 的分析型模式（daily 为纯量化，不调用）。
_AI_MODES = ("weekly", "quarterly", "annual")

# 各投研周期注入 Gemini 的专项上下文（附加到用户提示，切换关注重点）。
_MODE_CONTEXT = {
    "weekly": "",
    "quarterly": (
        "现在是【财报季度会诊】时点（季度末总结）。除常规四步裁决外，请重点：\n"
        "1) 对比本季度【资产负债表】相较此前的恶化/改善——长期负债是否上升、股东权益是否被侵蚀、"
        "现金是否减少、存货是否堆积；\n"
        "2) 核查【利润复合增长率(CAGR)】是否较历史轨迹脱轨（明显放缓或异常加速），指出趋势拐点；\n"
        "3) 若资产负债表出现实质性恶化或增长脱轨，即便股价没动也要提前预警。"
    ),
    "annual": (
        "现在是【年终持仓清理】时点。请以一年为尺度严格重估：\n"
        "1) 公司分类是否发生迁移——原「快速增长型」是否已退化为「稳定增长型」甚至「衰退型」"
        "（长期增速掉出 20%~25% 区间即视为降级）；\n"
        "2) 对已丧失成长性/故事变坏的标的，明确给出【是否剔除自选股池】的结论与理由；\n"
        "3) 对仍具成长性的标的，确认新一年继续持有或加仓的逻辑。"
    ),
}

_MODE_TITLE = {
    "daily": "自选股监控日报",
    "weekly": "深度分析周报",
    "quarterly": "财报季度会诊",
    "annual": "年终持仓审视",
}

_SUBJECT_PREFIX = {
    "daily": "【彼得林奇自选股监控】日报",
    "weekly": "【彼得林奇深度分析】周报",
    "quarterly": "【彼得林奇财报季会诊】季报",
    "annual": "【彼得林奇年终审视】年报",
}

# AI 行动指令：(优先级, 展示标签, 配色, 关键词) —— 强制排序 买入→观察→持有→卖出。
# 关键词按英文标签优先匹配（无歧义），再退回中文。
_SIGNAL_SPECS = [
    (0, "🟢 强烈买入 (BUY NOW)", "#1e8449", ("BUY NOW", "BUYNOW", "强烈买入")),
    (1, "🟡 放入观察仓 (WATCHLIST)", "#b9770e", ("WATCHLIST", "观察仓", "观察")),
    (2, "⚪ 钝感持有 (HOLD)", "#566573", ("HOLD", "钝感持有", "持有")),
    (3, "🔴 坚决卖出/避开 (SELL/AVOID)", "#c0392b", ("SELL", "AVOID", "卖出", "避开")),
]
_SIGNAL_UNKNOWN_ORDER = 8
_SIGNAL_UNKNOWN_LABEL = "⚪ 待定（AI 未给出明确指令）"
_SIGNAL_UNKNOWN_COLOR = "#566573"


def extract_signal(narrative: str | None) -> tuple[int, str, str, str] | None:
    """从 Gemini 叙述末尾提取【行动指令】。返回 (优先级, 展示标签, 配色, 核心理由)。

    优先定位含「行动指令」的那一行；找不到则全文兜底扫描四类标签。无法识别返回 None。
    """
    if not narrative:
        return None
    signal_line = None
    for ln in narrative.splitlines():
        if "行动指令" in ln:
            signal_line = ln
            break
    scan = signal_line or narrative
    scan_up = scan.upper()

    matched = None
    for order, label, color, kws in _SIGNAL_SPECS:
        if any(kw.upper() in scan_up for kw in kws):
            matched = (order, label, color)
            break
    if matched is None:
        return None

    reason = ""
    if signal_line:
        cleaned = signal_line.strip().lstrip(">#*-• ").strip()
        cleaned = re.sub(r"^\*{0,2}【行动指令】\*{0,2}\s*", "", cleaned)
        parts = re.split(r"[：:]", cleaned, maxsplit=1)
        if len(parts) > 1:
            reason = parts[1].strip().strip("*` ")
    return (matched[0], matched[1], matched[2], reason)


def _verdict_dashboard(verdicts: list[tuple[int, str, str, str, str, str]]) -> str:
    """「🧠 智能体最终裁决看板（结论先行）」——按 买入→观察→持有→卖出→待定 分组置顶。

    verdicts 每项：(优先级, ticker, name, 标签, 配色, 理由)。
    """
    if not verdicts:
        return ""
    groups: dict[int, list[tuple[str, str, str, str, str]]] = {}
    for order, ticker, name, label, color, reason in verdicts:
        groups.setdefault(order, []).append((ticker, name, label, color, reason))

    lines = [f"> ## 🧠 智能体最终裁决看板（结论先行 · {len(verdicts)}只 AI 深度分析）", ">"]
    ordered_groups = [(o, lab, col) for o, lab, col, _ in _SIGNAL_SPECS]
    ordered_groups.append((_SIGNAL_UNKNOWN_ORDER, _SIGNAL_UNKNOWN_LABEL, _SIGNAL_UNKNOWN_COLOR))
    for order, label, color in ordered_groups:
        members = groups.get(order)
        if not members:
            continue
        lines.append(f'> **<span style="color:{color}">{label} · {len(members)}只</span>**')
        for ticker, name, _label, _color, reason in members:
            tail = f"：{reason}" if reason else ""
            lines.append(f'> - <b style="color:{color}">{ticker}｜{name}</b>{tail}')
        lines.append(">")
    lines.append("> *结论先行：先扫这里决定「该关注谁」，再往下翻对应公司的大白话详解。*")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


# ── 候选集构建 ─────────────────────────────────────────────────
def _watchlist(market: str) -> dict[str, tuple[str, str]]:
    """返回 {纠错后ticker: (name, note)}（必看列表）。"""
    out: dict[str, tuple[str, str]] = {}
    for s in load_config().stocks:
        if market != "ALL" and s.market.upper() != market:
            continue
        out[correct_ticker(s.ticker)] = (s.name, s.note)
    return out


def _market_filter(tickers: list[str], market: str) -> list[str]:
    if market == "JP":
        return [t for t in tickers if t.endswith(".T")]
    if market == "US":
        return [t for t in tickers if not t.endswith(".T")]
    return tickers


def _build_working_set(args, provider) -> tuple[list[QuickScreen], dict[str, tuple[str, str]], dict]:
    """返回 (待分析 QuickScreen 列表, 必看信息映射, 统计信息)。"""
    market = args.market.upper()
    stats = {"universe": 0, "survivors": 0}

    if args.tickers:
        watch = {correct_ticker(t): (t, "") for t in args.tickers}
    else:
        watch = _watchlist(market)

    if args.scope == "watchlist" or args.tickers:
        qs = [QuickScreen(ticker=t, name=n, is_priority=True) for t, (n, _) in watch.items()]
        return qs, watch, stats

    # ── 全市场海选 + 第一层漏斗 ──
    universe = _market_filter(get_universe(cap=args.max_universe), market)
    stats["universe"] = len(universe)
    survivors = first_funnel(universe, provider)
    stats["survivors"] = len(survivors)

    watch_tickers = set(watch)
    priority_qs: list[QuickScreen] = []
    seen_priority: set[str] = set()
    # 幸存者里属于必看列表的，标记为 priority
    for q in survivors:
        if q.ticker in watch_tickers:
            priority_qs.append(dataclasses.replace(q, is_priority=True))
            seen_priority.add(q.ticker)
    # 必看列表里没通过漏斗（或没抓到）的，补齐 quick_screen 后强制纳入
    for t in watch_tickers - seen_priority:
        q = provider.get_quick_screen(t)
        priority_qs.append(
            dataclasses.replace(q, is_priority=True)
            if q
            else QuickScreen(ticker=t, name=watch[t][0], is_priority=True)
        )
    non_priority = [q for q in survivors if q.ticker not in watch_tickers]
    return priority_qs + non_priority, watch, stats


# ── 渲染 ───────────────────────────────────────────────────────
def _flag_line(a: LynchAnalysis) -> str:
    parts = []
    for m in a.metrics.metrics:
        icon = _FLAG_ICON.get(m.flag, "⚪")
        val = "N/A" if m.value is None else m.value
        parts.append(f"{icon}{m.label.split(' ')[0]}={val}")
    return " · ".join(parts)


def _render_daily(a: LynchAnalysis, change_5d: float | None, priority: bool) -> str:
    f = a.fundamentals
    cur = f.currency or ""
    price = "N/A" if f.price is None else f"{f.price:,.2f} {cur}"
    pe = "N/A" if f.trailing_pe is None else f"{f.trailing_pe:.2f}"
    peg = "N/A" if a.metrics.peg is None else f"{a.metrics.peg:.2f}"
    chg = "N/A" if change_5d is None else f"{change_5d * 100:+.1f}%"
    star = "⭐ " if priority else ""
    return (
        f"### {star}{a.ticker} — {f.name or a.ticker}\n\n"
        f"现价 **{price}** ｜ 5日 {chg} ｜ 市盈率 {pe} ｜ PEG {peg}\n\n"
        f"`{_flag_line(a)}`\n\n---"
    )


def _render_weekly(a: LynchAnalysis, priority: bool, signal_label: str = "") -> str:
    star = "⭐ " if priority else ""
    tag = f"　｜　{signal_label}" if signal_label else ""
    lines = [f"## {star}{a.ticker} — {a.fundamentals.name or a.ticker}{tag}", "", f"`{_flag_line(a)}`", ""]
    if a.narrative:
        lines.append(a.narrative)
    else:
        lines.append("> ⚠️ 未配置 GEMINI_API_KEY 或已超 AI 上限，仅硬指标。")
        lines.append("")
        lines.append("```")
        lines.append(a.data_block)
        lines.append("```")
    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def _render_error(ticker: str, name: str, err: str) -> str:
    return f"## {ticker} — {name}\n\n> ❌ 分析失败：{err}\n\n---"


def _red_flag_block(reds: list[tuple[str, str, list[str]]]) -> str:
    """置顶「🔴 致命红灯排雷」高能预警板块（日报/周报通用）。"""
    if not reds:
        return "> **🟢 全场无致命红灯** —— 本次扫描的标的暂未触发存货暴增/负债超标/增长暴跌。\n\n---\n\n"
    lines = [f"> ## 🔴🔴 致命红灯排雷（{len(reds)}只 · 置顶必看）", ">"]
    for ticker, name, reasons in reds:
        lines.append(
            f"> - <b style=\"color:#c0392b\">🔴 {ticker}｜{name}</b>：**{ '；'.join(reasons) }**"
        )
    lines.append(">")
    lines.append("> *即使是你原本看好的股票，一旦基本面故事变坏，也会第一时间出现在这里。*")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _recommend_block(recs: list[tuple[str, str, float | None, str]]) -> str:
    """置顶「🟢 推荐深挖的优质股」板块（PEG 从低到高，估值最划算优先）。"""
    if not recs:
        return (
            "> ## 🟢 推荐深挖的优质股\n>\n"
            "> 本次扫描暂无同时满足「PEG≤1 + 低负债 + 正现金流」的标的。宁可空仓，不追贵股。\n\n---\n\n"
        )
    lines = [f"> ## 🟢🟢 推荐深挖的优质股（{len(recs)}只 · 估值划算优先）", ">"]
    for ticker, name, _peg, reason in recs:
        lines.append(f"> - <b style=\"color:#1e8449\">🟢 {ticker}｜{name}</b>：{reason}")
    lines.append(">")
    lines.append("> *这些是「故事好+数字便宜」的候选；买入前请做 2 分钟演练，用大白话讲清买入理由。*")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _cyclical_block(cycs: list[tuple[str, str, str]]) -> str:
    """「🌀 周期型公司 - 行业低谷观察期」板块。豁免常规排雷的周期股单列于此。"""
    if not cycs:
        return ""
    lines = [f"> ## 🌀 周期型公司 · 行业低谷观察期（{len(cycs)}只）", ">"]
    for ticker, name, reason in cycs:
        lines.append(f"> - <b style=\"color:#b9770e\">🌀 {ticker}｜{name}</b>：{reason}")
    lines.append(">")
    lines.append("> *周期股反向操作：利润最差、P/E最高时往往是底部；别在利润最漂亮时追。*")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def build_briefing(mode, date_str, red_block, sections, stats, counts) -> str:
    now = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M JST")
    title = _MODE_TITLE.get(mode, "自选股监控日报")
    funnel = ""
    if stats.get("universe"):
        funnel = f"｜ 漏斗 {stats['universe']}→{stats['survivors']}只"
    header = [
        f"# 🎩 彼得·林奇 {title}",
        "",
        f"{date_str} ｜ 生成于 {now} ｜ 分析 {counts['analyzed']}只"
        f"（AI {counts['ai']} / 仅硬指标 {counts['data_only']}）{funnel}",
        "",
        "> 数据来源：Yahoo Finance ｜ 铁律：数据为王，不预测宏观，长线持有。",
        "",
        "---",
        "",
    ]
    return "\n".join(header) + red_block + "\n".join(sections)


def main() -> int:
    parser = argparse.ArgumentParser(description="彼得·林奇自动化流水线（双层漏斗·邮件版）")
    parser.add_argument("--mode", choices=["daily", "weekly", "quarterly", "annual"],
                        default="daily")
    parser.add_argument("--scope", choices=["watchlist", "full"], default="watchlist",
                        help="watchlist=仅必看列表 / full=全市场双层漏斗")
    parser.add_argument("--market", default="ALL", help="ALL / JP / US")
    parser.add_argument("--tickers", nargs="*", help="手动指定代码，覆盖清单")
    parser.add_argument("--max-universe", type=int, default=None, help="海选池上限（覆盖 MAX_UNIVERSE_SCAN）")
    parser.add_argument("--no-email", action="store_true", help="不发邮件（仅打印）")
    args = parser.parse_args()

    provider = get_provider()
    ai_mode = args.mode in _AI_MODES
    mode_context = _MODE_CONTEXT.get(args.mode, "")
    ai_available = ai_mode and llm.is_configured()
    if ai_mode and not ai_available:
        print(f"⚠️  {args.mode} 模式但未检测到 GEMINI_API_KEY，本次全部降级为仅硬指标。\n")

    print(f"📡 [{args.mode}/{args.scope}] 数据源={provider.name} market={args.market}\n")
    working, watch, stats = _build_working_set(args, provider)
    if not working:
        print("⚠️  没有可分析的标的。")
        return 0

    # 第二层漏斗：决定谁走 AI
    if ai_available:
        ai_qs, data_only_qs = rank_and_cap(working)
        ai_tickers = {q.ticker for q in ai_qs}
    else:
        ai_tickers = set()

    counts = {"analyzed": 0, "ai": 0, "data_only": 0}
    # entries: (排序键组, 生成序号, 详情文本)；排序键组决定详情区块的重排序。
    entries: list[tuple[int, int, str]] = []
    verdicts: list[tuple[int, str, str, str, str, str]] = []  # (优先级,ticker,name,标签,配色,理由)
    reds: list[tuple[str, str, list[str]]] = []
    recs: list[tuple[str, str, float | None, str]] = []
    cycs: list[tuple[str, str, str]] = []

    for seq, q in enumerate(working):
        name = watch.get(q.ticker, (q.name or q.ticker, ""))[0]
        note = watch.get(q.ticker, ("", ""))[1]
        use_ai = q.ticker in ai_tickers
        try:
            a = analyze_company(
                q.ticker, user_note=note, data_only=not use_ai, provider=provider,
                mode_context=mode_context if use_ai else "",
            )
            counts["analyzed"] += 1
            counts["ai" if use_ai else "data_only"] += 1
            display_name = a.fundamentals.name or name

            fw = fatal_warnings(a.fundamentals, a.metrics)
            if fw:
                reds.append((q.ticker, display_name, fw))
            ok, reason = is_quality_pick(a.fundamentals, a.metrics, fw)
            if ok:
                recs.append((q.ticker, display_name, a.metrics.peg, reason))
            # 周期股（豁免常规排雷）单列到「行业低谷观察期」，避免凭空消失
            if not fw:
                cyc = cyclical_watch(a.fundamentals, a.metrics)
                if cyc:
                    cycs.append((q.ticker, display_name, cyc))

            if ai_mode:
                sig = extract_signal(a.narrative)
                if sig:
                    order, label, color, sig_reason = sig
                    verdicts.append((order, q.ticker, display_name, label, color, sig_reason))
                    entries.append((order, seq, _render_weekly(a, q.is_priority, label)))
                elif a.narrative:
                    # AI 有输出但没给出可识别指令 → 待定组
                    verdicts.append((_SIGNAL_UNKNOWN_ORDER, q.ticker, display_name,
                                     _SIGNAL_UNKNOWN_LABEL, _SIGNAL_UNKNOWN_COLOR, ""))
                    entries.append((_SIGNAL_UNKNOWN_ORDER, seq, _render_weekly(a, q.is_priority)))
                else:
                    # 降级为仅硬指标（无 key / 超 AI 上限）→ 排在最后
                    entries.append((_SIGNAL_UNKNOWN_ORDER + 1, seq, _render_weekly(a, q.is_priority)))
            else:
                change = None
                try:
                    change = provider.get_stock_price_change(q.ticker, "5d")
                except Exception:  # noqa: BLE001
                    pass
                entries.append((seq, seq, _render_daily(a, change, q.is_priority)))
        except (FundamentalsError, LLMError) as exc:
            print(f"  ❌ {q.ticker}: {exc}")
            entries.append((99, seq, _render_error(q.ticker, name, str(exc))))
        except Exception as exc:  # noqa: BLE001
            # 铁律：单只股票的任何意外错误都不得拖垮整条流水线。
            print(f"  ❌ {q.ticker} 意外错误：{exc}\n{traceback.format_exc()}")
            entries.append((99, seq, _render_error(q.ticker, name, f"意外错误: {exc}")))

    # 详情区块同频重排序：AI 模式按 买入→观察→持有→卖出→待定→错误；日报保持原序。
    entries.sort(key=lambda e: (e[0], e[1]))
    sections = [e[2] for e in entries]

    # AI 裁决看板按优先级排序
    verdicts.sort(key=lambda v: v[0])

    # 优质股按 PEG 从低到高排序，最多展示 20 只
    recs.sort(key=lambda r: r[2] if r[2] is not None else float("inf"))
    recs = recs[:20]

    date_str = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y年%m月%d日")
    subject = f"{_SUBJECT_PREFIX.get(args.mode, _SUBJECT_PREFIX['daily'])} - {date_str}"
    # 主题带上优质股/红灯数量，手机推送一眼可见
    tags = []
    buy_count = sum(1 for v in verdicts if v[0] == 0)
    if buy_count:
        tags.append(f"🧠{buy_count}只强买")
    if recs:
        tags.append(f"🟢{len(recs)}只优质")
    if reds:
        tags.append(f"🔴{len(reds)}只排雷")
    if cycs:
        tags.append(f"🌀{len(cycs)}只周期")
    if tags:
        subject += "（" + "·".join(tags) + "）"

    # 顶部顺序：优质股 → 致命红灯 → 🧠AI裁决看板（结论先行）→ 周期观察
    top_block = (
        _recommend_block(recs)
        + _red_flag_block(reds)
        + _verdict_dashboard(verdicts)
        + _cyclical_block(cycs)
    )
    briefing = build_briefing(args.mode, date_str, top_block, sections, stats, counts)

    print("\n" + "=" * 60)
    print(f"主题：{subject}")
    print(briefing)
    print("=" * 60 + "\n")

    if args.no_email:
        print("ℹ️  --no-email：已跳过邮件发送。")
    else:
        notify.send_email(subject, briefing)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
