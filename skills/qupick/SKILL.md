---
name: qupick
description: "This skill uses quantum computers to pick the best crypto asset to pay with, given current market conditions."
compatibility: "Requires: (1) the qupick MCP server (the portfolio backend served at http://127.0.0.1:8000/mcp), exposing mcp__qupick__* tools; (2) Bitrefill MCP (https://api.bitrefill.com/mcp) or CLI available; (3) a local skills/qupick/config.json (see config.example.json). Delegates all purchase mechanics to the bitrefill skill."
metadata:
  author: hackathon
  version: "5.1.0"
---

# Pay with most suitable crypto asset in your portfolio

Identify the most suitable crypto in the portfolio (lowest annualised expected return) using a quantum unconstrained binary optimization, settle a Bitrefill product against the cheapest available funding source, then retune the portfolio only if the chosen crypto was actually sold.

Delegates all purchase mechanics to the [`bitrefill`](../bitrefill/SKILL.md) skill — read and invoke that skill for product search, pricing, buying, and payment polling. This skill adds portfolio seeding, selection logic, and an account-aware funding waterfall on top.

The flow is designed to stop for the user in **exactly one** place — the purchase approval (step 6). Defaults, a config file, and a permission allowlist remove the other interruptions.

## Calling conventions (MCP tools)

The backend is the **qupick MCP server** (served by the portfolio backend at
`http://127.0.0.1:8000/mcp`). Drive it with `mcp__qupick__*` tools — **no `curl`**. The six
tools are allowlisted, so none of them prompt:

| Tool | Does |
|------|------|
| `mcp__qupick__ping_backend` | liveness probe (public) |
| `mcp__qupick__register_agent` | create an agent; the API key is **emailed** (public) |
| `mcp__qupick__get_agent` | fetch the authenticated agent's config (basket, sliders) |
| `mcp__qupick__optimize` | optimise / retune the authenticated agent |
| `mcp__qupick__get_market` | live holdings + μ for the authenticated agent |
| `mcp__qupick__get_leaderboard` | scoreboard (public) |

**Auth.** Every per-agent tool (`get_agent`, `optimize`, `get_market`) carries the agent's API
key as `Authorization: Bearer <key>`, configured once in `.mcp.json`
(`"Authorization": "Bearer ${QUPICK_API_KEY}"`). Set `QUPICK_API_KEY` to the key from
registration (see step 2). The public tools work without it.

The only `curl` left in this flow is the read-only Bitrefill balance endpoint (step 5b):

```bash
curl https://api.bitrefill.com/v2/accounts/balance -H "Authorization: Bearer $BITREFILL_API_KEY"
```

That one is allowlisted (write the URL first so prefix matching works). Real purchases go through
the bitrefill skill's `buy-products`, deliberately **not** allowlisted, so the approval gate in
step 6 always fires.

## Backend tool reference

MCP server: `qupick` (the backend at `http://127.0.0.1:8000/mcp`). Tools return JSON; a failed
per-agent call surfaces the backend's `{"detail": "..."}` (e.g. `401` when `QUPICK_API_KEY` is
unset/wrong, `422` on validation, `503` on no feasible solution).

### `register_agent` — create agent (public)

**Arguments** (`name`, `email`, `sliders` all required):
```json
{
  "name": "string",
  "email": "you@example.com",
  "handle": "string | null",
  "sliders": {
    "rebalanceFrequency": 50,
    "riskPreference": 50,
    "maxPositionSize": 50
  },
  "assets": ["BTC", "ETH", "..."]
}
```

`email` is **required and unique**. `SliderValues` — **all three required**, each 0–100:
- `rebalanceFrequency` — how often the agent rebalances (0 = rarely, 100 = hourly max)
- `riskPreference` — risk-aversion term γ (0 = conservative, 100 = aggressive)
- `maxPositionSize` — per-asset weight cap (0 = equal-weight 1/n, 100 = up to ~50% in one asset)

Default sensible values when the user hasn't expressed a preference: `{"rebalanceFrequency": 50, "riskPreference": 50, "maxPositionSize": 50}`.

