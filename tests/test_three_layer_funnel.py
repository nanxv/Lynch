"""三层漏斗单元测试：Flash JSON 解析 + Layer3 名额选择 + 短评表渲染。"""

from __future__ import annotations

from src.lynch.agent import FlashMicroScore, parse_flash_micro_json, select_layer3_tickers
from src.lynch.notify import render_flash_shortlist_table


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
