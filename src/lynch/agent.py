"""编排器：抓基本面 → 算硬指标 → 组装数据区块 → 调 Gemini（Flash 节食 / Pro 终审）。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from . import config, knowledge, llm
from .cyclical import cyclical_data_block_lines
from .data import Fundamentals, get_provider
from .data.base import BaseDataProvider
from .metrics import (
    LynchMetrics,
    alpha_intel_lines,
    annual_rebalance_block_lines,
    compute_metrics,
    held_discipline_prompt_append,
    pe_vs_5y_ratio,
    quarterly_discipline_block_lines,
)
from .prompt import FLASH_MICRO_PROMPT, SYSTEM_PROMPT
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


@dataclass(frozen=True)
class FlashMicroScore:
    """Layer 2 Flash 微评分结果。"""
    ticker: str
    name: str
    company_type: str
    lynch_score: int
    one_liner: str
    raw_response: str = ""
    parse_ok: bool = True


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


def build_daily_data_block(
    f: Fundamentals,
    m: LynchMetrics,
    *,
    day_change: float | None = None,
) -> str:
    """日报·深度异动狙击：缓存底盘 + 即时估值锚（不拉全量财报）。"""
    cur = f.currency
    lines: list[str] = []
    if f.recent_news_block:
        lines.append(f.recent_news_block)
        lines.append("")
        lines.append("---")
        lines.append("")
    lines.append("【⚡ 日报·深度异动狙击数据盘】")
    chg_s = f"{day_change * 100:+.1f}%" if day_change is not None else "数据缺失"
    lines.append(f"当日涨跌幅: {chg_s} | 现价: {_fmt(f.spot_price or f.price)} {cur or ''}")
    spot_pe = f.spot_pe or f.trailing_pe
    lines.append(f"即时 P/E(TTM): {_fmt(spot_pe)} | 股息修正 PEG: {_fmt(m.peg)}")
    if f.pe_5y_avg is not None and spot_pe is not None:
        ratio = spot_pe / f.pe_5y_avg if f.pe_5y_avg > 0 else None
        lines.append(
            f"估值锚：5年历史均值 P/E {_fmt(f.pe_5y_avg)}"
            + (f" | 当前为均值的 {_fmt(ratio)} 倍" if ratio else "")
        )
    debt = m.by_key("debt")
    if debt:
        lines.append(f"护城河·负债: {debt.verdict}")
    fcf = m.by_key("fcf")
    if fcf:
        lines.append(f"护城河·现金流: {fcf.verdict}")
    if f.dio_yoy is not None:
        lines.append(f"周期底盘·DIO 近两期变化: {f.dio_yoy * 100:+.1f}%")
    elif f.dio_series:
        yrs = sorted(f.dio_series)
        lines.append(f"周期底盘·DIO: {f.dio_series[yrs[-1]]:.0f} 天（{yrs[-1]}）")
    lines.append(f"林奇分类(缓存): {m.company_type}")
    lines.append("")
    lines.append("— 林奇量化快照 —")
    for metric in m.metrics:
        icon = _FLAG_ICON.get(metric.flag, "⚪")
        val = "N/A" if metric.value is None else metric.value
        lines.append(f"{icon} {metric.label}: {val} → {metric.verdict}")
    return "\n".join(lines)


def build_data_block(f: Fundamentals, m: LynchMetrics, *, day_change: float | None = None) -> str:
    if f.report_mode == "daily":
        return build_daily_data_block(f, m, day_change=day_change)
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

    alpha_lines = alpha_intel_lines(f, m)
    if alpha_lines and f.report_mode == "weekly":
        lines.append("【林奇筹码面 Alpha 探针】")
        lines.extend(alpha_lines)
        lines.append("")
        lines.append("---")
        lines.append("")

    q_disc = quarterly_discipline_block_lines(f, m)
    if q_disc and f.report_mode == "quarterly":
        lines.extend(q_disc)
        lines.append("")
        lines.append("---")
        lines.append("")

    a_disc = annual_rebalance_block_lines(f, m)
    if a_disc and f.report_mode == "annual":
        lines.extend(a_disc)
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
    if m.growth_cap_warn:
        tags.append("growth_cap_warn（历史增速≥25%，紧箍咒）")
    if m.institutional_neglect and f.report_mode == "weekly":
        tags.append("institutional_neglect（机构冷落<40%）")
    if m.insider_net_buying and f.report_mode == "weekly":
        tags.append("insider_net_buying（内部人净买入）")
    if m.ultimate_alpha and f.report_mode == "weekly":
        tags.append("ultimate_alpha（终极 Alpha 双响炮）")
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
    qy = f.quarterly_earnings_yoy or f.quarterly_revenue_yoy
    if qy:
        basis = "净利润" if f.quarterly_earnings_yoy else "营收"
        seq = " → ".join(f"{y * 100:.1f}%" for y in qy[-4:])
        lines.append(f"季度{basis}同比(YoY)近四季: {seq}")
    if m.company_type == "稳定增长型":
        ratio = pe_vs_5y_ratio(f)
        if ratio is not None and f.pe_5y_avg is not None:
            pe = f.spot_pe or f.trailing_pe
            lines.append(
                f"稳增估值锚：当前 P/E 为 5 年历史均值的 {ratio:.2f} 倍"
                f"（当前 {pe:.1f} / 5年均 {f.pe_5y_avg:.1f}）"
            )
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


def build_micro_data_block(f: Fundamentals, m: LynchMetrics) -> str:
    """Layer 2 Token 节食：极短量化快照（供 Flash JSON 打分）。"""
    peg = m.peg
    pe = f.trailing_pe
    debt = m.by_key("debt")
    fcf = m.by_key("fcf")
    inv = m.by_key("inventory")
    lines = [
        f"ticker={f.ticker}",
        f"name={f.name or f.ticker}",
        f"type={m.company_type}",
        f"PEG={peg if peg is not None else 'NA'}",
        f"PE={pe if pe is not None else 'NA'}",
        f"debt={debt.verdict if debt else 'NA'}",
        f"fcf={fcf.verdict if fcf else 'NA'}",
        f"inventory={inv.verdict if inv else 'NA'}",
        f"div_yield={f.dividend_yield if f.dividend_yield is not None else 'NA'}",
        f"earn_yoy={f.earnings_growth_yoy if f.earnings_growth_yoy is not None else 'NA'}",
        f"rev_yoy={f.revenue_growth_yoy if f.revenue_growth_yoy is not None else 'NA'}",
        f"tags="
        + ",".join(
            t for t, on in (
                ("growth_cap_warn", m.growth_cap_warn),
                ("institutional_neglect", m.institutional_neglect),
                ("insider_net_buying", m.insider_net_buying),
                ("ultimate_alpha", m.ultimate_alpha),
                ("cyclical", m.is_cyclical),
                ("financial", m.is_financial),
            ) if on
        ),
    ]
    for line in alpha_intel_lines(f, m):
        lines.append(line.replace("\n", " ")[:120])
    return "\n".join(lines)


def _clean_flash_json_text(text: str) -> str:
    """榨出 Flash 模型回复中的裸 JSON（剥离 Markdown 围栏与首尾噪音）。"""
    response_text = (text or "").strip()
    response_text = (
        response_text.removeprefix("```json")
        .removeprefix("```")
        .removesuffix("```")
        .strip()
    )
    if "```" in response_text:
        response_text = re.sub(r"```(?:json)?\s*", "", response_text)
        response_text = response_text.replace("```", "").strip()
    return response_text


def parse_flash_micro_json(text: str, *, ticker: str, name: str, company_type: str) -> FlashMicroScore:
    """健壮解析 Flash 微评分 JSON；失败时降级为 score=0，不抛异常。"""
    raw = (text or "").strip()
    try:
        cleaned = _clean_flash_json_text(raw)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if not match:
                raise ValueError("no json object")
            data = json.loads(match.group(0))
        score = int(data.get("lynch_score", 0))
        score = max(0, min(100, score))
        one = str(data.get("one_liner") or "").strip().replace("\n", " ")
        if len(one) > 30:
            one = one[:30]
        tk = str(data.get("ticker") or ticker).strip().upper() or ticker
        return FlashMicroScore(
            ticker=tk,
            name=name,
            company_type=company_type,
            lynch_score=score,
            one_liner=one or "无短评",
            raw_response=raw,
            parse_ok=True,
        )
    except Exception:  # noqa: BLE001
        return FlashMicroScore(
            ticker=ticker,
            name=name,
            company_type=company_type,
            lynch_score=0,
            one_liner="JSON解析失败",
            raw_response=raw[:500],
            parse_ok=False,
        )


def _rate_limit_sleep(model: str) -> None:
    """免费档 RPM 防御：Flash 强制间隔 4.5s（≈15RPM），Pro 强制间隔 32s（≈2RPM）。"""
    import time

    from .llm import _last_call_mono, _throttle_lock, interval_for_model

    resolved = (model or "").strip() or config.GEMINI_FLASH_MODEL
    gap = interval_for_model(resolved)
    if gap <= 0:
        return
    with _throttle_lock:
        now = time.monotonic()
        last = _last_call_mono.get(resolved, 0.0)
        wait = gap - (now - last)
        if wait > 0:
            time.sleep(wait)
        _last_call_mono[resolved] = time.monotonic()


def flash_micro_score(analysis: LynchAnalysis) -> FlashMicroScore:
    """Layer 2：强制 Flash 微 Prompt，只产出 JSON 评分。"""
    f, m = analysis.fundamentals, analysis.metrics
    name = f.name or f.ticker
    ctype = m.company_type or ""
    if not llm.is_configured():
        return FlashMicroScore(
            ticker=f.ticker, name=name, company_type=ctype,
            lynch_score=0, one_liner="未配置GEMINI", parse_ok=False,
        )
    model = config.GEMINI_FLASH_MODEL
    micro = build_micro_data_block(f, m)
    user_content = f"请为下列标的打分并只输出 JSON：\n\n{micro}"
    try:
        _rate_limit_sleep(model)  # Flash 4.5s
        text = llm.generate(
            FLASH_MICRO_PROMPT,
            user_content,
            model=model,
            max_tokens=llm.FLASH_MICRO_MAX_TOKENS,
            skip_throttle=True,
        )
        return parse_flash_micro_json(text, ticker=f.ticker, name=name, company_type=ctype)
    except Exception as exc:  # noqa: BLE001
        hint = str(exc).replace("\n", " ")
        if "NOT_FOUND" in hint or "not found" in hint.lower():
            short = "模型不可用/已下线"
        elif "429" in hint or "ResourceExhausted" in hint:
            short = "配额耗尽429"
        else:
            short = type(exc).__name__
        return FlashMicroScore(
            ticker=f.ticker,
            name=name,
            company_type=ctype,
            lynch_score=0,
            one_liner=f"Flash失败:{short}"[:30],
            raw_response=str(exc)[:300],
            parse_ok=False,
        )


def compute_layer3_flash_top_n(held_count: int) -> int:
    """海选进阶名额 = max(0, 总预算 - held数 - 保留名额)。held 另算，永不占用此名额。"""
    dynamic = max(
        0,
        config.LAYER3_PRO_TOTAL_BUDGET - held_count - config.LAYER3_PRO_RESERVED,
    )
    cap = config.LAYER3_FLASH_TOP_N
    if cap > 0:
        return min(cap, dynamic)
    return dynamic


def select_layer3_tickers(
    held_tickers: set[str],
    flash_scores: list[FlashMicroScore],
    *,
    top_n: int | None = None,
    held_count: int | None = None,
) -> list[str]:
    """Layer 3 名额：全部 held + Flash 评分 Top N（去重保序，held 永不因配额为 0 被截断）。"""
    hc = held_count if held_count is not None else len(held_tickers)
    n = compute_layer3_flash_top_n(hc) if top_n is None else max(0, top_n)
    ranked = sorted(flash_scores, key=lambda s: (-s.lynch_score, s.ticker))
    out: list[str] = []
    seen: set[str] = set()
    for t in held_tickers:
        tu = config.correct_ticker(t).upper()
        if tu not in seen:
            seen.add(tu)
            out.append(tu)
    for s in ranked[:n]:
        tu = config.correct_ticker(s.ticker).upper()
        if tu not in seen:
            seen.add(tu)
            out.append(tu)
    return out


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
    day_change: float | None = None,
) -> LynchAnalysis:
    """完整分析一家公司。data_only=True 时跳过 LLM，仅返回硬指标数据区块。

    report_mode: daily/weekly/monthly/quarterly/annual，决定底层数据颗粒度与 Task Prompt。
    user_status: 影子持仓状态 held / watch（来自 watchlist.yaml status 字段）。
    深度会诊默认强制 Pro（Layer 3 / 日报 / 季年报）；可显式传入 model 覆盖。
    """
    prov = provider or get_provider()
    f = prov.get_fundamentals(ticker, mode=report_mode)
    m = compute_metrics(f)
    data_block = build_data_block(f, m, day_change=day_change)

    narrative: str | None = None
    if not data_only:
        note = f"\n\n用户补充说明：{user_note}" if user_note.strip() else ""
        status = normalize_user_status(user_status)
        task_content = llm.build_task_prompt(report_mode, status)
        story = f"\n\n{story_diff_context}" if story_diff_context.strip() else ""
        discipline = held_discipline_prompt_append(
            f, m, user_status=status, report_mode=report_mode,
        )
        discipline_block = f"\n\n{discipline}" if discipline else ""
        ref = ""
        try:
            query = f"{m.company_type} {f.sector or ''} {f.industry or ''} {f.name or f.ticker} 如何估值与买卖决策"
            block = knowledge.build_reference_block(query, k=3)
            if block:
                ref = f"\n\n{block}"
        except Exception:  # noqa: BLE001
            ref = ""
        user_content = (
            f"{task_content}{discipline_block}\n\n"
            f"请按系统设定四步结构分析下面这家公司。\n\n"
            f"{data_block}{note}{story}{ref}\n\n"
            "请严格引用上面的真实数字，并在最末尾单独一行给出唯一的【行动指令】。"
        )
        # Layer 3 / 深度会诊：强制 Pro + 32s RPM 防御
        resolved = (model or config.GEMINI_PRO_MODEL).strip() or config.GEMINI_PRO_MODEL
        _rate_limit_sleep(resolved)  # Pro 32s
        narrative = llm.generate(
            _system_prompt(), user_content, model=resolved, skip_throttle=True,
        )

    return LynchAnalysis(
        ticker=f.ticker,
        fundamentals=f,
        metrics=m,
        data_block=data_block,
        narrative=narrative,
    )
