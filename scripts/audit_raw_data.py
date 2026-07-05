#!/usr/bin/env python3
"""原始数据质量审计（阶段 1）— 验证 Yahoo 抓取数据是否「干净」。

用法:
    python scripts/audit_raw_data.py --ticker AMD --mode weekly
    python scripts/audit_raw_data.py --watchlist --market US
    python scripts/audit_raw_data.py --ticker NVDA --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:  # noqa: BLE001
    pass

import pandas as pd  # noqa: E402

from src.config_loader import load_config  # noqa: E402
from src.lynch.config import correct_ticker  # noqa: E402
from src.lynch.data_quality import format_report, report_to_dict, validate_raw_data  # noqa: E402
from src.lynch.data.yahoo import (  # noqa: E402
    YahooFinanceProvider,
    _base_fundamentals_from_info,
    _fetch_info,
    _finalize_with_temporal,
    _ticker,
)
from src.lynch.report_modes import normalize_mode  # noqa: E402
from src.lynch.temporal import latest_statement_end  # noqa: E402


def _last_bar_date(tk) -> pd.Timestamp | None:
    try:
        hist = tk.history(period="5d", interval="1d", auto_adjust=False)
    except Exception:  # noqa: BLE001
        return None
    if hist is None or hist.empty:
        return None
    return pd.Timestamp(hist.index[-1])


def fetch_and_audit(ticker: str, mode: str, provider: YahooFinanceProvider):
    ticker = correct_ticker(ticker)
    mode = normalize_mode(mode)
    fetched_at = datetime.now(timezone.utc)

    info = _fetch_info(ticker)
    tk = _ticker(ticker)

    income = balance = cash = income_q = None
    try:
        income = tk.income_stmt
        balance = tk.balance_sheet
        cash = tk.cashflow
    except Exception:  # noqa: BLE001
        pass
    try:
        income_q = tk.quarterly_income_stmt
    except Exception:  # noqa: BLE001
        pass

    base = _base_fundamentals_from_info(
        ticker, info, income, balance, cash, mode=mode, source=provider.name,
    )
    f = _finalize_with_temporal(base, tk, mode=mode)
    qs = provider.get_quick_screen(ticker)
    last_bar = _last_bar_date(tk)
    last_bar_date = last_bar.date() if last_bar is not None else None
    stmt_end = latest_statement_end(income_q if mode == "quarterly" else income)

    return validate_raw_data(
        f,
        info,
        mode=mode,
        quick_screen=qs,
        last_bar_date=last_bar_date,
        latest_statement_date=stmt_end,
        has_quarterly=income_q is not None and not getattr(income_q, "empty", True),
        has_price_history=last_bar is not None,
        fetched_at=fetched_at,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Lynch 原始数据质量审计（阶段 1）")
    parser.add_argument("--ticker", help="单股代码")
    parser.add_argument("--watchlist", action="store_true", help="审计 watchlist.yaml")
    parser.add_argument("--market", default="US", choices=["ALL", "US", "JP"])
    parser.add_argument("--mode", default="weekly",
                        choices=["daily", "weekly", "monthly", "quarterly", "annual"])
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    if not args.ticker and not args.watchlist:
        parser.error("请指定 --ticker 或 --watchlist")

    provider = YahooFinanceProvider()
    tickers: list[str] = []
    if args.watchlist:
        for s in load_config().stocks:
            if args.market != "ALL" and s.market.upper() != args.market:
                continue
            tickers.append(correct_ticker(s.ticker))
    else:
        tickers = [correct_ticker(args.ticker)]

    if not tickers:
        print("⚠️  没有可审计的标的。")
        return 1

    reports = []
    exit_code = 0
    for t in tickers:
        try:
            report = fetch_and_audit(t, args.mode, provider)
            reports.append(report)
            if args.json:
                continue
            print(format_report(report))
            print()
            if not report.is_trusted:
                exit_code = 1
        except Exception as exc:  # noqa: BLE001
            print(f"❌ {t}: {exc}\n")
            exit_code = 1

    if args.json:
        print(json.dumps([report_to_dict(r) for r in reports], ensure_ascii=False, indent=2))

    if not args.json:
        trusted = sum(1 for r in reports if r.is_trusted)
        print(f"✅ 完成 {len(reports)} 只：{trusted} trusted / {len(reports) - trusted} 不可信")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
