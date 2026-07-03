"""Anthropic (Claude) 客户端封装。"""

from __future__ import annotations

import os


class LLMError(Exception):
    """LLM 调用失败或未配置时抛出。"""


DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")


def is_configured() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def generate(system_prompt: str, user_content: str, *, model: str | None = None,
             max_tokens: int = 2000) -> str:
    """调用 Claude 生成分析叙述。"""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMError("未设置 ANTHROPIC_API_KEY，无法生成 LLM 叙述（可用 --data-only 仅看硬指标）。")

    try:
        from anthropic import Anthropic
    except ImportError as exc:  # noqa: BLE001
        raise LLMError("未安装 anthropic 库，请先 pip install -r requirements.txt。") from exc

    client = Anthropic(api_key=api_key)
    try:
        resp = client.messages.create(
            model=model or DEFAULT_MODEL,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception as exc:  # noqa: BLE001
        raise LLMError(f"Claude 调用失败：{exc}") from exc

    parts = [block.text for block in resp.content if getattr(block, "type", None) == "text"]
    text = "\n".join(parts).strip()
    if not text:
        raise LLMError("Claude 返回空内容。")
    return text
