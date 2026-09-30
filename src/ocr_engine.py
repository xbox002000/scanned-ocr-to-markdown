"""RapidOCR wrapper — lazy singleton, CPU onnxruntime."""
from __future__ import annotations

from typing import Any

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR
        _engine = RapidOCR()
    return _engine


def ocr_image(img) -> list[dict[str, Any]]:
    """Run OCR on a numpy BGR/RGB array or path. Returns list of {box, text, score}.

    RapidOCR result shapes vary by version; normalize to a stable list.
    """
    engine = get_engine()
    result = engine(img)
    return _normalize(result)


def _normalize(result) -> list[dict[str, Any]]:
    if result is None:
        return []
    # RapidOCR >=3 may return an object with .txts / .boxes / .scores
    boxes = texts = scores = None
    if hasattr(result, "boxes") and hasattr(result, "txts"):
        boxes = list(result.boxes) if result.boxes is not None else []
        texts = list(result.txts) if result.txts is not None else []
        scores = list(result.scores) if getattr(result, "scores", None) is not None else [1.0] * len(texts)
    elif isinstance(result, (list, tuple)):
        # Older / alternate: ([box, text, score], ...) or (result_list, elapse)
        payload = result[0] if result and isinstance(result[0], list) and result and not _looks_like_item(result[0]) else result
        if payload is None:
            return []
        out = []
        for item in payload:
            if item is None:
                continue
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                box, text = item[0], item[1]
                score = float(item[2]) if len(item) > 2 else 1.0
                out.append({"box": _box_list(box), "text": str(text), "score": score})
        return out
    else:
        return []

    out = []
    for i, text in enumerate(texts):
        box = boxes[i] if i < len(boxes) else None
        score = float(scores[i]) if i < len(scores) else 1.0
        out.append({"box": _box_list(box), "text": str(text), "score": score})
    return out


def _looks_like_item(x) -> bool:
    return isinstance(x, (list, tuple)) and len(x) >= 2 and not isinstance(x[0], (list, tuple))


def _box_list(box) -> list[list[float]] | None:
    if box is None:
        return None
    try:
        import numpy as np
        arr = np.asarray(box, dtype=float)
        if arr.ndim == 2 and arr.shape[0] >= 4:
            return arr.reshape(-1, 2).tolist()
        if arr.size >= 8:
            return arr.reshape(-1, 2).tolist()
    except Exception:  # noqa: BLE001
        pass
    return None
