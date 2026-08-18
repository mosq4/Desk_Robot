"""Controller：上位机会话层。

职责：
- 创建/管理通信后端（仿真、串口）
- 发送控制指令
- 整段 G-code 下发：分包 + ACK 等待 + 超时中止（流量控制）
- 定时状态轮询，把下位机回报解析成 Status 对象广播给界面
"""
from __future__ import annotations

import threading

from PyQt5.QtCore import QObject, QThread, QTimer, pyqtSignal

from .codec import FrameCodec
from .serial_transport import SerialTransport
from .simulation import SimulationTransport


class Status:
    """下位机状态回报。"""

    __slots__ = ("state", "x", "y", "motors", "line", "buf", "pen", "raw")

    def __init__(self, raw: str = ""):
        self.state = "UNKNOWN"
        self.x = 0.0
        self.y = 0.0
        self.motors = False
        self.line = 0
        self.buf = 0
        self.pen = "UP"
        self.raw = raw

    @staticmethod
    def parse(fields: str) -> "Status":
        st = Status(fields)
        for part in fields.split(";"):
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            k, v = k.strip(), v.strip()
            if k == "state":
                st.state = v
            elif k == "x":
                st.x = float(v)
            elif k == "y":
                st.y = float(v)
            elif k == "motors":
                st.motors = v.upper() == "ON"
            elif k == "line":
                st.line = int(v)
            elif k == "buf":
                st.buf = int(v)
            elif k == "pen":
                st.pen = v
        return st


class Controller(QObject):
    status_updated = pyqtSignal(object)         # Status
    log = pyqtSignal(str)
    connected_changed = pyqtSignal(bool)
    segment_progress = pyqtSignal(int, int)     # 已发送行 / 总行
    segment_finished = pyqtSignal()

    CHUNK_LINES = 200      # 每个分块的行数
    ACK_TIMEOUT = 1.5      # 等待 ACK 的超时（秒）
    POLL_INTERVAL_MS = 100  # 末端位置状态轮询周期（100ms = 10Hz，笔刷位置刷新率）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._transport = None
        self._codec = FrameCodec()
        self._ack = threading.Event()
        self._ack_err = None
        self._sender = None
        self._poll = QTimer(self)
        self._poll.setInterval(self.POLL_INTERVAL_MS)
        self._poll.timeout.connect(self.query_status)

    # ---------------- 连接 ----------------

    def connect_backend(self, backend: str, port: str = "", baudrate: int = 115200) -> bool:
        if self._transport is not None:
            self.disconnect()
        if backend == "serial":
            tr = SerialTransport(port, baudrate, parent=self)
        else:
            tr = SimulationTransport(parent=self)
        tr.data_received.connect(self._on_data)
        tr.error.connect(lambda m: self.log.emit(f"[通信] {m}"))
        tr.state_changed.connect(self._on_state_changed)
        ok = tr.connect()
        if ok:
            self._transport = tr
            self.log.emit(f"已连接后端: {'串口 ' + port if backend == 'serial' else '仿真'}")
        else:
            tr.deleteLater()
        return ok

    def disconnect(self):
        if self._transport is not None:
            self._transport.disconnect()
            self._transport.deleteLater()
            self._transport = None
        self._poll.stop()
        self.connected_changed.emit(False)

    def is_connected(self) -> bool:
        return bool(self._transport is not None and self._transport.is_connected())

    def _on_state_changed(self, connected: bool):
        if connected:
            self._poll.start()
        else:
            self._poll.stop()
        self.connected_changed.emit(connected)

    # ---------------- 指令与查询 ----------------

    def send_command(self, name: str, args: str = "") -> bool:
        if not self.is_connected():
            self.log.emit("[通信] 未连接，指令被忽略")
            return False
        self._transport.write(self._codec.encode_command(name, args))
        log_txt = f"[发] CMD:{name}" + (f" {args}" if args else "")
        self.log.emit(log_txt)
        return True

    def query_status(self):
        if self.is_connected():
            self._transport.write(self._codec.encode_query())

    # ---------------- 整段下发 ----------------

    def send_segment(self, gcode_text: str) -> bool:
        if not self.is_connected():
            self.log.emit("[通信] 未连接，无法下发")
            return False
        if self._sender is not None and self._sender.isRunning():
            self.log.emit("[通信] 已有下发任务在进行")
            return False
        lines = [l for l in gcode_text.splitlines() if l.strip()]
        if not lines:
            self.log.emit("[通信] G-code 为空")
            return False
        self._sender = _SegmentSender(self, lines)
        self._sender.finished.connect(self._on_sender_done)
        self._sender.start()
        return True

    def _on_sender_done(self):
        self.log.emit("[通信] 整段下发完成")
        self.segment_finished.emit()

    # ---------------- 收帧 ----------------

    def _on_data(self, raw: bytes):
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return
        msg = self._codec.decode_line(text)
        if msg is None:
            return
        t = msg["type"]
        if t == "status":
            # 高频状态回报（100ms 一次），不写日志避免刷屏
            self.status_updated.emit(Status.parse(msg["fields"]))
            return
        self.log.emit(f"[收] {text}")
        if t == "ack":
            self._ack.set()
        elif t == "err":
            self.log.emit(f"[下位机] ERR: {msg['msg']}")
            self._ack_err = msg["msg"]
            self._ack.set()


class _SegmentSender(QThread):
    """整段下发工作线程：分包发送，每块等待 ACK，超时中止。"""

    def __init__(self, ctrl: Controller, lines):
        super().__init__(ctrl)
        self._ctrl = ctrl
        self._lines = lines

    def run(self):
        c = self._ctrl
        tr = c._transport
        codec = c._codec
        total = len(self._lines)
        c.log.emit(f"[下发] 整段开始，共 {total} 行（每块 {c.CHUNK_LINES} 行）")

        def wait_ack() -> bool:
            c._ack.clear()
            c._ack_err = None
            if not c._ack.wait(c.ACK_TIMEOUT):
                return False
            return c._ack_err is None

        tr.write(codec.encode_segment_begin(total))
        if not wait_ack():
            c.log.emit("[下发] SEG_BEGIN 无确认，中止")
            return
        sent = 0
        while sent < total:
            chunk = self._lines[sent:sent + c.CHUNK_LINES]
            tr.write(codec.encode_segment_data(chunk))
            c.segment_progress.emit(min(sent + len(chunk), total), total)
            if not wait_ack():
                c.log.emit(f"[下发] 第 {sent} 行分块无确认，中止")
                return
            sent += len(chunk)
        tr.write(codec.encode_segment_end())
        if not wait_ack():
            c.log.emit("[下发] SEG_END 无确认，中止")
            return
        c.log.emit(f"[下发] 整段完成（{total} 行）")
