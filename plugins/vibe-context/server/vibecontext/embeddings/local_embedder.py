"""sentence-transformers embeddings, computed on this machine.

The model downloads from Hugging Face on first use (about 1 GB for the default)
into the standard Hugging Face cache, then works offline.
"""

import logging
import threading

from vibecontext.embeddings.base import Embedder
from vibecontext.ingest.errors import RetryLater

log = logging.getLogger("vibecontext.ingest")


def prompts_for(model_name: str) -> tuple[str | None, str | None]:
    """(query prompt, document prompt). E5 models are trained with these prefixes and
    lose quality without them; other models fall back to their own configured prompts."""
    if "e5" in model_name.lower().split("/")[-1].split("-"):
        return "query: ", "passage: "
    return None, None


class LocalEmbedder(Embedder):
    def __init__(self, model_name: str, batch_size: int):
        self.model_id = f"local/{model_name}"
        self._model_name = model_name
        self._batch_size = batch_size
        self._query_prompt, self._document_prompt = prompts_for(model_name)
        self._model = None
        self._lock = threading.Lock()
        # The ingestion worker and search requests share one model instance.
        self._use = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                # Imported here: torch takes seconds to import and most processes never need it.
                from sentence_transformers import SentenceTransformer

                log.info("Loading local embedding model %s (first run downloads it)", self._model_name)
                try:
                    self._model = SentenceTransformer(self._model_name)
                except OSError as error:
                    raise RetryLater(f"Could not load local embedding model {self._model_name}: {error}") from error
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        with self._use:
            vectors = model.encode_document(
                texts, prompt=self._document_prompt, batch_size=self._batch_size, normalize_embeddings=True
            )
        return vectors.tolist()

    def embed_query(self, text: str) -> list[float]:
        model = self._load()
        with self._use:
            vector = model.encode_query([text], prompt=self._query_prompt, normalize_embeddings=True)
        return vector[0].tolist()
