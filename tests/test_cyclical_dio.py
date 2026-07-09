"""DIO / 行业 P/E / 宏观禁令测试。"""

from __future__ import annotations

from src.lynch.cyclical import (
    cyclical_dio_fatal,
    format_dio_trend_tail,
    format_industry_pe_anchor,
    inventory_health_block_lines,
)
from src.lynch.data.base import Fundamentals
from src.lynch.data.fmp import _MACRO_ENDPOINTS_BANNED, _dio_series_from_key_metrics
from src.lynch.fundamentals import FundamentalsError
from src.lynch.metrics import compute_metrics


def test_macro_endpoints_banned():
    assert "economic-indicators" in _MACRO_ENDPOINTS_BANNED
    from src.lynch.data.fmp import _FmpClient

    client = _FmpClient()
    try:
        client.get("economic-indicators", {"name": "GDP"})
        raise AssertionError("expected macro ban")
    except FundamentalsError as exc:
        assert "宏观端点" in str(exc)


def test_dio_series_and_yoy():
    rows = [
        {"fiscalYear": "2023", "daysOfInventoryOutstanding": 80.0},
        {"fiscalYear": "2024", "daysOfInventoryOutstanding": 70.0},
        {"fiscalYear": "2025", "daysOfInventoryOutstanding": 86.0},
    ]
    series, yoy = _dio_series_from_key_metrics(rows)
    assert series[2025] == 86.0
    assert yoy is not None
    assert yoy > 0  # worsening


def test_format_dio_trend_tail_worsening():
    f = Fundamentals(
        ticker="NUE",
        name="Nucor",
        sector="Basic Materials",
        industry="Steel",
        currency="USD",
        price=100.0,
        market_cap=1e10,
        trailing_pe=12.0,
        forward_pe=None,
        earnings_growth_yoy=0.2,
        revenue_growth_yoy=0.1,
        dio_series={2024: 70.0, 2025: 86.0},
        dio_yoy=0.23,
    )
    tail = format_dio_trend_tail(f)
    assert "70天" in tail and "86天" in tail and "恶化" in tail


def test_industry_pe_anchor():
    f = Fundamentals(
        ticker="NUE",
        name="Nucor",
        sector="Basic Materials",
        industry="Steel",
        currency="USD",
        price=100.0,
        market_cap=1e10,
        trailing_pe=22.0,
        forward_pe=None,
        earnings_growth_yoy=None,
        revenue_growth_yoy=None,
        industry_pe=27.5,
    )
    anchor = format_industry_pe_anchor(f)
    assert "22.0" in anchor and "27.5" in anchor and "低于" in anchor


def test_cyclical_dio_fatal():
    f = Fundamentals(
        ticker="NUE",
        name="Nucor",
        sector="Basic Materials",
        industry="Steel",
        currency="USD",
        price=100.0,
        market_cap=1e10,
        trailing_pe=12.0,
        forward_pe=None,
        earnings_growth_yoy=0.2,
        revenue_growth_yoy=0.1,
        dio_yoy=0.18,
    )
    m = compute_metrics(f)
    msg = cyclical_dio_fatal(f, m)
    assert msg is not None
    assert "DIO" in msg
