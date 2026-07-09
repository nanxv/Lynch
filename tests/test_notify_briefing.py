"""简报动态折叠与林奇分类标签 — 离线测试。"""

from __future__ import annotations

from src.lynch.funnel import BUCKET_FAST
from src.lynch.notify import (
    format_ticker_with_category,
    render_briefing_summary,
    render_multi_bucket_briefing,
    render_red_flag_block,
)


def test_category_tag_format():
    assert format_ticker_with_category("META", "Meta Platforms, Inc.", "快速增长型") == (
        "META Meta Platforms, Inc. [快速增长型]"
    )


def test_mechanical_blocks_show_when_no_ai():
    buckets = {BUCKET_FAST: [("META", "Meta", 0.53, "PEG合理", "快速增长型")]}
    reds = [("NUE", "Nucor", ["存货暴增"], "周期型")]
    out = render_briefing_summary(
        mode="weekly",
        buckets=buckets, reds=reds, cycs=[], verdicts=[], ai_count=0, ai_mode=False,
    )
    assert "快速增长区" in out
    assert "致命红灯排雷" in out
    assert "[快速增长型]" in out
    assert "[周期型]" in out
    assert "智能体最终裁决看板" not in out


def test_mechanical_blocks_hidden_when_ai_present():
    buckets = {BUCKET_FAST: [("META", "Meta", 0.53, "PEG合理", "快速增长型")]}
    reds = [("NUE", "Nucor", ["存货暴增"], "周期型")]
    verdicts = [(0, "META", "Meta", "🟢 强烈买入", "#1e8449", "便宜", True, 0.5, 0.1)]
    out = render_briefing_summary(
        mode="weekly",
        buckets=buckets, reds=reds, cycs=[], verdicts=verdicts, ai_count=3, ai_mode=True,
    )
    assert "快速增长区" not in out
    assert "致命红灯排雷" not in out
    assert "智能体最终裁决看板" in out


def test_multi_bucket_category_in_line():
    block = render_multi_bucket_briefing({
        BUCKET_FAST: [("AMD", "AMD", 0.4, "低PEG", "快速增长型")],
    })
    assert "AMD AMD [快速增长型]" in block


def test_red_flag_block_category_in_line():
    block = render_red_flag_block([("NUE", "Nucor Corporation", ["存货+20%"], "周期型")])
    assert "NUE Nucor Corporation [周期型]" in block
