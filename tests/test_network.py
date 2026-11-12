"""학교·회사 네트워크 대응: 윈도우 인증서 사용, 연결 실패 이유 표시."""
import ssl

import httpx
import pytest

from app.errors import UserError
from app.netsetup import use_system_certificates
from app.scraper.naver import fetch_post


def test_use_system_certificates_patches_ssl():
    import truststore

    try:
        assert use_system_certificates() is True
        assert ssl.SSLContext is truststore.SSLContext
    finally:
        truststore.extract_from_ssl()


def _fetch_with_error(monkeypatch, exc):
    def handler(request):
        raise exc

    import app.scraper.naver as naver
    real = naver._client
    monkeypatch.setattr(naver, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        fetch_post("https://blog.naver.com/abc123/223456789012")
    finally:
        monkeypatch.setattr(naver, "_client", real)


def test_ssl_failure_explains_certificate(monkeypatch, caplog):
    exc = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate in certificate chain")
    with pytest.raises(UserError) as e:
        _fetch_with_error(monkeypatch, exc)
    assert "인증서" in e.value.message
    assert "CERTIFICATE_VERIFY_FAILED" in caplog.text  # 기록 파일에 진짜 원인


def test_timeout_explains_timeout(monkeypatch):
    with pytest.raises(UserError) as e:
        _fetch_with_error(monkeypatch, httpx.ConnectTimeout("timed out"))
    assert "시간" in e.value.message


def test_proxy_failure_explains_proxy(monkeypatch):
    with pytest.raises(UserError) as e:
        _fetch_with_error(monkeypatch, httpx.ProxyError("407 Proxy Authentication Required"))
    assert "프록시" in e.value.message


def test_plain_connect_error_keeps_offline_message(monkeypatch):
    with pytest.raises(UserError) as e:
        _fetch_with_error(monkeypatch, httpx.ConnectError("[Errno 11001] getaddrinfo failed"))
    assert e.value.message.startswith("인터넷 연결을 확인해 주세요.")


def test_tts_certificate_failure_explained(tmp_path, short_script, caplog):
    from app.tts import synthesize_all

    async def cert_fail(text, voice, out):
        raise ssl.SSLCertVerificationError("certificate verify failed: self-signed certificate in certificate chain")

    with pytest.raises(UserError) as e:
        synthesize_all(short_script, "female", tmp_path, synth=cert_fail)
    assert "인증서" in e.value.message and "certificate verify failed" in caplog.text
