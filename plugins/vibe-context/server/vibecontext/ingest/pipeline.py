"""File on disk to chunks. Phase 3 adds embedding and the Qdrant upsert after this step."""

from pathlib import Path

from vibecontext.config import Settings
from vibecontext.db.documents import Chunk
from vibecontext.ingest.chunking.code import chunk_code
from vibecontext.ingest.chunking.text import chunk_blocks
from vibecontext.ingest.errors import IngestError
from vibecontext.ingest.parsers.base import decode_text
from vibecontext.ingest.parsers.docx import parse_docx
from vibecontext.ingest.parsers.pdf import parse_pdf
from vibecontext.ingest.parsers.text import parse_markdown, parse_text

PARSERS = {
    "pdf": parse_pdf,
    "docx": parse_docx,
    "markdown": lambda path: parse_markdown(decode_text(path.read_bytes())),
    "text": lambda path: parse_text(decode_text(path.read_bytes())),
}


def build_chunks(path: Path, kind: str, language: str | None, settings: Settings) -> list[Chunk]:
    if kind == "code":
        chunks = chunk_code(decode_text(path.read_bytes()), language, settings.code_chunk_max_tokens)
    else:
        blocks = PARSERS[kind](path)
        chunks = chunk_blocks(blocks, settings.text_chunk_tokens, settings.text_chunk_overlap)
    if not chunks:
        raise IngestError("The file has no text to index.")
    return chunks
