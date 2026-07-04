#!/usr/bin/env python3
"""林奇单股闪击 CLI — 绕过定时器，即时抓取 + 四步会诊。

用法:
    python lynch_cli.py --ticker AMD
    python lynch_cli.py --ticker 4063.T --note "PVC 龙头"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:  # noqa: BLE001
    pass

from src.lynch import analyze_company, get_provider, llm  # noqa: E402
from src.lynch.fundamentals import FundamentalsError  # noqa: E402
from src.lynch.history import (  # noqa: E402
    append_record,
    build_story_diff_context,
    load_previous,
    record_from_analysis,
)
from src.lynch.llm import LLMError, get_mode_context  # noqa: E402
from src.lynch.report_modes import normalize_mode  # noqa: E402
from src.lynch.signals import extract_signal  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="林奇单股闪击 — 按需即时四步会诊")
    parser.add_argument("--ticker", required=True, help="股票代码，如 AMD、4063.T")
    parser.add_argument("--note", default="", help="补充说明（可选）")
    parser.add_argument("--model", default=None, help="覆盖 Gemini 模型名")
    parser.add_argument("--mode", default="weekly",
                        choices=["daily", "weekly", "monthly", "quarterly", "annual"],
                        help="报告周期（决定数据颗粒度）")
    args = parser.parse_args()

    if not llm.is_configured():
        print("❌ 未检测到 GEMINI_API_KEY，闪击模式需要 AI 会诊。请在 .env 中配置。")
        return 1

    ticker = args.ticker.strip()
    report_mode = normalize_mode(args.mode)
    provider = get_provider()
    prev = load_previous(ticker)
    story_ctx = build_story_diff_context(prev) if prev else ""

    print(f"📡 闪击模式 [{report_mode}]：正在从 {provider.name} 抓取 {ticker} …\n")
    try:
        result = analyze_company(
            ticker,
            user_note=args.note,
            data_only=False,
            model=args.model,
            provider=provider,
            story_diff_context=story_ctx,
            report_mode=report_mode,
            mode_context=get_mode_context(report_mode),
        )
    except FundamentalsError as exc:
        print(f"❌ 数据获取失败：{exc}")
        return 1
    except LLMError as exc:
        print(f"❌ Gemini 调用失败：{exc}")
        return 1

    print(result.data_block)
    print("\n" + "=" * 60 + "\n")
    if result.narrative:
        print("🎩 彼得·林奇四步会诊报告：\n")
        print(result.narrative)
        sig = extract_signal(result.narrative)
        try:
            append_record(record_from_analysis(
                result.ticker,
                result.metrics,
                signal_label=sig[1] if sig else None,
                signal_order=sig[0] if sig else None,
            ))
        except Exception as exc:  # noqa: BLE001
            print(f"\n⚠️  历史存档失败：{exc}")
    else:
        print("（未生成 AI 叙述。）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
