"""Background thread that drains the ingestion queue (documents in 'pending').

The queue lives in SQLite, so uploads survive a backend restart: on start, the
worker requeues whatever a previous process left in 'processing'.
"""

import fcntl
import logging
import threading
import time

from vibecontext.config import Paths, Settings
from vibecontext.db import documents, store
from vibecontext.ingest import storage
from vibecontext.ingest.errors import IngestError, RetryLater
from vibecontext.ingest.indexer import Indexer
from vibecontext.ingest.pipeline import build_chunks

log = logging.getLogger("vibecontext.ingest")

IDLE_POLL_SECONDS = 5
MIN_BACKOFF_SECONDS = 5
MAX_BACKOFF_SECONDS = 300


class IngestWorker:
    def __init__(self, paths: Paths, settings: Settings, indexer: Indexer):
        self._paths = paths
        self._settings = settings
        self._indexer = indexer
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock_file = None

    def start(self) -> None:
        # One worker per VIBECONTEXT_HOME. A second backend (say, one that lost the
        # port race) must not requeue documents the running worker is processing.
        lock_file = open(self._paths.run / "worker.lock", "w")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            raise RuntimeError("Another VibeContext backend is already running for this VIBECONTEXT_HOME")
        self._lock_file = lock_file

        conn = store.connect(self._paths.db)
        try:
            requeued = documents.reset_interrupted(conn)
            outdated = documents.requeue_outdated(conn, self._indexer.collection)
        finally:
            conn.close()
        if requeued:
            log.info("Requeued %d document(s) interrupted by a previous shutdown", requeued)
        if outdated:
            log.info("Requeued %d document(s) indexed with another embedding model", outdated)
        self._thread = threading.Thread(target=self._loop, name="ingest-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        if self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None

    def wake(self) -> None:
        self._wake.set()

    def run_once(self) -> str:
        """Process the oldest pending document.

        Returns "idle" when the queue is empty, "done" when the document was indexed or
        failed, and "deferred" when an environment problem sent it back to the queue.
        """
        conn = store.connect(self._paths.db)
        try:
            job = documents.claim_next(conn)
            if job is None:
                return "idle"
            return self._process(conn, job)
        finally:
            conn.close()

    def _process(self, conn, job: dict) -> str:
        doc_id, filename = job["id"], job["filename"]
        started = time.monotonic()
        try:
            path = storage.file_path(self._paths.files, job["stored_path"])
            if not path.exists():
                raise IngestError("The stored file is missing. Upload it again.")
            chunks = build_chunks(path, job["kind"], job["language"], self._settings)
            self._indexer.index(job, chunks)
        except RetryLater as error:
            log.warning("WAITING %s (%s): %s", filename, doc_id, error)
            documents.defer(conn, doc_id, f"Waiting to retry: {error}")
            return "deferred"
        except IngestError as error:
            log.warning("FAILED %s (%s): %s", filename, doc_id, error)
            documents.fail(conn, doc_id, str(error))
            return "done"
        except Exception:
            log.exception("FAILED %s (%s): unexpected error", filename, doc_id)
            documents.fail(conn, doc_id, "Unexpected error while processing the file. Details in logs/ingest.log.")
            return "done"

        collection = self._indexer.collection
        if not documents.complete(conn, doc_id, chunks, collection):
            # Deleted or requeued meanwhile: the points just written belong to nothing.
            self._indexer.remove(doc_id, collection)
            log.info("DISCARDED %s (%s): deleted or requeued while processing", filename, doc_id)
            return "done"
        if job["index_key"] and job["index_key"] != collection:
            # Reindexed after an embedding model change: drop the vectors in the old collection.
            self._indexer.remove(doc_id, job["index_key"])
        log.info("INDEXED %s (%s): %d chunks in %.1fs", filename, doc_id, len(chunks), time.monotonic() - started)
        return "done"

    def _loop(self) -> None:
        backoff = 0.0
        while not self._stopping.is_set():
            try:
                outcome = self.run_once()
            except Exception:
                log.exception("Ingestion worker error")
                outcome = "idle"
            if outcome == "deferred":
                # Qdrant down or a missing key affects every document; wait before the next try.
                backoff = min(max(backoff * 2, MIN_BACKOFF_SECONDS), MAX_BACKOFF_SECONDS)
                self._stopping.wait(backoff)
                continue
            if outcome == "done":
                backoff = 0.0
                continue
            self._wake.wait(timeout=IDLE_POLL_SECONDS)
            self._wake.clear()
