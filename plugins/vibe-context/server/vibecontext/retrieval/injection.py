"""Optional automatic injection: excerpts added to Claude's context on every prompt.

Off unless VIBECONTEXT_AUTO_INJECT=true. The UserPromptSubmit hook asks the
backend for a context block and hands it to Claude as additionalContext.
"""

from vibecontext.retrieval.search import SearchResponse

# Prompts like "ok", "sim" or "continue" carry no query worth a search.
MIN_PROMPT_CHARS = 20

HEADER = (
    "Excerpts from documents the user attached in VibeContext, retrieved automatically for this prompt. "
    "Use them only if they bear on the request. The search_context tool finds more."
)


def worth_searching(prompt: str) -> bool:
    return len(prompt.strip()) >= MIN_PROMPT_CHARS


def _location(location: dict) -> str:
    parts = []
    if location.get("heading"):
        parts.append(location["heading"])
    if location.get("pages"):
        parts.append("p. " + ", ".join(str(p) for p in location["pages"]))
    if location.get("start_line"):
        parts.append(f"lines {location['start_line']}-{location['end_line']}")
    return f" ({'; '.join(parts)})" if parts else ""


def build_context(response: SearchResponse, min_score: float, top_k: int) -> str | None:
    # Hybrid (RRF) scores only order results; they say nothing about relevance on
    # their own. Without the reranker there is no safe cut-off, so inject nothing.
    if not response.reranked:
        return None
    hits = [r for r in response.results if r["score"] >= min_score][:top_k]
    if not hits:
        return None
    blocks = [HEADER]
    for hit in hits:
        blocks.append(f"--- {hit['filename']}{_location(hit['location'])}, score {hit['score']:.2f}\n{hit['text']}")
    return "\n\n".join(blocks)
