from dataclasses import dataclass

from vibecontext.ingest.errors import IngestError


@dataclass
class Block:
    """A paragraph-sized piece of a document, the unit the text chunker packs."""

    text: str
    page: int | None = None
    heading: str | None = None
    # True for a heading: the chunker starts a new chunk here instead of packing across sections.
    starts_section: bool = False


class HeadingTrail:
    """The chain of headings above the current position, such as "Spec > Scope > Out of scope"."""

    def __init__(self) -> None:
        self._stack: list[tuple[int, str]] = []

    def enter(self, level: int, title: str) -> None:
        while self._stack and self._stack[-1][0] >= level:
            self._stack.pop()
        self._stack.append((level, title))

    @property
    def path(self) -> str | None:
        return " > ".join(title for _, title in self._stack) or None


def decode_text(data: bytes) -> str:
    if b"\x00" in data:
        raise IngestError("File contains binary data and cannot be read as text.")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.replace("\r\n", "\n").split("\n\n") if p.strip()]
