"""简报动态折叠与林奇分类标签 — 离线测试。"""

from __future__ import annotations

from src.lynch.notify import (
    format_ticker_with_category,
    render_briefing_summary,
    render_recommend_block,
    render_red_flag_block,
)


def test_category_tag_format():
    assert format_ticker_with_category("META", "Meta Platforms, Inc.", "快速增长型") == (
        "META Meta Platforms, Inc. [快速增长型]"
    )


def test_mechanical_blocks_show_when_no_ai():
    recs = [("META", "Meta", 0.53, "PEG合理", "快速增长型")]
    reds = [("NUE", "Nucor", ["存货暴增"], "周期型")]
    out = render_briefing_summary(
        recs=recs, reds=reds, cycs=[], verdicts=[], ai_count=0, ai_mode=False,
    )
    assert "推荐深挖的优质股" in out
    assert "致命红灯排雷" in out
    assert "[快速增长型]" in out
    assert "[周期型]" in out
    assert "智能体最终裁决看板" not in out


def test_mechanical_blocks_hidden_when_ai_present():
    recs = [("META", "Meta", 0.53, "PEG合理", "快速增长型")]
    reds = [("NUE", "Nucor", ["存货暴增"], "周期型")]
    verdicts = [(0, "META", "Meta", "🟢 强烈买入", "#1e8449", "便宜", True, 0.5, 0.1)]
    out = render_briefing_summary(
        recs=recs, reds=reds, cycs=[], verdicts=verdicts, ai_count=3, ai_mode=True,
    )
    assert "推荐深挖的优质股" not in out
    assert "致命红灯排雷" not in out
    assert "智能体最终裁决看板" in out


def test_recommend_block_category_in_line():
    block = render_recommend_block([("AMD", "AMD", 0.4, "低PEG", "快速增长型")])
    assert "AMD AMD [快速增长型]" in block


def test_red_flag_block_category_in_line():
    block = render_red_flag_block([("NUE", "Nucor Corporation", ["存货+20%"], "周期型")])
    assert "NUE Nucor Corporation [周期型]" in block
