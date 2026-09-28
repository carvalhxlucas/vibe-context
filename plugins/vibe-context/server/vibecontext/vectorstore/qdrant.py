"""Qdrant access. Every call that can fail because Qdrant is down raises RetryLater."""

import functools
import warnings

import httpx
from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from vibecontext.config import Settings
from vibecontext.db.documents import Chunk, describe_targets
from vibecontext.ingest.errors import RetryLater

DENSE = "dense"
SPARSE = "bm25"
PAYLOAD_INDEXES = {
    "document_id": models.PayloadSchemaType.KEYWORD,
    "global": models.PayloadSchemaType.BOOL,
    "sessions": models.PayloadSchemaType.KEYWORD,
    "kind": models.PayloadSchemaType.KEYWORD,
}
UPSERT_BATCH = 256

# The local Qdrant is plain HTTP on 127.0.0.1; the client warns about sending the key without TLS.
warnings.filterwarnings("ignore", message="Api key is used with an insecure connection")


def _qdrant_call(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except (ResponseHandlingException, httpx.HTTPError, ConnectionError) as error:
            raise RetryLater(f"Qdrant is unreachable at {self.url}: {error}") from error
        except UnexpectedResponse as error:
            if error.status_code in (401, 403):
                raise RetryLater("Qdrant rejected QDRANT_API_KEY. Check ~/.vibecontext/.env.") from error
            if error.status_code >= 500:
                raise RetryLater(f"Qdrant error {error.status_code}: {error}") from error
            raise

    return wrapper


class VectorStore:
    def __init__(self, settings: Settings | None = None, client: QdrantClient | None = None):
        self.url = settings.qdrant_url if settings else ":memory:"
        self._client = client or QdrantClient(
            url=settings.qdrant_url, api_key=settings.qdrant_api_key or None, timeout=30
        )
        self._ready: set[str] = set()  # collections checked for dimension and indexes
        self._indexed: set[str] = set()  # collections whose payload indexes are known to exist

    @_qdrant_call
    def ensure_collection(self, collection: str, dimension: int) -> None:
        if collection in self._ready:
            return
        if self._client.collection_exists(collection):
            size = self._client.get_collection(collection).config.params.vectors[DENSE].size
            if size != dimension:
                raise RuntimeError(f"Collection {collection} holds {size}-dimension vectors, got {dimension}")
        else:
            self._client.create_collection(
                collection,
                vectors_config={DENSE: models.VectorParams(size=dimension, distance=models.Distance.COSINE)},
                sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
            )
        self._create_payload_indexes(collection)
        self._ready.add(collection)

    def _create_payload_indexes(self, collection: str) -> None:
        # Also run on existing collections: ones created before attachments lack "global" and "sessions".
        if collection in self._indexed:
            return
        existing = self._client.get_collection(collection).payload_schema or {}
        for field, schema in PAYLOAD_INDEXES.items():
            if field not in existing:
                self._client.create_payload_index(collection, field, schema)
        self._indexed.add(collection)

    @_qdrant_call
    def replace_document(
        self,
        collection: str,
        document: dict,
        chunks: list[Chunk],
        dense: list[list[float]],
        sparse: list[models.SparseVector],
        targets: list[str],
    ) -> None:
        self.ensure_collection(collection, len(dense[0]))
        self.delete_document(collection, document["id"])
        points = [
            models.PointStruct(
                id=chunk.id,
                vector={DENSE: dense_vector, SPARSE: sparse_vector},
                payload={
                    "document_id": document["id"],
                    **describe_targets(targets),
                    "filename": document["filename"],
                    "kind": document["kind"],
                    "language": document["language"],
                    "ordinal": ordinal,
                    "text": chunk.text,
                    "meta": chunk.meta,
                },
            )
            for ordinal, (chunk, dense_vector, sparse_vector) in enumerate(zip(chunks, dense, sparse))
        ]
        for start in range(0, len(points), UPSERT_BATCH):
            self._client.upsert(collection, points[start:start + UPSERT_BATCH], wait=True)

    @_qdrant_call
    def set_targets(self, collection: str, doc_id: str, targets: list[str]) -> None:
        """Rewrite who can see a document's points. No re-embedding: payload only."""
        if collection not in self._ready and not self._client.collection_exists(collection):
            return
        self._create_payload_indexes(collection)
        self._client.set_payload(
            collection,
            payload=describe_targets(targets),
            points=models.Filter(
                must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=doc_id))]
            ),
            wait=True,
        )

    @_qdrant_call
    def delete_document(self, collection: str, doc_id: str) -> None:
        if collection not in self._ready and not self._client.collection_exists(collection):
            return
        self._client.delete(
            collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=doc_id))]
                )
            ),
            wait=True,
        )

    @_qdrant_call
    def hybrid_query(
        self,
        collection: str,
        dense: list[float],
        sparse: models.SparseVector,
        query_filter: models.Filter,
        limit: int,
    ) -> list[models.ScoredPoint]:
        """Dense and BM25 candidates fused with Reciprocal Rank Fusion, inside Qdrant."""
        if not self._client.collection_exists(collection):
            return []
        return self._client.query_points(
            collection,
            prefetch=[
                models.Prefetch(query=dense, using=DENSE, filter=query_filter, limit=limit),
                models.Prefetch(query=sparse, using=SPARSE, filter=query_filter, limit=limit),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
            with_payload=True,
        ).points

    @_qdrant_call
    def count(self, collection: str, doc_id: str | None = None) -> int:
        if not self._client.collection_exists(collection):
            return 0
        condition = None
        if doc_id is not None:
            condition = models.Filter(
                must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=doc_id))]
            )
        return self._client.count(collection, count_filter=condition, exact=True).count

    @property
    def client(self) -> QdrantClient:
        return self._client
