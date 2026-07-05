#!/usr/bin/env python3
"""彼得·林奇自动化流水线（GitHub Actions 后台，邮件版，双层漏斗）。

【立体化投研周期】坚决拒绝盘中实时告警/高频轮询，五种静默节奏：
  --mode daily      日报：纯代码量化 + 全市场 SBI 狙击（例外调 Gemini）
  --mode weekly     周报：年度口径 + Gemini 四步裁决
  --mode monthly    月报（月末最后交易日）：价量/RSI/PEG 漂移 + Gemini 动量会诊
  --mode quarterly  季报：真实季度财报 QoQ/YoY + Gemini 财报季会诊
  --mode annual     年报：5-10 年长周期资本配置 + Gemini 清仓审视

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
    check_daily_sniper_trigger,
    cyclical_watch,
    fatal_warnings,
    first_funnel,
    is_quality_pick,
    rank_and_cap,
)
from src.lynch.history import (  # noqa: E402
    append_record,
    build_story_diff_context,
    load_previous,
    record_from_analysis,
)
from src.lynch.sniper import run_sniper_alert  # noqa: E402
from src.lynch.signals import (  # noqa: E402
    SIGNAL_UNKNOWN_COLOR,
    SIGNAL_UNKNOWN_LABEL,
    SIGNAL_UNKNOWN,
    extract_signal,
    fcf_yield,
)
from src.lynch.llm import LLMError  # noqa: E402
from src.lynch.report_modes import (  # noqa: E402
    AI_MODES,
    MODE_TITLES,
    SUBJECT_PREFIX,
    is_ai_mode,
    is_last_trading_day_of_month,
    normalize_mode,
)
from src.lynch.market_calendar import (  # noqa: E402
    expected_daily_session_date,
    should_run_daily_report,
)
from src.lynch.universe import get_universe  # noqa: E402

_FLAG_ICON = {"green": "🟢", "yellow": "🟡", "red": "🔴"}

# AI 行动指令常量（与 src/lynch/signals.py 同步，供本脚本渲染兜底）
_SIGNAL_UNKNOWN_ORDER = SIGNAL_UNKNOWN


# ── 候选集构建 ─────────────────────────────────────────────────
def _watchlist(market: str) -> dict[str, tuple[str, str, str]]:
    """返回 {纠错后ticker: (name, note, user_status)}（必看列表）。"""
    out: dict[str, tuple[str, str, str]] = {}
    for s in load_config().stocks:
        if market != "ALL" and s.market.upper() != market:
            continue
        out[correct_ticker(s.ticker)] = (s.name, s.note, s.user_status)
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
        watch = {correct_ticker(t): (t, "", "watch") for t in args.tickers}
    else:
        watch = _watchlist(market)

    if args.scope == "watchlist" or args.tickers:
        qs = [QuickScreen(ticker=t, name=n, is_priority=True) for t, (n, _, _) in watch.items()]
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


def build_briefing(
    mode,
    date_str,
    red_block,
    main_sections,
    hardcore_sections,
    stats,
    counts,
    *,
    ai_mode: bool = False,
    flat_sections: list[str] | None = None,
    us_session_date: str | None = None,
) -> str:
    now = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M JST")
    title = MODE_TITLES.get(mode, "自选股监控日报")
    funnel = ""
    if stats.get("universe"):
        funnel = f"｜ 漏斗 {stats['universe']}→{stats['survivors']}只"
    session_line = ""
    if mode == "daily" and us_session_date:
        session_line = f"> 数据对齐：美东 **{us_session_date}** 收盘（NYSE 常规时段；休市日自动停报）"
    header = [
        f"# 🎩 彼得·林奇 {title}",
        "",
        f"{date_str} ｜ 生成于 {now} ｜ 分析 {counts['analyzed']}只"
        f"（AI {counts['ai']} / 仅硬指标 {counts['data_only']}）{funnel}",
        "",
        "> 数据来源：Yahoo Finance ｜ 铁律：数据为王，不预测宏观，长线持有。",
    ]
    if session_line:
        header.append(session_line)
    header.extend(["", "---", ""])
    detail = notify.render_dual_track_detail_sections(
        main_sections, hardcore_sections, ai_mode=ai_mode, flat_sections=flat_sections,
    )
    return "\n".join(header) + red_block + detail


def main() -> int:
    parser = argparse.ArgumentParser(description="彼得·林奇自动化流水线（双层漏斗·邮件版）")
    parser.add_argument("--mode", choices=["daily", "weekly", "monthly", "quarterly", "annual"],
                        default="daily")
    parser.add_argument("--scope", choices=["watchlist", "full"], default="watchlist",
                        help="watchlist=仅必看列表 / full=全市场双层漏斗")
    parser.add_argument("--market", default=None,
                        help="ALL / JP / US（默认读 MARKET 环境变量，未设则为 US）")
    parser.add_argument("--tickers", nargs="*", help="手动指定代码，覆盖清单")
    parser.add_argument("--max-universe", type=int, default=None, help="海选池上限（覆盖 MAX_UNIVERSE_SCAN）")
    parser.add_argument("--no-email", action="store_true", help="不发邮件（仅打印）")
    parser.add_argument("--force", action="store_true", help="跳过月报「月末交易日」门禁（调试用）")
    args = parser.parse_args()

    if args.market is None:
        args.market = config.DEFAULT_MARKET

    args.mode = normalize_mode(args.mode)
    if args.mode == "daily" and not args.force:
        ok, info = should_run_daily_report()
        if not ok:
            print(f"ℹ️  日报跳过：{info}（可用 --force 强制执行）")
            return 0
    if args.mode == "monthly" and not args.force and not is_last_trading_day_of_month():
        print("ℹ️  今日非当月最后交易日，月报跳过（可用 --force 强制执行）。")
        return 0

    provider = get_provider()
    ai_mode = is_ai_mode(args.mode)
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
    # entries: (排序键, 序号, 详情文本, sbi_tradable)
    entries: list[tuple[int, int, str, bool]] = []
    verdicts: list[tuple[int, str, str, str, str, str, bool]] = []
    reds: list[tuple[str, str, list[str]]] = []
    recs: list[tuple[str, str, float | None, str]] = []
    cycs: list[tuple[str, str, str]] = []

    for seq, q in enumerate(working):
        name = watch.get(q.ticker, (q.name or q.ticker, "", "watch"))[0]
        note = watch.get(q.ticker, ("", "", "watch"))[1]
        user_status = watch.get(q.ticker, ("", "", "watch"))[2]
        use_ai = q.ticker in ai_tickers
        story_ctx = ""
        if use_ai and ai_mode:
            prev = load_previous(q.ticker)
            if prev:
                story_ctx = build_story_diff_context(prev)
        try:
            a = analyze_company(
                q.ticker, user_note=note, data_only=not use_ai, provider=provider,
                user_status=user_status,
                story_diff_context=story_ctx,
                report_mode=args.mode,
            )
            counts["analyzed"] += 1
            counts["ai" if use_ai else "data_only"] += 1
            display_name = a.fundamentals.name or name
            sbi_ok = a.metrics.sbi_tradable

            try:
                sig_hist = extract_signal(a.narrative) if a.narrative else None
                append_record(record_from_analysis(
                    a.ticker,
                    a.metrics,
                    signal_label=sig_hist[1] if sig_hist else None,
                    signal_order=sig_hist[0] if sig_hist else None,
                ))
            except Exception as exc:  # noqa: BLE001
                print(f"  ⚠️  {q.ticker} 历史存档失败：{exc}")

            fw = fatal_warnings(a.fundamentals, a.metrics)
            if fw:
                reds.append((q.ticker, display_name, fw, a.metrics.company_type))
            ok, reason = is_quality_pick(a.fundamentals, a.metrics, fw)
            if ok:
                recs.append((q.ticker, display_name, a.metrics.peg, reason, a.metrics.company_type))
            if not fw:
                cyc = cyclical_watch(a.fundamentals, a.metrics)
                if cyc:
                    cycs.append((q.ticker, display_name, cyc))

            if ai_mode:
                peg = a.metrics.peg
                fcf_y = fcf_yield(a.fundamentals.market_cap, a.fundamentals.free_cashflow)
                if use_ai:
                    sig = extract_signal(a.narrative)
                    if sig:
                        order, label, color, sig_reason = sig
                        verdicts.append((
                            order, q.ticker, display_name, label, color, sig_reason, sbi_ok, peg, fcf_y,
                        ))
                        entries.append((order, seq, _render_weekly(a, q.is_priority, label), sbi_ok))
                    else:
                        verdicts.append((
                            _SIGNAL_UNKNOWN_ORDER, q.ticker, display_name,
                            _SIGNAL_UNKNOWN_LABEL, _SIGNAL_UNKNOWN_COLOR, "", sbi_ok, peg, fcf_y,
                        ))
                        entries.append((
                            _SIGNAL_UNKNOWN_ORDER, seq,
                            _render_weekly(a, q.is_priority, _SIGNAL_UNKNOWN_LABEL if a.narrative else ""),
                            sbi_ok,
                        ))
                else:
                    entries.append((_SIGNAL_UNKNOWN_ORDER + 1, seq, _render_weekly(a, q.is_priority), sbi_ok))
            else:
                change = None
                day_change = None
                try:
                    change = provider.get_stock_price_change(q.ticker, "5d")
                    day_change = provider.get_daily_price_change(q.ticker)
                except Exception:  # noqa: BLE001
                    pass
                if (
                    args.mode == "daily"
                    and check_daily_sniper_trigger(a.fundamentals, a.metrics, day_change)
                ):
                    try:
                        run_sniper_alert(
                            q.ticker,
                            provider=provider,
                            day_change=day_change,
                            user_status=user_status,
                            send=not args.no_email,
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(f"  ⚠️  {q.ticker} 狙击警报失败：{exc}")
                entries.append((seq, seq, _render_daily(a, change, q.is_priority), sbi_ok))
        except (FundamentalsError, LLMError) as exc:
            print(f"  ❌ {q.ticker}: {exc}")
            entries.append((99, seq, _render_error(q.ticker, name, str(exc)), True))
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ {q.ticker} 意外错误：{exc}\n{traceback.format_exc()}")
            entries.append((99, seq, _render_error(q.ticker, name, f"意外错误: {exc}"), True))

    entries.sort(key=lambda e: (e[0], e[1]))
    main_sections = [e[2] for e in entries if e[3]]
    hardcore_sections = [e[2] for e in entries if not e[3]]
    flat_sections = [e[2] for e in entries]

    verdicts.sort(key=lambda v: v[0])

    # 优质股按 PEG 从低到高排序，最多展示 20 只
    recs.sort(key=lambda r: r[2] if r[2] is not None else float("inf"))
    recs = recs[:20]

    date_str = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y年%m月%d日")
    subject = f"{SUBJECT_PREFIX.get(args.mode, SUBJECT_PREFIX['daily'])} - {date_str}"
    # 主题带上优质股/红灯数量，手机推送一眼可见
    tags = []
    buy_count = sum(1 for v in verdicts if v[0] == 0 and v[6])
    if buy_count:
        tags.append(f"🏦{buy_count}只SBI强买")
    sbi_buy = buy_count
    if sbi_buy == 0:
        buy_count_all = sum(1 for v in verdicts if v[0] == 0)
        if buy_count_all:
            tags.append(f"🧠{buy_count_all}只强买")
    if recs:
        tags.append(f"🟢{len(recs)}只优质")
    if reds:
        tags.append(f"🔴{len(reds)}只排雷")
    if cycs:
        tags.append(f"🌀{len(cycs)}只周期")
    if tags:
        subject += "（" + "·".join(tags) + "）"

    top_block = notify.render_briefing_summary(
        recs=recs,
        reds=reds,
        cycs=cycs,
        verdicts=verdicts,
        ai_count=counts["ai"],
        ai_mode=ai_mode,
    )
    us_session = expected_daily_session_date().isoformat() if args.mode == "daily" else None
    briefing = build_briefing(
        args.mode, date_str, top_block, main_sections, hardcore_sections, stats, counts,
        ai_mode=ai_mode, flat_sections=flat_sections, us_session_date=us_session,
    )

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
