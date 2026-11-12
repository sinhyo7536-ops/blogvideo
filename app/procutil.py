"""외부 프로그램(ffmpeg) 실행 옵션."""
from __future__ import annotations

import os
import subprocess

CREATE_NO_WINDOW = 0x08000000


def quiet_kwargs(os_name: str | None = None) -> dict:
    """창 없는 앱에서 ffmpeg를 띄울 때 콘솔 창이 깜빡이지 않게 한다."""
    kw: dict = {"stdin": subprocess.DEVNULL}
    if (os_name or os.name) == "nt":
        kw["creationflags"] = CREATE_NO_WINDOW
    return kw
