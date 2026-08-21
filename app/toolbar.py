from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QColorDialog,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

PRESET_COLORS = [
    QColor("#E85454"),
    QColor("#FF8A00"),
    QColor("#F7C948"),
    QColor("#26C666"),
    QColor("#3370FF"),
    QColor("#FFFFFF"),
]
PRESET_WIDTHS = (2, 4, 6, 8)


class CircleIconButton(QPushButton):
    def __init__(self, tooltip: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(36, 36)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)

    def _draw_circle(self, painter: QPainter, color: QColor, hover: QColor) -> None:
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(hover if self.underMouse() else color)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(2, 2, 32, 32)
        painter.setPen(QPen(QColor(255, 255, 255), 3))


class CancelButton(CircleIconButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("取消本次截图", parent)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        self._draw_circle(painter, QColor(232, 84, 84), QColor(244, 108, 108))
        painter.drawLine(12, 12, 24, 24)
        painter.drawLine(24, 12, 12, 24)


class ConfirmButton(CircleIconButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("复制截图到剪贴板", parent)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        self._draw_circle(painter, QColor(38, 198, 102), QColor(52, 214, 118))
        painter.drawLine(10, 19, 16, 25)
        painter.drawLine(16, 25, 26, 13)


class ShapeToolButton(QPushButton):
    def __init__(self, kind: str, tooltip: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(36, 36)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setToolTipDuration(5000)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        hovered = self.underMouse()
        checked = self.isChecked()
        if checked:
            bg = QColor(51, 112, 255, 90)
        elif hovered:
            bg = QColor(255, 255, 255, 28)
        else:
            bg = QColor(0, 0, 0, 0)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(2, 2, 32, 32, 6, 6)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(242, 242, 242), 2))
        if self.kind == "rect":
            painter.drawRect(9, 11, 18, 14)
        else:
            painter.drawEllipse(9, 9, 18, 18)


class ColorDot(QPushButton):
    def __init__(self, color: QColor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.color = QColor(color)
        self.setFixedSize(18, 18)
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)
        self.setToolTip(f"边框颜色 {color.name().upper()}")
        self.setToolTipDuration(4000)
        self._apply()

    def _apply(self) -> None:
        border = "#ffffff" if self.isChecked() else "#00000000"
        self.setStyleSheet(
            f"background: {self.color.name()}; border: 2px solid {border}; border-radius: 9px;"
        )

    def nextCheckState(self) -> None:
        if not self.isChecked():
            self.setChecked(True)

    def checkStateSet(self) -> None:
        super().checkStateSet()
        self._apply()


class StyleBar(QWidget):
    color_changed = Signal(QColor)
    width_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor("#E85454")
        self._width = 4
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 6)
        layout.setSpacing(6)
        self.color_dots: list[ColorDot] = []
        self.width_btns: list[QPushButton] = []
        for color in PRESET_COLORS:
            dot = ColorDot(color)
            dot.clicked.connect(lambda checked=False, c=color: self.set_color(c))
            self.color_dots.append(dot)
            layout.addWidget(dot)
        more = QPushButton("+")
        more.setObjectName("widthBtn")
        more.setFixedSize(18, 18)
        more.setCursor(Qt.PointingHandCursor)
        more.setToolTip("自定义边框颜色")
        more.setToolTipDuration(4000)
        more.clicked.connect(self._pick_color)
        layout.addWidget(more)
        for width in PRESET_WIDTHS:
            btn = QPushButton(str(width))
            btn.setObjectName("widthBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(f"边框粗细 {width} 像素")
            btn.setToolTipDuration(4000)
            btn.clicked.connect(lambda checked=False, w=width: self.set_width(w))
            self.width_btns.append(btn)
            layout.addWidget(btn)
        layout.addStretch()
        self._sync()

    def set_color(self, color: QColor) -> None:
        self._color = QColor(color)
        self._sync()
        self.color_changed.emit(self._color)

    def set_width(self, width: int) -> None:
        self._width = width
        self._sync()
        self.width_changed.emit(width)

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(self._color, self, "选择边框颜色")
        if color.isValid():
            self.set_color(color)

    def _sync(self) -> None:
        for dot in self.color_dots:
            dot.setChecked(dot.color.name().lower() == self._color.name().lower())
        for btn, width in zip(self.width_btns, PRESET_WIDTHS):
            btn.setChecked(width == self._width)

    def current_color(self) -> QColor:
        return QColor(self._color)

    def current_width(self) -> int:
        return self._width


class CaptureToolbar(QWidget):
    pin_clicked = Signal()
    ocr_clicked = Signal()
    translate_clicked = Signal()
    scroll_clicked = Signal()
    confirm_clicked = Signal()
    cancel_requested = Signal()
    tool_changed = Signal(str)
    color_changed = Signal(QColor)
    width_changed = Signal(int)
    undo_clicked = Signal()
    redo_clicked = Signal()
    pointer_entered = Signal()
    pointer_left = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self.setStyleSheet(
            """
            CaptureToolbar {
                background: rgba(32, 32, 32, 235);
                border-radius: 8px;
            }
            QPushButton#textBtn {
                color: #f2f2f2;
                background: transparent;
                border: none;
                padding: 6px 10px;
                font-size: 13px;
            }
            QPushButton#textBtn:hover {
                background: rgba(255, 255, 255, 28);
                border-radius: 6px;
            }
            QPushButton#textBtn:disabled { color: #888; }
            QPushButton#widthBtn {
                color: #f2f2f2;
                background: transparent;
                border: none;
                min-width: 22px;
                padding: 4px 6px;
            }
            QPushButton#widthBtn:checked {
                background: rgba(51, 112, 255, 90);
                border-radius: 4px;
            }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(0)

        row = QHBoxLayout()
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(4)
        font = QFont()
        font.setPixelSize(13)

        self.rect_btn = ShapeToolButton("rect", "画矩形框")
        self.ellipse_btn = ShapeToolButton("ellipse", "画圆形框")
        self.pin_btn = self._text_btn("固定到屏幕", font)
        self.pin_btn.setToolTip("把当前截图钉在屏幕上；可拖动、横竖拉伸、滚轮缩放，右键复制")
        self.ocr_btn = self._text_btn("识别文字", font)
        self.ocr_btn.setToolTip("识别选区内的中英文")
        self.translate_btn = self._text_btn("翻译", font)
        self.translate_btn.setToolTip("识别选区文字并中英互译")
        self.scroll_btn = self._text_btn("滚动截图", font)
        self.scroll_btn.setToolTip("在窗口内滚动，拼接成长图")
        self.undo_btn = self._text_btn("撤销", font)
        self.undo_btn.setToolTip("撤销上一笔标注（Ctrl+Z）")
        self.redo_btn = self._text_btn("前进", font)
        self.redo_btn.setToolTip("恢复撤销的标注（Ctrl+Y）")
        self.cancel_btn = CancelButton()
        self.confirm_btn = ConfirmButton()

        self.pin_btn.clicked.connect(self.pin_clicked.emit)
        self.ocr_btn.clicked.connect(self.ocr_clicked.emit)
        self.translate_btn.clicked.connect(self.translate_clicked.emit)
        self.scroll_btn.clicked.connect(self.scroll_clicked.emit)
        self.rect_btn.clicked.connect(self._on_rect_tool)
        self.ellipse_btn.clicked.connect(self._on_ellipse_tool)
        self.undo_btn.clicked.connect(self.undo_clicked.emit)
        self.redo_btn.clicked.connect(self.redo_clicked.emit)
        self.cancel_btn.clicked.connect(self.cancel_requested.emit)
        self.confirm_btn.clicked.connect(self.confirm_clicked.emit)

        row.addWidget(self.rect_btn)
        row.addWidget(self.ellipse_btn)
        row.addWidget(self.pin_btn)
        row.addWidget(self.ocr_btn)
        row.addWidget(self.translate_btn)
        row.addWidget(self.scroll_btn)
        row.addWidget(self.undo_btn)
        row.addWidget(self.redo_btn)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.confirm_btn)
        root.addLayout(row)

        self.style_bar = StyleBar()
        self.style_bar.color_changed.connect(self.color_changed.emit)
        self.style_bar.width_changed.connect(self.width_changed.emit)
        self.style_bar.hide()
        root.addWidget(self.style_bar)
        self.adjustSize()

    def _text_btn(self, text: str, font: QFont) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("textBtn")
        btn.setFont(font)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTipDuration(4000)
        return btn

    def _on_rect_tool(self) -> None:
        if self.rect_btn.isChecked():
            self.ellipse_btn.setChecked(False)
            self._apply_tool("rect")
        else:
            self._apply_tool("")

    def _on_ellipse_tool(self) -> None:
        if self.ellipse_btn.isChecked():
            self.rect_btn.setChecked(False)
            self._apply_tool("ellipse")
        else:
            self._apply_tool("")

    def _apply_tool(self, tool: str) -> None:
        self.style_bar.setVisible(bool(tool))
        self.adjustSize()
        self.tool_changed.emit(tool)

    def current_color(self) -> QColor:
        return self.style_bar.current_color()

    def current_width(self) -> int:
        return self.style_bar.current_width()

    def sizeHint(self) -> QSize:
        extra = 34 if self.style_bar.isVisible() else 0
        return QSize(max(self.minimumSizeHint().width(), 280), 48 + extra)

    def set_busy(self, busy: bool) -> None:
        for btn in (
            self.pin_btn,
            self.ocr_btn,
            self.translate_btn,
            self.scroll_btn,
            self.rect_btn,
            self.ellipse_btn,
            self.undo_btn,
            self.redo_btn,
            self.cancel_btn,
            self.confirm_btn,
        ):
            btn.setEnabled(not busy)
        self.style_bar.setEnabled(not busy)
        self.ocr_btn.setText("处理中…" if busy else "识别文字")

    def enterEvent(self, event) -> None:
        self.pointer_entered.emit()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        from PySide6.QtGui import QCursor

        if not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            self.pointer_left.emit()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton:
            self.cancel_requested.emit()
            event.accept()
            return
        super().mousePressEvent(event)
