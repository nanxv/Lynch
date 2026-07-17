"""Google Gemini 客户端封装（google-genai SDK）+ 免费档 RPM 节流。"""

from __future__ import annotations

import os
import threading
import time
from typing import Literal

from . import config
from .prompt import TASK_PROMPTS
from .watchlist import normalize_user_status

ApiTier = Literal["flash", "pro"]


class LLMError(Exception):
    """LLM 调用失败或未配置时抛出。"""


# 官方稳定版（三层漏斗硬编码默认；可用环境变量覆盖）
FLASH_MODEL = (os.getenv("GEMINI_FLASH_MODEL") or config.GEMINI_FLASH_MODEL).strip()
PRO_MODEL = (os.getenv("GEMINI_PRO_MODEL") or config.GEMINI_PRO_MODEL).strip()
_FALLBACK_MODEL = FLASH_MODEL or "gemini-2.5-flash"
# 兼容旧 GEMINI_MODEL：未设则默认 Flash（节食）
DEFAULT_MODEL = (os.getenv("GEMINI_MODEL") or _FALLBACK_MODEL).strip() or _FALLBACK_MODEL
SNIPER_DRILL_MAX_TOKENS = 2048
# Layer 2 Flash：原生 JSON Mode 下纯短 JSON，保持低上限以节省免费档额度
FLASH_MICRO_MAX_TOKENS = 200
FLASH_MICRO_TEMPERATURE = 0.2

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

# 按档位熔断：Flash / Pro 双 Key 互不影响（一侧额度耗尽另一侧仍可跑）
_gemini_circuit_lock = threading.Lock()
_gemini_circuit_open: dict[ApiTier, bool] = {"flash": False, "pro": False}
_gemini_circuit_reason: dict[ApiTier, str] = {"flash": "", "pro": ""}


def _env_key(*names: str) -> str:
    for name in names:
        val = (os.getenv(name) or "").strip()
        if val:
            return val
    return ""


def resolve_api_tier(model: str | None = None, *, api_tier: ApiTier | None = None) -> ApiTier:
    """硬路由档位：显式 api_tier 优先，否则按模型名含 pro 判定。"""
    if api_tier in ("flash", "pro"):
        return api_tier
    return "pro" if is_pro_model(model or "") else "flash"


def resolve_api_key(model: str | None = None, *, api_tier: ApiTier | None = None) -> str:
    """按档位取 Key；专用 Key 未设时 fallback 到 legacy，再跨档互备。"""
    return resolve_api_key_with_source(model, api_tier=api_tier)[0]


def _collect_named_keys() -> dict[str, str]:
    """收集当前环境中所有可用 Gemini Key（去空）。"""
    legacy = _env_key("GEMINI_API_KEY") or (config.GEMINI_API_KEY or "").strip()
    flash = _env_key("GEMINI_FLASH_API_KEY") or (config.GEMINI_FLASH_API_KEY or "").strip()
    pro = _env_key("GEMINI_PRO_API_KEY") or (config.GEMINI_PRO_API_KEY or "").strip()
    out: dict[str, str] = {}
    if flash:
        out["GEMINI_FLASH_API_KEY"] = flash
    if pro:
        out["GEMINI_PRO_API_KEY"] = pro
    if legacy:
        out["GEMINI_API_KEY"] = legacy
    return out


def iter_api_key_candidates(
    model: str | None = None, *, api_tier: ApiTier | None = None,
) -> list[tuple[str, str]]:
    """按档位优先序返回候选 (key, 来源名)；同内容去重。

    Pro: PRO → legacy → FLASH（日报/L3 可与周报共用唯一可用 Key）
    Flash: FLASH → legacy → PRO
    """
    named = _collect_named_keys()
    tier = resolve_api_tier(model, api_tier=api_tier)
    order = (
        ("GEMINI_PRO_API_KEY", "GEMINI_API_KEY", "GEMINI_FLASH_API_KEY")
        if tier == "pro"
        else ("GEMINI_FLASH_API_KEY", "GEMINI_API_KEY", "GEMINI_PRO_API_KEY")
    )
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for src in order:
        key = named.get(src) or ""
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((key, src))
    return out


