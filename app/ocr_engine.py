from __future__ import annotations

from PIL import Image

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR

        _engine = RapidOCR()
    return _engine


def recognize(image: Image.Image) -> str:
    import numpy as np

    engine = get_engine()
    rgb = np.array(image.convert("RGB"))
    result = engine(rgb)
    txts = getattr(result, "txts", None)
    if not txts:
        return ""
    return "\n".join(str(t) for t in txts if t).strip()
