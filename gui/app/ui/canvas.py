"""绘图画布：工作区网格、轨迹/线稿显示、手绘采集、缩放平移、末端位置标记。"""
from __future__ import annotations

import traceback

from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import QWidget


class Canvas(QWidget):
    stroke_drawn = pyqtSignal(object)       # list[(x_mm, y_mm)]
    cursor_moved = pyqtSignal(float, float)  # 鼠标所在毫米坐标
    error = pyqtSignal(str)                 # 绘制异常文本
    zoom_changed = pyqtSignal(float)        # 缩放倍率变化

    MIN_ZOOM = 0.1
    MAX_ZOOM = 50.0

    COL_DRAW = QColor(30, 80, 180)
    COL_UNDRAWN = QColor(168, 168, 168)   # 未绘制部分
    COL_PREVIEW = QColor(150, 150, 150)
    COL_EE = QColor(220, 40, 40)
    COL_GRID_MINOR = QColor(233, 233, 233)
    COL_GRID_MAJOR = QColor(205, 205, 205)
    COL_BORDER = QColor(90, 90, 90)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(480, 360)
        self.setMouseTracking(True)
        self._workspace = (400.0, 300.0)
        self._trajectory = None
        self._trajectory_visible = True   # 调参预览时隐藏已提交的蓝色轨迹
        self._drawn_segments = None       # 已绘制线段数（None = 不区分，整体蓝色）
        self._preview = []           # 图片线稿调参预览笔画
        self._drawing_enabled = False
        self._current = None         # 正在手绘的点
        self._end = None             # (x, y, pen_down)
        self._zoom = 2.0             # px / mm
        self._offset = QPointF(40.0, 40.0)
        self._pan_pos = None

    # ---------------- 对外接口 ----------------

    def set_workspace(self, w_mm: float, h_mm: float):
        self._workspace = (float(w_mm), float(h_mm))
        self.update()

    def set_trajectory(self, traj):
        self._trajectory = traj
        if traj is not None:
            traj.changed.connect(self.update)
        self.update()

    def set_preview_strokes(self, strokes):
        self._preview = list(strokes)
        self.update()

    def set_trajectory_visible(self, visible: bool):
        """切换正式轨迹（蓝色）的显示；只影响显示，不修改数据。"""
        self._trajectory_visible = bool(visible)
        self.update()

    def set_drawn_segments(self, n):
        """设置已绘制线段数：>0 时按 已画(蓝)/未画(灰) 分色渲染；None 恢复整体蓝色。"""
        if self._drawn_segments == n:
            return
        self._drawn_segments = n
        self.update()

    def clear_preview(self):
        self._preview = []
        self.update()

    def set_drawing(self, enabled: bool):
        self._drawing_enabled = enabled
        if not enabled:
            self._current = None
        self.setCursor(Qt.CrossCursor if enabled else Qt.ArrowCursor)
        self.update()

    def set_end_effector(self, x_mm: float, y_mm: float, pen_down: bool = False):
        self._end = (float(x_mm), float(y_mm), bool(pen_down))
        self.update()

    # ---------------- 坐标变换 ----------------

    def _to_world(self, p: QPointF) -> QPointF:
        return (p - self._offset) / self._zoom

    def _to_screen(self, x: float, y: float) -> QPointF:
        return QPointF(x, y) * self._zoom + self._offset

    # ---------------- 缩放 ----------------

    def zoom(self) -> float:
        """当前缩放倍率（px/mm）。"""
        return self._zoom

    def set_zoom(self, factor: float):
        """设置缩放倍率（带上下限钳制）。"""
        self._zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, float(factor)))
        self.zoom_changed.emit(self._zoom)
        self.update()

    def zoom_by(self, factor: float):
        self.set_zoom(self._zoom * factor)

    def zoom_fit_pct(self) -> int:
        """缩放百分比：100% = 工作区刚好完整可见。"""
        w, h = self._workspace
        zx = (self.width() - 40) / max(w, 1.0)
        zy = (self.height() - 40) / max(h, 1.0)
        fit = max(self.MIN_ZOOM, min(zx, zy))
        return int(round(self._zoom / fit * 100))

    def fit_view(self):
        w, h = self._workspace
        zx = (self.width() - 40) / max(w, 1.0)
        zy = (self.height() - 40) / max(h, 1.0)
        self.set_zoom(max(self.MIN_ZOOM, min(zx, zy)))
        self._offset = QPointF(20.0, 20.0)
        self.update()

    # ---------------- 事件 ----------------

    def paintEvent(self, event):
        try:
            self._paint(event)
        except Exception:  # noqa: BLE001
            # 绘制异常不崩程序，转入日志面板方便排查
            self.error.emit("画布绘制异常:\n" + traceback.format_exc())

    def _paint(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), Qt.white)
        w, h = self._workspace

        # 网格（10mm 细线 / 50mm 粗线）
        x = 0.0
        while x <= w:
            s = self._to_screen(x, 0)
            major = abs(x % 50.0) < 1e-6
            p.setPen(QPen(self.COL_GRID_MAJOR if major else self.COL_GRID_MINOR, 1))
            p.drawLine(QPointF(s.x(), self.rect().top()),
                       QPointF(s.x(), self.rect().bottom()))
            x += 10.0
        y = 0.0
        while y <= h:
            s = self._to_screen(0, y)
            major = abs(y % 50.0) < 1e-6
            p.setPen(QPen(self.COL_GRID_MAJOR if major else self.COL_GRID_MINOR, 1))
            p.drawLine(QPointF(self.rect().left(), s.y()),
                       QPointF(self.rect().right(), s.y()))
            y += 10.0

        # 工作区边框
        top_left = self._to_screen(0, 0)
        bot_right = self._to_screen(w, h)
        p.setPen(QPen(self.COL_BORDER, 2))
        p.drawRect(QRectF(top_left, bot_right))

        # 线稿预览（灰色）
        if self._preview:
            self._draw_strokes(p, self._preview, self.COL_PREVIEW, 1.0)

        # 正式轨迹（蓝色；执行中按 已绘制/未绘制 分色）
        if self._trajectory is not None and self._trajectory_visible:
            strokes = [s.points for s in self._trajectory.strokes]
            if self._drawn_segments is None:
                self._draw_strokes(p, strokes, self.COL_DRAW, 1.6)
            else:
                self._draw_strokes_split(p, strokes, self._drawn_segments,
                                         self.COL_DRAW, self.COL_UNDRAWN)

        # 正在手绘的笔画
        if self._current is not None and len(self._current) >= 2:
            self._draw_strokes(p, [self._current], self.COL_DRAW, 1.6)

        # 末端位置标记
        if self._end is not None:
            x, y, pen = self._end
            s = self._to_screen(x, y)
            p.setPen(QPen(self.COL_EE, 2))
            p.drawEllipse(s, 8.0, 8.0)
            p.drawLine(QPointF(s.x() - 14, s.y()), QPointF(s.x() + 14, s.y()))
            p.drawLine(QPointF(s.x(), s.y() - 14), QPointF(s.x(), s.y() + 14))
            if pen:
                p.setBrush(QBrush(self.COL_EE))
                p.drawEllipse(s, 3.0, 3.0)
        p.end()

    def _draw_strokes(self, p: QPainter, strokes, color: QColor, width: float):
        p.setPen(QPen(color, width))
        for pts in strokes:
            if len(pts) < 2:
                continue
            poly = QPolygonF([self._to_screen(x, y) for (x, y) in pts])
            p.drawPolyline(poly)

    def _draw_strokes_split(self, p: QPainter, strokes, drawn_segments: int,
                            color_drawn: QColor, color_undrawn: QColor):
        """按已绘制线段数分色绘制：drawn 段蓝色，其余灰色。"""
        seg_off = 0
        for pts in strokes:
            m = len(pts)
            if m < 2:
                continue
            local = max(0, min(m - 1, drawn_segments - seg_off))
            seg_off += m - 1
            if local >= 1:
                self._draw_strokes(p, [pts[:local + 1]], color_drawn, 1.6)
            if local < m - 1:
                self._draw_strokes(p, [pts[local:]], color_undrawn, 1.2)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._drawing_enabled:
            w = self._to_world(QPointF(e.pos()))
            # 必须存元组：后续代码按 (x, y) 元组处理，混入 QPointF 会触发 TypeError
            self._current = [(w.x(), w.y())]
        elif e.button() in (Qt.MiddleButton, Qt.RightButton):
            self._pan_pos = QPointF(e.pos())

    def mouseMoveEvent(self, e):
        w = self._to_world(QPointF(e.pos()))
        self.cursor_moved.emit(w.x(), w.y())
        if self._pan_pos is not None:
            self._offset += QPointF(e.pos()) - self._pan_pos
            self._pan_pos = QPointF(e.pos())
            self.update()
        elif self._current is not None:
            pt = self._to_world(QPointF(e.pos()))
            if self._current:
                dx = pt.x() - self._current[-1][0]
                dy = pt.y() - self._current[-1][1]
                if dx * dx + dy * dy < 0.25:  # <0.5mm 过滤过密点
                    return
            self._current.append((pt.x(), pt.y()))
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._current is not None:
            if len(self._current) >= 2:
                self.stroke_drawn.emit(self._current)
            self._current = None
            self.update()
        if e.button() in (Qt.MiddleButton, Qt.RightButton):
            self._pan_pos = None

    def wheelEvent(self, e):
        factor = 1.25 if e.angleDelta().y() > 0 else 1.0 / 1.25
        pos = QPointF(e.pos())
        w = self._to_world(pos)
        new_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self._zoom * factor))
        if abs(new_zoom - self._zoom) < 1e-9:
            return
        self._zoom = new_zoom
        self._offset = pos - w * self._zoom  # 以光标为中心缩放
        self.zoom_changed.emit(self._zoom)
        self.update()
