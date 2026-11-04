"""Cross-platform CJK font discovery for the 2D layout layer."""

from __future__ import annotations

import os
import sys


_WIN_BOLD = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyhbd.ttf",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\SourceHanSansSC-Bold.otf",
]
_WIN_REG = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyh.ttf",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\Deng.ttf",
]
_LINUX_BOLD = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
]
_LINUX_REG = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]
_MAC_BOLD = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]
_MAC_REG = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]


def _first_existing(paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def find_font(bold: bool = False) -> str:
    env = os.environ.get("S3D_FONT_BOLD" if bold else "S3D_FONT")
    if env and os.path.exists(env):
        return env
    if sys.platform.startswith("win"):
        return _first_existing(_WIN_BOLD if bold else _WIN_REG) or _first_existing(_WIN_REG)
    if sys.platform == "darwin":
        return _first_existing(_MAC_BOLD if bold else _MAC_REG)
    found = _first_existing(_LINUX_BOLD if bold else _LINUX_REG)
    if found:
        return found
    # last resort: any CJK-ish font in the usual directories
    for root in ("/usr/share/fonts", "/usr/local/share/fonts"):
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                low = f.lower()
                if low.endswith((".ttc", ".otf", ".ttf")) and ("cjk" in low or "noto" in low or "wqy" in low):
                    return os.path.join(dirpath, f)
    raise RuntimeError(
        "no CJK font found. Install Noto Sans CJK or set S3D_FONT=/path/to/font.ttf")
