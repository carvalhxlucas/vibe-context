"""Downloadable ingestion resources: tree-sitter grammars and the tiktoken vocabulary.

Both libraries fetch these on first use. configure() points their caches inside
VIBECONTEXT_HOME, and prefetch() fills them during `vibecontext start`, so
ingestion keeps working offline afterwards.
"""

import logging
import os

import tiktoken
import tree_sitter_language_pack as grammars
from tree_sitter_language_pack.options import PackConfig

from vibecontext.config import Paths

log = logging.getLogger("vibecontext.ingest")

TOKEN_ENCODING = "cl100k_base"

# Languages the code chunker splits by syntax. The rest of CODE_EXTENSIONS uses line windows.
STRUCTURED_LANGUAGES = [
    "python", "javascript", "typescript", "tsx", "go", "rust", "java", "kotlin",
    "ruby", "php", "c", "cpp", "csharp", "swift", "scala", "bash",
]


def configure(paths: Paths) -> None:
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(paths.cache / "tiktoken"))
    grammars.configure(PackConfig(cache_dir=str(paths.cache / "tree-sitter")))


def prefetch(paths: Paths) -> list[str]:
    """Download everything ingestion needs, except the local embedding model (about
    1 GB, loaded by the worker on first use). Returns warnings instead of raising."""
    warnings = []
    try:
        tiktoken.get_encoding(TOKEN_ENCODING)
    except Exception as error:
        warnings.append(f"tiktoken vocabulary unavailable, token counts will be estimated: {error}")
    try:
        grammars.download(STRUCTURED_LANGUAGES)
    except Exception as error:
        warnings.append(f"tree-sitter grammars unavailable, code will be split by lines: {error}")
    try:
        from fastembed import SparseTextEmbedding

        SparseTextEmbedding("Qdrant/bm25", cache_dir=str(paths.cache / "fastembed"))
    except Exception as error:
        warnings.append(f"BM25 model unavailable, indexing will wait until it downloads: {error}")
    for warning in warnings:
        log.warning(warning)
    return warnings
