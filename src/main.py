#!/usr/bin/env python3
"""Iron Rule 2.5 stock monitor — CLI entry point."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config_loader import AppConfig, load_config, load_config_from_db  # noqa: E402
from src.db.database import Database  # noqa: E402
from src.notifier import notify_signals, print_report  # noqa: E402
from src.services.scan_service import filter_market, run_scan  # noqa: E402
from src.strategy import scan_all  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="铁律 2.5 量化监控 Agent")
    parser.add_argument("--dry-run", action="store_true", help="不推送，仅终端输出")
    parser.add_argument(
        "--market",
        choices=["ALL", "JP", "US", "all", "jp", "us"],
        default="ALL",
        help="只扫描指定市场 (默认 ALL)",
    )
    parser.add_argument(
        "--legacy-yaml",
        action="store_true",
        help="从 watchlist.yaml 读取（不使用数据库）",
    )
    args = parser.parse_args()

    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    market_label = args.market.upper()
    print(f"\n扫描时间 (JST): {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"扫描范围: {market_label}\n")

    database = Database()
    database.init()

    if args.legacy_yaml:
        config = filter_market(load_config(), market_label)
    else:
        config = filter_market(load_config_from_db(database.path), market_label)

    if not config.stocks:
        print(f"⚠️  股票池中没有市场={market_label} 的标的")
        return 0

    if args.dry_run:
        config = AppConfig(
            strategy=config.strategy,
            markets=config.markets,
            notifications=type(config.notifications)(
                telegram_enabled=False,
                footer_hint=config.notifications.footer_hint,
                dedup_ttl_hours=config.notifications.dedup_ttl_hours,
                alert_cache_path=config.notifications.alert_cache_path,
            ),
            stocks=config.stocks,
        )
        results = scan_all(config)
        print_report(results, config)
        return 1 if any(r.error for r in results) else 0

    results, summary, _ = run_scan(
        market=market_label,
        dry_run=False,
        db=database,
    )
    print_report(results, config)
    print(
        f"\n信号 {summary.signals} | Push/Telegram 推送 {summary.pushed} | "
        f"去重跳过 {summary.skipped} | 错误 {summary.errors}"
    )
    return 1 if summary.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
