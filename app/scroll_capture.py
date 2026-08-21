from __future__ import annotations

import ctypes
import time

import cv2
import mss
import numpy as np
import win32api
import win32con
import win32gui
from PIL import Image
from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

SCROLL_BAR_TITLE = "NiceShotScrollBar"
SCROLL_PREVIEW_TITLE = "NiceShotScrollPreview"
PREVIEW_WIDTH = 220
PREVIEW_MARGIN = 16
PREVIEW_MIN_WIDTH = 64
MAX_HEIGHT = 32000
SIDE_MARGIN = 16
MIN_BAND = 24
MIN_OVERLAP = 12
INSET_RATIO = 0.1
ROW_TOL = 10.0
ROW_STRIDE = 4
TICK_MS = 16
CAPTURE_HZ = 1000 / TICK_MS
PREVIEW_MS = 80
DELTA_GOOD = 1.8
DELTA_ACCEPT = 10.0
IDLE_DIFF = 5.5
MIN_GROW = 3
STRIP_TOL = 3.0
BLUR_RATIO = 0.5
WDA_EXCLUDEFROMCAPTURE = 0x00000011
_sct = None


def exclude_from_capture(widget: QWidget) -> None:
    try:
        ctypes.windll.user32.SetWindowDisplayAffinity(int(widget.winId()), WDA_EXCLUDEFROMCAPTURE)
    except Exception:
        pass


def _mss() -> mss.mss:
    global _sct
    if _sct is None:
        _sct = mss.mss()
    return _sct


def grab_region(rect: QRect) -> Image.Image:
    raw = _mss().grab(
        {
            "left": int(rect.x()),
            "top": int(rect.y()),
            "width": max(1, int(rect.width())),
            "height": max(1, int(rect.height())),
        }
    )
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def grab_region_bgr(rect: QRect) -> np.ndarray:
    raw = _mss().grab(
        {
            "left": int(rect.x()),
            "top": int(rect.y()),
            "width": max(1, int(rect.width())),
            "height": max(1, int(rect.height())),
        }
    )
    frame = np.frombuffer(raw.bgra, dtype=np.uint8).reshape(raw.height, raw.width, 4)
    return frame[:, :, :3].copy()


def _to_bgr(image: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)


def frame_sharpness(image: np.ndarray) -> float:
    left, right = _content_x(image.shape[1])
    inset = _inset(image.shape[0])
    body = image[inset : image.shape[0] - inset, left:right]
    if body.shape[0] < 8 or body.shape[1] < 8:
        body = image
    gray = cv2.cvtColor(body, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def frames_same(a: np.ndarray, b: np.ndarray, mean_diff: float = 1.8) -> bool:
    if a.shape != b.shape:
        return False
    return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16)))) < mean_diff


def frames_idle(a: np.ndarray, b: np.ndarray) -> bool:
    """到边界时画面几乎不动，但光标/滚动条会让 frames_same 失败。"""
    return frames_same(a, b, IDLE_DIFF)


def _mae(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16))))


def _verified_y(search: np.ndarray, template: np.ndarray) -> int | None:
    th, tw = template.shape[:2]
    if search.shape[0] < th or search.shape[1] < tw:
        return None
    sq = cv2.matchTemplate(search, template, cv2.TM_SQDIFF_NORMED)
    _max_val, min_val, min_loc, _max_loc = cv2.minMaxLoc(sq)
    y = min_loc[1]
    if min_val <= 0.08 or _mae(search[y : y + th, :tw], template) <= 12:
        return y
    return None


def _content_x(width: int) -> tuple[int, int]:
    right = max(SIDE_MARGIN + 8, width - SIDE_MARGIN)
    left = min(SIDE_MARGIN, right - 8)
    return left, right


