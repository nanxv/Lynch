"""故事线历史存档与差异追踪（本地 data/history/，不进 Git）。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from .metrics import LynchMetrics

_ROOT = Path(__file__).resolve().parent.parent.parent
HISTORY_DIR = _ROOT / "data" / "history"


@dataclass(frozen=True)
class HistoryRecord:
    ticker: str
    date: str  # YYYY-MM-DD
    signal_label: str | None
    signal_order: int | None
    peg: float | None
    debt_ratio: float | None
    inventory_gap: float | None  # 存货增速 vs 销售增速差（百分点）
    company_type: str | None = None


def _history_path(d: date | None = None) -> Path:
    d = d or date.today()
    return HISTORY_DIR / f"{d.isoformat()}.jsonl"


def _metric_value(m: LynchMetrics, key: str) -> float | None:
    metric = m.by_key(key)
    return metric.value if metric else None


def record_from_analysis(
    ticker: str,
    m: LynchMetrics,
    *,
    signal_label: str | None = None,
    signal_order: int | None = None,
    on_date: date | None = None,
) -> HistoryRecord:
    return HistoryRecord(
        ticker=ticker.upper(),
        date=(on_date or date.today()).isoformat(),
        signal_label=signal_label,
        signal_order=signal_order,
        peg=m.peg,
        debt_ratio=_metric_value(m, "debt"),
        inventory_gap=_metric_value(m, "inventory"),
        company_type=m.company_type,
    )


def append_record(record: HistoryRecord) -> None:
    """按日期追加一行 JSON（同日多次分析会追加多条）。"""
    path = _history_path(date.fromisoformat(record.date))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def load_previous(ticker: str, *, before: date | None = None) -> HistoryRecord | None:
    """读取该代码最近一次（早于 before 当天）的历史存档。"""
    if not HISTORY_DIR.exists():
        return None
    before = before or date.today()
    ticker = ticker.upper()
    for path in sorted(HISTORY_DIR.glob("*.jsonl"), reverse=True):
        try:
            file_date = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if file_date >= before:
            continue
        matches: list[HistoryRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if data.get("ticker", "").upper() == ticker:
                matches.append(HistoryRecord(**data))
        if matches:
            return matches[-1]
    return None


def build_story_diff_context(prev: HistoryRecord) -> str:
    """注入 User Prompt 的历史对比上下文。"""
    peg = f"{prev.peg:.2f}" if prev.peg is not None else "N/A"
    debt = f"{prev.debt_ratio:.2f}" if prev.debt_ratio is not None else "N/A"
    inv = f"{prev.inventory_gap}" if prev.inventory_gap is not None else "N/A"
    label = prev.signal_label or "未知"
    return (
        f"【上期故事线存档】（{prev.date}）\n"
        f"- 裁决标签：{label}\n"
        f"- 股息修正 PEG：{peg}\n"
        f"- 长期负债/权益：{debt}\n"
        f"- 存货增速差(百分点)：{inv}\n\n"
        "【强制要求】你必须对比该股票与上期数据的故事线差异。"
        "在 Step 1 分类之前，单独用一句话指出基本面相较上期是「好转」「持平」还是「恶化」，"
        "并简述依据（PEG/负债/存货/裁决变化）。"
    )
