#!/usr/bin/env python3
"""生成 Lynch 数据 + 计算全链路审计报告（阶段 1 + 阶段 2）。

用法:
    python scripts/generate_audit_report.py
    python scripts/generate_audit_report.py --ticker AMD --mode weekly
    python scripts/generate_audit_report.py --output docs/audit-report.md
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
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
from src.lynch.audit_pipeline import run_full_audit  # noqa: E402
from src.lynch.calculation_audit import format_calculation_report  # noqa: E402
from src.lynch.config import correct_ticker  # noqa: E402
from src.lynch.data.yahoo import YahooFinanceProvider  # noqa: E402
from src.lynch.data_quality import format_report as format_dq_report  # noqa: E402


def _format_dq_markdown(ticker: str, dq) -> str:
    lines = [f"### 阶段 1：原始数据质检 · {ticker}", ""]
    if not dq.issues:
        lines.append("✅ 未发现数据质量问题")
    else:
        lines.append("| 级别 | 维度 | 字段 | 说明 |")
        lines.append("|------|------|------|------|")
        for i in dq.issues:
            lines.append(f"| **{i.level.upper()}** | {i.dimension} | {i.field} | {i.message} |")
    lines.append("")
    trust = "✅ 可信" if dq.is_trusted else "❌ 不可信"
    lines.append(
        f"**质检结论**：{trust} · score **{dq.score:.0f}** · "
        f"{dq.fail_count} fail / {dq.warn_count} warn"
    )
    if dq.missing_fields:
        lines.append(f"· 缺失字段：`{', '.join(dq.missing_fields)}`")
    lines.append("")
    lines.append("<details><summary>字段溯源</summary>")
    lines.append("")
    lines.append("| 字段 | Yahoo 来源 |")
    lines.append("|------|-----------|")
    for k, v in sorted(dq.provenance.items()):
        lines.append(f"| `{k}` | {v} |")
    lines.append("")
    lines.append("</details>")
    lines.append("")
    return "\n".join(lines)


def build_markdown_report(results, *, scope: str, mode: str) -> str:
    now_jst = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M JST")
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    trusted_dq = sum(1 for r in results if r.data_quality.is_trusted)
    calc_ok = sum(1 for r in results if r.calculation.all_match)

    lines = [
        "# Lynch 数据与计算审计报告",
        "",
        f"| 项目 | 值 |",
        f"|------|-----|",
        f"| 生成时间 | {now_jst}（{now_utc}） |",
        f"| 范围 | {scope} |",
        f"| 报告模式 | `{mode}` |",
        f"| 标的数量 | {len(results)} |",
        f"| 阶段 1 可信 | {trusted_dq}/{len(results)} |",
        f"| 阶段 2 验算全过 | {calc_ok}/{len(results)} |",
        "",
        "---",
        "",
        "## 执行摘要",
        "",
    ]

    for r in results:
        dq = r.data_quality
        ca = r.calculation
        dq_icon = "✅" if dq.is_trusted else "❌"
        ca_icon = "✅" if ca.all_match else "❌"
        peg_step = next((s for s in ca.steps if s.key == "peg"), None)
        peg = f"{peg_step.engine_value:.2f}" if peg_step and peg_step.engine_value is not None else "—"
        lines.append(
            f"- **{r.ticker}**：阶段1 {dq_icon} score {dq.score:.0f} "
            f"（{dq.fail_count}F/{dq.warn_count}W）· "
            f"阶段2 {ca_icon} score {ca.score:.0f} · "
            f"PEG={peg}"
        )

    lines.extend(["", "---", "", "## 逐股明细", ""])

    for r in results:
        lines.append(f"## {r.ticker}")
        lines.append("")
        f = r.fundamentals
        lines.append(
            f"> {f.name or r.ticker} · {f.sector or '?'} · "
            f"现价 {f.price} {f.currency or ''} · TTM P/E {f.trailing_pe}"
        )
        lines.append("")
        lines.append(_format_dq_markdown(r.ticker, r.data_quality))
        lines.append(format_calculation_report(r.calculation))
        lines.append("---")
        lines.append("")

    lines.extend([
        "## 附录：验证方法论",
        "",
        "### 阶段 1 — 原始数据是否干净",
        "1. **完整性**：按 report_mode 检查必填字段",
        "2. **新鲜度**：日 K 末根 vs 最近美股交易日；年表最新财年滞后",
        "3. **内部自洽**：市值 ≈ price×shares；P/E 与 price/EPS 一致",
        "4. **跨源一致**：info.debtToEquity vs 年表 ltd/equity；info.earningsGrowth vs 年表 YoY",
        "5. **合理性**：dividendYield 单位、极端 P/E/存货",
        "6. **溯源**：每个关键字段标注 Yahoo 路径",
        "",
        "### 阶段 2 — 参数计算是否正确",
        "在阶段 1 原始值基础上，独立展开公式手算，与 `compute_metrics()` 引擎输出逐项对比。",
        "含：CAGR、股息修正 PEG、负债比、存货差、每股净现金、FCF、SBI 可交易、漏斗 quick_peg。",
        "",
        "### 判定标准",
        "- 阶段 1 **FAIL** → `trusted: NO`，不建议进 Gemini",
        "- 阶段 2 数值容差：PEG ±1.5%相对误差；比率类 ±2%",
        "- `quick_peg` 与正式 PEG 口径不同，仅验算漏斗自洽",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Lynch 全链路审计报告")
    parser.add_argument("--ticker", help="单股（默认 watchlist US）")
    parser.add_argument("--watchlist", action="store_true", help="审计 watchlist（默认）")
    parser.add_argument("--market", default="US", choices=["ALL", "US", "JP"])
    parser.add_argument("--mode", default="weekly",
                        choices=["daily", "weekly", "monthly", "quarterly", "annual"])
    parser.add_argument("--output", default=None, help="输出 Markdown 路径")
    args = parser.parse_args()

    provider = YahooFinanceProvider()
    tickers: list[str] = []
    if args.ticker:
        tickers = [correct_ticker(args.ticker)]
        scope = f"单股 {args.ticker}"
    else:
        for s in load_config().stocks:
            if args.market != "ALL" and s.market.upper() != args.market:
                continue
            tickers.append(correct_ticker(s.ticker))
        scope = f"watchlist · market={args.market}"

    if not tickers:
        print("⚠️  没有可审计的标的。")
        return 1

    results = []
    for t in tickers:
        print(f"🔍 审计 {t} …")
        try:
            results.append(run_full_audit(t, args.mode, provider))
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ {t}: {exc}")

    if not results:
        return 1

    md = build_markdown_report(results, scope=scope, mode=args.mode)
    out = Path(args.output) if args.output else ROOT / "docs" / (
        f"audit-report-{datetime.now(ZoneInfo('Asia/Tokyo')).strftime('%Y-%m-%d')}.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"\n✅ 报告已写入 {out}")
    print(f"   阶段1 可信: {sum(1 for r in results if r.data_quality.is_trusted)}/{len(results)}")
    print(f"   阶段2 全过: {sum(1 for r in results if r.calculation.all_match)}/{len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
