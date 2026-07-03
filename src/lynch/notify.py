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


def _markdown_to_html(md: str) -> str:
    """把 Markdown 转成 HTML；无 markdown 库时退回 <pre> 包裹（保证可读）。"""
    try:
        import markdown  # type: ignore

        body = markdown.markdown(md, extensions=["fenced_code", "tables", "nl2br"])
    except Exception:  # noqa: BLE001
        import html

        body = f"<pre style='white-space:pre-wrap'>{html.escape(md)}</pre>"
    return (
        "<html><body style=\"font-family:-apple-system,Segoe UI,Helvetica,Arial,"
        "'PingFang SC','Microsoft YaHei',sans-serif;line-height:1.6;color:#222;"
        'max-width:760px;margin:0 auto;padding:12px">'
        f"{body}</body></html>"
    )


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
