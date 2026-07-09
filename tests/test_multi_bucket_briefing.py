"""P2-6 多桶简报 + P2-2 增速紧箍咒 — 离线测试。"""

from __future__ import annotations

from src.lynch.data.base import Fundamentals, growth_cap_warn
from src.lynch.funnel import (
    BUCKET_ASSET,
    BUCKET_DIVIDEND,
    BUCKET_FAST,
    BUCKET_TURNAROUND,
    assign_briefing_bucket,
)
from src.lynch.metrics import LynchMetrics, Metric, compute_metrics
from src.lynch.notify import render_briefing_summary, render_multi_bucket_briefing


def _m(
    *,
    company_type: str = "快速增长型",
    peg: float = 0.6,
    growth_cap_warn_flag: bool = False,
) -> LynchMetrics:
    return LynchMetrics(
        growth_rate=0.28 if growth_cap_warn_flag else 0.18,
        growth_basis="test",
        peg=peg,
        metrics=[
            Metric("peg", "PEG", peg, "green", ""),
            Metric("debt", "debt", 0.2, "green", ""),
            Metric("fcf", "fcf", 1, "green", ""),
            Metric("net_cash", "nc", 5, "green", ""),
        ],
        company_type=company_type,
        growth_cap_warn=growth_cap_warn_flag,
    )


def test_multi_bucket_mock_render():
    buckets = {
        BUCKET_FAST: [
            ("NVDA", "NVIDIA", 0.45, "PEG 0.45·极佳｜低负债｜正现金流｜⚠️超高增速紧箍咒", "快速增长型"),
        ],
        BUCKET_ASSET: [
            ("CASH", "CashCo", -0.35, "每股净现金垫占股价 35%", "稳定增长型"),
        ],
        BUCKET_TURNAROUND: [
            ("TURN", "TurnCo", -0.2, "困境反转候选｜盈利仍承压(-15%)", "困境反转型"),
        ],
        BUCKET_DIVIDEND: [
            ("KO", "Coca-Cola", -3.2, "股息 3.2%｜P/E 仅 5 年均值的 82%", "缓慢增长型"),
        ],
    }
    out = render_multi_bucket_briefing(buckets)
    assert "多赛道雷达" in out
    assert "快速增长区" in out
    assert "隐蔽资产区" in out
    assert "困境反转区" in out
    assert "股息养老区" in out
    assert "NVDA" in out and "CASH" in out and "TURN" in out and "KO" in out
    assert "推荐深挖的优质股" not in out


def test_empty_buckets_hidden():
    out = render_multi_bucket_briefing({BUCKET_FAST: []})
    assert "快速增长区" not in out
    assert "多赛道雷达" in out


def test_briefing_summary_uses_buckets_when_no_ai():
    buckets = {
        BUCKET_FAST: [("AMD", "AMD", 0.4, "PEG合理", "快速增长型")],
    }
    out = render_briefing_summary(
        buckets=buckets,
        reds=[],
        cycs=[],
        verdicts=[],
        ai_count=0,
        ai_mode=False,
    )
    assert "快速增长区" in out
    assert "推荐深挖的优质股" not in out


def test_growth_cap_warn_threshold():
    f = Fundamentals(
        ticker="HYPER",
        name="Hyper",
        sector="Technology",
        industry="Software",
        currency="USD",
        price=100.0,
        market_cap=1e10,
        trailing_pe=30.0,
        forward_pe=None,
        earnings_growth_yoy=0.30,
        revenue_growth_yoy=None,
        eps_series={2020: 1.0, 2024: 2.5},
    )
    assert growth_cap_warn(f) is True
    m = compute_metrics(f)
    assert m.growth_cap_warn is True
    assert m.company_type == "快速增长型"


def test_assign_bucket_fast_grower():
    f = Fundamentals(
        ticker="FAST",
        name="Fast",
        sector="Technology",
        industry="Software",
        currency="USD",
        price=50.0,
        market_cap=1e9,
        trailing_pe=20.0,
        forward_pe=None,
        earnings_growth_yoy=0.22,
        revenue_growth_yoy=None,
        free_cashflow=100.0,
        long_term_debt=10.0,
        stockholders_equity=100.0,
        total_cash=50.0,
        total_debt=20.0,
        shares_outstanding=10.0,
    )
    bucket, reason = assign_briefing_bucket(f, _m(company_type="快速增长型", peg=0.7), [])
    assert bucket == BUCKET_FAST
    assert "PEG" in reason


def test_assign_bucket_asset_play():
    f = Fundamentals(
        ticker="RICH",
        name="Rich",
        sector="Technology",
        industry="Software",
        currency="USD",
        price=10.0,
        market_cap=1e8,
        trailing_pe=8.0,
        forward_pe=None,
        earnings_growth_yoy=0.05,
        revenue_growth_yoy=None,
        total_cash=500.0,
        total_debt=50.0,
        shares_outstanding=10.0,
    )
    bucket, reason = assign_briefing_bucket(
        f, _m(company_type="稳定增长型", peg=1.5), [],
    )
    assert bucket == BUCKET_ASSET
    assert "净现金" in reason


def test_assign_bucket_turnaround():
    f = Fundamentals(
        ticker="REV",
        name="Rev",
        sector="Consumer",
        industry="Retail",
        currency="USD",
        price=5.0,
        market_cap=1e8,
        trailing_pe=None,
        forward_pe=None,
        earnings_growth_yoy=-0.15,
        revenue_growth_yoy=None,
        long_term_debt=100.0,
    )
    bucket, _ = assign_briefing_bucket(
        f, _m(company_type="困境反转型", peg=None), [],
    )
    assert bucket == BUCKET_TURNAROUND


def test_assign_bucket_dividend():
    f = Fundamentals(
        ticker="PG",
        name="P&G",
        sector="Consumer",
        industry="Household",
        currency="USD",
        price=150.0,
        market_cap=3e11,
        trailing_pe=22.0,
        forward_pe=None,
        earnings_growth_yoy=0.06,
        revenue_growth_yoy=None,
        dividend_yield=4.5,
        pe_5y_avg=25.0,
    )
    bucket, reason = assign_briefing_bucket(
        f, _m(company_type="缓慢增长型", peg=1.8), [],
    )
    assert bucket == BUCKET_DIVIDEND
    assert "股息" in reason
