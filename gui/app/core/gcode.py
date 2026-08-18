"""轨迹 → G-code 文本，以及单行 G-code 解析（仿真下位机复用）。"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class GcodeParams:
    feed_rate: int = 3000        # 绘制速度 mm/min
    travel_rate: int = 6000      # 空走速度 mm/min
    pen_down_cmd: str = "M3"     # 落笔指令（占位，按实际下位机改）
    pen_up_cmd: str = "M5"       # 抬笔指令（占位）
    decimals: int = 2
    header: str = "Desk Robot generated"


def trajectory_to_gcode(trajectory, params: GcodeParams = None) -> str:
    """把 Trajectory 转成整段 G-code 文本。"""
    text, _ = trajectory_to_gcode_with_progress(trajectory, params)
    return text


def trajectory_to_gcode_with_progress(trajectory, params: GcodeParams = None):
    """生成 G-code 文本 + 已绘制进度映射。

    返回 (text, progress_per_line)：progress_per_line 与 text 的非空行一一对应，
    值 = 该行执行完成后已画完的线段数（跨所有笔画累计）。
    画布据此把"已绘制"部分和"未绘制"部分分色显示。
    """
    if params is None:
        params = GcodeParams()
    lines = [f"; {params.header}", "G21 ; 单位 mm", "G90 ; 绝对坐标"]
    progress = [0, 0, 0]
    seg_done = 0
    for stroke in trajectory.strokes:
        if len(stroke.points) < 2:
            continue
        x0, y0 = stroke.points[0]
        lines.append(
            f"G0 X{x0:.{params.decimals}f} Y{y0:.{params.decimals}f} "
            f"F{params.travel_rate} ; 空走到起点"
        )
        progress.append(seg_done)  # 本笔画尚未开始
        lines.append(params.pen_down_cmd)
        progress.append(seg_done)
        lines.append(f"G1 F{params.feed_rate}")
        progress.append(seg_done)
        for x, y in stroke.points[1:]:
            lines.append(f"G1 X{x:.{params.decimals}f} Y{y:.{params.decimals}f}")
            seg_done += 1
            progress.append(seg_done)
        lines.append(params.pen_up_cmd)
        progress.append(seg_done)  # 本笔画全部画完
    lines.append("M2 ; 程序结束")
    progress.append(seg_done)
    return "\n".join(lines) + "\n", progress


_G_RE = re.compile(r"G(\d+)", re.I)
_M_RE = re.compile(r"M(\d+)", re.I)


def _find_axis(body: str, letter: str):
    m = re.search(rf"{letter}\s*([-+]?\d*\.?\d+)", body, re.I)
    return float(m.group(1)) if m else None


def parse_gcode_line(line: str):
    """解析单行 G-code。

    返回 dict：{g, m, x, y, f}（缺失字段为 None），空行/纯注释返回 None。
    仅支持本上位机生成的那一小撮指令，够仿真下位机用。
    """
    body = line.split(";")[0].strip()
    if not body:
        return None
    res = {"g": None, "m": None, "x": None, "y": None, "f": None, "raw": body}
    gm = _G_RE.search(body)
    if gm:
        res["g"] = int(gm.group(1))
    mm = _M_RE.search(body)
    if mm:
        res["m"] = int(mm.group(1))
    res["x"] = _find_axis(body, "X")
    res["y"] = _find_axis(body, "Y")
    res["f"] = _find_axis(body, "F")
    return res
