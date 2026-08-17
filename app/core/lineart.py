"""图片 → 线稿：灰度化、二值化/Canny、轮廓提取、简化，映射到工作区毫米坐标。"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class LineartParams:
    mode: str = "binary"        # "binary" | "canny"
    threshold: int = 127        # 二值化阈值 0-255
    canny_low: int = 60
    canny_high: int = 150
    blur: int = 0               # 高斯模糊核半径（0 = 不模糊）
    simplify: float = 1.0       # 轮廓简化容差（像素，0 = 不简化）
    min_area: int = 20          # 最小轮廓面积（像素²）
    invert: bool = False        # 反色（白底黑线图勾选）
    fit_workspace: bool = True  # 自动缩放居中到工作区
    margin_mm: float = 10.0     # 缩放时的边距

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        p = LineartParams()
        for k, v in (d or {}).items():
            if hasattr(p, k):
                setattr(p, k, v)
        return p


def polylines_from_image(img_gray, params: LineartParams, workspace_size):
    """从灰度图提取折线列表 [[(x_mm, y_mm), ...], ...]。

    workspace_size=(w_mm, h_mm)。返回空列表表示没有轮廓。
    """
    import cv2

    edge = _extract_edge(img_gray, params)
    contours, _ = cv2.findContours(edge, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    strokes = []
    for c in contours:
        if cv2.contourArea(c) < params.min_area:
            continue
        if params.simplify > 0:
            c = cv2.approxPolyDP(c, params.simplify, False)
        if len(c) < 2:
            continue
        strokes.append([(float(p[0][0]), float(p[0][1])) for p in c])

    if not strokes:
        return []

    # 像素 → 毫米映射
    h, w = edge.shape
    if params.fit_workspace:
        ws_w, ws_h = workspace_size
        m = params.margin_mm
        scale = min((ws_w - 2 * m) / max(w, 1), (ws_h - 2 * m) / max(h, 1))
        ox = (ws_w - w * scale) / 2.0
        oy = (ws_h - h * scale) / 2.0
        to_mm = lambda p: (p[0] * scale + ox, p[1] * scale + oy)
    else:
        to_mm = lambda p: p  # 保留原始像素坐标（单位不再是 mm）

    return [[to_mm(pt) for pt in s] for s in strokes]


def _extract_edge(img, params: LineartParams):
    """按模式把灰度图转成线条二值图（白 = 前景，交给 findContours）。

    各模式内部自行做预处理；「模糊」滑块在 binary/canny 里是高斯模糊，
    在 adaptive/blackhat 里兼任结构参数（自适应块大小 / 黑帽核大小）。
    """
    import cv2
    import numpy as np

    def blur(image):
        if params.blur > 0:
            k = params.blur * 2 + 1
            return cv2.GaussianBlur(image, (k, k), 0)
        return image

    mode = params.mode

    if mode == "canny":
        return cv2.Canny(blur(img), params.canny_low, params.canny_high)

    if mode == "adaptive":
        # 自适应阈值：光照不均的图效果好；blockSize 由「模糊」滑块控制
        block = max(3, params.blur * 2 + 3)
        flag = cv2.THRESH_BINARY_INV if params.invert else cv2.THRESH_BINARY
        return cv2.adaptiveThreshold(blur(img), 255,
                                     cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     flag, block, params.threshold)

    if mode == "blackhat":
        # 黑帽：提取暗色细笔划（白底黑线/文字/素描），核大小由「模糊」滑块控制；
        # 输出本就是亮线黑底，反色选项不生效
        k = max(3, params.blur * 2 + 3)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
        bh = cv2.morphologyEx(blur(img), cv2.MORPH_BLACKHAT, kernel)
        return cv2.threshold(bh, params.threshold, 255,
                             cv2.THRESH_BINARY)[1]

    if mode == "sobel":
        # Sobel 梯度幅值：对噪声敏感，阈值建议调高；输出亮边黑底，反色不生效
        gx = cv2.Sobel(blur(img), cv2.CV_64F, 1, 0, ksize=3)
        gy = cv2.Sobel(blur(img), cv2.CV_64F, 0, 1, ksize=3)
        mag = cv2.magnitude(gx, gy)
        mag = cv2.normalize(mag, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        return cv2.threshold(mag, params.threshold, 255,
                             cv2.THRESH_BINARY)[1]

    if mode == "xdog":
        # XDoG（扩展高斯差分）：经典素描线条提取算法。
        # sigma 由「模糊」滑块控制（1.0 + 0.2*blur），threshold 为线条二值化阈值
        # （0-255 → 0-1）；k=1.0, p=20, phi=10, epsilon=-0.1 取论文常用值，
        # 输出白线黑底，反色选项不生效。
        sigma = 1.0 + params.blur * 0.2
        s1 = cv2.GaussianBlur(img, (0, 0), sigma)
        s2 = cv2.GaussianBlur(img, (0, 0), sigma * 1.6)
        dog = (s1.astype(np.float32) - s2.astype(np.float32)) / 255.0
        xdog = dog * (1.0 + 20.0 * np.tanh(10.0 * (dog + 0.1)))
        th = max(0.0, min(1.0, params.threshold / 255.0))
        return np.where(xdog > th, 255, 0).astype(np.uint8)

    # binary（默认）
    flag = cv2.THRESH_BINARY_INV if params.invert else cv2.THRESH_BINARY
    return cv2.threshold(blur(img), params.threshold, 255, flag)[1]


def load_lineart(path: str, params: LineartParams, workspace_size):
    """从文件读图并提取折线（字节流读取，支持中文路径）。"""
    import cv2
    import numpy as np

    try:
        with open(path, "rb") as f:
            data = np.frombuffer(f.read(), np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    except OSError as e:
        raise RuntimeError(f"无法读取图片: {path} ({e})")
    if img is None:
        raise RuntimeError(f"无法解析图片: {path}")
    return polylines_from_image(img, params, workspace_size)
