"""模板识别：自动定位虚线框、型号/花宽/花高三个填写位、字号与文字颜色。

识别结果是一份 JSON profile，可缓存、可手工微调：

    {
      "size": [w, h],
      "bg": [r, g, b],
      "ink_color": [r, g, b],
      "frame": [x0, y0, x1, y1],          # 虚线框内缘（含）
      "font_size": 63,
      "slots": {
        "model":  {"x": 549, "y": 473, "align": "left"},
        "width":  {"x": 1579, "y": 473, "align": "right"},
        "height": {"x": 2203, "y": 473, "align": "right"}
      }
    }
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image


class TemplateError(RuntimeError):
    pass


# ---------------------------------------------------------------- 基础工具


def _bg_color(arr: np.ndarray, band: int = 2) -> np.ndarray:
    h, w = arr.shape[:2]
    b = max(1, min(band, h // 4, w // 4))
    edge = np.concatenate([
        arr[:b].reshape(-1, 3), arr[-b:].reshape(-1, 3),
        arr[:, :b].reshape(-1, 3), arr[:, -b:].reshape(-1, 3),
    ])
    return np.median(edge, axis=0)


def _groups(idx: list[int], tol: int) -> list[list[int]]:
    out: list[list[int]] = []
    for i in idx:
        if out and i - out[-1][-1] <= tol:
            out[-1].append(i)
        else:
            out.append([i])
    return out


def _is_dashed(vec: np.ndarray, min_runs: int = 20, max_cv: float = 0.15) -> bool:
    """一行/一列上的墨点是等间距短划线时返回 True（虚线框特征）。"""
    xs = np.flatnonzero(vec)
    if len(xs) < min_runs * 3:
        return False
    gaps = np.diff(xs) - 1
    gaps = gaps[gaps >= 2]
    if len(gaps) < min_runs:
        return False
    mean = gaps.mean()
    return mean > 0 and (gaps.std() / mean) < max_cv


def _extent(vec: np.ndarray) -> int:
    xs = np.flatnonzero(vec)
    return int(xs[-1] - xs[0] + 1) if len(xs) else 0


# ---------------------------------------------------------------- 识别


@dataclass
class TemplateProfile:
    size: tuple[int, int]
    bg: tuple[int, int, int]
    ink_color: tuple[int, int, int]
    frame: tuple[int, int, int, int]
    font_size: int
    slots: dict[str, dict[str, Any]] = field(default_factory=dict)
    source: str = ""

    @property
    def frame_size(self) -> tuple[int, int]:
        x0, y0, x1, y1 = self.frame
        return x1 - x0 + 1, y1 - y0 + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "size": list(self.size),
            "bg": list(self.bg),
            "ink_color": list(self.ink_color),
            "frame": list(self.frame),
            "font_size": self.font_size,
            "slots": self.slots,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TemplateProfile":
        return cls(
            size=tuple(d["size"]),
            bg=tuple(d["bg"]),
            ink_color=tuple(d.get("ink_color", (255, 255, 255))),
            frame=tuple(d["frame"]),
            font_size=int(d["font_size"]),
            slots=d["slots"],
            source=d.get("source", ""),
        )

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str) -> "TemplateProfile":
        with open(path, encoding="utf-8-sig") as fh:
            return cls.from_dict(json.load(fh))


def detect_profile(img: Image.Image, source: str = "") -> TemplateProfile:
    """从空白模板里自动识别版面。识别失败时抛出 TemplateError。"""
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    h, w = arr.shape[:2]
    bg = _bg_color(arr)
    dist = np.linalg.norm(arr - bg, axis=2)
    ink = dist > 60

    # --- 1. 找虚线框：横向虚线（占宽 >80%）给出上下边
    row_cand = [y for y in range(h)
                if _extent(ink[y]) > 0.80 * w and _is_dashed(ink[y])]
    rg = _groups(row_cand, tol=2)
    if len(rg) < 2:
        raise TemplateError(
            "没能识别出虚线框的上下边。请确认模板是黑底（或纯色底）+ 等距虚线框，"
            "或者用 `detect --profile` 生成 profile 后手工填写 frame 坐标。")
    top = rg[0][-1] + 1
    bottom = rg[-1][0] - 1

    # --- 2. 纵向虚线给出左右边（限定在框内，避开页脚二维码）
    sub = ink[top + 2:bottom - 1, :]
    col_cand = [x for x in range(w)
                if _extent(ink[:, x]) > 0.60 * h and _is_dashed(sub[:, x])]
    cg = _groups(col_cand, tol=2)
    if len(cg) < 2:
        raise TemplateError(
            "没能识别出虚线框的左右边（纵向虚线）。可在 profile 里手工填写 frame。")
    left = cg[0][-1] + 1
    right = cg[-1][0] - 1

    if right - left < 0.2 * w or bottom - top < 0.2 * h:
        raise TemplateError(
            f"识别到的框太小：{(left, top, right, bottom)}。请检查模板或用 profile 手工指定。")

    # --- 3. 找标签行：框上方最后一段文字
    dashed_rows = set(row_cand)
    above = ink[:max(0, top - 2), :]
    counts = above.sum(axis=1)
    text_rows = [y for y in np.flatnonzero(counts > 5).tolist() if y not in dashed_rows]
    if not text_rows:
        raise TemplateError("虚线框上方没有找到标签文字，无法定位填写位。")
    tg = _groups(text_rows, tol=25)
    y0, y1 = tg[-1][0], tg[-1][-1]
    band = ink[y0:y1 + 1, :]

    # --- 4. 按列聚成文字块
    proj = band.sum(axis=0) > 0
    runs = _groups(np.flatnonzero(proj).tolist(), tol=int(0.006 * w))
    clusters = []
    for r in runs:
        s, e = r[0], r[-1]
        ys = np.flatnonzero(band[:, s:e + 1].sum(axis=1) > 0) + y0
        clusters.append({"x0": s, "x1": e, "y0": int(ys.min()), "y1": int(ys.max())})

    # 把同一标签里的字符并起来（词间距小），不同标签之间留大空档
    merge_gap = int(0.035 * w)
    merged: list[dict] = []
    for c in clusters:
        if merged and c["x0"] - merged[-1]["x1"] <= merge_gap:
            merged[-1]["x1"] = c["x1"]
            merged[-1]["y0"] = min(merged[-1]["y0"], c["y0"])
            merged[-1]["y1"] = max(merged[-1]["y1"], c["y1"])
        else:
            merged.append(dict(c))

    if len(merged) < 2:
        raise TemplateError("标签文字太少，无法定位填写位。")

    heights = [c["y1"] - c["y0"] + 1 for c in merged]
    label_h = float(np.median(heights))
    font_size = max(8, int(round(label_h / 0.73)))
    gap_after_label = int(round(1.1 * label_h))
    gap_before_unit = int(round(0.6 * label_h))

    def label_color() -> tuple[int, int, int]:
        region = arr[y0:y1 + 1, :]
        m = np.linalg.norm(region - bg, axis=2) > 60
        if not m.any():
            return (255, 255, 255)
        px = region[m]
        # 取墨迹里偏亮的那部分，避免抗锯齿边缘把颜色拉灰
        lum = px.mean(axis=1)
        core = px[lum >= np.percentile(lum, 75)]
        return tuple(int(v) for v in np.median(core, axis=0))

    def slot_after(idx: int) -> dict[str, Any]:
        c = merged[idx]
        return {"x": c["x1"] + gap_after_label, "y": c["y0"], "align": "left"}

    def slot_before(idx_label: int, idx_unit: int) -> dict[str, Any]:
        c = merged[idx_label]
        u = merged[idx_unit]
        return {"x": u["x0"] - gap_before_unit, "y": c["y0"], "align": "right"}

    if len(merged) >= 5:
        # 常见布局：标签1 标签2 单位 标签3 单位
        slots = {
            "model": slot_after(0),
            "width": slot_before(1, 2),
            "height": slot_before(3, 4),
        }
    elif len(merged) == 4:
        slots = {
            "model": slot_after(0),
            "width": slot_after(1),
            "height": slot_before(2, 3),
        }
    else:
        slots = {"model": slot_after(0)}
        if len(merged) >= 2:
            slots["width"] = slot_after(1)
        if len(merged) >= 3:
            slots["height"] = slot_after(2)

    return TemplateProfile(
        size=(w, h),
        bg=tuple(int(v) for v in np.round(bg)),
        ink_color=label_color(),
        frame=(left, top, right, bottom),
        font_size=font_size,
        slots=slots,
        source=source,
    )
