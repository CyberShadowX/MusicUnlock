# -*- mode: python ; coding: utf-8 -*-
"""Single-file Windows build; run through build_windows.ps1."""
from importlib.metadata import distribution
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH)
ffmpeg = root / "vendor" / "ffmpeg" / "ffmpeg.exe"
if not ffmpeg.is_file():
    raise SystemExit("Missing staged FFmpeg. Run build_windows.ps1 first.")

datas = [
    (str(root / "web" / "static"), "web/static"),
    (str(root / "assets" / "kugou_key.xz"), "assets"),
    (str(root / "LICENSE"), "licenses/music-geshizhuanhuan"),
    (str(root / "vendor" / "ffmpeg" / "LICENSE"), "licenses/ffmpeg"),
    (str(root / "THIRD_PARTY_NOTICES.txt"), "licenses"),
    (str(root / "build" / "build-info.json"), "."),
]
datas += collect_data_files("webview")
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.is_file():
    datas.append((str(python_license), "licenses/python"))
for package in ("Flask", "Werkzeug", "Jinja2", "MarkupSafe", "itsdangerous",
                "click", "blinker", "pycryptodome", "mutagen", "pywebview",
                "pythonnet", "clr_loader", "bottle", "proxy_tools", "cffi",
                "pycparser", "typing_extensions"):
    installed = distribution(package)
    for entry in installed.files or []:
        if any(word in entry.name.lower() for word in ("license", "copying", "notice")):
            path = Path(installed.locate_file(entry))
            if path.is_file():
                datas.append((str(path), f"licenses/{package}"))

analysis = Analysis(
    [str(root / "windows_launcher.py")],
    pathex=[str(root)],
    binaries=[(str(ffmpeg), "bin")],
    datas=datas,
    hiddenimports=["webview.platforms.winforms", "webview.platforms.edgechromium",
                   "clr_loader.netfx", "_cffi_backend"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tkinter", "webview.platforms.qt", "webview.platforms.gtk",
              "webview.platforms.cocoa", "webview.platforms.cef", "webview.platforms.android"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="MusicUnlock",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
