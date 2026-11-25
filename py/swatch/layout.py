"""排版：把花边放进虚线框（满幅 / 单条 / 双条），再写型号与规格。"""

from __future__ import annotations

import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .template import TemplateProfile

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
CJK_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def load_font(path: str | None, size: int, text: str = "") -> ImageFont.FreeTypeFont:
    """选字体：型号里带中文时优先用中文字体，避免出现方框。"""
    needs_cjk = any(ord(ch) > 0x2E7F for ch in text)
    candidates = ([path] if path else [])
    if needs_cjk:
        candidates += CJK_FONT_CANDIDATES
    candidates += FONT_CANDIDATES
    if not needs_cjk:
        candidates += CJK_FONT_CANDIDATES
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default(size)


def _resize_h(lace: Image.Image, height: int) -> Image.Image:
    scale = height / lace.height
    width = max(1, int(round(lace.width * scale)))
    return lace.resize((width, height), Image.LANCZOS)


def _paste(card: Image.Image, lace: Image.Image, x: int, y: int) -> None:
    card.paste(lace, (x, y), lace)


def _sharpen_rgba(img: Image.Image, percent: float) -> Image.Image:
    """只锐化 RGB，不动 alpha，避免透明边缘出现亮边。"""
    if percent <= 0:
        return img
    rgb = img.convert("RGB").filter(
        ImageFilter.UnsharpMask(radius=1.1, percent=int(percent), threshold=2))
    out = rgb.convert("RGBA")
    out.putalpha(img.split()[-1])
    return out


def decide_mode(profile: TemplateProfile, lace: Image.Image, mode: str = "auto") -> str:
    """按花边宽度决定排版模式（compose 内部也用它）。"""
    if mode != "auto":
        return mode
    x0, y0, x1, y1 = profile.frame
    fw, fh = x1 - x0 + 1, y1 - y0 + 1
    strip_w = lace.width * (fh / max(lace.height, 1))
    if strip_w >= 0.68 * fw:
        return "fill"
    if strip_w >= 0.45 * fw:
        return "single"
    return "double"


