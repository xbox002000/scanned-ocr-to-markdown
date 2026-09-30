# Changelog

## 0.1.0 — 2026-09-30

- Initial private MVP: scanned PDF / images → Markdown via RapidOCR + onnxruntime.
- Optional RAG chunking (same field names as the digital PDF/DOCX Actor) and geometry-based table assembly from OCR boxes.
- PPE: `document-ocrd` $0.005, `page-ocrd` $0.006 (raised after multi-page LOC cloud cost measurement), default memory 2048 MB.
