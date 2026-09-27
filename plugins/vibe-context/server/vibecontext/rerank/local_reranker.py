"""Cross-encoder reranking on this machine, with sentence-transformers.

The default model downloads about 2 GB on first use. It loads in a background
thread so a search never waits for the download; until it is ready, search
returns the hybrid ranking and says so.
"""

import logging
import threading

from vibecontext.rerank.base import Reranker, RerankUnavailable

log = logging.getLogger("vibecontext.search")

# Chunks are at most a few hundred tokens; the query plus a chunk fits in 512.
MAX_LENGTH = 512


class LocalReranker(Reranker):
    def __init__(self, model_name: str):
        self.model_id = f"local/{model_name}"
        self._model_name = model_name
        self._model = None
        self._error: str | None = None
        self._thread: threading.Thread | None = None
        self._state = threading.Lock()
        self._use = threading.Lock()

    def start_loading(self) -> None:
        with self._state:
            if self._model is not None or (self._thread is not None and self._thread.is_alive()):
                return
            self._error = None
            self._thread = threading.Thread(target=self._load, name="reranker-load", daemon=True)
            self._thread.start()

    def _load(self) -> None:
        try:
            from sentence_transformers import CrossEncoder

            log.info("Loading reranker %s (first run downloads it)", self._model_name)
            self._model = CrossEncoder(self._model_name, max_length=MAX_LENGTH)
            log.info("Reranker %s ready", self._model_name)
        except Exception as error:
            self._error = str(error)
            log.warning("Reranker %s failed to load: %s", self._model_name, error)

    def is_ready(self) -> bool:
        return self._model is not None

    @property
    def unavailable_reason(self) -> str | None:
        if self._model is not None:
            return None
        if self._error:
            return f"failed to load ({self._error}); it retries on the next search"
        return "is still loading (the first start downloads about 2 GB)"

    def score(self, query: str, texts: list[str]) -> list[float]:
        if self._model is None:
            raise RerankUnavailable(f"Reranker {self.unavailable_reason}")
        with self._use:
            scores = self._model.predict([(query, text) for text in texts], batch_size=16)
        return [float(s) for s in scores]
