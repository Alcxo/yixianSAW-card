"""单张出卡 + 批量驱动。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace

from PIL import Image, ImageOps

from . import layout as layout_mod
from .imageio import SUPPORTED, load_image, save_image, save_pdf_book
from .lace import auto_polarity, extract, subject_luminance, to_white
from .upscale import enhance_rgba
from .table import ProductTable, find_table_near
from .template import TemplateProfile, detect_profile

COPY_SUFFIX = [
    r"\s*[-_ ]?副本\s*\d*$",
    r"\s*\(\d+\)$",
    r"\s*[-_ ]?copy\s*\d*$",
    r"[-_ ]?(双条|单条|双条带|单条带)$",
]


def model_from_filename(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    for pattern in COPY_SUFFIX:
        stem = re.sub(pattern, "", stem, flags=re.IGNORECASE)
    return stem.strip()


@dataclass
class CardOptions:
    mode: str = "auto"
    gap_ratio: float = 0.33
    coverage: float | None = None
    max_zoom: float = 3.0
    max_count: int = 4
    swap_note: str | None = "换色"
    kind: str = "auto"
    enhance: str = "auto"
    ai_exe: str | None = None
    font: str | None = None
    font_size: int | None = None
    threshold: float | None = None
    polarity: str = "auto"
    dpi: int = 300
    jpg_quality: int = 95


@dataclass
class BatchReport:
    ok: int = 0
    failed: int = 0
    items: list[dict] = field(default_factory=list)

    def add(self, **kwargs) -> None:
        self.items.append(kwargs)
        if kwargs.get("error"):
            self.failed += 1
        else:
            self.ok += 1


def make_card(
    template: Image.Image,
    profile: TemplateProfile,
    lace_path: str,
    *,
    model: str = "",
    width=None,
    height=None,
    opts: CardOptions | None = None,
) -> tuple[Image.Image, str, dict]:
    opts = opts or CardOptions()
    if opts.kind == "fabric" and opts.mode == "auto":
        # 整幅面料一律铺满整框（不够就居中裁掉多余）
        opts = replace(opts, mode="fill")
    source = load_image(lace_path, dpi=opts.dpi)
    card_is_dark = sum(profile.bg) / 3 < 128

    polarity = opts.polarity
    if polarity == "auto":
        # 先看这张图本身：白纸底黑花边 / 深垫布白花边
        polarity = auto_polarity(source)

    lace = extract(source, tol=opts.threshold, polarity=polarity, kind=opts.kind)

    # --- 深色主体（黑花边/黑面料）：只在「单条类」排版里换色
    swapped = False
    invert_template = False
    if card_is_dark and polarity == "dark" and subject_luminance(lace) < 90:
        mode_guess = layout_mod.decide_mode(profile, lace, opts.mode)
        if mode_guess == "fill":
            # 大条整幅：把模板底子反成白底黑字，花边保持原色
            invert_template = True
        else:
            # 小条 / 并列：把花边本身反成白色，黑卡不动
            lace = to_white(lace)
            swapped = True

    if invert_template:
        template = ImageOps.invert(template.convert("RGB"))
        profile = replace(profile, bg=(255, 255, 255), ink_color=(0, 0, 0))

    # --- 清晰度增强：能用 AI 超分就用，否则只做轻度锐化
    enhanced = False
    sharpen = 0.6
    zoom_cap = opts.max_zoom
    mode = (opts.enhance or "auto").lower()
    if mode in ("ai", "auto") and lace.height < profile.frame[3] - profile.frame[1]:
        big = enhance_rgba(lace, profile.bg, exe=opts.ai_exe)
        if big is not None:
            factor = big.height / max(lace.height, 1)
            lace = big
            enhanced = True
            sharpen = 0.25          # 超分后只补一点点，避免过硬
            zoom_cap = opts.max_zoom * factor   # 清晰度上限仍按「原图像素」算
        elif mode == "ai":
            raise RuntimeError(
                "找不到 Real-ESRGAN 超分程序。把 realesrgan-ncnn-vulkan.exe 和 models "
                "放到 %LOCALAPPDATA%\\swatchcard-tools\\ 下，或用 --ai-exe 指定。")
    elif mode not in ("ai", "auto", "sharpen", "off"):
        raise ValueError(f"未知的清晰度模式：{opts.enhance}（ai / sharpen / off / auto）")
    if mode == "off":
        sharpen = 0.0

    values = {
        "model": model or None,
        "width": layout_mod.format_number(width),
        "height": layout_mod.format_number(height),
    }
    card, used_mode, info = layout_mod.compose(
        template, profile, lace,
        mode=opts.mode,
        gap_ratio=opts.gap_ratio,
        coverage=opts.coverage,
        max_zoom=zoom_cap,
        max_count=opts.max_count,
        note=(opts.swap_note or None) if swapped else None,
        sharpen=sharpen,
        font_path=opts.font,
        font_size=opts.font_size,
        values=values,
    )
    info["swapped"] = swapped
    info["enhanced"] = enhanced
    return card, used_mode, info


def load_template(template_path: str, profile_path: str | None = None,
                  dpi: int = 300) -> tuple[Image.Image, TemplateProfile]:
    template = load_image(template_path, dpi=dpi)
    if profile_path and os.path.exists(profile_path):
        return template, TemplateProfile.load(profile_path)
    return template, detect_profile(template, source=os.path.abspath(template_path))


def process_one(
    template: Image.Image,
    profile: TemplateProfile,
    lace_path: str,
    out_dir: str,
    *,
    model: str = "",
    width=None,
    height=None,
    formats: list[str] = None,
    opts: CardOptions | None = None,
) -> dict:
    formats = formats or ["jpg"]
    card, used_mode, info = make_card(template, profile, lace_path,
                                      model=model, width=width, height=height, opts=opts)
    name = model or model_from_filename(lace_path)
    outputs = []
    for fmt in formats:
        path = os.path.join(out_dir, f"{name}.{fmt}")
        outputs.append(save_image(card, path, fmt,
                                  dpi=(opts or CardOptions()).dpi,
                                  jpg_quality=(opts or CardOptions()).jpg_quality))
    return {"card": card, "mode": used_mode, "info": info, "outputs": outputs, "name": name}


def batch(
    template_path: str,
    input_dir: str,
    out_dir: str,
    *,
    profile_path: str | None = None,
    table_path: str | None = None,
    formats: list[str] | None = None,
    opts: CardOptions | None = None,
    recursive: bool = False,
    pdf_book: str | None = None,
    logger=print,
) -> BatchReport:
    opts = opts or CardOptions()
    formats = formats or ["jpg"]
    template, profile = load_template(template_path, profile_path, dpi=opts.dpi)
    if not table_path:
        # 没指定表格就自动找：输入目录（或上级）里的规格信息/产品信息表
        table_path = find_table_near(input_dir)
        if table_path:
            logger(f"[表格] 自动使用 {table_path}")
    table = ProductTable.load(table_path) if table_path else ProductTable()

    files: list[str] = []
    if recursive:
        for root, _dirs, names in os.walk(input_dir):
            for n in sorted(names):
                if os.path.splitext(n)[1].lower() in SUPPORTED:
                    files.append(os.path.join(root, n))
    else:
        for n in sorted(os.listdir(input_dir)):
            p = os.path.join(input_dir, n)
            if os.path.isfile(p) and os.path.splitext(n)[1].lower() in SUPPORTED:
                files.append(p)

    report = BatchReport()
    cards: list[Image.Image] = []
    used: dict[str, int] = {}
    for path in files:
        model = model_from_filename(path)
        info = table.lookup(model)
        # 表格里对上的编号更干净（例如 197469扫描.pdf -> 197469）
        shown = str(info.get("model") or model)
        if shown in used:  # 同一批次里重名时加序号，避免互相覆盖
            used[shown] += 1
            shown = f"{shown}-{used[shown]}"
        else:
            used[shown] = 1
        try:
            result = process_one(template, profile, path, out_dir,
                                 model=shown, width=info.get("width"),
                                 height=info.get("height"), formats=formats, opts=opts)
            cards.append(result["card"])
            info = result.get("info") or {}
            cov = info.get("coverage")
            note = f"，占比 {cov * 100:.0f}%" if cov else ""
            if info.get("count"):
                note += f"，{info['count']} 条"
            if info.get("limited"):
                note += "（受清晰度限制）"
            if info.get("enhanced"):
                note += "，AI 超分"
            report.add(file=os.path.basename(path), model=shown,
                       mode=result["mode"], outputs=result["outputs"],
                       coverage=cov)
            logger(f"[OK] {os.path.basename(path)} -> {shown}（{result['mode']}{note}）")
        except Exception as exc:  # noqa: BLE001
            report.add(file=os.path.basename(path), model=shown, error=str(exc))
            logger(f"[失败] {os.path.basename(path)}: {exc}")

    if pdf_book and cards:
        save_pdf_book(cards, pdf_book, dpi=opts.dpi)
        logger(f"[PDF] 合并本：{pdf_book}（{len(cards)} 页）")

    return report
