"""周期股林奇分相逻辑测试。"""

from __future__ import annotations

from src.lynch.cyclical import (
    CyclicalPhase,
    assess_cyclical,
    assess_cyclical_quick,
    passes_cyclical_funnel,
)
from src.lynch.data.base import Fundamentals, QuickScreen
from src.lynch.metrics import LynchMetrics, compute_metrics


def _cyclical_fundamentals(**kwargs) -> Fundamentals:
    base = dict(
        ticker="ALB",
        name="Albemarle",
        sector="Basic Materials",
        industry="Specialty Chemicals",
        currency="USD",
        price=50.0,
        market_cap=5e9,
        trailing_pe=-20.0,
        forward_pe=None,
        earnings_growth_yoy=-0.25,
        revenue_growth_yoy=0.05,
        inventory_series={2024: 100.0, 2025: 105.0},
        revenue_series={2024: 1000.0, 2025: 1100.0},
        net_income_series={2023: 500.0, 2024: 200.0, 2025: 100.0},
        source="test",
    )
    base.update(kwargs)
    return Fundamentals(**base)


def _cyclical_quick(**kwargs) -> QuickScreen:
    base = dict(
        ticker="ALB",
        is_cyclical=True,
        trailing_pe=-20.0,
        growth_yoy=-0.25,
        inventory_growth=0.05,
        sales_growth=0.10,
        quick_peg=None,
    )
    base.update(kwargs)
    return QuickScreen(**base)


def test_bottom_candidate_loss_and_inventory_ok():
    q = _cyclical_quick()
    a = assess_cyclical_quick(q)
    assert a.phase == CyclicalPhase.BOTTOM_CANDIDATE
    assert passes_cyclical_funnel(q)


def test_top_trap_low_pe_strong_earnings():
    q = _cyclical_quick(
        trailing_pe=8.0,
        growth_yoy=0.30,
        inventory_growth=0.20,
        sales_growth=0.05,
        quick_peg=0.4,
    )
    a = assess_cyclical_quick(q)
    assert a.phase == CyclicalPhase.TOP_WARNING
    assert not passes_cyclical_funnel(q)


def test_top_trap_earnings_peak_with_inventory():
    f = _cyclical_fundamentals(
        trailing_pe=10.0,
        earnings_growth_yoy=0.25,
        inventory_series={2024: 100.0, 2025: 150.0},
        revenue_series={2024: 1000.0, 2025: 1050.0},
        net_income_series={2023: 400.0, 2024: 600.0, 2025: 650.0},
    )
    m = compute_metrics(f)
    a = assess_cyclical(f, m)
    assert a.phase == CyclicalPhase.TOP_WARNING
    assert m.is_cyclical


def test_neutral_cyclical_no_clear_signal():
    q = _cyclical_quick(
        trailing_pe=18.0,
        growth_yoy=0.08,
        inventory_growth=0.06,
        sales_growth=0.07,
        quick_peg=1.2,
    )
    a = assess_cyclical_quick(q)
    assert a.phase == CyclicalPhase.NEUTRAL
    assert not passes_cyclical_funnel(q)


def test_non_cyclical_not_assessed():
    q = QuickScreen(ticker="KO", is_cyclical=False, trailing_pe=25.0)
    assert assess_cyclical_quick(q).phase == CyclicalPhase.NOT_CYCLICAL
