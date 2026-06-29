# Pre-deployment preparation

What to set up **before** deploying the qubitrefill backend (the qupick MCP
server) to a real environment. This is a checklist of external accounts, secrets,
config edits, and infrastructure — not a deploy runbook.

Everything here is derived from `backend/src/backend/config.py`, `docker-compose.yml`,
`.mcp.json.example`, and the auth/email code (`backend/src/backend/api/auth.py`,
`backend/src/backend/email/sender.py`).

## 0. Decisions for this deployment

Locked choices that the rest of this doc assumes:

| Area | Choice | Consequence |
|------|--------|-------------|
| **Postgres** | Company **Supabase** | `DATABASE_URL` → Supabase **session pooler, port 5432** (long-running service). Prefer a **dedicated project or schema** — startup `create_all` should not share company tables. |
| **Email** | **Resend** (org-owned account) | `SmtpEmailSender` over `smtp.resend.com` (STARTTLS); sender `noreply@quip.network` (domain verified in Resend). Username is the literal `resend`; password is the Resend API key. Plain SMTP, so switching relays later is env-only. **DigitalOcean blocks outbound 25/465/587 → set `SMTP_PORT=2587`** (see §4). Use an **org-owned** account, not a personal one — registration mail carries the API key. |
| **Hosting** | **DigitalOcean droplet + Caddy** | Single instance is fine but **no longer forced** — the background mark-to-market loop was removed, so the server holds no in-process state. Caddy does auto-HTTPS and SSE pass-through for `/mcp`. `create_all` on a shared DB is the only multi-instance concern (see §7). |
| **Audience** | **Internal-only** | Per-agent bearer auth is sufficient; rate-limiting public registration is a nice-to-have, not a launch blocker. |
| **Schema** | Keep startup `create_all` | Fastest to launch; no migration tooling yet (see §7). Re-evaluate before the first schema change. |
| **Subdomain** | e.g. `qupick.quip.network` | Pointed at the droplet; image pushed to `registry.gitlab.com/quip.network/qupick` (amd64). |
| **Solvers** | **SA-only for v1 (PoC/MVP)** | `GUROBI_IN_RACE=0` (no Gurobi license in prod) and `DWAVE_API_TOKEN` unset (no QPU cost). Simulated annealing is the entire race field and always runs. Reversible later via env alone — re-add Gurobi or the QPU without a code change or image rebuild. |
| **Market data** | **Live assets-api** | `MARKET_DATA_SOURCE=assets-api` pointed at `https://asset-tracker.quip.network`, so the μ/Σ inputs reflect real market conditions. Adds a runtime dependency on that service being reachable; `synthetic` stays the offline fallback if it is down or unbuilt. |
| **Concurrency** | **Scale workers to vCPU** | `WEB_CONCURRENCY` = the droplet's vCPU count. The server is stateless in-process (no background loops), so this is safe; keep `workers × ~15` DB connections under the Supabase pooler cap. |

---

## 1. What actually gets deployed

| Component | What it is | Notes |
|-----------|-----------|-------|
| **Backend** | FastAPI app (`backend.api.app:app`) serving REST + the `/mcp` transport on port `8000` | Containerized (`backend/Dockerfile`); this is the qupick MCP server |
| **PostgreSQL** | Holds agents (the API keys) and jobs | The `db` service in `docker-compose.yml` is dev-grade only |
| **assets-api** | External market-data price service (REST, SQLite-backed) | Separate service; only needed when `MARKET_DATA_SOURCE=assets-api` |
| **qupick skill + client** | Claude Code side that calls the MCP server and Bitrefill | Needs `QUPICK_API_KEY` and `BITREFILL_API_KEY` |

The `docker-compose.yml` in the repo is a **dev stack** (weak Postgres password,
`MARKET_DATA_SOURCE: synthetic`, console email). Treat it as a reference, not a
production manifest.

---

## 2. External accounts to create

The backend (the server you deploy) talks to PostgreSQL, an SMTP server, D-Wave,
and the assets-api — nothing else. **Bitrefill is not a server dependency**: there are no
Bitrefill calls anywhere in `backend/` — it is used only by the qupick/bitrefill
skill running client-side in Claude Code. Keep the two surfaces separate.

