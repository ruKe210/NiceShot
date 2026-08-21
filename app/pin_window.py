from __future__ import annotations

from io import BytesIO

from PIL import Image
from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPen, QPixmap, QWheelEvent
from PySide6.QtWidgets import QPushButton, QToolTip, QWidget

from app.clipboard_win import copy_image

PIN_TITLE = "NiceShotPin"
CLOSE_SIZE = 22
CLOSE_MARGIN = 4
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
MIN_SIDE = 48
MIN_SCALE = 0.15
MAX_SCALE = 8.0
WHEEL_STEP = 1.12


class PinCloseButton(QPushButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(CLOSE_SIZE, CLOSE_SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("关闭这张固定截图")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        fill = QColor(232, 84, 84, 235) if self.underMouse() else QColor(40, 40, 40, 200)
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        painter.drawEllipse(1, 1, CLOSE_SIZE - 2, CLOSE_SIZE - 2)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        pad = 6
        painter.drawLine(pad, pad, CLOSE_SIZE - pad, CLOSE_SIZE - pad)
        painter.drawLine(CLOSE_SIZE - pad, pad, pad, CLOSE_SIZE - pad)


class PinWindow(QWidget):
    closed = Signal(object)

    def __init__(self, pixmap: QPixmap, screen_rect: QRect, parent=None) -> None:
        super().__init__(parent)
        self._source = QPixmap(pixmap)
        self._drag_pos: QPoint | None = None
        self._resize_handle: str | None = None
        self._resize_origin: QRect | None = None
        self.setWindowTitle(PIN_TITLE)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.OpenHandCursor)
        self.setGeometry(
            screen_rect.x(),
            screen_rect.y(),
            max(pixmap.width(), 1),
            max(pixmap.height(), 1),
        )
        self.setToolTip("拖动中间移动；拖边框可横竖拉伸；滚轮缩放；右键复制")

        self._close = PinCloseButton(self)
        self._place_close()
        self._close.clicked.connect(self.close)

    def _src_size(self) -> tuple[int, int]:
        return max(1, self._source.width()), max(1, self._source.height())

    def _scale_xy(self) -> tuple[float, float]:
        src_w, src_h = self._src_size()
        return max(self.width(), 1) / src_w, max(self.height(), 1) / src_h

    def _clamp_size(self, width: int, height: int) -> tuple[int, int]:
        src_w, src_h = self._src_size()
        min_w = max(MIN_SIDE, round(src_w * MIN_SCALE))
        min_h = max(MIN_SIDE, round(src_h * MIN_SCALE))
        max_w = max(min_w, round(src_w * MAX_SCALE))
        max_h = max(min_h, round(src_h * MAX_SCALE))
        return max(min_w, min(max_w, int(width))), max(min_h, min(max_h, int(height)))

    def _apply_geometry(self, x: int, y: int, width: int, height: int) -> None:
        self.setGeometry(x, y, width, height)
        self._place_close()
        self.update()

    def _place_close(self) -> None:
        x = max(0, self.width() - CLOSE_SIZE - CLOSE_MARGIN)
        y = CLOSE_MARGIN if self.height() >= CLOSE_SIZE + CLOSE_MARGIN else 0
        self._close.move(x, y)
        self._close.raise_()

    def _local_pos(self, event) -> QPoint:
        if hasattr(event, "position"):
            return event.position().toPoint()
        return event.pos()

    def _global_pos(self, event) -> QPoint:
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        return event.globalPos()

    def _handle_points(self) -> dict[str, QPoint]:
        rect = self.rect()
        cx = rect.center().x()
        cy = rect.center().y()
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

    def _hit_handle(self, pos: QPoint) -> str | None:
        if self._close.geometry().adjusted(-2, -2, 2, 2).contains(pos):
            return None
        for name, pt in self._handle_points().items():
            if abs(pos.x() - pt.x()) <= HANDLE_HIT and abs(pos.y() - pt.y()) <= HANDLE_HIT:
                return name
        x, y = pos.x(), pos.y()
        rect = self.rect()
        inside_x = -HANDLE_HIT <= x <= rect.width() + HANDLE_HIT
        inside_y = -HANDLE_HIT <= y <= rect.height() + HANDLE_HIT
        if inside_x and abs(y - rect.top()) <= HANDLE_HIT:
            return "n"
        if inside_x and abs(y - rect.bottom()) <= HANDLE_HIT:
            return "s"
        if inside_y and abs(x - rect.left()) <= HANDLE_HIT:
            return "w"
        if inside_y and abs(x - rect.right()) <= HANDLE_HIT:
            return "e"
        return None

    def _apply_hover_cursor(self, pos: QPoint) -> None:
        if self._close.geometry().contains(pos):
            return
        handle = self._hit_handle(pos)
        if handle:
            self.setCursor(HANDLE_CURSOR[handle])
        elif self._drag_pos is None:
            self.setCursor(Qt.OpenHandCursor)

    def _resize_from_handle(self, handle: str, global_pos: QPoint, origin: QRect) -> None:
        width, height = origin.width(), origin.height()
        if handle in {"nw", "ne", "se", "sw"}:
            if handle in {"ne", "se"}:
                new_w = global_pos.x() - origin.x()
            else:
                new_w = origin.x() + origin.width() - global_pos.x()
            if handle in {"se", "sw"}:
                new_h = global_pos.y() - origin.y()
            else:
                new_h = origin.y() + origin.height() - global_pos.y()
            scale_w = new_w / max(1, origin.width())
            scale_h = new_h / max(1, origin.height())
            scale = scale_w if abs(new_w - origin.width()) >= abs(new_h - origin.height()) else scale_h
            src_w, src_h = self._src_size()
            min_w = max(MIN_SIDE, round(src_w * MIN_SCALE))
            min_h = max(MIN_SIDE, round(src_h * MIN_SCALE))
            max_w = max(min_w, round(src_w * MAX_SCALE))
            max_h = max(min_h, round(src_h * MAX_SCALE))
            lo = max(min_w / max(1, origin.width()), min_h / max(1, origin.height()))
            hi = min(max_w / max(1, origin.width()), max_h / max(1, origin.height()))
            scale = max(lo, min(hi, scale))
            width = max(1, round(origin.width() * scale))
            height = max(1, round(origin.height() * scale))
        else:
            if handle == "e":
                width = global_pos.x() - origin.x()
            elif handle == "w":
                width = origin.x() + origin.width() - global_pos.x()
            elif handle == "s":
                height = global_pos.y() - origin.y()
            elif handle == "n":
                height = origin.y() + origin.height() - global_pos.y()
            width, height = self._clamp_size(width, height)
        x, y = origin.x(), origin.y()
        if handle in {"nw", "w", "sw"}:
            x = origin.x() + origin.width() - width
        if handle in {"nw", "n", "ne"}:
            y = origin.y() + origin.height() - height
        self._apply_geometry(x, y, width, height)

    def _zoom_at(self, factor: float, local_anchor: QPoint) -> None:
        old_w, old_h = max(1, self.width()), max(1, self.height())
        width, height = self._clamp_size(round(old_w * factor), round(old_h * factor))
        rx = local_anchor.x() / old_w
        ry = local_anchor.y() / old_h
        x = self.x() + local_anchor.x() - round(rx * width)
        y = self.y() + local_anchor.y() - round(ry * height)
        self._apply_geometry(x, y, width, height)

    def _current_pixmap(self) -> QPixmap:
        if self.width() == self._source.width() and self.height() == self._source.height():
            return self._source
        return self._source.scaled(
            self.size(),
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation,
        )

    def _copy(self) -> None:
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice

        pix = self._current_pixmap()
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pix.save(buf, "PNG")
        image = Image.open(BytesIO(bytes(ba))).convert("RGB")
        try:
            copy_image(image)
        except Exception:
            QToolTip.showText(QCursor.pos(), "复制失败", self)
            return
        QToolTip.showText(QCursor.pos(), "已复制", self)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawPixmap(self.rect(), self._source)
        painter.setPen(QPen(QColor(51, 112, 255), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setPen(QPen(QColor(51, 112, 255), 1))
        painter.setBrush(QColor(255, 255, 255))
        half = HANDLE_SIZE // 2
        for pt in self._handle_points().values():
            painter.drawRect(pt.x() - half, pt.y() - half, HANDLE_SIZE, HANDLE_SIZE)
        if self._resize_handle is not None:
            self._draw_size_badge(painter)

    def _draw_size_badge(self, painter: QPainter) -> None:
        sx, sy = self._scale_xy()
        text = f"{self.width()} × {self.height()}  {round(sx * 100)}% × {round(sy * 100)}%"
        font = QFont()
        font.setPixelSize(12)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        pad_x, pad_y = 6, 3
        tw = metrics.horizontalAdvance(text) + pad_x * 2
        th = metrics.height() + pad_y * 2
        box = QRect(6, self.height() - th - 6, tw, th)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(20, 20, 20, 210))
        painter.drawRect(box)
        painter.setPen(QColor(242, 242, 242))
        painter.setBrush(Qt.NoBrush)
        painter.drawText(box.adjusted(pad_x, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, text)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton:
            self._copy()
            event.accept()
            return
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        pos = self._local_pos(event)
        handle = self._hit_handle(pos)
        if handle:
            self._resize_handle = handle
            self._resize_origin = QRect(self.geometry())
            self.setCursor(HANDLE_CURSOR[handle])
            event.accept()
            return
        self._drag_pos = self._global_pos(event) - self.frameGeometry().topLeft()
        self.setCursor(Qt.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        pos = self._local_pos(event)
        if self._resize_handle and self._resize_origin is not None:
            self._resize_from_handle(self._resize_handle, self._global_pos(event), self._resize_origin)
            event.accept()
            return
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(self._global_pos(event) - self._drag_pos)
            event.accept()
            return
        self._apply_hover_cursor(pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_pos = None
            self._resize_handle = None
            self._resize_origin = None
            self._apply_hover_cursor(self._local_pos(event))
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            src_w, src_h = self._src_size()
            self._apply_geometry(self.x(), self.y(), src_w, src_h)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        factor = WHEEL_STEP if delta > 0 else 1 / WHEEL_STEP
        self._zoom_at(factor, self._local_pos(event))
        event.accept()

    def enterEvent(self, event) -> None:
        self._apply_hover_cursor(self.mapFromGlobal(QCursor.pos()))
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self._resize_handle is None and self._drag_pos is None:
            self.setCursor(Qt.OpenHandCursor)
        super().leaveEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        self.closed.emit(self)
        super().closeEvent(event)
