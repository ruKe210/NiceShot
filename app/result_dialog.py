from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
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
        self.resize(460, 360)
        self._copy_text = translation if translation else text

        layout = QVBoxLayout(self)
        if translation is None:
            edit = QTextEdit()
            edit.setReadOnly(True)
            edit.setPlainText(text)
            edit.setCursor(Qt.IBeamCursor)
            edit.viewport().setCursor(Qt.IBeamCursor)
            layout.addWidget(edit)
        else:
            layout.addWidget(QLabel("原文"))
            src = QTextEdit()
            src.setReadOnly(True)
            src.setPlainText(text)
            src.setFixedHeight(120)
            src.setCursor(Qt.IBeamCursor)
            src.viewport().setCursor(Qt.IBeamCursor)
            layout.addWidget(src)
            layout.addWidget(QLabel("译文"))
            dst = QTextEdit()
            dst.setReadOnly(True)
            dst.setPlainText(translation)
            dst.setCursor(Qt.IBeamCursor)
            dst.viewport().setCursor(Qt.IBeamCursor)
            layout.addWidget(dst)

        row = QHBoxLayout()
        row.addStretch()
        copy_btn = QPushButton("复制")
        close_btn = QPushButton("关闭")
        copy_btn.clicked.connect(self._copy)
        close_btn.clicked.connect(self.accept)
        row.addWidget(copy_btn)
        row.addWidget(close_btn)
        layout.addLayout(row)

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
