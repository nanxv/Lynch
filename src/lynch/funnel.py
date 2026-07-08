"""双层漏斗核心逻辑。

第一层（纯代码硬指标粗筛）：从上万只成分股里用无延迟的本地计算刷掉绝大多数垃圾股。
第二层（AI 成本熔断）：对幸存者排序并只把前 N 只送给 Gemini，其余降级为仅硬指标。
另含 fatal_warnings：提取"故事变坏"的致命红灯，用于置顶排雷摘要。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import dataclasses

from . import config
from .data.base import BaseDataProvider, Fundamentals, QuickScreen
from .metrics import LynchMetrics, check_sbi_tradable, check_sbi_tradable_fundamentals
from .sniper import is_sniper_candidate


def is_hardcore_alpha_candidate(sbi_tradable: bool, signal_order: int | None) -> bool:
    """硬核深挖区候选：SBI 买不到，但 AI 给出买入/观察/持有信号。"""
    if sbi_tradable or signal_order is None:
        return False
    return signal_order <= 2  # 强烈买入 / 观察仓 / 钝感持有


def _passes_first_funnel(q: QuickScreen) -> bool:
    """第一层多通道漏斗（Phase 1）：负债门 + (PEG | 周期底部 | 净现金) OR。

    返回前若放行，会把命中通道写回 q.pass_channels（通过 replace 由调用方接收新对象）。
    本函数只返回 bool；通道标签由 evaluate_first_funnel 附带。
    """
    return bool(evaluate_first_funnel(q)[0])


def evaluate_first_funnel(q: QuickScreen) -> tuple[bool, QuickScreen]:
    """判定是否通过第一层；返回 (通过?, 可能打标后的 QuickScreen)。"""
    # ── 负债门（金融无条件豁免）──
    if not q.is_financial:
        if q.debt_ratio is not None and q.debt_ratio > config.FUNNEL_MAX_DEBT_RATIO:
            return False, q

    channels: list[str] = []

    # 通道 A：粗略股息修正 PEG（快增/通用）
    if q.quick_peg is not None and 0 < q.quick_peg <= config.FUNNEL_MAX_PEG:
        channels.append("peg")

    # 通道 C：周期底部旁路 — 无 PEG/亏损，且存货未堆积（或无存货数据）
    if q.is_cyclical and (q.quick_peg is None or q.trailing_pe is None or q.trailing_pe <= 0):
        inv, sales = q.inventory_growth, q.sales_growth
        inventory_ok = inv is None or sales is None or inv <= sales
        if inventory_ok:
            channels.append("cyclical")

    # 通道 B：隐蔽资产（净现金）
    if q.net_cash_ratio is not None and q.net_cash_ratio >= config.FUNNEL_MIN_NETCASH_RATIO:
        channels.append("net_cash")

    if not channels:
        return False, q

    updated = dataclasses.replace(
        q,
        pass_channels=tuple(channels),
        asset_play_hint=("net_cash" in channels) or q.asset_play_hint,
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
    channel_hits: dict[str, int] = {"peg": 0, "cyclical": 0, "net_cash": 0}

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
        f"net_cash={channel_hits.get('net_cash', 0)}"
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


def cyclical_watch(f: Fundamentals, m: LynchMetrics) -> str | None:
    """判定周期股是否处于「行业低谷观察期」。返回一句观察理由，否则 None。

    对周期股而言，亏损/高 P/E/长期利润下滑/短期利润暴跌都被豁免了常规排雷，
    但它们不该凭空消失——反而正是需要盯着行业库存拐点的潜在底部买点，单列展示。
    """
    if not m.is_cyclical:
        return None
    signals: list[str] = []
    if f.trailing_pe is None:
        signals.append("当前亏损/无有效P/E")
    elif f.trailing_pe > 30:
        signals.append(f"P/E高达{f.trailing_pe:.0f}")
    if m.growth_rate is not None and m.growth_rate < 0:
        signals.append("长期利润下滑")
    if f.earnings_growth_yoy is not None and f.earnings_growth_yoy <= -0.30:
        signals.append(f"短期利润暴跌{f.earnings_growth_yoy:.0%}")
    if not signals:
        return None
    return "；".join(signals) + " → 疑似周期底部，盯行业渠道/库存拐点，勿当红灯"


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
