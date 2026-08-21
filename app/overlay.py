from __future__ import annotations

from dataclasses import dataclass
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
from app.scroll_capture import ScrollCapturePanel
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
        self._tool = ""
        self._pen_color = QColor("#E85454")
        self._pen_width = 4
        self._annots: list[Annotation] = []
        self._draw_start: QPoint | None = None
        self._draft: QRect | None = None
        self._resize_handle: str | None = None
        self._resize_origin: QRect | None = None
        self._active_annot = -1
        self._annot_resize_handle: str | None = None
        self._annot_resize_origin: QRect | None = None

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
        self.toolbar.scroll_clicked.connect(self._on_scroll)
        self.toolbar.tool_changed.connect(self._on_tool_changed)
        self.toolbar.color_changed.connect(self._on_color_changed)
        self.toolbar.width_changed.connect(self._on_width_changed)
        self.toolbar.undo_clicked.connect(self._undo_annot)
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
        if self._scroll is not None:
            self._scroll.close()
            self._scroll = None
        self.closed.emit()
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.cancel()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self._selection:
            self._on_confirm()
            return
        if event.key() == Qt.Key_Z and event.modifiers() & Qt.ControlModifier:
            self._undo_annot()
            return
        if event.key() == Qt.Key_C and event.modifiers() & Qt.ControlModifier:
            self._copy_color()
            return
        if event.key() == Qt.Key_Shift and not event.isAutoRepeat():
            self.magnifier.toggle_mode()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton:
            self.cancel()
            return
        if event.button() != Qt.LeftButton:
            return
        if self._finalized:
            handle = self._hit_handle(event.pos())
            if handle and self._selection:
                self._resize_handle = handle
                self._resize_origin = QRect(self._selection)
                return
            annot_hit = self._hit_annot_handle(event.pos())
            if annot_hit is not None:
                self._active_annot, self._annot_resize_handle = annot_hit
                self._annot_resize_origin = QRect(self._annots[self._active_annot].rect)
                return
            picked = self._hit_annot(event.pos())
            if picked is not None:
                self._active_annot = picked
                self.update()
                return
            if (
                self._tool
                and self._selection
                and self._selection.contains(event.pos())
            ):
                self._draw_start = event.pos()
                self._draft = None
                self._active_annot = -1
            return
        self._press = event.pos()
        self._dragging = False
        if self._hover is None:
            self._hover = self._window_or_monitor_at(self._press)

    def mouseMoveEvent(self, event) -> None:
        pos = event.pos()
        if self._finalized and self._resize_handle and self._resize_origin:
            self._selection = self._resize_rect(self._resize_handle, pos, self._resize_origin)
            self._place_toolbar()
            self._update_magnifier(pos, (self._selection.width(), self._selection.height()))
            self.update()
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
            self._update_magnifier(pos, (rect.width(), rect.height()))
            self.update()
            return
        if self._finalized and self._draw_start is not None and self._selection:
            self._draft = self._norm_rect(self._draw_start, pos).intersected(self._selection)
            self.magnifier.hide()
            self.update()
            return
        if self._finalized:
            self._apply_hover_cursor(pos)
            sel_size = (self._selection.width(), self._selection.height()) if self._selection else None
            self._update_magnifier(pos, sel_size)
            self.update()
            return
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
        if event.button() != Qt.LeftButton:
            return
        if self._finalized:
            if self._resize_handle:
                self._resize_handle = None
                self._resize_origin = None
                self._place_toolbar()
                self._update_magnifier(event.pos(), None)
                self.update()
                return
            if self._annot_resize_handle:
                self._annot_resize_handle = None
                self._annot_resize_origin = None
                self._update_magnifier(event.pos(), None)
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
            self._draw_start = None
            self._draft = None
            self._update_magnifier(event.pos(), None)
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

    def _update_magnifier(self, pos: QPoint, sel_size: tuple[int, int] | None) -> None:
        if self._draw_start is not None:
            self.magnifier.hide()
            return
        self.magnifier.show()
        self.magnifier.raise_()
        self.magnifier.update_at(pos, sel_size)
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

    def _hit_handle_on(self, rect: QRect, pos: QPoint) -> str | None:
        points = self._handle_points(rect)
        for name in ("nw", "ne", "se", "sw", "n", "s", "e", "w"):
            pt = points[name]
            if abs(pos.x() - pt.x()) <= HANDLE_HIT and abs(pos.y() - pt.y()) <= HANDLE_HIT:
                return name
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
            handle = self._hit_handle_on(self._annots[self._active_annot].rect, pos)
            if handle:
                return self._active_annot, handle
        for index in range(len(self._annots) - 1, -1, -1):
            handle = self._hit_handle_on(self._annots[index].rect, pos)
            if handle:
                return index, handle
        return None

    def _hit_annot(self, pos: QPoint) -> int | None:
        for index in range(len(self._annots) - 1, -1, -1):
            if self._annots[index].rect.adjusted(-4, -4, 4, 4).contains(pos):
                return index
        return None

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
            self.setCursor(HANDLE_CURSOR[handle])
            return
        annot_hit = self._hit_annot_handle(pos)
        if annot_hit is not None:
            self.setCursor(HANDLE_CURSOR[annot_hit[1]])
            return
        if self._tool:
            self.setCursor(Qt.CrossCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

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
        self.setCursor(Qt.CrossCursor if tool else Qt.ArrowCursor)
        self._place_toolbar()

    def _on_color_changed(self, color: QColor) -> None:
        self._pen_color = QColor(color)
        if 0 <= self._active_annot < len(self._annots):
            self._annots[self._active_annot].color = QColor(color)
            self.update()

    def _on_width_changed(self, width: int) -> None:
        self._pen_width = int(width)
        if 0 <= self._active_annot < len(self._annots):
            self._annots[self._active_annot].width = int(width)
            self.update()

    def _undo_annot(self) -> None:
        if self._annots:
            self._annots.pop()
            if self._active_annot >= len(self._annots):
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

    def _crop(self) -> Image.Image | None:
        if not self._selection:
            return None
        pix = self._shot.copy(self._selection)
        if self._annots:
            painter = QPainter(pix)
            painter.setClipRect(QRect(0, 0, pix.width(), pix.height()))
            self._draw_annotations(painter, self._selection.topLeft(), include_draft=False)
            painter.end()
        return pixmap_to_pil(pix)

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
        self.hide()
        self._scroll = ScrollCapturePanel(region)
        self._scroll.finished.connect(self._on_scroll_done)
        self._scroll.cancelled.connect(self.cancel)
        self._scroll.start()

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
            if self._finalized:
                painter.save()
                painter.setClipRect(active)
                self._draw_annotations(painter)
                painter.restore()
            painter.setPen(QPen(ACCENT, 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(active.adjusted(0, 0, -1, -1))
            if self._finalized:
                self._draw_handles(painter, active)
                if 0 <= self._active_annot < len(self._annots):
                    self._draw_handles(painter, self._annots[self._active_annot].rect)

        if not self._finalized:
            pos = self.mapFromGlobal(QCursor.pos())
            if self.rect().contains(pos):
                painter.setPen(QPen(QColor(255, 255, 255, 90), 1, Qt.DashLine))
                painter.drawLine(pos.x(), 0, pos.x(), self.height())
                painter.drawLine(0, pos.y(), self.width(), pos.y())
