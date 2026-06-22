"""API-key auth — Bearer middleware over the whole HTTP surface.

The agent uuid is the secret API key. Every HTTP request must carry
``Authorization: Bearer <uuid>`` EXCEPT the public routes (registration,
leaderboard, docs). On success the validated agent id + public handle are stashed
on ``request.state`` for the handlers; handlers reload the full agent in their own
request-scoped session.

WebSocket connections use a separate ASGI scope that does not pass through HTTP
middleware, so they remain public-by-design (they subscribe by public
handle/name, never the secret key).
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..db.engine import session_scope
from ..db.models import Agent

# (method, exact-path) pairs that need no API key.
_PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/agents"),  # registration
        ("GET", "/leaderboard"),
    }
)
# Path prefixes that need no API key (docs, schema, root).
_PUBLIC_PREFIXES: tuple[str, ...] = ("/docs", "/redoc", "/openapi.json")


def _is_public(method: str, path: str) -> bool:
    if method == "OPTIONS" or path == "/":
        return True
    if (method, path) in _PUBLIC_ROUTES:
        return True
    return any(path == p or path.startswith(p + "/") for p in _PUBLIC_PREFIXES)


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if _is_public(request.method, request.url.path):
            return await call_next(request)

        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return JSONResponse(
                {"detail": "missing API key (Authorization: Bearer <key>)"},
                status_code=401,
            )
        key = header[len("Bearer ") :].strip()

        async with session_scope() as session:
            agent = await session.get(Agent, key)
            if agent is None:
                return JSONResponse({"detail": "invalid API key"}, status_code=401)
            request.state.agent_id = agent.id
            request.state.agent_handle = agent.handle or agent.name

        return await call_next(request)
