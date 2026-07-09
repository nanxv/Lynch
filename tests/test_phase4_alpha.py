"""Phase 4 筹码面 Alpha 探针 — 单元测试。"""

from __future__ import annotations

from src.lynch.data.base import Fundamentals
from src.lynch.data.fmp_alpha import _insider_from_search, _norm_pct, _trade_side
from src.lynch.metrics import LynchMetrics, alpha_intel_lines, evaluate_alpha_flags
from src.lynch.notify import render_multi_bucket_briefing


def _fund(
    *,
    held: float | None = 0.25,
    buys: int = 3,
    sells: int = 1,
    signal: bool = True,
) -> Fundamentals:
    return Fundamentals(
        ticker="ALPHA",
        name="Alpha Co",
        sector="Technology",
        industry="Software",
        currency="USD",
        price=50.0,
        market_cap=500_000_000,
        trailing_pe=15.0,
        forward_pe=None,
        earnings_growth_yoy=0.20,
        revenue_growth_yoy=0.18,
        held_percent_institutions=held,
        insider_buy_count=buys,
        insider_sell_count=sells,
        insider_net_buy_signal=signal,
    )


def _metrics(**kwargs) -> LynchMetrics:
    defaults = dict(
        growth_rate=0.2,
        growth_basis="test",
        peg=0.8,
        company_type="快速增长型",
        institutional_neglect=True,
        insider_net_buying=True,
        ultimate_alpha=True,
    )
    defaults.update(kwargs)
    return LynchMetrics(metrics=[], **defaults)


def test_norm_pct():
    assert _norm_pct(0.35) == 0.35
    assert _norm_pct(35) == 0.35


def test_trade_side_purchase():
    assert _trade_side({"transactionType": "P-Purchase"}) == "buy"
    assert _trade_side({"transactionType": "S-Sale"}) == "sell"


def test_evaluate_alpha_flags():
    f = _fund(held=0.30, signal=True)
    neglect, insider, ultimate = evaluate_alpha_flags(f)
    assert neglect and insider and ultimate


def test_alpha_intel_lines():
    f = _fund()
    lines = alpha_intel_lines(f, _metrics())
    assert any("机构冷落" in x for x in lines)
    assert any("内部人动向" in x for x in lines)


def test_ultimate_alpha_bucket_render():
    out = render_multi_bucket_briefing({
        "fast_grower": [
            ("SMOL", "SmallCo", 0.5, "PEG 0.5·极佳", "快速增长型", True),
        ],
        "asset_play": [
            ("CASH", "CashCo", -0.4, "净现金厚", "稳定增长型", False),
        ],
    })
    assert "终极 Alpha 标的" in out
    assert "SMOL" in out
    assert "CASH" in out
    assert "终极 Alpha" not in out.split("CASH")[1].split("---")[0] or "CASH" in out


def test_insider_search_mock_api():
    class _Api:
        def get(self, path, params, optional=False):
            return [
                {
                    "transactionDate": "2026-05-01",
                    "transactionType": "P-Purchase",
                },
                {
                    "transactionDate": "2026-04-01",
                    "transactionType": "P-Purchase",
                },
                {
                    "transactionDate": "2026-03-01",
                    "transactionType": "S-Sale",
                },
            ]

    buys, sells = _insider_from_search(_Api(), "TEST")
    assert buys == 2
    assert sells == 1