### 2a. Backend / server accounts

| # | Service | Why it's needed | What to obtain | Cost / gotchas |
|---|---------|-----------------|----------------|----------------|
| 1 | **Supabase Postgres** (company account) | System of record; **stores the API keys** that are the only credential per agent | A `postgresql+asyncpg://…` string for the **session pooler (port 5432)**, in a dedicated project/schema, TLS enabled | Access provisioned by whoever manages the Supabase account. Losing this DB = losing every account — back it up. Don't reuse the `qtw:qtw` dev creds. |
| 2 | **Resend** (org-owned account, https://resend.com) | Registration emails the API key out-of-band; without working email **registration fails and rolls back** (`routes.py` register handler) | A Resend API key (used as `SMTP_PASSWORD`), username `resend`, verified sender domain (`noreply@quip.network`, SPF/DKIM DNS); host `smtp.resend.com`, port `2587` on DigitalOcean (§4), STARTTLS | Use an **org-owned** Resend account — the email carries every agent's API key, so a personal account exposes them. Verify `quip.network` in Resend. Free tier suits transactional volume. |
| 3 | **D-Wave Leap** (https://cloud.dwavesys.com) | The QPU competitor in the solver race; joins **only when `DWAVE_API_TOKEN` is set** | A Leap account + Solver API token | **QPU time costs real money / quota per job.** Without the token the backend runs SA-only (CPU) and still works. |
| 4 | **assets-api host** | Live prices for μ/Σ (the v1 choice — see §0) | The base URL of the running price service (`https://asset-tracker.quip.network`) | If it isn't reachable, fall back to `MARKET_DATA_SOURCE=synthetic` (deterministic, offline) and accept fake prices. |
| 5 | **DNS + domain** | A subdomain (e.g. `qupick.quip.network`) pointed at the droplet; TLS for the backend (§4) | DNS access for `quip.network` | Caddy gets its certificate for this name; the sender domain is already `quip.network`. |
| 6 | **Gurobi** (dev only) | Local oracle in the solver race | A license **only on dev machines** | **Not deployed to prod** (licensing). Set `GUROBI_IN_RACE=0` in production. |

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
| `DATABASE_URL` | `postgresql+asyncpg://qtw:qtw@127.0.0.1:5432/qtw` | Point at the Supabase **session pooler (:5432)**, appending **`?ssl=require`** (asyncpg's form — `sslmode=` is rejected) |
| `SMTP_PASSWORD` | `""` (→ console logger) | The Resend **API key** (secret). **Unset → emails only log to console**, so registration "succeeds" without delivering a key |
| `SMTP_USERNAME` | `resend` | The Resend SMTP login — the literal string `resend` |
| `EMAIL_FROM` | `Qubitrefill <noreply@quip.network>` | Sender; `quip.network` must be a **verified domain** in Resend |
| `MARKET_DATA_SOURCE` | `assets-api` | `assets-api` (real) or `synthetic` (offline). Compose overrides to `synthetic` |
| `ASSETS_API_BASE_URL` | `http://127.0.0.1:8080` | Set to `https://asset-tracker.quip.network` (the live assets-api) |
| `GUROBI_IN_RACE` | `1` | Set `0` in production (no Gurobi license there) |
| `PORT` | `8000` | Internal listen port behind Caddy (e.g. `8080`) |
| `WEB_CONCURRENCY` | `1` | uvicorn worker count; safe to raise (stateless), keep `workers × ~15` DB conns under the pooler cap |

**Optional / tuning:**

| Env var | Default | Purpose |
|---------|---------|---------|
| `SMTP_HOST` | `smtp.resend.com` | SMTP submission host; override only for a different relay |
| `SMTP_PORT` | `587` | SMTP submission port. **On DigitalOcean set `2587`** — DO blocks outbound 25/465/587 (anti-spam); `2587`/`2465` are Resend's alt submission ports |
| `SMTP_STARTTLS` | `true` | STARTTLS on; set `false` only for a plaintext local relay |
| `DWAVE_API_TOKEN` | unset | Enables the QPU competitor. Unset → SA-only |
| `DWAVE_NUM_READS` | `500` | QPU reads (parity with SA) |
| `DWAVE_ANNEAL_TIME_US` | `100` | Anneal time per read |
| `DWAVE_CHAIN_STRENGTH_PREFACTOR` | `3.0` | Raise if `qtw verify-dwave` shows >5% chain breaks |

**Client / skill side (not the backend service):**

| Env var | Used by | Notes |
|---------|---------|-------|
| `QUPICK_API_KEY` | `.mcp.json` Bearer header → per-agent MCP tools | The agent's key, emailed at registration. Unset → public tools work, per-agent tools 401 |
| `BITREFILL_API_KEY` | the bitrefill skill (`Authorization: Bearer`) | Funds the purchase flow |

---

## 4. Infrastructure prerequisites

- **TLS / HTTPS in front of the backend.** The app serves plain HTTP on its
  internal `PORT` (default `8000`; set e.g. `8080` behind the proxy), and the
  per-agent API key travels as `Authorization: Bearer <key>` on every request
  **including the public `/mcp` transport**. **Caddy** terminates TLS (auto-HTTPS)
  on the DigitalOcean droplet so keys are never in cleartext on the wire. Caddy
  also passes the `/mcp` SSE/streaming connection through unbuffered
  (`flush_interval -1`). The repo `Caddyfile` is the ready-to-use config.
- **Outbound SMTP egress (host-dependent).** Registration email leaves over SMTP
  submission. **DigitalOcean blocks outbound ports 25/465/587** by default
  (anti-spam), so Resend's default `587` times out on a DO droplet. Set
  `SMTP_PORT=2587` (Resend's alt STARTTLS port; `2465` for TLS-on-connect) — no
  code change, `SMTP_PORT` is env-driven. This bites every DO deploy otherwise.
- **Database schema management.** There is **no Alembic / migrations**. Tables are
  created at startup via `Base.metadata.create_all` (`app.py` lifespan), which only
  creates missing tables — it does **not** alter existing ones. Plan a migration
  story before the first schema change in production.
- **PostgreSQL backups + encryption at rest.** The DB holds the API keys (each is
  the primary key *and* the only credential). Encrypt at rest; take regular,
  tested backups; lock down network access.
- **Container registry + image build.** Build `backend/Dockerfile` (amd64), push to
  `registry.gitlab.com/quip.network/qupick`; the dev `docker-compose build` is not
  a deploy path.
- **Health check.** `GET /healthz` is public and DB-free — wire it to your
  orchestrator's liveness/readiness probe (compose already does).

---

## 5. Security checklist

- [ ] Strong, unique `DATABASE_URL` password; DB not publicly reachable.
- [ ] Postgres encrypted at rest; backups encrypted and access-controlled.
- [ ] TLS in front of the backend (keys flow on every request — §4).
- [ ] Server secrets (`SMTP_PASSWORD`, `DWAVE_API_TOKEN`, DB creds) in a secret
      manager, not in the image or compose file.
- [ ] `EMAIL_FROM` (`noreply@quip.network`) sends via an **org-owned** Resend
      account (domain verified); test that a real client inbox receives the key.
      Do not route registration mail through a personal account — it carries the
      per-agent API key.
- [ ] `GUROBI_IN_RACE=0` in prod (no license deployed).
- [ ] Decide on **key rotation** — there is currently no rotation/revocation
      endpoint; the only way to invalidate a key is to delete the agent row.
- [ ] Consider **rate limiting** on `POST /agents` — registration is public and
      writes a row + sends an email per call.

Client-side only (not on the server): keep `BITREFILL_API_KEY` and the agent's
`QUPICK_API_KEY` local; use a dedicated **low-balance** Bitrefill account for
real-money blast radius.

---

## 6. Pre-launch verification (do these against staging first)

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

Client-side check (not a server test): run the qupick flow once with a real
`BITREFILL_API_KEY` against a low-balance account and confirm the approval gate
fires before `buy-products`.

---

## 7. Known gaps to decide on before launch

- No DB migration tooling (schema changes are manual).
- No API-key rotation/revocation flow.
- No rate limiting on public registration.
- `assets-api` is an external dependency this repo does not ship — host it or run synthetic.
