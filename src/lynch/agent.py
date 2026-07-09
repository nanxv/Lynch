"""编排器：抓基本面 → 算硬指标 → 组装数据区块 → 调 Gemini 生成林奇式分析。"""

from __future__ import annotations

from dataclasses import dataclass

from . import knowledge, llm
from .cyclical import cyclical_data_block_lines
from .data import Fundamentals, get_provider
from .data.base import BaseDataProvider
from .metrics import LynchMetrics, compute_metrics
from .prompt import SYSTEM_PROMPT
from .watchlist import normalize_user_status


def _system_prompt() -> str:
    """system prompt = 人设 SOP + 林奇心法包（自撰提炼知识库）。"""
    playbook = knowledge.load_playbook()
    if playbook:
        return f"{SYSTEM_PROMPT}\n\n---\n\n# 附：林奇心法包（判断时严格遵循）\n\n{playbook}"
    return SYSTEM_PROMPT

_FLAG_ICON = {"green": "🟢", "yellow": "🟡", "red": "🔴"}


@dataclass(frozen=True)
class LynchAnalysis:
    ticker: str
    fundamentals: Fundamentals
    metrics: LynchMetrics
    data_block: str
    narrative: str | None  # LLM 叙述；data-only 模式下为 None


def _fmt(v: float | None, pct: bool = False, money: bool = False,
         currency: str | None = None) -> str:
    if v is None:
        return "数据缺失"
    if pct:
        return f"{v * 100:.1f}%"
    if money:
        cur = f"{currency} " if currency else ""
        for unit, div in (("万亿", 1e12), ("十亿", 1e9), ("百万", 1e6)):
            if abs(v) >= div:
                return f"{cur}{v / div:.2f} {unit}"
        return f"{cur}{v:,.0f}"
    return f"{v:.2f}"


def _series_str(series: dict[int, float], money: bool = True,
                currency: str | None = None) -> str:
    if not series:
        return "数据缺失"
    years = sorted(series)
    return " → ".join(f"{y}:{_fmt(series[y], money=money, currency=currency)}" for y in years)


