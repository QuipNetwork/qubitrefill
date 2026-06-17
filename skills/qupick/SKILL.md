---
name: qupick
description: "This skill uses quantum computers to pick the best crypto asset to pay with, given current market conditions."
compatibility: "Requires: (1) a running portfolio backend at http://127.0.0.1:8000; (2) Bitrefill MCP (https://api.bitrefill.com/mcp) or CLI available. Delegates all purchase mechanics to the bitrefill skill."
metadata:
  author: hackathon
  version: "3.0.0"
---

# Pay with most suitable crypto asset in your portfolio

Identify the most suitable crypto in the portfolio (lowest annualised expected return) using a quantum unconstrained binary optimization, spend it on a Bitrefill product, then retune the portfolio without it.

Delegates all purchase mechanics to the [`bitrefill`](../bitrefill/SKILL.md) skill — read and invoke that skill for product search, pricing, buying, and payment polling. This skill adds portfolio seeding and selection logic on top.

## Backend API reference

Base URL: `http://127.0.0.1:8000`

All endpoints are JSON over HTTP. Error responses follow FastAPI's default shape:
`{"detail": [...]}` for validation errors (422) or `{"detail": "..."}` for app errors.

### POST /agents — create agent

**Request body** (`AgentConfig`, `name` + `sliders` required):
```json
{
  "name": "string",
  "email": "string | null",
  "handle": "string | null",
  "sliders": {
    "rebalanceFrequency": 50,
    "riskPreference": 50,
    "maxPositionSize": 50
  },
  "assets": ["BTC", "ETH", "..."]
}
```

`SliderValues` constraints — **all three fields are required**, each a number 0–100:
- `rebalanceFrequency` — how often the agent rebalances (0 = rarely, 100 = hourly max)
- `riskPreference` — risk-aversion term γ (0 = conservative, 100 = aggressive)
- `maxPositionSize` — per-asset weight cap (0 = equal-weight 1/n, 100 = up to ~50% in one asset)

Default sensible values when the user hasn't expressed a preference: `{"rebalanceFrequency": 50, "riskPreference": 50, "maxPositionSize": 50}`.

**Response** (`SubmitAgentResponse`):
```json
{
  "agentId": "afae79c9",
  "qrUrl": "https://qtw-tradinggame.netlify.app/p/afae79c9",
  "bankroll": 10000.0
}
```

### GET /agents/{agent_id} — fetch agent config

**Response** (`AgentConfig`): same shape as the POST request body. Use this to retrieve the current asset basket when re-using an existing agent.

### POST /agents/{agent_id}/optimize — optimise (and optionally retune)

**Request body** (entirely optional — omit body for a plain re-optimise with no changes):
```json
{
  "sliders": { ... },
  "assets": ["BTC", "ETH", "..."]
}
```

Both `sliders` and `assets` are individually optional. If `assets` is provided the agent's basket is replaced atomically before solving — this is the retune path. A retune liquidates all existing holdings and reallocates over the new basket.

**Response** (`RoutingResult`):
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

### GET /agents/{agent_id}/market — live holdings + μ values

