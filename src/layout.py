"""Turn OCR boxes into Markdown: reading-order lines + optional table assembly."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Line:
    text: str
    y: float
    x0: float
    x1: float
    score: float
    is_table_candidate: bool = False


def _center(box: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return min(xs), min(ys), max(xs), max(ys)


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def cluster_lines(items: list[dict], *, y_tol_ratio: float = 0.6) -> list[Line]:
    """Cluster OCR items into horizontal lines by Y proximity, left-to-right within line."""
    prepared = []
    heights = []
    for it in items:
        box = it.get("box")
        if not box or not it.get("text"):
            continue
        x0, y0, x1, y1 = _center(box)
        h = max(y1 - y0, 1.0)
        heights.append(h)
        prepared.append({
            "text": it["text"].strip(),
            "score": float(it.get("score") or 0),
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "yc": 0.5 * (y0 + y1), "h": h,
        })
    if not prepared:
        return []
    med_h = _median(heights) or 20.0
    y_tol = med_h * y_tol_ratio
    prepared.sort(key=lambda p: (p["yc"], p["x0"]))
    lines_raw: list[list[dict]] = []
    for p in prepared:
        if not lines_raw:
            lines_raw.append([p])
            continue
        last = lines_raw[-1]
        if abs(p["yc"] - _median([q["yc"] for q in last])) <= y_tol:
            last.append(p)
        else:
            lines_raw.append([p])
    out: list[Line] = []
    for group in lines_raw:
        group.sort(key=lambda q: q["x0"])
        text = " ".join(q["text"] for q in group if q["text"])
        if not text:
            continue
        gaps = [group[i + 1]["x0"] - group[i]["x1"] for i in range(len(group) - 1)]
        wide = sum(1 for g in gaps if g > med_h * 1.8)
        out.append(Line(
            text=text,
            y=_median([q["yc"] for q in group]),
            x0=group[0]["x0"],
            x1=group[-1]["x1"],
            score=sum(q["score"] for q in group) / len(group),
            is_table_candidate=wide >= 1 and len(group) >= 2,
        ))
    return out


def lines_to_markdown(lines: list[Line], *, repair_tables: bool = True) -> str:
    if not lines:
        return ""
    if not repair_tables:
        return "\n\n".join(ln.text for ln in lines)

    parts: list[str] = []
    i = 0
    while i < len(lines):
        if not lines[i].is_table_candidate:
            parts.append(lines[i].text)
            i += 1
            continue
        # Collect a run of table-ish lines
        run = []
        while i < len(lines) and (lines[i].is_table_candidate or (
            run and len(lines[i].text.split()) <= 8 and _similar_x_span(lines[i], run[0])
        )):
            run.append(lines[i])
            i += 1
            if len(run) >= 2 and i < len(lines) and not lines[i].is_table_candidate:
                # allow one non-candidate continuation only if still aligned
                break
        if len(run) >= 2:
            parts.append(_assemble_table(run))
        else:
            parts.extend(r.text for r in run)
    return "\n\n".join(p for p in parts if p.strip())


def _similar_x_span(a: Line, b: Line) -> bool:
    return abs(a.x0 - b.x0) < 40 and abs(a.x1 - b.x1) < 80


def _assemble_table(run: list[Line]) -> str:
    """Best-effort Markdown table: split each line on wide gaps already collapsed to spaces.

    Without per-cell boxes here we split on 2+ spaces or pipe chars from OCR.
    """
    rows = []
    for ln in run:
        cells = [c.strip() for c in _split_cells(ln.text) if c.strip()]
        if cells:
            rows.append(cells)
    if len(rows) < 2:
        return "\n\n".join(ln.text for ln in run)
    ncols = max(len(r) for r in rows)
    if ncols < 2:
        return "\n\n".join(ln.text for ln in run)
    norm = [r + [""] * (ncols - len(r)) for r in rows]
    header = norm[0]
    sep = ["---"] * ncols
    body = norm[1:]
    def fmt(r):
        return "| " + " | ".join(c.replace("\n", " ") for c in r) + " |"
    return "\n".join([fmt(header), fmt(sep)] + [fmt(r) for r in body])


def _split_cells(text: str) -> list[str]:
    if "|" in text:
        return [c.strip() for c in text.split("|")]
    import re
    parts = re.split(r"\s{2,}", text.strip())
    if len(parts) >= 2:
        return parts
    # Fallback: split on single spaces when many short tokens look columnar
    toks = text.split()
    if len(toks) >= 4 and sum(1 for t in toks if len(t) <= 8) >= len(toks) * 0.7:
        # pair into rough columns of ~2 tokens? keep as one cell — not enough signal
        return [text]
    return [text]


def items_to_markdown(items: list[dict], *, repair_tables: bool = True) -> tuple[str, dict]:
    lines = cluster_lines(items)
    md = lines_to_markdown(lines, repair_tables=repair_tables)
    stats = {
        "ocrLines": len(lines),
        "ocrItems": len(items),
        "avgScore": round(sum(i.get("score") or 0 for i in items) / len(items), 4) if items else 0,
        "tableCandidateLines": sum(1 for ln in lines if ln.is_table_candidate),
    }
    return md, stats
