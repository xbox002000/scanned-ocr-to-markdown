"""Heading- and table-aware chunking for RAG.

Blocks (headings, paragraphs, lists, tables) are packed into chunks up to
`chunk_size`; tables are never split mid-row (oversized tables are split by rows
with the header repeated); oversized paragraphs are split on sentence boundaries.
Each chunk carries source, page range, heading path and chunk index.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


def approx_tokens(text: str) -> int:
    # ~4 chars/token for English prose (OpenAI rule of thumb); dependency-free on purpose.
    return math.ceil(len(text) / 4)


@dataclass
class Block:
    text: str
    page: int | None
    heading_path: tuple[str, ...]
    kind: str  # heading | table | text


def _split_blocks(md: str) -> list[str]:
    blocks, buf, in_table = [], [], False
    for line in md.split("\n"):
        is_tbl = line.lstrip().startswith("|")
        if not line.strip():
            if buf:
                blocks.append("\n".join(buf))
                buf = []
            in_table = False
            continue
        if _HEADING.match(line):
            if buf:
                blocks.append("\n".join(buf))
                buf = []
            blocks.append(line)
            continue
        if buf and is_tbl != in_table:
            blocks.append("\n".join(buf))
            buf = []
        in_table = is_tbl
        buf.append(line)
    if buf:
        blocks.append("\n".join(buf))
    return blocks


def build_blocks(pages: list[tuple[int | None, str]]) -> list[Block]:
    path: list[tuple[int, str]] = []
    out: list[Block] = []
    for pno, md in pages:
        for b in _split_blocks(md):
            m = _HEADING.match(b)
            if m:
                level = len(m.group(1))
                path = [p for p in path if p[0] < level] + [(level, m.group(2).strip())]
                out.append(Block(b, pno, tuple(t for _, t in path), "heading"))
            else:
                kind = "table" if b.lstrip().startswith("|") else "text"
                out.append(Block(b, pno, tuple(t for _, t in path), kind))
    return out


def _measure(text: str, unit: str) -> int:
    return approx_tokens(text) if unit == "tokens" else len(text)


def _explode(block: Block, size: int, unit: str) -> list[Block]:
    """Split a single oversized block."""
    if _measure(block.text, unit) <= size:
        return [block]
    if block.kind == "table":
        lines = block.text.split("\n")
        head, body = lines[:2], lines[2:]
        parts, cur = [], list(head)
        for ln in body:
            if len(cur) > 2 and _measure("\n".join(cur + [ln]), unit) > size:
                parts.append("\n".join(cur))
                cur = list(head)
            cur.append(ln)
        parts.append("\n".join(cur))
        return [Block(p, block.page, block.heading_path, "table") for p in parts]
    sents = _SENT.split(block.text)
    parts, cur = [], ""
    for s in sents:
        while _measure(s, unit) > size:  # pathological: no sentence breaks
            cut = size * 4 if unit == "tokens" else size
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(s[:cut])
            s = s[cut:]
        if cur and _measure(cur + " " + s, unit) > size:
            parts.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        parts.append(cur)
    return [Block(p, block.page, block.heading_path, block.kind) for p in parts]


def chunk_document(
    pages: list[tuple[int | None, str]],
    *,
    chunk_size: int = 1000,
    chunk_overlap: int = 150,
    unit: str = "tokens",
    prepend_heading_path: bool = False,
) -> list[dict]:
    chunk_overlap = max(0, min(chunk_overlap, chunk_size // 2))
    blocks: list[Block] = []
    for b in build_blocks(pages):
        blocks.extend(_explode(b, chunk_size, unit))

    chunks: list[list[Block]] = []
    cur: list[Block] = []
    cur_size = 0
    for b in blocks:
        bsize = _measure(b.text, unit)
        if cur and cur_size + bsize > chunk_size:
            chunks.append(cur)
            # overlap: carry trailing non-heading text blocks up to chunk_overlap
            carry, csize = [], 0
            for prev in reversed(cur):
                psize = _measure(prev.text, unit)
                if prev.kind == "heading" or csize + psize > chunk_overlap:
                    break
                carry.insert(0, prev)
                csize += psize
            # a heading at the end of the previous chunk belongs to the next one
            while cur and cur[-1].kind == "heading":
                carry.append(cur.pop())
            if not chunks[-1]:
                chunks.pop()
            cur, cur_size = carry, sum(_measure(x.text, unit) for x in carry)
        cur.append(b)
        cur_size += bsize
    if cur:
        chunks.append(cur)
    # drop chunks that are only headings / overlap duplicates
    chunks = [c for c in chunks if any(x.kind != "heading" for x in c)] or chunks

    out = []
    for i, c in enumerate(chunks):
        text = "\n\n".join(x.text for x in c)
        content_blocks = [x for x in c if x.kind != "heading"] or c
        hp = list(content_blocks[0].heading_path)
        pages_in = [x.page for x in c if x.page is not None]
        if prepend_heading_path and hp:
            text = "Section: " + " > ".join(hp) + "\n\n" + text
        out.append({
            "chunkIndex": i,
            "chunkCount": len(chunks),
            "text": text,
            "headingPath": hp,
            "pageStart": min(pages_in) if pages_in else None,
            "pageEnd": max(pages_in) if pages_in else None,
            "charCount": len(text),
            "tokenEstimate": approx_tokens(text),
            "containsTable": any(x.kind == "table" for x in c),
        })
    return out
