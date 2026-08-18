"""ESP32 TCP 模拟器：按固件 TCP 行协议应答，用于上位机 TCP 后端联调测试。

协议（与 firmware/src/main.cpp 一致）：
    上位机 → ESP32: CMD:<name> [args] / GCODE:<n> + n 行 / STATUS / CONFIG
    ESP32 → 上位机: R:{json} / S:{json} / C:{json}
"""
from __future__ import annotations

import json
import socket
import threading


def _default_status() -> dict:
    return {
        "st": 1, "stateName": "READY", "busy": 0, "pen": 1, "fault": 0,
        "m1": {"p": 1.23, "v": 0.05, "t": 0.10, "e": 1, "mo": 35, "rt": 41},
        "m2": {"p": 2.34, "v": 0.05, "t": 0.10, "e": 1, "mo": 36, "rt": 42},
        "x": 30.12, "y": 20.34, "q": 5, "line": 12,
        "feeding": True, "feedIdx": 8, "feedTotal": 40, "stale": False,
    }


class MockEsp32Server:
    """单客户端 TCP 服务器，模拟 ESP32 的 TCP 行协议。"""

    def __init__(self, status: dict | None = None):
        self.status = status if status is not None else _default_status()
        self.cmds = []          # 收到的 CMD: 正文（顺序）
        self.gcode = []         # 收到的 G-code 正文行
        self._expect = 0        # 待收的 G-code 行数
        self._conn = None
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._th = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._th.start()

    def stop(self):
        try:
            if self._conn:
                self._conn.close()
            self._sock.close()
        except OSError:
            pass

    def _run(self):
        conn, _ = self._sock.accept()
        self._conn = conn
        buf = b""
        while True:
            data = conn.recv(4096)
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                self._handle(line.decode("utf-8", errors="replace").strip())

    def _handle(self, line: str):
        if not line:
            return
        if line == "STATUS":
            self._send("S:" + json.dumps(self.status))
            return
        if line == "CONFIG":
            cfg = {"A4_W": 210, "A4_H": 297, "PX": 2, "MARGIN": 10, "FEED": 1500,
                   "jogMin": 5, "jogMax": 30, "jogDef": 10}
            self._send("C:" + json.dumps(cfg))
            return
        if line.startswith("GCODE:"):
            self._expect = int(line[6:])
            return
        if line.startswith("CMD:"):
            body = line[4:]
            self.cmds.append(body)
            if body in ("enable", "disable"):
                # 与固件一致：使能/失能未实现（需补全 STM32 侧协议）
                self._send('R:{"ok":0,"code":7,"msg":"未实现: 需补全STM32侧协议"}')
            else:
                self._send('R:{"ok":1,"code":0,"msg":"ok"}')
            return
        # G-code 正文行
        if self._expect > 0:
            self.gcode.append(line)
            self._expect -= 1
            if self._expect == 0:
                self._send("R:" + json.dumps({"ok": 1, "lines": len(self.gcode),
                                              "msg": "已缓存"}))
            return
        self._send('R:{"ok":0,"code":4,"msg":"unknown cmd"}')

    def _send(self, text: str):
        if self._conn:
            try:
                self._conn.sendall((text + "\n").encode("utf-8"))
            except OSError:
                pass
