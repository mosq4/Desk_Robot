"""仿真下位机后端。

在没有硬件时模拟一台"双关节臂下位机"：
缓冲整段 G-code，按进给速度移动虚拟末端，响应控制指令与状态查询。
所有状态都在主线程事件循环里变更（输入经 QTimer.singleShot 投递），
避免与发送线程产生竞态。
"""
from __future__ import annotations

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from ..core.gcode import parse_gcode_line
from .codec import FrameCodec


class SimulationTransport(QObject):
    data_received = pyqtSignal(bytes)
    error = pyqtSignal(str)
    state_changed = pyqtSignal(bool)

    _incoming = pyqtSignal(str)   # 跨线程投递入口（队列连接 → 主线程执行）

    TICK_MS = 20          # 运动仿真步长
    JOG_FEED = 40.0       # jog 速度 mm/s

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False
        self._codec = FrameCodec()
        self._rx = ""
        self._pending = 0            # seg_data 还需读取的行数
        self._buffer = []            # 整段 G-code 行（下位机缓冲）
        self._exec_index = 0         # 当前执行行号
        self._jogging = False

        self.pos = [0.0, 0.0]        # 原始坐标
        self.zero = [0.0, 0.0]       # 零点偏移
        self.target = [0.0, 0.0]
        self.state = "IDLE"          # IDLE/RUNNING/PAUSED/STOPPED/ESTOP
        self.motors = False
        self.pen_down = False
        self.feed = 50.0             # mm/s 当前进给

        self._tick = QTimer(self)
        self._tick.setInterval(self.TICK_MS)
        self._tick.timeout.connect(self._step)
        # 注意：write() 可能从发送线程调用，必须经队列信号回到主线程处理，
        # 保证所有状态变更都在主线程事件循环内（QTimer.singleShot 会绑定到调用线程，
        # 发送线程没有事件循环，feed 永远不会执行）
        self._incoming.connect(self._feed)

    # ---------------- Transport 接口 ----------------

    def connect(self) -> bool:
        self._connected = True
        self.state = "IDLE"
        self.state_changed.emit(True)
        self._reply("STATUS:" + self._status_text())
        return True

    def disconnect(self):
        self._connected = False
        self._tick.stop()
        self.state_changed.emit(False)

    def is_connected(self) -> bool:
        return self._connected

    def write(self, data: bytes):
        if not self._connected:
            return
        self._incoming.emit(data.decode("utf-8", errors="replace"))

    # ---------------- 协议处理 ----------------

    def _feed(self, text: str):
        self._rx += text
        self._process()

    def _process(self):
        while True:
            if self._pending > 0:
                nl = self._rx.find("\n")
                if nl < 0:
                    return
                line = self._rx[:nl].rstrip("\r")
                self._rx = self._rx[nl + 1:]
                self._buffer.append(line)
                self._pending -= 1
                if self._pending == 0:
                    self._reply("ACK")
                continue
            nl = self._rx.find("\n")
            if nl < 0:
                return
            line = self._rx[:nl].rstrip("\r")
            self._rx = self._rx[nl + 1:]
            if not line:
                continue
            msg = self._codec.decode_line(line)
            if msg is None:
                continue
            t = msg["type"]
            if t == "cmd":
                self._on_cmd(msg["name"], msg["args"])
            elif t == "query":
                self._reply("STATUS:" + self._status_text())
            elif t == "seg_begin":
                self._buffer = []
                self._exec_index = 0
                self._reply("ACK")
            elif t == "seg_data":
                self._pending = msg["count"]
            elif t == "seg_end":
                self._reply("ACK")
                if self.state == "IDLE":
                    self._start()
            elif t == "err":
                self.error.emit(msg["msg"])
            else:
                self.error.emit(f"无法识别: {line}")

    def _on_cmd(self, name: str, args: str):
        name = name.upper()
        if name == "ENABLE":
            self.motors = True
            self._ack()
        elif name == "DISABLE":
            self.motors = False
            self._tick.stop()
            self._ack()
        elif name == "ZERO":
            self.zero = self.pos[:]
            self._ack()
        elif name == "HOME":
            self.target = [0.0, 0.0]
            if self.motors and self.state == "IDLE":
                self._jogging = True
                self.state = "RUNNING"
                self._tick.start()
            self._ack()
        elif name == "PEN_UP":
            self.pen_down = False
            self._ack()
        elif name == "PEN_DOWN":
            self.pen_down = True
            self._ack()
        elif name == "PAUSE":
            if self.state == "RUNNING":
                self.state = "PAUSED"
                self._tick.stop()
            self._ack()
        elif name == "RESUME":
            if self.state in ("PAUSED", "STOPPED", "ESTOP"):
                self.state = "IDLE"
                if self._buffer:
                    self._start()
            self._ack()
        elif name == "STOP":
            self._tick.stop()
            self._jogging = False
            self._buffer = []
            self._exec_index = 0
            self.state = "IDLE"
            self._ack()
        elif name == "ESTOP":
            self._tick.stop()
            self._jogging = False
            self.state = "ESTOP"
            self._ack()
        elif name == "START":
            if self.state == "IDLE":
                self._start()
            self._ack()
        elif name == "JOG":
            parts = args.split()
            if len(parts) >= 2:
                try:
                    dx, dy = float(parts[0]), float(parts[1])
                except ValueError:
                    self._reply("ERR JOG 参数错误")
                    return
                if self.motors:
                    self.target = [self.target[0] + dx, self.target[1] + dy]
                    if self.state == "IDLE":
                        self._jogging = True
                        self.state = "RUNNING"
                        self._tick.start()
            self._ack()
        elif name == "FEED":
            try:
                self.feed = float(args) / 60.0
            except ValueError:
                self._reply("ERR FEED 参数错误")
                return
            self._ack()
        else:
            self._reply("ERR 未知指令:" + name)

    def _start(self):
        if not self.motors:
            self._reply("ERR 电机未使能")
            return
        if not self._buffer:
            return
        self._jogging = False
        self._exec_index = 0
        self.state = "RUNNING"
        self._tick.start()

    # ---------------- 运动仿真 ----------------

    def _step(self):
        if self.state != "RUNNING":
            return
        if self._jogging:
            self._move_toward(self.target, self.JOG_FEED)
            if self._arrived():
                self._jogging = False
                if not self._buffer:
                    self._tick.stop()
                    self.state = "IDLE"
            return
        if self._exec_index >= len(self._buffer):
            self._tick.stop()
            self.state = "IDLE"
            self._reply("STATUS:" + self._status_text())
            return
        line = self._buffer[self._exec_index]
        p = parse_gcode_line(line)
        if p is None:
            self._exec_index += 1
            return
        if p["m"] is not None:
            if p["m"] == 3:
                self.pen_down = True
            elif p["m"] == 5:
                self.pen_down = False
            self._exec_index += 1
            return
        if p["f"] is not None:
            self.feed = p["f"] / 60.0
        if p["g"] is not None and (p["x"] is not None or p["y"] is not None):
            self.target = [
                p["x"] if p["x"] is not None else self.target[0],
                p["y"] if p["y"] is not None else self.target[1],
            ]
            self._move_toward(self.target, self.feed)
            if self._arrived():
                self._exec_index += 1
        else:
            self._exec_index += 1

    def _move_toward(self, target, speed_mm_s):
        dx = target[0] - self.pos[0]
        dy = target[1] - self.pos[1]
        dist = (dx * dx + dy * dy) ** 0.5
        step = speed_mm_s * (self.TICK_MS / 1000.0)
        if dist <= step or dist == 0.0:
            self.pos = target[:]
        else:
            self.pos[0] += dx / dist * step
            self.pos[1] += dy / dist * step

    def _arrived(self) -> bool:
        dx = self.target[0] - self.pos[0]
        dy = self.target[1] - self.pos[1]
        return dx * dx + dy * dy < 1e-9

    # ---------------- 应答 ----------------

    def _ack(self):
        self._reply("ACK")
        self._reply("STATUS:" + self._status_text())

    def _reply(self, text: str):
        # 延迟 1ms 模拟真实串口往返
        QTimer.singleShot(1, lambda: self.data_received.emit(text.encode("utf-8")))

    def _status_text(self) -> str:
        return (
            f"state={self.state};"
            f"x={self.pos[0] - self.zero[0]:.2f};"
            f"y={self.pos[1] - self.zero[1]:.2f};"
            f"motors={'ON' if self.motors else 'OFF'};"
            f"line={self._exec_index};"
            f"buf={max(0, len(self._buffer) - self._exec_index)};"
            f"pen={'DOWN' if self.pen_down else 'UP'}"
        )
