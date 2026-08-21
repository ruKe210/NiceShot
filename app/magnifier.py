from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

SAMPLE = 15
ZOOM = 12
INFO_H = 48
MODE_HEX = "hex"
MODE_DEC = "dec"


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
        self._mode = MODE_HEX
        self._copied = False
        self._copied_code = ""

    def set_source(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self._image = pixmap.toImage() if not pixmap.isNull() else None

    def update_at(self, pos: QPoint, sel_size: tuple[int, int] | None = None) -> None:
        self._cursor = pos
        self._sel_size = sel_size
        if self._copied and self.color_code() != self._copied_code:
            self._copied = False
            self._copied_code = ""
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

    def current_color(self) -> QColor:
        if self._image is None:
            return QColor(0, 0, 0)
        cx, cy = self._cursor.x(), self._cursor.y()
        if 0 <= cx < self._image.width() and 0 <= cy < self._image.height():
            return QColor(self._image.pixel(cx, cy))
        return QColor(0, 0, 0)

    def color_code(self) -> str:
        color = self.current_color()
        if self._mode == MODE_DEC:
            return f"{color.red()},{color.green()},{color.blue()}"
        return f"#{color.red():02X}{color.green():02X}{color.blue():02X}"

    def toggle_mode(self) -> str:
        self._mode = MODE_DEC if self._mode == MODE_HEX else MODE_HEX
        self._copied = False
        self._copied_code = ""
        self.update()
        return self._mode

    def mark_copied(self) -> None:
        self._copied = True
        self._copied_code = self.color_code()
        self.update()

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

        color = self.current_color()
        code = self.color_code()
        painter.fillRect(0, SAMPLE * ZOOM, self.width(), INFO_H, QColor(24, 24, 24))
        painter.setPen(QColor(240, 240, 240))
        font = QFont()
        font.setPixelSize(12)
        painter.setFont(font)
        mode_name = "HEX" if self._mode == MODE_HEX else "RGB"
        lines = [f"{cx}, {cy}  {code}"]
        if self._copied:
            lines.append("已复制")
        elif self._sel_size:
            lines.append(f"{self._sel_size[0]} × {self._sel_size[1]}  {mode_name}")
        else:
            lines.append(f"{mode_name}  Shift切换  Ctrl+C复制")
        painter.drawText(
            QRect(8, SAMPLE * ZOOM, self.width() - 16, INFO_H),
            Qt.AlignVCenter | Qt.AlignLeft,
            "\n".join(lines),
        )
        swatch = QRect(self.width() - 22, SAMPLE * ZOOM + 8, 14, 14)
        painter.setPen(QPen(QColor(255, 255, 255), 1))
        painter.setBrush(color)
        painter.drawRect(swatch)
        painter.setPen(QPen(QColor(51, 112, 255), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