def detect_scroll(prev: np.ndarray, curr: np.ndarray) -> tuple[str, np.ndarray | None]:
    """比较相邻两帧，判断上滚/下滚，并切出需要补上的新区域。"""
    if prev.shape[1] != curr.shape[1]:
        curr = cv2.resize(curr, (prev.shape[1], curr.shape[0]), interpolation=cv2.INTER_AREA)
    if frames_same(prev, curr):
        return "none", None

    h1, w1 = prev.shape[:2]
    h2 = curr.shape[0]
    left, right = _content_x(w1)
    prev_c = prev[:, left:right]
    curr_c = curr[:, left:right]
    downs: list[tuple[np.ndarray, int, int]] = []
    ups: list[tuple[np.ndarray, int, int]] = []

    for band in (120, 80, 48, MIN_BAND):
        band = min(band, h1 // 3, h2 // 3)
        if band < MIN_BAND:
            continue
        y_down = _verified_y(curr_c, prev_c[-band:])
        if y_down is not None:
            start = y_down + band
            extra = curr[start:] if start < h2 else None
            if extra is not None and extra.shape[0] > 0:
                downs.append((extra, y_down, band))
        y_up = _verified_y(curr_c, prev_c[:band])
        if y_up is not None and y_up > 0:
            extra = curr[:y_up]
            if extra.shape[0] > 0:
                ups.append((extra, y_up, band))

    best_down = max(downs, key=lambda item: item[0].shape[0], default=None)
    best_up = max(ups, key=lambda item: item[0].shape[0], default=None)

    if best_down and best_up:
        down_len, up_len = best_down[0].shape[0], best_up[0].shape[0]
        if up_len > down_len * 1.15:
            return "up", best_up[0]
        if down_len > up_len * 1.15:
            return "down", best_down[0]
        if best_down[1] <= best_up[1]:
            return "down", best_down[0]
        return "up", best_up[0]
    if best_down:
        return "down", best_down[0]
    if best_up:
        return "up", best_up[0]

    mid = h1 // 2
    band = min(60, h1 // 4, h2 // 4)
    if band >= MIN_BAND:
        y = _verified_y(curr_c, prev_c[mid : mid + band])
        if y is not None:
            shift = y - mid
            if shift > 2:
                extra = curr[:shift]
                if extra.shape[0] > 0:
                    return "up", extra
            if shift < -2:
                extra = curr[y + band :]
                if extra.shape[0] > 0:
                    return "down", extra
    return "none", None


def _inset(height: int) -> int:
    return min(max(8, int(height * INSET_RATIO)), max(8, height // 6))


def _col_profile(image: np.ndarray) -> np.ndarray:
    """取内容区三列灰度均值，避开左右边和顶底固定栏。"""
    h, w = image.shape[:2]
    left, right = _content_x(w)
    top = _inset(h)
    body = image[top : h - top, left:right]
    if body.shape[0] < MIN_OVERLAP or body.shape[1] < 8:
        body = image[:, left:right]
    color = body.astype(np.int16)
    bw = color.shape[1]
    spans = (
        (0, max(1, bw // 4)),
        (bw // 2, min(bw, (5 * bw) // 8)),
        ((6 * bw) // 8, min(bw, (7 * bw) // 8)),
    )
    cols = [color[:, a:b].mean(axis=1) for a, b in spans if b > a]
    return np.concatenate(cols, axis=1)


def _delta_error(prev: np.ndarray, curr: np.ndarray, delta: int) -> float:
    height = prev.shape[0]
    if delta >= 0:
        overlap = height - delta
        if overlap < MIN_OVERLAP:
            return 1e9
        return float(np.mean(np.abs(prev[delta:] - curr[:overlap])))
    overlap = height + delta
    if overlap < MIN_OVERLAP:
        return 1e9
    return float(np.mean(np.abs(prev[:overlap] - curr[-delta:])))


def _band_delta(prev: np.ndarray, curr: np.ndarray) -> int | None:
    """用上一帧中间内容条定位：找到的位置偏上=往下翻，偏下=往上翻。"""
    if prev.shape[1] != curr.shape[1]:
        curr = cv2.resize(curr, (prev.shape[1], curr.shape[0]), interpolation=cv2.INTER_AREA)
    left, right = _content_x(prev.shape[1])
    inset = _inset(prev.shape[0])
    prev_c = prev[inset : prev.shape[0] - inset, left:right]
    curr_c = curr[inset : curr.shape[0] - inset, left:right]
    if prev_c.shape[0] < MIN_BAND * 2 or curr_c.shape[0] < MIN_BAND * 2:
        prev_c, curr_c = prev[:, left:right], curr[:, left:right]
    h1, h2 = prev_c.shape[0], curr_c.shape[0]
    band = min(64, h1 // 4, h2 // 4)
    if band < MIN_BAND:
        return None
    votes: list[int] = []
    for frac in (0.28, 0.5, 0.72):
        y0 = min(max(0, int(h1 * frac) - band // 2), h1 - band)
        y = _verified_y(curr_c, prev_c[y0 : y0 + band])
        if y is not None:
            votes.append(y0 - y)
    if not votes:
        return None
    votes.sort()
    return votes[len(votes) // 2]


def find_scroll_delta(prev: np.ndarray, curr: np.ndarray, predict: int = 0) -> int | None:
    """有符号位移：正数=往下翻（往长图底下接），负数=往上翻（往顶上接）。"""
    if prev.shape[1] != curr.shape[1]:
        curr = cv2.resize(curr, (prev.shape[1], curr.shape[0]), interpolation=cv2.INTER_AREA)
    signed = _band_delta(prev, curr)
    prev_c = _col_profile(prev)
    curr_c = _col_profile(curr)
    if prev_c.shape[0] != curr_c.shape[0]:
        return signed
    height = prev_c.shape[0]
    max_off = max(1, height - MIN_OVERLAP)
    center = signed if signed is not None else int(np.clip(predict, -max_off, max_off))
    best_d = center
    best_err = _delta_error(prev_c, curr_c, center) if abs(center) <= max_off else 1e9
    for delta in range(center - 12, center + 13):
        if delta < -max_off or delta > max_off:
            continue
        err = _delta_error(prev_c, curr_c, delta)
        if err < best_err:
            best_err = err
            best_d = delta
    if best_err <= DELTA_ACCEPT:
        if signed is not None and best_d * signed < 0 and abs(signed) >= 3:
            return signed
        return best_d
    return signed


def _row_strip(image: np.ndarray, y: int) -> np.ndarray:
    left, right = _content_x(image.shape[1])
    return image[y, left:right:ROW_STRIDE].astype(np.int16)


def _row_mae_map(image: np.ndarray, needle: np.ndarray, y0: int, y1: int) -> np.ndarray:
    left, right = _content_x(image.shape[1])
    block = image[y0:y1, left:right:ROW_STRIDE].astype(np.int16)
    if block.size == 0 or needle.size == 0 or block.shape[1] != needle.shape[0]:
        return np.full(max(0, y1 - y0), 1e9, np.float64)
    return np.mean(np.abs(block - needle), axis=(1, 2))


def compare_row(left: np.ndarray, right: np.ndarray, y_left: int, y_right: int) -> float:
    """文章里的 CompareRow：比较两图指定行，返回平均色差。"""
    return float(np.mean(np.abs(_row_strip(left, y_left) - _row_strip(right, y_right))))


def _sticky_bands(prev: np.ndarray, curr: np.ndarray) -> tuple[int, int]:
    """相邻帧里位置不变的顶/底行数（吸顶栏、底栏）。整屏都没动则视为 0，交给 idle。"""
    if prev.shape[0] != curr.shape[0] or prev.shape[1] != curr.shape[1]:
        inset = _inset(min(prev.shape[0], curr.shape[0]))
        return inset, inset
    height = prev.shape[0]
    top = 0
    for y in range(height):
        if compare_row(prev, curr, y, y) <= ROW_TOL:
            top += 1
        else:
            break
    bot = 0
    for y in range(height - 1, -1, -1):
        if compare_row(prev, curr, y, y) <= ROW_TOL:
            bot += 1
        else:
            break
    if top + bot >= height - 2:
        return 0, 0
    limit = max(0, (height - MIN_OVERLAP) // 2)
    return min(top, limit), min(bot, limit)


def _body_view(image: np.ndarray, top: int, bot: int) -> np.ndarray:
    bot = min(bot, max(0, image.shape[0] - top - MIN_OVERLAP))
    top = min(top, max(0, image.shape[0] - MIN_OVERLAP))
    if top <= 0 and bot <= 0:
        return image
    return image[top : image.shape[0] - bot]


def _verify_overlap(base: np.ndarray, y0: int, nxt: np.ndarray, n0: int, height: int) -> float:
    if height < 1:
        return 1e9
    step = max(1, height // 8)
    diffs = [compare_row(base, nxt, y0 + k, n0 + k) for k in range(0, height, step)]
    return float(np.mean(diffs))


def _pick_overlap(hits: list[tuple[float, int]]) -> tuple[int | None, float]:
    if not hits:
        return None, 1e9
    best = min(score for score, _overlap in hits)
    overlap = max(h for score, h in hits if score <= best + 0.4)
    return overlap, best


def find_overlap_down(base: np.ndarray, nxt: np.ndarray) -> tuple[int | None, float]:
    """文章里的 FindOverlap：base 底部与 next 顶部重合多少行。"""
    view = base[-min(base.shape[0], nxt.shape[0]) :]
    vh, h2 = view.shape[0], nxt.shape[0]
    inset = min(_inset(h2), max(0, h2 // 8))
    errs = _row_mae_map(view, _row_strip(nxt, 0), 0, max(0, vh - MIN_OVERLAP + 1))
    hits: list[tuple[float, int]] = []
    for y in np.flatnonzero(errs <= ROW_TOL):
        y = int(y)
        if inset and y + inset < vh and inset < h2 and compare_row(view, nxt, y + inset, inset) > ROW_TOL:
            continue
        overlap = vh - y
        score = _verify_overlap(view, y, nxt, 0, min(overlap, 56))
        if score <= ROW_TOL:
            hits.append((score, overlap))
    return _pick_overlap(hits)


def find_overlap_up(base: np.ndarray, nxt: np.ndarray) -> tuple[int | None, float]:
    """对称：base 顶部与 next 底部重合多少行（往上滚）。"""
    view = base[: min(base.shape[0], nxt.shape[0])]
    vh, h2 = view.shape[0], nxt.shape[0]
    errs = _row_mae_map(nxt, _row_strip(view, 0), 0, max(0, h2 - MIN_OVERLAP + 1))
    hits: list[tuple[float, int]] = []
    for extra in np.flatnonzero(errs <= ROW_TOL):
        extra = int(extra)
        overlap = h2 - extra
        if overlap > vh:
            continue
        score = _verify_overlap(view, 0, nxt, extra, min(overlap, 56))
        if score <= ROW_TOL:
            hits.append((score, overlap))
    return _pick_overlap(hits)


def _origin_fit(canvas: np.ndarray, frame: np.ndarray, origin: int) -> float:
    src0 = max(0, -origin)
    dst0 = max(0, origin)
    overlap = min(frame.shape[0] - src0, canvas.shape[0] - dst0)
    if overlap < MIN_OVERLAP:
        return 1e9
    skip = min(_inset(frame.shape[0]), max(0, overlap // 5))
    return _verify_overlap(canvas, dst0 + skip, frame, src0 + skip, min(overlap - skip, 80))


def find_signed_delta(
    prev: np.ndarray,
    curr: np.ndarray,
    predict: int = 0,
    canvas: np.ndarray | None = None,
    last_origin: int = 0,
) -> tuple[int | None, float]:
    """相邻帧位移：正数往下翻，负数往上翻。固定顶/底栏不参与重叠。"""
    if prev.shape[1] != curr.shape[1]:
        curr = cv2.resize(curr, (prev.shape[1], curr.shape[0]), interpolation=cv2.INTER_AREA)
    height = min(prev.shape[0], curr.shape[0])
    top, bot = _sticky_bands(prev, curr)
    prev_b = _body_view(prev, top, bot)
    curr_b = _body_view(curr, top, bot)
    down_h, down_s = find_overlap_down(prev_b, curr_b)
    up_h, up_s = find_overlap_up(prev_b, curr_b)
    height = min(prev_b.shape[0], curr_b.shape[0])
    down_d = height - down_h if down_h is not None else None
    up_d = -(height - up_h) if up_h is not None else None
    if down_d is None and up_d is None:
        return None, 1e9
    if down_d is None:
        return up_d, up_s
    if up_d is None:
        return down_d, down_s
    if canvas is not None and abs(down_s - up_s) <= 1.2:
        down_fit = _origin_fit(canvas, curr, last_origin + down_d)
        up_fit = _origin_fit(canvas, curr, last_origin + up_d)
        if down_fit < up_fit:
            return down_d, down_s
        if up_fit < down_fit:
            return up_d, up_s
    if abs(down_s - up_s) <= 1.2:
        if abs(abs(down_d) - abs(up_d)) <= 8:
            if predict > 2:
                return down_d, down_s
            if predict < -2:
                return up_d, up_s
        if abs(down_d) <= abs(up_d):
            return down_d, down_s
        return up_d, up_s
    return (up_d, up_s) if up_s < down_s else (down_d, down_s)


def locate_frame(
    canvas: np.ndarray,
    frame: np.ndarray,
    hint_origin: int,
) -> tuple[int | None, float]:
    """翻太快、相邻帧对不上时，在长图里重定位当前屏。"""
    if canvas.shape[1] != frame.shape[1]:
        frame = cv2.resize(frame, (canvas.shape[1], frame.shape[0]), interpolation=cv2.INTER_AREA)
    h1, h2 = canvas.shape[0], frame.shape[0]
    inset = _inset(h2)
    probes = sorted(
        {
            p
            for p in (inset + 4, inset + 8, h2 // 2, max(inset, h2 - inset - 8), h2 - inset - 4)
            if inset <= p < h2 - max(0, inset - 1)
        }
    )
    ranges: list[tuple[int, int]] = []

    def add_range(a: int, b: int) -> None:
        a, b = max(0, a), min(h1, b)
        if b > a:
            ranges.append((a, b))

    add_range(hint_origin - 2 * h2, hint_origin + 3 * h2)
    add_range(0, 2 * h2)
    add_range(h1 - 2 * h2, h1)

    hits: list[tuple[float, int]] = []
    seen: set[int] = set()
    for probe in probes:
        needle = _row_strip(frame, probe)
        for lo, hi in ranges:
            errs = _row_mae_map(canvas, needle, lo, hi)
            for i in np.flatnonzero(errs <= ROW_TOL):
                origin = lo + int(i) - probe
                if origin in seen:
                    continue
                seen.add(origin)
                src0 = max(0, -origin)
                dst0 = max(0, origin)
                overlap = min(h2 - src0, h1 - dst0)
                if overlap < MIN_OVERLAP:
                    continue
                skip = min(inset, max(0, overlap // 4))
                score = _verify_overlap(
                    canvas, dst0 + skip, frame, src0 + skip, min(max(1, overlap - skip), 56)
                )
                if score <= ROW_TOL:
                    hits.append((score, origin))
    if not hits:
        return None, 1e9
    best = min(score for score, _origin in hits)
    good = [(score, origin) for score, origin in hits if score <= best + 0.6]
    origin = min(good, key=lambda item: abs(item[1] - hint_origin))[1]
    return origin, best


def _origin_from_canvas_edge(canvas: np.ndarray, frame: np.ndarray) -> tuple[int | None, float, int]:
    """相邻帧和长图内部都对不上时，再试长图顶/底（刚翻出已截范围）。"""
    down_h, down_s = find_overlap_down(canvas, frame)
    up_h, up_s = find_overlap_up(canvas, frame)
    down_o = canvas.shape[0] - down_h if down_h is not None else None
    up_o = (up_h - frame.shape[0]) if up_h is not None else None
    if down_o is None and up_o is None:
        return None, 1e9, 0
    if down_o is None:
        return up_o, up_s, up_o or 0
    if up_o is None:
        return down_o, down_s, down_o - (canvas.shape[0] - frame.shape[0])
    if down_s <= up_s:
        return down_o, down_s, down_o - (canvas.shape[0] - frame.shape[0])
    return up_o, up_s, up_o


def looks_like_render(prev: np.ndarray, curr: np.ndarray) -> bool:
    """上一帧还很空/糊，这一帧清楚很多，多半是 PDF 等页面刚渲染完。"""
    old = frame_sharpness(prev)
    new = frame_sharpness(curr)
    return new > old + 5 and (old < 100 or new > old * 1.25)


def refresh_viewport(
    canvas: np.ndarray,
    frame: np.ndarray,
    origin: int,
    top: int = 0,
    bot: int = 0,
) -> tuple[np.ndarray, bool]:
    """同一视口再截一次：内容加载完后覆盖长图上对应区域。固定栏不覆盖进正文。"""
    if canvas.shape[1] != frame.shape[1]:
        frame = cv2.resize(frame, (canvas.shape[1], frame.shape[0]), interpolation=cv2.INTER_AREA)
    src0 = max(0, -origin) + top
    dst0 = max(0, origin) + top
    copy_h = min(frame.shape[0] - bot - src0, canvas.shape[0] - bot - dst0)
    if copy_h < 12:
        return canvas, False
    old = canvas[dst0 : dst0 + copy_h]
    new = frame[src0 : src0 + copy_h]
    if frames_same(old, new):
        return canvas, False
    old_s = frame_sharpness(old)
    new_s = frame_sharpness(new)
    if new_s + 1 < old_s * 0.82:
        return canvas, False
    canvas[dst0 : dst0 + copy_h] = new
    return canvas, True


def _overlaps_canvas(canvas_h: int, frame_h: int, origin: int) -> bool:
    return origin < canvas_h and origin + frame_h > 0


def _fully_contained(
    canvas: np.ndarray,
    frame: np.ndarray,
    hint: int,
    located: int | None,
    skip_top: int = 0,
    skip_bot: int = 0,
) -> int | None:
    """当前屏的滚动内容是否已经完整落在长图里。固定顶/底栏不参与判断。"""
    h1, h2 = canvas.shape[0], frame.shape[0]
    if h1 < h2:
        return None
    skip_top = max(skip_top, _inset(h2))
    skip_bot = max(skip_bot, _inset(h2))
    body_h = h2 - skip_top - skip_bot
    if body_h < MIN_OVERLAP:
        return None
    max_inside = h1 - h2
    origins = {0, max_inside, int(np.clip(hint, 0, max_inside))}
    if located is not None and 0 <= located <= max_inside:
        origins.add(int(located))
    best_o, best_s = None, 1e9
    for origin in origins:
        score = _verify_overlap(canvas, origin + skip_top, frame, skip_top, min(body_h, 80))
        if score < best_s:
            best_s, best_o = score, origin
    if best_o is not None and best_s <= ROW_TOL:
        return best_o
    return None


def _strip_already_in_canvas(canvas: np.ndarray, extra: np.ndarray, side: str) -> bool:
    """拟新增条带是否已在长图顶/底（到边界后回弹、重复接）。"""
    eh = extra.shape[0]
    if eh < MIN_GROW:
        return True
    if eh > canvas.shape[0]:
        return False
    if side == "bottom":
        if _mae(canvas[-eh:], extra) <= STRIP_TOL:
            return True
        span = min(eh + 12, canvas.shape[0] - eh)
        for off in range(1, max(1, span)):
            if _mae(canvas[-eh - off : canvas.shape[0] - off], extra) <= STRIP_TOL:
                return True
        return False
    if _mae(canvas[:eh], extra) <= STRIP_TOL:
        return True
    span = min(eh + 12, canvas.shape[0] - eh)
    for off in range(1, max(1, span)):
        if _mae(canvas[off : off + eh], extra) <= STRIP_TOL:
            return True
    return False


def _chrome_flags(canvas: np.ndarray, frame: np.ndarray, top: int, bot: int) -> tuple[bool, bool]:
    has_header = top > 0 and canvas.shape[0] >= top and _mae(canvas[:top], frame[:top]) <= STRIP_TOL
    has_footer = bot > 0 and canvas.shape[0] >= bot and _mae(canvas[-bot:], frame[-bot:]) <= STRIP_TOL
    return has_header, has_footer


def _growth_pads(
    canvas: np.ndarray,
    frame: np.ndarray,
    origin: int,
    top: int,
    bot: int,
) -> tuple[int, int, bool, bool]:
    h1, h2 = canvas.shape[0], frame.shape[0]
    has_header, has_footer = _chrome_flags(canvas, frame, top, bot)
    body_start = top if has_header else 0
    body_end = h1 - bot if has_footer else h1
    pad_top = max(0, body_start - (origin + top))
    pad_bot = max(0, origin + h2 - bot - body_end)
    return pad_top, pad_bot, has_header, has_footer


def _refuse_duplicate_growth(
    canvas: np.ndarray,
    frame: np.ndarray,
    origin: int,
    last_origin: int,
    top: int = 0,
    bot: int = 0,
) -> tuple[int, int]:
    """要往外接时，先确认新条带不是已截内容。固定栏不拿来当新图。"""
    h2 = frame.shape[0]
    pad_top, pad_bot, has_header, has_footer = _growth_pads(canvas, frame, origin, top, bot)
    if pad_top < MIN_GROW and pad_bot < MIN_GROW:
        if pad_top or pad_bot:
            return last_origin, 0
        return origin, origin - last_origin
    if pad_bot >= MIN_GROW:
        extra = frame[h2 - bot - pad_bot : h2 - bot]
        check = canvas[:-bot] if has_footer and canvas.shape[0] > bot else canvas
        if extra.shape[0] >= MIN_GROW and _strip_already_in_canvas(check, extra, "bottom"):
            return last_origin, 0
    if pad_top >= MIN_GROW:
        extra = frame[top : top + pad_top]
        check = canvas[top:] if has_header and canvas.shape[0] > top else canvas
        if extra.shape[0] >= MIN_GROW and _strip_already_in_canvas(check, extra, "top"):
            return last_origin, 0
    return origin, origin - last_origin


def place_frame(
    canvas: np.ndarray,
    frame: np.ndarray,
    origin: int,
    top: int = 0,
    bot: int = 0,
) -> tuple[np.ndarray, str, int]:
    """按垂直坐标把当前帧贴进长图；固定顶/底栏只保留一份，只补滚动内容。"""
    if canvas.shape[1] != frame.shape[1]:
        frame = cv2.resize(frame, (canvas.shape[1], frame.shape[0]), interpolation=cv2.INTER_AREA)
    h1, h2 = canvas.shape[0], frame.shape[0]
    pad_top, pad_bot, has_header, has_footer = _growth_pads(canvas, frame, origin, top, bot)
    if pad_top < MIN_GROW and pad_bot < MIN_GROW:
        return canvas, "none", origin
    if pad_bot >= MIN_GROW:
        extra = frame[h2 - bot - pad_bot : h2 - bot]
        if extra.shape[0] >= MIN_GROW:
            if has_footer:
                out = np.concatenate([canvas[:-bot], extra, canvas[-bot:]], axis=0)
            else:
                out = np.concatenate([canvas, extra], axis=0)
            return out, "down", origin
    if pad_top >= MIN_GROW:
        extra = frame[top : top + pad_top]
        if extra.shape[0] >= MIN_GROW:
            if has_header:
                out = np.concatenate([canvas[:top], extra, canvas[top:]], axis=0)
                return out, "up", origin + pad_top
            out = np.concatenate([extra, canvas], axis=0)
            return out, "up", origin + pad_top
    return canvas, "none", origin


def stitch_vertical(
    canvas: np.ndarray,
    frame: np.ndarray,
    prev_frame: np.ndarray | None = None,
    last_origin: int = 0,
    last_delta: int = 0,
) -> tuple[np.ndarray, str, int, int]:
    """相邻帧逐行求位移，按坐标只补超出已截范围的像素；来回翻不重复接。"""
    if canvas.shape[1] != frame.shape[1]:
        frame = cv2.resize(frame, (canvas.shape[1], frame.shape[0]), interpolation=cv2.INTER_AREA)
    top = bot = 0
    if prev_frame is not None:
        top, bot = _sticky_bands(prev_frame, frame)
    if prev_frame is not None and (frames_same(prev_frame, frame) or frames_idle(prev_frame, frame)):
        canvas, changed = refresh_viewport(canvas, frame, last_origin, top, bot)
        return canvas, ("refresh" if changed else "none"), last_origin, 0

    origin = last_origin
    delta = 0
    dscore = 1e9
    if prev_frame is not None:
        signed, dscore = find_signed_delta(prev_frame, frame, last_delta, canvas, last_origin)
        if signed is not None:
            origin = last_origin + signed
            delta = signed

    located, lscore = locate_frame(canvas, frame, last_origin)
    if located is not None:
        predicted = last_origin + delta if dscore < 1e8 else last_origin
        if dscore >= 1e8:
            origin = located
            delta = located - last_origin
        elif abs(located - predicted) > 8 and lscore + 0.8 < dscore:
            origin = located
            delta = located - last_origin

    contained = _fully_contained(canvas, frame, last_origin, located, top, bot)
    if contained is not None and abs(delta) < MIN_GROW:
        canvas, changed = refresh_viewport(canvas, frame, contained, top, bot)
        return canvas, ("refresh" if changed else "none"), contained, 0

    if dscore >= 1e8 and located is None:
        edge_o, _edge_s, edge_d = _origin_from_canvas_edge(canvas, frame)
        if edge_o is None:
            canvas, changed = refresh_viewport(canvas, frame, last_origin, top, bot)
            return canvas, ("refresh" if changed else "none"), last_origin, last_delta
        origin, delta = edge_o, edge_d

    if not _overlaps_canvas(canvas.shape[0], frame.shape[0], origin):
        canvas, changed = refresh_viewport(canvas, frame, last_origin, top, bot)
        return canvas, ("refresh" if changed else "none"), last_origin, last_delta

    origin, delta = _refuse_duplicate_growth(canvas, frame, origin, last_origin, top, bot)

    rendering = prev_frame is not None and looks_like_render(prev_frame, frame)
    if rendering and abs(delta) < 8:
        canvas, changed = refresh_viewport(canvas, frame, last_origin, top, bot)
        return canvas, ("refresh" if changed else "none"), last_origin, last_delta
    if abs(delta) < 2:
        canvas, changed = refresh_viewport(canvas, frame, origin, top, bot)
        return canvas, ("refresh" if changed else "none"), origin, 0

    merged, direction, new_origin = place_frame(canvas, frame, origin, top, bot)
    merged, refreshed = refresh_viewport(merged, frame, new_origin, top, bot)
    if direction == "none" and refreshed:
        direction = "refresh"
    return merged, direction, new_origin, delta


def focus_window_at(point: QPoint) -> None:
    hwnd = win32gui.WindowFromPoint((point.x(), point.y()))
    if not hwnd:
        return
    root = win32gui.GetAncestor(hwnd, win32con.GA_ROOT) or hwnd
    title = win32gui.GetWindowText(root)
    if title in {SCROLL_BAR_TITLE, SCROLL_PREVIEW_TITLE, "NiceShotOverlay", "NiceShotHost"}:
        return
    try:
        ctypes.windll.user32.AllowSetForegroundWindow(-1)
        win32gui.SetForegroundWindow(root)
    except Exception:
        pass


def wheel_at(point: QPoint, steps: int = -4) -> None:
    focus_window_at(point)
    old = win32api.GetCursorPos()
    win32api.SetCursorPos((point.x(), point.y()))
    win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, steps * 120, 0)
    win32api.SetCursorPos(old)


class ScrollPreview(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(SCROLL_PREVIEW_TITLE)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background: rgba(20, 20, 20, 230);")
        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignCenter)
        self._pad = 6
        self._avoid = QRect()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        exclude_from_capture(self)

    def set_canvas(self, canvas: np.ndarray, screen: QRect, avoid: QRect | None = None) -> None:
        h, w = canvas.shape[:2]
        if w < 1 or h < 1:
            return
        max_h = max(80, screen.height() - PREVIEW_MARGIN * 2)
        scale = PREVIEW_WIDTH / w
        pw = PREVIEW_WIDTH
        ph = max(1, int(round(h * scale)))
        if ph > max_h:
            scale = max_h / h
            pw = max(PREVIEW_MIN_WIDTH, int(round(w * scale)))
            ph = max_h
        resized = cv2.resize(canvas, (pw, ph), interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, pw, ph, rgb.strides[0], QImage.Format.Format_RGB888).copy()
        pix = QPixmap.fromImage(qimg)
        self._label.setPixmap(pix)
        self._label.setGeometry(self._pad, self._pad, pw, ph)
        self.resize(pw + self._pad * 2, ph + self._pad * 2)
        if avoid is not None:
            self._avoid = QRect(avoid)
        box = self.size()
        margin = PREVIEW_MARGIN
        candidates = [
            QPoint(screen.right() - box.width() - margin, screen.bottom() - box.height() - margin),
            QPoint(screen.right() - box.width() - margin, screen.top() + margin),
            QPoint(screen.left() + margin, screen.bottom() - box.height() - margin),
            QPoint(screen.left() + margin, screen.top() + margin),
        ]
        placed = False
        for pos in candidates:
            if not QRect(pos, box).intersects(self._avoid):
                self.move(pos)
                placed = True
                break
        if not placed:
            self.move(candidates[0])
        self.show()
        self.raise_()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(20, 20, 20, 230))
        painter.setPen(QPen(QColor(51, 112, 255), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)


class ScrollCapturePanel(QWidget):
    finished = Signal(object)
    cancelled = Signal()

    def __init__(self, region: QRect) -> None:
        super().__init__()
        self._region = QRect(region)
        self._canvas: np.ndarray | None = None
        self._last: np.ndarray | None = None
        self._frame_origin = 0
        self._last_delta = 0
        self._last_sharp = 0.0
        self._same_count = 0
        self._auto = False
        self._auto_ticks = 0
        self._busy = False
        self._preview_dirty = False
        self._preview_at = 0.0

        self.setWindowTitle(SCROLL_BAR_TITLE)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            """
            ScrollCapturePanel {
                background: rgba(32, 32, 32, 235);
                border-radius: 8px;
            }
            QLabel { color: #f2f2f2; font-size: 13px; }
            QPushButton {
                color: #f2f2f2;
                background: rgba(255, 255, 255, 20);
                border: none;
                padding: 6px 12px;
                border-radius: 6px;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 36); }
            QPushButton#doneBtn { background: #26c666; color: white; }
            QPushButton#doneBtn:hover { background: #34d676; }
            QPushButton#cancelBtn { background: #e85454; color: white; }
            QPushButton#cancelBtn:hover { background: #f46c6c; }
            """
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)
        self.hint = QLabel("在挖空选区内滚动，仅拼接超出已截范围的新内容")
        font = QFont()
        font.setPixelSize(13)
        self.hint.setFont(font)
        self.auto_btn = QPushButton("自动滚动")
        self.done_btn = QPushButton("完成")
        self.done_btn.setObjectName("doneBtn")
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("cancelBtn")
        self.auto_btn.clicked.connect(self._toggle_auto)
        self.done_btn.clicked.connect(self._finish)
        self.cancel_btn.clicked.connect(self._cancel)
        layout.addWidget(self.hint)
        layout.addWidget(self.auto_btn)
        layout.addWidget(self.done_btn)
        layout.addWidget(self.cancel_btn)
        self.adjustSize()
        self._overlap = False
        self._preview = ScrollPreview()
        self._place()

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        exclude_from_capture(self)

    def start(self) -> None:
        self.show()
        self.raise_()
        QTimer.singleShot(180, self._begin)

    def _begin(self) -> None:
        first = grab_region_bgr(self._region)
        self._last = first
        self._canvas = first.copy()
        self._frame_origin = 0
        self._last_delta = 0
        self._last_sharp = frame_sharpness(first)
        self._preview_dirty = False
        self._preview_at = 0.0
        self._update_hint("none")
        self._refresh_preview()
        self._timer.start()

    def _place(self) -> None:
        self.adjustSize()
        gap = 10
        virtual = QRect()
        for screen in QGuiApplication.screens():
            virtual = virtual.united(screen.geometry())
        tw, th = self.width(), self.height()
        candidates = [
            QPoint(self._region.right() - tw + 1, self._region.top() - th - gap),
            QPoint(self._region.left(), self._region.top() - th - gap),
            QPoint(self._region.right() - tw + 1, self._region.bottom() + gap),
            QPoint(self._region.left(), self._region.bottom() + gap),
            QPoint(self._region.left() - tw - gap, self._region.top()),
            QPoint(self._region.right() + gap, self._region.top()),
            QPoint(virtual.right() - tw - 12, virtual.top() + 12),
        ]
        for pos in candidates:
            bar = QRect(pos, self.sizeHint() if self.size().isEmpty() else self.size())
            if virtual.contains(bar) and not bar.intersects(self._region):
                self.move(pos)
                self._overlap = False
                return
        self.move(max(virtual.left(), virtual.right() - tw - 12), virtual.top() + 12)
        self._overlap = QRect(self.pos(), self.size()).intersects(self._region)

    def _center(self) -> QPoint:
        return self._region.center()

    def _toggle_auto(self) -> None:
        self._auto = not self._auto
        self.auto_btn.setText("停止滚动" if self._auto else "自动滚动")
        if self._auto:
            wheel_at(self._center())

    def _tick(self) -> None:
        if self._busy or self._canvas is None:
            return
        self._busy = True
        try:
            self._tick_body()
        finally:
            self._busy = False

    def _tick_body(self) -> None:
        frame = grab_region_bgr(self._region)
        if self._last is not None and (frames_same(self._last, frame) or frames_idle(self._last, frame)):
            self._same_count += 1
            if self._canvas is not None and self._same_count >= 3:
                h2 = frame.shape[0]
                at_bottom = self._frame_origin + h2 >= self._canvas.shape[0] - 2
                at_top = self._frame_origin <= 2
                if at_bottom or at_top:
                    side = "底部" if at_bottom else "顶部"
                    self.hint.setText(f"已到滚动{side}，继续翻不会再接图，可点「完成」")
            if self._auto:
                if self._same_count >= 8:
                    self._auto = False
                    self.auto_btn.setText("自动滚动")
                    self.hint.setText("似乎已到边界，可点「完成」")
                elif self._same_count >= 4:
                    self._auto_ticks += 1
                    if self._auto_ticks % 2 == 0:
                        wheel_at(self._center())
            return

        sharp = frame_sharpness(frame)
        merged, direction, origin, delta = stitch_vertical(
            self._canvas, frame, self._last, self._frame_origin, self._last_delta
        )
        if direction in {"up", "down"} and self._last_sharp > 20 and sharp < self._last_sharp * BLUR_RATIO:
            return
        self._last = frame
        self._same_count = 0
        grew = merged.shape[0] != self._canvas.shape[0]
        refreshed = direction == "refresh"
        if grew or refreshed or origin != self._frame_origin:
            self._frame_origin = origin
            if delta != 0:
                self._last_delta = delta
            self._last_sharp = max(sharp, self._last_sharp * 0.85)
        if merged.shape[0] > MAX_HEIGHT:
            self._canvas = merged[:MAX_HEIGHT] if direction != "down" else merged[-MAX_HEIGHT:]
            self._timer.stop()
            self._auto = False
            self.hint.setText(f"已达最大高度 {MAX_HEIGHT}px，请点完成")
            self._refresh_preview()
            return
        self._canvas = merged
        if grew or refreshed:
            self._update_hint(direction)
            self._preview_dirty = True
            now = time.monotonic()
            if now - self._preview_at >= PREVIEW_MS / 1000:
                self._refresh_preview()
                self._preview_at = now
                self._preview_dirty = False
        elif self._preview_dirty and time.monotonic() - self._preview_at >= PREVIEW_MS / 1000:
            self._refresh_preview()
            self._preview_at = time.monotonic()
            self._preview_dirty = False
        if self._auto:
            self._auto_ticks += 1
            if self._auto_ticks % 3 == 0:
                wheel_at(self._center())

    def _preview_screen(self) -> QRect:
        screen = QGuiApplication.screenAt(self._region.center())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)

    def _refresh_preview(self) -> None:
        if self._canvas is None:
            return
        self._preview.set_canvas(self._canvas, self._preview_screen(), self._region)

    def _update_hint(self, direction: str = "none") -> None:
        if self._canvas is None:
            return
        h = self._canvas.shape[0]
        screens = max(1, round(h / max(1, self._region.height())))
        way = {
            "up": "向上补图",
            "down": "向下补图",
            "refresh": "当前屏已更新（页面刚加载完）",
        }.get(direction, "继续上下滚动")
        self.hint.setText(f"已拼接约 {screens} 屏 · {h} px，{way}")

    def _finish(self) -> None:
        if self._canvas is None:
            self._cancel()
            return
        self._timer.stop()
        rgb = cv2.cvtColor(self._canvas, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        self.finished.emit(image)
        self._preview.close()
        self.close()

    def _cancel(self) -> None:
        self._timer.stop()
        self.cancelled.emit()
        self._preview.close()
        self.close()

    def closeEvent(self, event) -> None:
        self._timer.stop()
        self._preview.close()
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._cancel()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._finish()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton:
            self._cancel()
            return
        super().mousePressEvent(event)
