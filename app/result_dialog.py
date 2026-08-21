from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class ResultDialog(QDialog):
    def __init__(
        self,
        title: str,
        text: str,
        translation: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.resize(520, 520)
        self._copy_text = translation if translation else text

        layout = QVBoxLayout(self)
        if translation is None:
            layout.addWidget(self._text_edit(text))
        else:
            splitter = QSplitter(Qt.Vertical)
            splitter.setChildrenCollapsible(False)
            splitter.setHandleWidth(8)
            splitter.addWidget(self._labeled_pane("原文", text))
            splitter.addWidget(self._labeled_pane("译文", translation))
            splitter.setStretchFactor(0, 1)
            splitter.setStretchFactor(1, 1)
            splitter.setSizes([220, 220])
            layout.addWidget(splitter)

        row = QHBoxLayout()
        row.addStretch()
        copy_btn = QPushButton("复制")
        close_btn = QPushButton("关闭")
        copy_btn.clicked.connect(self._copy)
        close_btn.clicked.connect(self.accept)
        row.addWidget(copy_btn)
        row.addWidget(close_btn)
        layout.addLayout(row)

    def _text_edit(self, text: str) -> QTextEdit:
        edit = QTextEdit()
        edit.setReadOnly(True)
        edit.setPlainText(text)
        edit.setCursor(Qt.IBeamCursor)
        edit.viewport().setCursor(Qt.IBeamCursor)
        edit.setMinimumHeight(80)
        return edit

    def _labeled_pane(self, title: str, text: str) -> QWidget:
        pane = QWidget()
        box = QVBoxLayout(pane)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)
        box.addWidget(QLabel(title))
        box.addWidget(self._text_edit(text))
        return pane

    def _copy(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self._copy_text)
        QMessageBox.information(self, "NiceShot", "已复制文本")


def show_error(parent, message: str) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle("NiceShot")
    box.setIcon(QMessageBox.Warning)
    box.setText(message)
    box.setWindowFlags(box.windowFlags() | Qt.WindowStaysOnTopHint)
    box.exec()
