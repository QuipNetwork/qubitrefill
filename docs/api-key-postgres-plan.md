# Plan: API-key registration + PostgreSQL backend

## Context

The backend (`backend/src/backend`) currently stores everything in-memory (`AgentStore`/`JobStore`
singletons, dict + `RLock`) and has **no auth** — any caller can hit any agent's endpoints, and the
agent's 8-char id is handed out freely (in `SubmitAgentResponse`, `qrUrl`, the leaderboard, and the
per-agent WS path). We want to gate the API behind a per-user key and persist data durably.

The change makes the **agent uuid a secret API key**: you register with a unique email + name,
receive the uuid by email (Proton SMTP), and must send it as `Authorization: Bearer <uuid>` on every
other request. Because the uuid is now secret, it must disappear from all public surfaces.

### Locked decisions
- **DB:** PostgreSQL via docker-compose; **SQLAlchemy 2.x async + asyncpg**.
- **Routes:** derive the agent from the Bearer token — **drop `{agent_id}` from URLs** (key never in a URL/log).
- **Public identity:** use the **unique `name`/`handle`** (no separate public id) for leaderboard, TV, and WS.
- **WebSockets:** **public-by-design** — subscribe by `name`/`handle`, no key required (keeps the booth display working).
- **Email:** **Proton SMTP** submission (`smtp.protonmail.ch:587`, STARTTLS) via `aiosmtplib`.
- **Registration response is email-only** — never returns the key; `qrUrl` is dropped (it embedded the secret id).

## 1. Infra & dependencies

- **`docker-compose.yml`** (repo root): one `postgres:16` service — `POSTGRES_USER/PASSWORD/DB=qtw`,
  named volume, port `5432:5432`, healthcheck. (App still run locally via uvicorn.)
- **`backend/pyproject.toml`** deps: add `sqlalchemy[asyncio]>=2.0`, `asyncpg>=0.29`,
  `pydantic[email]` (for `EmailStr`), `aiosmtplib>=3.0` (async SMTP).
- **`backend/src/backend/config.py`** new env-backed settings: `DATABASE_URL`
  (default `postgresql+asyncpg://qtw:qtw@127.0.0.1:5432/qtw`), `SMTP_HOST`
  (default `smtp.protonmail.ch`), `SMTP_PORT` (default `587`), `SMTP_USERNAME`, `SMTP_PASSWORD`,
  `SMTP_STARTTLS` (default `true`), `EMAIL_FROM` (default `Qubitrefill <onboarding@protonmail.com>`).

## 2. DB layer — new `backend/src/backend/db/`

- **`engine.py`**: `create_async_engine(config.DATABASE_URL)`, `async_sessionmaker(expire_on_commit=False)`,
  and a `get_session()` FastAPI dependency (yields an `AsyncSession`).
- **`models.py`** (SQLAlchemy 2.0 declarative, `Base`):
  - `Agent` — `id` (str PK = uuid hex = API key), `name` **UNIQUE** (indexed), `handle`,
    `email` **UNIQUE** (indexed), `reach_out` (JSON), `updates_opt_in`, `sliders` (JSON),
    `assets` (JSON), `bankroll`, `holdings_units` (JSON), `total`, `pl_usd`, `pl_pct`,
    `jobs_solved`, `primary_provider`, `created_at`.
  - `Job` — mirrors `JobRecord` (`jobs.py:18`): `id` PK, `agent_id` FK→Agent, `q_hash`, `provider`,
    `provider_role`, `solve_time_s`, `deadline_s`, `feasible`, `solved_at`.
- **Table creation:** `Base.metadata.create_all` in the app lifespan (`app.py:49`) — simplest for the
  hackathon. (Alembic is the production path; out of scope.)

## 3. Persistence → async session-scoped repositories

