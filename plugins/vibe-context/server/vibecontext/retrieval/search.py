"""Hybrid search with reranking, behind the search_context MCP tool.

1. Dense and BM25 candidates, fused with RRF inside Qdrant, filtered to documents
   attached globally or to the caller's session.
2. Candidates SQLite no longer allows are dropped: deleted documents, or detached
   ones whose Qdrant payload has not caught up (Qdrant was down when it changed).
3. A cross-encoder reranks what is left; top_k are returned.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from qdrant_client import models

from vibecontext.db import documents, store
from vibecontext.ingest.indexer import Indexer
from vibecontext.rerank.base import Reranker, RerankUnavailable

log = logging.getLogger("vibecontext.search")


@dataclass
class SearchResponse:
    results: list[dict]
    reranked: bool
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"results": self.results, "reranked": self.reranked, "notes": self.notes}


def scope_filter(session_id: str | None) -> models.Filter:
    """Documents attached globally, plus the session's own. Other sessions' never match."""
    conditions = [models.FieldCondition(key="global", match=models.MatchValue(value=True))]
    if session_id:
        conditions.append(models.FieldCondition(key="sessions", match=models.MatchValue(value=session_id)))
    return models.Filter(should=conditions)


def _visible_chunks(db_path: Path, ids: list[str], session_id: str | None) -> dict[str, str]:
    conn = store.connect(db_path)
    try:
        return documents.visible_chunks(conn, ids, session_id)
    finally:
        conn.close()


def _result(point: models.ScoredPoint, scope: str) -> dict:
    payload = point.payload
    return {
        "chunk_id": str(point.id),
        "document_id": payload["document_id"],
        "filename": payload["filename"],
        # Why this caller sees it: attached to their session, or globally.
        "scope": scope,
        "kind": payload["kind"],
        "location": payload.get("meta") or {},
        "score": round(float(point.score), 4),
        "text": payload["text"],
    }


def _unique_texts(results: list[dict]) -> list[dict]:
    """Keep the best-ranked copy of each text. Two library files can share a passage, a
    template paragraph for instance, and Claude only needs it once."""
    seen: set[str] = set()
    unique = []
    for result in results:
        if result["text"] not in seen:
            seen.add(result["text"])
            unique.append(result)
    return unique


class Searcher:
    def __init__(self, indexer: Indexer, reranker: Reranker | None, db_path: Path, candidates: int):
        self._indexer = indexer
        self._reranker = reranker
        self._db_path = db_path
        self._candidates = candidates

    @property
    def reranker_id(self) -> str | None:
        return self._reranker.model_id if self._reranker else None

    @property
    def reranker_ready(self) -> bool:
        return self._reranker is not None and self._reranker.is_ready()

    def warm_up(self) -> None:
        """Load models ahead of the first search. Runs in a background thread."""
        if self._reranker is not None:
            self._reranker.start_loading()
        try:
            self._indexer.embedder.embed_query("warm up")
            self._indexer.sparse.embed_query("warm up")
        except Exception as error:
            # Qdrant down or a missing key: the first search reports it properly.
            log.info("Search warm-up skipped: %s", error)

    def search(self, query: str, session_id: str | None = None, top_k: int = 5) -> SearchResponse:
        notes: list[str] = []
        dense = self._indexer.embedder.embed_query(query)
        sparse = self._indexer.sparse.embed_query(query)
        points = self._indexer.store.hybrid_query(
            self._indexer.collection, dense, sparse, scope_filter(session_id), self._candidates
        )
        visible = _visible_chunks(self._db_path, [str(p.id) for p in points], session_id) if points else {}
        if len(visible) < len(points):
            log.info("Dropped %d result(s) deleted or detached since indexing", len(points) - len(visible))
        points = [p for p in points if str(p.id) in visible]
        if not points:
            return SearchResponse([], False, ["No indexed document matched. Attach files with /vibe-context:add."])

        results = [_result(p, visible[str(p.id)]) for p in points]
        reranked = False
        if self._reranker is not None:
            if self._reranker.is_ready():
                try:
                    # Raw text, unlike the embeddings: measured with bge-reranker-v2-m3, the
                    # "file > heading" prefix cut a relevant chunk's score from 0.15 to 0.04.
                    texts = [r["text"] for r in results]
                    for result, score in zip(results, self._reranker.score(query, texts)):
                        result["score"] = round(score, 4)
                    results.sort(key=lambda r: r["score"], reverse=True)
                    reranked = True
                except RerankUnavailable as error:
                    log.warning("%s", error)
                    notes.append(f"{error}. Results use the hybrid ranking only.")
            else:
                self._reranker.start_loading()
                notes.append(f"Reranker {self._reranker.unavailable_reason}. Results use the hybrid ranking only.")
        return SearchResponse(_unique_texts(results)[:top_k], reranked, notes)
