"""FastAPI application factory.

Wires the HTTP routes, the API-key middleware, and a lifespan that
initialises the DB (engine + tables). Run locally:

    docker compose up -d            # Postgres on :5432
    uvicorn backend.api.app:app --reload --workers 1
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi_mcp import FastApiMCP

from .. import config
from ..db.engine import get_engine, init_engine
from ..db.models import Base
from ..orchestration import digest_scheduler
from . import routes
from .auth import APIKeyMiddleware

log = logging.getLogger(__name__)

# The backend curl surface the qupick skill drives, exposed as MCP tools. Names
# are the routes' operation_ids.
_MCP_OPERATIONS = [
    "register_agent",
    "get_agent",
    "optimize",
    "get_market",
    "get_leaderboard",
    "ping_backend",
]


def _check_market_source() -> None:
    """Announce the active data source; a missing assets-api should fail loudly."""
    from ..financial.prices.assets_api import AssetsApiSource
    from ..financial.prices.source import get_source

    source = get_source()
    log.info("market data source: %s", type(source).__name__)
    if isinstance(source, AssetsApiSource):
        try:
            source.health()
            log.info("assets-api reachable at %s", config.ASSETS_API_BASE_URL)
        except Exception as e:
            log.error(
                "assets-api unreachable at %s — solves will fail until it is up "
                "(or set MARKET_DATA_SOURCE=synthetic): %s",
                config.ASSETS_API_BASE_URL,
                e,
            )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _check_market_source()
    init_engine()
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    digest_scheduler.start(app)
    yield
    await digest_scheduler.stop(app)
    # Note: the engine is process-wide and intentionally NOT disposed here —
    # tests share it across many app instances and own its lifecycle.


def create_app() -> FastAPI:
    app = FastAPI(title="Qubitrefill", lifespan=lifespan)
    app.add_middleware(APIKeyMiddleware)
    app.include_router(routes.router)

    # Mount the MCP server in-process, after the routers so it reads the populated
    # OpenAPI schema. fastapi-mcp dispatches each tool through this app's own ASGI
    # stack, so APIKeyMiddleware re-runs on the per-agent routes; headers=["authorization"]
    # forwards the caller's Bearer key to that internal call.
    mcp = FastApiMCP(
        app,
        name="qupick",
        include_operations=_MCP_OPERATIONS,
        headers=["authorization"],
    )
    mcp.mount_http(mount_path="/mcp")
    return app


app = create_app()
