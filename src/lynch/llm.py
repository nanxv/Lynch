"""Google Gemini 客户端封装（google-genai SDK）。"""

from __future__ import annotations

import os


class LLMError(Exception):
    """LLM 调用失败或未配置时抛出。"""


# 默认用 gemini-2.5-flash（快且省，追求深度推理可设 GEMINI_MODEL=gemini-2.5-pro）。
# 注意：GitHub Actions 未配置的 secret 会注入为"空字符串"而非缺失，故用 `or` 兜底，
# 避免空字符串导致 SDK 报 "model is required"。
_FALLBACK_MODEL = "gemini-2.5-flash"
DEFAULT_MODEL = (os.getenv("GEMINI_MODEL") or _FALLBACK_MODEL).strip()


def is_configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def generate(system_prompt: str, user_content: str, *, model: str | None = None,
             max_tokens: int = 8192) -> str:
    """调用 Gemini 生成林奇式分析叙述。"""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise LLMError("未设置 GEMINI_API_KEY，无法生成 LLM 叙述（可用 --data-only 仅看硬指标）。")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # noqa: BLE001
        raise LLMError("未安装 google-genai 库，请先 pip install -r requirements.txt。") from exc

    # 再兜底一次：确保绝不把空字符串/None 传给 SDK（否则报 "model is required"）。
    resolved_model = (model or DEFAULT_MODEL or _FALLBACK_MODEL).strip() or _FALLBACK_MODEL

    client = genai.Client(api_key=api_key)
    try:
        resp = client.models.generate_content(
            model=resolved_model,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=max_tokens,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        raise LLMError(f"Gemini 调用失败：{exc}") from exc

    text = (resp.text or "").strip()
    if not text:
        raise LLMError("Gemini 返回空内容（可能触发安全过滤或超出 token 上限）。")
    return text
