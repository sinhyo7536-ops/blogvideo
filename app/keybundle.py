"""지인용 설치 파일: 설치 파일 끝에 API 키를 덧붙이고, 설치 후 꺼내 쓴다.

구조: [원래 설치 파일][트레일러 1024바이트]
트레일러: b"BVKEY1" + 길이(4자리 ASCII) + 인코딩된 키(16진수) + 공백 채움
설치 프로그램(Inno Setup [Code])이 자기 파일 끝 1024바이트를 읽어
인코딩된 키를 %APPDATA%\\BlogVideo\\import.key 로 저장하고,
프로그램이 처음 켜질 때 그것을 윈도우 계정 암호화(DPAPI)로 옮긴 뒤 지운다.

⚠️ 이것은 '숨기기'일 뿐 암호화가 아니다. 파일을 가진 사람은 키를 꺼낼 수 있다.
"""
from __future__ import annotations

import logging

from app import config
from app.errors import UserError
from app.paths import config_dir

log = logging.getLogger(__name__)

MAGIC = b"BVKEY1"
TRAILER_SIZE = 1024
IMPORT_FILE = "import.key"
_PAD = b"BlogVideo/keybundle/v1:do-not-share"
_SIGNATURE = "BlogVideo".encode("utf-16-le")  # 설치 파일 버전 정보에 들어 있는 이름
NOT_INSTALLER = "블로그 영상 변환기 설치 파일(BlogVideo-Setup.exe)을 골라 주세요."


def _xor(data: bytes) -> bytes:
    return bytes(b ^ _PAD[i % len(_PAD)] for i, b in enumerate(data))


def encode_key(key: str) -> str:
    return _xor(key.encode("utf-8")).hex()


def decode_key(text: str) -> str:
    return _xor(bytes.fromhex(text.strip())).decode("utf-8")


def _strip(data: bytes) -> bytes:
    if len(data) > TRAILER_SIZE and data[-TRAILER_SIZE:].startswith(MAGIC):
        return data[:-TRAILER_SIZE]
    return data


def read_trailer(data: bytes) -> str | None:
    tail = data[-TRAILER_SIZE:]
    if len(tail) != TRAILER_SIZE or not tail.startswith(MAGIC):
        return None
    n = int(tail[6:10])
    return decode_key(tail[10:10 + n].decode("ascii"))


def embed(installer: bytes, key: str) -> bytes:
    if not installer.startswith(b"MZ") or _SIGNATURE not in installer:
        raise UserError(NOT_INSTALLER)
    enc = encode_key(key).encode("ascii")
    trailer = MAGIC + f"{len(enc):04d}".encode("ascii") + enc
    if len(trailer) > TRAILER_SIZE:
        raise UserError("API 키가 너무 길어요.")
    return _strip(installer) + trailer.ljust(TRAILER_SIZE, b" ")


def import_pending_key() -> bool:
    """설치 프로그램이 남긴 키를 가져와 암호화 저장한다. 가져왔으면 True."""
    f = config_dir() / IMPORT_FILE
    if not f.exists():
        return False
    try:
        key = decode_key(f.read_text("ascii"))
        if not key.startswith("sk-"):
            raise ValueError("not an api key")
    except (ValueError, UnicodeError, OSError):
        log.warning("ignored invalid key import file")
        f.unlink(missing_ok=True)
        return False
    data = config.load_config()
    data["anthropic_api_key"] = key
    config.save_config(data)
    f.unlink(missing_ok=True)
    log.info("imported API key from installer")
    return True
