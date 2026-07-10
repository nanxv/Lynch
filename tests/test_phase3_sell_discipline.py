"""Phase 3 持仓卖出硬化 — 单元测试。"""

from __future__ import annotations

import dataclasses

from src.lynch.data.base import Fundamentals, QuickScreen
from src.lynch.funnel import fatal_warnings, rank_and_cap
from src.lynch.metrics import (
    LynchMetrics,
    Metric,
    growth_stall_detector,
    pe_vs_5y_ratio,
    stalwart_pe_exhaustion_warning,
)
from src.lynch.notify import render_held_consultation_block


def _metrics(company_type: str = "快速增长型", growth_rate: float | None = 0.25) -> LynchMetrics:
    return LynchMetrics(
        growth_rate=growth_rate,
        growth_basis="test",
        peg=1.0,
        metrics=[],
        company_type=company_type,
    )


def test_growth_stall_detector_consecutive_drop():
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
        earnings_growth_yoy=0.2,
        revenue_growth_yoy=0.2,
        quarterly_earnings_yoy=(0.40, 0.38, 0.20, 0.18),
    )
    m = _metrics("快速增长型", 0.25)
    msg = growth_stall_detector(f, m)
    assert msg is not None
    assert "掉档" in msg or "CAGR" in msg


def test_growth_stall_ignored_for_stalwart():
    f = Fundamentals(
        ticker="JNJ",
        name="JNJ",
        sector="Healthcare",
        industry="Drug",
        currency="USD",
        price=150.0,
        market_cap=3e11,
        trailing_pe=18.0,
        forward_pe=None,
        earnings_growth_yoy=0.05,
        revenue_growth_yoy=0.05,
        quarterly_earnings_yoy=(0.40, 0.38, 0.20, 0.18),
    )
    assert growth_stall_detector(f, _metrics("稳定增长型")) is None


def test_stalwart_pe_exhaustion():
    f = Fundamentals(
        ticker="JNJ",
        name="JNJ",
        sector="Healthcare",
        industry="Drug",
        currency="USD",
        price=150.0,
        market_cap=3e11,
        trailing_pe=30.0,
        forward_pe=None,
        earnings_growth_yoy=0.05,
        revenue_growth_yoy=0.05,
        pe_5y_avg=20.0,
    )
    m = _metrics("稳定增长型", 0.06)
    assert pe_vs_5y_ratio(f) == 1.5
    warn = stalwart_pe_exhaustion_warning(f, m)
    assert warn is not None
    assert "1.50" in warn or "1.5" in warn


def test_fatal_warnings_held_stall():
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
        earnings_growth_yoy=0.2,
        revenue_growth_yoy=0.2,
        quarterly_earnings_yoy=(0.40, 0.38, 0.20, 0.18),
    )
    m = _metrics("快速增长型", 0.25)
    fw = fatal_warnings(f, m, user_status="held")
    assert any("连续两季失速" in r for r in fw)


def test_rank_and_cap_held_never_dropped():
    held = QuickScreen(ticker="PTC", is_held=True, is_priority=True, user_status="held")
    others = [
        QuickScreen(ticker=f"T{i}", quick_peg=0.5 + i * 0.01)
        for i in range(20)
    ]
    ai, data_only = rank_and_cap([held] + others, max_count=3)
    assert ai[0].ticker == "PTC"
    assert held in ai
    assert held not in data_only
    # held/priority 不占用名额 → 还应再收 3 只 rest
    assert len([q for q in ai if q.ticker != "PTC"]) == 3
    assert len(data_only) == 17


def test_rank_and_cap_zero_means_unlimited():
    qs = [QuickScreen(ticker=f"T{i}", quick_peg=float(i)) for i in range(10)]
    ai, data_only = rank_and_cap(qs, max_count=0)
    assert len(ai) == 10
    assert data_only == []


def test_rank_and_cap_priority_does_not_consume_slots():
    priority = QuickScreen(ticker="WATCH", is_priority=True, quick_peg=9.0)
    others = [QuickScreen(ticker=f"T{i}", quick_peg=0.1 * i) for i in range(5)]
    ai, data_only = rank_and_cap([priority] + others, max_count=2)
    assert priority in ai
    assert len([q for q in ai if q.ticker != "WATCH"]) == 2
    assert len(data_only) == 3


def test_render_held_consultation_block():
    out = render_held_consultation_block([
        ("PTC", "PTC Inc", "快速增长型", ["🚨 连续两季失速：test"], "🟡 观察仓"),
    ])
    assert "核心持仓独立会诊" in out
    assert "PTC" in out
    assert "连续两季失速" in out
