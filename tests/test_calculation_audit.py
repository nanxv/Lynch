"""阶段 2 计算验算 — 离线测试。"""

from __future__ import annotations

from src.lynch.calculation_audit import audit_calculations
from src.lynch.data.base import Fundamentals


def _f(**kw) -> Fundamentals:
    d = dict(
        ticker="TEST",
        name="Test",
        sector="Technology",
        industry="Semi",
        currency="USD",
        price=100.0,
        market_cap=1e12,
        trailing_pe=20.0,
        forward_pe=18.0,
        earnings_growth_yoy=0.2,
        revenue_growth_yoy=0.15,
        eps_series={2022: 2.0, 2023: 2.5, 2024: 3.0},
        net_income_series={2022: 1e9, 2023: 1.2e9, 2024: 1.5e9},
        revenue_series={2023: 8e9, 2024: 9e9},
        inventory_series={2023: 1e9, 2024: 1.05e9},
        long_term_debt=2e9,
        total_debt=3e9,
        stockholders_equity=10e9,
        total_cash=5e9,
        free_cashflow=2e9,
        shares_outstanding=10e9,
        dividend_yield=1.5,
        exchange="NMS",
    )
    d.update(kw)
    return Fundamentals(**d)


def test_calculation_audit_all_pass():
    r = audit_calculations(_f(), mode="weekly", data_trusted=True)
    assert r.all_match, [(s.key, s.manual_value, s.engine_value) for s in r.steps if s.status == "fail"]
