"""Chunks to vectors in Qdrant: dense embeddings plus BM25 sparse vectors, same points."""

import logging

from vibecontext.config import Paths, Settings
from vibecontext.db.documents import Chunk
from vibecontext.embeddings.base import Embedder, collection_name, create_embedder
from vibecontext.ingest.errors import RetryLater
from vibecontext.sparse.bm25 import Bm25Encoder
from vibecontext.vectorstore.qdrant import VectorStore

log = logging.getLogger("vibecontext.ingest")


def context_text(filename: str, meta: dict, text: str) -> str:
    """A chunk plus where it comes from, for embedding.

    A chunk reading "Delivery moves to October" matches a question about the
    project schedule better when it also says it sits under "Decisions > Deadlines".
    """
    context = [filename]
    if meta.get("heading"):
        context.append(meta["heading"])
    elif meta.get("symbols"):
        context.append(", ".join(meta["symbols"]))
    return " > ".join(context) + "\n\n" + text


def embedding_text(document: dict, chunk: Chunk) -> str:
    return context_text(document["filename"], chunk.meta, chunk.text)


class Indexer:
    def __init__(
        self,
        settings: Settings,
        paths: Paths | None = None,
        embedder: Embedder | None = None,
        sparse: Bm25Encoder | None = None,
        store: VectorStore | None = None,
    ):
        self.collection = collection_name(settings)
        self.embedder = embedder or create_embedder(settings)
        self.sparse = sparse or Bm25Encoder(settings.bm25_language, paths.cache / "fastembed" if paths else None)
        self.store = store or VectorStore(settings)

    def index(self, document: dict, chunks: list[Chunk], targets: list[str]) -> None:
        texts = [embedding_text(document, chunk) for chunk in chunks]
        dense = self.embedder.embed_documents(texts)
        sparse = self.sparse.embed_documents(texts)
        self.store.replace_document(self.collection, document, chunks, dense, sparse, targets)

    def sync_targets(self, doc_id: str, collection: str | None, targets: list[str]) -> bool:
        """Push a document's attachments to its points. Never raises: SQLite stays the
        source of truth, search filters by it, and the next backend start resyncs."""
        try:
            self.store.set_targets(collection or self.collection, doc_id, targets)
            return True
        except RetryLater as error:
            log.warning("Could not update attachments of %s in Qdrant: %s", doc_id, error)
            return False

    def remove(self, doc_id: str, collection: str | None) -> None:
        """Delete a document's points. Never raises: callers are cleaning up."""
        try:
            self.store.delete_document(collection or self.collection, doc_id)
        except RetryLater as error:
            log.warning("Could not delete vectors of %s from %s: %s", doc_id, collection, error)
