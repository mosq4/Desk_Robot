"""手绘轨迹面板。"""
from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSpinBox,
                             QVBoxLayout, QWidget)


class HandTab(QWidget):
    draw_toggled = pyqtSignal(bool)
    undo_requested = pyqtSignal()
    clear_requested = pyqtSignal()
    smooth_requested = pyqtSignal(int)
    reorder_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.btn_draw = QPushButton("开始绘制")
        self.btn_draw.setCheckable(True)
        self.btn_draw.toggled.connect(self.draw_toggled)

        self.spin_smooth = QSpinBox()
        self.spin_smooth.setRange(1, 4)
        self.spin_smooth.setValue(1)
        self.spin_smooth.setSuffix(" 次")
        self.btn_smooth = QPushButton("平滑")
        self.btn_smooth.clicked.connect(
            lambda: self.smooth_requested.emit(self.spin_smooth.value())
        )
        self.btn_reorder = QPushButton("笔画排序")
        self.btn_reorder.setToolTip("按最近邻重排笔画顺序，优先画相邻笔画，减少抬笔空走")
        self.btn_reorder.clicked.connect(self.reorder_requested)

        self.btn_undo = QPushButton("撤销")
        self.btn_undo.clicked.connect(self.undo_requested)
        self.btn_clear = QPushButton("清空")
        self.btn_clear.clicked.connect(self.clear_requested)

        row1 = QHBoxLayout()
        row1.addWidget(self.btn_draw)
        row1.addStretch(1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("平滑:"))
        row2.addWidget(self.spin_smooth)
        row2.addWidget(self.btn_smooth)
        row2.addWidget(self.btn_reorder)
        row2.addStretch(1)

        row3 = QHBoxLayout()
        row3.addWidget(self.btn_undo)
        row3.addWidget(self.btn_clear)
        row3.addStretch(1)

        v = QVBoxLayout(self)
        v.addLayout(row1)
        v.addWidget(QLabel("在画布上按住左键绘制；滚轮缩放（以光标为中心），右键拖动画布。"))
        v.addWidget(QLabel("顶部工具条可放大/缩小/适应窗口（快捷键 Ctrl+± / Ctrl+0）。"))
        v.addWidget(QLabel("坐标单位为毫米（mm）。"))
        v.addLayout(row2)
        v.addLayout(row3)
        v.addStretch(1)
