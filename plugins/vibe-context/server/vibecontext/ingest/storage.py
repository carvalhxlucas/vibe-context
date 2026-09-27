"""Where uploaded files live on disk.

The client's file name never becomes part of a path. Files are stored as
"<document id><extension>", with the extension taken from a fixed allowlist.
"""

import hashlib
import re
import shutil
from pathlib import Path
from typing import BinaryIO

from vibecontext.ingest.errors import IngestError
from vibecontext.ingest.filetypes import HEAD_BYTES

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class UploadTooLarge(Exception):
    pass


def display_name(raw: str | None) -> str:
    """Reduce a client-supplied file name to a harmless label."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _CONTROL_CHARS.sub("", name).strip()[:255]
    if not name or name in (".", ".."):
        raise IngestError("Upload has no usable file name.")
    return name


def file_path(files_dir: Path, stored_path: str) -> Path:
    path = (files_dir / stored_path).resolve()
    if path.parent != files_dir.resolve():
        raise ValueError(f"Stored path escapes the files directory: {stored_path!r}")
    return path


def save(source: BinaryIO, destination: Path, max_bytes: int) -> tuple[int, str, bytes]:
    """Copy an upload to disk, enforcing the size limit while streaming.

    Returns (size in bytes, sha256 hex digest, leading bytes for content checks).
    """
    partial = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    size, head = 0, b""
    try:
        with open(partial, "xb") as out:
            while block := source.read(1024 * 1024):
                size += len(block)
                if size > max_bytes:
                    raise UploadTooLarge
                if len(head) < HEAD_BYTES:
                    head += block[: HEAD_BYTES - len(head)]
                digest.update(block)
                out.write(block)
        shutil.move(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
    return size, digest.hexdigest(), head
