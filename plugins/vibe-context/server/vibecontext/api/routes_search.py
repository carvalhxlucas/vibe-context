from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from vibecontext.ingest.errors import RetryLater
from vibecontext.retrieval import injection

router = APIRouter(prefix="/api")


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None
    top_k: int = Field(5, ge=1, le=20)


class InjectRequest(BaseModel):
    prompt: str = Field(max_length=20000)
    session_id: str | None = None


@router.post("/search")
def search(body: SearchRequest, request: Request) -> dict:
    try:
        response = request.app.state.searcher.search(body.query.strip(), body.session_id, body.top_k)
    except RetryLater as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return response.as_dict()


@router.post("/inject")
def inject(body: InjectRequest, request: Request) -> dict:
    """Context block for the UserPromptSubmit hook, or null when there is nothing worth adding."""
    settings = request.app.state.settings
    if not settings.vibecontext_auto_inject:
        return {"context": None, "reason": "disabled"}
    if not injection.worth_searching(body.prompt):
        return {"context": None, "reason": "prompt too short"}
    try:
        response = request.app.state.searcher.search(
            body.prompt.strip()[:2000], body.session_id, settings.search_candidates
        )
    except RetryLater as error:
        return {"context": None, "reason": str(error)}
    context = injection.build_context(response, settings.auto_inject_min_score, settings.auto_inject_top_k)
    return {"context": context, "reason": None if context else "no result above AUTO_INJECT_MIN_SCORE"}
