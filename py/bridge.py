"""浏览器端（Pyodide）出卡桥接。

Worker 把照片写进虚拟文件系统后调用这里的函数，算法完全复用桌面版的 swatch 包，
所以出卡效果和电脑版一模一样（只是没有 AI 超分，因为跑不了 Real-ESRGAN）。
"""

from __future__ import annotations

import gc
import json
import os
import sys

sys.path.insert(0, "/app")

from swatch.card import CardOptions, make_card          # noqa: E402
from swatch.imageio import load_image, save_image       # noqa: E402
from swatch.table import ProductTable                   # noqa: E402
from swatch.template import TemplateProfile, detect_profile  # noqa: E402

_STATE = {"template": None, "profile": None, "table": ProductTable()}


def init(template_path: str, profile_path: str) -> str:
    """读入模板和识别结果（profile）。"""
    _STATE["template"] = load_image(template_path)
    _STATE["profile"] = TemplateProfile.load(profile_path)
    _STATE["table"] = ProductTable()
    info = _STATE["profile"].to_dict()
    return json.dumps({
        "ok": True,
        "size": info["size"],
        "frame": info["frame"],
        "font": info["font_size"],
    }, ensure_ascii=False)


def detect(template_path: str, profile_path: str) -> str:
    """换了新模板时重新识别版面，并存成 profile。"""
    img = load_image(template_path)
    profile = detect_profile(img, source=template_path)
    profile.save(profile_path)
    info = profile.to_dict()
    return json.dumps({
        "ok": True,
        "size": info["size"],
        "frame": info["frame"],
        "frame_size": [info["frame"][2] - info["frame"][0] + 1,
                       info["frame"][3] - info["frame"][1] + 1],
        "font": info["font_size"],
        "slots": info["slots"],
    }, ensure_ascii=False)


def set_table(path: str) -> str:
    """规格表可换可不换，空字符串 = 不用表格。"""
    if path and os.path.isfile(path):
        _STATE["table"] = ProductTable.load(path)
    else:
        _STATE["table"] = ProductTable()
    return json.dumps({"ok": True}, ensure_ascii=False)


def lookup(model: str) -> str:
    """查一下型号在表格里的花宽 / 花高（给界面预览用）。"""
    info = _STATE["table"].lookup(model) if model else {}
    return json.dumps({
        "model": info.get("model"),
        "width": info.get("width"),
        "height": info.get("height"),
    }, ensure_ascii=False)


def render_one(input_path: str, out_path: str, model: str,
               params_json: str) -> str:
    """出一张卡，写到 out_path（JPG），返回附加信息。"""
    params = json.loads(params_json or "{}")
    table = _STATE["table"]
    info = table.lookup(model) if model else {}
    shown = str(info.get("model") or model or "")

    opts = CardOptions(
        mode=params.get("mode") or "auto",
        gap_ratio=float(params.get("gap") or 0.33),
        coverage=params.get("coverage"),
        max_zoom=float(params.get("maxZoom") or 3.0),
        max_count=int(params.get("maxCount") or 4),
        swap_note=params.get("swapNote", "换色") or None,
        enhance="sharpen",              # 手机上跑不了 Real-ESRGAN，只做锐化
        kind=params.get("kind") or "auto",
        dpi=300,
        jpg_quality=int(params.get("quality") or 95),
    )
    card, mode, detail = make_card(
        _STATE["template"], _STATE["profile"], input_path,
        model=shown, width=info.get("width"), height=info.get("height"),
        opts=opts,
    )
    save_image(card, out_path, "jpg", dpi=300, jpg_quality=opts.jpg_quality)
    size = card.size
    card.close()
    gc.collect()
    return json.dumps({
        "ok": True,
        "name": shown,
        "mode": mode,
        "size": list(size),
        "coverage": detail.get("coverage"),
        "count": detail.get("count"),
        "scale": detail.get("scale"),
        "limited": detail.get("limited"),
        "swapped": detail.get("swapped"),
    }, ensure_ascii=False)