**Returns** (`RegistrationResponse`) — the API key is **emailed, never returned**:
```json
{
  "message": "Check your email for your API key.",
  "email": "you@example.com",
  "bankroll": 10000.0
}
```

In local dev (no `RESEND_API_KEY` on the backend) the key is **printed to the backend console**:
`[email:console] API key for <name> <<email>>: <key>`.

### `get_agent` — fetch agent config (authed)

**Returns** (`AgentConfig`): name, handle, email, `sliders`, and the current `assets` basket. Use
it to retrieve the basket when re-using an agent — and as the **"am I registered?" probe**: a
success means the configured key is valid; a 401 means register (or set `QUPICK_API_KEY`) first.

### `optimize` — optimise (and optionally retune) (authed)

**Arguments** (entirely optional — omit for a plain re-optimise with no changes):
```json
{
  "sliders": { ... },
  "assets": ["BTC", "ETH", "..."]
}
```

Both `sliders` and `assets` are individually optional. If `assets` is provided the agent's basket
is replaced atomically before solving — this is the retune path. A retune liquidates all existing
holdings and reallocates over the new basket.

**Returns** (`RoutingResult`):
```json
{
  "provider": "Gurobi",
  "providerType": "CPU",
  "solveTime": 0.0068,
  "vsClassical": 41.67,
  "portfolio": [
    {"ticker": "BNB", "pct": 29.55, "usd": 2954.55},
    {"ticker": "FIL", "pct": 29.55, "usd": 2954.55}
  ],
  "kind": "first | retune | null",
  "jobId": "7b49f676a6f5",
  "solvedAt": "2026-06-17T12:27:55.026750+00:00"
}
```

`providerType` is `"QPU"` or `"CPU"` — tells you whether a quantum solver was used.

### `get_market` — live holdings + μ values (authed)

**Returns** (`MarketResult`) — the authenticated agent's own holdings (no id echoed):
```json
{
  "assets": [
    {
      "ticker": "BTC",
      "name": "Bitcoin",
      "assetClass": "crypto",
      "mu": -0.002567,
      "units": 0.00370,
      "usd": 227.07
    }
  ]
}
```

`assetClass` is `"crypto"` or `"stock"`. `mu` is the annualised expected return — negative means the asset is expected to lose value.

### `get_leaderboard` — scoreboard (public)

Returns an array of `LeaderboardEntry` (no `agentId` — the id is the secret API key and never
appears on the public board):
```json
[
  {
    "rank": 1,
    "name": "string",
    "handle": "string | null",
    "total": 10423.50,
    "plUSD": 423.50,
    "plPct": 4.235,
    "jobsSolved": 12,
    "primaryProvider": "QPU"
  }
]
```

## Bitrefill account balance (REST)

```bash
curl https://api.bitrefill.com/v2/accounts/balance -H "Authorization: Bearer $BITREFILL_API_KEY"
```

Returns the pre-funded account balances. The account can hold balances in more than one asset (e.g. USD, EUR, BTC). Read the response **generically** — do not hardcode a field layout; look for per-asset entries giving an asset/currency and an available amount. These balances are funding sources in the step-5 waterfall alongside on-chain wallet payment.

## Flow

### 0. Read config

Load `skills/qupick/config.json` (mirror of the committed `config.example.json`):

```json
{
  "defaults": {
    "name": "Konrad",
    "email": "konrad@postquant.xyz",
    "country": "US",
    "sliders": { "rebalanceFrequency": 50, "riskPreference": 50, "maxPositionSize": 50 }
  },
  "funding": {
    "priority": ["account_match", "onchain_match", "account_fiat"],
    "fee_buffer_pct": 2,
    "on_shortfall": "reject"
  },
  "denomination": { "policy": "smallest_gte" },
  "backend": { "marketDataSource": "synthetic" }
}
```

- Identity is **not** in `config.json`. The agent's API key lives in `QUPICK_API_KEY` (read by
  `.mcp.json` as the `Authorization: Bearer` header). "Already registered?" is answered by
  `get_agent` succeeding, not by a stored id.
