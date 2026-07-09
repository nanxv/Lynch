"""林奇周期股分相逻辑：底部候选 / 顶部陷阱 / 中性。

原书反直觉法则（Cyclicals）：
- 底部：利润差、P/E 高甚至亏损，且存货未堆积（渠道/厂商库存在去化）
- 顶部：利润靓、P/E 极低，且存货开始堆积——「最便宜时往往最贵」
- 绝不在财报最漂亮、P/E 最低时追买周期股
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from . import config
from .data.base import Fundamentals, QuickScreen
from .metrics import LynchMetrics


class CyclicalPhase(str, Enum):
    NOT_CYCLICAL = "not_cyclical"
    BOTTOM_CANDIDATE = "bottom"   # 低谷观察 / 漏斗通道 C
    TOP_WARNING = "top"           # 周期见顶陷阱
    NEUTRAL = "neutral"           # 周期股但信号混杂


@dataclass(frozen=True)
class CyclicalAssessment:
    """周期股量化分相结果。"""

    phase: CyclicalPhase
    distress_signals: tuple[str, ...]
    top_signals: tuple[str, ...]
    inventory_ok: bool
    summary: str | None = None


def _yoy(series: dict[int, float]) -> float | None:
    if len(series) < 2:
        return None
    years = sorted(series)
    prev, cur = series[years[-2]], series[years[-1]]
    if prev == 0:
        return None
    return (cur - prev) / abs(prev)


def _inventory_growth(inv_yoy: float | None, sales_yoy: float | None) -> bool:
    """存货未堆积：缺失数据不拦；有数据则要求存货增速 ≤ 销售增速。"""
    if inv_yoy is None or sales_yoy is None:
        return True
    return inv_yoy <= sales_yoy


def _inventory_building(inv_yoy: float | None, sales_yoy: float | None) -> bool:
    """存货开始堆积（林奇周期见顶核心信号之一）。"""
    if inv_yoy is None or sales_yoy is None:
        return False
    gap = inv_yoy - sales_yoy
    if gap <= config.CYCLICAL_INV_GAP_TOP:
        return False
    if inv_yoy > max(sales_yoy, 0) * config.CYCLICAL_INV_SALES_MULT:
        return True
    return gap > config.CYCLICAL_INV_GAP_TOP


def _earnings_at_peak(f: Fundamentals) -> bool:
    """净利润是否处于近年高位（周期顶部常见特征）。"""
    if len(f.net_income_series) < 2:
        return False
    years = sorted(f.net_income_series)
    latest = f.net_income_series[years[-1]]
    prior = [f.net_income_series[y] for y in years[:-1]]
    if not prior:
        return False
    return latest >= max(prior) * (1.0 - config.CYCLICAL_PEAK_TOLERANCE)


def _distress_signals(
    *,
    trailing_pe: float | None,
    growth_yoy: float | None,
    long_growth: float | None = None,
    quick_peg: float | None = None,
) -> tuple[str, ...]:
    """周期底部常见「利润难看」信号（任一命中即可）。"""
    out: list[str] = []
    if trailing_pe is None or trailing_pe <= 0:
        out.append("当前亏损/无有效P/E")
    elif trailing_pe >= config.CYCLICAL_PE_DISTRESS:
        out.append(f"P/E高达{trailing_pe:.0f}")
    if growth_yoy is not None and growth_yoy < 0:
        out.append(f"盈利同比{growth_yoy:.0%}")
    if long_growth is not None and long_growth < 0:
        out.append("长期利润下滑")
    if quick_peg is None and trailing_pe is not None and trailing_pe > 0:
        # 有盈利但 PEG 算不出（分母为负或缺失）——常是周期底部
        if (growth_yoy is not None and growth_yoy <= 0) or (long_growth is not None and long_growth <= 0):
            out.append("盈利下滑致PEG失真")
    return tuple(out)


def _dio_worsening(dio_yoy: float | None) -> bool:
    """DIO 天数拉长超过阈值（正变化率 = 恶化）。"""
    return dio_yoy is not None and dio_yoy > config.CYCLICAL_DIO_WORSEN_THRESHOLD


def _profits_elevated(trailing_pe: float | None) -> bool:
    """周期股「利润高位」代理：P/E 偏低且盈利为正。"""
    return (
        trailing_pe is not None
        and trailing_pe > 0
        and trailing_pe <= config.CYCLICAL_PE_TRAP_MAX
    )


def format_dio_trend_tail(f: Fundamentals) -> str:
    """简报 UI 用 DIO 趋势尾巴，如 [DIO: 70天 📈 86天 (恶化)]。"""
    if len(f.dio_series) < 2:
        return ""
    years = sorted(f.dio_series)
    prev_d = f.dio_series[years[-2]]
    cur_d = f.dio_series[years[-1]]
    if cur_d > prev_d * 1.02:
        arrow, label = "📈", "恶化"
    elif cur_d < prev_d * 0.98:
        arrow, label = "📉", "改善"
    else:
        arrow, label = "→", "持平"
    return f"[DIO: {prev_d:.0f}天 {arrow} {cur_d:.0f}天 ({label})]"


def format_industry_pe_anchor(f: Fundamentals) -> str:
    """行业 P/E 锚点文案。"""
    pe = f.trailing_pe or f.spot_pe
    if pe is None and f.industry_pe is None:
        return ""
    pe_s = f"{pe:.1f}" if pe is not None else "N/A"
    if f.industry_pe is None:
        return f"当前 P/E {pe_s}倍（行业均值缺失）"
    ind_s = f"{f.industry_pe:.1f}"
    if pe is not None and f.industry_pe > 0:
        rel = "低于" if pe < f.industry_pe else ("高于" if pe > f.industry_pe else "持平于")
        return f"当前 P/E {pe_s}倍 vs 行业平均 {ind_s}倍（{rel}同业）"
    return f"当前 P/E {pe_s}倍 vs 行业平均 {ind_s}倍"


def inventory_health_block_lines(f: Fundamentals) -> list[str]:
    """喂给大模型的【存货周转健康度】+ 行业 P/E 锚点。"""
    lines: list[str] = []
    if f.dio_series:
        lines.append("— 存货周转健康度（微观·DIO，天数越短越好）—")
        years = sorted(f.dio_series)
        seq = " → ".join(f"{y}:{f.dio_series[y]:.0f}天" for y in years)
        lines.append(f"DIO 序列: {seq}")
        if f.dio_yoy is not None:
            direction = "恶化（存货周转拉长）" if f.dio_yoy > 0 else "改善（去库加速）"
            lines.append(f"最近两期 DIO 变化: {f.dio_yoy:+.1%}（{direction}）")
            if _dio_worsening(f.dio_yoy):
                lines.append(
                    f"⚠️ DIO 拉长超过 {config.CYCLICAL_DIO_WORSEN_THRESHOLD:.0%}——周期股隐性库存积压红灯"
                )
    anchor = format_industry_pe_anchor(f)
    if anchor:
        lines.append("— 行业 P/E 锚点（同业微观参照，非宏观）—")
        lines.append(anchor)
    return lines


def cyclical_dio_fatal(f: Fundamentals, m: LynchMetrics) -> str | None:
    """周期股 DIO 熔断：利润高位 + DIO 恶化 → 致命红灯文案。"""
    if not m.is_cyclical:
        return None
    if _profits_elevated(f.trailing_pe) and _dio_worsening(f.dio_yoy):
        pct = f.dio_yoy * 100 if f.dio_yoy is not None else 0
        return (
            f"周期见顶探针：利润高位(P/E{f.trailing_pe:.0f})但DIO恶化+{pct:.0f}%"
            f"（隐性库存积压，勿被低PEG迷惑）"
        )
    return None


def _top_trap_signals(
    *,
    trailing_pe: float | None,
    growth_yoy: float | None,
    inv_yoy: float | None,
    sales_yoy: float | None,
    pe_5y_avg: float | None = None,
    earnings_peak: bool = False,
    quick_peg: float | None = None,
    dio_yoy: float | None = None,
) -> tuple[str, ...]:
    """周期顶部「漂亮财报 + 低 P/E」陷阱信号。"""
    out: list[str] = []
    pe_low = (
        trailing_pe is not None
        and trailing_pe > 0
        and trailing_pe <= config.CYCLICAL_PE_TRAP_MAX
    )
    earnings_strong = growth_yoy is not None and growth_yoy >= config.CYCLICAL_EARNINGS_STRONG
    inv_build = _inventory_building(inv_yoy, sales_yoy)

    if pe_low and earnings_strong:
        out.append(f"利润同比+{growth_yoy:.0%}但P/E仅{trailing_pe:.0f}（周期便宜假象）")
    if pe_low and pe_5y_avg is not None and pe_5y_avg > 0:
        if trailing_pe <= pe_5y_avg * config.CYCLICAL_PE_VS_5Y_TRAP:
            out.append(f"P/E{trailing_pe:.0f}处5年均{pe_5y_avg:.0f}低位（历史便宜区）")
    if earnings_peak and earnings_strong:
        out.append("净利润处近年高位")
    if inv_build:
        gap = (inv_yoy - sales_yoy) if inv_yoy is not None and sales_yoy is not None else None
        if gap is not None:
            out.append(f"存货堆积(存货+{inv_yoy:.0%} vs 销售+{sales_yoy:.0%})")
    if (
        quick_peg is not None
        and 0 < quick_peg < config.CYCLICAL_TRAP_PEG_MAX
        and earnings_strong
        and inv_build
    ):
        out.append(f"低PEG{quick_peg:.2f}伴随存货堆积（典型周期顶）")
    if pe_low and earnings_strong and _dio_worsening(dio_yoy):
        out.append(f"DIO恶化+{dio_yoy:.0%}（存货周转天数拉长）")

    # 林奇：低 P/E + 利润靓 + 存货堆 = 强顶部；低 P/E + 利润靓 + 利润峰值 = 中顶部
    if not out:
        return ()
    if inv_build or earnings_peak:
        return tuple(out)
    if pe_low and earnings_strong:
        return tuple(out)
    return ()


def _resolve_phase(
    *,
    is_cyclical: bool,
    distress: tuple[str, ...],
    top: tuple[str, ...],
    inventory_ok: bool,
) -> CyclicalPhase:
    if not is_cyclical:
        return CyclicalPhase.NOT_CYCLICAL
    # 存货堆积 → 铁定顶部；其余顶部信号亦优先于底部
    if top and _inventory_building_from_top(top):
        return CyclicalPhase.TOP_WARNING
    if top:
        return CyclicalPhase.TOP_WARNING
    if distress and inventory_ok:
        return CyclicalPhase.BOTTOM_CANDIDATE
    return CyclicalPhase.NEUTRAL


def _inventory_building_from_top(top: tuple[str, ...]) -> bool:
    return any("存货堆积" in s for s in top)


def assess_cyclical_quick(q: QuickScreen) -> CyclicalAssessment:
    """漏斗轻量层：用 QuickScreen 字段做周期分相。"""
    if not q.is_cyclical:
        return CyclicalAssessment(CyclicalPhase.NOT_CYCLICAL, (), (), True)

    inv, sales = q.inventory_growth, q.sales_growth
    inventory_ok = _inventory_growth(inv, sales)
    distress = _distress_signals(
        trailing_pe=q.trailing_pe,
        growth_yoy=q.growth_yoy,
        quick_peg=q.quick_peg,
    )
    top = _top_trap_signals(
        trailing_pe=q.trailing_pe,
        growth_yoy=q.growth_yoy,
        inv_yoy=inv,
        sales_yoy=sales,
        pe_5y_avg=q.pe_5y_avg,
        quick_peg=q.quick_peg,
    )
    phase = _resolve_phase(
        is_cyclical=True,
        distress=distress,
        top=top,
        inventory_ok=inventory_ok,
    )
    summary = _format_summary(phase, distress, top, inventory_ok)
    return CyclicalAssessment(phase, distress, top, inventory_ok, summary)


def assess_cyclical(f: Fundamentals, m: LynchMetrics) -> CyclicalAssessment:
    """深度层：用完整财报序列做周期分相。"""
    if not m.is_cyclical:
        return CyclicalAssessment(CyclicalPhase.NOT_CYCLICAL, (), (), True)

    inv_yoy = _yoy(f.inventory_series)
    sales_yoy = _yoy(f.revenue_series)
    inventory_ok = _inventory_growth(inv_yoy, sales_yoy)
    distress = _distress_signals(
        trailing_pe=f.trailing_pe,
        growth_yoy=f.earnings_growth_yoy,
        long_growth=m.growth_rate,
    )
    top = _top_trap_signals(
        trailing_pe=f.trailing_pe,
        growth_yoy=f.earnings_growth_yoy,
        inv_yoy=inv_yoy,
        sales_yoy=sales_yoy,
        pe_5y_avg=f.pe_5y_avg,
        earnings_peak=_earnings_at_peak(f),
        dio_yoy=f.dio_yoy,
    )
    phase = _resolve_phase(
        is_cyclical=True,
        distress=distress,
        top=top,
        inventory_ok=inventory_ok,
    )
    summary = _format_summary(phase, distress, top, inventory_ok)
    return CyclicalAssessment(phase, distress, top, inventory_ok, summary)


def _format_summary(
    phase: CyclicalPhase,
    distress: tuple[str, ...],
    top: tuple[str, ...],
    inventory_ok: bool,
) -> str | None:
    if phase == CyclicalPhase.NOT_CYCLICAL:
        return None
    if phase == CyclicalPhase.TOP_WARNING:
        return "；".join(top) + " → 疑似周期顶部陷阱，勿在「最便宜」时追买"
    if phase == CyclicalPhase.BOTTOM_CANDIDATE:
        inv_note = "存货未堆积" if inventory_ok else ""
        body = "；".join(distress)
        if inv_note:
            body = f"{body}；{inv_note}" if body else inv_note
        return body + " → 疑似周期底部，盯行业渠道/库存拐点，勿当红灯"
    if distress:
        return "；".join(distress) + "（信号混杂，需结合产业库存/报价数据）"
    if top:
        return "；".join(top) + "（部分见顶信号，谨慎）"
    return None


def passes_cyclical_funnel(q: QuickScreen) -> bool:
    """通道 C：仅放行周期底部候选，排除顶部陷阱与中性质疑。"""
    return assess_cyclical_quick(q).phase == CyclicalPhase.BOTTOM_CANDIDATE


def cyclical_watch(f: Fundamentals, m: LynchMetrics) -> str | None:
    """周期低谷观察期：简报置顶桶用。"""
    a = assess_cyclical(f, m)
    if a.phase == CyclicalPhase.BOTTOM_CANDIDATE:
        return a.summary
    return None


def cyclical_top_warning(f: Fundamentals, m: LynchMetrics) -> str | None:
    """周期顶部陷阱：简报置顶桶用（与低谷观察并列）。"""
    a = assess_cyclical(f, m)
    if a.phase == CyclicalPhase.TOP_WARNING:
        return a.summary
    return None


def cyclical_data_block_lines(f: Fundamentals, m: LynchMetrics) -> list[str]:
    """喂给 Gemini 的周期分相摘要（深度分析用）。"""
    lines = inventory_health_block_lines(f)
    a = assess_cyclical(f, m)
    if a.phase == CyclicalPhase.NOT_CYCLICAL:
        return lines
    lines.append("— 林奇周期分相（反直觉法则）—")
    phase_cn = {
        CyclicalPhase.BOTTOM_CANDIDATE: "低谷候选（利润难看+存货未堆）",
        CyclicalPhase.TOP_WARNING: "顶部陷阱（利润靓/低P/E+存货或利润峰值）",
        CyclicalPhase.NEUTRAL: "信号混杂（需产业库存/报价佐证）",
    }.get(a.phase, str(a.phase))
    lines.append(f"分相: {phase_cn}")
    if a.distress_signals:
        lines.append(f"底部信号: {'；'.join(a.distress_signals)}")
    if a.top_signals:
        lines.append(f"顶部信号: {'；'.join(a.top_signals)}")
    lines.append(
        f"存货状态: {'未堆积（利好底部）' if a.inventory_ok else '有堆积风险'}"
    )
    if a.summary:
        lines.append(f"系统提示: {a.summary}")
    lines.append(
        "纪律: 周期股不在财报最漂亮、P/E最低时追买；底部看 DIO 去库，顶部看 DIO 拉长与存货堆积。"
    )
    return lines
