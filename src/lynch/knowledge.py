"""林奇知识层：心法包（随仓库分发）+ 本地 RAG 检索器（原著仅本地，可选）。

设计原则：
- 心法包 lynch_playbook.md 是自撰提炼，注入 system prompt，随时可用。
- RAG 索引由 scripts/ingest_book.py 从**本地**原著生成，存到 books/（已 gitignore）。
  索引不存在 / 未配置 GEMINI_API_KEY / 依赖缺失时，检索一律优雅降级为返回空，绝不报错。
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
PLAYBOOK_PATH = _ROOT / "knowledge" / "lynch_playbook.md"
INDEX_PATH = _ROOT / "books" / "lynch_book.rag.json"
EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")


@lru_cache(maxsize=1)
def load_playbook() -> str:
    """读取林奇心法包（自撰提炼）。文件缺失则返回空串。"""
    try:
        return PLAYBOOK_PATH.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        return ""


def has_book_index() -> bool:
    return INDEX_PATH.exists()


def embed_texts(texts: list[str], *, task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    """用 Gemini 向量化一批文本。失败/未配置则抛异常，由调用方决定降级。"""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("未配置 GEMINI_API_KEY，无法向量化。")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    resp = client.models.embed_content(
        model=EMBED_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(task_type=task_type),
    )
    return [list(e.values) for e in resp.embeddings]


@lru_cache(maxsize=1)
def _load_index() -> tuple[list[str], list[list[float]]]:
    """加载本地 RAG 索引 → (chunks, embeddings)。缺失/损坏返回空。"""
    if not INDEX_PATH.exists():
        return [], []
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        return data.get("chunks", []), data.get("embeddings", [])
    except Exception:  # noqa: BLE001
        return [], []


def retrieve(query: str, k: int = 3) -> list[str]:
    """按语义相似度从本地原著索引取回最相关的 k 段。任何问题都降级为返回 []。"""
    chunks, embeddings = _load_index()
    if not chunks or not embeddings:
        return []
    try:
        import numpy as np

        # 用 float64 计算，并先清洗掉可能的 NaN/inf，避免高维向量溢出影响排序
        q_vec = np.nan_to_num(
            np.asarray(embed_texts([query], task_type="RETRIEVAL_QUERY")[0], dtype="float64")
        )
        mat = np.nan_to_num(np.asarray(embeddings, dtype="float64"))
        q_norm = q_vec / (np.linalg.norm(q_vec) + 1e-12)
        m_norm = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12)
        # 数据已确认有限；抑制 BLAS matmul 内核在大矩阵上偶发的无害 FP 标志告警
        with np.errstate(all="ignore"):
            scores = m_norm @ q_norm
        scores = np.nan_to_num(scores, nan=-1.0, posinf=-1.0, neginf=-1.0)
        top = np.argsort(-scores)[:k]
        return [chunks[i] for i in top]
    except Exception:  # noqa: BLE001
        return []


def build_reference_block(query: str, k: int = 3) -> str:
    """把检索到的原著片段拼成一段可注入提示的参考区块；无内容返回空串。"""
    passages = retrieve(query, k=k)
    if not passages:
        return ""
    lines = ["【彼得·林奇原著相关片段（仅供参考，请结合上面的真实数据判断）】"]
    for i, p in enumerate(passages, 1):
        snippet = p.strip().replace("\n", " ")
        lines.append(f"{i}. {snippet}")
    return "\n".join(lines)
