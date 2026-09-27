"""Deterministic stand-ins for the embedding models, so tests need no downloads."""

import hashlib
import re

from qdrant_client import QdrantClient, models

from vibecontext.embeddings.base import Embedder
from vibecontext.ingest.errors import RetryLater
from vibecontext.ingest.indexer import Indexer
from vibecontext.rerank.base import Reranker, RerankUnavailable
from vibecontext.vectorstore.qdrant import VectorStore

DIMENSION = 16


def _hash(token: str) -> int:
    return int(hashlib.sha256(token.encode()).hexdigest()[:8], 16)


class FakeEmbedder(Embedder):
    model_id = "fake/hash-embedder"

    def __init__(self):
        self.unavailable: str | None = None
        self.calls = 0

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * DIMENSION
        for token in re.findall(r"\w+", text.lower()):
            vector[_hash(token) % DIMENSION] += 1.0
        return vector if any(vector) else [1.0] + [0.0] * (DIMENSION - 1)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.unavailable:
            raise RetryLater(self.unavailable)
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        if self.unavailable:
            raise RetryLater(self.unavailable)
        return self._vector(text)


class FakeSparse:
    def _vector(self, text: str) -> models.SparseVector:
        counts: dict[int, float] = {}
        for token in re.findall(r"\w+", text.lower()):
            index = _hash(token) % 100_000
            counts[index] = counts.get(index, 0.0) + 1.0
        return models.SparseVector(indices=list(counts), values=list(counts.values()))

    def embed_documents(self, texts: list[str]) -> list[models.SparseVector]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> models.SparseVector:
        return self._vector(text)


def fake_indexer(settings) -> Indexer:
    return Indexer(
        settings,
        embedder=FakeEmbedder(),
        sparse=FakeSparse(),
        store=VectorStore(client=QdrantClient(":memory:")),
    )


class FakeReranker(Reranker):
    """Scores by the share of query words present in the text."""

    model_id = "fake/overlap-reranker"

    def __init__(self):
        self.ready = True
        self.broken: str | None = None
        self.load_requests = 0

    def start_loading(self) -> None:
        self.load_requests += 1

    def is_ready(self) -> bool:
        return self.ready

    @property
    def unavailable_reason(self) -> str | None:
        return None if self.ready else "is still loading"

    def score(self, query: str, texts: list[str]) -> list[float]:
        if self.broken:
            raise RerankUnavailable(self.broken)
        words = set(re.findall(r"\w+", query.lower()))
        return [len(words & set(re.findall(r"\w+", t.lower()))) / max(len(words), 1) for t in texts]
