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


def _already_filled(canvas: np.ndarray, extra: np.ndarray, side: str) -> bool:
    take = min(canvas.shape[0], extra.shape[0])
    if take < 1:
        return True
    if side == "down":
        return frames_same(canvas[-take:], extra[-take:])
    return frames_same(canvas[:take], extra[:take])


def stitch_vertical(
    canvas: np.ndarray,
    frame: np.ndarray,
    prev_frame: np.ndarray | None = None,
) -> tuple[np.ndarray, str]:
    """按滚动方向把新区域接到长图上：下滚接下，上滚接上。"""
    if canvas.shape[1] != frame.shape[1]:
        frame = cv2.resize(frame, (canvas.shape[1], frame.shape[0]), interpolation=cv2.INTER_AREA)
    prev = prev_frame if prev_frame is not None else (
        canvas[-frame.shape[0] :] if canvas.shape[0] >= frame.shape[0] else canvas
    )
    if prev.shape[1] != frame.shape[1]:
        prev = cv2.resize(prev, (frame.shape[1], prev.shape[0]), interpolation=cv2.INTER_AREA)

    direction, extra = detect_scroll(prev, frame)
    if direction == "none" or extra is None or extra.shape[0] == 0:
        return canvas, "none"
    if extra.shape[1] != canvas.shape[1]:
        extra = cv2.resize(extra, (canvas.shape[1], extra.shape[0]), interpolation=cv2.INTER_AREA)
    if _already_filled(canvas, extra, direction):
        return canvas, "none"
    if direction == "up":
        return np.vstack([extra, canvas]), "up"
    return np.vstack([canvas, extra]), "down"


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

    def set_canvas(self, canvas: np.ndarray, screen: QRect) -> None:
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
        x = screen.right() - self.width() - PREVIEW_MARGIN
        y = screen.bottom() - self.height() - PREVIEW_MARGIN
        self.move(x, y)
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
        self.hint = QLabel("请在选区内滚动，画面会自动拼接")
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

    def start(self) -> None:
        self.show()
        self.raise_()
        QTimer.singleShot(180, self._begin)

    def _begin(self) -> None:
        first = grab_region(self._region)
        self._last = _to_bgr(first)
        self._canvas = self._last.copy()
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
        preview_overlap = self._preview.isVisible() and QRect(
            self._preview.pos(), self._preview.size()
        ).intersects(self._region)
        if self._overlap or preview_overlap:
            self.hide()
            self._preview.hide()
            QGuiApplication.processEvents()
        frame = _to_bgr(grab_region(self._region))
        if self._overlap or preview_overlap:
            self.show()
            self._preview.show()
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
        merged, direction = stitch_vertical(self._canvas, frame, self._last)
        self._last = frame
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
        self._preview.set_canvas(self._canvas, self._preview_screen())

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
