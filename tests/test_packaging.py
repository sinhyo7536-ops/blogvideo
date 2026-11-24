"""설치 파일이 백신·방화벽에 걸리지 않도록 하는 빌드 설정.

2026-11-17: 지인 PC에서 설치 중 Defender가 BlogVideo.exe를 막았다('MoveFile 실패; 코드 225').
2026-11-24: 설치 뒤 다시 켤 때도 보안 프로그램이 막았다 → PyInstaller(서명 없는 실행 파일)를 버리고
파이썬 재단이 서명한 공식 파이썬(embeddable)의 pythonw.exe 로 앱을 켠다 (사용자 결정, 무료 방법).
"""
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ISS = (ROOT / "packaging" / "installer.iss").read_text("utf-8-sig")
CI = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text("utf-8")
sys.path.insert(0, str(ROOT / "packaging"))
import build_app  # noqa: E402


def test_no_pyinstaller_anymore():
    assert not (ROOT / "packaging" / "blogvideo.spec").exists()
    assert "pip install pyinstaller" not in CI.lower() and "blogvideo.spec" not in CI
    assert "build\\dist" not in ISS


def test_runtime_finds_app_code_next_to_it():
    lines = build_app.pth_lines()
    assert lines[0] == f"python{build_app.PY_TAG}.zip" and "Lib\\site-packages" in lines
    assert ".." in lines and lines[-1] == "import site"     # runtime\.. = 앱 폴더 (app 패키지)


def test_build_uses_signed_official_python_of_same_minor_version():
    assert build_app.EMBED_URL.startswith("https://www.python.org/ftp/python/")
    assert build_app.PY_VERSION.startswith("3." + build_app.PY_TAG[1:] + ".")
    assert "pythonw.exe" in build_app.SIGNED_FILES and build_app.SIGNER == "Python Software Foundation"
    assert 'python-version: "3.' + build_app.PY_TAG[1:] + '"' in CI


def test_dependencies_come_from_pyproject():
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["dependencies"]
    assert build_app.dependencies() == deps


def test_shortcuts_start_signed_pythonw():
    assert '#define AppRun "runtime\\pythonw.exe"' in ISS and '#define AppArgs "-m app.launcher"' in ISS
    for section in ('Name: "{group}\\{#AppName}"', 'Name: "{autodesktop}\\{#AppName}"'):
        line = next(l for l in ISS.splitlines() if l.startswith(section))
        assert 'Filename: "{app}\\{#AppRun}"' in line and 'Parameters: "{#AppArgs}"' in line
        assert 'WorkingDir: "{app}"' in line and 'IconFilename: "{app}\\icon.ico"' in line


def test_upgrade_removes_old_unsigned_exe():
    assert 'Type: files; Name: "{app}\\{#OldExe}"' in ISS and '#define OldExe "BlogVideo.exe"' in ISS
    assert 'Name: "{app}\\_internal"' in ISS
    assert "/IM pythonw.exe" not in ISS                       # 다른 파이썬 프로그램을 끄지 않는다
    assert "CloseApplications=force" in ISS


def test_ci_checks_signatures_and_scans_with_defender():
    assert "Get-AuthenticodeSignature" in CI and "Python Software Foundation" in CI
    assert "MpCmdRun" in CI and 'throw "Microsoft Defender detected' in CI
    assert "pyproject.toml" in CI                             # 설치 파일 버전 = pyproject 버전


def test_installer_never_opens_firewall():
    assert "add rule" not in ISS and "add rule" not in CI
    launcher = (ROOT / "app" / "launcher.py").read_text("utf-8")
    assert 'HOST = "127.0.0.1"' in launcher                  # PC 안에서만 열려서 방화벽 창이 안 뜬다


@pytest.mark.skipif(sys.platform != "win32", reason="윈도우 전용")
def test_resource_paths_work_from_plain_source_layout():
    from app.paths import resource_path

    assert resource_path("assets/fonts/NotoSansKR-Bold.ttf").exists()
