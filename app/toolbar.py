from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QColorDialog,
    QHBoxLayout,
    QPushButton,
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


class ColorDot(QPushButton):
    def __init__(self, color: QColor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.color = QColor(color)
        self.setFixedSize(18, 18)
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)
        self.setToolTip(color.name())
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


class CaptureToolbar(QWidget):
    ocr_clicked = Signal()
    translate_clicked = Signal()
    scroll_clicked = Signal()
    confirm_clicked = Signal()
    cancel_requested = Signal()
    tool_changed = Signal(str)
    color_changed = Signal(QColor)
    width_changed = Signal(int)
    undo_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(48)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._color = QColor("#E85454")
        self._width = 4
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
            QPushButton#textBtn:checked {
                background: rgba(51, 112, 255, 90);
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
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        font = QFont()
        font.setPixelSize(13)
        self.ocr_btn = self._text_btn("识别文字", font)
        self.translate_btn = self._text_btn("翻译", font)
        self.scroll_btn = self._text_btn("滚动截图", font)
        self.scroll_btn.setToolTip("在窗口内滚动，拼接成长图")
        self.rect_btn = self._text_btn("矩形", font, checkable=True)
        self.ellipse_btn = self._text_btn("圆形", font, checkable=True)
        self.undo_btn = self._text_btn("撤销", font)
        self.undo_btn.setToolTip("撤销上一笔标注")
        self.cancel_btn = CancelButton()
        self.confirm_btn = ConfirmButton()

        self.ocr_btn.clicked.connect(self.ocr_clicked.emit)
        self.translate_btn.clicked.connect(self.translate_clicked.emit)
        self.scroll_btn.clicked.connect(self.scroll_clicked.emit)
        self.rect_btn.clicked.connect(self._on_rect_tool)
        self.ellipse_btn.clicked.connect(self._on_ellipse_tool)
        self.undo_btn.clicked.connect(self.undo_clicked.emit)
        self.cancel_btn.clicked.connect(self.cancel_requested.emit)
        self.confirm_btn.clicked.connect(self.confirm_clicked.emit)

        self.color_dots: list[ColorDot] = []
        self.width_btns: list[QPushButton] = []

        layout.addWidget(self.ocr_btn)
        layout.addWidget(self.translate_btn)
        layout.addWidget(self.scroll_btn)
        layout.addWidget(self.rect_btn)
        layout.addWidget(self.ellipse_btn)

        self._style_widgets: list[QWidget] = []
        for color in PRESET_COLORS:
            dot = ColorDot(color)
            dot.clicked.connect(lambda checked=False, c=color: self._set_color(c))
            self.color_dots.append(dot)
            self._style_widgets.append(dot)
            layout.addWidget(dot)
        more = QPushButton("+")
        more.setObjectName("widthBtn")
        more.setFixedSize(18, 18)
        more.setCursor(Qt.PointingHandCursor)
        more.setToolTip("自定义颜色")
        more.clicked.connect(self._pick_color)
        self._style_widgets.append(more)
        layout.addWidget(more)
        for width in PRESET_WIDTHS:
            btn = QPushButton(str(width))
            btn.setObjectName("widthBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(f"边框粗细 {width}px")
            btn.clicked.connect(lambda checked=False, w=width: self._set_width(w))
            self.width_btns.append(btn)
            self._style_widgets.append(btn)
            layout.addWidget(btn)

        layout.addWidget(self.undo_btn)
        layout.addWidget(self.cancel_btn)
        layout.addWidget(self.confirm_btn)
        self._sync_style()
        self._set_style_visible(False)
        self.adjustSize()

    def _text_btn(self, text: str, font: QFont, checkable: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("textBtn")
        btn.setFont(font)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setCheckable(checkable)
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
        self._set_style_visible(bool(tool))
        self.adjustSize()
        self.tool_changed.emit(tool)

    def _set_style_visible(self, visible: bool) -> None:
        for widget in self._style_widgets:
            widget.setVisible(visible)

    def _set_color(self, color: QColor) -> None:
        self._color = QColor(color)
        self._sync_style()
        self.color_changed.emit(self._color)

    def _set_width(self, width: int) -> None:
        self._width = width
        self._sync_style()
        self.width_changed.emit(width)

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(self._color, self, "选择边框颜色")
        if color.isValid():
            self._set_color(color)

    def _sync_style(self) -> None:
        for dot in self.color_dots:
            dot.setChecked(dot.color.name().lower() == self._color.name().lower())
        for btn, width in zip(self.width_btns, PRESET_WIDTHS):
            btn.setChecked(width == self._width)

    def current_color(self) -> QColor:
        return QColor(self._color)

    def current_width(self) -> int:
        return self._width

    def sizeHint(self) -> QSize:
        return QSize(max(self.minimumSizeHint().width(), 200), 48)

    def set_busy(self, busy: bool) -> None:
        for btn in (
            self.ocr_btn,
            self.translate_btn,
            self.scroll_btn,
            self.rect_btn,
            self.ellipse_btn,
            self.undo_btn,
            self.cancel_btn,
            self.confirm_btn,
        ):
            btn.setEnabled(not busy)
        for widget in self._style_widgets:
            widget.setEnabled(not busy)
        self.ocr_btn.setText("处理中…" if busy else "识别文字")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton:
            self.cancel_requested.emit()
            event.accept()
            return
        super().mousePressEvent(event)
