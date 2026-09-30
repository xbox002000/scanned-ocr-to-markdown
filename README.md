# Scanned PDF/Image OCR to Markdown

**Turn scanned PDFs and images into Markdown** with RapidOCR (onnxruntime) — CJK-capable, no external AI API keys.

This Actor rasterizes each PDF page (or loads an image), runs offline OCR, and writes Markdown with optional page markers and RAG chunks. Field names for chunking mirror the digital PDF/DOCX Actor where practical.

## What you get

- OCR for **scanned PDFs** and common **images** (PNG, JPEG, WebP, TIFF, …)
- Default engine: **RapidOCR + onnxruntime** (Apache-2.0 / MIT stack; see `NOTICE`)
- Optional **page markers** and **RAG chunks** (`chunkSize` / `chunkOverlap` / `chunkUnit`)
- Best-effort **table assembly** from OCR box geometry — **not** the digital PDF word-index table repair used by the PDF/DOCX Actor
- Dataset items + optional `.md` key-value store files

## Measured cloud runs (2026-09-30, Asia/Taipei)

Own runs on this Actor, build **0.1.3**, memory **2048 MB**. Platform cost = `usageTotalUsd` re-read after finish (not PPE revenue). Public-domain LOC/WDL scans only.

| Run | Input | Pages | Wall | Peak RSS | CU | Platform USD |
|---|---|---|---|---|---|---|
| `huicge2RO7IJnpaQs` | Emancipation Proclamation (image PDF) | 5 | 109 s | 1077 MB | 0.061 | $0.0123 |
| `OdyOeoSfhaaghyr9t` | Furness sermon 1841, pages 1–8 | 8 | 42 s | 1361 MB | 0.023 | $0.0048 |
| `e5xfmZVXzIZwjQexI` | RapidOCR CJK+EN sample JPG | 1 | 9.5 s | 310 MB | 0.005 | $0.0012 |

Dense-page platform cost ≈ **$0.0025 / page** on the Emancipation scan (large page images @ 200 DPI). Sparse cover pages are cheaper. These runs confirm OCR completes and writes dataset items; they are **not** an accuracy benchmark. Old print and manuscript-style pages show typical OCR noise (misread characters). Do not expect digital-PDF table quality.

## Use cases

- Ingest **scanned** reports, pamphlets, and image-only PDFs into Markdown for RAG or note-taking
- Batch OCR of screenshots / phone photos of documents (print, not handwriting)
- CJK + Latin mixed scans where a local RapidOCR stack is enough (no cloud vision API)

## How to use

1. Paste direct URLs to scanned PDFs/images, or upload files.
2. Choose `markdown`, `chunks`, or both.
3. Optionally set `pageRange`, `dpi` (PDF), and `maxPagesPerDocument`.

### Example input

```json
{
  "urls": [{ "url": "https://example.com/scan.pdf" }],
  "outputFormat": "markdown",
  "pageMarkers": "comment",
  "dpi": 200,
  "maxPagesPerDocument": 10
}
```

### Example output (dataset `document` item, fields abbreviated)

```json
{
  "type": "document",
  "status": "success",
  "fileName": "scan.pdf",
  "pageCount": 5,
  "pagesConverted": 5,
  "stats": { "ocrItems": 105, "tables": 0, "words": 728, "dpi": 200 },
  "markdown": "<!-- page: 1 -->\n\n…",
  "markdownKey": "scan.md"
}
```

## Pricing

Pay-per-event:

| Event | Price |
|---|---|
| Actor start | Apify default ($0.00005 / GB memory) |
| Document OCR'd | **$0.005** per successful document |
| Page OCR'd (primary) | **$0.006** per page |

Failed downloads / unreadable files are **never** charged. RAG chunking does not add events.

Worked example: 1 document × 10 pages → $0.005 + 10 × $0.006 = **$0.065** (plus the synthetic start event).

## Known limits

- **Handwriting** is unreliable; this Actor targets print scans.
- **Japanese / Korean**: RapidOCR can return some text, but this release was **not** accuracy-tested on JP/KR corpora — treat as best-effort.
- Complex multi-column layouts may read top-to-bottom incorrectly.
- **Table assembly ≠ digital repair**: geometry from OCR boxes only; do not expect the PDF/DOCX Actor's word-index table quality.
- Very large / high-DPI PDFs need more memory and time (default run memory **2048 MB**; multipage LOC peaks observed ~1.1–1.4 GB RSS).
- Prefer the digital PDF/DOCX Actor when a text layer already exists.

## Related Actors / See also

- [PDF & DOCX to Markdown — Table Extraction & RAG Chunks](https://apify.com/ingenious_quip_bxq/pdf-docx-to-markdown) — prefer this for **digital** PDFs/DOCX with a text layer (better table repair). Use this OCR Actor when pages are scans/images.
- [Sitemap URL Extractor — PDF/DOCX Tags + robots.txt](https://apify.com/ingenious_quip_bxq/sitemap-url-discovery) — find PDF URLs on a site, then run OCR on image-only files.
- [Bulk URL Status Checker — 404s & Redirects](https://apify.com/ingenious_quip_bxq/url-status-checker) — optional: drop dead document URLs before OCR (OCR is slower/costlier per page).

## License & source code

This Actor is open source under the **GNU Affero General Public License v3.0 (AGPL-3.0)** — see `LICENSE`. The full source code is public: https://github.com/xbox002000/scanned-ocr-to-markdown

Third-party notices: `NOTICE`.
