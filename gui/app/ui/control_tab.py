"""控制面板：连接（仿真/串口/TCP）、运动（jog 长按速度式）、运行、笔、状态。"""
from __future__ import annotations

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QComboBox, QGridLayout, QGroupBox, QHBoxLayout,
                             QLabel, QLineEdit, QProgressBar, QPushButton,
                             QSpinBox, QVBoxLayout, QWidget)

from ..comm.controller import Status

JOG_MAP = [
    (-1, -1, "↖", 0, 0), (0, -1, "↑", 0, 1), (1, -1, "↗", 0, 2),
    (-1, 0, "←", 1, 0), (None, None, "", 1, 1), (1, 0, "→", 1, 2),
    (-1, 1, "↙", 2, 0), (0, 1, "↓", 2, 1), (1, 1, "↘", 2, 2),
]

# ESP32 点动速度档位（mm/s，上限 30 = 3cm/s，与固件 JOG_SPEED_MAX_MM_S 一致）
JOG_SPEEDS = (5, 10, 15, 20, 25, 30)

# ESP32 默认地址与端口
DEFAULT_TCP_ADDR = "192.168.4.1:8080"


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
        self._jog_axis = None          # 当前按住的 jog 方向 (dx, dy)
        self._jog_timer = QTimer(self)  # 按住期间周期重发速度（防 STM32 1s 看门狗）
        self._jog_timer.setInterval(400)
        self._jog_timer.timeout.connect(self._jog_repeat)

        # ---------------- 连接 ----------------
        g_conn = QGroupBox("连接")
        self.cmb_backend = QComboBox()
        self.cmb_backend.addItem("仿真", "simulation")
        self.cmb_backend.addItem("串口", "serial")
        self.cmb_backend.addItem("TCP（ESP32）", "tcp")
        self.cmb_backend.currentIndexChanged.connect(self._update_conn_fields)

        self.cmb_port = QComboBox()
        self.cmb_port.setEditable(True)
        self.cmb_port.setMinimumWidth(120)
        self.btn_refresh = QPushButton("刷新端口")
        self.btn_refresh.clicked.connect(self.refresh_ports_requested)

        self.edt_tcp = QLineEdit(DEFAULT_TCP_ADDR)
        self.edt_tcp.setPlaceholderText("ESP32 地址:端口")

        self.cmb_baud = QComboBox()
        for b in (9600, 19200, 38400, 57600, 115200, 230400, 460800):
            self.cmb_baud.addItem(str(b), b)
        self.cmb_baud.setCurrentIndex(4)
        self.btn_connect = QPushButton("连接")
        self.btn_connect.clicked.connect(self._on_connect_clicked)

        form = QGridLayout()
        self.lbl_port = QLabel("端口")
        self.lbl_tcp = QLabel("TCP 地址")
        self.lbl_baud = QLabel("波特率")
        form.addWidget(QLabel("后端"), 0, 0)
        form.addWidget(self.cmb_backend, 0, 1)
        form.addWidget(self.lbl_port, 1, 0)
        row1 = QHBoxLayout()
        row1.addWidget(self.cmb_port, 1)
        row1.addWidget(self.btn_refresh)
        form.addLayout(row1, 1, 1)
        form.addWidget(self.lbl_tcp, 2, 0)
        form.addWidget(self.edt_tcp, 2, 1)
        form.addWidget(self.lbl_baud, 3, 0)
        form.addWidget(self.cmb_baud, 3, 1)
        form.addWidget(self.btn_connect, 4, 0, 1, 2)
        g_conn.setLayout(form)

        # ---------------- 运动 ----------------
        g_motion = QGroupBox("运动")
        self.cmb_speed = QComboBox()
        for s in JOG_SPEEDS:
            self.cmb_speed.addItem(f"{s} mm/s", float(s))
        self.cmb_speed.setCurrentIndex(1)   # 默认 10 mm/s

        jog_grid = QGridLayout()
        for dx, dy, label, r, c in JOG_MAP:
            if dx is None:
                jog_grid.addWidget(QLabel("速度"), r, c, alignment=Qt.AlignHCenter)
                continue
            btn = QPushButton(label)
            btn.setFixedSize(34, 26)
            # 长按速度式点动：按住移动、松开即停（与 ESP32/网页行为一致）
            btn.pressed.connect(lambda dx=dx, dy=dy: self._jog_press(dx, dy))
            btn.released.connect(self._jog_release)
            jog_grid.addWidget(btn, r, c)
        jog_box = QHBoxLayout()
        jog_box.addLayout(jog_grid)
        jog_box.addStretch(1)
        jog_box.addWidget(self.cmb_speed)

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

        self._update_conn_fields()
        self.set_connected(False)

    # ---------------- 对外接口 ----------------

    def set_connected(self, ok: bool):
        self._connected = ok
        self.btn_connect.setText("断开" if ok else "连接")
        for w in (self.btn_enable, self.btn_disable, self.btn_zero, self.btn_home,
                  self.btn_run, self.btn_pause, self.btn_resume, self.btn_stop,
                  self.btn_estop, self.btn_pen_up, self.btn_pen_down):
            w.setEnabled(ok)
        if not ok:
            self._jog_stop()

    def set_ports(self, ports):
        self.cmb_port.clear()
        self.cmb_port.addItems(ports)

    def set_progress(self, sent: int, total: int):
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(min(sent, total))
        self.lbl_progress.setText(f"下发进度: {sent}/{total} 行")

    def set_status(self, st: Status):
        """完整展示 ESP32 可获取的全部信息。"""
        m1, m2 = st.m1, st.m2

        def mot(m: dict) -> str:
            if not m:
                return "无数据"
            e = m.get("e", 255)
            if e == 1:
                est = "使能"
            elif e == 0:
                est = "失能"
            elif e == 255:
                est = "无反馈"
            else:
                est = f"故障码{e}"
            return (f"p={m.get('p', 0.0):.2f}rad  v={m.get('v', 0.0):.2f}rad/s  "
                    f"τ={m.get('t', 0.0):.2f}N·m  {est}  "
                    f"MOS={m.get('mo', 0)}℃  线圈={m.get('rt', 0)}℃")

        lines = [f"状态: {st.state}"]
        if st.state_name and st.state_name != st.state:
            lines[0] += f"（{st.state_name}）"
        if st.busy:
            lines[0] += "  忙碌中"
        if st.fault:
            lines[0] += f"  ⚠故障码:{st.fault}"
        lines += [
            f"位置: X={st.x:.2f}  Y={st.y:.2f} mm   笔: {st.pen}"
            + ("  [失联]" if st.stale else ""),
            f"电机: {'ON' if st.motors else 'OFF'}",
            f"M1: {mot(m1)}",
            f"M2: {mot(m2)}",
            f"执行行: {st.line}   队列: {st.buf}/16"
            + (f"   下发: {st.feed_idx}/{st.feed_total}" if st.feed_total else "")
            + ("   正在下发" if st.feeding else ""),
        ]
        self.lbl_status.setText("\n".join(lines))

    def backend(self) -> str:
        return str(self.cmb_backend.currentData())

    def port(self) -> str:
        """串口端口名，或 TCP 地址（host:port）。"""
        if self.backend() == "tcp":
            return self.edt_tcp.text().strip()
        return str(self.cmb_port.currentText().strip())

    def baudrate(self) -> int:
        return int(self.cmb_baud.currentData())

    def jog_speed(self) -> float:
        return float(self.cmb_speed.currentData())

    def set_backend(self, backend: str):
        idx = self.cmb_backend.findData(backend)
        if idx >= 0:
            self.cmb_backend.setCurrentIndex(idx)
        self._update_conn_fields()

    # ---------------- 内部 ----------------

    def _update_conn_fields(self):
        """按后端类型切换连接字段可见性。"""
        b = self.backend()
        is_tcp = b == "tcp"
        is_serial = b == "serial"
        self.lbl_port.setVisible(is_serial)
        self.cmb_port.setVisible(is_serial)
        self.btn_refresh.setVisible(is_serial)
        self.lbl_tcp.setVisible(is_tcp)
        self.edt_tcp.setVisible(is_tcp)
        self.lbl_baud.setVisible(is_serial)
        self.cmb_baud.setVisible(is_serial)

    def _jog_press(self, dx: int, dy: int):
        if not self._connected:
            return
        self._jog_axis = (dx, dy)
        self._jog_send_speed(dx, dy)
        self._jog_timer.start()

    def _jog_release(self):
        self._jog_timer.stop()
        self._jog_axis = None
        if self._connected:
            self.command_requested.emit("JOG_X", "0")
            self.command_requested.emit("JOG_Y", "0")

    def _jog_repeat(self):
        """按住期间周期重发速度（STM32 有 1s 点动看门狗，需持续喂指令）。"""
        if self._jog_axis is not None and self._connected:
            self._jog_send_speed(*self._jog_axis)

    def _jog_send_speed(self, dx: int, dy: int):
        v = self.jog_speed()
        if dx:
            self.command_requested.emit("JOG_X", f"{dx * v:.0f}")
        if dy:
            self.command_requested.emit("JOG_Y", f"{dy * v:.0f}")

    def _jog_stop(self):
        self._jog_timer.stop()
        self._jog_axis = None

    def _on_connect_clicked(self):
        if self._connected:
            self.disconnect_requested.emit()
        else:
            self.connect_requested.emit(self.backend(), self.port(), self.baudrate())
