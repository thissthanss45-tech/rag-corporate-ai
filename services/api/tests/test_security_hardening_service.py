from fastapi import HTTPException

from app.core.config import settings
from app.main import _is_docx_payload, _is_ogg_payload, _is_pdf_payload, _is_rtf_payload, _is_txt_payload, require_service_token


def test_pdf_signature_validation() -> None:
    assert _is_pdf_payload(b"%PDF-1.7\n...")
    assert not _is_pdf_payload(b"NOTPDF")


def test_docx_signature_validation() -> None:
    assert _is_docx_payload(b"PK\x03\x04....")
    assert not _is_docx_payload(b"DOCX")


def test_ogg_signature_validation() -> None:
    assert _is_ogg_payload(b"OggS\x00\x02")
    assert not _is_ogg_payload(b"RIFF....")


def test_txt_signature_validation() -> None:
    assert _is_txt_payload("План продаж на квартал".encode("utf-8"))
    assert _is_txt_payload("Отчёт по складу".encode("cp1251"))
    assert not _is_txt_payload(b"\x00\x10\x00\x08")


def test_rtf_signature_validation() -> None:
    assert _is_rtf_payload(b"{\\rtf1\\ansi This is rtf}")
    assert not _is_rtf_payload(b"{\\notrtf content}")


def test_require_service_token_rejects_invalid_token() -> None:
    original = settings.SERVICE_AUTH_TOKEN
    settings.SERVICE_AUTH_TOKEN = "secret-token"
    try:
        try:
            require_service_token("wrong-token")
            assert False, "Expected HTTPException for invalid token"
        except HTTPException as exc:
            assert exc.status_code == 401
    finally:
        settings.SERVICE_AUTH_TOKEN = original


def test_require_service_token_accepts_valid_token() -> None:
    original = settings.SERVICE_AUTH_TOKEN
    settings.SERVICE_AUTH_TOKEN = "secret-token"
    try:
        require_service_token("secret-token")
    finally:
        settings.SERVICE_AUTH_TOKEN = original