def resolve_api_key_with_source(
    model: str | None = None, *, api_tier: ApiTier | None = None,
) -> tuple[str, str]:
    """返回首选 (api_key, 来源环境变量名)。"""
    cands = iter_api_key_candidates(model, api_tier=api_tier)
    if not cands:
        return "", "(missing)"
    return cands[0]


def api_key_source(model: str | None = None, *, api_tier: ApiTier | None = None) -> str:
    return resolve_api_key_with_source(model, api_tier=api_tier)[1]


def _key_fingerprint(key: str) -> str:
    k = (key or "").strip()
    if len(k) < 8:
        return f"len={len(k)}"
    return f"{k[:4]}…{k[-4:]} (len={len(k)})"


def log_gemini_key_status() -> None:
    """启动时打印双 Key 配置概况（指纹，不泄露全文）。"""
    named = _collect_named_keys()
    if not named:
        print("⚠️  Gemini：未配置任何 API Key")
        return
    for src, key in named.items():
        print(f"🔑 {src} → {_key_fingerprint(key)}")
    for tier in ("flash", "pro"):
        key, src = resolve_api_key_with_source(api_tier=tier)  # type: ignore[arg-type]
        print(f"   └ {tier} 首选 → {src}")


def is_gemini_auth_error(exc: BaseException | str) -> bool:
    text = str(exc)
    low = text.lower()
    return (
        "API_KEY_INVALID" in text
        or "api key not valid" in low
        or "PERMISSION_DENIED" in text
        or "API key expired" in low
    )


# Pro 深度会诊在本进程已确认不可用（免费档 limit:0 / 配额耗尽）→ 改用 Flash
_pro_deep_unavailable = False
_pro_deep_lock = threading.Lock()


def mark_pro_deep_unavailable(reason: str = "") -> None:
    """标记 Pro 深度模型不可用，后续会诊直接走 Flash。"""
    global _pro_deep_unavailable
    with _pro_deep_lock:
        if _pro_deep_unavailable:
            return
        _pro_deep_unavailable = True
        trip_gemini_circuit(reason or "Pro 免费配额不可用", api_tier="pro")
        print(
            "ℹ️  深度会诊降级：`gemini` Pro 免费档不可用 → 改用 "
            f"`{FLASH_MODEL or config.GEMINI_FLASH_MODEL}` + Flash Key"
        )


def pro_deep_unavailable() -> bool:
    return _pro_deep_unavailable or bool(config.GEMINI_FORCE_FLASH_DEEP) or gemini_circuit_is_open(
        "pro"
    )


def resolve_deep_model_and_tier(
    preferred_model: str | None = None,
) -> tuple[str, ApiTier]:
    """日报 / L3 / 狙击深度会诊所用模型与 Key 档位。

    优先 Pro；若免费档 Pro 配额为 0 或已熔断，自动改 Flash（不花钱仍能出报告）。
    """
    if pro_deep_unavailable():
        flash = (FLASH_MODEL or config.GEMINI_FLASH_MODEL).strip() or "gemini-2.5-flash"
        return flash, "flash"
    pro = (preferred_model or config.GEMINI_PRO_MODEL or PRO_MODEL).strip() or "gemini-2.5-pro"
    if is_pro_model(pro):
        return pro, "pro"
    return pro, "flash"

def gemini_circuit_is_open(api_tier: ApiTier | None = None) -> bool:
    if api_tier is None:
        return any(_gemini_circuit_open.values())
    return bool(_gemini_circuit_open.get(api_tier))


def gemini_circuit_reason(api_tier: ApiTier | None = None) -> str:
    if api_tier is None:
        for tier in ("flash", "pro"):
            if _gemini_circuit_open.get(tier):
                return _gemini_circuit_reason.get(tier) or f"Gemini {tier} 额度耗尽"
        return "Gemini额度耗尽"
    return _gemini_circuit_reason.get(api_tier) or f"Gemini {api_tier} 额度耗尽"


