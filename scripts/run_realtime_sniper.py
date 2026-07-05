#!/usr/bin/env python3
"""盘中实时狙击雷达 — 仅扫描 watchlist 中 SBI 可交易自选股。

用法:
    python scripts/run_realtime_sniper.py
    python scripts/run_realtime_sniper.py --no-email
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:  # noqa: BLE001
    pass

from src.config_loader import load_config  # noqa: E402
from src.lynch import get_provider, llm  # noqa: E402
from src.lynch.config import correct_ticker  # noqa: E402
from src.lynch.sniper import run_realtime_sniper_alert  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="林奇盘中实时狙击（watchlist · SBI 可交易）")
    parser.add_argument("--no-email", action="store_true", help="不发邮件，仅打印")
    parser.add_argument("--market", default=None, choices=["ALL", "US", "JP"],
                        help="只扫指定市场自选股（默认 US）")
    args = parser.parse_args()

    from src.lynch import config as lynch_config  # noqa: E402

    market = (args.market or lynch_config.DEFAULT_MARKET).upper()

    if not llm.is_configured():
        print("⚠️  未配置 GEMINI_API_KEY，盘中狙击跳过 AI 确认。")
        return 0

    provider = get_provider()
    stocks = [s for s in load_config().stocks if market == "ALL" or s.market.upper() == market]
    if not stocks:
        print(f"⚠️  watchlist 中没有 market={market} 的标的。")
        return 0

    print(f"📡 盘中狙击扫描 · {len(stocks)} 只自选股 (market={market}) · 数据源={provider.name}\n")
    sent = 0
    scanned = 0
    for s in stocks:
        ticker = correct_ticker(s.ticker)
        scanned += 1
        try:
            qs = provider.get_quick_screen(ticker)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️  {ticker} 粗筛失败：{exc}")
            continue
        if not qs or not qs.sbi_tradable:
            print(f"  · {ticker} 非 SBI 可交易，跳过")
            continue
        try:
            if run_realtime_sniper_alert(
                ticker, provider=provider, user_status=s.user_status, send=not args.no_email,
            ):
                sent += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ {ticker} 盘中狙击异常：{exc}")

    print(f"\n✅ 扫描 {scanned} 只，触发并发送 {sent} 封深夜特快。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
