"""周报三层不对称漏斗：L1 机器硬筛 → L2 Flash 节食 → L3 Pro 终审。"""

from __future__ import annotations

from dataclasses import dataclass

from .agent import (
    FlashMicroScore,
    LynchAnalysis,
    analyze_company,
    flash_micro_score,
    select_layer3_tickers,
)
from . import config, llm
from .data.base import QuickScreen
from .fundamentals import FundamentalsError
from .llm import LLMError
from .watchlist import normalize_user_status


@dataclass
class ThreeLayerResult:
    """周报三层产出。"""
    analyses: dict[str, LynchAnalysis]  # 全部 L1 幸存者的硬指标分析（data_only 或 Pro）
    flash_scores: list[FlashMicroScore]
    layer3_tickers: set[str]
    flash_table_rows: list[tuple[str, str, int, str]]  # 未进 L3
    counts: dict[str, int]


def run_layer2_and_select_layer3(
    working: list[QuickScreen],
    watch: dict[str, tuple[str, str, str]],
    provider,
    *,
    report_mode: str = "weekly",
) -> ThreeLayerResult:
    """对 working 集执行 L2 Flash 扫射，并选出 L3 名单。

    - held：跳过 Flash，直接进 L3
    - 非 held：data_only 拉硬指标 → Flash JSON 打分
    """
    analyses: dict[str, LynchAnalysis] = {}
    flash_scores: list[FlashMicroScore] = []
    held_tickers: set[str] = set()
    counts = {"analyzed": 0, "flash": 0, "pro": 0, "data_only": 0, "ai": 0}

    ai_ok = llm.is_configured()

    for q in working:
        name, note, user_status = watch.get(q.ticker, (q.name or q.ticker, "", "watch"))
        is_held = normalize_user_status(user_status) == "held"
        if is_held:
            held_tickers.add(q.ticker.upper())
        try:
            # L1 已过；此处拉全量硬指标（不调长文 LLM）
            a = analyze_company(
                q.ticker,
                user_note=note,
                data_only=True,
                provider=provider,
                user_status=user_status,
                report_mode=report_mode,
            )
            analyses[a.ticker.upper()] = a
            counts["analyzed"] += 1
            counts["data_only"] += 1

            if is_held:
                continue
            if not ai_ok:
                flash_scores.append(FlashMicroScore(
                    ticker=a.ticker,
                    name=a.fundamentals.name or name,
                    company_type=a.metrics.company_type,
                    lynch_score=0,
                    one_liner="未配置GEMINI",
                    parse_ok=False,
                ))
                continue
            score = flash_micro_score(a)
            flash_scores.append(score)
            counts["flash"] += 1
            print(
                f"  ⚡ L2 Flash {score.ticker}: score={score.lynch_score} "
                f"| {score.one_liner}"
                + ("" if score.parse_ok else " (parse_fail)")
            )
        except (FundamentalsError, LLMError) as exc:
            print(f"  ❌ L2 {q.ticker}: {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ L2 {q.ticker} 意外：{exc}")

    layer3_list = select_layer3_tickers(held_tickers, flash_scores)
    layer3 = {t.upper() for t in layer3_list}
    print(
        f"\n🎯 L3 Pro 终审名额 {len(layer3)} 只"
        f"（held={len(held_tickers)} + Flash Top{config.LAYER3_FLASH_TOP_N}）"
        f"：{', '.join(layer3_list)}\n"
    )

    flash_table_rows: list[tuple[str, str, int, str]] = []
    for s in flash_scores:
        if s.ticker.upper() in layer3:
            continue
        flash_table_rows.append(
            (s.ticker, s.company_type or "—", int(s.lynch_score), s.one_liner or "")
        )

    return ThreeLayerResult(
        analyses=analyses,
        flash_scores=flash_scores,
        layer3_tickers=layer3,
        flash_table_rows=flash_table_rows,
        counts=counts,
    )


def run_layer3_pro(
    layer3_tickers: set[str],
    watch: dict[str, tuple[str, str, str]],
    provider,
    *,
    report_mode: str = "weekly",
    prior_analyses: dict[str, LynchAnalysis] | None = None,
) -> dict[str, LynchAnalysis]:
    """对 L3 名单强制 Pro 深度会诊。"""
    out: dict[str, LynchAnalysis] = dict(prior_analyses or {})
    if not llm.is_configured():
        return out

    for ticker in sorted(layer3_tickers):
        name, note, user_status = watch.get(
            ticker, (ticker, "", "held" if ticker in layer3_tickers else "watch")
        )
        # watch keys may be mixed case
        for k, v in watch.items():
            if k.upper() == ticker:
                name, note, user_status = v
                break
        story_ctx = ""
        try:
            from .history import build_story_diff_context, load_previous

            prev = load_previous(ticker)
            if prev:
                story_ctx = build_story_diff_context(prev)
        except Exception:  # noqa: BLE001
            story_ctx = ""
        try:
            a = analyze_company(
                ticker,
                user_note=note,
                data_only=False,
                model=config.GEMINI_PRO_MODEL,
                provider=provider,
                user_status=user_status,
                story_diff_context=story_ctx,
                report_mode=report_mode,
            )
            out[a.ticker.upper()] = a
            print(f"  🎩 L3 Pro {a.ticker} 会诊完成")
        except (FundamentalsError, LLMError) as exc:
            print(f"  ❌ L3 {ticker}: {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ L3 {ticker} 意外：{exc}")
    return out
