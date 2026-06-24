# Pre-deployment preparation

What to set up **before** deploying the qubitrefill backend (the qupick MCP
server) to a real environment. This is a checklist of external accounts, secrets,
config edits, and infrastructure — not a deploy runbook.

Everything here is derived from `backend/src/backend/config.py`, `docker-compose.yml`,
`.mcp.json.example`, and the auth/email code (`backend/src/backend/api/auth.py`,
`backend/src/backend/email/sender.py`).

---

## 1. What actually gets deployed

| Component | What it is | Notes |
|-----------|-----------|-------|
| **Backend** | FastAPI app (`backend.api.app:app`) serving REST + WebSocket + the `/mcp` transport on port `8000` | Containerized (`backend/Dockerfile`); this is the qupick MCP server |
| **PostgreSQL** | Holds agents (the API keys), jobs, valuations | The `db` service in `docker-compose.yml` is dev-grade only |
| **assets-api** | External market-data price service (REST, SQLite-backed) | Separate service; only needed when `MARKET_DATA_SOURCE=assets-api` |
| **Frontend (MVP)** | Netlify-hosted UI / QR deep-link target | `https://qtw-tradinggame.netlify.app`; referenced by CORS + QR base |
| **qupick skill + client** | Claude Code side that calls the MCP server and Bitrefill | Needs `QUPICK_API_KEY` and `BITREFILL_API_KEY` |

The `docker-compose.yml` in the repo is a **dev stack** (weak Postgres password,
`MARKET_DATA_SOURCE: synthetic`, console email). Treat it as a reference, not a
production manifest.

---

## 2. External accounts to create

The backend (the server you deploy) talks to PostgreSQL, Resend, D-Wave, and the
assets-api — nothing else. **Bitrefill is not a server dependency**: there are no
Bitrefill calls anywhere in `backend/` — it is used only by the qupick/bitrefill
skill running client-side in Claude Code. Keep the two surfaces separate.

### 2a. Backend / server accounts

