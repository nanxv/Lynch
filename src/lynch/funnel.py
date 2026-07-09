"""双层漏斗核心逻辑。

第一层（纯代码硬指标粗筛）：从上万只成分股里用无延迟的本地计算刷掉绝大多数垃圾股。
第二层（AI 成本熔断）：对幸存者排序并只把前 N 只送给 Gemini，其余降级为仅硬指标。
另含 fatal_warnings：提取"故事变坏"的致命红灯，用于置顶排雷摘要。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import dataclasses

from . import config
from .cyclical import cyclical_dio_fatal, cyclical_top_warning, cyclical_watch, passes_cyclical_funnel
from .data.base import BaseDataProvider, Fundamentals, QuickScreen
from .metrics import LynchMetrics, check_sbi_tradable, check_sbi_tradable_fundamentals
from .sniper import is_sniper_candidate


def is_hardcore_alpha_candidate(sbi_tradable: bool, signal_order: int | None) -> bool:
    """硬核深挖区候选：SBI 买不到，但 AI 给出买入/观察/持有信号。"""
    if sbi_tradable or signal_order is None:
        return False
    return signal_order <= 2  # 强烈买入 / 观察仓 / 钝感持有


def _debt_ok(q: QuickScreen, max_ratio: float) -> bool:
    """负债门：金融豁免；缺数据不拦截；否则 LTD/E ≤ 上限。"""
    if q.is_financial:
        return True
    if q.debt_ratio is None:
        return True
    return q.debt_ratio <= max_ratio


def _div_sustainable(q: QuickScreen) -> bool:
    """粗略分红可持续：FCF>0 或 payout < 上限。"""
    if q.fcf_positive is True:
        return True
    if q.payout_ratio is not None and q.payout_ratio < config.FUNNEL_MAX_PAYOUT_RATIO:
        return True
    return False


def _passes_first_funnel(q: QuickScreen) -> bool:
    """第一层多通道漏斗：负债分通道 + (PEG | 周期 | 净现金 | 稳增 | 慢增股息 | 困境) OR。"""
    return bool(evaluate_first_funnel(q)[0])


def evaluate_first_funnel(q: QuickScreen) -> tuple[bool, QuickScreen]:
    """判定是否通过第一层；返回 (通过?, 可能打标后的 QuickScreen)。

    通道：
      A peg — 严格负债 + 粗略股息修正 PEG
      B net_cash — 严格负债（金融豁免）+ 净现金/股价
      C cyclical — 周期 + 无有效 PEG/亏损 + 存货未堆
      D stalwart — 非快增非周期、盈利；PE≤5y均×折扣(可配) + 稳增负债上限；
                   或稳增股息旁路（股息≥STALWART_MIN + 可持续）
      E slow_div — 慢增/低增速；股息≥4% + 可持续
      F turnaround — 利润衰退 + 债缩或净现金升
    """
    channels: list[str] = []
    strict_debt = _debt_ok(q, config.FUNNEL_MAX_DEBT_RATIO)
    stalwart_debt = _debt_ok(q, config.FUNNEL_STALWART_MAX_DEBT_RATIO)

    # 通道 A：粗略股息修正 PEG（快增/通用）——严格负债；周期股不走 PEG（底部走 C，顶部低 PEG 是陷阱）
    if (
        strict_debt
        and not q.is_cyclical
        and q.quick_peg is not None
        and 0 < q.quick_peg <= config.FUNNEL_MAX_PEG
    ):
        channels.append("peg")

    # 通道 C：周期底部旁路（林奇分相：排除顶部陷阱，要求利润难看+存货未堆）
    if passes_cyclical_funnel(q):
        channels.append("cyclical")

    # 通道 B：隐蔽资产（净现金）——打 asset_play_hint，不改主类
    if strict_debt and q.net_cash_ratio is not None and q.net_cash_ratio >= config.FUNNEL_MIN_NETCASH_RATIO:
        channels.append("net_cash")

    # 通道 D：稳增型错杀旁路（P2-3）
    # 非快速增长主类、非周期、非亏损；PE≤5y均×折扣（或更宽的 PE_VS_AVG_MAX）；稳增负债上限
    is_fast = q.coarse_class == "快速增长型"
    profitable = q.trailing_pe is not None and q.trailing_pe > 0
    pe_cap_mult = config.FUNNEL_STALWART_PE_VS_AVG_MAX  # 漏斗入口可宽于原书 0.85（见 FUNNEL_STALWART_PE_DISCOUNT）
    if (
        stalwart_debt
        and profitable
        and not q.is_cyclical
        and not is_fast
        and q.pe_5y_avg is not None
        and q.pe_5y_avg > 0
        and q.trailing_pe is not None
        and q.trailing_pe <= q.pe_5y_avg * pe_cap_mult
    ):
        channels.append("stalwart")
    # 稳增股息旁路：达不到慢增 4%，但股息尚可 + 可持续（救 KO/PG 类当前估值）
    elif (
        stalwart_debt
        and not q.is_cyclical
        and not is_fast
        and q.dividend_yield is not None
        and q.dividend_yield >= config.FUNNEL_STALWART_MIN_DIV_YIELD
        and _div_sustainable(q)
        and profitable
    ):
        channels.append("stalwart")

    # 通道 E：缓慢增长型股息旁路（P2-4）
    slowish = q.coarse_class == "缓慢增长型" or (
        q.growth_yoy is not None and q.growth_yoy < 0.08
    ) or (q.growth_yoy is None and q.coarse_class in (None, "稳定增长型", "缓慢增长型"))
    if (
        stalwart_debt
        and slowish
        and not q.is_cyclical
        and q.dividend_yield is not None
        and q.dividend_yield >= config.FUNNEL_MIN_DIV_YIELD
        and _div_sustainable(q)
    ):
        channels.append("slow_div")

    # 通道 F：困境反转型趋势初筛（P2-5）
    earning_down = (q.growth_yoy is not None and q.growth_yoy < 0) or q.coarse_class == "困境反转型"
    ltd_shrinking = q.ltd_yoy is not None and q.ltd_yoy <= config.FUNNEL_TURNAROUND_LTD_YOY
    cash_rising = q.net_cash_yoy is not None and q.net_cash_yoy > 0
    if earning_down and (ltd_shrinking or cash_rising):
        channels.append("turnaround")

    if not channels:
        return False, q

    updated = dataclasses.replace(
        q,
        pass_channels=tuple(dict.fromkeys(channels)),  # 保序去重
        asset_play_hint=("net_cash" in channels) or q.asset_play_hint,
        turnaround_hint=("turnaround" in channels) or q.turnaround_hint,
    )
    return True, updated


def first_funnel(
    tickers: list[str],
    provider: BaseDataProvider,
    *,
    workers: int | None = None,
) -> list[QuickScreen]:
    """并发粗筛，返回通过第一层漏斗的 QuickScreen 列表。"""
    workers = workers or config.SCAN_WORKERS
    survivors: list[QuickScreen] = []
    scanned = 0
    total = len(tickers)
    channel_hits: dict[str, int] = {
        "peg": 0,
        "cyclical": 0,
        "net_cash": 0,
        "stalwart": 0,
        "slow_div": 0,
        "turnaround": 0,
    }

    def _screen(t: str) -> QuickScreen | None:
        try:
            return provider.get_quick_screen(t)
        except Exception:  # noqa: BLE001
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_screen, t): t for t in tickers}
        for fut in as_completed(futures):
            scanned += 1
            if scanned % 100 == 0:
                print(f"  …已粗筛 {scanned}/{total}，当前幸存 {len(survivors)}")
            q = fut.result()
            if not q:
                continue
            ok, tagged = evaluate_first_funnel(q)
            if ok:
                survivors.append(tagged)
                for ch in tagged.pass_channels:
                    channel_hits[ch] = channel_hits.get(ch, 0) + 1

    print(
        f"🕳️  第一层漏斗：{total} → {len(survivors)} 只幸存（刷掉 {total - len(survivors)}）"
        f"｜通道 peg={channel_hits.get('peg', 0)} "
        f"cyclical={channel_hits.get('cyclical', 0)} "
        f"net_cash={channel_hits.get('net_cash', 0)} "
        f"stalwart={channel_hits.get('stalwart', 0)} "
        f"slow_div={channel_hits.get('slow_div', 0)} "
        f"turnaround={channel_hits.get('turnaround', 0)}"
    )
    return survivors


def _sort_key(q: QuickScreen):
    if config.AI_SORT_KEY == "net_cash":
        # 每股净现金从高到低（安全垫最厚优先）
        return -(q.net_cash_ratio if q.net_cash_ratio is not None else -1e9)
    # 默认 PEG 从低到高（估值最划算优先），None 排最后
    return q.quick_peg if q.quick_peg is not None else float("inf")


def rank_and_cap(
    survivors: list[QuickScreen],
    max_count: int | None = None,
) -> tuple[list[QuickScreen], list[QuickScreen]]:
    """按配置口径排序，返回 (送 AI 的前 N 只, 降级为仅硬指标的其余)。

    is_priority=True（必看列表）永远进 AI 组且排在最前，不占用/受限于 max_count 之外的名额。
    """
    max_count = config.MAX_AI_ANALYSIS_COUNT if max_count is None else max_count
    priority = [q for q in survivors if q.is_priority]
    rest = sorted((q for q in survivors if not q.is_priority), key=_sort_key)

    ai_group = priority + rest
    if len(ai_group) <= max_count:
        return ai_group, []
    return ai_group[:max_count], ai_group[max_count:]


def is_quality_pick(f: Fundamentals, m: LynchMetrics, fatal: list[str]) -> tuple[bool, str]:
    """判定是否为"值得深挖的优质股"（林奇式买入候选）。返回 (是否推荐, 一句理由)。

    条件：无致命红灯 且 (PEG 在 0~1 之间[估值被增长覆盖] 且 低负债 且 正自由现金流)。
    """
    if fatal:
        return False, ""
    debt = m.by_key("debt")
    fcf = m.by_key("fcf")
    debt_ok = debt is not None and debt.flag == "green"
    fcf_ok = fcf is not None and fcf.flag == "green"
    peg = m.peg
    if peg is not None and 0 < peg <= 1.0 and debt_ok and fcf_ok:
        tier = "极佳(PEG≤0.5)" if peg <= 0.5 else "合理"
        return True, f"PEG {peg:.2f}·{tier}｜低负债｜正现金流"
    return False, ""


def fatal_warnings(f: Fundamentals, m: LynchMetrics) -> list[str]:
    """提取"故事变坏"的致命量化红灯。空列表表示暂无硬伤。

    豁免规则：
    - 金融股（银行/保险）负债天生高，豁免负债红灯。
    - 周期股：短期利润暴跌/长期增长转负往往是周期底部买点，豁免利润下滑红灯
      （但存货暴增对周期股恰是见顶卖点，仍保留）。
    """
    reasons: list[str] = []

    # 1) 存货增速 > 销售增速的 2 倍（增加轻资产与科技/通信股豁免）
    inv_yoy = _yoy(f.inventory_series)
    sales_yoy = _yoy(f.revenue_series)
    if inv_yoy is not None and sales_yoy is not None and inv_yoy > 0:
        # 科技/通信属于轻资产行业（否则谷歌等会被存货规则错杀）
        is_tech = f.sector in ("Technology", "Communication Services")

        # 最新一期存货占总资产比例
        latest_inv = 0.0
        if f.inventory_series:
            latest_inv = f.inventory_series[max(f.inventory_series)]
        inv_ratio = (latest_inv / f.total_assets) if (latest_inv and f.total_assets) else 0.0

        # 科技股 或 存货占总资产极低(<5%) → 强制豁免存货暴增红灯
        is_light_asset_safe = is_tech or (0 < inv_ratio < 0.05)

        if not is_light_asset_safe:
            if inv_yoy > max(sales_yoy, 0) * 2 and inv_yoy - sales_yoy > 0.05:
                reasons.append(f"存货暴增(存货+{inv_yoy:.0%} vs 销售+{sales_yoy:.0%})")

    # 2) 长期负债 / 股东权益 > 1/3（金融股豁免）
    if not m.is_financial and f.long_term_debt is not None and f.stockholders_equity and f.stockholders_equity > 0:
        ratio = f.long_term_debt / f.stockholders_equity
        if ratio > 1 / 3:
            reasons.append(f"负债超标(长期负债/权益={ratio:.0%}>33%)")

    # 3) 增长率暴跌（周期股豁免——底部利润差是买点，非红灯）
    if not m.is_cyclical:
        if f.earnings_growth_yoy is not None and f.earnings_growth_yoy <= -0.30:
            reasons.append(f"盈利同比暴跌{f.earnings_growth_yoy:.0%}")
        elif m.growth_rate is not None and m.growth_rate < 0:
            reasons.append("长期盈利增长转负")

    # 4) 周期股 DIO 熔断：利润高位 + 存货周转天数拉长（隐性库存顶）
    dio_fatal = cyclical_dio_fatal(f, m)
    if dio_fatal:
        reasons.append(dio_fatal)

    return reasons


def check_daily_sniper_trigger(
    f: Fundamentals,
    m: LynchMetrics,
    day_change: float | None,
) -> bool:
    """日间粗筛拦截器：SBI 自选股暴跌 + 低 PEG 击球区（不改变原有漏斗逻辑）。"""
    return is_sniper_candidate(f, m, day_change)


def _yoy(series: dict[int, float]) -> float | None:
    if len(series) < 2:
        return None
    years = sorted(series)
    prev, cur = series[years[-2]], series[years[-1]]
    if prev == 0:
        return None
    return (cur - prev) / abs(prev)
