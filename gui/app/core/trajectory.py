"""轨迹数据模型。

Stroke   : 一条连续笔画，points 为 [(x_mm, y_mm), ...]。
Trajectory: 一组笔画，是手绘/线稿/G-code 生成共用的数据源。
"""
from __future__ import annotations

from PyQt5.QtCore import QObject, pyqtSignal


class Stroke:
    """一条连续笔画（落笔状态下画出的折线）。"""

    __slots__ = ("points",)

    def __init__(self, points=None):
        self.points = list(points) if points else []

    def __len__(self) -> int:
        return len(self.points)

    def bbox(self):
        """返回 (x_min, y_min, x_max, y_max)，空笔画返回 None。"""
        if not self.points:
            return None
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return (min(xs), min(ys), max(xs), max(ys))

    def length(self) -> float:
        """折线总长（mm）。"""
        total = 0.0
        for i in range(1, len(self.points)):
            dx = self.points[i][0] - self.points[i - 1][0]
            dy = self.points[i][1] - self.points[i - 1][1]
            total += (dx * dx + dy * dy) ** 0.5
        return total

    def chaikin(self, iterations: int = 1) -> "Stroke":
        """Chaikin 角点切割平滑，返回新 Stroke（不修改自身）。"""
        pts = self.points
        for _ in range(iterations):
            if len(pts) < 3:
                break
            new = [pts[0]]
            for i in range(len(pts) - 1):
                ax, ay = pts[i]
                bx, by = pts[i + 1]
                new.append((0.75 * ax + 0.25 * bx, 0.75 * ay + 0.25 * by))
                new.append((0.25 * ax + 0.75 * bx, 0.25 * ay + 0.75 * by))
            new.append(pts[-1])
            pts = new
        return Stroke(pts)


class Trajectory(QObject):
    """当前工作轨迹（手绘笔画 + 线稿笔画的总容器）。"""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.strokes: list[Stroke] = []

    def clear(self):
        self.strokes.clear()
        self.changed.emit()

    def add_stroke(self, points):
        self.strokes.append(Stroke(points))
        self.changed.emit()

    def undo(self):
        if self.strokes:
            self.strokes.pop()
            self.changed.emit()

    def smooth_all(self, iterations: int = 1):
        if iterations > 0 and self.strokes:
            self.strokes = [s.chaikin(iterations) for s in self.strokes]
            self.changed.emit()

    def replace(self, strokes):
        """整体替换（线稿生成时使用），strokes 为 Stroke 列表。"""
        self.strokes = list(strokes)
        self.changed.emit()

    def reorder_nearest(self, start=(0.0, 0.0)):
        """贪心最近邻排序：优先画相邻笔画，减少抬笔空走。

        start 为排序起点的笔尖位置（可用当前末端位置）。
        返回 (travel_before, travel_after)，单位 mm。
        """
        from .planner import reorder_nearest  # 局部导入避免循环依赖

        ordered, before, after = reorder_nearest(self.strokes, start)
        self.strokes = ordered
        self.changed.emit()
        return before, after

    def bbox(self):
        boxes = [s.bbox() for s in self.strokes if s.bbox()]
        if not boxes:
            return None
        x0 = min(b[0] for b in boxes)
        y0 = min(b[1] for b in boxes)
        x1 = max(b[2] for b in boxes)
        y1 = max(b[3] for b in boxes)
        return (x0, y0, x1, y1)

    def point_count(self) -> int:
        return sum(len(s) for s in self.strokes)

    def total_length(self) -> float:
        return sum(s.length() for s in self.strokes)
