class IngestError(Exception):
    """An expected ingestion failure. The message is shown to the user as is."""


class RetryLater(Exception):
    """A failure outside the file: Qdrant down, API key missing, rate limit.

    The document goes back to the queue with this message and is retried later.
    """
