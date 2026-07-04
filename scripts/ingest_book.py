#!/usr/bin/env python3
"""把本地原著切块并用 Gemini 向量化，生成本地 RAG 索引。

⚠️ 版权：原著文件与生成的索引都只留在本地（books/ 已被 .gitignore 忽略），绝不提交。

用法：
    python scripts/ingest_book.py /path/to/彼得林奇的成功投资.txt
    python scripts/ingest_book.py /path/to/book.pdf --chunk-size 900 --overlap 150

支持 .txt / .md（直接读）与 .pdf（需 pypdf）。.epub 请先转成 txt。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:  # noqa: BLE001
    pass

from src.lynch.knowledge import INDEX_PATH, EMBED_MODEL, embed_texts  # noqa: E402


def _read_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            sys.exit("❌ 读取 PDF 需要 pypdf：pip install pypdf")
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    sys.exit(f"❌ 暂不支持的格式：{suffix}（请转成 .txt）")


def _chunk(text: str, size: int, overlap: int) -> list[str]:
    """按段落聚合成 ~size 字的块，块间保留 overlap 字重叠，保证语义连续。"""
    paras = [p.strip() for p in text.replace("\r\n", "\n").split("\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 1 <= size:
            buf = f"{buf}\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            # 从上一块尾部保留 overlap 字，减少切割导致的语义断裂
            tail = buf[-overlap:] if overlap and buf else ""
            buf = f"{tail}\n{p}" if tail else p
    if buf:
        chunks.append(buf)
    return [c for c in chunks if len(c) > 40]


def main() -> int:
    ap = argparse.ArgumentParser(description="原著 → 本地 RAG 索引（仅本地）")
    ap.add_argument("book", help="原著文件路径（.txt/.md/.pdf）")
    ap.add_argument("--chunk-size", type=int, default=900, help="每块目标字数（默认 900）")
    ap.add_argument("--overlap", type=int, default=150, help="块间重叠字数（默认 150）")
    ap.add_argument("--batch", type=int, default=64, help="每批向量化数量")
    args = ap.parse_args()

    path = Path(args.book).expanduser()
    if not path.exists():
        sys.exit(f"❌ 找不到文件：{path}")

    print(f"📖 读取 {path.name} …")
    text = _read_text(path)
    chunks = _chunk(text, args.chunk_size, args.overlap)
    print(f"✂️  切成 {len(chunks)} 块，开始用 {EMBED_MODEL} 向量化 …")

    embeddings: list[list[float]] = []
    for i in range(0, len(chunks), args.batch):
        batch = chunks[i : i + args.batch]
        embeddings.extend(embed_texts(batch))
        print(f"  …已向量化 {min(i + args.batch, len(chunks))}/{len(chunks)}")

    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(
        json.dumps(
            {"model": EMBED_MODEL, "source": path.name, "chunks": chunks, "embeddings": embeddings},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"✅ 索引已写入本地 {INDEX_PATH}（已被 .gitignore 忽略，不会提交）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
