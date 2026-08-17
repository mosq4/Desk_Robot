"""G-code 预览面板。"""
from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (QCheckBox, QHBoxLayout, QPlainTextEdit,
                             QPushButton, QVBoxLayout, QWidget)


class GcodeTab(QWidget):
    generate_requested = pyqtSignal()
    save_requested = pyqtSignal(str)
    copy_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText(
            "点击「从当前轨迹生成」生成 G-code；也可直接编辑后下发"
        )
        self.edit.setFont(QFont("Consolas", 9))

        btn_gen = QPushButton("从当前轨迹生成")
        btn_gen.clicked.connect(self.generate_requested)
        btn_save = QPushButton("保存为文件…")
        btn_save.clicked.connect(self._on_save)
        btn_copy = QPushButton("复制")
        btn_copy.clicked.connect(
            lambda: self.copy_requested.emit(self.edit.toPlainText())
        )

        self.chk_auto_opt = QCheckBox("生成时自动优化笔画顺序（优先画相邻笔画，减少空走）")
        self.chk_auto_opt.setChecked(True)

        row = QHBoxLayout()
        row.addWidget(btn_gen)
        row.addWidget(btn_save)
        row.addWidget(btn_copy)
        row.addStretch(1)

        v = QVBoxLayout(self)
        v.addLayout(row)
        v.addWidget(self.chk_auto_opt)
        v.addWidget(self.edit)

    def _on_save(self):
        self.save_requested.emit(self.edit.toPlainText())

    def set_text(self, text: str):
        self.edit.setPlainText(text)

    def text(self) -> str:
        return self.edit.toPlainText()
