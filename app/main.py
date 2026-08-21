from __future__ import annotations

import ctypes
import os
import sys

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from app.autostart import is_enabled as autostart_enabled
from app.autostart import set_enabled as set_autostart
from app.config import load_config, save_config
from app.hotkey import GlobalHotkey, format_key_event
from app.overlay import CaptureOverlay, grab_virtual_desktop
from app.window_detect import enum_windows


def enable_dpi_aware() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "0")


def make_tray_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(51, 112, 255))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)
    painter.setPen(QPen(QColor(255, 255, 255), 4))
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(16, 20, 32, 24, 4, 4)
    painter.drawLine(16, 20, 32, 12)
    painter.drawLine(48, 20, 32, 12)
    painter.end()
    return QIcon(pix)


class HotkeyDialog(QDialog):
    def __init__(self, current: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置快捷键")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.value = current
        self.setFixedSize(320, 150)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("请按下新的快捷键（需包含 Ctrl / Shift / Alt / Win）"))
        self.label = QLabel(current)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("font-size: 18px; padding: 12px;")
        layout.addWidget(self.label)
        row = QHBoxLayout()
        row.addStretch()
        ok = QPushButton("确定")
        cancel = QPushButton("取消")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        layout.addLayout(row)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.grabKeyboard()

    def closeEvent(self, event) -> None:
        self.releaseKeyboard()
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        formatted = format_key_event(event)
        if formatted:
            self.value = formatted
            self.label.setText(formatted)


class NiceShotApp:
    def __init__(self, qt_app: QApplication) -> None:
        self.app = qt_app
        self.app.setQuitOnLastWindowClosed(False)
        self.config = load_config()
        self._overlay: CaptureOverlay | None = None

        self.host = QWidget()
        self.host.setWindowTitle("NiceShotHost")
        self.host.setWindowFlags(Qt.Tool)
        self.host.resize(1, 1)
        self.host.move(-32000, -32000)
        hwnd = int(self.host.winId())

        self.hotkey = GlobalHotkey(hwnd, 1, lambda: self.start_capture(0))
        self._register_hotkey(self.config.get("hotkey", "Ctrl+Shift+A"), warn=True)

        self.tray = QSystemTrayIcon(make_tray_icon(), self.app)
        self.tray.setToolTip(f"NiceShot  {self.config.get('hotkey', '')}")
        self.menu = QMenu()
        self.act_capture = QAction("开始截图", self.menu)
        self.act_hotkey = QAction(self._hotkey_action_text(), self.menu)
        self.act_autostart = QAction("开机启动", self.menu)
        self.act_autostart.setCheckable(True)
        self.act_autostart.setChecked(autostart_enabled())
        self.act_quit = QAction("退出", self.menu)
        self.menu.addAction(self.act_capture)
        self.menu.addAction(self.act_hotkey)
        self.menu.addAction(self.act_autostart)
        self.menu.addSeparator()
        self.menu.addAction(self.act_quit)
        self.tray.setContextMenu(self.menu)

        self.act_capture.triggered.connect(lambda: self.start_capture(150))
        self.act_hotkey.triggered.connect(self._change_hotkey)
        self.act_autostart.triggered.connect(self._toggle_autostart)
        self.act_quit.triggered.connect(self.quit)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _hotkey_action_text(self) -> str:
        return f"设置快捷键（{self.config.get('hotkey', '')}）"

    def _register_hotkey(self, text: str, warn: bool = False) -> bool:
        try:
            self.hotkey.register(text)
            return True
        except Exception as exc:
            if warn:
                QTimer.singleShot(
                    300,
                    lambda: QMessageBox.warning(None, "NiceShot", str(exc)),
                )
            else:
                QMessageBox.warning(None, "NiceShot", str(exc))
            return False

    def start_capture(self, delay_ms: int = 0) -> None:
        if self._overlay is not None:
            return
        if delay_ms:
            QTimer.singleShot(delay_ms, self._do_capture)
        else:
            self._do_capture()

    def _do_capture(self) -> None:
        if self._overlay is not None:
            return
        screenshot, origin = grab_virtual_desktop()
        windows = enum_windows()
        overlay = CaptureOverlay(screenshot, origin, windows)
        overlay.closed.connect(self._on_overlay_closed)
        overlay.copied.connect(self._on_copied)
        self._overlay = overlay
        overlay.show()
        overlay.activateWindow()
        overlay.setFocus()

    def _on_overlay_closed(self) -> None:
        self._overlay = None

    def _on_copied(self) -> None:
        self.tray.showMessage("NiceShot", "已复制到剪贴板，可粘贴到文件夹或其它程序", QSystemTrayIcon.Information, 2000)

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.start_capture(150)

    def _change_hotkey(self) -> None:
        dialog = HotkeyDialog(self.config.get("hotkey", "Ctrl+Shift+A"))
        if dialog.exec() != QDialog.Accepted:
            return
        if not self._register_hotkey(dialog.value):
            self._register_hotkey(self.config.get("hotkey", "Ctrl+Shift+A"))
            return
        self.config["hotkey"] = dialog.value
        save_config(self.config)
        self.act_hotkey.setText(self._hotkey_action_text())
        self.tray.setToolTip(f"NiceShot  {dialog.value}")

    def _toggle_autostart(self, checked: bool) -> None:
        try:
            set_autostart(checked)
            self.config["autostart"] = checked
            save_config(self.config)
        except Exception as exc:
            self.act_autostart.setChecked(autostart_enabled())
            QMessageBox.warning(None, "NiceShot", f"设置开机启动失败：{exc}")

    def quit(self) -> None:
        self.hotkey.unregister()
        if self._overlay is not None:
            self._overlay.close()
        self.tray.hide()
        self.app.quit()


def main() -> None:
    enable_dpi_aware()
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NiceShot.Screenshot")
    except Exception:
        pass

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("NiceShot")
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "NiceShot", "系统托盘不可用，无法常驻运行。")
        sys.exit(1)

    holder = NiceShotApp(qt_app)
    _ = holder
    sys.exit(qt_app.exec())


if __name__ == "__main__":
    main()
