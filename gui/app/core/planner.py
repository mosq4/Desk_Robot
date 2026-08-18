"""路径规划：笔画排序（贪心最近邻），优先画相邻笔画，减少抬笔空走。"""
from __future__ import annotations

from .trajectory import Stroke


def _dist(a, b) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return (dx * dx + dy * dy) ** 0.5


def _total_travel(strokes) -> float:
    """所有笔画之间的抬笔空走总长（第一笔前不算）。"""
    if not strokes:
        return 0.0
    total = 0.0
    prev_end = strokes[0].points[-1]
    for s in strokes[1:]:
        total += _dist(prev_end, s.points[0])
        prev_end = s.points[-1]
    return total


def reorder_nearest(strokes, start=(0.0, 0.0)):
    """贪心最近邻 + 2-opt 局部优化，重排笔画。

    返回 (ordered_strokes, travel_before, travel_after)：
    - 贪心：每一步从当前笔尖位置出发，选择"到笔画最近端点"代价最小的笔画，
      必要时反转笔画方向，使起笔点靠近当前笔尖；
    - 2-opt：对贪心结果做块反转/方向翻转微调，消除残留的远距离跳跃；
    - travel_before/travel_after 为排序前后的抬笔空走总长（mm）。
    """
    ordered = []
    remaining = [s for s in strokes if len(s.points) >= 2]
    singles = [s for s in strokes if len(s.points) < 2]  # 单点笔画排最后

    travel_before = _total_travel(strokes)
    pos = tuple(start)

    while remaining:
        best_i = None
        best_rev = False
        best_cost = float("inf")
        for i, s in enumerate(remaining):
            c1 = _dist(pos, s.points[0])
            c2 = _dist(pos, s.points[-1])
            if c1 <= c2:
                cost, rev = c1, False
            else:
                cost, rev = c2, True
            if cost < best_cost:
                best_i, best_rev, best_cost = i, rev, cost
        s = remaining.pop(best_i)
        if best_rev:
            s = Stroke(s.points[::-1])
        ordered.append(s)
        pos = s.points[-1]

    ordered.extend(singles)
    improved = _improve_2opt(ordered)
    travel_after = _total_travel(improved)
    return improved, travel_before, travel_after


def _improve_2opt(strokes, max_passes=30):
    """2-opt 局部优化：对笔画顺序做块反转（含单笔画方向翻转）。

    只接受能减少空走的改动，直到一轮无改进或达到轮数上限。
    """
    n = len(strokes)
    if n < 3:
        return strokes
    for _ in range(max_passes):
        best_delta = 0.0
        best_ij = None
        for i in range(n - 1):
            prev_end = strokes[i].points[-1]       # end_i
            next_start = strokes[i + 1].points[0]  # start_{i+1}
            for j in range(i + 1, n):
                j_end = strokes[j].points[-1]      # end_j
                jp1 = strokes[j + 1].points[0] if j + 1 < n else None
                before = _dist(prev_end, next_start) + (
                    _dist(j_end, jp1) if jp1 is not None else 0.0
                )
                after = _dist(prev_end, j_end) + (
                    _dist(next_start, jp1) if jp1 is not None else 0.0
                )
                delta = after - before
                if delta < best_delta:
                    best_delta = delta
                    best_ij = (i, j)
        if best_ij is None:
            break
        i, j = best_ij
        # 反转 i+1..j 段，段内每笔方向翻转，保持路径连续
        seg = [Stroke(s.points[::-1]) for s in reversed(strokes[i + 1:j + 1])]
        strokes[i + 1:j + 1] = seg
    return strokes
