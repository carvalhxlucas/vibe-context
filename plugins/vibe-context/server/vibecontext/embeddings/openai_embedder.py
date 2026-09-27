import openai

from vibecontext.embeddings.base import Embedder
from vibecontext.ingest.errors import IngestError, RetryLater

RESTART_HINT = "then restart with /vibe-context:stop and /vibe-context:start"


class OpenAIEmbedder(Embedder):
    def __init__(self, api_key: str, model: str, batch_size: int):
        self.model_id = f"openai/{model}"
        self._api_key = api_key
        self._model = model
        self._batch_size = batch_size
        self._client: openai.OpenAI | None = None

    def _get_client(self) -> openai.OpenAI:
        if not self._api_key:
            raise RetryLater(
                "OPENAI_API_KEY is not set. Set it in ~/.vibecontext/.env (or use EMBEDDING_PROVIDER=local), "
                + RESTART_HINT + "."
            )
        if self._client is None:
            self._client = openai.OpenAI(api_key=self._api_key, max_retries=5)
        return self._client

    def _create(self, texts: list[str]) -> list[list[float]]:
        client = self._get_client()
        try:
            response = client.embeddings.create(model=self._model, input=texts)
        except openai.AuthenticationError as error:
            raise RetryLater(f"OpenAI rejected OPENAI_API_KEY. Fix it in ~/.vibecontext/.env, {RESTART_HINT}.") from error
        except (openai.APIConnectionError, openai.APITimeoutError, openai.RateLimitError) as error:
            raise RetryLater(f"OpenAI embeddings unavailable: {error}") from error
        except openai.APIStatusError as error:
            if error.status_code >= 500:
                raise RetryLater(f"OpenAI embeddings unavailable: {error}") from error
            raise IngestError(f"OpenAI rejected the text: {error.message}") from error
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            vectors.extend(self._create(texts[start:start + self._batch_size]))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._create([text])[0]
