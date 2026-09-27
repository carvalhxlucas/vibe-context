import cohere

from vibecontext.rerank.base import Reranker, RerankUnavailable


class CohereReranker(Reranker):
    def __init__(self, api_key: str, model: str):
        self.model_id = f"cohere/{model}"
        self._api_key = api_key
        self._model = model
        self._client: cohere.ClientV2 | None = None

    def is_ready(self) -> bool:
        return bool(self._api_key)

    @property
    def unavailable_reason(self) -> str | None:
        return None if self._api_key else "needs COHERE_API_KEY in ~/.vibecontext/.env"

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not self._api_key:
            raise RerankUnavailable(f"Cohere reranker {self.unavailable_reason}")
        if self._client is None:
            self._client = cohere.ClientV2(api_key=self._api_key)
        try:
            response = self._client.rerank(model=self._model, query=query, documents=texts, top_n=len(texts))
        except Exception as error:
            raise RerankUnavailable(f"Cohere rerank failed: {error}") from error
        scores = [0.0] * len(texts)
        for result in response.results:
            scores[result.index] = float(result.relevance_score)
        return scores
