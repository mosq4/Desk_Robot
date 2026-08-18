"""协议编解码。

两种协议并存：
1. 占位文本协议（仿真后端 simulation 使用）—— 可读文本行，便于无硬件调试；
2. TCP 协议（ESP32 上位机后端使用）—— 行文本，命令集与 ESP32 HTTP /api/* 对齐。

TCP 帧格式（UTF-8，\\n 分隔）：
    上位机 → ESP32:
        CMD:<name> [args]   控制命令（start/stop/pause/resume/estop/clear_estop/
                            setzero/return_zero/pen_up/pen_down/enable/disable/
                            jogx <v>/jogy <v>，v=mm/s 速度）
        GCODE:<n>           开始上传 n 行 G-code，随后 n 行裸 G-code 正文
        STATUS              查询状态（返回 S:{json}）
        CONFIG              查询配置（返回 C:{json}）
    ESP32 → 上位机:
        R:{json}            命令/上传 ACK（ok/code/msg）
        S:{json}            状态帧（与 ESP32 /api/status 同构，全字段）
        C:{json}            配置帧（与 ESP32 /api/config 同构）
"""
from __future__ import annotations

# 上位机指令名 → TCP 协议命令名
_TCP_CMD_MAP = {
    "ENABLE": "enable",
    "DISABLE": "disable",
    "ZERO": "setzero",
    "HOME": "return_zero",
    "PEN_UP": "pen_up",
    "PEN_DOWN": "pen_down",
    "PAUSE": "pause",
    "RESUME": "resume",
    "STOP": "stop",
    "ESTOP": "estop",
    "START": "start",
    "JOG_X": "jogx",
    "JOG_Y": "jogy",
}


class FrameCodec:
    """占位帧编解码（仿真文本协议 + TCP 协议）。"""

    # ---- 仿真文本协议（上位机 → 下位机）----

    def encode_command(self, name: str, args: str = "") -> bytes:
        s = f"CMD:{name}"
        if args:
            s += f" {args}"
        return (s + "\n").encode("utf-8")

    def encode_query(self) -> bytes:
        return b"?STATUS\n"

    def encode_segment_begin(self, num_lines: int) -> bytes:
        return f"SEG_BEGIN {num_lines}\n".encode("utf-8")

    def encode_segment_data(self, lines) -> bytes:
        """一行块头 + k 行 G-code 正文。"""
        head = f"SEG_DATA {len(lines)}\n".encode("utf-8")
        body = ("\n".join(lines) + "\n").encode("utf-8")
        return head + body

    def encode_segment_end(self) -> bytes:
        return b"SEG_END\n"

    # ---- TCP 协议（上位机 → ESP32）----

    def encode_tcp_command(self, name: str, args: str = "") -> bytes:
        """TCP 控制命令；上位机指令名自动映射到 ESP32 命令集。"""
        cmd = _TCP_CMD_MAP.get(name, name)
        s = f"CMD:{cmd}"
        if args:
            s += f" {args}"
        return (s + "\n").encode("utf-8")

    def encode_tcp_query(self) -> bytes:
        return b"STATUS\n"

    def encode_tcp_config(self) -> bytes:
        return b"CONFIG\n"

    def encode_tcp_gcode(self, lines) -> bytes:
        """整段 G-code 上传：GCODE:<n> 头 + n 行裸 G-code 正文。"""
        head = f"GCODE:{len(lines)}\n".encode("utf-8")
        body = ("\n".join(lines) + "\n").encode("utf-8")
        return head + body

    # ---- 双向解析（两种协议共用）----

    @staticmethod
    def decode_line(line: str):
        """解析一行帧文本，返回 dict 或 None。"""
        line = line.rstrip("\r\n")
        if not line:
            return None
        # TCP 协议帧（ESP32）
        if line.startswith("R:"):
            return {"type": "ack", "json": line[2:].strip()}
        if line.startswith("S:"):
            return {"type": "status", "json": line[2:].strip()}
        if line.startswith("C:"):
            return {"type": "config", "json": line[2:].strip()}
        # 仿真文本协议帧
        if line.startswith("CMD:"):
            rest = line[4:].strip()
            parts = rest.split(maxsplit=1)
            return {"type": "cmd", "name": parts[0],
                    "args": parts[1] if len(parts) > 1 else ""}
        if line == "?STATUS":
            return {"type": "query"}
        if line.startswith("SEG_BEGIN"):
            return {"type": "seg_begin", "count": int(line.split()[1])}
        if line.startswith("SEG_DATA"):
            return {"type": "seg_data", "count": int(line.split()[1])}
        if line == "SEG_END":
            return {"type": "seg_end"}
        if line == "ACK":
            return {"type": "ack"}
        if line.startswith("ERR"):
            return {"type": "err", "msg": line[4:].strip()}
        if line.startswith("STATUS:"):
            return {"type": "status", "fields": line[7:].strip()}
        return {"type": "raw", "text": line}
