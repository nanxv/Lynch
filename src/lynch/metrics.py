"""林奇量化排雷引擎：把原始基本面换算成 PEG、负债率、存货/销售、每股净现金等硬指标。

判定灯（flag）：
- "green"  → 通过 / 极佳
- "yellow" → 警惕 / 数据不足
- "red"    → 红灯 / 危险
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .data.base import Fundamentals


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    value: float | None
    flag: str  # green / yellow / red
    verdict: str  # 一句大白话判定


@dataclass(frozen=True)
class LynchMetrics:
    growth_rate: float | None  # 长期 CAGR（小数），用于 PEG
    growth_basis: str
    peg: float | None  # 股息修正 PEG
    metrics: list[Metric] = field(default_factory=list)
    company_type: str = "稳定增长型"
    is_financial: bool = False
    is_cyclical: bool = False

    def by_key(self, key: str) -> Metric | None:
        return next((m for m in self.metrics if m.key == key), None)


# 林奇：没有公司能长期维持 50%+ 增长；超过则把 PEG 分母锚定到 35% 上限。
_GROWTH_CAP = 0.35
_GROWTH_CAP_TRIGGER = 0.50


def _cagr(series: dict[int, float]) -> float | None:
    """从年度序列计算 CAGR；端点非正或数据不足时返回 None。"""
    if len(series) < 2:
        return None
    years = sorted(series)
    first, last = series[years[0]], series[years[-1]]
    span = years[-1] - years[0]
    if span <= 0 or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1 / span) - 1


def _yoy(series: dict[int, float]) -> float | None:
    if len(series) < 2:
        return None
    years = sorted(series)
    prev, cur = series[years[-2]], series[years[-1]]
    if prev == 0:
        return None
    return (cur - prev) / abs(prev)


def _pick_growth(f: Fundamentals) -> tuple[float | None, str]:
    """林奇 PEG 必须用长期复合增长率(CAGR)，严禁用单季度同比(earningsGrowth)。

    优先多年摊薄 EPS 的 CAGR，其次净利润 CAGR；两者都缺则拒绝计算（不退回单季度同比）。
    """
    eps_cagr = _cagr(f.eps_series)
    if eps_cagr is not None:
        return eps_cagr, f"{len(f.eps_series)}年摊薄EPS复合增长率(CAGR)"
    ni_cagr = _cagr(f.net_income_series)
    if ni_cagr is not None:
        return ni_cagr, f"{len(f.net_income_series)}年净利润复合增长率(CAGR)"
    return None, "缺少≥2年年度EPS/净利润，无法算CAGR（拒绝用单季度同比代替）"


def _peg_metric(
    f: Fundamentals, growth_rate: float | None, cyclical: bool
) -> tuple[float | None, Metric]:
    """股息修正 PEG = P/E ÷ (CAGR% + 股息率%)，含 35% 增长上限锚定与周期股豁免。"""
    label = "股息修正PEG (P/E÷(CAGR+股息))"
    pe = f.trailing_pe
    div = f.dividend_yield or 0.0  # yfinance 已是百分比

    if pe is None or pe <= 0:
        note = (
            "周期股：当前亏损/无有效 P/E——很可能正处周期底部，勿当红灯，盯行业库存拐点。"
            if cyclical
            else "缺少有效市盈率（可能亏损或数据缺失），无法计算 PEG。"
        )
        return None, Metric("peg", label, None, "yellow", note)

    if growth_rate is None or growth_rate <= 0:
        note = (
            "周期股：利润下滑致长期增长为负——常是底部信号，交给行业数据判断，勿排雷。"
            if cyclical
            else "长期 CAGR 为负或缺失，PEG 失真——这门生意在萎缩，需人工核实。"
        )
        return None, Metric("peg", label, None, "yellow", note)

    capped = _GROWTH_CAP if growth_rate > _GROWTH_CAP_TRIGGER else growth_rate
    denom = capped * 100 + div
    peg = pe / denom
    cap_note = "（增速>50%已按上限35%锚定）" if growth_rate > _GROWTH_CAP_TRIGGER else ""
    div_note = f"（分母含股息{div:.1f}%）" if div > 0 else ""

    if peg <= 0.5:
        flag, note = "green", f"股息修正PEG {peg:.2f}≤0.5{div_note}{cap_note}——极佳击球区！"
    elif peg <= 1.0:
        flag, note = "green", f"股息修正PEG {peg:.2f}≤1{div_note}{cap_note}，估值被增长覆盖，合理。"
    elif peg <= 2.0:
        flag, note = "yellow", f"股息修正PEG {peg:.2f} 在 1~2 之间{div_note}，谈不上便宜。"
    elif cyclical:
        flag, note = "yellow", f"股息修正PEG {peg:.2f}>2，但周期股高估值可能是底部，勿盲目排雷。"
    else:
        flag, note = "red", f"股息修正PEG {peg:.2f}>2，市盈率远超增长——危险高估区。"
    return peg, Metric("peg", label, round(peg, 2), flag, note)


def _debt_metric(f: Fundamentals, financial: bool) -> Metric:
    if financial:
        return Metric(
            "debt", "长期负债 / 股东权益", None, "green",
            "金融业（银行/保险）负债天生极高，按林奇规则完全豁免此项排雷。",
        )
    ltd, eq = f.long_term_debt, f.stockholders_equity
    if ltd is None or eq is None or eq <= 0:
        return Metric(
            "debt", "长期负债 / 股东权益", None, "yellow",
            "长期负债或股东权益数据缺失，无法核实负债结构。",
        )
    ratio = ltd / eq
    if ratio <= 0.05:
        flag, note = "green", "几乎零长期负债——不会破产的公司，睡得着觉。"
    elif ratio <= 0.33:
        flag, note = "green", f"长期负债占股东权益 {ratio:.0%}，在 1/3 安全线以内。"
    elif ratio <= 0.80:
        flag, note = "yellow", f"长期负债占股东权益 {ratio:.0%}，超过 1/3 安全线，留意利息。"
    else:
        flag, note = "red", f"长期负债占股东权益 {ratio:.0%}，杠杆偏高，逆风时脆弱。"
    return Metric("debt", "长期负债 / 股东权益", round(ratio, 2), flag, note)


def _inventory_metric(f: Fundamentals) -> Metric:
    inv_yoy = _yoy(f.inventory_series)
    sales_yoy = _yoy(f.revenue_series)
    if inv_yoy is None or sales_yoy is None:
        return Metric(
            "inventory", "存货增速 vs 销售增速", None, "yellow",
            "存货或营收序列不足，无法比较（服务型公司可能无存货，正常）。",
        )
    gap = inv_yoy - sales_yoy
    val = round(gap * 100, 1)
    if inv_yoy <= sales_yoy:
        flag = "green"
        note = f"存货增速 {inv_yoy:.0%} ≤ 销售增速 {sales_yoy:.0%}，货能卖出去，健康。"
    elif gap <= 0.10:
        flag = "yellow"
        note = f"存货增速 {inv_yoy:.0%} 略高于销售 {sales_yoy:.0%}，轻微积压，盯着点。"
    else:
        flag = "red"
        note = f"存货增速 {inv_yoy:.0%} 远高于销售 {sales_yoy:.0%}——红灯！货堆在仓库里了。"
    return Metric("inventory", "存货增速 vs 销售增速(差,百分点)", val, flag, note)


def _net_cash_metric(f: Fundamentals) -> Metric:
    cash, debt, shares = f.total_cash, f.total_debt, f.shares_outstanding
    if cash is None or shares is None or shares <= 0:
        if f.cash_per_share is not None:
            return Metric(
                "net_cash", "每股净现金", round(f.cash_per_share, 2), "yellow",
                f"每股现金 {f.cash_per_share:.2f}（未扣负债），负债数据缺失。",
            )
        return Metric(
            "net_cash", "每股净现金", None, "yellow", "现金/股本数据缺失，无法计算安全垫。",
        )
    net_cash = cash - (debt or 0.0)
    per_share = net_cash / shares
    price = f.price
    if price and price > 0:
        pct = per_share / price
        if per_share <= 0:
            flag, note = "yellow", f"每股净现金 {per_share:.2f}（现金<负债），无隐蔽现金垫。"
        elif pct >= 0.30:
            flag, note = "green", f"每股净现金 {per_share:.2f}，占股价 {pct:.0%}——厚厚的安全垫！"
        else:
            flag, note = "green", f"每股净现金 {per_share:.2f}，占股价 {pct:.0%}，有一定缓冲。"
    else:
        flag = "green" if per_share > 0 else "yellow"
        note = f"每股净现金 {per_share:.2f}（现金 - 总负债 / 总股本）。"
    return Metric("net_cash", "每股净现金", round(per_share, 2), flag, note)


def _fcf_metric(f: Fundamentals) -> Metric:
    fcf = f.free_cashflow
    if fcf is None:
        return Metric("fcf", "自由现金流", None, "yellow", "自由现金流数据缺失。")
    if fcf > 0:
        mc = f.market_cap
        if mc and mc > 0:
            yld = fcf / mc
            return Metric(
                "fcf", "自由现金流", round(fcf, 0), "green",
                f"自由现金流为正，FCF/市值 ≈ {yld:.1%}——公司自己会造血。",
            )
        return Metric("fcf", "自由现金流", round(fcf, 0), "green", "自由现金流为正，能自我造血。")
    return Metric(
        "fcf", "自由现金流", round(fcf, 0), "red",
        "自由现金流为负——公司在烧钱，需靠外部输血维持。",
    )


def compute_metrics(f: Fundamentals) -> LynchMetrics:
    from .data.base import classify_company, is_cyclical, is_financial

    financial = is_financial(f)
    cyclical = is_cyclical(f)
    growth_rate, basis = _pick_growth(f)
    peg, peg_metric = _peg_metric(f, growth_rate, cyclical)
    metrics = [
        peg_metric,
        _debt_metric(f, financial),
        _inventory_metric(f),
        _net_cash_metric(f),
        _fcf_metric(f),
    ]
    return LynchMetrics(
        growth_rate=growth_rate,
        growth_basis=basis,
        peg=peg,
        metrics=metrics,
        company_type=classify_company(f),
        is_financial=financial,
        is_cyclical=cyclical,
    )
