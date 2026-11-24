"""설치할 프로그램 폴더 만들기 (2026-11-24, PyInstaller 대신).

지인 PC에서 PyInstaller로 만든 BlogVideo.exe가 실행할 때 백신에 걸렸다(서명 없는 실행 파일).
그래서 파이썬 재단(Python Software Foundation)이 서명한 공식 파이썬(embeddable)으로 앱을 켠다.
바로가기는 runtime\\pythonw.exe -m app.launcher 를 실행한다.

결과 폴더 구조:
    runtime/   공식 파이썬 (pythonw.exe 등, 서명됨) + Lib/site-packages (필요한 라이브러리)
    app/       이 프로그램의 파이썬 코드
    assets/    글꼴·음악
    ffmpeg/    ffmpeg.exe, ffprobe.exe (있으면)
    icon.ico   바로가기 아이콘

실행 (저장소 루트에서, 윈도우 + 파이썬 3.12):
    python packaging/build_app.py --out build/app
"""
from __future__ import annotations

import argparse
import hashlib
import io
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_VERSION = "3.12.10"          # 3.12의 마지막 설치 파일 배포판 (CI의 setup-python 3.12와 같은 버전대)
PY_TAG = "312"
EMBED_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
SIGNER = "Python Software Foundation"
SIGNED_FILES = ("pythonw.exe", "python.exe", f"python{PY_TAG}.dll")


def pth_lines() -> list[str]:
    """파이썬이 코드를 찾을 곳: 표준 라이브러리, 라이브러리 폴더, 그리고 앱 폴더(runtime의 한 단계 위)."""
    return [f"python{PY_TAG}.zip", ".", "Lib\\site-packages", "..", "import site"]


def dependencies() -> list[str]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["dependencies"]


def app_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "blogvideo-build"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def check_signatures(runtime: Path) -> None:
    """공식 파이썬 파일이 파이썬 재단 서명을 그대로 갖고 있는지 확인 (윈도우)."""
    import os

    files = ",".join(f"'{(runtime / f).resolve()}'" for f in SIGNED_FILES)
    ps = (f"foreach ($f in @({files})) {{ $s = Get-AuthenticodeSignature -LiteralPath $f; "
          f"if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch '{SIGNER}') "
          f"{{ Write-Output \"BAD $f $($s.Status) $($s.SignerCertificate.Subject)\" }} }}; Write-Output CHECKED")
    shell = shutil.which("pwsh") or "powershell"
    # PowerShell 7 안에서 5.1을 부르면 물려받은 모듈 경로 때문에 서명 확인 명령을 못 불러온다 → 지우고 부른다
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    out = subprocess.run([shell, "-NoProfile", "-Command", ps], capture_output=True, text=True, env=env)
    if "BAD" in out.stdout or "CHECKED" not in out.stdout:
        raise SystemExit("python signature check failed:\n" + out.stdout + out.stderr)
    print("signatures ok:", ", ".join(SIGNED_FILES))


def build(out: Path, embed_zip: Path | None = None) -> Path:
    if sys.platform != "win32" or sys.version_info[:2] != (3, int(PY_TAG[1:])):
        raise SystemExit(f"윈도우의 파이썬 3.{PY_TAG[1:]}로 실행해 주세요 (라이브러리 버전을 맞추기 위해).")
    if out.exists():
        shutil.rmtree(out)
    runtime = out / "runtime"
    runtime.mkdir(parents=True)
    data = embed_zip.read_bytes() if embed_zip else _download(EMBED_URL)
    print("python embed", PY_VERSION, hashlib.sha256(data).hexdigest())
    zipfile.ZipFile(io.BytesIO(data)).extractall(runtime)
    check_signatures(runtime)
    (runtime / f"python{PY_TAG}._pth").write_text("\n".join(pth_lines()) + "\n", "utf-8")

    site = runtime / "Lib" / "site-packages"
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check",
                    "--only-binary=:all:", "--target", str(site), *dependencies()], check=True)
    for junk in site.glob("*/__pycache__"):
        shutil.rmtree(junk, ignore_errors=True)
    shutil.rmtree(site / "bin", ignore_errors=True)            # 쓰지 않는 명령 실행 파일(uvicorn.exe 등)

    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "app", out / "app", ignore=ignore)
    shutil.copytree(ROOT / "assets", out / "assets", ignore=ignore)
    if (ROOT / "ffmpeg").is_dir():
        shutil.copytree(ROOT / "ffmpeg", out / "ffmpeg")
    shutil.copy2(ROOT / "packaging" / "icon.ico", out / "icon.ico")
    (out / "VERSION.txt").write_text(app_version() + "\n", "utf-8")

    # 미리 컴파일(설치 폴더는 쓰기 금지라 실행 때 만들 수 없다 → 처음 켤 때 빨라진다)
    subprocess.run([str(runtime / "python.exe"), "-m", "compileall", "-q", "-j", "0", str(out / "app"),
                    str(site)], check=False)
    # 앱이 이 파이썬으로 실제로 불러와지는지 확인
    subprocess.run([str(runtime / "python.exe"), "-c", "import app.launcher, app.main, app.render; print('import ok')"],
                   cwd=str(out), check=True)
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(ROOT / "build" / "app"))
    p.add_argument("--embed-zip", default="", help="미리 받아 둔 python-*-embed-amd64.zip (없으면 python.org에서 받음)")
    a = p.parse_args()
    build(Path(a.out), Path(a.embed_zip) if a.embed_zip else None)
    print("built", a.out)


if __name__ == "__main__":
    main()
