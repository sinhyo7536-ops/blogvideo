# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — 실행: pyinstaller packaging/blogvideo.spec (저장소 루트에서)
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
SUFFIX = ".exe" if sys.platform == "win32" else ""

datas = [
    (str(ROOT / "app" / "web"), "app/web"),
    (str(ROOT / "assets"), "assets"),
]
ff = ROOT / "ffmpeg"
for name in (f"ffmpeg{SUFFIX}", f"ffprobe{SUFFIX}", "LICENSE.txt", "README.txt"):
    if (ff / name).exists():
        datas.append((str(ff / name), "ffmpeg"))

datas += collect_data_files("certifi")

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("edge_tts")
    + collect_submodules("anthropic")
    + collect_submodules("google.genai")
    + collect_submodules("yt_dlp")
    + ["app.main", "app.render", "app.tts", "app.script", "app.scraper.naver", "truststore", "app.gemini", "app.visuals", "app.ytmusic"]
)

a = Analysis(
    [str(ROOT / "app" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "playwright"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BlogVideo",
    console=False,
    icon=str(ROOT / "packaging" / "icon.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="BlogVideo")
