"""控制面板：连接、运动（jog）、运行（整段下发/暂停/继续/停止/急停）、笔、状态。"""
from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QComboBox, QGridLayout, QGroupBox, QHBoxLayout,
                             QLabel, QProgressBar, QPushButton, QSpinBox,
                             QVBoxLayout, QWidget)

from ..comm.controller import Status

JOG_MAP = [
    (-1, -1, "↖", 0, 0), (0, -1, "↑", 0, 1), (1, -1, "↗", 0, 2),
    (-1, 0, "←", 1, 0), (None, None, "", 1, 1), (1, 0, "→", 1, 2),
    (-1, 1, "↙", 2, 0), (0, 1, "↓", 2, 1), (1, 1, "↘", 2, 2),
]


class ControlTab(QWidget):
    connect_requested = pyqtSignal(str, str, int)   # backend, port, baudrate
    disconnect_requested = pyqtSignal()
    command_requested = pyqtSignal(str, str)        # 指令名, 参数
    run_requested = pyqtSignal()
    feed_changed = pyqtSignal(int)
    refresh_ports_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False

        # ---------------- 连接 ----------------
        g_conn = QGroupBox("连接")
        self.cmb_backend = QComboBox()
        self.cmb_backend.addItem("仿真", "simulation")
        self.cmb_backend.addItem("串口", "serial")
        self.cmb_port = QComboBox()
        self.cmb_port.setEditable(True)
        self.cmb_port.setMinimumWidth(120)
        self.btn_refresh = QPushButton("刷新端口")
        self.btn_refresh.clicked.connect(self.refresh_ports_requested)
        self.cmb_baud = QComboBox()
        for b in (9600, 19200, 38400, 57600, 115200, 230400, 460800):
            self.cmb_baud.addItem(str(b), b)
        self.cmb_baud.setCurrentIndex(4)
        self.btn_connect = QPushButton("连接")
        self.btn_connect.clicked.connect(self._on_connect_clicked)

        form = QGridLayout()
        form.addWidget(QLabel("后端"), 0, 0)
        form.addWidget(self.cmb_backend, 0, 1)
        form.addWidget(QLabel("端口"), 1, 0)
        row1 = QHBoxLayout()
        row1.addWidget(self.cmb_port, 1)
        row1.addWidget(self.btn_refresh)
        form.addLayout(row1, 1, 1)
        form.addWidget(QLabel("波特率"), 2, 0)
        form.addWidget(self.cmb_baud, 2, 1)
        form.addWidget(self.btn_connect, 3, 0, 1, 2)
        g_conn.setLayout(form)

        # ---------------- 运动 ----------------
        g_motion = QGroupBox("运动")
        self.cmb_step = QComboBox()
        for s in (1, 5, 10, 25, 50, 100):
            self.cmb_step.addItem(f"{s} mm", float(s))
        self.cmb_step.setCurrentIndex(2)

        jog_grid = QGridLayout()
        for dx, dy, label, r, c in JOG_MAP:
            if dx is None:
                jog_grid.addWidget(QLabel("步进"), r, c, alignment=Qt.AlignHCenter)
                continue
            btn = QPushButton(label)
            btn.setFixedSize(34, 26)
            btn.clicked.connect(
                lambda _=False, dx=dx, dy=dy: self._jog(dx, dy)
            )
            jog_grid.addWidget(btn, r, c)
        jog_box = QHBoxLayout()
        jog_box.addLayout(jog_grid)
        jog_box.addStretch(1)
        jog_box.addWidget(self.cmb_step)

        self.btn_enable = QPushButton("电机使能")
        self.btn_enable.clicked.connect(lambda: self.command_requested.emit("ENABLE", ""))
        self.btn_disable = QPushButton("电机失能")
        self.btn_disable.clicked.connect(lambda: self.command_requested.emit("DISABLE", ""))
        self.btn_zero = QPushButton("设零点")
        self.btn_zero.clicked.connect(lambda: self.command_requested.emit("ZERO", ""))
        self.btn_home = QPushButton("回零")
        self.btn_home.clicked.connect(lambda: self.command_requested.emit("HOME", ""))
        row_motion = QHBoxLayout()
        row_motion.addWidget(self.btn_enable)
        row_motion.addWidget(self.btn_disable)
        row_motion.addWidget(self.btn_zero)
        row_motion.addWidget(self.btn_home)

        v_motion = QVBoxLayout()
        v_motion.addLayout(jog_box)
        v_motion.addLayout(row_motion)
        g_motion.setLayout(v_motion)

        # ---------------- 运行 ----------------
        g_run = QGroupBox("运行")
        self.btn_run = QPushButton("整段下发并运行")
        self.btn_run.clicked.connect(self.run_requested)
        self.btn_pause = QPushButton("暂停")
        self.btn_pause.clicked.connect(lambda: self.command_requested.emit("PAUSE", ""))
        self.btn_resume = QPushButton("继续")
        self.btn_resume.clicked.connect(lambda: self.command_requested.emit("RESUME", ""))
        self.btn_stop = QPushButton("停止")
        self.btn_stop.clicked.connect(lambda: self.command_requested.emit("STOP", ""))
        self.btn_estop = QPushButton("急停")
        self.btn_estop.setStyleSheet(
            "background:#d33; color:white; font-weight:bold; font-size:14px;"
        )
        self.btn_estop.clicked.connect(lambda: self.command_requested.emit("ESTOP", ""))

        self.spin_feed = QSpinBox()
        self.spin_feed.setRange(100, 10000)
        self.spin_feed.setSingleStep(100)
        self.spin_feed.setValue(3000)
        self.spin_feed.setSuffix(" mm/min")
        self.spin_feed.valueChanged.connect(self.feed_changed)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.lbl_progress = QLabel("下发进度: -")

        row_run = QHBoxLayout()
        row_run.addWidget(self.btn_run)
        row_run.addWidget(self.btn_pause)
        row_run.addWidget(self.btn_resume)
        row_run.addWidget(self.btn_stop)
        row_run.addWidget(self.btn_estop)

        row_feed = QHBoxLayout()
        row_feed.addWidget(QLabel("绘制速度"))
        row_feed.addWidget(self.spin_feed)
        row_feed.addStretch(1)

        v_run = QVBoxLayout()
        v_run.addLayout(row_run)
        v_run.addLayout(row_feed)
        v_run.addWidget(self.progress)
        v_run.addWidget(self.lbl_progress)
        g_run.setLayout(v_run)

        # ---------------- 笔 ----------------
        g_pen = QGroupBox("笔控制")
        self.btn_pen_up = QPushButton("抬笔")
        self.btn_pen_up.clicked.connect(lambda: self.command_requested.emit("PEN_UP", ""))
        self.btn_pen_down = QPushButton("落笔")
        self.btn_pen_down.clicked.connect(lambda: self.command_requested.emit("PEN_DOWN", ""))
        row_pen = QHBoxLayout()
        row_pen.addWidget(self.btn_pen_up)
        row_pen.addWidget(self.btn_pen_down)
        row_pen.addStretch(1)
        g_pen.setLayout(row_pen)

        # ---------------- 状态 ----------------
        self.lbl_status = QLabel("状态: 未连接")
        self.lbl_status.setWordWrap(True)

        v = QVBoxLayout(self)
        v.addWidget(g_conn)
        v.addWidget(g_motion)
        v.addWidget(g_run)
        v.addWidget(g_pen)
        v.addWidget(self.lbl_status)
        v.addStretch(1)

        self.set_connected(False)

    # ---------------- 对外接口 ----------------

    def set_connected(self, ok: bool):
        self._connected = ok
        self.btn_connect.setText("断开" if ok else "连接")
        for w in (self.btn_enable, self.btn_disable, self.btn_zero, self.btn_home,
                  self.btn_run, self.btn_pause, self.btn_resume, self.btn_stop,
                  self.btn_estop, self.btn_pen_up, self.btn_pen_down):
            w.setEnabled(ok)

    def set_ports(self, ports):
        self.cmb_port.clear()
        self.cmb_port.addItems(ports)

    def set_progress(self, sent: int, total: int):
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(min(sent, total))
        self.lbl_progress.setText(f"下发进度: {sent}/{total} 行")

    def set_status(self, st: Status):
        self.lbl_status.setText(
            f"状态: {st.state}\n"
            f"位置: X={st.x:.2f}  Y={st.y:.2f} mm\n"
            f"电机: {'ON' if st.motors else 'OFF'}   笔: {st.pen}\n"
            f"执行行: {st.line}   缓冲剩余: {st.buf}"
        )

    def backend(self) -> str:
        return str(self.cmb_backend.currentData())

    def port(self) -> str:
        return str(self.cmb_port.currentText().strip())

    def baudrate(self) -> int:
        return int(self.cmb_baud.currentData())

    def jog_step(self) -> float:
        return float(self.cmb_step.currentData())

    # ---------------- 内部 ----------------

    def _jog(self, dx: int, dy: int):
        step = self.jog_step()
        self.command_requested.emit("JOG", f"{dx * step:.0f} {dy * step:.0f}")

    def _on_connect_clicked(self):
        if self._connected:
            self.disconnect_requested.emit()
        else:
            self.connect_requested.emit(self.backend(), self.port(), self.baudrate())
