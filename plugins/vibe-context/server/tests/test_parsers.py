import io
import zipfile

import pytest

from tests.helpers import make_docx, make_pdf
from vibecontext.ingest.errors import IngestError
from vibecontext.ingest.parsers.base import decode_text
from vibecontext.ingest.parsers.docx import parse_docx
from vibecontext.ingest.parsers.pdf import parse_pdf
from vibecontext.ingest.parsers.text import parse_markdown


def test_markdown_tracks_heading_path_and_keeps_fences_whole():
    blocks = parse_markdown(
        "# Spec\n\nIntro.\n\n## Scope\n\n```python\n# not a heading\n\nx = 1\n```\n\n# Other\n\nEnd."
    )
    assert [(b.text, b.heading, b.starts_section) for b in blocks] == [
        ("# Spec", "Spec", True),
        ("Intro.", "Spec", False),
        ("## Scope", "Spec > Scope", True),
        ("```python\n# not a heading\n\nx = 1\n```", "Spec > Scope", False),
        ("# Other", "Other", True),
        ("End.", "Other", False),
    ]


def test_decode_text_falls_back_to_cp1252():
    assert decode_text("reunião".encode("cp1252")) == "reunião"
    assert decode_text("﻿reunião".encode("utf-8")) == "reunião"


def test_pdf_blocks_carry_page_numbers(tmp_path):
    path = tmp_path / "spec.pdf"
    path.write_bytes(make_pdf(["First page text.", "Second page text."]))
    blocks = parse_pdf(path)
    assert [(b.text, b.page) for b in blocks] == [("First page text.", 1), ("Second page text.", 2)]


def test_pdf_errors_are_readable(tmp_path):
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"%PDF-1.4\nthis is not really a pdf")
    with pytest.raises(IngestError, match="corrupted or unreadable"):
        parse_pdf(corrupt)

    empty = tmp_path / "scanned.pdf"
    empty.write_bytes(make_pdf([""]))
    with pytest.raises(IngestError, match="OCR"):
        parse_pdf(empty)


def test_docx_keeps_headings_and_tables(tmp_path):
    path = tmp_path / "spec.docx"
    path.write_bytes(make_docx())
    blocks = parse_docx(path)
    assert [(b.text, b.heading) for b in blocks] == [
        ("Spec", "Spec"),
        ("The billing service charges customers monthly.", "Spec"),
        ("Scope", "Spec > Scope"),
        ("Refunds are out of scope for the first release.", "Spec > Scope"),
        ("Plan | Price\nPro | 20 USD", "Spec > Scope"),
    ]


def test_docx_rejects_zip_that_is_not_word(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "hi")
    path = tmp_path / "fake.docx"
    path.write_bytes(buffer.getvalue())
    with pytest.raises(IngestError, match="not a Word document"):
        parse_docx(path)
