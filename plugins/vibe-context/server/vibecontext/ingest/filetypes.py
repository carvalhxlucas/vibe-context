"""Which files VibeContext accepts, decided by extension and confirmed by content."""

from dataclasses import dataclass

from vibecontext.ingest.errors import IngestError

DOCUMENT_EXTENSIONS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".rst": "text",
}

# Extension to tree-sitter language name.
CODE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".scala": "scala",
    ".sh": "bash", ".bash": "bash",
    ".sql": "sql",
    ".yaml": "yaml", ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
}

SUPPORTED_EXTENSIONS = sorted({*DOCUMENT_EXTENSIONS, *CODE_EXTENSIONS})

# How many leading bytes detect() needs.
HEAD_BYTES = 8192


@dataclass(frozen=True)
class FileType:
    kind: str
    extension: str
    language: str | None = None


def extension_of(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot > 0 else ""


def from_filename(filename: str) -> FileType:
    """Classify by extension alone, before reading the upload."""
    extension = extension_of(filename)
    if extension in DOCUMENT_EXTENSIONS:
        return FileType(DOCUMENT_EXTENSIONS[extension], extension)
    if extension in CODE_EXTENSIONS:
        return FileType("code", extension, CODE_EXTENSIONS[extension])
    shown = extension or "(no extension)"
    raise IngestError(f"Unsupported file type {shown}. Supported: {', '.join(SUPPORTED_EXTENSIONS)}")


def confirm_content(file_type: FileType, head: bytes) -> None:
    """Reject files whose content does not match their extension, such as an executable renamed to .pdf."""
    if file_type.kind == "pdf":
        if not head.startswith(b"%PDF-"):
            raise IngestError("File has a .pdf extension but is not a PDF.")
    elif file_type.kind == "docx":
        if not head.startswith(b"PK\x03\x04"):
            raise IngestError("File has a .docx extension but is not a Word document.")
    elif b"\x00" in head:
        raise IngestError(f"File has a {file_type.extension} extension but contains binary data.")
