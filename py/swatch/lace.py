"""花边抠图。

思路：
1. 先按亮度把四周的扫描白边、纸边裁掉（它们总是横贯一整条）；
2. 按“极性”确定背景色——模板是深色底，就认定花边是画面里偏亮的那部分，
   取图像暗部的主色当背景；模板是浅色底则反过来；
3. 用颜色距离做软 alpha，叠到模板底色上时边缘自然过渡。

这样不依赖“背景恰好占多数”，白纸边、深色垫布、主体占满画面都能处理。
"""

from __future__ import annotations

import numpy as np
from PIL import Image


def _otsu(values: np.ndarray, bins: int = 256) -> float:
    """Otsu 阈值，用于把背景和花边分开。"""
    vmax = float(values.max())
    if vmax <= 0:
        return 30.0
    hist, edges = np.histogram(values, bins=bins, range=(0.0, vmax))
    hist = hist.astype(np.float64)
    total = hist.sum()
    if total <= 0:
        return 30.0
    centers = (edges[:-1] + edges[1:]) / 2.0
    w0 = np.cumsum(hist)
    w1 = total - w0
    m0 = np.cumsum(hist * centers)
    mt = m0[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        mu0 = m0 / w0
        mu1 = (mt - m0) / w1
        var = w0 * w1 * (mu0 - mu1) ** 2
    var = np.nan_to_num(var, nan=0.0, posinf=0.0, neginf=0.0)
    return float(centers[int(np.argmax(var))])


def _trim_side(means: np.ndarray, limit: int) -> int:
    """从一侧裁掉扫描白边：先吃整段纯白，再多吃一点点渐变带。"""
    cut = 0
    while cut < limit and means[cut] > 235:
        cut += 1
    if cut or (len(means) and means[0] > 180):
        cut += max(3, int(round(0.0035 * len(means))))
    return min(cut, limit)


def _background_color(arr: np.ndarray, polarity: str) -> np.ndarray:
    """按极性取背景色：亮花边就取暗部主色，暗花边就取亮部主色。"""
    lum = arr.mean(axis=2)
    if polarity == "light":
        sel = lum <= np.percentile(lum, 12)
    else:
        sel = lum >= np.percentile(lum, 88)
    if not sel.any():
        sel = np.ones(lum.shape, bool)
    return np.median(arr[sel], axis=0)


def auto_polarity(img: Image.Image) -> str:
    """判断花边比底色亮还是暗。

    看图像四周（背景）是不是**同一种颜色**：
    - 白纸底 + 黑花边：四边几乎全白 → "dark"（主体偏暗）；
    - 深色垫布 + 白花边：边缘是深色或黑白混杂（白纸边、扫描白边）
      → 归到 "light"（主体偏亮），这也是绝大多数来料照片的情况。
    """
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    h, w = a.shape[:2]
    b = max(2, int(0.02 * min(h, w)))
    ring = np.concatenate([
        a[:b].reshape(-1, 3), a[-b:].reshape(-1, 3),
        a[:, :b].reshape(-1, 3), a[:, -b:].reshape(-1, 3),
    ])
    lum = ring.mean(axis=1)
    bright = float((lum > 170).mean())
    return "dark" if bright > 0.75 else "light"


def to_white(rgba: Image.Image) -> Image.Image:
    """把深色花边反相成白色（保留 alpha 与灰阶层次）。

    黑色主体 → 接近纯白；半透明的网底 → 浅灰，和黑卡上的白花边观感一致。
    """
    arr = np.asarray(rgba).astype(np.float32)
    lum = arr[..., :3].mean(axis=2)
    inv = 255.0 - lum
    mask = arr[..., 3] > 128
    if mask.any():
        lo = float(np.percentile(inv[mask], 3))
        hi = float(np.percentile(inv[mask], 97))
        if hi - lo < 1:
            hi = lo + 1.0
        inv = np.clip((inv - lo) / (hi - lo) * 255.0, 0, 255)
    out = np.dstack([inv, inv, inv, arr[..., 3]]).astype(np.uint8)
    return Image.fromarray(out, mode="RGBA")


def _local_background(lum: np.ndarray, polarity: str) -> np.ndarray:
    """估计「局部底色」：先缩到 64 宽，再做 5x5 最大值（或最小值）滤波，
    把细窄的花边从背景里抹掉，最后放大回原尺寸。这样扫描的明暗渐变、
    纸底发灰都不会被误判成花边。
    """
    h, w = lum.shape
    small = Image.fromarray(np.clip(lum, 0, 255).astype(np.uint8)).resize(
        (64, max(8, int(round(64 * h / max(w, 1))))), Image.BILINEAR)
    s = np.asarray(small).astype(np.float32)
    k = 5
    pad = k // 2
    sp = np.pad(s, pad, mode="edge")
    stack = [sp[i:i + s.shape[0], j:j + s.shape[1]]
             for i in range(k) for j in range(k)]
    filtered = np.max(np.stack(stack), axis=0) if polarity == "dark" \
        else np.min(np.stack(stack), axis=0)
    back = Image.fromarray(np.clip(filtered, 0, 255).astype(np.uint8)).resize(
        (w, h), Image.BICUBIC)
    return np.asarray(back).astype(np.float32)


def _edge_band(arr: np.ndarray) -> float:
    """边上是否贴着一条「窄深色卷边」（扫描仪边缘的阴影/黑边）。

    整幅面料照片几乎都带这么一条：白纸边 → 暗条 → 面料明显变亮。
    花边照片没有（背景要么全白、要么整片深色垫布）。
    返回暗条宽度占图宽的比例，没有则为 0。
    """
    lum = arr.mean(axis=2)
    h, w = lum.shape
    cols = lum.mean(axis=0)
    best = 0.0
    for seq in (cols, cols[::-1]):
        limit = int(len(seq) * 0.22)
        i = 0
        while i < limit:
            if seq[i] >= 120:
                i += 1
                continue
            start = i
            while i < limit and seq[i] < 120:
                i += 1
            run = i - start
            if i >= len(seq):
                break
            after_win = seq[i:i + max(10, int(w * 0.4))]
            after = float(np.median(after_win))
            band = float(np.median(seq[start:i]))
            # 暗条之后必须是一大片持续比它亮的面料，才算「整幅面料」；
            # 若后面很快又暗回去（花边条 + 深色垫布），那是花边照片。
            sustained = float(np.mean(after_win > band + 40)) if after_win.size else 0.0
            if 0.015 * w <= run <= 0.12 * w and after - band > 40 and sustained > 0.65:
                best = max(best, run / w)
    return best


def _trim_dark_bands(arr: np.ndarray, limit_ratio: float = 0.15) -> np.ndarray:
    """裁掉贴在边上的深色条 / 残留白线（扫描仪边缘、卷边阴影），它们不属于面料。"""
    h, w = arr.shape[:2]
    lum = arr.mean(axis=2)
    center = float(np.median(lum[int(h * 0.3):int(h * 0.7), int(w * 0.3):int(w * 0.7)]))

    def cut(means: np.ndarray, limit: int) -> int:
        """从边上往里走，找到「连续几列都和面料亮度接近」的位置，前面的都算边料。"""
        limit = max(1, min(limit, len(means) - 8))
        run = 6
        for i in range(limit):
            window = means[i:i + run]
            if len(window) >= run and np.all(np.abs(window - center) < 35):
                return i
        return 0

    top = cut(lum.mean(axis=1), int(h * 0.12))
    bottom = cut(lum[::-1].mean(axis=1), int(h * 0.12))
    left = cut(lum.mean(axis=0), int(w * 0.15))
    right = cut(lum[:, ::-1].mean(axis=0), int(w * 0.15))
    if top + bottom >= h - 10 or left + right >= w - 10:
        return arr
    return arr[top:h - bottom, left:w - right]


def extract(img: Image.Image, tol: float | None = None, pad: int = 4,
            polarity: str = "light", kind: str = "auto") -> Image.Image:
    """返回裁到主体、带 alpha 的 RGBA 图（背景已透明）。

    polarity: "light" 花边比背景亮（黑底白花）；"dark" 比背景暗（白底黑花）。
    kind: "fabric" 整幅面料（不做抠图，只裁掉白边和卷边阴影，整块不透明）；
          "lace" 花边条（默认，抠图留通透）；"auto" 目前等同 "lace"。
    tol: 可选，手动指定阈值；不给就自动算。
    """
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    h, w = a.shape[:2]

    # --- 1. 先裁掉四周白边 / 纸边
    bright = a.mean(axis=2)
    limit = max(4, int(0.08 * min(h, w)))
    top = _trim_side(bright.mean(axis=1), limit)
    bottom = _trim_side(bright[::-1].mean(axis=1), limit)
    left = _trim_side(bright.mean(axis=0), limit)
    right = _trim_side(bright[:, ::-1].mean(axis=0), limit)
    if top + bottom < h - 10 and left + right < w - 10:
        a = a[top:h - bottom, left:w - right]
    h, w = a.shape[:2]

    # --- 1.5 整幅面料：边上有窄深色卷边、且整张照片偏亮 → 不抠图，整块用
    if kind == "fabric" or (kind == "auto" and _edge_band(a) > 0
                            and float(np.median(a.mean(axis=2))) > 110):
        piece = _trim_dark_bands(a)
        rgb = np.clip(piece * 1.02 + 2.0, 0, 255).astype(np.uint8)
        rgba = np.dstack([rgb, np.full(piece.shape[:2], 255, np.uint8)])
        return Image.fromarray(rgba, mode="RGBA")

    # --- 2. 背景 + 有方向的亮度差
    # 白花边（light）：用全局背景色，太细的网底也能保留通透感；
    # 黑花边（dark）：纸底常有明暗渐变，用「局部底色」相减，避免整片纸被当成花边。
    bg = _background_color(a, polarity)
    lum = a.mean(axis=2)
    if polarity == "dark":
        base = _local_background(lum, polarity)
        delta = np.maximum(base - lum, 0.0)
    else:
        base = np.full_like(lum, float(bg.mean()))
        delta = np.maximum(lum - base, 0.0)
    if tol is None:
        tol = max(14.0, _otsu(delta))
    tol = float(tol)

    low = 0.35 * tol
    high = 1.15 * tol
    alpha = np.clip((delta - low) / max(high - low, 1e-6), 0.0, 1.0)

    # --- 3. 主体包围盒：按列/行的 alpha 质量找主体那一段，零星噪点不算
    mask = alpha > 0.30

    def _span(mass: np.ndarray, dim: int, gap_ratio: float = 0.01) -> tuple[int, int] | None:
        flags = mass > max(3.0, 0.02 * dim)
        idx = np.flatnonzero(flags)
        if not len(idx):
            return None
        gap = max(3, int(round(gap_ratio * len(mass))))
        runs: list[tuple[int, int]] = []
        start = prev = int(idx[0])
        for i in idx[1:]:
            i = int(i)
            if i - prev <= gap:
                prev = i
            else:
                runs.append((start, prev))
                start = prev = i
        runs.append((start, prev))
        best = max(runs, key=lambda r: float(mass[r[0]:r[1] + 1].sum()))
        return best

    cols = _span(mask.sum(axis=0).astype(np.float64), h)
    rows = _span(mask.sum(axis=1).astype(np.float64), w)
    if cols and rows:
        x0, x1 = cols[0], cols[1] + 1
        y0, y1 = rows[0], rows[1] + 1
    else:  # 没识别出背景（整张都是花边），保留全图
        x0, y0, x1, y1 = 0, 0, w, h

    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(w, x1 + pad)
    y1 = min(h, y1 + pad)

    alpha = alpha[y0:y1, x0:x1]
    src = a[y0:y1, x0:x1]

    # 反预乘：把“已经在原背景上混过色”的像素还原成前景色，
    # 这样半透明的网底叠到卡面底色上仍然是原来的观感，不会被压暗。
    a3 = alpha[..., None]
    with np.errstate(divide="ignore", invalid="ignore"):
        fg = (src - (1.0 - a3) * bg) / np.maximum(a3, 1e-3)
    fg = np.nan_to_num(fg, nan=0.0, posinf=255.0, neginf=0.0)
    fg = np.clip(fg * 1.03 + 4.0, 0, 255)

    rgba = np.dstack([fg, alpha * 255.0]).astype(np.uint8)
    return Image.fromarray(rgba, mode="RGBA")


def subject_luminance(rgba: Image.Image) -> float:
    """主体（alpha>128）的平均亮度，用来判断是不是「黑色花边」。"""
    arr = np.asarray(rgba).astype(np.float32)
    mask = arr[..., 3] > 128
    if not mask.any():
        return 255.0
    return float(arr[..., :3][mask].mean())


def content_size(lace: Image.Image) -> tuple[int, int]:
    """alpha 真正覆盖的区域大小（用于排版决策）。"""
    a = np.asarray(lace.split()[-1]) > 40
    if not a.any():
        return lace.size
    ys, xs = np.where(a)
    return int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)