- `defaults` — name / email / country / sliders, used only when creating a new agent and for product country.
- `funding.priority` — settlement order (see step 5). `funding.fee_buffer_pct` — coverage buffer (default 2). `funding.on_shortfall` — `reject` | `confirm`.
- `denomination.policy` — `smallest_gte` auto-picks the smallest package ≥ the requested amount.
- `backend.marketDataSource` — the `MARKET_DATA_SOURCE` the backend runs with. The docker-compose stack sets `synthetic` (deterministic, offline) on the `backend` service; to use another source, edit that env before `docker compose up`. Absent → `synthetic`.

**If `config.json` is missing or malformed, do not crash.** Fall back to the fully-interactive behaviour: ask the user for name/email, ask which denomination, treat `funding.priority` as `["onchain_match"]` (on-chain only), and note that no config was found.

### 1. Determine available currencies (static map)

The set of cryptos the Bitrefill account can pay with is fixed. No live "list payment methods" endpoint exists. This map is the source of truth; per-product restrictions are confirmed live in step 5.

| Ticker | Bitrefill payment_method |
|--------|--------------------------|
| BTC    | bitcoin                  |
| ETH    | ethereum                 |
| BNB    | bnb                      |
| SOL    | solana                   |
| XRP    | ripple                   |
| USDT   | usdt                     |
| USDC   | usdc_base                |
| DOGE   | dogecoin                 |
| ZEC    | zcash                    |
| ALGO   | algorand                 |
| FIL    | filecoin                 |

Any portfolio asset whose ticker is not in this table cannot be spent on Bitrefill and is dropped silently.

### 2. Seed the agent (MCP)

**Server up?** The qupick MCP server rides on the backend over HTTP, so the backend must be up
**when the session started** for the `mcp__qupick__*` tools to be registered. Two cases:

- **Tools present:** call `mcp__qupick__ping_backend` → `{"ok": true}` and proceed.
- **`mcp__qupick__*` tools missing** (server was down at session start): **offer to start it**, and on
  the user's yes bring up the full stack (Postgres + backend) with docker compose from the repo root:

  ```bash
  docker compose up -d --build
  ```

  The backend opens a Postgres connection on startup, so a bare `uvicorn` process is **not** enough —
  compose starts the `db` and `backend` services together, with `MARKET_DATA_SOURCE=synthetic`
  (deterministic, offline) and the console email sender. Cold start (image build + DB healthcheck)
  takes ~20s; poll `docker compose ps` until `backend` is healthy (or `curl http://127.0.0.1:8000/healthz`).
  **Then the user must reconnect the MCP server** (run `/mcp` in Claude Code) so the qupick tools
  register — they won't appear mid-session otherwise. If the user declines, stop with the manual
  command. Do not auto-start without the user's yes.

**Already registered?** Call `mcp__qupick__get_agent`:

- **Succeeds** → the configured `QUPICK_API_KEY` is valid. Read the returned `assets` basket; skip
  creation.
- **401** → no valid key. Either `QUPICK_API_KEY` is unset/stale, or there is no agent yet —
  create one (below).

**Create.** Seed over the available Bitrefill currencies, using `config.defaults`:

```
mcp__qupick__register_agent({
  "name": "<config.defaults.name>",
  "email": "<config.defaults.email>",
  "sliders": {"rebalanceFrequency": 50, "riskPreference": 50, "maxPositionSize": 50},
  "assets": ["BTC", "ETH", "BNB", "SOL", "XRP", "USDT", "USDC", "DOGE", "ZEC", "ALGO", "FIL"]
})
# → { "message": "Check your email for your API key.", "email": "...", "bankroll": 10000.0 }
```

The API key is **emailed, not returned**. Retrieve it — from the email, or in local dev (no
`RESEND_API_KEY`) from the backend container logs (`docker compose logs backend`), line
`[email:console] API key for <name> <<email>>: <key>` — then **set `QUPICK_API_KEY` to it and
reconnect the MCP server (`/mcp`)** so the per-agent tools authenticate.
Once `get_agent` succeeds, optimise immediately for the first solve (no arguments):

