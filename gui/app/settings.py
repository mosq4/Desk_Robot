"""参数持久化（QSettings，INI 文件 config/deskrobot.ini）。

注意：Windows 注册表原生格式在本机（及沙箱）下写入会被静默拦截，
因此显式使用项目内 INI 文件，可移植且便于查看。
"""
from __future__ import annotations

import json
import os

from PyQt5.QtCore import QSettings

from .core.gcode import GcodeParams
from .core.lineart import LineartParams

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_DIR = os.path.join(PROJECT_DIR, "config")


class AppSettings:
    SETTINGS_VERSION = 3

    def __init__(self):
        os.makedirs(_CONFIG_DIR, exist_ok=True)
        ini = os.path.join(_CONFIG_DIR, "deskrobot.ini")
        self.qs = QSettings(ini, QSettings.IniFormat)
        self._migrate()

    def _migrate(self):
        """老配置迁移。"""
        if int(self.qs.value("meta/version", 0)) < 2:
            # v2: 绘画范围默认改为 A4
            self.qs.setValue("canvas/workspace_w", 210)
            self.qs.setValue("canvas/workspace_h", 297)
            self.qs.setValue("meta/version", 2)
        if int(self.qs.value("meta/version", 0)) < 3:
            # v3: 纸张在桌面上是横置的，工作区改为横向 A4 (297×210)
            self.qs.setValue("canvas/workspace_w", 297)
            self.qs.setValue("canvas/workspace_h", 210)
            self.qs.setValue("meta/version", self.SETTINGS_VERSION)

    # ---- 通信 ----
    def backend(self) -> str:
        return str(self.qs.value("comm/backend", "simulation"))

    def set_backend(self, v: str):
        self.qs.setValue("comm/backend", v)

    def port(self) -> str:
        return str(self.qs.value("comm/port", ""))

    def set_port(self, v: str):
        self.qs.setValue("comm/port", v)

    def baudrate(self) -> int:
        return int(self.qs.value("comm/baudrate", 115200))

    def set_baudrate(self, v: int):
        self.qs.setValue("comm/baudrate", v)

    # ---- 画布 ----
    def workspace(self):
        # 默认横向 A4（纸张横置在桌面上，297mm 为长边）
        w = float(self.qs.value("canvas/workspace_w", 297))
        h = float(self.qs.value("canvas/workspace_h", 210))
        return (w, h)

    # ---- 控制 ----
    def jog_step(self) -> float:
        return float(self.qs.value("control/jog_step", 10))

    def set_jog_step(self, v: float):
        self.qs.setValue("control/jog_step", v)

    # ---- 线稿参数 ----
    def image_params(self) -> LineartParams:
        raw = self.qs.value("lineart/params")
        if raw:
            try:
                return LineartParams.from_dict(json.loads(str(raw)))
            except (ValueError, TypeError):
                pass
        return LineartParams()

    def set_image_params(self, p: LineartParams):
        self.qs.setValue("lineart/params", json.dumps(p.to_dict()))

    # ---- G-code 参数 ----
    def gcode_params(self) -> GcodeParams:
        return GcodeParams(
            feed_rate=int(self.qs.value("gcode/feed_rate", 3000)),
            travel_rate=int(self.qs.value("gcode/travel_rate", 6000)),
            pen_down_cmd=str(self.qs.value("gcode/pen_down", "M3")),
            pen_up_cmd=str(self.qs.value("gcode/pen_up", "M5")),
        )

    def set_gcode_params(self, p: GcodeParams):
        self.qs.setValue("gcode/feed_rate", p.feed_rate)
        self.qs.setValue("gcode/travel_rate", p.travel_rate)
        self.qs.setValue("gcode/pen_down", p.pen_down_cmd)
        self.qs.setValue("gcode/pen_up", p.pen_up_cmd)
