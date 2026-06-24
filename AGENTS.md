# AGENTS.md

Guidance for coding agents working in this repository.

## Skills

### Bitrefill

Vendored at [`skills/bitrefill/`](skills/bitrefill/SKILL.md) (upstream: <https://github.com/bitrefill/agents/blob/main/skills/bitrefill/SKILL.md>). Read the full `SKILL.md` and follow its link-outs before acting.

**Bitrefill** sells digital goods (gift cards, mobile top-ups, eSIMs) across 180+ countries and 1,500+ brands. Pay with crypto, Lightning, USDC via x402, or pre-funded account balance. Codes deliver instantly after payment confirms.

This skill **routes by capability, not by use case**. The same intent ("buy a Steam card") plays out differently across hosts — pick a path based on what the runtime can do.

**Triggers:** the user mentions Bitrefill, gift cards, mobile top-up, eSIM data plan, refilling a phone, or asks to pay or check out with crypto, Lightning, USDC, or x402.

#### Pick a path (first match wins)

1. **Inside OpenClaw?** Check for `~/.openclaw/openclaw.json`, `~/.openclaw/skills/`, or `openclaw` on PATH. Default purchase path: guest CLI via `exec` (no auth). Sign in for `balance`/cashback.
2. **Browse-only intent (no purchase)?** Residential-IP browser → browse path. Datacenter egress only → `www.bitrefill.com` returns 403 Cloudflare; use MCP `search-products` / `product-details` instead.
3. **MCP supported?** Remote HTTP/SSE MCP at `https://api.bitrefill.com/mcp`. Highest-fidelity purchase channel — typed tool calls, OAuth or API key, no shell needed.
4. **Shell + `npm install` available?** CLI ≥ 0.3.0: guest checkout first (`buy-products --email` + crypto). Sign in for `balance`, cashback, order history.
5. **Outbound HTTP from agent loop?** REST API as last resort — verbose, no typed validation.
6. **None of the above?** Give the user a `bitrefill.com` link and stop.

#### Spending safeguards (read before any purchase)

This skill enables **real-money transactions**. Codes deliver instantly and digital goods are non-refundable.

- **Confirm before buying.** Present product, denomination, price, and payment method. Wait for explicit user approval. Autonomous purchasing only when the user opts in for the current session.
- **Treat codes as cash.** Never paste them in group chats or public channels. Prefer in-memory storage over plain-text logs. Advise the user to redeem ASAP.
- **Use a dedicated, low-balance account.** Never give the agent access to high-balance accounts or crypto wallet seeds. This skill is **not a wallet**.
- **Log every purchase.** `invoice_id`, product, amount, payment method.

#### Reference files

| File | Use when |
|------|----------|
| [browse.md](skills/bitrefill/references/browse.md) | Agent has residential-IP browser; user wants to explore |
| [mcp.md](skills/bitrefill/references/mcp.md) | MCP-capable host; preferred purchase path |
| [cli.md](skills/bitrefill/references/cli.md) | Shell + npm; guest checkout or signed-in CLI ≥ 0.3.0 |
| [cli-headless-auth.md](skills/bitrefill/references/cli-headless-auth.md) | Inbox + magic-link auth for headless agents |
| [api.md](skills/bitrefill/references/api.md) | HTTP-only runtime; Personal / Business / Affiliate REST tiers |
| [host-openclaw.md](skills/bitrefill/references/host-openclaw.md) | OpenClaw Gateway — guest CLI via `exec` preferred |
| [capability-matrix.md](skills/bitrefill/references/capability-matrix.md) | Per-client viable paths cheat sheet |
| [safeguards.md](skills/bitrefill/references/safeguards.md) | Spending policy + per-host hardening |
| [troubleshooting.md](skills/bitrefill/references/troubleshooting.md) | Common errors across all paths |

#### Source of truth

For exhaustive enums (countries, payment methods, full endpoint list), see <https://docs.bitrefill.com>.

### Qupick

Vendored at [`skills/qupick/SKILL.md`](skills/qupick/SKILL.md). Delegates purchase mechanics to the bitrefill skill above and adds portfolio selection on top.

**Triggers:** "pay with my worst performer", "use my worst crypto to buy X".

**Requires:** the **qupick MCP server** — the portfolio backend served at `http://127.0.0.1:8000/mcp`, exposing `mcp__qupick__*` tools. The backend must be up when the session starts for the tools to register; if they are missing the skill offers to start it backgrounded with `MARKET_DATA_SOURCE = config.backend.marketDataSource` (default `synthetic`) and then has the user reconnect MCP (`/mcp`). Also a local `skills/qupick/config.json` (copy of the committed `config.example.json`; gitignored because it holds the real email), and `QUPICK_API_KEY` set to the agent's key (emailed at registration; `.mcp.json` passes it as the Bearer header). Without the config the skill falls back to fully-interactive, on-chain-only behaviour.

**Selection vs settlement.** The skill always computes the worst performer — `min(μ)` over held crypto that Bitrefill accepts (`mcp__qupick__get_market`, static `PAYMENT_METHOD_MAP`). Selection is never bypassed by funding. It then resolves `config.funding.priority` against live balances (`GET /accounts/balance`) and on-chain holdings, settling against the first source that covers `price × (1 + fee_buffer_pct/100)`:

- `account_match` — Bitrefill account balance in the worst-performing asset → sells it → **retune**.
- `onchain_match` — on-chain wallet holdings of the worst-performing asset → sells it → **retune**.
- `account_fiat` — Bitrefill USD/EUR balance → no sale → **no retune**.

On shortfall (`funding.on_shortfall`): `reject` stops; `confirm` warns and waits. Retune (drop the spent asset, re-optimize) fires **only** when the worst performer was actually sold.

**Single human stop.** The flow is built to pause in exactly one place — the purchase approval. `mcp__bitrefill__buy-products` is deliberately kept off the `.claude/settings.local.json` allowlist. The six `mcp__qupick__*` tools are allowlisted (none spend real money), and the only `curl` is the read-only `/v2/accounts/balance` endpoint (write the URL first so prefix matching works). A purchase via `curl POST /v2/invoices` is **not** allowlisted and still prompts.

**Agent (re)use:** seeds over the Bitrefill-payable currencies (BTC, ETH, BNB, SOL, XRP, USDT, USDC, DOGE, ZEC, ALGO, FIL) via `mcp__qupick__register_agent` + `mcp__qupick__optimize`, or re-uses the existing agent — `get_agent` succeeding (with the configured `QUPICK_API_KEY`) means skip creation.

#### `mcp__qupick__get_market`

Returns per-asset expected return (μ) and current holdings for the authenticated agent (no id echoed — the API key identifies the caller):

```json
{
  "assets": [
    {"ticker": "BTC", "name": "Bitcoin", "assetClass": "crypto", "mu": 0.0012, "units": 0.05, "usd": 3200.0},
    {"ticker": "ETH", "name": "Ethereum", "assetClass": "crypto", "mu": -0.0003, "units": 1.2, "usd": 3600.0}
  ]
}
```

Pre-solve: falls back to the agent's configured basket with `units=0`. Post-solve: reflects actual holdings. μ is the annualised hourly expected return over the last 7 days (`MU_WINDOW_HOURS=168`).