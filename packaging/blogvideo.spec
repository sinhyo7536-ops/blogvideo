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
    + collect_submodules("yt_dlp")
    + ["app.main", "app.render", "app.tts", "app.script", "app.scraper.naver", "truststore", "app.ai.models", "app.ai.analyze", "app.ai.rewrite", "app.ai.photos", "app.ai.shorts", "app.ai.youtube", "app.ai.checkup", "app.projects", "app.textscript", "app.visuals", "app.ytmusic", "app.director"]
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

# 실행 파일 정보(파일 설명·회사·버전). 정보 없는 실행 파일은 백신이 더 의심한다.
import tomllib  # noqa: E402

from PyInstaller.utils.win32.versioninfo import (  # noqa: E402
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo)

APP_VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
_nums = tuple(int(x) for x in (APP_VERSION.split(".") + ["0"] * 4)[:4])
VERSION = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_nums, prodvers=_nums),
    kids=[
        StringFileInfo([StringTable("041204B0", [
            StringStruct("CompanyName", "BlogVideo"),
            StringStruct("FileDescription", "블로그 영상 변환기"),
            StringStruct("FileVersion", APP_VERSION),
            StringStruct("InternalName", "BlogVideo"),
            StringStruct("OriginalFilename", "BlogVideo.exe"),
            StringStruct("ProductName", "블로그 영상 변환기"),
            StringStruct("ProductVersion", APP_VERSION),
            StringStruct("LegalCopyright", "BlogVideo"),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0412, 1200])]),
    ],
)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BlogVideo",
    console=False,
    upx=False,                # 압축 실행 파일은 백신 오진이 잦다
    version=VERSION,
    icon=str(ROOT / "packaging" / "icon.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="BlogVideo")
