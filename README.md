# qupick — usage

Pay for a Bitrefill product (gift card, top-up, eSIM) with the **worst-performing crypto** in your
portfolio — the asset with the lowest expected return μ — then retune the portfolio without it.

The agent logic lives in [`SKILL.md`](skills/qupick/SKILL.md); this README is the operator's quick-start.
Purchase mechanics are delegated to the **bitrefill** skill, installed separately from upstream (see
[Install the skill](#install-the-skill)).

## Register & set up (hosted)

The fastest path uses the **live hosted server** at `https://qupick.quip.network` — you don't run any
backend yourself. Five steps from clone to first run:

1. **Install the skills.** Install `qupick` (this repo) and the `bitrefill` plugin (from upstream) —
   see [Install the skill](#install-the-skill).

2. **Configure your defaults.** Copy the example and fill it in (see [Configure](#configure)):

   ```bash
   cp skills/qupick/config.example.json skills/qupick/config.json
   ```

   Set at least `defaults.name` and `defaults.email` — registration emails your API key to that
   address, and both `name` and `email` must be unique on the server.

3. **Point Claude Code at the hosted MCP server.** Copy the example and set the `url` to the hosted
   endpoint. The API key goes **directly in the `Authorization` header** — Claude Code does **not**
   expand `${VAR}` in `.mcp.json` headers, so an environment variable won't reach the server. You have
   no key yet, so leave a placeholder for now (`.mcp.json` is gitignored, so the real key is safe here
   later):

   ```bash
   cp .mcp.json.example .mcp.json
   ```

   ```json
   { "mcpServers": { "qupick": { "type": "http",
     "url": "https://qupick.quip.network/mcp",
     "headers": { "Authorization": "Bearer REPLACE_WITH_YOUR_KEY" } } } }
   ```

4. **Register to get your API key.** With the placeholder still in place, start Claude Code and ask
   the agent to proceed. `register_agent` is a **public** tool — it works without a valid key — and it
   is called with your `config.defaults` (name, email, sliders); the server **emails your API key** to
   that address (it is never shown in the response). A repeat email or name returns `409` with detail
   `email already registered` / `name already taken` (or `email or name already taken`).

5. **Paste the key and reconnect.** Put the emailed key into `.mcp.json`, replacing the placeholder in
   the `Authorization` header:

   ```json
   "headers": { "Authorization": "Bearer <key-from-email>" }
   ```

   Then reload the server with `/mcp` inside Claude Code. The per-agent `mcp__qupick__*` tools now
   authenticate and the agent runs its first `optimize`.

That's it — continue to [Use it](#use-it). To run the backend yourself instead of using the hosted
server, see [Development](#development).

## Prerequisites

- The **qupick MCP server** — the portfolio backend, registered via `.mcp.json` and exposing
  `mcp__qupick__*` tools. Use the hosted server at `https://qupick.quip.network/mcp` (above) or run
  it locally at `http://127.0.0.1:8000/mcp` (see [Development](#development)).
- The **bitrefill skill + Bitrefill MCP**, installed from upstream via
  `/plugin install bitrefill@bitrefill-skills` (see [Install the skill](#install-the-skill)). The MCP
  (`https://api.bitrefill.com/mcp`, OAuth or API key) drives product search, balance reads, and
  invoice creation; the Bitrefill REST API key is an alternative for the balance probe.
- A funding source the waterfall can draw on: a pre-funded **Bitrefill account balance** (USD, EUR,
  and/or the worst-performing asset) and/or a funded on-chain wallet for that asset. The funding order is
  configurable (see [Configure](#configure)).
- A **`skills/qupick/config.json`** — copy `skills/qupick/config.example.json` and fill it in. Without
  it the skill still runs, but fully interactively (it asks for name, denomination, and pays on-chain
  only).

## Install the skill

This repo ships the **qupick** skill. Its purchase layer — the **bitrefill** skill — is **not**
vendored here; install it from upstream.

1. **bitrefill (upstream plugin).** Registers the skill and its eCommerce MCP in one step:

   ```
   /plugin marketplace add bitrefill/agents
   /plugin install bitrefill@bitrefill-skills
   /reload-plugins
   ```

   Upstream: <https://github.com/bitrefill/agents>. Authenticate via OAuth or API key on first use.

2. **qupick (this repo).** Claude Code discovers skills under `.claude/skills/`:

   ```bash
   mkdir -p .claude/skills
   cp -R skills/qupick .claude/skills/
   ```

`.claude/` is gitignored — the skill install is local, not committed. The one tracked exception is
`backend/.claude/settings.json`, the shared permission allowlist (see
[Permissions & approvals](#permissions--approvals)).

## Configure

Copy the example config and edit it:

```bash
cp skills/qupick/config.example.json skills/qupick/config.json
```

`config.json` is gitignored (it holds your real email). Identity is **not** in the config — the
agent's API key lives in the `.mcp.json` `Authorization` header (see
[Register & set up](#register--set-up-hosted)). Fields:

| Field | Purpose |
|-------|---------|
| `defaults` | `name` / `email` / `country` / `sliders` used when creating a new agent. |
| `funding.priority` | Settlement order. Default `["account_match", "onchain_match", "account_fiat"]`. |
| `funding.fee_buffer_pct` | Coverage buffer over the sticker price (default `2`). |
| `funding.on_shortfall` | `reject` (stop) or `confirm` (warn and ask) when nothing covers the price. |
| `denomination.policy` | `smallest_gte` auto-picks the smallest package ≥ the requested amount. |
| `backend.marketDataSource` | `MARKET_DATA_SOURCE` used when the skill auto-starts the backend. Default `synthetic` (offline, deterministic). |

**Funding waterfall.** The worst performer (`min(μ)`) is *always* computed. The bill is then settled by
the first source in `funding.priority` that covers the price:

- `account_match` — Bitrefill account balance held in the worst-performing asset → sells it → **retunes**.
  Bitrefill sub-accounts are limited to `XBT`/`USD`/`EUR`, so this only applies when the worst
  performer is **BTC**; for any other asset it is skipped and the waterfall falls through.
- `onchain_match` — on-chain wallet holdings of the worst-performing asset → sells it → **retunes**.
- `account_fiat` — Bitrefill USD/EUR balance → settles without selling crypto → **no retune**.

Reorder or drop tokens to change behaviour — e.g. `["account_fiat", "account_match", "onchain_match"]`
to spend fiat first, or drop `account_fiat` to only ever sell crypto.

This config plus the permission allowlist in `backend/.claude/settings.json` make a run stop in
**exactly one** place — the purchase approval. See [Permissions & approvals](#permissions--approvals).

## Permissions & approvals

The skill is designed to interrupt you in **exactly one place** — the purchase approval in step 6.
Everything else it does is read-only. To get that clean single-stop experience, pre-approve the
read-only tools so they don't prompt, and keep the spend/mutation tools gated.

The allowlist lives in **`backend/.claude/settings.json`** — this is the project directory Claude
Code resolves from when you run the skill from `backend/`. It is the one file under `.claude/` that
is git-tracked (via a `.gitignore` exception); `settings.local.json` is for personal, machine-local
overrides and stays ignored.

**Pre-approve (read-only — safe in `permissions.allow`):**

| Entry | Why it's safe |
|-------|---------------|
| `mcp__qupick__ping_backend`, `get_agent`, `get_market`, `get_leaderboard` | backend reads — liveness, config, holdings, scoreboard |
| `mcp__bitrefill__search-products`, `get-product-details` | catalog search + pricing/accepted methods |
| `mcp__bitrefill__get-invoice-by-id`, `list-invoices` | invoice polling for on-chain settlement |
| `Bash(curl https://api.bitrefill.com/v2/accounts/balance*)` | read-only account-balance probe |
| `Read(.../skills/qupick/**)` | reads `config.json` and the skill files |

**Keep gated (in `permissions.ask`, never `allow`):**

| Entry | Why it must prompt |
|-------|--------------------|
| `mcp__bitrefill__buy-products` | **real-money purchase** — the single, non-negotiable human stop |
| `mcp__qupick__optimize` | the **irreversible retune** — drops the sold asset from the basket |
| `mcp__qupick__register_agent`, `submit-prepayment-step`, `update-order` | account/order mutations |

`ask` rules take precedence over `allow`, so even if a broader allow pattern would match, these
always surface a prompt.

### A note on auto-mode

The flow works end-to-end under Claude Code's auto-accept modes — but for a real-money skill that's
the wrong default:

- **`bypassPermissions` ("dangerously skip permissions") removes the spend gate.** Nothing prompts,
  so `buy-products` and the irreversible `optimize` retune would run unattended. **Don't run this
  skill in that mode.**
- **Prefer default mode + the read-only allowlist above.** You get the same uninterrupted run for
  every read-only step, while purchases and retunes still stop for explicit approval — the gate the
  skill's safeguards depend on.
- The `permissions.ask` entries above are your safety net: they force a prompt for purchases and
  retunes regardless of the allowlist (but they do **not** override `bypassPermissions`, which is
  why that mode is off-limits here).

To regenerate or extend the allowlist from your own usage, run the `/fewer-permission-prompts`
helper — it scans recent transcripts for read-only calls and proposes entries.

> **Portability quirk:** the two `Read(...)` rules use **absolute paths** for this machine
> (`/home/konrad/Quip/qubitrefill/...`). Because `settings.json` is now committed, anyone cloning to
> a different path must update those two lines (or delete them and accept a single prompt on the
> first config read). The MCP-tool and `curl` entries are fully portable.

## Use it

Ask the agent in natural language, e.g.:

> "Buy a $20 Steam gift card and pay with my worst-performing crypto."

The skill then runs the flow from `SKILL.md`:

0. **Read config** — `skills/qupick/config.json` (defaults, funding order). Missing/malformed
   → fully interactive fallback, no crash.
1. **Available currencies** — static map of which holdings are spendable. Of the 11 basket tickers,
   only **BTC, ETH, SOL, USDC, USDT** have a `buy-products` rail; the other six (BNB, XRP, DOGE,
   ZEC, ALGO, FIL) are held for diversification but can't fund a purchase. Stablecoins are
   multi-rail (`usdc_base`/`usdc_solana`/…, `usdt_trc20`/…), matched against the product's accepted
   methods live.
2. **Check + seed agent (MCP)** — `ping_backend`; if the qupick tools are missing, offer to start the
   server. Then `get_agent` (success → reuse the basket), or `register_agent` from `config.defaults`
   → put the emailed/console key in `.mcp.json`'s `Authorization` header, reconnect MCP, then `optimize` for the first solve.
3. **Pick product (MCP)** — `search-products` → `product-details` for price + accepted
   `payment_methods`; `denomination.policy` auto-selects the package.
4. **Market (MCP)** — `get_market` for per-asset μ, units, USD value.
5. **Select + fund** — compute the worst performer (`min(μ)`) over held, product-accepted crypto, then
   read account balances (the `account_balances` block from `product-details`, falling back to
   `GET /accounts/balance`) and resolve `funding.priority` to the first source covering the price.
6. **Confirm + buy (MCP)** — the agent **stops for your explicit approval** at a fully-resolved
   screen (worst performer + chosen funding source), then buys: instant `balance` pay, or an on-chain link it
   polls to `complete`. Surfaces the redemption code.
7. **Retune (MCP)** — `optimize` with the basket minus the spent ticker — **only**
   when the worst performer was actually sold (`account_match` / `onchain_match`). Fiat settlement leaves the
   portfolio unchanged.

### Example

```
/market ranked by μ (worst first):
  BTC   crypto   μ=-0.002567   $230.67   ← worst performer (always selected)
  SOL   crypto   μ=-0.000341   $225.07
  USDC  crypto   μ=+0.000046   $2272.70
  ETH   crypto   μ=+0.000129   $229.61

Product:  Steam USD $20 ($21.60, +2% buffer → $22.03) · accepts bitcoin/ethereum/solana/usdc_base
Worst:    BTC (μ=-0.0026)
Waterfall: account_match → Bitrefill account BTC $60 covers $22.03 ✓
Settle:   Bitrefill account BTC · sells it ✓ · will retune
Buy:      payment_method="balance", balance_currency="XBT" → complete → redemption code
Retune:   drop BTC, re-optimize over the remaining 10 currencies
```

## Safeguards (real money)

- The agent **never buys without explicit approval** — it always pauses at step 6.
- Codes deliver instantly and are **non-refundable**; treat redemption codes as cash and redeem ASAP.
- Use a dedicated, low-balance wallet. Full policy: the bitrefill skill's `safeguards.md` (installed
  with the plugin).
- The step-7 retune is irreversible — the spent asset leaves the basket until you re-add it.

## Development

To work on the backend or run fully offline, run the server yourself instead of using the hosted one.
The backend serves both its REST API and the **qupick MCP server** (mounted at `/mcp`) from one
process; start it before the Claude session so the `mcp__qupick__*` tools register.

**Docker (Postgres + backend together):**

```bash
docker compose up -d --build
```

This brings up Postgres and the backend (image built from `backend/Dockerfile`), publishing the
server on `http://127.0.0.1:8000`. The container defaults to `MARKET_DATA_SOURCE=synthetic` and
`GUROBI_IN_RACE=0` (SA is the CPU solver — no Gurobi licence or D-Wave token needed).

**Local (uv), Postgres from compose:**

```bash
docker compose up -d db
cd backend
MARKET_DATA_SOURCE=synthetic uv run uvicorn backend.api.app:app --workers 1 --port 8000
```

Either way, wait until `GET http://127.0.0.1:8000/healthz` returns `{"ok": true}`. If the server is
down at session start the skill offers to start it backgrounded (allowlisted `synthetic` command) —
but the MCP tools only appear after you **reconnect the server** (run `/mcp` in Claude Code).

> First-solve cold start: the very first `optimize` call can return
> `503 no feasible solution ... before deadline` while the D-Wave/Gurobi libs warm up. Just retry
> once — subsequent solves are sub-10ms.

**Point `.mcp.json` at the local server** — same as the [hosted setup](#register--set-up-hosted),
but with the local URL (the key still goes directly in the header, not via an environment variable):

```json
{ "mcpServers": { "qupick": { "type": "http", "url": "http://127.0.0.1:8000/mcp",
  "headers": { "Authorization": "Bearer <your-key>" } } } }
```

Registration works the same way, except a local backend with no `SMTP_PASSWORD` set does **not** send
email — instead the key is printed to the backend console as
`[email:console] API key for … : <key>` (`docker compose logs backend`). Paste that value into
`.mcp.json`'s `Authorization` header and reconnect (`/mcp`).