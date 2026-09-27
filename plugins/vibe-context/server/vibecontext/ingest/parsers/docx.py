import zipfile
from pathlib import Path

import docx
from docx.opc.exceptions import PackageNotFoundError
from docx.table import Table

from vibecontext.ingest.errors import IngestError
from vibecontext.ingest.parsers.base import Block, HeadingTrail

# A DOCX is a zip file; refuse archives that would expand past this (zip bombs).
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024

HEADING_PREFIXES = ("heading", "título", "titulo", "title")


def _check_archive(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            expanded = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile as error:
        raise IngestError("DOCX is corrupted or not a Word document.") from error
    if "word/document.xml" not in names:
        raise IngestError("File is a zip archive but not a Word document.")
    if expanded > MAX_UNCOMPRESSED_BYTES:
        raise IngestError("DOCX expands to more than 200 MB and was rejected.")


def _heading_level(style_name: str) -> int | None:
    name = style_name.lower()
    if not name.startswith(HEADING_PREFIXES):
        return None
    digits = "".join(c for c in name if c.isdigit())
    return int(digits) if digits else 1


def parse_docx(path: Path) -> list[Block]:
    _check_archive(path)
    try:
        document = docx.Document(str(path))
    except (PackageNotFoundError, KeyError, ValueError) as error:
        raise IngestError(f"DOCX is corrupted or unreadable: {error}") from error

    blocks: list[Block] = []
    trail = HeadingTrail()
    for item in document.iter_inner_content():
        if isinstance(item, Table):
            rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in item.rows]
            text = "\n".join(row for row in rows if row.strip(" |"))
            if text:
                blocks.append(Block(text, heading=trail.path))
            continue
        text = item.text.strip()
        if not text:
            continue
        level = _heading_level(item.style.name if item.style is not None else "")
        if level is not None:
            trail.enter(level, text)
            blocks.append(Block(text, heading=trail.path, starts_section=True))
        else:
            blocks.append(Block(text, heading=trail.path))

    if not blocks:
        raise IngestError("The Word document has no text.")
    return blocks
