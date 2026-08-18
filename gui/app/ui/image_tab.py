"""图片 → 线稿 面板。"""
from __future__ import annotations

import os

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout,
                             QGroupBox, QHBoxLayout, QLabel, QPushButton,
                             QSlider, QSpinBox, QVBoxLayout, QWidget, QApplication)

from ..core.lineart import LineartParams
from ..settings import PROJECT_DIR


class ImageTab(QWidget):
    import_requested = pyqtSignal(str)          # 图片路径
    preview_requested = pyqtSignal(object)      # LineartParams（调参防抖触发）
    commit_requested = pyqtSignal(object)       # LineartParams（生成线稿）
    orient_requested = pyqtSignal(str)          # cw/ccw/flip_h/flip_v/reset
    log = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path = None
        self._last_dir = None  # 文件对话框起始目录（首次默认项目目录）

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(100)  # 拖动滑块时的预览刷新延迟
        self._debounce.timeout.connect(self._emit_preview)

        btn_import = QPushButton("导入图片…")
        btn_import.clicked.connect(self._on_import)
        self.lbl_img = QLabel("未导入图片")
        self.lbl_img.setAlignment(Qt.AlignCenter)
        self.lbl_img.setMinimumHeight(160)
        self.lbl_img.setStyleSheet(
            "border:1px dashed #aaa; color:#888; border-radius:4px;"
        )

        # ---- 方向调节（纸张横置在桌面上，图片需要旋转/翻转来匹配）----
        g_dir = QGroupBox("方向")
        btn_ccw = QPushButton("左转90°")
        btn_ccw.clicked.connect(lambda: self.orient_requested.emit("ccw"))
        btn_cw = QPushButton("右转90°")
        btn_cw.clicked.connect(lambda: self.orient_requested.emit("cw"))
        btn_fh = QPushButton("水平翻转")
        btn_fh.clicked.connect(lambda: self.orient_requested.emit("flip_h"))
        btn_fv = QPushButton("垂直翻转")
        btn_fv.clicked.connect(lambda: self.orient_requested.emit("flip_v"))
        btn_reset = QPushButton("重置")
        btn_reset.clicked.connect(lambda: self.orient_requested.emit("reset"))
        row1 = QHBoxLayout()
        row1.addWidget(btn_ccw)
        row1.addWidget(btn_cw)
        row2 = QHBoxLayout()
        row2.addWidget(btn_fh)
        row2.addWidget(btn_fv)
        row2.addWidget(btn_reset)
        v_dir = QVBoxLayout()
        v_dir.addLayout(row1)
        v_dir.addLayout(row2)
        g_dir.setLayout(v_dir)

        # ---- 参数区 ----
        g = QGroupBox("线稿参数")
        self.cmb_mode = QComboBox()
        _MODES = [
            ("二值化", "binary", "全局阈值二值化，适合对比度高的图"),
            ("Canny 边缘", "canny", "Canny 边缘检测，对噪声较稳健"),
            ("自适应阈值", "adaptive", "局部阈值，适合光照不均的图"),
            ("黑帽细线", "blackhat", "形态学提取暗色细笔划（白底黑线图）；反色不生效"),
            ("Sobel 梯度", "sobel", "梯度幅值边缘，对噪声敏感，阈值建议调高"),
            ("XDoG 线条", "xdog", "扩展高斯差分素描线条；模糊=σ，阈值=线条阈值；反色不生效"),
        ]
        for label, val, tip in _MODES:
            self.cmb_mode.addItem(label, val)
            self.cmb_mode.setItemData(self.cmb_mode.count() - 1, tip,
                                      Qt.ToolTipRole)

        self.sld_thresh = QSlider(Qt.Horizontal)
        self.sld_thresh.setRange(0, 255)
        self.lbl_thresh = QLabel()

        self.sld_canny_low = QSlider(Qt.Horizontal)
        self.sld_canny_low.setRange(0, 255)
        self.lbl_canny_low = QLabel()
        self.sld_canny_high = QSlider(Qt.Horizontal)
        self.sld_canny_high.setRange(1, 255)
        self.lbl_canny_high = QLabel()

        self.sld_blur = QSlider(Qt.Horizontal)
        self.sld_blur.setRange(0, 10)
        self.lbl_blur = QLabel()

        self.sld_simplify = QSlider(Qt.Horizontal)
        self.sld_simplify.setRange(0, 50)
        self.lbl_simplify = QLabel()

        self.spin_min_area = QSpinBox()
        self.spin_min_area.setRange(0, 10000)
        self.spin_min_area.setSuffix(" px²")

        self.chk_invert = QCheckBox("反色（白底黑线）")
        self.chk_fit = QCheckBox("自动缩放居中到工作区")
        self.chk_fit.setChecked(True)

        form = QFormLayout()
        form.addRow("模式", self.cmb_mode)
        form.addRow("阈值", self._slider_row(self.sld_thresh, self.lbl_thresh))
        form.addRow("Canny 低", self._slider_row(self.sld_canny_low, self.lbl_canny_low))
        form.addRow("Canny 高", self._slider_row(self.sld_canny_high, self.lbl_canny_high))
        form.addRow("模糊", self._slider_row(self.sld_blur, self.lbl_blur))
        form.addRow("简化容差", self._slider_row(self.sld_simplify, self.lbl_simplify))
        form.addRow("最小轮廓", self.spin_min_area)
        form.addRow("", self.chk_invert)
        form.addRow("", self.chk_fit)
        g.setLayout(form)

        btn_commit = QPushButton("生成线稿（替换当前轨迹）")
        btn_commit.clicked.connect(
            lambda: self.commit_requested.emit(self.current_params())
        )
        self.lbl_info = QLabel("")

        v = QVBoxLayout(self)
        v.addWidget(btn_import)
        v.addWidget(self.lbl_img)
        v.addWidget(g_dir)
        v.addWidget(g)
        v.addWidget(btn_commit)
        v.addWidget(self.lbl_info)
        v.addStretch(1)

        self._connect_params()
        self.apply_params(LineartParams())

    # ---------------- 构造辅助 ----------------

    @staticmethod
    def _slider_row(slider: QSlider, label: QLabel):
        row = QHBoxLayout()
        row.addWidget(slider, 1)
        row.addWidget(label)
        return row

    def _connect_params(self):
        for s in (self.sld_thresh, self.sld_canny_low, self.sld_canny_high,
                  self.sld_blur, self.sld_simplify):
            s.valueChanged.connect(self._on_param_changed)
        self.spin_min_area.valueChanged.connect(self._on_param_changed)
        self.cmb_mode.currentIndexChanged.connect(self._on_param_changed)
        self.chk_invert.toggled.connect(self._on_param_changed)
        self.chk_fit.toggled.connect(self._on_param_changed)

    # ---------------- 对外接口 ----------------

    def set_preview_image(self, pixmap: QPixmap):
        self.lbl_img.setPixmap(
            pixmap.scaled(self.lbl_img.size(), Qt.KeepAspectRatio,
                          Qt.SmoothTransformation)
        )

    def set_info(self, text: str):
        self.lbl_info.setText(text)

    def apply_params(self, p: LineartParams):
        """把参数恢复到界面控件（用于启动时读设置）。"""
        idx = self.cmb_mode.findData(p.mode)
        if idx >= 0:
            self.cmb_mode.setCurrentIndex(idx)
        self.sld_thresh.setValue(p.threshold)
        self.sld_canny_low.setValue(p.canny_low)
        self.sld_canny_high.setValue(p.canny_high)
        self.sld_blur.setValue(p.blur)
        self.sld_simplify.setValue(int(p.simplify))
        self.spin_min_area.setValue(p.min_area)
        self.chk_invert.setChecked(p.invert)
        self.chk_fit.setChecked(p.fit_workspace)
        self._update_labels()

    def current_params(self) -> LineartParams:
        p = LineartParams()
        p.mode = self.cmb_mode.currentData()
        p.threshold = self.sld_thresh.value()
        p.canny_low = self.sld_canny_low.value()
        p.canny_high = self.sld_canny_high.value()
        p.blur = self.sld_blur.value()
        p.simplify = float(self.sld_simplify.value())
        p.min_area = self.spin_min_area.value()
        p.invert = self.chk_invert.isChecked()
        p.fit_workspace = self.chk_fit.isChecked()
        return p

    def has_image(self) -> bool:
        return self._path is not None

    # ---------------- 内部 ----------------

    def _on_import(self):
        start = self._last_dir or PROJECT_DIR
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", start, "图片 (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if not path:
            return
        self._last_dir = os.path.dirname(path)
        self._path = path
        self.import_requested.emit(path)

    def _on_param_changed(self, *_):
        self._update_labels()
        self._debounce.start()

    def _emit_preview(self):
        if self._path:
            self.preview_requested.emit(self.current_params())

    def _update_labels(self):
        self.lbl_thresh.setText(str(self.sld_thresh.value()))
        self.lbl_canny_low.setText(str(self.sld_canny_low.value()))
        self.lbl_canny_high.setText(str(self.sld_canny_high.value()))
        self.lbl_blur.setText(str(self.sld_blur.value()))
        self.lbl_simplify.setText(str(self.sld_simplify.value()))
