#!/usr/bin/env python3
"""彼得·林奇分析 Agent — CLI 入口。

用法:
    .venv/bin/python scripts/analyze.py AMD
    .venv/bin/python scripts/analyze.py 4063.T --note "因为我天天用它家的PVC"
    .venv/bin/python scripts/analyze.py 6859.T --data-only   # 仅看硬指标，不调用 Gemini
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from src.lynch import analyze_company, get_provider  # noqa: E402
from src.lynch.fundamentals import FundamentalsError  # noqa: E402
from src.lynch.llm import LLMError, is_configured  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="彼得·林奇专属股票分析 Agent")
    parser.add_argument("ticker", help="股票代码，如 AMD、4063.T、KO")
    parser.add_argument("--note", default="", help="补充说明（例如为什么想买、看中哪个产品）")
    parser.add_argument("--data-only", action="store_true", help="只输出硬指标，不调用 LLM")
    parser.add_argument("--model", default=None, help="覆盖 Gemini 模型名")
    args = parser.parse_args()

    data_only = args.data_only or not is_configured()
    if args.data_only:
        pass
    elif not is_configured():
        print("⚠️  未检测到 GEMINI_API_KEY，自动切换为 --data-only 模式（仅硬指标）。\n")

    provider = get_provider()
    print(f"📡 正在抓取 {args.ticker} 的基本面数据 ({provider.name})...\n")
    try:
        result = analyze_company(
            args.ticker, user_note=args.note, data_only=data_only, model=args.model
        )
    except FundamentalsError as exc:
        print(f"❌ 数据获取失败：{exc}")
        return 1
    except LLMError as exc:
        print(f"❌ LLM 调用失败：{exc}")
        return 1

    print(result.data_block)
    print("\n" + "=" * 60 + "\n")
    if result.narrative:
        print("🎩 彼得·林奇的分析：\n")
        print(result.narrative)
    else:
        print("（data-only 模式：以上为已核实硬指标，未生成 LLM 叙述。）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
