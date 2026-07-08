"""GATE A (SC-DEPS): pypdf parity for the vendored PageIndex PDF utils.

The PyPDF2 -> pypdf swap must be a drop-in: pypdf's PdfReader / .pages /
page.extract_text() / .metadata(.title) behave identically for the utility
functions in ``pageindex/utils.py``. We prove that by (a) asserting the source
no longer imports PyPDF2 and the default parser string is "pypdf", and (b)
round-tripping a hand-crafted in-memory PDF through the real util functions.

We synthesize the PDF by hand (no data/ samples — they are gitignored, and
reportlab is not installed) so the test is fully self-contained.
"""

import inspect
import io
from pathlib import Path

import pytest

from pageindex import utils

UTILS_SOURCE = Path(utils.__file__).read_text(encoding="utf-8")


def _build_pdf() -> bytes:
    """Return bytes of a minimal, valid 2-page PDF with real text + a /Title."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 7 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>",
    ]
    stream_one = b"BT /F1 24 Tf 72 700 Td (Hello World Page One) Tj ET"
    stream_two = b"BT /F1 24 Tf 72 700 Td (Second Page Content) Tj ET"
    objs.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream_one), stream_one))
    objs.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream_two), stream_two))
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objs.append(b"<< /Title (Sample SOW Title) >>")

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n%s\nendobj\n" % (i, body))
    xref_pos = out.tell()
    count = len(objs) + 1
    out.write(b"xref\n0 %d\n" % count)
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R /Info 8 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (count, xref_pos)
    )
    return out.getvalue()


@pytest.fixture()
def pdf_stream() -> io.BytesIO:
    return io.BytesIO(_build_pdf())


def test_source_uses_pypdf_not_pypdf2():
    """The swap removed every PyPDF2 reference and switched the default parser."""
    assert "PyPDF2" not in UTILS_SOURCE, "pageindex/utils.py still references PyPDF2"
    assert "import pypdf" in UTILS_SOURCE
    default = inspect.signature(utils.get_page_tokens).parameters["pdf_parser"].default
    assert default == "pypdf"


def test_extract_text_from_pdf(pdf_stream):
    text = utils.extract_text_from_pdf(pdf_stream)
    assert "Hello World Page One" in text
    assert "Second Page Content" in text


def test_get_pdf_title(pdf_stream):
    assert utils.get_pdf_title(pdf_stream) == "Sample SOW Title"


def test_get_number_of_pages(pdf_stream):
    assert utils.get_number_of_pages(pdf_stream) == 2


def test_get_text_of_pages(pdf_stream):
    tagged = utils.get_text_of_pages(pdf_stream, 1, 2, tag=True)
    assert "Hello World Page One" in tagged
    assert "Second Page Content" in tagged
    assert "<start_index_1>" in tagged


def test_get_page_tokens_default_parser(pdf_stream):
    pages = utils.get_page_tokens(pdf_stream, model="gpt-4o-mini")
    assert len(pages) == 2
    for page_text, token_length in pages:
        assert isinstance(page_text, str)
        assert isinstance(token_length, int)
        assert token_length > 0


def test_get_pdf_name_reads_title_for_stream(pdf_stream):
    assert utils.get_pdf_name(pdf_stream) == "Sample SOW Title"
