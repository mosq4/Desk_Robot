"""占位协议编解码（协议留空！）。

当前用可读文本行模拟帧，便于仿真调试与人工抓包；
协议定稿后只需替换本模块的 encode_*/decode_line，上层接口不变。

临时帧格式（上位机 → 下位机）:
    CMD:<name> [args]     控制指令（ENABLE/DISABLE/ZERO/HOME/PEN_UP/PEN_DOWN/
                          PAUSE/RESUME/STOP/ESTOP/START/JOG <dx> <dy>/FEED <mm/min>）
    ?STATUS               状态查询
    SEG_BEGIN <n>         开始下发整段轨迹（n = 总行数）
    SEG_DATA <k>          下发 k 行 G-code（k 行正文紧跟其后）
    SEG_END               整段下发结束，下位机开始缓冲执行

应答（下位机 → 上位机）:
    ACK                   成功确认（每一帧都要确认）
    ERR <msg>             出错
    STATUS:key=val;...    状态回报（state/x/y/motors/line/buf/pen）
"""
from __future__ import annotations


class FrameCodec:
    """占位帧编解码。TODO(协议): 定稿后重写本类，替换成真实帧格式。"""

    # ---- 上位机 → 下位机 ----

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

    # ---- 双向解析 ----

    @staticmethod
    def decode_line(line: str):
        """解析一行帧文本，返回 dict 或 None。"""
        line = line.rstrip("\r\n")
        if not line:
            return None
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