| # | Service | Why it's needed | What to obtain | Cost / gotchas |
|---|---------|-----------------|----------------|----------------|
| 1 | **Managed PostgreSQL** (RDS / Cloud SQL / Neon / self-hosted 16) | System of record; **stores the API keys** that are the only credential per agent | A `postgresql+asyncpg://…` connection string with a strong password, TLS enabled | Losing this DB = losing every account. Back it up. Don't reuse the `qtw:qtw` dev creds. |
| 2 | **Resend** (https://resend.com) | Registration emails the API key out-of-band; without working email **registration fails and rolls back** (`routes.py:95-100`) | An API key; a **verified sending domain** (DKIM/SPF DNS records) | Free tier has send limits. The default `onboarding@resend.dev` From only reaches your own address — useless for real clients. |
| 3 | **D-Wave Leap** (https://cloud.dwavesys.com) | The QPU competitor in the solver race; joins **only when `DWAVE_API_TOKEN` is set** | A Leap account + Solver API token | **QPU time costs real money / quota per job.** Without the token the backend runs SA-only (CPU) and still works. |
| 4 | **assets-api host** | Live prices for μ/Σ when not synthetic | A reachable base URL for the price service (you stand this up separately) | If you can't host it, ship with `MARKET_DATA_SOURCE=synthetic` (deterministic, offline) and accept fake prices. |
| 5 | **Netlify** (or wherever the MVP frontend lives) | Hosts the UI and the QR `/p/{agentId}` deep-link target | The deployed frontend URL | Its origin must match `CORS_ORIGINS` and `QR_BASE_URL` (currently hardcoded — see §4). |
| 6 | **DNS + domain** | Email domain verification (#2) and TLS for the backend (§5) | A domain you control; DNS access | Needed by both Resend and your HTTPS termination. |
| 7 | **Gurobi** (dev only) | Local oracle in the solver race | A license **only on dev machines** | **Not deployed to prod** (licensing). Set `GUROBI_IN_RACE=0` in production. |

### 2b. Client-side (qupick skill) accounts — not part of the server deploy

These belong to whoever runs the qupick skill in Claude Code, configured locally
(`.mcp.json`, `skills/qupick/config.json`, shell env). They are never set on the
backend.

| Service | Why it's needed | What to obtain | Cost / gotchas |
|---------|-----------------|----------------|----------------|
| **Bitrefill** (https://www.bitrefill.com) | The qupick purchase flow buys gift cards / settles invoices via the bitrefill skill | An account API key (`BITREFILL_API_KEY`) and a **funded, low-balance** account/wallet | Real money. Use a dedicated low-balance account. Local-only — never commit, never set server-side. |
| **A registered qupick agent** | Per-agent MCP tools authenticate with its key | `QUPICK_API_KEY` (emailed at registration by the backend) | Stored client-side in `.mcp.json` / env, not on the server. |

---

## 3. Secrets & environment variables

Backend env vars (all read in `config.py`). Set the secrets in your platform's
secret store, **never** in git or the image.

**Must set for production:**

| Env var | Default | Production action |
|---------|---------|-------------------|
| `DATABASE_URL` | `postgresql+asyncpg://qtw:qtw@127.0.0.1:5432/qtw` | Point at managed Postgres with a strong password + TLS |
| `RESEND_API_KEY` | `""` (→ console logger) | Set to a real Resend key, or registration emails won't send |
| `EMAIL_FROM` | `Qubitrefill <onboarding@resend.dev>` | An address on your **verified** Resend domain |
| `MARKET_DATA_SOURCE` | `assets-api` | `assets-api` (real) or `synthetic` (offline). Compose overrides to `synthetic` |
| `ASSETS_API_BASE_URL` | `http://127.0.0.1:8080` | Reachable assets-api URL when source is `assets-api` |
| `GUROBI_IN_RACE` | `1` | Set `0` in production (no Gurobi license there) |

**Optional / tuning:**

| Env var | Default | Purpose |
|---------|---------|---------|
| `DWAVE_API_TOKEN` | unset | Enables the QPU competitor. Unset → SA-only |
| `DWAVE_NUM_READS` | `500` | QPU reads (parity with SA) |
| `DWAVE_ANNEAL_TIME_US` | `100` | Anneal time per read |
| `DWAVE_CHAIN_STRENGTH_PREFACTOR` | `3.0` | Raise if `qtw verify-dwave` shows >5% chain breaks |
| `RESEND_API_URL` | `https://api.resend.com/emails` | Leave unless proxying |

**Client / skill side (not the backend service):**

| Env var | Used by | Notes |
|---------|---------|-------|
| `QUPICK_API_KEY` | `.mcp.json` Bearer header → per-agent MCP tools | The agent's key, emailed at registration. Unset → public tools work, per-agent tools 401 |
| `BITREFILL_API_KEY` | the bitrefill skill (`Authorization: Bearer`) | Funds the purchase flow |

---

## 4. Config edits that need code changes (not just env)

Two production-relevant values are **hardcoded** in `config.py`, not env-driven.
If your frontend/domain differs from the demo, you must edit the code (or make
them env-overridable first):

- `CORS_ORIGINS` (`config.py:131-135`) — currently `qtw-tradinggame.netlify.app`
  plus localhost. A browser frontend on any other origin will be CORS-blocked.
- `QR_BASE_URL` (`config.py:136`) — `https://qtw-tradinggame.netlify.app`; the
  `/p/{agentId}` deep-link base baked into QR codes.

Recommended: lift both to `os.environ.get(...)` before deploying to a new domain,
mirroring how `DATABASE_URL` etc. are done.

---

## 5. Infrastructure prerequisites

- **TLS / HTTPS in front of the backend.** The app serves plain HTTP on `8000`,
  and the per-agent API key travels as `Authorization: Bearer <key>` on every
  request **including the public `/mcp` transport**. Terminate TLS at a reverse
  proxy (nginx/Caddy/ALB) so keys are never in cleartext on the wire.
- **Database schema management.** There is **no Alembic / migrations**. Tables are
  created at startup via `Base.metadata.create_all` (`app.py:71`), which only
  creates missing tables — it does **not** alter existing ones. Plan a migration
  story before the first schema change in production.
- **PostgreSQL backups + encryption at rest.** The DB holds the API keys (each is
  the primary key *and* the only credential). Encrypt at rest; take regular,
  tested backups; lock down network access.
- **Container registry + image build.** Build `backend/Dockerfile`, push to your
  registry; the dev `docker-compose build` is not a deploy path.
- **Background scheduler.** The app starts a mark-to-market loop
  (`run_mtm_loop`, `app.py:74`) inside the process. Run a single backend instance
  for that loop, or externalize/guard it before horizontal scaling (otherwise
  every replica ticks).
- **Health check.** `GET /healthz` is public and DB-free — wire it to your
  orchestrator's liveness/readiness probe (compose already does).

---

## 6. Security checklist

- [ ] Strong, unique `DATABASE_URL` password; DB not publicly reachable.
- [ ] Postgres encrypted at rest; backups encrypted and access-controlled.
- [ ] TLS in front of the backend (keys flow on every request — §5).
- [ ] Server secrets (`RESEND_API_KEY`, `DWAVE_API_TOKEN`, DB creds) in a secret
      manager, not in the image or compose file.
- [ ] `EMAIL_FROM` on a verified domain; test that a real client inbox receives the key.
- [ ] `GUROBI_IN_RACE=0` in prod (no license deployed).
- [ ] Decide on **key rotation** — there is currently no rotation/revocation
      endpoint; the only way to invalidate a key is to delete the agent row.
- [ ] Consider **rate limiting** on `POST /agents` — registration is public and
      writes a row + sends an email per call.

Client-side only (not on the server): keep `BITREFILL_API_KEY` and the agent's
`QUPICK_API_KEY` local; use a dedicated **low-balance** Bitrefill account for
real-money blast radius.

---

## 7. Pre-launch verification (do these against staging first)

1. **DB connectivity** — backend boots, `create_all` runs, `GET /healthz` → 200.
2. **Registration → real email** — `POST /agents`; confirm the API key lands in a
   real external inbox (not just the console logger). Confirm a **forced email
   failure rolls back** (no orphaned agent).
3. **Auth enforcement** — a protected route (`GET /agents/me`) returns 401 without
   a key and 200 with the emailed key.
4. **MCP path** — run `backend/scripts/mcp_smoke.py` with `QUPICK_API_KEY` set;
   public tools work keyless, per-agent tools authenticate.
5. **Market data** — if `MARKET_DATA_SOURCE=assets-api`, confirm `ASSETS_API_BASE_URL`
   is reachable and returns prices; otherwise confirm `synthetic` is intended.
6. **Solver race** — with `DWAVE_API_TOKEN` set, run `qtw verify-dwave` and check
   chain breaks <5%; without it, confirm SA-only still solves.
7. **CORS / QR** — load the real frontend origin; confirm no CORS errors and QR
   deep links point at the right domain (§4).

Client-side check (not a server test): run the qupick flow once with a real
`BITREFILL_API_KEY` against a low-balance account and confirm the approval gate
fires before `buy-products`.

---

## 8. Known gaps to decide on before launch

- No DB migration tooling (schema changes are manual).
- No API-key rotation/revocation flow.
- No rate limiting on public registration.
- `CORS_ORIGINS` and `QR_BASE_URL` are hardcoded to the demo domain.
- `assets-api` is an external dependency this repo does not ship — host it or run synthetic.
