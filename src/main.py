"""Apify Actor: scanned PDF / images → Markdown via RapidOCR (CJK-capable) + optional RAG chunks."""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from apify import Actor

from chunking import approx_tokens, chunk_document
from convert import convert_image, convert_pdf, sniff_kind

EVENT_DOC = "document-ocrd"
EVENT_PAGE = "page-ocrd"
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
UA = "Mozilla/5.0 (compatible; ApifyScannedOcrToMarkdown/0.1; +https://apify.com)"


def _page_marker(style: str, n: int) -> str:
    if style == "comment":
        return f"<!-- page: {n} -->"
    if style == "text":
        return f"--- Page {n} ---"
    return ""


def _parse_range(s: str | None) -> tuple[int, int] | None:
    if not s:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d*)\s*)?", s)
    if not m:
        raise ValueError(f"Invalid pageRange '{s}'. Use e.g. '1-10', '5', or '3-'.")
    a = int(m.group(1))
    b = m.group(2)
    return (a, int(b) if b else (0 if "-" in s else a))


def _safe_key(name: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9!\-_.'()]", "-", name)[:200].strip("-") or "document"
    return base


async def _download(client: httpx.AsyncClient, url: str, dest: Path) -> tuple[str, str]:
    headers = {"User-Agent": UA}
    host = urlparse(url).hostname or ""
    token = os.environ.get("APIFY_TOKEN")
    if token and host.endswith("api.apify.com"):
        headers["Authorization"] = f"Bearer {token}"
    async with client.stream("GET", url, headers=headers, follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        size = 0
        with dest.open("wb") as f:
            async for chunk in r.aiter_bytes(1 << 16):
                size += len(chunk)
                if size > MAX_DOWNLOAD_BYTES:
                    raise ValueError("File is larger than 200 MB.")
                f.write(chunk)
        ctype = r.headers.get("content-type", "")
        cd = r.headers.get("content-disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd)
    name = unquote(m.group(1)) if m else unquote(Path(urlparse(url).path).name or "document")
    return name, ctype


async def _load_kvs_record(ref: str, dest: Path) -> tuple[str, str]:
    store_ref, key = (ref.split("/", 1) if "/" in ref else (None, ref))
    value = None
    if store_ref:
        for kwargs in ({"name": store_ref}, {"id": store_ref}):
            try:
                store = await Actor.open_key_value_store(**kwargs)
                value = await store.get_value(key)
            except Exception:  # noqa: BLE001
                value = None
            if value is not None:
                break
    else:
        store = await Actor.open_key_value_store()
        value = await store.get_value(key)
    if value is None:
        raise FileNotFoundError(f"Key-value store record '{ref}' not found.")
    if isinstance(value, str):
        value = value.encode("latin-1")
    if not isinstance(value, (bytes, bytearray)):
        raise ValueError(f"Record '{ref}' is not a file (got {type(value).__name__}).")
    dest.write_bytes(value)
    return key, ""


def _collect_sources(inp: dict) -> list[dict]:
    sources = []
    for u in inp.get("urls") or []:
        url = u.get("url") if isinstance(u, dict) else u
        if url and str(url).strip():
            sources.append({"kind": "url", "ref": str(url).strip()})
    for f in inp.get("files") or []:
        f = (f.get("url") if isinstance(f, dict) else f) or ""
        f = str(f).strip()
        if not f:
            continue
        sources.append({"kind": "url" if re.match(r"https?://", f) else "kvs", "ref": f})
    seen, out = set(), []
    for s in sources:
        if s["ref"] not in seen:
            seen.add(s["ref"])
            out.append(s)
    return out


def _assemble_markdown(pages: list[tuple[int | None, str]], marker_style: str) -> str:
    parts = []
    for pno, md in pages:
        mk = _page_marker(marker_style, pno) if pno is not None else ""
        if mk:
            parts.append(mk)
        if md.strip():
            parts.append(md.strip())
    return "\n\n".join(parts).strip() + "\n"


async def process_source(src: dict, inp: dict, client: httpx.AsyncClient, workdir: Path, page_budget: int | None):
    t0 = time.time()
    tmp = workdir / hashlib.sha1(src["ref"].encode()).hexdigest()
    if src["kind"] == "url":
        name, _ctype = await _download(client, src["ref"], tmp)
    else:
        name, _ctype = await _load_kvs_record(src["ref"], tmp)
    head = tmp.open("rb").read(2048)
    kind = sniff_kind(str(tmp), head, name)
    page_range = _parse_range(inp.get("pageRange"))
    max_pages = int(inp.get("maxPagesPerDocument") or 0) or None
    marker_style = inp.get("pageMarkers", "comment")
    dpi = int(inp.get("dpi") or 200)
    dpi = max(72, min(dpi, 400))
    repair = inp.get("repairTables", True)
    warnings: list[str] = []
    truncated = False

    if kind == "pdf":
        import pymupdf
        with pymupdf.open(str(tmp)) as d:
            total = d.page_count
        start, end = (page_range[0], page_range[1] or total) if page_range else (1, total)
        end = min(end, total)
        limit = min(x for x in [max_pages, page_budget, end - start + 1] if x is not None)
        if limit < end - start + 1:
            truncated = True
            end = start + max(limit, 0) - 1
            warnings.append(f"OCR'd pages {start}-{end} only (maxPagesPerDocument / spending limit).")
        if end < start:
            raise ValueError("Spending limit reached before this document could be OCR'd.")
        conv = await asyncio.to_thread(
            convert_pdf, str(tmp), page_range=(start, end), dpi=dpi,
            repair_tables=repair, password=inp.get("pdfPassword") or None,
        )
    else:
        if page_budget is not None and page_budget < 1:
            raise ValueError("Spending limit reached before this image could be OCR'd.")
        conv = await asyncio.to_thread(convert_image, str(tmp), repair_tables=repair)

    pages = [(p.number, p.markdown) for p in conv.pages]
    billable_pages = len(conv.pages)
    title = (conv.metadata.get("title") or "").strip() or Path(name).stem
    markdown = _assemble_markdown(pages, marker_style if len(pages) > 1 or kind == "pdf" else "none")
    fmode = inp.get("outputFormat", "markdown")
    warnings += conv.warnings

    doc_item: dict = {
        "type": "document",
        "source": src["ref"],
        "sourceType": src["kind"],
        "fileName": name,
        "format": conv.format,
        "status": "success",
        "title": title,
        "pageCount": conv.page_count,
        "pagesConverted": billable_pages,
        "truncated": truncated,
        "metadata": conv.metadata,
        "stats": {
            **conv.stats,
            "characters": len(markdown),
            "words": len(markdown.split()),
            "tokenEstimate": approx_tokens(markdown),
            "dpi": dpi if kind == "pdf" else None,
        },
        "warnings": warnings,
    }
    if inp.get("saveMarkdownFiles", True):
        key = _safe_key(Path(name).stem + "-" + hashlib.sha1(src["ref"].encode()).hexdigest()[:8]) + ".md"
        store = await Actor.open_key_value_store()
        await store.set_value(key, markdown, content_type="text/markdown; charset=utf-8")
        doc_item["markdownKey"] = key
        try:
            doc_item["markdownUrl"] = await store.get_public_url(key)
        except Exception:  # noqa: BLE001
            pass
    if fmode in ("markdown", "markdown_and_chunks"):
        doc_item["markdown"] = markdown if len(markdown.encode()) < 8_000_000 else None
    chunk_items: list[dict] = []
    if fmode in ("chunks", "markdown_and_chunks"):
        chunks = chunk_document(
            pages,
            chunk_size=int(inp.get("chunkSize", 1000)),
            chunk_overlap=int(inp.get("chunkOverlap", 150)),
            unit=inp.get("chunkUnit", "tokens"),
            prepend_heading_path=inp.get("prependHeadingPath", False),
        )
        for c in chunks:
            chunk_items.append({"type": "chunk", "source": src["ref"], "fileName": name, "title": title, **c})
        doc_item["chunkCount"] = len(chunks)
    doc_item["processingMs"] = int((time.time() - t0) * 1000)
    doc_item["billablePages"] = billable_pages
    return doc_item, chunk_items


async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        sources = _collect_sources(inp)
        if not sources:
            raise ValueError("Provide at least one scanned PDF or image in `urls` or `files`.")
        Actor.log.info(f"OCR-converting {len(sources)} document(s)")
        # Warm OCR engine once
        await asyncio.to_thread(__import__("ocr_engine").get_engine)
        cm = Actor.get_charging_manager()
        ok = failed = pages_total = 0
        workdir = Path(tempfile.mkdtemp(prefix="ocr2md-"))
        async with httpx.AsyncClient(http2=False) as client:
            for i, src in enumerate(sources, 1):
                await Actor.set_status_message(f"{i}/{len(sources)}: {src['ref'][:80]}")
                budget = cm.calculate_max_event_charge_count_within_limit(EVENT_PAGE)
                if budget is not None and budget <= 0:
                    Actor.log.warning("Spending limit reached; stopping.")
                    break
                doc_budget = cm.calculate_max_event_charge_count_within_limit(EVENT_DOC)
                if doc_budget is not None and doc_budget <= 0:
                    Actor.log.warning("Spending limit reached; stopping.")
                    break
                try:
                    doc_item, chunk_items = await process_source(src, inp, client, workdir, budget)
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    Actor.log.warning(f"Failed {src['ref']}: {e}")
                    await Actor.push_data({
                        "type": "document", "source": src["ref"], "sourceType": src["kind"],
                        "status": "error", "error": str(e)[:1000],
                    })
                    continue
                await Actor.push_data(doc_item)
                if chunk_items:
                    for k in range(0, len(chunk_items), 500):
                        await Actor.push_data(chunk_items[k:k + 500])
                await Actor.charge(EVENT_DOC)
                res = await Actor.charge(EVENT_PAGE, count=doc_item["billablePages"])
                ok += 1
                pages_total += doc_item["billablePages"]
                Actor.log.info(
                    f"OK {doc_item['fileName']}: {doc_item['billablePages']} page(s), "
                    f"{doc_item['stats'].get('ocrItems', 0)} OCR item(s), {doc_item['processingMs']} ms"
                )
                if res.event_charge_limit_reached:
                    Actor.log.warning("Spending limit reached; stopping after this document.")
                    break
        import resource
        peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        msg = f"Done: {ok} OCR'd, {failed} failed, {pages_total} page(s). Peak memory {peak_mb:.0f} MB."
        Actor.log.info(msg)
        await Actor.set_status_message(msg, is_terminal=True)


if __name__ == "__main__":
    asyncio.run(main())
