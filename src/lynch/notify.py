"""邮件通知：把 Markdown 简报通过 SMTP 发送为邮件（纯文本 + HTML 双格式）。

凭证全部来自系统环境变量（便于 GitHub Secrets 配置）：
- SMTP_SERVER    SMTP 服务器，如 smtp.gmail.com
- SMTP_PORT      端口，587 (STARTTLS) 或 465 (SSL)
- SMTP_USERNAME  发件邮箱地址
- SMTP_PASSWORD  发件邮箱的授权码/应用专用密码
- RECEIVER_EMAIL 收件邮箱（多个用逗号分隔）

未配置齐全时优雅降级：打印提示并返回 False，绝不让主程序崩溃。
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

_REQUIRED = ("SMTP_SERVER", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "RECEIVER_EMAIL")
_TIMEOUT = 30


def is_configured() -> bool:
    return all(os.environ.get(k) for k in _REQUIRED)


def _missing() -> list[str]:
    return [k for k in _REQUIRED if not os.environ.get(k)]


# 让「结论先行」的置顶摘要板块（优质股/排雷/🧠AI裁决看板/周期）以醒目卡片呈现。
_EMAIL_STYLE = (
    "<style>"
    "body{font-family:-apple-system,Segoe UI,Helvetica,Arial,"
    "'PingFang SC','Microsoft YaHei',sans-serif;line-height:1.6;color:#222;"
    "max-width:760px;margin:0 auto;padding:12px}"
    "h1{font-size:22px}h2{font-size:18px;margin:14px 0 6px}"
    "blockquote{margin:12px 0;padding:10px 14px;background:#f6f8fa;"
    "border-left:5px solid #4a90d9;border-radius:6px}"
    "blockquote h2{margin-top:2px}"
    "blockquote ul{margin:6px 0;padding-left:20px}"
    "hr{border:none;border-top:1px solid #e1e4e8;margin:16px 0}"
    "code{background:#eef1f4;padding:1px 5px;border-radius:4px;font-size:90%}"
    "pre{background:#f6f8fa;padding:10px;border-radius:6px;overflow:auto}"
    "</style>"
)


def _markdown_to_html(md: str) -> str:
    """把 Markdown 转成 HTML；无 markdown 库时退回 <pre> 包裹（保证可读）。"""
    try:
        import markdown  # type: ignore

        body = markdown.markdown(md, extensions=["fenced_code", "tables", "nl2br"])
    except Exception:  # noqa: BLE001
        import html

        body = f"<pre style='white-space:pre-wrap'>{html.escape(md)}</pre>"
    return f"<html><head>{_EMAIL_STYLE}</head><body>{body}</body></html>"


# ── 双轨报告渲染（SBI 免税直通车 vs 硬核场外深挖）────────────────
# VerdictRow = (优先级, ticker, name, 标签, 配色, 理由, sbi_tradable, peg, fcf_yield)
from .signals import SIGNAL_BUY, format_lynch_metrics, lynch_buy_sort_key

_VERDICT_SIGNAL_SPECS = [
    (0, "🟢 强烈买入 (BUY NOW)", "#1e8449"),
    (1, "🟡 放入观察仓 (WATCHLIST)", "#b9770e"),
    (2, "⚪ 钝感持有 (HOLD)", "#566573"),
    (3, "🔴 坚决卖出/避开 (SELL/AVOID)", "#c0392b"),
]
_VERDICT_UNKNOWN_ORDER = 8
_VERDICT_UNKNOWN_LABEL = "⚪ 待定（AI 未给出明确指令）"
_VERDICT_UNKNOWN_COLOR = "#566573"
_HARDCORE_MAX_ORDER = 2  # 硬核区只展示 买入/观察/持有


def format_ticker_with_category(
    ticker: str,
    name: str,
    company_type: str | None,
) -> str:
    """机械榜单行首：`META Meta Platforms, Inc. [快速增长型]`"""
    tag = f" [{company_type}]" if company_type else ""
    return f"{ticker} {name}{tag}"


def append_cyclical_detail_tail(
    text: str,
    *,
    dio_tail: str = "",
    industry_pe_anchor: str = "",
) -> str:
    """周期股简报行：附加 DIO 趋势尾巴与行业 P/E 锚点。"""
    extras: list[str] = []
    if dio_tail:
        extras.append(dio_tail)
    if industry_pe_anchor:
        extras.append(f"({industry_pe_anchor})")
    if not extras:
        return text
    return f"{text} {' '.join(extras)}"


def cyclical_briefing_extras(f) -> tuple[str, str]:
    """从 Fundamentals 提取周期股简报附加字段 (dio_tail, industry_pe_anchor)。"""
    from .cyclical import format_dio_trend_tail, format_industry_pe_anchor

    return format_dio_trend_tail(f), format_industry_pe_anchor(f)


def _render_verdict_groups(
    verdicts: list[tuple],
    *,
    title: str,
    empty_note: str,
    filter_fn,
) -> list[str]:
    """按信号优先级分组渲染；🟢 强烈买入组内按林奇 PEG/FCF 排行。"""
    filtered = [v for v in verdicts if filter_fn(v)]
    if not filtered:
        return [f"> {empty_note}", ">"]
    groups: dict[int, list[tuple[str, str, str, str, str, float | None, float | None]]] = {}
    for order, ticker, name, label, color, reason, _sbi, peg, fcf_y in filtered:
        groups.setdefault(order, []).append((ticker, name, label, color, reason, peg, fcf_y))
    lines = [f"> ### {title}（{len(filtered)}只）", ">"]
    ordered = [(o, lab, col) for o, lab, col in _VERDICT_SIGNAL_SPECS]
    ordered.append((_VERDICT_UNKNOWN_ORDER, _VERDICT_UNKNOWN_LABEL, _VERDICT_UNKNOWN_COLOR))
    for order, label, color in ordered:
        members = groups.get(order)
        if not members:
            continue
        if order == SIGNAL_BUY:
            members = sorted(
                members,
                key=lambda m: lynch_buy_sort_key(m[5], m[6], m[0]),
            )
        lines.append(f'> **<span style="color:{color}">{label} · {len(members)}只</span>**')
        for ticker, name, _label, _color, reason, peg, fcf_y in members:
            tail = f"：{reason}" if reason else ""
            if order == SIGNAL_BUY:
                metrics_tag = format_lynch_metrics(peg, fcf_y)
                lines.append(
                    f'> - {metrics_tag} <b style="color:{color}">{ticker} - {name}</b>{tail}'
                )
            else:
                lines.append(f'> - <b style="color:{color}">{ticker}｜{name}</b>{tail}')
        lines.append(">")
    return lines


def render_red_flag_block(reds: list[tuple]) -> str:
    """置顶「🔴 致命红灯排雷」。reds: (ticker, name, reasons[, company_type])"""
    if not reds:
        return (
            "> **🟢 全场无致命红灯** —— 本次扫描的标的暂未触发存货暴增/负债超标/增长暴跌。\n\n"
            "---\n\n"
        )
    lines = [f"> ## 🔴🔴 致命红灯排雷（{len(reds)}只 · 置顶必看）", ">"]
    for row in reds:
        ticker, name, reasons = row[0], row[1], row[2]
        company_type = row[3] if len(row) > 3 else None
        label = format_ticker_with_category(ticker, name, company_type)
        lines.append(
            f'> - <b style="color:#c0392b">🔴 {label}</b>：**{"；".join(reasons)}**'
        )
    lines.append(">")
    lines.append("> *即使是你原本看好的股票，一旦基本面故事变坏，也会第一时间出现在这里。*")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def render_recommend_block(recs: list[tuple]) -> str:
    """已废弃：请用 render_multi_bucket_briefing。保留供旧测试兼容。"""
    buckets: dict[str, list[tuple]] = {}
    for row in recs:
        buckets.setdefault("fast_grower", []).append(row)
    return render_multi_bucket_briefing(buckets)


_BUCKET_SPECS: dict[str, tuple[str, str, str, str]] = {
    "fast_grower": ("🚀", "快速增长区", "PEG极佳·低负债·正现金流", "#1e8449"),
    "asset_play": ("💎", "隐蔽资产区", "净现金厚垫", "#117a65"),
    "turnaround": ("🩹", "困境反转区", "债务骤降/现金回流", "#884ea0"),
    "dividend_retirement": ("🐢", "股息养老区", "高股息或估值错杀", "#2874a6"),
}


def render_multi_bucket_briefing(buckets: dict[str, list[tuple]]) -> str:
    """P2-6 多桶分类简报：非 held 幸存者按林奇分类分轨展示；空桶自动隐藏。

    每桶行格式：(ticker, name, sort_key, reason[, company_type[, ultimate_alpha]])
    """
    from .funnel import BUCKET_ORDER

    parts: list[str] = []
    total = 0
    for bid in BUCKET_ORDER:
        rows = buckets.get(bid, [])
        if not rows:
            continue
        total += len(rows)
        icon, title, subtitle, color = _BUCKET_SPECS[bid]
        lines = [
            f"> ## {icon} {title}（{len(rows)}只 · {subtitle}）",
            ">",
        ]
        for row in rows:
            ticker, name, _sort, reason = row[0], row[1], row[2], row[3]
            company_type = row[4] if len(row) > 4 else None
            ultimate = row[5] if len(row) > 5 else False
            label = format_ticker_with_category(ticker, name, company_type)
            alpha_badge = " **[🌟 终极 Alpha 标的]**" if ultimate else ""
            lines.append(
                f'> - <b style="color:{color}">{icon} {label}</b>{alpha_badge}：{reason}'
            )
        lines.append(">")
        lines.append("")
        lines.append("---")
        lines.append("")
        parts.append("\n".join(lines))

    if not parts:
        return (
            "> ## 📂 多赛道雷达\n>\n"
            "> 本次扫描暂无符合分桶条件的非持仓幸存者。宁可空仓，不追贵股。\n\n"
            "---\n\n"
        )
    header = (
        f"> ## 📂 多赛道雷达（共 {total} 只 · 分类分桶）\n"
        ">\n"
        "> *废除单一「优质股」大框；按林奇六类逻辑分轨展示候选。*\n"
        ">\n"
        "---\n\n"
    )
    return header + "".join(parts)


def render_recommend_block_legacy(recs: list[tuple]) -> str:
    """置顶「🟢 推荐深挖的优质股」。recs: (ticker, name, peg, reason[, company_type])"""
    if not recs:
        return (
            "> ## 🟢 推荐深挖的优质股\n>\n"
            "> 本次扫描暂无同时满足「PEG≤1 + 低负债 + 正现金流」的标的。宁可空仓，不追贵股。\n\n"
            "---\n\n"
        )
    lines = [f"> ## 🟢🟢 推荐深挖的优质股（{len(recs)}只 · 估值划算优先）", ">"]
    for row in recs:
        ticker, name, _peg, reason = row[0], row[1], row[2], row[3]
        company_type = row[4] if len(row) > 4 else None
        label = format_ticker_with_category(ticker, name, company_type)
        lines.append(f'> - <b style="color:#1e8449">🟢 {label}</b>：{reason}')
    lines.append(">")
    lines.append("> *这些是「故事好+数字便宜」的候选；买入前请做 2 分钟演练，用大白话讲清买入理由。*")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def render_cyclical_top_block(tops: list[tuple[str, str, str]]) -> str:
    """「⚠️ 周期型公司 - 顶部陷阱」板块。"""
    if not tops:
        return ""
    lines = [f"> ## ⚠️ 周期型公司 · 顶部陷阱（{len(tops)}只）", ">"]
    for ticker, name, reason in tops:
        lines.append(f"> - <b style=\"color:#c0392b\">⚠️ {ticker}｜{name}</b>：{reason}")
    lines.append(">")
    lines.append("> *林奇铁律：周期股在财报最漂亮、P/E 最低时往往是顶部；存货堆积时更要远离。*")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def render_cyclical_block(cycs: list[tuple[str, str, str]]) -> str:
    """「🌀 周期型公司 - 行业低谷观察期」板块。"""
    if not cycs:
        return ""
    lines = [f"> ## 🌀 周期型公司 · 行业低谷观察期（{len(cycs)}只）", ">"]
    for ticker, name, reason in cycs:
        lines.append(f"> - <b style=\"color:#b9770e\">🌀 {ticker}｜{name}</b>：{reason}")
    lines.append(">")
    lines.append("> *周期股反向操作：利润最差、P/E最高时往往是底部；别在利润最漂亮时追。*")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def render_held_consultation_block(
    items: list[tuple[str, str, str, list[str], str]],
    detail_sections: list[str] | None = None,
    *,
    include_details: bool = True,
) -> str:
    """🛡️ 核心持仓独立会诊：置顶展示 held 标的，脱离普通优质/排雷榜单。

    items: (ticker, name, company_type, warnings, signal_label)
    detail_sections: 与 items 同序的完整会诊 Markdown（可选）
    include_details: False 时只渲染摘要卡（周报用：摘要后立刻接裁决看板）
    """
    if not items:
        return ""
    lines = [
        f"> ## 🛡️ 核心持仓独立会诊（{len(items)}只）",
        ">",
        "> *影子持仓享受最高优先级 AI 会诊；故事变坏时必须果断卖出。*",
        ">",
    ]
    for i, (ticker, name, ctype, warnings, signal) in enumerate(items):
        tag = f" [{ctype}]" if ctype else ""
        sig = f" ｜ {signal}" if signal else ""
        lines.append(f"> ### 🛡️ {ticker}｜{name}{tag}{sig}")
        if warnings:
            for w in warnings:
                lines.append(f"> - **{w}**")
        else:
            lines.append("> - 暂无持仓铁律红灯")
        lines.append(">")
    lines.append("")
    lines.append("---")
    lines.append("")
    body = ""
    if include_details and detail_sections:
        body = "\n".join(detail_sections) + "\n\n---\n\n"
    return "\n".join(lines) + body


def render_mode_banner(mode: str) -> str:
    """四维时间架构：各模式置顶说明条。"""
    banners = {
        "daily": (
            "> ## ⚡ 深度异动狙击手\n"
            ">\n"
            "> *仅 watchlist 异动标的｜触发：涨跌幅≥阈值 / 突发8-K / 内部人净买入｜"
            "无异动则静默不发报。*\n"
            ">\n---\n\n"
        ),
        "weekly": (
            "> ## 🌍 全境三层漏斗雷达\n"
            ">\n"
            "> *置顶：持仓会诊 → 智能体裁决看板｜其后多桶/排雷｜"
            "L1→L2 Flash→L3 Pro｜文末 Flash 海选短评。*\n"
            ">\n---\n\n"
        ),
        "quarterly": (
            "> ## ⚖️ 持仓生死拷问（季报）\n"
            ">\n"
            "> *仅限 held 影子持仓｜零宽容卖出审判｜非 BUY/HOLD 即 SELL。*\n"
            ">\n---\n\n"
        ),
        "annual": (
            "> ## 🧭 林奇逻辑重估（年报）\n"
            ">\n"
            "> *仅限 held 影子持仓｜分类退化评估 + 年终清仓审视。*\n"
            ">\n---\n\n"
        ),
    }
    return banners.get(mode, "")


def render_daily_sniper_summary(
    triggered: list[tuple[str, str, list[str], str]],
) -> str:
    """日报异动摘要。triggered: (ticker, name, reasons, signal_label)"""
    if not triggered:
        return ""
    lines = [
        f"> ## ⚡ 今日异动触发（{len(triggered)}只）",
        ">",
    ]
    for ticker, name, reasons, signal in triggered:
        sig = f" ｜ {signal}" if signal else ""
        lines.append(f"> - **{ticker}｜{name}**{sig}：{'；'.join(reasons)}")
    lines.append(">")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def render_flash_shortlist_table(
    rows: list[tuple[str, str, int, str]],
) -> str:
    """第二梯队：🔍 全境海选短评榜单（Layer 2 未进 Layer 3）。

    rows: (ticker, company_type, lynch_score, one_liner)
    JSON/API 失败行不进主表，只在表尾汇总，避免周报尾部被「JSON解析失败」淹没。
    """
    if not rows:
        return ""

    def _is_flash_fail(one: str) -> bool:
        s = (one or "").strip()
        return s.startswith("JSON解析失败") or s.startswith("Flash失败:") or s.startswith("未配置GEMINI")

    ok_rows = [r for r in rows if not _is_flash_fail(r[3])]
    fail_n = len(rows) - len(ok_rows)
    ranked = sorted(ok_rows, key=lambda r: (-int(r[2]), r[0]))
    lines = [
        "",
        "---",
        "",
        f"## 🔍 全境海选短评榜单（Flash Layer 2 · {len(ranked)}只有效评分未进 Pro 终审）",
        "",
        "*Token 节食扫射结果：仅 JSON 评分 + 一句话，无长文会诊。*",
        "",
    ]
    if fail_n:
        lines.append(f"*另有 {fail_n} 只 Flash 调用/解析失败，已从本表剔除（不影响 held 进 L3）。*")
        lines.append("")
    if not ranked:
        lines.append("> 本次无有效 Flash 短评（全部解析失败或均已进入 Pro）。")
        lines.append("")
        return "\n".join(lines)
    lines.extend([
        "| 代码 | 林奇分类 | 林奇评分 (Score) | 一句话简评 (One Liner) |",
        "| --- | --- | ---: | --- |",
    ])
    for ticker, ctype, score, one in ranked:
        safe_one = (one or "").replace("|", "/").replace("\n", " ")
        safe_type = (ctype or "—").replace("|", "/")
        lines.append(f"| {ticker} | {safe_type} | {int(score)} | {safe_one} |")
    lines.append("")
    return "\n".join(lines)


def render_held_interrogation_summary(
    mode: str,
    held_items: list[tuple[str, str, str, list[str], str]],
) -> str:
    """季报/年报 held 专属摘要（无多桶雷达）。"""
    if not held_items:
        return ""
    title = "⚖️ 持仓生死拷问" if mode == "quarterly" else "🧭 年终逻辑重估"
    lines = [f"> ## {title}（{len(held_items)}只 held）", ">"]
    for ticker, name, ctype, warnings, signal in held_items:
        tag = f" [{ctype}]" if ctype else ""
        sig = f" ｜ {signal}" if signal else ""
        lines.append(f"> ### {ticker}｜{name}{tag}{sig}")
        for w in warnings or []:
            lines.append(f"> - **{w}**")
        if not warnings:
            lines.append("> - 暂无量化红灯")
        lines.append(">")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def render_briefing_summary(
    *,
    mode: str = "weekly",
    buckets: dict[str, list[tuple]] | None = None,
    recs: list[tuple] | None = None,
    reds: list[tuple],
    cycs: list[tuple[str, str, str]],
    cyc_tops: list[tuple[str, str, str]] | None = None,
    verdicts: list[tuple],
    ai_count: int,
    ai_mode: bool,
    daily_triggered: list[tuple[str, str, list[str], str]] | None = None,
    held_items: list[tuple[str, str, str, list[str], str]] | None = None,
    include_banner: bool = True,
) -> str:
    """简报置顶区：按时间维度分离渲染逻辑。"""
    parts: list[str] = []
    if include_banner:
        parts.append(render_mode_banner(mode))

    if mode == "daily":
        parts.append(render_daily_sniper_summary(daily_triggered or []))
        return "".join(parts)

    if mode in ("quarterly", "annual"):
        parts.append(render_held_interrogation_summary(mode, held_items or []))
        if ai_count > 0 and held_items:
            parts.append(render_dual_track_verdict_dashboard(
                verdicts, ai_count=ai_count, show_when_empty=True,
            ))
        return "".join(parts)

    # weekly：裁决看板紧随持仓（由调用方先拼 held），再多桶/排雷/周期
    if mode == "weekly":
        parts.append(render_dual_track_verdict_dashboard(
            verdicts,
            ai_count=ai_count,
            show_when_empty=ai_mode,
        ))
        if buckets is not None:
            parts.append(render_multi_bucket_briefing(buckets))
        elif recs:
            parts.append(render_recommend_block(recs))
        else:
            parts.append(render_multi_bucket_briefing({}))
        parts.append(render_red_flag_block(reds))
        parts.append(render_cyclical_top_block(cyc_tops or []))
        parts.append(render_cyclical_block(cycs))
        return "".join(parts)

    if ai_count == 0 and mode == "monthly":
        parts.append(render_red_flag_block(reds))
    parts.append(render_dual_track_verdict_dashboard(
        verdicts,
        ai_count=ai_count,
        show_when_empty=ai_mode and ai_count > 0,
    ))
    parts.append(render_cyclical_top_block(cyc_tops or []))
    parts.append(render_cyclical_block(cycs))
    return "".join(parts)


def render_dual_track_verdict_dashboard(
    verdicts: list[tuple],
    *,
    ai_count: int | None = None,
    show_when_empty: bool = False,
) -> str:
    """双轨 AI 裁决看板：SBI 免税直通车 + 硬核场外深挖。

    ai_count: 实际送入 Gemini 的数量（用于标题「N只 AI 深度分析」）。
    show_when_empty: 周报/季报/年报模式下即使 verdicts 为空也保留看板框架。
    """
    n_ai = ai_count if ai_count is not None else len(verdicts)
    if not verdicts and not show_when_empty:
        return ""

    lines = [
        f"> ## 🧠 智能体最终裁决看板（结论先行 · 双轨分流 · {n_ai}只 AI 深度分析）",
        ">",
        "> **【赛道一：🏦 SBI / NISA 免税直通车专区】**",
        "> *纽交所/纳斯达克主板 · 市值≥3亿美元 · 手机 SBI 可直购*",
        ">",
    ]
    if not verdicts:
        lines.append("> 本次 AI 分析暂未解析出【行动指令】，请查看下方详情区完整叙述。")
        lines.append(">")
    else:
        lines.extend(_render_verdict_groups(
            verdicts,
            title="🏦 免税直通车",
            empty_note="本次暂无 SBI 可直购标的的 AI 裁决。",
            filter_fn=lambda v: v[6],
        ))
    lines.append(">")
    lines.append("> **【赛道二：🌀 硬核 / 非主板深挖区（盈透/盛宝限定）】**")
    lines.append("> *OTC/超微盘 · SBI 买不到 · 仅展示 AI 判定有价值的生僻股*")
    lines.append(">")
    if verdicts:
        lines.extend(_render_verdict_groups(
            verdicts,
            title="🌀 硬核深挖",
            empty_note="本次暂无值得深挖的场外/超微盘 Alpha。",
            filter_fn=lambda v: (not v[6]) and v[0] <= _HARDCORE_MAX_ORDER,
        ))
    else:
        lines.append("> 本次暂无值得深挖的场外/超微盘 Alpha。")
        lines.append(">")
    lines.append("> *结论先行：赛道一随时可下单；赛道二仅供专业券商账户参考。*")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def render_dual_track_detail_sections(
    main_sections: list[str],
    hardcore_sections: list[str],
    *,
    ai_mode: bool,
    flat_sections: list[str] | None = None,
) -> str:
    """详情区双轨：第一章主板免税 · 第二章硬核场外。"""
    if not ai_mode and not hardcore_sections:
        return "\n".join(flat_sections or main_sections)
    parts: list[str] = []
    parts.append("# 第一章：🏦 主板免税成分股会诊详情")
    parts.append("")
    parts.append("*SBI / NISA 账户可直购 · 方便手机随时下单*")
    parts.append("")
    if main_sections:
        parts.extend(main_sections)
    else:
        parts.append("> 本次暂无 SBI 可直购标的的深度分析。")
        parts.append("")
    parts.append("---")
    parts.append("")
    parts.append("# 第二章：🌀 硬核场外与超微盘股深度会诊详情")
    parts.append("")
    parts.append("*盈透 / 盛宝等专业券商限定 · OTC 与超微盘 Alpha*")
    parts.append("")
    if hardcore_sections:
        parts.extend(hardcore_sections)
    else:
        parts.append("> 本次暂无硬核场外标的的深度分析。")
        parts.append("")
    return "\n".join(parts)


def send_email(subject: str, markdown_body: str) -> bool:
    """发送一封邮件。返回 True 表示已发送；未配置或失败返回 False。"""
    if not is_configured():
        print(f"ℹ️  未配置 SMTP（缺少 {', '.join(_missing())}），跳过邮件发送。")
        return False

    server = os.environ["SMTP_SERVER"]
    username = os.environ["SMTP_USERNAME"]
    password = os.environ["SMTP_PASSWORD"]
    receivers = [addr.strip() for addr in os.environ["RECEIVER_EMAIL"].split(",") if addr.strip()]
    try:
        port = int(os.environ["SMTP_PORT"])
    except ValueError:
        print("⚠️  SMTP_PORT 不是有效数字，跳过邮件发送。")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("彼得·林奇分析 Agent", username))
    msg["To"] = ", ".join(receivers)
    msg.set_content(markdown_body)
    msg.add_alternative(_markdown_to_html(markdown_body), subtype="html")

    try:
        if port == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(server, port, timeout=_TIMEOUT, context=context) as smtp:
                smtp.login(username, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(server, port, timeout=_TIMEOUT) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                smtp.login(username, password)
                smtp.send_message(msg)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  邮件发送失败：{exc}")
        return False

    print(f"✅ 邮件已发送 → {', '.join(receivers)}")
    return True


def send_sniper_alert(
    *,
    ticker: str,
    name: str,
    day_change_pct: str,
    peg: float | None,
    price: str,
    narrative: str,
) -> bool:
    """发送日间狙击加急邮件（独立于常规日报/周报）。"""
    subject = f"【🚨林奇狙击警报】{ticker} 现价已杀入绝佳特价期！"
    peg_str = f"{peg:.2f}" if peg is not None else "N/A"
    body = (
        f"# 🚨 林奇狙击警报 · {ticker} — {name}\n\n"
        f"**触发条件**：SBI 可交易 ｜ 单日跌幅 {day_change_pct} ｜ 股息修正 PEG {peg_str}\n\n"
        f"**现价**：{price}\n\n"
        f"---\n\n{narrative}\n"
    )
    return send_email(subject, body)


def send_realtime_sniper_alert(
    *,
    ticker: str,
    name: str,
    change_pct: str,
    peg: float | None,
    price: str,
    pe_5y_min: float | None,
    narrative: str,
) -> bool:
    """盘中深夜特快邮件。"""
    short = name.split()[0] if name else ticker
    subject = (
        f"【🚨林奇深夜特快】{ticker} - {short} 盘中突发暴跌 [{change_pct}]！"
        f"速去 SBI 账户护航！"
    )
    peg_str = f"{peg:.2f}" if peg is not None else "N/A"
    pe5y_str = f"{pe_5y_min:.1f}" if pe_5y_min is not None else "N/A"
    body = (
        f"# 🚨 林奇深夜特快 · {ticker} — {name}\n\n"
        f"**盘中触发**：相对昨收 {change_pct} ｜ 即时 PEG {peg_str} ｜ "
        f"5年最低P/E {pe5y_str}\n\n"
        f"**即时现价**：{price}\n\n"
        f"---\n\n{narrative}\n"
    )
    return send_email(subject, body)
