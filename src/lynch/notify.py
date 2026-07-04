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


def render_dual_track_verdict_dashboard(
    verdicts: list[tuple],
) -> str:
    """双轨 AI 裁决看板：SBI 免税直通车 + 硬核场外深挖。"""
    if not verdicts:
        return ""

    lines = [
        f"> ## 🧠 智能体最终裁决看板（结论先行 · 双轨分流 · {len(verdicts)}只 AI 深度分析）",
        ">",
        "> **【赛道一：🏦 SBI / NISA 免税直通车专区】**",
        "> *纽交所/纳斯达克主板 · 市值≥3亿美元 · 手机 SBI 可直购*",
        ">",
    ]
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
    lines.extend(_render_verdict_groups(
        verdicts,
        title="🌀 硬核深挖",
        empty_note="本次暂无值得深挖的场外/超微盘 Alpha。",
        filter_fn=lambda v: (not v[6]) and v[0] <= _HARDCORE_MAX_ORDER,
    ))
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
