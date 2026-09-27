import re
from abc import ABC, abstractmethod

from vibecontext.config import Settings


class Embedder(ABC):
    """Dense embeddings. Implementations load lazily so the backend starts fast."""

    model_id: str

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]: ...


def model_id(settings: Settings) -> str:
    if settings.embedding_provider == "openai":
        return f"openai/{settings.openai_embedding_model}"
    return f"local/{settings.local_embedding_model}"


def collection_name(settings: Settings) -> str:
    """One Qdrant collection per embedding model: vectors of different models never mix."""
    return "vibecontext__" + re.sub(r"[^a-z0-9]+", "_", model_id(settings).lower()).strip("_")


def create_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "openai":
        from vibecontext.embeddings.openai_embedder import OpenAIEmbedder

        return OpenAIEmbedder(settings.openai_api_key, settings.openai_embedding_model, settings.embedding_batch_size)
    from vibecontext.embeddings.local_embedder import LocalEmbedder

    return LocalEmbedder(settings.local_embedding_model, settings.embedding_batch_size)
