from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget


class ConfirmButton(QPushButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(36, 36)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("复制截图到剪贴板")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        hovered = self.underMouse()
        painter.setBrush(QColor(38, 198, 102) if not hovered else QColor(52, 214, 118))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(2, 2, 32, 32)
        painter.setPen(QPen(QColor(255, 255, 255), 3))
        painter.drawLine(10, 19, 16, 25)
        painter.drawLine(16, 25, 26, 13)


class CaptureToolbar(QWidget):
    ocr_clicked = Signal()
    translate_clicked = Signal()
    confirm_clicked = Signal()
    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(48)
        self.setAttribute(Qt.WA_StyledBackground, True)
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
                padding: 6px 14px;
                font-size: 13px;
            }
            QPushButton#textBtn:hover {
                background: rgba(255, 255, 255, 28);
                border-radius: 6px;
            }
            QPushButton#textBtn:disabled {
                color: #888;
            }
            """
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        font = QFont()
        font.setPixelSize(13)
        self.ocr_btn = QPushButton("识别文字")
        self.ocr_btn.setObjectName("textBtn")
        self.ocr_btn.setFont(font)
        self.ocr_btn.setCursor(Qt.PointingHandCursor)
        self.translate_btn = QPushButton("翻译")
        self.translate_btn.setObjectName("textBtn")
        self.translate_btn.setFont(font)
        self.translate_btn.setCursor(Qt.PointingHandCursor)
        self.confirm_btn = ConfirmButton()

        self.ocr_btn.clicked.connect(self.ocr_clicked.emit)
        self.translate_btn.clicked.connect(self.translate_clicked.emit)
        self.confirm_btn.clicked.connect(self.confirm_clicked.emit)

        layout.addWidget(self.ocr_btn)
        layout.addWidget(self.translate_btn)
        layout.addWidget(self.confirm_btn)
        self.adjustSize()

    def sizeHint(self) -> QSize:
        return QSize(max(self.minimumSizeHint().width(), 200), 48)

    def set_busy(self, busy: bool) -> None:
        self.ocr_btn.setEnabled(not busy)
        self.translate_btn.setEnabled(not busy)
        self.confirm_btn.setEnabled(not busy)
        self.ocr_btn.setText("处理中…" if busy else "识别文字")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton:
            self.cancel_requested.emit()
            event.accept()
            return
        super().mousePressEvent(event)
