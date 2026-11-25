"""读写图片：支持 jpg/png/webp/bmp/tif 以及 pdf（借助 PyMuPDF）。"""

from __future__ import annotations

import io
import os

from PIL import Image, ImageOps

RASTER_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
PDF_EXT = {".pdf"}
SUPPORTED = RASTER_EXT | PDF_EXT


def _pdf_to_image(path: str, dpi: int) -> Image.Image:
    try:
        import pymupdf as fitz  # PyMuPDF >= 1.24 的正式包名
    except ImportError:
        try:
            import fitz  # 旧包名
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "读取 PDF 需要 PyMuPDF，请先运行：pip install pymupdf"
            ) from exc

    doc = fitz.open(path)
    try:
        page = doc[0]
        # 优先取页面里内嵌的原始位图，避免重采样损失
        best = None
        for info in page.get_images(full=True):
            xref = info[0]
            try:
                img = doc.extract_image(xref)
            except Exception:
                continue
            area = img.get("width", 0) * img.get("height", 0)
            if best is None or area > best[0]:
                best = (area, img)
        if best and best[1].get("width", 0) >= 400:
            im = Image.open(io.BytesIO(best[1]["image"]))
        else:
            pix = page.get_pixmap(dpi=dpi)
            im = Image.open(io.BytesIO(pix.tobytes("png")))
        return im.convert("RGB")
    finally:
        doc.close()


def _looks_like_pdf(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(5) == b"%PDF-"
    except OSError:
        return False


def load_image(path: str, dpi: int = 300) -> Image.Image:
    """读入任意支持的格式，统一返回 RGB。

    扩展名缺失或不认识时按文件头判断（PDF 或位图），所以
    `197469.pdf`、无扩展名的导出来源、`.jfif` 之类都能读。
    """
    if not os.path.isfile(path):
        raise ValueError(f"文件不存在：{path}")
    ext = os.path.splitext(path)[1].lower()
    if ext in PDF_EXT or (ext not in RASTER_EXT and _looks_like_pdf(path)):
        return _pdf_to_image(path, dpi)
    try:
        im = Image.open(path)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"无法识别图片格式：{path}（{exc}）") from exc
    im = ImageOps.exif_transpose(im)
    return im.convert("RGB")


def save_image(img: Image.Image, path: str, fmt: str | None = None,
               dpi: int = 300, jpg_quality: int = 95) -> str:
    """按扩展名或 fmt 保存，支持 jpg / png / pdf。"""
    fmt = (fmt or os.path.splitext(path)[1].lstrip(".")).lower()
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    if fmt in ("jpg", "jpeg"):
        img.save(path, "JPEG", quality=jpg_quality, subsampling=0, dpi=(dpi, dpi))
    elif fmt == "png":
        img.save(path, "PNG", optimize=True, dpi=(dpi, dpi))
    elif fmt == "pdf":
        # 300dpi 下 2480x3508 正好是 A4，PDF 里按像素/分辨率铺满一页
        img.save(path, "PDF", resolution=dpi)
    else:
        raise ValueError(f"不支持的输出格式：{fmt}")
    return path


def save_pdf_book(images: list[Image.Image], path: str, dpi: int = 300) -> str:
    """把多张卡合并成一个多页 PDF。"""
    if not images:
        raise ValueError("没有可写入 PDF 的图片")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    images[0].save(path, "PDF", resolution=dpi, save_all=True,
                   append_images=images[1:])
    return path
