"""PDF checks, chunk ranges and page extraction (SPEC.md sections 8.1 and 8.2).

Raises only ``ResiboError`` (AD-6).
"""

from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader, PdfWriter

from resibo.models import ErrorCode, ResiboError


@dataclass(frozen=True)
class PreparedPdf:
    data: bytes  # bytes to send to the model: decrypted and re-serialized when needed
    page_count: int


def _open(data: bytes) -> PdfReader:
    try:
        return PdfReader(BytesIO(data))
    except Exception as exc:
        raise ResiboError(
            ErrorCode.PDF_UNREADABLE, "This PDF could not be read. Is the file damaged?"
        ) from exc


def _encrypted_error() -> ResiboError:
    return ResiboError(
        ErrorCode.PDF_ENCRYPTED,
        "This PDF is password protected. Remove the password and try again.",
    )


def _decrypted_bytes(reader: PdfReader) -> bytes:
    """Decrypt with the empty password and re-serialize; any failure is PDF_ENCRYPTED."""
    try:
        if not reader.decrypt(""):
            raise _encrypted_error()
        writer = PdfWriter(clone_from=reader)
        out = BytesIO()
        writer.write(out)
        return out.getvalue()
    except ResiboError:
        raise
    except Exception as exc:  # DependencyError, FileNotDecryptedError, broken crypto data
        raise _encrypted_error() from exc


def prepare_pdf(data: bytes, max_pages: int, public: bool = False) -> PreparedPdf:
    """Run the section 8.1 checks (except the upload size, done by the HTTP shell)."""
    if not data.startswith(b"%PDF-"):
        raise ResiboError(ErrorCode.NOT_A_PDF, "This file is not a PDF.")
    reader = _open(data)
    if reader.is_encrypted:
        data = _decrypted_bytes(reader)
        reader = _open(data)
    try:
        page_count = len(reader.pages)
    except Exception as exc:
        raise ResiboError(
            ErrorCode.PDF_UNREADABLE, "This PDF could not be read. Is the file damaged?"
        ) from exc
    if page_count == 0:
        raise ResiboError(ErrorCode.PDF_EMPTY, "This PDF has no pages.")
    if page_count > max_pages:
        who = "This demo" if public else "This service"
        raise ResiboError(
            ErrorCode.TOO_MANY_PAGES,
            f"{who} accepts up to {max_pages} pages. Your file has {page_count}.",
        )
    return PreparedPdf(data=data, page_count=page_count)


def count_pages(data: bytes) -> int:
    return len(_open(data).pages)


def chunk_ranges(page_count: int, per_chunk: int) -> list[tuple[int, int]]:
    """1-based inclusive page ranges, ``per_chunk`` pages each."""
    if per_chunk < 1:
        raise ValueError("per_chunk must be at least 1")
    return [
        (start, min(start + per_chunk - 1, page_count))
        for start in range(1, page_count + 1, per_chunk)
    ]


def extract_pages(data: bytes, first_page: int, last_page: int) -> bytes:
    """A new PDF holding pages ``first_page``..``last_page`` (1-based, inclusive)."""
    reader = _open(data)
    writer = PdfWriter()
    try:
        for index in range(first_page - 1, last_page):
            writer.add_page(reader.pages[index])
        out = BytesIO()
        writer.write(out)
    except Exception as exc:
        raise ResiboError(
            ErrorCode.PDF_UNREADABLE, "This PDF could not be split into pages."
        ) from exc
    return out.getvalue()
