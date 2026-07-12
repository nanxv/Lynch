"""Google Gemini 客户端封装（google-genai SDK）+ 免费档 RPM 节流。"""

from __future__ import annotations

import os
import threading
import time

from . import config
from .prompt import TASK_PROMPTS
from .watchlist import normalize_user_status


class LLMError(Exception):
    """LLM 调用失败或未配置时抛出。"""


# 官方稳定版（三层漏斗硬编码默认；可用环境变量覆盖）
FLASH_MODEL = (os.getenv("GEMINI_FLASH_MODEL") or config.GEMINI_FLASH_MODEL).strip()
PRO_MODEL = (os.getenv("GEMINI_PRO_MODEL") or config.GEMINI_PRO_MODEL).strip()
_FALLBACK_MODEL = FLASH_MODEL or "gemini-2.5-flash"
# 兼容旧 GEMINI_MODEL：未设则默认 Flash（节食）
DEFAULT_MODEL = (os.getenv("GEMINI_MODEL") or _FALLBACK_MODEL).strip() or _FALLBACK_MODEL
SNIPER_DRILL_MAX_TOKENS = 2048
# Flash 微评分：强制 JSON + thinking_budget=0 后不再需要给思考预留大额度
FLASH_MICRO_MAX_TOKENS = 512

# Layer 2 Flash 结构化输出 schema（与 FLASH_MICRO_PROMPT 字段对齐）
FLASH_MICRO_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "ticker": {"type": "string"},
        "lynch_score": {"type": "integer"},
        "one_liner": {"type": "string"},
    },
    "required": ["ticker", "lynch_score", "one_liner"],
}

_last_call_mono: dict[str, float] = {}
_throttle_lock = threading.Lock()


def build_task_prompt(mode: str, user_status: str = "watch") -> str:
    """按报告周期 + 影子持仓状态组装动态 Task Prompt。"""
    task_key = mode if mode in TASK_PROMPTS else "weekly"
    status = normalize_user_status(user_status)
    return TASK_PROMPTS[task_key].format(user_status=status)


def get_mode_context(mode: str, user_status: str = "watch") -> str:
    """兼容旧调用方：返回动态 Task Prompt 文本。"""
    return build_task_prompt(mode, user_status)


def is_configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def is_pro_model(model: str) -> bool:
    return "pro" in (model or "").lower()


def interval_for_model(model: str) -> float:
    if is_pro_model(model):
        return float(config.GEMINI_PRO_INTERVAL_SEC)
    return float(config.GEMINI_FLASH_INTERVAL_SEC)


def throttle_for_model(model: str) -> None:
    """免费档 RPM 防御：Flash 间隔 4.5s（≈15RPM），Pro 间隔 32s（≈2RPM）。"""
    resolved = (model or DEFAULT_MODEL or _FALLBACK_MODEL).strip() or _FALLBACK_MODEL
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


def _extract_response_text(resp: object) -> str:
    """从 SDK 响应尽量抠出可见文本（兼容 thinking 占满导致 .text 为空）。"""
    try:
        text = (getattr(resp, "text", None) or "").strip()
        if text:
            return text
    except Exception:  # noqa: BLE001
        pass
    chunks: list[str] = []
    for cand in getattr(resp, "candidates", None) or []:
        content = getattr(cand, "content", None)
        for part in getattr(content, "parts", None) or []:
            t = getattr(part, "text", None)
            if t:
                chunks.append(str(t))
    return "\n".join(chunks).strip()


def generate(
    system_prompt: str,
    user_content: str,
    *,
    model: str | None = None,
    max_tokens: int = 8192,
    skip_throttle: bool = False,
    response_mime_type: str | None = None,
    response_json_schema: dict | None = None,
    thinking_budget: int | None = None,
) -> str:
    """调用 Gemini 生成内容（默认按模型节流；agent 可先 throttle 再 skip）。

    Flash 微评分应传 response_mime_type='application/json' + thinking_budget=0，
    避免 2.5 思考 token 吃光 max_output_tokens 导致空响应 / JSON 脱轨。
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise LLMError("未设置 GEMINI_API_KEY，无法生成 LLM 叙述（可用 --data-only 仅看硬指标）。")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # noqa: BLE001
        raise LLMError("未安装 google-genai 库，请先 pip install -r requirements.txt。") from exc

    resolved_model = (model or DEFAULT_MODEL or _FALLBACK_MODEL).strip() or _FALLBACK_MODEL
    if not skip_throttle:
        throttle_for_model(resolved_model)

    cfg_kwargs: dict = {
        "system_instruction": system_prompt,
        "max_output_tokens": max_tokens,
    }
    if response_mime_type:
        cfg_kwargs["response_mime_type"] = response_mime_type
    if response_json_schema is not None:
        # google-genai 新旧字段名兼容
        cfg_kwargs["response_json_schema"] = response_json_schema
    if thinking_budget is not None:
        try:
            cfg_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=int(thinking_budget),
            )
        except Exception:  # noqa: BLE001
            # 旧 SDK / 不支持 thinking 的模型：忽略，靠 JSON mime 兜底
            pass

    client = genai.Client(api_key=api_key)
    try:
        resp = client.models.generate_content(
            model=resolved_model,
            contents=user_content,
            config=types.GenerateContentConfig(**cfg_kwargs),
        )
    except TypeError:
        # 部分 SDK 版本不认 response_json_schema / thinking_config：降级重试
        soft = {
            "system_instruction": system_prompt,
            "max_output_tokens": max_tokens,
        }
        if response_mime_type:
            soft["response_mime_type"] = response_mime_type
        try:
            resp = client.models.generate_content(
                model=resolved_model,
                contents=user_content,
                config=types.GenerateContentConfig(**soft),
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"Gemini 调用失败：{exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise LLMError(f"Gemini 调用失败：{exc}") from exc

    text = _extract_response_text(resp)
    if not text:
        finish = ""
        try:
            cands = getattr(resp, "candidates", None) or []
            if cands:
                finish = str(getattr(cands[0], "finish_reason", "") or "")
        except Exception:  # noqa: BLE001
            finish = ""
        hint = f"（finish={finish}）" if finish else ""
        raise LLMError(f"Gemini 返回空内容{hint}（可能思考占满输出或触发安全过滤）。")
    return text
