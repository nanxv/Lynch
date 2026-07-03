#!/usr/bin/env python3
"""
Run the monitor only during the US cash-session close window.

US equities close at 16:00 America/New_York. This guard fires the scan at
15:30 ET (±2 minutes) on weekdays so a single JST cron line works across
EDT (JST 04:30) and EST (JST 05:30) without manual seasonal edits.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
ET = ZoneInfo("America/New_York")
CLOSE_SCAN_HOUR = 15
CLOSE_SCAN_MINUTE = 30
MINUTE_TOLERANCE = 2


def in_us_close_window(now_et: datetime) -> bool:
    if now_et.weekday() >= 5:
        return False
    if now_et.hour != CLOSE_SCAN_HOUR:
        return False
    return abs(now_et.minute - CLOSE_SCAN_MINUTE) <= MINUTE_TOLERANCE


def main() -> int:
    now_et = datetime.now(ET)
    now_jst = datetime.now(ZoneInfo("Asia/Tokyo"))

    if not in_us_close_window(now_et):
        print(
            f"跳过美股扫描：当前 ET {now_et.strftime('%Y-%m-%d %H:%M')} / "
            f"JST {now_jst.strftime('%Y-%m-%d %H:%M')} "
            f"不在收盘前 30 分钟窗口 (15:{CLOSE_SCAN_MINUTE:02d} ET)"
        )
        return 0

    print(
        f"触发美股战术扫描：ET {now_et.strftime('%Y-%m-%d %H:%M')} "
        f"(JST {now_jst.strftime('%Y-%m-%d %H:%M')})"
    )

    python = ROOT / ".venv" / "bin" / "python"
    if not python.exists():
        python = Path(sys.executable)

    return subprocess.call(
        [str(python), "-m", "src.main", "--market", "US"],
        cwd=ROOT,
    )


if __name__ == "__main__":
    raise SystemExit(main())