Rewrite `persistence/agents.py`, `jobs.py`, `leaderboard.py` from singletons into repos that take an
`AsyncSession`. Drop `get_agent_store()/get_job_store()` singletons and `reset()`.
- `AgentRepo(session)`: `create`, `get`, `get_by_email`, `get_by_name`, `all`, `update_sliders`,
  `update_assets`, `apply_solve`, `set_valuation` — same semantics as today (`agents.py:54`), now `await`ed.
- `JobRepo(session)`: `record`, `get`, `all`.
- `build_leaderboard(session)` (`leaderboard.py:13`): async query, **omit `agent_id`** from each entry.

### Sync-in-thread refactor (the riskiest piece)
`run_optimization` (`orchestration/job.py:51`) currently does DB I/O *and* the CPU solve inside one
call run via `asyncio.to_thread`. Async sessions can't be used from that worker thread, so split it:
- **Pure compute** stays in the thread: build `PortfolioProblem` (μ/Σ via `get_source()`),
  `race(...)`, compute `holdings_units`/portfolio. Refactor to `solve_portfolio(snapshot) -> outcome`
  taking a plain dict snapshot (tickers, sliders, current holdings, bankroll) — **no store access**.
- **DB I/O moves to the async route handler**: load agent + apply slider/asset updates (await),
  call `await asyncio.to_thread(solve_portfolio, snapshot)`, then persist `apply_solve` + `JobRepo.record`
  and publish events (await). The bus publish stays as-is.
- **MTM scheduler** (`orchestration/scheduler.py::run_mtm_loop`): open an `AsyncSession` per tick from
  the sessionmaker, read all agents, `mark_to_market` (`financial/pnl.py`), `set_valuation`, publish.

## 4. Registration — `POST /agents` (public)

- New `RegistrationRequest` schema: `name` (required), `email` **required** (`EmailStr`), `handle?`,
  `sliders`, `assets?`, `reach_out?`, `updates_opt_in?`.
- Handler (async, uses `AgentRepo` + `get_session`): validate basket (`financial/basket.validate_basket`);
  reject duplicate email or name → **409** (DB unique constraints as backstop: catch `IntegrityError`);
  `id = uuid4().hex`; create agent (`bankroll = config.BANKROLL_USD`); **send the key via Proton SMTP**;
  on send failure roll back + **502** so the user can retry (email-only delivery means a lost email = lost key).
- New `RegistrationResponse` = `{ message: "Check your email for your API key", email, bankroll }`.
  **Remove** `SubmitAgentResponse.qr_url`/`agent_id` exposure.

## 5. Auth middleware

- **`backend/src/backend/api/auth.py`**: a `BaseHTTPMiddleware` that, for every HTTP request **except**
  `POST /agents`, `GET /leaderboard`, `/docs`/`/openapi.json`/`/health`, requires
  `Authorization: Bearer <uuid>`, looks the agent up (async session), and attaches it to
  `request.state.agent` (401 if missing/invalid). Registered in `create_app()` (`app.py:67`).
- WebSocket scopes bypass HTTP middleware in Starlette → WS stays public (as designed).
- Protected handlers read `request.state.agent` instead of an `{agent_id}` path param.

## 6. Routes & schemas

- **`routes.py`** — token-derived shapes:
  - `POST /agents` → registration (public, above).
  - `GET /agents/me` (was `GET /agents/{id}`) → authed agent's `AgentConfig`.
  - `POST /agents/optimize` (was `/agents/{id}/optimize`) → optimize authed agent.
  - `GET /agents/market` (was `/agents/{id}/market`).
  - `GET /leaderboard` → public; entries without `agent_id`.
- **`ws.py`** — per-agent stream subscribes by **`name`/`handle`** (`/agents/ws/{handle}`), channel
  `agent:{handle}`; `/tv/events` unchanged. The optimize handler + scheduler publish on `agent:{handle}`;
  the TV `new-agent` payload drops `agentId`, sends `handle`/`name`.
