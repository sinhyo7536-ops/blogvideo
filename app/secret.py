"""API 키 암호화. 윈도우에서는 DPAPI(현재 윈도우 계정에 묶인 암호화)를 쓴다.

설정 파일을 다른 PC나 다른 윈도우 계정으로 복사하면 풀 수 없다.
윈도우가 아닌 개발 환경에서는 암호화 없이 그대로 둔다.
"""
from __future__ import annotations

import os

_ENTROPY = b"BlogVideo.api-key.v1"


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32
    _UI_FORBIDDEN = 0x1

    def _blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
        buf = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _call(fn, data: bytes) -> bytes:
        src, _keep = _blob(data)
        ent, _keep2 = _blob(_ENTROPY)
        out = _Blob()
        if not fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, _UI_FORBIDDEN, ctypes.byref(out)):
            raise OSError("DPAPI failed")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(out.pbData)

    def protect(data: bytes) -> bytes:
        return _call(_crypt32.CryptProtectData, data)

    def unprotect(data: bytes) -> bytes:
        return _call(_crypt32.CryptUnprotectData, data)

else:

    def protect(data: bytes) -> bytes:
        return data

    def unprotect(data: bytes) -> bytes:
        return data
