from __future__ import annotations

from PIL import Image

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR

        _engine = RapidOCR()
    return _engine


def _box_geom(box) -> tuple[float, float, float, float]:
    ys = [float(pt[1]) for pt in box]
    xs = [float(pt[0]) for pt in box]
    return min(xs), max(xs), min(ys), max(ys)


def _same_row(left, right) -> bool:
    overlap = min(left[3], right[3]) - max(left[2], right[2])
    short = min(left[3] - left[2], right[3] - right[2], 12.0)
    return overlap > short * 0.35


def _join_ocr_lines(txts, boxes) -> str:
    items: list[tuple[float, float, float, float, str]] = []
    for text, box in zip(txts, boxes):
        if not text:
            continue
        x0, x1, y0, y1 = _box_geom(box)
        items.append((x0, x1, y0, y1, str(text)))
    if not items:
        return ""
    items.sort(key=lambda item: ((item[2] + item[3]) / 2, item[0]))
    rows: list[list[tuple[float, float, float, float, str]]] = []
    for item in items:
        placed = False
        for row in rows:
            if any(_same_row(item, other) for other in row):
                row.append(item)
                placed = True
                break
        if not placed:
            rows.append([item])
    rows.sort(key=lambda row: sum((p[2] + p[3]) / 2 for p in row) / len(row))
    lines = []
    for row in rows:
        row.sort(key=lambda item: item[0])
        lines.append(" ".join(item[4] for item in row))
    return "\n".join(lines).strip()


def recognize(image: Image.Image) -> str:
    import numpy as np

    engine = get_engine()
    rgb = np.array(image.convert("RGB"))
    result = engine(rgb)
    txts = getattr(result, "txts", None)
    if not txts:
        return ""
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return " ".join(str(t) for t in txts if t).strip()
    return _join_ocr_lines(txts, boxes)
