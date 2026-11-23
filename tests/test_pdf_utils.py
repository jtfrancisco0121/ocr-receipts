from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter

from resibo.models import ErrorCode, ResiboError
from resibo.pdf_utils import chunk_ranges, count_pages, extract_pages, prepare_pdf


def make_pdf(pages: int = 1, encrypt: tuple[str, str, str | None] | None = None) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    if encrypt:
        user, owner, algorithm = encrypt
        if algorithm:
            writer.encrypt(user, owner, algorithm=algorithm)
        else:
            writer.encrypt(user, owner)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def code_of(excinfo) -> ErrorCode:
    return excinfo.value.code


def test_not_a_pdf():
    with pytest.raises(ResiboError) as exc:
        prepare_pdf(b"hello world", 10)
    assert code_of(exc) == ErrorCode.NOT_A_PDF


def test_unreadable():
    with pytest.raises(ResiboError) as exc:
        prepare_pdf(b"%PDF-1.7\nthis is not really a pdf", 10)
    assert code_of(exc) == ErrorCode.PDF_UNREADABLE


def test_empty_pdf():
    with pytest.raises(ResiboError) as exc:
        prepare_pdf(make_pdf(0), 10)
    assert code_of(exc) == ErrorCode.PDF_EMPTY


def test_encrypted_with_user_password():
    with pytest.raises(ResiboError) as exc:
        prepare_pdf(make_pdf(2, ("secret", "owner", None)), 10)
    assert code_of(exc) == ErrorCode.PDF_ENCRYPTED


def test_encrypted_with_user_password_aes256():
    with pytest.raises(ResiboError) as exc:
        prepare_pdf(make_pdf(2, ("secret", "owner", "AES-256")), 10)
    assert code_of(exc) == ErrorCode.PDF_ENCRYPTED


@pytest.mark.parametrize("algorithm", [None, "RC4-128", "AES-128", "AES-256"])
def test_empty_user_password_is_decrypted(algorithm):
    original = make_pdf(3, ("", "owner", algorithm))
    assert PdfReader(BytesIO(original)).is_encrypted
    prepared = prepare_pdf(original, 10)
    assert prepared.page_count == 3
    again = PdfReader(BytesIO(prepared.data))
    assert not again.is_encrypted
    assert len(again.pages) == 3  # opens without a password


def test_decrypt_failure_never_escapes_as_other_error(monkeypatch):
    original = make_pdf(1, ("", "owner", None))

    def boom(self, password):
        raise RuntimeError("no crypto backend")

    monkeypatch.setattr(PdfReader, "decrypt", boom)
    with pytest.raises(ResiboError) as exc:
        prepare_pdf(original, 10)
    assert code_of(exc) == ErrorCode.PDF_ENCRYPTED


def test_page_count_and_plain_pdf_is_passed_through():
    data = make_pdf(4)
    prepared = prepare_pdf(data, 10)
    assert prepared.page_count == 4 and prepared.data == data
    assert count_pages(data) == 4


def test_too_many_pages_messages():
    data = make_pdf(16)
    with pytest.raises(ResiboError) as exc:
        prepare_pdf(data, 15, public=True)
    assert code_of(exc) == ErrorCode.TOO_MANY_PAGES
    assert exc.value.message == "This demo accepts up to 15 pages. Your file has 16."
    assert prepare_pdf(data, 30).page_count == 16


def test_chunk_ranges():
    assert chunk_ranges(23, 10) == [(1, 10), (11, 20), (21, 23)]
    assert chunk_ranges(10, 10) == [(1, 10)]
    assert chunk_ranges(1, 10) == [(1, 1)]


def test_extract_pages():
    writer = PdfWriter()
    for width in (101, 102, 103, 104):
        writer.add_blank_page(width=width, height=100)
    out = BytesIO()
    writer.write(out)
    part = extract_pages(out.getvalue(), 2, 3)
    pages = PdfReader(BytesIO(part)).pages
    assert [round(float(p.mediabox.width)) for p in pages] == [102, 103]
