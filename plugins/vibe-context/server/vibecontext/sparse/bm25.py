"""BM25 sparse vectors through fastembed. Qdrant applies the IDF part at query time
(the collection's sparse vector uses Modifier.IDF), so these carry term frequencies only."""

import threading
from pathlib import Path

from qdrant_client import models

from vibecontext.ingest.errors import RetryLater

MODEL = "Qdrant/bm25"


class Bm25Encoder:
    def __init__(self, language: str, cache_dir: Path | None = None):
        self._language = language
        self._cache_dir = cache_dir
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                from fastembed import SparseTextEmbedding

                try:
                    self._model = SparseTextEmbedding(
                        MODEL, language=self._language, cache_dir=str(self._cache_dir) if self._cache_dir else None
                    )
                except Exception as error:
                    raise RetryLater(f"Could not load the BM25 model ({self._language}): {error}") from error
        return self._model

    @staticmethod
    def _to_vector(embedding) -> models.SparseVector:
        return models.SparseVector(indices=embedding.indices.tolist(), values=embedding.values.tolist())

    def embed_documents(self, texts: list[str]) -> list[models.SparseVector]:
        return [self._to_vector(e) for e in self._load().embed(texts)]

    def embed_query(self, text: str) -> models.SparseVector:
        return self._to_vector(next(iter(self._load().query_embed(text))))
