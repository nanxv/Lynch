#!/usr/bin/env python3
"""彼得·林奇自动化流水线（GitHub Actions 后台，邮件版，双层漏斗）。

【运行频次铁律】坚决拒绝任何盘中实时告警/高频轮询。只有两种静默节奏：
  --mode daily   日报（周二~周六，美股收盘后）：纯代码量化，无 AI，秒级。
  --mode weekly  周报（周六中午）：全套 Claude 四步叙述与裁决 + 成本熔断。

【双层漏斗 / 沙漏机制】（--scope full 时）：
  全市场成分股(万级) → 第一层纯代码硬指标漏斗(刷掉95%) → 第二层 AI 上限熔断(≤MAX_AI_ANALYSIS_COUNT) → 邮件

【必看列表】watchlist.yaml 降级为"高优先级必看列表"：永远纳入分析、永远优先送 AI。

所有凭证/参数均从系统环境变量读取（适配 GitHub Secrets）：
  DATA_PROVIDER / MAX_AI_ANALYSIS_COUNT / UNIVERSE_SOURCES / MAX_UNIVERSE_SCAN / AI_SORT_KEY
  ANTHROPIC_API_KEY / ANTHROPIC_MODEL
  SMTP_SERVER / SMTP_PORT / SMTP_USERNAME / SMTP_PASSWORD / RECEIVER_EMAIL
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
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
from src.lynch.funnel import fatal_warnings, first_funnel, rank_and_cap  # noqa: E402
from src.lynch.llm import LLMError  # noqa: E402
from src.lynch.universe import get_universe  # noqa: E402

_FLAG_ICON = {"green": "🟢", "yellow": "🟡", "red": "🔴"}


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


def _render_weekly(a: LynchAnalysis, priority: bool) -> str:
    star = "⭐ " if priority else ""
    lines = [f"## {star}{a.ticker} — {a.fundamentals.name or a.ticker}", "", f"`{_flag_line(a)}`", ""]
    if a.narrative:
        lines.append(a.narrative)
    else:
        lines.append("> ⚠️ 未配置 ANTHROPIC_API_KEY 或已超 AI 上限，仅硬指标。")
        lines.append("")
        lines.append("```")
        lines.append(a.data_block)
        lines.append("```")
    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def _render_error(ticker: str, name: str, err: str) -> str:
    return f"## {ticker} — {name}\n\n> ❌ 分析失败：{err}\n\n---"


def _red_flag_block(reds: dict[str, list[str]]) -> str:
    """置顶「本周 🔴 红灯排雷标的」高能预警板块（日报/周报通用）。"""
    if not reds:
        return (
            "> **🟢 全场无致命红灯** —— 本次扫描的标的暂未触发存货暴增/负债超标/增长暴跌。\n\n---\n\n"
        )
    lines = ["> ## 🔴🔴 致命红灯排雷（置顶必看）", ">"]
    for ticker, reasons in reds.items():
        lines.append(f"> - <b style=\"color:#c0392b\">🔴 {ticker}</b>：**{ '；'.join(reasons) }**")
    lines.append(">")
    lines.append("> *即使是你原本看好的股票，一旦基本面故事变坏，也会第一时间出现在这里。*")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def build_briefing(mode, date_str, red_block, sections, stats, counts) -> str:
    now = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M JST")
    title = "深度分析周报" if mode == "weekly" else "自选股监控日报"
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
    parser.add_argument("--mode", choices=["daily", "weekly"], default="daily")
    parser.add_argument("--scope", choices=["watchlist", "full"], default="watchlist",
                        help="watchlist=仅必看列表 / full=全市场双层漏斗")
    parser.add_argument("--market", default="ALL", help="ALL / JP / US")
    parser.add_argument("--tickers", nargs="*", help="手动指定代码，覆盖清单")
    parser.add_argument("--max-universe", type=int, default=None, help="海选池上限（覆盖 MAX_UNIVERSE_SCAN）")
    parser.add_argument("--no-email", action="store_true", help="不发邮件（仅打印）")
    args = parser.parse_args()

    provider = get_provider()
    weekly = args.mode == "weekly"
    ai_available = weekly and llm.is_configured()
    if weekly and not ai_available:
        print("⚠️  周报模式但未检测到 ANTHROPIC_API_KEY，本次全部降级为仅硬指标。\n")

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
    sections: list[str] = []
    reds: dict[str, list[str]] = {}

    for q in working:
        name = watch.get(q.ticker, (q.name or q.ticker, ""))[0]
        note = watch.get(q.ticker, ("", ""))[1]
        use_ai = q.ticker in ai_tickers
        try:
            a = analyze_company(q.ticker, user_note=note, data_only=not use_ai, provider=provider)
            counts["analyzed"] += 1
            counts["ai" if use_ai else "data_only"] += 1

            fw = fatal_warnings(a.fundamentals, a.metrics)
            if fw:
                reds[q.ticker] = fw

            if weekly:
                sections.append(_render_weekly(a, q.is_priority))
            else:
                change = provider.get_stock_price_change(q.ticker, "5d")
                sections.append(_render_daily(a, change, q.is_priority))
        except (FundamentalsError, LLMError) as exc:
            print(f"  ❌ {q.ticker}: {exc}")
            sections.append(_render_error(q.ticker, name, str(exc)))

    date_str = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y年%m月%d日")
    subject = (
        f"【彼得林奇深度分析】周报 - {date_str}"
        if weekly
        else f"【彼得林奇自选股监控】日报 - {date_str}"
    )
    # 主题带上红灯数量，手机推送一眼可见
    if reds:
        subject += f"（🔴{len(reds)}只排雷）"

    briefing = build_briefing(args.mode, date_str, _red_flag_block(reds), sections, stats, counts)

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
