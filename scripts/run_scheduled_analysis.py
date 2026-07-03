#!/usr/bin/env python3
"""彼得·林奇自动化流水线（GitHub Actions 全自动后台，邮件版）。

双模策略：
  --mode daily   日报（周二~周六）：仅纯代码量化过滤（不调用 LLM），
                 监控自选股的每日股价/估值异动。邮件标题：
                 「【彼得林奇自选股监控】日报 - 年月日」
  --mode weekly  周报（周六）：开启全套 Claude 分析（四步叙述与裁决），
                 并在顶部置顶「本周 🔴 红灯排雷标的」摘要。邮件标题：
                 「【彼得林奇深度分析】周报 - 年月日」

所有密钥均从系统环境变量读取（适配 GitHub Secrets）：
  ANTHROPIC_API_KEY / ANTHROPIC_MODEL       Claude（缺失则自动降级为仅硬指标）
  SMTP_SERVER / SMTP_PORT / SMTP_USERNAME /
  SMTP_PASSWORD / RECEIVER_EMAIL            邮件发送凭证（缺失则只打印不发信）
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 本地开发时若存在 .env 则加载；GitHub Actions 上无 .env，直接用系统环境变量。
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:  # noqa: BLE001
    pass

from src.config_loader import StockEntry, load_config  # noqa: E402
from src.lynch import analyze_company, llm, notify  # noqa: E402
from src.lynch.agent import LynchAnalysis  # noqa: E402
from src.lynch.fundamentals import FundamentalsError  # noqa: E402
from src.lynch.llm import LLMError  # noqa: E402

_FLAG_ICON = {"green": "🟢", "yellow": "🟡", "red": "🔴"}


def _select_stocks(market: str, tickers: list[str] | None) -> list[StockEntry]:
    if tickers:
        return [
            StockEntry(ticker=t.upper(), name=t.upper(), market="?", tier=2)
            for t in tickers
        ]
    stocks = load_config().stocks
    market = market.upper()
    if market != "ALL":
        stocks = [s for s in stocks if s.market.upper() == market]
    return stocks


def _flag_line(analysis: LynchAnalysis) -> str:
    parts = []
    for m in analysis.metrics.metrics:
        icon = _FLAG_ICON.get(m.flag, "⚪")
        val = "N/A" if m.value is None else m.value
        parts.append(f"{icon}{m.label.split(' ')[0]}={val}")
    return " · ".join(parts)


def _red_metrics(analysis: LynchAnalysis) -> list[str]:
    return [m.label.split(" ")[0] for m in analysis.metrics.metrics if m.flag == "red"]


# ── Daily 渲染：紧凑量化异动 ────────────────────────────────────────
def _render_daily(entry: StockEntry, a: LynchAnalysis) -> str:
    f = a.fundamentals
    name = f.name or entry.name
    cur = f.currency or ""
    price = "N/A" if f.price is None else f"{f.price:,.2f} {cur}"
    pe = "N/A" if f.trailing_pe is None else f"{f.trailing_pe:.2f}"
    peg = "N/A" if a.metrics.peg is None else f"{a.metrics.peg:.2f}"
    reds = _red_metrics(a)
    red_str = f"　🔴 {'/'.join(reds)}" if reds else ""
    return (
        f"### {a.ticker} — {name}\n\n"
        f"现价 **{price}** ｜ 市盈率 {pe} ｜ PEG {peg}{red_str}\n\n"
        f"`{_flag_line(a)}`\n\n---"
    )


# ── Weekly 渲染：完整叙述 ──────────────────────────────────────────
def _render_weekly(entry: StockEntry, a: LynchAnalysis) -> str:
    name = a.fundamentals.name or entry.name
    lines = [f"## {a.ticker} — {name}", "", f"`{_flag_line(a)}`", ""]
    if a.narrative:
        lines.append(a.narrative)
    else:
        lines.append("> ⚠️ 未配置 ANTHROPIC_API_KEY，仅硬指标（无 LLM 叙述）。")
        lines.append("")
        lines.append("```")
        lines.append(a.data_block)
        lines.append("```")
    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def _render_error(entry: StockEntry, err: str) -> str:
    return f"## {entry.ticker} — {entry.name}\n\n> ❌ 分析失败：{err}\n\n---"


def _red_flag_summary(reds: dict[str, list[str]]) -> str:
    if not reds:
        return "## 🔴 本周红灯排雷\n\n本周自选股**无 🔴 红灯**指标，基本面暂无硬伤。\n\n---\n"
    lines = ["## 🔴 本周红灯排雷（置顶）", ""]
    for ticker, labels in reds.items():
        lines.append(f"- **{ticker}**：{ '、'.join(labels) }")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def build_briefing(mode: str, date_str: str, body_sections: list[str],
                   count: int, errors: int, red_summary: str = "") -> str:
    now = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M JST")
    title = "深度分析周报" if mode == "weekly" else "自选股监控日报"
    header = [
        f"# 🎩 彼得·林奇 {title}",
        "",
        f"{date_str} ｜ 生成于 {now} ｜ 标的数：{count} ｜ 失败：{errors}",
        "",
        "> 数据来源：Yahoo Finance ｜ 铁律：数据为王，不预测宏观。",
        "",
        "---",
        "",
    ]
    return "\n".join(header) + red_summary + "\n".join(body_sections)


def main() -> int:
    parser = argparse.ArgumentParser(description="彼得·林奇自动化流水线（邮件版）")
    parser.add_argument("--mode", choices=["daily", "weekly"], default="daily",
                        help="daily=日报(仅量化) / weekly=周报(全套Claude)")
    parser.add_argument("--market", default="ALL", help="ALL / JP / US（默认 ALL）")
    parser.add_argument("--tickers", nargs="*", help="手动指定代码，覆盖自选股")
    parser.add_argument("--no-email", action="store_true", help="不发邮件（仅打印）")
    args = parser.parse_args()

    weekly = args.mode == "weekly"
    # 日报：强制仅量化。周报：开启 LLM（无 key 时降级）。
    data_only = True if not weekly else not llm.is_configured()
    if weekly and data_only:
        print("⚠️  周报模式但未检测到 ANTHROPIC_API_KEY，本次降级为仅硬指标。\n")

    stocks = _select_stocks(args.market, args.tickers)
    if not stocks:
        print(f"⚠️  没有匹配 market={args.market} 的标的。")
        return 0

    date_str = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y年%m月%d日")
    subject = (
        f"【彼得林奇深度分析】周报 - {date_str}"
        if weekly
        else f"【彼得林奇自选股监控】日报 - {date_str}"
    )

    print(f"📡 [{args.mode}] 分析 {len(stocks)} 只标的（market={args.market}）\n")

    sections: list[str] = []
    reds: dict[str, list[str]] = {}
    errors = 0
    for entry in stocks:
        print(f"— {entry.ticker} ({entry.name}) ...")
        try:
            a = analyze_company(entry.ticker, user_note=entry.note, data_only=data_only)
            r = _red_metrics(a)
            if r:
                reds[a.ticker] = r
            sections.append(_render_weekly(entry, a) if weekly else _render_daily(entry, a))
        except (FundamentalsError, LLMError) as exc:
            errors += 1
            print(f"  ❌ {exc}")
            sections.append(_render_error(entry, str(exc)))

    red_summary = _red_flag_summary(reds) if weekly else ""
    briefing = build_briefing(args.mode, date_str, sections, len(stocks), errors, red_summary)

    print("\n" + "=" * 60)
    print(f"主题：{subject}")
    print(briefing)
    print("=" * 60 + "\n")

    if args.no_email:
        print("ℹ️  --no-email：已跳过邮件发送。")
    else:
        notify.send_email(subject, briefing)

    return 1 if errors and errors == len(stocks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
