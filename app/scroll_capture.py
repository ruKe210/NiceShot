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
MIN_OVERLAP = 24
INSET_RATIO = 0.1
ROW_TOL = 10.0
TICK_MS = 32
PREVIEW_MS = 80
DELTA_GOOD = 1.8
DELTA_ACCEPT = 10.0
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
    return image[y, left:right].astype(np.int16)


def compare_row(left: np.ndarray, right: np.ndarray, y_left: int, y_right: int) -> float:
    """文章里的 CompareRow：比较两图指定行，返回平均色差。"""
    return float(np.mean(np.abs(_row_strip(left, y_left) - _row_strip(right, y_right))))


def _verify_overlap(base: np.ndarray, y0: int, nxt: np.ndarray, n0: int, height: int) -> float:
    if height < 1:
        return 1e9
    step = max(1, height // 8)
    diffs = [compare_row(base, nxt, y0 + k, n0 + k) for k in range(0, height, step)]
    return float(np.mean(diffs))


def find_overlap_down(base: np.ndarray, nxt: np.ndarray) -> tuple[int | None, float]:
    """文章里的 FindOverlap：base 底部与 next 顶部重合多少行。"""
    view = base[-min(base.shape[0], nxt.shape[0]) :]
    vh, h2 = view.shape[0], nxt.shape[0]
    inset = min(_inset(h2), max(0, h2 // 8))
    hits: list[tuple[float, int]] = []
    for y in range(0, vh - MIN_OVERLAP + 1):
        if compare_row(view, nxt, y, 0) > ROW_TOL:
            continue
        if inset and y + inset < vh and inset < h2 and compare_row(view, nxt, y + inset, inset) > ROW_TOL:
            continue
        overlap = vh - y
        score = _verify_overlap(view, y, nxt, 0, min(overlap, 56))
        if score <= ROW_TOL:
            hits.append((score, overlap))
    if not hits:
        return None, 1e9
    best = min(score for score, _overlap in hits)
    overlap = max(h for score, h in hits if score <= best + 0.4)
    return overlap, best


def find_overlap_up(base: np.ndarray, nxt: np.ndarray) -> tuple[int | None, float]:
    """对称：base 顶部与 next 底部重合多少行（往上滚）。"""
    view = base[: min(base.shape[0], nxt.shape[0])]
    vh, h2 = view.shape[0], nxt.shape[0]
    hits: list[tuple[float, int]] = []
    for extra in range(0, h2 - MIN_OVERLAP + 1):
        overlap = h2 - extra
        if overlap > vh:
            continue
        if compare_row(view, nxt, 0, extra) > ROW_TOL:
            continue
        score = _verify_overlap(view, 0, nxt, extra, min(overlap, 56))
        if score <= ROW_TOL:
            hits.append((score, overlap))
    if not hits:
        return None, 1e9
    best = min(score for score, _overlap in hits)
    overlap = max(h for score, h in hits if score <= best + 0.4)
    return overlap, best


def combine_down(base: np.ndarray, nxt: np.ndarray, overlap: int) -> tuple[np.ndarray, str]:
    """文章里的 CombineImages：next 画在 (0, baseH - overlap)。"""
    extra = nxt.shape[0] - overlap
    if extra <= 1:
        out = base.copy()
        top = max(0, base.shape[0] - nxt.shape[0])
        out[top : top + nxt.shape[0]] = nxt
        return out, "refresh"
    out = np.empty((base.shape[0] + extra, base.shape[1], base.shape[2]), base.dtype)
    out[: base.shape[0]] = base
    out[base.shape[0] - overlap :] = nxt
    return out, "down"


def combine_up(base: np.ndarray, nxt: np.ndarray, overlap: int) -> tuple[np.ndarray, str]:
    extra = nxt.shape[0] - overlap
    if extra <= 1:
        out = base.copy()
        out[: nxt.shape[0]] = nxt[: min(nxt.shape[0], out.shape[0])]
        return out, "refresh"
    out = np.empty((base.shape[0] + extra, base.shape[1], base.shape[2]), base.dtype)
    out[extra:] = base
    out[: nxt.shape[0]] = nxt
    return out, "up"


def looks_like_render(prev: np.ndarray, curr: np.ndarray) -> bool:
    """上一帧还很空/糊，这一帧清楚很多，多半是 PDF 等页面刚渲染完。"""
    old = frame_sharpness(prev)
    new = frame_sharpness(curr)
    return new > old + 5 and (old < 100 or new > old * 1.25)


def refresh_viewport(
    canvas: np.ndarray,
    frame: np.ndarray,
    origin: int,
) -> tuple[np.ndarray, bool]:
    """同一视口再截一次：内容加载完后覆盖长图上对应区域。"""
    if canvas.shape[1] != frame.shape[1]:
        frame = cv2.resize(frame, (canvas.shape[1], frame.shape[0]), interpolation=cv2.INTER_AREA)
    src0 = max(0, -origin)
    dst0 = max(0, origin)
    copy_h = min(frame.shape[0] - src0, canvas.shape[0] - dst0)
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


def place_frame(
    canvas: np.ndarray,
    frame: np.ndarray,
    origin: int,
) -> tuple[np.ndarray, str, int]:
    """按垂直坐标把当前帧贴进长图，只补超出已覆盖区间的像素。"""
    if canvas.shape[1] != frame.shape[1]:
        frame = cv2.resize(frame, (canvas.shape[1], frame.shape[0]), interpolation=cv2.INTER_AREA)
    h1, h2 = canvas.shape[0], frame.shape[0]
    frame_bot = origin + h2
    pad_top = max(0, -origin)
    pad_bot = max(0, frame_bot - h1)
    if pad_top == 0 and pad_bot == 0:
        return canvas, "none", origin
    out = np.empty((h1 + pad_top + pad_bot, canvas.shape[1], canvas.shape[2]), canvas.dtype)
    if pad_top:
        out[:pad_top] = frame[:pad_top]
    out[pad_top : pad_top + h1] = canvas
    if pad_bot:
        src = h1 - origin
        out[pad_top + h1 :] = frame[src : src + pad_bot]
    direction = "up" if pad_top else "down"
    return out, direction, origin + pad_top


def stitch_vertical(
    canvas: np.ndarray,
    frame: np.ndarray,
    prev_frame: np.ndarray | None = None,
    last_origin: int = 0,
    last_delta: int = 0,
) -> tuple[np.ndarray, str, int, int]:
    """按知乎文：逐行找重叠高度，再 CombineImages 接到顶或底。"""
    if canvas.shape[1] != frame.shape[1]:
        frame = cv2.resize(frame, (canvas.shape[1], frame.shape[0]), interpolation=cv2.INTER_AREA)
    if frames_same(canvas[-min(canvas.shape[0], frame.shape[0]) :], frame) or (
        canvas.shape[0] >= frame.shape[0] and frames_same(canvas[: frame.shape[0]], frame)
    ):
        canvas, changed = refresh_viewport(canvas, frame, last_origin)
        return canvas, ("refresh" if changed else "none"), last_origin, 0

    down_h, down_s = find_overlap_down(canvas, frame)
    up_h, up_s = find_overlap_up(canvas, frame)
    use_up = False
    if down_h is None and up_h is None:
        canvas, changed = refresh_viewport(canvas, frame, last_origin)
        return canvas, ("refresh" if changed else "none"), last_origin, last_delta
    if down_h is None:
        use_up = True
    elif up_h is None:
        use_up = False
    elif abs(down_s - up_s) <= 1.2:
        if last_delta < 0:
            use_up = True
        elif last_delta > 0:
            use_up = False
        else:
            use_up = up_h > down_h
    else:
        use_up = up_s < down_s

    overlap = up_h if use_up else down_h
    if overlap is None:
        return canvas, "none", last_origin, last_delta
    extra = frame.shape[0] - overlap
    if extra <= 1:
        merged, direction = combine_up(canvas, frame, overlap) if use_up else combine_down(canvas, frame, overlap)
        origin = 0 if use_up else max(0, merged.shape[0] - frame.shape[0])
        return merged, direction, origin, 0
    if use_up:
        merged, direction = combine_up(canvas, frame, overlap)
        return merged, direction, 0, -extra
    merged, direction = combine_down(canvas, frame, overlap)
    return merged, direction, merged.shape[0] - frame.shape[0], extra


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
        if self._last is not None and frames_same(self._last, frame):
            self._same_count += 1
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
        grew = merged.shape[0] != self._canvas.shape[0]
        refreshed = direction == "refresh"
        if grew or refreshed or origin != self._frame_origin:
            self._last = frame
            self._frame_origin = origin
            self._last_delta = delta
            self._last_sharp = max(sharp, self._last_sharp * 0.85)
            self._same_count = 0
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
