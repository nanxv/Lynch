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
SNIPER_DRILL_MAX_TOKENS = 2048  # 日间狙击「两分钟演练」专用上限

# 各报告周期注入 Gemini 的专项上下文（须与数据颗粒度 mode 对齐）
MODE_CONTEXT: dict[str, str] = {
    "weekly": "",
    "monthly": (
        "现在是【月度动量会诊】时点（月末，无新财报）。\n"
        "你必须优先使用「月度动量与估值漂移」与「多维时间轴」区块："
        "约1个月前收盘价、一个月前 P/E 与 PEG、当前即时估值。\n"
        "核心任务：对比历史与当前估值，判断这一个月内估值是扩张还是收缩；"
        "当前月度回调是否砸出了新的击球区。禁止用年度财报臆造本月突变。"
    ),
    "quarterly": (
        "现在是【财报季度会诊】时点（季度末）。\n"
        "你必须优先使用「真实季度财报」与「财报锚定价」区块："
        "QoQ/单季同比、财报发布后3日均收、锚定日 P/E vs 即时 P/E。\n"
        "核心问题：自最新财报发布以来，股价走势是否已透支该份财报的利好？"
        "禁止仅用即时现价评判旧利润（避免高位接盘幻觉）。"
    ),
    "annual": (
        "现在是【年终持仓清理】时点。你必须优先使用「长期历史视野」与 5 年 P/E 水位线。\n"
        "站在 3-5 年宏观视角审视类型是否退化；结合 5 年平均/最低 P/E 判断低估或透支。\n"
        "对故事变坏或增长迁移的标的，必须在文末单独列出【清仓剔除名单】及理由。"
    ),
}


def get_mode_context(mode: str) -> str:
    return MODE_CONTEXT.get(mode, "")


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
