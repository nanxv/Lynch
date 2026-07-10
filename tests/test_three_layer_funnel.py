"""三层漏斗单元测试：Flash JSON 解析 + Layer3 名额选择 + 短评表渲染 + 集成 mock。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.lynch.agent import (
    FlashMicroScore,
    LynchAnalysis,
    compute_layer3_flash_top_n,
    parse_flash_micro_json,
    select_layer3_tickers,
)
from src.lynch.data.base import QuickScreen
from src.lynch.notify import render_flash_shortlist_table
from src.lynch.three_layer import run_layer2_and_select_layer3, run_layer3_pro


def test_parse_flash_micro_json_clean():
    raw = '{"ticker":"SYF","lynch_score":88,"one_liner":"PEG极低且高管买入"}'
    s = parse_flash_micro_json(raw, ticker="SYF", name="Synchrony", company_type="快速增长型")
    assert s.parse_ok
    assert s.lynch_score == 88
    assert s.ticker == "SYF"
    assert "PEG" in s.one_liner


def test_parse_flash_micro_json_fenced_and_noise():
    raw = """好的，结果如下：
```json
{"ticker": "AAA", "lynch_score": 120, "one_liner": "这是一句超过三十个汉字的超长短评内容应该被截断处理掉才对"}
```
额外废话
"""
    s = parse_flash_micro_json(raw, ticker="AAA", name="A", company_type="稳定增长型")
    assert s.parse_ok
    assert s.lynch_score == 100  # clamped
    assert len(s.one_liner) <= 30


def test_parse_flash_micro_json_garbage():
    s = parse_flash_micro_json("not json at all", ticker="ZZZ", name="Z", company_type="—")
    assert not s.parse_ok
    assert s.lynch_score == 0
    assert "解析失败" in s.one_liner


def test_select_layer3_held_plus_top_n():
    held = {"PTC", "SYF"}
    scores = [
        FlashMicroScore("A", "A", "快增", 90, "x"),
        FlashMicroScore("B", "B", "快增", 80, "x"),
        FlashMicroScore("C", "C", "快增", 70, "x"),
        FlashMicroScore("D", "D", "快增", 60, "x"),
    ]
    out = select_layer3_tickers(held, scores, top_n=2)
    assert out[:2] == ["PTC", "SYF"] or set(out[:2]) == {"PTC", "SYF"}
    assert "A" in out and "B" in out
    assert "C" not in out and "D" not in out
    assert len(out) == 4


def test_render_flash_shortlist_table():
    md = render_flash_shortlist_table([
        ("BBB", "稳定增长型", 55, "现金流尚可"),
        ("AAA", "快速增长型", 77, "PEG低"),
    ])
    assert "全境海选短评榜单" in md
    assert "| 代码 | 林奇分类 |" in md
    # higher score first
    assert md.index("AAA") < md.index("BBB")


def test_compute_layer3_flash_top_n():
    assert compute_layer3_flash_top_n(3) == 10  # min(10, 50-3-5=42)
    assert compute_layer3_flash_top_n(46) == 0  # min(10, -1) -> 0 flash slots
    held = {"H1", "H2", "H3"}
    scores = [FlashMicroScore(f"F{i}", f"F{i}", "快增", 90 - i, "x") for i in range(5)]
    out = select_layer3_tickers(held, scores)
    assert out[:3] == ["H1", "H2", "H3"] or set(out[:3]) == held
    assert len(out) == 3 + min(len(scores), compute_layer3_flash_top_n(3))


def test_parse_flash_micro_json_removeprefix_chain():
    raw = '```json\n{"ticker":"X","lynch_score":66,"one_liner":"测试"}\n```'
    s = parse_flash_micro_json(raw, ticker="X", name="X", company_type="—")
    assert s.parse_ok
    assert s.lynch_score == 66


@patch("src.lynch.three_layer.llm.is_configured", return_value=True)
@patch("src.lynch.three_layer.flash_micro_score")
@patch("src.lynch.three_layer.analyze_company")
def test_three_layer_mock_3_held_5_nonheld(mock_analyze, mock_flash, _mock_llm_ok):
    """微型集成：3 held + 5 非 held → L3 队列含 held 且 Pro 被调用。"""
    held = ("ACGL", "PTC", "RKLB")
    nonheld = ("AAA", "BBB", "CCC", "DDD", "EEE")
    watch = {
        t: (t, "", "held" if t in held else "watch") for t in (*held, *nonheld)
    }
    working = [
        QuickScreen(ticker=t, name=t, is_priority=(t in held), is_held=(t in held), user_status="held" if t in held else "watch")
        for t in (*held, *nonheld)
    ]

    def fake_analyze(ticker, **kwargs):
        data_only = kwargs.get("data_only", True)
        f = MagicMock()
        f.ticker = ticker
        f.name = ticker
        m = MagicMock()
        m.company_type = "快速增长型"
        m.metrics = []
        narrative = None if data_only else f"【行动指令】钝感持有 (HOLD)：mock {ticker}"
        return LynchAnalysis(ticker=ticker, fundamentals=f, metrics=m, data_block="block", narrative=narrative)

    mock_analyze.side_effect = fake_analyze
    mock_flash.side_effect = lambda a: FlashMicroScore(
        a.ticker, a.fundamentals.name, a.metrics.company_type,
        {"AAA": 90, "BBB": 80, "CCC": 70, "DDD": 60, "EEE": 50}[a.ticker],
        "flash ok",
    )

    provider = MagicMock()
    tl = run_layer2_and_select_layer3(working, watch, provider, report_mode="weekly")
    assert len(tl.layer3_queue) >= 3
    assert all(h in tl.layer3_queue for h in held)
    assert "AAA" in tl.layer3_queue

    pro_calls: list[str] = []
    real_analyze = mock_analyze.side_effect

    def track_pro(ticker, **kwargs):
        if not kwargs.get("data_only", True):
            pro_calls.append(ticker)
        return real_analyze(ticker, **kwargs)

    mock_analyze.side_effect = track_pro
    out = run_layer3_pro(tl.layer3_queue, watch, provider, prior_analyses=tl.analyses)
    assert len(pro_calls) == len(tl.layer3_queue)
    assert sum(1 for t in tl.layer3_queue if out[t.upper()].narrative) == len(tl.layer3_queue)
