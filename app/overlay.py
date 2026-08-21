from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

import mss
from PIL import Image
from PySide6.QtCore import QPoint, QRect, Qt, QThread, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QImage, QPainter, QPen, QPixmap, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from app.clipboard_win import copy_image
from app.magnifier import Magnifier
from app.ocr_engine import recognize
from app.pin_window import PinWindow
from app.result_dialog import ResultDialog, show_error
from app.scroll_capture import ScrollCapturePanel, exclude_from_capture, wheel_at
from app.toolbar import CaptureToolbar
from app.translator import translate
from app.window_detect import OVERLAY_TITLE, hit_test

DRAG_THRESHOLD = 4
ACCENT = QColor(51, 112, 255)
DIM = QColor(0, 0, 0, 125)
HANDLE_HIT = 8
HANDLE_SIZE = 7
HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
HANDLE_CURSOR = {
    "nw": Qt.SizeFDiagCursor,
    "se": Qt.SizeFDiagCursor,
    "ne": Qt.SizeBDiagCursor,
    "sw": Qt.SizeBDiagCursor,
    "n": Qt.SizeVerCursor,
    "s": Qt.SizeVerCursor,
    "e": Qt.SizeHorCursor,
    "w": Qt.SizeHorCursor,
}


@dataclass
class Annotation:
    kind: str
    rect: QRect
    color: QColor
    width: int


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
    pin_requested = Signal(object)

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
        self._scroll: ScrollCapturePanel | None = None
        self._scrolling = False
        self._tool = ""
        self._pen_color = QColor("#E85454")
        self._pen_width = 4
        self._annots: list[Annotation] = []
        self._redo: list[Annotation] = []
        self._draw_start: QPoint | None = None
        self._draft: QRect | None = None
        self._resize_handle: str | None = None
        self._resize_origin: QRect | None = None
        self._move_press: QPoint | None = None
        self._move_origin: QRect | None = None
        self._move_annots: list[QRect] | None = None
        self._move_cursor = False
        self._active_annot = -1
        self._annot_resize_handle: str | None = None
        self._annot_resize_origin: QRect | None = None
        self._annot_move_press: QPoint | None = None
        self._annot_move_origin: QRect | None = None

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
        self.toolbar.pin_clicked.connect(self._on_pin)
        self.toolbar.ocr_clicked.connect(self._on_ocr)
        self.toolbar.translate_clicked.connect(self._on_translate)
        self.toolbar.scroll_clicked.connect(self._on_scroll)
        self.toolbar.tool_changed.connect(self._on_tool_changed)
        self.toolbar.color_changed.connect(self._on_color_changed)
        self.toolbar.width_changed.connect(self._on_width_changed)
        self.toolbar.undo_clicked.connect(self._undo_annot)
        self.toolbar.redo_clicked.connect(self._redo_annot)
        self.toolbar.confirm_clicked.connect(self._on_confirm)
        self.toolbar.cancel_requested.connect(self.cancel)
        self.toolbar.pointer_entered.connect(self.magnifier.hide)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        pos = self.mapFromGlobal(QCursor.pos())
        self._update_magnifier(pos)
        self.update()

    def cancel(self) -> None:
        self.close()

    def closeEvent(self, event) -> None:
        self._clear_override_cursor()
        self._set_move_cursor(False)
        if QWidget.mouseGrabber() is self:
            self.releaseMouse()
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(50)
        if self._scroll is not None:
            self._scroll.close()
            self._scroll = None
        self.closed.emit()
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.cancel()
            return
        if self._scrolling:
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self._selection:
            self._on_confirm()
            return
        if event.key() == Qt.Key_Z and event.modifiers() & Qt.ControlModifier:
            self._undo_annot()
            return
        if event.key() == Qt.Key_Y and event.modifiers() & Qt.ControlModifier:
            self._redo_annot()
            return
        if event.key() == Qt.Key_C and event.modifiers() & Qt.ControlModifier:
            self._copy_color()
            return
        if event.key() == Qt.Key_Shift and not event.isAutoRepeat():
            self.magnifier.toggle_mode()
            return
        super().keyPressEvent(event)

    def _event_pos(self, event) -> QPoint:
        if hasattr(event, "globalPosition"):
            return self.mapFromGlobal(event.globalPosition().toPoint())
        return self.mapFromGlobal(event.globalPos())

    def _start_move(self, pos: QPoint) -> None:
        if not self._selection:
            return
        self._move_press = QPoint(pos)
        self._move_origin = QRect(self._selection)
        self._move_annots = [QRect(item.rect) for item in self._annots]
        self._active_annot = -1
        self._set_move_cursor(True)
        self.magnifier.hide()
        self.grabMouse()

    def _apply_move(self, pos: QPoint) -> None:
        if self._move_press is None or self._move_origin is None:
            return
        self._selection = self._moved_rect(self._move_origin, pos - self._move_press)
        delta = self._selection.topLeft() - self._move_origin.topLeft()
        if self._move_annots is not None:
            for item, origin in zip(self._annots, self._move_annots):
                item.rect = origin.translated(delta)
        self._place_toolbar()
        self.update()

    def _end_move(self, pos: QPoint) -> None:
        if QWidget.mouseGrabber() is self:
            self.releaseMouse()
        self._move_press = None
        self._move_origin = None
        self._move_annots = None
        self._place_toolbar()
        self._apply_hover_cursor(pos)
        self._update_magnifier(pos)
        self.update()

    def _inside_move_area(self, pos: QPoint) -> bool:
        if not self._selection:
            return False
        inner = self._selection.adjusted(HANDLE_HIT + 1, HANDLE_HIT + 1, -(HANDLE_HIT + 1), -(HANDLE_HIT + 1))
        return inner.contains(pos)

    def _clear_override_cursor(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        while app.overrideCursor() is not None:
            app.restoreOverrideCursor()
        self._move_cursor = False

    def _set_move_cursor(self, active: bool) -> None:
        if active:
            self.setCursor(Qt.SizeAllCursor)
            self._move_cursor = True
            return
        if self._move_cursor:
            self._move_cursor = False
            self.setCursor(Qt.ArrowCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton:
            self.cancel()
            return
        if self._scrolling:
            return
        if event.button() != Qt.LeftButton:
            return
        pos = self._event_pos(event)
        if self._finalized:
            handle = self._hit_handle(pos)
            if handle and self._selection:
                self._resize_handle = handle
                self._resize_origin = QRect(self._selection)
                return
            annot_hit = self._hit_annot_handle(pos)
            if annot_hit is not None:
                self._active_annot, self._annot_resize_handle = annot_hit
                self._annot_resize_origin = QRect(self._annots[self._active_annot].rect)
                return
            border = self._hit_annot_border(pos)
            if border is not None:
                self._start_annot_move(border, pos)
                return
            picked = self._hit_annot(pos)
            if picked is not None:
                self._active_annot = picked
                self.update()
                return
            if self._inside_move_area(pos) or (self._selection and self._selection.contains(pos)):
                if self._tool:
                    self._draw_start = pos
                    self._draft = None
                    self._active_annot = -1
                    return
                self._start_move(pos)
                return
            return
        self._press = pos
        self._dragging = False
        if self._hover is None:
            self._hover = self._window_or_monitor_at(self._press)

    def wheelEvent(self, event) -> None:
        if self._scrolling and self._selection:
            steps = -3 if event.angleDelta().y() < 0 else 3
            if event.angleDelta().y() != 0:
                wheel_at(self._selection.center() + self._origin, steps)
            event.accept()
            return
        super().wheelEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._scrolling:
            return
        pos = self._event_pos(event)
        if self._finalized and self._resize_handle and self._resize_origin:
            self._selection = self._resize_rect(self._resize_handle, pos, self._resize_origin)
            self._place_toolbar()
            self._update_magnifier(pos)
            self.update()
            return
        if self._finalized and self._annot_move_press is not None and self._annot_move_origin is not None:
            self._apply_annot_move(pos)
            return
        if self._finalized and self._move_press is not None and self._move_origin is not None:
            self._apply_move(pos)
            return
        if (
            self._finalized
            and self._move_press is None
            and self._resize_handle is None
            and self._draw_start is None
            and bool(event.buttons() & Qt.LeftButton)
            and not self._tool
            and self._inside_move_area(pos)
        ):
            self._start_move(pos)
            self._apply_move(pos)
            return
        if (
            self._finalized
            and self._annot_resize_handle
            and self._annot_resize_origin is not None
            and 0 <= self._active_annot < len(self._annots)
        ):
            rect = self._resize_rect(self._annot_resize_handle, pos, self._annot_resize_origin)
            if self._selection:
                rect = rect.intersected(self._selection)
            self._annots[self._active_annot].rect = rect
            self._update_magnifier(pos)
            self.update()
            return
        if self._finalized and self._draw_start is not None and self._selection:
            self._draft = self._norm_rect(self._draw_start, pos).intersected(self._selection)
            self.magnifier.hide()
            self.update()
            return
        if self._finalized:
            self._apply_hover_cursor(pos)
            self._update_magnifier(pos)
            self.update()
            return
        if self._press is not None and not self._finalized:
            delta = pos - self._press
            if abs(delta.x()) >= DRAG_THRESHOLD or abs(delta.y()) >= DRAG_THRESHOLD:
                self._dragging = True
            if self._dragging:
                self._selection = self._norm_rect(self._press, pos)
                self._hover = None
        elif not self._finalized:
            self._hover = self._window_or_monitor_at(pos)
        self._update_magnifier(pos)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        if self._scrolling:
            return
        if self._finalized:
            if self._resize_handle:
                self._resize_handle = None
                self._resize_origin = None
                self._place_toolbar()
                self._update_magnifier(event.pos())
                self.update()
                return
            if self._move_press is not None:
                self._end_move(self._event_pos(event))
                return
            if self._annot_move_press is not None:
                self._end_annot_move(self._event_pos(event))
                return
            if self._annot_resize_handle:
                if self._annot_resize_origin is not None and 0 <= self._active_annot < len(self._annots):
                    if self._annots[self._active_annot].rect != self._annot_resize_origin:
                        self._redo.clear()
                self._annot_resize_handle = None
                self._annot_resize_origin = None
                self._update_magnifier(event.pos())
                self.update()
                return
            if self._draw_start is not None and self._draft and self._draft.width() > 2 and self._draft.height() > 2:
                self._annots.append(
                    Annotation(
                        self._tool,
                        QRect(self._draft),
                        QColor(self._pen_color),
                        self._pen_width,
                    )
                )
                self._active_annot = len(self._annots) - 1
                self._redo.clear()
            self._draw_start = None
            self._draft = None
            self._update_magnifier(event.pos())
            self.update()
            return
        if self._press is None:
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
        self.toolbar.show()
        self.toolbar.raise_()
        self._place_toolbar()
        self._apply_hover_cursor(self.mapFromGlobal(QCursor.pos()))
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

    def _copy_color(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self.magnifier.color_code())
        self.magnifier.mark_copied()

    def _over_toolbar(self, pos: QPoint) -> bool:
        if not self.toolbar.isVisible():
            return False
        return self.toolbar.geometry().adjusted(-6, -6, 6, 6).contains(pos)

    def _update_magnifier(self, pos: QPoint) -> None:
        if self._scrolling or self._draw_start is not None or self._over_toolbar(pos):
            self.magnifier.hide()
            return
        self.magnifier.show()
        self.magnifier.raise_()
        self.toolbar.raise_()
        self.magnifier.update_at(pos)
        self.magnifier.place_near(pos, self.rect())

    def _handle_points(self, rect: QRect) -> dict[str, QPoint]:
        cx, cy = rect.center().x(), rect.center().y()
        return {
            "nw": rect.topLeft(),
            "n": QPoint(cx, rect.top()),
            "ne": QPoint(rect.right(), rect.top()),
            "e": QPoint(rect.right(), cy),
            "se": rect.bottomRight(),
            "s": QPoint(cx, rect.bottom()),
            "sw": QPoint(rect.left(), rect.bottom()),
            "w": QPoint(rect.left(), cy),
        }

    def _hit_handle_points(self, rect: QRect, pos: QPoint) -> str | None:
        points = self._handle_points(rect)
        for name in ("nw", "ne", "se", "sw", "n", "s", "e", "w"):
            pt = points[name]
            if abs(pos.x() - pt.x()) <= HANDLE_HIT and abs(pos.y() - pt.y()) <= HANDLE_HIT:
                return name
        return None

    def _hit_handle_on(self, rect: QRect, pos: QPoint) -> str | None:
        hit = self._hit_handle_points(rect, pos)
        if hit:
            return hit
        x, y = pos.x(), pos.y()
        inside_x = rect.left() - HANDLE_HIT <= x <= rect.right() + HANDLE_HIT
        inside_y = rect.top() - HANDLE_HIT <= y <= rect.bottom() + HANDLE_HIT
        if inside_x and abs(y - rect.top()) <= HANDLE_HIT:
            return "n"
        if inside_x and abs(y - rect.bottom()) <= HANDLE_HIT:
            return "s"
        if inside_y and abs(x - rect.left()) <= HANDLE_HIT:
            return "w"
        if inside_y and abs(x - rect.right()) <= HANDLE_HIT:
            return "e"
        return None

    def _hit_handle(self, pos: QPoint) -> str | None:
        if not self._selection:
            return None
        return self._hit_handle_on(self._selection, pos)

    def _hit_annot_handle(self, pos: QPoint) -> tuple[int, str] | None:
        if 0 <= self._active_annot < len(self._annots):
            handle = self._hit_handle_points(self._annots[self._active_annot].rect, pos)
            if handle:
                return self._active_annot, handle
        for index in range(len(self._annots) - 1, -1, -1):
            handle = self._hit_handle_points(self._annots[index].rect, pos)
            if handle:
                return index, handle
        return None

    def _on_rect_border(self, rect: QRect, pos: QPoint, tol: int) -> bool:
        outer = rect.adjusted(-tol, -tol, tol, tol)
        inner = rect.adjusted(tol, tol, -tol, -tol)
        if not outer.contains(pos):
            return False
        if inner.width() > 2 and inner.height() > 2 and inner.contains(pos):
            return False
        return True

    def _on_ellipse_border(self, rect: QRect, pos: QPoint, tol: int) -> bool:
        rx = rect.width() / 2
        ry = rect.height() / 2
        if rx < 1 or ry < 1:
            return self._on_rect_border(rect, pos, tol)
        cx = rect.left() + rx
        cy = rect.top() + ry
        dx = (pos.x() - cx) / rx
        dy = (pos.y() - cy) / ry
        dist = dx * dx + dy * dy
        band = max(tol / max(min(rx, ry), 1.0), 0.08)
        return abs(dist - 1.0) <= band

    def _hit_annot_border(self, pos: QPoint) -> int | None:
        for index in range(len(self._annots) - 1, -1, -1):
            item = self._annots[index]
            tol = max(8, item.width + 4)
            if item.kind == "ellipse":
                if self._on_ellipse_border(item.rect, pos, tol):
                    return index
            elif self._on_rect_border(item.rect, pos, tol):
                return index
        return None

    def _start_annot_move(self, index: int, pos: QPoint) -> None:
        self._active_annot = index
        self._annot_move_press = QPoint(pos)
        self._annot_move_origin = QRect(self._annots[index].rect)
        self._set_move_cursor(True)
        self.magnifier.hide()
        self.update()

    def _apply_annot_move(self, pos: QPoint) -> None:
        if (
            self._annot_move_press is None
            or self._annot_move_origin is None
            or not (0 <= self._active_annot < len(self._annots))
        ):
            return
        moved = self._moved_rect(self._annot_move_origin, pos - self._annot_move_press)
        if self._selection:
            moved = self._clamp_rect(moved, self._selection)
        self._annots[self._active_annot].rect = moved
        self._update_magnifier(pos)
        self.update()

    def _end_annot_move(self, pos: QPoint) -> None:
        if (
            self._annot_move_origin is not None
            and 0 <= self._active_annot < len(self._annots)
            and self._annots[self._active_annot].rect != self._annot_move_origin
        ):
            self._redo.clear()
        self._annot_move_press = None
        self._annot_move_origin = None
        self._apply_hover_cursor(pos)
        self._update_magnifier(pos)
        self.update()

    def _clamp_rect(self, rect: QRect, bounds: QRect) -> QRect:
        w, h = rect.width(), rect.height()
        x = max(bounds.left(), min(rect.x(), bounds.right() - w + 1))
        y = max(bounds.top(), min(rect.y(), bounds.bottom() - h + 1))
        if w > bounds.width():
            x = bounds.left()
            w = bounds.width()
        if h > bounds.height():
            y = bounds.top()
            h = bounds.height()
        return QRect(x, y, w, h).intersected(bounds)

    def _hit_annot(self, pos: QPoint) -> int | None:
        for index in range(len(self._annots) - 1, -1, -1):
            if self._annots[index].rect.adjusted(-4, -4, 4, 4).contains(pos):
                return index
        return None

    def _moved_rect(self, origin: QRect, delta: QPoint) -> QRect:
        bounds = self.rect()
        x = max(bounds.left(), min(origin.x() + delta.x(), bounds.right() - origin.width() + 1))
        y = max(bounds.top(), min(origin.y() + delta.y(), bounds.bottom() - origin.height() + 1))
        return QRect(x, y, origin.width(), origin.height()).intersected(bounds)

    def _resize_rect(self, handle: str, pos: QPoint, origin: QRect) -> QRect:
        left, top = origin.left(), origin.top()
        right, bottom = origin.right(), origin.bottom()
        if handle in {"nw", "w", "sw"}:
            left = pos.x()
        if handle in {"ne", "e", "se"}:
            right = pos.x()
        if handle in {"nw", "n", "ne"}:
            top = pos.y()
        if handle in {"sw", "s", "se"}:
            bottom = pos.y()
        return self._norm_rect(QPoint(left, top), QPoint(right, bottom))

    def _apply_hover_cursor(self, pos: QPoint) -> None:
        handle = self._hit_handle(pos)
        if handle:
            self._set_move_cursor(False)
            self.setCursor(HANDLE_CURSOR[handle])
            return
        annot_hit = self._hit_annot_handle(pos)
        if annot_hit is not None:
            self._set_move_cursor(False)
            self.setCursor(HANDLE_CURSOR[annot_hit[1]])
            return
        if self._hit_annot_border(pos) is not None:
            self.setCursor(Qt.SizeAllCursor)
            self._set_move_cursor(True)
            return
        if self._tool:
            self._set_move_cursor(False)
            self.setCursor(Qt.CrossCursor)
            return
        if self._inside_move_area(pos) or (self._selection and self._selection.contains(pos)):
            self._set_move_cursor(True)
            return
        self._set_move_cursor(False)
        self.setCursor(Qt.ArrowCursor)

    def _draw_sel_size(self, painter: QPainter, rect: QRect) -> None:
        text = f"{rect.width()} × {rect.height()}"
        font = QFont()
        font.setPixelSize(12)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        pad_x, pad_y = 6, 3
        tw = metrics.horizontalAdvance(text) + pad_x * 2
        th = metrics.height() + pad_y * 2
        x = rect.left()
        y = rect.top() - th - 2
        if y < self.rect().top():
            y = rect.top() + 2
            x = rect.left() + 2
        box = QRect(x, y, tw, th)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(20, 20, 20, 210))
        painter.drawRect(box)
        painter.setPen(QColor(242, 242, 242))
        painter.setBrush(Qt.NoBrush)
        painter.drawText(box.adjusted(pad_x, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, text)

    def _draw_handles(self, painter: QPainter, rect: QRect) -> None:
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setPen(QPen(ACCENT, 1))
        painter.setBrush(QColor(255, 255, 255))
        half = HANDLE_SIZE // 2
        for pt in self._handle_points(rect).values():
            painter.drawRect(pt.x() - half, pt.y() - half, HANDLE_SIZE, HANDLE_SIZE)

    def _norm_rect(self, p1: QPoint, p2: QPoint) -> QRect:
        x1, x2 = sorted((p1.x(), p2.x()))
        y1, y2 = sorted((p1.y(), p2.y()))
        rect = QRect(x1, y1, x2 - x1 + 1, y2 - y1 + 1)
        return rect.intersected(self.rect())

    def _on_tool_changed(self, tool: str) -> None:
        self._tool = tool
        self._pen_color = self.toolbar.current_color()
        self._pen_width = self.toolbar.current_width()
        self._set_move_cursor(False)
        self.setCursor(Qt.CrossCursor if tool else Qt.ArrowCursor)
        self._place_toolbar()
        self._apply_hover_cursor(self.mapFromGlobal(QCursor.pos()))

    def _on_color_changed(self, color: QColor) -> None:
        self._pen_color = QColor(color)
        if 0 <= self._active_annot < len(self._annots):
            self._annots[self._active_annot].color = QColor(color)
            self._redo.clear()
            self.update()

    def _on_width_changed(self, width: int) -> None:
        self._pen_width = int(width)
        if 0 <= self._active_annot < len(self._annots):
            self._annots[self._active_annot].width = int(width)
            self._redo.clear()
            self.update()

    def _undo_annot(self) -> None:
        if not self._annots:
            return
        self._redo.append(self._annots.pop())
        self._active_annot = len(self._annots) - 1
        self.update()

    def _redo_annot(self) -> None:
        if not self._redo:
            return
        self._annots.append(self._redo.pop())
        self._active_annot = len(self._annots) - 1
        self.update()

    def _draw_annotations(
        self,
        painter: QPainter,
        origin: QPoint | None = None,
        include_draft: bool = True,
    ) -> None:
        offset = origin or QPoint(0, 0)
        items = list(self._annots)
        if include_draft and self._draft is not None and self._tool:
            items.append(
                Annotation(self._tool, QRect(self._draft), QColor(self._pen_color), self._pen_width)
            )
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(Qt.NoBrush)
        for item in items:
            rect = item.rect.translated(-offset)
            painter.setPen(QPen(item.color, item.width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            if item.kind == "ellipse":
                painter.drawEllipse(rect)
            else:
                painter.drawRect(rect.adjusted(0, 0, -1, -1))

    def _crop_pixmap(self) -> QPixmap | None:
        if not self._selection:
            return None
        pix = self._shot.copy(self._selection)
        if self._annots:
            painter = QPainter(pix)
            painter.setClipRect(QRect(0, 0, pix.width(), pix.height()))
            self._draw_annotations(painter, self._selection.topLeft(), include_draft=False)
            painter.end()
        return pix

    def _crop(self) -> Image.Image | None:
        pix = self._crop_pixmap()
        if pix is None:
            return None
        return pixmap_to_pil(pix)

    def _on_pin(self) -> None:
        pix = self._crop_pixmap()
        if pix is None or not self._selection:
            return
        geo = QRect(
            self._selection.x() + self._origin.x(),
            self._selection.y() + self._origin.y(),
            self._selection.width(),
            self._selection.height(),
        )
        self.pin_requested.emit(PinWindow(pix, geo))
        self.close()

    def _on_scroll(self) -> None:
        if not self._selection:
            return
        if self._selection.height() < 80:
            show_error(self, "选区太矮，请框选窗口里的可滚动区域后再试。")
            return
        region = QRect(
            self._selection.x() + self._origin.x(),
            self._selection.y() + self._origin.y(),
            self._selection.width(),
            self._selection.height(),
        )
        self._scrolling = True
        self.toolbar.hide()
        self.magnifier.hide()
        exclude_from_capture(self)
        hole = QRect(self._selection)
        if hole.isValid() and hole.width() > 2 and hole.height() > 2:
            mask = QRegion(self.rect()).subtracted(QRegion(hole))
            self.setMask(mask)
        self._scroll = ScrollCapturePanel(region)
        self._scroll.finished.connect(self._on_scroll_done)
        self._scroll.cancelled.connect(self.cancel)
        self._scroll.start()
        self.update()

    def _on_scroll_done(self, image: object) -> None:
        if not isinstance(image, Image.Image):
            self.cancel()
            return
        try:
            copy_image(image)
        except Exception as exc:
            show_error(None, f"复制到剪贴板失败：{exc}")
            self.cancel()
            return
        self.copied.emit()
        self.close()

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
        self._clear_override_cursor()
        self._apply_hover_cursor(self.mapFromGlobal(QCursor.pos()))

    def _show_ocr(self, text: object) -> None:
        content = str(text or "").strip()
        if not content:
            show_error(self, "未识别到文字")
            return
        self._clear_override_cursor()
        dialog = ResultDialog("识别文字", content, parent=self)
        dialog.exec()

    def _show_translate(self, result: object) -> None:
        text, translated = result if isinstance(result, tuple) else ("", "")
        if not str(text).strip():
            show_error(self, "未识别到文字")
            return
        self._clear_override_cursor()
        dialog = ResultDialog("翻译", str(text), str(translated), parent=self)
        dialog.exec()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._shot)
        painter.fillRect(self.rect(), DIM)

        active = self._selection if (self._dragging or self._finalized) else self._hover
        if active and active.isValid():
            painter.drawPixmap(active, self._shot, active)
            if self._finalized and not self._scrolling:
                painter.save()
                painter.setClipRect(active)
                self._draw_annotations(painter)
                painter.restore()
            painter.setPen(QPen(ACCENT, 2))
            painter.setBrush(Qt.NoBrush)
            frame = active.adjusted(-2, -2, 1, 1) if self._scrolling else active.adjusted(0, 0, -1, -1)
            painter.drawRect(frame)
            if not self._scrolling:
                self._draw_sel_size(painter, active)
            if self._finalized and not self._scrolling:
                self._draw_handles(painter, active)
                if 0 <= self._active_annot < len(self._annots):
                    self._draw_handles(painter, self._annots[self._active_annot].rect)

        if not self._finalized:
            pos = self.mapFromGlobal(QCursor.pos())
            if self.rect().contains(pos):
                painter.setPen(QPen(QColor(255, 255, 255, 90), 1, Qt.DashLine))
                painter.drawLine(pos.x(), 0, pos.x(), self.height())
                painter.drawLine(0, pos.y(), self.width(), pos.y())