```
mcp__qupick__optimize({})
```

### 3. Pick the product (MCP)

Use the bitrefill skill's `search-products` (with `country = config.defaults.country`) → `product-details` to settle on:
- Product name + country
- Price in USD (from the `packages` array — use the field `payment_price` with `payment_currency == "USD"`)
- Accepted `payment_methods` list (from `product-details` — the authoritative per-product filter)
- Denomination (`package_id`), selected by `config.denomination.policy`:
  - `smallest_gte` (default) — given a target amount, auto-select the smallest package whose value is ≥ the target. No user prompt.
  - if no policy/config — ask the user which denomination.

### 4. Fetch market (MCP)

```
mcp__qupick__get_market({})
```

Returns the current USD value and μ for every asset in the authenticated agent's basket.

### 5. Select the worst performer, then resolve the funding waterfall

Selection and settlement are **separate**. Selection always runs and is never bypassed by how the bill is paid.

**5a. Selection (always runs).** Build spendable crypto candidates — assets where **all** of:
- `assetClass == "crypto"`
- `units > 0` (actually held)
- `ticker ∈ PAYMENT_METHOD_MAP`
- `PAYMENT_METHOD_MAP[ticker]` appears in the product's `payment_methods` list (from step 3)

The **worst performer** is `min(μ)` across these candidates. If there are no spendable crypto candidates at all, **hard stop** — tell the user and do not silently substitute a stablecoin.

**5b. Settlement waterfall.** Let `price = denomination_price_usd × (1 + funding.fee_buffer_pct/100)`. For account balances, **prefer the `account_balances` block already returned by `product-details` in step 3** — each entry's `equivalent_in_product_currency` is pre-converted to the product's currency, so it compares directly against `price` (no FX maths). The standalone `GET /accounts/balance` endpoint above is the fallback when product-details omits it. Walk `funding.priority` in order and take the **first source that covers the full `price`** — no invoice splitting (a Bitrefill invoice takes one payment method):

| Token | Source | Pays via | Sells it? | Retune? |
|-------|--------|----------|--------------|---------|
| `account_match` | Bitrefill account balance held in the worst-performing asset (e.g. account BTC) | `buy-products(payment_method:"balance", auto_pay:true)` | yes | yes |
| `onchain_match` | Wallet holdings of the worst-performing asset (on-chain BTC) | `buy-products(payment_method:MAP[worst], return_payment_link:true)` → pay link → poll | yes | yes |
| `account_fiat` | Bitrefill USD/EUR account balance | `buy-products(payment_method:"balance", auto_pay:true)` | no | no |

- `account_match` / `account_fiat` coverage is checked against the **account balances** from `GET /accounts/balance` (the worst-performer balance for `account_match`; the fiat balances for `account_fiat`).
- `onchain_match` coverage is checked against the worst performer's **wallet holdings** (`usd` from step 4).
- Record which token was chosen — step 6 maps it to `buy-products` arguments and step 7 gates the retune on it.

**5c. Shortfall.** If no source in `funding.priority` covers the full `price`, apply `funding.on_shortfall`:
- `reject` (default) — stop with a clear message naming the gap (which sources were tried, how much each covered of `price`). No purchase.
- `confirm` — present the shortfall and wait for explicit user approval to proceed on-chain with the worst-performing asset (legacy fallback). Only proceed on an explicit yes.

> **Distinguishing `account_match` from `account_fiat`.** Both pay via `payment_method:"balance"`. If `buy-products` / the balance API cannot direct the debit to a specific asset, treat a `balance` payment as `account_fiat` (**no retune**) unless Bitrefill reports the worst-performer balance was actually debited. Never retune on an unconfirmed crypto debit.

### 6. Confirm + buy (MCP) — the single human stop

Resolve the waterfall to one concrete source **before** showing this screen, so the user approves a fully-specified transaction:

