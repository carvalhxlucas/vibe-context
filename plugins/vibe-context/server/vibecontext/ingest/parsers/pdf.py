import logging
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError, PdfReadError

from vibecontext.ingest.errors import IngestError
from vibecontext.ingest.parsers.base import Block, paragraphs

log = logging.getLogger("vibecontext.ingest")


def parse_pdf(path: Path) -> list[Block]:
    try:
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise IngestError("PDF is password-protected.")
        pages = reader.pages
        page_count = len(pages)
    except (PdfReadError, FileNotDecryptedError) as error:
        raise IngestError(f"PDF is corrupted or unreadable: {error}") from error

    blocks: list[Block] = []
    for number in range(page_count):
        try:
            text = pages[number].extract_text() or ""
        except Exception as error:
            # One bad page should not lose the rest of the document.
            log.warning("%s: skipping page %d: %s", path.name, number + 1, error)
            continue
        blocks.extend(Block(p, page=number + 1) for p in paragraphs(text))

    if not blocks:
        raise IngestError("No text could be extracted from this PDF. Scanned PDFs need OCR, which is not supported.")
    return blocks