**Response** (`MarketResult`):
```json
{
  "agentId": "afae79c9",
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

### GET /leaderboard — hackathon scoreboard

Returns an array of `LeaderboardEntry`:
```json
[
  {
    "rank": 1,
    "agentId": "afae79c9",
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

## Flow

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

### 2. Seed the agent (REST)

If the user already has an `agent_id`, fetch the current config with `GET /agents/{agent_id}` to read the existing basket — skip creation.

If no agent exists yet, create one seeded over the available Bitrefill currencies:

```
POST http://127.0.0.1:8000/agents
{
  "name": "<user-chosen name>",
  "email": "<user email>",
  "sliders": {"rebalanceFrequency": 50, "riskPreference": 50, "maxPositionSize": 50},
  "assets": ["BTC", "ETH", "BNB", "SOL", "XRP", "USDT", "USDC", "DOGE", "ZEC", "ALGO", "FIL"]
}
→ { "agentId": "<id>", "qrUrl": "...", "bankroll": 10000.0 }
```

Then optimise immediately (no body needed for first run):

```
POST http://127.0.0.1:8000/agents/{agentId}/optimize
```

Save `agentId` — it is used in every subsequent call.

### 3. Pick the product (MCP)

Use the bitrefill skill's `search-products` → `get-product-details` to settle on:
- Product name + country
- Denomination (`package_id`)
- Price in USD (from the `packages` array — use the field `payment_price` with `payment_currency == "USD"`)
- Accepted `payment_methods` list (from `get-product-details` — this is the authoritative per-product filter)

### 4. Fetch market (REST)

```
GET http://127.0.0.1:8000/agents/{agentId}/market
```

Returns the current USD value and μ for every asset in the basket.

### 5. Choose the best crypto asset for payment, preferring enough currency

Build candidates: assets where **all** of:
- `assetClass == "crypto"`
- `units > 0` (actually held)
- `ticker ∈ PAYMENT_METHOD_MAP`
- `PAYMENT_METHOD_MAP[ticker]` appears in the product's `payment_methods` list (from step 3)

**Selection logic (prefer enough, fall back to most optimized):**

1. **Preferred candidates** — those whose `usd >= product_price_usd * 1.02` (2 % fee buffer). Among these, pick `min(μ)`.
2. **Fallback** — if no candidate holds enough, pick `min(μ)` across *all* spendable candidates regardless of balance. Surface the shortfall and wait for explicit user approval before proceeding:
   ```
   ⚠️  Your [TICKER] holdings cover $X of the $Y price.
       You'll need to top up the payment wallet before the invoice can settle.
       Proceed anyway?
   ```
3. **Hard stop** — if there are *no* spendable crypto candidates at all, tell the user and stop. Do not silently fall back to a stablecoin.

### 6. Confirm + buy (MCP)

Present to the user and wait for explicit approval:
```
Product:    [name] — [denomination]
Price:      $[amount]
Pay with:   [TICKER] ([payment_method]) — your worst performer (μ = [mu])
Holdings:   $[usd] (covers price: yes / ⚠️ covers only $X of $Y)
```

After approval, use the bitrefill skill to:
1. `buy-products(cart_items=[{product_id, package_id, quantity: 1}], payment_method=MAP[worst], return_payment_link=true)`
2. Pay via the returned payment link
3. Poll `get-invoice-by-id` until `status == "complete"`
4. Call `get-order-by-id` for the redemption code / QR

Log: `invoice_id`, product, amount, payment method.

### 7. Retune (REST)

Remove the spent ticker from the basket and re-optimise. Pass only `assets` in the body — `sliders` can be omitted to keep existing values:

```
POST http://127.0.0.1:8000/agents/{agentId}/optimize
{
  "assets": ["BTC", "BNB", "SOL", ...]
}
```

(The list is the current basket minus the spent ticker.)

Report the new allocation: what was kept, what was dropped, new `portfolio` percentages.

## Worked example

> "Buy a $20 Steam gift card and dump my worst crypto"

1. **Available currencies** — static map gives 11 tickers.
2. **Seed agent** — `POST /agents {sliders: {rebalanceFrequency:50, riskPreference:50, maxPositionSize:50}, assets: [...11 tickers...]}` → `agentId = afae79c9`; `POST /agents/afae79c9/optimize`.
3. **Product** — `search-products("Steam", country="US")` → slug `steam-usa`; `get-product-details("steam-usa")` → `package_id = steam-usa<&>20`, price = $21.60, `payment_methods` includes `bitcoin`, `ethereum`, `solana`, `usdc_base`.
4. **Market** — `GET /agents/afae79c9/market` → BTC (μ=−0.0026, $227), ETH (μ=+0.0001, $227), SOL (μ=−0.0003, $227), USDC (μ=+0.00005, $2272).
5. **Select** — all four pass static filter + runtime check (Steam accepts all four). All hold ≥ $22.03 (preferred). Worst μ = BTC (−0.0026). **BTC chosen**.
6. **Confirm** — "Steam USD $20 · pay with BTC (bitcoin, μ=−0.0026) · holdings $227 ✓ · Approve?"
7. **Buy** → pay → poll → redeem code.
8. **Retune** — `POST /agents/afae79c9/optimize {"assets": ["ETH","BNB","SOL","XRP","USDT","USDC","DOGE","ZEC","ALGO","FIL"]}` (BTC dropped).

## Safeguards

This skill executes real-money purchases. See [`skills/bitrefill/references/safeguards.md`](../bitrefill/references/safeguards.md) for the full spending policy:
- Confirm before every purchase — no autonomous buying without explicit opt-in
- Stop before `buy-products` unless the user opts into a real purchase (real money)
- Treat codes as cash — never log or paste redemption codes in public channels
- Use a dedicated low-balance account
- Log every purchase: `invoice_id`, product, amount, method

The retune in step 7 is irreversible — the spent asset is removed from the basket permanently until the user re-adds it manually.
