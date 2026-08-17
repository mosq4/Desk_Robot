"""串口后端：pyserial + 后台读线程，按行成帧。

TODO(协议): 定稿后如需二进制帧（帧头/长度/CRC），在此处改为按帧切分。
"""
from __future__ import annotations

import threading

from .transport import Transport


class SerialTransport(Transport):
    def __init__(self, port: str, baudrate: int = 115200, parent=None):
        super().__init__(parent)
        self._port = port
        self._baudrate = baudrate
        self._ser = None
        self._thread = None
        self._lock = threading.Lock()
        self._alive = False

    def connect(self) -> bool:
        try:
            import serial  # 延迟导入，未装 pyserial 时给出明确提示
        except ImportError:
            self.error.emit("缺少 pyserial，请运行 pip install pyserial")
            return False
        try:
            self._ser = serial.Serial(self._port, self._baudrate, timeout=0.1)
        except Exception as e:  # noqa: BLE001
            self.error.emit(f"串口打开失败 {self._port}: {e}")
            return False
        self._alive = True
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
        self.state_changed.emit(True)
        return True

    def _reader(self):
        buf = b""
        while self._alive and self._ser is not None:
            try:
                data = self._ser.read(1024)
            except Exception as e:  # noqa: BLE001
                self.error.emit(f"串口读取错误: {e}")
                break
            if not data:
                continue
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line:
                    self.data_received.emit(line)
        self.state_changed.emit(False)

    def write(self, data: bytes):
        if self._ser is None:
            return
        with self._lock:
            try:
                self._ser.write(data)
            except Exception as e:  # noqa: BLE001
                self.error.emit(f"串口写入错误: {e}")

    def disconnect(self):
        self._alive = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:  # noqa: BLE001
                pass
            self._ser = None
        self.state_changed.emit(False)

    def is_connected(self) -> bool:
        return self._ser is not None and self._ser.is_open
