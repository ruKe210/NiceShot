from __future__ import annotations

import ctypes

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
WDA_EXCLUDEFROMCAPTURE = 0x00000011


def exclude_from_capture(widget: QWidget) -> None:
    try:
        ctypes.windll.user32.SetWindowDisplayAffinity(int(widget.winId()), WDA_EXCLUDEFROMCAPTURE)
    except Exception:
        pass


def grab_region(rect: QRect) -> Image.Image:
    monitor = {
        "left": int(rect.x()),
        "top": int(rect.y()),
        "width": max(1, int(rect.width())),
        "height": max(1, int(rect.height())),
    }
    with mss.mss() as sct:
        raw = sct.grab(monitor)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def _to_bgr(image: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)


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
    _max_val, _min_ignored, min_loc, _max_loc = cv2.minMaxLoc(sq)
    y = min_loc[1]
    if _mae(search[y : y + th, :tw], template) <= 12:
        return y
    coeff = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
    _mn, _mx, _ml, max_loc = cv2.minMaxLoc(coeff)
    y2 = max_loc[1]
    if _mae(search[y2 : y2 + th, :tw], template) <= 12:
        return y2
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


def _band_starts(height: int, band: int) -> list[int]:
    if height < band:
        return []
    starts = [
        0,
        max(0, height // 5),
        max(0, height // 2 - band // 2),
        max(0, (height * 4) // 5 - band),
        height - band,
    ]
    out: list[int] = []
    for y in starts:
        y = min(max(0, y), height - band)
        if y not in out:
            out.append(y)
    return out


def _agree_origin(votes: list[int]) -> int | None:
    if not votes:
        return None
    best: list[int] = []
    for vote in votes:
        cluster = [item for item in votes if abs(item - vote) <= 2]
        if len(cluster) > len(best):
            best = cluster
    if len(votes) >= 3 and len(best) < 2:
        return None
    return int(round(sum(best) / len(best)))


def find_frame_origin(
    canvas: np.ndarray,
    frame: np.ndarray,
    hint: int | None = None,
) -> int | None:
    """当前帧顶边在长图坐标系里的 Y。负数表示比已有内容更靠上。"""
    if canvas.shape[1] != frame.shape[1]:
        frame = cv2.resize(frame, (canvas.shape[1], frame.shape[0]), interpolation=cv2.INTER_AREA)
    left, right = _content_x(canvas.shape[1])
    canvas_c = canvas[:, left:right]
    frame_c = frame[:, left:right]
    h1, h2 = canvas_c.shape[0], frame_c.shape[0]
    votes: list[int] = []
    for band in (80, 48, MIN_BAND):
        if h1 < band or h2 < band:
            continue
        for fy in _band_starts(h2, band):
            y = _verified_y(canvas_c, frame_c[fy : fy + band])
            if y is not None:
                votes.append(y - fy)
        for cy in _band_starts(h1, band):
            if hint is not None and not (hint - h2 - 8 <= cy <= hint + h2 + 8):
                continue
            y = _verified_y(frame_c, canvas_c[cy : cy + band])
            if y is not None:
                votes.append(cy - y)
        origin = _agree_origin(votes)
        if origin is not None:
            return origin
    return _agree_origin(votes)


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
) -> tuple[np.ndarray, str, int]:
    """算出新帧顶边的垂直坐标，按坐标贴入，只增加尚未覆盖的区间。"""
    if canvas.shape[1] != frame.shape[1]:
        frame = cv2.resize(frame, (canvas.shape[1], frame.shape[0]), interpolation=cv2.INTER_AREA)
    if canvas.shape[0] >= frame.shape[0]:
        if frames_same(canvas[: frame.shape[0]], frame) or frames_same(canvas[-frame.shape[0] :], frame):
            origin = 0 if frames_same(canvas[: frame.shape[0]], frame) else canvas.shape[0] - frame.shape[0]
            return canvas, "none", origin

    hinted: int | None = None
    if prev_frame is not None:
        rel = find_frame_origin(prev_frame, frame)
        if rel is not None:
            hinted = last_origin + rel
            if 0 <= hinted and hinted + frame.shape[0] <= canvas.shape[0]:
                return canvas, "none", hinted

    origin = find_frame_origin(canvas, frame, hint=hinted)
    if origin is None:
        origin = hinted
    if origin is None:
        return canvas, "none", last_origin
    pad_top = max(0, -origin)
    pad_bot = max(0, origin + frame.shape[0] - canvas.shape[0])
    if hinted is not None and max(pad_top, pad_bot) > frame.shape[0] * 0.55:
        if abs(origin - hinted) > 4:
            origin = hinted
    return place_frame(canvas, frame, origin)


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
        resized = cv2.resize(canvas, (pw, ph), interpolation=cv2.INTER_AREA)
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
        self._same_count = 0
        self._auto = False
        self._busy = False

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
        self._timer.setInterval(380)
        self._timer.timeout.connect(self._tick)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        exclude_from_capture(self)

    def start(self) -> None:
        self.show()
        self.raise_()
        QTimer.singleShot(180, self._begin)

    def _begin(self) -> None:
        first = grab_region(self._region)
        self._last = _to_bgr(first)
        self._canvas = self._last.copy()
        self._frame_origin = 0
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
        frame = _to_bgr(grab_region(self._region))
        if self._last is not None and frames_same(self._last, frame):
            self._same_count += 1
            if self._auto:
                if self._same_count >= 3:
                    self._auto = False
                    self.auto_btn.setText("自动滚动")
                    self.hint.setText("似乎已到边界，可点「完成」")
                else:
                    wheel_at(self._center())
            return

        self._same_count = 0
        merged, direction, origin = stitch_vertical(
            self._canvas, frame, self._last, self._frame_origin
        )
        self._last = frame
        self._frame_origin = origin
        if merged.shape[0] > MAX_HEIGHT:
            self._canvas = merged[:MAX_HEIGHT] if direction != "down" else merged[-MAX_HEIGHT:]
            self._timer.stop()
            self._auto = False
            self.hint.setText(f"已达最大高度 {MAX_HEIGHT}px，请点完成")
            self._refresh_preview()
            return
        self._canvas = merged
        self._update_hint(direction)
        self._refresh_preview()
        if self._auto:
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
        way = {"up": "向上补图", "down": "向下补图"}.get(direction, "继续上下滚动")
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