- **`schemas.py`**: add `RegistrationRequest`/`RegistrationResponse`; **remove `agent_id` from
  `LeaderboardEntry`** (`schemas.py:73`); retire `SubmitAgentResponse`; drop `agentId` from the TV event.

## 7. Email — new `backend/src/backend/email/sender.py`

- `EmailSender` protocol with `send_api_key(to_email, name, api_key)`.
- `ProtonSmtpEmailSender`: builds a `text`+`html` `EmailMessage` and `aiosmtplib.send(...)` to
  `SMTP_HOST:SMTP_PORT` with STARTTLS, authenticating as `SMTP_USERNAME` / `SMTP_PASSWORD`.
- `FakeEmailSender` (tests) captures the last sent key. `get_email_sender()` factory (Proton SMTP when
  `SMTP_PASSWORD` set, else a console logger). Injected as a FastAPI dependency so tests override it.

## 8. Tests (`backend/tests/`)

- `conftest.py`: replace the in-memory `isolated_state` reset with a **test Postgres** fixture —
  `TEST_DATABASE_URL`, create tables once, **truncate between tests**; override `get_session` +
  `get_email_sender` (→ `FakeEmailSender`) on the app.
- `test_api.py`: `_register()` posts email+name and reads the key from `FakeEmailSender`; protected
  calls send `Authorization: Bearer <key>`; assert 401 without it, 409 on duplicate email/name,
  leaderboard has **no** `agentId`, WS streams by handle.
- `test_persistence.py`: port store tests to async `AgentRepo`/`JobRepo` against the test DB.

## Files

| File | Change |
|------|--------|
| `docker-compose.yml` | **new** — postgres:16 |
| `backend/pyproject.toml` | add sqlalchemy[asyncio], asyncpg, pydantic[email] |
| `backend/src/backend/config.py` | DATABASE_URL, SMTP_* , EMAIL_FROM |
| `backend/src/backend/db/{engine,models}.py` | **new** — async engine + ORM models |
| `backend/src/backend/persistence/{agents,jobs,leaderboard}.py` | singletons → async session repos; leaderboard drops agent_id |
| `backend/src/backend/orchestration/{job,scheduler}.py` | split DB I/O out of the worker thread; session-per-tick scheduler |
| `backend/src/backend/api/auth.py` | **new** — Bearer middleware → `request.state.agent` |
| `backend/src/backend/api/app.py` | register middleware; create tables in lifespan |
| `backend/src/backend/api/routes.py` | registration + token-derived routes |
| `backend/src/backend/api/ws.py` | subscribe by handle/name |
| `backend/src/backend/api/schemas.py` | RegistrationRequest/Response; LeaderboardEntry w/o agent_id |
| `backend/src/backend/email/sender.py` | **new** — Proton SMTP + fake |
| `backend/tests/*` | DB-backed fixtures; auth + uniqueness tests |
| `backend/src/backend/cli.py` | follow-up: it uses the old sync stores — port or flag out of scope |

## Verification

1. `docker compose up -d` → Postgres healthy on 5432.
2. `cd backend && DATABASE_URL=… SMTP_USERNAME=… SMTP_PASSWORD=… uv run uvicorn backend.api.app:app --workers 1`.
3. **Register:** `curl -X POST /agents -d '{"name":"Demo","email":"you@x.com","sliders":{...}}'`
   → 201 "check your email"; key arrives via Proton SMTP (or console). Re-POST same email/name → 409.
4. **Auth:** call `GET /agents/me` without header → 401; with `Authorization: Bearer <key>` → 200.
   `POST /agents/optimize`, `GET /agents/market` work with the bearer.
5. **No leak:** `GET /leaderboard` returns entries with **no** `agentId`; WS `/agents/ws/{handle}`
   streams P&L without a key.
6. `cd backend && TEST_DATABASE_URL=… uv run pytest` — green (auth, uniqueness, leaderboard, optimize).