def trip_gemini_circuit(reason: str, *, api_tier: ApiTier = "flash") -> None:
    with _gemini_circuit_lock:
        if _gemini_circuit_open.get(api_tier):
            return
        _gemini_circuit_open[api_tier] = True
        _gemini_circuit_reason[api_tier] = (reason or "RESOURCE_EXHAUSTED").replace("\n", " ")[:200]
        key_hint = (
            "GEMINI_FLASH_API_KEY" if api_tier == "flash" else "GEMINI_PRO_API_KEY"
        )
        print(
            f"🛑 Gemini {api_tier} 熔断已开启：{_gemini_circuit_reason[api_tier]}\n"
            f"   本轮该档位剩余调用将跳过（另一档位不受影响）。"
            f"请确认 {key_hint} / GEMINI_API_KEY 为 AI Studio【免费档】，"
            "见 https://aistudio.google.com/apikey"
        )


def is_gemini_quota_error(exc: BaseException | str) -> bool:
    text = str(exc)
    low = text.lower()
    return (
        "RESOURCE_EXHAUSTED" in text
        or "prepayment credits" in low
        or "credits are depleted" in low
        or ("429" in text and ("quota" in low or "billing" in low or "resource_exhausted" in low))
    )


def build_task_prompt(mode: str, user_status: str = "watch") -> str:
    """按报告周期 + 影子持仓状态组装动态 Task Prompt。"""
    task_key = mode if mode in TASK_PROMPTS else "weekly"
    status = normalize_user_status(user_status)
    return TASK_PROMPTS[task_key].format(user_status=status)


def get_mode_context(mode: str, user_status: str = "watch") -> str:
    """兼容旧调用方：返回动态 Task Prompt 文本。"""
    return build_task_prompt(mode, user_status)


def is_configured() -> bool:
    """任一档位有可用 Key（含 legacy GEMINI_API_KEY）即视为已配置。"""
    return bool(resolve_api_key(api_tier="flash") or resolve_api_key(api_tier="pro"))


def is_flash_configured() -> bool:
    return bool(resolve_api_key(api_tier="flash"))


