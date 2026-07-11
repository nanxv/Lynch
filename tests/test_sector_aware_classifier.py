"""行业常识分类 / 排雷豁免 / 伪成长防线 单元测试。"""

from __future__ import annotations

from src.lynch.classifier import (
    classify_company,
    coarse_classify_from_labels,
    financial_from_labels,
    growth_cap_warn,
    inventory_exempt_from_labels,
    revenue_is_contracting,
)
from src.lynch.data.base import Fundamentals, QuickScreen
from src.lynch.funnel import evaluate_first_funnel
from src.lynch.metrics import compute_metrics


def test_financial_sector_labels():
    assert financial_from_labels("Financial Services", "Banks - Diversified")
    assert financial_from_labels("Insurance", "Life Insurance")
    assert financial_from_labels(None, "Credit Services")
    assert not financial_from_labels("Technology", "Software")


def test_inventory_exempt_tech_and_finance():
    assert inventory_exempt_from_labels("Technology", "Software - Application")
    assert inventory_exempt_from_labels("Communication Services", "Internet Content")
    assert inventory_exempt_from_labels("Financial Services", "Banks")
    assert not inventory_exempt_from_labels("Consumer Cyclical", "Apparel Retail")


def test_financial_metrics_exempt_debt_and_net_cash():
    f = Fundamentals(
        ticker="JPM",
        name="JPMorgan",
        sector="Financial Services",
        industry="Banks - Diversified",
        currency="USD",
        price=100.0,
        market_cap=1e11,
        trailing_pe=12.0,
        forward_pe=None,
        earnings_growth_yoy=0.10,
        revenue_growth_yoy=0.05,
        long_term_debt=5e11,
        stockholders_equity=1e11,
        total_cash=1e10,
        total_debt=2e12,
        shares_outstanding=1e9,
        eps_series={2020: 5.0, 2024: 8.0},
        revenue_series={2020: 1e11, 2024: 1.2e11},
        inventory_series={},
    )
    m = compute_metrics(f)
    assert m.is_financial
    debt = m.by_key("debt")
    nc = m.by_key("net_cash")
    inv = m.by_key("inventory")
    assert debt and debt.flag == "green" and "豁免" in debt.verdict
    assert nc and nc.flag == "green" and "豁免" in nc.verdict
    assert inv and inv.flag == "green" and "豁免" in inv.verdict
    assert "无法比较" not in (inv.verdict or "")


def test_fake_fast_grower_demoted_on_revenue_shrink():
    # 利润 CAGR 很高，但近两年营收萎缩 → 禁止快速增长型
    f = Fundamentals(
        ticker="AIG",
        name="FakeGrower",
        sector="Financial Services",
        industry="Insurance - Diversified",
        currency="USD",
        price=50.0,
        market_cap=1e10,
        trailing_pe=8.0,
        forward_pe=None,
        earnings_growth_yoy=0.40,
        revenue_growth_yoy=-0.12,
        long_term_debt=1e9,
        stockholders_equity=2e9,
        eps_series={2019: 1.0, 2020: 2.0, 2021: 3.0, 2022: 4.0, 2023: 5.0, 2024: 6.0},
        revenue_series={2022: 100.0, 2023: 90.0, 2024: 80.0},
        inventory_series={},
    )
    assert revenue_is_contracting(f) is True
    assert classify_company(f) != "快速增长型"
    assert classify_company(f) in ("缓慢增长型", "困境反转型", "稳定增长型")


def test_growth_cap_warn_only_fast_grower():
    cyc = Fundamentals(
        ticker="XOM",
        name="OilCo",
        sector="Energy",
        industry="Oil & Gas Integrated",
        currency="USD",
        price=100.0,
        market_cap=1e11,
        trailing_pe=40.0,
        forward_pe=None,
        earnings_growth_yoy=1.50,
        revenue_growth_yoy=0.40,
        eps_series={2020: 1.0, 2024: 5.0},
        revenue_series={2020: 1e10, 2024: 2e10},
        inventory_series={2020: 1e9, 2024: 1.1e9},
    )
    assert classify_company(cyc) == "周期型"
    assert growth_cap_warn(cyc, company_type="周期型") is False
    m = compute_metrics(cyc)
    assert m.growth_cap_warn is False

    fast = Fundamentals(
        ticker="FAST",
        name="FastCo",
        sector="Technology",
        industry="Software",
        currency="USD",
        price=100.0,
        market_cap=1e10,
        trailing_pe=30.0,
        forward_pe=None,
        earnings_growth_yoy=0.30,
        revenue_growth_yoy=0.25,
        eps_series={2020: 1.0, 2024: 2.5},
        revenue_series={2020: 1e9, 2024: 2e9},
        inventory_series={},
    )
    assert classify_company(fast) == "快速增长型"
    assert growth_cap_warn(fast, company_type="快速增长型") is True


def test_funnel_passes_cyclical_with_peg_na():
    q = QuickScreen(
        ticker="STEEL",
        name="SteelCo",
        is_cyclical=True,
        coarse_class="周期型",
        quick_peg=None,
        trailing_pe=None,
        debt_ratio=0.1,
        inventory_growth=0.02,
        sales_growth=0.05,
        growth_yoy=-0.40,
        sector="Basic Materials",
        industry="Steel",
    )
    ok, tagged = evaluate_first_funnel(q)
    assert ok is True
    assert "peg_na_cyclical" in tagged.pass_channels or "cyclical" in tagged.pass_channels


def test_coarse_classify_revenue_guard():
    assert (
        coarse_classify_from_labels(
            sector="Consumer Defensive",
            industry="Packaged Foods",
            growth=0.35,
            dividend_yield_pct=1.0,
            revenue_growth=-0.15,
        )
        != "快速增长型"
    )
