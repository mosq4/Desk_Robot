"""日志面板：界面显示 + 落盘（logs/deskrobot_YYYYMMDD.log）。"""
from __future__ import annotations

import os
import time

from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                             QVBoxLayout, QWidget)

_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "logs",
)


class LogPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.edit = QPlainTextEdit()
        self.edit.setReadOnly(True)
        self.edit.setMaximumBlockCount(2000)
        self.edit.setFont(QFont("Consolas", 8))

        os.makedirs(_LOG_DIR, exist_ok=True)
        self._log_path = os.path.join(_LOG_DIR, time.strftime("deskrobot_%Y%m%d.log"))

        btn_clear = QPushButton("清空日志")
        btn_clear.clicked.connect(self.edit.clear)

        row = QHBoxLayout()
        row.addWidget(QLabel("日志"))
        row.addStretch(1)
        row.addWidget(btn_clear)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 2)
        v.addLayout(row)
        v.addWidget(self.edit)

    def append(self, text: str):
        line = f"[{time.strftime('%H:%M:%S')}] {text}"
        self.edit.appendPlainText(line)
        self._to_file(line)

    def append_exception(self, text: str):
        """写入未捕获异常的完整 traceback（多行）。"""
        ts = time.strftime("%H:%M:%S")
        block = f"[{ts}] 异常:\n{text.rstrip()}"
        self.edit.appendPlainText(block)
        self._to_file(block)

    def _to_file(self, line: str):
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
