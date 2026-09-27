import re

from vibecontext.ingest.parsers.base import Block, HeadingTrail, paragraphs

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def parse_text(text: str) -> list[Block]:
    return [Block(p) for p in paragraphs(text)]


def parse_markdown(text: str) -> list[Block]:
    """Split on headings and blank lines, keeping fenced code blocks whole."""
    blocks: list[Block] = []
    trail = HeadingTrail()
    buffer: list[str] = []
    in_fence = False

    def flush() -> None:
        if buffer and "".join(buffer).strip():
            blocks.append(Block("\n".join(buffer).strip(), heading=trail.path))
        buffer.clear()

    for line in text.replace("\r\n", "\n").split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
            buffer.append(line)
            continue
        if in_fence:
            buffer.append(line)
            continue
        heading = _HEADING.match(line)
        if heading:
            flush()
            trail.enter(len(heading.group(1)), heading.group(2))
            blocks.append(Block(line.strip(), heading=trail.path, starts_section=True))
        elif not line.strip():
            flush()
        else:
            buffer.append(line)
    flush()
    return blocks
