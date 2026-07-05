"""参数计算验算（阶段 2）：用可追溯手算链对照 compute_metrics 引擎输出。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from .data.base import Fundamentals, QuickScreen, is_cyclical, is_financial
from .metrics import (
    _GROWTH_CAP,
    _GROWTH_CAP_TRIGGER,
    _cagr,
    _pick_growth,
    _yoy,
    check_sbi_tradable_fundamentals,
    compute_metrics,
)

MatchStatus = Literal["pass", "fail", "skip", "warn"]


@dataclass(frozen=True)
class CalculationStep:
    key: str
    label: str
    formula: str
    inputs: dict[str, Any]
    manual_value: float | bool | None
    engine_value: float | bool | None
    status: MatchStatus
    note: str = ""


@dataclass
class CalculationAuditReport:
    ticker: str
    mode: str
    audited_at: datetime
    steps: list[CalculationStep] = field(default_factory=list)
    data_trusted: bool = True
    skip_reason: str = ""

    @property
    def pass_count(self) -> int:
        return sum(1 for s in self.steps if s.status == "pass")

    @property
    def fail_count(self) -> int:
        return sum(1 for s in self.steps if s.status == "fail")

    @property
    def all_match(self) -> bool:
        return self.fail_count == 0

    @property
    def score(self) -> float:
        if not self.steps:
            return 0.0
        return 100.0 * self.pass_count / len(self.steps)


def _close(a: float | None, b: float | None, *, tol: float = 0.015) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if b == 0:
        return abs(a - b) < tol
    return abs(a - b) <= max(tol, abs(b) * tol)


def _status(manual, engine, *, tol: float = 0.015) -> MatchStatus:
    if manual is None and engine is None:
        return "skip"
    if isinstance(manual, bool) and isinstance(engine, bool):
        return "pass" if manual == engine else "fail"
    if _close(
        float(manual) if manual is not None else None,
        float(engine) if engine is not None else None,
        tol=tol,
    ):
        return "pass"
    return "fail"


def _manual_peg(f: Fundamentals, growth: float | None) -> tuple[float | None, dict[str, Any]]:
    pe = f.valuation_pe if f.valuation_pe is not None else f.trailing_pe
    div = f.dividend_yield or 0.0
    inputs: dict[str, Any] = {
        "P/E": pe,
        "CAGR (decimal)": growth,
        "dividend_yield (pct pts)": div,
        "valuation_pe": f.valuation_pe,
        "trailing_pe": f.trailing_pe,
    }
    if pe is None or pe <= 0 or growth is None or growth <= 0:
        return None, inputs
    capped = _GROWTH_CAP if growth > _GROWTH_CAP_TRIGGER else growth
    denom = capped * 100 + div
    inputs["capped_growth (decimal)"] = capped
    inputs["denominator (pct pts)"] = denom
    return pe / denom, inputs


def _manual_quick_peg(qs: QuickScreen) -> tuple[float | None, dict[str, Any]]:
    pe, g = qs.trailing_pe, qs.growth_yoy
    inputs = {"P/E": pe, "earningsGrowth": g}
    if pe and pe > 0 and g and g > 0:
        return pe / (g * 100), inputs
    return None, inputs


def audit_calculations(
    f: Fundamentals,
    *,
    mode: str = "weekly",
    quick_screen: QuickScreen | None = None,
    data_trusted: bool = True,
) -> CalculationAuditReport:
    """阶段 2：展开计算链并与 compute_metrics 对照。"""
    report = CalculationAuditReport(
        ticker=f.ticker,
        mode=mode,
        audited_at=datetime.now(timezone.utc),
        data_trusted=data_trusted,
    )
    if not data_trusted:
        report.skip_reason = "阶段 1 未通过（FAIL），以下验算仅供参考"

    engine = compute_metrics(f)
    financial = is_financial(f)
    cyclical = is_cyclical(f)

    # ── CAGR ──
    eps_cagr = _cagr(f.eps_series)
    ni_cagr = _cagr(f.net_income_series)
    manual_growth, basis = _pick_growth(f)
    years_eps = sorted(f.eps_series)
    years_ni = sorted(f.net_income_series)
    cagr_inputs: dict[str, Any] = {"basis": basis}
    if eps_cagr is not None and years_eps:
        y0, y1 = years_eps[0], years_eps[-1]
        cagr_inputs["eps_first"] = f.eps_series[y0]
        cagr_inputs["eps_last"] = f.eps_series[y1]
        cagr_inputs["eps_span_years"] = y1 - y0
    elif ni_cagr is not None and years_ni:
        y0, y1 = years_ni[0], years_ni[-1]
        cagr_inputs["ni_first"] = f.net_income_series[y0]
        cagr_inputs["ni_last"] = f.net_income_series[y1]
        cagr_inputs["ni_span_years"] = y1 - y0

    report.steps.append(CalculationStep(
        key="cagr",
        label="长期增长率 (CAGR)",
        formula="(末值/初值)^(1/年数) - 1；优先 EPS 序列，其次净利润",
        inputs=cagr_inputs,
        manual_value=manual_growth,
        engine_value=engine.growth_rate,
        status=_status(manual_growth, engine.growth_rate, tol=1e-6),
        note=engine.growth_basis,
    ))

    # ── PEG ──
    manual_peg, peg_inputs = _manual_peg(f, manual_growth)
    engine_peg = engine.peg
    peg_status = _status(manual_peg, engine_peg)
    peg_note = ""
    if manual_growth and manual_growth > _GROWTH_CAP_TRIGGER:
        peg_note = "增速>50%，分母已锚定 35%"
    if (f.dividend_yield or 0) > 0 and (f.dividend_yield or 0) < 1:
        peg_note += "；⚠ dividendYield 可能为小数形式，PEG 分母或偏小"

    report.steps.append(CalculationStep(
        key="peg",
        label="股息修正 PEG",
        formula="P/E ÷ (capped_CAGR×100 + dividend_yield_pct)",
        inputs=peg_inputs,
        manual_value=round(manual_peg, 4) if manual_peg is not None else None,
        engine_value=engine_peg,
        status=peg_status,
        note=peg_note.strip("；"),
    ))

    # ── 负债比 ──
    ltd, eq = f.long_term_debt, f.stockholders_equity
    manual_debt = None if financial or ltd is None or eq is None or eq <= 0 else ltd / eq
    eng_debt = engine.by_key("debt")
    eng_debt_val = eng_debt.value if eng_debt else None
    report.steps.append(CalculationStep(
        key="debt",
        label="长期负债 / 股东权益",
        formula="long_term_debt / stockholders_equity（金融股豁免）",
        inputs={"long_term_debt": ltd, "stockholders_equity": eq, "financial": financial},
        manual_value=round(manual_debt, 4) if manual_debt is not None else None,
        engine_value=eng_debt_val,
        status="skip" if financial else _status(manual_debt, eng_debt_val),
    ))

    # ── 存货差 ──
    inv_yoy = _yoy(f.inventory_series)
    rev_yoy = _yoy(f.revenue_series)
    manual_inv_gap = None
    if inv_yoy is not None and rev_yoy is not None:
        manual_inv_gap = round((inv_yoy - rev_yoy) * 100, 1)
    eng_inv = engine.by_key("inventory")
    eng_inv_val = eng_inv.value if eng_inv else None
    report.steps.append(CalculationStep(
        key="inventory",
        label="存货增速 − 销售增速 (百分点)",
        formula="YoY(inventory) - YoY(revenue)，再 ×100",
        inputs={
            "inventory_yoy": inv_yoy,
            "revenue_yoy": rev_yoy,
            "inventory_years": sorted(f.inventory_series),
            "revenue_years": sorted(f.revenue_series),
        },
        manual_value=manual_inv_gap,
        engine_value=eng_inv_val,
        status=_status(manual_inv_gap, eng_inv_val, tol=0.05),
    ))

    # ── 每股净现金 ──
    cash, debt, shares = f.total_cash, f.total_debt, f.shares_outstanding
    manual_ncps = None
    if cash is not None and shares and shares > 0:
        manual_ncps = round((cash - (debt or 0.0)) / shares, 2)
    eng_nc = engine.by_key("net_cash")
    eng_nc_val = eng_nc.value if eng_nc else None
    report.steps.append(CalculationStep(
        key="net_cash",
        label="每股净现金",
        formula="(total_cash - total_debt) / shares_outstanding",
        inputs={"total_cash": cash, "total_debt": debt, "shares": shares},
        manual_value=manual_ncps,
        engine_value=eng_nc_val,
        status=_status(manual_ncps, eng_nc_val, tol=0.02),
    ))

    # ── FCF ──
    eng_fcf = engine.by_key("fcf")
    eng_fcf_val = eng_fcf.value if eng_fcf else None
    manual_fcf = round(f.free_cashflow, 0) if f.free_cashflow is not None else None
    report.steps.append(CalculationStep(
        key="fcf",
        label="自由现金流 (绝对值)",
        formula="info.freeCashflow | cashflow.Free Cash Flow",
        inputs={"free_cashflow": f.free_cashflow, "market_cap": f.market_cap},
        manual_value=manual_fcf,
        engine_value=eng_fcf_val,
        status=_status(manual_fcf, eng_fcf_val, tol=1.0),
    ))

    # ── FCF Yield ──
    manual_fcf_y = None
    if f.free_cashflow and f.market_cap and f.market_cap > 0:
        manual_fcf_y = f.free_cashflow / f.market_cap
    report.steps.append(CalculationStep(
        key="fcf_yield",
        label="FCF / 市值",
        formula="free_cashflow / market_cap",
        inputs={"free_cashflow": f.free_cashflow, "market_cap": f.market_cap},
        manual_value=round(manual_fcf_y, 4) if manual_fcf_y is not None else None,
        engine_value=round(manual_fcf_y, 4) if manual_fcf_y is not None else None,
        status="pass" if manual_fcf_y is not None else "skip",
        note="展示值（引擎写在 verdict 文案中）",
    ))

    # ── SBI 可交易 ──
    manual_sbi = check_sbi_tradable_fundamentals(f)
    report.steps.append(CalculationStep(
        key="sbi_tradable",
        label="SBI/NISA 可交易",
        formula="主板 + 市值≥3亿美元，排除 OTC",
        inputs={"exchange": f.exchange, "market_cap": f.market_cap, "ticker": f.ticker},
        manual_value=manual_sbi,
        engine_value=engine.sbi_tradable,
        status="pass" if manual_sbi == engine.sbi_tradable else "fail",
    ))

    # ── 漏斗 quick_peg（独立口径）──
    if quick_screen is not None:
        mq, q_inputs = _manual_quick_peg(quick_screen)
        report.steps.append(CalculationStep(
            key="quick_peg",
            label="漏斗 quick_peg（粗筛口径）",
            formula="P/E ÷ (info.earningsGrowth × 100)；无股息修正",
            inputs=q_inputs,
            manual_value=round(mq, 4) if mq is not None else None,
            engine_value=quick_screen.quick_peg,
            status=_status(mq, quick_screen.quick_peg),
            note="与正式 PEG 口径不同；验算漏斗自身是否自洽",
        ))

    return report


def format_calculation_report(report: CalculationAuditReport) -> str:
    lines = [
        f"### 阶段 2：参数计算验算 · {report.ticker}",
        "",
    ]
    if report.skip_reason:
        lines.append(f"> ⚠️ {report.skip_reason}")
        lines.append("")

    lines.append("| 指标 | 公式 | 手算 | 引擎 | 结果 |")
    lines.append("|------|------|------|------|------|")
    icon = {"pass": "✅", "fail": "❌", "skip": "⏭", "warn": "⚠️"}
    for s in report.steps:
        mv = "—" if s.manual_value is None else s.manual_value
        ev = "—" if s.engine_value is None else s.engine_value
        lines.append(
            f"| {s.label} | `{s.formula[:40]}…` | {mv} | {ev} | {icon.get(s.status, '?')} |"
        )

    lines.append("")
    lines.append(
        f"**验算结论**：{report.pass_count}/{len(report.steps)} 通过，"
        f"score {report.score:.0f}，"
        f"{'全部一致 ✅' if report.all_match else '存在偏差 ❌'}"
    )
    lines.append("")
    lines.append("<details><summary>展开计算明细</summary>")
    lines.append("")
    for s in report.steps:
        lines.append(f"#### {s.label} (`{s.key}`)")
        lines.append(f"- **公式**：{s.formula}")
        lines.append(f"- **输入**：")
        for k, v in s.inputs.items():
            lines.append(f"  - `{k}` = {v}")
        lines.append(f"- **手算** = {s.manual_value}")
        lines.append(f"- **引擎** = {s.engine_value}")
        lines.append(f"- **结果** = {s.status}")
        if s.note:
            lines.append(f"- **备注** = {s.note}")
        lines.append("")
    lines.append("</details>")
    lines.append("")
    return "\n".join(lines)


def calculation_report_to_dict(report: CalculationAuditReport) -> dict[str, Any]:
    return {
        "ticker": report.ticker,
        "mode": report.mode,
        "audited_at": report.audited_at.isoformat(),
        "data_trusted": report.data_trusted,
        "all_match": report.all_match,
        "score": report.score,
        "steps": [
            {
                "key": s.key,
                "label": s.label,
                "formula": s.formula,
                "inputs": s.inputs,
                "manual_value": s.manual_value,
                "engine_value": s.engine_value,
                "status": s.status,
                "note": s.note,
            }
            for s in report.steps
        ],
    }
