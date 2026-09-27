from abc import ABC, abstractmethod

from vibecontext.config import Settings


class RerankUnavailable(Exception):
    """Reranking failed; search falls back to the hybrid ranking."""


class Reranker(ABC):
    model_id: str

    def start_loading(self) -> None:
        """Begin loading in the background, if the reranker needs to load."""

    def is_ready(self) -> bool:
        return True

    @property
    def unavailable_reason(self) -> str | None:
        return None

    @abstractmethod
    def score(self, query: str, texts: list[str]) -> list[float]:
        """Relevance of each text to the query, from 0 to 1."""


def create_reranker(settings: Settings) -> Reranker | None:
    if settings.rerank_provider == "none":
        return None
    if settings.rerank_provider == "cohere":
        from vibecontext.rerank.cohere_reranker import CohereReranker

        return CohereReranker(settings.cohere_api_key, settings.cohere_rerank_model)
    from vibecontext.rerank.local_reranker import LocalReranker

    return LocalReranker(settings.local_rerank_model)