def is_pro_configured() -> bool:
    return bool(resolve_api_key(api_tier="pro"))


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
    temperature: float | None = None,
    api_tier: ApiTier | None = None,
    _allow_flash_deep_fallback: bool = True,
) -> str:
    """调用 Gemini 生成内容（默认按模型节流；agent 可先 throttle 再 skip）。

    Layer 2 Flash：response_mime_type='application/json' + 低 max_output_tokens + 低 temperature，
    从 API 层禁止 Markdown/<think>，节省免费档额度。

    api_tier='flash'|'pro'：硬路由到对应 API Key（未设则 fallback GEMINI_API_KEY）。
    Pro 免费配额耗尽时自动降级 Flash 模型+Key（日报/L3 可继续）。
    """
    requested_tier = resolve_api_tier(model, api_tier=api_tier)
    # 深度 Pro 已确认不可用：直接改走 Flash，避免每只票先撞一次 429
    if (
        requested_tier == "pro"
        and _allow_flash_deep_fallback
        and pro_deep_unavailable()
    ):
        flash_model = (FLASH_MODEL or config.GEMINI_FLASH_MODEL).strip() or "gemini-2.5-flash"
        return generate(
            system_prompt,
            user_content,
            model=flash_model,
            max_tokens=max_tokens,
            skip_throttle=skip_throttle,
            response_mime_type=response_mime_type,
            response_json_schema=response_json_schema,
            thinking_budget=thinking_budget,
            temperature=temperature,
            api_tier="flash",
            _allow_flash_deep_fallback=False,
        )

    tier = requested_tier
    candidates = iter_api_key_candidates(model, api_tier=tier)
    if not candidates:
        raise LLMError(
            "未设置 GEMINI_FLASH_API_KEY / GEMINI_PRO_API_KEY / GEMINI_API_KEY，"
            "无法生成 LLM 叙述（可用 --data-only 仅看硬指标）。"
        )

    if gemini_circuit_is_open(tier):
        raise LLMError(f"Gemini {tier} 已熔断（{gemini_circuit_reason(tier)}），跳过调用。")

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
    if temperature is not None:
        cfg_kwargs["temperature"] = float(temperature)
    if response_mime_type:
        cfg_kwargs["response_mime_type"] = response_mime_type
    if response_json_schema is not None:
        cfg_kwargs["response_json_schema"] = response_json_schema
    if thinking_budget is not None:
        try:
            cfg_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=int(thinking_budget),
            )
        except Exception:  # noqa: BLE001
            pass

    last_exc: BaseException | None = None
    last_src = candidates[0][1]
    last_fp = _key_fingerprint(candidates[0][0])

    for idx, (api_key, key_src) in enumerate(candidates):
        last_src, last_fp = key_src, _key_fingerprint(api_key)
        client = genai.Client(api_key=api_key)
        try:
            try:
                resp = client.models.generate_content(
                    model=resolved_model,
                    contents=user_content,
                    config=types.GenerateContentConfig(**cfg_kwargs),
                )
            except TypeError:
                soft = {
                    "system_instruction": system_prompt,
                    "max_output_tokens": max_tokens,
                }
                if temperature is not None:
                    soft["temperature"] = float(temperature)
                if response_mime_type:
                    soft["response_mime_type"] = response_mime_type
                resp = client.models.generate_content(
                    model=resolved_model,
                    contents=user_content,
                    config=types.GenerateContentConfig(**soft),
                )
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if is_gemini_auth_error(exc) and idx + 1 < len(candidates):
                nxt = candidates[idx + 1][1]
                print(
                    f"⚠️  Gemini {tier} Key 无效（{key_src} · {_key_fingerprint(api_key)}），"
                    f"改用 {nxt} 重试…"
                )
                continue
            # Pro 配额耗尽（含 free tier limit:0）→ 降级 Flash，不直接整轮报废
            if (
                tier == "pro"
                and _allow_flash_deep_fallback
                and is_gemini_quota_error(exc)
            ):
                mark_pro_deep_unavailable(str(exc))
                flash_model = (
                    FLASH_MODEL or config.GEMINI_FLASH_MODEL
                ).strip() or "gemini-2.5-flash"
                print(
                    f"⚠️  Pro 配额不可用（{key_src}），改用 `{flash_model}` 重试…"
                )
                return generate(
                    system_prompt,
                    user_content,
                    model=flash_model,
                    max_tokens=max_tokens,
                    skip_throttle=skip_throttle,
                    response_mime_type=response_mime_type,
                    response_json_schema=response_json_schema,
                    thinking_budget=thinking_budget,
                    temperature=temperature,
                    api_tier="flash",
                    _allow_flash_deep_fallback=False,
                )
            if is_gemini_quota_error(exc) or is_gemini_auth_error(exc):
                trip_gemini_circuit(str(exc), api_tier=tier)
            raise LLMError(
                f"Gemini 调用失败（密钥来源 {key_src} · {_key_fingerprint(api_key)}）：{exc}"
            ) from exc

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
            raise LLMError(f"Gemini 返回空内容{hint}（可能触发安全过滤或输出上限）。")
        if idx > 0:
            print(f"✅ Gemini {tier} 已改用 {key_src} 成功")
        return text

    assert last_exc is not None
    if (
        tier == "pro"
        and _allow_flash_deep_fallback
        and is_gemini_quota_error(last_exc)
    ):
        mark_pro_deep_unavailable(str(last_exc))
        flash_model = (FLASH_MODEL or config.GEMINI_FLASH_MODEL).strip() or "gemini-2.5-flash"
        print(f"⚠️  Pro 配额不可用，改用 `{flash_model}` 重试…")
        return generate(
            system_prompt,
            user_content,
            model=flash_model,
            max_tokens=max_tokens,
            skip_throttle=skip_throttle,
            response_mime_type=response_mime_type,
            response_json_schema=response_json_schema,
            thinking_budget=thinking_budget,
            temperature=temperature,
            api_tier="flash",
            _allow_flash_deep_fallback=False,
        )
    trip_gemini_circuit(str(last_exc), api_tier=tier)
    raise LLMError(
        f"Gemini 调用失败（密钥来源 {last_src} · {last_fp}）：{last_exc}"
    ) from last_exc
