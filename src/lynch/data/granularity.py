"""报告周期专属数据颗粒度包（与年度常规基本面分离，喂给 LLM 的高敏区块）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DataGranularity:
    """某一 report_mode 下额外拉取并格式化的投研数据。"""

    mode: str
    supplement_block: str  # 注入 LLM 的专属数据区块（Markdown 纯文本）
    raw: dict[str, Any] = field(default_factory=dict)


def empty_granularity(mode: str) -> DataGranularity:
    return DataGranularity(mode=mode, supplement_block="", raw={})


def _pct_str(v: float | None) -> str:
    if v is None:
        return "N/A"
    return f"{v * 100:+.1f}%"


def _money_str(v: float | None, currency: str | None = None) -> str:
    if v is None:
        return "N/A"
    cur = f"{currency} " if currency else ""
    for unit, div in (("万亿", 1e12), ("十亿", 1e9), ("百万", 1e6)):
        if abs(v) >= div:
            return f"{cur}{v / div:.2f}{unit}"
    return f"{cur}{v:,.0f}"


def format_monthly_block(
    *,
    change_20d: float | None,
    rsi_14: float | None,
    peg_now: float | None,
    peg_prior: float | None,
    peg_delta: float | None,
    currency: str | None,
) -> tuple[str, dict[str, Any]]:
    rsi_note = "N/A"
    if rsi_14 is not None:
        if rsi_14 < 30:
            rsi_note = f"{rsi_14:.1f}（超卖区 <30）"
        elif rsi_14 > 70:
            rsi_note = f"{rsi_14:.1f}（超买区 >70）"
        else:
            rsi_note = f"{rsi_14:.1f}（中性）"
    peg_prior_s = f"{peg_prior:.2f}" if peg_prior is not None else "无上月存档"
    peg_now_s = f"{peg_now:.2f}" if peg_now is not None else "N/A"
    peg_delta_s = _pct_str(peg_delta) if peg_delta is not None else "N/A"
    block = (
        "【月度动量与估值漂移 · 高敏数据（无新财报，以价量为主）】\n"
        f"- 近20交易日涨跌幅: {_pct_str(change_20d)}\n"
        f"- RSI(14): {rsi_note}\n"
        f"- 股息修正 PEG（当前）: {peg_now_s}\n"
        f"- 股息修正 PEG（约一月前）: {peg_prior_s}\n"
        f"- PEG 月度变化: {peg_delta_s}\n"
        "\n"
        "⚠️ 月报会诊请优先基于以上价量/估值漂移判断「故事是否变化、回调是否砸出击球区」，"
        "勿用年度财报臆造本季变化。"
    )
    raw = {
        "change_20d": change_20d,
        "rsi_14": rsi_14,
        "peg_now": peg_now,
        "peg_prior": peg_prior,
        "peg_delta": peg_delta,
    }
    return block, raw


def format_quarterly_block(
    *,
    periods: list[str],
    revenue: dict[str, float | None],
    net_income: dict[str, float | None],
    inventory: dict[str, float | None],
    gross_margin: dict[str, float | None],
    qoq: dict[str, float | None],
    yoy_q: dict[str, float | None],
    currency: str | None,
) -> tuple[str, dict[str, Any]]:
    latest = periods[-1] if periods else "?"
    lines = [
        "【真实季度财报 · 高敏数据（季报会诊唯一权威口径）】",
        f"最新报告期: {latest}",
        "",
        "— 单季度绝对值 —",
    ]
    for p in periods[-4:]:
        lines.append(
            f"{p}: 营收 {_money_str(revenue.get(p), currency)} | "
            f"净利润 {_money_str(net_income.get(p), currency)} | "
            f"存货 {_money_str(inventory.get(p), currency)} | "
            f"毛利率 {_pct_str(gross_margin.get(p)) if gross_margin.get(p) is not None else 'N/A'}"
        )
    lines.append("")
    lines.append("— 环比 QoQ（最新季 vs 上季）—")
    for k, label in (
        ("revenue", "营收"),
        ("net_income", "净利润"),
        ("inventory", "存货"),
        ("gross_margin", "毛利率(百分点差)"),
    ):
        v = qoq.get(k)
        if k == "gross_margin" and v is not None:
            lines.append(f"{label}: {v * 100:+.1f}pp")
        else:
            lines.append(f"{label}: {_pct_str(v)}")
    lines.append("")
    lines.append("— 单季同比 YoY（最新季 vs 去年同季）—")
    for k, label in (
        ("revenue", "营收"),
        ("net_income", "净利润"),
        ("inventory", "存货"),
    ):
        lines.append(f"{label}: {_pct_str(yoy_q.get(k))}")
    lines.append("")
    lines.append(
        "⚠️ 财报季会诊必须基于以上季度序列判断：存货积压是否加速、利润率是否遭挤压；"
        "下方年度序列仅作长期背景，不可替代本季数据。"
    )
    block = "\n".join(lines)
    raw = {
        "periods": periods,
        "revenue": revenue,
        "net_income": net_income,
        "inventory": inventory,
        "qoq": qoq,
        "yoy_q": yoy_q,
    }
    return block, raw


def format_annual_block(
    *,
    revenue_series: dict[int, float],
    net_income_series: dict[int, float],
    gross_margin_series: dict[int, float],
    buyback_latest_year: float | None,
    dividend_paid_latest_year: float | None,
    roic_proxy_series: dict[int, float],
    currency: str | None,
    span_years: int,
) -> tuple[str, dict[str, Any]]:
    def _yr_series(d: dict[int, float], pct: bool = False) -> str:
        if not d:
            return "数据缺失"
        years = sorted(d)
        parts = []
        for y in years:
            v = d[y]
            parts.append(f"{y}:{_pct_str(v) if pct else _money_str(v, currency)}")
        return " → ".join(parts)

    block = (
        f"【长期历史视野 · {span_years}年资本配置（年报会诊权威口径）】\n"
        f"- 最近财年股票回购总额: {_money_str(buyback_latest_year, currency)}\n"
        f"- 最近财年股息支付总额: {_money_str(dividend_paid_latest_year, currency)}\n"
        "\n"
        "— 多年营收 —\n"
        f"{_yr_series(revenue_series)}\n"
        "\n"
        "— 多年净利润 —\n"
        f"{_yr_series(net_income_series)}\n"
        "\n"
        "— 多年毛利率趋势 —\n"
        f"{_yr_series(gross_margin_series, pct=True)}\n"
        "\n"
        "— 股东回报代理 ROIC/ROE（净利润÷股东权益，逐年）—\n"
        f"{_yr_series(roic_proxy_series, pct=True)}\n"
        "\n"
        "⚠️ 年终审视请站在 3-5 年尺度判断类型是否退化；"
        "必须输出【清仓剔除名单】（故事变坏或增长迁移的标的）。"
    )
    raw = {
        "buyback_latest_year": buyback_latest_year,
        "dividend_paid_latest_year": dividend_paid_latest_year,
        "gross_margin_series": gross_margin_series,
        "span_years": span_years,
    }
    return block, raw
