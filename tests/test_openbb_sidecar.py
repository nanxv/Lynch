"""OpenBB Layer-3 sidecar：失败降级、非周期跳过宏观、日股无 SEC。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.lynch.data.openbb_provider import (
    NO_OPENBB_DATA,
    build_openbb_sidecar_block,
    fetch_macro_trend,
    fetch_options_sentiment,
    fetch_sec_mda,
)


def test_macro_skips_non_cyclical():
    out = fetch_macro_trend("Technology", "Software", company_type="快速增长型")
    assert "非周期语境" in out


def test_sidecar_never_raises_without_openbb():
    with patch("src.lynch.data.openbb_provider._get_obb", return_value=None):
        block = build_openbb_sidecar_block(
            "ACGL",
            sector="Financial Services",
            industry="Insurance",
            company_type="稳定增长型",
        )
    assert "【OpenBB 深度定性外挂 (免费源)】" in block
    assert NO_OPENBB_DATA in block or "非周期语境" in block


def test_sec_mda_japan_ticker_skipped():
    assert fetch_sec_mda("4063.T") == NO_OPENBB_DATA


def test_options_without_obb():
    with patch("src.lynch.data.openbb_provider._get_obb", return_value=None):
        assert fetch_options_sentiment("ACGL") == NO_OPENBB_DATA


def test_analyze_company_injects_openbb_only_when_not_data_only():
    """data_only 路径不得注入；Pro 深诊路径必须出现外挂标题。"""
    from src.lynch.agent import analyze_company
    from src.lynch.data.base import Fundamentals
    from src.lynch.metrics import LynchMetrics, Metric

    fake_f = Fundamentals(
        ticker="ACGL",
        name="Arch",
        sector="Financial Services",
        industry="Insurance",
        currency="USD",
        price=100.0,
        market_cap=1e9,
        trailing_pe=10.0,
        forward_pe=9.0,
        earnings_growth_yoy=0.1,
        revenue_growth_yoy=0.08,
        source="test",
        report_mode="daily",
    )
    fake_m = LynchMetrics(
        company_type="稳定增长型",
        is_financial=True,
        is_cyclical=False,
        growth_rate=0.1,
        growth_basis="test",
        peg=1.0,
        metrics=[Metric(key="peg", label="PEG", value=1.0, flag="green", verdict="ok")],
    )

    provider = MagicMock()
    provider.get_fundamentals.return_value = fake_f

    with (
        patch("src.lynch.agent.compute_metrics", return_value=fake_m),
        patch(
            "src.lynch.agent.build_openbb_sidecar_block",
            return_value="\n\n---\n\n【OpenBB 深度定性外挂 (免费源)】\nTEST_INJECT",
        ) as mock_obb,
        patch("src.lynch.agent.llm.is_configured", return_value=False),
    ):
        a0 = analyze_company("ACGL", data_only=True, provider=provider, report_mode="daily")
        assert mock_obb.call_count == 0
        assert "TEST_INJECT" not in a0.data_block

        # data_only=False 会走 LLM；此处 mock generate 避免真调 API
        with (
            patch("src.lynch.agent.llm.generate", return_value="【行动指令】钝感持有 (HOLD)：test"),
            patch("src.lynch.agent._rate_limit_sleep"),
            patch("src.lynch.agent.llm.build_task_prompt", return_value="task"),
        ):
            a1 = analyze_company(
                "ACGL", data_only=False, provider=provider, report_mode="daily",
            )
        assert mock_obb.call_count == 1
        assert "TEST_INJECT" in a1.data_block
        assert "【OpenBB 深度定性外挂 (免费源)】" in a1.data_block
