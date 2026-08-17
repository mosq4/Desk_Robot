"""通信后端抽象：只约定"字节流 + 行帧"接口，具体后端可插拔。"""
from __future__ import annotations

from PyQt5.QtCore import QObject, pyqtSignal


class Transport(QObject):
    """下位机通信后端基类。

    信号:
        data_received(bytes)  收到一帧（一行）数据
        error(str)            通信错误
        state_changed(bool)   连接状态变化
    """

    data_received = pyqtSignal(bytes)
    error = pyqtSignal(str)
    state_changed = pyqtSignal(bool)

    def connect(self) -> bool:  # noqa: A003
        raise NotImplementedError

    def disconnect(self):
        raise NotImplementedError

    def write(self, data: bytes):
        raise NotImplementedError

    def is_connected(self) -> bool:
        raise NotImplementedError
