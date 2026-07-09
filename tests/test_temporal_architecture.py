"""四维时间架构路由与渲染 — 离线测试。"""

from __future__ import annotations

from types import SimpleNamespace

from src.lynch.daily_sniper import check_daily_trigger
from src.lynch.metrics import annual_rebalance_block_lines, quarterly_discipline_block_lines
from src.lynch.notify import render_briefing_summary, render_mode_banner
from src.lynch.report_modes import allows_full_universe, held_only_mode


def test_mode_routing_helpers():
    assert allows_full_universe("weekly") is True
    assert allows_full_universe("daily") is False
    assert held_only_mode("quarterly") is True
    assert held_only_mode("annual") is True
    assert held_only_mode("weekly") is False


def test_mode_banners():
    assert "异动狙击手" in render_mode_banner("daily")
    assert "多桶雷达" in render_mode_banner("weekly")
    assert "生死拷问" in render_mode_banner("quarterly")


def test_daily_briefing_summary():
    out = render_briefing_summary(
        mode="daily",
        reds=[],
        cycs=[],
        verdicts=[],
        ai_count=1,
        ai_mode=True,
        daily_triggered=[("AAPL", "Apple", ["价格波动 +5.0%"], "🟡 观察仓")],
    )
    assert "异动触发" in out
    assert "AAPL" in out


def test_quarterly_discipline_block():
    from src.lynch.data.base import Fundamentals
    from src.lynch.metrics import LynchMetrics

    f = Fundamentals(
        ticker="PTC",
        name="PTC",
        sector="Technology",
        industry="Software",
        currency="USD",
        price=100.0,
        market_cap=1e10,
        trailing_pe=30.0,
        forward_pe=None,
        earnings_growth_yoy=0.1,
        revenue_growth_yoy=0.1,
        quarterly_earnings_yoy=(0.4, 0.38, 0.2, 0.18),
    )
    m = LynchMetrics(growth_rate=0.25, growth_basis="t", peg=0.8, company_type="快速增长型")
    lines = quarterly_discipline_block_lines(f, m)
    assert any("季报" in x for x in lines)


def test_check_daily_trigger_price():
    class _P:
        def get_daily_price_change(self, ticker: str):
            return 0.05

    ok, reasons, chg = check_daily_trigger(_P(), "TEST")
    assert ok
    assert chg == 0.05
    assert any("价格波动" in r for r in reasons)


def test_apply_temporal_routing_scope():
  from scripts.run_scheduled_analysis import _apply_temporal_routing
  args = SimpleNamespace(mode="quarterly", scope="full")
  _apply_temporal_routing(args)
  assert args.scope == "watchlist"
