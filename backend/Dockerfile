# syntax=docker/dockerfile:1
#
# Backend image: serves the REST API and the in-process qupick MCP server
# (uvicorn backend.api.app:app, exposing /mcp). Multi-stage uv build — the build
# toolchain stays in the builder; the runtime image carries only the venv + source.

FROM python:3.13-slim AS builder

# Pinned uv (matches the lockfile resolver); copied as a static binary.
COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Build toolchain for any sdist-only deps (numpy/scipy/dwave native bits). Kept
# out of the runtime stage. hadolint ignore=DL3008 — base-image apt pins drift.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first (this layer is cached until uv.lock/pyproject change).
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Then the project source, and install the project itself.
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.13-slim AS runtime

# Non-root runtime user owning the app tree.
RUN adduser --system --group --home /app app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    MARKET_DATA_SOURCE=synthetic \
    GUROBI_IN_RACE=0

WORKDIR /app
COPY --from=builder --chown=app:app /app /app

USER app
EXPOSE 8000

# Bind address is fixed (0.0.0.0); PORT is env-overridable so a reverse proxy
# (Caddy) can front an internal port. EXPOSE documents the default.
ENV PORT=8000

# Liveness via the public /healthz tool route (no DB, no auth, no extra packages).
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD ["sh", "-c", "python -c \"import os,urllib.request; urllib.request.urlopen('http://localhost:%s/healthz' % os.environ.get('PORT','8000'))\""]

CMD ["sh", "-c", "exec uvicorn backend.api.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