def compose(
    template: Image.Image,
    profile: TemplateProfile,
    lace: Image.Image,
    *,
    mode: str = "auto",
    gap_ratio: float = 0.33,
    coverage: float | None = None,
    max_zoom: float | None = 3.0,
    max_count: int = 4,
    note: str | None = None,
    sharpen: float = 0.6,
    font_path: str | None = None,
    font_size: int | None = None,
    values: dict[str, str | None] | None = None,
) -> tuple[Image.Image, str, dict]:
    """返回 (成品图, 实际使用的排版模式, 附加信息)。

    coverage：双条时两条花边的横向总宽 / 虚线框宽（0~1）。
    max_zoom：清晰度上限，原图像素最多放大多少倍；到上限还达不到 coverage
    就停在上限，info["limited"] 为 True 并回报实际占比。
    """
    card = template.copy()
    values = values or {}

    fx0, fy0, fx1, fy1 = profile.frame
    fw, fh = fx1 - fx0 + 1, fy1 - fy0 + 1
    lw, lh = lace.size
    strip = _resize_h(lace, fh)
    strip_w = strip.width
    info: dict = {}

    mode = decide_mode(profile, lace, mode)

    if mode == "fill":
        # 等比放大到刚好盖住整框，再居中裁掉多余部分
        scale = max(fw / lw, fh / lh)
        big = lace.resize((max(1, int(round(lw * scale))), max(1, int(round(lh * scale)))),
                          Image.LANCZOS)
        left = (big.width - fw) // 2
        top = (big.height - fh) // 2
        crop = big.crop((left, top, left + fw, top + fh))
        _paste(card, _sharpen_rgba(crop, sharpen), fx0, fy0)

    elif mode == "single":
        if strip_w > fw:  # 比框还宽的整幅布：按宽度贴合，上下居中留边
            scale = fw / lw
            strip = lace.resize((fw, max(1, int(round(lh * scale)))), Image.LANCZOS)
            _paste(card, _sharpen_rgba(strip, sharpen),
                   fx0, fy0 + (fh - strip.height) // 2)
        else:
            _paste(card, _sharpen_rgba(strip, sharpen),
                   fx0 + (fw - strip_w) // 2, fy0)

    elif mode in ("double", "multi"):
        strip, span, count, used_gap, info = _layout_repeat(
            lace, fw, fh, coverage, max_zoom, max_count=max_count)
        strip = _sharpen_rgba(strip, sharpen)
        total = count * span + (count - 1) * used_gap
        left = fx0 + (fw - total) // 2
        top = fy0 + (fh - strip.height) // 2
        for i in range(count):
            # 单条的透明留白居中放进自己的槽位，保证条与条之间的缝隙就是 used_gap
            x = left + i * (span + used_gap) + (span - strip.width) // 2
            _paste(card, strip, int(round(x)), top)

    else:
        raise ValueError(f"未知排版模式：{mode}（可选 auto/fill/single/double）")

    size = font_size or profile.font_size
    color = tuple(profile.ink_color)
    for key in ("model", "width", "height"):
        text = values.get(key)
        if text in (None, ""):
            continue
        slot = profile.slots.get(key)
        if not slot:
            continue
        text = str(text)
        _draw_text(card, text, slot, load_font(font_path, size, text), color)

    if note:
        _draw_note(card, note, profile, font_path)

    return card, mode, info


def _draw_note(card: Image.Image, note: str, profile: TemplateProfile,
               font_path: str | None) -> None:
    """右下角小角标（例如「换色」）：自动避开二维码等已有内容。"""
    w, h = card.size
    size = max(14, int(round(profile.font_size * 0.42)))
    font = load_font(font_path, size, note)
    canvas = Image.new("L", (max(64, len(note) * size * 3), size * 3), 0)
    ImageDraw.Draw(canvas).text((8, 8), note, fill=255, font=font)
    bbox = canvas.getbbox()
    if not bbox:
        return
    glyph = canvas.crop(bbox)

    # 在右下角区域找已有内容（二维码、地址）的边界，角标放在它的下方或左侧
    arr = np.asarray(card.convert("RGB")).astype(np.float32)
    bg = np.array(profile.bg, dtype=np.float32)
    top = int(h * 0.85)
    left = int(w * 0.80)
    region = arr[top:, left:]
    ink = np.linalg.norm(region - bg, axis=2) > 60
    rows = np.flatnonzero(ink.any(axis=1))
    cols = np.flatnonzero(ink.any(axis=0))
    bottom = top + int(rows[-1]) if len(rows) else top
    block_left = left + int(cols[0]) if len(cols) else left

    margin = max(8, int(w * 0.006))
    gap = max(6, int(h * 0.004))
    if bottom + gap + glyph.height <= h - margin:      # 二维码下方放得下
        x = w - margin - glyph.width
        y = bottom + gap
    else:                                              # 放到二维码左边
        x = max(margin, block_left - gap - glyph.width)
        y = max(top, bottom - glyph.height)

    color = tuple(int(c * 0.85) for c in profile.ink_color)
    tile = Image.new("RGBA", glyph.size, color + (0,))
    tile.putalpha(glyph.point(lambda v: int(v * 0.7)))
    card.paste(tile, (x, y), tile)


def _opaque_ratio(img: Image.Image) -> float:
    """alpha > 128 的像素占比，用来算「有色部分」面积。"""
    alpha = np.asarray(img.split()[-1])
    return float((alpha > 128).mean())


def _layout_repeat(lace: Image.Image, fw: int, fh: int,
                   coverage: float | None = None,
                   max_zoom: float | None = 3.0,
                   min_gap_ratio: float = 0.02,
                   max_count: int = 4) -> tuple[Image.Image, int, int, int, dict]:
    """并列填充（原「双条」）。

    1) 花边先按**原尺寸**（等比缩放到框高，不拉伸）摆成 N 条，
       N ≈ 目标占比 × 框宽 ÷ 单条实际宽度，最多 max_count 条；
    2) 如果条数已经顶到 max_count 还达不到目标占比，就把每一条**等比放大**
       （横向纵向同一个倍数，始终不失真），直到够占比、或受框宽/清晰度上限约束。
    返回 (单条图, 条宽, 条数, 间距, 信息)。
    """
    lw, lh = lace.size
    target = 0.50 if not coverage else max(0.10, min(0.95, float(coverage)))
    min_gap = max(2, int(round(min_gap_ratio * fw)))
    cap = float(max_zoom) if max_zoom else 99.0
    base = fh / lh                       # 「上下顶满」的等比缩放

    def build(scale: float) -> tuple[Image.Image, int]:
        sw = max(1, int(round(lw * scale)))
        sh = max(1, int(round(lh * scale)))
        img = lace.resize((sw, sh), Image.LANCZOS)
        if sh > fh:                      # 高度超出就居中裁掉多余
            top = (sh - fh) // 2
            img = img.crop((0, top, sw, top + fh))
        alpha = np.asarray(img.split()[-1]) > 128
        cols = alpha.sum(axis=0) > max(2, 0.01 * img.height)
        return img, int(cols.sum()) or sw

    # --- 1. 原尺寸摆条
    limited = base > cap
    scale = min(base, cap)
    strip, span = build(scale)
    n_max = max(1, int((fw - min_gap) // (span + min_gap)))
    n = max(1, min(int(round(target * fw / max(span, 1))), n_max, max_count))

    # --- 2. 条数到顶还不够 → 整条等比放大，取最接近目标的那一版
    zoomed = False
    if n >= max_count and n * span < target * fw - 0.5:
        need_span = min(target * fw / n, (fw - (n - 1) * min_gap) / n)
        best_err = abs(n * span / fw - target)
        best = (strip, span, scale)
        cur_scale, cur_span = scale, span
        for _ in range(3):
            if cur_span <= 0:
                break
            ws = min(cur_scale * need_span / cur_span, cap)
            if ws <= cur_scale * 1.001:
                break
            s2, sp2 = build(ws)
            err = abs(n * sp2 / fw - target)
            if err < best_err:
                best_err, best = err, (s2, sp2, ws)
            if err <= 0.008:
                break
            cur_scale, cur_span = ws, sp2
        strip, span, scale = best
        zoomed = scale > base * 1.001

    gap = max(1.0, (fw - n * span) / (n + 1))         # 两边和中间均匀留白
    info = {
        "coverage": round(n * span / fw, 4),
        "target": round(target, 4),
        "count": n,
        "span": span,
        "gap": round(gap / fw, 4),
        "scale": round(scale / base, 3),              # 相对「原尺寸」放大了多少
        "zoomed": zoomed,
        "limited": limited,
    }
    return strip, span, n, int(round(gap)), info


def _draw_text(card: Image.Image, text: str, slot: dict, font, color) -> None:
    canvas = Image.new("L", (max(64, len(text) * font.size * 2), font.size * 3), 0)
    ImageDraw.Draw(canvas).text((8, 8), text, fill=255, font=font)
    bbox = canvas.getbbox()
    if not bbox:
        return
    glyph = canvas.crop(bbox)
    x = slot["x"] - glyph.width if slot.get("align") == "right" else slot["x"]
    tile = Image.new("RGBA", glyph.size, color + (0,))
    tile.putalpha(glyph)
    card.paste(tile, (int(x), int(slot["y"])), tile)


def format_number(value) -> str | None:
    """把 4.50 / '4.5' / 10.0 统一成 '4.5' / '10'。"""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return text
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"
