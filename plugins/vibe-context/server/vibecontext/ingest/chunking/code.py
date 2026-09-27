"""Splits source code along its syntax tree, falling back to line windows.

Top-level nodes (functions, classes, statements) become spans that cover every
line, so comments and decorators stay with the definition below them. A node
larger than max_tokens is split through its children (a class into methods, a
function into statements). Adjacent small spans are then packed together.
"""

import logging
from dataclasses import dataclass, field
from functools import lru_cache

import tree_sitter_language_pack as grammars

from vibecontext.db.documents import Chunk
from vibecontext.ingest.chunking.tokens import count_tokens
from vibecontext.ingest.resources import STRUCTURED_LANGUAGES

log = logging.getLogger("vibecontext.ingest")


@dataclass
class _Span:
    start: int  # first line, 0-based, inclusive
    end: int  # last line, inclusive
    tokens: int
    symbols: list[str] = field(default_factory=list)


@lru_cache(maxsize=None)
def _parser(language: str):
    if language not in STRUCTURED_LANGUAGES:
        return None
    try:
        return grammars.get_parser(language)
    except Exception as error:
        log.warning("No tree-sitter grammar for %s, splitting by lines: %s", language, error)
        return None


def _last_row(node) -> int:
    end = node.end_point
    # A node that ends at column 0 stops at the end of the previous line.
    return end.row - 1 if end.column == 0 and end.row > node.start_point.row else end.row


_DEFINITION_HINTS = ("definition", "declaration", "function", "method", "class", "struct", "interface",
                     "trait", "impl", "enum", "module")


def _name(node) -> str | None:
    # Imports and other statements can have a "name" field too; only definitions are symbols.
    if "import" in node.type or not any(hint in node.type for hint in _DEFINITION_HINTS):
        return None
    for candidate in (node, node.child_by_field_name("definition"), node.child_by_field_name("declaration")):
        if candidate is None:
            continue
        name = candidate.child_by_field_name("name")
        if name is not None and name.text:
            return name.text.decode("utf-8", "replace")
    return None


def _children(node) -> list:
    body = node.child_by_field_name("body")
    if body is None:
        inner = node.child_by_field_name("definition") or node.child_by_field_name("declaration")
        if inner is not None:
            return [inner]
    return (body.named_children if body is not None else []) or node.named_children


def _text(lines: list[str], start: int, end: int) -> str:
    return "".join(lines[start:end + 1])


def _line_windows(lines: list[str], start: int, end: int, max_tokens: int, symbols: list[str]) -> list[_Span]:
    spans: list[_Span] = []
    window_start, window_tokens = start, 0
    for row in range(start, end + 1):
        line_tokens = count_tokens(lines[row])
        if row > window_start and window_tokens + line_tokens > max_tokens:
            spans.append(_Span(window_start, row - 1, window_tokens, list(symbols)))
            window_start, window_tokens = row, 0
        window_tokens += line_tokens
    spans.append(_Span(window_start, end, window_tokens, list(symbols)))
    return spans


def _split(nodes: list, first: int, last: int, lines: list[str], max_tokens: int, parent: str | None) -> list[_Span]:
    if not nodes:
        return _line_windows(lines, first, last, max_tokens, [parent] if parent else [])
    spans: list[_Span] = []
    cursor = first
    for index, node in enumerate(nodes):
        end = last if index == len(nodes) - 1 else min(_last_row(node), last)
        if end < cursor:
            continue  # shares a line with the previous node
        name = _name(node)
        symbol = f"{parent}.{name}" if parent and name else (name or parent)
        tokens = count_tokens(_text(lines, cursor, end))
        if tokens <= max_tokens:
            spans.append(_Span(cursor, end, tokens, [symbol] if symbol else []))
        else:
            spans.extend(_split(_children(node), cursor, end, lines, max_tokens, symbol))
        cursor = end + 1
    return spans


def _pack(spans: list[_Span], max_tokens: int) -> list[_Span]:
    packed: list[_Span] = []
    for span in spans:
        if packed and packed[-1].tokens + span.tokens <= max_tokens:
            last = packed[-1]
            last.end = span.end
            last.tokens += span.tokens
            last.symbols += [s for s in span.symbols if s not in last.symbols]
        else:
            packed.append(_Span(span.start, span.end, span.tokens, list(span.symbols)))
    return packed


def _hard_split(text: str, tokens: int, max_tokens: int) -> list[str]:
    # A single enormous line, as in minified code: cut it into equal character slices.
    parts = -(-tokens // max_tokens)
    size = -(-len(text) // parts)
    return [text[i:i + size] for i in range(0, len(text), size)]


def chunk_code(source: str, language: str | None, max_tokens: int) -> list[Chunk]:
    lines = source.splitlines(keepends=True)
    if not source.strip():
        return []
    last = len(lines) - 1
    parser = _parser(language) if language else None
    if parser is None:
        spans = _line_windows(lines, 0, last, max_tokens, [])
    else:
        tree = parser.parse(source.encode("utf-8"))
        spans = _split(tree.root_node.named_children, 0, last, lines, max_tokens, None)

    chunks: list[Chunk] = []
    for span in _pack(spans, max_tokens):
        text = _text(lines, span.start, span.end)
        if not text.strip():
            continue
        meta = {"start_line": span.start + 1, "end_line": span.end + 1}
        if span.symbols:
            meta["symbols"] = span.symbols
        tokens = count_tokens(text)
        pieces = [text] if tokens <= max_tokens else _hard_split(text, tokens, max_tokens)
        chunks.extend(Chunk(text=piece, token_count=count_tokens(piece), meta=dict(meta)) for piece in pieces)
    return chunks
