"""路径约定：默认把成品放到当前用户桌面下的「壹线+年月日」文件夹。"""

from __future__ import annotations

import os
from datetime import datetime

PREFIX = "壹线"


def desktop_dir() -> str:
    """桌面目录（兼容 OneDrive 重定向、中文系统）。"""
    home = os.path.expanduser("~")
    for candidate in (
        os.path.join(home, "OneDrive", "Desktop"),
        os.path.join(home, "Desktop"),
        os.path.join(home, "桌面"),
    ):
        if os.path.isdir(candidate):
            return candidate
    return home


def out_folder_name(when: datetime | None = None) -> str:
    """壹线 + 年月日，例如 壹线20261118。"""
    stamp = (when or datetime.now()).strftime("%Y%m%d")
    return f"{PREFIX}{stamp}"


def default_out() -> str:
    return os.path.join(desktop_dir(), out_folder_name())
