"""端到端冒烟测试（离屏）：
轨迹 → G-code → 连接仿真 → 整段下发 → 缓冲执行 → 校验末端位置。

运行: python tests/smoke_test.py
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication  # noqa: E402

from app.core.gcode import GcodeParams, trajectory_to_gcode  # noqa: E402
from app.main_window import MainWindow  # noqa: E402


def wait_until(app, cond, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def main():
    app = QApplication([])
    win = MainWindow()
    win.show()

    # 1. 手绘轨迹 → 正方形（40mm 边长）
    square = [(10, 10), (50, 10), (50, 50), (10, 50), (10, 10)]
    win.trajectory.add_stroke(square)
    assert len(win.trajectory.strokes) == 1, "轨迹添加失败"

    # 2. 生成 G-code
    gcode = trajectory_to_gcode(win.trajectory, GcodeParams(feed_rate=2400))
    assert "G21" in gcode and "M3" in gcode and "M5" in gcode and "M2" in gcode
    win.gcode_tab.set_text(gcode)
    print(f"[OK] G-code 生成: {len(gcode.splitlines())} 行")

    # 3. 连接仿真后端
    assert win.controller.connect_backend("simulation"), "仿真连接失败"
    win.control_tab.set_connected(True)
    win.controller.send_command("ENABLE")  # 仿真下位机：电机未使能时不执行
    print("[OK] 仿真后端已连接")

    # 4. 整段下发
    win.controller.send_segment(gcode)
    sender = win.controller._sender
    assert wait_until(app, lambda: not (sender and sender.isRunning()), 10.0), "下发超时"
    print("[OK] 整段下发完成")

    # 5. 等待执行结束（缓冲非空且执行行走到头）
    sim = win.controller._transport
    assert wait_until(
        app,
        lambda: len(sim._buffer) > 0 and sim._exec_index >= len(sim._buffer),
        15.0,
    ), f"执行未完成 state={sim.state} line={sim._exec_index}/{len(sim._buffer)}"
    print(f"[OK] 执行完毕: {sim._exec_index} 行")

    # 6. 末端位置应回到起点 (10,10)
    ex = sim.pos[0] - sim.zero[0]
    ey = sim.pos[1] - sim.zero[1]
    assert abs(ex - 10) < 0.5 and abs(ey - 10) < 0.5, f"末端位置错误: ({ex}, {ey})"
    print(f"[OK] 末端位置 ({ex:.2f}, {ey:.2f}) 正确")

    # 7. 控制指令路径：jog / 使能 / 暂停 等不抛异常
    win.controller.send_command("ENABLE")
    win.controller.send_command("JOG", "20 0")
    assert wait_until(app, lambda: sim.state == "IDLE", 3.0)
    win.controller.send_command("ZERO")
    win.controller.send_command("ESTOP")
    win.controller.send_command("STOP")
    app.processEvents()
    print("[OK] 控制指令路径正常")

    win.controller.disconnect()

    # 8. 手绘回归：直接注入 QMouseEvent 模拟绘制
    #    （曾因 QPointF 混入点列表，第一次移动就 TypeError 崩溃；
    #     注：QTest.mouseMove 在 offscreen 平台不送达事件，故直接调处理函数）
    from PyQt5.QtCore import QEvent, QPointF, Qt
    from PyQt5.QtGui import QMouseEvent

    def mk_evt(t, x, y):
        return QMouseEvent(t, QPointF(x, y), Qt.LeftButton, Qt.LeftButton,
                           Qt.NoModifier)

    before = len(win.trajectory.strokes)
    c = win.canvas
    c.set_drawing(True)
    c.mousePressEvent(mk_evt(QEvent.MouseButtonPress, 150, 100))
    c.mouseMoveEvent(mk_evt(QEvent.MouseMove, 200, 100))
    c.mouseMoveEvent(mk_evt(QEvent.MouseMove, 250, 150))
    c.mouseReleaseEvent(mk_evt(QEvent.MouseButtonRelease, 250, 150))
    c.set_drawing(False)
    app.processEvents()
    assert len(win.trajectory.strokes) == before + 1, \
        f"手绘未生成笔画: {len(win.trajectory.strokes)}/{before}"
    assert all(isinstance(p, tuple) for p in win.trajectory.strokes[-1].points), \
        "笔画点混入了非元组类型"
    print("[OK] 手绘回归通过")

    # 9. 缩放控制回归
    z0 = c.zoom()
    c.zoom_by(1.25)
    assert abs(c.zoom() - z0 * 1.25) < 1e-6, "zoom_by 未生效"
    c.set_zoom(99999)
    assert c.zoom() <= c.MAX_ZOOM, "缩放上限钳制失败"
    c.set_zoom(0.0001)
    assert c.zoom() >= c.MIN_ZOOM, "缩放下限钳制失败"
    c.fit_view()
    pct = c.zoom_fit_pct()
    assert 90 <= pct <= 110, f"适应窗口后百分比异常: {pct}%"
    assert win.lbl_zoom.text().endswith("%"), "缩放百分比未更新"
    print("[OK] 缩放控制回归通过")

    # 10. 图片读取回归（中文路径，cv2.imread 会失败、imdecode 必须成功）
    import numpy as np, cv2, os

    from app.core.lineart import LineartParams, load_lineart

    w_ws, h_ws = win.settings.workspace()
    assert (w_ws, h_ws) == (297.0, 210.0), f"工作区应为横置 A4: {(w_ws, h_ws)}"
    # 配置持久化回归（INI 写入/读回，注册表方案在本机写入会被拦截）
    from app.settings import AppSettings

    win.settings.set_image_params(LineartParams(mode="canny", threshold=77))
    p2 = AppSettings().image_params()
    assert p2.mode == "canny" and p2.threshold == 77, "配置持久化失败"
    win.settings.set_image_params(LineartParams())
    img_src = np.full((60, 80), 255, np.uint8)
    cv2.circle(img_src, (40, 30), 20, 0, -1)
    ok, buf = cv2.imencode(".png", img_src)
    assert ok
    img_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "测试图片.png")
    with open(img_path, "wb") as f:
        f.write(buf.tobytes())
    strokes = load_lineart(img_path, LineartParams(invert=True), (210, 297))
    os.remove(img_path)
    assert strokes, "中文路径图片读取失败"
    print(f"[OK] 图片读取回归通过（A4 工作区 {w_ws:.0f}×{h_ws:.0f}mm）")

    # 11. 多模式轮廓提取回归 + 预览隐藏蓝色线稿
    img2 = np.full((120, 160, 3), 255, np.uint8)  # BGR 白底
    cv2.circle(img2, (40, 40), 25, (0, 0, 0), -1)
    cv2.rectangle(img2, (90, 30), (130, 70), (0, 0, 0), -1)
    cv2.line(img2, (10, 110), (150, 110), (0, 0, 0), 2)
    win._image = img2
    cases = [
        LineartParams(mode="binary", invert=True, min_area=10),
        LineartParams(mode="canny", min_area=10),
        LineartParams(mode="adaptive", invert=True, min_area=10, threshold=5),
        LineartParams(mode="blackhat", min_area=5, threshold=60, blur=1),
        LineartParams(mode="sobel", min_area=10, threshold=60, blur=1),
        LineartParams(mode="xdog", min_area=10),
    ]
    for p in cases:
        strokes = win._image_to_strokes(p)
        assert strokes, f"模式 {p.mode} 未提取到轮廓"
        print(f"[OK] 模式 {p.mode}: {len(strokes)} 段")
    win._on_preview_params(cases[0])
    assert win.canvas._trajectory_visible is False, "预览时未隐藏蓝色线稿"
    win._on_commit_params(cases[0])
    assert win.canvas._trajectory_visible is True, "提交后未恢复蓝色线稿"
    print("[OK] 预览/提交显隐回归通过")

    # 12. 已绘制/未绘制分色回归
    from app.core.gcode import trajectory_to_gcode_with_progress

    text2, lmap = trajectory_to_gcode_with_progress(win.trajectory,
                                                    GcodeParams())
    nonempty = [l for l in text2.splitlines() if l.strip()]
    assert len(lmap) == len(nonempty), "进度映射与行数不一致"
    assert lmap[0] == 0 and lmap[-1] >= 1, f"进度映射边界异常: {lmap}"
    assert all(lmap[i] <= lmap[i + 1] for i in range(len(lmap) - 1)), \
        "进度映射必须单调不减"
    total_seg = sum(max(0, len(s.points) - 1)
                    for s in win.trajectory.strokes if len(s.points) >= 2)
    assert lmap[-1] == total_seg, f"总线段数不符: {lmap[-1]} vs {total_seg}"
    # 排空此前指令可能积压的异步状态回报（singleShot 延迟投递），避免干扰断言
    for _ in range(20):
        app.processEvents()
        time.sleep(0.005)
    win.canvas.set_drawn_segments(lmap[-1] // 2)
    app.processEvents()  # 触发分色绘制路径
    assert win.canvas._drawn_segments == lmap[-1] // 2
    win.canvas.set_drawn_segments(None)
    print("[OK] 分色进度映射回归通过")

    # 13. 图像方向调整回归（旋转/翻转/重置）
    img3 = np.full((100, 150, 3), 255, np.uint8)
    cv2.rectangle(img3, (10, 10), (140, 90), (0, 0, 0), -1)
    win._image = img3
    win._image_orig = img3.copy()
    win._on_orient("cw")
    assert win._image.shape[:2] == (150, 100), f"右转90°失败: {win._image.shape}"
    win._on_orient("flip_h")
    assert win._image.shape[:2] == (150, 100), "翻转后尺寸不应变化"
    win._on_orient("ccw")
    assert win._image.shape[:2] == (100, 150), "左转90°未还原尺寸"
    win._on_orient("reset")
    assert win._image.shape[:2] == (100, 150) and (win._image == img3).all(), \
        "重置未还原原始图"
    strokes = win._image_to_strokes(LineartParams(mode="binary", invert=True,
                                                  min_area=50))
    assert strokes, "方向调整后线稿提取失败"
    print("[OK] 图像方向调整回归通过（横置 A4 297×210mm）")

    # 14. 路径规划回归（相邻笔画优先）
    from app.core.planner import reorder_nearest
    from app.core.trajectory import Stroke

    plan_strokes = [
        Stroke([(0, 0), (10, 0)]),
        Stroke([(200, 200), (210, 200)]),
        Stroke([(8, 2), (8, 12)]),      # 紧邻第一条
    ]
    ordered, before, after = reorder_nearest(plan_strokes, start=(0, 0))
    assert after <= before, "排序后空走不应变长"
    assert ordered[0].points[0] == (0.0, 0.0), "最近邻首笔选择错误"
    pts_set = lambda ss: {tuple(p) for s in ss for p in s.points}
    assert pts_set(ordered) == pts_set(plan_strokes), "排序丢失/新增了笔画"
    win.trajectory.replace([Stroke(s.points) for s in plan_strokes])
    win._last_pos = None
    win._on_reorder()
    assert len(win.trajectory.strokes) == 3, "主窗口排序失败"
    print(f"[OK] 路径规划回归通过（空走 {before:.1f}→{after:.1f}mm）")

    # 15. 真实轨迹文件排序回归（tests/trajectory.gcode，247 笔）
    import re as _re

    def parse_gcode_file(path):
        strokes, cur = [], None
        with open(path, encoding="utf-8") as f:
            for line in f:
                body = line.split(";")[0].strip()
                if not body:
                    continue
                if body == "M3":
                    continue
                if body == "M5":
                    if cur and len(cur) >= 2:
                        strokes.append(cur)
                    cur = None
                    continue
                m = _re.match(r"G0 X([-\d.]+) Y([-\d.]+)", body)
                if m:
                    cur = [(float(m.group(1)), float(m.group(2)))]
                    continue
                m = _re.match(r"G1 X([-\d.]+) Y([-\d.]+)", body)
                if m and cur is not None:
                    cur.append((float(m.group(1)), float(m.group(2))))
        return strokes

    real = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "trajectory.gcode")
    real_strokes = [Stroke(s) for s in parse_gcode_file(real)]
    assert len(real_strokes) > 100, f"真实轨迹解析异常: {len(real_strokes)} 笔"
    ordered, before, after = reorder_nearest(real_strokes, start=(0, 0))
    assert after <= before * 0.4, \
        f"排序收益不足: 空走 {before:.0f} → {after:.0f}mm"
    assert len(ordered) == len(real_strokes), "排序丢失了笔画"
    print(f"[OK] 真实轨迹排序回归通过（247 笔，空走 {before:.0f}→{after:.0f}mm）")

    # 16. 文件对话框默认目录（首次打开应落在上位机 gui 目录）
    from app.settings import PROJECT_DIR

    assert os.path.basename(PROJECT_DIR) == "gui", \
        f"PROJECT_DIR 异常: {PROJECT_DIR}"
    assert win.image_tab._last_dir is None, "图片对话框起始目录应初始为 None"
    assert win._last_gcode_dir is None, "保存对话框起始目录应初始为 None"
    print("[OK] 默认目录回归通过")

    # 17. 版本号回归（app/__init__.py 为唯一来源，窗口标题跟随）
    from app import __version__

    assert __version__, "版本号为空"
    assert f"v{__version__}" in win.windowTitle(), "窗口标题未跟随版本号"
    print(f"[OK] 版本号回归通过（v{__version__}）")

    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
