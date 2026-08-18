"""TCP 通信后端：连接 ESP32 的 TCP 服务（单连接实例，行帧协议）。

行帧：\\n 分隔的 UTF-8 文本（见 codec.py 的 TCP 帧格式）。
QTcpSocket 在 Qt 事件循环中异步收发，与 GUI 线程天然亲和。
"""
from __future__ import annotations

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtNetwork import QAbstractSocket, QHostAddress, QTcpSocket


class TcpTransport(QObject):
    """ESP32 TCP 后端。

    信号:
        data_received(bytes)  收到一行数据（以 \\n 结尾）
        error(str)            通信错误
        state_changed(bool)   连接状态变化
    """

    data_received = pyqtSignal(bytes)
    error = pyqtSignal(str)
    state_changed = pyqtSignal(bool)

    _send = pyqtSignal(bytes)   # 跨线程写入队列（下发线程 → GUI 线程）

    def __init__(self, host: str, port: int, parent=None):
        super().__init__(parent)
        self._host = host
        self._port = port
        self._rx = b""
        self._send.connect(self._do_write)

    def connect(self) -> bool:  # noqa: A003
        self._sock = QTcpSocket(self)
        self._sock.readyRead.connect(self._on_ready_read)
        self._sock.errorOccurred.connect(self._on_error)
        self._sock.connected.connect(self._on_connected)
        self._sock.disconnected.connect(self._on_disconnected)
        self._sock.connectToHost(QHostAddress(self._host), self._port)
        # 等待连接结果（最多 3 秒），同步返回连接是否建立
        return self._sock.waitForConnected(3000)

    def disconnect(self):
        sock = getattr(self, "_sock", None)
        if sock is not None:
            sock.disconnectFromHost()
            sock.deleteLater()
        self._rx = b""

    def is_connected(self) -> bool:
        sock = getattr(self, "_sock", None)
        return bool(sock is not None
                    and sock.state() == QAbstractSocket.ConnectedState)

    def write(self, data: bytes):
        """任意线程可调用；通过队列信号回到 GUI 线程真正写 socket。"""
        self._send.emit(data)

    def _do_write(self, data: bytes):
        sock = getattr(self, "_sock", None)
        if sock is not None and self.is_connected():
            sock.write(data)

    # ---------------- 内部 ----------------

    def _on_ready_read(self):
        sock = self._sock
        self._rx += bytes(sock.readAll())
        while b"\n" in self._rx:
            line, _, rest = self._rx.partition(b"\n")
            self._rx = rest
            if line:
                self.data_received.emit(line + b"\n")

    def _on_connected(self):
        self.state_changed.emit(True)

    def _on_disconnected(self):
        self.state_changed.emit(False)

    def _on_error(self, err):
        if err == QAbstractSocket.RemoteHostClosedError:
            return  # 正常断开，不提示
        self.error.emit(f"TCP {self._host}:{self._port} 错误: {self._sock.errorString()}")
