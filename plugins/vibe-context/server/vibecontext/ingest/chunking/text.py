"""Packs paragraph blocks into chunks of up to max_tokens, with overlap between neighbors."""

import re
from dataclasses import dataclass

from vibecontext.db.documents import Chunk
from vibecontext.ingest.chunking.tokens import count_tokens
from vibecontext.ingest.parsers.base import Block

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")

# A heading starts a new chunk only when the current one already has this share of
# max_tokens; otherwise a document full of short sections turns into tiny chunks.
SECTION_BREAK_MIN_SHARE = 0.25


@dataclass
class _Unit:
    text: str
    tokens: int
    block: Block
    block_index: int
    first_of_block: bool


def _hard_split(text: str, max_tokens: int) -> list[str]:
    """Split a single oversized word (a URL, a base64 blob) into equal character slices."""
    parts = -(-count_tokens(text) // max_tokens)
    size = -(-len(text) // parts)
    return [text[i:i + size] for i in range(0, len(text), size)]


def _split_long(text: str, max_tokens: int) -> list[str]:
    """Split a paragraph over max_tokens into sentences, then into word windows."""
    pieces: list[str] = []
    for sentence in _SENTENCE_END.split(text):
        if count_tokens(sentence) <= max_tokens:
            pieces.append(sentence)
            continue
        window: list[str] = []
        window_tokens = 0
        for word in sentence.split():
            word_tokens = count_tokens(" " + word)
            if word_tokens > max_tokens:
                if window:
                    pieces.append(" ".join(window))
                    window, window_tokens = [], 0
                pieces.extend(_hard_split(word, max_tokens))
                continue
            if window and window_tokens + word_tokens > max_tokens:
                pieces.append(" ".join(window))
                window, window_tokens = [], 0
            window.append(word)
            window_tokens += word_tokens
        if window:
            pieces.append(" ".join(window))
    return pieces


def _units(blocks: list[Block], max_tokens: int) -> list[_Unit]:
    units = []
    for index, block in enumerate(blocks):
        tokens = count_tokens(block.text)
        pieces = [block.text] if tokens <= max_tokens else _split_long(block.text, max_tokens)
        for position, piece in enumerate(pieces):
            units.append(_Unit(piece, count_tokens(piece), block, index, position == 0))
    return units


def _make_chunk(units: list[_Unit]) -> Chunk:
    text = units[0].text
    for previous, unit in zip(units, units[1:]):
        # Pieces of one paragraph rejoin with a space; separate paragraphs keep a blank line.
        text += (" " if unit.block_index == previous.block_index else "\n\n") + unit.text
    meta: dict = {}
    pages = sorted({u.block.page for u in units if u.block.page is not None})
    if pages:
        meta["pages"] = pages
    heading = next((u.block.heading for u in units if u.block.heading), None)
    if heading:
        meta["heading"] = heading
    return Chunk(text=text, token_count=count_tokens(text), meta=meta)


def _overlap_tail(units: list[_Unit], overlap: int, room: int) -> list[_Unit]:
    """The trailing units of the previous chunk that fit in both the overlap and the room left."""
    budget = min(overlap, room)
    tail: list[_Unit] = []
    used = 0
    for unit in reversed(units):
        if used + unit.tokens > budget:
            break
        tail.insert(0, unit)
        used += unit.tokens
    return tail


def chunk_blocks(blocks: list[Block], max_tokens: int, overlap: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    current: list[_Unit] = []
    current_tokens = 0

    for unit in _units(blocks, max_tokens):
        section_break = (
            unit.block.starts_section
            and unit.first_of_block
            and current_tokens >= max_tokens * SECTION_BREAK_MIN_SHARE
        )
        if current and (section_break or current_tokens + unit.tokens > max_tokens):
            chunks.append(_make_chunk(current))
            # No overlap across a section break: the previous section is unrelated context.
            current = [] if section_break else _overlap_tail(current, overlap, max_tokens - unit.tokens)
            current_tokens = sum(u.tokens for u in current)
        current.append(unit)
        current_tokens += unit.tokens

    if current:
        chunks.append(_make_chunk(current))
    return chunks
