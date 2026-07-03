"""林奇量化排雷引擎：把原始基本面换算成 PEG、负债率、存货/销售、每股净现金等硬指标。

判定灯（flag）：
- "green"  → 通过 / 极佳
- "yellow" → 警惕 / 数据不足
- "red"    → 红灯 / 危险
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .fundamentals import Fundamentals


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    value: float | None
    flag: str  # green / yellow / red
    verdict: str  # 一句大白话判定


@dataclass(frozen=True)
class LynchMetrics:
    growth_rate: float | None  # 用于 PEG 的年增长率（小数）
    growth_basis: str
    peg: float | None
    metrics: list[Metric] = field(default_factory=list)

    def by_key(self, key: str) -> Metric | None:
        return next((m for m in self.metrics if m.key == key), None)


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
    """林奇 PEG 用的是长期 EPS 增长率；多年 EPS CAGR 优先，其次净利润，再退回同比。"""
    eps_cagr = _cagr(f.eps_series)
    if eps_cagr is not None:
        n = len(f.eps_series)
        return eps_cagr, f"{n}年摊薄EPS复合增长率(CAGR)"
    ni_cagr = _cagr(f.net_income_series)
    if ni_cagr is not None:
        n = len(f.net_income_series)
        return ni_cagr, f"{n}年净利润复合增长率(CAGR)"
    if f.earnings_growth_yoy is not None:
        return f.earnings_growth_yoy, "info 提供的盈利同比增长(退回值)"
    return None, "无可靠增长数据"


def _peg_metric(pe: float | None, growth_rate: float | None) -> tuple[float | None, Metric]:
    if pe is None or pe <= 0:
        return None, Metric(
            "peg", "PEG (市盈率/增长率)", None, "yellow",
            "缺少有效市盈率（可能亏损或数据缺失），无法计算 PEG。",
        )
    if growth_rate is None or growth_rate <= 0:
        return None, Metric(
            "peg", "PEG (市盈率/增长率)", None, "yellow",
            "增长率为负或缺失，PEG 失真——这门生意在萎缩，需人工核实。",
        )
    growth_pct = growth_rate * 100
    peg = pe / growth_pct
    if peg <= 0.5:
        flag, note = "green", "PEG≤0.5，市盈率不到增长率一半——林奇眼里的极佳低估。"
    elif peg <= 1.0:
        flag, note = "green", "PEG≤1，市盈率被增长率覆盖，价格合理。"
    elif peg <= 2.0:
        flag, note = "yellow", "PEG 在 1~2 之间，谈不上便宜，需要故事撑腰。"
    else:
        flag, note = "red", "PEG>2，市盈率是增长率两倍以上——危险的高估区。"
    return peg, Metric("peg", "PEG (市盈率/增长率)", round(peg, 2), flag, note)


def _debt_metric(f: Fundamentals) -> Metric:
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
    growth_rate, basis = _pick_growth(f)
    peg, peg_metric = _peg_metric(f.trailing_pe, growth_rate)
    metrics = [
        peg_metric,
        _debt_metric(f),
        _inventory_metric(f),
        _net_cash_metric(f),
        _fcf_metric(f),
    ]
    return LynchMetrics(
        growth_rate=growth_rate,
        growth_basis=basis,
        peg=peg,
        metrics=metrics,
    )
