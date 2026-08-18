"""主窗口：装配画布、四个面板、日志与状态栏，并负责全部信号接线。"""
from __future__ import annotations

import os
import sys
import traceback

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QKeySequence, QPixmap
from PyQt5.QtWidgets import (QApplication, QDockWidget, QFileDialog,
                             QHBoxLayout, QLabel, QMainWindow, QPushButton,
                             QShortcut, QSplitter, QTabWidget, QVBoxLayout,
                             QWidget)

from . import __version__
from .comm.controller import Controller, Status
from .core.gcode import trajectory_to_gcode_with_progress
from .core.lineart import polylines_from_image
from .core.trajectory import Stroke, Trajectory
from .settings import AppSettings, PROJECT_DIR
from .ui.canvas import Canvas
from .ui.control_tab import ControlTab
from .ui.gcode_tab import GcodeTab
from .ui.hand_tab import HandTab
from .ui.image_tab import ImageTab
from .ui.log_panel import LogPanel


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Desk Robot 上位机 v{__version__}")
        self.resize(1280, 800)

        self.settings = AppSettings()
        self.trajectory = Trajectory()
        self.controller = Controller()
        self._image = None            # 当前导入的图片（BGR ndarray，已应用方向调整）
        self._image_orig = None       # 导入时的原始图（用于重置方向）
        self._image_preview_active = False  # 调参预览期间隐藏蓝色线稿
        self._last_pos = None         # 最近一次下位机回报的末端位置（排序起点用）
        self._last_gcode_dir = None   # 保存 G-code 对话框起始目录
        self._total_lines = 0
        self._line_map = None         # G-code 行号 → 已绘制线段数（分色显示用）
        self._line_map_text = None    # 该映射对应的 G-code 文本
        self._exec_active = False     # 执行中（启用分色显示）

        self._gcode_params = self.settings.gcode_params()
        self._image_params = self.settings.image_params()

        # ---------------- 控件 ----------------
        self.canvas = Canvas()
        self.canvas.set_workspace(*self.settings.workspace())
        self.canvas.set_trajectory(self.trajectory)
        self.canvas.fit_view()

        # 缩放工具条
        self.canvas_box = QWidget()
        cv = QVBoxLayout(self.canvas_box)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(2)
        self.btn_zoom_out = QPushButton("缩小")
        self.btn_zoom_in = QPushButton("放大")
        self.btn_zoom_fit = QPushButton("适应窗口")
        self.lbl_zoom = QLabel("100%")
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(self.btn_zoom_out)
        zoom_row.addWidget(self.btn_zoom_in)
        zoom_row.addWidget(self.btn_zoom_fit)
        zoom_row.addStretch(1)
        zoom_row.addWidget(QLabel("缩放:"))
        zoom_row.addWidget(self.lbl_zoom)
        cv.addLayout(zoom_row)
        cv.addWidget(self.canvas, 1)

        self.hand_tab = HandTab()
        self.image_tab = ImageTab()
        self.gcode_tab = GcodeTab()
        self.control_tab = ControlTab()
        self.log_panel = LogPanel()

        tabs = QTabWidget()
        tabs.addTab(self.hand_tab, "手绘")
        tabs.addTab(self.image_tab, "图片线稿")
        tabs.addTab(self.gcode_tab, "G-code")
        tabs.addTab(self.control_tab, "控制")
        tabs.setMinimumWidth(380)
        self._image_tab_index = tabs.indexOf(self.image_tab)
        tabs.currentChanged.connect(self._on_tab_changed)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.canvas_box)
        splitter.addWidget(tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([760, 380])

        self.setCentralWidget(splitter)

        dock = QDockWidget("日志", self)
        dock.setWidget(self.log_panel)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        self.resizeDocks([dock], [180], Qt.Vertical)

        # ---------------- 状态栏 ----------------
        sb = self.statusBar()
        self.lbl_conn = QLabel("未连接")
        self.lbl_pos = QLabel("位置 -")
        self.lbl_state = QLabel("状态 -")
        self.lbl_motor = QLabel("电机 -")
        self.lbl_line = QLabel("行 -")
        self.lbl_cursor = QLabel("鼠标 -")
        for w in (self.lbl_conn, self.lbl_pos, self.lbl_state,
                  self.lbl_motor, self.lbl_line, self.lbl_cursor):
            sb.addPermanentWidget(w)
        sb.showMessage("提示: 先在「控制」页连接（可用仿真后端调试），再在「手绘/图片线稿」生成轨迹 → 「G-code」生成 → 运行")

        # ---------------- 接线 ----------------
        self.hand_tab.draw_toggled.connect(self.canvas.set_drawing)
        self.hand_tab.undo_requested.connect(self.trajectory.undo)
        self.hand_tab.clear_requested.connect(self.trajectory.clear)
        self.hand_tab.smooth_requested.connect(self.trajectory.smooth_all)
        self.hand_tab.reorder_requested.connect(self._on_reorder)
        self.canvas.stroke_drawn.connect(self.trajectory.add_stroke)
        self.canvas.cursor_moved.connect(self._on_cursor)
        self.canvas.error.connect(self.log_panel.append)
        self.trajectory.changed.connect(self._on_trajectory_changed)

        # 缩放控制
        self.btn_zoom_out.clicked.connect(lambda: self.canvas.zoom_by(0.8))
        self.btn_zoom_in.clicked.connect(lambda: self.canvas.zoom_by(1.25))
        self.btn_zoom_fit.clicked.connect(self.canvas.fit_view)
        self.canvas.zoom_changed.connect(
            lambda _z: self.lbl_zoom.setText(f"{self.canvas.zoom_fit_pct()}%")
        )
        QShortcut(QKeySequence.ZoomIn, self,
                  activated=lambda: self.canvas.zoom_by(1.25))
        QShortcut(QKeySequence.ZoomOut, self,
                  activated=lambda: self.canvas.zoom_by(0.8))
        QShortcut(QKeySequence("Ctrl+0"), self, activated=self.canvas.fit_view)

        self.image_tab.import_requested.connect(self._import_image)
        self.image_tab.preview_requested.connect(self._on_preview_params)
        self.image_tab.commit_requested.connect(self._on_commit_params)
        self.image_tab.orient_requested.connect(self._on_orient)
        self.image_tab.log.connect(self.log_panel.append)

        self.gcode_tab.generate_requested.connect(self._generate_gcode)
        self.gcode_tab.save_requested.connect(self._save_gcode)
        self.gcode_tab.copy_requested.connect(self._copy_gcode)

        self.control_tab.connect_requested.connect(self._on_connect)
        self.control_tab.disconnect_requested.connect(self._on_disconnect)
        self.control_tab.command_requested.connect(self._on_command)
        self.control_tab.run_requested.connect(self._on_run)
        self.control_tab.feed_changed.connect(self._on_feed_changed)
        self.control_tab.refresh_ports_requested.connect(self._refresh_ports)

        self.controller.status_updated.connect(self._on_status)
        self.controller.log.connect(self.log_panel.append)
        self.controller.connected_changed.connect(self._on_connected)
        self.controller.segment_progress.connect(self.control_tab.set_progress)
        self.controller.segment_finished.connect(self._on_segment_finished)

        # 启动时恢复参数
        self.image_tab.apply_params(self._image_params)
        self.control_tab.spin_feed.setValue(self._gcode_params.feed_rate)
        self.control_tab.set_backend(self.settings.backend())
        if self.settings.port():
            if self.settings.backend() == "tcp":
                self.control_tab.edt_tcp.setText(self.settings.port())
            else:
                self.control_tab.cmb_port.setEditText(self.settings.port())
        self._refresh_ports()
        self._load_image_params_info()

        # 未捕获异常 → 日志面板 + 日志文件（GUI 启动时控制台不可见）
        sys.excepthook = self._excepthook

    def _excepthook(self, exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        print(text, file=sys.stderr)
        self.log_panel.append_exception(text)

    # ---------------- 状态栏 ----------------

    def _on_cursor(self, x: float, y: float):
        self.lbl_cursor.setText(f"鼠标 {x:.1f}, {y:.1f} mm")

    def _on_status(self, st: Status):
        self._last_pos = (st.x, st.y)
        self.canvas.set_end_effector(st.x, st.y, st.pen == "DOWN")
        self.lbl_pos.setText(f"位置 X={st.x:.2f} Y={st.y:.2f}")
        self.lbl_state.setText(f"状态 {st.state}")
        self.lbl_motor.setText(f"电机 {'ON' if st.motors else 'OFF'}")
        self.lbl_line.setText(f"行 {st.line}")
        # 已绘制/未绘制分色：按下位机回报的行号查进度映射
        if self._exec_active and self._line_map:
            idx = max(0, min(st.line, len(self._line_map) - 1))
            self.canvas.set_drawn_segments(self._line_map[idx])
        else:
            self.canvas.set_drawn_segments(None)

    def _on_command(self, name: str, args: str):
        if name == "STOP":
            # 停止后不再分色，恢复整体蓝色
            self._exec_active = False
            self.canvas.set_drawn_segments(None)
        self.controller.send_command(name, args)

    def _on_trajectory_changed(self):
        # 轨迹被修改（手绘/撤销/清空/提交线稿），旧的进度映射失效
        self._exec_active = False
        self._line_map = None
        self._line_map_text = None
        self.canvas.set_drawn_segments(None)

    def _on_reorder(self):
        """笔画排序：优先画相邻笔画，减少抬笔空走。"""
        if not self.trajectory.strokes:
            self.log_panel.append("轨迹为空，无法排序")
            return
        start = self._last_pos if self._last_pos is not None else (0.0, 0.0)
        before, after = self.trajectory.reorder_nearest(start=start)
        saved = before - after
        pct = saved / before * 100 if before > 0 else 0.0
        self.log_panel.append(
            f"笔画排序完成: 抬笔空走 {before:.1f}mm → {after:.1f}mm"
            f"（节省 {pct:.0f}%），起点=末端位置"
        )

    def _on_connected(self, ok: bool):
        self.control_tab.set_connected(ok)
        self.lbl_conn.setText("已连接" if ok else "未连接")

    def _on_segment_finished(self):
        # 下位机缓冲完成后，显式触发执行（真实设备可在此确认启动指令）
        self.controller.send_command("START")
        self.log_panel.append("已发送 START 指令")

    # ---------------- 图片线稿 ----------------

    def _import_image(self, path: str):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.log_panel.append("缺少 opencv-python，无法处理图片")
            return
        try:
            # cv2.imread 在 Windows 上不支持中文路径（会返回 None），
            # 改用字节流 imdecode
            with open(path, "rb") as f:
                data = np.frombuffer(f.read(), np.uint8)
            img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        except OSError as e:
            self.log_panel.append(f"读取图片失败: {path}\n{e}")
            return
        if img is None:
            self.log_panel.append(f"无法解析图片文件（已损坏或不是图片）: {path}")
            return
        h, w = img.shape[:2]
        max_dim = 1600
        if max(h, w) > max_dim:  # 限制尺寸，避免预览卡顿
            scale = max_dim / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)),
                             interpolation=cv2.INTER_AREA)
        self._image = img
        self._image_orig = img.copy()  # 方向「重置」用
        self._show_image_preview()
        self.image_tab.set_info(f"已导入 {img.shape[1]}×{img.shape[0]}")
        self.log_panel.append(f"已导入图片: {path}")
        self.image_tab._emit_preview()

    def _show_image_preview(self):
        """把当前 self._image 显示到图片页缩略图。"""
        import cv2

        rgb = cv2.cvtColor(self._image, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0],
                      QImage.Format_RGB888).copy()
        self.image_tab.set_preview_image(QPixmap.fromImage(qimg))

    def _on_orient(self, op: str):
        """图像方向调整：旋转/翻转/重置（配合横置 A4 纸张）。"""
        import cv2

        if self._image is None:
            self.log_panel.append("请先导入图片")
            return
        img = self._image
        if op == "cw":
            self._image = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        elif op == "ccw":
            self._image = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
        elif op == "flip_h":
            self._image = cv2.flip(img, 1)
        elif op == "flip_v":
            self._image = cv2.flip(img, 0)
        elif op == "reset":
            if self._image_orig is None:
                self.log_panel.append("没有可重置的原始图像")
                return
            self._image = self._image_orig.copy()
        else:
            return
        self._show_image_preview()
        self.image_tab.set_info(
            f"{self._image.shape[1]}×{self._image.shape[0]}（已调方向，"
            f"点击「生成线稿」应用）"
        )
        # 方向变了，线稿预览立即重算（不走 _path 守卫）
        self.image_tab.preview_requested.emit(self.image_tab.current_params())
        self.log_panel.append(f"图像方向已调整: {op}")

    def _load_image_params_info(self):
        self.image_tab.set_info("当前参数来自上次会话")

    def _image_to_strokes(self, params):
        if self._image is None:
            return []
        import cv2

        gray = cv2.cvtColor(self._image, cv2.COLOR_BGR2GRAY)
        return polylines_from_image(gray, params, self.settings.workspace())

    def _on_preview_params(self, params):
        strokes = self._image_to_strokes(params)
        self.canvas.set_preview_strokes(strokes)
        # 拖动调参时隐藏已提交的蓝色线稿，只留灰色预览，避免新旧混在一起
        self.canvas.set_trajectory_visible(False)
        self._image_preview_active = True
        n_pts = sum(len(s) for s in strokes)
        self.image_tab.set_info(f"轮廓 {len(strokes)} 段, {n_pts} 点（预览为灰色）")

    def _on_commit_params(self, params):
        strokes = self._image_to_strokes(params)
        if not strokes:
            self.log_panel.append("未提取到任何轮廓，请调整参数")
            return
        self.trajectory.replace([Stroke(s) for s in strokes])
        self.canvas.set_preview_strokes([])
        self.canvas.set_trajectory_visible(True)
        self._image_preview_active = False
        self._image_params = params
        self.settings.set_image_params(params)
        n_pts = sum(len(s) for s in strokes)
        self.log_panel.append(f"线稿已替换当前轨迹: {len(strokes)} 段, {n_pts} 点")

    def _on_tab_changed(self, index: int):
        # 离开图片线稿页时恢复蓝色轨迹显示（手绘/G-code 页要看到它）
        if index != self._image_tab_index and self._image_preview_active:
            self.canvas.set_trajectory_visible(True)
            self.canvas.clear_preview()
            self._image_preview_active = False

    # ---------------- G-code ----------------

    def _generate_gcode(self):
        if not self.trajectory.strokes:
            self.log_panel.append("轨迹为空，先手绘或生成线稿")
            return
        # 默认自动优化笔画顺序，保证生成的 G-code 相邻笔画优先、空走最少
        if self.gcode_tab.chk_auto_opt.isChecked():
            start = self._last_pos if self._last_pos is not None else (0.0, 0.0)
            before, after = self.trajectory.reorder_nearest(start=start)
            saved = before - after
            pct = saved / before * 100 if before > 0 else 0.0
            self.log_panel.append(
                f"自动优化笔画顺序: 空走 {before:.1f} → {after:.1f}mm"
                f"（节省 {pct:.0f}%）"
            )
        text, self._line_map = trajectory_to_gcode_with_progress(
            self.trajectory, self._gcode_params)
        self._line_map_text = text
        self.gcode_tab.set_text(text)
        self._total_lines = len([l for l in text.splitlines() if l.strip()])
        self.log_panel.append(f"已生成 G-code: {self._total_lines} 行")

    def _save_gcode(self, text: str):
        start = self._last_gcode_dir or PROJECT_DIR
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 G-code", os.path.join(start, "trajectory.gcode"),
            "G-code (*.gcode *.nc *.txt)"
        )
        if not path:
            return
        self._last_gcode_dir = os.path.dirname(path)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        self.log_panel.append(f"已保存: {path}")

    def _copy_gcode(self, text: str):
        QApplication.clipboard().setText(text)
        self.log_panel.append("G-code 已复制到剪贴板")

    # ---------------- 连接与运行 ----------------

    def _on_connect(self, backend: str, port: str, baudrate: int):
        ok = self.controller.connect_backend(backend, port, baudrate)
        if ok:
            self.settings.set_backend(backend)
            if backend in ("serial", "tcp"):
                self.settings.set_port(port)
            self.settings.set_baudrate(baudrate)
            self.control_tab.set_connected(True)

    def _on_disconnect(self):
        self.controller.disconnect()
        self.control_tab.set_connected(False)

    def _on_feed_changed(self, value: int):
        self._gcode_params.feed_rate = value
        self.settings.set_gcode_params(self._gcode_params)
        # 仿真后端需要单独下发 FEED；TCP 后端进给已内嵌在 G-code 的 F 值中
        if not self.controller.is_tcp():
            self.controller.send_command("FEED", str(value))

    def _on_run(self):
        text = self.gcode_tab.text()
        if not text.strip():
            self._generate_gcode()
            text = self.gcode_tab.text()
        if not text.strip():
            self.log_panel.append("没有可下发的 G-code")
            return
        if not self.controller.is_connected():
            self.log_panel.append("未连接下位机（先在「控制」页连接，可用仿真后端）")
            return
        # 手动编辑过 G-code 时进度映射失效，回退为不分色显示
        if self._line_map_text != text:
            self._line_map = None
        if self.controller.send_segment(text):
            self._exec_active = True
            self.log_panel.append("开始执行，画布按 已绘制(蓝)/未绘制(灰) 分色")

    def _refresh_ports(self):
        ports = []
        try:
            from serial.tools import list_ports

            ports = [p.device for p in list_ports.comports()]
        except Exception as e:  # noqa: BLE001
            self.log_panel.append(f"枚举串口失败: {e}")
        self.control_tab.set_ports(ports)

    # ---------------- 收尾 ----------------

    def showEvent(self, event):
        super().showEvent(event)
        # 布局定稿后再按窗口尺寸适应视图（A4 默认填满图形窗口）
        QTimer.singleShot(0, self.canvas.fit_view)

    def closeEvent(self, event):
        self.controller.disconnect()
        self.settings.set_image_params(self.image_tab.current_params())
        self.settings.set_gcode_params(self._gcode_params)
        self.settings.set_backend(self.control_tab.backend())
        self.settings.set_baudrate(self.control_tab.baudrate())
        if self.control_tab.backend() in ("serial", "tcp") and self.control_tab.port():
            self.settings.set_port(self.control_tab.port())
        super().closeEvent(event)
