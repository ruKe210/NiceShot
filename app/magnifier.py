from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

SAMPLE = 15
ZOOM = 12
INFO_H = 40


class Magnifier(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        side = SAMPLE * ZOOM
        self.setFixedSize(side, side + INFO_H)
        self._pixmap = QPixmap()
        self._image = None
        self._cursor = QPoint(0, 0)
        self._sel_size: tuple[int, int] | None = None

    def set_source(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self._image = pixmap.toImage() if not pixmap.isNull() else None

    def update_at(self, pos: QPoint, sel_size: tuple[int, int] | None = None) -> None:
        self._cursor = pos
        self._sel_size = sel_size
        self.update()

    def place_near(self, cursor_global_in_parent: QPoint, bounds: QRect) -> None:
        margin = 18
        x = cursor_global_in_parent.x() + margin
        y = cursor_global_in_parent.y() + margin
        if x + self.width() > bounds.right():
            x = cursor_global_in_parent.x() - self.width() - margin
        if y + self.height() > bounds.bottom():
            y = cursor_global_in_parent.y() - self.height() - margin
        x = max(bounds.left(), min(x, bounds.right() - self.width()))
        y = max(bounds.top(), min(y, bounds.bottom() - self.height()))
        self.move(x, y)

    def sizeHint(self) -> QSize:
        return self.size()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(20, 20, 20))
        if self._pixmap.isNull():
            return

        cx, cy = self._cursor.x(), self._cursor.y()
        half = SAMPLE // 2
        src = QRect(cx - half, cy - half, SAMPLE, SAMPLE)
        dest = QRect(0, 0, SAMPLE * ZOOM, SAMPLE * ZOOM)
        painter.drawPixmap(dest, self._pixmap, src)

        painter.setPen(QPen(QColor(60, 60, 60), 1))
        for i in range(SAMPLE + 1):
            p = i * ZOOM
            painter.drawLine(p, 0, p, SAMPLE * ZOOM)
            painter.drawLine(0, p, SAMPLE * ZOOM, p)

        mid = half * ZOOM
        painter.setPen(QPen(QColor(255, 70, 70), 2))
        painter.drawRect(mid, mid, ZOOM, ZOOM)

        rgb = QColor(0, 0, 0)
        if (
            self._image is not None
            and 0 <= cx < self._image.width()
            and 0 <= cy < self._image.height()
        ):
            rgb = QColor(self._image.pixel(cx, cy))

        painter.fillRect(0, SAMPLE * ZOOM, self.width(), INFO_H, QColor(24, 24, 24))
        painter.setPen(QColor(240, 240, 240))
        font = QFont()
        font.setPixelSize(12)
        painter.setFont(font)
        lines = [f"{cx}, {cy}  RGB({rgb.red()},{rgb.green()},{rgb.blue()})"]
        if self._sel_size:
            lines.append(f"{self._sel_size[0]} × {self._sel_size[1]}")
        painter.drawText(
            QRect(8, SAMPLE * ZOOM, self.width() - 16, INFO_H),
            Qt.AlignVCenter | Qt.AlignLeft,
            "\n".join(lines),
        )
        painter.setPen(QPen(QColor(51, 112, 255), 1))
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