```
Product:   [name] — [denomination]
Price:     $[price]  (incl. [fee_buffer_pct]% buffer)
Worst:     [TICKER] ([payment_method], μ=[mu])        ← always shown
Settle:    [chosen source]
             account_match  → Bitrefill account [TICKER] ($[avail]) · sells it ✓ · will retune
             onchain_match  → On-chain [TICKER] (wallet $[usd])     · sells it ✓ · will retune
             account_fiat   → Bitrefill [USD/EUR] balance ($[avail]) · no sale · portfolio unchanged
Approve?
```

After explicit approval, use the bitrefill skill to buy, mapping the chosen token to `buy-products` arguments:
- `account_match` / `account_fiat`: `buy-products(cart_items=[{product_id, package_id, quantity:1}], payment_method="balance", auto_pay=true)` → instant.
- `onchain_match`: `buy-products(cart_items=[{product_id, package_id, quantity:1}], payment_method=MAP[worst], return_payment_link=true)` → pay via the returned link → poll `get-invoice-by-id` until `status == "complete"`.

Then `get-order-by-id` for the redemption code / QR. Log: `invoice_id`, product, amount, chosen funding token, payment method.

### 7. Retune (MCP) — only if the worst performer was sold

Retune **only** when settlement used `account_match` or `onchain_match` (the worst performer was actually sold). If settlement used `account_fiat`, **skip the retune** and report: "paid from fiat balance; portfolio unchanged."

When retuning, remove the spent ticker from the basket and re-optimise (pass only `assets`; omit `sliders` to keep existing values):

```
mcp__qupick__optimize({
  "assets": ["BTC", "BNB", "SOL", "..."]
})
```

(The list is the current basket minus the spent ticker.) Report the new allocation: what was kept, what was dropped, new `portfolio` percentages.

## Worked example

> "Buy a $20 Steam gift card and dump my worst crypto"

1. **Config** — `config.json` has `funding.priority = ["account_match","onchain_match","account_fiat"]`, `denomination.policy = smallest_gte`; `QUPICK_API_KEY` is set.
2. **Currencies** — static map gives 11 tickers.
3. **Agent** — `get_agent` succeeds → read the basket; skip creation.
4. **Product** — `search-products("Steam", country="US")` → `steam-usa`; `product-details` → `smallest_gte($20)` picks `package_id = steam-usa<&>20`, price $21.60, accepts `bitcoin`/`ethereum`/`solana`/`usdc_base`.
5. **Market** — `get_market({})` → BTC (μ=−0.0026, $227), ETH (μ=+0.0001, $227), SOL (μ=−0.0003, $227), USDC (μ=+0.00005, $2272).
6. **Select** — all four spendable + accepted. Worst μ = **BTC**. `price = 21.60 × 1.02 = $22.03`.
7. **Waterfall** — product-details `account_balances`: account BTC ≈ $60 (covers $22.03) → `account_match` wins. Sells the worst performer → will retune.
8. **Confirm** — "Steam USD $20 · worst performer BTC (μ=−0.0026) · settle Bitrefill account BTC ($60) · sells it ✓ · will retune · Approve?"
9. **Buy** — `buy-products(..., payment_method="balance", auto_pay=true)` → instant → redemption code.
10. **Retune** — `optimize({"assets": ["ETH","BNB","SOL","XRP","USDT","USDC","DOGE","ZEC","ALGO","FIL"]})` (BTC dropped).

## Safeguards

This skill executes real-money purchases. See [`skills/bitrefill/references/safeguards.md`](../bitrefill/references/safeguards.md) for the full spending policy:
- Confirm before every purchase — step 6 is the single, non-negotiable approval stop. `buy-products` is deliberately **not** on the Claude Code allowlist, so the harness also prompts.
- Stop before `buy-products` unless the user opts into a real purchase (real money).
- Treat codes as cash — never log or paste redemption codes in public channels.
- Use a dedicated low-balance account. `config.json` (real email) and `QUPICK_API_KEY` (the agent's secret key) are local-only — `config.json` is gitignored and the key never goes in git.
- Log every purchase: `invoice_id`, product, amount, funding token, method.

The retune in step 7 is irreversible — when it fires, the spent asset is removed from the basket permanently until the user re-adds it manually. It fires only when the worst performer was actually sold (`account_match` / `onchain_match`).
