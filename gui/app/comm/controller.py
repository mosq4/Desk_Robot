"""Controller：上位机会话层。

职责：
- 创建/管理通信后端（仿真、串口、TCP）
- 发送控制指令（仿真/串口走占位文本协议，TCP 走 ESP32 行协议）
- 整段 G-code 下发：仿真后端分包 + ACK；TCP 后端一次性上传 + ESP32 ACK 流控
- 定时状态轮询，把下位机回报解析成 Status 对象广播给界面
"""
from __future__ import annotations

import json
import threading

from PyQt5.QtCore import QObject, QThread, QTimer, pyqtSignal

from .codec import FrameCodec
from .serial_transport import SerialTransport
from .simulation import SimulationTransport
from .tcp_transport import TcpTransport

# ESP32 状态机编号 → 中文名（网页端同款）
STATE_NAMES_CN = {
    0: "初始化", 1: "就绪", 2: "运行中", 3: "已暂停", 4: "点动",
    5: "急停", 6: "故障", 7: "回零中", 8: "标定中",
}


class Status:
    """下位机状态回报（覆盖 ESP32 /api/status 全部字段）。"""

    __slots__ = ("state", "state_name", "busy", "fault", "pen",
                 "m1", "m2", "x", "y", "q", "line",
                 "feeding", "feed_idx", "feed_total", "stale",
                 "motors", "buf", "raw")

    def __init__(self, raw: str = ""):
        self.state = "UNKNOWN"
        self.state_name = ""
        self.busy = False
        self.fault = 0
        self.pen = "UP"
        self.m1 = {}
        self.m2 = {}
        self.x = 0.0
        self.y = 0.0
        self.q = 0
        self.line = 0
        self.feeding = False
        self.feed_idx = 0
        self.feed_total = 0
        self.stale = False
        self.motors = False
        self.buf = 0
        self.raw = raw

    @staticmethod
    def parse(fields: str) -> "Status":
        """解析仿真后端文本状态帧（STATUS:key=val;...）。"""
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

    @staticmethod
    def parse_json(j: dict) -> "Status":
        """解析 ESP32 TCP 状态帧（S:{json}，与 /api/status 同构）。"""
        st = Status(json.dumps(j, ensure_ascii=False))
        st.state_name = str(j.get("stateName", ""))
        st.state = STATE_NAMES_CN.get(int(j.get("st", -1)), st.state_name or "UNKNOWN")
        st.busy = bool(j.get("busy"))
        st.fault = int(j.get("fault", 0) or 0)
        st.pen = "DOWN" if int(j.get("pen", 0) or 0) else "UP"
        st.m1 = dict(j.get("m1") or {})
        st.m2 = dict(j.get("m2") or {})
        st.x = float(j.get("x", 0) or 0)
        st.y = float(j.get("y", 0) or 0)
        st.q = int(j.get("q", 0) or 0)
        st.buf = st.q
        st.line = int(j.get("line", 0) or 0)
        st.feeding = bool(j.get("feeding"))
        st.feed_idx = int(j.get("feedIdx", 0) or 0)
        st.feed_total = int(j.get("feedTotal", 0) or 0)
        st.stale = bool(j.get("stale"))
        e1 = st.m1.get("e")
        e2 = st.m2.get("e")
        st.motors = (e1 == 1) or (e2 == 1)   # 任一电机使能即视为电机在线
        return st


class Controller(QObject):
    status_updated = pyqtSignal(object)         # Status
    log = pyqtSignal(str)
    connected_changed = pyqtSignal(bool)
    segment_progress = pyqtSignal(int, int)     # 已发送行 / 总行
    segment_finished = pyqtSignal()

    CHUNK_LINES = 200      # 仿真后端每个分块的行数
    ACK_TIMEOUT = 5.0      # 等待 ACK 的超时（秒；TCP 上传含大帧需放宽）
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
        elif backend == "tcp":
            host, _, p = port.partition(":")
            tr = TcpTransport(host.strip(), int(p or 8080), parent=self)
        else:
            tr = SimulationTransport(parent=self)
        tr.data_received.connect(self._on_data)
        tr.error.connect(lambda m: self.log.emit(f"[通信] {m}"))
        tr.state_changed.connect(self._on_state_changed)
        ok = tr.connect()
        if ok:
            self._transport = tr
            if backend == "tcp":
                self.log.emit(f"已连接 TCP 后端: {port}")
            elif backend == "serial":
                self.log.emit(f"已连接后端: 串口 {port}")
            else:
                self.log.emit("已连接后端: 仿真")
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

    def is_tcp(self) -> bool:
        return isinstance(self._transport, TcpTransport)

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
        if self.is_tcp():
            data = self._codec.encode_tcp_command(name, args)
        else:
            data = self._codec.encode_command(name, args)
        self._transport.write(data)
        log_txt = f"[发] CMD:{name}" + (f" {args}" if args else "")
        self.log.emit(log_txt)
        return True

    def query_status(self):
        if self.is_connected():
            if self.is_tcp():
                self._transport.write(self._codec.encode_tcp_query())
            else:
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
        if self.is_tcp() and len(lines) > 600:
            self.log.emit(f"[通信] G-code 共 {len(lines)} 行，超过 ESP32 上限 600，请精简")
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
            if "json" in msg:
                try:
                    st = Status.parse_json(json.loads(msg["json"]))
                except (ValueError, TypeError):
                    return
            else:
                st = Status.parse(msg["fields"])
            self.status_updated.emit(st)
            return
        self.log.emit(f"[收] {text}")
        if t == "ack":
            # TCP 帧：解析 ok/code/msg，失败时记录错误供下发线程中止
            if "json" in msg:
                try:
                    j = json.loads(msg["json"])
                    if not j.get("ok"):
                        code = j.get("code")
                        self._ack_err = f"{j.get('msg', '')} (code={code})"
                except (ValueError, TypeError):
                    pass
            self._ack.set()
        elif t == "err":
            self.log.emit(f"[下位机] ERR: {msg['msg']}")
            self._ack_err = msg["msg"]
            self._ack.set()


class _SegmentSender(QThread):
    """整段下发工作线程。

    - 仿真后端：分包 SEG_* 发送，每块等待 ACK，超时中止；
    - TCP 后端：一次性 GCODE:<n> 整帧上传（ESP32 自行按 ACK 流控逐行下发），
      等待一个 R: ACK 确认缓存完成。
    """

    def __init__(self, ctrl: Controller, lines):
        super().__init__(ctrl)
        self._ctrl = ctrl
        self._lines = lines

    def run(self):
        c = self._ctrl
        tr = c._transport
        codec = c._codec
        total = len(self._lines)

        def wait_ack() -> bool:
            c._ack.clear()
            c._ack_err = None
            if not c._ack.wait(c.ACK_TIMEOUT):
                return False
            return c._ack_err is None

        # ---- TCP 后端：整段一次上传 ----
        if c.is_tcp():
            c.log.emit(f"[下发] 上传 G-code 到 ESP32，共 {total} 行（ESP32 按 ACK 流控逐行下发）")
            tr.write(codec.encode_tcp_gcode(self._lines))
            if not wait_ack():
                c.log.emit(f"[下发] 上传无确认: {c._ack_err or '超时'}，中止")
                return
            c.log.emit(f"[下发] G-code 已缓存到 ESP32（{total} 行），等待 START")
            return

        # ---- 仿真后端：分包 SEG_* ----
        c.log.emit(f"[下发] 整段开始，共 {total} 行（每块 {c.CHUNK_LINES} 行）")
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
