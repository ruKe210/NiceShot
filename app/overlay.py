from __future__ import annotations

from io import BytesIO

import mss
from PIL import Image
from PySide6.QtCore import QPoint, QRect, Qt, QThread, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from app.clipboard_win import copy_image
from app.magnifier import Magnifier
from app.ocr_engine import recognize
from app.result_dialog import ResultDialog, show_error
from app.toolbar import CaptureToolbar
from app.translator import translate
from app.window_detect import OVERLAY_TITLE, hit_test

DRAG_THRESHOLD = 4
ACCENT = QColor(51, 112, 255)
DIM = QColor(0, 0, 0, 125)


def grab_virtual_desktop() -> tuple[QPixmap, QPoint]:
    with mss.mss() as sct:
        mon = sct.monitors[0]
        raw = sct.grab(mon)
        image = QImage(
            raw.bgra,
            raw.width,
            raw.height,
            raw.width * 4,
            QImage.Format.Format_ARGB32,
        ).copy()
        origin = QPoint(mon["left"], mon["top"])
        return QPixmap.fromImage(image), origin


def pixmap_to_pil(pixmap: QPixmap) -> Image.Image:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buf, "PNG")
    return Image.open(BytesIO(bytes(ba))).convert("RGB")


class Worker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args) -> None:
        super().__init__()
        self._fn = fn
        self._args = args

    def run(self) -> None:
        try:
            self.done.emit(self._fn(*self._args))
        except Exception as exc:
            self.failed.emit(str(exc))