def build_data_block(f: Fundamentals, m: LynchMetrics) -> str:
    cur = f.currency
    lines: list[str] = []

    # 舆情安全网：最新头条 + 8-K 置顶
    if f.recent_news_block:
        lines.append(f.recent_news_block)
        lines.append("")
        lines.append("---")
        lines.append("")

    # 政要巨鳄雷达（议员 + 13F）
    if f.whale_alert_block:
        lines.append(f.whale_alert_block)
        lines.append("")
        lines.append("---")
        lines.append("")

    # 模式专属高敏数据置顶（季/月/年会诊的权威口径）
    if f.granularity_block:
        lines.append(f.granularity_block)
        lines.append("")
        lines.append("---")
        lines.append("")

    mode_note = ""
    if f.report_mode == "quarterly":
        mode_note = "（以下年度序列为长期背景；季度会诊请以顶部季度区块为准）"
    elif f.report_mode == "monthly":
        mode_note = "（以下年度基本面为估值锚；月度会诊请以顶部价量数据为准）"
    elif f.report_mode == "annual":
        mode_note = "（以下常规指标辅助；年终审视请以顶部长周期资本配置为准）"

    lines.append(f"【已核实的真实财务数据】{mode_note} (来源: {f.source} | 模式: {f.report_mode})")
    lines.append(f"公司: {f.name or f.ticker} ({f.ticker})")
    lines.append(f"行业: {f.sector or '?'} / {f.industry or '?'}")
    tags = [f"代码初判类型: {m.company_type}"]
    if m.is_financial:
        tags.append("金融股（负债排雷已豁免）")
    if m.is_cyclical:
        tags.append("周期股（高P/E与利润下滑排雷已豁免，反向判定）")
    lines.append(" | ".join(tags))
    lines.append(f"现价(spot): {_fmt(f.spot_price or f.price)} {cur or ''} | 市值: {_fmt(f.market_cap, money=True, currency=cur)}")
    if f.valuation_anchor_price is not None:
        lines.append(
            f"财报锚定价({f.valuation_anchor_date or '?'}): "
            f"{_fmt(f.valuation_anchor_price)} {cur or ''} | "
            f"锚定P/E: {_fmt(f.valuation_pe)} | 即时P/E: {_fmt(f.spot_pe or f.trailing_pe)}"
        )
    lines.append("")
    lines.append("— 估值与增长 —")
    growth_str = "数据缺失" if m.growth_rate is None else f"{m.growth_rate * 100:.1f}%"
    lines.append(f"市盈率 TTM: {_fmt(f.trailing_pe)} | 预期市盈率: {_fmt(f.forward_pe)}")
    lines.append(f"长期增长率(PEG分子用): {growth_str}  (口径: {m.growth_basis})")
    lines.append("PEG 口径: 股息修正版 = P/E ÷ (长期CAGR% + 股息率%)，增速>50%按35%封顶")
    lines.append(f"营收同比: {_fmt(f.revenue_growth_yoy, pct=True)} | 盈利同比: {_fmt(f.earnings_growth_yoy, pct=True)}")
    lines.append("")
    lines.append("— 多年财报序列 —")
    lines.append(f"营业收入: {_series_str(f.revenue_series, currency=cur)}")
    lines.append(f"净利润:   {_series_str(f.net_income_series, currency=cur)}")
    lines.append(f"摊薄EPS:  {_series_str(f.eps_series, money=False)}")
    lines.append(f"存货:     {_series_str(f.inventory_series, currency=cur)}")
    lines.append("")
    lines.append("— 资产负债与现金 —")
    lines.append(f"长期负债: {_fmt(f.long_term_debt, money=True, currency=cur)} | 股东权益: {_fmt(f.stockholders_equity, money=True, currency=cur)}")
    lines.append(f"总现金: {_fmt(f.total_cash, money=True, currency=cur)} | 总负债: {_fmt(f.total_debt, money=True, currency=cur)}")
    lines.append(f"自由现金流: {_fmt(f.free_cashflow, money=True, currency=cur)} | 经营现金流: {_fmt(f.operating_cashflow, money=True, currency=cur)}")
    # yfinance 的 dividendYield 已是百分比数值（1.51 表示 1.51%），不再 ×100
    div_str = f"{f.dividend_yield:.2f}%" if f.dividend_yield else "无/缺失"
    lines.append(f"股息率: {div_str} | 机构持股: {_fmt(f.held_percent_institutions, pct=True)}")
    lines.append("")
    cyc_lines = cyclical_data_block_lines(f, m)
    if cyc_lines:
        lines.extend(cyc_lines)
        lines.append("")
    lines.append("— 林奇量化排雷（已算好，请直接引用）—")
    for metric in m.metrics:
        icon = _FLAG_ICON.get(metric.flag, "⚪")
        val = "N/A" if metric.value is None else metric.value
        lines.append(f"{icon} {metric.label}: {val} → {metric.verdict}")
    return "\n".join(lines)


def analyze_company(
    ticker: str,
    *,
    user_note: str = "",
    data_only: bool = False,
    model: str | None = None,
    provider: BaseDataProvider | None = None,
    user_status: str = "watch",
    story_diff_context: str = "",
    report_mode: str = "weekly",
) -> LynchAnalysis:
    """完整分析一家公司。data_only=True 时跳过 LLM，仅返回硬指标数据区块。

    report_mode: daily/weekly/monthly/quarterly/annual，决定底层数据颗粒度与 Task Prompt。
    user_status: 影子持仓状态 held / watch（来自 watchlist.yaml status 字段）。
    """
    prov = provider or get_provider()
    f = prov.get_fundamentals(ticker, mode=report_mode)
    m = compute_metrics(f)
    data_block = build_data_block(f, m)

    narrative: str | None = None
    if not data_only:
        note = f"\n\n用户补充说明：{user_note}" if user_note.strip() else ""
        status = normalize_user_status(user_status)
        task_content = llm.build_task_prompt(report_mode, status)
        story = f"\n\n{story_diff_context}" if story_diff_context.strip() else ""
        ref = ""
        try:
            query = f"{m.company_type} {f.sector or ''} {f.industry or ''} {f.name or f.ticker} 如何估值与买卖决策"
            block = knowledge.build_reference_block(query, k=3)
            if block:
                ref = f"\n\n{block}"
        except Exception:  # noqa: BLE001
            ref = ""
        user_content = (
            f"{task_content}\n\n"
            f"请按系统设定四步结构分析下面这家公司。\n\n"
            f"{data_block}{note}{story}{ref}\n\n"
            "请严格引用上面的真实数字，并在最末尾单独一行给出唯一的【行动指令】。"
        )
        narrative = llm.generate(_system_prompt(), user_content, model=model)

    return LynchAnalysis(
        ticker=f.ticker,
        fundamentals=f,
        metrics=m,
        data_block=data_block,
        narrative=narrative,
    )
