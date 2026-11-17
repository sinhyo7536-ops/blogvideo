"""설치 파일이 백신 오진·방화벽에 덜 걸리도록 하는 빌드 설정 (2026-11-17).

지인 PC에서 Defender가 BlogVideo.exe를 막아 설치 중 'MoveFile 실패; 코드 225'가 났다.
pip로 받은 PyInstaller 실행기(부트로더)는 다른 악성 프로그램도 많이 써서 오진이 잦다.
"""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = (ROOT / "packaging" / "blogvideo.spec").read_text("utf-8")
ISS = (ROOT / "packaging" / "installer.iss").read_text("utf-8-sig")
CI = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text("utf-8")


def test_exe_has_version_info_and_no_upx():
    assert "version=" in SPEC and "VSVersionInfo" in SPEC   # 파일 설명·회사·버전이 있는 실행 파일
    assert "upx=False" in SPEC                              # 압축 실행 파일은 오진이 잦다


def test_spec_reads_version_from_pyproject():
    assert "pyproject.toml" in SPEC
    tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]


def test_ci_builds_own_bootloader():
    assert "PYINSTALLER_COMPILE_BOOTLOADER" in CI and "--no-binary" in CI


def test_ci_scans_with_defender_and_fails_on_threat():
    assert "MpCmdRun" in CI and "-Scan" in CI
    assert re.search(r"throw .*Defender", CI)


def test_ci_installer_version_follows_pyproject():
    assert "pyproject.toml" in CI


def test_installer_never_opens_firewall():
    assert "add rule" not in ISS and "add rule" not in CI
    launcher = (ROOT / "app" / "launcher.py").read_text("utf-8")
    assert 'HOST = "127.0.0.1"' in launcher                  # PC 안에서만 열려서 방화벽 창이 안 뜬다
