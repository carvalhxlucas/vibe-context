import hmac

from starlette.requests import Request


def has_valid_token(request: Request) -> bool:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    expected = request.app.state.api_token
    return scheme.lower() == "bearer" and hmac.compare_digest(token.encode(), expected.encode())
