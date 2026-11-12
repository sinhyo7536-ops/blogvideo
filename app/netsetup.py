"""네트워크 설정.

학교·회사 네트워크나 보안 프로그램은 HTTPS를 중간에서 검사하면서 자체 인증서를 쓴다.
그 인증서는 윈도우에 등록돼 있어 크롬은 잘 되지만, 파이썬은 자체 인증서 목록(certifi)을 써서
연결을 거부한다. truststore로 윈도우(운영체제) 인증서 저장소를 쓰게 한다.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def use_system_certificates() -> bool:
    try:
        import truststore

        truststore.inject_into_ssl()
        return True
    except Exception:
        log.exception("could not enable system certificate store")
        return False


def describe_network_error(exc: BaseException) -> str:
    """연결 실패를 사용자용 문장으로. 진짜 원인은 기록 파일에 남긴다."""
    import httpx

    text = f"{type(exc).__name__}: {exc}"
    log.warning("network error: %s", text, exc_info=exc)
    base = "인터넷 연결을 확인해 주세요."
    if "CERTIFICATE" in text.upper() or "SSL" in text.upper():
        return base + " (보안 인증서 확인에 실패했어요. 학교·회사 네트워크나 보안 프로그램 때문일 수 있어요.)"
    if isinstance(exc, httpx.ProxyError) or "proxy" in text.lower():
        return base + " (프록시 연결에 실패했어요. 학교·회사 네트워크 설정 때문일 수 있어요.)"
    if isinstance(exc, httpx.TimeoutException):
        return base + " (응답 시간이 너무 오래 걸렸어요. 잠시 뒤 다시 시도해 주세요.)"
    return base
