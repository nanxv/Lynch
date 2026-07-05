"""原始数据质量质检 — 离线单元测试（合成数据，不依赖网络）。"""

from __future__ import annotations

from datetime import date

from src.lynch.data.base import Fundamentals
from src.lynch.data_quality import validate_raw_data


def _base_f(**kw) -> Fundamentals:
    defaults = dict(
        ticker="TEST",
        name="Test Co",
        sector="Technology",
        industry="Semiconductors",
        currency="USD",
        price=100.0,
        market_cap=1_000_000_000_000,
        trailing_pe=25.0,
        forward_pe=22.0,
        earnings_growth_yoy=0.20,
        revenue_growth_yoy=0.15,
        eps_series={2022: 2.0, 2023: 2.5, 2024: 3.0},
        net_income_series={2022: 1e9, 2023: 1.2e9, 2024: 1.5e9},
        revenue_series={2020: 5e9, 2021: 6e9, 2022: 7e9, 2023: 8e9, 2024: 9e9},
        inventory_series={2023: 1e9, 2024: 1.1e9},
        long_term_debt=2e9,
        total_debt=3e9,
        stockholders_equity=10e9,
        total_cash=5e9,
        free_cashflow=2e9,
        shares_outstanding=10_000_000_000,
        dividend_yield=1.5,
        exchange="NMS",
        report_mode="weekly",
    )
    defaults.update(kw)
    return Fundamentals(**defaults)


def test_clean_weekly_passes():
    f = _base_f()
    info = {
        "trailingPE": 25.0,
        "marketCap": 1e12,
        "debtToEquity": 20.0,
        "earningsGrowth": 0.18,
        "freeCashflow": 2e9,
    }
    r = validate_raw_data(
        f, info, mode="weekly",
        has_price_history=True,
        last_bar_date=date.today(),
        ref_date=date(2026, 7, 2),
    )
    assert r.is_trusted
    assert r.fail_count == 0


def test_missing_price_fails():
    f = _base_f(price=None)
    r = validate_raw_data(f, {}, mode="daily", has_price_history=False)
    assert not r.is_trusted
    assert "price" in r.missing_fields


def test_stale_annual_statements_fail():
    f = _base_f(
        eps_series={2020: 1.0, 2021: 1.2},
        net_income_series={2020: 1e9, 2021: 1.2e9},
        revenue_series={2018: 1e9, 2019: 2e9, 2020: 3e9, 2021: 4e9, 2022: 5e9},
    )
    r = validate_raw_data(
        f, {}, mode="weekly",
        has_price_history=True,
        last_bar_date=date(2026, 7, 2),
        ref_date=date(2026, 7, 2),
    )
    assert any(i.field == "annual_statements" and i.level == "fail" for i in r.issues)


def test_cross_source_debt_warn():
    f = _base_f(long_term_debt=1e9, stockholders_equity=10e9)
    info = {"debtToEquity": 80.0}
    r = validate_raw_data(
        f, info, mode="weekly", has_price_history=True, last_bar_date=date.today(),
    )
    assert any(i.field == "debt_ratio" for i in r.issues)


def test_dividend_yield_unit_warn():
    f = _base_f(dividend_yield=0.015)
    r = validate_raw_data(
        f, {"dividendYield": 0.015}, mode="weekly",
        has_price_history=True, last_bar_date=date.today(),
    )
    assert any(i.field == "dividend_yield" for i in r.issues)