class CaptureOverlay(QWidget):
    closed = Signal()
    copied = Signal()

    def __init__(
        self,
        screenshot: QPixmap,
        origin: QPoint,
        windows: list[tuple[int, tuple[int, int, int, int], str]],
    ) -> None:
        super().__init__()
        self._shot = screenshot
        self._origin = origin
        self._windows = windows
        self._hover: QRect | None = None
        self._selection: QRect | None = None
        self._press: QPoint | None = None
        self._dragging = False
        self._finalized = False
        self._worker: Worker | None = None

        self.setWindowTitle(OVERLAY_TITLE)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setCursor(Qt.CrossCursor)
        self.setGeometry(origin.x(), origin.y(), screenshot.width(), screenshot.height())

        self.magnifier = Magnifier(self)
        self.magnifier.set_source(screenshot)
        self.toolbar = CaptureToolbar(self)
        self.toolbar.hide()
        self.toolbar.ocr_clicked.connect(self._on_ocr)
        self.toolbar.translate_clicked.connect(self._on_translate)
        self.toolbar.confirm_clicked.connect(self._on_confirm)
        self.toolbar.cancel_requested.connect(self.cancel)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        pos = self.mapFromGlobal(QCursor.pos())
        self._update_magnifier(pos, None)
        self.update()

    def cancel(self) -> None:
        self.close()

    def closeEvent(self, event) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(50)
        self.closed.emit()
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.cancel()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self._selection:
            self._on_confirm()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton:
            self.cancel()
            return
        if event.button() != Qt.LeftButton or self._finalized:
            return
        self._press = event.pos()
        self._dragging = False
        if self._hover is None:
            self._hover = self._window_or_monitor_at(self._press)

    def mouseMoveEvent(self, event) -> None:
        pos = event.pos()
        sel_size = None
        if self._press is not None and not self._finalized:
            delta = pos - self._press
            if abs(delta.x()) >= DRAG_THRESHOLD or abs(delta.y()) >= DRAG_THRESHOLD:
                self._dragging = True
            if self._dragging:
                self._selection = self._norm_rect(self._press, pos)
                self._hover = None
                sel_size = (self._selection.width(), self._selection.height())
        elif not self._finalized:
            self._hover = self._window_or_monitor_at(pos)
        self._update_magnifier(pos, sel_size)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or self._press is None:
            return
        if self._finalized:
            self._press = None
            return
        if self._dragging and self._selection is not None:
            self._finalize(self._selection)
        else:
            if self._hover and self._hover.isValid():
                self._finalize(self._hover)
        self._press = None
        self._dragging = False

    def _window_or_monitor_at(self, pos: QPoint) -> QRect | None:
        hit = hit_test(
            self._windows,
            pos.x() + self._origin.x(),
            pos.y() + self._origin.y(),
        )
        if hit:
            left, top, right, bottom = hit[1]
            return QRect(
                left - self._origin.x(),
                top - self._origin.y(),
                right - left,
                bottom - top,
            ).intersected(self.rect())
        global_pos = pos + self._origin
        for screen in QGuiApplication.screens():
            geo = screen.geometry()
            if geo.contains(global_pos):
                return QRect(
                    geo.x() - self._origin.x(),
                    geo.y() - self._origin.y(),
                    geo.width(),
                    geo.height(),
                ).intersected(self.rect())
        return None

    def _finalize(self, rect: QRect) -> None:
        rect = rect.intersected(self.rect())
        if rect.width() < 1 or rect.height() < 1:
            return
        self._selection = rect
        self._finalized = True
        self._hover = None
        self.magnifier.hide()
        self.toolbar.show()
        self.toolbar.raise_()
        self._place_toolbar()
        self.update()

    def _place_toolbar(self) -> None:
        if not self._selection:
            return
        self.toolbar.adjustSize()
        tw, th = self.toolbar.width(), self.toolbar.height()
        gap = 8
        sel = self._selection
        x = sel.right() - tw + 1
        y = sel.bottom() + gap
        bounds = self.rect()
        if y + th > bounds.bottom():
            y = sel.top() - th - gap
        if x < bounds.left():
            x = sel.left()
        if x + tw > bounds.right():
            x = bounds.right() - tw
        if y < bounds.top():
            y = min(sel.bottom() + gap, bounds.bottom() - th)
        self.toolbar.move(max(0, x), max(0, y))

    def _update_magnifier(self, pos: QPoint, sel_size: tuple[int, int] | None) -> None:
        if self._finalized:
            return
        self.magnifier.show()
        self.magnifier.raise_()
        self.magnifier.update_at(pos, sel_size)
        self.magnifier.place_near(pos, self.rect())

    def _norm_rect(self, p1: QPoint, p2: QPoint) -> QRect:
        x1, x2 = sorted((p1.x(), p2.x()))
        y1, y2 = sorted((p1.y(), p2.y()))
        rect = QRect(x1, y1, x2 - x1 + 1, y2 - y1 + 1)
        return rect.intersected(self.rect())

    def _crop(self) -> Image.Image | None:
        if not self._selection:
            return None
        return pixmap_to_pil(self._shot.copy(self._selection))

    def _on_confirm(self) -> None:
        image = self._crop()
        if image is None:
            return
        try:
            copy_image(image)
        except Exception as exc:
            show_error(self, f"复制到剪贴板失败：{exc}")
            return
        self.copied.emit()
        self.close()

    def _on_ocr(self) -> None:
        image = self._crop()
        if image is None:
            return
        self._run_worker(lambda: recognize(image), self._show_ocr)

    def _on_translate(self) -> None:
        image = self._crop()
        if image is None:
            return

        def job():
            text = recognize(image)
            if not text:
                return ("", "")
            return (text, translate(text))

        self._run_worker(job, self._show_translate)

    def _run_worker(self, fn, on_done) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.toolbar.set_busy(True)
        self.setCursor(Qt.WaitCursor)
        self._worker = Worker(fn)
        self._worker.done.connect(on_done)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_worker_failed(self, message: str) -> None:
        show_error(self, message)

    def _on_worker_finished(self) -> None:
        self.toolbar.set_busy(False)
        self.setCursor(Qt.CrossCursor)

    def _show_ocr(self, text: object) -> None:
        content = str(text or "").strip()
        if not content:
            show_error(self, "未识别到文字")
            return
        dialog = ResultDialog("识别文字", content, parent=self)
        dialog.exec()

    def _show_translate(self, result: object) -> None:
        text, translated = result if isinstance(result, tuple) else ("", "")
        if not str(text).strip():
            show_error(self, "未识别到文字")
            return
        dialog = ResultDialog("翻译", str(text), str(translated), parent=self)
        dialog.exec()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._shot)
        painter.fillRect(self.rect(), DIM)

        active = self._selection if (self._dragging or self._finalized) else self._hover
        if active and active.isValid():
            painter.drawPixmap(active, self._shot, active)
            painter.setPen(QPen(ACCENT, 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(active.adjusted(0, 0, -1, -1))

        if not self._finalized:
            pos = self.mapFromGlobal(QCursor.pos())
            if self.rect().contains(pos):
                painter.setPen(QPen(QColor(255, 255, 255, 90), 1, Qt.DashLine))
                painter.drawLine(pos.x(), 0, pos.x(), self.height())
                painter.drawLine(0, pos.y(), self.width(), pos.y())
