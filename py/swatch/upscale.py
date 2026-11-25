"""AI 超分（Real-ESRGAN，ncnn Vulkan 版）。

工具是独立 exe，放在 `%LOCALAPPDATA%\\swatchcard-tools\\` 下即可，不依赖 PyTorch：
    realesrgan-ncnn-vulkan.exe
    models\\realesrgan-x4plus.{param,bin}
    vcomp140.dll ...

找不到时 `available()` 返回 False，调用方自动退回普通锐化。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from PIL import Image

EXE_NAME = "realesrgan-ncnn-vulkan.exe"
DEFAULT_MODEL = "realesrgan-x4plus"


def _candidates(extra: str | None = None) -> list[str]:
    out: list[str] = []
    if extra:
        out.append(extra)
    for env in ("LOCALAPPDATA", "APPDATA"):
        base = os.environ.get(env)
        if base:
            out.append(os.path.join(base, "swatchcard-tools", EXE_NAME))
            out.append(os.path.join(base, "swatchcard-tools", "realesrgan", EXE_NAME))
    pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out.append(os.path.join(pkg, "tools", EXE_NAME))
    return out


def find_exe(extra: str | None = None) -> str | None:
    for path in _candidates(extra):
        if path and os.path.isfile(path):
            return path
    return shutil.which("realesrgan-ncnn-vulkan") or shutil.which("realesrgan-ncnn-vulkan.exe")


def available(extra: str | None = None) -> bool:
    return find_exe(extra) is not None


def upscale(img: Image.Image, scale: int = 4, model: str = DEFAULT_MODEL,
            exe: str | None = None, timeout: int = 600) -> Image.Image | None:
    """超分一张图；失败返回 None（调用方回退）。"""
    exe_path = find_exe(exe)
    if not exe_path:
        return None
    work = tempfile.mkdtemp(prefix="swatchai_")
    src = os.path.join(work, "in.png")
    dst = os.path.join(work, "out.png")
    try:
        img.convert("RGB").save(src)
        cmd = [exe_path, "-i", src, "-o", dst, "-s", str(scale),
               "-n", model, "-f", "png"]
        # 必须以 exe 所在目录为工作目录，它按相对路径找 models
        flags = 0x08000000 if os.name == "nt" else 0
        subprocess.run(cmd, cwd=os.path.dirname(exe_path),
                       capture_output=True, timeout=timeout, creationflags=flags)
        if not os.path.isfile(dst):
            return None
        return Image.open(dst).convert("RGB")
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def enhance_rgba(lace: Image.Image, bg: tuple[int, int, int], scale: int = 4,
                 exe: str | None = None) -> Image.Image | None:
    """对带 alpha 的花边做超分：先合到底色上超分，再按放大后的 alpha 镂空。"""
    if lace.width < 8 or lace.height < 8:
        return None
    base = Image.new("RGB", lace.size, tuple(int(v) for v in bg))
    base.paste(lace, (0, 0), lace)
    big = upscale(base, scale, exe=exe)
    if big is None:
        return None
    alpha = lace.split()[-1].resize(big.size, Image.LANCZOS)
    out = big.convert("RGBA")
    out.putalpha(alpha)
    return out
