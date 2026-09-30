"""Rasterize scanned PDFs / load images → OCR → Markdown pages."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from layout import items_to_markdown
from ocr_engine import ocr_image

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif"}


@dataclass
class PageResult:
    number: int
    markdown: str
    stats: dict = field(default_factory=dict)


@dataclass
class ConvertResult:
    pages: list[PageResult]
    page_count: int
    format: str
    metadata: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _render_pdf_page(doc, page_index: int, dpi: int = 200):
    import numpy as np
    page = doc[page_index]
    zoom = dpi / 72.0
    mat = __import__("pymupdf").Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = img[:, :, :3]
    # RapidOCR / OpenCV expect BGR often; RapidOCR accepts RGB numpy too via Pillow path.
    return img


def convert_pdf(
    path: str,
    *,
    page_range: tuple[int, int] | None = None,
    dpi: int = 200,
    repair_tables: bool = True,
    password: str | None = None,
) -> ConvertResult:
    import pymupdf
    warnings: list[str] = []
    kwargs = {}
    if password:
        kwargs["password"] = password
    doc = pymupdf.open(path, **kwargs)
    try:
        if doc.needs_pass:
            if not password or not doc.authenticate(password):
                raise ValueError("PDF is encrypted; provide pdfPassword.")
        total = doc.page_count
        start, end = (page_range[0], page_range[1] or total) if page_range else (1, total)
        start = max(1, start)
        end = min(end, total)
        pages: list[PageResult] = []
        all_stats = {"ocrItems": 0, "tables": 0}
        for pno in range(start, end + 1):
            img = _render_pdf_page(doc, pno - 1, dpi=dpi)
            items = ocr_image(img)
            md, st = items_to_markdown(items, repair_tables=repair_tables)
            pages.append(PageResult(number=pno, markdown=md, stats=st))
            all_stats["ocrItems"] += st.get("ocrItems", 0)
            if st.get("tableCandidateLines", 0) >= 2:
                all_stats["tables"] += 1
        meta = {
            "title": (doc.metadata or {}).get("title") or None,
            "author": (doc.metadata or {}).get("author") or None,
            "dpi": dpi,
            "engine": "rapidocr+onnxruntime",
        }
        return ConvertResult(
            pages=pages,
            page_count=total,
            format="pdf",
            metadata=meta,
            stats=all_stats,
            warnings=warnings,
        )
    finally:
        doc.close()


def convert_image(path: str, *, repair_tables: bool = True) -> ConvertResult:
    import cv2
    img = cv2.imread(path)
    if img is None:
        # Pillow fallback for webp etc.
        from PIL import Image
        import numpy as np
        pil = Image.open(path).convert("RGB")
        img = np.array(pil)[:, :, ::-1]  # RGB → BGR
    items = ocr_image(img)
    md, st = items_to_markdown(items, repair_tables=repair_tables)
    tables = 1 if st.get("tableCandidateLines", 0) >= 2 else 0
    return ConvertResult(
        pages=[PageResult(number=1, markdown=md, stats=st)],
        page_count=1,
        format=Path(path).suffix.lower().lstrip(".") or "image",
        metadata={"engine": "rapidocr+onnxruntime", "source": "image"},
        stats={"ocrItems": st.get("ocrItems", 0), "tables": tables},
        warnings=[],
    )


def sniff_kind(path: str, head: bytes, name: str) -> str:
    low = name.lower()
    if head.startswith(b"%PDF") or b"%PDF" in head[:1024]:
        return "pdf"
    ext = Path(low).suffix
    if ext in IMAGE_EXTS:
        return "image"
    # magic
    if head.startswith(b"\x89PNG") or head[:3] == b"\xff\xd8\xff" or head[:4] == b"RIFF":
        return "image"
    if head.startswith(b"II*\x00") or head.startswith(b"MM\x00*"):
        return "image"
    raise ValueError(f"Unsupported file type for OCR. Supported: PDF, {', '.join(sorted(IMAGE_EXTS))}.")
